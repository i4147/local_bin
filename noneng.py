#!/usr/bin/env python3
"""Find non-English lines in text files and save a JSON report.

With no input paths, recursively scans the current directory. Findings are
shown as they are detected using Rich's live display. Install the CLI and
its default detector with ``python -m pip install typer rich langdetect``;
other detectors are optional and provide install hints when selected.
"""

from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Literal

from dh import SKIP_DIRS
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text
import typer


Backend = Literal["langdetect", "lingua", "pycld2", "gcld3", "py3langid", "langid"]

LIVE_FINDING_LIMIT = 15


@dataclass(frozen=True, slots=True)
class Detection:
    """Store a backend's language code and optional calibrated confidence."""

    language: str
    confidence: float | None


@dataclass(frozen=True, slots=True)
class Finding:
    """Represent one non-English source line in the JSON report."""

    file: str
    line: int
    language: str
    confidence: float | None
    text: str


class LanguageDetector:
    """Load one optional language-identification backend and expose a common API."""

    def __init__(self, backend: Backend) -> None:
        """Initialize the selected backend, raising an install hint if absent."""
        self.backend = backend
        self._detector: object
        if backend == "langdetect":
            try:
                from langdetect import DetectorFactory, detect_langs
            except ImportError as exc:
                raise RuntimeError("Install the default backend with: python -m pip install langdetect") from exc
            DetectorFactory.seed = 0
            self._detector = detect_langs
        elif backend == "lingua":
            try:
                from lingua import LanguageDetectorBuilder
            except ImportError as exc:
                raise RuntimeError("Install this backend with: python -m pip install lingua-language-detector") from exc
            self._detector = LanguageDetectorBuilder.from_all_languages().build()
        elif backend == "pycld2":
            try:
                import pycld2
            except ImportError as exc:
                raise RuntimeError("Install this backend with: python -m pip install pycld2") from exc
            self._detector = pycld2
        elif backend == "gcld3":
            try:
                import gcld3
            except ImportError as exc:
                raise RuntimeError("Install this backend with: python -m pip install gcld3") from exc
            self._detector = gcld3.NNetLanguageIdentifier(min_num_bytes=0, max_num_bytes=1000)
        elif backend == "py3langid":
            try:
                import py3langid
            except ImportError as exc:
                raise RuntimeError("Install this backend with: python -m pip install py3langid") from exc
            self._detector = py3langid
        else:
            try:
                import langid
            except ImportError as exc:
                raise RuntimeError("Install this additional backend with: python -m pip install langid") from exc
            self._detector = langid

    @staticmethod
    def _result(language: str, confidence: float | None) -> Detection | None:
        """Normalize a language label and discard the backend's unknown marker."""
        code = language.strip().lower()
        if code in {"", "un", "xx", "und", "unknown"}:
            return None
        return Detection(code, confidence)

    def detect(self, text: str) -> Detection | None:
        """Classify text and return its language code and confidence if available."""
        try:
            if self.backend == "langdetect":
                candidates = self._detector(text)  # type: ignore[operator]
                if not candidates:
                    return None
                result = candidates[0]
                return self._result(result.lang, float(result.prob))
            if self.backend == "lingua":
                values = self._detector.compute_language_confidence_values(text)  # type: ignore[attr-defined]
                if not values:
                    return None
                result = values[0]
                code = result.language.iso_code_639_1
                language = code.name.lower() if code is not None else result.language.iso_code_639_3.name.lower()
                return self._result(language, float(result.value))
            if self.backend == "pycld2":
                _reliable, _bytes_found, details = self._detector.detect(text, bestEffort=True)  # type: ignore[attr-defined]
                if not details:
                    return None
                _name, code, percent, _score = details[0]
                return self._result(str(code), float(percent) / 100.0)
            if self.backend == "gcld3":
                result = self._detector.FindLanguage(text)  # type: ignore[attr-defined]
                if not result.language:
                    return None
                return self._result(result.language, float(result.probability))
            if self.backend == "py3langid":
                language, _score = self._detector.classify(text)  # type: ignore[attr-defined]
                return self._result(str(language), None)
            language, _score = self._detector.classify(text)  # type: ignore[attr-defined]
            return self._result(str(language), None)
        except Exception:
            # Optional classifiers can reject short or malformed snippets; keep scanning.
            return None


def iter_text_files(roots: list[Path], report_path: Path) -> tuple[list[Path], list[str]]:
    """Find unique regular files beneath roots and collect traversal errors."""
    found: dict[Path, Path] = {}
    errors: list[str] = []
    report_resolved = report_path.resolve()
    for root in roots:
        if root.is_file() and not root.is_symlink():
            candidates = [root]
        elif root.is_dir() and not root.is_symlink():
            candidates = []
            for current, directories, filenames in os.walk(root, followlinks=False):
                directories[:] = sorted(name for name in directories if name not in SKIP_DIRS)
                for filename in sorted(filenames):
                    candidate = Path(current) / filename
                    if not candidate.is_symlink():
                        candidates.append(candidate)
        else:
            errors.append(f"Input not found or not a regular file/directory: {root.name or '.'}")
            continue
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                errors.append(f"Cannot access {candidate.name}: {exc}")
                continue
            if resolved != report_resolved:
                found.setdefault(resolved, candidate)
    return sorted(found.values(), key=lambda path: path.as_posix().casefold()), errors


def read_text_lines(path: Path) -> list[str] | None:
    """Read UTF-8 or Windows-1252 text, rejecting obvious binary files."""
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in payload:
        return None
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = payload.decode("cp1252")
        except UnicodeDecodeError:
            return None
    if text:
        controls = sum(ord(char) < 32 and char not in "\n\r\t\f\b" for char in text)
        if controls / len(text) > 0.01:
            return None
    return text.splitlines()


def _display_path(path: Path, working_directory: Path, roots: list[Path]) -> str:
    """Return a concise path relative to the most-specific supplied scan root."""
    resolved = path.resolve()
    containing_roots = [root.resolve() for root in roots if root.is_dir() and resolved.is_relative_to(root.resolve())]
    if containing_roots:
        root = max(containing_roots, key=lambda candidate: len(candidate.parts))
        relative = resolved.relative_to(root)
        if root != working_directory:
            return (Path(root.name) / relative).as_posix()
        return relative.as_posix()
    try:
        return resolved.relative_to(working_directory).as_posix()
    except ValueError:
        return (Path(path.parent.name) / path.name).as_posix()


def _has_enough_letters(text: str, minimum: int) -> bool:
    """Skip blank, numeric, and too-short lines that classifiers cannot judge well."""
    return sum(char.isalpha() for char in text) >= minimum


def _live_view(scanned: int, total: int, findings: list[Finding]) -> Group:
    """Build the refreshable progress text and recent-findings table."""
    heading = Text(f"Scanned {scanned}/{total} files · {len(findings)} non-English lines found", style="bold")
    table = Table(show_header=True, header_style="bold cyan", expand=True, box=None, padding=(0, 1))
    table.add_column("Location", no_wrap=True, overflow="ellipsis", max_width=32)
    table.add_column("Language", no_wrap=True, width=10)
    table.add_column("Found line", ratio=1, overflow="ellipsis")
    for finding in findings[-LIVE_FINDING_LIMIT:]:
        language = finding.language
        if finding.confidence is not None:
            language = f"{language} {finding.confidence:.2f}"
        table.add_row(f"{finding.file}:{finding.line}", language, Text(finding.text, no_wrap=True))
    if not findings:
        return Group(heading, Text("Waiting for non-English lines…", style="dim"))
    return Group(heading, table)


def scan_files(
    files: list[Path],
    detector: LanguageDetector,
    confidence_threshold: float,
    minimum_letters: int,
    console: Console,
    working_directory: Path,
    roots: list[Path],
) -> tuple[list[Finding], int, int, int]:
    """Scan files and update Rich Live immediately whenever a finding appears."""
    findings: list[Finding] = []
    scanned_lines = 0
    unreadable = 0
    with Live(_live_view(0, len(files), findings), console=console, refresh_per_second=12) as live:
        for file_index, path in enumerate(files, start=1):
            lines = read_text_lines(path)
            if lines is None:
                unreadable += 1
                live.update(_live_view(file_index, len(files), findings))
                continue
            display = _display_path(path, working_directory, roots)
            for line_number, line in enumerate(lines, start=1):
                scanned_lines += 1
                if not _has_enough_letters(line, minimum_letters):
                    continue
                result = detector.detect(line)
                if result is None or result.language == "en":
                    continue
                if result.confidence is not None and result.confidence < confidence_threshold:
                    continue
                findings.append(
                    Finding(
                        file=display,
                        line=line_number,
                        language=result.language,
                        confidence=result.confidence,
                        text=line,
                    )
                )
                # Force a refresh on each match so findings appear as soon as found.
                live.update(_live_view(file_index, len(files), findings), refresh=True)
            live.update(_live_view(file_index, len(files), findings))
    return findings, scanned_lines, unreadable, len(files)


def save_report(
    report_path: Path,
    backend: Backend,
    findings: list[Finding],
    files_scanned: int,
    lines_scanned: int,
    unreadable_files: int,
) -> None:
    """Write a UTF-8 JSON report with metadata and all findings."""
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "backend": backend,
        "files_scanned": files_scanned,
        "lines_scanned": lines_scanned,
        "unreadable_or_binary_files": unreadable_files,
        "non_english_lines": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


app = typer.Typer(add_completion=False, no_args_is_help=False)


@app.command()
def main(
    paths: list[Path] = typer.Argument(
        None, help="Files/directories to scan; defaults to the current directory recursively."
    ),
    backend: Backend = typer.Option(
        "langdetect",
        "-b",
        "--backend",
        case_sensitive=False,
        help="Language detector: langdetect, lingua, pycld2, gcld3, py3langid, or langid.",
    ),
    report: Path = typer.Option(Path("noneng-report.json"), "-o", "--report", help="JSON report destination."),
    min_confidence: float = typer.Option(
        0.80, "--min-confidence", min=0.0, max=1.0, help="Minimum backend confidence when available."
    ),
    min_letters: int = typer.Option(4, "--min-letters", min=1, help="Ignore lines with fewer alphabetic characters."),
) -> None:
    """Detect non-English lines under the given paths and save a JSON report."""
    try:
        detector = LanguageDetector(backend)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    working_directory = Path.cwd().resolve()
    roots = paths or [working_directory]
    report_path = report if report.is_absolute() else working_directory / report
    files, errors = iter_text_files(roots, report_path)
    console = Console()
    for error in errors:
        console.print(Text(error, style="yellow"), stderr=True)

    findings, line_count, unreadable, file_count = scan_files(
        files,
        detector,
        min_confidence,
        min_letters,
        console,
        working_directory,
        roots,
    )
    console.print()
    try:
        save_report(report_path, backend, findings, file_count, line_count, unreadable)
    except OSError as exc:
        console.print(f"Could not save JSON report: {exc}", style="red", stderr=True)
        raise typer.Exit(code=1) from exc

    console.print(f"Found {len(findings)} non-English lines in {file_count} files; report saved to {report_path.name}.")


if __name__ == "__main__":
    app()
