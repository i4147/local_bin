#!/data/data/com.termux/files/usr/bin/env python
# optimize this code for speed & termux env
# script must accept one or more files / dirs as input, if no input, recurse '.'
# validate result code before writing to file
# log any errors with loguru
# add mp.pool.imsp_unordeted with 4 workers for parallel processing
# consider covering all possible edge cases
# return full type annotated code

from __future__ import annotations

import argparse
import io
import os
import stat
import sys
import tempfile
import tokenize
from collections import defaultdict
from dataclasses import dataclass, field
from multiprocessing import Pool
from pathlib import Path
from typing import Literal, TypeGuard, cast

import libcst as cst
from libcst.metadata import MetadataWrapper, ScopeProvider
from libcst.metadata.scope_provider import (
    AnnotationScope,
    ClassScope,
    ComprehensionScope,
    FunctionScope,
    GlobalScope,
    ImportAssignment,
)
from loguru import logger

Owner = cst.FunctionDef | cst.ClassDef
SmallImport = cst.Import | cst.ImportFrom
ColorMode = Literal["auto", "always", "never"]
WORKERS = 8
DISCOVER_SUFFIXES = frozenset({".py", ".pyw"})
SKIP_DIRS = frozenset({
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "venv",
})


@dataclass(frozen=True, slots=True)
class FileResult:
    path: Path
    changed: bool
    diff: str = ""
    error: str | None = None


@dataclass(slots=True)
class DiscoveryState:
    seen: set[Path] = field(default_factory=set)
    discovered: int = 0
    errors: int = 0


class InvalidPythonError(ValueError):
    pass


def binding_name(alias):
    if alias.asname is not None:
        return cst.ensure_type(alias.asname.name, cst.Name).value
    name = alias.name
    while isinstance(name, cst.Attribute):
        name = name.value
    return cst.ensure_type(name, cst.Name).value


def is_import(node: cst.CSTNode) -> TypeGuard[cst.Import | cst.ImportFrom]:
    return isinstance(node, (cst.Import, cst.ImportFrom))


def is_future_import(node):
    return (
        isinstance(node, cst.ImportFrom)
        and isinstance(node.module, cst.Name)
        and node.module.value == "__future__"
        and not node.relative
    )


def owner_for_access(scope):
    access_scope = scope
    while not isinstance(scope, GlobalScope):
        node = getattr(scope, "node", None)
        if isinstance(scope, FunctionScope) and isinstance(node, cst.FunctionDef):
            return node, scope
        if isinstance(scope, ClassScope) and isinstance(node, cst.ClassDef):
            if scope is access_scope or (isinstance(access_scope, AnnotationScope) and access_scope.parent is scope):
                return node, scope
        scope = scope.parent
    return None


def root_name(node):
    while isinstance(node, cst.Attribute):
        node = node.value
    return node.value if isinstance(node, cst.Name) else None


def references_import(ref, name, assignments):
    if not isinstance(ref.scope, (FunctionScope, ComprehensionScope)):
        return True
    if root_name(ref.node) != name:
        return True
    root_assignments = set(ref.scope[name])
    return any(assignment.name == name and assignment in root_assignments for assignment in assignments)


def contains_comment(node):
    class CommentFinder(cst.CSTVisitor):
        found = False

        def visit_Comment(self, node):
            self.found = True

    finder = CommentFinder()
    node.visit(finder)
    return finder.found


def subset_import(original, aliases, *, inserted):
    names = list(aliases)
    assert not isinstance(original.names, cst.ImportStar)
    if isinstance(original, cst.ImportFrom) and original.lpar is not None:
        original_last = original.names[-1]
        names[-1] = names[-1].with_changes(comma=original_last.comma)
    else:
        names[-1] = names[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
    return original.with_changes(
        names=names,
        semicolon=cst.MaybeSentinel.DEFAULT if inserted else original.semicolon,
    )


def is_docstring(stmt):
    if not isinstance(stmt, cst.SimpleStatementLine) or not stmt.body:
        return False
    first = stmt.body[0]
    return isinstance(first, cst.Expr) and isinstance(first.value, (cst.SimpleString, cst.ConcatenatedString))


def block_for_insertion(body, imports):
    if isinstance(body, cst.SimpleStatementSuite):
        body = cst.IndentedBlock(
            body=[cst.SimpleStatementLine(body=list(body.body), trailing_whitespace=body.trailing_whitespace)]
        )
    assert isinstance(body, cst.IndentedBlock)
    statements = list(body.body)
    if statements and isinstance(statements[0], cst.SimpleStatementLine):
        first = statements[0]
        if is_docstring(first) and len(first.body) > 1:
            doc = first.body[0].with_changes(semicolon=cst.MaybeSentinel.DEFAULT)
            statements[:1] = [
                cst.SimpleStatementLine(body=[doc], leading_lines=first.leading_lines),
                first.with_changes(body=first.body[1:], leading_lines=()),
            ]
    index = 1 if statements and is_docstring(statements[0]) else 0
    statements[index:index] = imports
    return body.with_changes(body=statements)


class GlobalDeclarations(cst.CSTVisitor):
    def __init__(self, scopes):
        self.scopes = scopes
        self.names = defaultdict(set)

    def visit_Global(self, node):
        scope = self.scopes[node]
        assert scope is not None
        self.names[scope].update(item.name.value for item in node.names)


def analyze(module, wrapper):
    scopes = wrapper.resolve(ScopeProvider)
    global_scope = scopes[module]
    assert isinstance(global_scope, GlobalScope)
    declarations = GlobalDeclarations(scopes)
    module.visit(declarations)
    assignments_by_alias = defaultdict(list)
    for assignment in global_scope.assignments:
        if isinstance(assignment, ImportAssignment):
            assignments_by_alias[(id(assignment.node), id(assignment.as_name))].append(assignment)
    plan = {}
    inserts = defaultdict(list)
    future_annotations = any(
        isinstance(stmt, cst.SimpleStatementLine)
        and any(
            isinstance(small, cst.ImportFrom)
            and is_future_import(small)
            and not isinstance(small.names, cst.ImportStar)
            and any(isinstance(alias.name, cst.Name) and alias.name.value == "annotations" for alias in small.names)
            for small in stmt.body
        )
        for stmt in module.body
    )
    for i, statement in enumerate(module.body):
        if not isinstance(statement, cst.SimpleStatementLine):
            continue
        for j, small in enumerate(statement.body):
            if not is_import(small):
                continue
            if is_future_import(small) or isinstance(small.names, cst.ImportStar):
                continue
            groups = defaultdict(list)
            kept = []
            for alias in small.names:
                name = binding_name(alias)
                bound_node = alias.asname.name if alias.asname else alias.name
                assignments = assignments_by_alias.get((id(small), id(bound_node)), [])
                if not assignments or len(global_scope.assignments[name]) != 1 or name == "__all__":
                    kept.append(alias)
                    continue
                references = {
                    ref
                    for assignment in assignments
                    for ref in assignment.references
                    if references_import(ref, name, assignments)
                }
                destinations = {}
                unsafe = not references
                for ref in references:
                    if ref.is_annotation and (
                        future_annotations or isinstance(ref.node, (cst.SimpleString, cst.ConcatenatedString))
                    ):
                        unsafe = True
                        break
                    result = owner_for_access(ref.scope)
                    if result is None:
                        unsafe = True
                        break
                    owner, owner_scope = result
                    if name in declarations.names.get(owner_scope, set()) or owner_scope.assignments[name]:
                        unsafe = True
                        break
                    destinations[id(owner)] = result
                if unsafe:
                    kept.append(alias)
                    continue
                for key in destinations:
                    groups[key].append(alias)
            if not groups:
                continue
            split = bool(kept) or len(groups) > 1
            comment_on_line = contains_comment(statement)
            comment_in_import = contains_comment(small)
            if (split and comment_in_import) or (comment_on_line and (split or len(statement.body) > 1)):
                continue
            all_to_one = not kept and len(groups) == 1
            if all_to_one and len(statement.body) == 1:
                owner_id = next(iter(groups))
                leading = (
                    statement.leading_lines if any(line.comment is not None for line in statement.leading_lines) else ()
                )
                moved_line = statement.with_changes(
                    body=[small.with_changes(semicolon=cst.MaybeSentinel.DEFAULT)],
                    leading_lines=leading,
                )
                if (
                    isinstance(small.semicolon, cst.Semicolon)
                    and isinstance(small.semicolon.whitespace_after, cst.SimpleWhitespace)
                    and statement.trailing_whitespace.comment is not None
                ):
                    spacing = small.semicolon.whitespace_after.value + statement.trailing_whitespace.whitespace.value
                    moved_line = moved_line.with_changes(
                        trailing_whitespace=statement.trailing_whitespace.with_changes(
                            whitespace=cst.SimpleWhitespace(value=spacing)
                        )
                    )
                inserts[owner_id].append(moved_line)
            else:
                for owner_id, aliases in groups.items():
                    if all_to_one:
                        imported = small.with_changes(semicolon=cst.MaybeSentinel.DEFAULT)
                    else:
                        imported = subset_import(small, aliases, inserted=True)
                    inserts[owner_id].append(cst.SimpleStatementLine(body=[imported]))
            plan[(i, j)] = subset_import(small, kept, inserted=False) if kept else None
    return plan, inserts


class ImportMover(cst.CSTTransformer):
    def __init__(
        self,
        plan,
        inserts,
    ):
        self.plan = plan
        self.inserts = inserts

    def leave_FunctionDef(self, original_node, updated_node):
        lines = self.inserts.get(id(original_node))
        if not lines:
            return updated_node
        return updated_node.with_changes(body=block_for_insertion(updated_node.body, lines))

    def leave_ClassDef(self, original_node, updated_node):
        lines = self.inserts.get(id(original_node))
        if not lines:
            return updated_node
        return updated_node.with_changes(body=block_for_insertion(updated_node.body, lines))

    def leave_Module(self, original_node, updated_node):
        new_body = []
        first_original_index = None
        for i, (original, updated) in enumerate(zip(original_node.body, updated_node.body)):
            if not isinstance(original, cst.SimpleStatementLine):
                if first_original_index is None:
                    first_original_index = i
                new_body.append(updated)
                continue
            assert isinstance(updated, cst.SimpleStatementLine)
            items = []
            last_kept = -1
            for j, updated_small in enumerate(updated.body):
                replacement = self.plan.get((i, j), updated_small)
                if replacement is not None:
                    items.append(replacement)
                    last_kept = j
            if items:
                if last_kept < len(original.body) - 1:
                    items[-1] = items[-1].with_changes(semicolon=cst.MaybeSentinel.DEFAULT)
                if first_original_index is None:
                    first_original_index = i
                new_body.append(updated.with_changes(body=items))
        if new_body and first_original_index and hasattr(new_body[0], "leading_lines"):
            first = new_body[0]
            leading = list(first.leading_lines)
            while leading and leading[0].comment is None:
                leading.pop(0)
            new_body[0] = first.with_changes(leading_lines=leading)
        return updated_node.with_changes(body=new_body)


def validate_python_code(source, *, filename="<string>", encoding="utf-8"):
    if source.startswith("\ufeff") and encoding.lower().replace("_", "-") == "utf-8-sig":
        source = source[1:]
    try:
        compile(source.encode(encoding), filename, "exec", dont_inherit=True)
    except (SyntaxError, UnicodeError, ValueError) as exc:
        raise InvalidPythonError(f"Invalid Python generated for {filename}: {exc}") from exc


def move_imports(source, *, filename="<string>", encoding="utf-8"):
    bom = "\ufeff" if source.startswith("\ufeff") else ""
    wrapper = MetadataWrapper(cst.parse_module(source[len(bom) :]))
    plan, inserts = analyze(wrapper.module, wrapper)
    result = bom + wrapper.module.visit(ImportMover(plan, inserts)).code
    validate_python_code(result, filename=filename, encoding=encoding)
    return result


def _make_diff(path, before, after):
    import difflib
    return "".join(
        line + "\n"
        for line in difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=str(path),
            tofile=str(path),
            lineterm="",
        )
    )


def _color_enabled(mode, stream):
    return mode == "always" or (mode == "auto" and stream.isatty())


def _style(text, ansi_code, enabled):
    return f"\x1b[{ansi_code}m{text}\x1b[0m" if enabled else text


def color_diff(diff, *, enabled):
    if not enabled:
        return diff
    output = []
    for line in diff.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        newline = line[len(content) :]
        if content.startswith(("--- ", "+++ ")):
            code = "1;36"
        elif content.startswith("@@"):
            code = "36"
        elif content.startswith("-"):
            code = "31"
        elif content.startswith("+"):
            code = "32"
        else:
            output.append(line)
            continue
        output.append(_style(content, code, True) + newline)
    return "".join(output)


def _stat_identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
        info.st_nlink,
    )


def _write_atomic(path, data, original):
    target = path.resolve(strict=True)
    if original.st_nlink > 1:
        raise RuntimeError(f"Cannot atomically replace hard-linked file: {path}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.chmod(temporary, stat.S_IMODE(original.st_mode))
        if _stat_identity(target.stat()) != _stat_identity(original):
            raise RuntimeError(f"File changed during processing; refusing overwrite: {path}")
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _process_one(path, *, dry_run, include_diff):
    try:
        original = path.stat()
        raw = path.read_bytes()
        if _stat_identity(path.stat()) != _stat_identity(original):
            raise RuntimeError(f"File changed while being read: {path}")
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
        del raw
        updated = move_imports(source, filename=str(path), encoding=encoding)
        if updated == source:
            return FileResult(path=path, changed=False)
        if original.st_nlink > 1:
            raise RuntimeError(f"Cannot atomically replace hard-linked file: {path}")
        diff = _make_diff(path, source, updated) if include_diff else ""
        if not dry_run:
            _write_atomic(path, updated.encode(encoding), original)
        return FileResult(path=path, changed=True, diff=diff)
    except Exception as exc:
        return FileResult(path=path, changed=False, error=f"{type(exc).__name__}: {exc}")


def process_file(path):
    result = _process_one(path, dry_run=False, include_diff=False)
    if result.error is not None:
        logger.error("Failed {}: {}", path, result.error)
        raise RuntimeError(f"Failed {path}: {result.error}")
    if result.changed:
        print(f"Updated {path}")
    else:
        print(f"No changes for {path}")
    return result


def discover_files(paths, state):
    def unique(path):
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            logger.error("Cannot resolve {}: {}", path, exc)
            state.errors += 1
            return
        if resolved not in state.seen:
            state.seen.add(resolved)
            state.discovered += 1
            yield path

    def on_walk_error(exc):
        logger.error("Cannot scan directory: {}", exc)
        state.errors += 1

    for path in paths:
        try:
            if path.is_file():
                yield from unique(path)
            elif path.is_dir():
                for root, directories, names in os.walk(path, topdown=True, followlinks=False, onerror=on_walk_error):
                    directories[:] = sorted(name for name in directories if name not in SKIP_DIRS)
                    for name in sorted(names):
                        candidate = Path(root) / name
                        if candidate.suffix in DISCOVER_SUFFIXES and not candidate.is_symlink():
                            yield from unique(candidate)
            else:
                logger.error("Not a file or directory: {}", path)
                state.errors += 1
        except (OSError, RuntimeError) as exc:
            logger.error("Cannot inspect {}: {}", path, exc)
            state.errors += 1


def _configure_logging(mode):
    logger.remove()
    logger.add(
        sys.stderr,
        format="<level>{level}</level>: {message}",
        colorize=_color_enabled(mode, sys.stderr),
        backtrace=False,
        diagnose=False,
    )


class LoggingArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        logger.error("Argument error: {}", message)
        self.print_usage(sys.stderr)
        raise SystemExit(2)


def build_parser():
    parser = LoggingArgumentParser(
        description=(
            "Move module imports into every function/class that uses them. "
            "Validate generated Python before atomic writes; use 8 workers."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python move_imports.py                         # recurse through .\n"
            "  python move_imports.py src/a.py src/b.py pkg/  # write multiple inputs\n"
            "  python move_imports.py --dry-run pkg/          # preview only\n"
            "  python move_imports.py --diff --color always . # colored patch only\n"
            "\n--diff and --dry-run never write files.\n"
            "LibCST parses each source file in memory; paths/results stream, file text does not."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path(".")],
        metavar="PATH",
        help="Python file(s) and/or recursive directories (default: .)",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="show a colored unified diff without changing any files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report files that would change without changing them",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="when to color diffs and status output (default: auto)",
    )
    return parser


def main(argv=None):
    from functools import partial
    from itertools import chain
    _configure_logging("auto")
    args = build_parser().parse_intermixed_args(argv)
    paths = cast(list[Path], args.paths)
    mode = cast(ColorMode, args.color)
    diff_requested = cast(bool, args.diff)
    dry_run = cast(bool, args.dry_run) or diff_requested
    _configure_logging(mode)
    state = DiscoveryState()
    files = discover_files(paths, state)
    try:
        first = next(files, None)
    except Exception:
        logger.exception("Discovery failed before workers could start")
        return 1
    if first is None:
        if not state.errors:
            logger.warning("No Python source files found")
        return 1 if state.errors else 0
    mode_name = "diff" if diff_requested else "dry-run" if dry_run else "write"
    logger.info("Starting {} worker processes in {} mode", WORKERS, mode_name)
    color = _color_enabled(mode, sys.stdout)
    changed = 0
    unchanged = 0
    file_errors = 0
    worker = partial(_process_one, dry_run=dry_run, include_diff=diff_requested)
    try:
        with Pool(processes=WORKERS) as pool:
            for result in pool.imap_unordered(worker, chain((first,), files), chunksize=1):
                if result.error is not None:
                    logger.error("Failed {}: {}", result.path, result.error)
                    file_errors += 1
                elif result.changed:
                    changed += 1
                    if diff_requested:
                        sys.stdout.write(color_diff(result.diff, enabled=color))
                    else:
                        message = f"Would update {result.path}" if dry_run else f"Updated {result.path}"
                        print(_style(message, "33" if dry_run else "32", color))
                else:
                    unchanged += 1
                    if not diff_requested:
                        print(_style(f"No changes for {result.path}", "36", color))
    except KeyboardInterrupt:
        logger.error(
            "Interrupted after {} changes; completed file writes remain applied",
            changed,
        )
        return 130
    except Exception:
        logger.exception(
            "Parallel processing failed after {} changes; completed writes remain",
            changed,
        )
        return 1
    errors = state.errors + file_errors
    summary = f"{changed} changed, {unchanged} unchanged, {errors} errors"
    logger.info("Finished {} files: {}", state.discovered, summary)
    if not diff_requested:
        print(_style(summary, "36", color))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
