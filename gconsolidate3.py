#!/data/data/com.termux/files/usr/bin/env python
"""Consolidate a small-to-medium Python package into a single importable file."""

from __future__ import annotations
import argparse
import ast
import base64
from collections import defaultdict
from dataclasses import dataclass, field
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import tomllib
from typing import Any

import libcst as cst


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
    ".bin",
    ".dat",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".so",
    ".dylib",
    ".dll",
    ".pkl",
    ".pickle",
    ".npz",
    ".npy",
    ".zip",
    ".gz",
    ".tar",
    ".bz2",
    ".xz",
    ".7z",
    ".pdf",
    ".wasm",
})

IGNORED_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "build",
    "dist",
})

STDLIB_MODULES: frozenset[str] = getattr(sys, "stdlib_module_names", frozenset())

# Patterns for Python language directives, linters, type checkers, and encoding/shebang headers
_DIRECTIVE_COMMENT_RE = re.compile(
    r"^\s*#\s*(?:"
    r"type\s*:|"
    r"type\s*:\s*ignore\b|"
    r"noqa\b|"
    r"pylint\s*:|"
    r"mypy\s*:|"
    r"pyright\s*:|"
    r"ruff\s*:|"
    r"flake8\s*:|"
    r"fmt\s*:|"
    r"isort\s*:|"
    r"black\s*:|"
    r"yapf\s*:|"
    r"bandit\s*:|"
    r"nosec\b|"
    r"pragma\s*:\s*no\s*cover\b|"
    r"coding\s*[:=]|"
    r"-\*-\s*coding\s*:"
    r")",
    re.IGNORECASE,
)


def _is_preserved_comment(comment_value: str) -> bool:
    stripped = comment_value.strip()
    if not stripped.startswith("#"):
        return True
    if stripped.startswith("#!"):
        return True
    return bool(_DIRECTIVE_COMMENT_RE.match(stripped))


class CommentAndDocstringStripper(cst.CSTTransformer):
    """Strip non-directive comments and docstrings using LibCST."""

    def leave_Comment(self, original_node: cst.Comment, updated_node: cst.Comment) -> cst.Comment | cst.RemovalSentinel:
        if _is_preserved_comment(updated_node.value):
            return updated_node
        return cst.RemoveFromParent()

    def leave_EmptyLine(
        self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine
    ) -> cst.EmptyLine | cst.RemovalSentinel:
        if updated_node.comment is not None and not _is_preserved_comment(updated_node.comment.value):
            if updated_node.whitespace.value.strip() == "":
                return cst.RemoveFromParent()
            return updated_node.with_changes(comment=None)
        return updated_node

    def leave_TrailingWhitespace(
        self,
        original_node: cst.TrailingWhitespace,
        updated_node: cst.TrailingWhitespace,
    ) -> cst.TrailingWhitespace:
        if updated_node.comment is not None and not _is_preserved_comment(updated_node.comment.value):
            return updated_node.with_changes(comment=None)
        return updated_node

    @staticmethod
    def _is_docstring_stmt(stmt: cst.BaseStatement) -> bool:
        if isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) == 1:
            small = stmt.body[0]
            if isinstance(small, cst.Expr) and isinstance(small.value, (cst.SimpleString, cst.ConcatenatedString)):
                return True
        return False

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        body = list(updated_node.body)
        if body and self._is_docstring_stmt(body[0]):
            body = body[1:]
        return updated_node.with_changes(body=body)

    def leave_IndentedBlock(
        self, original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
    ) -> cst.IndentedBlock:
        body = list(updated_node.body)
        if body and self._is_docstring_stmt(body[0]):
            body = body[1:]
            if not body:
                body = [cst.SimpleStatementLine(body=[cst.Pass()])]
        return updated_node.with_changes(body=body)


def strip_source_with_libcst(source: str) -> str:
    """Parse source with LibCST, strip comments/docstrings, and return formatted code."""
    tree = cst.parse_module(source)
    stripped = tree.visit(CommentAndDocstringStripper())
    return stripped.code


@dataclass(slots=True)
class ImportSpec:
    """Normalized representation of an import statement."""

    module: str | None
    names: tuple[tuple[str, str | None], ...]
    level: int = 0
    is_future: bool = False
    is_type_checking: bool = False
    raw_code: str = ""


@dataclass(slots=True)
class TopLevelItem:
    """A top-level symbol or statement extracted from a package module."""

    kind: str  # "constant", "class", "function", "other"
    module_name: str
    mod_short: str
    orig_name: str | None
    final_name: str | None
    code: str
    is_public: bool = True


@dataclass(slots=True)
class DataFileSpec:
    """Package resource / data file representation."""

    rel_key: str
    is_binary: bool
    payload: str


@dataclass(slots=True)
class CliEntryPoint:
    """CLI console script entry point."""

    script_name: str
    target_module: str
    target_attr: str


@dataclass(slots=True)
class PackageBundle:
    """Collected state for a package before emitting the single file."""

    pkg_name: str
    pkg_dir: Path
    project_root: Path
    modules: list[tuple[str, Path, cst.Module]] = field(default_factory=list)
    future_imports: list[str] = field(default_factory=list)
    regular_imports: list[ImportSpec] = field(default_factory=list)
    constants: list[TopLevelItem] = field(default_factory=list)
    classes: list[TopLevelItem] = field(default_factory=list)
    functions: list[TopLevelItem] = field(default_factory=list)
    other_top_level: list[TopLevelItem] = field(default_factory=list)
    data_files: list[DataFileSpec] = field(default_factory=list)
    cli_entrypoints: list[CliEntryPoint] = field(default_factory=list)
    explicit_all: list[str] | None = None
    symbol_map: dict[tuple[str, str], str] = field(default_factory=dict)


def _sanitize_ident(text: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", text)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def _is_ignored_path(path: Path, pkg_root: Path) -> bool:
    rel_parts = path.relative_to(pkg_root).parts
    for part in rel_parts:
        if part in IGNORED_DIRS or part.endswith(".egg-info"):
            return True
        if part.startswith(".") and part not in {".gitkeep"}:
            return True
    return False


def _detect_project_root(pkg_dir: Path) -> Path:
    for candidate in (pkg_dir.parent, pkg_dir.parent.parent):
        if (candidate / "pyproject.toml").is_file() or (candidate / "setup.py").is_file():
            return candidate
    return pkg_dir.parent


def _parse_cli_entrypoints(project_root: Path, pkg_name: str) -> list[CliEntryPoint]:
    entrypoints: list[CliEntryPoint] = []
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        scripts = data.get("project", {}).get("scripts", {})
        if isinstance(scripts, dict):
            for script_name, target in scripts.items():
                if isinstance(target, str) and ":" in target:
                    mod_part, attr_part = target.split(":", 1)
                    mod_part = mod_part.strip()
                    attr_part = attr_part.strip().split(".", 1)[0]
                    if mod_part == pkg_name or mod_part.startswith(f"{pkg_name}."):
                        entrypoints.append(
                            CliEntryPoint(
                                script_name=str(script_name),
                                target_module=mod_part,
                                target_attr=attr_part,
                            )
                        )

    setup_py = project_root / "setup.py"
    if not entrypoints and setup_py.is_file():
        text = setup_py.read_text(encoding="utf-8", errors="replace")
        pattern = re.compile(r"""['"]([a-zA-Z0-9_-]+)\s*=\s*([a-zA-Z0-9_.-]+:[a-zA-Z0-9_.-]+)['"]""")
        for script_name, target in pattern.findall(text):
            mod_part, attr_part = target.split(":", 1)
            if mod_part == pkg_name or mod_part.startswith(f"{pkg_name}."):
                entrypoints.append(
                    CliEntryPoint(
                        script_name=script_name,
                        target_module=mod_part,
                        target_attr=attr_part.split(".", 1)[0],
                    )
                )

    return entrypoints


def _discover_files(pkg_dir: Path) -> tuple[list[tuple[str, Path]], list[Path]]:
    py_files: list[tuple[str, Path]] = []
    data_files: list[Path] = []

    all_files = sorted(pkg_dir.rglob("*"), key=lambda p: (len(p.parts), str(p)))
    for path in all_files:
        if not path.is_file() or path.is_symlink():
            continue
        if _is_ignored_path(path, pkg_dir):
            continue

        rel = path.relative_to(pkg_dir)
        if path.suffix == ".py":
            if rel.name == "__init__.py":
                mod_name = pkg_dir.name if len(rel.parts) == 1 else f"{pkg_dir.name}." + ".".join(rel.parts[:-1])
            else:
                parts = (pkg_dir.name, *rel.with_suffix("").parts)
                mod_name = ".".join(parts)
            py_files.append((mod_name, path))
        else:
            data_files.append(path)

    # Order __init__.py files before sibling submodules at each level
    def sort_key(item: tuple[str, Path]) -> tuple[int, int, str]:
        mod_name, _ = item
        depth = len(mod_name.split("."))
        is_init = 0 if mod_name == pkg_dir.name or mod_name.endswith(".__init__") else 1
        return (depth, is_init, mod_name)

    py_files.sort(key=sort_key)
    return py_files, data_files


def _load_data_files(pkg_dir: Path, paths: list[Path]) -> list[DataFileSpec]:
    specs: list[DataFileSpec] = []
    for path in paths:
        rel_key = path.relative_to(pkg_dir).as_posix()
        raw_bytes = path.read_bytes()
        is_binary = path.suffix.lower() in BINARY_EXTENSIONS or b"\x00" in raw_bytes
        if not is_binary:
            try:
                text = raw_bytes.decode("utf-8")
                specs.append(DataFileSpec(rel_key=rel_key, is_binary=False, payload=text))
                continue
            except UnicodeDecodeError:
                is_binary = True

        b64 = base64.b64encode(raw_bytes).decode("ascii")
        specs.append(DataFileSpec(rel_key=rel_key, is_binary=True, payload=b64))
    return specs


def _is_type_checking_if(node: cst.If) -> bool:
    test_code = cst.Module([]).code_for_node(node.test).strip()
    return test_code in {"TYPE_CHECKING", "typing.TYPE_CHECKING", "t.TYPE_CHECKING"}


def _extract_import_specs(stmt: cst.BaseStatement, in_type_checking: bool = False) -> list[ImportSpec]:
    specs: list[ImportSpec] = []
    if isinstance(stmt, cst.SimpleStatementLine):
        for small in stmt.body:
            if isinstance(small, cst.Import):
                names: list[tuple[str, str | None]] = []
                for alias in small.names:
                    imported = cst.Module([]).code_for_node(alias.name).strip()
                    asname = cst.Module([]).code_for_node(alias.asname.name).strip() if alias.asname else None
                    names.append((imported, asname))
                raw = cst.Module([]).code_for_node(small).strip()
                specs.append(
                    ImportSpec(
                        module=None,
                        names=tuple(names),
                        level=0,
                        is_future=False,
                        is_type_checking=in_type_checking,
                        raw_code=raw,
                    )
                )
            elif isinstance(small, cst.ImportFrom):
                level = len(small.relative)
                mod_str = cst.Module([]).code_for_node(small.module).strip() if small.module else None
                is_future = mod_str == "__future__" and level == 0
                if isinstance(small.names, cst.ImportStar):
                    names_tuple = (("*", None),)
                else:
                    parsed_names: list[tuple[str, str | None]] = []
                    for alias in small.names:
                        imported = cst.Module([]).code_for_node(alias.name).strip()
                        asname = cst.Module([]).code_for_node(alias.asname.name).strip() if alias.asname else None
                        parsed_names.append((imported, asname))
                    names_tuple = tuple(parsed_names)
                raw = cst.Module([]).code_for_node(small).strip()
                specs.append(
                    ImportSpec(
                        module=mod_str,
                        names=names_tuple,
                        level=level,
                        is_future=is_future,
                        is_type_checking=in_type_checking,
                        raw_code=raw,
                    )
                )
    return specs


def _is_internal_import(spec: ImportSpec, current_mod: str, pkg_name: str, known_modules: set[str]) -> bool:
    if spec.is_future:
        return False
    if spec.level > 0:
        return True
    if spec.module is not None:
        if spec.module == pkg_name or spec.module.startswith(f"{pkg_name}."):
            return True
        if spec.module in known_modules:
            return True
    else:
        for imported, _ in spec.names:
            top = imported.split(".", 1)[0]
            if top == pkg_name or imported in known_modules or f"{pkg_name}.{top}" in known_modules:
                return True
    return False


class ScopeAwareNameRewriter(cst.CSTTransformer):
    """Safely renames top-level symbols in a single module without clobbering local scopes."""

    def __init__(self, rename_map: dict[str, str]) -> None:
        self.rename_map = rename_map
        self.local_scopes: list[set[str]] = []

    def _is_shadowed(self, name: str) -> bool:
        for scope in reversed(self.local_scopes):
            if name in scope:
                return True
        return False

    def _collect_target_names(self, target: cst.BaseAssignTargetExpression) -> set[str]:
        names: set[str] = set()
        if isinstance(target, cst.Name):
            names.add(target.value)
        elif isinstance(target, (cst.Tuple, cst.List)):
            for el in target.elements:
                if isinstance(el, cst.Element) and isinstance(el.value, cst.BaseAssignTargetExpression):
                    names.update(self._collect_target_names(el.value))
        return names

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        scope: set[str] = set()
        for param in (
            *node.params.params,
            *node.params.posonly_params,
            *node.params.kwonly_params,
        ):
            scope.add(param.name.value)
        if isinstance(node.params.star_arg, cst.Param):
            scope.add(node.params.star_arg.name.value)
        if isinstance(node.params.star_kwarg, cst.Param):
            scope.add(node.params.star_kwarg.name.value)
        self.local_scopes.append(scope)
        return True

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        self.local_scopes.pop()
        new_name = updated_node.name.value
        if not self.local_scopes and new_name in self.rename_map:
            updated_node = updated_node.with_changes(name=cst.Name(self.rename_map[new_name]))
        return updated_node

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.local_scopes.append(set())
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        self.local_scopes.pop()
        new_name = updated_node.name.value
        if not self.local_scopes and new_name in self.rename_map:
            updated_node = updated_node.with_changes(name=cst.Name(self.rename_map[new_name]))
        return updated_node

    def visit_For(self, node: cst.For) -> bool:
        if self.local_scopes and isinstance(node.target, cst.BaseAssignTargetExpression):
            self.local_scopes[-1].update(self._collect_target_names(node.target))
        return True

    def visit_CompFor(self, node: cst.CompFor) -> bool:
        if self.local_scopes and isinstance(node.target, cst.BaseAssignTargetExpression):
            self.local_scopes[-1].update(self._collect_target_names(node.target))
        return True

    def visit_Assign(self, node: cst.Assign) -> bool:
        if self.local_scopes:
            for assign_target in node.targets:
                if isinstance(assign_target.target, cst.BaseAssignTargetExpression):
                    self.local_scopes[-1].update(self._collect_target_names(assign_target.target))
        return True

    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        if self.local_scopes and isinstance(node.target, cst.BaseAssignTargetExpression):
            self.local_scopes[-1].update(self._collect_target_names(node.target))
        return True

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        name = updated_node.value
        if name in self.rename_map and not self._is_shadowed(name):
            return updated_node.with_changes(value=self.rename_map[name])
        return updated_node


def _extract_literal_str_list(expr: cst.BaseExpression) -> list[str] | None:
    if isinstance(expr, (cst.List, cst.Tuple, cst.Set)):
        items: list[str] = []
        for el in expr.elements:
            if isinstance(el, cst.Element) and isinstance(el.value, cst.SimpleString):
                val = ast.literal_eval(el.value.value)
                if isinstance(val, str):
                    items.append(val)
        return items
    return None


def _is_constant_stmt(stmt: cst.BaseStatement) -> tuple[bool, str | None]:
    if isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) == 1:
        small = stmt.body[0]
        if isinstance(small, cst.Assign) and len(small.targets) == 1:
            target = small.targets[0].target
            if isinstance(target, cst.Name):
                name = target.value
                if name.isupper() or (name.startswith("_") and name[1:].isupper()):
                    return True, name
        elif isinstance(small, cst.AnnAssign) and isinstance(small.target, cst.Name):
            name = small.target.value
            if name.isupper() or (name.startswith("_") and name[1:].isupper()):
                return True, name
    return False, None


def _mod_short_name(mod_name: str, pkg_name: str) -> str:
    is_init = mod_name == pkg_name or mod_name.endswith(".__init__")
    rel_parts = mod_name.split(".")[1:]
    if is_init:
        raw = "init" if not rel_parts or rel_parts == ["__init__"] else "_".join(rel_parts[:-1]) or "init"
    else:
        raw = "_".join(rel_parts)
    return _sanitize_ident(raw)


def _analyze_and_collect(bundle: PackageBundle) -> None:
    known_modules = {mod_name for mod_name, _, _ in bundle.modules}
    raw_items_by_module: dict[str, list[tuple[str, str | None, cst.BaseStatement, bool]]] = defaultdict(list)

    for mod_name, _path, tree in bundle.modules:
        is_init = mod_name == bundle.pkg_name or mod_name.endswith(".__init__")

        for stmt in tree.body:
            if isinstance(stmt, cst.If) and _is_type_checking_if(stmt):
                for inner in stmt.body.body:
                    for spec in _extract_import_specs(inner, in_type_checking=True):
                        if spec.is_future:
                            bundle.future_imports.append(spec.raw_code)
                        elif not _is_internal_import(spec, mod_name, bundle.pkg_name, known_modules):
                            bundle.regular_imports.append(spec)
                continue

            specs = _extract_import_specs(stmt, in_type_checking=False)
            if specs:
                all_internal = True
                for spec in specs:
                    if spec.is_future:
                        bundle.future_imports.append(spec.raw_code)
                        all_internal = False
                    elif not _is_internal_import(spec, mod_name, bundle.pkg_name, known_modules):
                        bundle.regular_imports.append(spec)
                        all_internal = False
                if all_internal:
                    continue

            if is_init and isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) == 1:
                small = stmt.body[0]
                if isinstance(small, cst.Assign) and len(small.targets) == 1:
                    if isinstance(small.targets[0].target, cst.Name) and small.targets[0].target.value == "__all__":
                        extracted = _extract_literal_str_list(small.value)
                        if extracted is not None and bundle.explicit_all is None:
                            bundle.explicit_all = extracted
                        continue

            if isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) == 1:
                if isinstance(stmt.body[0], cst.Expr) and isinstance(stmt.body[0].value, cst.SimpleString):
                    continue

            if isinstance(stmt, cst.ClassDef):
                raw_items_by_module[mod_name].append((
                    "class",
                    stmt.name.value,
                    stmt,
                    not stmt.name.value.startswith("_"),
                ))
            elif isinstance(stmt, cst.FunctionDef):
                raw_items_by_module[mod_name].append((
                    "function",
                    stmt.name.value,
                    stmt,
                    not stmt.name.value.startswith("_"),
                ))
            else:
                is_const, const_name = _is_constant_stmt(stmt)
                if is_const and const_name is not None:
                    raw_items_by_module[mod_name].append(("constant", const_name, stmt, not const_name.startswith("_")))
                else:
                    raw_items_by_module[mod_name].append(("other", None, stmt, False))

    symbol_owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for mod_name, items in raw_items_by_module.items():
        for kind, orig_name, _, _ in items:
            if orig_name is not None:
                symbol_owners[orig_name].append((mod_name, kind))

    used_final_names: set[str] = set()
    per_module_renames: dict[str, dict[str, str]] = defaultdict(dict)

    for orig_name, owners in symbol_owners.items():
        has_collision = len(owners) > 1
        for mod_name, _kind in owners:
            mod_short = _mod_short_name(mod_name, bundle.pkg_name)
            if not has_collision and orig_name not in used_final_names:
                final_name = orig_name
            else:
                san = _sanitize_ident(orig_name)
                candidate = f"_{mod_short}_{san}" if not orig_name.startswith("_") else f"_{mod_short}{san}"
                counter = 2
                while candidate in used_final_names:
                    candidate = f"{candidate}_{counter}"
                    counter += 1
                final_name = candidate

            used_final_names.add(final_name)
            bundle.symbol_map[(mod_name, orig_name)] = final_name
            per_module_renames[mod_name][orig_name] = final_name

    for mod_name, items in raw_items_by_module.items():
        mod_short = _mod_short_name(mod_name, bundle.pkg_name)
        rewriter = ScopeAwareNameRewriter(per_module_renames[mod_name])

        for kind, orig_name, node, is_public in items:
            transformed = node.visit(rewriter)
            assert isinstance(transformed, cst.BaseStatement)
            code = cst.Module([]).code_for_node(transformed).strip()
            final_name = per_module_renames[mod_name][orig_name] if orig_name is not None else None

            item = TopLevelItem(
                kind=kind,
                module_name=mod_name,
                mod_short=mod_short,
                orig_name=orig_name,
                final_name=final_name,
                code=code,
                is_public=is_public,
            )
            if kind == "constant":
                bundle.constants.append(item)
            elif kind == "class":
                bundle.classes.append(item)
            elif kind == "function":
                bundle.functions.append(item)
            else:
                bundle.other_top_level.append(item)


def _format_merged_imports(bundle: PackageBundle) -> str:
    future_lines: list[str] = []
    seen_future: set[str] = set()
    for f_imp in bundle.future_imports:
        norm = re.sub(r"\s+", " ", f_imp.strip())
        if norm not in seen_future:
            seen_future.add(norm)
            future_lines.append(norm)

    stdlib_lines: list[str] = []
    third_party_lines: list[str] = []
    seen_lines: set[str] = set()
    from_groups: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    standalone_imports: list[str] = []

    for spec in bundle.regular_imports:
        if spec.module is None:
            for imported, asname in spec.names:
                line = f"import {imported}" + (f" as {asname}" if asname else "")
                if line not in seen_lines:
                    seen_lines.add(line)
                    standalone_imports.append(line)
        else:
            if ("*", None) in spec.names:
                line = f"from {spec.module} import *"
                if line not in seen_lines:
                    seen_lines.add(line)
                    standalone_imports.append(line)
            else:
                for imported, asname in spec.names:
                    pair = (imported, asname)
                    if pair not in from_groups[spec.module]:
                        from_groups[spec.module].append(pair)

    merged_from_lines: list[str] = []
    for mod, pairs in sorted(from_groups.items()):
        formatted_items = [f"{imp} as {alias}" if alias else imp for imp, alias in pairs]
        joined = ", ".join(formatted_items)
        line = f"from {mod} import {joined}"
        if len(line) > 100 and len(formatted_items) > 1:
            inner = ",\n    ".join(formatted_items)
            line = f"from {mod} import (\n    {inner},\n)"
        if line not in seen_lines:
            seen_lines.add(line)
            merged_from_lines.append(line)

    for line in [*standalone_imports, *merged_from_lines]:
        top_mod = line.split()[1].split(".")[0]
        if top_mod in STDLIB_MODULES:
            stdlib_lines.append(line)
        else:
            third_party_lines.append(line)

    sections: list[str] = []
    if future_lines:
        sections.append("\n".join(future_lines))
    if stdlib_lines:
        sections.append("\n".join(stdlib_lines))
    if third_party_lines:
        sections.append("\n".join(third_party_lines))
    return "\n\n".join(sections) if sections else "# No external imports required"


def _format_data_section(data_files: list[DataFileSpec]) -> str:
    if not data_files:
        return (
            "_DATA_FILES: dict[str, tuple[bool, str]] = {}\n\n"
            "def read_data_bytes(rel_path: str) -> bytes:\n"
            '    key = rel_path.lstrip("/").replace("\\\\", "/")\n'
            "    if key not in _DATA_FILES:\n"
            '        raise FileNotFoundError(f"Package data file not found: {rel_path}")\n'
            "    is_b64, content = _DATA_FILES[key]\n"
            "    return base64.b64decode(content.encode('ascii')) if is_b64 else content.encode('utf-8')\n\n"
            "def read_data_text(rel_path: str, encoding: str = 'utf-8') -> str:\n"
            "    return read_data_bytes(rel_path).decode(encoding)"
        )

    entries: list[str] = []
    for spec in data_files:
        key_repr = json.dumps(spec.rel_key)
        payload_repr = json.dumps(spec.payload)
        flag = "True" if spec.is_binary else "False"
        entries.append(f"    {key_repr}: ({flag}, {payload_repr}),")

    dict_block = "_DATA_FILES: dict[str, tuple[bool, str]] = {\n" + "\n".join(entries) + "\n}"
    helpers = (
        "def read_data_bytes(rel_path: str) -> bytes:\n"
        '    """Read an embedded binary or text package data file as bytes."""\n'
        '    key = rel_path.lstrip("/").replace("\\\\", "/")\n'
        "    if key not in _DATA_FILES:\n"
        '        raise FileNotFoundError(f"Package data file not found: {rel_path}")\n'
        "    is_b64, content = _DATA_FILES[key]\n"
        "    if is_b64:\n"
        '        return base64.b64decode(content.encode("ascii"))\n'
        '    return content.encode("utf-8")\n\n\n'
        'def read_data_text(rel_path: str, encoding: str = "utf-8") -> str:\n'
        '    """Read an embedded package data file as decoded text."""\n'
        "    return read_data_bytes(rel_path).decode(encoding)"
    )
    return f"{dict_block}\n\n{helpers}"


def _format_cli_section(bundle: PackageBundle) -> str:
    candidates: list[CliEntryPoint] = list(bundle.cli_entrypoints)
    if not candidates:
        for fn in bundle.functions:
            if fn.orig_name in {"main", "cli", "app"} and fn.final_name:
                candidates.append(
                    CliEntryPoint(
                        script_name=bundle.pkg_name,
                        target_module=fn.module_name,
                        target_attr=fn.final_name,
                    )
                )
                break

    if not candidates:
        return (
            "def main(argv: list[str] | None = None) -> int:\n"
            '    """Default CLI entry point for consolidated package."""\n'
            "    _ = argv\n"
            f'    raise SystemExit("{bundle.pkg_name}: no CLI entry point defined")\n'
        )

    lines: list[str] = [
        "def main(argv: list[str] | None = None) -> int:",
        '    """Unified CLI entry point."""',
    ]
    for ep in candidates:
        resolved_sym = bundle.symbol_map.get((ep.target_module, ep.target_attr), ep.target_attr)
        lines.append(f"    _entry = {resolved_sym}")
        lines.append("    import inspect")
        lines.append("    sig = inspect.signature(_entry)")
        lines.append("    if len(sig.parameters) == 0:")
        lines.append("        result = _entry()")
        lines.append("    else:")
        lines.append("        result = _entry(argv)")
        lines.append("    return int(result) if isinstance(result, int) else 0")
    lines.append("")
    return "\n".join(lines)


def _format_package_shim(bundle: PackageBundle) -> str:
    public_symbols: list[str] = []
    if bundle.explicit_all is not None:
        init_mod = f"{bundle.pkg_name}.__init__"
        for sym in bundle.explicit_all:
            if (init_mod, sym) in bundle.symbol_map:
                public_symbols.append(bundle.symbol_map[(init_mod, sym)])
            else:
                public_symbols.append(sym)
    else:
        for item in (*bundle.classes, *bundle.functions, *bundle.constants):
            if item.is_public and item.final_name:
                public_symbols.append(item.final_name)

    dedup_public = list(dict.fromkeys(public_symbols))
    all_repr = ", ".join(json.dumps(s) for s in dedup_public)

    submodules = sorted({
        mod_name
        for mod_name, _, _ in bundle.modules
        if mod_name != bundle.pkg_name and not mod_name.endswith(".__init__")
    })
    submod_lines = [
        "import sys as _sys",
        "_this_mod = _sys.modules.get(__name__)",
        "if _this_mod is not None:",
        "    if not hasattr(_this_mod, '__path__'):",
        "        _this_mod.__path__ = []",
    ]
    for sub in submodules:
        sub_repr = json.dumps(sub)
        submod_lines.append(f"    _sys.modules.setdefault({sub_repr}, _this_mod)")

    return f"__all__ = [{all_repr}]\n" + "\n".join(submod_lines)


def build_consolidated_source(bundle: PackageBundle) -> str:
    _analyze_and_collect(bundle)

    imports_block = _format_merged_imports(bundle)
    if "import base64" not in imports_block:
        imports_block = f"{imports_block}\nimport base64"

    constants_items = [c.code for c in bundle.constants if c.code] + [o.code for o in bundle.other_top_level if o.code]
    constants_block = "\n\n".join(constants_items) if constants_items else "# No package constants"

    data_block = _format_data_section(bundle.data_files)
    classes_block = "\n\n".join(c.code for c in bundle.classes if c.code) or "# No classes"
    functions_block = "\n\n".join(f.code for f in bundle.functions if f.code) or "# No functions"
    cli_block = _format_cli_section(bundle)
    shim_block = _format_package_shim(bundle)

    has_cli = bool(bundle.cli_entrypoints) or any(fn.orig_name in {"main", "cli", "app"} for fn in bundle.functions)
    main_block = (
        'if __name__ == "__main__":\n    raise SystemExit(main())'
        if has_cli
        else "# No CLI entry point detected in original package"
    )

    parts = [
        f'"""Consolidated single-file build of package `{bundle.pkg_name}`."""',
        "# === 1. IMPORTS ===",
        imports_block,
        "# === 2. CONSTANTS & MODULE STATE ===",
        constants_block,
        "# === 3. PACKAGE DATA FILES ===",
        data_block,
        "# === 4. CLASSES ===",
        classes_block,
        "# === 5. FUNCTIONS ===",
        functions_block,
        "# === 6. PACKAGE COMPATIBILITY & EXPORTS ===",
        shim_block,
        "# === 7. CLI ENTRY POINTS ===",
        cli_block,
        "# === 8. MAIN ===",
        main_block,
        "",
    ]
    return "\n\n".join(parts)


def verify_consolidated_file(output_path: Path, pkg_name: str) -> tuple[bool, str]:
    """Verify syntax, AST validity, and dynamic import behavior in an isolated namespace."""
    try:
        source = output_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"Failed reading output file: {exc}"

    try:
        compile(source, str(output_path), "exec")
    except SyntaxError as exc:
        return False, f"Syntax error in consolidated file: {exc}"

    spec = importlib.util.spec_from_file_location(pkg_name, str(output_path))
    if spec is None or spec.loader is None:
        return False, "Could not create module spec for verification"

    module = importlib.util.module_from_spec(spec)
    saved_modules: dict[str, Any] = {}
    injected_keys: list[str] = []

    try:
        for key in list(sys.modules.keys()):
            if key == pkg_name or key.startswith(f"{pkg_name}."):
                saved_modules[key] = sys.modules.pop(key)

        sys.modules[pkg_name] = module
        injected_keys.append(pkg_name)
        spec.loader.exec_module(module)

        for k in list(sys.modules.keys()):
            if k.startswith(f"{pkg_name}.") and k not in injected_keys:
                injected_keys.append(k)

        if not hasattr(module, "__all__"):
            return False, "Missing __all__ attribute on consolidated module"
        return (
            True,
            f"Verified `{pkg_name}` ({len(source)} bytes, {len(module.__all__)} exported symbols)",
        )
    except Exception as exc:
        return False, f"Runtime verification failed: {type(exc).__name__}: {exc}"
    finally:
        for k in injected_keys:
            sys.modules.pop(k, None)
        sys.modules.update(saved_modules)


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def consolidate_package(
    pkg_dir: Path,
    output_path: Path | None = None,
    verify: bool = True,
    strip: bool = False,
) -> Path:
    pkg_dir = pkg_dir.resolve()
    if not pkg_dir.is_dir():
        raise FileNotFoundError(f"Package directory does not exist: {pkg_dir}")
    if not (pkg_dir / "__init__.py").is_file():
        raise ValueError(f"Target directory is not a Python package (missing __init__.py): {pkg_dir}")

    pkg_name = pkg_dir.name
    project_root = _detect_project_root(pkg_dir)
    out_file = (output_path or (pkg_dir.parent / f"{pkg_name}.py")).resolve()

    py_files, data_file_paths = _discover_files(pkg_dir)
    bundle = PackageBundle(
        pkg_name=pkg_name,
        pkg_dir=pkg_dir,
        project_root=project_root,
        data_files=_load_data_files(pkg_dir, data_file_paths),
        cli_entrypoints=_parse_cli_entrypoints(project_root, pkg_name),
    )

    for mod_name, py_path in py_files:
        src = py_path.read_text(encoding="utf-8")
        if strip:
            src = strip_source_with_libcst(src)
        tree = cst.parse_module(src)
        bundle.modules.append((mod_name, py_path, tree))

    consolidated_code = build_consolidated_source(bundle)
    if strip:
        consolidated_code = strip_source_with_libcst(consolidated_code)

    _atomic_write_text(out_file, consolidated_code)

    if verify:
        ok, message = verify_consolidated_file(out_file, pkg_name)
        if not ok:
            raise RuntimeError(message)
        print(message)

    return out_file


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="consolidate.py",
        description="Consolidate a Python package directory into a single importable Python file.",
    )
    parser.add_argument(
        "package_dir",
        type=Path,
        help="Path to the Python package directory (containing __init__.py)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .py file path (default: <pkg_name>.py)",
    )
    parser.add_argument(
        "--strip",
        action="store_true",
        help="Strip comments and docstrings using LibCST while preserving language directives (# type:, # noqa, etc.)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip post-build import and syntax verification",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        out = consolidate_package(
            args.package_dir,
            args.output,
            verify=not args.no_verify,
            strip=args.strip,
        )
        print(f"Wrote: {out}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
