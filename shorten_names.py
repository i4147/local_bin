"""Shorten identifier names in a Python file, or across a whole package.

This module extends the classic single-file name shortener to packages:

* **Package mode** - pass a directory to :func:`shorten_names`.  Every ``*.py``
  file below it is discovered (``__pycache__``, hidden folders and ``*.bak``
  files are skipped) and processed as one unit.  Passing a single file still
  works exactly like before.
* **Cross-file resolution** - when a module-level name is shortened, every
  internal importer is updated to match: ``from .models import foo`` becomes
  ``from .models import a1`` and all references to it in the importing module
  are rewritten with full scope awareness, so a local variable that shadows an
  imported name keeps its own, separate short name.  Relative and absolute
  internal imports are both resolved through a registry built from the package
  layout; re-export chains (``a`` imports from ``b`` which imports from ``c``)
  are followed transitively, and import cycles degrade gracefully (the names
  involved are left untouched instead of being broken).
* **``__all__`` preservation** - names listed in any module's ``__all__`` are
  never renamed anywhere in the package, and ``__all__`` lists themselves are
  never rewritten.  Re-exports through ``__init__.py`` therefore keep the
  package's public API byte-for-byte identical from the outside.

What is renamed
---------------
Module-level names (functions, classes, constants) that are not exported via
``__all__``, plus function/lambda/comprehension-local names (parameters and
variables), subject to the safety rules below.  Short names are handed out in
order of descending usage frequency, just like the single-file version.

What is deliberately kept (safety rules)
----------------------------------------
* anything in any ``__all__`` of the package (module-level slots only);
* names accessed as attributes anywhere (``obj.foo`` / ``mod.foo``) - frozen
  package-wide for module-level slots, because attribute targets cannot be
  inferred statically;
* names used as call-site keywords anywhere (``f(**{"x": 1})`` style cannot be
  traced, so ``f(x=1)`` freezes every parameter slot named ``x``);
* names bound by external imports, module references (``from . import mod``,
  ``import pkg.mod``) and aliased imports (``from x import y as z`` keeps ``z``
  but the source side is still updated if the source is internal);
* names in modules with ``import *``, dynamic/unknown ``__all__``, or bare
  ``globals()``/``vars()`` calls;
* names mentioned in string annotations (``def f(x: "MyType")``) and in PEP 695
  type parameters / ``type`` aliases;
* names that libcst cannot resolve unambiguously (match-pattern captures,
  walrus targets leaking out of comprehensions, conditional-import
  ambiguities, ...).  Unresolvable means unrenamable - and the same name is
  frozen everywhere it could interact.

Known limitations
-----------------
Reflection that references names as raw strings (``getattr(obj, "name")``,
``eval``/``exec``, ``ctypes``) is not tracked; external code importing names
that are not listed in ``__all__`` will break (those names are considered
internal - which is the point of the tool).

Usage
-----
Programmatic::

    from shorten_names import shorten_names

    shorten_names("path/to/package")  # writes *.py.bak backups
    shorten_names("single_file.py", backup=True)

Command line::

    python shorten_names.py <file-or-dir> [--no-backup] [--dry-run] [-q]

Requires Python 3.10+ and ``libcst``.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import keyword
import string
import sys
from collections import Counter
from dataclasses import dataclass, field
from itertools import count, product
from pathlib import Path
from typing import Iterator, Optional, Union

import libcst as cst
from libcst.helpers import get_full_name_for_node
from libcst.metadata import ClassScope, MetadataWrapper, ScopeProvider

__all__ = ["shorten_names"]

RESERVED = set(dir(builtins)) | set(keyword.kwlist)


def _protected(name: str) -> bool:
    """Dunders (name mangling), and the conventional throwaway ``_``."""
    return name.startswith("__") or name == "_"


def _short_names(taken: set[str]) -> Iterator[str]:
    """Yield short identifiers that are not in the live set ``taken``."""
    for size in count(1):
        for chars in product(string.ascii_letters, repeat=size):
            candidate = "".join(chars)
            if candidate not in taken:
                yield candidate


# ---------------------------------------------------------------------------
# Package discovery and import resolution
# ---------------------------------------------------------------------------


def _discover(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        found.append(path)
    return found


def _build_registry(files: list[Path], root: Path, root_is_package: bool) -> tuple[dict[str, Path], dict[Path, str]]:
    """Map every plausible dotted import name to a file, and files to their
    canonical module name."""
    registry: dict[str, Path] = {}
    primary: dict[Path, str] = {}
    for path in files:
        parts = list(path.relative_to(root).with_suffix("").parts)
        is_init = bool(parts) and parts[-1] == "__init__"
        if is_init:
            parts = parts[:-1]
        if root_is_package:
            full = ".".join([root.name, *parts])
            alias = ".".join(parts) if parts else None
        else:
            full = ".".join(parts)
            alias = None
        if not full:
            continue
        if full in registry and registry[full] != path:
            continue  # ambiguous layout (mod.py next to mod/); first wins
        registry.setdefault(full, path)
        if alias and alias not in registry:
            registry[alias] = path
        primary.setdefault(path, full)
    return registry, primary


def _resolve_target(module_name: str, is_package: bool, level: int, module_str: Optional[str]) -> Optional[str]:
    """Dotted path an import refers to, or ``None`` if it escapes the root."""
    if level == 0:
        # absolute import: independent of the importing module
        return module_str
    parts = module_name.split(".")
    base = parts if is_package else parts[:-1]
    drop = level - 1
    if drop > len(base):
        return None
    if drop:
        base = base[: len(base) - drop]
    if module_str:
        merged = [*base, *module_str.split(".")]
        return ".".join(merged) if merged else None
    return ".".join(base) if base else None


# ---------------------------------------------------------------------------
# Per-module static analysis
# ---------------------------------------------------------------------------


@dataclass
class _AliasRecord:
    """One ``from X import name [as alias]`` (or ``import X``) clause."""

    node_id: int  # id() of the enclosing ImportFrom/Import
    index: int  # position within .names
    binding: str  # name bound in this module
    asname: Optional[str]
    source_name: str  # name as written on the source side
    target: Optional[str]  # resolved internal module (primary name)
    module_ref: bool  # binds a module object, not a value
    binding_qnames: set[str] = field(default_factory=set)


@dataclass
class _ModuleFacts:
    path: Path
    name: str
    is_package: bool
    source: str = ""
    ok: bool = False
    wrapper: Optional[MetadataWrapper] = None
    # static name collections
    identifiers: set[str] = field(default_factory=set)
    attrs: set[str] = field(default_factory=set)
    kwargs: set[str] = field(default_factory=set)
    empty_names: set[str] = field(default_factory=set)
    keep_names: set[str] = field(default_factory=set)
    qname_counts: Counter = field(default_factory=Counter)
    ambiguous_qnames: set[str] = field(default_factory=set)
    local_qnames: set[str] = field(default_factory=set)
    bare_qnames: set[str] = field(default_factory=set)
    module_slot_names: set[str] = field(default_factory=set)
    aliases: list[_AliasRecord] = field(default_factory=list)
    bindings_by_name: dict[str, list[_AliasRecord]] = field(default_factory=dict)
    import_qnames: set[str] = field(default_factory=set)
    all_names: Optional[set[str]] = None
    has_star_import: bool = False
    reflects: bool = False  # globals()/vars() or unknown __all__


class _NameCollector(cst.CSTVisitor):
    """Collect every ``cst.Name`` node inside a subtree."""

    def __init__(self) -> None:
        self.names: list[cst.Name] = []

    def visit_Name(self, node: cst.Name) -> bool:
        self.names.append(node)
        return True

    @classmethod
    def collect(cls, node: cst.CSTNode) -> list[cst.Name]:
        visitor = cls()
        node.visit(visitor)
        return visitor.names


def _string_literal(node: cst.CSTNode) -> Optional[str]:
    if isinstance(node, cst.SimpleString):
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            return None
        return value if isinstance(value, str) else None
    return None


def _string_list_literals(node: cst.CSTNode) -> Optional[list[str]]:
    """Literal string contents of a list/tuple/set of string literals."""
    if not isinstance(node, (cst.List, cst.Tuple, cst.Set)):
        return None
    out: list[str] = []
    for element in node.elements:
        inner = getattr(element, "value", element)
        literal = _string_literal(inner)
        if literal is None:
            return None
        out.append(literal)
    return out


class _AllScanner(cst.CSTVisitor):
    """Statically collect ``__all__`` entries at module flow level."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.unknown = False
        self.handled: set[int] = set()
        self.mentions: set[int] = set()
        self._in_def = 0
        self._in_import = 0

    def visit_FunctionDef(self, node) -> bool:
        self._in_def += 1
        return True

    def leave_FunctionDef(self, original_node) -> None:
        self._in_def -= 1

    def visit_ClassDef(self, node) -> bool:
        self._in_def += 1
        return True

    def leave_ClassDef(self, original_node) -> None:
        self._in_def -= 1

    def visit_Import(self, node) -> bool:
        self._in_import += 1
        return True

    def leave_Import(self, original_node) -> None:
        self._in_import -= 1

    def visit_ImportFrom(self, node) -> bool:
        self._in_import += 1
        return True

    def leave_ImportFrom(self, original_node) -> None:
        self._in_import -= 1

    def visit_Name(self, node: cst.Name) -> bool:
        if node.value == "__all__" and not self._in_def and not self._in_import:
            self.mentions.add(id(node))
        return True

    def _is_all(self, target: cst.CSTNode) -> bool:
        return isinstance(target, cst.Name) and target.value == "__all__"

    def _mentions_all(self, target: cst.CSTNode) -> bool:
        return any(n.value == "__all__" for n in _NameCollector.collect(target))

    def visit_Assign(self, node: cst.Assign) -> bool:
        if self._in_def:
            return True
        # libcst wraps plain assign targets in AssignTarget nodes
        targets = [t.target if isinstance(t, cst.AssignTarget) else t for t in node.targets]
        if not any(self._mentions_all(t) for t in targets):
            return True
        literals = _string_list_literals(node.value)
        if literals is None:
            self.unknown = True
            return True
        self.names.update(literals)
        for target in targets:
            if self._is_all(target):
                self.handled.add(id(target))
            else:  # e.g. tuple unpacking containing __all__
                self.unknown = True
        return True

    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        if self._in_def or not self._is_all(node.target):
            return True
        if node.value is None:
            self.handled.add(id(node.target))
            return True
        literals = _string_list_literals(node.value)
        if literals is None:
            self.unknown = True
            return True
        self.names.update(literals)
        self.handled.add(id(node.target))
        return True

    def visit_AugAssign(self, node: cst.AugAssign) -> bool:
        if self._in_def or not self._is_all(node.target):
            return True
        if not isinstance(node.operator, cst.AddAssign):
            self.unknown = True
            return True
        literals = _string_list_literals(node.value)
        if literals is None:
            self.unknown = True
            return True
        self.names.update(literals)
        self.handled.add(id(node.target))
        return True

    def visit_Del(self, node: cst.Del) -> bool:
        if not self._in_def and self._mentions_all(node.target):
            self.unknown = True
        return True

    def visit_Call(self, node: cst.Call) -> bool:
        if self._in_def:
            return True
        func = node.func
        if not (isinstance(func, cst.Attribute) and isinstance(func.value, cst.Name) and func.value.value == "__all__"):
            return True
        attr = func.attr.value
        if attr == "append" and len(node.args) == 1:
            literal = _string_literal(node.args[0].value)
            if literal is not None:
                self.names.add(literal)
                self.handled.add(id(func.value))
                return True
        elif attr == "extend" and len(node.args) == 1:
            literals = _string_list_literals(node.args[0].value)
            if literals is not None:
                self.names.update(literals)
                self.handled.add(id(func.value))
                return True
        elif attr == "insert" and len(node.args) == 2:
            literal = _string_literal(node.args[1].value)
            if literal is not None:
                self.names.add(literal)
                self.handled.add(id(func.value))
                return True
        self.unknown = True
        return True


def _extract_all(tree: cst.Module) -> Optional[set[str]]:
    scanner = _AllScanner()
    tree.visit(scanner)
    if scanner.unknown or (scanner.mentions - scanner.handled):
        return None
    return scanner.names


def _names_in_string_annotation(raw: str) -> set[str]:
    try:
        text = ast.literal_eval(raw)
        if not isinstance(text, str):
            return set()
        parsed = ast.parse(text, mode="eval")
    except (ValueError, SyntaxError):
        return set()
    return {node.id for node in ast.walk(parsed) if isinstance(node, ast.Name)}


class _Analyzer(cst.CSTVisitor):
    """Collect per-module facts needed to plan and apply renames."""

    METADATA_DEPENDENCIES = (ScopeProvider,)

    def __init__(
        self,
        facts: _ModuleFacts,
        registry: dict[str, Path],
        primary: dict[Path, str],
    ) -> None:
        self.f = facts
        self.registry = registry
        self.primary = primary
        self._import_depth = 0

    # -- imports ---------------------------------------------------------

    def visit_Import(self, node: cst.Import) -> bool:
        self._import_depth += 1
        for index, alias in enumerate(node.names):
            asname = alias.asname.name.value if alias.asname else None
            dotted = get_full_name_for_node(alias.name) or ""
            bound = asname or dotted.split(".")[0]
            self.f.identifiers.update(dotted.split("."))
            if asname:
                self.f.identifiers.add(asname)
            # uses of a plain-import binding resolve to the *module* qname
            # (e.g. ``import libcst as cst`` -> uses of ``cst`` are 'libcst'),
            # which is dotless for single-component modules - those qnames
            # must never be treated as renameable module slots.
            if dotted:
                self.f.import_qnames.add(dotted)
                self.f.import_qnames.add(dotted.split(".")[0])
            if not bound:
                continue
            record = _AliasRecord(
                node_id=id(node),
                index=index,
                binding=bound,
                asname=asname,
                source_name=dotted,
                target=None,
                module_ref=True,
            )
            self.f.aliases.append(record)
            self.f.bindings_by_name.setdefault(bound, []).append(record)
            self.f.module_slot_names.add(bound)
        return True

    def leave_Import(self, original_node: cst.Import) -> None:
        self._import_depth -= 1

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        self._import_depth += 1
        module_str = get_full_name_for_node(node.module) if node.module else None
        level = len(node.relative)
        if module_str:
            self.f.identifiers.update(module_str.split("."))
        if isinstance(node.names, cst.ImportStar):
            self.f.has_star_import = True
            return True
        raw_target = None
        target_primary: Optional[str] = None
        if not (level == 0 and module_str is None):
            raw_target = _resolve_target(self.f.name, self.f.is_package, level, module_str)
            if raw_target and raw_target in self.registry:
                target_primary = self.primary.get(self.registry[raw_target])
        try:
            scope = self.get_metadata(ScopeProvider, node)
        except KeyError:
            scope = None
        for index, alias in enumerate(node.names):
            if isinstance(alias, cst.ImportStar):
                self.f.has_star_import = True
                continue
            source_name = (
                alias.name.value if isinstance(alias.name, cst.Name) else (get_full_name_for_node(alias.name) or "")
            )
            asname = alias.asname.name.value if alias.asname else None
            binding = asname or source_name
            self.f.identifiers.update(n for n in (source_name, asname) if n)
            # `from x import y` may actually import the submodule x.y
            module_ref = bool(target_primary and raw_target and f"{raw_target}.{source_name}" in self.registry)
            binding_qnames: set[str] = set()
            if scope is not None and isinstance(alias.name, cst.Name):
                try:
                    binding_qnames = {q.name for q in scope.get_qualified_names_for(alias.name)}
                except KeyError:
                    pass
            record = _AliasRecord(
                node_id=id(node),
                index=index,
                binding=binding,
                asname=asname,
                source_name=source_name,
                target=target_primary,
                module_ref=module_ref,
                binding_qnames=binding_qnames,
            )
            self.f.aliases.append(record)
            self.f.bindings_by_name.setdefault(binding, []).append(record)
            self.f.module_slot_names.add(binding)
        return True

    def leave_ImportFrom(self, original_node: cst.ImportFrom) -> None:
        self._import_depth -= 1

    # -- other static name collections ------------------------------------

    def visit_Attribute(self, node: cst.Attribute) -> bool:
        if not self._import_depth:
            self.f.attrs.add(node.attr.value)
        return True

    def visit_MatchClass(self, node: cst.MatchClass) -> bool:
        # `case Point(x=0)` matches by attribute name on the subject
        for keyword in node.kwds:
            if keyword.keyword is not None:
                self.f.attrs.add(keyword.keyword.value)
        return True

    def visit_Arg(self, node: cst.Arg) -> bool:
        if node.keyword is not None:
            self.f.kwargs.add(node.keyword.value)
        return True

    def visit_Call(self, node: cst.Call) -> bool:
        if isinstance(node.func, cst.Name) and node.func.value in ("globals", "vars") and not node.args:
            self.f.reflects = True
        return True

    def visit_Annotation(self, node: cst.Annotation) -> bool:
        annotation = node.annotation
        if isinstance(annotation, cst.SimpleString):
            self.f.keep_names.update(_names_in_string_annotation(annotation.value))
        return True

    def visit_FunctionDef(self, node) -> bool:
        self._collect_type_parameters(getattr(node, "type_parameters", None))
        return True

    def visit_ClassDef(self, node) -> bool:
        self._collect_type_parameters(getattr(node, "type_parameters", None))
        return True

    def visit_TypeAlias(self, node) -> bool:  # pragma: no cover - PEP 695
        try:
            self.f.keep_names.add(node.name.value)
            for name in _NameCollector.collect(node.value):
                self.f.keep_names.add(name.value)
        except AttributeError:
            pass
        return True

    def _collect_type_parameters(self, type_parameters) -> None:
        if type_parameters is None:
            return
        for name in _NameCollector.collect(type_parameters):
            self.f.keep_names.add(name.value)

    # -- name tokens -------------------------------------------------------

    def visit_Name(self, node: cst.Name) -> bool:
        if self._import_depth:
            return True
        value = node.value
        self.f.identifiers.add(value)
        try:
            scope = self.get_metadata(ScopeProvider, node)
        except KeyError:
            return True  # attribute / global / nonlocal statement token
        try:
            qnames = scope.get_qualified_names_for(node)
        except KeyError:
            return True  # attribute access - handled via self.f.attrs
        if not qnames:
            # unresolvable: builtin-ish, star import, match capture,
            # walrus leaking out of a comprehension, ... -> freeze the name
            self.f.empty_names.add(value)
        elif len(qnames) == 1:
            qname = next(iter(qnames)).name
            self.f.qname_counts[qname] += 1
            if isinstance(scope, ClassScope):
                pass  # class members are attribute-accessible -> never rename
            elif "<locals>" in qname or "<comprehension>" in qname:
                self.f.local_qnames.add(qname)
            elif "." not in qname and qname not in self.f.import_qnames:
                self.f.bare_qnames.add(qname)
                self.f.module_slot_names.add(qname)
        else:
            for qualified in qnames:
                self.f.ambiguous_qnames.add(qualified.name)
        return True


def _analyze(path: Path, name: str, registry: dict[str, Path], primary: dict[Path, str]) -> _ModuleFacts:
    facts = _ModuleFacts(path=path, name=name, is_package=path.name == "__init__.py")
    facts.source = path.read_text(encoding="utf-8")
    tree = cst.parse_module(facts.source)
    # unsafe_skip_copy keeps node identity stable so id() lookups in the
    # rewrite phase match the analysis phase.
    facts.wrapper = MetadataWrapper(tree, unsafe_skip_copy=True)
    facts.wrapper.visit(_Analyzer(facts, registry, primary))
    facts.all_names = _extract_all(tree)
    if facts.all_names is None:
        facts.reflects = True
    facts.ok = True
    return facts


# ---------------------------------------------------------------------------
# Rename planning
# ---------------------------------------------------------------------------


def _follow_binding(facts: _ModuleFacts, name: str) -> Optional[_AliasRecord]:
    """The single plain internal ``from X import name`` binding, if any."""
    records = facts.bindings_by_name.get(name, [])
    if len(records) != 1:
        return None
    record = records[0]
    if record.target and not record.module_ref and not record.asname:
        return record
    return None


class _Plan:
    """Package-wide rename decisions."""

    def __init__(self, facts: dict[str, _ModuleFacts]) -> None:
        self.facts = facts
        self.frozen_module: set[str] = set()
        self.frozen_kwargs: set[str] = set()
        for module in facts.values():
            if module.all_names:
                self.frozen_module |= module.all_names
            self.frozen_module |= module.attrs
            self.frozen_kwargs |= module.kwargs
        self.global_taken: set[str] = set(RESERVED) | self.frozen_module
        for module in facts.values():
            self.global_taken |= module.identifiers
        self.global_gen = _short_names(self.global_taken)
        self.force_keep: set[tuple[str, str]] = set()
        self._cache: dict[tuple[str, str], str] = {}
        self._visiting: set[tuple[str, str]] = set()
        self._precompute_force_keep()

    def _precompute_force_keep(self) -> None:
        for module in self.facts.values():
            for name, records in module.bindings_by_name.items():
                plain = [r for r in records if r.target and not r.module_ref and not r.asname]
                if plain and len(records) > 1:
                    # one name bound by several imports: cannot follow them
                    # all, so every source must keep its name
                    for record in plain:
                        self.force_keep.add((record.target, record.source_name))
                if len(records) == 1 and plain and records[0].module_ref:
                    continue
            # ambiguous references through an import binding freeze the source
            for record in module.aliases:
                if record.target and not record.module_ref and record.binding_qnames & module.ambiguous_qnames:
                    self.force_keep.add((record.target, record.source_name))

    # -- module-level decisions -------------------------------------------

    def decide(self, module_name: str, name: str) -> str:
        """Final name for module-level slot ``name`` (unchanged if kept)."""
        key = (module_name, name)
        if key in self._cache:
            return self._cache[key]
        if key in self._visiting:  # import cycle -> keep
            return name
        self._visiting.add(key)
        try:
            new_name = self._decide_uncached(module_name, name)
        finally:
            self._visiting.discard(key)
        self._cache[key] = new_name
        return new_name

    def _decide_uncached(self, module_name: str, name: str) -> str:
        facts = self.facts.get(module_name)
        if facts is None:
            return name  # target module was skipped (e.g. parse failure)
        if (
            _protected(name)
            or name in RESERVED
            or name in facts.import_qnames
            or name in facts.empty_names
            or name in facts.keep_names
            or name in facts.ambiguous_qnames
            or facts.reflects
            or facts.has_star_import
            or name in self.frozen_module
            or (module_name, name) in self.force_keep
        ):
            return name
        record = _follow_binding(facts, name)
        if record is not None:
            source = self.facts.get(record.target)
            if source is None or record.source_name not in source.module_slot_names:
                return name
            # follow the chain of internal re-exports
            return self.decide(record.target, record.source_name)
        if facts.bindings_by_name.get(name):
            return name  # external / aliased / module-ref import binding
        if name not in facts.module_slot_names:
            return name
        new_name = next(self.global_gen)
        self.global_taken.add(new_name)
        return new_name

    def allocate_module_names(self) -> None:
        """Hand out the globally-unique short names, hottest names first."""
        frequency: Counter = Counter()
        for module in self.facts.values():
            for qname in module.bare_qnames:
                frequency[(module.name, qname)] += module.qname_counts[qname]
            for record in module.aliases:
                if record.target and not record.module_ref and not record.asname:
                    for qname in record.binding_qnames:
                        frequency[(record.target, record.source_name)] += module.qname_counts.get(qname, 0)
        for key in sorted(frequency, key=lambda k: (-frequency[k], k[0], k[1])):
            self.decide(*key)

    # -- per-module finalization ------------------------------------------

    def finalize_module(self, facts: _ModuleFacts) -> tuple[dict[str, str], dict[tuple[int, int], str]]:
        decisions: dict[str, str] = {}
        edits: dict[tuple[int, int], str] = {}

        slot_names = set(facts.bare_qnames) | set(facts.bindings_by_name)
        module_finals: set[str] = set()
        for name in sorted(slot_names):
            new_name = self.decide(facts.name, name)
            module_finals.add(new_name)
            if new_name != name:
                decisions[name] = new_name

        for name, records in facts.bindings_by_name.items():
            follows = _follow_binding(facts, name) is not None
            final = self.decide(facts.name, name) if follows else name
            for record in records:
                if record.target and not record.module_ref:
                    source_new = self.decide(record.target, record.source_name)
                    if source_new != record.source_name:
                        edits[(record.node_id, record.index)] = source_new
                for qname in record.binding_qnames:
                    if qname != name and final != name:
                        decisions[qname] = final

        local_taken = set(facts.identifiers) | module_finals | set(RESERVED)
        local_gen = _short_names(local_taken)
        for qname in sorted(facts.local_qnames, key=lambda q: (-facts.qname_counts[q], q)):
            name = qname.rsplit(".", 1)[-1]
            if (
                qname in facts.ambiguous_qnames
                or name in facts.empty_names
                or name in facts.keep_names
                or name in self.frozen_kwargs
                or _protected(name)
                or name in RESERVED
            ):
                continue
            new_name = next(local_gen)
            local_taken.add(new_name)
            decisions[qname] = new_name
        return decisions, edits


# ---------------------------------------------------------------------------
# Rewriting
# ---------------------------------------------------------------------------


class _Rewriter(cst.CSTTransformer):
    """Apply a qname-keyed decision map plus import-source edits."""

    METADATA_DEPENDENCIES = (ScopeProvider,)

    def __init__(
        self,
        decisions: dict[str, str],
        alias_edits: dict[tuple[int, int], str],
    ) -> None:
        self._decisions = decisions
        self._alias_edits = alias_edits
        self._import_depth = 0

    def visit_Import(self, node: cst.Import) -> bool:
        self._import_depth += 1
        return True

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import:
        self._import_depth -= 1
        return updated_node

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        self._import_depth += 1
        return True

    def leave_ImportFrom(self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom) -> cst.ImportFrom:
        self._import_depth -= 1
        if isinstance(original_node.names, cst.ImportStar) or isinstance(updated_node.names, cst.ImportStar):
            return updated_node
        new_names = []
        changed = False
        for index, alias in enumerate(updated_node.names):
            if isinstance(alias, cst.ImportAlias):
                new_source = self._alias_edits.get((id(original_node), index))
                if new_source and isinstance(alias.name, cst.Name) and alias.name.value != new_source:
                    alias = alias.with_changes(name=alias.name.with_changes(value=new_source))
                    changed = True
            new_names.append(alias)
        if changed:
            return updated_node.with_changes(names=tuple(new_names))
        return updated_node

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        if self._import_depth:
            return updated_node
        try:
            scope = self.get_metadata(ScopeProvider, original_node)
        except KeyError:
            return updated_node
        try:
            qnames = scope.get_qualified_names_for(original_node)
        except KeyError:
            return updated_node
        if len(qnames) != 1:
            return updated_node
        new_name = self._decisions.get(next(iter(qnames)).name)
        if new_name is not None and new_name != original_node.value:
            return updated_node.with_changes(value=new_name)
        return updated_node

    def _rewrite_declaration(
        self,
        original_node: Union[cst.Global, cst.Nonlocal],
        updated_node: Union[cst.Global, cst.Nonlocal],
    ) -> Union[cst.Global, cst.Nonlocal]:
        try:
            scope = self.get_metadata(ScopeProvider, original_node)
        except KeyError:
            scope = None
        new_items = []
        changed = False
        for original_item, updated_item in zip(original_node.names, updated_node.names):
            original_name = original_item.name if isinstance(original_item, cst.NameItem) else original_item
            updated_name = updated_item.name if isinstance(updated_item, cst.NameItem) else updated_item
            new_value = None
            if scope is not None:
                try:
                    qnames = scope.get_qualified_names_for(original_name)
                except KeyError:
                    qnames = set()
                if len(qnames) == 1:
                    new_value = self._decisions.get(next(iter(qnames)).name)
            if new_value is None and isinstance(original_node, cst.Global):
                # `global x` always refers to the module-level slot
                new_value = self._decisions.get(updated_name.value)
            if new_value and new_value != updated_name.value:
                updated_name = updated_name.with_changes(value=new_value)
                if isinstance(updated_item, cst.NameItem):
                    updated_item = updated_item.with_changes(name=updated_name)
                else:
                    updated_item = updated_name
                changed = True
            new_items.append(updated_item)
        if changed:
            return updated_node.with_changes(names=new_items)
        return updated_node

    def leave_Global(self, original_node: cst.Global, updated_node: cst.Global) -> cst.Global:
        return self._rewrite_declaration(original_node, updated_node)

    def leave_Nonlocal(self, original_node: cst.Nonlocal, updated_node: cst.Nonlocal) -> cst.Nonlocal:
        return self._rewrite_declaration(original_node, updated_node)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def shorten_names(
    path: Union[str, Path],
    backup: bool = True,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> bool:
    """Shorten names in ``path`` (a ``.py`` file or a package directory).

    Returns ``True`` when at least one file was (or would be) changed.  When
    ``backup`` is true, each rewritten file is first copied to
    ``<name>.py.bak``.  ``dry_run`` performs the full analysis and rewrite but
    does not write anything.  In package mode, files that fail to parse are
    skipped with a warning (their importers keep the old names, which is
    always safe); in single-file mode a :class:`SyntaxError` is raised, as
    before.
    """
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such file or directory: {target}")
    files = _discover(target)
    if not files:
        return False
    root = target if target.is_dir() else target.parent
    root_is_package = (root / "__init__.py").is_file()
    registry, primary = _build_registry(files, root, root_is_package)

    facts: dict[str, _ModuleFacts] = {}
    skipped = 0
    for file_path in files:
        module_name = primary.get(file_path)
        if module_name is None:
            skipped += 1
            continue
        try:
            facts[module_name] = _analyze(file_path, module_name, registry, primary)
        except Exception as exc:  # parse or read failure
            if target.is_file():
                raise SyntaxError(f"Failed to parse {file_path}: {exc}") from exc
            skipped += 1
            if verbose:
                print(f"skipped {file_path}: {exc}", file=sys.stderr)
    if not facts:
        return False

    plan = _Plan(facts)
    plan.allocate_module_names()

    changed_any = False
    for module_name in sorted(facts):
        module = facts[module_name]
        decisions, edits = plan.finalize_module(module)
        if not decisions and not edits:
            if verbose:
                print(f"{module.path}: nothing to rename")
            continue
        rewriter = _Rewriter(decisions, edits)
        new_source = module.wrapper.visit(rewriter).code
        if new_source == module.source:
            if verbose:
                print(f"{module.path}: nothing to rename")
            continue
        changed_any = True
        if verbose:
            print(f"{module.path}: {len(decisions)} names shortened, {len(edits)} import statement(s) updated")
        if not dry_run:
            if backup:
                backup_path = module.path.with_name(module.path.name + ".bak")
                backup_path.write_text(module.source, encoding="utf-8")
            module.path.write_text(new_source, encoding="utf-8")
    if verbose and skipped:
        print(f"({skipped} file(s) skipped)", file=sys.stderr)
    return changed_any


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Shorten identifier names in a Python file or package.")
    parser.add_argument("path", help="a .py file or a package directory")
    parser.add_argument("--no-backup", action="store_true", help="do not write .bak files")
    parser.add_argument("--dry-run", action="store_true", help="analyze only, write nothing")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress per-file output")
    args = parser.parse_args(argv)
    try:
        changed = shorten_names(
            args.path,
            backup=not args.no_backup,
            dry_run=args.dry_run,
            verbose=not args.quiet,
        )
    except (SyntaxError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.quiet:
        print("done." if changed else "nothing to change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
