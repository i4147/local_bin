#!/data/data/com.termux/files/usr/bin/env python
"""Low-memory Termux Python source stripper (Python 3.11+, stdlib only).
Default: current directory recursively; -c -d; one file at a time.
Bounded workloads: 1 MiB/file, 20,000 lines, 50,000 tokens, depth 64.
These limits reduce OOM risk; they are NOT a formal/hard process RAM limit.
No pools, native parsers, Rust walker, Rich, Loguru, or third-party packages.
"""

from __future__ import annotations
import argparse
import ast
from collections.abc import Iterator
from dataclasses import asdict, dataclass
import difflib
import fnmatch
import io
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
from time import perf_counter
import tokenize
import traceback

from loguru import logger


log = logger
LOGGER = logger
_Edit = tuple[int, int, bytes]


@dataclass
class StripFileResult:
    """Store path, saved, modified, error, elapsed and related state."""

    path: Path
    saved: int = 0
    modified: bool = False
    error: str | None = None
    elapsed: float = 0.0
    diff: str = ""
    backup: Path | None = None
    traceback: str = ""
    skipped: str | None = None


class ASTStripper:
    """AST docstring ranges and tokenize comments; never regenerates code."""

    # Extend this list for project-specific tools. Deliberately conservative:
    # comments resembling tool instructions are retained rather than removed.
    _DIRECTIVE_RE = re.compile(
        r"^#\s*(?:type|fmt|mypy|pylint|ruff|pyright|basedpyright|noqa|"
        r"nosec|bandit|isort|yapf|autopep8|flake8|black|pyre|pytype|"
        r"pragma|coverage|doctest|pytest|cython|distutils|numba|"
        r"region|endregion|uv)\b|^#\s*(?:%%|</?editor-fold\b)",
        re.IGNORECASE,
    )
    _COOKIE_RE = re.compile(rb"coding[:=]\s*[-\w.]+")

    def __init__(
        self,
        strip_comments: bool,
        strip_docstrings: bool,
        remove_blank_lines: bool = False,
        strip_all: bool = False,
    ) -> None:
        """Initialize the instance state from the supplied arguments."""
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
        if self.strip_comments and b"#" in src:
            src = self._apply(src, self._collect_comments(src))
        result = src.decode("utf-8")
        if self.remove_blank_lines:
            result = remove_blank_lines(result)
        if result != source and not self.remove_blank_lines:
            # Blank-line removal already validates both ASTs itself.
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
            if path.is_symlink() or read_bounded(path, len(original)) != original:
                raise RuntimeError("File changed while processing; not replaced")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def strip_file(
        self,
        path: Path,
        *,
        dryrun: bool = False,
        backup: bool = False,
        show_diff: bool = False,
        limits: Limits | None = None,
    ) -> StripFileResult:
        """Strip file."""
        started = perf_counter()
        limits = limits or Limits()
        result = StripFileResult(path)
        try:
            if path.is_symlink():
                raise ValueError("Refusing to replace a symbolic link")
            if path.stat().st_size > limits.max_bytes:
                raise BudgetExceeded(f"file exceeds {limits.max_bytes:,} bytes")
            original = read_bounded(path, limits.max_bytes)
            encoding, _ = tokenize.detect_encoding(io.BytesIO(original).readline)
            source = original.decode(encoding)
            check_budget(source, limits)
            stripped = self.strip(source)
            if stripped != source:
                # Without its encoding cookie, non-UTF-8 Python must be transcoded.
                output_encoding = "utf-8" if self.strip_all and self.strip_comments else encoding
                data = stripped.encode(output_encoding)
                # strip() validated the Unicode AST. Verify the serialized bytes
                # decode to that same text under Python's own cookie/BOM rules,
                # rather than parsing the whole module a redundant third time.
                actual_encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
                if data.decode(actual_encoding) != stripped:
                    raise ValueError("Output encoding declaration does not match serialized source")
                result.saved = len(original) - len(data)
                result.modified = True  # In dry runs this means "would modify".
                if show_diff:
                    result.diff = make_diff(source, stripped, path.name)
                if not dryrun:
                    if backup:
                        if path.is_symlink() or read_bounded(path, len(original)) != original:
                            raise RuntimeError("File changed while processing; not replaced")
                        result.backup = create_backup(path, original)
                    self._atomic_write(path, data, original)
        except BudgetExceeded as exc:
            result.skipped = str(exc)
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
    """Unified diff with explicit markers for missing final newlines."""
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


def colorize_diff(diff: str, color: bool) -> str:
    """Colorize diff."""
    if not color:
        return diff
    output: list[str] = []
    for line in diff.splitlines(keepends=True):
        code = (
            "1"
            if line.startswith(("---", "+++"))
            else "32"
            if line.startswith("+")
            else "31"
            if line.startswith("-")
            else "36"
            if line.startswith("@@")
            else ""
        )
        output.append(f"\033[{code}m{line}\033[0m" if code else line)
    return "".join(output)


@dataclass(frozen=True)
class Limits:
    """Store max_bytes, max_lines, max_tokens, max_depth."""

    max_bytes: int = 1_048_576
    max_lines: int = 20_000
    max_tokens: int = 50_000
    max_depth: int = 64


class BudgetExceeded(ValueError):
    """Signal an error raised by this module."""


def read_bounded(path: Path, limit: int) -> bytes:
    """Read bounded."""
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise BudgetExceeded(f"file exceeds {limit:,} bytes or grew while processing")
    return data


def check_budget(source: str, limits: Limits) -> None:
    """Check budget."""
    if source.count("\n") + 1 > limits.max_lines:
        raise BudgetExceeded(f"file exceeds {limits.max_lines:,} lines")
    for count, _ in enumerate(tokenize.generate_tokens(io.StringIO(source).readline), 1):
        if count > limits.max_tokens:
            raise BudgetExceeded(f"file exceeds {limits.max_tokens:,} tokens")


def is_python_file(path: Path) -> bool:
    """Return whether python file."""
    if path.suffix.lower() == ".py":
        return True
    if path.suffix:
        return False
    with path.open("rb") as stream:
        first = stream.readline(4096).decode("utf-8", errors="replace")
    if not first.startswith("#!"):
        return False
    try:
        words = shlex.split(first[2:])
        if not words:
            return False
        program = Path(words[0]).name
        if program == "env":
            expanded = [part for word in words[1:] for part in shlex.split(word)]
            program = next(
                (word for word in expanded if not word.startswith("-") and "=" not in word),
                "",
            )
        return re.fullmatch(r"(?:python|pypy)(?:[23](?:\.\d+)*)?", Path(program).name) is not None
    except ValueError:
        return False


def has_symlink_component(path: Path) -> bool:
    """Return whether symlink component."""
    return path.is_symlink() or any(parent.is_symlink() for parent in path.parents)


def excluded(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    """Return whether excluded."""
    relative = path.relative_to(root).as_posix()
    return any(
        fnmatch.fnmatchcase(path.name, pattern) or fnmatch.fnmatchcase(relative, pattern) for pattern in patterns
    )


def normalize_inputs(inputs: list[Path]) -> list[Path]:
    """Deduplicate input roots, not every discovered file (bounded by CLI size)."""
    paths = sorted(
        {path.expanduser().absolute() for path in inputs},
        key=lambda path: (len(path.parts), str(path)),
    )
    roots: list[Path] = []
    for path in paths:
        if has_symlink_component(path):
            LOGGER.warning("Skipping symlink input: {}", path)
            continue
        if not path.exists():
            raise FileNotFoundError(f"Input does not exist: {path}")
        if not path.is_dir() and not path.is_file():
            raise ValueError(f"Not a regular file/directory: {path}")
        if any(root.is_dir() and path.is_relative_to(root) for root in roots):
            continue
        roots.append(path)
    return roots


@dataclass
class Stats:
    """Store files, changed, skipped, errors, bytes_reduced."""

    files: int = 0
    changed: int = 0
    skipped: int = 0
    errors: int = 0
    bytes_reduced: int = 0


def walk_python_files(
    roots: list[Path],
    *,
    limits: Limits,
    skip_dirs: frozenset[str],
    patterns: tuple[str, ...],
    outputs: set[Path],
    stats: Stats,
) -> Iterator[Path]:
    """Walk python files."""

    def visit(directory: Path, root: Path, depth: int) -> Iterator[Path]:
        if depth > limits.max_depth:
            stats.skipped += 1
            LOGGER.warning("Depth limit reached: {}", directory)
            return
        try:
            if directory.is_symlink():
                return
            # Do not use Path.iterdir(): some Python releases materialize the
            # entire directory. scandir keeps only O(depth) open iterators.
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if path in outputs or excluded(path, root, patterns):
                        continue
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if path.name not in skip_dirs and not path.name.endswith(".egg-info"):
                                yield from visit(path, root, depth + 1)
                        elif entry.is_file(follow_symlinks=False) and is_python_file(path):
                            yield path
                    except OSError:
                        stats.errors += 1
                        LOGGER.exception("Cannot inspect {}", path)
        except OSError:
            stats.errors += 1
            LOGGER.exception("Cannot traverse {}", directory)

    for root in roots:
        if root.is_dir():
            if root.name not in skip_dirs and not root.name.endswith(".egg-info"):
                yield from visit(root, root, 0)
        elif root not in outputs and root.parent.name not in skip_dirs:
            try:
                if not excluded(root, root.parent, patterns) and is_python_file(root):
                    yield root
            except OSError:
                stats.errors += 1
                LOGGER.exception("Cannot inspect {}", root)


class StreamingReport:
    """Atomic JSON array output without retaining all file results in RAM."""

    def __init__(self, path: Path) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.path = path
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        self.temporary = Path(name)
        self.stream = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        self.first = True
        self.stream.write('{"version":1,"files":[')

    def add(self, result: StripFileResult) -> None:
        data = asdict(result)
        data["path"] = str(result.path)
        data["backup"] = str(result.backup) if result.backup else None
        # Detailed tracebacks go in the error log, not the machine report.
        data.pop("traceback", None)
        data.pop("diff", None)
        if not self.first:
            self.stream.write(",")
        json.dump(data, self.stream, ensure_ascii=False)
        self.first = False

    def finish(self, stats: Stats, dryrun: bool, elapsed: float) -> None:
        self.stream.write('],"summary":')
        json.dump({**asdict(stats), "dryrun": dryrun, "elapsed_seconds": elapsed}, self.stream)
        self.stream.write("}\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        self.temporary.replace(self.path)

    def close(self) -> None:
        self.stream.close()
        self.temporary.unlink(missing_ok=True)


def configure_logging(error_log: Path, log_file: Path | None, level: str) -> None:
    """Configure logging."""
    logger.remove()
    logger.add(sys.stderr, level=level.upper(), format="{level} | {message}")
    logger.add(
        error_log,
        level="ERROR",
        rotation=1_000_000,
        retention=2,
        encoding="utf-8",
        delay=True,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    )
    if log_file is not None:
        logger.add(
            log_file,
            level="DEBUG",
            rotation=2_000_000,
            retention=2,
            encoding="utf-8",
            delay=True,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        )


def main() -> int:
    """Run the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="*", type=Path, metavar="PATH")
    parser.add_argument("-c", action="store_true", help="Remove ordinary comments")
    parser.add_argument("-d", action="store_true", help="Remove function/class docstrings")
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Remove all comments/docstrings, including normally preserved ones",
    )
    parser.add_argument("--remove-blank-lines", action="store_true")
    parser.add_argument("--dryrun", "--dry-run", action="store_true")
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--diff", action="store_true")
    parser.add_argument("-t", "--time", action="store_true")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    parser.add_argument("--exclude-dir", action="append", default=[], metavar="NAME")
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=1_048_576,
        help="Per-file input cap (default: 1048576)",
    )
    parser.add_argument("--max-lines", type=int, default=20_000)
    parser.add_argument("--max-tokens", type=int, default=50_000)
    parser.add_argument(
        "--max-depth",
        type=int,
        default=64,
        help="Maximum traversal depth (default: 64)",
    )
    parser.add_argument("--report-json", type=Path, help="Stream a JSON report to disk atomically")
    parser.add_argument("--error-log", type=Path, default=Path("striper_termux.errors.log"))
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="WARNING")
    args = parser.parse_intermixed_args()
    if any(value < 1 for value in (args.max_bytes, args.max_lines, args.max_tokens, args.max_depth)):
        parser.error("All resource limits must be positive; zero/unlimited is not supported")
    if any(not name or "/" in name or "\\" in name for name in args.exclude_dir):
        parser.error("--exclude-dir requires a directory basename")
    if not (args.c or args.d or args.all or args.remove_blank_lines):
        args.c = args.d = True
    configure_logging(args.error_log, args.log_file, args.log_level)
    started = perf_counter()
    stats = Stats()
    report: StreamingReport | None = None
    interrupted = False
    try:
        roots = normalize_inputs(args.inputs or [Path.cwd()])
        limits = Limits(args.max_bytes, args.max_lines, args.max_tokens, args.max_depth)
        if args.report_json is not None:
            report = StreamingReport(args.report_json)
        outputs = {
            Path(path).absolute()
            for path in (
                args.error_log,
                args.log_file,
                args.report_json,
                report.temporary if report else None,
            )
            if path is not None
        }
        skip_dirs = SKIP_DIRS | frozenset(args.exclude_dir)
        stripper = ASTStripper(args.c, args.d, args.remove_blank_lines, args.all)
        color = args.color == "always" or (
            args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        )
        if args.all:
            LOGGER.warning("--all removes shebangs, encoding comments, tool directives and module docstrings")
        for path in walk_python_files(
            roots,
            limits=limits,
            skip_dirs=skip_dirs,
            patterns=tuple(args.exclude),
            outputs=outputs,
            stats=stats,
        ):
            result = stripper.strip_file(
                path,
                dryrun=args.dryrun,
                backup=args.backup,
                show_diff=args.diff,
                limits=limits,
            )
            stats.files += 1
            if result.skipped:
                stats.skipped += 1
                LOGGER.warning("{}: skipped: {}", path.name, result.skipped)
            elif result.error:
                stats.errors += 1
                LOGGER.error("{}: {}\n{}", path, result.error, result.traceback)
            else:
                stats.changed += result.modified
                stats.bytes_reduced += result.saved
                if result.diff:
                    print(colorize_diff(result.diff, color), end="")
                number = f"{result.saved:,}"
                if color:
                    number = f"\033[32m{number}\033[0m"
                timing = f"  {result.elapsed:.4f}s" if args.time else ""
                print(f"{path.name}  {number}{timing}")
                LOGGER.debug(
                    "{}: modified={}, saved={}, seconds={:.6f}",
                    path,
                    result.modified,
                    result.saved,
                    result.elapsed,
                )
            if args.time and (result.error or result.skipped):
                print(f"{path.name}  {result.elapsed:.4f}s")
            if report is not None:
                report.add(result)
            # Drop each source/diff/result before beginning the next file.
            del result
    except KeyboardInterrupt:
        interrupted = True
        stats.errors += 1
        LOGGER.error("Interrupted; previously committed source changes are not rolled back")
    except Exception:
        stats.errors += 1
        LOGGER.exception("Termux run failed")
    finally:
        if report is not None:
            try:
                report.finish(stats, args.dryrun, perf_counter() - started)
            except Exception:
                stats.errors += 1
                LOGGER.exception("Cannot finalize JSON report")
            finally:
                report.close()
    label = (
        "Potential source bytes reduced"
        if args.dryrun
        else "Total source bytes reduced"
        if args.backup
        else "Total space freed"
    )
    print(f"\n{label}: {stats.bytes_reduced:,} bytes")
    print(f"Files: {stats.files}; changed: {stats.changed}; skipped: {stats.skipped}; errors: {stats.errors}")
    if args.dryrun:
        print("Dry run: no source files or backups written; logs/reports may be written.")
    if args.backup and not args.dryrun:
        print("Backups consume extra space; totals count source files only.")
    if args.time:
        print(f"Total elapsed: {perf_counter() - started:.4f}s")
    # A limited scan is not silently reported as complete success.
    return 130 if interrupted else 1 if stats.errors else 3 if stats.skipped else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception:
        LOGGER.exception("Fatal error")
        raise SystemExit(1) from None
