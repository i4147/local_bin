#!/data/data/com.termux/files/usr/bin/env python
"""
Refactor qualified module attribute accesses into direct imports.

Examples:

    import shutil

    shutil.rmtree("build")

becomes approximately:

    import shutil; from shutil import rmtree as _refactor_rmtree

    _refactor_rmtree("build")

Dotted imports and nested attribute chains are supported:

    import os.path as path

    path.join("a", "b")

becomes approximately:

    import os.path as path; from os.path import join as _refactor_join

    _refactor_join("a", "b")

Original imports are retained to preserve their bindings and import side
effects. Generated aliases are unique among names appearing in the file.

Existing from-imports can optionally be treated as module imports:

    from os import path

    path.join("a", "b")

Use --include-from-imports to enable this behavior. This is opt-in because
the imported object may not actually be an importable module.

Usage:

    python refactor_imports.py path/to/file.py
    python refactor_imports.py path/to/file.py --dry-run
    python refactor_imports.py path/to/file.py --inplace
    python refactor_imports.py path/to/file.py --diff
    python refactor_imports.py path/to/file.py --include-from-imports

Requires:

    pip install libcst

Caveats:

* This transformation may change behavior for dynamically mutated modules,
  conditional imports, circular imports, or code relying on import timing.
* Writes such as module.attribute = value are not rewritten.
* Star imports, relative from-imports, and future imports are not transformed.
* Existing imports are retained; this tool does not remove unused imports.
* Always review the result and run the project's tests.
"""

from __future__ import annotations

import argparse
import difflib
import io
import keyword
import sys
import tokenize
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import libcst as cst
from libcst.metadata import (
    ExpressionContext,
    ExpressionContextProvider,
    MetadataWrapper,
    QualifiedName,
    QualifiedNameProvider,
    QualifiedNameSource,
)

# A module path and the attribute imported from it.
ImportKey = Tuple[str, str]


@dataclass(frozen=True)
class ModuleBinding:
    """
    A module-like binding introduced by an import statement.

    qualified_name is the name reported by QualifiedNameProvider for the
    expression identifying the imported module.

    import_path is the absolute module path used in a generated from-import.
    """

    qualified_name: str
    import_path: str


def dotted_name(node: cst.BaseExpression) -> Optional[str]:
    """Return a dotted name for a Name/Attribute expression, if possible."""
    if isinstance(node, cst.Name):
        return node.value

    if isinstance(node, cst.Attribute):
        prefix = dotted_name(node.value)
        if prefix is not None:
            return f"{prefix}.{node.attr.value}"

    return None


def valid_identifier(value: str) -> bool:
    """Return whether value is a legal, non-keyword Python identifier."""
    return value.isidentifier() and not keyword.iskeyword(value)


def make_fresh_name(preferred: str, occupied: Set[str]) -> str:
    """Create a legal identifier that is not already present in occupied."""
    cleaned = "".join(char if char.isalnum() or char == "_" else "_" for char in preferred)

    if not cleaned or cleaned[0].isdigit():
        cleaned = f"_{cleaned}"

    if keyword.iskeyword(cleaned):
        cleaned = f"_{cleaned}"

    candidate = cleaned
    suffix = 2

    while candidate in occupied:
        candidate = f"{cleaned}_{suffix}"
        suffix += 1

    occupied.add(candidate)
    return candidate


def import_alias_local_name(alias: cst.ImportAlias) -> Optional[str]:
    """Return the local binding introduced by an import alias."""
    if alias.asname is not None:
        return alias.asname.name.value

    name = dotted_name(alias.name)
    if name is None:
        return None

    return name.split(".", 1)[0]


def from_import_local_name(alias: cst.ImportAlias) -> Optional[str]:
    """Return the local binding introduced by a from-import alias."""
    if alias.asname is not None:
        return alias.asname.name.value

    name = dotted_name(alias.name)
    if name is None:
        return None

    return name.rsplit(".", 1)[-1]


class BindingCollector(cst.CSTVisitor):
    """
    Collect module-like import bindings and all identifiers in the file.

    The mapping from an import node to its bindings is used to insert
    generated imports immediately after the corresponding original import.
    """

    def __init__(self, include_from_imports: bool) -> None:
        self.include_from_imports = include_from_imports
        self.occupied_names: Set[str] = set()
        self.bindings_by_node: Dict[cst.CSTNode, List[ModuleBinding]] = {}
        self.bindings_by_qualified_name: DefaultDict[str, Set[ModuleBinding]] = defaultdict(set)

    def visit_Name(self, node: cst.Name) -> None:
        # Including attribute names is deliberately conservative.
        self.occupied_names.add(node.value)

    def _record(
        self,
        node: cst.CSTNode,
        bindings: Iterable[ModuleBinding],
    ) -> None:
        items = list(bindings)
        if not items:
            return

        self.bindings_by_node[node] = items

        for binding in items:
            self.bindings_by_qualified_name[binding.qualified_name].add(binding)

    def visit_Import(self, node: cst.Import) -> None:
        bindings: List[ModuleBinding] = []

        for alias in node.names:
            module_path = dotted_name(alias.name)
            if module_path is None:
                continue

            local_name = import_alias_local_name(alias)
            if local_name is None:
                continue

            if alias.asname is not None:
                # import os.path as path
                # path is qualified as os.path.
                qualified_name = module_path
            else:
                # import os.path
                # The expression os.path is qualified as os.path.
                qualified_name = module_path

            bindings.append(
                ModuleBinding(
                    qualified_name=qualified_name,
                    import_path=module_path,
                )
            )

        self._record(node, bindings)

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if not self.include_from_imports:
            return

        # Relative imports need package context and cannot safely be turned
        # into absolute imports from the source file alone.
        if node.relative or node.module is None:
            return

        module_path = dotted_name(node.module)
        if module_path is None or module_path == "__future__":
            return

        if isinstance(node.names, cst.ImportStar):
            return

        bindings: List[ModuleBinding] = []

        for alias in node.names:
            imported_name = dotted_name(alias.name)
            if imported_name is None:
                continue

            local_name = from_import_local_name(alias)
            if local_name is None:
                continue

            imported_path = f"{module_path}.{imported_name}"

            bindings.append(
                ModuleBinding(
                    qualified_name=imported_path,
                    import_path=imported_path,
                )
            )

        self._record(node, bindings)


class UsageCollector(cst.CSTVisitor):
    """Collect eligible read accesses to imported module attributes."""

    METADATA_DEPENDENCIES = (
        ExpressionContextProvider,
        QualifiedNameProvider,
    )

    def __init__(
        self,
        bindings_by_qualified_name: Dict[str, Set[ModuleBinding]],
    ) -> None:
        self.bindings_by_qualified_name = bindings_by_qualified_name
        self.used_keys: Set[ImportKey] = set()

        # Attribute node -> module path and attribute.
        self.matches: Dict[cst.Attribute, ImportKey] = {}

    def visit_Attribute(self, node: cst.Attribute) -> None:
        # Never replace direct writes or deletions:
        #
        #     module.attr = value
        #     del module.attr
        #
        # An inner read in module.attr.member = value can still be rewritten.
        context = self.get_metadata(
            ExpressionContextProvider,
            node,
            default=None,
        )
        if context != ExpressionContext.LOAD:
            return

        qualified_names = self.get_metadata(
            QualifiedNameProvider,
            node.value,
            default=set(),
        )

        # If LibCST cannot resolve the base, or resolves it ambiguously,
        # leave it untouched rather than guessing.
        if len(qualified_names) != 1:
            return

        qualified_name: QualifiedName = next(iter(qualified_names))

        if qualified_name.source != QualifiedNameSource.IMPORT:
            return

        candidates = self.bindings_by_qualified_name.get(
            qualified_name.name,
            set(),
        )

        # Multiple distinct import paths for the same qualified name are
        # treated as ambiguous.
        paths = {binding.import_path for binding in candidates}
        if len(paths) != 1:
            return

        module_path = next(iter(paths))
        attribute = node.attr.value

        if not valid_identifier(attribute):
            return

        key = (module_path, attribute)
        self.used_keys.add(key)
        self.matches[node] = key


class ImportFromTransformer(cst.CSTTransformer):
    """
    Insert direct imports and rewrite matched attribute reads.

    Generated imports are inserted after each original import that can
    supply the corresponding module path. Original imports remain in place.
    """

    def __init__(
        self,
        bindings_by_node: Dict[cst.CSTNode, List[ModuleBinding]],
        matches: Dict[cst.Attribute, ImportKey],
        aliases: Dict[ImportKey, str],
    ) -> None:
        self.bindings_by_node = bindings_by_node
        self.matches = matches
        self.aliases = aliases

        self.attributes_by_module: DefaultDict[str, List[Tuple[str, str]]] = defaultdict(list)

        for (module_path, attribute), local_alias in sorted(aliases.items()):
            self.attributes_by_module[module_path].append((attribute, local_alias))

    def _generated_imports(
        self,
        original_node: cst.CSTNode,
    ) -> List[cst.ImportFrom]:
        """Build direct imports associated with one original import node."""
        result: List[cst.ImportFrom] = []
        seen_paths: Set[str] = set()

        for binding in self.bindings_by_node.get(original_node, []):
            module_path = binding.import_path

            if module_path in seen_paths:
                continue

            seen_paths.add(module_path)
            attributes = self.attributes_by_module.get(module_path, [])

            if not attributes:
                continue

            names = [
                cst.ImportAlias(
                    name=cst.Name(attribute),
                    asname=cst.AsName(name=cst.Name(local_alias)),
                )
                for attribute, local_alias in attributes
            ]

            result.append(
                cst.ImportFrom(
                    module=cst.parse_expression(module_path),
                    names=names,
                )
            )

        return result

    def leave_Import(
        self,
        original_node: cst.Import,
        updated_node: cst.Import,
    ) -> cst.BaseSmallStatement | cst.FlattenSentinel[cst.BaseSmallStatement]:
        generated = self._generated_imports(original_node)

        if not generated:
            return updated_node

        return cst.FlattenSentinel([updated_node, *generated])

    def leave_ImportFrom(
        self,
        original_node: cst.ImportFrom,
        updated_node: cst.ImportFrom,
    ) -> cst.BaseSmallStatement | cst.FlattenSentinel[cst.BaseSmallStatement]:
        generated = self._generated_imports(original_node)

        if not generated:
            return updated_node

        return cst.FlattenSentinel([updated_node, *generated])

    def leave_Attribute(
        self,
        original_node: cst.Attribute,
        updated_node: cst.Attribute,
    ) -> cst.BaseExpression:
        key = self.matches.get(original_node)
        if key is None:
            return updated_node

        return cst.Name(
            value=self.aliases[key],
            lpar=updated_node.lpar,
            rpar=updated_node.rpar,
        )


def refactor_imports(
    source: str,
    *,
    include_from_imports: bool = False,
) -> str:
    """
    Return refactored Python source.

    Existing from-imports are only treated as module bindings when
    include_from_imports is True.
    """
    module = cst.parse_module(source)
    wrapper = MetadataWrapper(module)

    binding_collector = BindingCollector(
        include_from_imports=include_from_imports,
    )
    wrapper.visit(binding_collector)

    if not binding_collector.bindings_by_node:
        return source

    usage_collector = UsageCollector(bindings_by_qualified_name=dict(binding_collector.bindings_by_qualified_name))
    wrapper.visit(usage_collector)

    if not usage_collector.used_keys:
        return source

    occupied = set(binding_collector.occupied_names)
    aliases: Dict[ImportKey, str] = {}

    for module_path, attribute in sorted(usage_collector.used_keys):
        aliases[(module_path, attribute)] = make_fresh_name(
            f"_refactor_{attribute}",
            occupied,
        )

    transformer = ImportFromTransformer(
        bindings_by_node=binding_collector.bindings_by_node,
        matches=usage_collector.matches,
        aliases=aliases,
    )

    return wrapper.module.visit(transformer).code


def read_python_source(path: Path) -> Tuple[str, str]:
    """
    Read a Python file using its declared PEP 263 encoding.

    Newline characters are preserved so LibCST can preserve file formatting.
    """
    raw = path.read_bytes()
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding), encoding


def write_python_source(path: Path, source: str, encoding: str) -> None:
    """Write source using the original encoding."""
    path.write_bytes(source.encode(encoding))


def make_diff(original: str, modified: str, filename: str) -> str:
    """Return a unified diff."""
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=filename,
            tofile=f"{filename} (refactored)",
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=("Rewrite imported module attribute reads using direct imports."))

    parser.add_argument(
        "file",
        type=Path,
        help="Python source file to process",
    )

    output = parser.add_mutually_exclusive_group()

    output.add_argument(
        "--inplace",
        "-i",
        action="store_true",
        help="Write changes back to the file",
    )

    output.add_argument(
        "--dry-run",
        action="store_true",
        help="Print transformed source without writing (the default)",
    )

    output.add_argument(
        "--diff",
        action="store_true",
        help="Print a unified diff instead of the transformed source",
    )

    output.add_argument(
        "--check",
        action="store_true",
        help="Exit with status 1 if changes would be made; do not write",
    )

    parser.add_argument(
        "--include-from-imports",
        action="store_true",
        help=(
            "Also treat objects imported by 'from ... import ...' as "
            "modules. This may fail if an imported object is not a module."
        ),
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        original, encoding = read_python_source(args.file)

        modified = refactor_imports(
            original,
            include_from_imports=args.include_from_imports,
        )

        # Validate that the output is syntactically valid Python.
        cst.parse_module(modified)

        if args.check:
            return 1 if modified != original else 0

        if args.diff:
            sys.stdout.write(make_diff(original, modified, str(args.file)))
        elif args.inplace:
            if modified != original:
                write_python_source(args.file, modified, encoding)
                print(f"Updated {args.file}", file=sys.stderr)
            else:
                print(f"No changes: {args.file}", file=sys.stderr)
        else:
            sys.stdout.write(modified)

        return 0

    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        print(f"Error processing {args.file}: {exc}", file=sys.stderr)
        return 2
    except cst.ParserSyntaxError as exc:
        print(f"Parse error in {args.file}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
