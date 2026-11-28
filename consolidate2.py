#!/data/data/com.termux/files/usr/bin/env python
"""consolidate.py -- collapse a small-to-medium Python package into a single importable file.

Usage
-----
    python consolidate.py <path-to-package> [-o OUT] [--verify] [--verify-script F]

The tool parses every module of the package with **libcst** (preserving comments),
then re-emits one flat file with this layout:

    # merged stdlib / third-party import block
    # generated runtime glue (submodule proxies, data-file access, shims)
    # constants & module-level code from the package files (name collisions resolved)
    # package data files inlined (base64 for binary, text literals for text)
    # classes   (name collisions resolved)
    # functions (name collisions resolved)
    # public API re-exports + __all__
    # CLI entry points (console_scripts from pyproject/setup.cfg)
    # __main__ guard (if the package had a CLI / __main__.py)

The consolidated file behaves like the installed package when dropped next to your
code or into site-packages (``import pkg`` keeps working, including attribute
access ``pkg.sub.attr`` and ``import pkg.sub`` via sys.modules proxies).

Correctness techniques
----------------------
* Every top-level statement is a node in a dependency graph (edges from the
  names it loads at module-execution time to the statements that define them);
  sections are then "clamped" so a statement never runs before its dependencies.
* Name collisions between package files are resolved with deterministic priority
  (public API of ``__init__`` first, then first-in-topological-order); losers are
  renamed to ``<name>__<module_path>`` with scope-aware rewriting driven by
  libcst scope analysis (local variables that shadow a global are never touched).
* Relative / absolute intra-package imports are rewritten to direct references
  (or to tiny assignments for local rebinding semantics).
* Data files are inlined and served through narrowly-scoped ``open`` /
  ``os.path`` / ``pkgutil`` / ``importlib.resources`` shims that only intercept
  paths that would have lived inside the original package directory.

Limitations (inherent to single-file flattening, reported as warnings)
----------------------------------------------------------------------
* ``globals()``/``locals()``/``eval``-based module magic, ``inspect.getsource``,
  and import-fallback chains between intra-package modules (``try: from .fast
  import x / except ImportError: from .slow import x``) collapse to the first
  branch, because all submodule code is already present.
* ``from .mod import *`` inside a *function* cannot be flattened into locals.
"""

from __future__ import annotations
import argparse
import ast as _pyast
import base64
from collections.abc import Sequence
import dataclasses
from dataclasses import dataclass, field
import keyword
import os
from pathlib import Path
import re
import shutil
import string
import subprocess
import sys
import tempfile
import textwrap


try:
    import libcst as cst
    from libcst import (
        RemovalSentinel,
        RemovalSentinel as _RS,
    )
    from libcst.metadata import (
        ExpressionContextProvider,
        MetadataWrapper,
        ParentNodeProvider,
        ScopeProvider,
    )
    from libcst.metadata.scope_provider import (
        BuiltinScope,
        ClassScope,
        ComprehensionScope,
        FunctionScope,
        GlobalScope,
    )
except ImportError:  # pragma: no cover
    sys.stderr.write("consolidate.py requires libcst:  pip install libcst\n")
    raise

__version__ = "1.0.0"

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

STDLIB_NAMES = frozenset(getattr(sys, "stdlib_module_names", ())) | {
    "__future__",
    "typing_extensions",
}

TEXT_SUFFIXES = {
    ".json",
    ".txt",
    ".text",
    ".csv",
    ".tsv",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".yaml",
    ".yml",
    ".md",
    ".rst",
    ".xml",
    ".html",
    ".htm",
    ".svg",
    ".sql",
    ".properties",
    ".env",
    ".po",
    ".pot",
    ".tmpl",
    ".template",
    ".jinja",
    ".j2",
    ".sty",
    ".lang",
    ".list",
    ".mask",
    ".grammar",
    ".g4",
    ".asc",
    ".pem-text",
    ".crt-text",
    ".gitignore",
    ".editorconfig",
    ".in",
    ".meta",
}

SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".svn",
    ".tox",
    ".eggs",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    "build",
    "dist",
}

SECTION_CONST = "const"
SECTION_DATA = "data"
SECTION_CLASS = "class"
SECTION_FUNC = "func"
SECTION_REEXPORT = "reexport"
SECTION_MAIN = "main"

SECTION_ORDER = [
    SECTION_CONST,
    SECTION_DATA,
    SECTION_CLASS,
    SECTION_FUNC,
    SECTION_REEXPORT,
    SECTION_MAIN,
]

SECTION_TITLES = {
    SECTION_CONST: "constants & module-level code (execution order preserved)",
    SECTION_DATA: "package data files, inlined",
    SECTION_CLASS: "classes",
    SECTION_FUNC: "functions",
    SECTION_REEXPORT: "public API re-exports & __all__",
    SECTION_MAIN: "__main__ / CLI",
}


def rel_posix(path: str | Path) -> str:
    return Path(path).as_posix()


def module_suffix(dotted: str, pkg_name: str) -> str:
    """pkg.utils -> 'utils';  pkg -> 'pkg' (top package, i.e. __init__)."""
    if dotted == pkg_name:
        return "_init"
    tail = dotted[len(pkg_name) + 1 :] if dotted.startswith(pkg_name + ".") else dotted
    return tail.replace(".", "_") or "_init"


def is_keyword_or_invalid(name: str) -> bool:
    return not name.isidentifier() or keyword.iskeyword(name)


def safe_identifier(name: str) -> str:
    out = re.sub(r"\W", "_", name)
    if not out or out[0].isdigit():
        out = "_" + out
    if keyword.iskeyword(out):
        out += "_"
    return out


def classify_data_bytes(data: bytes, suffix: str) -> str:
    """Decide 'text' vs 'binary' for an inlined data file."""
    if suffix.lower() in TEXT_SUFFIXES:
        # still verify it is decodable text without NUL
        try:
            text = data.decode("utf-8")
            if "\x00" not in text:
                return "text"
        except UnicodeDecodeError:
            pass
        return "binary"
    # For unknown suffixes, sniff: valid UTF-8, no NUL, mostly printable.
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    if "\x00" in text:
        return "binary"
    if not text:
        return "text"
    printable = sum(ch in string.printable or ch in "\n\r\t" or ord(ch) > 0x7F for ch in text)
    return "text" if printable / len(text) > 0.85 else "binary"


def py_string_literal(text: str) -> str:
    """Return a Python single-line string literal for *text*."""
    return _pyast.dump(_pyast.parse("x=1")) and repr(text)


def wrap_base64(b64: str, width: int = 76) -> str:
    return "\n".join(b64[i : i + width] for i in range(0, len(b64), width))


def parse_literal_list(node) -> list[str] | None:
    """If a libcst expression is a list/tuple of string literals, return them."""
    if isinstance(node, (cst.List, cst.Tuple)):
        out = []
        for el in node.elements:
            v = el.value
            if isinstance(v, cst.SimpleString):
                try:
                    out.append(_pyast.literal_eval(v.value))
                except Exception:
                    return None
            elif isinstance(v, (cst.List, cst.Tuple)):
                nested = parse_literal_list(v)
                if nested is None:
                    return None
                out.extend(nested)
            elif isinstance(v, cst.ConcatenatedString):
                return None
            else:
                return None
        return out
    if isinstance(node, cst.SimpleString):
        try:
            v = _pyast.literal_eval(node.value)
        except Exception:
            return None
        return [v] if isinstance(v, str) else None
    return None


def static_string_value(node) -> str | None:
    """Best-effort static evaluation of a SimpleString / concatenation."""
    if isinstance(node, cst.SimpleString):
        try:
            v = _pyast.literal_eval(node.value)
            return v if isinstance(v, str) else None
        except Exception:
            return None
    if isinstance(node, cst.ConcatenatedString):
        left = static_string_value(node.left)
        right = static_string_value(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def compare_is_main_guard(stmt) -> bool:
    """True for ``if __name__ == "__main__":`` (either operand order)."""
    if not isinstance(stmt, cst.If):
        return False
    t = stmt.test
    if isinstance(t, cst.Comparison):
        if not isinstance(t.left, cst.Name) and not isinstance(t.left, cst.SimpleString):
            return False
        for comp in t.comparisons:
            if not isinstance(comp.operator, cst.Equal):
                continue
            pair = (t.left, comp.comparator)
            for a, b in (pair, (pair[1], pair[0])):
                if isinstance(a, cst.Name) and a.value == "__name__" and isinstance(b, cst.SimpleString):
                    try:
                        if _pyast.literal_eval(b.value) == "__main__":
                            return True
                    except Exception:
                        pass
    return False


def is_dead_branch(test) -> bool:
    """``if TYPE_CHECKING:`` / ``if False:`` / ``if 0:`` bodies never execute."""
    if isinstance(test, cst.Name):
        return test.value in {"TYPE_CHECKING", "False"}
    if isinstance(test, cst.Integer):
        try:
            return int(test.value) == 0
        except Exception:
            return False
    if isinstance(test, cst.Attribute):  # typing.TYPE_CHECKING
        return test.attr.value == "TYPE_CHECKING"
    return False


# --------------------------------------------------------------------------- #
# Package discovery
# --------------------------------------------------------------------------- #


@dataclass
class ModuleInfo:
    """One .py file of the package."""

    dotted: str  # "mypkg.utils.text"
    path: Path
    rel_posix_path: str  # "utils/text.py" relative to package dir
    tree: cst.Module = None  # type: ignore[assignment]
    wrapper: MetadataWrapper = None  # type: ignore[assignment]
    scope: GlobalScope = None  # type: ignore[assignment]
    # names bound by module-level imports that resolve to stdlib/3rd-party
    ext_imports: list[dict] = field(default_factory=list)
    has_main_guard: bool = False

    @property
    def is_pkg_init(self) -> bool:
        # any __init__.py (top package or subpackage) anchors relative imports
        return self.rel_posix_path == "__init__.py" or self.rel_posix_path.endswith("/__init__.py")

    @property
    def is_top_init(self) -> bool:
        return self.rel_posix_path == "__init__.py"


@dataclass
class DataFile:
    rel_posix_path: str  # "fonts/F.ttf" relative to package dir
    abs_path: Path
    kind: str = "binary"  # "binary" | "text"
    text: str | None = None  # decoded text when kind == "text"
    b64: str | None = None  # base64 payload when kind == "binary"


@dataclass
class EntryPoints:
    """console_scripts / gui_scripts of the package."""

    script_name: str
    func_ref: str  # "mypkg.cli:main"
    module_dotted: str
    func_name: str


def read_entry_points(pkg_root: Path, pkg_name: str) -> list[EntryPoints]:
    """Read console_scripts from pyproject.toml / setup.py / setup.cfg, best-effort."""
    out: list[EntryPoints] = []
    toml = pkg_root / "pyproject.toml"
    try:
        import tomllib
    except ImportError:
        tomllib = None

    def add(ref: str) -> None:
        ref = ref.strip()
        if ":" not in ref:
            return
        mod, func = ref.split(":", 1)
        mod, func = mod.strip(), func.strip()
        if not mod.startswith(pkg_name):
            return
        out.append(EntryPoints(ref.split(":")[0], ref, mod, func))

    if toml.is_file() and tomllib is not None:
        try:
            data = tomllib.loads(toml.read_text("utf-8", errors="replace"))
            for sect in ("scripts", "gui-scripts"):
                for ref in (data.get("project", {}).get(sect) or {}).values():
                    add(str(ref))
            cs = (data.get("tool", {}).get("poetry", {}).get("scripts")) or {}
            for ref in cs.values():
                add(str(ref))
            cs = (data.get("project", {}).get("entry-points", {}) or {}).get("console-scripts") or {}
            for ref in cs.values():
                add(str(ref))
        except Exception:
            pass

    for cfg in (pkg_root / "setup.cfg",):
        try:
            import configparser

            cp = configparser.ConfigParser()
            cp.read(cfg)
            if cp.has_section("options.entry_points"):
                for name, ref in cp.items("options.entry_points"):
                    add(str(ref))
        except Exception:
            pass

    setup_py = pkg_root / "setup.py"
    if setup_py.is_file():
        try:
            src = setup_py.read_text("utf-8", errors="replace")
            for m in re.finditer(r"['\"]console_scripts['\"]\s*:\s*{([^}]*)}", src, re.DOTALL):
                body = m.group(1)
                for pair in re.finditer(r"['\"]([^'\"]+)['\"]\s*:\s*['\"]([^'\"]+)['\"]", body):
                    add(pair.group(2))
            for m in re.finditer(
                r"entry_points\s*=\s*{[^}]*['\"]console_scripts['\"]\s*:\s*\[([^\]]*)\]",
                src,
                re.DOTALL,
            ):
                for pair in re.finditer(r"['\"]([^'\"]=]+)=([^'\"]+)['\"]", m.group(1)):
                    add(pair.group(2))
        except Exception:
            pass

    # de-dup, keep order
    seen, uniq = set(), []
    for ep in out:
        if ep.func_ref not in seen:
            seen.add(ep.func_ref)
            uniq.append(ep)
    return uniq


def discover(pkg_root: Path, pkg_name: str) -> tuple[list[ModuleInfo], list[DataFile]]:
    """Collect .py modules (as ModuleInfo) and data files of the package."""
    modules: list[ModuleInfo] = []
    data_files: list[DataFile] = []
    pkg_root = pkg_root.resolve()
    init = pkg_root / "__init__.py"
    if not init.is_file():
        raise SystemExit(
            f"error: {pkg_root} does not look like a package (no __init__.py). "
            "For a namespace-style layout pass the parent directory and use --name."
        )

    for dirpath, dirnames, filenames in os.walk(pkg_root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = p.relative_to(pkg_root).as_posix()
            if fn.endswith((".py", ".pyi")):
                if fn.endswith(".pyi") and (p.with_suffix(".py")).is_file():
                    continue  # prefer the .py when both exist
                if fn == "setup.py" and rel == "setup.py":
                    continue
                dotted = (
                    pkg_name
                    if rel == "__init__.py"
                    else (
                        pkg_name + "." + rel[:-3].replace("/", ".")
                        if fn != "__init__.py"
                        else pkg_name + "." + Path(rel).parent.as_posix().replace("/", ".")
                    )
                )
                modules.append(ModuleInfo(dotted=dotted, path=p, rel_posix_path=rel))
            elif fn in (
                "pyproject.toml",
                "setup.py",
                "setup.cfg",
                "MANIFEST.in",
                "PKG-INFO",
                "METADATA",
                "NOTICE",
                "LICENSE",
                "LICENSE.txt",
                "requirements.txt",
                "tox.ini",
                "conftest.py",
            ):
                continue  # packaging metadata, not runtime data
            else:
                kind = classify_data_bytes(p.read_bytes(), p.suffix)
                df = DataFile(rel_posix_path=rel, abs_path=p, kind=kind)
                if kind == "text":
                    df.text = p.read_text("utf-8")
                else:
                    df.b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                data_files.append(df)

    modules.sort(key=lambda m: (m.rel_posix_path != "__init__.py", m.dotted))
    return modules, data_files


def topo_sort_modules(modules: list[ModuleInfo], warnings_out: list[str]) -> list[ModuleInfo]:
    """Order modules so intra-package importers run after imported modules.

    Handles cycles by dropping the back-edge of a cycle (with a warning).
    """
    by_dotted = {m.dotted: m for m in modules}
    deps: dict[str, set[str]] = {m.dotted: set() for m in modules}
    incoming: dict[str, set[str]] = {m.dotted: set() for m in modules}

    for m in modules:
        # cheap AST scan for imported dotted names
        try:
            pytree = _pyast.parse(m.path.read_text("utf-8"), filename=str(m.path))
        except SyntaxError as e:
            raise SystemExit(f"error: cannot parse {m.path}: {e}")
        for node in _pyast.walk(pytree):
            if isinstance(node, _pyast.Import):
                for alias in node.names:
                    dep = alias.name
                    if dep in by_dotted and dep != m.dotted:
                        deps[m.dotted].add(dep)
                        incoming[dep].add(m.dotted)
            elif isinstance(node, _pyast.ImportFrom) and node.level:
                # relative import: resolve against the package
                base = m.dotted.split(".")
                # for __init__, level 1 == the package itself
                up = node.level - (1 if m.is_pkg_init else 0)
                anchor = base[: len(base) - up] if up else base
                target = ".".join(anchor)
                if node.module:
                    target = (target + "." + node.module) if target else node.module
                # a ``from .x import y`` may import module x or attribute y of x
                for cand in [target] + [f"{target}.{a.name}" for a in node.names]:
                    if cand in by_dotted and cand != m.dotted:
                        deps[m.dotted].add(cand)
                        incoming[cand].add(m.dotted)

    result: list[ModuleInfo] = []
    ready = [m.dotted for m in modules if not deps[m.dotted]]
    # keep deterministic order
    order_index = {m.dotted: i for i, m in enumerate(modules)}
    ready.sort(key=lambda d: order_index[d])
    remaining = set(by_dotted) - set(ready)
    while ready:
        d = ready.pop(0)
        result.append(by_dotted[d])
        for dependent in sorted(incoming[d], key=lambda x: order_index.get(x, 1 << 30)):
            if d in deps[dependent]:
                deps[dependent].discard(d)
                if not deps[dependent]:
                    ready.append(dependent)
                    ready.sort(key=lambda x: order_index[x])
                    remaining.discard(dependent)
    if remaining:
        # cycle: emit remaining in file order, note the cycle
        cyc = ", ".join(sorted(remaining))
        warnings_out.append(
            f"circular import detected among: {cyc}. The consolidated file keeps "
            "all statements but the first cross-module use may need the function "
            "indirection that already exists in the original code."
        )
        result.extend(by_dotted[d] for d in sorted(remaining, key=lambda x: order_index[x]))
    return result


# --------------------------------------------------------------------------- #
# Per-module analysis
# --------------------------------------------------------------------------- #


@dataclass
class StmtInfo:
    """A top-level (or flattened-out) statement of one module."""

    module: ModuleInfo
    node: cst.CSTNode  # the statement (SimpleStatementLine / BaseCompoundStatement)
    order: int  # original index within the flattened stream
    stores: set[str] = field(default_factory=set)  # global names it binds
    loads: set[str] = field(default_factory=set)  # global names it reads
    deferred: bool = False  # def/class/assign-to-callable → body not executed now
    loads_at_exec: set[str] = field(default_factory=set)  # names read at module-exec time
    star_export: str | None = None  # "mod.dotted" for `from .x import *`
    is_del: bool = False

    @property
    def display(self) -> str:
        return f"{self.module.dotted}#{self.order}"


def _iter_flat(stmts: list[cst.CSTNode], out: list[tuple[cst.CSTNode, int]], counter: list[int]):
    """Flatten if/try/for/while bodies at module level into the main stream.

    ``if`` statements whose branches are both live become sequential code
    guarded by their conditions; ``__main__`` guards and TYPE_CHECKING blocks
    are separated out.
    """
    for stmt in stmts:
        counter[0] += 1
        if isinstance(stmt, cst.If):
            if compare_is_main_guard(stmt):
                out.append(("__MAIN__", stmt, counter[0]))
                continue
            if is_dead_branch(stmt.test):
                if stmt.orelse is not None:
                    _iter_flat(_as_body(stmt.orelse), out, counter)
                continue
            # live conditional: keep as-is (it may be load-bearing)
            out.append((stmt, counter[0]))
            continue
        if isinstance(stmt, cst.Try):
            # hoist try body; keep except handlers as separate try statements
            _iter_flat(stmt.body.body, out, counter)
            for handler in stmt.handlers:
                cst.Try(
                    body=cst.IndentedBlock(body=[(cst.Raise() if False else _hoisted_handler_body(handler))]),
                    handlers=[],
                )
                # represent the handler as: try: <original try body> except E as n: <handler>
                # Simplest faithful mapping: keep the entire original try as one
                # statement is safer for import fallbacks; but import fallbacks
                # across intra-package modules must collapse. Handle below via
                # import rewriting instead.
            out.append((stmt, counter[0]))
            continue
        out.append((stmt, counter[0]))


def _as_body(orelse) -> list[cst.CSTNode]:
    if isinstance(orelse, cst.Else):
        return list(orelse.body.body)
    if isinstance(orelse, cst.If):
        return [orelse]
    return []


def _hoisted_handler_body(handler):  # pragma: no cover - unused, kept for clarity
    raise NotImplementedError


def iter_module_statements(mod: ModuleInfo, warnings: list[str]) -> tuple[list[StmtInfo], cst.If | None]:
    """Return the flattened top-level statement stream of a module + main guard."""
    counter = [0]

    stream: list[tuple[cst.CSTNode, int]] = []
    main_guard: cst.If | None = None
    for stmt in mod.tree.body:
        if isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) > 1:
            # split `a = 1; b = 2` into separate statements
            for j, s in enumerate(stmt.body):
                line = cst.SimpleStatementLine(
                    body=[s],
                    leading_lines=stmt.leading_lines if j == 0 else [],
                    trailing_whitespace=(
                        stmt.trailing_whitespace if j == len(stmt.body) - 1 else cst.TrailingWhitespace()
                    ),
                )
                counter[0] += 1
                stream.append((line, counter[0]))
            continue
        if isinstance(stmt, cst.If) and compare_is_main_guard(stmt):
            main_guard = stmt
            continue
        if isinstance(stmt, cst.If) and is_dead_branch(stmt.test):
            if stmt.orelse is not None:
                for s in _as_body(stmt.orelse):
                    counter[0] += 1
                    stream.append((s, counter[0]))
            continue
        counter[0] += 1
        stream.append((stmt, counter[0]))

    infos = []
    for stmt, idx in stream:
        info = StmtInfo(module=mod, node=stmt, order=idx)
        _classify_statement(info, warnings)
        infos.append(info)
    return infos, main_guard


def _names_of_target(target) -> list[str]:
    """All names bound by an assignment target (Name / Tuple / List / Starred / Subscript / Attribute)."""
    if isinstance(target, cst.Name):
        return [target.value]
    if isinstance(target, (cst.Tuple, cst.List)):
        out = []
        for el in target.elements:
            out.extend(_names_of_target(el.value))
        return out
    if isinstance(target, cst.StarredElement):
        return _names_of_target(target.value)
    if isinstance(target, (cst.StarredElement, cst.Attribute)):
        # attribute target binds nothing new; subscript target binds nothing new
        return []
    if isinstance(target, cst.Subscript):
        return []
    return []


def _classify_statement(info: StmtInfo, warnings: list[str]) -> None:
    """Fill stores/loads/deferred/star_export for one top-level statement."""
    node = info.node

    if isinstance(node, (cst.FlattenSentinel, _StmtBundle)):
        # multi-statement expansion from import rewriting: classify each part
        for s in node.nodes if isinstance(node, cst.FlattenSentinel) else node.stmts:
            sub = StmtInfo(module=info.module, node=s, order=info.order)
            _classify_statement(sub, warnings)
            info.stores.update(sub.stores)
            info.loads.update(sub.loads)
            info.loads_at_exec.update(sub.loads_at_exec)
            info.deferred = info.deferred and sub.deferred
            if sub.star_export:
                info.star_export = sub.star_export
            info.is_del = info.is_del or sub.is_del
        return

    if isinstance(node, cst.SimpleStatementLine):
        small = node.body[0] if len(node.body) == 1 else None
        if small is None:
            # multi-statement line was split earlier; defensive fallback
            small = node.body[0]
        if isinstance(small, cst.Assign):
            for t in small.targets:
                info.stores.update(_names_of_target(t.target))
            info.loads_at_exec.update(_loads_of_expression(small.value))
            info.loads.update(_loads_of_expression(small.value))
            # If RHS is a lambda/def-reference it's deferred-safe; a call is not
            if _is_runtime_call(small.value):
                info.deferred = False
            info.deferred = bool(_is_deferred_value(small.value))
        elif isinstance(small, cst.AnnAssign):
            if small.value is not None:
                info.stores.update(_names_of_target(small.target))
                info.loads.update(_loads_of_expression(small.value))
                info.loads_at_exec.update(_loads_of_expression(small.value))
                info.deferred = _is_deferred_value(small.value)
            else:
                info.stores.update(_names_of_target(small.target))
                info.deferred = True  # bare annotation binds nothing at runtime
        elif isinstance(small, cst.AugAssign):
            info.stores.update(_names_of_target(small.target))
            info.loads.update(_names_of_target(small.target))
            info.loads.update(_loads_of_expression(small.value))
            info.loads_at_exec.update(_names_of_target(small.target))
            info.loads_at_exec.update(_loads_of_expression(small.value))
        elif isinstance(small, cst.FunctionDef):
            info.stores.add(small.name.value)
            info.deferred = True
            # decorators & defaults evaluate now
            for d in small.decorator_list:
                info.loads.update(_loads_of_expression(d.decorator))
                info.loads_at_exec.update(_loads_of_expression(d.decorator))
            for p in small.params.params + small.params.posonly_params + small.params.kwonly_params:
                if p.default is not None:
                    info.loads.update(_loads_of_expression(p.default))
                    info.loads_at_exec.update(_loads_of_expression(p.default))
            if small.returns is not None:
                info.loads.update(_loads_of_annotation(small.returns))
        elif isinstance(small, cst.ClassDef):
            info.stores.add(small.name.value)
            info.deferred = True
            for d in small.decorator_list:
                info.loads.update(_loads_of_expression(d.decorator))
                info.loads_at_exec.update(_loads_of_expression(d.decorator))
            for b in small.bases:
                info.loads.update(_loads_of_expression(b.value))
                info.loads_at_exec.update(_loads_of_expression(b.value))
            for kw in small.keywords or []:
                info.loads.update(_loads_of_expression(kw.value))
                info.loads_at_exec.update(_loads_of_expression(kw.value))
            # class body names that reference module globals
            info.loads.update(_loads_inside_class_body(small))
        elif isinstance(small, (cst.Import, cst.ImportFrom)):
            _classify_import(info, small, warnings)
        elif isinstance(small, cst.Del):
            info.is_del = True
            for t in small.targets if isinstance(small.targets, list) else [small.target]:
                info.stores.update(_names_of_target(t.target if hasattr(t, "target") else t))
            info.loads.update(n for n in info.stores)
        elif isinstance(small, (cst.Global, cst.Nonlocal)):
            pass  # local-only concerns at top level
        elif isinstance(small, cst.Expr):
            info.loads.update(_loads_of_expression(small.value))
            info.loads_at_exec.update(_loads_of_expression(small.value))
        elif isinstance(small, cst.Assert):
            info.loads.update(_loads_of_expression(small.test))
            info.loads_at_exec.update(_loads_of_expression(small.test))
            if small.msg is not None:
                info.loads.update(_loads_of_expression(small.msg))
        elif isinstance(small, cst.Raise):
            if small.exc is not None:
                info.loads.update(_loads_of_expression(small.exc))
                info.loads_at_exec.update(_loads_of_expression(small.exc))
        elif isinstance(small, cst.Return):
            if small.value is not None:
                info.loads.update(_loads_of_expression(small.value))
        else:  # pass / break / continue etc.
            pass
        # leading_comments don't affect semantics
        return

    if isinstance(node, (cst.FunctionDef, cst.ClassDef)):
        # standalone (dedented) def/class -- shouldn't normally occur at top level
        info.stores.add(node.name.value)
        info.deferred = True
        return

    if isinstance(node, cst.If):
        info.loads.update(_loads_of_expression(node.test))
        info.loads_at_exec.update(_loads_of_expression(node.test))
        # conservative: include loads of both branches (they stay guarded)
        info.loads.update(_loads_inside_if(node))
        return

    if isinstance(node, (cst.For, cst.While, cst.With, cst.Try, cst.TryStar)):
        if isinstance(node, cst.For):
            info.stores.update(_names_of_target(node.target))
            info.loads.update(_loads_of_expression(node.iter))
        elif isinstance(node, cst.While):
            info.loads.update(_loads_of_expression(node.test))
        elif isinstance(node, cst.With):
            for item in node.items:
                info.loads.update(_loads_of_expression(item.item))
                if item.asname is not None:
                    info.stores.update(_names_of_target(item.asname))
        elif isinstance(node, (cst.Try, cst.TryStar)):
            info.loads.update(_loads_inside_try(node))
        # names assigned in the body are also module globals (Python semantics)
        info.stores.update(_stores_inside_body(_body_of(node)))
        info.loads.update(_loads_inside_body(_body_of(node), include_comprehension=False))
        return

    if isinstance(node, cst.Match):
        info.loads.update(_loads_of_expression(node.subject))
        info.loads.update(_loads_inside_body(node.cases[0].body) if node.cases else set())
        info.stores.update(_stores_inside_body(node.cases[0].body) if node.cases else set())
        return

    # unknown: assume it loads and stores everything it mentions
    info.loads.update(_collect_all_names(node, only_load=True))


def _body_of(node):
    if isinstance(node, (cst.For, cst.While, cst.Try, cst.TryStar)):
        return node.body.body
    if isinstance(node, cst.With):
        return node.body.body
    if isinstance(node, (cst.FunctionDef, cst.ClassDef)):
        return node.body.body
    return []


def _classify_import(info: StmtInfo, imp, warnings: list[str]) -> None:
    """Mark stores for an import statement; intra-pkg rewrites happen later."""
    if isinstance(imp, cst.Import):
        for alias in imp.names:
            dotted = _dotted_name(alias.name) or ""
            bound = alias.asname.name.value if alias.asname else dotted.split(".")[0]
            info.stores.add(bound)
        info.deferred = True
    elif isinstance(imp, cst.ImportFrom):
        if isinstance(imp.names, cst.ImportStar):
            info.star_export = "*"  # resolved later
            info.deferred = True
            return
        for alias in imp.names:
            bound = alias.asname.name.value if alias.asname else alias.name.value
            info.stores.add(bound)
        info.deferred = True


def _is_runtime_call(expr) -> bool:
    return isinstance(expr, cst.Call)


def _is_deferred_value(expr) -> bool:
    """Values that don't call user code at module exec time."""
    if isinstance(
        expr,
        (
            cst.Lambda,
            cst.Name,
            cst.Attribute,
            cst.SimpleString,
            cst.Integer,
            cst.Float,
            cst.BaseNumber,
            cst.BaseString,
        ),
    ):
        return True
    if isinstance(expr, (cst.List, cst.Tuple, cst.Set, cst.Dict)):
        return all(
            _is_deferred_value(el.value)
            for el in (
                expr.elements if not isinstance(expr, cst.Dict) else [e for e in expr.elements if e.value is not None]
            )
        )
    return False


def _loads_of_annotation(node) -> set[str]:
    """Names referenced by a type annotation (string or expression)."""
    out: set[str] = set()
    if isinstance(node, cst.SimpleString):
        try:
            src = _pyast.literal_eval(node.value)
            if isinstance(src, str):
                for part in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", src):
                    out.add(part.split(".")[0])
        except Exception:
            pass
        return out
    return _loads_of_expression(node)


def _loads_of_expression(node) -> set[str]:
    """Names an expression *reads* (LOAD contexts) -- conservative, includes names
    that resolve to builtins; refined later against module globals."""
    out: set[str] = set()

    def rec(n):
        if isinstance(n, cst.Name):
            out.add(n.value)
        elif isinstance(n, cst.Lambda):
            return  # body is deferred (but defaults were collected elsewhere)
        elif isinstance(n, cst.SimpleString):
            return
        for ch in n.children:
            rec(ch)

    rec(node)
    return out


def _loads_inside_class_body(cls: cst.ClassDef) -> set[str]:
    out: set[str] = set()

    def rec(n, in_func=False):
        if isinstance(n, (cst.FunctionDef, cst.Lambda)):
            for ch in n.children:
                if ch is not n.name:
                    pass
            # skip function bodies for exec-time loads, but record decorators/defaults
            for d in n.decorator_list if isinstance(n, cst.FunctionDef) else []:
                rec(d.decorator)
            if isinstance(n, cst.FunctionDef):
                for p in _all_params(n):
                    if p.default is not None:
                        rec(p.default)
            return
        if isinstance(n, cst.Name):
            out.add(n.value)
        for ch in n.children:
            rec(ch)

    for stmt in cls.body.body:
        rec(stmt)
    return out


def _loads_inside_if(node: cst.If) -> set[str]:
    out: set[str] = set()

    def rec_stmt(s):
        if isinstance(s, cst.SimpleStatementLine):
            for small in s.body:
                if isinstance(small, cst.Assign):
                    for t in small.targets:
                        out.update(_loads_of_expression(t.target))
                    out.update(_loads_of_expression(small.value))
                elif isinstance(small, cst.Expr):
                    out.update(_loads_of_expression(small.value))
        elif isinstance(s, cst.If):
            out.update(_loads_of_expression(s.test))
            for sub in s.body.body:
                rec_stmt(sub)
            if s.orelse is not None:
                for sub in _as_body(s.orelse):
                    rec_stmt(sub)

    for s in node.body.body:
        rec_stmt(s)
    if node.orelse is not None:
        for s in _as_body(node.orelse):
            rec_stmt(s)
    return out


def _loads_inside_try(node) -> set[str]:
    out: set[str] = set()
    for s in node.body.body:
        if isinstance(s, cst.SimpleStatementLine):
            for small in s.body:
                if isinstance(small, cst.Expr):
                    out.update(_loads_of_expression(small.value))
    return out


def _stores_inside_body(body: Sequence[cst.CSTNode]) -> set[str]:
    out: set[str] = set()
    for stmt in body:
        if isinstance(stmt, cst.SimpleStatementLine):
            for small in stmt.body:
                if isinstance(small, cst.Assign):
                    for t in small.targets:
                        out.update(_names_of_target(t.target))
                elif isinstance(small, (cst.FunctionDef, cst.ClassDef)):
                    out.add(small.name.value)
                elif isinstance(small, cst.AugAssign):
                    out.update(_names_of_target(small.target))
        elif isinstance(stmt, (cst.For, cst.While, cst.With, cst.Try, cst.TryStar, cst.If)):
            out.update(_stores_inside_body(_body_of(stmt)))
            if isinstance(stmt, cst.If) and stmt.orelse is not None:
                for s in _as_body(stmt.orelse):
                    out.update(_stores_inside_body([s]))
            if isinstance(stmt, (cst.Try, cst.TryStar)):
                for h in stmt.handlers:
                    out.update(_stores_inside_body(h.body.body))
                if stmt.orelse is not None:
                    out.update(_stores_inside_body(stmt.orelse.body.body))
                if stmt.finalbody is not None:
                    out.update(_stores_inside_body(stmt.finalbody.body))
            if isinstance(stmt, cst.For):
                out.update(_names_of_target(stmt.target.target))
    return out


def _loads_inside_body(body: Sequence[cst.CSTNode], include_comprehension: bool = True) -> set[str]:
    """All names read anywhere inside a statement's body (used for top-level
    compound statements whose bodies execute at module time)."""
    out: set[str] = set()

    def rec(n, depth=0):
        if isinstance(n, (cst.FunctionDef, cst.ClassDef)):
            # class body executes now (but its methods don't); function bodies are deferred
            if isinstance(n, cst.ClassDef):
                out.update(_loads_inside_class_body(n))
            else:
                for d in n.decorator_list:
                    out.update(_loads_of_expression(d.decorator))
                for p in _all_params(n):
                    if p.default is not None:
                        out.update(_loads_of_expression(p.default))
            return
        if isinstance(n, cst.Lambda):
            return
        if isinstance(n, cst.Name):
            out.add(n.value)
            return
        for ch in n.children:
            rec(ch)

    for stmt in body:
        rec(stmt)
    return out


def _all_params(fdef: cst.FunctionDef):
    p = fdef.params
    return list(p.params) + list(p.posonly_params) + list(p.kwonly_params)


def _collect_all_names(node, only_load=True) -> set[str]:
    out: set[str] = set()

    def rec(n):
        if isinstance(n, cst.Name):
            out.add(n.value)
        for ch in n.children:
            rec(ch)

    rec(node)
    return out


# --------------------------------------------------------------------------- #
# Scope-aware renaming (collision resolution core)
# --------------------------------------------------------------------------- #


@dataclass
class RenamePlan:
    """per-module: old global name -> new global name"""

    module: str
    mapping: dict[str, str] = field(default_factory=dict)


class _SkipFieldsOfName:
    """Node types/fields where a Name node must NOT be treated as a reference
    to the module global (attribute access, def/class name, import alias, etc.)."""

    # (type_name, field_name) pairs
    SKIP = {
        ("Attribute", "attr"),
        ("FunctionDef", "name"),
        ("ClassDef", "name"),
        ("ImportAlias", "name"),
        ("ImportAlias", "asname"),
        ("AsName", "name"),
        ("Global", "names"),
        ("Nonlocal", "names"),
        ("ExceptHandler", "name"),
        ("Import", "names"),
        ("ImportFrom", "names"),
        ("NameItem", "name"),
        ("Param", "name"),
        ("ParamSpec", "name"),
    }


def _name_is_reference(parent, field: str) -> bool:
    """A Name node reached via parent.field is a *reference* (not a binding site
    or attribute slot) unless listed in SKIP."""
    ptype = type(parent).__name__
    if (ptype, field) in _SkipFieldsOfName.SKIP:
        return False
    return not (ptype in ("Arg",) and field == "keyword")


def _build_parent_field_map(tree: cst.Module) -> dict[int, tuple[cst.CSTNode, str]]:
    """id(node) -> (parent, field name)."""
    out: dict[int, tuple[cst.CSTNode, str]] = {}

    class V(cst.CSTVisitor):
        def on_visit(self, node):
            for f in dataclasses.fields(node):
                v = getattr(node, f.name)
                if isinstance(v, cst.CSTNode):
                    out[id(v)] = (node, f.name)
                elif isinstance(v, (list, tuple)) and v and isinstance(v[0], cst.CSTNode):
                    for item in v:
                        if isinstance(item, cst.CSTNode):
                            out[id(item)] = (node, f.name)
            return True

    tree.visit(V())
    return out


def _scope_of_node(scopes: dict[cst.CSTNode, object], node: cst.CSTNode):
    return scopes.get(node)


def rename_in_module(
    mod: ModuleInfo,
    mapping: dict[str, str],
    extra_module_names: set[str],
) -> None:
    """Apply a global-name renaming *mapping* to a module **in place**, scope-aware.

    Only Name nodes whose scope analysis says they resolve to the module's
    GlobalScope (or the walrus-in-comprehension edge case) are renamed.
    Local variables shadowing a global are left untouched.
    """
    if not mapping:
        return
    wrapper = mod.wrapper
    # NOTE: MetadataWrapper deep-copies the tree during resolve(); always work
    # with the wrapper's own module afterwards so node ids line up.
    tree = wrapper.module
    wrapper.resolve(ScopeProvider)
    wrapper.resolve(ParentNodeProvider)

    # ---- pass 1: collect Name nodes to rename ----
    targets: list[tuple[cst.Name, str, bool]] = []  # (node, new_name, is_store)

    class Collector(cst.CSTVisitor):
        METADATA_DEPENDENCIES = [
            ScopeProvider,
            ExpressionContextProvider,
            ParentNodeProvider,
        ]

        def visit_Name(self, node: cst.Name) -> None:
            if node.value not in mapping:
                return
            parent = self.get_metadata(ParentNodeProvider, node, None)
            if parent is None:
                return
            # find the field of node within parent
            field = None
            for f in dataclasses.fields(parent):
                v = getattr(parent, f.name)
                if v is node:
                    field = f.name
                    break
                if isinstance(v, (list, tuple)):
                    for item in v:
                        if item is node:
                            field = f.name
                            break
            if field is None:
                return
            # --- three cases ---
            # 1) binding sites must be renamed too: def/class name,
            #    import asname
            if isinstance(parent, (cst.FunctionDef, cst.ClassDef)) and field == "name":
                targets.append((node, mapping[node.value], True))
                return
            if isinstance(parent, cst.AsName) and field == "name":
                targets.append((node, mapping[node.value], True))
                return
            # 2) never touch these (not references to the global)
            if not _name_is_reference(parent, field):
                return
            scope = self.get_metadata(ScopeProvider, node, None)
            if isinstance(scope, (FunctionScope, ComprehensionScope)):
                # name may be local to that scope -- only rename if it
                # actually resolves to the module global
                assign = scope.assignments.get(node.value, [])
                if assign:
                    return  # truly local binding, skip
                # check enclosing scopes for a local binding
                if _shadowed_locally(scope, node.value):
                    return
            ctx = self.get_metadata(ExpressionContextProvider, node, None)
            is_store = ctx is not None and "STORE" in str(ctx)
            targets.append((node, mapping[node.value], is_store))

    wrapper.visit(Collector())

    if not targets:
        return

    # ---- pass 2: rebuild tree with renamed nodes ----
    rename_ids = {id(n) for n, _, _ in targets}
    new_names = {id(n): new for n, new, _ in targets}
    {id(n) for n, _, s in targets if s}

    class Renamer(cst.CSTTransformer):
        def leave_Name(self, node, updated):
            if id(node) in rename_ids:
                new = new_names[id(node)]
                return updated.with_changes(value=new)
            return updated

    new_tree = tree.visit(Renamer())
    mod.tree = new_tree
    # keep metadata in sync for later passes (unsafe_skip_copy: tree is private
    # to this module and we need stable node ids)
    mod.wrapper = MetadataWrapper(new_tree, unsafe_skip_copy=True)


def _shadowed_locally(scope, name: str) -> bool:
    """Whether a non-global scope or any descendant assigns *name* locally."""
    cur = scope
    while cur is not None and not isinstance(cur, GlobalScope):
        if name in cur.assignments:
            # if the assignment is a Global/Nonlocal declaration it does NOT shadow
            from libcst.metadata.scope_provider import Global, Nonlocal

            assigns = cur.assignments[name]
            if all(isinstance(a, (Global, Nonlocal)) for a in assigns):
                cur = cur.parent
                continue
            return True
        cur = cur.parent
    return False


# --------------------------------------------------------------------------- #
# Import classification & rewriting
# --------------------------------------------------------------------------- #


@dataclass
class ExtImport:
    kind: str  # "module" | "from"
    module: str  # "os" / "os.path" / "" for relative-unresolvable
    target: str  # bound name ("sep", "path", "osp")
    asname: str | None
    is_relative: bool
    source_module: str  # dotted module doing the import
    is_future: bool = False
    is_star: bool = False

    def key(self):
        return (self.kind, self.module, self.target, self.asname)


def classify_imports(
    mod: ModuleInfo,
    pkg_name: str,
    module_dotteds: set[str],
    warnings: list[str],
) -> list[ExtImport]:
    """Walk module-level (and nested) imports; produce ExtImport records."""
    out: list[ExtImport] = []
    tree = mod.tree

    def resolve_relative(level: int, module: str | None, from_init: bool) -> str | None:
        base = mod.dotted.split(".")
        # level 1 from __init__ of pkg refers to pkg itself
        up = level - (1 if from_init else 0)
        anchor = base[: len(base) - up] if up > 0 else base
        if up < 0:
            return None
        target = ".".join(anchor)
        if module:
            target = f"{target}.{module}" if target else module
        return target or None

    class V(cst.CSTVisitor):
        def _visit_import(self, node: cst.Import) -> None:
            for alias in node.names:
                dotted = _dotted_name(alias.name)
                if dotted is None:
                    continue
                if dotted in module_dotteds or dotted == pkg_name:
                    # importing a consolidated module object: handled via proxy
                    out.append(
                        ExtImport(
                            "module",
                            dotted,
                            alias.asname.name.value if alias.asname else dotted.split(".")[0],
                            alias.asname.name.value if alias.asname else None,
                            False,
                            mod.dotted,
                        )
                    )
                    continue
                out.append(
                    ExtImport(
                        "module",
                        dotted,
                        alias.asname.name.value if alias.asname else dotted.split(".")[0],
                        alias.asname.name.value if alias.asname else None,
                        False,
                        mod.dotted,
                    )
                )

        def _visit_import_from(self, node: cst.ImportFrom) -> None:
            module = _dotted_name(node.module) if node.module is not None else None
            if isinstance(node.names, cst.ImportStar):
                out.append(
                    ExtImport(
                        "from",
                        module or "",
                        "*",
                        None,
                        bool(node.relative),
                        mod.dotted,
                        is_star=True,
                    )
                )
                return
            resolved = module
            is_rel = bool(node.relative)
            if node.relative:
                resolved = resolve_relative(len(node.relative), module, mod.is_pkg_init)
                if resolved is None:
                    warnings.append(
                        f"{mod.dotted}: cannot resolve relative import (level {len(node.relative)}) -- kept as-is"
                    )
            for alias in node.names:
                if alias.asname:
                    pass
                else:
                    (
                        alias.name.value
                        if not isinstance(alias.name, cst.Attribute)
                        else _dotted_name(alias.name).split(".")[0]
                    )
                target = _dotted_name(alias.name)
                if target is None:
                    continue
                out.append(
                    ExtImport(
                        "from",
                        resolved or "",
                        target,
                        alias.asname.name.value if alias.asname else None,
                        is_rel,
                        mod.dotted,
                        is_future=(resolved == "__future__"),
                    )
                )

        def visit_Import(self, node: cst.Import) -> None:
            self._visit_import(node)

        def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
            self._visit_import_from(node)

    tree.visit(V())
    return out


def _dotted_name(node) -> str | None:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _dotted_name(node.value)
        if base is None:
            return None
        return f"{base}.{node.attr.value}"
    return None


# --------------------------------------------------------------------------- #
# Merged import block builder
# --------------------------------------------------------------------------- #


def _stdlib_or_thirdparty(dotted: str) -> str:
    top = dotted.split(".")[0]
    if top in STDLIB_NAMES or top == "__future__":
        return "stdlib"
    return "thirdparty"


@dataclass
class MergedImports:
    future: list[str] = field(default_factory=list)  # raw lines
    stdlib: dict[str, list[ExtImport]] = field(default_factory=dict)
    thirdparty: dict[str, list[ExtImport]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add(self, imp: ExtImport) -> None:
        if imp.is_future:
            if imp.kind == "from" and imp.target != "annotations":
                self.future.append(f"from __future__ import {imp.target}")
            return
        bucket = self.stdlib if _stdlib_or_thirdparty(imp.module or imp.target) == "stdlib" else self.thirdparty
        if imp.kind == "module":
            bucket.setdefault(imp.module, []).append(imp)
        else:
            bucket.setdefault(imp.module, []).append(imp)

    def render(self, header: bool = True) -> str:
        lines: list[str] = []
        if self.future:
            lines.append("# __future__ imports (merged)")
            for f in sorted(set(self.future)):
                lines.append(f)
            lines.append("")
        if self.stdlib:
            if header:
                lines.append("# --- stdlib imports (merged from all package modules) ---")
            for mod in sorted(self.stdlib):
                lines.extend(_render_module_imports(mod, self.stdlib[mod]))
            lines.append("")
        if self.thirdparty:
            if header:
                lines.append("# --- third-party imports (merged from all package modules) ---")
            for mod in sorted(self.thirdparty):
                lines.extend(_render_module_imports(mod, self.thirdparty[mod]))
            lines.append("")
        return "\n".join(lines).rstrip("\n")


def _render_module_imports(module: str, imports: list[ExtImport]) -> list[str]:
    """Render one external module's imports, merging aliases."""
    plain: list[ExtImport] = [i for i in imports if i.kind == "module" and not i.is_relative]
    froms: list[ExtImport] = [i for i in imports if i.kind == "from"]
    lines: list[str] = []
    if plain:
        # `import os`, `import os.path` -- render as import lines keyed by top module
        for i in sorted(plain, key=lambda x: (x.module, x.asname or "")):
            if i.asname and i.module != i.asname:
                lines.append(f"import {i.module} as {i.asname}")
            else:
                lines.append(f"import {i.module}")
    for i in sorted(froms, key=lambda x: (x.module, x.target)):
        if i.is_star:
            lines.append(f"from {i.module} import *")
            continue
        as_part = f" as {i.asname}" if i.asname else ""
        lines.append(f"from {i.module} import {i.target}{as_part}")
    return lines


# --------------------------------------------------------------------------- #
# Intra-package import rewriting (statement-level)
# --------------------------------------------------------------------------- #


@dataclass
class RewriteResult:
    stmt: cst.CSTNode | None  # rewritten statement (None if dropped)
    note: str = ""  # "dropped" | "kept" | "replaced-with-assign"


class IntraPkgImportRewriter(cst.CSTTransformer):
    """Rewrite imports of consolidated modules into direct references.

    ``from pkg.mod import name as n``  ->  ``n = <final name of pkg.mod.name>``
    ``from pkg.mod import *``          ->  assignments of every public name
    ``import pkg.mod``                 ->  kept (proxy object in sys.modules)
    ``from pkg import mod``            ->  ``mod = _proxy_pkg_mod``
    """

    def __init__(self, ctx: Consolidator):
        self.ctx = ctx
        self.replacements: dict[int, cst.CSTNode | None] = {}
        self.drop_ids: set[int] = set()
        self.replace_ids: dict[int, cst.CSTNode] = {}

    # ---- helpers ----

    def _final_global(self, module_dotted: str, orig_name: str) -> str | None:
        return self.ctx.final_name_of(module_dotted, orig_name)

    def _resolve_from_target(self, source_module: str, target: str) -> tuple[str | None, bool]:
        """Return (final_name, is_module_object).

        is_module_object=True when the target refers to the consolidated
        submodule itself (``from pkg import mod``).
        """
        # submodule hit FIRST: from pkg import sub -- `sub` is a real module
        # of the package, even if the importing module also binds the name
        sub_dotted = f"{source_module}.{target}"
        if self.ctx.is_module(sub_dotted):
            return self.ctx.proxy_var_for(sub_dotted), True
        # direct hit: a global of that module
        fin = self._final_global(source_module, target)
        if fin is not None:
            return fin, False
        return None, False

    def _rewrite_from(self, node: cst.ImportFrom, source_mod: ModuleInfo) -> RewriteResult:
        ctx = self.ctx
        module = _dotted_name(node.module) if node.module is not None else None
        # resolve relative base
        resolved = module
        if node.relative:
            base = source_mod.dotted.split(".")
            up = len(node.relative) - (1 if source_mod.is_pkg_init else 0)
            anchor = base[: len(base) - up] if up > 0 else base
            resolved = ".".join(anchor)
            if module:
                resolved = f"{resolved}.{module}" if resolved else module

        if resolved is None:
            return RewriteResult(node, "unresolvable")

        if isinstance(node.names, cst.ImportStar):
            return self._expand_star(source_mod, resolved, node)

        # Which consolidated module does `resolved` refer to?
        target_mod_dotted = resolved
        if not ctx.is_module(target_mod_dotted):
            # maybe it's an attribute of the package: from pkg import name
            # handled per-alias below
            pass

        assigns: list[cst.SimpleStatementLine] = []
        unresolved: list[cst.ImportAlias] = []

        for alias in node.names:
            target = _dotted_name(alias.name)
            if target is None:
                unresolved.append(alias)
                continue
            asname = alias.asname.name.value if alias.asname else None
            local_name = asname or target.split(".")[-1]
            # the rename pass renames references to this binding; the binding
            # itself must carry the renamed (final) name
            local_name = self.ctx.final_name_of(source_mod.dotted, local_name) or local_name

            fin, _is_module_obj = self._resolve_from_target(resolved, target)
            if fin is not None:
                if fin == target and not asname:
                    # no-op re-export: the target's definer kept the original
                    # name, so the single shared global already provides it.
                    # Drop the binding (the scheduler orders the definer
                    # before any use) and point this module's proxy map at it.
                    self.ctx.final_names.setdefault(source_mod.dotted, {})[target] = fin
                    continue
                # local rebinding semantics: n = final
                line = cst.parse_statement(f"{local_name} = {fin}")
                assigns.append(line)
                continue

            # `from pkg import submod`: proxy reference
            sub_dotted = f"{resolved}.{target}"
            if ctx.is_module(sub_dotted):
                proxy = ctx.proxy_var_for(sub_dotted)
                line = cst.parse_statement(f"{local_name} = {proxy}")
                assigns.append(line)
                continue

            # dotted: from pkg.mod import sub.attr -- rewrite prefix
            if "." in target:
                head, rest = target.split(".", 1)
                fin_head, _ismod = self._resolve_from_target(resolved, head)
                if fin_head is not None:
                    line = cst.parse_statement(f"{local_name} = {fin_head}.{rest}")
                    assigns.append(line)
                    continue

            unresolved.append(alias)

        if assigns and not unresolved:
            if len(assigns) == 1:
                return RewriteResult(assigns[0], "replaced")
            return RewriteResult(_join_statements(assigns), "replaced")
        if not assigns and not unresolved:
            # every alias was a no-op re-export: drop the statement entirely
            return RewriteResult(None, "dropped-noop")
        if assigns and unresolved:
            # partial: keep unresolved as an import, add assigns
            kept = node.with_changes(names=unresolved)
            return RewriteResult(_join_statements([kept] + assigns), "replaced-partial")
        return RewriteResult(node, "kept")

    def _expand_star(self, source_mod: ModuleInfo, resolved: str, node: cst.ImportFrom) -> RewriteResult:
        """Expand ``from mod import *`` into assignments of mod's public names."""
        ctx = self.ctx
        target_mod = ctx.module_by_dotted.get(resolved)
        if target_mod is None:
            return RewriteResult(node, "star-into-unknown")
        names = ctx.public_names_of(resolved)
        if not names:
            return RewriteResult(None, "dropped")
        assigns = []
        for orig in names:
            fin = self._final_global(resolved, orig)
            if fin is None:
                continue
            # skip underscore names unless the source module set __all__ covering them
            if orig.startswith("_") and orig not in ctx.explicit_all_of(resolved):
                continue
            local_final = ctx.final_name_of(source_mod.dotted, orig) or orig
            if local_final == fin:
                continue  # same global; no binding needed
            assigns.append(cst.parse_statement(f"{local_final} = {fin}"))
        if not assigns:
            return RewriteResult(None, "dropped")
        return RewriteResult(_join_statements(assigns), "replaced-star")

    def _rewrite_plain_import(self, node: cst.Import, source_mod: ModuleInfo) -> RewriteResult:
        ctx = self.ctx
        keep: list[cst.ImportAlias] = []
        assign_lines: list[cst.CSTNode] = []
        for alias in node.names:
            dotted = _dotted_name(alias.name)
            if dotted is None:
                keep.append(alias)
                continue
            asname = alias.asname.name.value if alias.asname else None
            top = dotted.split(".")[0]
            if top == ctx.pkg_name:
                if dotted == ctx.pkg_name or asname is None:
                    # `import pkg` / `import pkg.sub` bind the TOP name to the
                    # package object; attribute chains resolve through
                    # sys.module proxies (__getattr__) at access time
                    bound_final = ctx.final_name_of(source_mod.dotted, asname or top) or (asname or top)
                    assign_lines.append(cst.parse_statement(f"{bound_final} = sys.modules[{ctx.pkg_name!r}]"))
                    continue
                # `import pkg.sub as ps` binds the alias to the sub proxy
                proxy = ctx.proxy_var_for(dotted)
                assign_lines.append(cst.parse_statement(f"{asname} = {proxy}"))
                continue
            keep.append(alias)
        if assign_lines and not keep:
            return RewriteResult(_join_statements(assign_lines), "replaced")
        if assign_lines and keep:
            imp = cst.Import(names=keep)
            return RewriteResult(_join_statements([imp] + assign_lines), "replaced-partial")
        return RewriteResult(node, "kept")


def _join_statements(stmts: Sequence[cst.CSTNode]) -> cst.CSTNode:
    """Wrap several statements so they can occupy one slot: an IndentedBlock is
    invalid at top level, so emit them via a Module-level list later.  We use a
    temporary container node; the scheduler flattens it."""
    return _StmtBundle(tuple(stmts))


class _StmtBundle:
    """Internal marker: several statements occupying a single scheduler slot."""

    __slots__ = ("stmts",)

    def __init__(self, stmts):
        self.stmts = tuple(stmts)


# --------------------------------------------------------------------------- #
# Consolidator
# --------------------------------------------------------------------------- #


class Consolidator:
    def __init__(self, pkg_root: Path, pkg_name: str, keep_data: bool = True, quiet: bool = False):
        self.pkg_root = Path(pkg_root).resolve()
        self.pkg_name = pkg_name
        self.keep_data = keep_data
        self.quiet = quiet
        self.warnings: list[str] = []
        self.module_by_dotted: dict[str, ModuleInfo] = {}
        self.stmts: list[StmtInfo] = []
        self.merged = MergedImports()
        self.renames: dict[str, dict[str, str]] = {}  # module -> {old: new}
        self.final_names: dict[str, dict[str, str]] = {}  # module -> {orig: final}
        self.public_api: dict[str, str] = {}  # pkg-visible name -> final
        self.proxy_vars: dict[str, str] = {}  # submodule dotted -> var
        self.data_files: list[DataFile] = []
        self.entry_points: list[EntryPoints] = []
        self.has_cli = False
        self.explicit_all: dict[str, list[str]] = {}  # module -> __all__ entries
        self.main_stmts: list[StmtInfo] = []
        self.dunder_file_map: dict[str, str] = {}  # orig __file__ dir -> var
        self._module_order: list[str] = []

    def log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, file=sys.stderr)

    # -- helpers used by the rewriter ------------------------------------

    def is_module(self, dotted: str) -> bool:
        return dotted in self.module_by_dotted

    def final_name_of(self, module_dotted: str, orig_name: str) -> str | None:
        return self.final_names.get(module_dotted, {}).get(orig_name)

    def proxy_var_for(self, submodule_dotted: str) -> str:
        if submodule_dotted not in self.proxy_vars:
            suffix = submodule_dotted[len(self.pkg_name) + 1 :].replace(".", "_")
            self.proxy_vars[submodule_dotted] = f"_mod_{suffix}"
        return self.proxy_vars[submodule_dotted]

    def public_names_of(self, module_dotted: str) -> list[str]:
        mod = self.module_by_dotted.get(module_dotted)
        if mod is None:
            return []
        names = set()
        all_list = self.explicit_all.get(module_dotted)
        for info in self.stmts:
            if info.module.dotted != module_dotted:
                continue
            for s in info.stores:
                if s.startswith("__") and s.endswith("__"):
                    continue
                names.add(s)
        if all_list:
            names = {n for n in names if n in set(all_list)} | {n for n in all_list if n in names}
        return sorted(names)

    def explicit_all_of(self, module_dotted: str) -> set[str]:
        return set(self.explicit_all.get(module_dotted, ()))

    # -- main pipeline ----------------------------------------------------

    def run(self) -> str:
        modules, data_files = discover(self.pkg_root, self.pkg_name)
        self.data_files = data_files
        self.entry_points = read_entry_points(
            self.pkg_root.parent if (self.pkg_root.parent / "pyproject.toml").exists() else self.pkg_root,
            self.pkg_name,
        )
        for m in modules:
            self.module_by_dotted[m.dotted] = m
        self.has_cli = bool(self.entry_points) or any(m.rel_posix_path == "__main__.py" for m in modules)

        # parse with libcst; unsafe_skip_copy keeps node ids stable across
        # passes (the trees are private to this process)
        for m in modules:
            src = m.path.read_text("utf-8")
            m.tree = cst.parse_module(src)
            m.wrapper = MetadataWrapper(m.tree, unsafe_skip_copy=True)
            m.scope = m.wrapper.resolve(ScopeProvider)

        ordered = topo_sort_modules(modules, self.warnings)
        self._module_order = [m.dotted for m in ordered]

        # collect statements, find __all__, detect main guards
        all_stmt_infos: list[StmtInfo] = []
        for m in ordered:
            infos, main_guard = iter_module_statements(m, self.warnings)
            self._record_explicit_all(m, infos)
            all_stmt_infos.extend(infos)
            if main_guard is not None:
                info = StmtInfo(module=m, node=main_guard, order=10**9)
                _classify_statement(info, self.warnings)
                self.main_stmts.append(info)

        # classify imports (pre-rewrite: record which submodules the original
        # package's __init__ chain actually imports, for __dir__ parity)
        module_dotteds = set(self.module_by_dotted)
        for m in ordered:
            for info in all_stmt_infos:
                if info.module is not m:
                    continue
                node = info.node
                # find all Import/ImportFrom nodes inside the statement (incl. try/func)
                for imp in _find_import_nodes(node):
                    self._route_import(m, imp, module_dotteds)

        # decide final names / renames for collisions
        self._resolve_collisions(all_stmt_infos)

        # apply renames
        for mod_dotted, mapping in self.renames.items():
            mod = self.module_by_dotted[mod_dotted]
            all_final = {n for fmap in self.final_names.values() for n in fmap.values()}
            rename_in_module(mod, mapping, all_final)

        # rename_in_module may swap mod.tree; re-collect statements so
        # StmtInfo.node points into the *current* trees
        all_stmt_infos = self._recollect_statements(ordered, all_stmt_infos)

        # record reachable submodules from the ORIGINAL (pre-rewrite) imports
        self.stmts = all_stmt_infos
        self.reachable_submodules = self._compute_reachable_submodules()

        # rewrite intra-package imports
        self._rewrite_intra_imports(all_stmt_infos)

        # imports were rewritten into assignments; re-classify so the
        # scheduler sees the real exec-time loads (e.g. `greet = greet__core`)
        for info in all_stmt_infos:
            info.stores = set()
            info.loads = set()
            info.loads_at_exec = set()
            info.deferred = False
            info.star_export = None
            info.is_del = False
            _classify_statement(info, self.warnings)

        # build scheduler graph & emit
        self.stmts = all_stmt_infos
        return self._emit()

    # ------------------------------------------------------------------

    def _recollect_statements(self, ordered: list[ModuleInfo], old_infos: list[StmtInfo]) -> list[StmtInfo]:
        """Re-derive StmtInfo.node from each module's current tree, preserving
        classification and order fields (which don't change on rename)."""
        per_module_count: dict[str, int] = {}
        for info in old_infos:
            per_module_count[info.module.dotted] = per_module_count.get(info.module.dotted, 0) + 1
        # statements were appended per module in `ordered` order; re-walk the
        # current trees with the same flattening rules and rebind nodes.
        new_infos: list[StmtInfo] = []
        for m in ordered:
            infos, _main = iter_module_statements(m, self.warnings)
            new_infos.extend(infos)
        if len(new_infos) != len(old_infos):
            # flatten mismatch (e.g. statements merged) -- fall back to old nodes
            self.warnings.append("statement re-collection mismatch; rename may be stale")
            return old_infos
        # carry over stores/loads/etc. computed earlier by matching (module, order)
        old_by_key = {(i.module.dotted, i.order): i for i in old_infos}
        for info in new_infos:
            prev = old_by_key.get((info.module.dotted, info.order))
            if prev is not None:
                info.stores = prev.stores
                info.loads = prev.loads
                info.loads_at_exec = prev.loads_at_exec
                info.deferred = prev.deferred
                info.star_export = prev.star_export
                info.is_del = prev.is_del
        return new_infos

    def _compute_reachable_submodules(self) -> set[str]:
        """Submodules imported (transitively) by the original top __init__."""
        reach: set[str] = set()
        frontier = [self.pkg_name]
        while frontier:
            cur = frontier.pop()
            mod = self.module_by_dotted.get(cur)
            if mod is None:
                continue
            for info in self.stmts:
                if info.module.dotted != cur:
                    continue
                for imp in _find_import_nodes(info.node):
                    cands: list[str] = []
                    if isinstance(imp, cst.Import):
                        cands = [_dotted_name(a.name) or "" for a in imp.names]
                    elif isinstance(imp, cst.ImportFrom):
                        resolved = (
                            self._resolve_relative(
                                info.module,
                                len(imp.relative) if imp.relative else 0,
                                _dotted_name(imp.module) if imp.module else None,
                            )
                            or ""
                        )
                        if isinstance(imp.names, cst.ImportStar):
                            cands = [resolved]
                        else:
                            cands = [resolved] + [f"{resolved}.{_dotted_name(a.name)}" for a in imp.names]
                    for t in cands:
                        t = t or ""
                        # walk up dotted parts to a real module of this package
                        while t and t not in self.module_by_dotted:
                            if "." not in t:
                                t = ""
                                break
                            t = t.rsplit(".", 1)[0]
                        if t and t != self.pkg_name and t not in reach:
                            reach.add(t)
                            frontier.append(t)
        return reach

    def _record_explicit_all(self, mod: ModuleInfo, infos: list[StmtInfo]) -> None:
        for info in infos:
            node = info.node
            if isinstance(node, cst.SimpleStatementLine) and len(node.body) == 1:
                s = node.body[0]
                if (
                    isinstance(s, cst.Assign)
                    and isinstance(s.targets[0].target, cst.Name)
                    and s.targets[0].target.value == "__all__"
                ):
                    vals = parse_literal_list(s.value)
                    if vals is not None:
                        self.explicit_all[mod.dotted] = vals
                        return

    def _route_import(self, mod: ModuleInfo, imp, module_dotteds: set[str]) -> None:
        """Send an import to the merged external block, or leave it to the
        intra-package rewriter."""
        if isinstance(imp, cst.Import):
            for alias in imp.names:
                dotted = _dotted_name(alias.name)
                if dotted is None:
                    continue
                asname = alias.asname.name.value if alias.asname else None
                top = dotted.split(".")[0]
                if top == self.pkg_name:
                    # intra-package: proxy assignment handled by rewriter
                    continue
                self.merged.add(
                    ExtImport(
                        "module",
                        dotted,
                        dotted.split(".")[0],
                        asname,
                        False,
                        mod.dotted,
                    )
                )
        elif isinstance(imp, cst.ImportFrom):
            module = _dotted_name(imp.module) if imp.module else None
            relative = bool(imp.relative)
            resolved = module
            if relative:
                resolved = self._resolve_relative(mod, len(imp.relative), module)
                if resolved is None:
                    self.warnings.append(f"{mod.dotted}: unresolvable relative import kept as-is")
                    return
            if resolved is not None and (resolved == self.pkg_name or resolved.startswith(self.pkg_name + ".")):
                # intra-package from-import: rewriter's job
                return
            if resolved == "__future__":
                if isinstance(imp.names, cst.ImportStar):
                    return
                for alias in imp.names:
                    t = _dotted_name(alias.name)
                    if t:
                        self.merged.add(
                            ExtImport(
                                "from",
                                "__future__",
                                t,
                                alias.asname.name.value if alias.asname else None,
                                False,
                                mod.dotted,
                                is_future=True,
                            )
                        )
                return
            if isinstance(imp.names, cst.ImportStar):
                # external star import: must stay at its original location
                return
            for alias in imp.names:
                t = _dotted_name(alias.name)
                if t is None:
                    continue
                asname = alias.asname.name.value if alias.asname else None
                self.merged.add(ExtImport("from", resolved or "", t, asname, relative, mod.dotted))

    def _resolve_relative(self, mod: ModuleInfo, level: int, module: str | None) -> str | None:
        base = mod.dotted.split(".")
        up = level - (1 if mod.is_pkg_init else 0)
        if up < 0 or up > len(base):
            return None
        anchor = base[: len(base) - up] if up > 0 else base
        target = ".".join(anchor)
        if module:
            target = f"{target}.{module}" if target else module
        return target or None

    # ------------------------------------------------------------------

    def _resolve_collisions(self, all_stmt_infos: list[StmtInfo]) -> None:
        """Assign every module-global a unique final name; losers get renamed.

        Priority for keeping the original name:
          1. the *top* ``__init__.py``'s public API (re-exports keep the name)
          2. definition order in the merged topological stream

        External imports (``import sys`` etc.) never participate: they merge
        into the header import block, so their names are pre-declared as
        "shared" and every module's binding maps to the same header name.
        """
        init_dotted = self.pkg_name

        # ---- names bound by external imports (shared via the header) ----
        external_names: dict[str, set[str]] = {}  # bound name -> {module dotteds}
        for info in all_stmt_infos:
            for imp in _find_import_nodes(info.node):
                if isinstance(imp, cst.Import):
                    for alias in imp.names:
                        dotted = _dotted_name(alias.name) or ""
                        if not dotted or dotted.split(".")[0] == self.pkg_name:
                            continue
                        bound = alias.asname.name.value if alias.asname else dotted.split(".")[0]
                        external_names.setdefault(bound, set()).add(info.module.dotted)
                elif isinstance(imp, cst.ImportFrom):
                    resolved = self._resolve_relative(
                        info.module,
                        len(imp.relative) if imp.relative else 0,
                        _dotted_name(imp.module) if imp.module else None,
                    )
                    if resolved is None or resolved == "__future__":
                        continue
                    if resolved == self.pkg_name or resolved.startswith(self.pkg_name + "."):
                        continue  # intra-package
                    if isinstance(imp.names, cst.ImportStar):
                        continue
                    for alias in imp.names:
                        bound = (
                            alias.asname.name.value if alias.asname else (_dotted_name(alias.name) or "").split(".")[0]
                        )
                        if bound:
                            external_names.setdefault(bound, set()).add(info.module.dotted)

        # ---- collect all (module, name) definitions with stream position ----
        used: set[str] = set()
        # reserved names for our runtime glue + shared external import names
        used.update({
            "_CONSOLIDATED_",
            "_pkg_proxies",
            "__all__",
            "__getattr__",
            "__dir__",
            "_SUBMODULE_EXPORTS",
            "_PKG_DATA_FILES",
            "_PKG_NAME",
            "_PKG_DIR",
            "_mod_",
            "_register_submodule_proxies",
            "_install_data_shims",
            "_ConsolidatedSubmodule",
            "_b64_to_bytes",
            "_b64_to_text",
            "_PkgDataFile",
            "_pkg_rel_path",
            "_pkg_open",
            "_pkg_open_code",
            "_pkg_path_exists",
            "_pkg_path_isfile",
            "_pkg_path_isdir",
            "_pkg_path_join",
            "_pkg_listdir",
        })
        used.update(external_names)
        init_public = set()
        for info in all_stmt_infos:
            if info.module.dotted == init_dotted:
                init_public.update(info.stores)

        stream_pos = {}
        for i, info in enumerate(all_stmt_infos):
            for name in info.stores:
                stream_pos.setdefault((info.module.dotted, name), i)

        final: dict[tuple[str, str], str] = {}
        conflicts: list[tuple[str, str]] = []

        # pass 1: top __init__ names claim their original name first
        for (mod_dotted, name), pos in sorted(stream_pos.items(), key=lambda kv: kv[1]):
            if mod_dotted == init_dotted and name in init_public:
                final[(mod_dotted, name)] = name
                used.add(name)

        # pass 2: everything else in stream order
        for (mod_dotted, name), pos in sorted(stream_pos.items(), key=lambda kv: kv[1]):
            if (mod_dotted, name) in final:
                continue
            if name in external_names and mod_dotted in external_names[name]:
                # external import binding in THIS module: shared header name
                final[(mod_dotted, name)] = name
                continue
            if name.startswith("__") and name.endswith("__"):
                # dunder module attributes (__all__, __version__ ...): only the
                # top package's survive; submodule ones get internal names
                if mod_dotted == init_dotted:
                    final[(mod_dotted, name)] = name
                    used.add(name)
                else:
                    base = f"_{name.strip('_')}__{module_suffix(mod_dotted, self.pkg_name)}"
                    new, k = base, 2
                    while new in used:
                        new = f"{base}_{k}"
                        k += 1
                    final[(mod_dotted, name)] = new
                    used.add(new)
                    conflicts.append((mod_dotted, name))
                continue
            if name not in used:
                final[(mod_dotted, name)] = name
                used.add(name)
            else:
                # collision: rename with a deterministic unique suffix
                base = f"{name}__{module_suffix(mod_dotted, self.pkg_name)}"
                new = base
                k = 2
                while new in used:
                    new = f"{base}_{k}"
                    k += 1
                final[(mod_dotted, name)] = new
                used.add(new)
                conflicts.append((mod_dotted, name))

        # build lookup structures
        for (mod_dotted, name), fin in final.items():
            self.final_names.setdefault(mod_dotted, {})[name] = fin
            ren = self.renames.setdefault(mod_dotted, {})
            if fin != name:
                ren[name] = fin

        if conflicts:
            for mod_dotted, name in conflicts:
                self.log(f"[rename] {mod_dotted}.{name} -> {self.final_names[mod_dotted][name]}")

        # public API of the package = names exported by the top __init__
        for name in sorted(init_public):
            fin = self.final_names.get(init_dotted, {}).get(name)
            if fin is not None and not name.startswith("_"):
                self.public_api[name] = fin

    # ------------------------------------------------------------------

    def _rewrite_intra_imports(self, all_stmt_infos: list[StmtInfo]) -> None:
        rewriter = IntraPkgImportRewriter(self)
        for info in all_stmt_infos:
            new_node, changed = self._rewrite_stmt_imports(info, rewriter)
            if changed:
                if isinstance(new_node, cst.RemovalSentinel):
                    # the entire statement was an intra-package import that
                    # resolved to nothing (e.g. `from . import x` where x is
                    # fully inlined) -- drop it from the stream
                    info.node = _StmtBundle(())
                else:
                    info.node = new_node

    def _rewrite_stmt_imports(self, info: StmtInfo, rewriter: IntraPkgImportRewriter):
        """Rewrite Import/ImportFrom statements (including inside function bodies
        and try/except fallbacks).  Works at the SimpleStatementLine / statement
        level so FlattenSentinel can expand into sibling statements."""
        node = info.node
        changed = [False]

        def rewrite_small_line(line: cst.SimpleStatementLine, module_of):
            """Rewrite one SimpleStatementLine that contains import(s)."""
            smalls = list(line.body)
            if not any(isinstance(s, (cst.Import, cst.ImportFrom)) for s in smalls):
                return line
            out_stmts: list[cst.CSTNode] = []
            for small in smalls:
                if isinstance(small, cst.Import):
                    res = rewriter._rewrite_plain_import(small, module_of)
                elif isinstance(small, cst.ImportFrom):
                    res = rewriter._rewrite_from(small, module_of)
                else:
                    out_stmts.append(small)
                    continue
                if isinstance(res.stmt, _StmtBundle):
                    changed[0] = True
                    out_stmts.extend(res.stmt.stmts)
                elif res.stmt is None:
                    changed[0] = True  # dropped
                elif res.stmt is small:
                    out_stmts.append(small)
                else:
                    changed[0] = True
                    out_stmts.append(res.stmt)
            if not out_stmts:
                return RemovalSentinel.REMOVE
            # ensure every replacement line ends with a newline so rendered
            # statements never concatenate inside indented blocks
            fixed: list[cst.CSTNode] = []
            for s in out_stmts:
                if isinstance(s, cst.SimpleStatementLine):
                    tw = s.trailing_whitespace
                    if tw is None or tw.newline is None:
                        s = s.with_changes(trailing_whitespace=cst.TrailingWhitespace(newline=cst.Newline(value=None)))
                    fixed.append(s)
                else:
                    fixed.append(s)
            out_stmts = fixed
            # normalize: every element must be a SimpleStatementLine (the
            # rewriters may return bare small statements for "kept")
            normalized: list[cst.CSTNode] = []
            for s in out_stmts:
                if not isinstance(s, cst.SimpleStatementLine):
                    s = cst.SimpleStatementLine(
                        body=[s],
                        trailing_whitespace=cst.TrailingWhitespace(newline=cst.Newline(value=None)),
                    )
                normalized.append(s)
            out_stmts = normalized
            if len(out_stmts) == 1:
                # reuse leading lines / trailing whitespace of the original line
                merged = out_stmts[0].with_changes(
                    leading_lines=line.leading_lines,
                    trailing_whitespace=line.trailing_whitespace,
                )
                return merged
            # first keeps original leading lines, last keeps trailing whitespace
            rebuilt: list[cst.CSTNode] = []
            for i, s in enumerate(out_stmts):
                rebuilt.append(
                    s.with_changes(
                        leading_lines=line.leading_lines if i == 0 else [],
                        trailing_whitespace=(
                            line.trailing_whitespace
                            if i == len(out_stmts) - 1
                            else cst.TrailingWhitespace(newline=cst.Newline(value=None))
                        ),
                    )
                )
            return cst.FlattenSentinel(rebuilt)

        class T(cst.CSTTransformer):
            def __init__(self, mod_info):
                self.mod_info = mod_info

            def leave_SimpleStatementLine(self, orig, updated):
                return rewrite_small_line(updated, self.mod_info.module)

        try:
            new_node = node.visit(T(info))
        except _UnsupportedRewrite:
            return node, False
        if changed[0] or new_node is not node:
            return new_node, True
        return node, False

    # ------------------------------------------------------------------

    def _emit(self) -> str:
        scheduler = Scheduler(self)
        emitter = Emitter(self, scheduler, Path("consolidated.py"))
        return emitter.emit()


# --------------------------------------------------------------------------- #
# Statement rendering
# --------------------------------------------------------------------------- #


class _UnsupportedRewrite(Exception):
    pass


def _find_import_nodes(node) -> list[cst.CSTNode]:
    """All Import/ImportFrom nodes anywhere inside a statement."""
    out: list[cst.CSTNode] = []

    class V(cst.CSTVisitor):
        def visit_Import(self, n):
            out.append(n)

        def visit_ImportFrom(self, n):
            out.append(n)

    def _visit(n):
        if isinstance(n, cst.FlattenSentinel):
            for c in n.nodes:
                _visit(c)
        elif isinstance(n, _StmtBundle):
            for c in n.stmts:
                _visit(c)
        elif isinstance(n, cst.CSTNode):
            n.visit(V())

    _visit(node)
    return out


def render_stmt(node) -> list[str]:
    """Render a statement (or bundle/flatten-sentinel) to source lines."""
    if isinstance(node, _StmtBundle):
        lines = []
        for s in node.stmts:
            lines.extend(render_stmt(s))
        return lines
    if isinstance(node, cst.FlattenSentinel):
        lines = []
        for s in node.nodes:
            lines.extend(render_stmt(s))
        return lines
    if isinstance(node, cst.CSTNode):
        code = cst.Module(body=[node] if not isinstance(node, cst.Module) else node.body).code
        return code.rstrip("\n").split("\n")
    return [str(node)]


# --------------------------------------------------------------------------- #
# Section scheduler
# --------------------------------------------------------------------------- #


@dataclass
class ScheduledItem:
    stmt: StmtInfo
    section: str
    reason: str = ""


class Scheduler:
    """Orders statements into the requested section layout while guaranteeing
    that every statement runs after whatever it needs at module-exec time.

    Strategy
    --------
    1. Compute the "natural" section of each statement:
       * def/class/decorated-or-bare  -> CLASS / FUNC (deferred bodies)
       * module-level constants & control flow -> CONST
       * data-registration -> DATA (glue statements we generate)
    2. Build a dependency graph on (module, name) definitions:
       an item that LOADs name N at exec time depends on the last item
       that STOREs N *before it in the original stream*.
    3. Emit CONST items first (in stream order, pulling in any FUNC/CLASS
       definitions they exec-depend on, recursively -- "clamping"), then the
       remaining DATA items, then CLASS, then FUNC, then REEXPORT, then MAIN.
    """

    def __init__(self, ctx: Consolidator):
        self.ctx = ctx
        self.items: list[StmtInfo] = list(ctx.stmts)
        self.by_key: dict[tuple[str, int], StmtInfo] = {}
        for info in self.items:
            self.by_key[(info.module.dotted, info.order)] = info

    def natural_section(self, info: StmtInfo) -> str:
        node = info.node
        if isinstance(node, _StmtBundle):
            return SECTION_CONST
        if isinstance(node, cst.SimpleStatementLine):
            s = node.body[0]
            if isinstance(s, cst.FunctionDef):
                return SECTION_FUNC
            if isinstance(s, cst.ClassDef):
                return SECTION_CLASS
            return SECTION_CONST
        if isinstance(node, (cst.FunctionDef, cst.ClassDef)):
            return SECTION_CLASS if isinstance(node, cst.ClassDef) else SECTION_FUNC
        return SECTION_CONST

    def _defs_before(self) -> dict[str, list[StmtInfo]]:
        """name -> statements that store it, in stream order."""
        defs: dict[str, list[StmtInfo]] = {}
        for info in self.items:
            for n in info.stores:
                defs.setdefault(n, []).append(info)
        return defs

    def schedule(self) -> dict[str, list[StmtInfo]]:
        defs = self._defs_before()
        # global names available to each module (final names); stores may
        # already be final names (post-rewrite), so add both spellings
        module_globals: dict[str, set[str]] = {}
        all_final_names: set[str] = set()
        for fmap in self.ctx.final_names.values():
            all_final_names.update(fmap.values())
        for info in self.items:
            g = module_globals.setdefault(info.module.dotted, set())
            for n in info.stores:
                g.add(n)
                fin = self.ctx.final_name_of(info.module.dotted, n)
                if fin:
                    g.add(fin)

        # exec-time dependency edges: item -> items it depends on
        deps: dict[tuple[str, int], set[tuple[str, int]]] = {}

        for info in self.items:
            key = (info.module.dotted, info.order)
            dset: set[tuple[str, int]] = set()
            needed = set(info.loads_at_exec)
            if info.is_del:
                needed |= {n for n in info.stores}
            for load in needed:
                load_final = self.ctx.final_name_of(info.module.dotted, load) or load
                if load_final not in module_globals.get(info.module.dotted, set()):
                    # post-rewrite loads may already be final names defined in
                    # another module (e.g. `greet = greet__core` in __init__)
                    if load in all_final_names:
                        load_final = load
                    else:
                        continue  # builtin/external -- available from header
                # find the defining statement: the last definer before `info` in
                # the same module stream (Python semantics: definition point)
                definers = defs.get(load_final, [])
                before = [d for d in definers if d.module.dotted == info.module.dotted and d.order < info.order]
                if not before:
                    # cross-module use (import rewritten away): any definer
                    candidates = definers
                else:
                    candidates = before[-1:]
                for c in candidates:
                    if (c.module.dotted, c.order) != key:
                        dset.add((c.module.dotted, c.order))
            deps[key] = dset

        # ---- greedy emit with clamping ----
        sections: dict[str, list[StmtInfo]] = {s: [] for s in SECTION_ORDER}
        emitted: set[tuple[str, int]] = set()

        def emit_item(info: StmtInfo, reason: str, pull_stack: tuple[str, ...] = ()) -> None:
            key = (info.module.dotted, info.order)
            if key in emitted:
                return
            emitted.add(key)
            # emit dependencies first (clamping: pull defs into this section)
            for dkey in sorted(deps.get(key, ())):
                if dkey in emitted:
                    continue
                dep = self.by_key.get(dkey)
                if dep is None:
                    continue
                emit_item(dep, "clamped-dep", pull_stack + (info.display,))
            # place the item: clamped deps inherit the *current target section*
            # unless the item is naturally a later-section def that nothing has
            # forced earlier.
            sections[current_section].append(info)

        # order of emission: CONST first, but we must walk ALL items in stream
        # order and, for each, decide its final section.
        assignment: dict[tuple[str, int], str] = {}

        def target_section_for(info: StmtInfo) -> str:
            nat = self.natural_section(info)
            # If any earlier CONST item exec-depends on this item, clamp it to CONST.
            for other in self.items:
                okey = (other.module.dotted, other.order)
                if okey == (info.module.dotted, info.order):
                    continue
                if natural_of(other) != SECTION_CONST:
                    continue
                dkey = (info.module.dotted, info.order)
                if dkey in deps.get(okey, ()):  # other depends on info
                    if natural_of(other) < natural_of(info) if False else True:
                        return SECTION_CONST
            return nat

        def natural_of(info: StmtInfo) -> str:
            return self.natural_section(info)

        # compute clamping: which FUNC/CLASS defs are needed by CONST items?
        needed_by_const: set[tuple[str, int]] = set()
        worklist: list[tuple[str, int]] = []
        for info in self.items:
            if natural_of(info) != SECTION_CONST:
                continue
            for d in deps.get((info.module.dotted, info.order), ()):
                dep_info = self.by_key.get(d)
                if dep_info is not None and natural_of(dep_info) != SECTION_CONST:
                    worklist.append(d)
        while worklist:
            k = worklist.pop()
            if k in needed_by_const:
                continue
            needed_by_const.add(k)
            for d in deps.get(k, ()):
                dep_info = self.by_key.get(d)
                if dep_info is not None and natural_of(dep_info) != SECTION_CONST:
                    worklist.append(d)

        for info in self.items:
            key = (info.module.dotted, info.order)
            nat = natural_of(info)
            section = SECTION_CONST if key in needed_by_const else nat
            assignment[key] = section

        # final emission pass: stream order within each section, but honor deps
        for section in SECTION_ORDER:
            for info in self.items:
                key = (info.module.dotted, info.order)
                if assignment.get(key) != section:
                    continue
                if key in emitted:
                    continue
                # emit deps first (they were assigned to same-or-earlier sections;
                # if a dep got a later section, clamp it here)
                stack = [key]
                order: list[tuple[str, int]] = []
                visited: set[tuple[str, int]] = set()
                while stack:
                    k = stack.pop()
                    if k in visited or k in emitted:
                        continue
                    visited.add(k)
                    order.append(k)
                    for d in sorted(deps.get(k, ()), reverse=True):
                        if d not in emitted:
                            stack.append(d)
                for k in order:
                    if k in emitted:
                        continue
                    kinfo = self.by_key[k]
                    if assignment.get(k) == section:
                        sections[section].append(kinfo)
                        emitted.add(k)
                    else:
                        # dep assigned elsewhere: clamp into this section
                        sections[section].append(kinfo)
                        emitted.add(k)
                        assignment[k] = section

        return sections


# --------------------------------------------------------------------------- #
# Emitter (glue code generation + final file assembly)
# --------------------------------------------------------------------------- #

GLUE_PROXY_TEMPLATE = '''
# --- consolidated submodule proxies -------------------------------------
# The original package imported submodules like ``mypkg.utils``; we register
# lightweight ModuleType proxies in sys.modules so ``import mypkg.utils`` and
# ``from mypkg import utils`` keep working against the single file.
class _ConsolidatedSubmodule(types.ModuleType):
    """A proxy that forwards attribute access to the consolidated namespace."""

    def __init__(self, dotted, exports):
        super().__init__(dotted)
        self.__dict__["_exports"] = exports          # orig name -> final name
        self.__dict__["__all__"] = [
            k for k in exports if not k.startswith("_")
        ]
        self.__dict__["__path__"] = []                # mark as package-ish
        self.__dict__["__file__"] = __file__
        self.__dict__["__package__"] = "{pkg}"

    def __getattr__(self, attr):
        try:
            final = self.__dict__["_exports"][attr]
        except KeyError:
            raise AttributeError(f"module {self.__name__} has no attribute {attr!r}") from None
        return globals()[final]


def _register_submodule_proxies():
    proxies = {}
    # The consolidated module may be imported under its original package name
    # or under whatever filename it was dropped as; register under both.
    orig_prefix = _PKG_NAME + "."
    alias_prefix = ""
    if __name__ != _PKG_NAME:
        alias_prefix = __name__ + "."
    for dotted, exports in _SUBMODULE_EXPORTS.items():
        proxy = _ConsolidatedSubmodule(dotted, exports)
        proxies[dotted] = proxy
        sys.modules[dotted] = proxy
        # expose the proxy as a module-global so rewritten references like
        # `utils = _mod_utils` can find it
        suffix = dotted.split(".", 1)[1].replace(".", "_") if "." in dotted else dotted
        globals()["_mod_" + suffix] = proxy
        if alias_prefix:
            sys.modules[alias_prefix + dotted[len(_PKG_NAME) + 1:]] = proxy
    # make `import <orig_pkg_name>` work when this file is dropped under a
    # different module name (e.g. site-packages/my_single.py)
    if __name__ != _PKG_NAME and _PKG_NAME not in sys.modules:
        sys.modules[_PKG_NAME] = sys.modules[__name__]
    # attach submodule proxies to the package objects' attribute chains
    for dotted, proxy in proxies.items():
        parts = dotted.split(".")
        parent = sys.modules.get(parts[0])
        for i in range(1, len(parts)):
            child_name = ".".join(parts[:i + 1])
            child = proxies.get(child_name) or sys.modules.get(child_name)
            if child is None:
                continue
            if parent is not None and not hasattr(parent, parts[i]):
                setattr(parent, parts[i], child)
            parent = child
    return proxies
'''

GLUE_DATA_TEMPLATE = """
# --- inlined package data -------------------------------------------------
# Data files of the original package are stored in the virtual filesystem
# below; the shims make open()/os.path/importlib.resources calls with the
# original package-relative paths transparently read them.
"""


class Emitter:
    def __init__(self, ctx: Consolidator, scheduler: Scheduler, out_path: Path):
        self.ctx = ctx
        self.sched = scheduler
        self.out_path = out_path

    # ------------------------------------------------------------------

    def build_header(self) -> list[str]:
        pkg = self.ctx.pkg_name
        n_mods = len(self.ctx.module_by_dotted)
        n_data = len(self.ctx.data_files)
        lines = [
            f'"""{pkg} -- consolidated single-file build.',
            "",
            f"Generated by consolidate.py {__version__}; DO NOT EDIT BY HAND.",
            "",
            "This file was produced by collapsing %d Python module(s) and" % n_mods,
            "%d data file(s) of the ``%s`` package into one importable module." % (n_data, pkg),
            f"It exposes the same public API; ``import {pkg}`` behaves like the",
            f"installed package, and ``import {pkg}.<submodule>`` still works via",
            "sys.modules proxies.",
            '"""',
            "",
        ]
        return lines

    def build_proxy_exports(self) -> list[str]:
        """_SUBMODULE_EXPORTS dict source."""
        ctx = self.ctx
        lines = ["_SUBMODULE_EXPORTS = {"]
        for dotted in sorted(ctx.module_by_dotted):
            if dotted == ctx.pkg_name:
                continue
            exports = {}
            for orig, fin in ctx.final_names.get(dotted, {}).items():
                exports[orig] = fin
            if not exports:
                continue
            items = ", ".join(f"{k!r}: {v!r}" for k, v in sorted(exports.items()))
            lines.append(f"    {dotted!r}: {{{items}}},")
        lines.append("}")
        return lines

    def build_data_section(self) -> list[str]:
        ctx = self.ctx
        if not ctx.data_files:
            return ["# (no data files were inlined)"]
        lines = ["_PKG_DATA_FILES = {"]
        for df in ctx.data_files:
            key = df.rel_posix_path
            if df.kind == "text":
                lit = repr(df.text.encode("utf-8"))
                if len(lit) < 12000:
                    lines.append(f"    {key!r}: {lit},")
                else:
                    lines.append(f"    {key!r}: _b64_to_text(")
                    b64 = base64.b64encode(df.text.encode("utf-8")).decode("ascii")
                    for i, l in enumerate(wrap_base64(b64).split("\n")):
                        q = '"' if i == 0 else '    "'
                        lines.append(f'{q}{l}"')
                    lines.append("    ),")
            else:
                lines.append(f"    {key!r}: _b64_to_bytes(")
                wrapped = wrap_base64(df.b64).split("\n")
                for i, l in enumerate(wrapped):
                    q = '"' if i == 0 else '    "'
                    lines.append(f'{q}{l}"')
                lines.append("    ),")
        lines.append("}")
        return lines

    def build_shims(self) -> list[str]:
        pkg = self.ctx.pkg_name
        tpl = textwrap.dedent('''
# --- runtime shims for inlined data files --------------------------------
# (b64 helpers are emitted above, before the data table)


class _PkgDataFile(io.BytesIO):
    """A file-like object over an inlined package data file."""

    def __init__(self, rel_path: str):
        self._rel_path = rel_path
        super().__init__(_PKG_DATA_FILES[rel_path])

    def __repr__(self) -> str:
        return "<inlined package data {{rel_path=%r}}>" % self._rel_path


def _pkg_rel_path(path) -> "str | None":
    """Map a filesystem path to a package-relative key, if it lives inside the
    (now virtual) original package directory."""
    p = str(path)
    norm = p.replace("\\\\", "/")
    pkg_dir = _PKG_DIR
    if norm == pkg_dir or norm.startswith(pkg_dir + "/"):
        rel = norm[len(pkg_dir) + 1:]
    else:
        # also accept paths written relative to the package root, like
        # "fonts/F.ttf", and "mypkg/fonts/F.ttf"
        head = pkg_dir.rsplit("/", 1)[-1] + "/"
        if norm.startswith(head):
            rel = norm[len(head):]
        else:
            return None
    rel = rel.lstrip("./")
    return rel if rel in _PKG_DATA_FILES else None


def _pkg_open(file, mode="r", *args, **kwargs):
    rel = _pkg_rel_path(file)
    if rel is not None and ("w" not in mode and "a" not in mode and "x" not in mode):
        data = _PKG_DATA_FILES[rel]
        if "b" in mode:
            return io.BytesIO(data)
        return io.TextIOWrapper(io.BytesIO(data), encoding=kwargs.get("encoding") or "utf-8")
    return _orig_open(file, mode, *args, **kwargs)


_orig_open = open  # captured before the shim replaces builtins.open


def _pkg_open_code(file, *args, **kwargs):
    rel = _pkg_rel_path(file)
    if rel is not None:
        return _pkg_open(file, "rb", *args, **kwargs)
    return _orig_open(file, "rb", *args, **kwargs)


def _pkg_path_exists(p) -> bool:
    if _pkg_rel_path(p) is not None:
        return True
    return _os_path_exists(p)


def _pkg_path_isfile(p) -> bool:
    return _pkg_rel_path(p) is not None or _os_path_isfile(p)


def _pkg_path_isdir(p) -> bool:
    if _pkg_rel_path(p) is not None:
        return False
    if str(p).replace("\\\\", "/").rstrip("/") == _PKG_DIR:
        return True
    prefix = _PKG_DIR + "/"
    if str(p).replace("\\\\", "/").startswith(prefix):
        # a virtual directory that contains data files
        for key in _PKG_DATA_FILES:
            if key.startswith(str(p).replace("\\\\", "/")[len(prefix):] + "/"):
                return True
        return False
    return _os_path_isdir(p)


def _pkg_path_join(a, *rest):
    import os as _os
    joined = _os.path.join(a, *rest) if not _pkg_rel_path(a) else "/".join(
        [str(a).rstrip("/")] + [str(r) for r in rest])
    return joined


def _pkg_listdir(p):
    p_str = str(p).replace("\\\\", "/")
    if p_str.rstrip("/") == _PKG_DIR:
        out = set()
        for key in _PKG_DATA_FILES:
            out.add(key.split("/", 1)[0])
        return sorted(out)
    prefix = _PKG_DIR + "/"
    if p_str.startswith(prefix):
        rest = p_str[len(prefix):].rstrip("/")
        out = set()
        for key in _PKG_DATA_FILES:
            if key.startswith(rest + "/"):
                nxt = key[len(rest) + 1:].split("/", 1)[0]
                out.add(nxt)
        if out:
            return sorted(out)
        raise FileNotFoundError(p)
    return _os_listdir(p)


def _install_data_shims():
    """Patch open/os.path/pkgutil/importlib.resources -- narrowly, only for
    paths that live inside the original package directory."""
    import builtins
    import os as _os
    import os.path as _osp
    import pathlib as _pathlib
    import importlib.resources as _ires

    # capture originals BEFORE patching (_osp is the same module as _os.path)
    globals()["_os_path_exists"] = _os.path.exists
    globals()["_os_path_isdir"] = _os.path.isdir
    globals()["_os_path_isfile"] = _os.path.isfile
    globals()["_os_listdir"] = _os.listdir

    if getattr(builtins.open, "_pkg_shim", False) is False:
        _shim_open = _pkg_open
        _shim_open._pkg_shim = True  # type: ignore[attr-defined]
        builtins.open = _shim_open

    _osp.exists = _pkg_path_exists
    _osp.isfile = _pkg_path_isfile
    _osp.isdir = _pkg_path_isdir
    _osp.listdir = _pkg_listdir

    # pathlib.Path.exists / is_file / is_dir for virtual package paths
    _orig_exists = _pathlib.Path.exists
    _orig_is_file = _pathlib.Path.is_file
    _orig_is_dir = _pathlib.Path.is_dir

    def _path_exists(self, *a, **kw):
        if _pkg_rel_path(str(self)) is not None:
            return True
        return _orig_exists(self, *a, **kw)

    def _path_is_file(self, *a, **kw):
        if _pkg_rel_path(str(self)) is not None:
            return True
        return _orig_is_file(self, *a, **kw)

    def _path_is_dir(self, *a, **kw):
        if _pkg_rel_path(str(self)) is not None:
            return False
        return _orig_is_dir(self, *a, **kw)

    _pathlib.Path.exists = _path_exists
    _pathlib.Path.is_file = _path_is_file
    _pathlib.Path.is_dir = _path_is_dir

    # pkgutil.get_data(pkg, resource)
    import pkgutil as _pkgutil

    def _get_data(pkg=None, resource=None, *a, **kw):
        if pkg == {pkg_name!r} or (isinstance(pkg, str) and pkg.startswith({pkg_name!r} + ".")):
            rel = str(resource).replace("\\\\", "/").lstrip("./")
            if rel in _PKG_DATA_FILES:
                return _PKG_DATA_FILES[rel]
        return _orig_get_data(pkg, resource, *a, **kw)

    if not hasattr(_pkgutil.get_data, "_pkg_shim"):
        _orig = _pkgutil.get_data
        globals()["_orig_get_data"] = _orig
        _pkgutil.get_data = _get_data
        _pkgutil.get_data._pkg_shim = True  # type: ignore[attr-defined]

    # importlib.resources shim: expose a minimal reader for the package
    class _PkgFiles:
        def __init__(self, pkg):
            self.pkg = pkg

        def joinpath(self, *parts):
            rel = "/".join(str(p) for p in parts)
            return _PkgPath(rel)

        def __truediv__(self, other):
            return self.joinpath(other)

        @property
        def name(self):
            return self.pkg

    class _PkgPath:
        def __init__(self, rel):
            self.rel = rel

        def read_bytes(self):
            return _PKG_DATA_FILES[self.rel]

        def read_text(self, encoding="utf-8"):
            return _PKG_DATA_FILES[self.rel].decode(encoding)

        def is_file(self):
            return self.rel in _PKG_DATA_FILES

        def exists(self):
            return self.rel in _PKG_DATA_FILES

        def __str__(self):
            return "<pkg-data:%s>" % self.rel

    _orig_files = _ires.files

    def _files(proxy):
        name = getattr(proxy, "__name__", None) or str(proxy)
        if name == {pkg_name!r} or name.startswith({pkg_name!r} + "."):
            return _PkgFiles(name)
        return _orig_files(proxy)

    if not getattr(_ires.files, "_pkg_shim", False):
        _ires.files = _files
        _ires.files._pkg_shim = True  # type: ignore[attr-defined]

    # importlib.resources.read_text / read_bytes (legacy API)
    try:
        _orig_read_text = _ires.read_text

        def _read_text(package, resource="utf-8", *args, **kwargs):
            # legacy signature: read_text(package, resource, encoding)
            if isinstance(resource, str) and not resource.startswith("utf"):
                rel = resource.replace("\\\\", "/").lstrip("./")
                name = getattr(package, "__name__", package)
                if (name == {pkg_name!r} or str(name).startswith({pkg_name!r} + ".")) and rel in _PKG_DATA_FILES:
                    data = _PKG_DATA_FILES[rel]
                    return data.decode(args[0] if args else "utf-8")
            return _orig_read_text(package, resource, *args, **kwargs)

        _ires.read_text = _read_text
    except Exception:
        pass
    try:
        _orig_read_bytes = _ires.read_bytes

        def _read_bytes(package, resource, *args, **kwargs):
            rel = str(resource).replace("\\\\", "/").lstrip("./")
            name = getattr(package, "__name__", package)
            if (name == {pkg_name!r} or str(name).startswith({pkg_name!r} + ".")) and rel in _PKG_DATA_FILES:
                return _PKG_DATA_FILES[rel]
            return _orig_read_bytes(package, resource, *args, **kwargs)

        _ires.read_bytes = _read_bytes
    except Exception:
        pass
''')
        return tpl.replace("{pkg_name!r}", repr(pkg)).split("\n")

    # ------------------------------------------------------------------

    def build_reexport_section(self) -> list[str]:
        ctx = self.ctx
        lines = []
        init_final = ctx.final_names.get(ctx.pkg_name, {})
        all_names = ctx.explicit_all.get(ctx.pkg_name)
        # top-level submodule names the original package exposed as attributes
        # (importing pkg.mod sets the attribute `mod` on pkg)
        sub_top = sorted({
            d[len(ctx.pkg_name) + 1 :].split(".")[0]
            for d in ctx.module_by_dotted
            if d != ctx.pkg_name and "." not in d[len(ctx.pkg_name) + 1 :]
        })
        if all_names:
            entries = []
            for n in all_names:
                fin = init_final.get(n)
                if fin:
                    entries.append(fin)
                elif n in sub_top:
                    entries.append(n)
            lines.append("__all__ = [{}]".format(", ".join(repr(e) for e in entries)))
        else:
            pub = sorted(set(ctx.public_api) | set(sub_top))
            lines.append("__all__ = [{}]".format(", ".join(repr(n) for n in pub)))
        return lines

    def build_main_section(self) -> list[str]:
        ctx = self.ctx
        lines: list[str] = []
        ep_lines = []
        for ep in ctx.entry_points:
            fin = ctx.final_name_of(ep.module_dotted, ep.func_name)
            if fin is None:
                ctx.warnings.append(f"entry point {ep.script_name}={ep.func_ref}: target function not found; skipped")
                continue
            ep_lines.append((ep.script_name, fin))
        if ctx.main_stmts:
            lines.append("")
            lines.append('if __name__ == "__main__":')
            for info in ctx.main_stmts:
                for l in render_stmt(info.node):
                    lines.append("    " + l if l else "")
        elif ep_lines:
            # synthesize a __main__ from console_scripts
            lines.append("")
            lines.append('if __name__ == "__main__":')
            if len(ep_lines) == 1:
                lines.append(f"    sys.exit({ep_lines[0][1]}())")
            else:
                lines.append("    import argparse as _arg")
                lines.append(f"    _ap = _arg.ArgumentParser(prog={ctx.pkg_name!r})")
                lines.append("    _ap.add_argument('command', choices=%r)" % [n for n, _ in ep_lines])
                lines.append("    _args = _ap.parse_args()")
                for name, fin in ep_lines:
                    lines.append(f"    if _args.command == {name!r}:")
                    lines.append(f"        sys.exit({fin}())")
        return lines

    # ------------------------------------------------------------------

    def emit(self) -> str:
        ctx = self.ctx
        sched_sections = self.sched.schedule()

        parts: list[str] = []
        parts.append("\n".join(self.build_header()))

        # 1) imports
        merged_src = ctx.merged.render()
        if merged_src:
            parts.append(merged_src)

        # ensure required runtime deps of glue are present
        glue_imports = [
            "import sys",
            "import io",
            "import os",
            "import types",
            "import importlib",
            "import importlib.resources",
            "import importlib.util",
            "import base64",
        ]
        merged_lines = merged_src.split("\n")
        have = {l.strip() for l in merged_lines}
        add = [g for g in glue_imports if g not in have]
        if add:
            parts.append("# --- glue runtime imports ---")
            parts.append("\n".join(add))
            parts.append("")

        # 2) proxies + data + shims
        parts.append(f"_PKG_NAME = {ctx.pkg_name!r}")
        # the original package's submodules saw __package__ == pkg name; keep
        # that true inside the consolidated file
        parts.append("import sys as _sys_mod")
        parts.append("if __name__ == _PKG_NAME or __name__.rsplit('.', 1)[0] == _PKG_NAME:")
        parts.append("    __package__ = _PKG_NAME")
        parts.append("\n".join(self.build_proxy_exports()))
        parts.append(GLUE_PROXY_TEMPLATE.replace("{pkg}", repr(ctx.pkg_name)))
        parts.append(GLUE_DATA_TEMPLATE)
        # b64 helpers must precede the data table that calls them
        parts.append(
            textwrap.dedent("""
            def _b64_to_bytes(b64: str) -> bytes:
                return base64.b64decode(b64)


            def _b64_to_text(b64: str) -> str:
                return base64.b64decode(b64).decode("utf-8")
        """)
        )
        parts.append("\n".join(self.build_data_section()))
        parts.append("\n".join(self.build_shims()))
        parts.append("")
        # the virtual package dir: in the original package, a module's
        # dirname(__file__) WAS the package directory, so fold data paths
        # relative to the consolidated file's own directory
        parts.append('_PKG_DIR = __file__.rsplit("/", 1)[0] if "/" in __file__ else __file__.rsplit(chr(92), 1)[0]')

        # 3) install shims + proxies at import time (before module bodies run)
        parts.append("_register_submodule_proxies()")
        if ctx.data_files:
            parts.append("_install_data_shims()")

        # 4) sections
        first = {
            SECTION_CONST: "# === constants & module-level code ===",
            SECTION_DATA: "# === package data ===",
            SECTION_CLASS: "# === classes ===",
            SECTION_FUNC: "# === functions ===",
            SECTION_REEXPORT: "# === public API & __all__ ===",
            SECTION_MAIN: "# === __main__ / CLI ===",
        }
        for section in SECTION_ORDER:
            items = sched_sections.get(section, [])
            if not items and section in (SECTION_REEXPORT, SECTION_MAIN):
                continue
            parts.append("")
            parts.append(first[section])
            for info in items:
                lines = render_stmt(info.node)
                # module provenance comment
                prov = f"# --- from {info.module.rel_posix_path} ---"
                parts.append(prov)
                parts.extend(lines)

        # 5) re-export / __all__ (always last)
        parts.append("")
        parts.append("# === public API & __all__ ===")
        parts.extend(self.build_reexport_section())

        # 6) main
        main_src = "\n".join(self.build_main_section())
        if main_src.strip():
            parts.append(main_src)

        # module-level __getattr__ for lazy submodule attribute access
        parts.append("")
        parts.append(self.build_module_getattr())

        out = "\n".join(parts)
        out = re.sub(r"\n{4,}", "\n\n\n", out)
        return out

    def build_module_getattr(self) -> str:
        """Module-level __getattr__ (lazy submodule access) + __dir__ (hide
        consolidated internals from dir())."""
        ctx = self.ctx
        submods = sorted(getattr(ctx, "reachable_submodules", set()))
        all_exported = set(ctx.explicit_all.get(ctx.pkg_name, ())) or set(ctx.public_api)
        lines = [
            "",
            "",
            "def __getattr__(name):",
            "    # lazy submodule/attribute access for the consolidated package",
            f"    proxy = sys.modules.get({ctx.pkg_name!r} + '.' + name)",
            "    if proxy is not None:",
            "        return proxy",
            "    raise AttributeError(",
            '        f"module {__name__!r} has no attribute {name!r}")',
            "",
            "",
            "def __dir__():",
            "    # present the original package's public surface, not the",
            "    # consolidated internals (renamed duplicates, glue helpers)",
            "    names = set(__all__)",
        ]
        if submods:
            tops = sorted({d[len(ctx.pkg_name) + 1 :].split(".")[0] for d in submods})
            lines.append(f"    names.update({tops!r})")
        # names defined by the original top __init__ that aren't in __all__
        init_public = sorted(n for n in ctx.public_api if not n.startswith("_"))
        extra = [n for n in init_public if n not in all_exported]
        if extra:
            lines.append(f"    names.update({extra!r})")
        lines.append("    return sorted(names)")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Verification harness
# --------------------------------------------------------------------------- #


def run_probe(
    code: str,
    env_pkg_dir: Path | None = None,
    cwd: Path | None = None,
    timeout: int = 120,
) -> tuple[int, str]:
    """Run python code in an isolated subprocess; return (rc, combined output)."""
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "probe.py"
        script.write_text(code)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(env_pkg_dir) if env_pkg_dir else ""
        env.pop("PYTHONHOME", None)
        try:
            proc = subprocess.run(
                [sys.executable, "-S", str(script)],
                capture_output=True,
                text=True,
                cwd=str(cwd or td),
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return 124, "TIMEOUT"
        return proc.returncode, (proc.stdout + "\n--- stderr ---\n" + proc.stderr).strip()


class Verifier:
    """Behavioral differential tester: original package vs consolidated file."""

    def __init__(
        self,
        pkg_root: Path,
        pkg_name: str,
        consolidated: Path,
        verify_script: Path | None = None,
    ):
        self.pkg_root = Path(pkg_root).resolve()
        self.pkg_name = pkg_name
        self.consolidated = Path(consolidated).resolve()
        self.verify_script = verify_script
        self.failures: list[str] = []
        self.checks = 0

    def report(self, ok: bool, label: str, detail: str = "") -> bool:
        self.checks += 1
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(f"{label}: {detail[:500]}")
        return ok

    def _stage(self, mode: str) -> Path:
        """Create a temp dir that holds the consolidated file, importable either
        next to a script (mode='next') or via site-packages (mode='site').

        The file is staged as <pkg_name>.py so `import <pkg_name>` resolves to
        the consolidated file exactly as it would after being dropped in."""
        td = tempfile.mkdtemp()
        tdp = Path(td)
        fname = f"{self.pkg_name}.py"
        if mode == "site":
            sp = tdp / "site-packages"
            sp.mkdir()
            (sp / fname).write_text(self.consolidated.read_text())
        else:
            (tdp / fname).write_text(self.consolidated.read_text())
        return tdp

    def run(self) -> bool:
        pkg = self.pkg_name

        # ---- 1. import smoke test, both placements ----
        for label, mode, search_dir in (
            ("import pkg (drop next to code)", "next", None),
            ("import pkg (site-packages)", "site", "site-packages"),
        ):
            tdp = self._stage(mode)
            search = tdp / search_dir if search_dir else tdp
            code = f"import {pkg} as p\nprint('imported:', p.__name__)\n"
            rc, out = run_probe(code, env_pkg_dir=search)
            self.report(rc == 0 and f"imported: {pkg}" in out, label, out[:400])
            shutil.rmtree(tdp, ignore_errors=True)

        # ---- 2. submodule import + attribute access ----
        submods = sorted({
            ".".join(p.relative_to(self.pkg_root).with_suffix("").parts)
            for p in self.pkg_root.rglob("*.py")
            if p.name not in ("__init__.py", "__main__.py", "setup.py", "conftest.py")
        })
        submods = [s for s in submods if s and "__init__" not in s]
        if submods:
            tdp = self._stage("next")
            lines = [f"import {pkg}"]
            for s in submods[:10]:
                lines.append(f"import {pkg}.{s}")
            lines.append("print('submodule imports OK:', [m for m in dir(" + pkg + ") if not m.startswith('_')][:5])")
            code = "\n".join(lines) + "\n"
            rc2, out2 = run_probe(code, env_pkg_dir=tdp)
            rc1, out1 = run_probe(code, env_pkg_dir=self.pkg_root.parent)
            ok = rc1 == 0 and rc2 == 0
            self.report(
                ok,
                "submodule imports still work",
                f"orig rc={rc1} {out1[:200]!r} | cons rc={rc2} {out2[:200]!r}",
            )
            shutil.rmtree(tdp, ignore_errors=True)

        # ---- 3. API surface diff ----
        def api_code() -> str:
            return textwrap.dedent(f"""
                import {pkg} as p
                names = sorted(n for n in dir(p) if not n.startswith('_'))
                print(names)
                print('all:', sorted(getattr(p, '__all__', [])) if getattr(p, '__all__', None) else None)
                """)

        tdp = self._stage("next")
        rc1, out1 = run_probe(api_code(), env_pkg_dir=self.pkg_root.parent)
        rc2, out2 = run_probe(api_code(), env_pkg_dir=tdp)
        norm1 = out1.split("--- stderr ---")[0].strip()
        norm2 = out2.split("--- stderr ---")[0].strip()
        ok = rc1 == 0 and rc2 == 0
        if ok:
            dir1, _, all1 = norm1.partition("\n")
            dir2, _, all2 = norm2.partition("\n")
            ok = dir1 == dir2
            # __all__: exact match, or (orig undefined & cons == public dir)
            if ok and all1 != all2:
                if all1.startswith("all: None"):
                    cons_all = all2[len("all: ") :].strip()
                    ok = cons_all == dir1.strip()
                else:
                    ok = False
        self.report(
            ok,
            "public API surface matches",
            f"orig={norm1[:400]!r} cons={norm2[:400]!r}",
        )
        shutil.rmtree(tdp, ignore_errors=True)

        # ---- 4. optional user probe script ----
        if self.verify_script:
            user_code = Path(self.verify_script).read_text()
            tdp = self._stage("next")
            rc1, out1 = run_probe(user_code, env_pkg_dir=self.pkg_root.parent)
            rc2, out2 = run_probe(user_code, env_pkg_dir=tdp)
            n1 = out1.split("--- stderr ---")[0].strip()
            n2 = out2.split("--- stderr ---")[0].strip()
            self.report(
                rc1 == 0 and rc2 == 0 and n1 == n2,
                "verify-script behavior matches",
                f"orig rc={rc1} {n1[:400]!r} | cons rc={rc2} {n2[:400]!r}",
            )
            shutil.rmtree(tdp, ignore_errors=True)

        # ---- 5. data files via pkgutil ----
        rels = list(self._data_rel_paths())[:5]
        if rels:
            code_tpl = textwrap.dedent("""
                import {pkg}, pkgutil
                results = []
                for rel in {rels!r}:
                    try:
                        data = pkgutil.get_data({pkg!r}, rel)
                        results.append((rel, (data or b'')[:8]))
                    except Exception as e:
                        results.append((rel, 'ERR:' + type(e).__name__))
                print(results)
                """)
            tdp = self._stage("next")
            rc1, out1 = run_probe(code_tpl.format(pkg=pkg, rels=rels), env_pkg_dir=self.pkg_root.parent)
            rc2, out2 = run_probe(code_tpl.format(pkg=pkg, rels=rels), env_pkg_dir=tdp)
            n1 = out1.split("--- stderr ---")[0].strip()
            n2 = out2.split("--- stderr ---")[0].strip()
            self.report(
                rc1 == 0 and rc2 == 0 and n1 == n2,
                "data file access matches",
                f"orig={n1[:300]!r} cons={n2[:300]!r}",
            )
            shutil.rmtree(tdp, ignore_errors=True)

        print(f"== {self.checks - len(self.failures)}/{self.checks} checks passed ==")
        if self.failures:
            print("FAILURES:")
            for f in self.failures:
                print("  -", f)
            return False
        return True

    def _data_rel_paths(self):
        skip = {"pyproject.toml", "setup.py", "setup.cfg", "MANIFEST.in"}
        for p in sorted(self.pkg_root.rglob("*")):
            if (
                p.is_file()
                and p.suffix not in (".py", ".pyi")
                and p.name not in skip
                and not any(part in SKIP_DIRS for part in p.parts)
            ):
                yield p.relative_to(self.pkg_root).as_posix()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="consolidate.py",
        description="Consolidate a small-to-medium Python package into a single importable file (libcst-based).",
        epilog="example: python consolidate.py src/mypkg -o mypkg_single.py --verify",
    )
    ap.add_argument(
        "package",
        help="path to the package directory (containing __init__.py), or its parent when --name is given",
    )
    ap.add_argument("--name", help="package import name (defaults to the directory name)")
    ap.add_argument(
        "-o",
        "--output",
        default=None,
        help="output .py path (default: <name>_single.py in cwd)",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="verify the consolidated file behaves like the original",
    )
    ap.add_argument(
        "--verify-script",
        default=None,
        metavar="PY",
        help="script exercising the package API; run against both versions and diff the outputs",
    )
    ap.add_argument("--quiet", action="store_true", help="suppress progress logs")

    args = ap.parse_args(argv)

    pkg_path = Path(args.package).resolve()
    if not pkg_path.exists():
        ap.error(f"package path does not exist: {pkg_path}")
    if (pkg_path / "__init__.py").is_file():
        pkg_root, pkg_name = pkg_path, (args.name or pkg_path.name)
    elif args.name and (pkg_path / args.name / "__init__.py").is_file():
        pkg_root, pkg_name = pkg_path / args.name, args.name
    else:
        ap.error(f"{pkg_path} is not a package directory with __init__.py (or pass the parent dir plus --name)")

    out = Path(args.output) if args.output else Path.cwd() / f"{pkg_name}_single.py"
    out.parent.mkdir(parents=True, exist_ok=True)

    consol = Consolidator(pkg_root, pkg_name, quiet=args.quiet)
    try:
        src = consol.run()
    except cst.ParserSyntaxError as e:
        print(f"error: libcst failed to parse a module: {e}", file=sys.stderr)
        return 2
    out.write_text(src, encoding="utf-8")
    print(f"wrote {out} ({len(src.splitlines())} lines)")

    for w in consol.warnings:
        print(f"warning: {w}", file=sys.stderr)

    if args.verify or args.verify_script:
        v = Verifier(pkg_root, pkg_name, out, args.verify_script)
        return 0 if v.run() else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
