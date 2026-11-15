#!/data/data/com.termux/files/usr/bin/env python

"""Strip comments, docstrings, and annotations from Python files.

Accepts multiple files or directories, recursively scans the current directory
when no inputs are supplied, and reports per-file bytes reduced with Rich.
"""

from __future__ import annotations
import ast
import codecs
from concurrent.futures import ProcessPoolExecutor
import dataclasses
import difflib
import enum
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import textwrap
from typing import TYPE_CHECKING, Any, Final, TypeVar, cast

from atomicio import atomic_write as _atomic_write
from dh import MAX_WORKERS, SKIP_DIRS
import libcst as cst
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.text import Text
import typer


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


# PEP 695 generic syntax (``def on_leave[N: cst.CSTNode]``) needs Python 3.12+;
# a TypeVar bound is equivalent and works on 3.11 too.
N = TypeVar("N", bound=cst.CSTNode)

VERSION: Final = "2.0.0"
FEATURE_VERSION: Final = (3, 12)
_COOKIE_RE: Final = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
_DIRECTIVE_RE: Final = re.compile(r"^#\s*(?:type|fmt)\s*:")
_SUFFIXES: Final = frozenset({".py", ".pyi", ".pyw"})
_TRIVIAL: Final = (ast.Name, ast.Constant, ast.Attribute)


class Status(enum.StrEnum):
    "Enumerate the supported Status values."

    MODIFIED = "modified"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclasses.dataclass(frozen=True, slots=True)
class Mode:
    comments: bool
    docstrings: bool
    annotations: bool


@dataclasses.dataclass(slots=True)
class Counts:
    comments: int = 0
    docstrings: int = 0
    annotations: int = 0


@dataclasses.dataclass(slots=True)
class FileResult:
    path: Path
    status: Status
    original_size: int = 0
    new_size: int = 0
    counts: Counts = dataclasses.field(default_factory=Counts)
    diff: str = ""
    reason: str = ""
    written: bool = False

    @property
    def bytes_saved(self) -> int:
        return self.original_size - self.new_size


@dataclasses.dataclass(frozen=True, slots=True)
class Job:
    path: Path
    mode: Mode
    write: bool
    want_diff: bool
    backup: bool
    backup_dir: Path | None


@dataclasses.dataclass(frozen=True, slots=True)
class Options:
    paths: tuple[Path, ...]
    mode: Mode
    stats: bool
    dry_run: bool
    diff: bool
    check: bool
    backup: bool
    backup_dir: Path | None
    quiet: bool
    verbose: bool
    includes: tuple[str, ...]
    excludes: tuple[str, ...]
    recursive: bool
    json_output: bool

    @property
    def write(self) -> bool:
        return not (self.dry_run or self.check or self.diff)


@dataclasses.dataclass(slots=True)
class Summary:
    scanned: int = 0
    modified: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    saved: int = 0


def _is_trivial(node: ast.stmt) -> bool:
    if isinstance(node, ast.Expr):
        return isinstance(node.value, _TRIVIAL)
    if isinstance(node, ast.AnnAssign):
        return node.value is None and isinstance(node.annotation, _TRIVIAL)
    return False


def _looks_like_code(values: Sequence[str]) -> bool:
    """Return True if the given comment block parses as meaningful Python code."""
    lines: list[str] = []
    for value in values:
        body = value[1:]
        lines.append(body.removeprefix(" "))
    source = textwrap.dedent("\n".join(lines)).strip("\n")
    if not source.strip():
        return False
    try:
        tree = ast.parse(source, feature_version=FEATURE_VERSION)
    except (SyntaxError, ValueError, RecursionError):
        return False
    return any(not _is_trivial(statement) for statement in tree.body)


class Stripper(cst.CSTTransformer):
    def __init__(self, mode: Mode) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__()
        self._mode = mode
        self._doc_suites: set[int] = set()
        self.counts = Counts()

    # ---- comment filtering (own-line comments) -------------------------

    def on_leave(self, original_node: N, updated_node: N) -> N | cst.RemovalSentinel | cst.FlattenSentinel[N]:
        result = super().on_leave(original_node, updated_node)
        if not self._mode.comments or not isinstance(result, cst.CSTNode):
            return result
        header_owner = isinstance(result, cst.Module)
        changes: dict[str, tuple[cst.EmptyLine, ...]] = {}
        for field in dataclasses.fields(cast("Any", result)):
            value = getattr(result, field.name)
            if not isinstance(value, tuple | list) or not value:
                continue
            if not all(isinstance(item, cst.EmptyLine) for item in value):
                continue
            filtered = self._filter_lines(tuple(value), header=header_owner and field.name == "header")
            if len(filtered) != len(value):
                changes[field.name] = filtered
        if not changes:
            return result
        return result.with_changes(**changes)

    @staticmethod
    def _is_special(index: int, value: str, header: bool) -> bool:
        # Coding cookies are always preserved.
        if header and index < 2 and _COOKIE_RE.match(value):
            return True
        # Shebangs are always preserved.
        if header and index == 0 and value.startswith("#!"):
            return True
        # type: /fmt: directives are always preserved.
        return _DIRECTIVE_RE.match(value) is not None

    def _filter_lines(self, lines: tuple[cst.EmptyLine, ...], *, header: bool) -> tuple[cst.EmptyLine, ...]:
        if all(line.comment is None for line in lines):
            return lines
        kept: list[cst.EmptyLine] = []
        skipping_blanks = False
        index = 0
        while index < len(lines):
            line = lines[index]
            comment = line.comment
            if comment is None:
                if not skipping_blanks:
                    kept.append(line)
                index += 1
                continue
            if self._is_special(index, comment.value, header):
                kept.append(line)
                skipping_blanks = False
                index += 1
                continue
            end = index
            while end < len(lines):
                candidate = lines[end].comment
                if candidate is None or self._is_special(end, candidate.value, header):
                    break
                end += 1
            run = lines[index:end]
            values = [item.comment.value for item in run if item.comment is not None]
            # Commented-out code is preserved unconditionally (even under -a).
            if _looks_like_code(values):
                kept.extend(run)
                skipping_blanks = False
            else:
                self.counts.comments += len(run)
                skipping_blanks = (not kept and header) or (bool(kept) and kept[-1].comment is None)
            index = end
        return tuple(kept)

    def leave_TrailingWhitespace(
        self,
        original_node: cst.TrailingWhitespace,
        updated_node: cst.TrailingWhitespace,
    ) -> cst.TrailingWhitespace:
        if not self._mode.comments:
            return updated_node
        comment = updated_node.comment
        if comment is None:
            return updated_node
        if _DIRECTIVE_RE.match(comment.value):
            return updated_node
        self.counts.comments += 1
        return updated_node.with_changes(whitespace=cst.SimpleWhitespace(""), comment=None)

    # ---- annotations ---------------------------------------------------

    def leave_Param(self, original_node: cst.Param, updated_node: cst.Param) -> cst.Param:
        if not self._mode.annotations or updated_node.annotation is None:
            return updated_node
        self.counts.annotations += 1
        if updated_node.default is None:
            return updated_node.with_changes(annotation=None)
        return updated_node.with_changes(annotation=None, equal=cst.MaybeSentinel.DEFAULT)

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        if not self._mode.annotations or updated_node.returns is None:
            return updated_node
        self.counts.annotations += 1
        return updated_node.with_changes(returns=None)

    # ---- docstrings ----------------------------------------------------

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        self._register(node.body)
        return True

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        self._register(node.body)
        return True

    def _register(self, body: cst.BaseSuite) -> None:
        if self._mode.docstrings:
            self._doc_suites.add(id(body))

    @staticmethod
    def _is_docstring(small: cst.BaseSmallStatement) -> bool:
        if not isinstance(small, cst.Expr):
            return False
        value = small.value
        if isinstance(value, cst.SimpleString | cst.ConcatenatedString):
            return isinstance(value.evaluated_value, str)
        return False

    def _rewrite_smalls(
        self,
        smalls: Sequence[cst.BaseSmallStatement],
        *,
        drop_docstring: bool,
    ) -> tuple[list[cst.BaseSmallStatement], bool]:
        out: list[cst.BaseSmallStatement] = []
        changed = False
        for index, small in enumerate(smalls):
            if drop_docstring and index == 0 and self._is_docstring(small):
                self.counts.docstrings += 1
                changed = True
                continue
            if self._mode.annotations and isinstance(small, cst.AnnAssign):
                self.counts.annotations += 1
                changed = True
                if small.value is not None:
                    out.append(
                        cst.Assign(
                            targets=[cst.AssignTarget(target=small.target)],
                            value=small.value,
                            semicolon=small.semicolon,
                        )
                    )
                continue
            out.append(small)
        if changed and out:
            out[-1] = out[-1].with_changes(semicolon=cst.MaybeSentinel.DEFAULT)
        return out, changed

    def _rewrite(
        self,
        body: Sequence[cst.BaseStatement],
        *,
        drop_docstring: bool,
    ) -> tuple[list[cst.BaseStatement], list[cst.EmptyLine]]:
        out: list[cst.BaseStatement] = []
        pending: list[cst.EmptyLine] = []
        for position, statement in enumerate(body):
            current: cst.BaseStatement = statement
            if isinstance(statement, cst.SimpleStatementLine):
                smalls, changed = self._rewrite_smalls(
                    statement.body,
                    drop_docstring=drop_docstring and position == 0,
                )
                if changed:
                    if not smalls:
                        pending.extend(statement.leading_lines)
                        continue
                    current = statement.with_changes(body=smalls)
            if pending and isinstance(current, cst.SimpleStatementLine | cst.BaseCompoundStatement):
                current = current.with_changes(leading_lines=[*pending, *current.leading_lines])
                pending = []
            out.append(current)
        return out, pending

    def leave_IndentedBlock(
        self, original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
    ) -> cst.IndentedBlock:
        drop = id(original_node) in self._doc_suites
        if not drop and not self._mode.annotations:
            return updated_node
        body, pending = self._rewrite(updated_node.body, drop_docstring=drop)
        if not body:
            body = [cst.SimpleStatementLine(body=[cst.Pass()], leading_lines=pending)]
            pending = []
        return updated_node.with_changes(body=body, footer=[*pending, *updated_node.footer])

    def leave_SimpleStatementSuite(
        self,
        original_node: cst.SimpleStatementSuite,
        updated_node: cst.SimpleStatementSuite,
    ) -> cst.SimpleStatementSuite:
        drop = id(original_node) in self._doc_suites
        smalls, changed = self._rewrite_smalls(updated_node.body, drop_docstring=drop)
        if not changed:
            return updated_node
        return updated_node.with_changes(body=smalls or [cst.Pass()])

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        # Module docstrings are always preserved; only annotations are handled here.
        if not self._mode.annotations:
            return updated_node
        body, pending = self._rewrite(updated_node.body, drop_docstring=False)
        return updated_node.with_changes(body=body, footer=[*pending, *updated_node.footer])


# ---- file IO helpers ---------------------------------------------------


def _decode(payload: bytes) -> tuple[str, str] | None:
    for encoding in ("utf-8", "cp1252"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None


def _backup_target(path: Path, backup_dir: Path | None) -> Path:
    if backup_dir is None:
        return path.with_name(f"{path.name}.bak")
    resolved = path.resolve()
    return backup_dir / resolved.relative_to(resolved.anchor)


def _make_backup(path: Path, backup_dir: Path | None) -> None:
    target = _backup_target(path, backup_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def _build_diff(path: Path, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
        )
    )


# ---- per-file processing ------------------------------------------------


def _process(job: Job) -> FileResult:
    path = job.path
    if job.write and not os.access(path, os.W_OK):
        return FileResult(path=path, status=Status.SKIPPED, reason="read-only file")
    info = path.stat()
    raw = path.read_bytes()
    size = len(raw)
    if not raw.strip():
        return FileResult(
            path=path,
            status=Status.UNCHANGED,
            original_size=size,
            new_size=size,
            reason="empty file",
        )
    if b"\x00" in raw:
        return FileResult(
            path=path,
            status=Status.SKIPPED,
            original_size=size,
            new_size=size,
            reason="binary content",
        )
    bom = raw.startswith(codecs.BOM_UTF8)
    payload = raw[len(codecs.BOM_UTF8) :] if bom else raw
    decoded = _decode(payload)
    if decoded is None:
        return FileResult(
            path=path,
            status=Status.SKIPPED,
            original_size=size,
            new_size=size,
            reason="not decodable as utf-8 or cp1252",
        )
    text, encoding = decoded
    try:
        module = cst.parse_module(text)
    except cst.ParserSyntaxError as exc:
        return FileResult(
            path=path,
            status=Status.FAILED,
            original_size=size,
            new_size=size,
            reason=f"parse error at line {exc.raw_line}: {exc.message}",
        )
    stripper = Stripper(job.mode)
    new_text = module.visit(stripper).code
    if new_text == text:
        return FileResult(path=path, status=Status.UNCHANGED, original_size=size, new_size=size)
    try:
        ast.parse(new_text, feature_version=FEATURE_VERSION)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return FileResult(
            path=path,
            status=Status.FAILED,
            original_size=size,
            new_size=size,
            reason=f"validation failed: {exc}",
        )
    output = (codecs.BOM_UTF8 if bom else b"") + new_text.encode(encoding)
    diff = _build_diff(path, text, new_text) if job.want_diff else ""
    written = False
    if job.write:
        if job.backup:
            _make_backup(path, job.backup_dir)
        _atomic_write(path, output, info.st_mode)
        written = True
    return FileResult(
        path=path,
        status=Status.MODIFIED,
        original_size=size,
        new_size=len(output),
        counts=stripper.counts,
        diff=diff,
        written=written,
    )


def process_file(job: Job) -> FileResult:
    "Process file."
    try:
        return _process(job)
    except Exception as exc:
        return FileResult(
            path=job.path,
            status=Status.FAILED,
            reason=f"{type(exc).__name__}: {exc}",
        )


# ---- parallel runner ---------------------------------------------------


def _init_worker() -> None:
    logger.remove()
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass


def run_jobs(jobs: Sequence[Job], workers: int) -> list[FileResult]:
    "Run jobs."
    if workers <= 1 or len(jobs) <= 1:
        return [process_file(job) for job in jobs]
    processes = min(workers, len(jobs))
    chunk = max(1, len(jobs) // (processes * 4))
    try:
        with ProcessPoolExecutor(max_workers=processes, initializer=_init_worker) as executor:
            return list(executor.map(process_file, jobs, chunksize=chunk))
    except (OSError, ImportError, ValueError) as exc:
        logger.warning("worker pool unavailable ({}); running serially", exc)
        return [process_file(job) for job in jobs]


# ---- discovery ---------------------------------------------------------


def _matches(path: Path, patterns: Sequence[str]) -> bool:
    posix = path.as_posix()
    return any(
        fnmatch.fnmatchcase(posix, pattern) or any(fnmatch.fnmatchcase(part, pattern) for part in path.parts)
        for pattern in patterns
    )


def _has_python_shebang(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            first = handle.readline(256)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def _wanted(path: Path, relative: Path, includes: Sequence[str]) -> bool:
    if includes:
        return _matches(relative, includes)
    if path.suffix in _SUFFIXES:
        return True
    return path.suffix == "" and _has_python_shebang(path)


def _walk(directory: Path, recursive: bool) -> Iterator[Path]:
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        logger.warning("cannot list {}: {}", directory.name or ".", exc)
        return
    for entry in entries:
        if entry.is_symlink():
            continue
        if entry.is_dir():
            skip = entry.name in SKIP_DIRS or entry.name.endswith(".egg-info")
            if recursive and not skip:
                yield from _walk(entry, recursive)
        elif entry.is_file():
            yield entry


def discover(options: Options) -> tuple[list[Path], bool]:
    found: dict[Path, Path] = {}
    missing = False
    backup_root = options.backup_dir.resolve() if options.backup_dir else None
    for root in options.paths:
        if root.is_dir():
            candidates: Iterator[Path] = _walk(root, options.recursive)
            explicit = False
        elif root.is_file():
            candidates = iter((root,))
            explicit = True
        else:
            logger.error("path not found: {}", root.name or ".")
            missing = True
            continue
        for candidate in candidates:
            relative = candidate if explicit else candidate.relative_to(root)
            if options.excludes and _matches(relative, options.excludes):
                logger.debug("excluded {}", candidate)
                continue
            if not explicit and not _wanted(candidate, relative, options.includes):
                continue
            resolved = candidate.resolve()
            if backup_root is not None and resolved.is_relative_to(backup_root):
                continue
            found.setdefault(resolved, candidate)
    ordered = sorted(found.values(), key=lambda item: item.as_posix())
    return ordered, missing


# ---- reporting ---------------------------------------------------------


def summarize(results: Sequence[FileResult]) -> Summary:
    summary = Summary(scanned=len(results))
    for result in results:
        match result.status:
            case Status.MODIFIED:
                summary.modified += 1
                summary.saved += result.bytes_saved
            case Status.UNCHANGED:
                summary.unchanged += 1
            case Status.SKIPPED:
                summary.skipped += 1
            case Status.FAILED:
                summary.failed += 1
    return summary


def log_results(results: Sequence[FileResult]) -> None:
    for result in results:
        match result.status:
            case Status.FAILED:
                logger.error("failed {}: {}", result.path.name, result.reason)
            case Status.SKIPPED:
                logger.warning("skipped {}: {}", result.path.name, result.reason)
            case Status.UNCHANGED:
                logger.debug("unchanged {}", result.path.name)
            case Status.MODIFIED:
                logger.debug(
                    "modified {} (comments={} docstrings={} annotations={})",
                    result.path.name,
                    result.counts.comments,
                    result.counts.docstrings,
                    result.counts.annotations,
                )


def render_text(results: Sequence[FileResult], summary: Summary, options: Options) -> None:
    """Render basename-only per-file byte savings and a total with Rich."""
    console = Console()
    total = len(results)
    digits = max(1, len(str(total)))
    index_width = digits * 2 + 3  # brackets, slash, and room for the counter
    byte_width = max(5, len(f"{max((abs(result.bytes_saved) for result in results), default=0):,}"))
    filename_width = max(1, console.width - index_width - byte_width - 6)
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(width=index_width, no_wrap=True)
    table.add_column(ratio=1, max_width=filename_width, no_wrap=True, overflow="ellipsis")
    table.add_column(justify="right", width=byte_width, no_wrap=True)

    for index, result in enumerate(results, start=1):
        counter = Text(f"[{index}/{total}]", style="dim")
        filename = Text(result.path.name, no_wrap=True, overflow="ellipsis")
        saved = result.bytes_saved if result.status is Status.MODIFIED else 0
        amount = Text(f"{saved:,}", style="dim" if saved == 0 else "green")
        table.add_row(counter, filename, amount)

    if not options.quiet:
        console.print(table)
    if options.stats:
        console.print(
            f"Scanned {summary.scanned}: {summary.modified} modified, "
            f"{summary.unchanged} unchanged, {summary.skipped} skipped, {summary.failed} failed"
        )
    if options.diff:
        for result in results:
            if result.diff:
                console.print(result.diff, markup=False, highlight=False, end="")
    if not options.write:
        console.print("[dim](dry run; no files written)[/dim]")
    qualifier = "freed" if options.write else "that would be freed"
    console.print(f"Total space {qualifier}: {summary.saved:,} bytes")


def render_json(results: Sequence[FileResult], summary: Summary, options: Options) -> None:
    "Render json."
    entries: list[dict[str, object]] = []
    for result in results:
        entry: dict[str, object] = {
            "path": result.path.name,
            "status": result.status.value,
            "original_size": result.original_size,
            "new_size": result.new_size,
            "bytes_saved": result.bytes_saved,
            "comments": result.counts.comments,
            "docstrings": result.counts.docstrings,
            "annotations": result.counts.annotations,
            "written": result.written,
            "reason": result.reason,
        }
        if options.diff:
            entry["diff"] = result.diff
        entries.append(entry)
    payload = {
        "version": VERSION,
        "results": entries,
        "summary": dataclasses.asdict(summary),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def exit_code(summary: Summary, options: Options, missing: bool) -> int:
    if summary.failed or missing:
        return 2
    if options.check and summary.modified:
        return 1
    return 0


# ---- CLI ---------------------------------------------------------------


def configure_logging(options: Options) -> None:
    """Configure Loguru verbosity for CLI status and error messages."""
    logger.remove()
    level = "ERROR" if options.quiet else "DEBUG" if options.verbose else "INFO"
    logger.add(sys.stderr, level=level, format="<level>{level: <8}</level> {message}")


app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help=(
        "Strip comments, docstrings, and annotations from Python files. "
        "With no paths, process Python files under the current directory recursively."
    ),
)


def _version_callback(value: bool) -> None:
    """Print the program version and stop when --version is provided."""
    if value:
        typer.echo(f"rmc {VERSION}")
        raise typer.Exit()


@app.command()
def main(
    paths: list[Path] = typer.Argument(
        None, help="Files or directories; defaults to the current directory recursively."
    ),
    comments: bool = typer.Option(
        False, "-c", "--comments", help="Remove comments (default if no mode flag is given)."
    ),
    docstrings: bool = typer.Option(False, "-d", "--docstrings", help="Remove function and class docstrings."),
    annotations: bool = typer.Option(False, "-t", "--annotations", help="Remove type annotations."),
    all_content: bool = typer.Option(False, "-a", "--all", help="Remove comments, docstrings, and annotations."),
    stats: bool = typer.Option(False, "-n", "--stats", help="Show scan status counts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not write files."),
    diff: bool = typer.Option(False, "--diff", help="Print unified diffs; implies --dry-run."),
    check: bool = typer.Option(False, "--check", help="Never write; exit 1 if any file would change."),
    backup: bool = typer.Option(False, "--backup", help="Write FILE.bak before modifying."),
    backup_dir: Path | None = typer.Option(
        None, "--backup-dir", help="Copy originals into this directory before modifying."
    ),
    includes: list[str] = typer.Option(None, "--include", help="Repeatable include glob."),
    excludes: list[str] = typer.Option(None, "--exclude", help="Repeatable exclude glob."),
    recursive: bool = typer.Option(
        True, "--recursive/--no-recursive", help="Descend into directories (enabled by default)."
    ),
    json_output: bool = typer.Option(False, "--json", help="Write machine-readable JSON instead of the Rich table."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress the per-file Rich table."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable debug logging."),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """Process Python files and report per-file and total bytes removed."""
    del version  # The eager callback handles this option before command execution.
    if quiet and verbose:
        raise typer.BadParameter("--quiet and --verbose cannot be used together")
    input_paths = tuple(paths or [Path.cwd()])
    any_mode_flag = bool(comments or docstrings or annotations or all_content)
    mode = Mode(
        comments=bool(comments or all_content or not any_mode_flag),
        docstrings=bool(docstrings or all_content),
        annotations=bool(annotations or all_content),
    )
    options = Options(
        paths=input_paths,
        mode=mode,
        stats=stats,
        dry_run=dry_run,
        diff=diff,
        check=check,
        backup=backup or backup_dir is not None,
        backup_dir=backup_dir,
        quiet=quiet,
        verbose=verbose,
        includes=tuple(includes or ()),
        excludes=tuple(excludes or ()),
        recursive=recursive,
        json_output=json_output,
    )
    configure_logging(options)
    discovered, missing = discover(options)
    if not discovered and not missing:
        logger.warning("no Python files found")
    jobs = [
        Job(
            path=path,
            mode=options.mode,
            write=options.write,
            want_diff=options.diff,
            backup=options.backup,
            backup_dir=options.backup_dir,
        )
        for path in discovered
    ]
    try:
        results = run_jobs(jobs, MAX_WORKERS)
    except KeyboardInterrupt:
        logger.error("interrupted")
        raise typer.Exit(code=2) from None
    results.sort(key=lambda item: item.path.as_posix())
    summary = summarize(results)
    log_results(results)
    if options.json_output:
        render_json(results, summary, options)
    else:
        render_text(results, summary, options)
    code = exit_code(summary, options, missing)
    if code:
        raise typer.Exit(code=code)


if __name__ == "__main__":
    app()
