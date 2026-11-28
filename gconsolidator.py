#!/data/data/com.termux/files/usr/bin/env python

"""Consolidate a small-to-medium Python package directory into a single .py file.

Uses LibCST (``libcst``) for parsing, comment/docstring stripping (``--strip``),
scope-aware symbol analysis and renaming, import deduplication/merging, and
code generation.
"""

from __future__ import annotations
import argparse
import base64
from collections.abc import Callable, Sequence
from concurrent.futures import (
    Executor,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
import configparser
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import tomllib

import libcst as cst
import libcst.matchers as m
from libcst.metadata import (
    BuiltinAssignment,
    ClassScope,
    ComprehensionScope,
    FunctionScope,
    GlobalScope,
    ImportAssignment,
    MetadataWrapper,
    ScopeProvider,
)


IGNORED_DIR_NAMES: frozenset[str] = frozenset({
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".nox",
    "build",
    "dist",
    "node_modules",
})

IGNORED_DIR_SUFFIXES: tuple[str, ...] = (".egg-info", ".dist-info")

IGNORED_FILE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo", ".pyd")
IGNORED_FILE_NAMES: frozenset[str] = frozenset({".DS_Store", "Thumbs.db"})
FIXED_WORKERS: int = 4

BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".eot",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".bmp",
    ".tiff",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".bz2",
    ".xz",
    ".7z",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".pyc",
    ".pyo",
    ".pyd",
    ".wasm",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".mp3",
    ".mp4",
    ".wav",
    ".ogg",
})

DIRECTIVE_COMMENT_RE = re.compile(
    r"^#\s*(?:"
    r"!|"
    r"-\*-.*coding[:=].*-\*-|"
    r"coding[:=]\s*[-_.a-zA-Z0-9]+|"
    r"type\s*:|"
    r"noqa(?:\b|:|$)|"
    r"pylint\s*:|"
    r"mypy\s*:|"
    r"pyright\s*:|"
    r"fmt\s*:|"
    r"ruff\s*:"
    r")",
    re.IGNORECASE,
)

PYTHON_KEYWORDS_AND_BUILTINS: frozenset[str] = frozenset({
    "False",
    "None",
    "True",
    "and",
    "as",
    "assert",
    "async",
    "await",
    "break",
    "class",
    "continue",
    "def",
    "del",
    "elif",
    "else",
    "except",
    "finally",
    "for",
    "from",
    "global",
    "if",
    "import",
    "in",
    "is",
    "lambda",
    "nonlocal",
    "not",
    "or",
    "pass",
    "raise",
    "return",
    "try",
    "while",
    "with",
    "yield",
    "read_data_bytes",
    "read_data_text",
    "_PACKAGE_TEXT_DATA",
    "_PACKAGE_BINARY_DATA_B64",
})


def is_directive_comment(comment_text: str) -> bool:
    """Return True if *comment_text* is a Python language/tool directive comment."""
    return bool(DIRECTIVE_COMMENT_RE.match(comment_text))


def _is_string_literal_expr(node: cst.BaseExpression) -> bool:
    """Return True if *node* is a static string literal (valid docstring)."""
    if isinstance(node, cst.SimpleString):
        return True
    if isinstance(node, cst.ConcatenatedString):
        return _is_string_literal_expr(node.left) and _is_string_literal_expr(node.right)
    return False


def _filter_empty_lines(
    lines: Sequence[cst.EmptyLine],
) -> tuple[cst.EmptyLine, ...]:
    """Keep blank lines and directive comments; drop ordinary comment lines."""
    kept: list[cst.EmptyLine] = []
    for line in lines:
        if line.comment is None or is_directive_comment(line.comment.value):
            kept.append(line)
    return tuple(kept)


def _prepend_leading_lines(
    stmt: cst.BaseStatement,
    extra_lines: Sequence[cst.EmptyLine],
) -> cst.BaseStatement:
    """Attach preserved directive leading lines to the following statement."""
    if not extra_lines:
        return stmt
    if hasattr(stmt, "leading_lines"):
        existing = stmt.leading_lines
        return stmt.with_changes(leading_lines=tuple(extra_lines) + tuple(existing))
    return stmt


class StripCommentsAndDocstringsTransformer(cst.CSTTransformer):
    """Strip non-directive comments and module/class/function docstrings via LibCST."""

    def _strip_body_docstring(
        self,
        body: Sequence[cst.BaseStatement],
        is_suite: bool = False,
    ) -> tuple[list[cst.BaseStatement], tuple[cst.EmptyLine, ...]]:
        """Remove leading docstring statement if present at index 0 of a block."""
        if not body:
            return list(body), ()

        first = body[0]
        if not isinstance(first, cst.SimpleStatementLine) or not first.body:
            return list(body), ()

        first_small = first.body[0]
        if not (isinstance(first_small, cst.Expr) and _is_string_literal_expr(first_small.value)):
            return list(body), ()

        # Ensure it's a pure str literal (not bytes b"...")
        if isinstance(first_small.value, cst.SimpleString) and first_small.value.prefix.lower().startswith("b"):
            return list(body), ()

        remaining_small = list(first.body[1:])
        preserved_leading = _filter_empty_lines(first.leading_lines)
        preserved_directives = tuple(line for line in preserved_leading if line.comment is not None)

        if remaining_small:
            new_first = first.with_changes(body=remaining_small)
            return [new_first, *body[1:]], ()

        rest = list(body[1:])
        if rest:
            rest[0] = _prepend_leading_lines(rest[0], preserved_directives)
            return rest, ()

        if is_suite:
            pass_stmt = cst.SimpleStatementLine(
                leading_lines=preserved_directives,
                body=[cst.Pass()],
            )
            return [pass_stmt], ()

        return [], preserved_directives

    def leave_EmptyLine(
        self,
        original_node: cst.EmptyLine,
        updated_node: cst.EmptyLine,
    ) -> cst.EmptyLine | cst.RemovalSentinel:
        if updated_node.comment is not None and not is_directive_comment(updated_node.comment.value):
            return cst.RemoveFromParent()
        return updated_node

    def leave_TrailingWhitespace(
        self,
        original_node: cst.TrailingWhitespace,
        updated_node: cst.TrailingWhitespace,
    ) -> cst.TrailingWhitespace:
        if updated_node.comment is not None and not is_directive_comment(updated_node.comment.value):
            return updated_node.with_changes(
                whitespace=cst.SimpleWhitespace(""),
                comment=None,
            )
        return updated_node

    def leave_FunctionDef(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.FunctionDef:
        if isinstance(updated_node.body, cst.IndentedBlock):
            new_body, trailing_dirs = self._strip_body_docstring(updated_node.body.body, is_suite=True)
            new_footer = tuple(updated_node.body.footer) + trailing_dirs
            return updated_node.with_changes(body=updated_node.body.with_changes(body=new_body, footer=new_footer))
        if isinstance(updated_node.body, cst.SimpleStatementSuite):
            # SimpleStatementSuite.body is Sequence[BaseSmallStatement]
            sb = list(updated_node.body.body)
            if (sb and isinstance(sb[0], cst.Expr) and _is_string_literal_expr(sb[0].value)) and not (
                isinstance(sb[0].value, cst.SimpleString) and sb[0].value.prefix.lower().startswith("b")
            ):
                sb = sb[1:]
                if not sb:
                    sb = [cst.Pass()]
                return updated_node.with_changes(body=updated_node.body.with_changes(body=sb))
        return updated_node

    def leave_ClassDef(
        self,
        original_node: cst.ClassDef,
        updated_node: cst.ClassDef,
    ) -> cst.ClassDef:
        if isinstance(updated_node.body, cst.IndentedBlock):
            new_body, trailing_dirs = self._strip_body_docstring(updated_node.body.body, is_suite=True)
            new_footer = tuple(updated_node.body.footer) + trailing_dirs
            return updated_node.with_changes(body=updated_node.body.with_changes(body=new_body, footer=new_footer))
        if isinstance(updated_node.body, cst.SimpleStatementSuite):
            sb = list(updated_node.body.body)
            if (sb and isinstance(sb[0], cst.Expr) and _is_string_literal_expr(sb[0].value)) and not (
                isinstance(sb[0].value, cst.SimpleString) and sb[0].value.prefix.lower().startswith("b")
            ):
                sb = sb[1:]
                if not sb:
                    sb = [cst.Pass()]
                return updated_node.with_changes(body=updated_node.body.with_changes(body=sb))
        return updated_node

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        new_body, trailing_dirs = self._strip_body_docstring(updated_node.body, is_suite=False)
        new_footer = _filter_empty_lines(updated_node.footer) + trailing_dirs
        new_header = _filter_empty_lines(updated_node.header)
        return updated_node.with_changes(
            header=new_header,
            body=new_body,
            footer=new_footer,
        )


@dataclass(frozen=True)
class DataFileEntry:
    """Embedded non-Python package data file."""

    rel_path: str
    is_binary: bool
    text_content: str | None = None
    b64_content: str | None = None


@dataclass(frozen=True)
class ImportTarget:
    """Resolved internal import target."""

    module: str
    symbol: str | None  # None means whole module reference


@dataclass(frozen=True)
class EntryPointSpec:
    """Discovered CLI console script entry point."""

    script_name: str
    module: str
    func_name: str


@dataclass
class StatementRecord:
    """Classified top-level statement belonging to a specific source module."""

    module: str
    kind: str  # 'state', 'class', 'function', 'deferred_state', 'type_checking'
    node: cst.BaseStatement | None = None
    defined_names: list[str] = field(default_factory=list)
    def_time_refs: set[str] = field(default_factory=set)
    runtime_refs: set[str] = field(default_factory=set)
    order_index: int = 0
    rendered_code: str | None = None


@dataclass
class ModuleInfo:
    """Parsed metadata and statements for one Python module in the package."""

    module_name: str
    file_path: Path
    is_package: bool
    cst_module: cst.Module
    import_bindings: dict[str, ImportTarget] = field(default_factory=dict)
    star_imports: list[str] = field(default_factory=list)
    defined_symbols: dict[str, str] = field(default_factory=dict)  # sym -> 'const'|'class'|'func'|'global'
    explicit_all: list[str] | None = None
    has_main_guard: bool = False
    main_guard_stmts: list[cst.BaseStatement] = field(default_factory=list)
    future_imports: set[str] = field(default_factory=set)
    external_import_stmts: list[cst.BaseStatement] = field(default_factory=list)
    type_checking_stmts: list[cst.BaseStatement] = field(default_factory=list)
    state_stmts: list[StatementRecord] = field(default_factory=list)
    class_stmts: list[StatementRecord] = field(default_factory=list)
    function_stmts: list[StatementRecord] = field(default_factory=list)
    directive_comments: list[str] = field(default_factory=list)


def _dotted_name_to_str(node: cst.BaseExpression) -> str:
    """Convert a Name or nested Attribute dotted path to string."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        left = _dotted_name_to_str(node.value)
        return f"{left}.{node.attr.value}" if left else node.attr.value
    return ""


def _resolve_relative_module(current_module: str, is_package: bool, level: int, mod_str: str) -> str:
    """Resolve a relative import (level >= 1) into an absolute dotted module name."""
    pkg_parts = current_module.split(".") if is_package else current_module.split(".")[:-1]
    up = level - 1
    if up > len(pkg_parts):
        base_parts: list[str] = []
    elif up == 0:
        base_parts = list(pkg_parts)
    else:
        base_parts = pkg_parts[:-up]
    if mod_str:
        base_parts.extend(mod_str.split("."))
    return ".".join(base_parts)


def _extract_target_names(target: cst.BaseAssignTargetExpression) -> list[str]:
    """Extract assigned variable names from an assignment target."""
    if isinstance(target, cst.Name):
        return [target.value]
    if isinstance(target, (cst.Tuple, cst.List)):
        names: list[str] = []
        for elem in target.elements:
            if isinstance(elem, (cst.StarredElement, cst.Element)):
                names.extend(_extract_target_names(elem.value))
        return names
    if isinstance(target, cst.StarredElement):
        return _extract_target_names(target.value)
    return []


def _extract_string_list(expr: cst.BaseExpression) -> list[str] | None:
    """Extract a static list/tuple of strings (e.g. ``__all__ = [...]``)."""
    if not isinstance(expr, (cst.List, cst.Tuple)):
        return None
    result: list[str] = []
    for elem in expr.elements:
        if not isinstance(elem, cst.Element) or not isinstance(elem.value, cst.SimpleString):
            return None
        val = elem.value.evaluated_value
        if not isinstance(val, str):
            return None
        result.append(val)
    return result


class _GlobalNameCollector(cst.CSTVisitor):
    """Collect ``global name`` declarations inside a module's functions."""

    def __init__(self) -> None:
        self.global_names: list[str] = []

    def visit_Global(self, node: cst.Global) -> None:
        for item in node.names:
            self.global_names.append(item.name.value)


class _NameRefCollector(cst.CSTVisitor):
    """Collect free/referenced identifier names in an expression or block."""

    def __init__(self, skip_function_bodies: bool = False) -> None:
        self.refs: set[str] = set()
        self.skip_function_bodies = skip_function_bodies

    def visit_Name(self, node: cst.Name) -> None:
        self.refs.add(node.value)

    def visit_Attribute(self, node: cst.Attribute) -> bool:
        # Only visit the base expression, not `.attr`
        node.value.visit(self)
        return False

    def visit_Arg(self, node: cst.Arg) -> bool:
        # Skip keyword arg name, visit value
        node.value.visit(self)
        return False

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        for dec in node.decorators:
            dec.visit(self)
        if node.returns is not None:
            node.returns.visit(self)
        for p in (
            list(node.params.posonly_params)
            + list(node.params.params)
            + ([node.params.star_arg] if isinstance(node.params.star_arg, cst.Param) else [])
            + list(node.params.kwonly_params)
            + ([node.params.star_kwarg] if node.params.star_kwarg is not None else [])
        ):
            if p.annotation is not None:
                p.annotation.visit(self)
            if p.default is not None:
                p.default.visit(self)
        if not self.skip_function_bodies:
            node.body.visit(self)
        return False

    def visit_Lambda(self, node: cst.Lambda) -> bool:
        for p in (
            list(node.params.posonly_params)
            + list(node.params.params)
            + ([node.params.star_arg] if isinstance(node.params.star_arg, cst.Param) else [])
            + list(node.params.kwonly_params)
            + ([node.params.star_kwarg] if node.params.star_kwarg is not None else [])
        ):
            if p.default is not None:
                p.default.visit(self)
        if not self.skip_function_bodies:
            node.body.visit(self)
        return False


def _collect_refs(node: cst.CSTNode, skip_function_bodies: bool = False) -> set[str]:
    collector = _NameRefCollector(skip_function_bodies=skip_function_bodies)
    node.visit(collector)
    return collector.refs


def _is_main_guard(node: cst.If) -> bool:
    """Return True if *node* is ``if __name__ == '__main__':``."""
    test = node.test
    if isinstance(test, cst.Comparison) and len(test.comparisons) == 1:
        comp = test.comparisons[0]
        if isinstance(comp.operator, cst.Equal):
            left = test.left
            right = comp.comparator
            if (
                isinstance(left, cst.Name)
                and left.value == "__name__"
                and (isinstance(right, cst.SimpleString) and right.evaluated_value == "__main__")
            ):
                return True
            if isinstance(right, cst.Name) and right.value == "__name__":
                if isinstance(left, cst.SimpleString) and left.evaluated_value == "__main__":
                    return True
    return False


def _is_type_checking_guard(node: cst.If) -> bool:
    """Return True if *node* is ``if TYPE_CHECKING:`` or ``if typing.TYPE_CHECKING:``."""
    return m.matches(
        node.test,
        m.Name("TYPE_CHECKING") | m.Attribute(value=m.Name("typing"), attr=m.Name("TYPE_CHECKING")),
    )


def _contains_only_imports(stmt: cst.BaseStatement) -> bool:
    """Return True if a top-level compound statement contains only imports/pass."""
    if isinstance(stmt, cst.SimpleStatementLine):
        return all(isinstance(s, (cst.Import, cst.ImportFrom, cst.Pass)) for s in stmt.body)
    if isinstance(stmt, cst.If):
        ok = all(_contains_only_imports(s) for s in stmt.body.body)
        if isinstance(stmt.orelse, cst.Else):
            ok = ok and all(_contains_only_imports(s) for s in stmt.orelse.body.body)
        elif isinstance(stmt.orelse, cst.If):
            ok = ok and _contains_only_imports(stmt.orelse)
        return ok
    if isinstance(stmt, cst.Try):
        ok = all(_contains_only_imports(s) for s in stmt.body.body)
        for handler in stmt.handlers:
            ok = ok and all(_contains_only_imports(s) for s in handler.body.body)
        if stmt.orelse is not None:
            ok = ok and all(_contains_only_imports(s) for s in stmt.orelse.body.body)
        if stmt.finalbody is not None:
            ok = ok and all(_contains_only_imports(s) for s in stmt.finalbody.body.body)
        return ok
    return False


class _SetupPyEntryPointVisitor(cst.CSTVisitor):
    """Extract console_scripts entry points from setup.py using LibCST."""

    def __init__(self) -> None:
        self.scripts: list[str] = []

    def _extract_str_list(self, node: cst.BaseExpression) -> list[str]:
        res: list[str] = []
        if isinstance(node, (cst.List, cst.Tuple)):
            for elem in node.elements:
                if isinstance(elem, cst.Element) and isinstance(elem.value, cst.SimpleString):
                    val = elem.value.evaluated_value
                    if isinstance(val, str):
                        res.append(val)
        return res

    def visit_Arg(self, node: cst.Arg) -> None:
        if node.keyword is not None and node.keyword.value == "entry_points":
            if isinstance(node.value, cst.Dict):
                for el in node.value.elements:
                    if isinstance(el, cst.DictElement) and isinstance(el.key, cst.SimpleString):
                        if el.key.evaluated_value == "console_scripts":
                            self.scripts.extend(self._extract_str_list(el.value))


def _parse_entry_spec(spec: str, script_name: str, pkg_name: str) -> EntryPointSpec | None:
    """Parse ``module:func`` or ``name = module:func`` into EntryPointSpec."""
    rhs = spec.strip()
    if "=" in rhs and not script_name:
        lhs, rhs = rhs.split("=", 1)
        script_name = lhs.strip()
        rhs = rhs.strip()
    if ":" not in rhs:
        return None
    mod_part, func_part = rhs.split(":", 1)
    mod_part = mod_part.strip()
    func_part = func_part.strip().split(".")[0]
    if mod_part == pkg_name or mod_part.startswith(f"{pkg_name}."):
        return EntryPointSpec(script_name=script_name, module=mod_part, func_name=func_part)
    return None


def discover_entry_points(package_dir: Path, pkg_name: str) -> list[EntryPointSpec]:
    """Discover console_scripts from pyproject.toml, setup.cfg, setup.py, or dist-info."""
    entries: list[EntryPointSpec] = []
    search_roots = [package_dir.parent]
    if package_dir.parent.name == "src":
        search_roots.append(package_dir.parent.parent)

    for root in search_roots:
        pyproject = root / "pyproject.toml"
        if pyproject.is_file() and not pyproject.is_symlink():
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                proj_scripts = data.get("project", {}).get("scripts", {})
                if isinstance(proj_scripts, dict):
                    for sname, target in proj_scripts.items():
                        if isinstance(target, str):
                            parsed = _parse_entry_spec(target, str(sname), pkg_name)
                            if parsed is not None:
                                entries.append(parsed)
                poetry_scripts = data.get("tool", {}).get("poetry", {}).get("scripts", {})
                if isinstance(poetry_scripts, dict):
                    for sname, target in poetry_scripts.items():
                        if isinstance(target, str):
                            parsed = _parse_entry_spec(target, str(sname), pkg_name)
                            if parsed is not None:
                                entries.append(parsed)
            except Exception:
                pass

        setup_cfg = root / "setup.cfg"
        if setup_cfg.is_file() and not setup_cfg.is_symlink():
            try:
                cp = configparser.ConfigParser()
                cp.read(setup_cfg, encoding="utf-8")
                if cp.has_option("options.entry_points", "console_scripts"):
                    raw = cp.get("options.entry_points", "console_scripts")
                    for line in raw.splitlines():
                        line = line.strip()
                        if line and "=" in line:
                            parsed = _parse_entry_spec(line, "", pkg_name)
                            if parsed is not None:
                                entries.append(parsed)
            except Exception:
                pass

        setup_py = root / "setup.py"
        if setup_py.is_file() and not setup_py.is_symlink():
            try:
                mod = cst.parse_module(setup_py.read_bytes())
                visitor = _SetupPyEntryPointVisitor()
                mod.visit(visitor)
                for line in visitor.scripts:
                    parsed = _parse_entry_spec(line, "", pkg_name)
                    if parsed is not None:
                        entries.append(parsed)
            except Exception:
                pass

        # Installed package metadata (*.dist-info/entry_points.txt)
        norm_pkg = pkg_name.lower().replace("-", "_")
        for dist_dir in sorted(root.glob("*.dist-info")):
            if not dist_dir.is_dir() or dist_dir.is_symlink():
                continue
            dist_stem = dist_dir.name.lower().split("-")[0].replace("-", "_")
            if dist_stem != norm_pkg:
                continue
            ep_file = dist_dir / "entry_points.txt"
            if ep_file.is_file() and not ep_file.is_symlink():
                try:
                    cp = configparser.ConfigParser()
                    cp.read(ep_file, encoding="utf-8")
                    if cp.has_section("console_scripts"):
                        for sname, target in cp.items("console_scripts"):
                            parsed = _parse_entry_spec(target, sname, pkg_name)
                            if parsed is not None:
                                entries.append(parsed)
                except Exception:
                    pass

    # Deduplicate preserving order
    seen: set[tuple[str, str, str]] = set()
    unique: list[EntryPointSpec] = []
    for e in entries:
        key = (e.script_name, e.module, e.func_name)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def resolve_package_root(input_path: Path) -> tuple[Path, str]:
    """Locate the package directory (containing ``__init__.py``) and package name."""
    resolved = input_path.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise FileNotFoundError(f"Package directory does not exist: {input_path}")

    if (resolved / "__init__.py").is_file():
        return resolved, resolved.name

    # Check if user passed a project root (e.g. repo root containing <pkg>/ or src/<pkg>/)
    candidates: list[Path] = []
    for base in (resolved, resolved / "src"):
        if base.is_dir() and not base.is_symlink():
            for child in sorted(base.iterdir()):
                if (
                    child.is_dir()
                    and not child.is_symlink()
                    and child.name not in IGNORED_DIR_NAMES
                    and not child.name.endswith(IGNORED_DIR_SUFFIXES)
                    and not child.name.startswith(".")
                    and child.name not in ("tests", "test", "docs", "scripts")
                    and (child / "__init__.py").is_file()
                ):
                    candidates.append(child)

    if len(candidates) == 1:
        return candidates[0], candidates[0].name

    raise ValueError(
        f"Could not find a Python package (__init__.py) in '{input_path}'. Pass the package directory directly."
    )


def discover_files(
    package_dir: Path,
    pkg_name: str,
    exclude_tests: bool = False,
) -> tuple[list[tuple[str, Path, bool]], list[DataFileEntry]]:
    """Walk *package_dir* safely, skipping symlinks and build/VCS artifacts."""
    py_modules: list[tuple[str, Path, bool]] = []
    data_files: list[DataFileEntry] = []

    for dirpath, dirnames, filenames in os.walk(package_dir, followlinks=False):
        cur_dir = Path(dirpath)
        # Prune ignored directories and symlinks in-place
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            full_d = cur_dir / d
            if full_d.is_symlink():
                continue
            if d in IGNORED_DIR_NAMES or d.endswith(IGNORED_DIR_SUFFIXES):
                continue
            if exclude_tests and d in ("tests", "test"):
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            full_f = cur_dir / fname
            if full_f.is_symlink():
                continue
            if fname in IGNORED_FILE_NAMES or fname.endswith(IGNORED_FILE_SUFFIXES):
                continue

            rel = full_f.relative_to(package_dir)
            if fname.endswith(".py"):
                if exclude_tests and (fname.startswith("test_") or fname == "tests.py"):
                    continue
                parts = list(rel.parts)
                if parts[-1] == "__init__.py":
                    sub_parts = parts[:-1]
                    mod_name = pkg_name if not sub_parts else f"{pkg_name}.{'.'.join(sub_parts)}"
                    py_modules.append((mod_name, full_f, True))
                else:
                    stem = parts[-1][:-3]
                    sub_parts = [*parts[:-1], stem]
                    mod_name = f"{pkg_name}.{'.'.join(sub_parts)}"
                    py_modules.append((mod_name, full_f, False))
            else:
                rel_posix = rel.as_posix()
                raw = full_f.read_bytes()
                is_bin = full_f.suffix.lower() in BINARY_EXTENSIONS or b"\x00" in raw
                text_val: str | None = None
                if not is_bin:
                    try:
                        text_val = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        is_bin = True
                if is_bin:
                    b64_val = base64.b64encode(raw).decode("ascii")
                    data_files.append(DataFileEntry(rel_path=rel_posix, is_binary=True, b64_content=b64_val))
                else:
                    data_files.append(DataFileEntry(rel_path=rel_posix, is_binary=False, text_content=text_val))

    # Deterministic order: root package first, then sorted module names
    py_modules.sort(key=lambda item: (0 if item[0] == pkg_name else 1, item[0]))
    data_files.sort(key=lambda item: item.rel_path)
    return py_modules, data_files


def _is_internal_module(mod_name: str, pkg_name: str) -> bool:
    return mod_name == pkg_name or mod_name.startswith(f"{pkg_name}.")


def _process_import_statement(
    imp_stmt: cst.Import | cst.ImportFrom,
    mod_info: ModuleInfo,
    pkg_name: str,
) -> cst.BaseSmallStatement | None:
    """Classify an import statement, recording internal bindings and returning external CST if any."""
    if isinstance(imp_stmt, cst.Import):
        kept_aliases: list[cst.ImportAlias] = []
        for alias in imp_stmt.names:
            dotted = _dotted_name_to_str(alias.name)
            if _is_internal_module(dotted, pkg_name):
                if alias.asname is not None and isinstance(alias.asname.name, cst.Name):
                    local_name = alias.asname.name.value
                    mod_info.import_bindings[local_name] = ImportTarget(module=dotted, symbol=None)
                else:
                    top_name = dotted.split(".")[0]
                    mod_info.import_bindings[top_name] = ImportTarget(module=top_name, symbol=None)
            else:
                kept_aliases.append(alias.with_changes(comma=cst.MaybeSentinel.DEFAULT))
        if not kept_aliases:
            return None
        return imp_stmt.with_changes(names=kept_aliases)

    # cst.ImportFrom
    level = len(imp_stmt.relative)
    mod_part = _dotted_name_to_str(imp_stmt.module) if imp_stmt.module is not None else ""
    if level > 0:
        abs_mod = _resolve_relative_module(mod_info.module_name, mod_info.is_package, level, mod_part)
        is_internal = True
    else:
        abs_mod = mod_part
        is_internal = _is_internal_module(abs_mod, pkg_name)

    if abs_mod == "__future__":
        if not isinstance(imp_stmt.names, cst.ImportStar):
            for alias in imp_stmt.names:
                mod_info.future_imports.add(_dotted_name_to_str(alias.name))
        return None

    if is_internal:
        if isinstance(imp_stmt.names, cst.ImportStar):
            mod_info.star_imports.append(abs_mod)
        else:
            for alias in imp_stmt.names:
                orig = _dotted_name_to_str(alias.name)
                local = (
                    alias.asname.name.value
                    if alias.asname is not None and isinstance(alias.asname.name, cst.Name)
                    else orig
                )
                mod_info.import_bindings[local] = ImportTarget(module=abs_mod, symbol=orig)
        return None

    return imp_stmt


def analyze_module(
    mod_name: str,
    file_path: Path,
    is_package: bool,
    pkg_name: str,
    strip: bool,
) -> ModuleInfo:
    """Parse a module with LibCST, optionally strip comments/docstrings, and classify statements."""
    raw_bytes = file_path.read_bytes()
    cst_mod = cst.parse_module(raw_bytes)
    if strip:
        cst_mod = cst_mod.visit(StripCommentsAndDocstringsTransformer())

    info = ModuleInfo(
        module_name=mod_name,
        file_path=file_path,
        is_package=is_package,
        cst_module=cst_mod,
    )

    for hline in cst_mod.header:
        if hline.comment is not None and is_directive_comment(hline.comment.value):
            cval = hline.comment.value
            if not cval.startswith("#!") and "coding" not in cval.lower():
                info.directive_comments.append(cval)

    # Collect `global` declarations inside functions as module-level symbols
    g_collector = _GlobalNameCollector()
    cst_mod.visit(g_collector)
    for gname in g_collector.global_names:
        info.defined_symbols.setdefault(gname, "global")

    for stmt in cst_mod.body:
        if isinstance(stmt, cst.ClassDef):
            cname = stmt.name.value
            info.defined_symbols[cname] = "class"
            info.class_stmts.append(
                StatementRecord(
                    module=mod_name,
                    kind="class",
                    node=stmt,
                    defined_names=[cname],
                )
            )
        elif isinstance(stmt, cst.FunctionDef):
            fname = stmt.name.value
            info.defined_symbols[fname] = "func"
            info.function_stmts.append(
                StatementRecord(
                    module=mod_name,
                    kind="function",
                    node=stmt,
                    defined_names=[fname],
                )
            )
        elif isinstance(stmt, cst.If):
            if _is_main_guard(stmt):
                info.has_main_guard = True
                if mod_name in (pkg_name, f"{pkg_name}.__main__"):
                    info.main_guard_stmts.extend(stmt.body.body)
                continue
            if _is_type_checking_guard(stmt):
                for inner in stmt.body.body:
                    if isinstance(inner, cst.SimpleStatementLine):
                        kept_inner: list[cst.BaseSmallStatement] = []
                        for small in inner.body:
                            if isinstance(small, (cst.Import, cst.ImportFrom)):
                                ext = _process_import_statement(small, info, pkg_name)
                                if ext is not None:
                                    kept_inner.append(ext)
                            else:
                                kept_inner.append(small)
                        if kept_inner:
                            info.type_checking_stmts.append(inner.with_changes(body=kept_inner))
                    else:
                        info.type_checking_stmts.append(inner)
                continue
            if _contains_only_imports(stmt):
                info.external_import_stmts.append(stmt)
                continue
            # General top-level `if` state
            info.state_stmts.append(StatementRecord(module=mod_name, kind="state", node=stmt))
        elif isinstance(stmt, cst.Try):
            if _contains_only_imports(stmt):
                info.external_import_stmts.append(stmt)
                continue
            info.state_stmts.append(StatementRecord(module=mod_name, kind="state", node=stmt))
        elif isinstance(stmt, cst.SimpleStatementLine):
            # Split multi-small-statement lines into individual statements cleanly
            last_idx = len(stmt.body) - 1
            for idx, small in enumerate(stmt.body):
                leading = stmt.leading_lines if idx == 0 else ()
                trailing = stmt.trailing_whitespace if idx == last_idx else cst.TrailingWhitespace()
                if isinstance(small, (cst.Import, cst.ImportFrom)):
                    ext = _process_import_statement(small, info, pkg_name)
                    if ext is not None:
                        info.external_import_stmts.append(
                            cst.SimpleStatementLine(
                                leading_lines=leading,
                                body=[ext],
                                trailing_whitespace=trailing,
                            )
                        )
                    else:
                        for lline in leading:
                            if lline.comment is not None and is_directive_comment(lline.comment.value):
                                info.directive_comments.append(lline.comment.value)
                        if trailing.comment is not None and is_directive_comment(trailing.comment.value):
                            info.directive_comments.append(trailing.comment.value)
                elif isinstance(small, cst.Assign):
                    targets: list[str] = []
                    for t in small.targets:
                        targets.extend(_extract_target_names(t.target))
                    if targets == ["__all__"]:
                        extracted = _extract_string_list(small.value)
                        if extracted is not None:
                            info.explicit_all = extracted
                        continue
                    for tname in targets:
                        info.defined_symbols.setdefault(tname, "const")
                    info.state_stmts.append(
                        StatementRecord(
                            module=mod_name,
                            kind="state",
                            node=cst.SimpleStatementLine(
                                leading_lines=leading,
                                body=[small],
                                trailing_whitespace=trailing,
                            ),
                            defined_names=targets,
                        )
                    )
                elif isinstance(small, cst.AnnAssign):
                    targets = _extract_target_names(small.target)
                    if targets == ["__all__"] and small.value is not None:
                        extracted = _extract_string_list(small.value)
                        if extracted is not None:
                            info.explicit_all = extracted
                        continue
                    for tname in targets:
                        info.defined_symbols.setdefault(tname, "const")
                    info.state_stmts.append(
                        StatementRecord(
                            module=mod_name,
                            kind="state",
                            node=cst.SimpleStatementLine(
                                leading_lines=leading,
                                body=[small],
                                trailing_whitespace=trailing,
                            ),
                            defined_names=targets,
                        )
                    )
                elif isinstance(small, cst.AugAssign):
                    targets = _extract_target_names(small.target)
                    for tname in targets:
                        info.defined_symbols.setdefault(tname, "const")
                    info.state_stmts.append(
                        StatementRecord(
                            module=mod_name,
                            kind="state",
                            node=cst.SimpleStatementLine(
                                leading_lines=leading,
                                body=[small],
                                trailing_whitespace=trailing,
                            ),
                            defined_names=targets,
                        )
                    )
                elif isinstance(small, cst.Expr):
                    if isinstance(small.value, cst.SimpleString):
                        continue
                    info.state_stmts.append(
                        StatementRecord(
                            module=mod_name,
                            kind="state",
                            node=cst.SimpleStatementLine(
                                leading_lines=leading,
                                body=[small],
                                trailing_whitespace=trailing,
                            ),
                        )
                    )
                elif isinstance(small, cst.Pass):
                    continue
                else:
                    info.state_stmts.append(
                        StatementRecord(
                            module=mod_name,
                            kind="state",
                            node=cst.SimpleStatementLine(
                                leading_lines=leading,
                                body=[small],
                                trailing_whitespace=trailing,
                            ),
                        )
                    )
        else:
            info.state_stmts.append(StatementRecord(module=mod_name, kind="state", node=stmt))

    return info


def build_symbol_table(
    modules: dict[str, ModuleInfo],
    pkg_name: str,
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], ImportTarget]]:
    """Build deterministic canonical names for all module-level symbols across *modules*.

    Returns:
        canonical_map: ``(module_name, original_symbol) -> consolidated_name``
        resolved_imports: ``(module_name, local_import_name) -> ImportTarget``
    """

    # Step 1: Resolve star imports iteratively
    def get_exports(mod_name: str, visited: set[str] | None = None) -> list[str]:
        if visited is None:
            visited = set()
        if mod_name in visited or mod_name not in modules:
            return []
        visited.add(mod_name)
        mod = modules[mod_name]
        if mod.explicit_all is not None:
            return list(mod.explicit_all)
        exports = [s for s in mod.defined_symbols if not s.startswith("_")]
        for k in mod.import_bindings:
            if not k.startswith("_") and k not in exports:
                exports.append(k)
        for star_mod in mod.star_imports:
            for s in get_exports(star_mod, visited):
                if s not in exports:
                    exports.append(s)
        return exports

    # Populate star-imported bindings into resolved_imports
    resolved_imports: dict[tuple[str, str], ImportTarget] = {}
    for mod_name, mod in modules.items():
        for star_mod in mod.star_imports:
            for exp_sym in get_exports(star_mod):
                if exp_sym not in mod.import_bindings and exp_sym not in mod.defined_symbols:
                    resolved_imports[(mod_name, exp_sym)] = ImportTarget(module=star_mod, symbol=exp_sym)
        for local_name, target in mod.import_bindings.items():
            resolved_imports[(mod_name, local_name)] = target

    # Follow re-export chains (e.g. A imports X from B which imported X from C)
    def follow_target(mod_name: str, sym: str) -> ImportTarget:
        seen: set[tuple[str, str]] = set()
        cur_mod, cur_sym = mod_name, sym
        while True:
            if (cur_mod, cur_sym) in seen:
                return ImportTarget(module=cur_mod, symbol=cur_sym)
            seen.add((cur_mod, cur_sym))
            if cur_mod not in modules:
                return ImportTarget(module=cur_mod, symbol=cur_sym)
            minfo = modules[cur_mod]
            if cur_sym in minfo.defined_symbols:
                return ImportTarget(module=cur_mod, symbol=cur_sym)
            next_target = resolved_imports.get((cur_mod, cur_sym))
            if next_target is None:
                return ImportTarget(module=cur_mod, symbol=cur_sym)
            if next_target.symbol is None:
                # Importing a submodule: e.g. `from pkg import sub`
                return next_target
            cur_mod, cur_sym = next_target.module, next_target.symbol

    final_imports: dict[tuple[str, str], ImportTarget] = {}
    for (mname, local_name), tgt in resolved_imports.items():
        if tgt.symbol is None or tgt.symbol in (
            "__path__",
            "__name__",
            "__package__",
            "__all__",
            "__file__",
        ):
            final_imports[(mname, local_name)] = tgt
        else:
            # Check if tgt.module + "." + tgt.symbol is actually a submodule!
            cand_sub = f"{tgt.module}.{tgt.symbol}"
            if (
                cand_sub in modules
                and tgt.symbol
                not in modules.get(tgt.module, ModuleInfo("", Path(), False, cst.Module([]))).defined_symbols
            ):
                final_imports[(mname, local_name)] = ImportTarget(module=cand_sub, symbol=None)
            else:
                final_imports[(mname, local_name)] = follow_target(tgt.module, tgt.symbol)

    # Step 2: Count occurrences of every defined symbol name across all modules
    owners_by_name: dict[str, list[str]] = {}
    for mod_name, mod in modules.items():
        for sym in mod.defined_symbols:
            owners_by_name.setdefault(sym, []).append(mod_name)

    root_mod = modules.get(pkg_name)
    root_preferred_names: set[str] = set()
    if root_mod is not None:
        root_preferred_names.update(root_mod.defined_symbols.keys())
        if root_mod.explicit_all:
            root_preferred_names.update(root_mod.explicit_all)

    canonical_map: dict[tuple[str, str], str] = {}
    used_names: set[str] = set(PYTHON_KEYWORDS_AND_BUILTINS)

    # First pass: reserve unique symbols & root-defined symbols (unless colliding with reserved builtins)
    for sym, owners in sorted(owners_by_name.items()):
        if len(owners) == 1 and sym not in used_names:
            canonical_map[(owners[0], sym)] = sym
            used_names.add(sym)

    # Second pass: resolve collisions deterministically
    for sym, owners in sorted(owners_by_name.items()):
        if len(owners) == 1 and (owners[0], sym) in canonical_map:
            continue

        # If root package defines `sym` directly, let root keep bare `sym`
        # or if root re-exports one specific owner's symbol, let that owner keep bare `sym`
        preferred_owner: str | None = None
        if pkg_name in owners and sym not in used_names:
            preferred_owner = pkg_name
        elif root_mod is not None and sym in root_preferred_names and sym not in used_names:
            tgt = final_imports.get((pkg_name, sym))
            if tgt is not None and tgt.symbol == sym and tgt.module in owners:
                preferred_owner = tgt.module

        for owner in owners:
            if owner == preferred_owner and sym not in used_names:
                canonical_map[(owner, sym)] = sym
                used_names.add(sym)
                continue

            if owner == pkg_name:
                prefix = pkg_name
            else:
                rel_mod = owner[len(pkg_name) + 1 :] if owner.startswith(f"{pkg_name}.") else owner
                prefix = rel_mod.replace(".", "_")

            candidate = f"{prefix}_{sym}"
            if candidate in used_names:
                idx = 2
                while f"{candidate}_{idx}" in used_names:
                    idx += 1
                candidate = f"{candidate}_{idx}"
            canonical_map[(owner, sym)] = candidate
            used_names.add(candidate)

    return canonical_map, final_imports


class ScopeAwareRenameTransformer(cst.CSTTransformer):
    """Rewrite module-global & imported internal symbols using LibCST ScopeProvider."""

    METADATA_DEPENDENCIES = (ScopeProvider,)

    def __init__(
        self,
        module_name: str,
        pkg_name: str,
        modules: dict[str, ModuleInfo],
        canonical_map: dict[tuple[str, str], str],
        resolved_imports: dict[tuple[str, str], ImportTarget],
    ) -> None:
        self.module_name = module_name
        self.pkg_name = pkg_name
        self.modules = modules
        self.canonical_map = canonical_map
        self.resolved_imports = resolved_imports
        self.mod_info = modules[module_name]
        self._attr_write_depth = 0
        self._in_function_depth = 0

    def _resolve_local_global_name(self, name: str) -> str | None:
        """Resolve a module-scope symbol name in *self.module_name* to its canonical name."""
        if (self.module_name, name) in self.canonical_map:
            return self.canonical_map[(self.module_name, name)]
        tgt = self.resolved_imports.get((self.module_name, name))
        if tgt is not None and tgt.symbol is not None:
            return self.canonical_map.get((tgt.module, tgt.symbol))
        return None

    def _is_global_or_imported_ref(self, original_node: cst.Name) -> bool:
        """Return True if *original_node* refers to module/global/import scope (not local/class/comp)."""
        scopes = self.get_metadata(ScopeProvider, original_node, None)
        if scopes is None:
            return False

        # In ClassDef/FunctionDef definition header, ClassDef.name / FunctionDef.name is in enclosing scope
        name_str = original_node.value
        assignments = scopes[name_str]
        assign_list = list(assignments)
        if not assign_list:
            # Unassigned in local/class/func scopes -> global/builtin or star-import ref
            return name_str in self.mod_info.defined_symbols or (self.module_name, name_str) in self.resolved_imports

        # If any assignment is in GlobalScope (or ImportAssignment in GlobalScope), and NOT shadowed
        # by FunctionScope / ClassScope / ComprehensionScope:
        has_local_shadow = any(
            isinstance(a.scope, (FunctionScope, ClassScope, ComprehensionScope))
            and not isinstance(a, BuiltinAssignment)
            for a in assign_list
        )
        if has_local_shadow:
            return False
        return any(isinstance(a.scope, GlobalScope) or isinstance(a, ImportAssignment) for a in assign_list)

    def _unwrap_dotted_attribute(self, node: cst.Attribute) -> tuple[cst.Name, list[str]] | None:
        """If *node* is ``base.a.b.c``, return ``(base_name_node, ['a', 'b', 'c'])``."""
        parts: list[str] = []
        cur: cst.BaseExpression = node
        while isinstance(cur, cst.Attribute):
            parts.append(cur.attr.value)
            cur = cur.value
        if isinstance(cur, cst.Name):
            parts.reverse()
            return cur, parts
        return None

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._in_function_depth += 1
        return True

    def leave_FunctionDef(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.FunctionDef:
        self._in_function_depth -= 1
        return updated_node

    def visit_AssignTarget(self, node: cst.AssignTarget) -> bool:
        if isinstance(node.target, cst.Attribute):
            self._attr_write_depth += 1
        return True

    def leave_AssignTarget(
        self,
        original_node: cst.AssignTarget,
        updated_node: cst.AssignTarget,
    ) -> cst.AssignTarget:
        if isinstance(original_node.target, cst.Attribute):
            self._attr_write_depth -= 1
        return updated_node

    def visit_SimpleStatementLine(self, node: cst.SimpleStatementLine) -> bool:
        # Handle lazy internal imports inside functions/methods by replacing them
        return True

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine | cst.RemovalSentinel:
        if self._in_function_depth == 0:
            return updated_node

        new_smalls: list[cst.BaseSmallStatement] = []
        for small in updated_node.body:
            if isinstance(small, cst.Import):
                kept: list[cst.ImportAlias] = []
                repls: list[cst.Assign] = []
                for alias in small.names:
                    dotted = _dotted_name_to_str(alias.name)
                    if _is_internal_module(dotted, self.pkg_name):
                        bind_name = (
                            alias.asname.name.value
                            if alias.asname is not None and isinstance(alias.asname.name, cst.Name)
                            else dotted.split(".")[0]
                        )
                        target_mod = dotted if alias.asname is not None else dotted.split(".")[0]
                        repls.append(
                            cst.Assign(
                                targets=[cst.AssignTarget(cst.Name(bind_name))],
                                value=cst.Subscript(
                                    value=cst.Attribute(cst.Name("sys"), cst.Name("modules")),
                                    slice=[cst.SubscriptElement(cst.Index(cst.SimpleString(repr(target_mod))))],
                                ),
                            )
                        )
                    else:
                        kept.append(alias.with_changes(comma=cst.MaybeSentinel.DEFAULT))
                if kept:
                    new_smalls.append(small.with_changes(names=kept))
                new_smalls.extend(repls)
            elif isinstance(small, cst.ImportFrom):
                level = len(small.relative)
                mod_part = _dotted_name_to_str(small.module) if small.module is not None else ""
                if level > 0:
                    abs_mod = _resolve_relative_module(self.module_name, self.mod_info.is_package, level, mod_part)
                    is_int = True
                else:
                    abs_mod = mod_part
                    is_int = _is_internal_module(abs_mod, self.pkg_name)

                if is_int and not isinstance(small.names, cst.ImportStar):
                    for alias in small.names:
                        orig = _dotted_name_to_str(alias.name)
                        local = (
                            alias.asname.name.value
                            if alias.asname is not None and isinstance(alias.asname.name, cst.Name)
                            else orig
                        )
                        canon = self.canonical_map.get((abs_mod, orig))
                        if canon is not None:
                            new_smalls.append(
                                cst.Assign(
                                    targets=[cst.AssignTarget(cst.Name(local))],
                                    value=cst.Name(canon),
                                )
                            )
                        else:
                            cand_sub = f"{abs_mod}.{orig}"
                            target_mod = cand_sub if cand_sub in self.modules else abs_mod
                            val_expr: cst.BaseExpression = cst.Subscript(
                                value=cst.Attribute(cst.Name("sys"), cst.Name("modules")),
                                slice=[cst.SubscriptElement(cst.Index(cst.SimpleString(repr(target_mod))))],
                            )
                            if cand_sub not in self.modules:
                                val_expr = cst.Attribute(val_expr, cst.Name(orig))
                            new_smalls.append(
                                cst.Assign(
                                    targets=[cst.AssignTarget(cst.Name(local))],
                                    value=val_expr,
                                )
                            )
                elif not is_int:
                    new_smalls.append(small)
            else:
                new_smalls.append(small)

        if not new_smalls:
            return cst.RemoveFromParent()
        return updated_node.with_changes(body=new_smalls)

    def leave_Global(
        self,
        original_node: cst.Global,
        updated_node: cst.Global,
    ) -> cst.Global:
        new_items: list[cst.NameItem] = []
        changed = False
        for item in updated_node.names:
            canon = self.canonical_map.get((self.module_name, item.name.value))
            if canon is not None and canon != item.name.value:
                new_items.append(item.with_changes(name=cst.Name(canon)))
                changed = True
            else:
                new_items.append(item)
        return updated_node.with_changes(names=new_items) if changed else updated_node

    def leave_Attribute(
        self,
        original_node: cst.Attribute,
        updated_node: cst.Attribute,
    ) -> cst.BaseExpression:
        if self._attr_write_depth > 0:
            return updated_node

        unwrapped = self._unwrap_dotted_attribute(original_node)
        if unwrapped is None:
            return updated_node

        base_name_node, attrs = unwrapped
        if not self._is_global_or_imported_ref(base_name_node):
            return updated_node

        tgt = self.resolved_imports.get((self.module_name, base_name_node.value))
        if tgt is None or tgt.symbol is not None:
            return updated_node

        # Base name refers to an internal package/submodule!
        # Check if `tgt.module + "." + attrs[:-1]` is a submodule and `attrs[-1]` is a symbol in it
        if len(attrs) >= 1:
            candidate_mod = ".".join([tgt.module, *attrs[:-1]])
            sym = attrs[-1]
            if (candidate_mod, sym) in self.canonical_map:
                return cst.Name(self.canonical_map[(candidate_mod, sym)])

        full_mod = ".".join([tgt.module, *attrs])
        if full_mod in self.modules:
            return cst.Subscript(
                value=cst.Attribute(cst.Name("sys"), cst.Name("modules")),
                slice=[cst.SubscriptElement(cst.Index(cst.SimpleString(repr(full_mod))))],
            )
        return updated_node

    def leave_Name(
        self,
        original_node: cst.Name,
        updated_node: cst.Name,
    ) -> cst.BaseExpression:
        name = original_node.value

        # Handle non-root submodule dunders (`__name__`, `__path__`, `__package__`)
        if self.module_name != self.pkg_name and name in (
            "__name__",
            "__path__",
            "__package__",
        ):
            scopes = self.get_metadata(ScopeProvider, original_node, None)
            if scopes is not None:
                assigns = list(scopes[name])
                if not assigns or all(isinstance(a, BuiltinAssignment) for a in assigns):
                    if name == "__name__":
                        return cst.SimpleString(repr(self.module_name))
                    if name == "__path__":
                        if self.mod_info.is_package:
                            return cst.List([
                                cst.Element(cst.SimpleString(repr(f"__consolidated__:{self.module_name}")))
                            ])
                        return cst.List([])
                    if name == "__package__":
                        pkg = self.module_name if self.mod_info.is_package else self.module_name.rpartition(".")[0]
                        return cst.SimpleString(repr(pkg))

        if not self._is_global_or_imported_ref(original_node):
            return updated_node

        # Check module dunder imported from another submodule (`from .tests import __path__ as path`)
        tgt = self.resolved_imports.get((self.module_name, name))
        if tgt is not None:
            if tgt.symbol == "__path__":
                target_mod_info = self.modules.get(tgt.module)
                if target_mod_info is not None and target_mod_info.is_package:
                    return cst.List([cst.Element(cst.SimpleString(repr(f"__consolidated__:{tgt.module}")))])
                return cst.List([])
            if tgt.symbol == "__name__":
                return cst.SimpleString(repr(tgt.module))
            if tgt.symbol == "__package__":
                target_mod_info = self.modules.get(tgt.module)
                pkg = (
                    tgt.module
                    if (target_mod_info is not None and target_mod_info.is_package)
                    else tgt.module.rpartition(".")[0]
                )
                return cst.SimpleString(repr(pkg))
            if tgt.symbol is None:
                # Standalone submodule reference (not immediately `.attr` because leave_Attribute
                # didn't replace it, or used as value like `mod = packer`)
                # Note: leave_Attribute runs on parent AFTER leave_Name on child, so if we replace
                # Name with Subscript here, leave_Attribute wouldn't match Name! Wait:
                # Let's NOT replace standalone Name(submod) inside leave_Name if parent is Attribute
                # that will be handled by leave_Attribute. Actually leave_Attribute uses original_node!
                # Since leave_Attribute inspects `original_node` (which still has `cst.Name`),
                # replacing `Name` here is SAFE only if we don't break `updated_node` when
                # `leave_Attribute` falls back to `updated_node`!
                # Better: leave `Name` as-is if `tgt.symbol is None` unless it's NOT inside an Attribute chain;
                # wait: `sys.modules['pkg.sub'].attr` ALSO works as a fallback in Python!
                return cst.Subscript(
                    value=cst.Attribute(cst.Name("sys"), cst.Name("modules")),
                    slice=[cst.SubscriptElement(cst.Index(cst.SimpleString(repr(tgt.module))))],
                )

        canon = self._resolve_local_global_name(name)
        if canon is not None and canon != name:
            return updated_node.with_changes(value=canon)
        return updated_node


def _toposort_records(
    records: list[StatementRecord],
    dep_key_getter: Callable[[StatementRecord], set[str]],
) -> list[StatementRecord]:
    """Stable topological sort of StatementRecords preserving original order when independent."""
    defined_by: dict[str, int] = {}
    for idx, rec in enumerate(records):
        for d in rec.defined_names:
            defined_by.setdefault(d, idx)

    n = len(records)
    adj: list[set[int]] = [set() for _ in range(n)]
    indegree: list[int] = [0] * n

    for idx, rec in enumerate(records):
        for ref in dep_key_getter(rec):
            owner_idx = defined_by.get(ref)
            if owner_idx is not None and owner_idx != idx and idx not in adj[owner_idx]:
                adj[owner_idx].add(idx)
                indegree[idx] += 1

    # Priority queue using sorted ready indices to preserve original relative order
    import heapq

    ready = [i for i in range(n) if indegree[i] == 0]
    heapq.heapify(ready)
    ordered: list[StatementRecord] = []
    visited: set[int] = set()

    while ready:
        cur = heapq.heappop(ready)
        visited.add(cur)
        ordered.append(records[cur])
        for nxt in sorted(adj[cur]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, nxt)

    if len(ordered) < n:
        for i in range(n):
            if i not in visited:
                ordered.append(records[i])
    return ordered


def _sort_state_records_respecting_intra_module_order(
    records: list[StatementRecord],
) -> list[StatementRecord]:
    """Topologically sort state records across modules while keeping same-module order."""
    defined_by: dict[str, list[int]] = {}
    for idx, rec in enumerate(records):
        for d in rec.defined_names:
            defined_by.setdefault(d, []).append(idx)

    n = len(records)
    adj: list[set[int]] = [set() for _ in range(n)]
    indegree: list[int] = [0] * n

    def add_edge(u: int, v: int) -> None:
        if u != v and v not in adj[u]:
            adj[u].add(v)
            indegree[v] += 1

    # Preserve relative order of statements within the same module
    last_in_mod: dict[str, int] = {}
    for idx, rec in enumerate(records):
        if rec.module in last_in_mod:
            add_edge(last_in_mod[rec.module], idx)
        last_in_mod[rec.module] = idx

    # Cross-module value dependencies
    for idx, rec in enumerate(records):
        for ref in rec.runtime_refs:
            for owner_idx in defined_by.get(ref, ()):
                if records[owner_idx].module != rec.module:
                    add_edge(owner_idx, idx)

    import heapq

    ready = [i for i in range(n) if indegree[i] == 0]
    heapq.heapify(ready)
    ordered: list[StatementRecord] = []
    visited: set[int] = set()
    while ready:
        cur = heapq.heappop(ready)
        visited.add(cur)
        ordered.append(records[cur])
        for nxt in sorted(adj[cur]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, nxt)

    if len(ordered) < n:
        for i in range(n):
            if i not in visited:
                ordered.append(records[i])
    return ordered


def _render_cst_stmt(stmt: cst.BaseStatement) -> str:
    """Render a LibCST statement cleanly without stray leading/trailing newlines."""
    mod = cst.Module(body=[stmt])
    return mod.code.strip("\n")


def _render_record(rec: StatementRecord) -> str:
    """Return rendered source text for a StatementRecord."""
    if rec.rendered_code is not None:
        return rec.rendered_code
    if rec.node is not None:
        return _render_cst_stmt(rec.node)
    return ""


def _format_imports_section(
    modules: dict[str, ModuleInfo],
    needs_base64: bool,
    needs_sys: bool,
    needs_types: bool,
    needs_pkgutil: bool,
    has_type_checking: bool,
) -> str:
    """Deduplicate and merge future, stdlib, third-party, and TYPE_CHECKING imports."""
    future_features: set[str] = set()
    plain_imports: set[tuple[str, str | None]] = set()
    from_imports: dict[str, set[tuple[str, str | None]]] = {}
    star_from_imports: set[str] = set()
    fallback_import_blocks: list[str] = []
    seen_fallback_code: set[str] = set()
    type_checking_lines: list[str] = []
    seen_tc_code: set[str] = set()

    if needs_base64:
        plain_imports.add(("base64", None))
    if needs_sys:
        plain_imports.add(("sys", None))
    if needs_types:
        plain_imports.add(("types", None))
    if needs_pkgutil:
        plain_imports.add(("pkgutil", None))
    if has_type_checking:
        from_imports.setdefault("typing", set()).add(("TYPE_CHECKING", None))

    for mod in modules.values():
        future_features.update(mod.future_imports)
        for stmt in mod.external_import_stmts:
            if isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) == 1:
                small = stmt.body[0]
                if isinstance(small, cst.Import):
                    for alias in small.names:
                        mod_str = _dotted_name_to_str(alias.name)
                        as_str = (
                            alias.asname.name.value
                            if alias.asname is not None and isinstance(alias.asname.name, cst.Name)
                            else None
                        )
                        plain_imports.add((mod_str, as_str))
                    continue
                if isinstance(small, cst.ImportFrom):
                    mod_str = _dotted_name_to_str(small.module) if small.module is not None else ""
                    if isinstance(small.names, cst.ImportStar):
                        star_from_imports.add(mod_str)
                    else:
                        bucket = from_imports.setdefault(mod_str, set())
                        for alias in small.names:
                            item = _dotted_name_to_str(alias.name)
                            as_str = (
                                alias.asname.name.value
                                if alias.asname is not None and isinstance(alias.asname.name, cst.Name)
                                else None
                            )
                            bucket.add((item, as_str))
                    continue
            code_str = _render_cst_stmt(stmt)
            if code_str not in seen_fallback_code:
                seen_fallback_code.add(code_str)
                fallback_import_blocks.append(code_str)

        for tc_stmt in mod.type_checking_stmts:
            tc_code = _render_cst_stmt(tc_stmt)
            if tc_code not in seen_tc_code:
                seen_tc_code.add(tc_code)
                type_checking_lines.append(tc_code)

    lines: list[str] = []
    if future_features:
        lines.append(f"from __future__ import {', '.join(sorted(future_features))}")
        lines.append("")

    stdlib_names = getattr(sys, "stdlib_module_names", frozenset())

    def is_stdlib(mod_path: str) -> bool:
        return mod_path.split(".")[0] in stdlib_names

    def emit_import_group(is_std: bool) -> list[str]:
        group_lines: list[str] = []
        for mod_str, as_str in sorted(
            (x for x in plain_imports if is_stdlib(x[0]) == is_std),
            key=lambda x: (x[0], x[1] or ""),
        ):
            group_lines.append(f"import {mod_str} as {as_str}" if as_str else f"import {mod_str}")

        for mod_str in sorted(m_name for m_name in star_from_imports if is_stdlib(m_name) == is_std):
            group_lines.append(f"from {mod_str} import *")

        for mod_str in sorted(m_name for m_name in from_imports if is_stdlib(m_name) == is_std):
            items = sorted(from_imports[mod_str], key=lambda x: (x[0], x[1] or ""))
            rendered_items = [f"{item} as {as_str}" if as_str else item for item, as_str in items]
            single_line = f"from {mod_str} import {', '.join(rendered_items)}"
            if len(single_line) <= 96:
                group_lines.append(single_line)
            else:
                group_lines.append(f"from {mod_str} import (")
                for ri in rendered_items:
                    group_lines.append(f"    {ri},")
                group_lines.append(")")
        return group_lines

    std_lines = emit_import_group(True)
    third_lines = emit_import_group(False)

    if std_lines:
        lines.extend(std_lines)
    if third_lines:
        if std_lines:
            lines.append("")
        lines.extend(third_lines)
    if fallback_import_blocks:
        lines.append("")
        lines.extend(fallback_import_blocks)
    if type_checking_lines:
        lines.append("")
        lines.append("if TYPE_CHECKING:")
        for tc_block in type_checking_lines:
            for tc_line in tc_block.splitlines():
                lines.append(f"    {tc_line}" if tc_line.strip() else "")

    return "\n".join(lines)


def _format_data_section(data_files: list[DataFileEntry]) -> str:
    """Render embedded package data files and read_data_bytes / read_data_text helpers."""
    lines: list[str] = ["_PACKAGE_TEXT_DATA: dict[str, str] = {"]
    for entry in data_files:
        if not entry.is_binary and entry.text_content is not None:
            lines.append(f"    {entry.rel_path!r}: {entry.text_content!r},")
    lines.append("}")
    lines.append("")
    lines.append("_PACKAGE_BINARY_DATA_B64: dict[str, str] = {")
    for entry in data_files:
        if entry.is_binary and entry.b64_content is not None:
            lines.append(f"    {entry.rel_path!r}: {entry.b64_content!r},")
    lines.append("}")
    lines.append("")
    lines.extend([
        "def read_data_bytes(rel_path: str) -> bytes:",
        "    key = rel_path.replace('\\\\', '/').lstrip('./')",
        "    if key in _PACKAGE_BINARY_DATA_B64:",
        "        return base64.b64decode(_PACKAGE_BINARY_DATA_B64[key])",
        "    if key in _PACKAGE_TEXT_DATA:",
        "        return _PACKAGE_TEXT_DATA[key].encode('utf-8')",
        "    raise FileNotFoundError(f'Embedded package data file not found: {rel_path}')",
        "",
        "",
        "def read_data_text(rel_path: str, encoding: str = 'utf-8') -> str:",
        "    key = rel_path.replace('\\\\', '/').lstrip('./')",
        "    if key in _PACKAGE_TEXT_DATA:",
        "        return _PACKAGE_TEXT_DATA[key]",
        "    if key in _PACKAGE_BINARY_DATA_B64:",
        "        return base64.b64decode(_PACKAGE_BINARY_DATA_B64[key]).decode(encoding)",
        "    raise FileNotFoundError(f'Embedded package data file not found: {rel_path}')",
    ])
    return "\n".join(lines)


def _format_submodule_registry(
    pkg_name: str,
    modules: dict[str, ModuleInfo],
    canonical_map: dict[tuple[str, str], str],
    resolved_imports: dict[tuple[str, str], ImportTarget],
) -> str:
    """Generate drop-in package metadata, __all__, sys.modules proxies, and pkgutil finder."""
    lines: list[str] = []

    root_mod = modules.get(pkg_name)
    # Re-export root __init__.py imported bindings at top level of consolidated module
    root_exports_to_bind: list[tuple[str, str]] = []
    if root_mod is not None:
        for (mname, local_name), tgt in sorted(resolved_imports.items()):
            if mname != pkg_name:
                continue
            if tgt.symbol is not None:
                canon = canonical_map.get((tgt.module, tgt.symbol))
                if canon is not None and canon != local_name:
                    root_exports_to_bind.append((local_name, canon))

    if root_exports_to_bind:
        lines.append("# Root package re-exports")
        for local_name, canon in root_exports_to_bind:
            lines.append(f"{local_name} = {canon}")
        lines.append("")

    # Root __all__
    if root_mod is not None and root_mod.explicit_all is not None:
        lines.append(f"__all__ = {root_mod.explicit_all!r}")
    elif root_mod is not None:
        inferred_all = sorted({
            s
            for s in list(root_mod.defined_symbols.keys()) + [k for (m, k) in resolved_imports if m == pkg_name]
            if not s.startswith("_")
        })
        lines.append(f"__all__ = {inferred_all!r}")

    lines.append(f"__path__ = ['__consolidated__:{pkg_name}']")
    lines.append("if __name__ != '__main__':")
    lines.append("    __package__ = __name__")
    lines.append("")

    # Build child map for pkgutil.iter_modules support
    children_by_parent: dict[str, list[tuple[str, bool]]] = {}
    for mod_name, minfo in sorted(modules.items()):
        if mod_name == pkg_name:
            continue
        parent = mod_name.rpartition(".")[0]
        short = mod_name.rpartition(".")[2]
        children_by_parent.setdefault(parent, []).append((short, minfo.is_package))

    lines.append(f"_CONSOLIDATED_CHILDREN: dict[str, list[tuple[str, bool]]] = {children_by_parent!r}")
    lines.append("")
    lines.extend([
        "class _ConsolidatedSubmoduleFinder:",
        "    def __init__(self, path: str) -> None:",
        "        if not path.startswith('__consolidated__:'):",
        "            raise ImportError(path)",
        "        self._pkg = path.split(':', 1)[1]",
        "",
        "    def iter_modules(self, prefix: str = ''):",
        "        for child_name, is_pkg in _CONSOLIDATED_CHILDREN.get(self._pkg, ()):",
        "            yield prefix + child_name, is_pkg",
        "",
        "if _ConsolidatedSubmoduleFinder not in sys.path_hooks:",
        "    sys.path_hooks.append(_ConsolidatedSubmoduleFinder)",
        "",
        "def _register_consolidated_submodules() -> None:",
        f"    root_pkg = {pkg_name!r}",
        "    root_mod = sys.modules.get(__name__)",
        "    created: dict[str, types.ModuleType] = {}",
    ])

    for mod_name, minfo in sorted(modules.items()):
        if mod_name == pkg_name:
            continue
        parent_mod_name, _, child_attr = mod_name.rpartition(".")
        lines.append(f"    _m = types.ModuleType({mod_name!r})")
        if minfo.is_package:
            lines.append(f"    _m.__path__ = ['__consolidated__:{mod_name}']")
            lines.append(f"    _m.__package__ = {mod_name!r}")
        else:
            lines.append(f"    _m.__package__ = {parent_mod_name!r}")
        lines.append("    _m.__file__ = globals().get('__file__', '')")

        if minfo.explicit_all is not None:
            lines.append(f"    _m.__all__ = {minfo.explicit_all!r}")

        # Populate defined symbols on this submodule
        for sym in sorted(minfo.defined_symbols):
            canon = canonical_map.get((mod_name, sym))
            if canon is not None:
                lines.append(f"    if {canon!r} in globals(): _m.{sym} = globals()[{canon!r}]")

        # Populate imported internal symbols on this submodule
        for (m, local_name), tgt in sorted(resolved_imports.items()):
            if m != mod_name or local_name in minfo.defined_symbols:
                continue
            if tgt.symbol is not None:
                canon = canonical_map.get((tgt.module, tgt.symbol))
                if canon is not None:
                    lines.append(f"    if {canon!r} in globals(): _m.{local_name} = globals()[{canon!r}]")

        lines.append(f"    sys.modules[{mod_name!r}] = _m")
        lines.append(f"    created[{mod_name!r}] = _m")
        lines.append("    if __name__ not in ('__main__', root_pkg):")
        lines.append(f"        _alias = __name__ + {mod_name[len(pkg_name) :]!r}")
        lines.append("        sys.modules[_alias] = _m")

    # Wire child attributes onto parent modules without clobbering existing non-module symbols (e.g. __version__)
    for mod_name in sorted(modules.keys()):
        if mod_name == pkg_name:
            continue
        parent_mod_name, _, child_attr = mod_name.rpartition(".")
        if parent_mod_name == pkg_name:
            lines.append(
                f"    if root_mod is not None and (not hasattr(root_mod, {child_attr!r}) or isinstance(getattr(root_mod, {child_attr!r}), types.ModuleType)):"
            )
            lines.append(f"        setattr(root_mod, {child_attr!r}, created[{mod_name!r}])")
        else:
            lines.append(f"    _p = created.get({parent_mod_name!r})")
            lines.append(
                f"    if _p is not None and (not hasattr(_p, {child_attr!r}) or isinstance(getattr(_p, {child_attr!r}), types.ModuleType)):"
            )
            lines.append(f"        setattr(_p, {child_attr!r}, created[{mod_name!r}])")

    lines.append("")
    lines.append("_register_consolidated_submodules()")
    return "\n".join(lines)


def _format_deferred_submodule_sync(
    pkg_name: str,
    modules: dict[str, ModuleInfo],
    canonical_map: dict[tuple[str, str], str],
    resolved_imports: dict[tuple[str, str], ImportTarget],
    deferred_names: set[str],
) -> str:
    """Sync deferred module-level variables (initialized after functions) onto submodule proxies."""
    if not deferred_names:
        return ""
    lines: list[str] = ["def _sync_deferred_submodule_attrs() -> None:"]
    added = False
    for mod_name, minfo in sorted(modules.items()):
        if mod_name == pkg_name:
            continue
        for sym in sorted(minfo.defined_symbols):
            canon = canonical_map.get((mod_name, sym))
            if canon is not None and canon in deferred_names:
                lines.append(f"    if {mod_name!r} in sys.modules and {canon!r} in globals():")
                lines.append(f"        setattr(sys.modules[{mod_name!r}], {sym!r}, globals()[{canon!r}])")
                added = True
        for (m, local_name), tgt in sorted(resolved_imports.items()):
            if m != mod_name or local_name in minfo.defined_symbols:
                continue
            if tgt.symbol is not None:
                canon = canonical_map.get((tgt.module, tgt.symbol))
                if canon is not None and canon in deferred_names:
                    lines.append(f"    if {mod_name!r} in sys.modules and {canon!r} in globals():")
                    lines.append(f"        setattr(sys.modules[{mod_name!r}], {local_name!r}, globals()[{canon!r}])")
                    added = True
    if not added:
        return ""
    lines.append("")
    lines.append("_sync_deferred_submodule_attrs()")
    return "\n".join(lines)


def _detect_callable_accepts_arg(func_node: cst.FunctionDef) -> bool:
    """Return True if *func_node* accepts at least one positional argument."""
    params = func_node.params
    return bool(params.params or params.posonly_params or params.star_arg is not cst.MaybeSentinel.DEFAULT)


def _format_cli_section(
    pkg_name: str,
    modules: dict[str, ModuleInfo],
    canonical_map: dict[tuple[str, str], str],
    entry_points: list[EntryPointSpec],
    function_nodes: dict[str, cst.FunctionDef] | None = None,
    function_accepts_arg: dict[str, bool] | None = None,
) -> tuple[str, bool]:
    """Generate Section 6 (CLI entry points) and whether Section 7 (__main__ guard) is needed."""
    root_mod = modules.get(pkg_name)
    main_mod = modules.get(f"{pkg_name}.__main__")

    fn_arg_map: dict[str, bool] = {}
    if function_accepts_arg is not None:
        fn_arg_map.update(function_accepts_arg)
    if function_nodes is not None:
        for fn_name, fn_node in function_nodes.items():
            fn_arg_map.setdefault(fn_name, _detect_callable_accepts_arg(fn_node))

    # Determine target CLI function(s)
    resolved_entries: list[tuple[str, str, bool]] = []  # (script_name, canon_func_name, takes_arg)
    for ep in entry_points:
        canon = canonical_map.get((ep.module, ep.func_name))
        if canon is not None and canon in fn_arg_map:
            resolved_entries.append((ep.script_name, canon, fn_arg_map[canon]))

    # Fallback: look for standard entry functions in __main__.py or __init__.py
    if not resolved_entries:
        for cand_mod in (main_mod, root_mod):
            if cand_mod is None:
                continue
            for cand_fn in ("main", "cli", "app"):
                canon = canonical_map.get((cand_mod.module_name, cand_fn))
                if canon is not None and canon in fn_arg_map:
                    resolved_entries.append((pkg_name, canon, fn_arg_map[canon]))
                    break
            if resolved_entries:
                break

    # Fallback: check if root __init__.py or __main__.py had `if __name__ == '__main__':` statements
    inline_main_stmts: list[cst.BaseStatement] = []
    if not resolved_entries:
        for cand_mod in (main_mod, root_mod):
            if cand_mod is not None and cand_mod.main_guard_stmts:
                inline_main_stmts = cand_mod.main_guard_stmts
                break

    if not resolved_entries and not inline_main_stmts:
        return "", False

    lines: list[str] = []
    if resolved_entries:
        # Preserve original entry point function if named `main` by calling it via alias
        alias_lines: list[str] = []
        for idx, (sname, canon, takes_arg) in enumerate(resolved_entries):
            if canon == "main":
                lines.append("_consolidated_entry_main = main")
                resolved_entries[idx] = (sname, "_consolidated_entry_main", takes_arg)
        if alias_lines:
            lines.extend(alias_lines)

        lines.append("")
        lines.append("def _invoke_entry(func, takes_arg: bool, argv: list[str] | None) -> int:")
        lines.append("    if takes_arg and argv is not None:")
        lines.append("        rc = func(argv)")
        lines.append("    elif argv is not None:")
        lines.append("        _old_argv = sys.argv")
        lines.append("        sys.argv = [sys.argv[0] if sys.argv else 'cli', *argv]")
        lines.append("        try:")
        lines.append("            rc = func()")
        lines.append("        finally:")
        lines.append("            sys.argv = _old_argv")
        lines.append("    else:")
        lines.append("        rc = func()")
        lines.append("    return 0 if rc is None else int(rc)")
        lines.append("")
        lines.append("def main(argv: list[str] | None = None) -> int:")
        if len(resolved_entries) == 1:
            _, target_fn, takes_arg = resolved_entries[0]
            lines.append(f"    return _invoke_entry({target_fn}, {takes_arg!r}, argv)")
        else:
            lines.append("    import os as _os")
            lines.append("    _prog = _os.path.basename(sys.argv[0]) if sys.argv else ''")
            lines.append("    _dispatch = {")
            for sname, target_fn, takes_arg in resolved_entries:
                lines.append(f"        {sname!r}: ({target_fn}, {takes_arg!r}),")
            lines.append("    }")
            lines.append("    if _prog in _dispatch:")
            lines.append("        fn, takes = _dispatch[_prog]")
            lines.append("        return _invoke_entry(fn, takes, argv)")
            _, default_fn, default_takes = resolved_entries[0]
            lines.append(f"    return _invoke_entry({default_fn}, {default_takes!r}, argv)")
    else:
        lines.append("def main(argv: list[str] | None = None) -> int:")
        lines.append("    _old_argv = sys.argv")
        lines.append("    if argv is not None:")
        lines.append("        sys.argv = [sys.argv[0] if sys.argv else 'cli', *argv]")
        lines.append("    try:")
        for st in inline_main_stmts:
            for ln in _render_cst_stmt(st).splitlines():
                lines.append(f"        {ln}" if ln.strip() else "")
        lines.append("    finally:")
        lines.append("        sys.argv = _old_argv")
        lines.append("    return 0")

    return "\n".join(lines), True


@dataclass(frozen=True)
class _ModuleStub:
    """Lightweight picklable module summary needed by ScopeAwareRenameTransformer."""

    module_name: str
    is_package: bool
    defined_symbols: dict[str, str]


@dataclass
class _ParsedModuleResult:
    """Picklable result returned from parallel Phase 1 module parsing."""

    module_name: str
    file_path: Path
    is_package: bool
    source_code: str
    header_directives: list[str]
    import_bindings: dict[str, ImportTarget]
    star_imports: list[str]
    defined_symbols: dict[str, str]
    explicit_all: list[str] | None
    has_main_guard: bool
    main_guard_codes: list[str]
    future_imports: set[str]
    external_import_codes: list[str]
    type_checking_codes: list[str]
    directive_comments: list[str]
    class_count: int
    function_count: int


@dataclass
class _TransformedModuleResult:
    """Picklable result returned from parallel Phase 3 LibCST transformation."""

    module_name: str
    state_records: list[StatementRecord]
    class_records: list[StatementRecord]
    function_records: list[StatementRecord]
    class_canonical_names: set[str]
    func_canonical_names: set[str]
    func_accepts_arg: dict[str, bool]
    type_checking_codes: list[str] | None
    main_guard_codes: list[str] | None


def _parse_module_worker(
    mod_name: str,
    file_path: Path,
    is_package: bool,
    pkg_name: str,
    strip: bool,
) -> _ParsedModuleResult:
    """Worker entry point for parallel LibCST parsing and optional stripping."""
    minfo = analyze_module(mod_name, file_path, is_package, pkg_name, strip=strip)
    header_dirs: list[str] = []
    for hline in minfo.cst_module.header:
        if hline.comment is not None and (
            hline.comment.value.startswith("#!") or "coding" in hline.comment.value.lower()
        ):
            header_dirs.append(hline.comment.value)

    return _ParsedModuleResult(
        module_name=mod_name,
        file_path=file_path,
        is_package=is_package,
        source_code=minfo.cst_module.code,
        header_directives=header_dirs,
        import_bindings=minfo.import_bindings,
        star_imports=minfo.star_imports,
        defined_symbols=minfo.defined_symbols,
        explicit_all=minfo.explicit_all,
        has_main_guard=minfo.has_main_guard,
        main_guard_codes=[_render_cst_stmt(s) for s in minfo.main_guard_stmts],
        future_imports=minfo.future_imports,
        external_import_codes=[_render_cst_stmt(s) for s in minfo.external_import_stmts],
        type_checking_codes=[_render_cst_stmt(s) for s in minfo.type_checking_stmts],
        directive_comments=minfo.directive_comments,
        class_count=len(minfo.class_stmts),
        function_count=len(minfo.function_stmts),
    )


def _transform_module_worker(
    mod_name: str,
    source_code: str,
    is_package: bool,
    pkg_name: str,
    stubs: dict[str, _ModuleStub],
    canonical_map: dict[tuple[str, str], str],
    resolved_imports: dict[tuple[str, str], ImportTarget],
) -> _TransformedModuleResult:
    """Worker entry point for parallel ScopeAwareRenameTransformer execution."""
    cst_mod = cst.parse_module(source_code)
    proxy_modules: dict[str, ModuleInfo] = {
        m_name: ModuleInfo(
            module_name=stub.module_name,
            file_path=Path(),
            is_package=stub.is_package,
            cst_module=cst.Module([]),
            defined_symbols=stub.defined_symbols,
        )
        for m_name, stub in stubs.items()
    }
    wrapper = MetadataWrapper(cst_mod)
    transformer = ScopeAwareRenameTransformer(
        module_name=mod_name,
        pkg_name=pkg_name,
        modules=proxy_modules,
        canonical_map=canonical_map,
        resolved_imports=resolved_imports,
    )
    rewritten_mod = wrapper.visit(transformer)
    rewritten_info = ModuleInfo(
        module_name=mod_name,
        file_path=Path(),
        is_package=is_package,
        cst_module=rewritten_mod,
    )

    state_records: list[StatementRecord] = []
    class_records: list[StatementRecord] = []
    function_records: list[StatementRecord] = []
    class_canonical_names: set[str] = set()
    func_canonical_names: set[str] = set()
    func_accepts_arg: dict[str, bool] = {}
    type_checking_codes: list[str] | None = None
    main_guard_codes: list[str] | None = None

    for stmt in rewritten_mod.body:
        if isinstance(stmt, cst.ClassDef):
            cname = stmt.name.value
            class_canonical_names.add(cname)
            def_refs: set[str] = set()
            for b in stmt.bases:
                def_refs.update(_collect_refs(b))
            for kw in stmt.keywords:
                def_refs.update(_collect_refs(kw))
            for dec in stmt.decorators:
                def_refs.update(_collect_refs(dec))
            if isinstance(stmt.body, cst.IndentedBlock):
                for cstmt in stmt.body.body:
                    if not isinstance(cstmt, (cst.FunctionDef, cst.ClassDef)):
                        def_refs.update(_collect_refs(cstmt, skip_function_bodies=True))
            class_records.append(
                StatementRecord(
                    module=mod_name,
                    kind="class",
                    node=None,
                    defined_names=[cname],
                    def_time_refs=def_refs,
                    runtime_refs=_collect_refs(stmt, skip_function_bodies=False),
                    rendered_code=_render_cst_stmt(stmt),
                )
            )
        elif isinstance(stmt, cst.FunctionDef):
            fname = stmt.name.value
            func_canonical_names.add(fname)
            func_accepts_arg[fname] = _detect_callable_accepts_arg(stmt)
            def_refs = set()
            for dec in stmt.decorators:
                def_refs.update(_collect_refs(dec))
            for p in (
                list(stmt.params.posonly_params)
                + list(stmt.params.params)
                + ([stmt.params.star_arg] if isinstance(stmt.params.star_arg, cst.Param) else [])
                + list(stmt.params.kwonly_params)
                + ([stmt.params.star_kwarg] if stmt.params.star_kwarg is not None else [])
            ):
                if p.default is not None:
                    def_refs.update(_collect_refs(p.default))
            function_records.append(
                StatementRecord(
                    module=mod_name,
                    kind="function",
                    node=None,
                    defined_names=[fname],
                    def_time_refs=def_refs,
                    runtime_refs=_collect_refs(stmt.body, skip_function_bodies=False),
                    rendered_code=_render_cst_stmt(stmt),
                )
            )
        elif isinstance(stmt, cst.If):
            if _is_main_guard(stmt):
                if mod_name in (pkg_name, f"{pkg_name}.__main__"):
                    main_guard_codes = [_render_cst_stmt(s) for s in stmt.body.body]
                continue
            if _is_type_checking_guard(stmt):
                tc_list: list[cst.BaseStatement] = []
                for inner in stmt.body.body:
                    if isinstance(inner, cst.SimpleStatementLine):
                        kept_tc: list[cst.BaseSmallStatement] = []
                        for small in inner.body:
                            if isinstance(small, (cst.Import, cst.ImportFrom)):
                                ext = _process_import_statement(small, rewritten_info, pkg_name)
                                if ext is not None:
                                    kept_tc.append(ext)
                            else:
                                kept_tc.append(small)
                        if kept_tc:
                            tc_list.append(inner.with_changes(body=kept_tc))
                    else:
                        tc_list.append(inner)
                type_checking_codes = [_render_cst_stmt(s) for s in tc_list]
                continue
            if _contains_only_imports(stmt):
                continue
            state_records.append(
                StatementRecord(
                    module=mod_name,
                    kind="state",
                    node=None,
                    runtime_refs=_collect_refs(stmt),
                    rendered_code=_render_cst_stmt(stmt),
                )
            )
        elif isinstance(stmt, cst.Try):
            if _contains_only_imports(stmt):
                continue
            state_records.append(
                StatementRecord(
                    module=mod_name,
                    kind="state",
                    node=None,
                    runtime_refs=_collect_refs(stmt),
                    rendered_code=_render_cst_stmt(stmt),
                )
            )
        elif isinstance(stmt, cst.SimpleStatementLine):
            last_idx = len(stmt.body) - 1
            for idx, small in enumerate(stmt.body):
                leading = stmt.leading_lines if idx == 0 else ()
                trailing = stmt.trailing_whitespace if idx == last_idx else cst.TrailingWhitespace()
                if isinstance(small, (cst.Import, cst.ImportFrom)):
                    continue
                if isinstance(small, cst.Assign):
                    targets: list[str] = []
                    for t in small.targets:
                        targets.extend(_extract_target_names(t.target))
                    if targets == ["__all__"]:
                        continue
                    s_node = cst.SimpleStatementLine(
                        leading_lines=leading,
                        body=[small],
                        trailing_whitespace=trailing,
                    )
                    state_records.append(
                        StatementRecord(
                            module=mod_name,
                            kind="state",
                            node=None,
                            defined_names=targets,
                            runtime_refs=_collect_refs(small.value),
                            rendered_code=_render_cst_stmt(s_node),
                        )
                    )
                elif isinstance(small, cst.AnnAssign):
                    targets = _extract_target_names(small.target)
                    if targets == ["__all__"]:
                        continue
                    s_node = cst.SimpleStatementLine(
                        leading_lines=leading,
                        body=[small],
                        trailing_whitespace=trailing,
                    )
                    refs = _collect_refs(small.value) if small.value is not None else set()
                    state_records.append(
                        StatementRecord(
                            module=mod_name,
                            kind="state",
                            node=None,
                            defined_names=targets,
                            runtime_refs=refs,
                            rendered_code=_render_cst_stmt(s_node),
                        )
                    )
                elif isinstance(small, cst.AugAssign):
                    targets = _extract_target_names(small.target)
                    s_node = cst.SimpleStatementLine(
                        leading_lines=leading,
                        body=[small],
                        trailing_whitespace=trailing,
                    )
                    state_records.append(
                        StatementRecord(
                            module=mod_name,
                            kind="state",
                            node=None,
                            defined_names=targets,
                            runtime_refs=_collect_refs(small.value) | set(targets),
                            rendered_code=_render_cst_stmt(s_node),
                        )
                    )
                elif isinstance(small, cst.Expr):
                    if _is_string_literal_expr(small.value):
                        continue
                    s_node = cst.SimpleStatementLine(
                        leading_lines=leading,
                        body=[small],
                        trailing_whitespace=trailing,
                    )
                    state_records.append(
                        StatementRecord(
                            module=mod_name,
                            kind="state",
                            node=None,
                            runtime_refs=_collect_refs(small),
                            rendered_code=_render_cst_stmt(s_node),
                        )
                    )
                elif isinstance(small, cst.Pass):
                    continue
                else:
                    s_node = cst.SimpleStatementLine(
                        leading_lines=leading,
                        body=[small],
                        trailing_whitespace=trailing,
                    )
                    state_records.append(
                        StatementRecord(
                            module=mod_name,
                            kind="state",
                            node=None,
                            runtime_refs=_collect_refs(small),
                            rendered_code=_render_cst_stmt(s_node),
                        )
                    )
        else:
            state_records.append(
                StatementRecord(
                    module=mod_name,
                    kind="state",
                    node=None,
                    runtime_refs=_collect_refs(stmt),
                    rendered_code=_render_cst_stmt(stmt),
                )
            )

    return _TransformedModuleResult(
        module_name=mod_name,
        state_records=state_records,
        class_records=class_records,
        function_records=function_records,
        class_canonical_names=class_canonical_names,
        func_canonical_names=func_canonical_names,
        func_accepts_arg=func_accepts_arg,
        type_checking_codes=type_checking_codes,
        main_guard_codes=main_guard_codes,
    )


def _create_worker_executor(max_workers: int = FIXED_WORKERS) -> Executor:
    """Create a fixed-size worker pool, falling back to threads if process pools are restricted."""
    try:
        return ProcessPoolExecutor(max_workers=max_workers)
    except (OSError, PermissionError):
        return ThreadPoolExecutor(max_workers=max_workers)


def _parse_stmt_codes(codes: list[str]) -> list[cst.BaseStatement]:
    """Parse small statement code blocks back into LibCST statement nodes."""
    if not codes:
        return []
    return list(cst.parse_module("\n".join(codes)).body)


def consolidate_package(
    input_path: Path,
    output_path: Path | None = None,
    pkg_name_override: str | None = None,
    strip: bool = False,
    exclude_tests: bool = False,
    verbose: bool = False,
    progress_cb: Callable[[dict[str, object]], None] | None = None,
) -> tuple[Path, dict[str, object]]:
    """Consolidate a Python package into a single self-contained file and write atomically."""

    def emit(stage: str, level: str, message: str, **extra: object) -> None:
        if progress_cb is not None:
            payload: dict[str, object] = {
                "stage": stage,
                "level": level,
                "message": message,
            }
            payload.update(extra)
            progress_cb(payload)

    emit("scan", "info", f"Resolving package root for {input_path}")
    package_dir, detected_name = resolve_package_root(input_path)
    pkg_name = pkg_name_override or detected_name
    final_output = output_path or Path(f"{pkg_name}.py")

    entry_points = discover_entry_points(package_dir, pkg_name)
    for ep in entry_points:
        emit(
            "scan",
            "info",
            f"Discovered console script '{ep.script_name}' -> {ep.module}:{ep.func_name}",
        )

    py_files, data_files = discover_files(package_dir, pkg_name, exclude_tests=exclude_tests)
    if not py_files:
        raise ValueError(f"No Python files found in {package_dir}")

    emit(
        "scan",
        "info",
        f"Scanned {len(py_files)} Python modules and {len(data_files)} package data files in '{pkg_name}'",
        meta={
            "py_count": len(py_files),
            "data_count": len(data_files),
            "workers": FIXED_WORKERS,
        },
    )
    for mod_name, file_path, is_pkg in py_files:
        rel_p = file_path.relative_to(package_dir).as_posix()
        emit("scan", "info", f"Queued module {mod_name} ({rel_p})", detail=rel_p)
    for df in data_files:
        kind_label = "binary/b64" if df.is_binary else "utf-8 text"
        emit(
            "scan",
            "info",
            f"Queued data file {df.rel_path} ({kind_label})",
            detail=df.rel_path,
        )

    with _create_worker_executor(FIXED_WORKERS) as executor:
        # Phase 1: Parse (and optionally --strip) all Python modules in parallel (4 workers)
        strip_msg = " + stripping comments/docstrings" if strip else ""
        emit(
            "parse",
            "info",
            f"Parsing {len(py_files)} modules via LibCST{strip_msg} ({FIXED_WORKERS} workers)",
        )
        parsed_by_mod: dict[str, _ParsedModuleResult] = {}
        parse_futures = {
            executor.submit(_parse_module_worker, mod_name, file_path, is_pkg, pkg_name, strip): mod_name
            for mod_name, file_path, is_pkg in py_files
        }
        for fut in as_completed(parse_futures):
            res = fut.result()
            parsed_by_mod[res.module_name] = res
            emit(
                "parse",
                "info",
                f"Parsed {res.module_name}: {res.class_count} classes, {res.function_count} functions, {len(res.defined_symbols)} symbols",
            )

        # Reconstruct ModuleInfo dict in deterministic discovery order
        modules: dict[str, ModuleInfo] = {}
        for mod_name, _, _ in py_files:
            pres = parsed_by_mod[mod_name]
            modules[mod_name] = ModuleInfo(
                module_name=pres.module_name,
                file_path=pres.file_path,
                is_package=pres.is_package,
                cst_module=cst.Module([]),
                import_bindings=pres.import_bindings,
                star_imports=pres.star_imports,
                defined_symbols=pres.defined_symbols,
                explicit_all=pres.explicit_all,
                has_main_guard=pres.has_main_guard,
                main_guard_stmts=_parse_stmt_codes(pres.main_guard_codes),
                future_imports=pres.future_imports,
                external_import_stmts=_parse_stmt_codes(pres.external_import_codes),
                type_checking_stmts=_parse_stmt_codes(pres.type_checking_codes),
                directive_comments=pres.directive_comments,
            )

        # Phase 2: Build deterministic collision-free symbol table
        emit(
            "resolve",
            "info",
            "Building cross-module symbol table and resolving internal imports",
        )
        canonical_map, resolved_imports = build_symbol_table(modules, pkg_name)
        collisions = [(m, orig, canon) for (m, orig), canon in sorted(canonical_map.items()) if orig != canon]
        if collisions:
            emit(
                "resolve",
                "warn" if len(collisions) > 0 else "info",
                f"Detected {len(collisions)} colliding module-level symbol definitions",
            )
            for mod_name, orig, canon in collisions:
                emit("resolve", "info", f"Renamed {mod_name}.{orig} -> {canon}")
        else:
            emit("resolve", "info", "No cross-module symbol collisions detected")

        # Phase 3: Rewrite all modules in parallel (4 workers) with scope-aware LibCST transformer
        emit(
            "transform",
            "info",
            f"Running scope-aware LibCST transformer ({FIXED_WORKERS} workers, ScopeProvider LEGB resolution)",
        )
        stubs: dict[str, _ModuleStub] = {
            m_name: _ModuleStub(
                module_name=minfo.module_name,
                is_package=minfo.is_package,
                defined_symbols=minfo.defined_symbols,
            )
            for m_name, minfo in modules.items()
        }
        transformed_by_mod: dict[str, _TransformedModuleResult] = {}
        transform_futures = {
            executor.submit(
                _transform_module_worker,
                mod_name,
                parsed_by_mod[mod_name].source_code,
                modules[mod_name].is_package,
                pkg_name,
                stubs,
                canonical_map,
                resolved_imports,
            ): mod_name
            for mod_name, _, _ in py_files
        }
        for fut in as_completed(transform_futures):
            tres = fut.result()
            transformed_by_mod[tres.module_name] = tres
            emit("transform", "info", f"Transformed references in {tres.module_name}")

    # Assemble transformed records in deterministic discovery order
    all_state_records: list[StatementRecord] = []
    all_class_records: list[StatementRecord] = []
    all_function_records: list[StatementRecord] = []
    class_canonical_names: set[str] = set()
    func_canonical_names: set[str] = set()
    func_accepts_arg_map: dict[str, bool] = {}

    for mod_name, _, _ in py_files:
        tres = transformed_by_mod[mod_name]
        all_state_records.extend(tres.state_records)
        all_class_records.extend(tres.class_records)
        all_function_records.extend(tres.function_records)
        class_canonical_names.update(tres.class_canonical_names)
        func_canonical_names.update(tres.func_canonical_names)
        func_accepts_arg_map.update(tres.func_accepts_arg)
        if tres.type_checking_codes is not None:
            modules[mod_name].type_checking_stmts = _parse_stmt_codes(tres.type_checking_codes)
        if tres.main_guard_codes is not None:
            modules[mod_name].main_guard_stmts = _parse_stmt_codes(tres.main_guard_codes)

    # Phase 4: Partition module-level state into:
    # - immediate (Section 2): no Class or Function dependencies
    # - class_only_deferred (Section 4, topo-sorted with Classes): depends on Classes, not Functions
    # - func_deferred (after Section 5 Functions & submodule registry): depends on Functions
    class_or_func_names = class_canonical_names | func_canonical_names
    deferred_vars: set[str] = set()
    changed = True
    while changed:
        changed = False
        for rec in all_state_records:
            if any(d in deferred_vars for d in rec.defined_names):
                continue
            if ((rec.runtime_refs & class_or_func_names) or (rec.runtime_refs & deferred_vars)) and rec.defined_names:
                deferred_vars.update(rec.defined_names)
                changed = True

    immediate_state: list[StatementRecord] = []
    deferred_state: list[StatementRecord] = []
    for rec in all_state_records:
        is_def = bool(
            (rec.runtime_refs & class_or_func_names)
            or (rec.runtime_refs & deferred_vars)
            or any(d in deferred_vars for d in rec.defined_names)
        )
        if is_def:
            deferred_state.append(rec)
        else:
            immediate_state.append(rec)

    # Propagate which deferred variables transitively depend on any Function
    func_dependent_vars: set[str] = set()
    changed = True
    while changed:
        changed = False
        for rec in deferred_state:
            if (rec.runtime_refs & func_canonical_names) or (rec.runtime_refs & func_dependent_vars):
                for d in rec.defined_names:
                    if d not in func_dependent_vars:
                        func_dependent_vars.add(d)
                        changed = True

    class_deferred_state: list[StatementRecord] = []
    func_deferred_state: list[StatementRecord] = []
    for rec in deferred_state:
        is_func_dep = bool(
            (rec.runtime_refs & func_canonical_names)
            or (rec.runtime_refs & func_dependent_vars)
            or any(d in func_dependent_vars for d in rec.defined_names)
        )
        if is_func_dep:
            func_deferred_state.append(rec)
        else:
            class_deferred_state.append(rec)

    immediate_state = _sort_state_records_respecting_intra_module_order(immediate_state)
    func_deferred_state = _sort_state_records_respecting_intra_module_order(func_deferred_state)
    sorted_classes_and_state = _toposort_records(
        all_class_records + class_deferred_state,
        lambda r: r.def_time_refs if r.kind == "class" else r.runtime_refs,
    )
    sorted_classes = [r for r in sorted_classes_and_state if r.kind == "class"]

    # Expand function definition-time dependencies transitively across classes and helper functions
    # (e.g. `def beautify(opts=default_options()):` -> `default_options` -> `BeautifierOptions` -> `_mergeOpts`)
    class_runtime_map: dict[str, set[str]] = {
        rec.defined_names[0]: rec.runtime_refs for rec in all_class_records if rec.defined_names
    }
    func_runtime_map: dict[str, set[str]] = {
        rec.defined_names[0]: rec.runtime_refs for rec in all_function_records if rec.defined_names
    }

    def _expanded_func_def_deps(rec: StatementRecord) -> set[str]:
        fn_name = rec.defined_names[0] if rec.defined_names else ""
        needed_funcs: set[str] = set()
        visited_symbols: set[str] = set()
        queue = list(rec.def_time_refs)
        while queue:
            sym = queue.pop()
            if sym in visited_symbols or sym == fn_name:
                continue
            visited_symbols.add(sym)
            if sym in func_runtime_map:
                needed_funcs.add(sym)
                queue.extend(func_runtime_map[sym])
            elif sym in class_runtime_map:
                queue.extend(class_runtime_map[sym])
        return needed_funcs

    sorted_functions = _toposort_records(all_function_records, _expanded_func_def_deps)

    # Phase 5: Assemble the 7-section consolidated Python output
    has_tc = any(bool(m.type_checking_stmts) for m in modules.values())
    sections: list[str] = []

    # Preserve root shebang / encoding directive header if present
    header_directives: list[str] = []
    root_parsed = parsed_by_mod.get(pkg_name)
    if root_parsed is not None:
        header_directives.extend(root_parsed.header_directives)

    # Preserve non-header module-level directive comments (from headers or merged/internal imports)
    seen_dirs: set[str] = set()
    extra_directives: list[str] = []
    for minfo in modules.values():
        for dc in minfo.directive_comments:
            if dc not in seen_dirs:
                seen_dirs.add(dc)
                extra_directives.append(dc)

    # 1. Imports
    imports_code = _format_imports_section(
        modules=modules,
        needs_base64=True,
        needs_sys=True,
        needs_types=True,
        needs_pkgutil=False,
        has_type_checking=has_tc,
    )
    header_prefix = ("\n".join(header_directives) + "\n") if header_directives else ""
    dir_block = ("\n" + "\n".join(extra_directives)) if extra_directives else ""
    sections.append(
        header_prefix + "# ============================================================================\n"
        "# 1. Imports\n"
        "# ============================================================================\n" + imports_code + dir_block
    )

    # 2. Constants & module state
    state_lines: list[str] = [
        "# ============================================================================",
        "# 2. Constants & module state",
        "# ============================================================================",
    ]
    for rec in immediate_state:
        state_lines.append(_render_record(rec))
    # Predeclare deferred module variables and `global` variables cleanly
    predeclared: set[str] = set()
    for rec in deferred_state:
        for d in rec.defined_names:
            if d not in predeclared:
                state_lines.append(f"{d} = None  # deferred initialization")
                predeclared.add(d)
    for mod_name, minfo in sorted(modules.items()):
        for sym, kind in sorted(minfo.defined_symbols.items()):
            if kind == "global":
                canon = canonical_map[(mod_name, sym)]
                if canon not in predeclared:
                    state_lines.append(f"{canon} = None  # module global")
                    predeclared.add(canon)
    sections.append("\n".join(state_lines))

    # 3. Package data files
    sections.append(
        "# ============================================================================\n"
        "# 3. Package data files\n"
        "# ============================================================================\n"
        + _format_data_section(data_files)
    )

    # 4. Classes (and class-instantiated module constants required at class-body time)
    class_lines: list[str] = [
        "# ============================================================================",
        "# 4. Classes",
        "# ============================================================================",
    ]
    for rec in sorted_classes_and_state:
        class_lines.append(_render_record(rec))
        class_lines.append("")
    sections.append("\n".join(class_lines))

    # 5. Functions (+ submodule aliases & function-dependent deferred state)
    func_lines: list[str] = [
        "# ============================================================================",
        "# 5. Functions",
        "# ============================================================================",
    ]
    for rec in sorted_functions:
        func_lines.append(_render_record(rec))
        func_lines.append("")

    func_lines.append("# --- Drop-in package metadata & submodule aliases ---")
    func_lines.append(_format_submodule_registry(pkg_name, modules, canonical_map, resolved_imports))
    func_lines.append("")

    if func_deferred_state:
        func_lines.append("# --- Deferred module-level state ---")
        for rec in func_deferred_state:
            func_lines.append(_render_record(rec))
        sync_code = _format_deferred_submodule_sync(
            pkg_name,
            modules,
            canonical_map,
            resolved_imports,
            func_dependent_vars,
        )
        if sync_code:
            func_lines.append("")
            func_lines.append(sync_code)
        func_lines.append("")

    sections.append("\n".join(func_lines))

    # 6. CLI entry points
    cli_code, has_cli = _format_cli_section(
        pkg_name=pkg_name,
        modules=modules,
        canonical_map=canonical_map,
        entry_points=entry_points,
        function_accepts_arg=func_accepts_arg_map,
    )
    if cli_code:
        sections.append(
            "# ============================================================================\n"
            "# 6. CLI entry points\n"
            "# ============================================================================\n" + cli_code
        )

    # 7. __main__ guard
    if has_cli:
        sections.append(
            "# ============================================================================\n"
            "# 7. __main__ guard\n"
            "# ============================================================================\n"
            'if __name__ == "__main__":\n'
            "    raise SystemExit(main())"
        )

    emit(
        "generate",
        "info",
        f"Assembling 7-section layout ({len(sorted_classes)} classes, {len(sorted_functions)} functions, {len(data_files)} data files)",
    )
    final_source = "\n\n".join(sections).rstrip() + "\n"

    # Validate syntax via LibCST parse + Python compile() before writing
    emit(
        "generate",
        "info",
        "Validating generated module with cst.parse_module() and compile()",
    )
    cst.parse_module(final_source)
    compile(final_source, str(final_output), "exec")

    # Atomic write via temp file + os.replace with cleanup on failure
    emit(
        "generate",
        "info",
        f"Writing output atomically via temp file + os.replace -> {final_output}",
    )
    final_output.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=final_output.parent,
            prefix=f".{final_output.stem}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(final_source)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, final_output)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    collisions_resolved = sum(1 for (_, orig), canon in canonical_map.items() if orig != canon)
    stats: dict[str, object] = {
        "package_name": pkg_name,
        "modules": len(modules),
        "data_files": len(data_files),
        "classes": len(sorted_classes),
        "functions": len(sorted_functions),
        "collisions_resolved": collisions_resolved,
        "has_cli": has_cli,
        "workers": FIXED_WORKERS,
        "output_bytes": len(final_source.encode("utf-8")),
    }
    emit(
        "generate",
        "ok",
        f"Generated {final_output.name} ({stats['output_bytes']} bytes, {collisions_resolved} collisions resolved)",
        meta=stats,
    )
    if verbose:
        print(f"Consolidated '{pkg_name}' -> {final_output} ({stats})")
    return final_output, stats


def verify_consolidated_file(
    output_path: Path,
    pkg_name: str,
    expected_data_paths: list[str] | None = None,
) -> None:
    """Verify compiled syntax and isolated runtime import of the consolidated file."""
    source = output_path.read_text(encoding="utf-8")
    # 1. Syntax & bytecode compilation check
    compile(source, str(output_path), "exec")

    # 2. Isolated runtime import verification in a clean subprocess without original package dir
    probe_script = """
import importlib.util
import sys

output_path, pkg_name = sys.argv[1], sys.argv[2]
expected_data = sys.argv[3:]

# Isolate sys.path so only stdlib/site-packages exist, and load explicitly by file path
spec = importlib.util.spec_from_file_location(pkg_name, output_path)
assert spec is not None and spec.loader is not None, "Failed to create module spec"
mod = importlib.util.module_from_spec(spec)
sys.modules[pkg_name] = mod
spec.loader.exec_module(mod)

assert hasattr(mod, "__all__"), "Missing __all__"
for sym in mod.__all__:
    assert hasattr(mod, sym), f"Missing exported symbol from __all__: {sym}"

assert hasattr(mod, "__path__"), "Missing __path__"
assert callable(getattr(mod, "read_data_bytes", None)), "Missing read_data_bytes"
assert callable(getattr(mod, "read_data_text", None)), "Missing read_data_text"

for rel in expected_data:
    b = mod.read_data_bytes(rel)
    assert isinstance(b, bytes), f"read_data_bytes failed for {rel}"

print(f"VERIFIED_OK module={pkg_name} exports={len(mod.__all__)} submodules={sum(1 for k in sys.modules if k == pkg_name or k.startswith(pkg_name + '.'))}")
"""
    with tempfile.TemporaryDirectory() as isolated_cwd:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                probe_script,
                str(output_path.resolve()),
                pkg_name,
                *(expected_data_paths or []),
            ],
            cwd=isolated_cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Isolated runtime verification failed for {output_path}:\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="consolidate",
        description="Consolidate a Python package directory into a single self-contained .py file using LibCST.",
    )
    parser.add_argument(
        "package_dir",
        type=Path,
        help="Path to the Python package directory (containing __init__.py) or project root.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .py file path (defaults to <pkg_name>.py in current directory).",
    )
    parser.add_argument(
        "-n",
        "--name",
        type=str,
        default=None,
        help="Override the detected package name.",
    )
    parser.add_argument(
        "--strip",
        action="store_true",
        help="Strip non-directive comments and module/class/function docstrings via LibCST before consolidation.",
    )
    parser.add_argument(
        "--exclude-tests",
        action="store_true",
        help="Exclude test directories/files inside the package directory.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip post-build compile and isolated runtime import verification.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print detailed consolidation summary.",
    )
    parser.add_argument(
        "--json-events",
        action="store_true",
        help="Emit line-delimited JSON progress events to stdout.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    started = time.perf_counter()

    def _emit_json(event: dict[str, object]) -> None:
        event_out = dict(event)
        event_out["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        print(json.dumps(event_out), flush=True)

    cb = _emit_json if args.json_events else None

    try:
        package_dir, detected_name = resolve_package_root(args.package_dir)
        pkg_name = args.name or detected_name
        _, data_files = discover_files(package_dir, pkg_name, exclude_tests=args.exclude_tests)

        out_path, stats = consolidate_package(
            input_path=args.package_dir,
            output_path=args.output,
            pkg_name_override=args.name,
            strip=args.strip,
            exclude_tests=args.exclude_tests,
            verbose=args.verbose and not args.json_events,
            progress_cb=cb,
        )

        if not args.no_verify:
            if cb is not None:
                cb({
                    "stage": "verify",
                    "level": "info",
                    "message": f"Running compile() and isolated runtime import check on {out_path.name}",
                })
            verify_consolidated_file(
                output_path=out_path,
                pkg_name=pkg_name,
                expected_data_paths=[d.rel_path for d in data_files[:5]],
            )
            if cb is not None:
                cb({
                    "stage": "verify",
                    "level": "ok",
                    "message": f"Isolated runtime import and __all__ verification passed for {out_path.name}",
                })
            elif args.verbose:
                print(f"Verification passed for {out_path}")

        if cb is not None:
            cb({
                "stage": "complete",
                "level": "ok",
                "message": f"Consolidation finished: {out_path}",
                "meta": {**stats, "output_path": str(out_path)},
            })
        elif not args.verbose:
            print(
                f"Wrote {out_path} ({stats['modules']} modules, {stats['data_files']} data files, "
                f"{stats['collisions_resolved']} collisions resolved)"
            )
        return 0
    except Exception as exc:
        if cb is not None:
            cb({
                "stage": "error",
                "level": "error",
                "message": f"{type(exc).__name__}: {exc}",
            })
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
