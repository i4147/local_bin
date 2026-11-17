#!/data/data/com.termux/files/usr/bin/env python
"""Strip comments and docstrings from Python files, in place.

Uses ``libcst`` so the rewrite is a real CST transformation (never a regex over
source text), then re-validates every result with ``ast.parse`` before it is
allowed to touch the disk.

What is removed
---------------
* comments (both whole-line comments and trailing comments)
* docstrings of modules, classes and functions/methods

What is always kept
-------------------
* ``# editor`` comments (and other editor/tool directives below)
* TODO / FIXME / XXX / HACK / NOTE markers, in any form (``# TODO``,
  ``# FIXME(john): later``, ``# todo later``, ``# ... (FIXME: keep this?)``)
* shebangs and encoding cookies
* commented-out *code*: runs of consecutive ``#`` lines are un-commented and
  parsed; if the run parses as Python (and is not just prose / a bare name),
  those lines are left untouched.

``--strip-types`` / ``-t`` additionally removes type annotations, including
``x: int = 1`` -> ``x = 1`` and ``def f(a: int) -> str`` -> ``def f(a)``.

Usage
-----
    python strip_comments.py             # rewrite every .py file under "."
    python strip_comments.py src/ -t     # also drop type annotations
    python strip_comments.py -j 8        # 8 worker processes (default: 4)
    python strip_comments.py -d          # print a colored diff per changed file
    python strip_comments.py -n          # dry run: diff only, write nothing

Cache and resume
----------------
Both live beside the files being rewritten and are safe to delete or commit
ignored:

* ``.strip-comments-cache.json`` -- ``path -> (size, mtime_ns)`` for files that
  were inspected, so a re-run skips unchanged files without parsing them.
  Disable with ``--no-cache``.
* ``.strip-comments-progress.json`` -- written on Ctrl+C (and every
  ``CHECKPOINT_EVERY`` files) with the work that is still pending. The next run
  picks it up automatically: ``--resume`` is the default, ``--no-resume`` starts
  over. A finished run deletes the file.
"""

from __future__ import annotations

import ast
import io
import json
import multiprocessing as mp
import os
import re
import textwrap
import tokenize
from difflib import unified_diff
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple, Sequence

import libcst as cst
import typer
from rich.console import Console
from rich.text import Text

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

#: Default number of worker processes (``-j/--workers`` overrides it).
WORKERS = 4

#: Bump when the transformation itself changes, to invalidate cache + journals.
SCHEMA_VERSION = 2

#: Cache and resume state live next to the files being rewritten.
CACHE_FILE = Path(".strip-comments-cache.json")
PROGRESS_FILE = Path(".strip-comments-progress.json")

#: Journal a checkpoint every N files, so Ctrl+C loses at most this much work.
CHECKPOINT_EVERY = 20

#: Upper bound on fixpoint iterations per file (2 is the normal case).
MAX_PASSES = 4

#: Three or more blank lines in a row (the residue a comment removal can leave).
_BLANK_RUN_RE = re.compile(r"\n[ \t]*\n[ \t]*\n[ \t]*\n")

#: Comments matching this are never removed ("# editor: vim", "# vim: set ts=4",
#: "# -*- coding: utf-8 -*-", ...).
EDITOR_COMMENT_RE = re.compile(
    r"^\#\s*(?:editor\b|editorconfig\b|vim?[:\s]|vi[:\s]|ex[:\s]|emacs|-\*-)",
    re.IGNORECASE,
)

#: Task markers. Matched on their own (not folded into the pragma rule) so a
#: bare ``# TODO``, ``# FIXME(john)`` or ``# todo later`` is never stripped --
#: those are notes for humans, not redundant prose.
TASK_MARKER_RE = re.compile(r"^\#\s*(?:TODO|FIXME|XXX|HACK|NOTE)\b", re.IGNORECASE)

#: A TODO/FIXME mentioned mid-comment ("# implicit all (FIXME: keep this?)")
#: counts too, but only in upper case: lower-case mentions are usually prose or
#: identifiers ("# don't do todo.remove() in the loop").
INLINE_MARKER_RE = re.compile(r"\b(?:TODO|FIXME)\b")

#: Tool pragmas that change the meaning of the code around them, so they must
#: survive too; deleting them silently breaks linters/type-checkers.
PRAGMA_COMMENT_RE = re.compile(
    r"^\#\s*(?:noqa|nosec|type:|pyright:|mypy:|pylint:|ruff:|flake8:|fmt:|isort:|yapf:|"
    r"pragma:|region|endregion)",
    re.IGNORECASE,
)

#: Directories that never contain user code worth rewriting.
SKIP_DIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".bzr",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
    ".tox",
    ".nox",
    ".eggs",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".next",
    ".nuxt",
    ".cache",
    "site-packages",
    ".idea",
    ".vscode",
    ".arena",
    ".local",
})

PY_SOURCE_SUFFIXES = frozenset({".py", ".pyw"})

CONSOLE = Console(highlight=False, soft_wrap=False)

# Styles degrade to plain text on terminals without colour support.
_DIM = "grey35" if CONSOLE.color_system else "dim"
_GOOD = "green"
_BAD = "bold red"

# --------------------------------------------------------------------------- #
# Filesystem walker
# --------------------------------------------------------------------------- #


def _has_python_shebang(path: Path) -> bool:
    """True when an extension-less file starts with a python shebang."""
    try:
        with path.open("rb") as fh:
            head = fh.read(150)
    except OSError:
        return False
    if not head.startswith(b"#!"):
        return False
    first_line = head.split(b"\n", 1)[0].lower()
    return b"python" in first_line


def iter_python_files(root: Path) -> Iterator[Path]:
    """Yield every Python source file under *root*, recursively.

    A file qualifies when it ends with a Python suffix or when it has no suffix
    at all and starts with a ``#!...python`` shebang (executable scripts).
    Symlinks and noisy directories (venvs, VCS metadata, caches) are skipped.
    """
    if not root.is_dir():
        # A single file argument is taken literally, not as its directory.
        if root.suffix.lower() in PY_SOURCE_SUFFIXES or (not root.suffix and _has_python_shebang(root)):
            yield root
        return

    stack: list[str] = [str(root)]
    seen: set[str] = set()

    while stack:
        directory = stack.pop()
        try:
            real = os.path.realpath(directory)
        except OSError:
            continue
        if real in seen:  # defend against directory loops via symlinks
            continue
        seen.add(real)

        try:
            entries = os.scandir(directory)
        except OSError:
            continue

        with entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                            stack.append(entry.path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue

                suffix = os.path.splitext(entry.name)[1].lower()
                if suffix:
                    if suffix in PY_SOURCE_SUFFIXES:
                        yield Path(entry.path)
                elif _has_python_shebang(Path(entry.path)):
                    yield Path(entry.path)


# --------------------------------------------------------------------------- #
# Which comments must be preserved
# --------------------------------------------------------------------------- #


def _looks_like_code(code: str) -> bool:
    """Heuristic: does this un-commented text look like real Python code?

    Prose like ``# done later`` often parses as a bare Name expression, and a
    commented-out docstring parses as a string constant, so both are rejected.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return False

    if not tree.body:
        return False

    for node in tree.body:
        if isinstance(node, ast.Expr):
            value = node.value
            if isinstance(value, ast.Name):  # "# todo" / "# done"
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                continue  # a commented-out string / docstring
        return True
    return False


def _uncomment(tok: tokenize.TokenInfo) -> str:
    body = tok.string[1:]  # drop the leading '#'
    if body.startswith(" "):
        body = body[1:]
    return body.rstrip()


def preserved_comments(src: str) -> set[str]:
    """Return the literal text of every comment that must survive the rewrite.

    Single pass over the token stream:

    * ``#!`` on line 1 is kept, as are ``# editor``-style directives, tool
      pragmas, and TODO/FIXME/NOTE/XXX/HACK task markers;
    * runs of consecutive whole-line comments are un-commented and parsed --
      if the run is valid Python, it is commented-out code and is kept as is.
    """
    keep: set[str] = set()
    lines = src.splitlines()
    ignored_types = {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
    }
    block: list[tokenize.TokenInfo] = []

    def flush() -> None:
        if not block:
            return
        candidates = [_uncomment(tok) for tok in block]
        if _looks_like_code(textwrap.dedent("\n".join(candidates))) or _looks_like_code(
            "\n".join(part.lstrip() for part in candidates)
        ):
            keep.update(tok.string for tok in block)
        block.clear()

    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                if tok.type not in ignored_types:
                    flush()
                continue

            text = tok.string
            line = lines[tok.start[0] - 1] if tok.start[0] <= len(lines) else ""
            trailing = bool(line[: tok.start[1]].strip())

            if tok.start[0] == 1 and text.startswith("#!"):
                keep.add(text)  # shebang
                continue
            if (
                TASK_MARKER_RE.match(text)
                or INLINE_MARKER_RE.search(text)
                or EDITOR_COMMENT_RE.match(text)
                or PRAGMA_COMMENT_RE.match(text)
            ):
                keep.add(text)
                continue
            if trailing:  # belongs to a statement, never to a block
                flush()
                continue

            if block and tok.start[0] != block[-1].end[0] + 1:
                flush()
            block.append(tok)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass

    flush()
    return keep


# --------------------------------------------------------------------------- #
# libcst transformer
# --------------------------------------------------------------------------- #

_DOCSTRING_TYPES = (cst.SimpleString, cst.ConcatenatedString)


def _as_docstring_small(stmt: cst.BaseSmallStatement) -> bool:
    return isinstance(stmt, cst.Expr) and isinstance(stmt.value, _DOCSTRING_TYPES)


def _is_docstring_line(stmt: cst.BaseStatement) -> bool:
    return isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) == 1 and _as_docstring_small(stmt.body[0])


def _collapse_blanks(lines: Sequence[cst.EmptyLine], limit: int = 2) -> list[cst.EmptyLine]:
    """Keep at most *limit* consecutive blank lines (comments reset the counter)."""
    out: list[cst.EmptyLine] = []
    blanks = 0
    for line in lines:
        if line.comment is None:
            blanks += 1
            if blanks > limit:
                continue
        else:
            blanks = 0
        out.append(line)
    return out


def _collapsed_or_self(lines: Sequence[cst.EmptyLine]) -> list[cst.EmptyLine]:
    """Collapse blanks, but return the original list when nothing changed."""
    collapsed = _collapse_blanks(lines)
    return collapsed if len(collapsed) != len(lines) else list(lines)


def _normalize_prefix(prefix, first_stmt):
    """Fold a suite's *prefix* lines into its first statement's leading lines.

    ``Module.header`` / ``IndentedBlock.header`` hold the lines before the first
    statement, and that statement keeps its own ``leading_lines``. libcst
    re-splits those on re-parse, so collapsing them apart would let a second run
    keep eating blank lines one at a time. Merging them first makes the pass
    idempotent.
    """
    merged = _collapse_blanks([*prefix, *first_stmt.leading_lines])
    if len(merged) == len(prefix) + len(first_stmt.leading_lines):
        return list(prefix), first_stmt
    return [], first_stmt.with_changes(leading_lines=merged)


class Stripper(cst.CSTTransformer):
    """Removes comments, docstrings and (optionally) type annotations."""

    def __init__(self, keep_comments: set[str], strip_types: bool) -> None:
        super().__init__()
        self.keep = keep_comments
        self.strip_types = strip_types

    # -- comments ---------------------------------------------------------- #

    def leave_EmptyLine(
        self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine
    ) -> cst.EmptyLine | cst.RemovalSentinel:
        comment = updated_node.comment
        if comment is not None and comment.value not in self.keep:
            return cst.RemoveFromParent()
        return updated_node

    def leave_TrailingWhitespace(
        self,
        original_node: cst.TrailingWhitespace,
        updated_node: cst.TrailingWhitespace,
    ) -> cst.TrailingWhitespace:
        comment = updated_node.comment
        if comment is None or comment.value in self.keep:
            return updated_node
        return updated_node.with_changes(
            comment=None,
            whitespace=(
                updated_node.whitespace.with_changes(value="")
                if isinstance(updated_node.whitespace, cst.SimpleWhitespace)
                else cst.SimpleWhitespace("")
            ),
        )

    # -- docstrings -------------------------------------------------------- #

    @staticmethod
    def _clean_indented(block: cst.IndentedBlock) -> cst.IndentedBlock:
        stmts = list(block.body)
        if not stmts or not _is_docstring_line(stmts[0]):
            return block
        rest = stmts[1:]
        if not rest:
            # `pass` keeps the suite syntactically valid.
            return block.with_changes(body=[stmts[0].with_changes(body=[cst.Pass()])])
        if stmts[0].leading_lines:
            # The docstring's own blank/kept-comment lines move to the next
            # statement; leave_IndentedBlock normalizes the prefix afterwards.
            rest[0] = rest[0].with_changes(leading_lines=[*stmts[0].leading_lines, *rest[0].leading_lines])
        return block.with_changes(body=rest)

    @staticmethod
    def _clean_simple(suite: cst.SimpleStatementSuite) -> cst.SimpleStatementSuite:
        small = list(suite.body)
        if len(small) == 1 and _as_docstring_small(small[0]):
            return suite.with_changes(body=[cst.Pass()])
        return suite

    def _strip_docstring(self, body):
        """Dispatch on suite type; `pass` replaces a body that was only a docstring."""
        if isinstance(body, cst.IndentedBlock):
            return self._clean_indented(body)
        if isinstance(body, cst.SimpleStatementSuite):
            return self._clean_simple(body)
        return body

    def leave_FunctionDef(self, original_node, updated_node):
        return updated_node.with_changes(
            body=self._strip_docstring(updated_node.body),
            returns=None if self.strip_types else updated_node.returns,
            type_parameters=None if self.strip_types else updated_node.type_parameters,
        )

    leave_AsyncFunctionDef = leave_FunctionDef  # libcst exposes only FunctionDef

    def leave_ClassDef(self, original_node, updated_node):
        return updated_node.with_changes(
            body=self._strip_docstring(updated_node.body),
            type_parameters=None if self.strip_types else updated_node.type_parameters,
        )

    def leave_Module(self, original_node, updated_node):
        header = list(updated_node.header)
        stmts = list(updated_node.body)

        if stmts and _is_docstring_line(stmts[0]):
            doc, stmts = stmts[0], stmts[1:]
            header = [*header, *doc.leading_lines]
            if not stmts:
                # An empty module is valid Python; keep what precedes the docstring.
                return updated_node.with_changes(header=_collapsed_or_self(header), body=[])

        if stmts:
            header, stmts[0] = _normalize_prefix(header, stmts[0])
        return updated_node.with_changes(
            header=_collapsed_or_self(header),
            body=stmts,
            footer=_collapsed_or_self(updated_node.footer),
        )

    # -- type annotations (-t / --strip-types) ----------------------------- #

    def leave_Param(self, original_node: cst.Param, updated_node: cst.Param):
        if not self.strip_types or updated_node.annotation is None:
            return updated_node
        if updated_node.default is None:
            return updated_node.with_changes(annotation=None)
        # Drop the padding the annotation forced on the "=" -> `b = "x"` becomes
        # `b="x"`, matching PEP 8 defaults without annotations.
        return updated_node.with_changes(
            annotation=None,
            equal=cst.AssignEqual(
                whitespace_before=cst.SimpleWhitespace(""),
                whitespace_after=cst.SimpleWhitespace(""),
            ),
        )

    def leave_AnnAssign(
        self, original_node: cst.AnnAssign, updated_node: cst.AnnAssign
    ) -> cst.BaseSmallStatement | cst.RemovalSentinel:
        if not self.strip_types:
            return updated_node
        if updated_node.value is not None:
            # `x: int = 1` -> `x = 1`
            return cst.Assign(
                targets=[cst.AssignTarget(target=updated_node.target)],
                value=updated_node.value,
                semicolon=updated_node.semicolon,
            )
        # `x: int` alone has no runtime effect, so drop it. libcst inserts a
        # `pass` if that would leave a suite empty.
        return cst.RemoveFromParent()

    def on_leave(self, original_node, updated_node):
        """Dispatch to ``leave_*`` first, then normalize whatever it produced.

        ``on_leave`` is libcst's dispatch entry point, so ``super()`` must run.
        Two cleanups happen on every node that owns a ``leading_lines`` sequence:

        * blank-line runs are capped at two (libcst keeps the newlines of the
          comments we removed);
        * an ``IndentedBlock`` whose first statement has dropped its leading
          lines (because they were all removed comments) must move the blank
          lines from the block header onto that statement -- otherwise they are
          stranded *outside* the block and the next run would eat one more.
        """
        updated_node = super().on_leave(original_node, updated_node)
        if not isinstance(updated_node, cst.CSTNode):
            return updated_node

        lines = getattr(updated_node, "leading_lines", None)
        if isinstance(lines, (list, tuple)) and lines:
            collapsed = _collapse_blanks(lines)
            if len(collapsed) != len(lines):
                updated_node = updated_node.with_changes(leading_lines=collapsed)

        if isinstance(updated_node, cst.IndentedBlock):
            updated_node = self._settle_block_header(original_node, updated_node)
        return updated_node

    @staticmethod
    def _settle_block_header(original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock) -> cst.IndentedBlock:
        body = updated_node.body
        if not body:
            return updated_node
        if original_node.body and original_node.body[0].leading_lines:
            return updated_node  # the leading lines survived; nothing moved
        header = updated_node.header
        if not isinstance(header, (list, tuple)) or not any(line.comment is None for line in header):
            return updated_node
        if not body[0].leading_lines:
            return updated_node
        first, header = body[0], _collapse_blanks(header)
        return updated_node.with_changes(
            header=[], body=[first.with_changes(leading_lines=[*header, *first.leading_lines]), *body[1:]]
        )

    def leave_IndentedBlock(self, original_node, updated_node):
        body = list(updated_node.body)
        if not body:  # safety net: never emit an empty suite
            return cst.IndentedBlock(body=[cst.SimpleStatementLine(body=[cst.Pass()])])
        # `header` is a run of EmptyLine nodes, or (for a suite that starts on
        # the same line) a lone TrailingWhitespace which cannot be normalized.
        header = updated_node.header
        if isinstance(header, (list, tuple)):
            header, body[0] = _normalize_prefix(header, body[0])
        footer = _collapsed_or_self(updated_node.footer) if updated_node.footer else None
        if header or footer is not None or len(body) != len(updated_node.body):
            return updated_node.with_changes(header=header, body=body, footer=footer or ())
        return updated_node.with_changes(body=body)


# --------------------------------------------------------------------------- #
# Per-file worker (runs in a subprocess)
# --------------------------------------------------------------------------- #


def _needs_work(source: str, strip_types: bool) -> bool:
    """Cheap pre-filter so already-clean files skip the expensive CST round-trip.

    A file needs no work when it contains no ``#`` at all, has no docstrings and
    (unless ``--strip-types``) no annotations. ``ast`` is several times faster
    than libcst, and anything it cannot parse is handed to libcst for a proper
    error message.
    """
    if "#" in source:
        return True
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return True

    if strip_types:
        for node in ast.walk(tree):
            if getattr(node, "type_params", None):  # PEP 695 generics
                return True
            if isinstance(node, ast.AnnAssign):
                return True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns is not None:
                    return True
                for param in _iter_args(node):
                    if param.annotation is not None:
                        return True

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                return True
    return False


def _iter_args(node: ast.AST):
    args = getattr(node, "args", None)
    if args is None:
        return ()
    return [
        *getattr(args, "posonlyargs", ()),
        *args.args,
        *getattr(args, "kwonlyargs", ()),
        *([args.vararg] if args.vararg else []),
        *([args.kwarg] if args.kwarg else []),
    ]


class Job(NamedTuple):
    """Everything a worker needs to handle one file."""

    index: int  # position in the run, so results can be journaled
    path: str
    strip_types: bool
    dry_run: bool
    diff: bool


class Result(NamedTuple):
    index: int
    name: str
    path: str
    saved: int
    error: str | None
    diff: tuple[str, ...] | None = None


def process_file(job: Job) -> Result:
    """Rewrite one file. Returns the file name, bytes freed and an error, if any."""
    index, path_str, strip_types, dry_run, want_diff = job
    path = Path(path_str)
    name = path.name

    def done(saved: int, diff: tuple[str, ...] | None = None) -> Result:
        return Result(index, name, path_str, saved, None, diff)

    try:
        raw = path.read_bytes()
        if not raw.strip():
            return done(0)

        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        except SyntaxError:  # bad/unknown coding cookie
            encoding = "utf-8"
        source = raw.decode(encoding)

        if not _needs_work(source, strip_types):
            return done(0)

        code = source
        for _ in range(MAX_PASSES):
            module = cst.parse_module(code)
            new_code = module.visit(Stripper(preserved_comments(code), strip_types)).code
            if new_code == code:
                break
            code = new_code
            # Removing a comment can strand a run of blank lines that only
            # becomes collapsible once libcst has re-parsed the file. Three or
            # more blank lines in a row are the only way a pass can still find
            # work, so that cheap check replaces a blind second transform and
            # makes a re-run of the tool a guaranteed no-op.
            if _BLANK_RUN_RE.search(code) is None:
                break

        if code == source:
            return done(0)
        new_code = code

        # Never write anything we cannot prove is valid Python.
        ast.parse(new_code, filename=str(path))

        new_raw = new_code.encode(encoding)
        if new_raw == raw:
            return done(0)

        diff = None
        if want_diff:
            diff = tuple(unified_diff(source.splitlines(), new_code.splitlines(), fromfile=path_str, tofile=path_str))

        if not dry_run:
            path.write_bytes(new_raw)
        return done(len(raw) - len(new_raw), diff)
    except Exception as exc:  # noqa: BLE001 - one bad file must not kill the run
        message = f"{type(exc).__name__}: {exc}".replace("\n", " ")
        return Result(index, name, path_str, 0, message[:300])


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


def _cache_key(path: Path, strip_types: bool) -> str:
    """Absolute path plus the options that change the outcome."""
    return f"{path.resolve()}|t={int(strip_types)}"


def load_cache(cache_file: Path) -> dict[str, list[int]]:
    """Read ``{key: [size, mtime_ns]}``; a corrupt cache is silently ignored."""
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, list) and len(v) == 2}


def save_cache(cache_file: Path, cache: dict[str, list[int]]) -> None:
    """Atomically replace the cache file (tmp file + rename)."""
    tmp = cache_file.with_suffix(cache_file.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, cache_file)
    except OSError:
        tmp.unlink(missing_ok=True)


def _stat_signature(path: Path) -> list[int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


# --------------------------------------------------------------------------- #
# Progress journal (resume after Ctrl+C)
# --------------------------------------------------------------------------- #


def job_id(directory: Path, workers: int, strip_types: bool, dry_run: bool, diff: bool) -> str:
    """Stable id for a run: the directory plus every option that shapes output.

    A journal from a run with different options must never be resumed, so the
    options are part of the id.
    """
    return f"{directory.resolve()}|w={workers}|t={int(strip_types)}|n={int(dry_run)}|d={int(diff)}|v={SCHEMA_VERSION}"


def load_journal(progress_file: Path) -> dict | None:
    try:
        data = json.loads(progress_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "pending" in data else None


def save_journal(progress_file: Path, journal: dict) -> None:
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = progress_file.with_suffix(progress_file.suffix + ".tmp")
    tmp.write_text(json.dumps(journal, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, progress_file)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _human(size: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} GiB"


def _fit(text: str, width: int) -> str:
    """Truncate from the middle so the extension stays visible."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    head = (width - 1) // 2
    tail = width - 1 - head
    return text[:head] + "…" + (text[-tail:] if tail else "")


def _render(index: int, total: int, result: Result, width: int) -> Text:
    """One progress line: ``[i/n]  name  bytes``.

    The name column absorbs whatever the terminal offers, the byte column is
    dim when nothing was freed and green otherwise.
    """
    idx = f"[{index}/{total}]"
    idx_col = len(idx) + 2
    size_col = 9
    name_col = max(10, width - idx_col - size_col - 2)

    line = Text()
    line.append(idx.ljust(idx_col), style=_DIM)
    line.append(_fit(result.name, name_col).ljust(name_col), style=_DIM if result.error else "")

    if result.error:
        line.append("!error".rjust(size_col), style=_BAD)
    else:
        style = _DIM if result.saved == 0 else (_GOOD if result.saved > 0 else _BAD)
        line.append(f"{result.saved:>{size_col},}", style=style)
    return line


def _diff_body(line: str) -> Text:
    """Classify one unified-diff line: header, hunk, addition or removal."""
    if line.startswith(("--- ", "+++ ", "diff ", "index ")):
        return Text(line, style="bold")
    if line.startswith(("@@", "@@")):
        return Text(line, style="bold cyan")
    if line.startswith("+"):
        return Text(line, style=_GOOD)
    if line.startswith("-"):
        return Text(line, style=_BAD)
    if line.startswith("\\"):
        return Text(line, style=_DIM)
    return Text(line, style=_DIM)  # context


def render_diff(result: Result) -> Text | None:
    """Colored unified diff for one file, or None when there is nothing to show."""
    if not result.diff:
        return None
    out = Text()
    out.append(f"\n{'─' * 4} {result.path}\n", style="bold")
    for line in result.diff:
        out.append(_diff_body(line))
        out.append("\n")
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

app = typer.Typer(
    add_completion=False,
    rich_markup_mode="rich",
    help=__doc__,
    pretty_exceptions_show_locals=False,
)


@app.command()
def main(
    directory: Path = typer.Argument(
        Path("."),
        exists=True,
        help="Directory to rewrite recursively (default: current directory).",
    ),
    strip_types: bool = typer.Option(
        False,
        "-t",
        "--strip-types",
        is_flag=True,
        help="Also remove type annotations (params, returns, variables, generics).",
    ),
    jobs: int = typer.Option(
        WORKERS,
        "-j",
        "--workers",
        min=1,
        help=f"Worker processes (default: {WORKERS}).",
    ),
    diff: bool = typer.Option(
        False,
        "-d",
        "--diff",
        is_flag=True,
        help="Print a colored unified diff for every changed file.",
    ),
    dry_run: bool = typer.Option(
        False,
        "-n",
        "--dry-run",
        is_flag=True,
        help="Report (and diff) what would change, without writing any file.",
    ),
    cache: bool = typer.Option(
        True,
        "--cache/--no-cache",
        help="Skip files that are unchanged since the last run (stat-based cache).",
    ),
    resume: bool = typer.Option(
        True,
        "--resume/--no-resume",
        help="Resume an interrupted run; --no-resume starts over.",
    ),
) -> None:
    """Strip comments and docstrings from Python files in place."""
    console = CONSOLE
    show_diff = diff or dry_run  # a dry run is only useful if you can see the change
    cache_path = directory / CACHE_FILE if directory.is_dir() else directory.parent / CACHE_FILE
    progress_path = directory / PROGRESS_FILE if directory.is_dir() else directory.parent / PROGRESS_FILE

    files = list(iter_python_files(directory))  # cheap: paths only, no file reads
    if not files:
        console.print(f"[yellow]No Python files found under[/] {directory}")
        raise typer.Exit(code=0)

    run_id = job_id(directory, jobs, strip_types, dry_run, show_diff)
    journal = load_journal(progress_path) if resume else None
    if journal and journal.get("job") != run_id:
        journal = None  # different directory/options: the old journal is moot
    elif journal is None and progress_path.exists():
        progress_path.unlink(missing_ok=True)

    # A dry run never writes, so it must neither trust nor create cache entries:
    # "unchanged since we checked" is only meaningful for files we rewrote.
    use_cache = cache and not dry_run
    known_cache = load_cache(cache_path) if use_cache else {}
    cache_data: dict[str, list[int]] = dict(known_cache)

    # Work out what is left to do: unfinished files minus cache hits.
    # The journal stores *paths*, not indices, so adding or deleting files
    # between the interrupted run and the resume cannot silently shift work.
    positions = {str(path): index for index, path in enumerate(files)}
    skipped_cache = 0
    todo: list[Job] = []
    if journal:
        wanted = set(journal["pending"])
        pending_paths = [path for path in wanted if path in positions]
        skipped_done = max(0, journal.get("total", 0) - len(pending_paths))
        candidates = [(positions[path], Path(path)) for path in pending_paths]
        candidates.sort()
    else:
        skipped_done = 0
        candidates = list(enumerate(files))

    for index, path in candidates:
        if use_cache:
            signature = _stat_signature(path)
            if signature is not None and known_cache.get(_cache_key(path, strip_types)) == signature:
                skipped_cache += 1
                continue
        todo.append(Job(index, str(path), strip_types, dry_run, show_diff))

    total = len(files)
    state = {
        "job": run_id,
        "version": SCHEMA_VERSION,
        "total": total,
        "pending": [job.path for job in todo],
        "written": 0,  # bytes freed so far (journaled so a resume can report a total)
        "changed": 0,
        "errors": [],  # [name, message] pairs
        "cache": {},  # key -> signature, only for files we rewrote
    }
    if journal:
        for key in ("written", "changed", "errors", "cache"):
            if key in journal:
                state[key] = journal[key]

    banner = Text.assemble(
        (f"{total} file{'s' if total != 1 else ''} ", "bold"),
        (f"under {directory}", ""),
        (f" · {jobs} workers", "dim"),
    )
    if strip_types:
        banner.append(" · strip types", style="dim")
    if dry_run:
        banner.append(" · dry run", style="dim")
    if diff:
        banner.append(" · diff", style="dim")
    console.print(banner)
    if skipped_done or skipped_cache:
        note = Text("resuming", style="cyan") if skipped_done else Text()
        if skipped_done:
            note.append(f": {skipped_done} already done", style="cyan")
        if skipped_cache:
            note.append(f"{' · ' if skipped_done else ''}{skipped_cache} cached", style="dim")
        console.print(note)

    freed = state["written"]
    changed = state["changed"]
    errors: list[tuple[str, str]] = [tuple(e) for e in state["errors"]]
    pending = [job.path for job in todo]
    processed = 0

    pool = None if dry_run or jobs == 1 else mp.Pool(processes=jobs)
    try:
        if not todo:
            results: Iterable[Result] = ()
        elif pool is None:
            results = (process_file(job) for job in todo)
        else:
            # chunksize=1 keeps the [i/n] progress numbers close to completion
            # order without starving the workers on tiny files.
            results = pool.imap_unordered(process_file, todo, chunksize=1)

        for result in results:
            processed += 1
            if result.path in pending:
                pending.remove(result.path)
            checkpoint = processed % CHECKPOINT_EVERY == 0 or processed == len(todo)

            if result.error:
                errors.append((result.name, result.error))
                state["errors"].append([result.name, result.error])
            elif result.saved:
                freed += result.saved
                changed += 1
                state["written"] = freed
                state["changed"] = changed
                state["cache"][_cache_key(Path(result.path), strip_types)] = _stat_signature(Path(result.path))
            elif use_cache:
                # Untouched: remember it so the next run can skip the parse.
                state["cache"][_cache_key(Path(result.path), strip_types)] = _stat_signature(Path(result.path))

            console.print(
                _render(result.index + 1, total, result, console.width),
                soft_wrap=True,
                crop=False,
                overflow="ignore",
            )
            if show_diff:
                block = render_diff(result)
                if block is not None:
                    console.print(block, soft_wrap=False, crop=True)

            if checkpoint:
                state["pending"] = sorted(pending)
                save_journal(progress_path, state)
    except KeyboardInterrupt:
        if pool is not None:
            pool.terminate()
            pool = None
        state["pending"] = sorted(pending)
        save_journal(progress_path, state)
        if use_cache:
            cache_data.update(state["cache"])
            save_cache(cache_path, cache_data)
        console.print()
        stopped = Text("Interrupted", style="bold yellow")
        stopped.append(
            f" · {len(pending)} file{'s' if len(pending) != 1 else ''} left"
            f" · resume with --resume (state in {progress_path.name})",
            style="yellow",
        )
        console.print(stopped)
        so_far = Text("Freed so far ", style="bold")
        so_far.append(_human(freed), style="bold green" if freed else "bold dim")
        so_far.append(f" ({freed:,} bytes) across {changed} file{'s' if changed != 1 else ''}")
        console.print(so_far)
        raise typer.Exit(code=130)
    finally:
        if pool is not None:
            pool.close()
            pool.join()

    if use_cache:
        cache_data.update(state["cache"])
        save_cache(cache_path, cache_data)
    progress_path.unlink(missing_ok=True)  # run finished: nothing left to resume

    console.rule(style="dim")
    summary = Text()
    summary.append("Freed ", style="bold")
    summary.append(_human(freed), style="bold green" if freed else "bold dim")
    summary.append(f" ({freed:,} bytes) across {changed} file{'s' if changed != 1 else ''}")
    if dry_run:
        summary.append("  [dry run: nothing written]", style="dim")
    if skipped_cache:
        summary.append(f"  ·  {skipped_cache} cached", style="dim")
    if errors:
        summary.append(f"  ·  {len(errors)} failed", style="bold red")
    console.print(summary)

    for name, message in errors[:20]:
        console.print(Text(f"  {name}: {message}", style="dim red"))
    if len(errors) > 20:
        console.print(Text(f"  … and {len(errors) - 20} more", style="dim red"))


if __name__ == "__main__":
    app()
