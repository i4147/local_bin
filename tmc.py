#!/data/data/com.termux/files/usr/bin/env python

import argparse
import ast
import concurrent.futures as cf
import dataclasses
import difflib
import fnmatch
import importlib
import importlib.util
import io
import json
import multiprocessing as mp
import os
import re
import shlex
import signal
import stat
import sys
import tempfile
import textwrap
import tokenize
import traceback
from collections.abc import Callable, Iterable, Iterator
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

import libcst as cst
from atomicio import atomic_write
from dh import MAX_WORKERS, SKIP_DIRS
from loguru import logger

if TYPE_CHECKING:
    from rich.progress import Progress, TaskID

    _CSTTypes = tuple[type[cst.CSTTransformer], type[cst.CSTTransformer]]


_Edit = tuple[int, int, bytes]
_FEATURE_VERSION: Final = (3, 12)
_TRIVIAL: Final = (ast.Name, ast.Constant, ast.Attribute)
_DIRECTIVE_RE: Final = re.compile(
    r"^#\s*(?:type|fmt|mypy|pylint|ruff|pyright|basedpyright|noqa|"
    r"nosec|bandit|isort|yapf|autopep8|flake8|black|pyre|pytype|"
    r"pragma|coverage|doctest|pytest|cython|distutils|numba|"
    r"region|endregion|uv)\b|^#\s*(?:%%|</?editor-fold\b)",
    re.I,
)
_COOKIE_RE_STR: Final = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
_COOKIE_RE_BYTES: Final = re.compile(rb"coding[:=]\s*[-\w.]+")


class SkipFile(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Mode:
    comments: bool
    docstrings: bool


@dataclass(slots=True)
class StripFileResult:
    path: Path
    saved: int = 0
    modified: bool = False
    error: str | None = None
    elapsed: float = 0.0
    diff: str = ""
    backup: Path | None = None
    traceback: str = ""
    skipped: bool = False
    skip_reason: str = ""


def _is_trivial(node: ast.stmt) -> bool:
    if isinstance(node, ast.Expr):
        return isinstance(node.value, _TRIVIAL)
    if isinstance(node, ast.AnnAssign):
        return node.value is None and isinstance(node.annotation, _TRIVIAL)
    return False


def _looks_like_code(values: Iterable[str]) -> bool:
    lines: list[str] = []
    for value in values:
        body = value[1:]
        lines.append(body.removeprefix(" "))
    source = textwrap.dedent("\n".join(lines)).strip("\n")
    if not source.strip():
        return False
    try:
        tree = ast.parse(source, feature_version=_FEATURE_VERSION)
    except (SyntaxError, ValueError, RecursionError):
        return False
    return any(not _is_trivial(statement) for statement in tree.body)


class Stripper(cst.CSTTransformer):
    def __init__(self, mode: Mode, *, remove_blank_lines: bool = False) -> None:
        super().__init__()
        self._mode = mode
        self._remove_blank_lines = remove_blank_lines
        self._doc_suites: set[int] = set()

    def on_leave[N: cst.CSTNode](
        self, original_node: N, updated_node: N
    ) -> N | cst.RemovalSentinel | cst.FlattenSentinel[N]:
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
        if header and index == 0 and value.startswith("#!"):
            return True
        if header and index < 2 and _COOKIE_RE_STR.match(value):
            return True
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
            if _looks_like_code(values):
                kept.extend(run)
                skipping_blanks = False
            else:
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
        return updated_node.with_changes(whitespace=cst.SimpleWhitespace(""), comment=None)

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        if self._mode.docstrings:
            self._doc_suites.add(id(node.body))
        return True

    def visit_AsyncFunctionDef(self, node: cst.AsyncFunctionDef) -> bool | None:
        if self._mode.docstrings:
            self._doc_suites.add(id(node.body))
        return True

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        if self._mode.docstrings:
            self._doc_suites.add(id(node.body))
        return True

    @staticmethod
    def _is_docstring(small: cst.BaseSmallStatement) -> bool:
        if not isinstance(small, cst.Expr):
            return False
        value = small.value
        if isinstance(value, cst.SimpleString | cst.ConcatenatedString):
            return isinstance(value.evaluated_value, str)
        return False

    def _rewrite_smalls(
        self, smalls: Sequence[cst.BaseSmallStatement], *, drop_docstring: bool
    ) -> tuple[list[cst.BaseSmallStatement], bool]:
        out: list[cst.BaseSmallStatement] = []
        changed = False
        for index, small in enumerate(smalls):
            if drop_docstring and index == 0 and self._is_docstring(small):
                changed = True
                continue
            out.append(small)
        if changed and out:
            out[-1] = out[-1].with_changes(semicolon=cst.MaybeSentinel.DEFAULT)
        return out, changed

    def _rewrite(
        self, body: Sequence[cst.BaseStatement], *, drop_docstring: bool
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
        if id(original_node) not in self._doc_suites:
            return updated_node
        body, pending = self._rewrite(updated_node.body, drop_docstring=True)
        if not body:
            body = [cst.SimpleStatementLine(body=[cst.Pass()], leading_lines=pending)]
            pending = []
        return updated_node.with_changes(body=body, footer=[*pending, *updated_node.footer])

    def leave_SimpleStatementSuite(
        self,
        original_node: cst.SimpleStatementSuite,
        updated_node: cst.SimpleStatementSuite,
    ) -> cst.SimpleStatementSuite:
        if id(original_node) not in self._doc_suites:
            return updated_node
        smalls, changed = self._rewrite_smalls(updated_node.body, drop_docstring=True)
        if not changed:
            return updated_node
        return updated_node.with_changes(body=smalls or [cst.Pass()])

    def strip(self, source: str) -> str:
        self._doc_suites = set()
        module = cst.parse_module(source)
        result = module.visit(self).code
        if self._remove_blank_lines:
            result = remove_blank_lines(result)
        ast.parse(result)
        return result

    def strip_file(
        self,
        path: Path,
        *,
        dryrun: bool = False,
        backup: bool = False,
        show_diff: bool = False,
    ) -> StripFileResult:
        started = perf_counter()
        result = StripFileResult(path)
        try:
            if path.is_symlink():
                raise ValueError("Refusing to replace a symbolic link")
            original = path.read_bytes()
            try:
                encoding, _ = tokenize.detect_encoding(io.BytesIO(original).readline)
                source = original.decode(encoding)
            except (SyntaxError, UnicodeDecodeError, LookupError) as exc:
                raise SkipFile(f"{type(exc).__name__}: {exc}") from None
            stripped = self.strip(source)
            if stripped != source:
                data = stripped.encode(encoding)
                actual_encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
                if data.decode(actual_encoding) != stripped:
                    raise ValueError("Output encoding declaration does not match serialized source")
                result.saved = len(original) - len(data)
                result.modified = True
                if show_diff:
                    result.diff = make_diff(source, stripped, path.name)
                if not dryrun:
                    if backup:
                        if path.is_symlink() or path.read_bytes() != original:
                            raise RuntimeError("File changed while processing; not replaced")
                        result.backup = create_backup(path, original)
                    atomic_write(path, data)
        except SkipFile as exc:
            result.skipped = True
            result.skip_reason = str(exc)
            result.saved = 0
            result.modified = False
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.traceback = traceback.format_exc()
            result.saved = 0
            result.modified = False
        result.elapsed = perf_counter() - started
        return result


def make_diff(before: str, after: str, name: str) -> str:
    output: list[str] = []
    for line in difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{name}",
        tofile=f"b/{name}",
    ):
        output.append(line)
        if not line.endswith("\n"):
            output.append("\n\\ No newline at end of file\n")
    return "".join(output)


def create_backup(path: Path, original: bytes) -> Path:
    backup = path.with_name(path.name + ".bak")
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
    protected: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.start[0] != token.end[0]:
            protected.update(range(token.start[0], token.end[0] + 1))
    lines = io.StringIO(source).readlines()
    for row, line in enumerate(lines, 1):
        if line.rstrip("\r\n").endswith("\\"):
            protected.add(row + 1)
        if row <= 2 and _COOKIE_RE_BYTES.search(line.encode("utf-8")):
            protected.update(range(1, row + 1))
    result = "".join(line for row, line in enumerate(lines, 1) if row in protected or line.strip(" \t\f\r\n"))
    before = ast.dump(ast.parse(source), include_attributes=False)
    after = ast.dump(ast.parse(result), include_attributes=False)
    if before != after:
        raise ValueError("Blank-line removal would change the AST; not written")
    return result


@dataclass(frozen=True)
class FileOptions:
    comments: bool = False
    docstrings: bool = False
    blank_lines: bool = False
    dryrun: bool = False
    backup: bool = False
    diff: bool = False


@dataclass(frozen=True)
class Task:
    path: Path
    options: FileOptions


def _init_worker() -> None:
    logger.remove()
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass


def process_file(task: Task) -> StripFileResult:
    started = perf_counter()
    path, options = task.path, task.options
    try:
        stripper = Stripper(
            Mode(comments=options.comments, docstrings=options.docstrings),
            remove_blank_lines=options.blank_lines,
        )
        result = stripper.strip_file(
            path,
            dryrun=options.dryrun,
            backup=options.backup,
            show_diff=options.diff,
        )
    except Exception as exc:
        result = StripFileResult(
            path,
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
        )
    result.elapsed = perf_counter() - started
    return result


def run_tasks(tasks: list[Task], workers: int, chunksize: int | None = None) -> Iterator[StripFileResult]:
    if not tasks:
        return
    workers = min(workers, len(tasks))
    if workers == 1:
        for task in tasks:
            yield process_file(task)
        return
    chunk = chunksize or max(1, min(64, -(-len(tasks) // (4 * workers))))
    with cf.ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as executor:
        yield from executor.map(process_file, tasks, chunksize=chunk)


class WalkFiles(Protocol):
    def __call__(self, root: str | Path, /) -> list[Path]: ...


@dataclass(frozen=True, slots=True)
class DiscoveryIssue:
    path: Path
    error: str


def record_discovery_error(path: Path, exc: Exception, issues: list[DiscoveryIssue] | None) -> None:
    error = f"{type(exc).__name__}: {exc}"
    if issues is not None:
        issues.append(DiscoveryIssue(path, error))
    logger.bind(traceback=traceback.format_exc(), file_path=str(path)).error("Discovery {}: {}", path.name, error)


def is_skip_dir(path: Path) -> bool:
    return path.name in SKIP_DIRS or path.name.endswith(".egg-info")


def is_python_file(path: Path, issues: list[DiscoveryIssue] | None = None) -> bool:
    if path.suffix.lower() == ".py":
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as stream:
            first_line = stream.readline(4096).decode("utf-8", errors="replace")
    except OSError as exc:
        record_discovery_error(path, exc, issues)
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
        candidates: list[str] = []
        try:
            for word in words[1:]:
                candidates.extend(shlex.split(word))
        except ValueError:
            return False
        interpreter = Path(
            next(
                (word for word in candidates if not word.startswith("-") and "=" not in word),
                "",
            )
        ).name
    return re.fullmatch(r"(?:python|pypy)(?:[23](?:\.\d+)*)?", interpreter) is not None


def is_excluded(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return True
    return any(
        fnmatch.fnmatchcase(path.name, pattern) or fnmatch.fnmatchcase(relative, pattern) for pattern in patterns
    )


def pathlib_walk_files(
    root_dir: Path,
    *,
    exclude_dirs: frozenset[str] = frozenset(),
    excludes: tuple[str, ...] = (),
    issues: list[DiscoveryIssue] | None = None,
) -> Iterator[Path]:
    pending = [root_dir]
    while pending:
        directory = pending.pop()
        try:
            for path in directory.iterdir():
                if is_excluded(path, root_dir, excludes):
                    continue
                try:
                    if sys.version_info >= (3, 14):
                        info = path.info
                        if info.is_symlink():
                            continue
                        is_directory = info.is_dir(follow_symlinks=False)
                        is_regular = not is_directory and info.is_file(follow_symlinks=False)
                    else:
                        mode = path.lstat().st_mode
                        is_directory = stat.S_ISDIR(mode)
                        is_regular = stat.S_ISREG(mode)
                    if is_directory:
                        if not is_skip_dir(path) and path.name not in exclude_dirs:
                            pending.append(path)
                    elif is_regular:
                        yield path
                except OSError as exc:
                    record_discovery_error(path, exc, issues)
        except OSError as exc:
            record_discovery_error(directory, exc, issues)


def _fastwalk_parent_ok(parent: Path, root: Path, exclude_dirs: frozenset[str], excludes: tuple[str, ...]) -> bool:
    current = parent
    while current != root and current != current.parent:
        if (
            is_skip_dir(current)
            or current.name in exclude_dirs
            or is_excluded(current, root, excludes)
            or current.is_symlink()
        ):
            return False
        current = current.parent
    return True


def iter_python_files(
    root_dir: Path,
    *,
    use_fastwalk: bool = False,
    exclude_dirs: frozenset[str] = frozenset(),
    excludes: tuple[str, ...] = (),
    issues: list[DiscoveryIssue] | None = None,
) -> Iterator[Path]:
    root_dir = Path(Path(root_dir.expanduser()).absolute())
    mode = root_dir.lstat().st_mode
    if stat.S_ISLNK(mode):
        return
    if stat.S_ISREG(mode):
        if not is_excluded(root_dir, root_dir.parent, excludes) and is_python_file(root_dir, issues):
            yield root_dir
        return
    if not stat.S_ISDIR(mode):
        raise ValueError(f"Input is not a regular file or directory: {root_dir}")
    if use_fastwalk:
        walk_files = cast(WalkFiles, importlib.import_module("fastwalk").walk_files)
        safe_parents: dict[Path, bool] = {root_dir: True}
        for found in walk_files(root_dir):
            path = found if found.is_absolute() else root_dir / found
            try:
                relative = path.relative_to(root_dir)
                if ".." in relative.parts or is_excluded(path, root_dir, excludes):
                    continue
                parent_ok = safe_parents.get(path.parent)
                if parent_ok is None:
                    parent_ok = _fastwalk_parent_ok(path.parent, root_dir, exclude_dirs, excludes)
                    safe_parents[path.parent] = parent_ok
                if parent_ok and stat.S_ISREG(path.lstat().st_mode) and is_python_file(path, issues):
                    yield path
            except (OSError, ValueError) as exc:
                record_discovery_error(path, exc, issues)
    else:
        for path in pathlib_walk_files(root_dir, exclude_dirs=exclude_dirs, excludes=excludes, issues=issues):
            if is_python_file(path, issues):
                yield path


def iter_input_files(
    inputs: Iterable[Path],
    *,
    use_fastwalk: bool = False,
    exclude_dirs: frozenset[str] = frozenset(),
    excludes: tuple[str, ...] = (),
    issues: list[DiscoveryIssue] | None = None,
) -> Iterator[Path]:
    seen: set[Path] = set()
    for input_path in inputs:
        try:
            for path in iter_python_files(
                input_path,
                use_fastwalk=use_fastwalk,
                exclude_dirs=exclude_dirs,
                excludes=excludes,
                issues=issues,
            ):
                if path not in seen:
                    seen.add(path)
                    yield path
        except (OSError, ValueError) as exc:
            record_discovery_error(input_path, exc, issues)


# ---- reporting ---------------------------------------------------------


def write_json_report(path: Path, report: dict[str, object]) -> None:
    data = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def colorize_diff(diff: str, color: bool) -> str:
    if not color:
        return diff
    output: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith(("---", "+++")):
            code = "1"
        elif line.startswith("+"):
            code = "32"
        elif line.startswith("-"):
            code = "31"
        elif line.startswith("@@"):
            code = "36"
        else:
            code = ""
        output.append(f"\033[{code}m{line}\033[0m" if code else line)
    return "".join(output)


def make_rich_ui(color_mode: str) -> tuple[Progress, Callable[[str], None]]:
    from rich.console import Console
    from rich.progress import BarColumn
    from rich.progress import Progress as RichProgress
    from rich.progress import TaskProgressColumn, TextColumn, TimeElapsedColumn
    from rich.text import Text

    force_terminal = True if color_mode == "always" else False if color_mode == "never" else None
    console = Console(stderr=True, force_terminal=force_terminal, no_color=color_mode == "never")
    progress = RichProgress(
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.completed:.0f}/{task.total:.0f}"),
        TimeElapsedColumn(),
        console=console,
    )

    def emit(message: str) -> None:
        console.print(Text.from_ansi(message.rstrip("\n")))

    return progress, emit


# ---- CLI ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Strip comments and docstrings from Python files safely. Shebangs, "
            "module docstrings, tool directives, and commented-out code are "
            "always preserved."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        metavar="PATH",
        help="Files/directories to process (default: current directory recursively)",
    )
    parser.add_argument("--fastwalk", action="store_true", help="Use the optional Rust fastwalk walker")
    parser.add_argument(
        "-c",
        "--comments",
        action="store_true",
        help="Strip comments (default when no mode flag is given)",
    )
    parser.add_argument(
        "-d",
        "--docstrings",
        action="store_true",
        help="Strip function/class docstrings; module docstrings are always kept",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help=(
            "Strip comments and docstrings; shebangs, module docstrings, "
            "directives, and commented-out code are still preserved"
        ),
    )
    parser.add_argument(
        "--remove-blank-lines",
        action="store_true",
        help="Remove blank lines outside strings",
    )
    parser.add_argument("--chunksize", type=int, help="Process-pool batch size (default: adaptive, up to 64)")
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        metavar="NAME",
        help="Additional directory basename to prune (repeatable)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Exclude a basename or path relative to each input directory (repeatable)",
    )
    parser.add_argument("--report-json", type=Path, metavar="PATH", help="Write an atomic JSON run report")
    parser.add_argument(
        "--error-log",
        type=Path,
        default=Path("strip_python.errors.log"),
        help="Error log with tracebacks (default: strip_python.errors.log; created only on error)",
    )
    parser.add_argument("--diff", action="store_true", help="Show unified diffs")
    parser.add_argument("--backup", action="store_true", help="Save originals as NAME.bak; never overwrite backups")
    parser.add_argument("--dryrun", "--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--check", action="store_true", help="Dry run; exit with status 1 if any file would change")
    parser.add_argument("-t", "--time", action="store_true", help="Show processing time for every file")
    parser.add_argument("--rich", action="store_true", help="Rich progress instead of per-file byte reports")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto", help="Color output")
    parser.add_argument("--log-file", type=Path, help="Also append DEBUG-level Loguru logs to this file")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="WARNING",
        help="Console log level (default: WARNING)",
    )
    return parser


def configure_logging(args: argparse.Namespace, emit: Callable[[str], None] | None) -> bool:
    logger.remove()
    logger.configure(extra={"traceback": "", "file_path": ""})
    log_format = "<level>{level: <7}</level> | {message}"
    if emit is not None:
        logger.add(emit, level=args.log_level, format=log_format, colorize=False)
    else:
        logger.add(
            sys.stderr,
            level=args.log_level,
            format=log_format,
            colorize=args.color == "always"
            or (args.color == "auto" and sys.stderr.isatty() and "NO_COLOR" not in os.environ),
        )
    file_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {extra[file_path]} | {message}\n{extra[traceback]}"
    try:
        logger.add(
            args.error_log,
            level="ERROR",
            encoding="utf-8",
            delay=True,
            rotation="5 MB",
            retention=3,
            diagnose=False,
            format=file_format,
        )
        if args.log_file is not None:
            logger.add(
                args.log_file,
                level="DEBUG",
                encoding="utf-8",
                diagnose=False,
                format=file_format,
            )
    except (OSError, ValueError) as exc:
        logger.exception("Cannot configure log output: {}", exc)
        return False
    return True


def main() -> int:
    parser = build_parser()
    args = parser.parse_intermixed_args()
    # -a is shorthand for -c -d.
    if args.all:
        args.c = True
        args.d = True
    # Default (no mode flags) = comments only.
    if not (args.c or args.d or args.remove_blank_lines):
        args.c = True
    if args.check:
        args.dryrun = True
    if MAX_WORKERS < 1:
        parser.error("--workers must be at least 1")
    if args.chunksize is not None and args.chunksize < 1:
        parser.error("--chunksize must be at least 1")
    if any(not name or "/" in name or "\\" in name for name in args.exclude_dir):
        parser.error("--exclude-dir accepts a directory basename, not a path; use --exclude for patterns")
    dependencies: dict[str, str] = {"libcst": "libcst"}
    if args.rich:
        dependencies["rich"] = "rich"
    missing = [package for module, package in dependencies.items() if importlib.util.find_spec(module) is None]
    if missing:
        parser.error("install dependencies: python -m pip install " + " ".join(missing))
    color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ)
    progress: Progress | None = None
    emit: Callable[[str], None] | None = None
    if args.rich:
        progress, emit = make_rich_ui(args.color)
    if not configure_logging(args, emit):
        return 1
    started = perf_counter()
    discovery_issues: list[DiscoveryIssue] = []
    try:
        inputs: list[Path] = args.inputs or [Path.cwd()]
        for input_path in inputs:
            expanded = input_path.expanduser()
            if not Path(expanded).exists() or Path(expanded).is_symlink():
                raise FileNotFoundError(f"Input does not exist: {input_path}")
        if args.fastwalk and not callable(getattr(importlib.import_module("fastwalk"), "walk_files", None)):
            raise AttributeError("fastwalk.walk_files is missing or is not callable")
        outputs = {
            Path(Path(output).absolute())
            for output in (args.error_log, args.log_file, args.report_json)
            if output is not None
        }
        paths = sorted(
            path
            for path in iter_input_files(
                inputs,
                use_fastwalk=args.fastwalk,
                exclude_dirs=frozenset(args.exclude_dir),
                excludes=tuple(args.exclude),
                issues=discovery_issues,
            )
            if path not in outputs
        )
    except (ImportError, AttributeError) as exc:
        logger.exception(
            "Cannot load fastwalk.walk_files API: {}. Install your custom fastwalk package.",
            exc,
        )
        return 1
    except Exception as exc:
        logger.exception("File discovery failed: {}", exc)
        return 1
    options = FileOptions(
        comments=args.c,
        docstrings=args.d,
        blank_lines=args.remove_blank_lines,
        dryrun=args.dryrun,
        backup=args.backup,
        diff=args.diff,
    )
    tasks = [Task(path, options) for path in paths]
    logger.info(
        "Found {} Python files; backend=libcst, workers={}, dryrun={}",
        len(paths),
        MAX_WORKERS,
        args.dryrun,
    )

    def green(number: int) -> str:
        text = f"{number:,}"
        return f"\033[32m{text}\033[0m" if color else text

    total = changed = processed = skipped = 0
    errors = len(discovery_issues)
    report_rows: list[dict[str, object]] = []
    width = max((len(path.name) for path in paths), default=0)
    progress_id: TaskID | None = None
    context = progress if progress is not None else nullcontext()
    with context:
        if progress is not None:
            progress_id = progress.add_task("Processing", total=len(paths))
        try:
            for result in run_tasks(tasks, MAX_WORKERS, args.chunksize):
                processed += 1
                if args.report_json is not None:
                    report_rows.append({
                        "path": str(result.path),
                        "modified": result.modified,
                        "bytes_reduced": result.saved,
                        "elapsed_seconds": result.elapsed,
                        "error": result.error,
                        "skipped": result.skipped,
                        "skip_reason": result.skip_reason or None,
                        "backup": str(result.backup) if result.backup else None,
                    })
                if result.error:
                    errors += 1
                    logger.bind(traceback=result.traceback, file_path=str(result.path)).error(
                        "{}: {}", result.path.name, result.error
                    )
                elif result.skipped:
                    skipped += 1
                    logger.warning("Skipped {}: {}", result.path, result.skip_reason)
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
                        if emit is not None:
                            emit(diff)
                        else:
                            print(diff, end="", flush=True)
                    if progress is None:
                        timing = f"  {result.elapsed:.4f}s" if args.time else ""
                        print(
                            f"{result.path.name:<{width}}  {green(result.saved)}{timing}",
                            flush=True,
                        )
                if progress is not None and progress_id is not None:
                    progress.update(progress_id, advance=1, description=result.path.name)
                    if args.time and emit is not None:
                        emit(f"{result.path.name}  {result.elapsed:.4f}s")
                elif args.time and (result.error or result.skipped):
                    print(f"{result.path.name:<{width}}  {result.elapsed:.4f}s", flush=True)
        except Exception as exc:
            errors += 1
            if isinstance(exc, cf.process.BrokenProcessPool):
                logger.error("A worker process died (crash or out-of-memory). Try -j 1 or --chunksize 1.")
            logger.exception("Execution backend failed: {}", exc)
    if args.report_json is not None:
        try:
            write_json_report(
                args.report_json,
                {
                    "version": 3,
                    "dryrun": options.dryrun,
                    "backend": "libcst",
                    "pool": "cf.process",
                    "walker": "fastwalk" if args.fastwalk else "pathlib",
                    "discovered": len(paths),
                    "processed": processed,
                    "complete": processed == len(paths),
                    "unreported_files": len(paths) - processed,
                    "changed": changed,
                    "skipped": skipped,
                    "errors": errors,
                    "source_bytes_reduced": total,
                    "elapsed_seconds": perf_counter() - started,
                    "discovery_errors": [{"path": str(issue.path), "error": issue.error} for issue in discovery_issues],
                    "files": report_rows,
                },
            )
        except Exception as exc:
            errors += 1
            logger.exception("Cannot write JSON report {}: {}", args.report_json, exc)
    if args.dryrun:
        label = "Potential source bytes reduced"
    elif args.backup:
        label = "Total source bytes reduced"
    else:
        label = "Total space freed"
    print(f"\n{label}: {green(total)} bytes")
    if args.dryrun:
        print(f"Dry run: {changed} file(s) would change; no source files or backups written.")
    if skipped:
        print(f"Skipped (syntax/encoding errors): {skipped}")
    if args.backup and not args.dryrun:
        print("Backup copies consume additional space; byte totals count source files only.")
    if errors:
        print(
            f"Errors: {errors}; details logged to {args.error_log.name}",
            file=sys.stderr,
        )
    if args.time:
        print(f"Total elapsed: {perf_counter() - started:.4f}s")
    logger.info(
        "Finished: {} changed, {} skipped, {} errors, {} source bytes reduced, {:.4f}s",
        changed,
        skipped,
        errors,
        total,
        perf_counter() - started,
    )
    if errors:
        return 1
    return 1 if args.check and changed else 0


def entrypoint() -> int:
    try:
        return main()
    except KeyboardInterrupt:
        logger.error("Interrupted; already committed source updates are not rolled back.")
        return 130
    except Exception:
        logger.exception("Fatal unhandled error")
        return 1


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(entrypoint())
