#!/data/data/com.termux/files/usr/bin/env python
"""Remove ordinary comments, docstrings and unprotected blank lines from Python code.

CLI entry point; processes files or directory roots (default '.') and preserves
shebangs, coding cookies and protected comments unless told otherwise."""

from __future__ import annotations
import argparse
import ast
import codecs
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager, nullcontext
from difflib import unified_diff
from enum import Enum
import errno
import hashlib
import io
from itertools import islice
import multiprocessing as mp
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import textwrap
import token
import tokenize
from typing import Any, Literal, NamedTuple

import libcst as cst
from libcst.metadata import CodePosition, MetadataWrapper, PositionProvider
from rich.console import Console
from rich.segment import Segment, Segments
from rich.style import Style
from rich.text import Text


def default_workers() -> int:
    try:
        cpus = len(os.sched_getaffinity(0))
    except AttributeError:
        cpus = os.cpu_count() or 4
    return max(1, min(8, cpus))


WORKERS = default_workers()
BATCH_SIZE: int = 64
STATE_FILE: Path = Path(".cache.db")
STATE_VERSION: int = 4
SKIP_DIRS = frozenset({"__pycache__", ".git"})
SUFFIXES = frozenset({".py", ".pyi"})
PHYSICAL_LINE = re.compile(rb"[^\r\n]*(?:\r\n?|\n|\Z)")
COOKIE = re.compile(r"^#.*?coding[:=][ \t]*[-\w.]+", re.ASCII)
KEEP = re.compile(
    r"^#\s*(?:editor |fmt\s*:)",
    re.IGNORECASE,
)
_COMMENT_TOK = tokenize.COMMENT
_FSTRING_START = getattr(token, "FSTRING_START", -1)
_FSTRING_END = getattr(token, "FSTRING_END", -1)
_TSTRING_START = getattr(token, "TSTRING_START", -1)
_TSTRING_END = getattr(token, "TSTRING_END", -1)


class Mode(Enum):
    """Enumerate the supported Mode values."""

    NORMAL = "normal"
    ALL = "all"


def display(path: Path) -> str:
    """A basename that cannot inject another terminal row or control sequence."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in path.name)


def is_python(path: Path) -> bool:
    """Return whether python."""
    if path.suffix:
        return path.suffix.lower() in SUFFIXES
    with path.open("rb") as f:
        first = f.readline(256).lower()
    return first.startswith(b"#!") and b"python" in first


def discover(roots: Sequence[Path]) -> list[Path]:
    """Roots are canonical. Follow explicitly named links, not nested links."""
    files: set[Path] = set()
    dirs: set[Path] = set()
    stack: list[Path] = []
    for path in roots:
        if path.is_dir():
            stack.append(path)
        elif path.is_file() and (not path.suffix or path.suffix.lower() in SUFFIXES):
            files.add(path)
        else:
            raise ValueError(f"Not a Python source file or directory: {display(path)}")
    while stack:
        directory = stack.pop()
        if directory in dirs:
            continue
        dirs.add(directory)
        with os.scandir(directory) as entries:
            for e in entries:
                name = e.name
                if e.is_dir(follow_symlinks=False):
                    if not name.startswith(".") and name not in SKIP_DIRS:
                        stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    dot = name.rfind(".")
                    if dot > 0:
                        if name[dot:].lower() in SUFFIXES:
                            files.add(Path(e.path))
                    else:
                        p = Path(e.path)
                        if is_python(p):
                            files.add(p)
    return sorted(files)


def looks_like_code(text: str) -> bool:
    """Return whether looks like code."""
    try:
        body = ast.parse(text).body
    except (SyntaxError, ValueError):
        return False
    for stmt in body:
        if isinstance(stmt, ast.Expr):
            value = stmt.value
            if isinstance(value, ast.Name) or (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
        return True
    return False


def preserved(source: str) -> set[CodePosition]:
    """Keep occurrences, not strings: identical inline and standalone text can differ."""
    keep: set[CodePosition] = set()
    block: list[tokenize.TokenInfo] = []

    def flush() -> None:
        if block:
            parts = [t.string[1:].removeprefix(" ").rstrip() for t in block]
            dedented = textwrap.dedent("\n".join(parts))
            if looks_like_code(dedented):
                keep.update(CodePosition(*t.start) for t in block)
            else:
                stripped = "\n".join(p.lstrip() for p in parts)
                if stripped != dedented and looks_like_code(stripped):
                    keep.update(CodePosition(*t.start) for t in block)
            block.clear()

    for tok in tokenize.generate_tokens(io.StringIO(source, newline=None).readline):
        if tok.type != _COMMENT_TOK:
            continue
        line, col = tok.start
        standalone = not tok.line[:col].strip()
        if (
            (line == 1 and col == 0 and tok.string.startswith("#!"))
            or (line <= 2 and standalone and COOKIE.match(tok.string))
            or KEEP.search(tok.string)
        ):
            flush()
            keep.add(CodePosition(line, col))
        elif not standalone:
            flush()
        else:
            if block and line != block[-1].end[0] + 1:
                flush()
            block.append(tok)
    flush()
    return keep


Statement = cst.SimpleStatementLine | cst.BaseCompoundStatement


def detached_comments(node: cst.CSTNode | None) -> Iterator[cst.EmptyLine]:
    """Rescue already-filtered comments embedded in syntax about to disappear."""
    if isinstance(node, cst.Comment):
        yield cst.EmptyLine(comment=node)
    elif node is not None:
        for child in node.children:
            yield from detached_comments(child)


def is_docstring(s: cst.BaseSmallStatement) -> bool:
    """Return whether docstring."""
    return (
        isinstance(s, cst.Expr)
        and isinstance(s.value, (cst.SimpleString, cst.ConcatenatedString))
        and isinstance(s.value.evaluated_value, str)
    )


class Stripper(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, keep: set[CodePosition]) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__()
        self.keep = keep
        self.changed = False
        self.doc_suites: set[cst.BaseSuite] = set()

    def discard(self, comment: cst.Comment | None) -> bool:
        """Return whether discard."""
        if comment is None or (self.keep and self.get_metadata(PositionProvider, comment).start in self.keep):
            return False
        self.changed = True
        return True

    def leave_EmptyLine(
        self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine
    ) -> cst.EmptyLine | cst.RemovalSentinel:
        """Return the transformed EmptyLine node."""
        return cst.RemoveFromParent() if self.discard(original_node.comment) else updated_node

    def leave_TrailingWhitespace(
        self,
        original_node: cst.TrailingWhitespace,
        updated_node: cst.TrailingWhitespace,
    ) -> cst.TrailingWhitespace:
        """Return the transformed TrailingWhitespace node."""
        if self.discard(original_node.comment):
            return updated_node.with_changes(comment=None, whitespace=cst.SimpleWhitespace(""))
        return updated_node

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        """Visit FunctionDef nodes."""
        self.doc_suites.add(node.body)

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        """Visit ClassDef nodes."""
        self.doc_suites.add(node.body)

    def small(
        self, body: Sequence[cst.BaseSmallStatement], first_line: bool = True
    ) -> tuple[list[cst.BaseSmallStatement], list[cst.EmptyLine]]:
        out: list[cst.BaseSmallStatement] = []
        extra: list[cst.EmptyLine] = []
        for i, s in enumerate(body):
            if not out and is_docstring(s):
                if first_line and i == 0:
                    self.changed = True
                    extra.extend(detached_comments(s))
                    continue
                out.append(cst.Pass())
            out.append(s)
        if out and out[-1].semicolon is not body[-1].semicolon:
            out[-1] = out[-1].with_changes(semicolon=body[-1].semicolon)
        return out, extra

    def block(
        self,
        body: Sequence[cst.BaseStatement],
        prefix: Sequence[cst.EmptyLine],
        doc_start: bool,
    ) -> tuple[list[Statement], list[cst.EmptyLine]]:
        out: list[Statement] = []
        pending = list(prefix)
        for i, s in enumerate(body):
            if not isinstance(s, (cst.SimpleStatementLine, cst.BaseCompoundStatement)):
                raise TypeError(f"Unsupported statement: {type(s).__name__}")
            had_pending = bool(pending)
            if doc_start and not out and isinstance(s, cst.SimpleStatementLine):
                small, extra = self.small(s.body, i == 0)
                if not small:
                    pending.extend(s.leading_lines)
                    pending.extend(extra)
                    tail = s.trailing_whitespace
                    if tail.comment is not None:
                        pending.append(cst.EmptyLine(comment=tail.comment, newline=tail.newline))
                    continue
                if len(small) != len(s.body) or any(a is not b for a, b in zip(small, s.body)):
                    s = s.with_changes(body=small)
                if extra:
                    pending.extend(s.leading_lines)
                    pending.extend(extra)
                    out.append(s.with_changes(leading_lines=tuple(pending)))
                    pending.clear()
                    continue
            if had_pending:
                pending.extend(s.leading_lines)
                out.append(s.with_changes(leading_lines=tuple(pending)))
                pending.clear()
            else:
                out.append(s)
        return out, pending

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        """Return the transformed Module node."""
        n = updated_node
        body, pending = self.block(n.body, n.header, True)
        footer = (*pending, *n.footer)
        has_trailing = n.has_trailing_newline and bool(body or footer)
        if (
            not n.header
            and not pending
            and has_trailing == n.has_trailing_newline
            and len(body) == len(n.body)
            and all(a is b for a, b in zip(body, n.body))
        ):
            return n
        return n.with_changes(
            header=(),
            body=body,
            footer=footer,
            has_trailing_newline=has_trailing,
        )

    def leave_IndentedBlock(
        self, original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
    ) -> cst.IndentedBlock:
        """Return the transformed IndentedBlock node."""
        if original_node not in self.doc_suites:
            return updated_node
        self.doc_suites.discard(original_node)
        n = updated_node
        body, pending = self.block(n.body, (), True)
        if not body:
            body.append(cst.SimpleStatementLine([cst.Pass()], leading_lines=tuple(pending)))
            pending.clear()
        elif not pending and len(body) == len(n.body) and all(a is b for a, b in zip(body, n.body)):
            return n
        return n.with_changes(body=body, footer=(*pending, *n.footer))

    def leave_SimpleStatementSuite(
        self,
        original_node: cst.SimpleStatementSuite,
        updated_node: cst.SimpleStatementSuite,
    ) -> cst.BaseSuite:
        """Return the transformed SimpleStatementSuite node."""
        if original_node not in self.doc_suites:
            return updated_node
        self.doc_suites.discard(original_node)
        n = updated_node
        small, extra = self.small(n.body)
        if not small:
            small = [cst.Pass()]
        elif not extra and len(small) == len(n.body) and all(a is b for a, b in zip(small, n.body)):
            return n
        if extra:
            line = cst.SimpleStatementLine(small, leading_lines=extra, trailing_whitespace=n.trailing_whitespace)
            return cst.IndentedBlock([line])
        return n.with_changes(body=small)


def _ast_equal(a: object, b: object) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, ast.AST):
        for field in a._fields:
            if not _ast_equal(getattr(a, field, None), getattr(b, field, None)):
                return False
        return True
    if isinstance(a, list):
        return len(a) == len(b) and all(_ast_equal(x, y) for x, y in zip(a, b))
    return a == b


def remove_blank_lines(source: str) -> str:
    """Drop only safe physical blank lines, without normalizing line endings."""
    lines = io.StringIO(source, newline="").readlines()
    drop = {i for i, line in enumerate(lines, 1) if not line.strip(" \t\f\r\n")}
    if not drop:
        return source
    for i, line in enumerate(lines, 1):
        if line.rstrip("\r\n").endswith("\\"):
            drop.discard(i + 1)
        if i <= 2 and COOKIE.match(line.lstrip(" \t\f")):
            drop.difference_update(range(1, i + 1))
    if not drop:
        return source
    string_starts: list[int] = []
    for tok in tokenize.generate_tokens(io.StringIO(source, newline=None).readline):
        start, end = tok.start[0], tok.end[0]
        tt = tok.type
        if tt == _FSTRING_START or tt == _TSTRING_START:
            string_starts.append(start)
        elif tt == _FSTRING_END or tt == _TSTRING_END:
            start = string_starts.pop()
        if start < end:
            drop.difference_update(range(start, end + 1))
            if not drop:
                return source
    kept = ((i, line) for i, line in enumerate(lines, 1) if i not in drop)
    for old_row, line in islice(kept, 2):
        head = line.lstrip(" \t\f")
        if COOKIE.match(head):
            if old_row > 2:
                drop.difference_update((1, 2))
            break
        if head.rstrip("\r\n") and not head.startswith("#"):
            break
    if not drop:
        return source
    result = "".join(line for i, line in enumerate(lines, 1) if i not in drop)
    if not _ast_equal(ast.parse(source), ast.parse(result)):
        raise ValueError("Blank-line removal would change the AST; not written")
    return result


def rewrite(source: str, mode: Mode) -> str:
    if source.strip() and ("#" in source or '"' in source or "'" in source):
        module = cst.parse_module(source).with_changes(has_trailing_newline=source.endswith(("\n", "\r")))
        keep = preserved(source) if mode is not Mode.ALL and "#" in source else set()
        visitor = Stripper(keep)
        if keep:
            result = MetadataWrapper(module, unsafe_skip_copy=True).visit(visitor)
        else:
            result = module.visit(visitor)
        if visitor.changed:
            source = result.code
    return remove_blank_lines(source)


def error_text(exc: Exception) -> str:
    return exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)


def patch(before: str, after: str, path: Path) -> Iterator[str]:
    """Yield patch from the supplied input."""
    name = display(path)
    old, new = (io.StringIO(s, newline="").readlines() for s in (before, after))
    for line in unified_diff(old, new, fromfile=name, tofile=name):
        if line.endswith("\n"):
            yield line
        elif line.endswith("\r"):
            yield line[:-1] + "\n"
        else:
            yield line + "\n"
            yield "\\ No newline at end of file\n"


def make_console(*, stderr: bool = False) -> Console:
    """Create console."""
    return Console(
        stderr=stderr,
        markup=False,
        emoji=False,
        highlight=False,
        soft_wrap=True,
        color_system=None if "NO_COLOR" in os.environ else "auto",
    )


def byte_style(removed: int) -> str:
    return "green" if removed != 0 else "dim"


def progress_row(index: int, total: int, counter_width: int, path: Path, removed: int, width: int) -> Text:
    counter = f"[{index}/{total}]"
    gap = "  " if width >= counter_width + 16 else " "
    remaining = width - counter_width - 2 * len(gap)
    value = f"{removed:,}"
    value_width = max(len(value), min(9, remaining // 3))
    name_width = max(1, remaining - value_width)
    name = Text(display(path))
    name.truncate(name_width, overflow="ellipsis", pad=True)
    return Text.assemble(
        (counter.ljust(counter_width), "dim"),
        gap,
        name,
        gap,
        (value.rjust(value_width), byte_style(removed)),
    )


DIFF_HEADER = Style(bold=True)
DIFF_STYLES: dict[str, Any] = {
    "@": Style(color="cyan", bold=True),
    "+": Style(color="green"),
    "-": Style(color="red"),
    "\\": Style(dim=True),
}


def print_patch(console: Console, lines: Sequence[str]) -> None:
    """Print patch."""
    segments = (Segment(line, DIFF_HEADER if i < 2 else DIFF_STYLES.get(line[:1])) for i, line in enumerate(lines))
    console.print(Segments(segments), end="", soft_wrap=True)


class Job(NamedTuple):
    """Store path, mode, diff, known."""

    path: Path
    mode: Mode
    diff: bool
    known: tuple[bytes, ...]


class Clean(NamedTuple):
    """Store path, digest, skipped."""

    path: Path
    digest: bytes
    skipped: bool


class Edit(NamedTuple):
    """Store path, digest, before, data, diff and related state."""

    path: Path
    digest: bytes
    before: os.stat_result
    data: bytes
    diff: tuple[str, ...]
    removed: int


class Failure(NamedTuple):
    """Store path, message."""

    path: Path
    message: str


Result = Clean | Edit | Failure


def digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def encoding(data: bytes) -> str:
    lines = (m.group() for m in PHYSICAL_LINE.finditer(data))
    return tokenize.detect_encoding(lambda: next(lines, b""))[0]


class InvalidPython(ValueError):
    """The active interpreter cannot compile a file as a standalone module."""


def validate_python(data: bytes, path: Path, *, stage: Literal["input", "rewritten output"]) -> None:
    """Raise InvalidPython on failure. compile() also checks scope rules that ast.parse() misses."""
    try:
        compile(data, display(path), "exec", dont_inherit=True, optimize=0)
    except SyntaxError as exc:
        where = ""
        if exc.lineno is not None and exc.lineno > 0:
            where = f" at line {exc.lineno}"
            if exc.offset is not None and exc.offset > 0:
                where += f", column {exc.offset}"
        raise InvalidPython(f"Invalid {stage}{where}: {exc.msg}") from exc
    except (ValueError, LookupError, OverflowError, RecursionError, MemoryError) as exc:
        raise InvalidPython(f"Cannot validate {stage}: {type(exc).__name__}: {exc}") from exc


def process(job: Job) -> Result:
    try:
        with job.path.open("rb") as f:
            before = os.fstat(f.fileno())
            raw = f.read()
        old_hash = digest(raw)
        if old_hash in job.known:
            return Clean(job.path, old_hash, True)
        if not raw:
            return Clean(job.path, old_hash, False)
        validate_python(raw, job.path, stage="input")
        enc = encoding(raw)
        source = raw.decode(enc)
        new = rewrite(source, job.mode)
        if source == new:
            return Clean(job.path, old_hash, False)
        if job.mode is Mode.ALL:
            enc = "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else "utf-8"
        data = new.encode(enc)
        if data.decode(encoding(data)) != new:
            raise ValueError("Rewrite would change the meaning of the encoding declaration")
        validate_python(data, job.path, stage="rewritten output")
        diff = tuple(patch(source, new, job.path)) if job.diff else ()
        return Edit(job.path, digest(data), before, data, diff, len(raw) - len(data))
    except InvalidPython as exc:
        return Failure(job.path, str(exc))
    except Exception as exc:  # noqa: BLE001 -- isolate failures at the worker boundary
        return Failure(job.path, f"{type(exc).__name__}: {error_text(exc)}")


def signature(s: os.stat_result) -> tuple[int, ...]:
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def apply(edit: Edit, *, dry_run: bool = False) -> None:
    """Atomically replace target file; output has already been validated."""
    if dry_run:
        return
    if edit.before.st_nlink > 1:
        raise OSError("Refusing to break hard links; edit this file separately")
    if not os.access(edit.path, os.W_OK):
        raise PermissionError("File is not writable")
    fd, name = tempfile.mkstemp(dir=edit.path.parent, prefix=".pystrip-")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(edit.data)
        try:
            tmp.chmod(stat.S_IMODE(edit.before.st_mode))
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP):
                raise
        if signature(edit.path.lstat()) != signature(edit.before):
            raise OSError("File changed during processing; refusing to overwrite it")
        os.replace(tmp, edit.path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def database(enabled: bool = False) -> Iterator[sqlite3.Connection | None]:
    """Yield database from the supplied input."""
    if not enabled:
        yield None
        return
    with closing(sqlite3.connect(STATE_FILE)) as db:
        try:
            db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("CREATE TABLE IF NOT EXISTS done (scope TEXT, path BLOB, hash BLOB, PRIMARY KEY(scope,path))")
        yield db


def run(
    roots: Sequence[Path],
    mode: Mode,
    workers: int,
    diff: bool,
    dry: bool,
    cache: bool,
    resume: bool,
) -> int:
    out = make_console()
    err = make_console(stderr=True)
    files = discover(roots)
    if not files:
        err.print("No Python files found.", style="yellow")
        return 0
    total = len(files)
    counter_width = len(f"[{total}/{total}]")
    key = f"{STATE_VERSION}:{sys.implementation.cache_tag}:{mode.value}"
    root_hash = digest(b"\0".join(os.fsencode(p) for p in roots)).hex()
    journal = f"run:{key}:{root_hash}"
    scopes: list[str] = []
    if not dry:
        if cache:
            scopes.append(f"cache:{key}")
        if resume:
            scopes.append(journal)
    changed = skipped = failed = saved = 0
    pending_rows: list[tuple[str, bytes, bytes]] = []

    def flush_db(conn: sqlite3.Connection | None) -> None:
        if conn is not None and pending_rows:
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO done(scope,path,hash) VALUES (?,?,?)",
                    pending_rows,
                )
            pending_rows.clear()

    count = min(total, workers)
    use_pool = total >= 4 and count >= 2
    if use_pool:
        method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
        pool_cm = mp.get_context(method).Pool(count)
    else:
        pool_cm = nullcontext(None)
    with pool_cm as pool, database(bool(scopes)) as db:
        known_map: dict[bytes, list[bytes]] = {}
        if db is not None:
            placeholders = ",".join("?" * len(scopes))
            for path_bytes, h in db.execute(
                f"SELECT path, hash FROM done WHERE scope IN ({placeholders})",
                scopes,
            ):
                known_map.setdefault(path_bytes, []).append(h)
        jobs = [Job(p, mode, diff or dry, tuple(known_map.get(os.fsencode(p), ()))) for p in files]
        chunksize = max(1, min(8, total // (count * 4))) if pool is not None else 1
        stream = pool.imap(process, jobs, chunksize=chunksize) if pool is not None else map(process, jobs)
        term_width = err.width
        try:
            for i, r in enumerate(stream, 1):
                if isinstance(r, Edit):
                    try:
                        apply(r, dry_run=dry)
                    except OSError as exc:
                        r = Failure(r.path, error_text(exc))
                if i & 31 == 0:
                    term_width = err.width
                removed = r.removed if isinstance(r, Edit) else 0
                err.print(progress_row(i, total, counter_width, r.path, removed, term_width))
                if isinstance(r, Failure):
                    err.print(
                        f"  {display(r.path)}: not written: {r.message}",
                        style="bold red",
                    )
                    failed += 1
                    continue
                if isinstance(r, Edit):
                    changed += 1
                    saved += removed
                    if r.diff:
                        print_patch(out, r.diff)
                else:
                    skipped += int(r.skipped)
                if db is not None and not (isinstance(r, Clean) and r.skipped):
                    path_bytes = os.fsencode(r.path)
                    for s in scopes:
                        pending_rows.append((s, path_bytes, r.digest))
                    if len(pending_rows) >= BATCH_SIZE:
                        flush_db(db)
        finally:
            flush_db(db)
        if db is not None and resume and not failed:
            with db:
                db.execute("DELETE FROM done WHERE scope=?", (journal,))
    summary = Text.assemble(
        ("Would free " if dry else "Freed ", "bold"),
        (f"{saved:,}", byte_style(saved)),
        f" net bytes across {changed}/{total} files.",
    )
    if dry:
        summary.append("  [dry run: nothing written]", style="dim")
    if skipped:
        summary.append(f"  {skipped} cached/checkpointed", style="dim")
    if failed:
        summary.append(f"  {failed} failed", style="bold red")
    err.print(summary)
    return int(bool(failed))


def positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from exc
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    """Build parser."""
    parser = argparse.ArgumentParser(
        description="Remove ordinary comments, docstrings, and unprotected blank lines.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        metavar="PATH",
        help="Python files or recursive directory roots; default: .",
    )
    parser.add_argument(
        "-a",
        "--all",
        dest="strip_all",
        action="store_true",
        help="Also strip protected comments, shebangs, and coding cookies.",
    )
    parser.add_argument(
        "-d",
        "--diff",
        action="store_true",
        help="Print a colored unified diff; still writes unless --dry-run is used.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Validate and show diffs without writing source, cache or resume state.",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=positive_int,
        default=WORKERS,
        help=f"Number of parallel worker processes (default: {WORKERS}).",
    )
    parser.add_argument(
        "--cache",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Cache clean content hashes in .pystrip-state.sqlite3 in the working directory.",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Checkpoint successes for this set of inputs and stripping mode.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    args = build_parser().parse_args(argv)
    mode = Mode.ALL if args.strip_all else Mode.NORMAL
    try:
        roots = sorted({p.resolve(strict=True) for p in (args.paths or [Path(".")])})
        return run(roots, mode, args.workers, args.diff, args.dry_run, args.cache, args.resume)
    except KeyboardInterrupt:
        make_console(stderr=True).print("Interrupted.", style="bold yellow")
        return 130
    except (OSError, ValueError, sqlite3.Error) as exc:
        label = ""
        if isinstance(exc, OSError) and exc.filename is not None:
            label = f"{display(Path(os.fsdecode(exc.filename)))}: "
        make_console(stderr=True).print(label + error_text(exc), style="bold red")
        return 1


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
