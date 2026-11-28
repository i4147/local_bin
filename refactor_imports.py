#!/data/data/com.termux/files/usr/bin/env python
"""Refactor qualified Python imports into direct attribute imports.

By default this tool rewrites files **in place**. With no positional paths it
recursively processes Python files under the current directory. Typer is used
when available; otherwise the script falls back to argparse.
"""

import sys

if sys.version_info < (3, 10):
    raise SystemExit("refactor-imports requires Python 3.10 or newer")

import argparse
import difflib
import io
import os
import re
import stat
import tempfile
import tokenize
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from textwrap import dedent
from typing import Annotated, Any, BinaryIO, Final, TypeAlias, cast

PROG: Final = Path(sys.argv[0]).name or "refactor-imports"
VERSION: Final = "2.0.0"

try:
    import libcst as cst
    import libcst.metadata as md
    from libcst import matchers as m
    from libcst.helpers import get_full_name_for_node
except ModuleNotFoundError as exc:
    raise SystemExit(f"{PROG}: libcst is required; install it with 'python -m pip install libcst' ({exc})") from exc

Import: TypeAlias = cst.Import | cst.ImportFrom
Line: TypeAlias = cst.SimpleStatementLine | cst.SimpleStatementSuite
Small: TypeAlias = cst.BaseSmallStatement
Parents: TypeAlias = Mapping[cst.CSTNode, cst.CSTNode]
DEFAULT: Final = cst.MaybeSentinel.DEFAULT

DEFAULT_EXTENSIONS: Final[tuple[str, ...]] = (".py",)
DEFAULT_EXCLUDES: Final[tuple[str, ...]] = (
    ".git",
    ".hg",
    ".svn",
    ".bzr",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    ".env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
    ".idea",
    ".vscode",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "*.egg-info",
    "*.egg",
    ".eggs",
)
ANSI_RESET: Final = "\033[0m"
ANSI_GREEN: Final = "\033[32m"
ANSI_RED: Final = "\033[31m"
ANSI_CYAN: Final = "\033[36m"


class UsageError(Exception):
    """A command-line usage error that both parser backends can report."""


class Color(str, Enum):
    auto = "auto"
    always = "always"
    never = "never"

    def __str__(self) -> str:
        return self.value


class ParserBackend(str, Enum):
    auto = "auto"
    typer = "typer"
    argparse = "argparse"

    def __str__(self) -> str:
        return self.value


class Mode(str, Enum):
    write = "write"
    dry_run = "dry_run"
    diff = "diff"
    check = "check"
    stdout = "stdout"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Options:
    paths: tuple[Path, ...] = ()
    mode: Mode = Mode.write
    color: Color = Color.auto
    context: int = 3
    conservative: bool = False
    skip: tuple[str, ...] = ()
    keep_imports: bool = False
    include_from_imports: bool = False
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    exclude: tuple[str, ...] = DEFAULT_EXCLUDES
    follow_symlinks: bool = False
    jobs: int = 1
    max_passes: int = 100
    quiet: bool = False
    verbose: bool = False
    backend: ParserBackend = ParserBackend.auto
    requested_backend: ParserBackend = ParserBackend.auto


@dataclass(frozen=True, slots=True)
class FileResult:
    path: Path
    changed: bool = False
    before: str = ""
    after: str = ""
    candidate: bytes = b""
    encoding: str = "utf-8"
    error: str | None = None


def fresh(attribute: str, occupied: set[str]) -> str:
    base = name = f"_{attribute}"
    n = 2
    while name in occupied:
        name = f"{base}_{n}"
        n += 1
    occupied.add(name)
    return name


def successors(parents: Parents) -> dict[Small, Small]:
    result: dict[Small, Small] = {}
    for block in set(parents.values()):
        if isinstance(block, (cst.SimpleStatementLine, cst.SimpleStatementSuite)):
            result.update(zip(block.body, block.body[1:]))
        elif isinstance(block, (cst.Module, cst.IndentedBlock)):
            for left, right in zip(block.body, block.body[1:]):
                if isinstance(left, cst.SimpleStatementLine) and isinstance(right, cst.SimpleStatementLine):
                    result[left.body[-1]] = right.body[0]
    return result


def legacy_names(stmt: Import, paths: set[str], following: Mapping[Small, Small]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    peer: Small | None = following.get(stmt)
    while isinstance(peer, cst.ImportFrom) and not peer.relative and peer.module is not None:
        if isinstance(peer.names, cst.ImportStar) or not all(
            re.fullmatch(rf"_{re.escape(a.evaluated_name)}(?:_\d+)?", a.evaluated_alias or "") for a in peer.names
        ):
            break
        origin: str | None = get_full_name_for_node(peer.module)
        if origin is None or origin not in paths:
            break
        result.setdefault(origin, {}).update((a.evaluated_name, a.evaluated_alias or "") for a in peer.names)
        peer = following.get(peer)
    return result


def standalone(stmt: Import, parents: Parents) -> bool:
    line: cst.CSTNode = parents[stmt]
    parent: cst.CSTNode | None = parents.get(line)
    return isinstance(line, cst.SimpleStatementLine) and len(line.body) == 1 and isinstance(parent, cst.Module)


def imported_paths(stmt: Import, include_from: bool) -> list[tuple[cst.ImportAlias, str]]:
    prefix = ""
    if isinstance(stmt, cst.ImportFrom):
        if not include_from or stmt.relative or stmt.module is None:
            return []
        origin: str | None = get_full_name_for_node(stmt.module)
        if not origin or origin == "__future__":
            return []
        prefix = origin + "."
    if isinstance(stmt.names, cst.ImportStar):
        return []
    return [(a, prefix + a.evaluated_name) for a in stmt.names]


def syntax_sensitive(node: cst.Attribute, parents: Parents) -> bool:
    name = node.attr.value
    if name.startswith("__") and not name.endswith("__"):
        return True
    parent: cst.CSTNode = parents[node]
    if isinstance(parent, (cst.MatchValue, cst.MatchMappingElement)):
        return True
    while not isinstance(parent, cst.BaseStatement) and parent in parents:
        if isinstance(parent, cst.FormattedStringExpression) and parent.equal is not None:
            return True
        parent = parents[parent]
    return False


class Planner:
    def __init__(
        self,
        wrapper: md.MetadataWrapper,
        keep_imports: bool,
        skip: Collection[str],
        conservative: bool,
    ) -> None:
        self.module: cst.Module = wrapper.module
        self.parents: Parents = wrapper.resolve(md.ParentNodeProvider)
        self.scopes: Mapping[cst.CSTNode, md.Scope] = wrapper.resolve(md.ScopeProvider)
        self.contexts: Mapping[cst.CSTNode, md.ExpressionContext] = wrapper.resolve(md.ExpressionContextProvider)
        self.keep_imports: bool = keep_imports
        self.conservative: bool = conservative
        self.positions: Mapping[cst.CSTNode, md.CodeRange] = (
            wrapper.resolve(md.PositionProvider) if conservative else {}
        )
        self.following: Mapping[Small, Small] = successors(self.parents)
        self.occupied: set[str] = {n.value for n in self.parents if isinstance(n, cst.Name)}
        self.pinned: set[str] = set()
        self.ambiguous: set[str] = set()
        self.reads: dict[cst.Attribute, str] = {}
        self.statements: dict[Small, list[Small]] = {}
        self.star_scopes: set[md.Scope | None] = {
            self.scopes.get(n)
            for n in self.parents
            if isinstance(n, cst.ImportFrom) and isinstance(n.names, cst.ImportStar)
        }
        for node, parent in self.parents.items():
            if isinstance(node, cst.Comment) and node.value.startswith("# type:"):
                self.pinned.update(re.findall(r"\w+", node.value))
            if not isinstance(node, cst.Name):
                continue
            if isinstance(parent, (cst.NameItem, cst.MatchAs, cst.MatchStar, cst.MatchMapping)):
                self.ambiguous.add(node.value)
            if node.value in {"__all__", "globals", "locals", "vars"}:
                while parent in self.parents and not isinstance(parent, (Small, cst.BaseStatement)):
                    parent = self.parents[parent]
                for literal in m.findall(parent, m.SimpleString() | m.ConcatenatedString()):
                    if isinstance(literal, (cst.SimpleString, cst.ConcatenatedString)):
                        value: object | None = literal.evaluated_value
                        if isinstance(value, str) and value.isidentifier():
                            self.pinned.add(value)
        self.occupied.update(self.pinned)
        self.blocked: set[str] = set(skip) | {
            q.name
            for node, ctx in self.contexts.items()
            if isinstance(node, cst.Attribute) and ctx != md.ExpressionContext.LOAD
            if (scope := self.scopes.get(node)) is not None
            for q in scope.get_qualified_names_for(node)
            if q.source is md.QualifiedNameSource.IMPORT
        }

    def plan_alias(
        self,
        stmt: Import,
        alias: cst.ImportAlias,
        path: str,
        legacy: Mapping[str, str],
    ) -> tuple[bool, cst.ImportFrom | None] | None:
        spelling = alias.evaluated_alias or alias.evaluated_name
        root = spelling.split(".", 1)[0]
        scope: md.Scope | None = self.scopes.get(alias)
        if scope is None or scope in self.star_scopes or path == "__future__" or root in self.ambiguous:
            return None
        roots = scope.assignments[root]
        full = scope.assignments[spelling]
        if not roots or len(full) != 1:
            return None
        if len(roots) > 1 and any(
            not isinstance(a, md.ImportAssignment)
            or a.get_qualified_names_for(root) != {md.QualifiedName(root, md.QualifiedNameSource.IMPORT)}
            for a in roots
        ):
            return None
        binding = next(iter(full))
        if not isinstance(binding, md.ImportAssignment) or binding.node is not stmt:
            return None
        if any(path == name or path.startswith(name + ".") for name in self.blocked):
            return None

        keep = (
            self.keep_imports
            or root in self.pinned
            or isinstance(scope, md.ClassScope)
            or alias.evaluated_alias == alias.evaluated_name
        )
        prefix = spelling
        while "." in prefix:
            prefix = prefix.rsplit(".", 1)[0]
            keep |= any(a.references for a in scope.assignments[prefix])

        reads: defaultdict[str, list[cst.Attribute]] = defaultdict(list)
        for ref in binding.references:
            attr: cst.CSTNode | None = self.parents.get(ref.node)
            if (
                not isinstance(attr, cst.Attribute)
                or attr.value is not ref.node
                or set(ref.referents) != {binding}
                or (root != spelling and ref.scope[root] != roots)
                or (self.conservative and self.positions[attr].start.line <= self.positions[stmt].start.line)
                or self.contexts.get(attr) != md.ExpressionContext.LOAD
                or f"{path}.{attr.attr.value}" in self.blocked
                or syntax_sensitive(attr, self.parents)
                or "#" in self.module.code_for_node(attr)
            ):
                keep = True
            else:
                reads[attr.attr.value].append(attr)

        names: list[cst.ImportAlias] = []
        for field, uses in sorted(reads.items()):
            existing: str | None = legacy.get(field)
            if (
                existing
                and len(scope.assignments[existing]) == 1
                and all((here := self.scopes.get(n)) is not None and here[existing] == scope[existing] for n in uses)
            ):
                local = existing
            else:
                local = fresh(field, self.occupied)
                names.append(cst.ImportAlias(cst.Name(field), asname=cst.AsName(cst.Name(local))))
            self.reads.update((n, local) for n in uses)
        if not names:
            return (keep, None) if legacy else None
        module: cst.BaseExpression = cst.parse_expression(path)
        assert isinstance(module, (cst.Name, cst.Attribute))
        return keep, cst.ImportFrom(module, names)

    def plan(self, include_from_imports: bool) -> None:
        if self.conservative and self.star_scopes:
            return
        candidates: dict[Import, list[tuple[cst.ImportAlias, str]]] = {
            stmt: imported_paths(stmt, include_from_imports)
            for stmt in self.parents
            if isinstance(stmt, (cst.Import, cst.ImportFrom))
            and (not self.conservative or standalone(stmt, self.parents))
        }
        counts: Counter[str] = Counter(path for pairs in candidates.values() for path in {p for _, p in pairs})
        for stmt, pairs in candidates.items():
            if not pairs or "#" in self.module.code_for_node(stmt):
                continue
            legacy: dict[str, dict[str, str]] = legacy_names(stmt, {p for _, p in pairs}, self.following)
            parts: list[Small] = []
            changed = False
            for alias, path in pairs:
                edit: tuple[bool, cst.ImportFrom | None] | None = None
                if not (alias.evaluated_alias or "").startswith("_") and (not self.conservative or counts[path] == 1):
                    edit = self.plan_alias(stmt, alias, path, legacy.get(path, {}))
                keep, direct = edit if edit is not None else (True, None)
                changed |= edit is not None
                if keep:
                    parts.append(stmt.with_changes(names=[alias.with_changes(comma=DEFAULT)]))
                if direct is not None:
                    parts.append(direct)
            if changed:
                self.statements[stmt] = parts


class Rewrite(cst.CSTTransformer):
    def __init__(self, plan: Planner) -> None:
        self.plan: Planner = plan
        self.leading: dict[cst.CSTNode, list[cst.EmptyLine]] = {}

    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.BaseExpression:
        name = self.plan.reads.get(original_node)
        if name is None:
            return updated_node
        base: cst.BaseExpression = updated_node
        while isinstance(base, cst.Attribute) and not base.lpar:
            base = base.value
        lpar: Any = getattr(base, "lpar", ())
        rpar: Any = getattr(base, "rpar", ())
        return cst.Name(name, lpar=lpar, rpar=rpar)

    def lines(self, old: Line, new: Line) -> list[cst.SimpleStatementLine] | None:
        if not any(s in self.plan.statements for s in old.body):
            return None
        tail: cst.TrailingWhitespace = new.trailing_whitespace
        lines: list[cst.SimpleStatementLine] = [
            cst.SimpleStatementLine(
                [s.with_changes(semicolon=DEFAULT)],
                trailing_whitespace=cst.TrailingWhitespace(newline=tail.newline),
            )
            for before, after in zip(old.body, new.body)
            for s in self.plan.statements.get(before, [after])
        ]
        if lines:
            lines[-1] = lines[-1].with_changes(trailing_whitespace=tail)
        return lines

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine | cst.FlattenSentinel[cst.BaseStatement] | cst.RemovalSentinel:
        parent: cst.CSTNode = self.plan.parents[original_node]
        carry: list[cst.EmptyLine] = self.leading.pop(parent, [])
        lines = self.lines(original_node, updated_node)
        if lines is None and not carry:
            return updated_node
        carry.extend(updated_node.leading_lines)
        if lines is None:
            return updated_node.with_changes(leading_lines=carry)
        if not lines:
            tail: cst.TrailingWhitespace = updated_node.trailing_whitespace
            if tail.comment is not None:
                carry.append(cst.EmptyLine(comment=tail.comment, newline=tail.newline))
            self.leading[parent] = carry
            return cst.RemoveFromParent()
        lines[0] = lines[0].with_changes(leading_lines=carry)
        return cst.FlattenSentinel(lines)

    def leave_SimpleStatementSuite(
        self, original_node: cst.SimpleStatementSuite, updated_node: cst.SimpleStatementSuite
    ) -> cst.BaseSuite:
        lines = self.lines(original_node, updated_node)
        if lines is None:
            return updated_node
        return cst.IndentedBlock(
            lines,
            header=cst.TrailingWhitespace(newline=updated_node.trailing_whitespace.newline),
        )


def refactor_imports(
    source: str,
    *,
    include_from_imports: bool = False,
    keep_imports: bool = False,
    conservative: bool = False,
    skip: Collection[str] = (),
    max_passes: int = 100,
) -> str:
    if max_passes < 1:
        raise ValueError("max_passes must be >= 1")
    trailing = source.endswith(("\n", "\r"))

    def transform(current: str) -> str:
        wrapper: md.MetadataWrapper = md.MetadataWrapper(cst.parse_module(current), unsafe_skip_copy=True)
        plan = Planner(wrapper, keep_imports, skip, conservative)
        plan.plan(include_from_imports)
        if not plan.statements:
            return current
        result: str = wrapper.module.visit(Rewrite(plan)).with_changes(has_trailing_newline=trailing).code
        return result if result != current else current

    for _ in range(max_passes):
        result = transform(source)
        if result == source:
            return source
        source = result
    # A final no-op pass is allowed; a further real change means non-convergence.
    if transform(source) == source:
        return source
    raise RuntimeError(f"refactoring did not converge after {max_passes} passes")


def flatten_values(values: Sequence[str] | None) -> tuple[str, ...]:
    result: list[str] = []
    for value in values or ():
        for piece in value.split(","):
            piece = piece.strip()
            if piece:
                result.append(piece)
    return tuple(dict.fromkeys(result))


def normalize_extensions(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        value = value.strip().lower()
        if not value:
            continue
        if not value.startswith("."):
            value = "." + value
        result.append(value)
    return tuple(dict.fromkeys(result)) or DEFAULT_EXTENSIONS


def normalize_paths(files: Sequence[Path] | Path | None) -> tuple[Path, ...]:
    if files is None:
        return ()
    if isinstance(files, Path):
        return (files,)
    return tuple(files)


def normalize_pattern(pattern: str) -> str:
    value = pattern.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.rstrip("/")


def resolve_mode(*, write: bool, dry_run: bool, diff: bool, check: bool, stdout: bool) -> Mode:
    flags: tuple[tuple[Mode, bool], ...] = (
        (Mode.write, write),
        (Mode.dry_run, dry_run),
        (Mode.diff, diff),
        (Mode.check, check),
        (Mode.stdout, stdout),
    )
    selected = [mode for mode, enabled in flags if enabled]
    if len(selected) > 1:
        names = ", ".join(mode.value.replace("_", "-") for mode in selected)
        raise UsageError(f"--write, --dry-run, --diff, --check and --stdout are mutually exclusive ({names} given)")
    return selected[0] if selected else Mode.write


def build_options(
    *,
    files: Sequence[Path] | Path | None,
    mode: Mode,
    color: Color,
    context: int,
    conservative: bool,
    skip: Sequence[str] | None,
    keep_imports: bool,
    include_from_imports: bool,
    extensions: Sequence[str] | None,
    exclude: Sequence[str] | None,
    no_default_excludes: bool,
    follow_symlinks: bool,
    jobs: int,
    max_passes: int,
    quiet: bool,
    verbose: bool,
    backend: ParserBackend,
    requested_backend: ParserBackend,
) -> Options:
    if context < 0:
        raise UsageError("--context must be >= 0")
    if jobs < 1:
        raise UsageError("--jobs/--workers must be >= 1")
    if max_passes < 1:
        raise UsageError("--max-passes must be >= 1")
    if quiet and verbose:
        raise UsageError("--quiet and --verbose are mutually exclusive")

    skip_values = flatten_values(skip)
    user_excludes = flatten_values(exclude)
    base_excludes: tuple[str, ...] = () if no_default_excludes else DEFAULT_EXCLUDES
    exclude_values = tuple(dict.fromkeys((*base_excludes, *user_excludes)))

    return Options(
        paths=normalize_paths(files),
        mode=Mode(mode),
        color=Color(color),
        context=context,
        conservative=conservative,
        skip=skip_values,
        keep_imports=keep_imports,
        include_from_imports=include_from_imports,
        extensions=normalize_extensions(flatten_values(extensions)),
        exclude=exclude_values,
        follow_symlinks=follow_symlinks,
        jobs=jobs,
        max_passes=max_passes,
        quiet=quiet,
        verbose=verbose,
        backend=ParserBackend(backend),
        requested_backend=ParserBackend(requested_backend),
    )


class Excluder:
    __slots__ = ("patterns",)

    def __init__(self, patterns: Sequence[str]) -> None:
        normalized = (normalize_pattern(p) for p in patterns)
        self.patterns: tuple[str, ...] = tuple(dict.fromkeys(p for p in normalized if p and p != "."))

    def excluded(self, relative: str) -> bool:
        rel = relative.replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        rel = rel.rstrip("/")
        parts = tuple(part for part in rel.split("/") if part)
        for pattern in self.patterns:
            if (
                fnmatchcase(rel, pattern)
                or fnmatchcase("/" + rel, pattern)
                or fnmatchcase(rel, pattern + "/*")
                or any(fnmatchcase(part, pattern) for part in parts)
            ):
                return True
        return False


def color_enabled(color: Color) -> bool:
    if color is Color.always:
        return True
    if color is Color.never:
        return False
    if os.getenv("NO_COLOR"):
        return False
    force = os.getenv("FORCE_COLOR")
    if force and force != "0":
        return True
    if os.getenv("TERM") == "dumb":
        return False
    try:
        return bool(sys.stdout.isatty())
    except (OSError, ValueError):
        return False


def stdout_buffer() -> BinaryIO | None:
    return cast(BinaryIO | None, getattr(sys.stdout, "buffer", None))


def write_stdout_text(text: str) -> None:
    buffer = stdout_buffer()
    if buffer is not None:
        encoding = sys.stdout.encoding or "utf-8"
        buffer.write(text.encode(encoding, errors="backslashreplace"))
    else:
        sys.stdout.write(text)


def write_stdout_bytes(data: bytes, encoding: str) -> None:
    buffer = stdout_buffer()
    if buffer is not None:
        buffer.write(data)
    else:
        sys.stdout.write(data.decode(encoding, errors="replace"))
    flush_stdout()


def flush_stdout() -> None:
    buffer = stdout_buffer()
    if buffer is not None:
        buffer.flush()
    sys.stdout.flush()


def emit_diff(before: str, after: str, path: Path, context: int, color: Color) -> None:
    enabled = color_enabled(color)
    diff: Iterable[str] = difflib.unified_diff(
        before.splitlines(True),
        after.splitlines(True),
        str(path),
        f"{path} (refactored)",
        n=context,
    )
    palette: dict[str, str] = {
        "+": ANSI_GREEN,
        "-": ANSI_RED,
        "@": ANSI_CYAN,
    }
    for index, line in enumerate(diff):
        code = ANSI_CYAN if index < 2 else palette.get(line[:1], "")
        text = line.rstrip("\r\n")
        if enabled and code:
            write_stdout_text(f"{code}{text}{ANSI_RESET}\n")
        else:
            write_stdout_text(text + "\n")
        if not line.endswith(("\n", "\r")):
            write_stdout_text("\\ No newline at end of file\n")
    flush_stdout()


def validate_python(source: str | bytes, filename: str | Path, *, stage: str = "Output") -> None:
    try:
        compile(source, str(filename), "exec", dont_inherit=True, optimize=0)
    except SyntaxError as exc:
        location = f" at line {exc.lineno}" if exc.lineno and exc.lineno > 0 else ""
        if location and exc.offset and exc.offset > 0:
            location += f", column {exc.offset}"
        raise ValueError(f"{stage} validation failed{location}: {exc.msg}") from exc


def fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    path = path.resolve(strict=True)
    mode = stat.S_IMODE(path.stat().st_mode)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            tmp = Path(handle.name)
            if handle.write(data) != len(data):
                raise OSError("Incomplete write to temporary file")
            handle.flush()
            os.fsync(handle.fileno())
        tmp.chmod(mode)
        os.replace(tmp, path)
        tmp = None
        fsync_directory(path.parent)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def process_file(path: Path, options: Options) -> FileResult:
    try:
        raw = path.read_bytes()
        validate_python(raw, path, stage="Input")
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        before = raw.decode(encoding)
        after = refactor_imports(
            before,
            include_from_imports=options.include_from_imports,
            keep_imports=options.keep_imports,
            conservative=options.conservative,
            skip=options.skip,
            max_passes=options.max_passes,
        )
        candidate = raw if after == before else after.encode(encoding)
        changed = candidate != raw
        if changed:
            validate_python(candidate, path, stage="Refactored output")
        return FileResult(
            path=path,
            changed=changed,
            before=before,
            after=after,
            candidate=candidate,
            encoding=encoding,
        )
    except Exception as exc:
        return FileResult(path=path, error=f"{type(exc).__name__}: {exc}")


def process_all(paths: Sequence[Path], options: Options) -> list[FileResult]:
    if not paths:
        return []
    if options.jobs == 1 or len(paths) == 1:
        return [process_file(path, options) for path in paths]
    workers = min(options.jobs, len(paths))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="refactor") as pool:
        return list(pool.map(lambda path: process_file(path, options), paths))


def directory_key(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def discover(root: Path, options: Options) -> tuple[list[Path], list[str]]:
    excluder = Excluder(options.exclude)
    extensions = set(options.extensions)
    found: list[Path] = []
    problems: list[str] = []
    visited: set[tuple[int, int]] = set()

    def onerror(exc: OSError) -> None:
        target = exc.filename or str(root)
        problems.append(f"{target}: {exc.strerror or exc}")

    for dirpath, dirnames, filenames in os.walk(root, followlinks=options.follow_symlinks, onerror=onerror):
        current = Path(dirpath)
        try:
            rel_dir = current.relative_to(root)
        except ValueError:
            rel_dir = Path(".")

        def relative(name: str) -> str:
            return (rel_dir / name).as_posix()

        if options.follow_symlinks:
            key = directory_key(current)
            if key is None or key in visited:
                dirnames[:] = []
                continue
            visited.add(key)

        kept: list[str] = []
        for name in sorted(dirnames):
            if excluder.excluded(relative(name)):
                continue
            if options.follow_symlinks:
                child_key = directory_key(current / name)
                if child_key is None or child_key in visited:
                    continue
            kept.append(name)
        dirnames[:] = kept

        for name in sorted(filenames):
            path = current / name
            if path.suffix.lower() not in extensions:
                continue
            if excluder.excluded(relative(name)):
                continue
            found.append(path)

    found.sort(key=lambda path: path.as_posix())
    return found, problems


def collect_paths(options: Options) -> tuple[list[Path], list[str]]:
    if not options.paths:
        return discover(Path("."), options)

    result: list[Path] = []
    problems: list[str] = []
    for path in options.paths:
        try:
            if path.is_dir():
                found, more = discover(path, options)
            elif path.is_file():
                if not os.access(path, os.R_OK):
                    problems.append(f"{path}: file is not readable")
                    continue
                found, more = [path], []
            else:
                problems.append(f"{path}: no such file or directory")
                continue
        except OSError as exc:
            problems.append(f"{path}: {exc}")
            continue
        result.extend(found)
        problems.extend(more)

    seen: set[str] = set()
    unique: list[Path] = []
    for path in result:
        try:
            key = os.path.normcase(str(path.resolve()))
        except (OSError, RuntimeError, ValueError) as exc:
            problems.append(f"{path}: {exc}")
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique, problems


def note(options: Options, message: str, *, verbose_only: bool = False) -> None:
    if options.quiet or (verbose_only and not options.verbose):
        return
    print(f"{PROG}: {message}", file=sys.stderr)


def report_problem(message: str) -> None:
    print(f"{PROG}: {message}", file=sys.stderr)


def report_file_error(path: Path, error: str, options: Options) -> None:
    suffix = "\nFile not written." if options.mode is Mode.write else ""
    print(f"{PROG}: {path}: {error}{suffix}", file=sys.stderr)


def run(options: Options) -> int:
    note(
        options,
        f"backend={options.backend.value} (requested={options.requested_backend.value}), mode={options.mode.value}",
        verbose_only=True,
    )
    paths, problems = collect_paths(options)
    for problem in problems:
        report_problem(problem)
    had_errors = bool(problems)

    if options.mode is Mode.stdout:
        if len(paths) != 1:
            report_problem(f"--stdout requires exactly one input file ({len(paths)} selected)")
            return 2
        if had_errors:
            return 2

    if not paths:
        note(options, "no Python files found")
        return 2 if had_errors else 0

    note(options, f"selected {len(paths)} file(s)", verbose_only=True)
    results = process_all(paths, options)
    changed_any = False

    for result in results:
        if result.error is not None:
            report_file_error(result.path, result.error, options)
            had_errors = True
            continue
        changed_any |= result.changed

        if options.mode is Mode.stdout:
            try:
                write_stdout_bytes(result.candidate, result.encoding)
            except BrokenPipeError:
                raise
            except (OSError, ValueError) as exc:
                report_file_error(result.path, f"error displaying result: {exc}", options)
                had_errors = True
            continue

        if options.mode is Mode.write:
            if result.changed:
                try:
                    atomic_write(result.path, result.candidate)
                except Exception as exc:
                    report_file_error(result.path, f"error writing file: {exc}", options)
                    had_errors = True
                    continue
                note(options, f"Updated: {result.path}")
            else:
                note(options, f"Unchanged: {result.path}", verbose_only=True)
            continue

        if options.mode is Mode.diff:
            if result.changed:
                try:
                    emit_diff(
                        result.before,
                        result.after,
                        result.path,
                        options.context,
                        options.color,
                    )
                except BrokenPipeError:
                    raise
                except (OSError, UnicodeError, ValueError) as exc:
                    report_file_error(result.path, f"error displaying result: {exc}", options)
                    had_errors = True
            else:
                note(options, f"Unchanged: {result.path}", verbose_only=True)
            continue

        if options.mode is Mode.check:
            if result.changed:
                note(options, f"Would reformat: {result.path}")
            else:
                note(options, f"Unchanged: {result.path}", verbose_only=True)
            continue

        if options.mode is Mode.dry_run:
            if result.changed:
                note(options, f"Would update: {result.path}")
            else:
                note(options, f"Would leave unchanged: {result.path}", verbose_only=True)
            continue

        raise AssertionError(f"unhandled mode: {options.mode}")

    if had_errors:
        return 2
    if options.mode is Mode.check and changed_any:
        return 1
    return 0


def nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return number


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def build_argparse_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Rewrite Python imports in place; with no paths, scan '.' recursively.",
        epilog=dedent(
            f"""\
            examples:
              {PROG}                         # rewrite every .py file under . recursively
              {PROG} src tests               # rewrite files and directories recursively
              {PROG} --diff -U 5 src         # preview a unified diff, no writes
              {PROG} --check .               # exit 1 if any file needs changes
              {PROG} --stdout app.py         # print one refactored file, no writes
              {PROG} --parser argparse       # force argparse even when Typer is installed

            explicit files are processed regardless of --ext; --ext and --exclude apply to
            directory discovery. Typer is used when installed, otherwise argparse is used.
            """
        ),
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        metavar="FILE_OR_DIR",
        help="Files/directories to process. If omitted, recursively process Python files under '.'.",
    )
    parser.add_argument(
        "--version",
        "-V",
        action="store_true",
        help="Show version and exit.",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--write",
        "-w",
        dest="mode",
        action="store_const",
        const=Mode.write,
        help="Rewrite files in place (default behavior).",
    )
    mode_group.add_argument(
        "--dry-run",
        "--no-write",
        "-n",
        dest="mode",
        action="store_const",
        const=Mode.dry_run,
        help="Report what would change without writing (does not print file contents).",
    )
    mode_group.add_argument(
        "--diff",
        "-d",
        dest="mode",
        action="store_const",
        const=Mode.diff,
        help="Show a unified diff without writing.",
    )
    mode_group.add_argument(
        "--check",
        "-c",
        dest="mode",
        action="store_const",
        const=Mode.check,
        help="Exit 1 if any file would change; never write.",
    )
    mode_group.add_argument(
        "--stdout",
        dest="mode",
        action="store_const",
        const=Mode.stdout,
        help="Print the refactored bytes of exactly one file to stdout; never write.",
    )
    parser.set_defaults(mode=Mode.write)

    parser.add_argument(
        "--color",
        type=Color,
        choices=tuple(Color),
        default=Color.auto,
        help="Diff coloring: auto, always or never (auto honors NO_COLOR/FORCE_COLOR).",
    )
    parser.add_argument(
        "--context",
        "-U",
        type=nonnegative_int,
        default=3,
        metavar="N",
        help="Unified diff context lines (default: 3).",
    )
    parser.add_argument(
        "--conservative",
        "-C",
        action="store_true",
        help="Only unique standalone top-level imports and later uses; skip files with star imports.",
    )
    parser.add_argument(
        "--skip",
        action="append",
        metavar="NAME",
        help="Do not bind this module/attribute directly (repeatable/comma-separated, e.g. sys.stderr).",
    )
    parser.add_argument(
        "--keep-imports",
        "-k",
        action="store_true",
        help="Retain module bindings, e.g. for re-exports.",
    )
    parser.add_argument(
        "--include-from-imports",
        action="store_true",
        help="Assume absolute from-imported objects are submodules. Unsafe for classes/functions.",
    )
    parser.add_argument(
        "--ext",
        "--extension",
        action="append",
        metavar="EXT",
        help="Directory-discovery extension (repeatable/comma-separated; default .py). Add .pyi for stubs.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        metavar="GLOB",
        help="Additional path glob to skip during directory discovery (repeatable/comma-separated).",
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="Do not apply the built-in directory excludes.",
    )
    parser.add_argument(
        "--follow-symlinks",
        "-L",
        action="store_true",
        help="Follow directory symlinks during discovery (with loop protection).",
    )
    parser.add_argument(
        "--jobs",
        "--workers",
        "-j",
        type=positive_int,
        default=1,
        metavar="N",
        help="Number of files to process in parallel (default: 1).",
    )
    parser.add_argument(
        "--max-passes",
        type=positive_int,
        default=100,
        metavar="N",
        help="Maximum refactoring passes per file (default: 100).",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress non-error status messages.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print extra status messages.",
    )

    backend_group = parser.add_argument_group("CLI backend")
    backend_group.add_argument(
        "--parser",
        dest="parser_backend",
        type=ParserBackend,
        choices=tuple(ParserBackend),
        default=ParserBackend.auto,
        metavar="BACKEND",
        help="Parser backend: auto, typer or argparse (default: auto).",
    )
    backend_group.add_argument(
        "--typer",
        dest="parser_backend",
        action="store_const",
        const=ParserBackend.typer,
        help="Force the Typer backend (error if Typer is unavailable).",
    )
    backend_group.add_argument(
        "--argparse",
        "--no-typer",
        dest="parser_backend",
        action="store_const",
        const=ParserBackend.argparse,
        help="Force the argparse backend, even if Typer is installed.",
    )
    return parser


def run_argparse(args: Sequence[str], *, backend: ParserBackend, requested: ParserBackend) -> int:
    parser = build_argparse_parser()
    namespace = parser.parse_args(list(args))
    if namespace.version:
        print(f"{PROG} {VERSION}")
        return 0
    try:
        options = build_options(
            files=namespace.files,
            mode=namespace.mode,
            color=namespace.color,
            context=namespace.context,
            conservative=namespace.conservative,
            skip=namespace.skip,
            keep_imports=namespace.keep_imports,
            include_from_imports=namespace.include_from_imports,
            extensions=namespace.ext,
            exclude=namespace.exclude,
            no_default_excludes=namespace.no_default_excludes,
            follow_symlinks=namespace.follow_symlinks,
            jobs=namespace.jobs,
            max_passes=namespace.max_passes,
            quiet=namespace.quiet,
            verbose=namespace.verbose,
            backend=backend,
            requested_backend=requested,
        )
        return run(options)
    except UsageError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover


def parser_exception_exit_code(exc: Exception) -> int | None:
    """Return an exit code for Click/Typer parser exceptions, or None otherwise."""
    show: Any = getattr(exc, "show", None)
    exit_code: Any = getattr(exc, "exit_code", None)
    name = type(exc).__name__
    if callable(show):
        try:
            show(file=sys.stderr)
        except TypeError:
            show()
        return int(exit_code) if isinstance(exit_code, int) else 2
    if name == "Exit" and isinstance(exit_code, int):
        return int(exit_code)
    if name == "Abort":
        print(f"{PROG}: aborted", file=sys.stderr)
        return 1
    return None


def run_typer(args: Sequence[str], *, backend: ParserBackend, requested: ParserBackend) -> int:
    try:
        import typer
    except ImportError as exc:
        print(f"{PROG}: error: Typer backend unavailable: {exc}", file=sys.stderr)
        return 2

    try:
        app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)
    except TypeError:
        app = typer.Typer(add_completion=False)

    @app.command(
        context_settings={
            "help_option_names": ["-h", "--help"],
            "allow_interspersed_args": True,
        }
    )
    def command(
        files: Annotated[
            list[Path] | None,
            typer.Argument(
                exists=True,
                dir_okay=True,
                file_okay=True,
                readable=True,
                help="Files/directories to process. If omitted, scan '.' recursively.",
            ),
        ] = None,
        write: Annotated[
            bool, typer.Option("--write", "-w", help="Rewrite files in place (default behavior).")
        ] = False,
        dry_run: Annotated[
            bool,
            typer.Option(
                "--dry-run",
                "-n",
                "--no-write",
                help="Report what would change without writing (does not print file contents).",
            ),
        ] = False,
        diff: Annotated[bool, typer.Option("--diff", "-d", help="Show a unified diff without writing.")] = False,
        check: Annotated[
            bool, typer.Option("--check", "-c", help="Exit 1 if any file would change; never write.")
        ] = False,
        stdout: Annotated[
            bool,
            typer.Option(
                "--stdout",
                help="Print the refactored bytes of exactly one file to stdout; never write.",
            ),
        ] = False,
        version: Annotated[bool, typer.Option("--version", "-V", help="Show version and exit.")] = False,
        color: Annotated[
            Color,
            typer.Option("--color", help="Diff coloring: auto, always or never."),
        ] = Color.auto,
        context: Annotated[
            int, typer.Option("--context", "-U", min=0, help="Unified diff context lines (default: 3).")
        ] = 3,
        conservative: Annotated[
            bool,
            typer.Option(
                "--conservative",
                "-C",
                help="Only unique standalone top-level imports and later uses; skip files with star imports.",
            ),
        ] = False,
        skip: Annotated[
            list[str] | None,
            typer.Option(
                "--skip",
                metavar="NAME",
                help="Do not bind this module/attribute directly (repeatable/comma-separated).",
            ),
        ] = None,
        keep_imports: Annotated[
            bool,
            typer.Option("--keep-imports", "-k", help="Retain module bindings, e.g. for re-exports."),
        ] = False,
        include_from_imports: Annotated[
            bool,
            typer.Option(
                "--include-from-imports",
                help="Assume absolute from-imported objects are submodules. Unsafe for classes/functions.",
            ),
        ] = False,
        extensions: Annotated[
            list[str] | None,
            typer.Option(
                "--ext",
                "--extension",
                metavar="EXT",
                help="Directory-discovery extension (repeatable/comma-separated; default .py).",
            ),
        ] = None,
        exclude: Annotated[
            list[str] | None,
            typer.Option(
                "--exclude",
                metavar="GLOB",
                help="Additional path glob to skip during directory discovery (repeatable/comma-separated).",
            ),
        ] = None,
        no_default_excludes: Annotated[
            bool,
            typer.Option("--no-default-excludes", help="Do not apply the built-in directory excludes."),
        ] = False,
        follow_symlinks: Annotated[
            bool,
            typer.Option(
                "--follow-symlinks",
                "-L",
                help="Follow directory symlinks during discovery (with loop protection).",
            ),
        ] = False,
        jobs: Annotated[
            int,
            typer.Option(
                "--jobs",
                "-j",
                "--workers",
                min=1,
                metavar="N",
                help="Number of files to process in parallel (default: 1).",
            ),
        ] = 1,
        max_passes: Annotated[
            int,
            typer.Option(
                "--max-passes",
                min=1,
                metavar="N",
                help="Maximum refactoring passes per file (default: 100).",
            ),
        ] = 100,
        quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Suppress non-error status messages.")] = False,
        verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Print extra status messages.")] = False,
        parser_backend: Annotated[
            ParserBackend,
            typer.Option(
                "--parser",
                metavar="BACKEND",
                help="Parser backend: auto, typer or argparse (default: auto).",
            ),
        ] = ParserBackend.auto,
        typer_backend: Annotated[bool, typer.Option("--typer", help="Force the Typer backend.")] = False,
        argparse_backend: Annotated[
            bool,
            typer.Option(
                "--argparse",
                "--no-typer",
                help="Force the argparse backend, even if Typer is installed.",
            ),
        ] = False,
    ) -> int:
        """Rewrite Python imports in place; with no paths, scan '.' recursively."""
        # The actual backend is selected before parsing so --parser/--typer/--argparse
        # can work uniformly with either backend. These values are parsed for help/validation.
        _ = (parser_backend, typer_backend, argparse_backend)
        try:
            mode = resolve_mode(
                write=write,
                dry_run=dry_run,
                diff=diff,
                check=check,
                stdout=stdout,
            )
            if version:
                print(f"{PROG} {VERSION}")
                return 0
            options = build_options(
                files=files,
                mode=mode,
                color=color,
                context=context,
                conservative=conservative,
                skip=skip,
                keep_imports=keep_imports,
                include_from_imports=include_from_imports,
                extensions=extensions,
                exclude=exclude,
                no_default_excludes=no_default_excludes,
                follow_symlinks=follow_symlinks,
                jobs=jobs,
                max_passes=max_passes,
                quiet=quiet,
                verbose=verbose,
                backend=backend,
                requested_backend=requested,
            )
            return run(options)
        except UsageError as exc:
            raise typer.BadParameter(str(exc)) from exc

    try:
        result = app(args=list(args), standalone_mode=False, prog_name=PROG)
    except SystemExit as exc:
        return system_exit_code(exc)
    except Exception as exc:
        handled = parser_exception_exit_code(exc)
        if handled is not None:
            return handled
        raise
    return int(result or 0)


def before_separator(argv: Sequence[str]) -> list[str]:
    result: list[str] = []
    for arg in argv:
        if arg == "--":
            break
        result.append(arg)
    return result


def requested_backend(argv: Sequence[str]) -> ParserBackend:
    requested = ParserBackend.auto
    head = before_separator(argv)
    for index, arg in enumerate(head):
        if arg in {"--argparse", "--no-typer"}:
            requested = ParserBackend.argparse
        elif arg == "--typer":
            requested = ParserBackend.typer
        elif arg == "--parser":
            if index + 1 < len(head):
                try:
                    requested = ParserBackend(head[index + 1])
                except ValueError:
                    pass
        elif arg.startswith("--parser="):
            try:
                requested = ParserBackend(arg.split("=", 1)[1])
            except ValueError:
                pass
    return requested


@lru_cache(maxsize=1)
def typer_available() -> bool:
    try:
        import typer  # noqa: F401
    except Exception:
        return False
    return True


def system_exit_code(exc: SystemExit) -> int:
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def silence_broken_pipe() -> None:
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
        finally:
            os.close(devnull)
    except (OSError, ValueError, AttributeError):
        pass


def run_cli(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    requested = requested_backend(args)
    if requested is ParserBackend.argparse:
        backend = ParserBackend.argparse
    else:
        available = typer_available()
        if requested is ParserBackend.typer and not available:
            print(
                f"{PROG}: error: Typer backend requested but Typer is not installed; "
                "install typer or use --parser argparse",
                file=sys.stderr,
            )
            return 2
        backend = (
            requested
            if requested is ParserBackend.typer
            else (ParserBackend.typer if available else ParserBackend.argparse)
        )

    try:
        if backend is ParserBackend.typer:
            return run_typer(args, backend=backend, requested=requested)
        return run_argparse(args, backend=backend, requested=requested)
    except ImportError as exc:
        print(f"{PROG}: error: Typer backend unavailable: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        return system_exit_code(exc)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run_cli(argv)
    except KeyboardInterrupt:
        print(f"\n{PROG}: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        silence_broken_pipe()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
