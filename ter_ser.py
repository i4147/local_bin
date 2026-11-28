#!/data/data/com.termux/files/usr/bin/env python
"""Recursively minify JavaScript files in place using Terser."""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import (
    Iterator,
    Sequence,
)
from collections.abc import (
    Set as AbstractSet,
)
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Final, Literal, NamedTuple

from dh import MAX_WORKERS, SKIP_DIRS
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


__version__: str = "1.4.7"
StatusTag = Literal["OK", "DRY-RUN", "NOCHG", "SKIP", "ERROR"]

DEFAULT_SUFFIXES: Final[frozenset[str]] = frozenset({".js", ".mjs", ".cjs", ".jsm"})
IMAP_CHUNKSIZE: Final[int] = 4
DEFAULT_TIMEOUT: Final[float] = 300.0
COMPARE_CHUNK: Final[int] = 1 << 16
TERSER_SUCCESS: Final[int] = 0
DEFAULT_MIN_SAVINGS_BYTES: Final[int] = 2
BYTES_PER_KIB: Final[float] = 1024.0
PERCENT_SCALE: Final[float] = 100.0
MIN_TERMINAL_WIDTH: Final[int] = 40
MIN_FILENAME_WIDTH: Final[int] = 12
COLUMN_GAP: Final[int] = 4
BACKUP_SUFFIX: Final[str] = ".bak"

EXIT_OK: Final[int] = 0
EXIT_ERROR: Final[int] = 1
EXIT_SIGINT: Final[int] = 130

STATUS_TAGS: Final[tuple[StatusTag, ...]] = (
    "OK",
    "DRY-RUN",
    "NOCHG",
    "SKIP",
    "ERROR",
)


@dataclass(frozen=True, slots=True)
class DiscoveryOptions:
    """Options controlling JavaScript file discovery."""

    suffixes: frozenset[str] = DEFAULT_SUFFIXES
    skip_dirs: frozenset[str] = SKIP_DIRS
    exclude_globs: tuple[str, ...] = ()
    skip_minified: bool = True
    verbose: bool = False


@dataclass(frozen=True, slots=True)
class MinifyOptions:
    """Options passed to Terser and used while replacing files."""

    terser: str = "terser"
    mangle: bool = True
    compress: bool = True
    toplevel: bool = False
    module: bool = False
    ecma: int | None = None
    comments: str | None = None
    keep_fnames: bool = False
    keep_classnames: bool = False
    safari10: bool = False
    config_file: Path | None = None
    extra_args: tuple[str, ...] = ()
    timeout: float = DEFAULT_TIMEOUT
    min_savings: int = DEFAULT_MIN_SAVINGS_BYTES
    skip_single_line: bool = True
    dry_run: bool = False
    backup: bool = False
    verbose: bool = True


@dataclass(frozen=True, slots=True)
class DisplayOptions:
    """Options controlling command-line output."""

    summary_only: bool = False
    human_bytes: bool = False
    no_color: bool = False
    max_errors: int | None = None


DEFAULT_DISCOVERY_OPTIONS: Final[DiscoveryOptions] = DiscoveryOptions()
DEFAULT_MINIFY_OPTIONS: Final[MinifyOptions] = MinifyOptions()


class ProcessResult(NamedTuple):
    """The outcome of processing one JavaScript file."""

    path: Path
    original_size: int
    new_size: int
    stdout: str
    stderr: str
    skipped: bool
    no_change: bool
    error: str | None


def _normalize_suffix(raw: str) -> str:
    cleaned = raw.strip().lower()
    if not cleaned:
        msg = "File extension cannot be empty."
        raise argparse.ArgumentTypeError(msg)
    return cleaned if cleaned.startswith(".") else f".{cleaned}"


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"Expected an integer, got {raw!r}."
        raise argparse.ArgumentTypeError(msg) from exc
    if value < 1:
        msg = f"Value must be >= 1, got {value}."
        raise argparse.ArgumentTypeError(msg)
    return value


def _non_negative_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"Expected an integer, got {raw!r}."
        raise argparse.ArgumentTypeError(msg) from exc
    if value < 0:
        msg = f"Value must be >= 0, got {value}."
        raise argparse.ArgumentTypeError(msg)
    return value


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        msg = f"Expected a number, got {raw!r}."
        raise argparse.ArgumentTypeError(msg) from exc
    if value <= 0.0:
        msg = f"Value must be > 0, got {value:g}."
        raise argparse.ArgumentTypeError(msg)
    return value


def walk_files(
    directory: Path,
    skip_dirs: AbstractSet[str] = SKIP_DIRS,
    err_console: Console | None = None,
) -> Iterator[Path]:
    """Yield files beneath ``directory`` without following directory symlinks."""
    err_out = err_console or Console(stderr=True)

    def on_walk_error(error: OSError) -> None:
        err_out.print(f"[yellow]warning:[/yellow] walk failed for {directory}: {error}")

    for root, directories, files in os.walk(
        directory,
        topdown=True,
        onerror=on_walk_error,
        followlinks=False,
    ):
        root_path = Path(root)
        directories[:] = [name for name in directories if name not in skip_dirs and not (root_path / name).is_symlink()]
        for name in files:
            yield root_path / name


def format_bytes(size: int) -> str:
    """Format a byte count using binary IEC units."""
    sign = "-" if size < 0 else ""
    value = float(abs(size))

    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < BYTES_PER_KIB or unit == "TiB":
            if unit == "B":
                return f"{sign}{int(value)} B"
            return f"{sign}{value:.2f} {unit}"
        value /= BYTES_PER_KIB

    return f"{sign}{value:.2f} TiB"


def format_delta(delta: int, *, human_bytes: bool = False) -> str:
    """Format a byte delta, optionally in human-readable units."""
    if delta == 0:
        return "0"
    if human_bytes:
        return format_bytes(delta)
    return str(delta)


def display_path(path: Path) -> str:
    """Return a path relative to the current directory when possible."""
    try:
        return str(path.relative_to(Path.cwd()))
    except (OSError, ValueError):
        return str(path)


def _dedupe(path: Path, seen: set[Path]) -> bool:
    try:
        key = path.resolve()
    except (OSError, RuntimeError):
        key = path

    if key in seen:
        return False
    seen.add(key)
    return True


def _is_minified_name(path: Path, suffixes: AbstractSet[str]) -> bool:
    lower_name = path.name.lower()
    return any(lower_name.endswith(f".min{extension}") for extension in suffixes)


def _is_excluded_vendor_path(path: Path) -> bool:
    parts = path.parts
    return "site-packages" in parts and "notebook" in parts


def _is_excluded_by_glob(path: Path, root: Path, patterns: Sequence[str]) -> bool:
    if not patterns:
        return False

    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()

    return any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def _should_accept_candidate(
    path: Path,
    root: Path,
    options: DiscoveryOptions,
    seen: set[Path],
    err_console: Console,
) -> bool:
    if path.suffix.lower() not in options.suffixes:
        return False

    if options.skip_minified and _is_minified_name(path, options.suffixes):
        if options.verbose:
            err_console.print(f"[dim]info: skipping pre-minified file: {display_path(path)}[/dim]")
        return False

    if _is_excluded_vendor_path(path):
        return False

    if _is_excluded_by_glob(path, root, options.exclude_globs):
        if options.verbose:
            err_console.print(f"[dim]info: excluded by pattern: {display_path(path)}[/dim]")
        return False

    try:
        if path.is_symlink():
            return False
    except OSError:
        return False

    return _dedupe(path, seen)


def _iter_directory(
    root: Path,
    seen: set[Path],
    options: DiscoveryOptions,
    err_console: Console,
) -> Iterator[Path]:
    for path in walk_files(
        root,
        skip_dirs=options.skip_dirs,
        err_console=err_console,
    ):
        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = path

        if any(part in options.skip_dirs for part in relative.parts[:-1]):
            continue

        if _should_accept_candidate(
            path,
            root,
            options,
            seen,
            err_console,
        ):
            yield path


def _inspect_explicit_root(
    raw_root: Path,
    seen: set[Path],
    options: DiscoveryOptions,
    err_console: Console,
) -> Iterator[Path]:
    try:
        if raw_root.is_symlink():
            err_console.print(f"[yellow]warning:[/yellow] skipping symlink: {raw_root}")
            return

        if not raw_root.exists():
            err_console.print(f"[yellow]warning:[/yellow] path does not exist: {raw_root}")
            return

        if raw_root.is_file():
            if _should_accept_candidate(
                raw_root,
                raw_root.parent,
                options,
                seen,
                err_console,
            ):
                yield raw_root
            return

        if raw_root.is_dir():
            try:
                resolved_root = raw_root.resolve()
            except (OSError, RuntimeError) as exc:
                err_console.print(f"[yellow]warning:[/yellow] cannot resolve {raw_root}: {exc}")
                return

            yield from _iter_directory(
                resolved_root,
                seen,
                options,
                err_console,
            )
            return

        err_console.print(f"[yellow]warning:[/yellow] not a file or directory: {raw_root}")
    except OSError as exc:
        err_console.print(f"[yellow]warning:[/yellow] cannot inspect {raw_root}: {exc}")


def iter_js_files(
    roots: Sequence[Path],
    options: DiscoveryOptions = DEFAULT_DISCOVERY_OPTIONS,
    err_console: Console | None = None,
) -> Iterator[Path]:
    """Yield unique candidate JavaScript files below the supplied roots."""
    err_out = err_console or Console(stderr=True)
    seen: set[Path] = set()

    for raw_root in roots:
        yield from _inspect_explicit_root(
            raw_root,
            seen,
            options,
            err_out,
        )


def _is_single_line_file(path: Path) -> bool:
    """Return whether a file contains at most one newline."""
    newline_count = 0

    with path.open("rb") as handle:
        while chunk := handle.read(COMPARE_CHUNK):
            newline_count += chunk.count(b"\n")
            if newline_count > 1:
                return False

    return True


def _files_identical(
    source: Path,
    candidate: Path,
    source_size: int,
    candidate_size: int,
) -> bool:
    if source_size != candidate_size:
        return False

    with source.open("rb") as source_handle, candidate.open("rb") as candidate_handle:
        while True:
            source_chunk = source_handle.read(COMPARE_CHUNK)
            candidate_chunk = candidate_handle.read(COMPARE_CHUNK)

            if source_chunk != candidate_chunk:
                return False
            if not source_chunk:
                return True


def _run_terser(
    command: Sequence[str],
    timeout: float,
) -> tuple[subprocess.CompletedProcess[bytes] | None, str | None]:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            timeout=timeout,
            check=False,
            close_fds=True,
        )
    except subprocess.TimeoutExpired:
        return None, f"terser timed out after {timeout:g}s"
    except FileNotFoundError:
        return None, f"terser executable not found: {command[0]!r}"
    except OSError as exc:
        return None, f"failed to run terser: {exc}"

    return completed, None


@dataclass(frozen=True, slots=True)
class _CandidateContext:
    path: Path
    target: Path
    tmp_path: Path
    original_size: int
    original_mode: int
    stdout: str
    stderr: str


def _build_terser_command(
    target: Path,
    tmp_path: Path,
    options: MinifyOptions,
) -> list[str]:
    command: list[str] = [options.terser]

    value_flags: tuple[tuple[str, str | None], ...] = (
        (
            "--config-file",
            str(options.config_file) if options.config_file is not None else None,
        ),
        ("--ecma", str(options.ecma) if options.ecma is not None else None),
        ("--comments", options.comments),
    )

    for flag, value in value_flags:
        if value is not None:
            command.extend((flag, value))

    bool_flags: tuple[tuple[str, bool], ...] = (
        ("--mangle", options.mangle),
        ("--compress", options.compress),
        (
            "--toplevel",
            options.toplevel and (options.mangle or options.compress),
        ),
        ("--module", options.module),
        ("--keep-fnames", options.keep_fnames),
        ("--keep-classnames", options.keep_classnames),
        ("--safari10", options.safari10),
    )

    command.extend(flag for flag, enabled in bool_flags if enabled)
    command.extend(options.extra_args)
    command.extend(("-o", str(tmp_path), "--", str(target)))
    return command


def _create_temp_file(target: Path) -> Path:
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{target.stem}.",
        suffix=f"{target.suffix}.tmp",
        dir=str(target.parent),
    )

    with contextlib.suppress(OSError):
        os.close(file_descriptor)

    return Path(temporary_name)


def _error_result(
    context: _CandidateContext,
    message: str,
    *,
    new_size: int = 0,
) -> ProcessResult:
    return ProcessResult(
        context.path,
        context.original_size,
        new_size,
        context.stdout,
        context.stderr,
        False,
        False,
        message,
    )


def _validate_candidate(
    context: _CandidateContext,
    options: MinifyOptions,
) -> int | ProcessResult:
    try:
        new_size = context.tmp_path.stat().st_size
    except OSError as exc:
        return _error_result(
            context,
            f"terser produced no output: {exc}",
        )

    if new_size == 0:
        return _error_result(
            context,
            "terser produced an empty file",
        )

    try:
        identical = _files_identical(
            context.target,
            context.tmp_path,
            context.original_size,
            new_size,
        )
    except OSError as exc:
        return _error_result(
            context,
            f"cannot compare files: {exc}",
            new_size=new_size,
        )

    if identical:
        return ProcessResult(
            context.path,
            context.original_size,
            context.original_size,
            context.stdout,
            context.stderr,
            False,
            True,
            None,
        )

    if new_size > context.original_size:
        return ProcessResult(
            context.path,
            context.original_size,
            new_size,
            context.stdout,
            context.stderr,
            True,
            False,
            None,
        )

    if context.original_size - new_size < options.min_savings:
        return ProcessResult(
            context.path,
            context.original_size,
            context.original_size,
            context.stdout,
            context.stderr,
            False,
            True,
            None,
        )

    return new_size


def _create_backup(target: Path) -> None:
    backup_path = target.with_name(f"{target.name}{BACKUP_SUFFIX}")
    shutil.copy2(target, backup_path)


def _commit_candidate(
    context: _CandidateContext,
    new_size: int,
    *,
    dry_run: bool,
    backup: bool,
) -> tuple[ProcessResult, bool]:
    if dry_run:
        return (
            ProcessResult(
                context.path,
                context.original_size,
                new_size,
                context.stdout,
                context.stderr,
                False,
                False,
                None,
            ),
            False,
        )

    try:
        context.tmp_path.chmod(context.original_mode)

        if backup:
            _create_backup(context.target)

        context.tmp_path.replace(context.target)
    except OSError as exc:
        return (
            ProcessResult(
                context.path,
                context.original_size,
                new_size,
                context.stdout,
                context.stderr,
                False,
                False,
                f"failed to replace original: {exc}",
            ),
            False,
        )

    return (
        ProcessResult(
            context.path,
            context.original_size,
            new_size,
            context.stdout,
            context.stderr,
            False,
            False,
            None,
        ),
        True,
    )


def _check_preconditions(
    path: Path,
    options: MinifyOptions,
) -> tuple[Path, int, int] | ProcessResult:
    try:
        target = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return ProcessResult(
            path,
            0,
            0,
            "",
            "",
            False,
            False,
            f"cannot resolve: {exc}",
        )

    try:
        target_stat = target.stat()
    except OSError as exc:
        return ProcessResult(
            path,
            0,
            0,
            "",
            "",
            False,
            False,
            f"cannot stat: {exc}",
        )

    original_size = target_stat.st_size

    if original_size == 0:
        return ProcessResult(
            path,
            0,
            0,
            "",
            "",
            False,
            True,
            None,
        )

    if options.skip_single_line:
        try:
            is_single_line = _is_single_line_file(target)
        except OSError as exc:
            return ProcessResult(
                path,
                original_size,
                0,
                "",
                "",
                False,
                False,
                f"cannot read: {exc}",
            )

        if is_single_line:
            return ProcessResult(
                path,
                original_size,
                original_size,
                "",
                "",
                False,
                True,
                None,
            )

    return target, original_size, stat.S_IMODE(target_stat.st_mode)


def process_file(
    path: Path,
    options: MinifyOptions = DEFAULT_MINIFY_OPTIONS,
) -> ProcessResult:
    """Minify one JavaScript file in place, safely and atomically."""
    precheck = _check_preconditions(path, options)

    if isinstance(precheck, ProcessResult):
        return precheck

    target, original_size, original_mode = precheck

    try:
        temporary_path = _create_temp_file(target)
    except OSError as exc:
        return ProcessResult(
            path,
            original_size,
            0,
            "",
            "",
            False,
            False,
            f"cannot create temp file: {exc}",
        )

    replaced = False

    try:
        command = _build_terser_command(target, temporary_path, options)
        completed, run_error = _run_terser(command, options.timeout)

        if completed is None:
            return ProcessResult(
                path,
                original_size,
                0,
                "",
                "",
                False,
                False,
                run_error,
            )

        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")

        if completed.returncode != TERSER_SUCCESS:
            return ProcessResult(
                path,
                original_size,
                0,
                stdout,
                stderr,
                False,
                False,
                f"terser exited with status {completed.returncode}",
            )

        context = _CandidateContext(
            path=path,
            target=target,
            tmp_path=temporary_path,
            original_size=original_size,
            original_mode=original_mode,
            stdout=stdout,
            stderr=stderr,
        )

        validated = _validate_candidate(context, options)

        if isinstance(validated, ProcessResult):
            return validated

        result, replaced = _commit_candidate(
            context,
            validated,
            dry_run=options.dry_run,
            backup=options.backup,
        )
        return result
    finally:
        if not replaced:
            with contextlib.suppress(OSError):
                temporary_path.unlink()


def _classify(result: ProcessResult, *, dry_run: bool) -> StatusTag:
    if result.error is not None:
        return "ERROR"
    if result.no_change:
        return "NOCHG"
    if result.skipped:
        return "SKIP"
    if dry_run:
        return "DRY-RUN"
    return "OK"


def _emit_terser_output(
    result: ProcessResult,
    console: Console,
    err_console: Console,
) -> None:
    if result.stdout:
        text = result.stdout
        if not text.endswith("\n"):
            text = f"{text}\n"
        console.out(text, end="")

    if result.stderr:
        text = result.stderr
        if not text.endswith("\n"):
            text = f"{text}\n"
        err_console.out(text, end="")


def _byte_delta(result: ProcessResult, tag: StatusTag) -> int:
    if tag in ("ERROR", "NOCHG", "SKIP"):
        return 0
    return result.new_size - result.original_size


def _delta_style(delta: int) -> str:
    if delta < 0:
        return "yellow"
    if delta > 0:
        return "green"
    return "dim"


def _truncate_filename(name: str, max_width: int) -> str:
    if max_width <= 1:
        return name[:max_width]
    if len(name) <= max_width:
        return name
    return f"{name[: max_width - 1]}…"


def _print_status(
    result: ProcessResult,
    tag: StatusTag,
    index: int,
    total: int,
    console: Console,
    err_console: Console,
    display: DisplayOptions,
) -> None:
    filename = result.path.name

    if tag == "ERROR":
        err_console.print(f"[{index}/{total}]    {filename}    [red]ERROR: {result.error}[/red]")
        return

    if display.summary_only:
        return

    delta = _byte_delta(result, tag)
    delta_label = format_delta(delta, human_bytes=display.human_bytes)
    style = _delta_style(delta)
    prefix = f"[{index}/{total}]"
    terminal_width = max(console.width, MIN_TERMINAL_WIDTH)
    reserved = len(prefix) + len(delta_label) + (COLUMN_GAP * 2)
    filename_width = max(MIN_FILENAME_WIDTH, terminal_width - reserved)
    fitted_name = _truncate_filename(filename, filename_width)

    line = Text()
    line.append(prefix)
    line.append(" " * COLUMN_GAP)
    line.append(fitted_name.ljust(filename_width))
    line.append(" " * COLUMN_GAP)
    line.append(delta_label.rjust(len(delta_label)), style=style)

    console.print(line, soft_wrap=True)


def _print_summary(
    counters: dict[StatusTag, int],
    net_delta_bytes: int,
    total_before: int,
    total_after: int,
    console: Console,
    *,
    human_bytes: bool = False,
) -> None:
    processed = sum(counters.values())

    if net_delta_bytes < 0:
        freed = abs(net_delta_bytes)
        freed_text = format_bytes(freed) if human_bytes else f"{freed} bytes"
        percent_text = f" ({freed / total_before * PERCENT_SCALE:.1f}%)" if total_before else ""
        space_line = f"[yellow]Total space freed: {freed_text}{percent_text}[/yellow]"
    elif net_delta_bytes > 0:
        added_text = format_bytes(net_delta_bytes) if human_bytes else f"{net_delta_bytes} bytes"
        percent_text = f" ({net_delta_bytes / total_before * PERCENT_SCALE:.1f}%)" if total_before else ""
        space_line = f"[green]Total space occupied more: {added_text}{percent_text}[/green]"
    else:
        space_line = "[dim]Total space change: 0 bytes[/dim]"

    lines: list[str] = [
        f"Processed : {processed}",
        f"Changed   : {counters['OK']}",
        f"Unchanged : {counters['NOCHG']}",
        f"Skipped   : {counters['SKIP']}",
        f"Errored   : {counters['ERROR']}",
    ]

    if counters["DRY-RUN"]:
        lines.insert(2, f"Dry-run   : {counters['DRY-RUN']}")

    lines.extend(("", space_line))

    if total_before > 0:
        lines.append(f"Size total: {format_bytes(total_before)} -> {format_bytes(total_after)}")

    console.print()
    console.print(
        Panel(
            "\n".join(lines),
            title="Run Summary",
            expand=False,
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    default_extensions = ", ".join(sorted(DEFAULT_SUFFIXES))

    parser = argparse.ArgumentParser(
        prog="terser-optimize",
        description=(
            "Minify JavaScript files in place using terser. With no paths, the current directory is walked recursively."
        ),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Files or directories to process (default: current directory).",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=_positive_int,
        default=MAX_WORKERS,
        metavar="N",
        help=(f"Number of parallel worker processes (default: {MAX_WORKERS})."),
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Process files sequentially; useful on low-memory Termux devices.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress terser's own stdout/stderr streams.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Suppress per-file non-error status lines and show only the summary.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print diagnostic details for skipped files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run and validate minification without modifying any files.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Keep a <file>.bak copy before replacing each changed file.",
    )
    parser.add_argument(
        "--human-bytes",
        action="store_true",
        help="Format byte deltas and totals with binary IEC units.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in Rich console output.",
    )
    parser.add_argument(
        "--max-errors",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Stop processing after N file errors.",
    )
    parser.add_argument(
        "--ext",
        action="append",
        type=_normalize_suffix,
        default=None,
        metavar="EXT",
        help=(f"File extension to include (repeatable; replaces defaults: {default_extensions})."),
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="Additional directory name to skip during traversal (repeatable).",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help=("Exclude files matching a glob pattern; matches either the relative path or filename (repeatable)."),
    )
    parser.add_argument(
        "--skip-min",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=("Skip *.min.<ext> files (default: on). Use --no-skip-min to process them."),
    )
    parser.add_argument(
        "-s",
        "--skip-single-line",
        action="store_true",
        default=True,
        help="Skip files with at most one line as likely already minified.",
    )
    parser.add_argument(
        "--min-savings",
        type=_non_negative_int,
        default=DEFAULT_MIN_SAVINGS_BYTES,
        metavar="BYTES",
        help=(f"Minimum byte reduction required to replace a file (default: {DEFAULT_MIN_SAVINGS_BYTES})."),
    )
    parser.add_argument(
        "--mangle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --mangle to terser (default: on). Use --no-mangle to skip.",
    )
    parser.add_argument(
        "--compress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=("Pass --compress to terser (default: on). Use --no-compress to skip."),
    )
    parser.add_argument(
        "--toplevel",
        action="store_true",
        help=("Pass --toplevel to terser. Requires --mangle or --compress to have any effect."),
    )
    parser.add_argument(
        "--module",
        action="store_true",
        help="Pass --module to terser (treat input as an ES module).",
    )
    parser.add_argument(
        "--ecma",
        type=int,
        default=None,
        metavar="N",
        help="Pass --ecma N to terser (for example: 5, 2015, 2020).",
    )
    parser.add_argument(
        "--comments",
        default=None,
        metavar="MODE",
        help="Pass --comments MODE to terser (for example: false, some, all).",
    )
    parser.add_argument(
        "--keep-fnames",
        action="store_true",
        default=True,
        help="Pass --keep-fnames to terser to preserve function names.",
    )
    parser.add_argument(
        "--keep-classnames",
        action="store_true",
        help="Pass --keep-classnames to terser to preserve class names.",
    )
    parser.add_argument(
        "--safari10",
        action="store_true",
        help="Pass --safari10 to terser to work around Safari 10/11 loop bugs.",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        metavar="FILE",
        help="Pass --config-file FILE to terser.",
    )
    parser.add_argument(
        "--terser",
        default="terser",
        metavar="PATH",
        help="Path or name of the terser executable (default: search PATH).",
    )
    parser.add_argument(
        "--terser-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="Extra raw argument forwarded to terser (repeatable).",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=(f"Per-file terser timeout in seconds (default: {DEFAULT_TIMEOUT:g}s)."),
    )

    return parser


def _iter_results(
    files: Sequence[Path],
    options: MinifyOptions,
    jobs: int,
) -> Iterator[ProcessResult]:
    from functools import partial
    if not files:
        return

    if len(files) == 1 or jobs == 1:
        for file_path in files:
            yield process_file(file_path, options)
        return

    worker = partial(process_file, options=options)

    with Pool(processes=jobs) as pool:
        yield from pool.imap_unordered(
            worker,
            iter(files),
            chunksize=IMAP_CHUNKSIZE,
        )


class _CLIArgs(argparse.Namespace):
    paths: list[str]
    jobs: int
    no_parallel: bool
    quiet: bool
    summary_only: bool
    verbose: bool
    dry_run: bool
    backup: bool
    human_bytes: bool
    no_color: bool
    max_errors: int | None
    ext: list[str] | None
    exclude_dir: list[str]
    exclude: list[str]
    skip_min: bool
    skip_single_line: bool
    min_savings: int
    mangle: bool
    compress: bool
    toplevel: bool
    module: bool
    ecma: int | None
    comments: str | None
    keep_fnames: bool
    keep_classnames: bool
    safari10: bool
    config_file: Path | None
    terser: str
    terser_arg: list[str]
    timeout: float


def _options_from_namespace(
    args: _CLIArgs,
) -> tuple[list[Path], DiscoveryOptions, MinifyOptions, DisplayOptions]:
    roots = [Path(path) for path in args.paths] if args.paths else [Path()]

    suffixes = frozenset(args.ext) if args.ext is not None else DEFAULT_SUFFIXES
    skip_dirs = SKIP_DIRS | frozenset(args.exclude_dir)

    discovery = DiscoveryOptions(
        suffixes=suffixes,
        skip_dirs=skip_dirs,
        exclude_globs=tuple(args.exclude),
        skip_minified=args.skip_min,
        verbose=args.verbose,
    )

    resolved_terser = shutil.which(args.terser) or args.terser

    minify = MinifyOptions(
        terser=resolved_terser,
        mangle=args.mangle,
        compress=args.compress,
        toplevel=args.toplevel,
        module=args.module,
        ecma=args.ecma,
        comments=args.comments,
        keep_fnames=args.keep_fnames,
        keep_classnames=args.keep_classnames,
        safari10=args.safari10,
        config_file=args.config_file,
        extra_args=tuple(args.terser_arg),
        timeout=args.timeout,
        min_savings=args.min_savings,
        skip_single_line=args.skip_single_line,
        dry_run=args.dry_run,
        backup=args.backup,
        verbose=args.verbose,
    )

    display = DisplayOptions(
        summary_only=args.summary_only,
        human_bytes=args.human_bytes,
        no_color=args.no_color,
        max_errors=args.max_errors,
    )

    return roots, discovery, minify, display


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(
        argv,
        namespace=_CLIArgs(),
    )

    roots, discovery_options, minify_options, display_options = _options_from_namespace(args)

    console = Console(no_color=display_options.no_color)
    err_console = Console(
        stderr=True,
        no_color=display_options.no_color,
    )

    discovered_files = list(
        iter_js_files(
            roots,
            discovery_options,
            err_console=err_console,
        )
    )

    total_files = len(discovered_files)
    counters: dict[StatusTag, int] = dict.fromkeys(STATUS_TAGS, 0)
    net_delta_bytes = 0
    total_before = 0
    total_after = 0
    jobs = 1 if args.no_parallel else args.jobs

    try:
        for index, result in enumerate(
            _iter_results(
                discovered_files,
                minify_options,
                jobs=jobs,
            ),
            start=1,
        ):
            if not args.quiet:
                _emit_terser_output(result, console, err_console)

            tag = _classify(
                result,
                dry_run=minify_options.dry_run,
            )
            counters[tag] += 1

            delta = _byte_delta(result, tag)
            net_delta_bytes += delta

            if tag in ("OK", "DRY-RUN"):
                total_before += result.original_size
                total_after += result.new_size
            elif tag == "SKIP":
                total_before += result.original_size
                total_after += result.original_size

            _print_status(
                result,
                tag,
                index,
                total_files,
                console,
                err_console,
                display_options,
            )

            if display_options.max_errors is not None and counters["ERROR"] >= display_options.max_errors:
                err_console.print(
                    f"[red]Stopping early after reaching --max-errors={display_options.max_errors}.[/red]"
                )
                break

    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted.[/yellow]")
        return EXIT_SIGINT

    _print_summary(
        counters,
        net_delta_bytes,
        total_before,
        total_after,
        console,
        human_bytes=display_options.human_bytes,
    )

    return EXIT_ERROR if counters["ERROR"] else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
