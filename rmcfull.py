#!/data/data/com.termux/files/usr/bin/env python
"""Strip Python comments/docstrings recursively, updating files atomically.

AST transformations use only the standard library.
Application dependency: loguru.
Optional Rust walker: your fastwalk library, enabled with --fastwalk.
Inputs default to the current directory; operations default to -c -d.
Optional progress: python -m pip install rich
Requires Python 3.14 for cf.interpreter; other backends work on 3.11+.
Install: python -m pip install tree-sitter tree-sitter-python
LibCST backend: python -m pip install libcst
Optional: python -m pip install joblib
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import io
import difflib
import importlib
import shlex
import multiprocessing as mp
import os
import re
import stat
import sys
import tempfile
import tokenize
from time import perf_counter
from dataclasses import dataclass
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import Iterable, Iterator, Protocol, cast

from loguru import logger

_Edit = tuple[int, int, bytes]


@dataclass
class StripFileResult:
    path: Path
    saved: int = 0
    modified: bool = False
    error: str | None = None
    elapsed: float = 0.0
    diff: str = ""
    backup: Path | None = None


class ASTStripper:
    """AST docstring ranges and tokenize comments; never regenerates code."""

    # Extend this list for project-specific tools. Deliberately conservative:
    # comments resembling tool instructions are retained rather than removed.
    _DIRECTIVE_RE = re.compile(
        r"^#\s*(?:type|fmt|mypy|pylint|ruff|pyright|basedpyright|noqa|"
        r"nosec|bandit|isort|yapf|autopep8|flake8|black|pyre|pytype|"
        r"pragma|coverage|doctest|pytest|cython|distutils|numba|"
        r"region|endregion|uv)\b|^#\s*(?:%%|</?editor-fold\b)",
        re.I,
    )
    _COOKIE_RE = re.compile(rb"coding[:=]\s*[-\w.]+")

    def __init__(
        self, strip_comments: bool, strip_docstrings: bool, remove_blank_lines: bool = False, strip_all: bool = False
    ) -> None:
        self.strip_comments = strip_comments or strip_all
        self.strip_docstrings = strip_docstrings or strip_all
        self.strip_all = strip_all
        self.remove_blank_lines = remove_blank_lines

    @staticmethod
    def _line_start(src: bytes, pos: int) -> int:
        return src.rfind(b"\n", 0, pos) + 1

    @staticmethod
    def _line_end(src: bytes, pos: int) -> int:
        end = src.find(b"\n", pos)
        return len(src) if end < 0 else end + 1

    def _keep_comment(self, text: str, row: int) -> bool:
        if self.strip_all:
            return False
        return bool(
            (row == 0 and text.startswith("#!"))
            or (row < 2 and self._COOKIE_RE.search(text.encode("utf-8")))
            or self._DIRECTIVE_RE.match(text)
        )

    def _collect_comments(self, src: bytes) -> list[_Edit]:
        source = src.decode("utf-8")
        lines = io.StringIO(source).readlines()
        offsets: list[int] = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line.encode("utf-8")))
        edits: list[_Edit] = []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            row, column = token.start
            if self._keep_comment(token.string, row - 1):
                continue
            line = lines[row - 1]
            line_start = offsets[row - 1]
            start = line_start + len(line[:column].encode("utf-8"))
            end = line_start + len(line[: token.end[1]].encode("utf-8"))
            if not src[line_start:start].strip(b" \t"):
                edits.append((line_start, self._line_end(src, end), b""))
            else:
                while start > line_start and src[start - 1 : start] in (b" ", b"\t"):
                    start -= 1
                edits.append((start, end, b""))
        return edits

    def _collect_docstrings(self, src: bytes, tree: ast.AST) -> list[_Edit]:
        lines = io.BytesIO(src).readlines()
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        edits: list[_Edit] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.Module) and not self.strip_all:
                continue
            if not node.body:
                continue
            first = node.body[0]
            if not (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                continue
            start = offsets[first.lineno - 1] + first.col_offset
            assert first.end_lineno is not None and first.end_col_offset is not None
            end = offsets[first.end_lineno - 1] + first.end_col_offset
            line_start = self._line_start(src, start)
            line_end = self._line_end(src, end)
            alone_on_lines = not src[line_start:start].strip() and not src[end:line_end].strip()
            next_is_string = (
                len(node.body) > 1
                and isinstance(node.body[1], ast.Expr)
                and isinstance(node.body[1].value, ast.Constant)
                and isinstance(node.body[1].value.value, str)
            )
            if alone_on_lines and not next_is_string and (isinstance(node, ast.Module) or len(node.body) > 1):
                edits.append((line_start, line_end, b""))
            elif isinstance(node, ast.Module) and not next_is_string:
                # A pass here would break a following __future__ import. Remove
                # a separating semicolon as well, without reformatting the rest.
                tail = end
                while tail < len(src) and src[tail : tail + 1] in (b" ", b"\t"):
                    tail += 1
                if src[tail : tail + 1] == b";":
                    tail += 1
                    while tail < len(src) and src[tail : tail + 1] in (b" ", b"\t"):
                        tail += 1
                    end = tail
                edits.append((start, end, b""))
            else:
                # Keeps empty suites/one-line suites valid, and avoids promoting
                # a following standalone string literal to a new docstring.
                edits.append((start, end, b"pass"))
        return edits

    def strip(self, source: str) -> str:
        tree = ast.parse(source)  # Never rewrite invalid input.
        src = source.encode("utf-8")
        # Process docstrings first, then reparse for comment offsets. This avoids
        # overlapping edits when a deleted docstring line has a trailing comment.
        if self.strip_docstrings:
            src = self._apply(src, self._collect_docstrings(src, tree))
        if self.strip_comments:
            src = self._apply(src, self._collect_comments(src))
        result = src.decode("utf-8")
        if self.remove_blank_lines:
            result = remove_blank_lines(result)
        ast.parse(result)  # Validate before writing.
        return result

    @staticmethod
    def _apply(src: bytes, edits: list[_Edit]) -> bytes:
        out = bytearray()
        cursor = 0
        for start, end, replacement in sorted(edits):
            if start < cursor:
                raise ValueError("Overlapping source edits")
            out.extend(src[cursor:start])
            out.extend(replacement)
            cursor = end
        out.extend(src[cursor:])
        return bytes(out)

    @staticmethod
    def _atomic_write(path: Path, data: bytes, original: bytes) -> None:
        mode = stat.S_IMODE(path.stat().st_mode)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temp = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temp.chmod(mode)
            if path.is_symlink() or path.read_bytes() != original:
                raise RuntimeError("File changed while processing; not replaced")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def strip_file(
        self, path: Path, *, dryrun: bool = False, backup: bool = False, show_diff: bool = False
    ) -> StripFileResult:
        started = perf_counter()
        result = StripFileResult(path)
        try:
            if path.is_symlink():
                raise ValueError("Refusing to replace a symbolic link")
            original = path.read_bytes()
            encoding, _ = tokenize.detect_encoding(io.BytesIO(original).readline)
            source = original.decode(encoding)
            stripped = self.strip(source)
            if stripped != source:
                # Without its encoding cookie, non-UTF-8 Python must be transcoded.
                output_encoding = "utf-8" if self.strip_all and self.strip_comments else encoding
                data = stripped.encode(output_encoding)
                ast.parse(data)  # Validate actual encoded output, not just Unicode.
                result.saved = len(original) - len(data)
                result.modified = True  # In dry runs this means "would modify".
                if show_diff:
                    result.diff = make_diff(source, stripped, path.name)
                if not dryrun:
                    if path.is_symlink() or path.read_bytes() != original:
                        raise RuntimeError("File changed while processing; not replaced")
                    if backup:
                        result.backup = create_backup(path, original)
                    self._atomic_write(path, data, original)
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.saved = 0
            result.modified = False
        result.elapsed = perf_counter() - started
        return result


def make_diff(before: str, after: str, name: str) -> str:
    """Unified diff with explicit markers for missing final newlines."""
    output: list[str] = []
    for line in difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{name}", tofile=f"b/{name}"
    ):
        output.append(line)
        if not line.endswith("\n"):
            output.append("\n\\ No newline at end of file\n")
    return "".join(output)


def create_backup(path: Path, original: bytes) -> Path:
    """Never overwrite an existing backup, including a symlink at that name."""
    backup = path.with_name(path.name + ".bak")
    # Exclusive creation: fail safely rather than clobber an earlier backup.
    with backup.open("xb") as stream:
        try:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
            backup.chmod(stat.S_IMODE(path.stat().st_mode))
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
    return backup


def remove_blank_lines(source: str) -> str:
    """Remove whitespace-only physical lines, never string contents/continuations.

    Token spans protect multiline strings, including f/t-string literal chunks.
    An AST equivalence check guards against accidental semantic changes.
    """
    protected: set[int] = set()
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        if token.start[0] != token.end[0]:
            protected.update(range(token.start[0], token.end[0] + 1))
    lines = io.StringIO(source).readlines()
    for row, line in enumerate(lines, 1):
        if line.rstrip("\r\n").endswith("\\"):
            protected.add(row + 1)
        # Keep encoding declarations in their original physical header position.
        if row <= 2 and ASTStripper._COOKIE_RE.search(line.encode("utf-8")):
            protected.update(range(1, row + 1))
    result = "".join(line for row, line in enumerate(lines, 1) if row in protected or line.strip(" \t\f\r\n"))
    before = ast.dump(ast.parse(source), include_attributes=False)
    after = ast.dump(ast.parse(result), include_attributes=False)
    if before != after:
        raise ValueError("Blank-line removal would change the AST; not written")
    return result


class TSStripper(ASTStripper):
    """Tree-sitter comments with the shared AST docstring/editing machinery."""

    def __init__(
        self, strip_comments: bool, strip_docstrings: bool, remove_blank_lines: bool = False, strip_all: bool = False
    ) -> None:
        super().__init__(strip_comments, strip_docstrings, remove_blank_lines, strip_all)
        import tree_sitter
        import tree_sitter_python

        self._parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_python.language()))

    def _collect_comments(self, src: bytes) -> list[_Edit]:
        edits: list[_Edit] = []
        stack = [self._parser.parse(src).root_node]
        while stack:
            node = stack.pop()
            if node.type != "comment":
                stack.extend(reversed(node.children))
                continue
            text = src[node.start_byte : node.end_byte]
            row = node.start_point.row
            if self._keep_comment(text.decode("utf-8"), row):
                continue
            start = self._line_start(src, node.start_byte)
            if not src[start : node.start_byte].strip(b" \t"):
                edits.append((start, self._line_end(src, node.end_byte), b""))
            else:
                start = node.start_byte
                while start and src[start - 1 : start] in (b" ", b"\t"):
                    start -= 1
                # Leave newline characters untouched, including CRLF.
                edits.append((start, node.end_byte, b""))
        return edits


def _libcst_strip(source: str, comments: bool, docstrings: bool, strip_all: bool = False) -> str:
    # Lazy import: tree-sitter users do not need LibCST, and vice versa.
    import libcst as cst
    from libcst.metadata import MetadataWrapper, PositionProvider

    class _CommentRemover(cst.CSTTransformer):
        METADATA_DEPENDENCIES = (PositionProvider,)

        def _keep(self, comment: cst.Comment) -> bool:
            if strip_all:
                return False
            line = self.get_metadata(PositionProvider, comment).start.line
            text = comment.value
            return bool(
                (line == 1 and text.startswith("#!"))
                or (line <= 2 and ASTStripper._COOKIE_RE.search(text.encode("utf-8")))
                or ASTStripper._DIRECTIVE_RE.match(text)
            )

        def leave_EmptyLine(
            self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine
        ) -> cst.EmptyLine | cst.RemovalSentinel:
            if original_node.comment is not None and not self._keep(original_node.comment):
                return cst.RemoveFromParent()
            return updated_node

        def leave_TrailingWhitespace(
            self, original_node: cst.TrailingWhitespace, updated_node: cst.TrailingWhitespace
        ) -> cst.TrailingWhitespace:
            if original_node.comment is not None and not self._keep(original_node.comment):
                return updated_node.with_changes(comment=None, whitespace=cst.SimpleWhitespace(""))
            return updated_node

    class _DocstringRemover(cst.CSTTransformer):
        @staticmethod
        def _is_docstring(expr: cst.CSTNode) -> bool:
            # SimpleString also includes bytes literals, which are NOT docstrings.
            # ConcatenatedString may contain formatted strings: exclude those too.
            if not isinstance(expr, cst.Expr):
                return False
            if not isinstance(expr.value, (cst.SimpleString, cst.ConcatenatedString)):
                return False
            try:
                value = ast.literal_eval(cst.Module([]).code_for_node(expr.value))
            except (ValueError, SyntaxError):
                return False
            return isinstance(value, str)

        def _strip_suite(self, suite: cst.BaseSuite) -> cst.BaseSuite:
            if isinstance(suite, cst.SimpleStatementSuite):
                if suite.body and self._is_docstring(suite.body[0]):
                    # Retain separators and any trailing comment verbatim.
                    first = suite.body[0]
                    return suite.with_changes(body=[cst.Pass(semicolon=first.semicolon), *suite.body[1:]])
                return suite
            if not isinstance(suite, cst.IndentedBlock) or not suite.body:
                return suite
            first = suite.body[0]
            if not isinstance(first, cst.SimpleStatementLine) or not first.body:
                return suite
            if not self._is_docstring(first.body[0]):
                return suite
            if (
                len(first.body) == 1
                and len(suite.body) > 1
                and first.trailing_whitespace.comment is None
                and not (
                    isinstance(suite.body[1], cst.SimpleStatementLine) and self._is_docstring(suite.body[1].body[0])
                )
            ):
                rest = list(suite.body[1:])
                next_stmt = rest[0]
                assert isinstance(next_stmt, (cst.SimpleStatementLine, cst.BaseCompoundStatement))
                rest[0] = next_stmt.with_changes(leading_lines=(*first.leading_lines, *next_stmt.leading_lines))
                return suite.with_changes(body=rest)
            replacement = first.with_changes(body=[cst.Pass(semicolon=first.body[0].semicolon), *first.body[1:]])
            return suite.with_changes(body=[replacement, *suite.body[1:]])

        def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
            if not strip_all or not updated_node.body:
                return updated_node
            first = updated_node.body[0]
            if not isinstance(first, cst.SimpleStatementLine) or not first.body:
                return updated_node
            if not self._is_docstring(first.body[0]):
                return updated_node
            if len(first.body) > 1:
                remaining = (
                    [cst.Pass(semicolon=first.body[0].semicolon), *first.body[1:]]
                    if self._is_docstring(first.body[1])
                    else list(first.body[1:])
                )
                replacement = first.with_changes(body=remaining)
                return updated_node.with_changes(body=[replacement, *updated_node.body[1:]])
            next_is_string = (
                len(updated_node.body) > 1
                and isinstance(updated_node.body[1], cst.SimpleStatementLine)
                and self._is_docstring(updated_node.body[1].body[0])
            )
            if next_is_string:
                replacement = first.with_changes(body=[cst.Pass()])
                return updated_node.with_changes(body=[replacement, *updated_node.body[1:]])
            # Unlike a class/function, an empty module needs no pass. This also
            # keeps __future__ imports legal after removing the module docstring.
            return updated_node.with_changes(body=updated_node.body[1:])

        def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
            # Includes async functions. Module and control-flow blocks are untouched.
            return updated_node.with_changes(body=self._strip_suite(updated_node.body))

        def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
            return updated_node.with_changes(body=self._strip_suite(updated_node.body))

    ast.parse(source)
    module = cst.parse_module(source)
    if docstrings:
        module = module.visit(_DocstringRemover())
    if comments:
        module = MetadataWrapper(module).visit(_CommentRemover())
    result = module.code
    ast.parse(result)
    return result


class CSTStripper(ASTStripper):
    """LibCST transformations with the same safe file I/O as TSStripper."""

    def strip(self, source: str) -> str:
        result = _libcst_strip(source, self.strip_comments, self.strip_docstrings, self.strip_all)
        return remove_blank_lines(result) if self.remove_blank_lines else result


@dataclass(frozen=True)
class FileOptions:
    comments: bool = False
    docstrings: bool = False
    backend: str = "tree-sitter"
    blank_lines: bool = False
    strip_all: bool = False
    dryrun: bool = False
    backup: bool = False
    diff: bool = False


@dataclass(frozen=True)
class Task:
    path: Path
    options: FileOptions


def process_file(task: Task) -> StripFileResult:
    started = perf_counter()
    path, options = task.path, task.options
    try:
        stripper_type = {"tree-sitter": TSStripper, "libcst": CSTStripper, "ast": ASTStripper}[options.backend]
        stripper = stripper_type(options.comments, options.docstrings, options.blank_lines, options.strip_all)
        result = stripper.strip_file(path, dryrun=options.dryrun, backup=options.backup, show_diff=options.diff)
    except Exception as exc:
        result = StripFileResult(path, error=f"{type(exc).__name__}: {exc}")
    result.elapsed = perf_counter() - started
    return result


SKIP_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".bzr",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".tox",
    ".nox",
    ".ipynb_checkpoints",
    "build",
    "dist",
    "out",
    "target",
    ".eggs",
    "site-packages",
    ".idea",
    ".vscode",
    "htmlcov",
    "coverage",
})


class WalkFiles(Protocol):
    def __call__(self, root: str | Path, /) -> list[Path]: ...


def should_skip(path: Path, root_dir: Path) -> bool:
    """Check every ancestor too: the walker might follow directory symlinks."""
    root = root_dir.absolute()
    candidate = path if path.is_absolute() else path.absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    if ".." in relative.parts or is_skip_dir(root):
        return True
    if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in relative.parts[:-1]):
        return True
    current = candidate
    while current != root:
        if current.is_symlink():
            return True
        current = current.parent
    return has_symlink_component(root) or not candidate.is_file()


def is_python_file(path: Path) -> bool:
    """Recognize .py and extensionless scripts with a Python/PyPy shebang."""
    if path.suffix.lower() == ".py":
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as stream:
            first_line = stream.readline(4096).decode("utf-8", errors="replace")
    except OSError as exc:
        logger.warning("{}: cannot inspect shebang: {}", path.name, exc)
        return False
    if not first_line.startswith("#!"):
        return False
    try:
        words = shlex.split(first_line[2:].strip())
    except ValueError:
        return False
    if not words:
        return False
    interpreter = Path(words[0]).name
    if interpreter == "env":
        # env -S may itself hold a quoted command string.
        candidates: list[str] = []
        for word in words[1:]:
            candidates.extend(shlex.split(word))
        interpreter = next((word for word in candidates if not word.startswith("-") and "=" not in word), "")
        interpreter = Path(interpreter).name
    return re.fullmatch(r"(?:python|pypy)(?:[23](?:\.\d+)*)?", interpreter) is not None


def is_skip_dir(path: Path) -> bool:
    return path.name in SKIP_DIRS or path.name.endswith(".egg-info")


def has_symlink_component(path: Path) -> bool:
    return path.is_symlink() or any(parent.is_symlink() for parent in path.parents)


def pathlib_walk_files(root_dir: Path) -> Iterator[Path]:
    """Lazy pathlib traversal, pruning excluded directories before descending."""
    if has_symlink_component(root_dir) or is_skip_dir(root_dir):
        return
    pending = [root_dir]
    while pending:
        directory = pending.pop()
        if directory.is_symlink():
            continue
        # iterdir() is a generator: do not materialize all discovered files.
        for path in directory.iterdir():
            if path.is_symlink():
                continue
            if path.is_dir():
                if not is_skip_dir(path):
                    pending.append(path)
            elif path.is_file():
                yield path


def iter_python_files(root_dir: Path, *, use_fastwalk: bool = False) -> Iterator[Path]:
    """Yield Python files from one file/directory input with either walker.

    fastwalk returns a list and cannot prune directories with its supplied API;
    pathlib is lazy and prunes skipped directories before traversal.
    """
    root_dir = Path(os.path.abspath(root_dir.expanduser()))
    if has_symlink_component(root_dir):
        return
    if not root_dir.exists():
        raise FileNotFoundError(f"Input does not exist: {root_dir}")
    if root_dir.is_file():
        if not should_skip(root_dir, root_dir.parent) and is_python_file(root_dir):
            yield root_dir
        return
    if not root_dir.is_dir():
        raise ValueError(f"Input is not a regular file or directory: {root_dir}")
    if is_skip_dir(root_dir):
        return
    if use_fastwalk:
        walk_files = cast(WalkFiles, importlib.import_module("fastwalk").walk_files)
        candidates: Iterable[Path] = walk_files(root_dir)
    else:
        candidates = pathlib_walk_files(root_dir)
    for path in candidates:
        path = path if path.is_absolute() else root_dir / path
        if should_skip(path, root_dir):
            continue
        if is_python_file(path):
            yield path


def iter_input_files(inputs: Iterable[Path], *, use_fastwalk: bool = False) -> Iterator[Path]:
    """Deduplicate overlapping file/directory inputs before scheduling writes."""
    seen: set[Path] = set()
    for input_path in inputs:
        for path in iter_python_files(input_path, use_fastwalk=use_fastwalk):
            if path not in seen:
                seen.add(path)
                yield path


METHODS = (
    "mp.map",
    "mp.imap",
    "mp.imap_unordered",
    "mp.map_async",
    "mp.starmap",
    "mp.starmap_async",
    "mp.apply",
    "mp.apply_async",
    "threadpool.map",
    "threadpool.imap",
    "threadpool.imap_unordered",
    "threadpool.map_async",
    "threadpool.starmap",
    "threadpool.starmap_async",
    "threadpool.apply",
    "threadpool.apply_async",
    "cf.process",
    "cf.thread",
    "cf.interpreter",
    "joblib",
)


def run_tasks(tasks: list[Task], method: str, workers: int) -> Iterator[StripFileResult]:
    if method.startswith(("mp.", "threadpool.")):
        pool_type = mp.Pool if method.startswith("mp.") else ThreadPool
        operation = method.split(".", 1)[1]
        with pool_type(workers) as pool:
            if operation == "apply":
                # apply blocks; this option is necessarily serial.
                for task in tasks:
                    yield pool.apply(process_file, (task,))
            elif operation == "apply_async":
                pending = [pool.apply_async(process_file, (task,)) for task in tasks]
                for result in pending:
                    yield result.get()
            elif operation in ("starmap", "starmap_async"):
                result = getattr(pool, operation)(process_file, [(task,) for task in tasks])
                yield from result.get() if operation.endswith("_async") else result
            elif operation == "map_async":
                yield from pool.map_async(process_file, tasks).get()
            else:
                yield from getattr(pool, operation)(process_file, tasks)
    elif method.startswith("cf."):
        name = {
            "cf.process": "ProcessPoolExecutor",
            "cf.thread": "ThreadPoolExecutor",
            "cf.interpreter": "InterpreterPoolExecutor",
        }[method]
        executor_type = getattr(cf, name, None)
        if executor_type is None:
            raise RuntimeError(f"{name} requires Python 3.14+")
        with executor_type(max_workers=workers) as executor:
            yield from executor.map(process_file, tasks)
    else:
        try:
            from joblib import Parallel, delayed
        except ImportError as exc:
            raise RuntimeError("Install joblib: python -m pip install joblib") from exc
        yield from Parallel(n_jobs=workers, backend="loky", return_as="generator")(
            delayed(process_file)(task) for task in tasks
        )


def colorize_diff(diff: str, color: bool) -> str:
    if not color:
        return diff
    output: list[str] = []
    for line in diff.splitlines(keepends=True):
        code = (
            "1"
            if line.startswith(("---", "+++"))
            else (
                "32"
                if line.startswith("+")
                else "31"
                if line.startswith("-")
                else "36"
                if line.startswith("@@")
                else ""
            )
        )
        output.append(f"\033[{code}m{line}\033[0m" if code else line)
    return "".join(output)


def main() -> int:
    from contextlib import nullcontext
    import importlib.util

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        metavar="PATH",
        help="Files/directories to process (default: current directory recursively)",
    )
    parser.add_argument(
        "--fastwalk", action="store_true", help="Use your optional Rust fastwalk walker instead of pathlib"
    )
    parser.add_argument("-c", action="store_true", help="Strip comments; keep directives and headers")
    parser.add_argument("-d", action="store_true", help="Strip function/class docstrings; keep module docstrings")
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Strip ALL comments and docstrings, including module docs, headers and directives",
    )
    parser.add_argument(
        "-b",
        "--backend",
        choices=("tree-sitter", "libcst", "ast"),
        default="libcst",
        help="Stripping backend (default: tree-sitter)",
    )
    parser.add_argument(
        "--remove-blank-lines",
        default=True,
        action="store_true",
        help="Remove blank lines outside strings (usable alone)",
    )
    parser.add_argument(
        "--pool-method", choices=METHODS, default="mp.imap_unordered", help="Execution backend (default: mp.map)"
    )
    parser.add_argument("-j", "--workers", type=int, default=4, help="Worker count (default: 4)")
    parser.add_argument("--diff", action="store_true", help="Show unified diffs; combine with --dryrun to preview only")
    parser.add_argument(
        "--backup", action="store_true", help="Save originals as NAME.bak; refuse to overwrite existing backups"
    )
    parser.add_argument(
        "--dryrun", "--dry-run", action="store_true", help="Preview changes without writing files or backups"
    )
    parser.add_argument("-t", "--time", default=True, action="store_true", help="Show processing time for every file")
    parser.add_argument("--rich", action="store_true", help="Rich progress instead of per-file byte reports")
    parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="always", help="Color output (default: auto)"
    )
    parser.add_argument("--log-file", type=Path, help="Also append DEBUG-level Loguru logs to this file")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Console log level (default: WARNING)",
    )
    args = parser.parse_intermixed_args()
    if not (args.c or args.d or args.all or args.remove_blank_lines):
        args.c = args.d = True
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.pool_method == "cf.interpreter" and not hasattr(cf, "InterpreterPoolExecutor"):
        parser.error("cf.interpreter requires Python 3.14+")
    dependencies: dict[str, str] = {}
    if args.backend == "tree-sitter":
        dependencies.update(tree_sitter="tree-sitter", tree_sitter_python="tree-sitter-python")
    elif args.backend == "libcst":
        dependencies["libcst"] = "libcst"
    if args.pool_method == "joblib":
        dependencies["joblib"] = "joblib"
    if args.rich:
        dependencies["rich"] = "rich"
    missing = [package for module, package in dependencies.items() if importlib.util.find_spec(module) is None]
    if missing:
        parser.error("install dependencies: python -m pip install " + " ".join(missing))

    color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ)
    progress = None
    progress_id = None
    if args.rich:
        from rich.console import Console
        from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
        from rich.text import Text

        force_terminal = True if args.color == "always" else False if args.color == "never" else None
        console = Console(stderr=True, force_terminal=force_terminal, no_color=args.color == "never")
        progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.completed:.0f}/{task.total:.0f}"),
            TimeElapsedColumn(),
            console=console,
        )

    logger.remove()
    log_format = "<level>{level: <7}</level> | {message}"
    if progress is not None:

        def rich_log(message: str) -> None:
            if progress is not None:
                progress.console.print(Text.from_ansi(message.rstrip()))

        logger.add(rich_log, level=args.log_level, format=log_format, colorize=False)
    else:
        logger.add(
            sys.stderr,
            level=args.log_level,
            format=log_format,
            colorize=args.color == "always"
            or (args.color == "auto" and sys.stderr.isatty() and "NO_COLOR" not in os.environ),
        )
    if args.log_file is not None:
        logger.add(
            args.log_file,
            level="DEBUG",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
        )

    started = perf_counter()
    try:
        inputs: list[Path] = args.inputs or [Path.cwd()]
        paths = sorted(iter_input_files(inputs, use_fastwalk=args.fastwalk))
    except (ImportError, AttributeError) as exc:
        logger.error("Cannot load your fastwalk.walk_files API: {}. Install your custom fastwalk package.", exc)
        return 1
    except Exception as exc:
        logger.error("File discovery failed: {}", exc)
        return 1
    options = FileOptions(
        comments=args.c,
        docstrings=args.d,
        backend=args.backend,
        blank_lines=args.remove_blank_lines,
        strip_all=args.all,
        dryrun=args.dryrun,
        backup=args.backup,
        diff=args.diff,
    )
    tasks = [Task(path, options) for path in paths]
    logger.info(
        "Found {} Python files; backend={}, pool={}, workers={}, dryrun={}",
        len(paths),
        args.backend,
        args.pool_method,
        args.workers,
        args.dryrun,
    )
    if args.all:
        logger.warning("--all removes executable shebangs, encoding comments and tool directives too.")

    def green(number: int) -> str:
        text = f"{number:,}"
        return f"\033[32m{text}\033[0m" if color else text

    total = errors = changed = 0
    width = max((len(path.name) for path in paths), default=0)
    with progress if progress is not None else nullcontext():
        if progress is not None:
            progress_id = progress.add_task("Processing", total=len(paths))
        try:
            for result in run_tasks(tasks, args.pool_method, args.workers):
                if result.error:
                    errors += 1
                    logger.error("{}: {}", result.path.name, result.error)
                else:
                    total += result.saved
                    changed += result.modified
                    logger.debug(
                        "{}: modified={}, bytes_reduced={}, seconds={:.6f}, backup={}",
                        result.path.name,
                        result.modified,
                        result.saved,
                        result.elapsed,
                        result.backup.name if result.backup else None,
                    )
                    if result.diff:
                        diff = colorize_diff(result.diff, color)
                        if progress is not None:
                            progress.console.print(Text.from_ansi(diff), end="")
                        else:
                            print(diff, end="", flush=True)
                    if progress is None:
                        timing = f"  {result.elapsed:.4f}s" if args.time else ""
                        print(f"{result.path.name:<{width}}  {green(result.saved)}{timing}", flush=True)
                if progress is not None and progress_id is not None:
                    progress.update(progress_id, advance=1, description=result.path.name)
                    if args.time:
                        progress.console.print(Text(f"{result.path.name}  {result.elapsed:.4f}s"))
                elif result.error and args.time:
                    print(f"{result.path.name:<{width}}  {result.elapsed:.4f}s", flush=True)
        except Exception as exc:
            errors += 1
            logger.error("Execution backend failed: {}", exc)
    label = (
        "Potential source bytes reduced"
        if args.dryrun
        else "Total source bytes reduced"
        if args.backup
        else "Total space freed"
    )
    print(f"\n{label}: {green(total)} bytes")
    if args.dryrun:
        print(f"Dry run: {changed} file(s) would change; no source files or backups written.")
    if args.backup and not args.dryrun:
        print("Backup copies consume additional space; byte totals count source files only.")
    if args.time:
        print(f"Total elapsed: {perf_counter() - started:.4f}s")
    logger.info(
        "Finished: {} changed, {} errors, {} source bytes reduced, {:.4f}s",
        changed,
        errors,
        total,
        perf_counter() - started,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
