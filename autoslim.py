#!/data/data/com.termux/files/usr/bin/env python
"""AutoSlim (libcst edition) -- the whole project in one file.

AutoSlim traces the import graph that starts at one or more *entry* files, analyses
every reachable module with ``libcst`` and then re-emits a much smaller, self
contained copy of the project:

* ``FileLevelCodeGenerator``  -- copy every traced file, flatten local imports.
* ``SegmentCodeGenerator``    -- additionally drop dead code (unused functions,
  classes, methods and imports) and optionally *merge* an inheritance chain into
  the leaf class so the base classes disappear completely.

Why libcst instead of ``ast`` + ``astor``?

===========================  =========================================================
``ast`` + ``astor``           ``libcst``
===========================  =========================================================
loses comments, formatting    concrete syntax tree: comments, blank lines, quotes and
                              indentation survive untouched
needs a code generator that   ``module.code`` -- no third party printer required
re-invents layout
mutating the tree in place    immutable nodes + ``CSTTransformer`` passes, so every
                              rewrite is a pure function
no metadata                   ``MetadataWrapper`` gives parents, positions, scopes and
                              qualified names for free
===========================  =========================================================

Quick start
-----------
::

    from autoslim import AutoSlim

    (
        AutoSlim("pkg/train.py", "out/")  # entry file(s) + output dir
        .mode(AutoSlim.SegmentLevel)  # segment level slimming
        .merge_class(AutoSlim.Eliminate)  # flatten inheritance chains
        .generate()
    )

Run ``python autoslim.py demo`` for a complete, self checking example (it builds a
tiny package, slims it two different ways and executes the result), or
``python autoslim.py slim ENTRY... -o OUT`` to slim your own code.

Layout of this file
-------------------
1.  utilities                     6.  rewriting primitives (``Rewriter`` & friends)
2.  entries                       7.  class merging
3.  endpoints                     8.  code generators
4.  symbol table / analysis       9.  ``AutoSlim`` facade
5.  import graph ``Parser``      10.  CLI + demo

Where the old modules went
--------------------------
===================  =================================================  =============
original file        what it became                                     section
===================  =================================================  =============
``utils.py``         ``cd`` (+ path/module helpers)                     1
``entry.py``         ``Entry``, ``FileEntry``, ``StringEntry`` (now      2
                     really implemented), ``SegmentEntry``, ``ClassEntry``
``endpoint.py``      ``EndPoint`` and friends, ``EndPointManager``       3
``parse.py``         ``CSTFileParser`` (was ``DefaultASTParser``),       4, 5
                     ``_ImportNode``/``_DefNode``/``_CallNode``, ``Parser``
``codegen.py``       ``Rewriter``, ``ClassMerging``, ``CodeGenerator``,  6, 7, 8
                     ``FileLevelCodeGenerator``, ``SegmentCodeGenerator``
                     -- and the two empty stubs ``UnusedRemoval`` /
                     ``InLine`` are now real passes (``PruneUnusedDefs``,
                     ``PruneUnusedImports``, ``ClassMerging``)
``scripts.py``       ``AutoSlim``                                       9
``__init__.py``      ``__all__`` at the top of this file                -
===================  =================================================  =============

Deliberate behaviour changes
----------------------------
* ``astor.to_source`` is gone: the generated file is the original source with only the
  rewritten nodes changed, so comments, quoting and blank lines survive.
* ``LocalEndPoint`` used to answer ``True`` for files *outside* the project (and used
  the string based ``os.path.commonprefix``).  It now answers ``True`` for files inside
  it, which is what "trace my own code" means.
* ``EndPointManager`` combines endpoints with AND by default (``CombinePolicy.ALL``), so
  ``ExceptEndPoint`` can actually veto something; ``CombinePolicy.ANY`` reproduces the
  old OR behaviour (and keeps the old incompatibility check).
* ``REMOVE_NODE`` is ``libcst.RemovalSentinel.REMOVE`` instead of ``None``; a rewrite
  hook returning ``None`` now means "leave the node alone".
* ``ClassMerging._rewrite_super`` was a ``pass``; ``SuperRewriter`` implements it, so an
  override that called ``super().m()`` keeps calling the inlined base implementation
  (renamed ``_slim_<Class>_m``).
* Merging copies the module level imports/constants/functions the inlined code needs,
  which the old version did not -- the generated code is runnable, not just smaller.
* Dead code removal iterates to a fixpoint: dropping a class frees the imports that kept
  another file alive, which frees more dead code, and so on.
* Decorated definitions are kept unless you pass ``prune_decorated=True``: a decorator
  runs at import time and often registers the function somewhere.
"""

from __future__ import annotations

import argparse
import builtins
import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from abc import ABCMeta, abstractmethod
from collections import OrderedDict, defaultdict
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    Any,
)

import libcst as cst
from libcst.metadata import MetadataWrapper, ParentNodeProvider, PositionProvider
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


__all__ = [
    "CODEGEN_PREFIX",
    "REMOVE_NODE",
    "AutoSlim",
    "BuiltinEndPoint",
    "CSTFileParser",
    "ClassEntry",
    "ClassMerging",
    "CodeGenerator",
    "CombinePolicy",
    "EndPoint",
    "EndPointManager",
    "ExceptEndPoint",
    "FileEntry",
    "FileLevelCodeGenerator",
    "FileReport",
    "FlattenImports",
    "IncludeEndPoint",
    "LocalEndPoint",
    "MergeImportStatements",
    "MergePlan",
    "Parser",
    "PruneUnusedDefs",
    "PruneUnusedImports",
    "RelativizeImports",
    "RenameRefs",
    "Rewriter",
    "SegmentCodeGenerator",
    "SegmentEntry",
    "StringEntry",
    "SuperRewriter",
    "TidyModule",
    "add_codegen_header",
    "annotate_docstring",
    "apply_transform",
    "assigned_names",
    "binding_names",
    "bound_names_of",
    # parity helpers from the original project
    "cd",
    # CST toolbox (handy inside a ``custom_rewriter`` hook)
    "chain_name",
    "chain_root",
    "chain_root_str",
    "chain_tail",
    "collect_chains",
    "collect_names",
    "collect_string_identifiers",
    "free_names",
    "get_file_name",
    "is_file_exist",
    "makedirs",
    "parse_source",
    "run_demo",
    "scan_module",
    "write_demo_package",
]

# ======================================================================================
# 1. utilities
# ======================================================================================

#: Header written on top of every generated file (parity with the original project).
CODEGEN_PREFIX = "# Auto Generated by AutoSlim (libcst edition)"

#: Prefix used for the private helpers created when an inheritance chain is merged.
SLIM_PREFIX = "_slim_"

#: Sentinel returned by a rewrite hook to delete a node (the original used ``None``).
REMOVE_NODE = cst.RemovalSentinel.REMOVE

#: Kept for parity with the original ``parse._IMPORT_CACHE``: modules that are treated
#: as "installed dependency, do not trace into" without hitting the filesystem.
_IMPORT_CACHE = ["torch", "torch.nn"]

_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")

#: stdlib modules whose *import* is the point (they are never dropped when unused)
SIDE_EFFECT_MODULES = {
    "readline",
    "site",
    "this",
    "antigravity",
    "idlelib",
    "__future__",
}


@contextmanager
def cd(target: str | Path) -> Generator[None, None, None]:
    """``with cd(path):`` -- temporarily change the working directory."""
    prev = Path.cwd()
    os.chdir(Path(target).expanduser())
    try:
        yield
    finally:
        os.chdir(prev)


def makedirs(path: str | Path) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return str(path)


def is_file_exist(dir: str | Path, filename: str) -> bool:
    return (Path(dir) / filename).exists()


def get_file_name(file_path: str | Path) -> str:
    """``a/b/models.py`` -> ``models`` (parity with the original helper)."""
    return ".".join(Path(file_path).name.split(".")[:-1])


def common_root(paths: Iterable[str | Path]) -> Path:
    """Nearest common *directory* of ``paths`` (path aware, unlike ``commonprefix``)."""
    parts = [Path(p).resolve().parts for p in paths]
    parts = [p for p in parts if p]
    if not parts:
        return Path.cwd()
    shared: list[str] = []
    for chunk in zip(*parts):
        if len(set(chunk)) != 1:
            break
        shared.append(chunk[0])
    root = Path(*shared) if shared else Path(parts[0][0])
    return root if root.is_dir() else root.parent


def package_root(file_path: str | Path) -> Path:
    """First ancestor directory that is *not* a package (has no ``__init__.py``)."""
    p = Path(file_path).resolve()
    d = p.parent if p.is_file() or p.suffix else p
    while (d / "__init__.py").is_file() and d != d.parent:
        d = d.parent
    return d


def dotted_module_name(file_path: str | Path) -> str:
    """``/src/pkg/models.py`` -> ``pkg.models``; ``/src/pkg/__init__.py`` -> ``pkg``."""
    p = Path(file_path).resolve()
    try:
        rel = p.relative_to(package_root(p))
    except ValueError:  # pragma: no cover - defensive
        rel = Path(p.name)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def package_of(file_path: str | Path) -> str:
    """Dotted name of the package that owns ``file_path`` (``""`` for top level)."""
    dotted = dotted_module_name(file_path)
    if Path(file_path).name == "__init__.py":
        return dotted
    return dotted.rsplit(".", 1)[0] if "." in dotted else ""


def resolve_dotted(file_path: str | Path, level: int, module_name: str | None) -> str:
    """Resolve a (possibly relative) import to an absolute dotted module name.

    ``level`` is the number of leading dots: 0 means the module is already absolute,
    1 means "this package", 2 means "the parent package", ...
    """
    if level <= 0:
        return module_name or ""
    pkg = package_of(file_path)
    parts = pkg.split(".") if pkg else []
    cut = level - 1
    if cut:
        parts = parts[: len(parts) - cut] if cut <= len(parts) else []
    if module_name:
        parts = parts + module_name.split(".")
    return ".".join(parts)


def parse_source(code: str, origin: str = "<string>") -> cst.Module:
    """Parse ``code`` into a :class:`libcst.Module`, raising a readable error."""
    try:
        return cst.parse_module(code)
    except cst.ParserSyntaxError as exc:  # pragma: no cover - defensive
        raise SyntaxError(f"Cannot parse {origin}: {exc}") from exc


def add_codegen_header(module: cst.Module, text: str = CODEGEN_PREFIX) -> cst.Module:
    """Prepend ``text`` as a comment to ``module`` without touching anything else."""
    for line in module.header:
        if line.comment is not None and text.splitlines()[0] in line.comment.value:
            return module  # already stamped
    stamp = cst.EmptyLine(indent=False, comment=cst.Comment(text.splitlines()[0]))
    return module.with_changes(header=[stamp, *module.header])


# --------------------------------------------------------------------------------------
# small CST helpers
# --------------------------------------------------------------------------------------


def _asname_str(asname: cst.AsName) -> str:
    return asname.name.value if isinstance(asname.name, cst.Name) else ""


def chain_name(node: cst.CSTNode | None) -> str | None:
    """Render an expression as the dotted/call chain the original tracer produced.

    ``foo.bar`` -> ``"foo.bar"``, ``foo.bar()`` -> ``"foo.bar()"``,
    ``self.x[i]`` -> ``"self.x[i]"``.  Returns ``None`` for exotic nodes.
    """
    if node is None:
        return None
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = chain_name(node.value)
        return f"{base}.{node.attr.value}" if base is not None else None
    if isinstance(node, cst.Call):
        base = chain_name(node.func)
        return f"{base}()" if base is not None else None
    if isinstance(node, cst.Subscript):
        base = chain_name(node.value)
        if base is None:
            return None
        bits: list[str] = []
        for element in node.slice:
            sl = element.slice
            if isinstance(sl, cst.Index):
                bits.append(chain_name(sl.value) or "?")
            elif isinstance(sl, cst.Slice):
                bits.append(":")
            else:  # pragma: no cover - defensive
                bits.append("?")
        return f"{base}[{', '.join(bits)}]"
    return None


_CHAIN_SUFFIX_RE = re.compile(r"(?:\(\)|\[[^\[\]]*\])+$")


def chain_tail(chain: str) -> str | None:
    """Last identifier of a rendered chain: ``"Helper().describe()"`` -> ``"describe"``.

    Trailing ``()`` / ``[...]`` suffixes are stripped first, so calls and subscripts in
    the middle of a chain do not confuse the lookup.
    """
    stripped = _CHAIN_SUFFIX_RE.sub("", chain or "")
    if "." not in stripped:
        return None
    return stripped.rsplit(".", 1)[-1] or None


def chain_root_str(chain: str) -> str | None:
    """First identifier of a rendered chain: ``"Helper().describe()"`` -> ``"Helper"``."""
    match = re.match(r"[A-Za-z_]\w*", chain or "")
    return match.group(0) if match else None


def chain_root(node: cst.CSTNode | None) -> str | None:
    """Leftmost ``Name`` of an attribute/call chain (``a.b().c`` -> ``"a"``)."""
    while True:
        if isinstance(node, cst.Name):
            return node.value
        if isinstance(node, cst.Attribute):
            node = node.value
        elif isinstance(node, cst.Call):
            node = node.func
        elif isinstance(node, cst.Subscript):
            node = node.value
        else:
            return None


class _NameCollector(cst.CSTVisitor):
    """Collects every name a subtree *uses*.

    Binding positions are skipped: a ``def``/``class`` name, a parameter, an assignment
    target or an import alias is not a use of a global, and counting it as one would
    keep dead code alive (an inlined method called ``dead_helper`` must not resurrect a
    module level ``dead_helper``).  Attribute tails (``self.forward`` -> ``forward``)
    *are* collected, which keeps the result conservative.
    """

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.chains: set[str] = set()
        self.strings: set[str] = set()
        self._skip: set[int] = set()

    def _bind(self, node: cst.CSTNode) -> None:
        if isinstance(node, cst.Name):
            self._skip.add(id(node))
        elif isinstance(node, (cst.Tuple, cst.List)):
            for element in node.elements:
                self._bind(element.value)
        elif isinstance(node, cst.StarredElement):
            self._bind(node.value)

    def visit_Name(self, node: cst.Name) -> bool | None:
        if id(node) not in self._skip:
            self.names.add(node.value)
        return False  # nothing interesting below a Name

    def visit_Attribute(self, node: cst.Attribute) -> bool | None:
        chain = chain_name(node)
        if chain:
            self.chains.add(chain)
            self.names.add(chain.split(".")[-1].split("(")[0].split("[")[0])
        return True

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        self._skip.add(id(node.name))
        return True

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        self._skip.add(id(node.name))
        return True

    def visit_Param(self, node: cst.Param) -> bool | None:
        self._skip.add(id(node.name))
        return True

    def visit_Assign(self, node: cst.Assign) -> bool | None:
        for target in node.targets:
            self._bind(target.target)
        return True

    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool | None:
        self._bind(node.target)
        return True

    def visit_AugAssign(self, node: cst.AugAssign) -> bool | None:
        self._bind(node.target)
        return True

    def visit_For(self, node: cst.For) -> bool | None:
        self._bind(node.target)
        return True

    def visit_CompFor(self, node: cst.CompFor) -> bool | None:
        self._bind(node.target)
        return True

    def visit_WithItem(self, node: cst.WithItem) -> bool | None:
        if node.asname is not None:
            self._bind(node.asname.name)
        return True

    def visit_ExceptHandler(self, node: cst.ExceptHandler) -> bool | None:
        if node.name is not None:
            self._bind(node.name)
        return True

    def visit_NamedExpr(self, node: cst.NamedExpr) -> bool | None:
        self._bind(node.target)
        return True

    def visit_Import(self, node: cst.Import) -> bool | None:
        return False  # an import binds names, it does not use them

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool | None:
        return False

    def visit_SimpleString(self, node: cst.SimpleString) -> bool | None:
        self.strings.update(_IDENTIFIER_RE.findall(node.value))
        return False

    def visit_FormattedString(self, node: cst.FormattedString) -> bool | None:
        return True

    def visit_ConcatenatedString(self, node: cst.ConcatenatedString) -> bool | None:
        return True


def collect_names(node: cst.CSTNode) -> set[str]:
    """Every ``Name`` (plus attribute tails) inside ``node`` -- deliberately lax."""
    collector = _NameCollector()
    node.visit(collector)
    return collector.names


def collect_chains(node: cst.CSTNode) -> set[str]:
    collector = _NameCollector()
    node.visit(collector)
    return collector.chains


def collect_string_identifiers(node: cst.CSTNode) -> set[str]:
    """Identifiers mentioned inside string literals (type annotations in strings...)."""
    collector = _NameCollector()
    node.visit(collector)
    return collector.strings


def is_docstring(stmt: cst.BaseStatement) -> str | None:
    """Return the literal of ``stmt`` when it is a bare docstring expression."""
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    small = stmt.body[0]
    if not isinstance(small, cst.Expr):
        return None
    value = small.value
    if isinstance(value, (cst.SimpleString, cst.ConcatenatedString, cst.FormattedString)):
        text = getattr(value, "value", None)
        if isinstance(text, str) and text[:1] in ("'", '"'):
            return text
    return None


def statement_name(stmt: cst.BaseStatement) -> str | None:
    """Name bound by a top level statement, if any."""
    if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)):
        return stmt.name.value
    if isinstance(stmt, cst.SimpleStatementLine):
        names: list[str] = []
        for small in stmt.body:
            if isinstance(small, cst.Assign):
                names += [t.target.value for t in small.targets if isinstance(t.target, cst.Name)]
            elif isinstance(small, cst.AnnAssign) and isinstance(small.target, cst.Name):
                names.append(small.target.value)
        return names[0] if len(names) == 1 else None
    return None


def binding_names(node: cst.CSTNode) -> set[str]:
    """Names bound by an assignment *target* pattern (``a``, ``(a, b)``, ``[a]``, ``*a``)."""
    if isinstance(node, cst.Name):
        return {node.value}
    if isinstance(node, (cst.Tuple, cst.List)):
        found: set[str] = set()
        for element in node.elements:
            found |= binding_names(element.value)
        return found
    if isinstance(node, cst.StarredElement):
        return binding_names(node.value)
    return set()  # `obj.attr = x` / `obj[i] = x` bind nothing at module scope


def bound_names_of(stmt: cst.BaseStatement) -> set[str]:
    """Names a module level statement binds (imports, assignments, defs)."""
    names: set[str] = set()
    smalls = stmt.body if isinstance(stmt, cst.SimpleStatementLine) else [stmt]
    for small in smalls:
        if isinstance(small, (cst.Import, cst.ImportFrom)):
            if isinstance(small.names, cst.ImportStar):
                names.add("*")
                continue
            for alias in small.names:
                if alias.asname is not None:
                    names.add(_asname_str(alias.asname))
                else:
                    source = chain_name(alias.name) or ""
                    names.add(source.split(".")[0])
        elif isinstance(small, cst.Assign):
            for target in small.targets:
                names |= binding_names(target.target)
        elif isinstance(small, cst.AnnAssign):
            names |= binding_names(small.target)
    if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)):
        names.add(stmt.name.value)
    return names


def contains_node_types(node: cst.CSTNode, types: tuple[type, ...]) -> bool:
    """Cheap "does this subtree contain one of ``types``" check."""

    class _Finder(cst.CSTVisitor):
        def __init__(self) -> None:
            self.found = False

        def on_visit(self, node: cst.CSTNode) -> bool:
            if isinstance(node, types):
                self.found = True
                return False
            return super().on_visit(node)

    finder = _Finder()
    node.visit(finder)
    return finder.found


def assigned_names(node: cst.CSTNode) -> set[str]:
    """Names bound inside ``node`` (params, targets, imports, walrus, ...)."""

    class _Binder(cst.CSTVisitor):
        def __init__(self) -> None:
            self.bound: set[str] = set()

        def visit_Param(self, node: cst.Param) -> bool | None:
            self.bound.add(node.name.value)
            return True

        def visit_AssignTarget(self, node: cst.AssignTarget) -> bool | None:
            self._pattern(node.target)
            return True

        def visit_AnnAssign(self, node: cst.AnnAssign) -> bool | None:
            self._pattern(node.target)
            return True

        def visit_AugAssign(self, node: cst.AugAssign) -> bool | None:
            self._pattern(node.target)
            return True

        def visit_For(self, node: cst.For) -> bool | None:
            self._pattern(node.target)
            return True

        def visit_CompFor(self, node: cst.CompFor) -> bool | None:
            self._pattern(node.target)
            return True

        def visit_WithItem(self, node: cst.WithItem) -> bool | None:
            if node.asname is not None:
                self._pattern(node.asname.name)
            return True

        def visit_ExceptHandler(self, node: cst.ExceptHandler) -> bool | None:
            if node.name is not None:
                self._pattern(node.name)
            return True

        def visit_NamedExpr(self, node: cst.NamedExpr) -> bool | None:
            self._pattern(node.target)
            return True

        def visit_ImportAlias(self, node: cst.ImportAlias) -> bool | None:
            if node.asname is not None:
                self.bound.add(_asname_str(node.asname))
            elif isinstance(node.name, cst.Name):
                self.bound.add(node.name.value)
            else:
                self.bound.add(chain_root(node.name) or "")
            return True

        def _pattern(self, node: cst.CSTNode) -> None:
            if isinstance(node, cst.Name):
                self.bound.add(node.value)
            elif isinstance(node, (cst.Tuple, cst.List)):
                for element in node.elements:
                    self._pattern(element.value)
            elif isinstance(node, cst.StarredElement):
                self._pattern(node.value)

    binder = _Binder()
    node.visit(binder)
    binder.bound.discard("")
    return binder.bound


class _FreeNameCollector(cst.CSTVisitor):
    """Collects names that must exist at *module* scope for this subtree to work.

    Unlike :func:`collect_names` this ignores attribute tails (``self.depth`` does not
    need a global called ``depth``), keyword arguments and decorator attributes.
    """

    def __init__(self) -> None:
        self.names: set[str] = set()
        self._skip: set[int] = set()  # ids of Name children that are not references

    def _skip_child(self, node: cst.CSTNode | None) -> None:
        if isinstance(node, cst.Name):
            self._skip.add(id(node))

    def visit_Name(self, node: cst.Name) -> bool | None:
        if id(node) not in self._skip:
            self.names.add(node.value)
        return False

    def visit_Attribute(self, node: cst.Attribute) -> bool | None:
        self._skip_child(node.attr)  # `obj.attr` -- attr is not a global
        return True

    def visit_Arg(self, node: cst.Arg) -> bool | None:
        self._skip_child(node.keyword)  # `f(keyword=1)` -- keyword is not a global
        return True

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        self._skip_child(node.name)  # the name is a binding, not a reference
        return True

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        self._skip_child(node.name)
        return True

    def visit_Import(self, node: cst.Import) -> bool | None:
        return False  # an import needs nothing from module scope

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool | None:
        return False


def free_names(node: cst.CSTNode) -> set[str]:
    """Names ``node`` needs from module scope (minus its own locals and builtins)."""
    collector = _FreeNameCollector()
    node.visit(collector)
    return collector.names - assigned_names(node) - set(dir(builtins))


def apply_transform(node: cst.CSTNode, transformer: cst.CSTTransformer) -> cst.CSTNode | cst.RemovalSentinel:
    """Run ``transformer`` over an arbitrary node by borrowing an empty module."""
    if isinstance(node, cst.Module):
        return node.visit(transformer)
    if isinstance(node, (cst.SimpleStatementLine, cst.BaseCompoundStatement)):
        module = cst.Module(body=[node]).visit(transformer)
        return module.body[0] if module.body else cst.RemovalSentinel.REMOVE
    # expression / small statement: wrap in a throw-away statement
    if isinstance(node, cst.BaseExpression):
        stmt = cst.SimpleStatementLine(body=[cst.Expr(value=node)])
        module = cst.Module(body=[stmt]).visit(transformer)
        first = module.body[0]
        if isinstance(first, cst.SimpleStatementLine) and isinstance(first.body[0], cst.Expr):
            return first.body[0].value
    return node


def validate_module(module: cst.Module, origin: str) -> None:
    """Re-parse the generated code so a broken rewrite fails loudly."""
    try:
        cst.parse_module(module.code)
    except cst.ParserSyntaxError as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"AutoSlim generated invalid python for {origin}: {exc}") from exc


# ======================================================================================
# 2. entries
# ======================================================================================


class Entry(metaclass=ABCMeta):
    """Something AutoSlim can start tracing from."""

    entries: list[str]
    asts: list[cst.Module]

    @classmethod
    def build(cls, *args: Any) -> Entry:
        return cls(*args)

    @abstractmethod
    def convert_to_cst(self, entries: Sequence[Any]) -> list[cst.Module]:
        """Turn raw entries into parsed :class:`libcst.Module` objects."""

    @abstractmethod
    def get_cache(self) -> list[str]:
        """Absolute identifiers (file paths) of the entries."""

    def module_for(self, entry_id: str) -> cst.Module:
        for key, module in zip(self.get_cache(), self.asts):
            if key == entry_id:
                return module
        raise KeyError(entry_id)  # pragma: no cover - defensive

    def __iter__(self) -> Iterator[tuple[str, cst.Module]]:
        yield from zip(self.entries, self.asts)


def parse_file(filename: str | Path) -> cst.Module:
    """Read + parse one python file (the libcst counterpart of ``parse_file``)."""
    filename = str(filename)
    if not filename.endswith(".py"):
        raise ValueError(f"AutoSlim only slims python files, got: {filename}")
    with open(filename, "r", encoding="utf-8") as handle:
        source_code = handle.read()
    return parse_source(source_code, filename)


class FileEntry(Entry):
    """One or more python files on disk."""

    def __init__(self, entry_files: str | Path | Sequence[str | Path]):
        entries = [entry_files] if isinstance(entry_files, (str, Path)) else list(entry_files)
        self.entries: list[str] = [str(Path(i).resolve()) for i in entries]
        for entry in self.entries:
            if not Path(entry).is_file():
                raise FileNotFoundError(f"Entry file does not exist: {entry}")
        self.asts: list[cst.Module] = self.convert_to_cst(self.entries)

    def convert_to_cst(self, entries: Sequence[Any]) -> list[cst.Module]:
        return [parse_file(f) for f in entries]

    def get_cache(self) -> list[str]:
        return self.entries


class StringEntry(Entry):
    """Source code held in memory.

    Accepts plain strings or ``(filename, source)`` pairs.  The snippets are
    materialised in a temporary directory so that import resolution, relative
    imports and the output layout behave exactly like :class:`FileEntry`.
    """

    def __init__(
        self,
        entries: str | tuple[str, str] | Sequence[Any],
        roots: Sequence[str] | None = None,
    ):
        """``entries``: sources or ``(filename, source)`` pairs.

        ``roots`` optionally names the snippets tracing should start from; the rest is
        still reachable through imports (and slimmable).  Without it every snippet is an
        entry point, and entry points keep all of their definitions.
        """
        if isinstance(entries, (str, tuple)):
            entries = [entries]
        self.root = Path(tempfile.mkdtemp(prefix="autoslim-src-"))
        self.files: dict[str, str] = {}
        for index, entry in enumerate(entries):
            name, source = entry if isinstance(entry, tuple) else (f"snippet_{index}.py", entry)
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(source).strip("\n") + "\n", encoding="utf-8")
            self.files[name] = str(path.resolve())
        if roots is None:
            self.entries = list(self.files.values())
        else:
            missing = [r for r in roots if r not in self.files]
            if missing:
                raise KeyError(f"roots are not part of the snippets: {missing}")
            self.entries = [self.files[r] for r in roots]
        self.asts = [parse_file(f) for f in self.entries]

    @classmethod
    def build(cls, *args: Any) -> Entry:
        # modules discovered while tracing are real files in the temporary directory
        return FileEntry(*args)

    def convert_to_cst(self, entries: Sequence[Any]) -> list[cst.Module]:
        return [parse_file(f) for f in entries]

    def get_cache(self) -> list[str]:
        return self.entries

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __del__(self) -> None:  # pragma: no cover - best effort
        self.cleanup()


class SegmentEntry(Entry):
    """Base class for entries that are *pieces* of code rather than whole files."""


class ClassEntry(SegmentEntry):
    """Entry built from already parsed :class:`libcst.ClassDef` nodes."""

    def __init__(self, class_nodes: Sequence[cst.ClassDef]):
        self.entries = [f"<class {n.name.value}>" for n in class_nodes]
        self.nodes = list(class_nodes)
        self.asts = self.convert_to_cst(class_nodes)

    def convert_to_cst(self, entries: Sequence[Any]) -> list[cst.Module]:
        return [cst.Module(body=[node]) for node in entries]

    def get_cache(self) -> list[str]:
        return self.entries


# ======================================================================================
# 3. endpoints -- "is this import worth tracing?"
# ======================================================================================


class EndPoint(metaclass=ABCMeta):
    """Predicate over an import candidate.

    ``__target__`` lists the keyword arguments the manager should pass in; the
    available keys are ``file_path`` and ``module_name``.  Returning ``True``
    means "yes, trace into this module".
    """

    __target__: list[str] = ["file_path"]

    @abstractmethod
    def __call__(self, **kwargs: Any) -> bool:  # pragma: no cover - abstract
        pass


class BuiltinEndPoint(EndPoint):
    """Filters the standard library / builtin modules out (veto style endpoint)."""

    __target__ = ["module_name"]

    def __init__(self) -> None:
        self.builtins = set(dir(builtins)) | set(sys.builtin_module_names)

    def __call__(self, module_name: str = "", **_: Any) -> bool:
        root = (module_name or "").split(".")[0]
        return not (root in self.builtins or root in sys.stdlib_module_names)


class LocalEndPoint(EndPoint):
    """Accepts only files that live inside ``local_dir`` (i.e. your own project).

    The original implementation inverted this test (and used the string based
    ``os.path.commonprefix``); here ``True`` really means "local project file".
    """

    __target__ = ["file_path"]

    def __init__(self, local_dir: str | Path):
        self.local_dir = Path(local_dir).resolve()

    def __call__(self, file_path: str = "", **_: Any) -> bool:
        if not file_path:
            return False
        candidate = Path(file_path).resolve()
        return candidate == self.local_dir or self.local_dir in candidate.parents


class ExceptEndPoint(LocalEndPoint):
    """``LocalEndPoint`` with a module blacklist."""

    __target__ = ["file_path", "module_name"]

    def __init__(self, local_dir: str | Path, excepts: str | Sequence[str]):
        if isinstance(excepts, str):
            excepts = [excepts]
        self.excepts = list(excepts)
        super().__init__(local_dir)

    def __call__(self, file_path: str = "", module_name: str = "", **_: Any) -> bool:
        root = (module_name or "").split(".")[0]
        if module_name in self.excepts or root in self.excepts:
            return False
        return super().__call__(file_path)


class IncludeEndPoint(EndPoint):
    """Accepts an explicit list of module names (useful for installed packages)."""

    __target__ = ["module_name"]

    def __init__(self, modules: str | Sequence[str]):
        self.modules = [modules] if isinstance(modules, str) else list(modules)

    def __call__(self, module_name: str = "", **_: Any) -> bool:
        root = (module_name or "").split(".")[0]
        return any(module_name == m or root == m.split(".")[0] for m in self.modules)


class CombinePolicy(Enum):
    """How several endpoints are combined."""

    ALL = "all"  # every endpoint must accept  (default -- exclusions stay effective)
    ANY = "any"  # one endpoint is enough      (the original OR behaviour)


class EndPointManager:
    """Runs a list of endpoints against an import candidate."""

    #: pairs that contradict each other when combined with ``CombinePolicy.ANY``
    INCOMPATIBLE: list[tuple[type[EndPoint], type[EndPoint]]] = [(LocalEndPoint, ExceptEndPoint)]

    def __init__(
        self,
        endpoints: EndPoint | Sequence[EndPoint],
        policy: CombinePolicy = CombinePolicy.ALL,
    ):
        ep_list: list[EndPoint] = [endpoints] if isinstance(endpoints, EndPoint) else list(endpoints)
        self.policy = policy
        if policy is CombinePolicy.ANY:
            types = [type(e) for e in ep_list]
            for pair in EndPointManager.INCOMPATIBLE:
                if pair[0] in types and pair[1] in types:
                    raise ValueError(f"Incompatible endpoints for ANY policy: {pair}")
        self.endpoints = ep_list

    @classmethod
    def add_imcompatible_pair(cls, pair: tuple[type[EndPoint], type[EndPoint]]) -> None:
        assert len(pair) == 2
        cls.INCOMPATIBLE.append(pair)

    def check(self, local: Mapping[str, Any]) -> bool:
        if not self.endpoints:
            return True
        results = []
        for endpoint in self.endpoints:
            kwargs = {key: local.get(key, "") for key in endpoint.__target__}
            results.append(bool(endpoint(**kwargs)))
        return all(results) if self.policy is CombinePolicy.ALL else any(results)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        names = ", ".join(type(e).__name__ for e in self.endpoints)
        return f"EndPointManager([{names}], policy={self.policy.value})"


# ======================================================================================
# 4. symbol table -- one libcst visitor per file
# ======================================================================================


class _DefType(Enum):
    Function = 0
    Class = 1
    Method = 2


@dataclass
class _DefNode:
    """A function / class / method definition found in a file."""

    node: cst.FunctionDef | cst.ClassDef
    def_type: _DefType
    parent: str | None = None
    bases: list[str] | None = None
    metas: list[str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.node, (cst.FunctionDef, cst.ClassDef)):
            raise TypeError(f"_DefNode expects a FunctionDef/ClassDef, got {type(self.node)}")
        self.name = self.node.name.value
        if self.metas is None:
            self.metas = [chain_name(d.decorator) or "?" for d in self.node.decorators]
        if self.def_type is _DefType.Class and self.bases is None and isinstance(self.node, cst.ClassDef):
            self.bases = [b for a in self.node.bases if a.keyword is None and (b := chain_name(a.value))]

    def __repr__(self) -> str:
        return f"_DefNode(name={self.name}, bases={self.bases}, metas={self.metas})"


@dataclass
class _CallNode:
    """A call site, kept as the dotted chain the original tracer produced."""

    name: str
    trace_info: list[str] | None = None
    node: cst.Call | None = None


@dataclass
class _ImportNode:
    """One bound name produced by an ``import`` / ``from ... import`` statement."""

    node: cst.Import | cst.ImportFrom
    name: str  # local binding ("*" for star imports)
    module_name: str | None  # dotted module, None for `from . import x`
    asname: str | None = None
    level: int = 0
    imported_name: str | None = None  # source name for `from x import a as b`
    file_path: str | None = None  # resolved module file, when it is local
    is_target: bool = False
    dotted_module: str = ""  # absolute dotted module name

    # -- resolution -------------------------------------------------------------------
    def _candidate_files(self, owner_file: str) -> list[Path]:
        """Filesystem candidates for this import, without importing anything."""
        candidates: list[Path] = []
        if self.level:
            base = Path(owner_file).resolve().parent
            for _ in range(self.level - 1):
                base = base.parent
            rel = (self.module_name or "").replace(".", os.sep)
            candidates += [
                base / f"{rel}.py" if rel else base / "__init__.py",
                (base / rel / "__init__.py") if rel else base / "__init__.py",
            ]
        else:
            dotted = (self.module_name or "").replace(".", os.sep)
            roots = [
                package_root(owner_file),
                Path.cwd(),
                *[Path(p) for p in sys.path if p],
            ]
            for root in roots:
                if not root:
                    continue
                candidates += [root / f"{dotted}.py", root / dotted / "__init__.py"]
        seen, unique = set(), []
        for cand in candidates:
            key = str(cand)
            if key not in seen:
                seen.add(key)
                unique.append(cand)
        return unique

    def _importlib_lookup(self) -> str | None:
        """Fallback that actually imports the module (opt-in, may execute code)."""
        if not self.module_name:
            return None
        try:
            module = importlib.import_module(self.module_name)
        except BaseException:  # noqa: BLE001 - any failure means "not resolvable"
            return None
        file_path = getattr(module, "__file__", None)
        if file_path:
            return str(Path(file_path).resolve())
        search = getattr(module, "__path__", None)
        if search:
            init = Path(next(iter(search))) / "__init__.py"
            if init.is_file():
                return str(init.resolve())
        return None

    def resolve(self, endpoints: EndPointManager, owner_file: str, use_importlib: bool = False) -> str | None:
        """Fill in ``file_path`` / ``is_target``; return the traced file (if any)."""
        self.dotted_module = resolve_dotted(owner_file, self.level, self.module_name)
        for candidate in self._candidate_files(owner_file):
            if candidate.is_file():
                self.file_path = str(candidate.resolve())
                break
        if self.file_path is None and use_importlib and not self.level:
            self.file_path = self._importlib_lookup()
        if self.file_path is None:
            return None
        local = {"file_path": self.file_path, "module_name": self.dotted_module}
        if endpoints.check(local):
            self.is_target = True
            return self.file_path
        return None


class CSTFileParser(cst.CSTVisitor):
    """Analyses a single module: imports, defs, methods, calls, references.

    This is the libcst counterpart of the original ``DefaultASTParser``.  After
    construction it exposes:

    ``module``            the (metadata resolved) tree
    ``imports``           ``{local name: _ImportNode}``
    ``uncertain_imports`` star imports
    ``defs``              ``{name: _DefNode}`` for top level functions / classes
    ``classes``           ``{class name: _DefNode}``
    ``methods``           ``{class name: {method name: _DefNode}}``
    ``calls``             ``{dotted chain: _CallNode}``
    ``references``        every identifier used in the file
    ``chains``            every attribute chain used (``self.foo``, ``mod.attr`` ...)
    ``all_names``         the contents of a module level ``__all__``
    """

    METADATA_DEPENDENCIES = (ParentNodeProvider, PositionProvider)

    def __init__(
        self,
        module: cst.Module,
        endpoints: EndPointManager,
        file_name: str,
        resolve_installed_modules: bool = False,
    ):
        self.endpoints = endpoints
        self.file_name = str(Path(file_name).resolve())
        self.file_path = str(Path(self.file_name).parent)
        self.resolve_installed_modules = resolve_installed_modules

        self.imports: OrderedDict[str, _ImportNode] = OrderedDict()
        self.uncertain_imports: list[_ImportNode] = []
        self.defs: OrderedDict[str, _DefNode] = OrderedDict()
        self.classes: OrderedDict[str, _DefNode] = OrderedDict()
        self.methods: OrderedDict[str, OrderedDict[str, _DefNode]] = OrderedDict()
        self.calls: OrderedDict[str, _CallNode] = OrderedDict()
        self.all_names: list[str] = []
        self._to_merge_classes: OrderedDict[str, list[str]] = OrderedDict()

        self._class_stack: list[str] = []
        self.references: set[str] = set()
        self.chains: set[str] = set()
        self.strings: set[str] = set()

        wrapper = MetadataWrapper(cst.ensure_type(module, cst.Module))
        #: the metadata resolved tree -- always rewrite through this one
        self.module: cst.Module = wrapper.visit(self)

    # -- visitor ---------------------------------------------------------------------
    def visit_Import(self, node: cst.Import) -> bool | None:
        self._record_import(node)
        return False  # an import binding is not a *use* of a name

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool | None:
        self._record_import(node)
        return False

    def _record_import(self, node: cst.Import | cst.ImportFrom) -> None:
        is_import_from = isinstance(node, cst.ImportFrom)
        level = len(node.relative or ()) if is_import_from else 0
        if isinstance(node.names, cst.ImportStar):
            record = _ImportNode(node, "*", None, None, level)
            self.uncertain_imports.append(record)
            return
        module_name = chain_name(node.module) if is_import_from else None
        for alias in node.names:
            source = chain_name(alias.name) or "?"
            local = _asname_str(alias.asname) if alias.asname is not None else source.split(".")[0]
            record = _ImportNode(
                node=node,
                name=local,
                module_name=source if not is_import_from else module_name,
                asname=_asname_str(alias.asname) if alias.asname is not None else None,
                level=level,
                imported_name=source if is_import_from else None,
            )
            self.imports[local] = record

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        self._class_stack.append(node.name.value)
        bases = [b for a in node.bases if a.keyword is None and (b := chain_name(a.value))]
        metas = [m for a in node.bases if a.keyword is not None and (m := chain_name(a.value))]
        metas += [chain_name(d.decorator) or "?" for d in node.decorators]
        parent = self._class_stack[-2] if len(self._class_stack) > 1 else None
        record = _DefNode(node, _DefType.Class, parent=parent, bases=bases, metas=metas)
        self.classes[node.name.value] = record
        if parent is None:
            self.defs[node.name.value] = record
        table = self.methods.setdefault(node.name.value, OrderedDict())
        for stmt in node.body.body if isinstance(node.body, cst.IndentedBlock) else []:
            if isinstance(stmt, cst.FunctionDef):
                table[stmt.name.value] = _DefNode(stmt, _DefType.Method, parent=node.name.value)
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        if self._class_stack and self._class_stack[-1] == original_node.name.value:
            self._class_stack.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        if not self._class_stack:
            parent = self.get_metadata(ParentNodeProvider, node)
            if isinstance(parent, cst.Module):
                self.defs[node.name.value] = _DefNode(node, _DefType.Function)
        return True

    def visit_Call(self, node: cst.Call) -> bool | None:
        chain = chain_name(node.func)
        if chain:
            self.calls.setdefault(chain, _CallNode(chain, None, node))
        return True

    def visit_Assign(self, node: cst.Assign) -> bool | None:
        for target in node.targets:
            if isinstance(target.target, cst.Name) and target.target.value == "__all__":
                value = node.value
                if isinstance(value, (cst.List, cst.Tuple, cst.Set)):
                    for element in value.elements:
                        literal = getattr(element.value, "evaluated_value", None)
                        if isinstance(literal, str):
                            self.all_names.append(literal)
        return True

    def visit_Name(self, node: cst.Name) -> bool | None:
        self.references.add(node.value)
        return False

    def visit_Attribute(self, node: cst.Attribute) -> bool | None:
        chain = chain_name(node)
        if chain:
            self.chains.add(chain)
            self.references.add(node.attr.value)
        return True

    def visit_SimpleString(self, node: cst.SimpleString) -> bool | None:
        self.strings.update(_IDENTIFIER_RE.findall(node.value))
        return False

    # -- queries ---------------------------------------------------------------------
    def resolve_imports(self) -> list[str]:
        """Resolve every import against the endpoints; return traced file paths."""
        traced = []
        for record in list(self.imports.values()) + list(self.uncertain_imports):
            found = record.resolve(self.endpoints, self.file_name, self.resolve_installed_modules)
            if found:
                traced.append(found)
        return traced

    def get_import_path(self) -> list[str]:
        """Parity with the original API: paths of the local modules this file imports."""
        return [r.file_path for r in self.imports.values() if r.is_target and r.file_path]

    def get_target_import_names(self) -> list[str]:
        return [name for name, record in self.imports.items() if record.is_target]

    def get_target_merge_class(self) -> OrderedDict[str, list[str]]:
        """``{class name: [base names that come from another traced file]}``."""
        mergeable: OrderedDict[str, list[str]] = OrderedDict()
        for name, record in self.classes.items():
            if not record.bases:
                continue
            local_bases = [b for b in record.bases if b in self.imports and self.imports[b].is_target]
            record.bases = local_bases or None
            if local_bases:
                mergeable[name] = local_bases
        self._to_merge_classes = mergeable
        return mergeable

    def base_source(self, base_name: str) -> tuple[str, str] | None:
        """For an imported base class name: ``(file, class name in that file)``."""
        record = self.imports.get(base_name)
        if record is None or not record.is_target or not record.file_path:
            return None
        source_name = (record.imported_name or base_name).split(".")[-1]
        return record.file_path, source_name

    @property
    def _local_defs(self) -> OrderedDict[str, _DefNode]:
        """Compatibility view: top level defs followed by methods (original layout)."""
        merged: OrderedDict[str, _DefNode] = OrderedDict(self.defs)
        for table in self.methods.values():
            merged.update(table)
        return merged

    def line_of(self, node: cst.CSTNode) -> int:
        try:
            return self.get_metadata(PositionProvider, node).start.line
        except Exception:  # pragma: no cover - node not from this tree
            return -1

    def info(self) -> str:
        lines = [
            f"file      : {self.file_name}",
            "imports   : "
            + (
                ", ".join(
                    f"{n}->{r.dotted_module or r.module_name}{' (traced)' if r.is_target else ''}"
                    for n, r in self.imports.items()
                )
                or "-"
            ),
            "defs      : " + (", ".join(self.defs) or "-"),
            "classes   : " + (", ".join(f"{c}({','.join(r.bases or [])})" for c, r in self.classes.items()) or "-"),
            "methods   : " + (", ".join(f"{c}.{m}" for c, t in self.methods.items() for m in t) or "-"),
            "calls     : " + (", ".join(self.calls) or "-"),
            f"__all__   : {self.all_names or '-'}",
            f"uncertain : {[r.module_name for r in self.uncertain_imports] or '-'}",
        ]
        return "\n".join(lines)

    def print(self) -> None:
        print(self.info())


# --------------------------------------------------------------------------------------
# module level report (used after the tree has been rewritten)
# --------------------------------------------------------------------------------------


@dataclass
class FileReport:
    """A fresh, cheap scan of a (possibly rewritten) module."""

    module: cst.Module
    defs: OrderedDict[str, cst.BaseStatement] = field(default_factory=OrderedDict)
    classes: dict[str, cst.ClassDef] = field(default_factory=dict)
    methods: dict[str, OrderedDict[str, cst.FunctionDef]] = field(default_factory=dict)
    imports: dict[str, tuple[cst.BaseSmallStatement, str | None, int]] = field(default_factory=dict)
    references: set[str] = field(default_factory=set)
    chains: set[str] = field(default_factory=set)
    strings: set[str] = field(default_factory=set)
    annotation_strings: set[str] = field(default_factory=set)
    top_level_references: set[str] = field(default_factory=set)
    all_names: list[str] = field(default_factory=list)
    statements: list[cst.BaseStatement] = field(default_factory=list)


class _ReportScanner(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (ParentNodeProvider,)

    def __init__(self) -> None:
        self.report = FileReport(module=cst.Module(body=[]))
        self._class_stack: list[str] = []
        self._bindings: set[int] = set()  # ids of Name nodes that only bind a name

    def _mark_binding(self, node: cst.CSTNode) -> None:
        if isinstance(node, cst.Name):
            self._bindings.add(id(node))
        elif isinstance(node, (cst.Tuple, cst.List)):
            for element in node.elements:
                self._mark_binding(element.value)
        elif isinstance(node, cst.StarredElement):
            self._mark_binding(node.value)

    def visit_Assign(self, node: cst.Assign) -> bool | None:
        for target in node.targets:
            self._mark_binding(target.target)
        self._maybe_all(node)
        return True

    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool | None:
        self._mark_binding(node.target)
        return True

    def visit_AugAssign(self, node: cst.AugAssign) -> bool | None:
        self._mark_binding(node.target)
        return True

    def visit_Module(self, node: cst.Module) -> bool | None:
        self.report.module = node
        self.report.statements = list(node.body)
        return True

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        parent = self.get_metadata(ParentNodeProvider, node)
        if isinstance(parent, cst.Module):
            self.report.defs[node.name.value] = node
            self.report.classes[node.name.value] = node
            self.report.methods[node.name.value] = OrderedDict(
                (s.name.value, s)
                for s in node.body.body
                if isinstance(node.body, cst.IndentedBlock) and isinstance(s, cst.FunctionDef)
            )
            self._class_stack.append(node.name.value)
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        if self._class_stack and self._class_stack[-1] == original_node.name.value:
            self._class_stack.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        parent = self.get_metadata(ParentNodeProvider, node)
        if isinstance(parent, cst.Module):
            self.report.defs[node.name.value] = node
        return True

    def visit_Import(self, node: cst.Import) -> bool | None:
        self._record(node)
        return False  # otherwise every import would look "used" and survive

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool | None:
        self._record(node)
        return False

    def _record(self, node: cst.Import | cst.ImportFrom) -> None:
        module_name = chain_name(node.module) if isinstance(node, cst.ImportFrom) else None
        level = len(node.relative or ()) if isinstance(node, cst.ImportFrom) else 0
        if isinstance(node.names, cst.ImportStar):
            self.report.imports["*"] = (node, module_name, level)
            return
        for alias in node.names:
            source = chain_name(alias.name) or "?"
            local = _asname_str(alias.asname) if alias.asname is not None else source.split(".")[0]
            entry = (
                node,
                module_name if isinstance(node, cst.ImportFrom) else source,
                level,
            )
            self.report.imports[local] = entry

    def _maybe_all(self, node: cst.Assign) -> None:
        for target in node.targets:
            if isinstance(target.target, cst.Name) and target.target.value == "__all__":
                if isinstance(node.value, (cst.List, cst.Tuple, cst.Set)):
                    for element in node.value.elements:
                        literal = getattr(element.value, "evaluated_value", None)
                        if isinstance(literal, str):
                            self.report.all_names.append(literal)

    def visit_Name(self, node: cst.Name) -> bool | None:
        if id(node) not in self._bindings:
            self.report.references.add(node.value)
        return False

    def visit_Attribute(self, node: cst.Attribute) -> bool | None:
        chain = chain_name(node)
        if chain:
            self.report.chains.add(chain)
            self.report.references.add(node.attr.value)
        return True

    def visit_SimpleString(self, node: cst.SimpleString) -> bool | None:
        self.report.strings.update(_IDENTIFIER_RE.findall(node.value))
        return False

    def visit_Annotation(self, node: cst.Annotation) -> bool | None:
        # `def f() -> "Foo":` / `x: "pkg.Thing"` -- a string that really names an import
        collector = _StringCollector()
        node.visit(collector)
        self.report.annotation_strings |= collector.strings
        return True


class _StringCollector(cst.CSTVisitor):
    """Every identifier mentioned in a string literal of a subtree."""

    def __init__(self) -> None:
        self.strings: set[str] = set()

    def visit_SimpleString(self, node: cst.SimpleString) -> bool | None:
        self.strings.update(_IDENTIFIER_RE.findall(node.value))
        return False


def scan_module(module: cst.Module) -> FileReport:
    """Scan ``module`` and compute which names are used by *runnable* top level code."""
    scanner = _ReportScanner()
    scanner.report.module = MetadataWrapper(module).visit(scanner)
    report = scanner.report
    report.module = scanner.report.module
    for stmt in report.statements:
        if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)):
            continue  # reachable only through the keep-set closure
        if _is_import_only(stmt):
            continue  # `from x import y` must not make a local `y` reachable
        report.top_level_references |= collect_names(stmt)
    return report


def _is_import_only(stmt: cst.BaseStatement) -> bool:
    return isinstance(stmt, cst.SimpleStatementLine) and all(
        isinstance(small, (cst.Import, cst.ImportFrom)) for small in stmt.body
    )


# ======================================================================================
# 5. the import graph
# ======================================================================================


class Parser:
    """Walks the import graph that starts at ``entry`` and analyses every module.

    ``parser.get_parsers()`` returns ``{absolute file path: CSTFileParser}`` and
    ``parser.relations`` maps ``file -> [files it imports]``.
    """

    def __init__(
        self,
        entry: Entry,
        endpoints: EndPoint | Sequence[EndPoint] | None = None,
        parser_type: type[CSTFileParser] = CSTFileParser,
        resolve_installed_modules: bool = False,
        policy: CombinePolicy = CombinePolicy.ALL,
    ):
        self.entry = entry
        self.entry_files: list[str] = list(entry.get_cache())
        self.cache: set[str] = set(self.entry_files)
        self.parser_type = parser_type
        self.resolve_installed_modules = resolve_installed_modules
        if endpoints is None:
            endpoints = LocalEndPoint(common_root(self.entry_files))
        self.endpoints = EndPointManager(endpoints, policy=policy)

        self.ast_parsers: OrderedDict[str, CSTFileParser] = OrderedDict()
        self.relations: dict[str, list[str]] = defaultdict(list)
        #: ``file -> [files that import it]``
        self.imported_by: dict[str, list[str]] = defaultdict(list)
        self.parse()
        for file, targets in self.relations.items():
            for target in targets:
                if file not in self.imported_by[target]:
                    self.imported_by[target].append(file)

    # -- graph walk ------------------------------------------------------------------
    def _build_parsers(self, entry: Entry) -> dict[str, CSTFileParser]:
        parsers: dict[str, CSTFileParser] = {}
        for file, module in entry:
            if file in parsers:
                continue
            parsers[file] = self.parser_type(
                module,
                self.endpoints,
                file,
                resolve_installed_modules=self.resolve_installed_modules,
            )
        return parsers

    def parse(self) -> None:
        pending: list[Entry] = [self.entry]
        while pending:
            entry = pending.pop(0)
            parsers = self._build_parsers(entry)
            discovered: list[str] = []
            for file, file_parser in parsers.items():
                if file in self.ast_parsers:
                    continue
                self.ast_parsers[file] = file_parser
                traced = file_parser.resolve_imports()
                for path in traced:
                    if path not in self.relations[file]:
                        self.relations[file].append(path)
                for path in traced:
                    if path not in self.cache:
                        self.cache.add(path)
                        discovered.append(path)
            if discovered:
                pending.append(entry.build(discovered))

    # -- queries ---------------------------------------------------------------------
    def get_parsers(self) -> OrderedDict[str, CSTFileParser]:
        return self.ast_parsers

    @property
    def files(self) -> list[str]:
        return list(self.ast_parsers)

    def parser_of(self, file: str) -> CSTFileParser | None:
        return self.ast_parsers.get(str(Path(file).resolve()))

    def module_of(self, file: str) -> cst.Module | None:
        file_parser = self.parser_of(file)
        return file_parser.module if file_parser else None

    def dotted_index(self) -> dict[str, str]:
        """``{dotted module name: file}`` for every traced file."""
        return {dotted_module_name(f): f for f in self.ast_parsers}

    def file_for_dotted(self, dotted: str) -> str | None:
        return self.dotted_index().get(dotted)

    def names_imported_from(self, file: str) -> set[str]:
        """Names that *other* traced files import from ``file`` (source names)."""
        target = str(Path(file).resolve())
        names: set[str] = set()
        for other, other_parser in self.ast_parsers.items():
            if other == target:
                continue
            for record in other_parser.imports.values():
                if record.is_target and record.file_path == target:
                    names.add(record.imported_name or record.name)
            for record in other_parser.uncertain_imports:
                if record.is_target and record.file_path == target:
                    names.update(self.public_names(target))  # `from target import *`
        return names

    def public_names(self, file: str) -> set[str]:
        """Names a ``from file import *`` would pull in."""
        file_parser = self.parser_of(file)
        if file_parser is None:
            return set()
        if file_parser.all_names:
            return set(file_parser.all_names)
        return {name for name in file_parser.defs if not name.startswith("_")}

    def attribute_names(self) -> set[str]:
        """Every attribute tail used anywhere in the project.

        ``self.x``, ``cls.x`` and ``super().x`` are the interesting ones, but
        ``model.forward(...)`` / ``Config(3).describe()`` matter just as much, so the
        last component of *every* attribute chain is collected.  Deliberately lax:
        keeping a method that is never called is cheap, dropping one that is not.
        """
        tails: set[str] = set()
        for file_parser in self.ast_parsers.values():
            for chain in file_parser.chains:
                tail = chain_tail(chain)
                if tail:
                    tails.add(tail)
        return tails

    def info(self) -> str:
        blocks = [f"---------- {file} ----------\n{fp.info()}" for file, fp in self.ast_parsers.items()]
        header = (
            f"entries   : {self.entry_files}\n"
            f"traced    : {len(self.ast_parsers)} file(s)\n"
            f"endpoints : {self.endpoints!r}\n"
        )
        return header + "\n".join(blocks)

    def print(self) -> None:
        print(self.info())


# ======================================================================================
# 6. rewriting primitives
# ======================================================================================

Hook = Callable[[cst.CSTNode, cst.CSTNode], Any]


class Rewriter(cst.CSTTransformer):
    """Dispatch table driven transformer -- the libcst twin of the old ``Rewriter``.

    ``targets`` maps a node type name (``"FunctionDef"``, ``"ImportFrom"``, ...) to a
    callable ``(original_node, updated_node) -> node | REMOVE_NODE``.  ``pre_hooks``
    run before the children are visited, ``post_hooks`` after the node was rewritten::

        Rewriter(
            {"FunctionDef": drop_unused, "ImportFrom": flatten}, pre_hooks={"ClassDef": log_class}, post_hooks={"Module": tidy}
        )
    """

    def __init__(
        self,
        targets: Mapping[str, Hook] | None = None,
        pre_hooks: Mapping[str, Callable[[cst.CSTNode], Any]] | None = None,
        post_hooks: Mapping[str, Hook] | None = None,
    ):
        self.targets: dict[str, Hook] = dict(targets or {})
        self.pre_hooks = dict(pre_hooks or {})
        self.post_hooks: dict[str, Hook] = dict(post_hooks or {})

    def register(self, node_type: str, func: Hook) -> Rewriter:
        self.targets[node_type] = func
        return self

    def on_visit(self, node: cst.CSTNode) -> bool:
        hook = self.pre_hooks.get(type(node).__name__)
        if hook is not None and hook(node) is False:
            return False
        return super().on_visit(node)

    def on_leave(
        self, original_node: cst.CSTNode, updated_node: cst.CSTNode
    ) -> cst.CSTNode | cst.RemovalSentinel | cst.FlattenSentinel:
        name = type(original_node).__name__
        current: cst.CSTNode = updated_node
        handler = self.targets.get(name)
        if handler is not None:
            res = self._apply(handler, original_node, current)
            if isinstance(res, cst.RemovalSentinel):
                return res
            current = res
        hook = self.post_hooks.get(name)
        if hook is not None:
            return self._apply(hook, original_node, current)
        return current

    @staticmethod
    def _apply(func: Hook, original_node: cst.CSTNode, updated_node: cst.CSTNode) -> cst.CSTNode | cst.RemovalSentinel:
        """``None`` means "leave it alone", ``REMOVE_NODE`` means "delete it"."""
        result = func(original_node, updated_node)
        if result is None:
            return updated_node
        if result is REMOVE_NODE:
            return cst.RemovalSentinel.REMOVE
        return result


class RenameRefs(cst.CSTTransformer):
    """Renames every occurrence of ``mapping`` keys (used after a collision)."""

    def __init__(self, mapping: Mapping[str, str]):
        self.mapping = dict(mapping)

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        return updated_node.with_changes(value=self.mapping.get(updated_node.value, updated_node.value))

    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.Attribute:
        if updated_node.attr.value in self.mapping:
            return updated_node.with_changes(attr=cst.Name(self.mapping[updated_node.attr.value]))
        return updated_node


class SuperRewriter(cst.CSTTransformer):
    """Turns ``super().m(...)`` into ``self.<resolved>(...)``.

    ``resolver(method_name)`` returns the name that the base implementation now has
    inside the merged class, or ``None`` when the call should keep using ``super()``
    (the base class was *not* merged, so real inheritance still resolves it).
    """

    def __init__(self, resolver: Callable[[str], str | None], self_name: str = "self"):
        self.resolver = resolver
        self.self_name = self_name

    @staticmethod
    def _is_super(node: cst.CSTNode) -> bool:
        return isinstance(node, cst.Call) and isinstance(node.func, cst.Name) and node.func.value == "super"

    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.Attribute:
        if self._is_super(updated_node.value):
            replacement = self.resolver(updated_node.attr.value)
            if replacement is not None:
                return updated_node.with_changes(value=cst.Name(self.self_name), attr=cst.Name(replacement))
        return updated_node

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        func = updated_node.func
        if isinstance(func, cst.Attribute) and self._is_super(func.value):
            replacement = self.resolver(func.attr.value)
            if replacement is None:
                return updated_node
            args = [cst.Arg(value=cst.Name(self.self_name)), *updated_node.args]
            return updated_node.with_changes(
                func=cst.Attribute(value=cst.Name(self.self_name), attr=cst.Name(replacement)),
                args=args,
            )
        return updated_node


# ======================================================================================
# 7. class merging -- flatten an inheritance chain into the leaf class
# ======================================================================================


@dataclass
class _ClassInfo:
    """Everything AutoSlim needs to know about one class in a merge chain."""

    file: str
    name: str
    node: cst.ClassDef
    body: list[cst.BaseStatement]
    methods: OrderedDict[str, cst.FunctionDef]
    attributes: list[tuple[str, cst.BaseStatement]]
    docstring: cst.SimpleStatementLine | None
    other_statements: list[cst.BaseStatement]
    merged_bases: list[str]  # base names that point at another traced file
    kept_bases: list[cst.Arg]  # base args that stay real bases

    @property
    def bound_names(self) -> set[str]:
        names = set(self.methods) | {n for n, _ in self.attributes}
        return names


def _first_param_name(func: cst.FunctionDef) -> str:
    params = func.params
    for group in (
        params.params,
        [params.star_arg] if isinstance(params.star_arg, cst.Param) else [],
    ):
        for param in group:
            if isinstance(param, cst.Param):
                return param.name.value
    return "self"


def _strip_leading(stmt: cst.BaseStatement) -> cst.BaseStatement:
    if hasattr(stmt, "leading_lines"):
        return stmt.with_changes(leading_lines=[])
    return stmt


def annotate_docstring(stmt: cst.BaseStatement, origin: str) -> cst.BaseStatement:
    """Mark an inlined base docstring with a comment.

    A comment (rather than extra text inside the docstring) keeps ``__doc__`` clean and
    keeps the class name out of the string literals, which AutoSlim scans for
    "is this import still used?" hints.
    """
    if not isinstance(stmt, cst.SimpleStatementLine):
        return stmt
    note = cst.EmptyLine(comment=cst.Comment(f"# Inlined from {origin} by AutoSlim."))
    return stmt.with_changes(leading_lines=[note, *stmt.leading_lines])


def _blank_before(stmt: cst.BaseStatement) -> cst.BaseStatement:
    if hasattr(stmt, "leading_lines"):
        return stmt.with_changes(leading_lines=[cst.EmptyLine(indent=False)])
    return stmt


class ClassMerging:
    """Merge base classes into a leaf class.

    ``Eliminate``  walk the whole chain and inline every ancestor into the leaf, so
                   the generated class has no project base classes left.
    ``KeepOne``    inline the direct base only; its own parents stay real bases.

    ``super()`` calls keep working because the merged class now inherits from the
    *ancestor's* bases: an override that used to call ``super().m()`` is rewritten to
    ``self._slim_<Class>_m()``, i.e. to the inlined base implementation.
    """

    Eliminate = 1
    KeepOne = 2

    def __init__(
        self,
        parser: Parser,
        target_file: str,
        class_name: str,
        level: int = Eliminate,
        provided: set[str] | None = None,
    ):
        self.parser = parser
        self.parsers = parser.get_parsers()
        self.target_file = str(Path(target_file).resolve())
        self.class_name = class_name
        self.level = level
        #: names an earlier merge already added to this file (avoids duplicate copies)
        self.provided: set[str] = set(provided or ())
        self.notes: list[str] = []

    # -- chain discovery -------------------------------------------------------------
    def class_info(self, file: str, name: str) -> _ClassInfo | None:
        file_parser = self.parsers.get(str(Path(file).resolve()))
        if file_parser is None or name not in file_parser.classes:
            return None
        node = file_parser.classes[name].node
        if not isinstance(node, cst.ClassDef):
            return None
        body = list(node.body.body) if isinstance(node.body, cst.IndentedBlock) else []
        methods: OrderedDict[str, cst.FunctionDef] = OrderedDict()
        attributes: list[tuple[str, cst.BaseStatement]] = []
        docstring: cst.SimpleStatementLine | None = None
        other: list[cst.BaseStatement] = []
        for index, stmt in enumerate(body):
            if isinstance(stmt, cst.FunctionDef):
                methods[stmt.name.value] = stmt
            elif index == 0 and isinstance(stmt, cst.SimpleStatementLine) and is_docstring(stmt):
                docstring = stmt
            elif isinstance(stmt, cst.SimpleStatementLine) and (attr_name := statement_name(stmt)):
                attributes.append((attr_name, stmt))
            else:
                other.append(stmt)

        merged_bases: list[str] = []
        kept_bases: list[cst.Arg] = []
        for arg in node.bases:
            base = chain_name(arg.value)
            resolved = self._resolve_base(file_parser, base) if arg.keyword is None else None
            if resolved is not None:
                merged_bases.append(base or "?")
            else:
                kept_bases.append(arg)
        return _ClassInfo(
            file=str(Path(file).resolve()),
            name=name,
            node=node,
            body=body,
            methods=methods,
            attributes=attributes,
            docstring=docstring,
            other_statements=other,
            merged_bases=merged_bases,
            kept_bases=kept_bases,
        )

    def _resolve_base(self, file_parser: CSTFileParser, base: str | None) -> tuple[str, str] | None:
        """``(file, class name)`` when ``base`` is defined in another traced file."""
        if not base:
            return None
        head, _, tail = base.partition(".")
        record = file_parser.imports.get(head)
        if record is None or not record.is_target or not record.file_path:
            return None
        other = self.parsers.get(record.file_path)
        if other is None:
            return None
        if tail:  # `mod.Base` / `pkg.mod.Base`
            name = tail.split(".")[-1]
        else:  # `from .mod import Base [as B]`
            name = (record.imported_name or head).split(".")[-1]
        return (record.file_path, name) if name in other.classes else None

    def chain(self) -> list[_ClassInfo]:
        """``[leaf, base, base of base, ...]``."""
        leaf = self.class_info(self.target_file, self.class_name)
        if leaf is None:
            return []
        chain = [leaf]
        seen = {(leaf.file, leaf.name)}
        current = leaf
        while True:
            if not current.merged_bases:
                break
            resolved = self._resolve_base(self.parsers[current.file], current.merged_bases[0])
            if resolved is None or resolved in seen:
                break
            parent = self.class_info(*resolved)
            if parent is None:
                break
            chain.append(parent)
            seen.add(resolved)
            current = parent
            if self.level == ClassMerging.KeepOne:
                break
        return chain

    # -- planning --------------------------------------------------------------------
    def plan(self) -> MergePlan | None:
        chain = self.chain()
        if len(chain) < 2:
            return None
        leaf = chain[0]

        # who owns each method name (nearest class wins, exactly like the MRO)
        owner: dict[str, int] = {}
        for index, info in enumerate(chain):
            for name in info.methods:
                owner.setdefault(name, index)

        def final_name(index: int, name: str) -> str:
            if owner[name] == index:
                return name
            return f"{SLIM_PREFIX}{chain[index].name}_{name}"

        def resolver_for(index: int) -> Callable[[str], str | None]:
            def resolve(method: str) -> str | None:
                for below in range(index + 1, len(chain)):
                    if method in chain[below].methods:
                        return final_name(below, method)
                return None

            return resolve

        # ---- build the new class body ------------------------------------------------
        body: list[cst.BaseStatement] = []
        attributes: set[str] = set()
        leaf_docstring = leaf.docstring

        # 1. the leaf's own statements, with super() calls redirected
        leaf_resolver = resolver_for(0)
        for stmt in leaf.body:
            if isinstance(stmt, cst.FunctionDef):
                transformed = apply_transform(stmt, SuperRewriter(leaf_resolver, _first_param_name(stmt)))
                assert isinstance(transformed, cst.BaseStatement)
                stmt = transformed
            body.append(stmt)
            name = statement_name(stmt)
            if name:
                attributes.add(name)
        if leaf_docstring is not None:
            attributes.add("__doc__")

        # 2. statements inherited from the ancestors, nearest first
        injected: list[cst.BaseStatement] = []
        for index in range(1, len(chain)):
            info = chain[index]
            resolve = resolver_for(index)
            if index == 1 and leaf_docstring is None and info.docstring is not None:
                # keep the base docstring: it documents the merged behaviour
                body.insert(0, annotate_docstring(_strip_leading(info.docstring), info.name))
                if len(body) > 1:
                    body[1] = _blank_before(body[1])
            for stmt in info.body:
                if isinstance(stmt, cst.FunctionDef):
                    name = stmt.name.value
                    new_name = final_name(index, name)
                    renamed = new_name != name
                    moved = apply_transform(stmt, SuperRewriter(resolve, _first_param_name(stmt)))
                    assert isinstance(moved, cst.FunctionDef)
                    if renamed:
                        moved = moved.with_changes(name=cst.Name(new_name))
                        self.notes.append(
                            f"{info.name}.{name} inlined as {new_name} (overridden by {chain[owner[name]].name})"
                        )
                    else:
                        self.notes.append(f"{info.name}.{name} inlined")
                    injected.append(_blank_before(moved))
                    continue
                if stmt is info.docstring:
                    continue
                name = statement_name(stmt)
                if name and name in attributes:
                    continue  # the leaf (or a nearer base) shadows it
                if name:
                    attributes.add(name)
                injected.append(_blank_before(stmt))
        body += injected

        # ---- work out the new base list ---------------------------------------------
        # The base that was inlined is *replaced, in place*, by whatever the topmost
        # inlined ancestor inherits from.  Everything else stays: with multiple
        # inheritance (`class C(A, B)`) only the first local base is followed, so B must
        # survive as a real base or the MRO would silently change.
        top = chain[-1]
        leaf_parser = self.parsers[leaf.file]
        new_bases: list[cst.Arg] = []
        replaced = False
        for arg in leaf.node.bases:
            followed = (
                not replaced
                and arg.keyword is None
                and self._resolve_base(leaf_parser, chain_name(arg.value)) == (chain[1].file, chain[1].name)
            )
            if followed:
                new_bases.extend(top.node.bases)
                replaced = True
            else:
                new_bases.append(arg)
        if not replaced:
            new_bases = [*leaf.kept_bases, *top.node.bases]

        # de-duplicate (diamonds) while keeping the order
        unique: list[cst.Arg] = []
        seen_bases: set[str | None] = set()
        seen_keywords: set[str] = set()
        for arg in new_bases:
            if arg.keyword is not None:
                if arg.keyword.value in seen_keywords:
                    continue
                seen_keywords.add(arg.keyword.value)
            else:
                name = chain_name(arg.value)
                if name in seen_bases:
                    continue
                seen_bases.add(name)
            unique.append(arg)
        new_bases = [arg.with_changes(comma=cst.MaybeSentinel.DEFAULT, star="") for arg in unique]

        # ---- pull in whatever the inlined code needs from the ancestor files ----------
        extras, extra_imports = self._dependencies(chain, body, new_bases)

        merged = [(info.file, info.name) for info in chain[1:]]
        return MergePlan(
            file=leaf.file,
            class_name=leaf.name,
            chain=chain,
            new_bases=new_bases,
            body=body,
            extra_defs=extras,
            extra_imports=extra_imports,
            merged=merged,
            notes=list(self.notes),
            provided=self.provided,
        )

    # -- dependency transplant -------------------------------------------------------
    def _dependencies(
        self,
        chain: list[_ClassInfo],
        body: Sequence[cst.BaseStatement],
        new_bases: Sequence[cst.Arg],
    ) -> tuple[list[cst.BaseStatement], list[cst.BaseStatement]]:
        """Copy the module level imports/defs/constants the inlined code relies on."""
        target = self.parsers[self.target_file]
        bound: set[str] = set(target.imports) | set(target.defs) | set(dir(builtins))
        for stmt in target.module.body:
            bound |= bound_names_of(stmt)
        bound |= self.provided

        sources = [info.file for info in chain[1:]]
        needed: set[str] = set()
        for node in list(body) + list(new_bases):
            node_ = node.value if isinstance(node, cst.Arg) else node
            needed |= free_names(node_)

        extra_imports: list[cst.BaseStatement] = []
        extra_defs: list[cst.BaseStatement] = []
        emitted: set[str] = set()
        queue = sorted(needed - bound)
        seen: set[tuple[str, str]] = set()

        while queue:
            name = queue.pop(0)
            if name in bound or name in emitted:
                continue
            for source in sources:
                file_parser = self.parsers.get(source)
                if file_parser is None or (source, name) in seen:
                    continue
                seen.add((source, name))

                record = file_parser.imports.get(name)
                if record is not None:
                    stmt = self._import_statement(record, source)
                    if stmt is not None:
                        extra_imports.append(stmt)
                        emitted.add(name)
                        self.notes.append(f"import of '{name}' copied from {Path(source).name}")
                        break

                def_node = file_parser.defs.get(name)
                if def_node is not None:
                    extra_defs.append(_blank_before(def_node.node))
                    emitted.add(name)
                    self.notes.append(f"'{name}' copied from {Path(source).name}")
                    queue += sorted(free_names(def_node.node) - bound - emitted)
                    break

                assign = self._assignment_of(file_parser, name)
                if assign is not None:
                    extra_defs.append(_blank_before(assign))
                    emitted.add(name)
                    self.notes.append(f"'{name}' copied from {Path(source).name}")
                    queue += sorted(free_names(assign) - bound - emitted)
                    break
            else:
                if name not in bound and name not in emitted:
                    self.notes.append(f"unresolved reference '{name}' (not defined in the merged chain)")
        return extra_defs, extra_imports

    @staticmethod
    def _assignment_of(file_parser: CSTFileParser, name: str) -> cst.BaseStatement | None:
        for stmt in file_parser.module.body:
            if isinstance(stmt, cst.SimpleStatementLine) and statement_name(stmt) == name:
                return _strip_leading(stmt)
        return None

    @staticmethod
    def _import_statement(record: _ImportNode, source_file: str) -> cst.BaseStatement | None:
        """Re-emit one import binding as an absolute import (works after flattening)."""
        dotted = resolve_dotted(source_file, record.level, record.module_name)
        if isinstance(record.node, cst.Import):
            module_node = _dotted_node(record.module_name or dotted)
            alias = cst.ImportAlias(
                name=module_node,
                asname=cst.AsName(name=cst.Name(record.asname)) if record.asname else None,
            )
            return cst.SimpleStatementLine(body=[cst.Import(names=[alias])])
        if not dotted:
            return None
        alias = cst.ImportAlias(
            name=cst.Name(record.imported_name or record.name),
            asname=cst.AsName(name=cst.Name(record.asname or record.name)) if record.asname else None,
        )
        return cst.SimpleStatementLine(body=[cst.ImportFrom(module=_dotted_node(dotted), relative=[], names=[alias])])


def _dotted_node(dotted: str) -> cst.Name | cst.Attribute:
    """``"a.b.c"`` -> ``Attribute(Attribute(Name(a), b), c)``."""
    parts = dotted.split(".")
    node: cst.Name | cst.Attribute = cst.Name(parts[0])
    for part in parts[1:]:
        node = cst.Attribute(value=node, attr=cst.Name(part))
    return node


@dataclass
class MergePlan:
    """The recipe the transformer below applies to one file."""

    file: str
    class_name: str
    chain: list[_ClassInfo]
    new_bases: list[cst.Arg]
    body: list[cst.BaseStatement]
    extra_defs: list[cst.BaseStatement] = field(default_factory=list)
    extra_imports: list[cst.BaseStatement] = field(default_factory=list)
    merged: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    provided: set[str] = field(default_factory=set)

    @property
    def merged_classes(self) -> set[str]:
        return {name for _, name in self.merged}

    @property
    def provided_names(self) -> set[str]:
        """Names this plan adds to the module (so the next plan does not repeat them)."""
        names = set(self.provided)
        for stmt in list(self.extra_defs) + list(self.extra_imports):
            names |= bound_names_of(stmt)
        return names


class ClassMergeTransformer(cst.CSTTransformer):
    """Applies a :class:`MergePlan` to a module."""

    METADATA_DEPENDENCIES = (ParentNodeProvider,)

    def __init__(self, plan: MergePlan):
        self.plan = plan

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        plan = self.plan
        if original_node.name.value != plan.class_name:
            return updated_node
        parent = self.get_metadata(ParentNodeProvider, original_node)
        if not isinstance(parent, cst.Module):
            return updated_node

        body = list(plan.body)
        if isinstance(original_node.body, cst.IndentedBlock) and isinstance(updated_node.body, cst.IndentedBlock):
            # respect any change an earlier pass made to the leaf's own statements
            if len(original_node.body.body) == len(updated_node.body.body):
                swapped = {id(old): new for old, new in zip(original_node.body.body, updated_node.body.body)}
                body = [swapped.get(id(stmt), stmt) for stmt in body]
            new_body = updated_node.body.with_changes(body=body)
        else:
            new_body = cst.IndentedBlock(body=body)
        changes: dict[str, Any] = {"bases": plan.new_bases, "body": new_body}
        if not plan.new_bases:
            # `class X():` -> `class X:` (libcst omits the parens when they are default)
            changes.update(lpar=cst.MaybeSentinel.DEFAULT, rpar=cst.MaybeSentinel.DEFAULT)
        return updated_node.with_changes(**changes)

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        plan = self.plan
        if not plan.extra_defs and not plan.extra_imports:
            return updated_node
        body = list(updated_node.body)
        class_index = next(
            (i for i, stmt in enumerate(body) if isinstance(stmt, cst.ClassDef) and stmt.name.value == plan.class_name),
            len(body),
        )
        if plan.extra_defs:
            extra_top = [
                s for s in plan.extra_defs if isinstance(s, (cst.SimpleStatementLine, cst.BaseCompoundStatement))
            ]
            body[class_index:class_index] = extra_top
            class_index += len(extra_top)
        if plan.extra_imports:
            insert_at = 0
            for i, stmt in enumerate(body[:class_index]):
                if isinstance(stmt, cst.SimpleStatementLine) and any(
                    isinstance(small, (cst.Import, cst.ImportFrom)) for small in stmt.body
                ):
                    insert_at = i + 1
            extra_imp: list[cst.SimpleStatementLine | cst.BaseCompoundStatement] = [
                s
                for raw in plan.extra_imports
                if isinstance(
                    (s := _strip_leading(raw) if insert_at else raw),
                    (cst.SimpleStatementLine, cst.BaseCompoundStatement),
                )
            ]
            body[insert_at:insert_at] = extra_imp
        return updated_node.with_changes(body=body)


# ======================================================================================
# 8. code generators
# ======================================================================================


class FlattenImports(cst.CSTTransformer):
    """Rewrites imports of traced modules so they point at the generated files.

    ``from .utils import helper``  ->  ``from utils import helper``   (flat layout)
    ``from pkg import Thing``      ->  ``from models import Thing``   (re-export hop)
    ``from . import models``       ->  ``import models``              (submodule)
    """

    def __init__(self, owner_file: str, generator: CodeGenerator):
        self.owner_file = owner_file
        self.gen = generator
        self.notes: list[str] = []

    # -- helpers ---------------------------------------------------------------------
    def _target_module(self, dotted: str) -> str | None:
        return self.gen.output_module_for_dotted(dotted)

    def _alias_name(self, alias: cst.ImportAlias) -> str:
        return chain_name(alias.name) or ""

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.BaseSmallStatement | cst.RemovalSentinel | cst.FlattenSentinel:
        gen = self.gen
        level = len(updated_node.relative or ())
        base = resolve_dotted(self.owner_file, level, chain_name(updated_node.module))

        # `from . import sub` / `from pkg import submodule` -> plain imports
        if isinstance(updated_node.names, (list, tuple)):
            submodules = []
            for alias in updated_node.names:
                candidate = f"{base}.{self._alias_name(alias)}" if base else self._alias_name(alias)
                if candidate in gen.dotted_to_output:
                    submodules.append((alias, candidate))
            if submodules and len(submodules) == len(updated_node.names):
                aliases = []
                for alias, candidate in submodules:
                    new_name = gen.dotted_to_output[candidate]
                    local = _asname_str(alias.asname) if alias.asname else self._alias_name(alias)
                    aliases.append(
                        cst.ImportAlias(
                            name=_dotted_node(new_name),
                            asname=cst.AsName(name=cst.Name(local)) if alias.asname or "." in new_name else None,
                        )
                    )
                return cst.Import(names=aliases)

        target = self._target_module(base)
        if target is None:
            # maybe the base is a package whose __init__ was dropped: hop to the definer
            remapped = gen.reexport(base)
            if not remapped:
                if level and gen.layout == "flat":
                    self.notes.append(
                        f"'{original_node.module and chain_name(original_node.module) or '.'}'"
                        f" is not traced, so the relative import stays as it is and will "
                        f"not resolve in a flat layout (trace it, or use layout='mirror')"
                    )
                return updated_node
            groups: OrderedDict[str, list[cst.ImportAlias]] = OrderedDict()
            if isinstance(updated_node.names, cst.ImportStar):
                return updated_node
            for alias in updated_node.names:
                name = self._alias_name(alias)
                module = remapped.get(name)
                if module is None:
                    self.notes.append(f"cannot flatten '{name}' from {base}")
                    return updated_node
                groups.setdefault(module, []).append(alias.with_changes(comma=cst.MaybeSentinel.DEFAULT))
            statements = [
                cst.ImportFrom(module=_dotted_node(module), relative=[], names=aliases)
                for module, aliases in groups.items()
            ]
            return statements[0] if len(statements) == 1 else cst.FlattenSentinel(statements)

        if isinstance(updated_node.names, cst.ImportStar):
            return updated_node.with_changes(module=_dotted_node(target), relative=[])
        return updated_node.with_changes(module=_dotted_node(target), relative=[])

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import:
        gen = self.gen
        aliases = []
        changed = False
        for alias in updated_node.names:
            dotted = chain_name(alias.name) or ""
            target = gen.dotted_to_output.get(dotted)
            if target and target != dotted:
                if "." in dotted and alias.asname is None:
                    self.notes.append(
                        f"'import {dotted}' kept: attribute access '{dotted.split('.')[0]}.*' cannot be flattened"
                    )
                    aliases.append(alias)
                    continue
                aliases.append(
                    cst.ImportAlias(
                        name=_dotted_node(target),
                        asname=alias.asname
                        or (cst.AsName(name=cst.Name(dotted.replace(".", "_"))) if "." in target else None),
                        comma=cst.MaybeSentinel.DEFAULT,
                    )
                )
                changed = True
            else:
                aliases.append(alias)
        return updated_node.with_changes(names=aliases) if changed else updated_node


class RelativizeImports(cst.CSTTransformer):
    """The mirror layout counterpart of :class:`FlattenImports`.

    Code transplanted from another module arrives with an absolute import
    (``from demo_pkg.utils import scale_by``); inside a preserved package tree the
    relative form (``from .utils import scale_by``) reads better and matches the file's
    own style.
    """

    def __init__(self, owner_file: str, generator: CodeGenerator):
        self.owner_file = owner_file
        self.gen = generator

    def leave_ImportFrom(self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom) -> cst.ImportFrom:
        if updated_node.relative:
            return updated_node  # already relative
        dotted = chain_name(updated_node.module)
        if not dotted or dotted not in self.gen.dotted_to_output:
            return updated_node  # not one of our modules
        target_file = self.gen.file_of_dotted(dotted)
        if target_file is None:
            return updated_node
        own_pkg = package_of(self.owner_file).split(".") if package_of(self.owner_file) else []
        target_pkg = package_of(target_file).split(".") if package_of(target_file) else []
        shared = 0
        while shared < len(own_pkg) and shared < len(target_pkg) and own_pkg[shared] == target_pkg[shared]:
            shared += 1
        ups = len(own_pkg) - shared
        remainder = (
            target_pkg[shared:] + [dotted.rsplit(".", 1)[-1]] if dotted != ".".join(target_pkg) else target_pkg[shared:]
        )
        module = ".".join(remainder)
        return updated_node.with_changes(
            relative=[cst.Dot() for _ in range(ups + 1)],
            module=_dotted_node(module) if module else None,
        )


class MergeImportStatements(cst.CSTTransformer):
    """Folds ``from x import a`` + ``from x import b`` into one statement.

    Merging classes and flattening imports can easily produce several statements that
    point at the same module; this keeps the generated file tidy.
    """

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        groups: OrderedDict[tuple[str | None, int], list[int]] = OrderedDict()
        for index, stmt in enumerate(updated_node.body):
            small = self._single_import(stmt)
            if small is None or isinstance(small.names, cst.ImportStar):
                continue
            key = (chain_name(small.module), len(small.relative or ()))
            groups.setdefault(key, []).append(index)

        body = list(updated_node.body)
        drop: set[int] = set()
        for indexes in groups.values():
            if len(indexes) < 2:
                continue
            first = indexes[0]
            target = self._single_import(body[first])
            assert target is not None
            aliases: list[cst.ImportAlias] = []
            seen: set[tuple[str, str | None]] = set()
            for index in indexes:
                node = self._single_import(body[index])
                assert node is not None and not isinstance(node.names, cst.ImportStar)
                for alias in node.names:
                    key = (
                        chain_name(alias.name) or "",
                        _asname_str(alias.asname) if alias.asname else None,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    aliases.append(alias.with_changes(comma=cst.MaybeSentinel.DEFAULT))
                if index != first:
                    drop.add(index)
            line = body[first]
            assert isinstance(line, cst.SimpleStatementLine)
            body[first] = line.with_changes(body=[target.with_changes(names=aliases)])
        if not drop:
            return updated_node
        return updated_node.with_changes(body=[stmt for i, stmt in enumerate(body) if i not in drop])

    @staticmethod
    def _single_import(stmt: cst.BaseStatement) -> cst.ImportFrom | None:
        if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
            return None
        small = stmt.body[0]
        return small if isinstance(small, cst.ImportFrom) else None


class PruneUnusedImports(cst.CSTTransformer):
    """Drops import bindings that nothing in the file refers to any more."""

    METADATA_DEPENDENCIES = (ParentNodeProvider,)

    def __init__(
        self,
        used: set[str],
        is_init: bool,
        prune_external: bool,
        local_modules: set[str],
        owner_file: str,
        notes: list[str] | None = None,
    ):
        self.used = used
        self.is_init = is_init
        self.prune_external = prune_external
        self.local_modules = local_modules
        self.owner_file = owner_file
        self.notes = notes if notes is not None else []

    def _protected(self, node: cst.CSTNode, module_name: str | None) -> bool:
        if module_name == "__future__":
            return True
        parent = self.get_metadata(ParentNodeProvider, node)
        while parent is not None:
            if isinstance(parent, cst.Try):
                return True  # `try: import x / except ImportError:` guard
            parent = self.get_metadata(ParentNodeProvider, parent) if not isinstance(parent, cst.Module) else None
        return False

    def _is_local(self, module_name: str | None, level: int) -> bool:
        if level:
            return True
        dotted = resolve_dotted(self.owner_file, level, module_name)
        return dotted in self.local_modules or (module_name or "") in self.local_modules

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom | cst.RemovalSentinel:
        module_name = chain_name(updated_node.module)
        level = len(updated_node.relative or ())
        if isinstance(updated_node.names, cst.ImportStar):
            return updated_node
        if self._protected(original_node, module_name):
            return updated_node
        local = self._is_local(module_name, level)
        if not local and not self.prune_external:
            return updated_node
        kept = []
        for alias in updated_node.names:
            source = chain_name(alias.name) or ""
            bound = _asname_str(alias.asname) if alias.asname else source.split(".")[0]
            if self.is_init or bound in self.used:
                kept.append(alias)
            else:
                self.notes.append(f"dropped unused import '{bound}' (from {module_name or '.' * level})")
        if not kept:
            return cst.RemovalSentinel.REMOVE
        kept = [a.with_changes(comma=cst.MaybeSentinel.DEFAULT) for a in kept]
        return updated_node.with_changes(names=kept)

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import | cst.RemovalSentinel:
        kept = []
        for alias in updated_node.names:
            dotted = chain_name(alias.name) or ""
            bound = _asname_str(alias.asname) if alias.asname else dotted.split(".")[0]
            root = dotted.split(".")[0]
            droppable = dotted in self.local_modules or (
                self.prune_external and root in sys.stdlib_module_names and root not in SIDE_EFFECT_MODULES
            )
            if self._protected(original_node, dotted) or not droppable or bound in self.used or self.is_init:
                kept.append(alias)
            else:
                self.notes.append(f"dropped unused import '{bound}'")
        if not kept:
            return cst.RemovalSentinel.REMOVE
        if len(kept) == len(updated_node.names):
            return updated_node
        kept = [a.with_changes(comma=cst.MaybeSentinel.DEFAULT) for a in kept]
        return updated_node.with_changes(names=kept)


class PruneUnusedDefs(cst.CSTTransformer):
    """Removes top level functions/classes nobody can reach any more."""

    METADATA_DEPENDENCIES = (ParentNodeProvider,)

    def __init__(
        self,
        keep: set[str],
        prune_methods: bool = False,
        method_keep: Mapping[str, set[str]] | None = None,
        notes: list[str] | None = None,
        used: set[str] | None = None,
        prune_assignments: bool = True,
    ):
        self.keep = keep
        self.prune_methods = prune_methods
        self.method_keep = dict(method_keep or {})
        self.notes = notes if notes is not None else []
        self.removed: list[str] = []
        self.used = set(used or ())
        self.prune_assignments = prune_assignments

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine | cst.RemovalSentinel:
        """Drop ``UNUSED_LIMIT = 0.5`` style leftovers (only when side effect free)."""
        if not self.prune_assignments or not self._is_top_level(original_node):
            return updated_node
        if len(updated_node.body) != 1:
            return updated_node
        small = updated_node.body[0]
        value: cst.BaseExpression | None = None
        targets: Sequence[cst.BaseExpression] = ()
        if isinstance(small, cst.Assign):
            targets = [t.target for t in small.targets]
            value = small.value
        elif isinstance(small, cst.AnnAssign):
            targets, value = [small.target], small.value
        else:
            return updated_node
        names = [t.value for t in targets if isinstance(t, cst.Name)]
        if len(names) != len(targets) or not names:
            return updated_node  # tuple unpacking -> leave alone
        if any(name.startswith("__") and name.endswith("__") for name in names):
            return updated_node  # __all__, __version__, ...
        if any(name in self.used for name in names):
            return updated_node
        if value is not None and contains_node_types(value, (cst.Call, cst.Await, cst.Yield)):
            return updated_node  # `x = build()` may have side effects
        for name in names:
            self.removed.append(name)
            self.notes.append(f"removed unused assignment '{name}'")
        return cst.RemovalSentinel.REMOVE

    def _is_top_level(self, node: cst.CSTNode) -> bool:
        return isinstance(self.get_metadata(ParentNodeProvider, node), cst.Module)

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef | cst.RemovalSentinel:
        if self._is_top_level(original_node) and original_node.name.value not in self.keep:
            self.removed.append(original_node.name.value)
            self.notes.append(f"removed unused def '{original_node.name.value}'")
            return cst.RemovalSentinel.REMOVE
        return updated_node

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef | cst.RemovalSentinel:
        name = original_node.name.value
        if not self._is_top_level(original_node):
            return updated_node
        if name not in self.keep:
            self.removed.append(name)
            self.notes.append(f"removed unused class '{name}'")
            return cst.RemovalSentinel.REMOVE
        if self.prune_methods and name in self.method_keep and isinstance(updated_node.body, cst.IndentedBlock):
            allowed = self.method_keep[name]
            body = []
            for stmt in updated_node.body.body:
                if isinstance(stmt, cst.FunctionDef) and stmt.name.value not in allowed:
                    self.notes.append(f"removed unused method '{name}.{stmt.name.value}'")
                    self.removed.append(f"{name}.{stmt.name.value}")
                    continue
                body.append(stmt)
            if body:
                return updated_node.with_changes(body=updated_node.body.with_changes(body=body))
        return updated_node


class TidyModule(cst.CSTTransformer):
    """Cosmetic pass run last: no leading/trailing blank lines left by removals."""

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        body = list(updated_node.body)
        if body:
            first = body[0]
            leading = getattr(first, "leading_lines", None)
            if leading:
                kept = [line for line in leading if line.comment is not None]
                if len(kept) != len(leading):
                    body[0] = first.with_changes(leading_lines=kept)
        footer = [line for line in updated_node.footer if line.comment is not None]
        if body == list(updated_node.body) and footer == list(updated_node.footer):
            return updated_node
        return updated_node.with_changes(body=body, footer=footer)


class CodeGenerator:
    """Common machinery: layout planning, writing, reporting."""

    #: default output layout -- ``"flat"`` (one directory) or ``"mirror"`` (keep tree)
    layout = "flat"

    def __init__(
        self,
        target_dir: str | Path,
        parser: Parser,
        module_mapper: Mapping[str, str] | None = None,
        custom_rewriter: Mapping[str, Hook] | None = None,
        class_merge_level: int | None = None,
        layout: str | None = None,
        emit_init: bool | None = None,
        header: str = CODEGEN_PREFIX,
        validate: bool = True,
        prune_unused_imports: bool = True,
        prune_external_imports: bool = True,
        drop_empty_modules: bool = False,
    ):
        self.parser = parser
        self.parsers = parser.get_parsers()
        self.target_dir = str(Path(target_dir))
        self.module_mapper = dict(module_mapper or {})
        self.custom_rewriter = dict(custom_rewriter or {})
        self.merge_level = class_merge_level
        self.layout = layout or self.layout
        self.header = header
        self.validate = validate
        self.prune_unused_imports = prune_unused_imports
        self.prune_external_imports = prune_external_imports
        self.drop_empty_modules = drop_empty_modules
        #: modules another emitted module still imports (filled in by pass A.5)
        self.used_modules: set[str] = set()

        if self.__class__.__name__ == "FileLevelCodeGenerator" and class_merge_level:
            raise ValueError("File level code slim does not support class merging.")

        #: ``{source file: output path}`` (missing key == file is not emitted)
        self.outputs: dict[str, Path] = {}
        #: ``{source file: generated module name}``
        self.module_names: dict[str, str] = {}
        #: ``{dotted module name: generated module name}``
        self.dotted_to_output: dict[str, str] = {}
        self.reports: dict[str, FileReport] = {}
        self.stats: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.notes: list[str] = []
        self.emit_init = (self.layout == "mirror") if emit_init is None else emit_init
        self._plan_layout()

    # -- layout ----------------------------------------------------------------------
    def _plan_layout(self) -> None:
        files = list(self.parsers)
        if self.layout == "mirror":
            # mirror the tree from the outermost package root (`src/pkg/x.py` -> `pkg/x.py`)
            roots = sorted({package_root(f) for f in files}, key=lambda p: len(p.parts))
            root = roots[0] if roots else common_root(files)
            for file in files:
                path = Path(file)
                rel = path.relative_to(root) if root in path.parents else Path(path.name)
                self.outputs[file] = Path(self.target_dir) / rel
                self.module_names[file] = dotted_module_name(file)
        else:
            stems: dict[str, list[str]] = defaultdict(list)
            for file in files:
                stems[Path(file).stem].append(file)
            for file in files:
                name = Path(file).name
                if name == "__init__.py" and not self.emit_init:
                    continue
                stem = Path(file).stem
                if len(stems[stem]) > 1:
                    stem = f"{Path(file).parent.name}_{stem}"
                if name == "__init__.py":
                    stem = f"{Path(file).parent.name}__init__"
                stem = self.module_mapper.get(dotted_module_name(file), self.module_mapper.get(stem, stem))
                self.outputs[file] = Path(self.target_dir) / f"{stem}.py"
                self.module_names[file] = stem
        for file, module_name in self.module_names.items():
            self.dotted_to_output[dotted_module_name(file)] = module_name
        #: ``{package dotted name: {bound name: defining module name}}`` for dropped inits
        self._reexports: dict[str, dict[str, str]] = {}
        for file, file_parser in self.parsers.items():
            if Path(file).name != "__init__.py" or file in self.outputs:
                continue
            table: dict[str, str] = {}
            for record in file_parser.imports.values():
                if record.is_target and record.file_path and record.file_path in self.module_names:
                    table[record.name] = self.module_names[record.file_path]
            self._reexports[dotted_module_name(file)] = table

    def reexport(self, dotted: str) -> dict[str, str]:
        return self._reexports.get(dotted, {})

    def output_module_for_dotted(self, dotted: str) -> str | None:
        if dotted in self.module_mapper:
            return self.module_mapper[dotted]
        return self.dotted_to_output.get(dotted)

    def file_of_dotted(self, dotted: str) -> str | None:
        for file in self.parsers:
            if dotted_module_name(file) == dotted:
                return file
        return None

    def output_path_of(self, file: str) -> Path | None:
        return self.outputs.get(file)

    # -- pipeline --------------------------------------------------------------------
    #: dead code removal is repeated until nothing changes any more
    max_iterations = 8

    def generate(self) -> dict[str, Any]:
        """Run the whole pipeline and write the slimmed project. Returns the report."""
        makedirs(self.target_dir)
        modules: OrderedDict[str, cst.Module] = OrderedDict()
        with cd(self.target_dir):
            # pass A: merge classes / flatten imports (shape changing rewrites)
            for file, file_parser in self.parsers.items():
                modules[file] = self.transform(file, file_parser.module)
            for file in self.parsers:
                if file not in self.outputs:
                    self.notes.append(f"skipped {Path(file).name} (not emitted in '{self.layout}' layout)")
            # pass B: prune to a fixpoint -- dropping a class can make the imports that
            # kept another file alive unused, which in turn frees more dead code.
            for _ in range(self.max_iterations):
                self.after_transform(modules)
                changed = False
                for file, module in modules.items():
                    pruned = self.finalize(file, module)
                    if pruned.code != module.code:
                        modules[file] = pruned
                        changed = True
                if not changed:
                    break
            for file, module in modules.items():
                out_path = self.outputs.get(file)
                if out_path is None:
                    continue
                if self.drop_empty_modules and not module.body and file not in self.used_modules:
                    self.notes.append(f"dropped {Path(file).name}: nothing left to emit")
                    continue
                self.write(out_path, self.stamp(module), file)
        return self.report()

    def transform(self, file: str, module: cst.Module) -> cst.Module:
        """Pass A -- everything that changes the *shape* of the code."""
        module = self.rewrite_imports(file, module)
        if self.custom_rewriter:
            module = MetadataWrapper(module).visit(Rewriter(self.custom_rewriter))
        return module

    def rewrite_imports(self, file: str, module: cst.Module) -> cst.Module:
        """Flatten / re-point the imports of traced modules."""
        if self.layout == "mirror":
            # the package tree is preserved: the original relative imports still work,
            # only transplanted absolute ones are turned back into relative ones
            module = module.visit(RelativizeImports(file, self))
            return module.visit(MergeImportStatements())
        flattener = FlattenImports(file, self)
        module = MetadataWrapper(module).visit(flattener)
        self._add_notes(file, flattener.notes)
        return module.visit(MergeImportStatements())

    def after_transform(self, modules: Mapping[str, cst.Module]) -> None:
        """Pass A.5 -- hook run once every module has been rewritten."""
        for file, module in modules.items():
            self.reports[file] = scan_module(module)

    def finalize(self, file: str, module: cst.Module) -> cst.Module:
        """Pass B -- per file clean up right before writing."""
        return module

    # -- output ----------------------------------------------------------------------
    def stamp(self, module: cst.Module) -> cst.Module:
        return add_codegen_header(module, self.header) if self.header else module

    def write(self, out_path: str | Path, module: cst.Module, source_file: str) -> None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.validate:
            validate_module(module, source_file)
        out_path.write_text(module.code, encoding="utf-8")
        original = Path(source_file).read_text(encoding="utf-8") if Path(source_file).is_file() else ""
        stat = self.stats.setdefault(source_file, {})
        stat.update({
            "output": str(out_path),
            "module": self.module_names.get(source_file, out_path.stem),
            "in_lines": len(original.splitlines()),
            "out_lines": len(module.code.splitlines()),
            "notes": [n for n in self.notes if Path(source_file).name in n],
        })

    def _add_notes(self, file: str, notes: Sequence[str]) -> None:
        """Record rewrite notes without duplicating them across fixpoint rounds."""
        for note in notes:
            line = f"[{Path(file).name}] {note}"
            if line not in self.notes:
                self.notes.append(line)

    def _generate_from_str(self, filename: str | Path, contents: Sequence[str] | None = None) -> None:
        """Parity helper: write a file from raw strings, stamped with the header."""
        filename = Path(filename)
        filename.parent.mkdir(parents=True, exist_ok=True)
        with open(filename, "w", encoding="utf-8") as handle:
            handle.write(self.header + "\n" if self.header else "")
            handle.writelines(contents or [])

    def _generate_from_cst(self, filename: str | Path, module: cst.Module) -> None:
        """Parity helper for the old ``_generate_from_ast``."""
        self.write(filename, self.stamp(module), str(filename))

    def generate_init(self, target_path: str | Path, force: bool = False) -> None:
        """Create an (empty) ``__init__.py`` so the output stays a package."""
        target_path = Path(target_path)
        if not force and is_file_exist(target_path, "__init__.py"):
            raise RuntimeError("__init__.py exists!")
        self._generate_from_str(target_path / "__init__.py")

    def copy_file(
        self,
        source_file: str | Path,
        target_dir: str | Path,
        force: bool = False,
    ) -> None:
        source_file, target_dir = Path(source_file), Path(target_dir)
        if not force and is_file_exist(target_dir, source_file.name):
            raise RuntimeError(f"{source_file} exists!")
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(source_file), str(target_dir))

    def makedirs(self, path: str | Path) -> str:
        return makedirs(path)

    def format(self, file_path: str | Path) -> None:
        """No-op: libcst already round-trips the original formatting."""

    # -- reporting -------------------------------------------------------------------
    def report(self) -> dict[str, Any]:
        written = OrderedDict((f, s) for f, s in self.stats.items() if "in_lines" in s)
        dropped = [f for f, s in self.stats.items() if "in_lines" not in s]
        # "before" counts every traced file, "after" only what was written
        total_in = sum(len(Path(f).read_text(encoding="utf-8").splitlines()) for f in self.parsers if Path(f).is_file())
        total_out = sum(s["out_lines"] for s in written.values())
        summary = {
            "target_dir": self.target_dir,
            "layout": self.layout,
            "mode": type(self).__name__,
            "files_in": len(self.parsers),
            "files_out": len(written),
            "files_dropped": len(dropped),
            "lines_in": total_in,
            "lines_out": total_out,
            "saved": total_in - total_out,
            "per_file": written,
            "dropped": dropped,
            "notes": self.notes,
        }
        self.summary = summary
        return summary

    def format_report(self, summary: dict[str, Any] | None = None) -> str:
        summary = summary or getattr(self, "summary", self.report())
        dropped = summary.get("files_dropped", 0)
        lines = [
            f"{summary['mode']} -> {summary['target_dir']} (layout={summary['layout']})",
            f"files: {summary['files_in']} traced, {summary['files_out']} written"
            + (f", {dropped} dropped" if dropped else "")
            + f"   lines: {summary['lines_in']} -> {summary['lines_out']} "
            f"({-summary['saved']:+d} lines)",
        ]
        for file, stat in summary["per_file"].items():
            extra = ""
            if stat.get("merged"):
                extra = f"   merged: {', '.join(stat['merged'])}"
            if stat.get("removed"):
                extra += f"   removed: {', '.join(stat['removed'])}"
            try:
                written = str(Path(stat["output"]).relative_to(summary["target_dir"]))
            except ValueError:
                written = stat["output"]
            lines.append(
                f"  {Path(file).name:<22} {stat['in_lines']:>4} -> {stat['out_lines']:>4} lines   -> {written}{extra}"
            )
        return "\n".join(lines)


class FileLevelCodeGenerator(CodeGenerator):
    """Copy every traced file, flattening the local imports. No code is removed."""

    layout = "flat"


class SegmentCodeGenerator(CodeGenerator):
    """File level slimming *plus* dead code removal and inheritance merging."""

    layout = "flat"

    def __init__(
        self,
        *args: Any,
        prune_methods: bool = False,
        prune_decorated: bool = False,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.prune_methods = prune_methods
        #: decorated defs are kept by default -- decorators often register things
        self.prune_decorated = prune_decorated
        self.drop_empty_modules = kwargs.get("drop_empty_modules", True)
        self.plans: dict[str, list[MergePlan]] = {}
        self.provided: dict[str, set[str]] = {}
        self.roots: dict[str, set[str]] = {}
        self.keep: dict[str, set[str]] = {}
        self.externally_imported: dict[str, set[str]] = defaultdict(set)
        self.used_modules: set[str] = set()
        if self.merge_level:
            for file_parser in self.parsers.values():
                file_parser.get_target_merge_class()
            self._plan_merges()

    # -- merging ---------------------------------------------------------------------
    def _plan_merges(self) -> None:
        assert self.merge_level is not None
        for file, file_parser in self.parsers.items():
            mergeable = file_parser.get_target_merge_class()
            provided = self.provided.setdefault(file, set())
            for class_name in mergeable:
                merger = ClassMerging(
                    self.parser,
                    file,
                    class_name,
                    level=self.merge_level,
                    provided=provided,
                )
                plan = merger.plan()
                if plan is None:
                    continue
                provided |= plan.provided_names
                self.plans.setdefault(file, []).append(plan)
                self._add_notes(file, plan.notes)

    def transform(self, file: str, module: cst.Module) -> cst.Module:
        for plan in self.plans.get(file, []):
            module = MetadataWrapper(module).visit(ClassMergeTransformer(plan))
        return super().transform(file, module)

    # -- cross file usage ------------------------------------------------------------
    def after_transform(self, modules: Mapping[str, cst.Module]) -> None:
        super().after_transform(modules)
        # recomputed from scratch: this runs once per fixpoint iteration
        self.externally_imported = defaultdict(set)
        self.used_modules = set()
        for file, report in self.reports.items():
            for bound, (node, module_name, level) in report.imports.items():
                target = self._resolve_import(file, module_name, level, bound)
                if target is None:
                    continue
                self.used_modules.add(target)
                if isinstance(node, cst.ImportFrom) and not isinstance(node.names, cst.ImportStar):
                    for alias in node.names:
                        source = chain_name(alias.name)
                        if source:
                            self.externally_imported[target].add(source.split(".")[-1])
                else:
                    # `import mod` / `from mod import *` keeps every public name alive
                    self.externally_imported[target] |= self.parser.public_names(target)

    def _resolve_import(self, file: str, module_name: str | None, level: int, bound: str) -> str | None:
        candidates = []
        if module_name:
            candidates.append(module_name)
            candidates.append(resolve_dotted(file, level, module_name))
        if level and not module_name:
            candidates.append(resolve_dotted(file, level, None) + "." + bound)
        for candidate in candidates:
            if not candidate:
                continue
            for other in self.parsers:
                if self.module_names.get(other) == candidate or dotted_module_name(other) == candidate:
                    return other
        return None

    # -- dead code -------------------------------------------------------------------
    def _keep_set(self, file: str, report: FileReport) -> set[str]:
        roots = set(report.all_names)
        roots |= report.top_level_references
        roots |= self.externally_imported.get(file, set())
        if not self.prune_decorated:
            # `@register("x") def f(): ...` runs at import time: dropping it would
            # change behaviour even when nothing ever calls f.
            for name, node in report.defs.items():
                if isinstance(node, (cst.FunctionDef, cst.ClassDef)) and node.decorators:
                    roots.add(name)
        if Path(file).name == "__init__.py":
            roots |= set(report.imports)
        if file in self.parser.entry_files:
            roots |= set(report.defs)  # never slim away the entry points themselves
        self.roots[file] = roots
        keep: set[str] = set()
        queue = sorted(roots)
        while queue:
            name = queue.pop()
            if name in keep:
                continue
            keep.add(name)
            node = report.defs.get(name)
            if node is None:
                continue
            for ref in collect_names(node):
                if ref not in keep:
                    queue.append(ref)
        self.keep[file] = keep
        return keep

    def _method_keep(self, file: str, report: FileReport) -> dict[str, set[str]]:
        tails = self.parser.attribute_names()
        external = self.externally_imported.get(file, set())
        result: dict[str, set[str]] = {}
        for class_name, methods in report.methods.items():
            node = report.classes[class_name]
            roots = set()
            for name, func in methods.items():
                if (
                    name.startswith("__")
                    and name.endswith("__")
                    or func.decorators
                    or name in tails
                    or name in external
                ):
                    roots.add(name)
            for stmt in node.body.body if isinstance(node.body, cst.IndentedBlock) else []:
                if not isinstance(stmt, cst.FunctionDef):
                    roots |= collect_names(stmt) & set(methods)
            keep: set[str] = set()
            queue = sorted(roots)
            while queue:
                name = queue.pop()
                if name in keep or name not in methods:
                    continue
                keep.add(name)
                refs = collect_names(methods[name])
                refs |= {t for t in (chain_tail(c) for c in collect_chains(methods[name])) if t}
                for ref in refs:
                    if ref in methods and ref not in keep:
                        queue.append(ref)
            result[class_name] = keep
        return result

    def finalize(self, file: str, module: cst.Module) -> cst.Module:
        report = self.reports[file]
        notes: list[str] = []
        import_notes: list[str] = []
        # a name counts as used when it is referenced, when a *string annotation*
        # mentions it, or when it is exported through __all__
        used = report.references | report.annotation_strings | set(report.all_names)
        used |= {t for t in (chain_tail(c) for c in report.chains) if t}
        if self.prune_unused_imports:
            module = MetadataWrapper(module).visit(
                PruneUnusedImports(
                    used=used,
                    is_init=Path(file).name == "__init__.py",
                    prune_external=self.prune_external_imports,
                    local_modules=set(self.dotted_to_output),
                    owner_file=file,
                    notes=import_notes,
                )
            )
            report = scan_module(module)
            self.reports[file] = report
        keep = self._keep_set(file, report)
        pruner = PruneUnusedDefs(
            keep=keep,
            prune_methods=self.prune_methods,
            method_keep=self._method_keep(file, report) if self.prune_methods else None,
            notes=notes,
            used=used | self.externally_imported.get(file, set()),
        )
        module = MetadataWrapper(module).visit(pruner)
        module = module.visit(TidyModule())
        self._add_notes(file, notes)
        stat = self.stats.setdefault(file, {})
        stat["removed"] = list(OrderedDict.fromkeys([*stat.get("removed", []), *pruner.removed]))
        stat["kept"] = sorted(name for name in keep if name in report.defs)
        stat["merged"] = [
            f"{p.chain[0].name}<={'<-'.join(c.name for c in p.chain[1:])}" for p in self.plans.get(file, [])
        ]
        return module


# ======================================================================================
# 9. the public facade
# ======================================================================================


class AutoSlim:
    """Fluent entry point (same shape as the original ``AutoSlim``).

    ::

        AutoSlim("pkg/train.py", "slim/").mode(AutoSlim.SegmentLevel) \\
            .merge_class(AutoSlim.Eliminate).prune_methods().generate().report()
    """

    FileLevel = FileLevelCodeGenerator
    SegmentLevel = SegmentCodeGenerator
    File = FileEntry
    String = StringEntry
    Eliminate = ClassMerging.Eliminate
    KeepOne = ClassMerging.KeepOne

    def __init__(
        self,
        entries: Any,
        target_dir: str | Path,
        refactor_info: Mapping[str, str] | None = None,
    ):
        self.entries = entries
        self.target_dir = str(target_dir)
        #: ``refactor_info`` is the module mapper: ``{"pkg.utils": "helpers"}``
        self._refactor_info: dict[str, str] = dict(refactor_info or {})
        self._mode: type[CodeGenerator] = AutoSlim.FileLevel
        self._merge_class: int | None = None
        self._entry_type: type[Entry] = FileEntry
        self._endpoints: EndPoint | Sequence[EndPoint] | None = None
        self._custom_rewriter: dict[str, Hook] = {}
        self._options: dict[str, Any] = {}
        self.parser: Parser | None = None
        self.codegen: CodeGenerator | None = None
        self.summary: dict[str, Any] | None = None

    # -- fluent configuration --------------------------------------------------------
    def mode(self, mode: type[CodeGenerator]) -> AutoSlim:
        """``AutoSlim.FileLevel`` (copy + flatten) or ``AutoSlim.SegmentLevel`` (slim)."""
        self._mode = mode
        return self

    def merge_class(self, merge_level: int | None) -> AutoSlim:
        """``AutoSlim.Eliminate`` / ``AutoSlim.KeepOne`` (segment level only)."""
        self._merge_class = merge_level
        return self

    def entry_type(self, entry_type: type[Entry]) -> AutoSlim:
        self._entry_type = entry_type
        return self

    def endpoints(self, endpoints: EndPoint | Sequence[EndPoint]) -> AutoSlim:
        self._endpoints = endpoints
        return self

    def layout(self, layout: str) -> AutoSlim:
        """``"flat"`` (one directory) or ``"mirror"`` (keep the package tree)."""
        self._options["layout"] = layout
        return self

    def module_mapper(self, mapper: Mapping[str, str]) -> AutoSlim:
        self._refactor_info.update(mapper)
        return self

    def custom_rewriter(self, rewriter: Mapping[str, Hook]) -> AutoSlim:
        """Extra ``{NodeType: fn(original, updated)}`` hooks (see :class:`Rewriter`)."""
        self._custom_rewriter.update(rewriter)
        return self

    def prune_methods(self, flag: bool = True) -> AutoSlim:
        self._options["prune_methods"] = flag
        return self

    def options(self, **kwargs: Any) -> AutoSlim:
        """Any other generator keyword: ``emit_init``, ``header``, ``validate``, ..."""
        self._options.update(kwargs)
        return self

    # -- run -------------------------------------------------------------------------
    def generate(self) -> AutoSlim:
        import inspect

        # an already built Entry can be passed straight in (StringEntry(snippets, roots=..))
        entry = self.entries if isinstance(self.entries, Entry) else self._entry_type.build(self.entries)
        self.parser = Parser(entry, endpoints=self._endpoints)
        kwargs = dict(self._options)
        kwargs.update(
            module_mapper=self._refactor_info or None,
            custom_rewriter=self._custom_rewriter or None,
            class_merge_level=self._merge_class,
        )
        signature = inspect.signature(self._mode.__init__)
        accepts_anything = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
        allowed = set(kwargs) if accepts_anything else set(signature.parameters)
        ignored = [k for k in kwargs if k not in allowed]
        for key in ignored:
            kwargs.pop(key)
            self._warn(f"option '{key}' ignored by {self._mode.__name__}")
        self.codegen = self._mode(self.target_dir, self.parser, **kwargs)
        self.summary = self.codegen.generate()
        return self

    @staticmethod
    def _warn(message: str) -> None:
        print(f"[autoslim] {message}", file=sys.stderr)

    def report(self, notes: bool = False) -> str:
        if self.codegen is None:
            return "AutoSlim has not run yet -- call .generate() first."
        text = self.codegen.format_report(self.summary)
        unique = list(OrderedDict.fromkeys(self.codegen.notes))
        if notes and unique:
            text += "\nnotes:\n" + "\n".join(f"  - {n}" for n in unique)
        return text

    def print_report(self, notes: bool = False) -> AutoSlim:
        print(self.report(notes=notes))
        return self

    def __repr__(self) -> str:
        return (
            f"AutoSlim(entries={self.entries!r}, target_dir={self.target_dir!r}, "
            f"mode={self._mode.__name__}, merge={self._merge_class})"
        )


# ======================================================================================
# 10. command line + demo
# ======================================================================================

DEMO_PACKAGE: OrderedDict[str, str] = OrderedDict([
    (
        "demo_pkg/__init__.py",
        '''
        """Demo package used by `python autoslim.py demo`."""
        from .models import ResNet, make_resnet
        from .utils import get_logger

        __all__ = ["ResNet", "make_resnet", "get_logger"]
    ''',
    ),
    (
        "demo_pkg/utils.py",
        '''
        import math
        import json

        PI = 3.14159
        UNUSED_LIMIT = 0.5


        def get_logger(name="demo"):
            return "[{}]".format(name)


        def to_tuple(value, n=2):
            """Only used by BaseModel.dead_helper -- which is never called."""
            if isinstance(value, (list, tuple)):
                return tuple(value[:n])
            return (value,) * n


        def scale_by(value, factor):
            return round(value * factor * math.sqrt(PI), 4)


        def never_used_util():
            return json.dumps({"dead": True})


        class Config:
            def __init__(self, depth):
                self.depth = depth

            def describe(self):
                return "Config(depth={})".format(self.depth)


        class UnusedConfig:
            def __init__(self):
                self.nothing = None
    ''',
    ),
    (
        "demo_pkg/base.py",
        '''
        from .utils import get_logger, to_tuple

        KIND = "base"


        class BaseModel:
            """Shared behaviour of every demo model."""

            kind = KIND

            def __init__(self, depth):
                self.depth = depth
                self.log = get_logger(self.__class__.__name__)

            def forward(self, x):
                return x

            def summary(self):
                return "{}(depth={}, kind={})".format(self.log, self.depth, self.kind)

            def dead_helper(self):
                return to_tuple(self.depth)

            @property
            def name(self):
                return "base-model"
    ''',
    ),
    (
        "demo_pkg/mid.py",
        '''
        from .base import BaseModel
        from .utils import scale_by


        class MidModel(BaseModel):
            """Adds one scaling stage on top of BaseModel."""

            stage = "mid"

            def __init__(self, depth, factor=2.0):
                super().__init__(depth)
                self.factor = factor

            def forward(self, x):
                return scale_by(super().forward(x), self.factor)

            def describe(self):
                return self.summary() + "/" + self.stage
    ''',
    ),
    (
        "demo_pkg/models.py",
        """
        from .mid import MidModel
        from .utils import get_logger


        class ResNet(MidModel):
            def __init__(self, depth=4, factor=1.0):
                super().__init__(depth, factor)
                self.blocks = depth

            def forward(self, x):
                return super().forward(x) + self.blocks

            def never_called(self):
                return get_logger("dead")


        class VGG(MidModel):
            def forward(self, x):
                return x


        def make_resnet(depth=4, factor=3.0):
            return ResNet(depth, factor)


        def dead_helper():
            return VGG()
    """,
    ),
    (
        "demo_pkg/run.py",
        """
        from .models import ResNet, make_resnet
        from .utils import Config


        def main():
            model = make_resnet(depth=4, factor=2.0)
            print(model.forward(10))
            print(model.summary())
            print(model.name)
            print(model.describe())
            print(Config(3).describe())
            print(ResNet(2).forward(5))


        if __name__ == "__main__":
            main()
    """,
    ),
])


def _indent(text: str, prefix: str = "  | ") -> str:
    """``textwrap.indent`` that also prefixes blank lines (nicer for code dumps)."""
    return "".join(prefix + line + "\n" for line in text.rstrip("\n").split("\n"))


def write_demo_package(root: str | Path) -> Path:
    """Materialise :data:`DEMO_PACKAGE` below ``root`` and return the entry file."""
    root = Path(root)
    for name, source in DEMO_PACKAGE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")
    return root / "demo_pkg" / "run.py"


def _run_python(cwd: Path, args: Sequence[str]) -> str:
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"`{' '.join(args)}` failed in {cwd}:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def run_demo(workdir: str | Path | None = None, verbose: bool = True) -> dict[str, Any]:
    """Build the demo package, slim it twice and *execute* the results."""
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="autoslim-demo-"))
    tmp.mkdir(parents=True, exist_ok=True)
    src_root = tmp / "src"
    entry = write_demo_package(src_root)

    def say(*args: Any) -> None:
        if verbose:
            print(*args)

    say("=" * 78)
    say("AutoSlim demo -- source package")
    say("=" * 78)
    for name, source in DEMO_PACKAGE.items():
        lines = len(textwrap.dedent(source).strip().splitlines())
        say(f"  {name:<26} {lines:>4} lines")

    expected = _run_python(src_root, ["-m", "demo_pkg.run"])
    say("\noriginal program output:")
    say(_indent(expected))

    results: dict[str, Any] = {"workdir": str(tmp), "expected": expected}

    # ---- 1. file level: copy + flatten imports only --------------------------------
    file_level_dir = tmp / "out_filelevel"
    file_level = AutoSlim(str(entry), file_level_dir).mode(AutoSlim.FileLevel).layout("flat").generate()
    say("\n" + "=" * 78)
    say("1) FileLevelCodeGenerator -- copy every traced file, flatten local imports")
    say("=" * 78)
    say(file_level.report(notes=True))
    got = _run_python(file_level_dir, ["run.py"])
    assert got == expected, f"file level output differs:\n{got}\n!=\n{expected}"
    say("\nruntime check: `python run.py` in the generated folder -> identical output [OK]")
    say("\ngenerated models.py:")
    say(_indent((file_level_dir / "models.py").read_text()))
    results["file_level"] = file_level.summary

    # ---- 2. segment level: merge classes + drop dead code ---------------------------
    segment_dir = tmp / "out_segment"
    segment = (
        AutoSlim(str(entry), segment_dir)
        .mode(AutoSlim.SegmentLevel)
        .merge_class(AutoSlim.Eliminate)
        .prune_methods(True)
        .layout("flat")
        .generate()
    )
    say("\n" + "=" * 78)
    say("2) SegmentCodeGenerator -- merge the inheritance chain, drop dead code")
    say("=" * 78)
    say(segment.report(notes=True))
    got = _run_python(segment_dir, ["run.py"])
    assert got == expected, f"segment level output differs:\n{got}\n!=\n{expected}"
    say("\nruntime check: `python run.py` in the generated folder -> identical output [OK]")
    say("\ngenerated models.py (ResNet absorbed MidModel + BaseModel):")
    say(_indent((segment_dir / "models.py").read_text()))
    for name in ("base.py", "mid.py", "utils.py"):
        say(f"\ngenerated {name}:")
        generated = segment_dir / name
        say(
            _indent(generated.read_text())
            if generated.is_file()
            else "  | <not emitted -- everything was inlined into models.py>"
        )
    results["segment"] = segment.summary
    results["ok"] = True

    say("\n" + "=" * 78)
    assert segment.summary is not None
    total_in = segment.summary["lines_in"]
    total_out = segment.summary["lines_out"]
    say(
        f"segment level slimming: {total_in} -> {total_out} lines "
        f"({100 * (total_in - total_out) // max(total_in, 1)}% smaller), behaviour unchanged"
    )
    say(f"everything is in: {tmp}")
    say("=" * 78)
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoslim.py",
        description="Slim a python project with libcst: trace the import graph, flatten "
        "imports, merge inheritance chains and drop dead code.",
    )
    sub = parser.add_subparsers(dest="command")

    slim = sub.add_parser("slim", help="slim ENTRY files into -o OUT")
    slim.add_argument("entries", nargs="+", help="entry python file(s)")
    slim.add_argument("-o", "--out", default=None, help="output directory (required unless --inspect)")
    slim.add_argument("--mode", choices=["file", "segment"], default="segment")
    slim.add_argument("--merge", choices=["none", "eliminate", "keepone"], default="none")
    slim.add_argument("--layout", choices=["flat", "mirror"], default="flat")
    slim.add_argument(
        "--prune-methods",
        action="store_true",
        help="also drop unused methods (segment mode)",
    )
    slim.add_argument(
        "--prune-decorated",
        action="store_true",
        help="drop unused defs even when they are decorated (unsafe: decorators run at import time)",
    )
    slim.add_argument("--keep-unused-imports", action="store_true")
    slim.add_argument(
        "--keep-external-imports",
        action="store_true",
        help="only drop unused imports of traced local modules",
    )
    slim.add_argument("--emit-init", action="store_true", help="also emit __init__.py files")
    slim.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="rename a generated module (repeatable)",
    )
    slim.add_argument(
        "--inspect",
        action="store_true",
        help="print the import graph / symbol tables and stop",
    )
    slim.add_argument("--notes", action="store_true", help="print per rewrite notes")

    demo = sub.add_parser("demo", help="build a tiny package, slim it and run the result")
    demo.add_argument("--workdir", default=None, help="where to put the demo (default: tempdir)")
    demo.add_argument("-q", "--quiet", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("slim", "demo", "-h", "--help"):
        argv.insert(0, "demo")  # `autoslim.py --workdir X` == `autoslim.py demo ...`
    args = build_arg_parser().parse_args(argv)
    if args.command == "demo" or args.command is None:
        quiet = bool(getattr(args, "quiet", False))
        run_demo(workdir=getattr(args, "workdir", None), verbose=not quiet)
        return 0

    mapper: dict[str, str] = {}
    for item in args.map:
        if "=" not in item:
            raise SystemExit(f"--map expects OLD=NEW, got {item!r}")
        old, new = item.split("=", 1)
        mapper[old.strip()] = new.strip()

    if not args.out and not args.inspect:
        raise SystemExit("slim needs -o/--out (or --inspect to only print the graph)")
    parser = Parser(FileEntry(args.entries))
    if args.inspect:
        parser.print()
        return 0

    mode = FileLevelCodeGenerator if args.mode == "file" else SegmentCodeGenerator
    merge = {
        "none": None,
        "eliminate": ClassMerging.Eliminate,
        "keepone": ClassMerging.KeepOne,
    }[args.merge]
    kwargs: dict[str, Any] = {
        "layout": args.layout,
        "emit_init": args.emit_init,
        "module_mapper": mapper or None,
        "prune_unused_imports": not args.keep_unused_imports,
        "prune_external_imports": not args.keep_external_imports,
    }
    if mode is SegmentCodeGenerator:
        kwargs["prune_methods"] = args.prune_methods
        kwargs["prune_decorated"] = args.prune_decorated
    elif merge:
        raise SystemExit("--merge needs --mode segment")
    codegen = mode(args.out, parser, class_merge_level=merge, **kwargs)
    summary = codegen.generate()
    print(codegen.format_report(summary))
    if args.notes:
        for note in OrderedDict.fromkeys(codegen.notes):
            print(f"  - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
