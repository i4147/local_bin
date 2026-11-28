#!/data/data/com.termux/files/usr/bin/env python
"""Audit Python source files for comments, docstrings, and bare string expressions."""

from __future__ import annotations
import argparse
import ast
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
import io
import json
import multiprocessing as mp
from pathlib import Path
import re
import sys
import tokenize

import libcst as cst
from libcst.metadata import PositionProvider
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table
from rich.text import Text


DEFAULT_WORKERS: int = 4

DEFAULT_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".eggs",
    "node_modules",
    "site-packages",
})

DEFAULT_ALLOW_COMMENT_PATTERNS: tuple[str, ...] = (
    r"^#\s*type:\s*ignore",
    r"^#\s*noqa\b",
    r"^#\s*pragma:\s*no\s*cover",
    r"^#\s*pylint:",
    r"^#\s*pyright:",
    r"^#\s*fmt:\s*(off|on|skip)",
)


class FindingKind(StrEnum):
    """Enumerate the supported FindingKind values."""

    COMMENT = "comment"
    DOCSTRING = "docstring"
    BARE_STRING = "bare_string"
    SHEBANG = "shebang"
    ENCODING = "encoding"
    ALLOWED_COMMENT = "allowed_comment"


@dataclass(frozen=True, slots=True)
class Finding:
    """Store lineno, col, kind, text, scope."""

    lineno: int
    col: int
    kind: FindingKind
    text: str
    scope: str | None = None

    @property
    def is_special(self) -> bool:
        """Return whether special."""
        return self.kind in {
            FindingKind.SHEBANG,
            FindingKind.ENCODING,
            FindingKind.ALLOWED_COMMENT,
        }

    def preview(self, max_len: int = 72) -> str:
        lines = self.text.strip().splitlines()
        first = lines[0] if lines else ""
        if len(first) > max_len:
            return first[: max_len - 1] + "…"
        if len(lines) > 1:
            return f"{first} (+{len(lines) - 1} lines)"
        return first


@dataclass(slots=True)
class FileReport:
    """Store path, findings, errors."""

    path: Path
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def actionable_findings(self) -> list[Finding]:
        """Return the actionable findings."""
        return [f for f in self.findings if not f.is_special]

    @property
    def special_findings(self) -> list[Finding]:
        """Return the special findings."""
        return [f for f in self.findings if f.is_special]


@dataclass(frozen=True, slots=True)
class ScanConfig:
    """Store ignore_comments, ignore_docstrings, ignore_bare_strings, ignore_module_docstrings, allow_patterns."""

    ignore_comments: bool = False
    ignore_docstrings: bool = False
    ignore_bare_strings: bool = False
    ignore_module_docstrings: bool = False
    allow_patterns: tuple[str, ...] = ()


def _classify_comment(
    text: str,
    lineno: int,
    compiled_patterns: tuple[re.Pattern[str], ...],
) -> FindingKind:
    if lineno == 1 and text.startswith("#!"):
        return FindingKind.SHEBANG
    if lineno <= 2 and ("coding:" in text[:30] or "coding=" in text[:30] or "-*-" in text[:25]):
        return FindingKind.ENCODING
    if any(pattern.search(text) for pattern in compiled_patterns):
        return FindingKind.ALLOWED_COMMENT
    return FindingKind.COMMENT


def _collect_comments(raw: bytes, report: FileReport, config: ScanConfig) -> None:
    compiled_patterns = tuple(re.compile(p) for p in config.allow_patterns)
    try:
        for tok in tokenize.tokenize(io.BytesIO(raw).readline):
            if tok.type == tokenize.COMMENT:
                report.findings.append(
                    Finding(
                        lineno=tok.start[0],
                        col=tok.start[1],
                        kind=_classify_comment(tok.string, tok.start[0], compiled_patterns),
                        text=tok.string,
                    )
                )
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError) as exc:
        report.errors.append(f"Tokenize error: {exc}")


def _first_expr(body: cst.BaseSuite | Sequence[cst.BaseStatement]) -> cst.Expr | None:
    """Return the first statement of a suite if it is an expression statement."""
    if isinstance(body, cst.SimpleStatementSuite):
        small = body.body
        return small[0] if small and isinstance(small[0], cst.Expr) else None

    if isinstance(body, cst.IndentedBlock):
        statements = body.body
    elif isinstance(body, cst.BaseSuite):
        return None
    else:
        statements = body
    if not statements:
        return None
    first = statements[0]
    if isinstance(first, cst.SimpleStatementLine) and first.body and isinstance(first.body[0], cst.Expr):
        return first.body[0]
    return None


class _StringAuditor(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, config: ScanConfig) -> None:
        super().__init__()
        self.config = config
        self.findings: list[Finding] = []
        self._docstring_ids: set[int] = set()

    @staticmethod
    def _string_value(expr: cst.BaseExpression) -> str | None:
        if not isinstance(expr, (cst.SimpleString, cst.ConcatenatedString)):
            return None
        try:
            # Wrap in parentheses so multi-line implicit concatenation parses.
            value = ast.literal_eval(f"({cst.Module([]).code_for_node(expr)})")
        except (ValueError, SyntaxError):
            return None
        return value if isinstance(value, str) else None

    def _handle_docstring(self, expr: cst.Expr | None, scope: str, is_module: bool = False) -> None:
        if expr is None:
            return
        text = self._string_value(expr.value)
        if text is None:
            return
        self._docstring_ids.add(id(expr))
        if self.config.ignore_docstrings or (is_module and self.config.ignore_module_docstrings):
            return
        pos = self.get_metadata(PositionProvider, expr)
        self.findings.append(
            Finding(
                lineno=pos.start.line,
                col=pos.start.column,
                kind=FindingKind.DOCSTRING,
                text=text,
                scope=scope,
            )
        )

    def visit_Module(self, node: cst.Module) -> None:
        self._handle_docstring(_first_expr(node.body), "module <module>", is_module=True)

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self._handle_docstring(_first_expr(node.body), f"class {node.name.value}")

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._handle_docstring(_first_expr(node.body), f"def {node.name.value}")

    def visit_Expr(self, node: cst.Expr) -> bool:
        if id(node) in self._docstring_ids or self.config.ignore_bare_strings:
            return True
        text = self._string_value(node.value)
        if text is None:
            return True
        pos = self.get_metadata(PositionProvider, node)
        self.findings.append(
            Finding(
                lineno=pos.start.line,
                col=pos.start.column,
                kind=FindingKind.BARE_STRING,
                text=text,
            )
        )
        return True


def _collect_strings(raw: bytes, path: Path, report: FileReport, config: ScanConfig) -> None:
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
        module = cst.parse_module(source)
    except (SyntaxError, UnicodeDecodeError, LookupError, cst.ParserSyntaxError) as exc:
        report.errors.append(f"CST parse error in {path.name}: {exc}")
        return

    auditor = _StringAuditor(config)
    cst.MetadataWrapper(module, unsafe_skip_copy=True).visit(auditor)
    report.findings.extend(auditor.findings)


def scan_file(path: Path, config: ScanConfig) -> FileReport:
    """Scan file."""
    report = FileReport(path=path)

    try:
        raw = path.read_bytes()
    except OSError as exc:
        report.errors.append(f"OS read error: {exc}")
        return report

    if not config.ignore_comments:
        _collect_comments(raw, report, config)

    if not (config.ignore_docstrings and config.ignore_bare_strings):
        _collect_strings(raw, path, report, config)

    report.findings.sort(key=lambda f: (f.lineno, f.col))
    return report


def scan_all(
    files: Sequence[Path],
    config: ScanConfig,
    workers: int,
    show_progress: bool,
    err_console: Console,
) -> dict[Path, FileReport]:
    """Scan all."""
    results: dict[Path, FileReport] = {}

    if workers == 1 or len(files) == 1:
        for file_path in files:
            results[file_path] = scan_file(file_path, config)
        return results

    worker = partial(scan_file, config=config)
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=err_console,
        transient=True,
        disable=not show_progress,
    )
    with progress, mp.Pool(processes=workers) as pool:
        task = progress.add_task("Scanning Python files...", total=len(files))
        for report in pool.imap_unordered(worker, files):
            results[report.path] = report
            progress.advance(task)
    return results


def collect_python_files(
    inputs: Sequence[Path],
    skip_dirs: frozenset[str],
    exclude_globs: Sequence[str],
) -> list[Path]:
    """Collect python files."""
    targets = list(inputs) if inputs else [Path(".")]
    collected: set[Path] = set()

    def is_excluded(candidate: Path) -> bool:
        if any(part in skip_dirs for part in candidate.parts):
            return True
        return any(candidate.match(pat) for pat in exclude_globs)

    for target in targets:
        if target.is_file():
            if target.suffix == ".py" and not is_excluded(target):
                collected.add(target)
        elif target.is_dir():
            collected.update(p for p in target.rglob("*.py") if not is_excluded(p))

    return sorted(collected)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse args."""
    parser = argparse.ArgumentParser(
        prog="pyaudit",
        description="Audit Python files for unstripped comments, docstrings, and bare strings.",
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="Files or directories to scan (defaults to current directory recursively).",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of parallel worker processes (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--skip-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="Additional directory name to skip (repeatable).",
    )
    parser.add_argument(
        "--no-default-skips",
        action="store_true",
        help="Do not skip default directories (.venv, __pycache__, .git, etc.).",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Exclude paths matching glob pattern (repeatable).",
    )
    parser.add_argument("--no-comments", action="store_true", help="Ignore regular comments.")
    parser.add_argument(
        "--no-docstrings",
        action="store_true",
        help="Ignore module, class, and function docstrings.",
    )
    parser.add_argument(
        "--no-module-docstrings",
        action="store_true",
        help="Ignore top-level module docstrings only.",
    )
    parser.add_argument(
        "--no-bare-strings",
        action="store_true",
        help="Ignore standalone bare string expressions.",
    )
    parser.add_argument(
        "--allow-directives",
        action="store_true",
        help="Treat linter/type directives (# noqa, # type: ignore, # pragma, # fmt) as allowed.",
    )
    parser.add_argument(
        "--allow-pattern",
        action="append",
        default=[],
        metavar="REGEX",
        help="Regex pattern of comments to treat as allowed (repeatable).",
    )
    parser.add_argument(
        "--show-special",
        action="store_true",
        help="Display special/allowed comments (shebangs, encoding, directives) in output.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only print summary and errors (suppress per-file findings).",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit machine-readable JSON output to stdout.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored ANSI terminal output.",
    )
    return parser.parse_args(argv)


def _kind_badge(kind: FindingKind) -> Text:
    styles: dict[FindingKind, tuple[str, str]] = {
        FindingKind.COMMENT: ("COMMENT", "bold yellow"),
        FindingKind.DOCSTRING: ("DOCSTRING", "bold magenta"),
        FindingKind.BARE_STRING: ("BARE STR", "bold red"),
        FindingKind.SHEBANG: ("SHEBANG", "dim cyan"),
        FindingKind.ENCODING: ("ENCODING", "dim cyan"),
        FindingKind.ALLOWED_COMMENT: ("ALLOWED", "dim green"),
    }
    label, style = styles[kind]
    return Text(f"{label:<9}", style=style)


def render_rich_reports(
    reports: Sequence[FileReport],
    console: Console,
    show_special: bool,
    quiet: bool,
) -> None:
    """Render rich reports."""
    dirty_reports = [r for r in reports if r.errors or r.actionable_findings or (show_special and r.special_findings)]

    if not quiet:
        for report in dirty_reports:
            console.print()
            console.print(Text(f" {report.path} ", style="bold white on blue"))
            for err in report.errors:
                console.print(f"  [bold red]ERROR:[/bold red] {err}")

            items = report.findings if show_special else report.actionable_findings
            if items:
                table = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False)
                table.add_column("loc", style="cyan", no_wrap=True)
                table.add_column("kind", no_wrap=True)
                table.add_column("scope", style="dim italic", no_wrap=True)
                table.add_column("snippet", style="white")
                for item in items:
                    table.add_row(
                        f"  L{item.lineno}:{item.col}",
                        _kind_badge(item.kind),
                        item.scope or "",
                        repr(item.preview()),
                    )
                console.print(table)

    total_files = len(reports)
    files_with_issues = sum(1 for r in reports if r.actionable_findings or r.errors)
    total_comments = sum(1 for r in reports for f in r.actionable_findings if f.kind == FindingKind.COMMENT)
    total_docstrings = sum(1 for r in reports for f in r.actionable_findings if f.kind == FindingKind.DOCSTRING)
    total_bare = sum(1 for r in reports for f in r.actionable_findings if f.kind == FindingKind.BARE_STRING)
    total_errors = sum(len(r.errors) for r in reports)
    total_actionable = total_comments + total_docstrings + total_bare

    console.print()
    if total_actionable == 0 and total_errors == 0:
        console.print(
            Panel.fit(
                f"[bold green]✔ Clean[/bold green] — Checked [bold]{total_files}[/bold] file(s); "
                "no leftover comments, docstrings, or bare strings found.",
                border_style="green",
            )
        )
        return

    summary = (
        f"[bold red]✘ Found {total_actionable} item(s)[/bold red] across "
        f"[bold]{files_with_issues}/{total_files}[/bold] file(s)\n"
        f"  • Comments:     [yellow]{total_comments}[/yellow]\n"
        f"  • Docstrings:   [magenta]{total_docstrings}[/magenta]\n"
        f"  • Bare strings: [red]{total_bare}[/red]"
    )
    if total_errors:
        summary += f"\n  • Parse errors: [bold red]{total_errors}[/bold red]"
    console.print(Panel.fit(summary, title="Audit Summary", border_style="red"))


def emit_json(reports: Sequence[FileReport], show_special: bool) -> None:
    """Emit json."""
    payload = {
        "files_checked": len(reports),
        "files_with_issues": sum(1 for r in reports if r.actionable_findings or r.errors),
        "total_issues": sum(len(r.actionable_findings) for r in reports),
        "results": [
            {
                "path": str(r.path),
                "errors": r.errors,
                "findings": [
                    {
                        "line": f.lineno,
                        "col": f.col,
                        "kind": f.kind.value,
                        "scope": f.scope,
                        "text": f.text,
                    }
                    for f in (r.findings if show_special else r.actionable_findings)
                ],
            }
            for r in reports
            if r.errors or (r.findings if show_special else r.actionable_findings)
        ],
    }
    print(json.dumps(payload, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    args = parse_args(argv)

    logger.remove()
    logger.add(
        sys.stderr,
        level="WARNING" if args.quiet else "INFO",
        colorize=not args.no_color,
    )

    console = Console(no_color=args.no_color)
    err_console = Console(stderr=True, no_color=args.no_color)

    missing = [p for p in args.inputs if not p.exists()]
    if missing:
        for p in missing:
            logger.error("Path does not exist: {}", p)
        return 2

    base_skips = frozenset() if args.no_default_skips else DEFAULT_SKIP_DIRS
    skip_dirs = base_skips | frozenset(args.skip_dir)

    allow_patterns = list(args.allow_pattern)
    if args.allow_directives:
        allow_patterns.extend(DEFAULT_ALLOW_COMMENT_PATTERNS)

    config = ScanConfig(
        ignore_comments=args.no_comments,
        ignore_docstrings=args.no_docstrings,
        ignore_bare_strings=args.no_bare_strings,
        ignore_module_docstrings=args.no_module_docstrings,
        allow_patterns=tuple(allow_patterns),
    )

    files = collect_python_files(args.inputs, skip_dirs=skip_dirs, exclude_globs=args.exclude)
    if not files:
        if args.json_output:
            emit_json([], show_special=args.show_special)
        else:
            console.print("[yellow]No Python files found to inspect.[/yellow]")
        return 0

    workers = max(1, args.workers)
    logger.info("Scanning {} file(s) with {} worker(s)", len(files), workers)

    results = scan_all(
        files,
        config=config,
        workers=workers,
        show_progress=not (args.json_output or args.quiet),
        err_console=err_console,
    )
    ordered_reports = [results[f] for f in files]

    for report in ordered_reports:
        for err in report.errors:
            logger.debug("{}: {}", report.path, err)

    if args.json_output:
        emit_json(ordered_reports, show_special=args.show_special)
    else:
        render_rich_reports(
            ordered_reports,
            console=console,
            show_special=args.show_special,
            quiet=args.quiet,
        )

    has_issues = any(r.actionable_findings or r.errors for r in ordered_reports)
    return 1 if has_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
