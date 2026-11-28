#!/data/data/com.termux/files/usr/bin/env python
"""Strip comments and docstrings from Python files in place using libcst.

CLI entry point; walks files or directories recursively and dispatches work
through a multiprocessing pool."""

from __future__ import annotations

import argparse
import ast
import codecs
import enum
import io
import multiprocessing as mp
import os
import re
import shutil
import signal
import sys
import tempfile
import tokenize
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from multiprocessing.pool import AsyncResult, Pool
from pathlib import Path
from typing import Final, Literal, get_args

import libcst as cst
from loguru import logger

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
NUM_WORKERS: Final[int] = 8
"""Exact number of worker processes in the pool (a hard requirement)."""
CHUNKSIZE: Final[int] = 16
"""Chunk size for map-like pool methods; amortises IPC overhead on big trees."""
MAX_IN_FLIGHT: Final[int] = NUM_WORKERS * 8
"""Upper bound of outstanding tasks for ``apply_async`` (bounds memory use)."""
TARGET_PYTHON: Final[str] = "3.12"
TARGET_VERSION: Final[tuple[int, int]] = (3, 12)
PY_SUFFIXES: Final[frozenset[str]] = frozenset({".py", ".pyw", ".pyi"})
"""Extensions picked up while walking directories."""
DEFAULT_EXCLUDES: Final[frozenset[str]] = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    ".eggs",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
})
"""Directory names that are never descended into while walking a directory."""
PoolMethod = Literal["map", "imap", "imap_unordered", "apply", "apply_async", "starmap"]
POOL_METHODS: Final[tuple[str, ...]] = get_args(PoolMethod)
_PRESERVED_COMMENT: Final[re.Pattern[str]] = re.compile(r"#\s*(?:type|fmt):")
_UTF8_COMPATIBLE: Final[frozenset[str]] = frozenset({"utf-8", "utf-8-sig", "ascii", "us-ascii"})


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
class Status(enum.StrEnum):
    """Outcome of processing one file."""

    CHANGED = "changed"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Options:
    """Immutable, picklable settings shipped to every worker process."""

    dry_run: bool = False
    backup: bool = False
    want_diff: bool = False
    fallback_encoding: str | None = None


@dataclass(frozen=True, slots=True)
class FileResult:
    """Picklable per-file result returned from a worker to the parent process.
    Workers never log; the parent logs from these results so that loguru needs
    no cross-process configuration (works with both ``fork`` and ``spawn``).
    """

    path: Path
    status: Status
    bytes_before: int = 0
    bytes_after: int = 0
    comments_removed: int = 0
    docstrings_removed: int = 0
    diff: str = ""
    message: str = ""

    @property
    def bytes_saved(self) -> int:
        """Bytes removed from the file (0 for files that were not changed)."""
        return self.bytes_before - self.bytes_after


@dataclass(slots=True)
class Summary:
    """Aggregated run totals, updated in the parent as results stream in."""

    scanned: int = 0
    changed: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    missing: int = 0
    bytes_processed: int = 0
    bytes_saved: int = 0
    comments: int = 0
    docstrings: int = 0

    def add(self, result: FileResult) -> None:
        """Fold one :class:`FileResult` into the totals."""
        self.scanned += 1
        match result.status:
            case Status.CHANGED:
                self.changed += 1
                self.bytes_saved += result.bytes_saved
                self.comments += result.comments_removed
                self.docstrings += result.docstrings_removed
                self.bytes_processed += result.bytes_before
            case Status.UNCHANGED:
                self.unchanged += 1
                self.bytes_processed += result.bytes_before
            case Status.SKIPPED:
                self.skipped += 1
            case Status.FAILED:
                self.failed += 1


class SkipFile(Exception):
    """Raised inside a worker when a file should be skipped (not an error)."""


# --------------------------------------------------------------------------- #
# libcst transformer
# --------------------------------------------------------------------------- #
def _is_preserved(comment: str) -> bool:
    """Return True for ``# type:`` and ``# fmt:`` comments (whitespace tolerant)."""
    return _PRESERVED_COMMENT.match(comment) is not None


def _is_plain_string(expr: cst.BaseExpression) -> bool:
    """Return True if *expr* is a (possibly implicitly concatenated) ``str`` literal.
    Bytes literals and f-strings are *not* docstrings: ``ast.get_docstring``
    ignores them, so removing them would change program behaviour.
    """
    if isinstance(expr, cst.SimpleString):
        return "b" not in expr.prefix.lower()
    if isinstance(expr, cst.ConcatenatedString):
        return _is_plain_string(expr.left) and _is_plain_string(expr.right)
    return False


def _is_docstring_statement(stmt: cst.BaseSmallStatement) -> bool:
    """Return True if a small statement is a bare string-literal expression."""
    return isinstance(stmt, cst.Expr) and _is_plain_string(stmt.value)


class StripCommentsAndDocstrings(cst.CSTTransformer):
    """Remove comments and function/class docstrings from a libcst tree.
    How libcst models comments
    --------------------------
    libcst is a *concrete* syntax tree: comments are not statements, they live
    inside "whitespace" nodes attached to real nodes. There are exactly two
    node types that can carry a :class:`libcst.Comment`:
    ``EmptyLine``
        A full line that holds only a comment and/or blank space.  They occur
        in ``Module.header``/``footer``, ``leading_lines`` of statements,
        decorators, ``else``/``except``/``finally`` clauses, ``IndentedBlock
        .footer`` and inside parenthesised expressions.  A comment-only
        ``EmptyLine`` is deleted entirely (``RemoveFromParent``) so that no
        stray blank line is left behind.  Genuinely blank lines are kept.
    ``TrailingWhitespace``
        The tail of a line of code (``x = 1  # note``).  Here only the comment
        and the whitespace in front of it are cleared; the newline stays.
    Because every comment is reachable through one of those two nodes, two
    ``leave_*`` hooks cover all positions, including comments buried inside
    brackets or after block openers such as ``def f():  # comment``.
    How docstrings are handled
    --------------------------
    A docstring is the first statement of a ``FunctionDef`` / ``ClassDef``
    body when it is a plain string-literal expression.  The hooks run on the
    way *up* (``leave_``), after children are processed.  If other statements
    remain in the body the docstring is dropped; if it was the only statement
    it is replaced by ``pass``.  Both block bodies (``IndentedBlock``) and
    one-liner bodies (``def f(): "doc"``, a ``SimpleStatementSuite``) are
    supported, as is ``"doc"; x = 1`` on a single line.  The module docstring
    is never touched.
    Shebang
    -------
    The shebang is only legal on line 1.  The first ``EmptyLine`` visited is,
    by traversal order, the first line of the file, so it is spared if the
    source starts with ``#!``.
    Attributes:
        comments_removed: Number of comments deleted so far.
        docstrings_removed: Number of docstrings deleted so far.
    """

    def __init__(self, *, has_shebang: bool) -> None:
        """Create a transformer.
        Args:
            has_shebang: True if the source text starts with ``#!``.
        """
        super().__init__()
        self.comments_removed: int = 0
        self.docstrings_removed: int = 0
        self._shebang_pending: bool = has_shebang

    # -- comments ---------------------------------------------------------- #
    def leave_EmptyLine(
        self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine
    ) -> cst.EmptyLine | cst.RemovalSentinel:
        """Delete comment-only lines, keeping shebang and type/fmt comments."""
        comment = updated_node.comment
        if self._shebang_pending:
            # Traversal is in document order, so this is line 1 of the file.
            self._shebang_pending = False
            if comment is not None and comment.value.startswith("#!"):
                return updated_node
        if comment is None or _is_preserved(comment.value):
            return updated_node
        self.comments_removed += 1
        return cst.RemoveFromParent()

    def leave_TrailingWhitespace(
        self,
        original_node: cst.TrailingWhitespace,
        updated_node: cst.TrailingWhitespace,
    ) -> cst.TrailingWhitespace:
        """Clear an end-of-line comment together with the spaces before it."""
        comment = updated_node.comment
        if comment is None or _is_preserved(comment.value):
            return updated_node
        self.comments_removed += 1
        return updated_node.with_changes(comment=None, whitespace=cst.SimpleWhitespace(""))

    # -- docstrings -------------------------------------------------------- #
    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        """Strip the docstring of a (sync or async) function."""
        return updated_node.with_changes(body=self._strip_docstring(updated_node.body))

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        """Strip the docstring of a class."""
        return updated_node.with_changes(body=self._strip_docstring(updated_node.body))

    def _strip_docstring(self, body: cst.BaseSuite) -> cst.BaseSuite:
        """Remove a leading docstring from *body*, inserting ``pass`` if needed.
        Args:
            body: The suite of a function or class definition.
        Returns:
            The (possibly) rewritten suite. ``body`` itself if no docstring.
        """
        if isinstance(body, cst.SimpleStatementSuite):
            # One-liner: ``def f(): "doc"`` / ``def f(): "doc"; return 1``.
            small = body.body
            if not small or not _is_docstring_statement(small[0]):
                return body
            self.docstrings_removed += 1
            rest = small[1:]
            return body.with_changes(body=rest if rest else [cst.Pass()])
        if not isinstance(body, cst.IndentedBlock) or not body.body:
            return body
        first = body.body[0]
        if (
            not isinstance(first, cst.SimpleStatementLine)
            or not first.body
            or not _is_docstring_statement(first.body[0])
        ):
            return body
        self.docstrings_removed += 1
        rest_of_line = first.body[1:]
        rest_of_block = body.body[1:]
        if rest_of_line:
            # ``"doc"; x = 1``: keep the remaining statements of the line.
            new_first = first.with_changes(body=rest_of_line)
            return body.with_changes(body=(new_first, *rest_of_block))
        if rest_of_block:
            return body.with_changes(body=rest_of_block)
        # Docstring was the only statement: an empty body is a SyntaxError.
        # Reusing the line keeps its indentation and trailing whitespace.
        return body.with_changes(body=(first.with_changes(body=[cst.Pass()]),))


# --------------------------------------------------------------------------- #
# Single-file pipeline (runs in worker processes)
# --------------------------------------------------------------------------- #
def _check_declared_encoding(raw: bytes) -> None:
    """Skip files whose PEP 263 cookie declares a non-UTF-8 encoding.
    We drop comments (cookie included) and write UTF-8, which would silently
    change how such a file is interpreted, so it is safer to leave it alone.
    """
    try:
        declared, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    except (SyntaxError, LookupError):
        return  # malformed cookie: the parser will report it
    if declared.lower() not in _UTF8_COMPATIBLE:
        raise SkipFile(f"declares non-UTF-8 source encoding {declared!r}")


def _decode_source(raw: bytes, fallback: str | None) -> tuple[str, str, bytes]:
    """Decode *raw* as UTF-8, optionally falling back to another encoding.
    Args:
        raw: File contents.
        fallback: Encoding to try when UTF-8 fails (e.g. ``cp1252``), or None.
    Returns:
        ``(text, encoding_used, bom)``. The BOM is returned separately so it
        can be re-attached on write; *text* never contains it.
    Raises:
        SkipFile: If the data is binary or cannot be decoded.
    """
    if b"\x00" in raw:
        raise SkipFile("binary or non-text content (NUL bytes)")
    _check_declared_encoding(raw)
    bom = b""
    if raw.startswith(codecs.BOM_UTF8):
        bom, raw = codecs.BOM_UTF8, raw[len(codecs.BOM_UTF8) :]
    try:
        return raw.decode("utf-8"), "utf-8", bom
    except UnicodeDecodeError as exc:
        utf8_error = exc
    if fallback is None or bom:
        raise SkipFile(
            f"not valid UTF-8 ({utf8_error.reason} at byte {utf8_error.start}); use --fallback-encoding to process it"
        ) from utf8_error
    try:
        return raw.decode(fallback), fallback, b""
    except UnicodeDecodeError as exc:
        raise SkipFile(f"cannot decode as UTF-8 or {fallback}: {exc.reason}") from exc


def strip_source(source: str) -> tuple[str, int, int]:
    """Strip comments and docstrings from Python *source* text.
    Args:
        source: Python source code.
    Returns:
        ``(new_source, comments_removed, docstrings_removed)``.
    Raises:
        libcst.ParserSyntaxError: If *source* is not valid Python 3.12.
    """
    module = cst.parse_module(source, config=cst.PartialParserConfig(python_version=TARGET_PYTHON))
    transformer = StripCommentsAndDocstrings(has_shebang=source.startswith("#!"))
    new_module = module.visit(transformer)
    return new_module.code, transformer.comments_removed, transformer.docstrings_removed


def _make_diff(path: Path, old: str, new: str) -> str:
    """Return a unified diff of *old* -> *new* ending in a newline."""
    import difflib
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return diff if not diff or diff.endswith("\n") else diff + "\n"


def _make_backup(path: Path) -> None:
    """Copy *path* to ``<path>.bak`` (metadata preserved); never overwrite one."""
    backup = path.with_name(path.name + ".bak")
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    shutil.copy2(path, backup)


def _atomic_write(path: Path, data: bytes) -> None:
    """Atomically replace *path* with *data*, preserving its permission bits.
    The data is written to a temp file in the same directory (same filesystem,
    so the final rename is atomic) and fsynced before replacing the original.
    On any error the temp file is removed and the original is left untouched.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copymode(path, tmp)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _process_file(path: Path, options: Options) -> FileResult:
    """Run the full read -> transform -> validate -> (backup) -> write pipeline."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return FileResult(path, Status.FAILED, message=f"cannot read file: {exc}")
    try:
        source, encoding, bom = _decode_source(raw, options.fallback_encoding)
    except SkipFile as exc:
        return FileResult(path, Status.SKIPPED, message=str(exc))
    try:
        new_source, n_comments, n_docstrings = strip_source(source)
    except cst.ParserSyntaxError:
        return FileResult(path, Status.SKIPPED, message=f"not valid Python {TARGET_PYTHON} source")
    if new_source == source:
        return FileResult(path, Status.UNCHANGED, bytes_before=len(raw), bytes_after=len(raw))
    try:  # Safety net: never write something Python cannot parse.
        ast.parse(new_source, filename=str(path), feature_version=TARGET_VERSION)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return FileResult(
            path,
            Status.FAILED,
            message=f"transformed code failed validation, file untouched: {exc}",
        )
    new_raw = bom + new_source.encode(encoding)
    diff = _make_diff(path, source, new_source) if options.want_diff else ""
    changed = FileResult(
        path,
        Status.CHANGED,
        bytes_before=len(raw),
        bytes_after=len(new_raw),
        comments_removed=n_comments,
        docstrings_removed=n_docstrings,
        diff=diff,
    )
    if options.dry_run:
        return changed
    if not os.access(path, os.W_OK):
        return FileResult(path, Status.FAILED, message="file is not writable (permission denied)")
    try:
        if options.backup:
            _make_backup(path)
        _atomic_write(path, new_raw)
    except OSError as exc:
        return FileResult(path, Status.FAILED, message=f"cannot write file: {exc}")
    return changed


def process_file(path: Path, options: Options) -> FileResult:
    """Process one file; never raises (so one bad file can't kill the pool).
    This is the picklable, top-level entry point used by every pool method
    (``starmap`` and ``apply*`` call it directly with ``(path, options)``).
    """
    try:
        return _process_file(path, options)
    except Exception as exc:  # noqa: BLE001 - last line of defence in a worker
        return FileResult(path, Status.FAILED, message=f"unexpected {type(exc).__name__}: {exc}")


def _init_worker() -> None:
    """Pool initializer: let the parent handle Ctrl-C so workers exit cleanly."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


# --------------------------------------------------------------------------- #
# File discovery (parent process)
# --------------------------------------------------------------------------- #
def _looks_like_python_script(path: Path) -> bool:
    """Return True for an extension-less file whose shebang mentions python."""
    try:
        with path.open("rb") as handle:
            first_line = handle.readline(256)
    except OSError:
        return False
    return first_line.startswith(b"#!") and b"python" in first_line


def _log_walk_error(error: OSError) -> None:
    """``Path.walk`` error callback: log and keep walking."""
    logger.warning("Cannot traverse {}: {}", error.filename, error.strerror or error)


def iter_python_files(targets: Iterable[Path], excludes: frozenset[str]) -> Iterator[Path]:
    """Lazily yield unique Python files under *targets*.
    * An explicit file is always yielded (any extension, or none); non-Python
      content is rejected later by the parser.
    * A directory is walked recursively (symlinks not followed). It yields
      ``*.py``/``*.pyw``/``*.pyi`` plus extension-less files with a python
      shebang. Directories named in *excludes* are pruned.
    Paths are resolved, so symlinked arguments are rewritten at their target
    and overlapping arguments never process a file twice.
    """
    seen: set[Path] = set()
    for target in targets:
        resolved = target.resolve()
        if resolved.is_file():
            if resolved not in seen:
                seen.add(resolved)
                yield resolved
            continue
        for root, dirnames, filenames in ((Path(root), dirs, files) for root, dirs, files in os.walk(resolved, onerror=_log_walk_error)):
            dirnames[:] = sorted(d for d in dirnames if d not in excludes and not d.endswith(".egg-info"))
            for name in sorted(filenames):
                candidate = root / name
                if candidate.is_symlink():
                    continue
                if (
                    candidate.suffix in PY_SUFFIXES or (not candidate.suffix and _looks_like_python_script(candidate))
                ) and candidate not in seen:
                    seen.add(candidate)
                    yield candidate


# --------------------------------------------------------------------------- #
# Pool dispatch
# --------------------------------------------------------------------------- #
def iter_results(pool: Pool, method: PoolMethod, paths: Iterable[Path], options: Options) -> Iterator[FileResult]:
    """Dispatch *paths* to *pool* using the chosen method and stream results.
    Trade-offs:
        ``map`` / ``starmap``  Materialise the full result list before yielding.
        ``imap``               Lazy, ordered results.
        ``imap_unordered``     Lazy, completion-order results (lowest latency).
        ``apply``              Blocking, one task at a time (effectively serial;
                               provided for completeness).
        ``apply_async``        Ordered, with a sliding window of at most
                               ``MAX_IN_FLIGHT`` pending tasks (bounded memory).
    """
    import functools
    worker = functools.partial(process_file, options=options)
    match method:
        case "map":
            yield from pool.map(worker, list(paths), chunksize=CHUNKSIZE)
        case "starmap":
            yield from pool.starmap(process_file, [(path, options) for path in paths], chunksize=CHUNKSIZE)
        case "imap":
            yield from pool.imap(worker, paths, chunksize=CHUNKSIZE)
        case "imap_unordered":
            yield from pool.imap_unordered(worker, paths, chunksize=CHUNKSIZE)
        case "apply":
            for path in paths:
                yield pool.apply(process_file, (path, options))
        case "apply_async":
            pending: deque[AsyncResult[FileResult]] = deque()
            for path in paths:
                pending.append(pool.apply_async(process_file, (path, options)))
                if len(pending) >= MAX_IN_FLIGHT:
                    yield pending.popleft().get()
            while pending:
                yield pending.popleft().get()


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report_result(result: FileResult, *, stats: bool, options: Options) -> None:
    """Print/log the outcome of one file.
    Reports go to stdout, operational messages (skips, errors) to loguru/stderr.
    """
    match result.status:
        case Status.CHANGED:
            tag = "[dry-run] " if options.dry_run else ""
            if stats:
                detail = f"{result.comments_removed:,} comments, {result.docstrings_removed:,} docstrings removed"
            else:
                detail = f"{result.bytes_saved:,} bytes reduced ({result.bytes_before:,} -> {result.bytes_after:,})"
            print(f"{tag}{result.path}: {detail}")
            if result.diff:
                sys.stdout.write(result.diff)
        case Status.UNCHANGED:
            logger.debug("Unchanged: {}", result.path)
        case Status.SKIPPED:
            logger.warning("Skipped {}: {}", result.path, result.message)
        case Status.FAILED:
            logger.error("Failed {}: {}", result.path, result.message)


def print_summary(summary: Summary, *, options: Options, interrupted: bool = False) -> None:
    """Print the end-of-run totals."""
    verb = "would be saved" if options.dry_run else "saved"
    pct = (summary.bytes_saved / summary.bytes_processed * 100) if summary.bytes_processed else 0.0
    print()
    if interrupted:
        print("Interrupted - partial results:")
    print(
        f"Files: {summary.scanned} scanned, {summary.changed} changed, "
        f"{summary.unchanged} unchanged, {summary.skipped} skipped, "
        f"{summary.failed} failed, {summary.missing} missing"
    )
    print(f"Removed: {summary.comments:,} comments and {summary.docstrings:,} docstrings")
    print(f"Total space {verb}: {summary.bytes_saved:,} bytes ({pct:.1f}% of {summary.bytes_processed:,})")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _codec_name(value: str) -> str:
    """argparse type: validate that *value* names a known Python codec."""
    try:
        codecs.lookup(value)
    except LookupError as exc:
        raise argparse.ArgumentTypeError(f"unknown encoding: {value!r}") from exc
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="strip_comments",
        description="Strip comments and docstrings from Python files in place (libcst).",
        epilog="Kept: shebang, module docstring, '# type:' and '# fmt:' comments.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="files or directories to process recursively (default: current directory)",
    )
    parser.add_argument(
        "--pool-method",
        choices=POOL_METHODS,
        default="apply_async",
        help="multiprocessing.Pool dispatch method (default: %(default)s)",
    )
    parser.add_argument(
        "-n",
        "--stats",
        action="store_true",
        help="report the number of comments and docstrings removed per file",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="show what would change without writing anything",
    )
    parser.add_argument("--diff", action="store_true", help="print a unified diff of every change")
    parser.add_argument(
        "--backup",
        action="store_true",
        help="copy each file to '<name>.bak' before modifying it (never overwrites a .bak)",
    )
    parser.add_argument(
        "--fallback-encoding",
        type=_codec_name,
        metavar="ENCODING",
        help="decode files that are not valid UTF-8 with ENCODING (e.g. cp1252) and "
        "write them back in that encoding; by default such files are skipped",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="DIRNAME",
        help="extra directory name to skip while walking (repeatable)",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    verbosity.add_argument("-q", "--quiet", action="store_true", help="only log errors")
    return parser


def configure_logging(*, verbose: bool, quiet: bool) -> None:
    """Route all loguru output to stderr at the requested verbosity."""
    logger.remove()
    level = "DEBUG" if verbose else "ERROR" if quiet else "INFO"
    logger.add(sys.stderr, level=level, format="<level>{level: <8}</level> | {message}")


def main(argv: Sequence[str] | None = None) -> int:
    """Program entry point.
    Returns:
        0 on success, 1 if any file failed or any path was missing,
        130 if interrupted by the user.
    """
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose, quiet=args.quiet)
    options = Options(
        dry_run=args.dry_run,
        backup=args.backup,
        want_diff=args.diff,
        fallback_encoding=args.fallback_encoding,
    )
    summary = Summary()
    targets: list[Path] = []
    for raw_target in args.paths or [Path.cwd()]:
        if raw_target.exists():
            targets.append(raw_target)
        else:
            logger.error("Path does not exist: {}", raw_target)
            summary.missing += 1
    excludes = DEFAULT_EXCLUDES | frozenset(args.exclude)
    logger.info(
        "Processing {} target(s) with {} workers (method={}{})",
        len(targets),
        NUM_WORKERS,
        args.pool_method,
        ", dry-run" if options.dry_run else "",
    )
    interrupted = False
    try:
        with mp.Pool(processes=NUM_WORKERS, initializer=_init_worker) as pool:
            paths = iter_python_files(targets, excludes)
            for result in iter_results(pool, args.pool_method, paths, options):
                summary.add(result)
                report_result(result, stats=args.stats, options=options)
            pool.close()
            pool.join()
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("Interrupted; files are written atomically so none are corrupted")
    if summary.scanned == 0 and not interrupted:
        logger.warning("No Python files found")
    print_summary(summary, options=options, interrupted=interrupted)
    if interrupted:
        return 130
    return 1 if (summary.failed or summary.missing) else 0


if __name__ == "__main__":
    if sys.version_info < (3, 12):
        sys.exit("strip_comments requires Python 3.12 or newer")
    raise SystemExit(main())
