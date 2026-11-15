#!/data/data/com.termux/files/usr/bin/env python

"Consolidate a pure-Python package or script into a single self-contained file."

import argparse
import ast
import base64
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
import fnmatch
import io
import os
from pathlib import Path
import platform
import re
import sys
import time
import tokenize
from types import SimpleNamespace
from typing import Annotated
import zipfile

import click
from loguru import logger
import typer


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"consolidator {__version__}")
        raise typer.Exit


_STATE: dict[str, object] = {}


class _Args(SimpleNamespace):
    """argparse.Namespace look-alike: unknown attributes read as None."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


class _ParserShim:
    """Stand-in for the removed argparse parser; hands back what Typer parsed."""

    def parse_args(self, args=None, namespace=None):
        "Parse args."
        return _Args(**_STATE)

    def error(self, message):
        raise typer.BadParameter(message)

    def print_help(self, file=None):
        "Print help."
        typer.echo(click.get_current_context().get_help(), file=file)

    def print_usage(self, file=None):
        "Print usage."
        typer.echo(click.get_current_context().get_usage(), file=file)


__version__ = "1.4.7"

__all__ = [
    "BundledModule",
    "ConsolidationPlan",
    "ConsolidatorError",
    "PlanWarning",
    "analyze",
    "build_parser",
    "consolidate",
    "main",
    "render",
]

log = logger

_DETERMINISTIC_TIMESTAMP: tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0)

_DEFAULT_DATA_EXCLUDES: tuple[str, ...] = (
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.pyd",
    "**/*.egg-info/**",
    "**/*.dist-info/**",
)

_COMPRESSIONS: dict[str, int] = {
    "stored": zipfile.ZIP_STORED,
    "deflate": zipfile.ZIP_DEFLATED,
    "bzip2": zipfile.ZIP_BZIP2,
    "lzma": zipfile.ZIP_LZMA,
}

_BINARY_SUFFIXES: frozenset[str] = frozenset({".so", ".pyd", ".dylib"})

_IGNORED_TOPS: frozenset[str] = frozenset({"__main__", "__builtins__", "builtins", "__future__"})

_STRICT_WARNING_KINDS: frozenset[str] = frozenset({"unresolved", "broken"})

_ENTRY_CANDIDATES: tuple[str, ...] = ("main", "cli", "app", "run")


class ConsolidatorError(Exception):
    "Signal an error raised by this module."

    pass


@dataclass(frozen=True, slots=True)
class PlanWarning:
    "Store kind, message."

    kind: str
    message: str

    def __str__(self) -> str:
        "Return the object's display string."
        return f"[{self.kind}] {self.message}"


@dataclass(frozen=True, slots=True)
class BundledModule:
    "Store name, source, is_package."

    name: str
    source: Path
    is_package: bool

    @property
    def archive_path(self) -> str:
        base = self.name.replace(".", "/")
        return f"{base}/__init__.py" if self.is_package else f"{base}.py"


@dataclass(frozen=True, slots=True)
class _Located:
    name: str
    file: Path | None
    is_package: bool
    top_package_dir: Path | None
    namespace_dirs: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _ThirdPartyPackage:
    name: str
    path: Path
    is_dir: bool


@dataclass
class ConsolidationPlan:
    "Store entry, entry_module, entry_archive, search_paths, modules and related state."

    entry: Path
    entry_module: str | None
    entry_archive: str | None
    search_paths: tuple[Path, ...]
    modules: dict[str, BundledModule]
    data_files: dict[str, Path]
    package_dirs: dict[str, tuple[Path, ...]]
    warnings: list[PlanWarning]
    synthetic_files: dict[str, bytes] = field(default_factory=dict)

    def summary(self) -> str:
        if self.entry_module is not None:
            entry_desc = f"{self.entry_module} (python -m emulation)"
        else:
            entry_desc = f"{self.entry} -> bundle:/{self.entry_archive}"
        lines = [f"entry     : {entry_desc}"]
        lines.append("search    : " + (", ".join(str(p) for p in self.search_paths) or "-"))
        modules = sorted(self.modules)
        head = ", ".join(modules[:20])
        if len(modules) > 20:
            head += f", ... +{len(modules) - 20} more"
        lines.append(f"modules   : {len(modules)} [{head}]")
        if self.synthetic_files:
            lines.append("synthetic : " + ", ".join(sorted(self.synthetic_files)))
        if self.package_dirs:
            lines.append("packages  : " + ", ".join(sorted(self.package_dirs)))
        data = sorted(self.data_files)
        head = ", ".join(data[:12])
        if len(data) > 12:
            head += f", ... +{len(data) - 12} more"
        lines.append(f"data files: {len(data)} [{head}]")
        if self.warnings:
            lines.append("warnings  :")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        return "\n".join(lines)


def _is_excluded(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) or name.startswith(pattern + ".") for pattern in patterns)


def _validate_dotted(name: str, what: str) -> str:
    if not name or not all(part.isidentifier() for part in name.split(".")):
        msg = f"invalid dotted name for {what}: {name!r}"
        raise ConsolidatorError(msg)
    return name


def _flatten_search_paths(values: Iterable[str | Path | None]) -> list[Path]:
    out: list[Path] = []
    for value in values:
        if value is None:
            continue
        for part in str(value).split(os.pathsep):
            if not part:
                continue
            path = Path(part).resolve()
            if path not in out:
                out.append(path)
    return out


_GLOB_CACHE: dict[str, "re.Pattern[str]"] = {}


def _glob_match(pattern: str, rel_posix: str) -> bool:
    regex = _GLOB_CACHE.get(pattern)
    if regex is None:
        out: list[str] = []
        i, size = 0, len(pattern)
        while i < size:
            if pattern.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
            elif pattern.startswith("**", i):
                out.append(".*")
                i += 2
            elif pattern[i] == "*":
                out.append("[^/]*")
                i += 1
            elif pattern[i] == "?":
                out.append("[^/]")
                i += 1
            else:
                out.append(re.escape(pattern[i]))
                i += 1
        regex = re.compile("".join(out) + r"\Z")
        _GLOB_CACHE[pattern] = regex
    return regex.match(rel_posix) is not None


def _walk_tree(
    root: Path,
    *,
    prune_dir: Callable[[tuple[str, ...]], bool] | None = None,
    prune_file: Callable[[tuple[str, ...]], bool] | None = None,
) -> Iterator[tuple[Path, tuple[str, ...]]]:
    def recurse(directory: Path, seen: set[str]) -> Iterator[tuple[Path, tuple[str, ...]]]:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            log.warning("cannot list directory %s: %s", directory, exc)
            return
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            if name == "__pycache__" or name.endswith((".egg-info", ".dist-info")):
                continue
            path = Path(entry.path)
            if entry.is_dir():
                rel = path.relative_to(root).parts
                if prune_dir is not None and prune_dir(rel):
                    continue
                real = str(Path(path).resolve())
                if real in seen:
                    log.warning("symlink loop detected at %s; skipping", path)
                    continue
                seen.add(real)
                yield from recurse(path, seen)
                seen.discard(real)
            elif entry.is_file():
                rel = path.relative_to(root).parts
                if prune_file is not None and prune_file(rel):
                    continue
                yield path, rel

    yield from recurse(root, {str(Path(root).resolve())})


def _module_name_for(top: str, rel_parts: tuple[str, ...]) -> tuple[str, bool] | None:
    parts = list(rel_parts)
    filename = parts.pop()
    if not filename.endswith(".py"):
        return None
    stem = filename[: -len(".py")]
    if stem == "__init__":
        if not all(part.isidentifier() for part in parts):
            return None
        return (top, True) if not parts else (f"{top}.{'.'.join(parts)}", True)
    names = [*parts, stem]
    if not all(part.isidentifier() for part in names):
        return None
    return f"{top}.{'.'.join(names)}", False


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        msg = f"cannot read {path}: {exc}"
        raise ConsolidatorError(msg) from exc


def _human_size(size: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


@dataclass(slots=True)
class _ScanResult:
    imports: set[tuple[str, bool]]
    notes: list[PlanWarning]


def _dynamic_import_target(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        if func.id == "__import__":
            return "__import__"
        if func.id == "import_module":
            return "import_module"
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id == "importlib"
    ):
        return "importlib.import_module"
    return None


class _ImportScanner(ast.NodeVisitor):
    def __init__(self, module_name: str, is_package: bool, filename: str) -> None:
        "Initialize the instance state from the supplied arguments."
        self.filename = filename
        if is_package:
            self.package = module_name
        else:
            self.package = module_name.rpartition(".")[0]
        self.imports: set[tuple[str, bool]] = set()
        self.notes: list[PlanWarning] = []

    def _add_strict_chain(self, name: str) -> None:
        parts = name.split(".")
        for index in range(1, len(parts) + 1):
            self.imports.add((".".join(parts[:index]), True))

    def _add_loose(self, name: str) -> None:
        self.imports.add((name, False))

    def _absolute_base(self, level: int, module: str | None) -> str | None:
        if level == 0:
            return module
        if not self.package:
            self.notes.append(
                PlanWarning(
                    "dynamic",
                    f"relative import outside a package in {self.filename}",
                )
            )
            return None
        parts = self.package.split(".")
        if level > 1 and level - 1 >= len(parts):
            self.notes.append(
                PlanWarning(
                    "dynamic",
                    f"relative import beyond top-level package in {self.filename}",
                )
            )
            return None
        base = parts if level == 1 else parts[: -(level - 1)]
        if module:
            base = [*base, *module.split(".")]
        return ".".join(base)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add_strict_chain(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self._absolute_base(node.level, node.module)
        if base is None:
            return
        if base:
            self._add_strict_chain(base)
        for alias in node.names:
            if alias.name == "*":
                continue
            candidate = f"{base}.{alias.name}" if base else alias.name
            if not candidate.isidentifier() and "." not in candidate:
                continue
            self._add_loose(candidate)

    def visit_Call(self, node: ast.Call) -> None:
        target = _dynamic_import_target(node.func)
        if target is not None:
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                self._add_strict_chain(arg.value)
            else:
                self.notes.append(
                    PlanWarning(
                        "dynamic",
                        f"{target}() with non-literal module name in "
                        f"{self.filename}; static analysis cannot see it "
                        "(whole-package embedding covers dynamic imports "
                        "within bundled packages)",
                    )
                )
        self.generic_visit(node)


def _scan_module(module_name: str, path: Path, is_package: bool) -> _ScanResult:
    try:
        raw = path.read_bytes()
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        tree = ast.parse(raw.decode(encoding), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, LookupError) as exc:
        return _ScanResult(set(), [PlanWarning("parse", f"could not parse {path}: {exc}")])
    scanner = _ImportScanner(module_name, is_package, str(path))
    scanner.visit(tree)
    return _ScanResult(scanner.imports, scanner.notes)


def _descend_package(pkg_dir: Path, rest: Sequence[str]) -> tuple[str, Path | None]:
    current = pkg_dir
    for index, part in enumerate(rest):
        last = index == len(rest) - 1
        sub = current / part
        if (sub / "__init__.py").is_file():
            if last:
                return "package", sub / "__init__.py"
            current = sub
            continue
        if last:
            if (current / f"{part}.py").is_file():
                return "module", current / f"{part}.py"
            if sub.is_dir():
                return "namespace", None
            return "", None
        if sub.is_dir():
            current = sub
            continue
        return "", None
    return "", None


def _locate(name: str, search_paths: Sequence[Path]) -> _Located | None:
    parts = name.split(".")
    top = parts[0]
    top_dir: Path | None = None
    ns_dirs: list[Path] = []
    for root in search_paths:
        pkg_dir = root / top
        if (pkg_dir / "__init__.py").is_file():
            top_dir = pkg_dir
            break
        module_file = root / f"{top}.py"
        if module_file.is_file():
            if len(parts) == 1:
                return _Located(name, module_file, False, None, ())
            continue
        if pkg_dir.is_dir():
            ns_dirs.append(pkg_dir)
    if top_dir is not None:
        if len(parts) == 1:
            return _Located(name, top_dir / "__init__.py", True, top_dir, ())
        kind, file = _descend_package(top_dir, parts[1:])
        if not kind:
            return None
        return _Located(name, file, kind != "module", top_dir, ())
    if ns_dirs:
        if len(parts) == 1:
            return _Located(name, None, True, None, tuple(ns_dirs))
        for ns_root in ns_dirs:
            kind, file = _descend_package(ns_root, parts[1:])
            if kind:
                return _Located(name, file, kind != "module", None, tuple(ns_dirs))
    return None


def _site_dirs(extra: Sequence[Path]) -> list[Path]:
    dirs: list[Path] = [path for path in extra if path.is_dir()]
    try:
        import site

        candidates = [*site.getsitepackages(), site.getusersitepackages()]
    except Exception:
        candidates = []
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_dir() and path not in dirs:
            dirs.append(path)
    return dirs


def _resolve_thirdparty(requests: Sequence[str | Path], site_paths: Sequence[Path]) -> dict[str, _ThirdPartyPackage]:
    resolved: dict[str, _ThirdPartyPackage] = {}
    if not requests:
        return resolved
    dirs = _site_dirs(_flatten_search_paths(site_paths))
    for request in requests:
        request_path = Path(str(request))
        if request_path.is_dir() or request_path.is_file():
            name = request_path.stem if request_path.is_file() else request_path.name
            _validate_dotted(name, f"package path {request_path}")
            resolved[name] = _ThirdPartyPackage(name, request_path, request_path.is_dir())
            continue
        name = _validate_dotted(str(request), "--include-package entry")
        if name in resolved:
            continue
        found: _ThirdPartyPackage | None = None
        for directory in dirs:
            pkg_dir = directory / name
            if (pkg_dir / "__init__.py").is_file():
                found = _ThirdPartyPackage(name, pkg_dir, True)
                break
            if (directory / f"{name}.py").is_file():
                found = _ThirdPartyPackage(name, directory / f"{name}.py", False)
                break
            if pkg_dir.is_dir():
                found = _ThirdPartyPackage(name, pkg_dir, True)
                break
        if found is None:
            searched = ", ".join(str(d) for d in dirs) or "<no site directories>"
            msg = f"--include-package {name!r}: package not found in: {searched}"
            raise ConsolidatorError(msg)
        resolved[name] = found
    return resolved


def _find_package_dir(directory: Path) -> Path:
    resolved = Path(directory).resolve()
    if (resolved / "__init__.py").is_file():
        return resolved
    candidates = sorted(
        child
        for child in resolved.iterdir()
        if child.is_dir()
        and (child / "__init__.py").is_file()
        and not child.name.startswith(".")
        and child.name != "__pycache__"
        and not child.name.endswith((".egg-info", ".dist-info"))
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        msg = f"{directory} is not a package and contains no package"
        raise ConsolidatorError(msg)
    names = ", ".join(child.name for child in candidates)
    msg = f"{directory} contains multiple packages ({names}); pass one explicitly"
    raise ConsolidatorError(msg)


def _has_main_function(init_path: Path) -> bool:
    try:
        raw = init_path.read_bytes()
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        tree = ast.parse(raw.decode(encoding), filename=str(init_path))
    except (OSError, SyntaxError, UnicodeDecodeError, LookupError):
        return False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main":
            return True
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == "main":
                    return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "main":
                    return True
    return False


def _synthesize_main(pkg_name: str, pkg_dir: Path, override: str | None) -> tuple[str, list[str]]:
    if override is not None:
        parts = override.split(".")
        module_file = pkg_dir / "/".join(parts) + ".py"
        package_init = pkg_dir / "/".join(parts) / "__init__.py"
        if not module_file.is_file() and not package_init.is_file():
            msg = (
                f"--main-module {override!r}: not found in package {pkg_name!r} "
                f"(tried {module_file} and {package_init})"
            )
            raise ConsolidatorError(msg)
        target = f"{pkg_name}.{override}"
        source = f"import runpy as _runpy\n_runpy.run_module({target!r}, run_name='__main__', alter_sys=True)\n"
        return source, [target]

    for candidate in _ENTRY_CANDIDATES:
        if (pkg_dir / f"{candidate}.py").is_file():
            target = f"{pkg_name}.{candidate}"
            source = f"import runpy as _runpy\n_runpy.run_module({target!r}, run_name='__main__', alter_sys=True)\n"
            return source, [target]

    init = pkg_dir / "__init__.py"
    if _has_main_function(init):
        source = (
            "import importlib as _importlib\n"
            f"_mod = _importlib.import_module({pkg_name!r})\n"
            "_main = getattr(_mod, 'main', None)\n"
            "if _main is None:\n"
            f"    raise SystemExit('package {pkg_name} has no callable main()')\n"
            "_main()\n"
        )
        return source, [pkg_name]

    tried = ", ".join(f"{candidate}.py" for candidate in _ENTRY_CANDIDATES)
    msg = (
        f"package {pkg_name!r} has no __main__.py and no obvious entry point "
        f"(tried {tried}, and {pkg_name}.main()); add a __main__.py, define "
        "a main() in __init__.py, or pass --main-module"
    )
    raise ConsolidatorError(msg)


def analyze(
    entry: str | Path,
    *,
    add_python_path: Sequence[str | Path] = (),
    module_mode: bool = False,
    include_packages: Sequence[str | Path] = (),
    site_paths: Sequence[str | Path] = (),
    exclude_modules: Sequence[str] = (),
    include_data: Sequence[str] = (),
    include_package_data: Sequence[str] = (),
    exclude_data: Sequence[str] = (),
    whole_package: bool = True,
    strict: bool = False,
    main_module: str | None = None,
) -> ConsolidationPlan:
    extra_roots = _flatten_search_paths(add_python_path)
    entry_path = Path(str(entry))
    entry_file: Path | None = None
    entry_module: str | None = None
    entry_archive: str | None = None
    entry_pkg_dir: Path | None = None

    if module_mode or (not module_mode and entry_path.is_dir()):
        if entry_path.is_dir():
            pkg_dir = _find_package_dir(entry_path)
            entry_module = _validate_dotted(pkg_dir.name, "package directory")
            entry_pkg_dir = pkg_dir
            data_roots = [entry_pkg_dir.parent, *extra_roots]
            roots = [*data_roots, Path.cwd()]
        else:
            entry_module = _validate_dotted(str(entry), "module entry (-m)")
            data_roots = list(extra_roots)
            roots = [Path.cwd(), *extra_roots]
    else:
        if not entry_path.is_file():
            msg = f"entry script not found: {entry}"
            raise ConsolidatorError(msg)
        entry_file = Path(entry_path).resolve()
        entry_archive = entry_file.name
        data_roots = [entry_file.parent, *extra_roots]
        roots = list(data_roots)

    search_paths: list[Path] = []
    for root in roots:
        if root not in search_paths:
            search_paths.append(root)
    data_roots = [root for root in data_roots if root in search_paths]

    modules: dict[str, BundledModule] = {}
    package_dirs: dict[str, tuple[Path, ...]] = {}
    warnings: list[PlanWarning] = []
    synthetic_files: dict[str, bytes] = {}
    scanned: set[str] = set()
    scan_queue: deque[str] = deque()
    thirdparty = _resolve_thirdparty(include_packages, site_paths)
    embedded_thirdparty: set[str] = set()

    def warn(kind: str, message: str) -> None:
        warning = PlanWarning(kind, message)
        if warning not in warnings:
            warnings.append(warning)

    def embed_module(name: str, file: Path, is_package: bool) -> None:
        existing = modules.get(name)
        if existing is not None:
            if existing.source != file:
                warn(
                    "conflict",
                    f"module {name!r}: keeping {existing.source}, ignoring duplicate at {file}",
                )
            return
        modules[name] = BundledModule(name, file, is_package)
        scan_queue.append(name)

    def embed_tree(top_name: str, dirs: Sequence[Path]) -> None:
        offenders: list[Path] = []

        def dir_excluded(rel_parts: tuple[str, ...]) -> bool:
            return _is_excluded(".".join((top_name, *rel_parts)), exclude_modules)

        for base in dirs:
            for file, rel in _walk_tree(base, prune_dir=dir_excluded):
                if file.suffix in _BINARY_SUFFIXES:
                    offenders.append(file)
                    continue
                if file.suffix != ".py":
                    continue
                mapped = _module_name_for(top_name, rel)
                if mapped is None:
                    warn("note", f"skipping non-importable file {file}")
                    continue
                name, is_package = mapped
                if _is_excluded(name, exclude_modules):
                    continue
                embed_module(name, file, is_package)
        merged = (
            *package_dirs.get(top_name, ()),
            *[d for d in dirs if d not in package_dirs.get(top_name, ())],
        )
        package_dirs[top_name] = tuple(dict.fromkeys(merged))
        if offenders:
            listing = ", ".join(str(p) for p in offenders[:5])
            msg = (
                f"package {top_name!r} contains compiled extension(s): "
                f"{listing}. condolidator bundles pure-Python packages only; "
                "exclude the offending subtree with --exclude-module if it "
                "is unused."
            )
            raise ConsolidatorError(msg)

    def embed_thirdparty(top_name: str, package: _ThirdPartyPackage) -> None:
        if top_name in embedded_thirdparty:
            return
        embedded_thirdparty.add(top_name)
        if package.is_dir:
            embed_tree(top_name, [package.path])
        else:
            embed_module(top_name, package.path, False)

    def embed_located(located: _Located, name: str) -> None:
        top = name.split(".", maxsplit=1)[0]
        if whole_package and located.namespace_dirs:
            embed_tree(top, located.namespace_dirs)
        elif whole_package and located.top_package_dir is not None:
            embed_tree(top, [located.top_package_dir])
        elif located.file is not None:
            embed_module(name, located.file, located.is_package)

    def handle_import(name: str, strict: bool, importer: str) -> None:
        "Handle import."
        top = name.split(".", maxsplit=1)[0]
        if name in modules:
            return
        if strict and _is_excluded(name, exclude_modules):
            warn(
                "excluded",
                f"{name!r} matches an exclusion pattern but is imported by "
                f"{importer}; the bundle will fail if that import executes",
            )
            return
        located = _locate(name, search_paths)
        if located is not None:
            if _is_excluded(name, exclude_modules):
                warn(
                    "excluded",
                    f"{name!r} matches an exclusion pattern but is imported "
                    f"by {importer}; the bundle will fail if that import "
                    "executes",
                )
                return
            embed_located(located, name)
            return
        if top in _IGNORED_TOPS:
            return
        if top in sys.stdlib_module_names:
            return
        if top in thirdparty:
            embed_thirdparty(top, thirdparty[top])
            return
        if not strict:
            return
        if name == top:
            warn(
                "unresolved",
                f"cannot resolve import {name!r} (imported by {importer}); "
                "not found in the search paths, not stdlib and not bundled. "
                "Add its directory with --add-python-path, or use "
                f"--include-package {top} if it is a third-party dependency.",
            )
        elif top in modules:
            warn(
                "broken",
                f"import chain {name!r} (imported by {importer}): {name!r} not found inside bundled package {top!r}",
            )

    def process_scan(result: _ScanResult, importer: str) -> None:
        "Process scan."
        for note in result.notes:
            if note not in warnings:
                warnings.append(note)
        for name, strict in sorted(result.imports):
            handle_import(name, strict, importer)

    def ensure_main_module(pkg_dir: Path, pkg_module_name: str) -> None:
        "Ensure main module."
        archive = f"{pkg_module_name.replace('.', '/')}/__main__.py"
        real_main = pkg_dir / "__main__.py"
        if main_module is not None:
            source, extra = _synthesize_main(pkg_module_name, pkg_dir, main_module)
            synthetic_files[archive] = source.encode("utf-8")
            for dep in extra:
                handle_import(dep, True, f"<synthesized {pkg_module_name}.__main__>")
            return
        if real_main.is_file():
            embed_module(f"{pkg_module_name}.__main__", real_main, False)
            return
        source, extra = _synthesize_main(pkg_module_name, pkg_dir, None)
        synthetic_files[archive] = source.encode("utf-8")
        for dep in extra:
            handle_import(dep, True, f"<synthesized {pkg_module_name}.__main__>")

    if entry_module is not None:
        if entry_pkg_dir is not None:
            if whole_package:
                embed_tree(entry_module, [entry_pkg_dir])
            else:
                embed_module(entry_module, entry_pkg_dir / "__init__.py", True)
            ensure_main_module(entry_pkg_dir, entry_module)
        else:
            located = _locate(entry_module, search_paths)
            if located is None:
                msg = f"module {entry_module!r} not found in: {', '.join(str(p) for p in search_paths)}"
                raise ConsolidatorError(msg)
            embed_located(located, entry_module)
            if located.is_package:
                if located.file is not None:
                    ensure_main_module(located.file.parent, entry_module)
                elif f"{entry_module}.__main__" not in modules:
                    msg = (
                        f"namespace package {entry_module!r} has no "
                        "__main__.py; cannot synthesize one for namespace "
                        "packages — add a __main__.py to one of its portions"
                    )
                    raise ConsolidatorError(msg)
    else:
        assert entry_file is not None
        stem = entry_file.stem if entry_file.stem.isidentifier() else "entry"
        entry_scan = _scan_module(stem, entry_file, False)
        scanned.add(stem)
        process_scan(entry_scan, f"<entry {entry_file.name}>")

    while scan_queue:
        name = scan_queue.popleft()
        if name in scanned:
            continue
        scanned.add(name)
        module = modules.get(name)
        if module is None or not module.source.is_file():
            continue
        process_scan(_scan_module(name, module.source, module.is_package), name)

    for top_name, package in sorted(thirdparty.items()):
        if top_name in embedded_thirdparty:
            continue
        if any(m == top_name or m.startswith(top_name + ".") for m in modules):
            warn(
                "note",
                f"--include-package {top_name}: a local copy was already bundled; the third-party entry was ignored",
            )
            continue
        embed_thirdparty(top_name, package)

    data_files = _collect_data(
        specs=include_data,
        package_data=include_package_data,
        data_excludes=exclude_data,
        exclude_modules=exclude_modules,
        data_roots=tuple(data_roots),
        package_dirs=package_dirs,
        modules=modules,
        warn=warn,
    )

    warnings.sort(key=lambda warning: (warning.kind, warning.message))
    if strict:
        failures = [w for w in warnings if w.kind in _STRICT_WARNING_KINDS]
        if failures:
            detail = "\n  ".join(str(w) for w in failures)
            msg = f"strict mode: {len(failures)} unresolved import(s):\n  {detail}"
            raise ConsolidatorError(msg)

    return ConsolidationPlan(
        entry=entry_file or entry_pkg_dir or Path(str(entry)).resolve(),
        entry_module=entry_module,
        entry_archive=entry_archive,
        search_paths=tuple(search_paths),
        modules=modules,
        data_files=data_files,
        package_dirs=package_dirs,
        warnings=warnings,
        synthetic_files=synthetic_files,
    )


def _parse_data_spec(
    spec: str | Path,
    data_roots: Sequence[Path],
    package_dirs: dict[str, tuple[Path, ...]],
    warn: Callable[[str, str], None],
) -> tuple[list[tuple[Path, str]], str] | None:
    spec = str(spec)
    prefix: str | None
    if ":" in spec:
        prefix, pattern = spec.split(":", 1)
        pattern = pattern or "**"
    else:
        prefix, pattern = None, spec

    if prefix is None:
        path = Path(spec)
        if path.is_dir():
            return [(path, path.name)], "**"
        if path.is_file():
            return [(path.parent, "")], path.name
        return [(root, "") for root in data_roots], spec

    if prefix in package_dirs:
        return [(d, prefix) for d in package_dirs[prefix]], pattern
    for root in data_roots:
        if (root / prefix).is_dir():
            return [(root / prefix, prefix)], pattern
    path = Path(prefix)
    if path.is_dir():
        return [(path, path.name)], pattern
    warn(
        "note",
        f"--include-data {spec!r}: {prefix!r} is neither a bundled package nor an existing directory",
    )
    return None


def _collect_data(
    *,
    specs: Sequence[str],
    package_data: Sequence[str],
    data_excludes: Sequence[str],
    exclude_modules: Sequence[str],
    data_roots: tuple[Path, ...],
    package_dirs: dict[str, tuple[Path, ...]],
    modules: dict[str, BundledModule],
    warn: Callable[[str, str], None],
) -> dict[str, Path]:
    out: dict[str, Path] = {}
    module_archives = {module.archive_path for module in modules.values()}
    all_excludes = [*_DEFAULT_DATA_EXCLUDES, *data_excludes]

    def excluded(archive: str) -> bool:
        return any(_glob_match(pattern, archive) for pattern in all_excludes)

    def add(archive: str, source: Path) -> None:
        if excluded(archive):
            return
        if archive in module_archives:
            warn(
                "conflict",
                f"data file {source} collides with bundled module {archive}; the module wins",
            )
            return
        existing = out.get(archive)
        if existing is not None:
            if existing != source:
                warn(
                    "conflict",
                    f"bundle path {archive!r} already provided by {existing}; ignoring {source}",
                )
            return
        out[archive] = source

    if "*" in package_data:
        targets = sorted(package_dirs.items())
    else:
        targets = sorted((name, package_dirs[name]) for name in set(package_data) if name in package_dirs)
        for name in package_data:
            if name != "*" and name not in package_dirs:
                warn(
                    "note",
                    f"--include-package-data {name!r}: not a bundled package directory",
                )

    for pkg_name, dirs in targets:
        for base in dirs:

            def prune_pkg_dir(rel_parts: tuple[str, ...], _pkg: str = pkg_name) -> bool:
                return _is_excluded(".".join((_pkg, *rel_parts)), exclude_modules)

            for file, rel in _walk_tree(base, prune_dir=prune_pkg_dir):
                if file.suffix == ".py":
                    continue
                add(f"{pkg_name}/{'/'.join(rel)}", file)

    for spec in specs:
        parsed = _parse_data_spec(spec, data_roots, package_dirs, warn)
        if parsed is None:
            continue
        base_dirs, pattern = parsed
        for base, arc_prefix in base_dirs:

            def prune_dir(rel_parts: tuple[str, ...], _base: Path = base) -> bool:
                if rel_parts and rel_parts[0] in package_dirs:
                    dotted = ".".join(rel_parts)
                    return _is_excluded(dotted, exclude_modules)
                return False

            for file, rel in _walk_tree(base, prune_dir=prune_dir):
                if file.suffix == ".py":
                    continue
                rel_posix = "/".join(rel)
                if not _glob_match(pattern, rel_posix):
                    continue
                archive = f"{arc_prefix}/{rel_posix}" if arc_prefix else rel_posix
                add(archive, file)

    return out


_BOOTSTRAP_TEMPLATE = """\
import atexit
import base64
import io
import os
import runpy
import shutil
import sys
import tempfile
import zipfile

_BUNDLE = (
@@BUNDLE@@
)

_ENTRY_PATH = @@ENTRY_PATH@@
_ENTRY_MODULE = @@ENTRY_MODULE@@

_KEEP = @@KEEP@@ or os.environ.get("CONSOLIDATOR_KEEP") == "1"
_DEBUG = @@DEBUG@@ or os.environ.get("CONSOLIDATOR_DEBUG") == "1"

_IS_FORKED_CHILD = False


def _mark_forked_child() -> None:
    global _IS_FORKED_CHILD
    _IS_FORKED_CHILD = True


try:
    os.register_at_fork(after_in_child=_mark_forked_child)
except (AttributeError, NotImplementedError):
    pass


class BundleError(RuntimeError):
    pass


def _validate_member(name: str) -> str:
    name = name.rstrip("/")
    if not name or name.startswith("/") or "\\x00" in name:
        raise BundleError(f"unsafe bundle member {name!r}")
    for part in name.split("/"):
        if part in ("", ".", ".."):
            raise BundleError(f"unsafe bundle member {name!r}")
    return name


def _extract_bundle() -> str:
    root = tempfile.mkdtemp(prefix="consolidator-")
    try:
        raw = base64.b64decode(_BUNDLE)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                name = _validate_member(info.filename)
                target = str(Path(root).joinpath(*name.split("/")))
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                parent = str(Path(target).parent)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with archive.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                mode = (info.external_attr >> 16) & 0o777
                os.chmod(target, mode if mode & 0o400 else 0o644)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root


def _cleanup(root: str) -> None:
    if not _IS_FORKED_CHILD:
        shutil.rmtree(root, ignore_errors=True)


def _bootstrap() -> str:
    root = _extract_bundle()
    sys.path.insert(0, root)
    if _DEBUG:
        print(f"consolidator: extracted to {root}", file=sys.stderr)
    if not _KEEP:
        atexit.register(_cleanup, root)
    return root


if __name__ in ("__main__", "__mp_main__"):
    _CONSOLIDATOR_ROOT = _bootstrap()

if __name__ == "__main__":
    if _ENTRY_MODULE is not None:
        runpy.run_module(_ENTRY_MODULE, run_name="__main__", alter_sys=True)
    else:
        runpy.run_path(
            str(Path(_CONSOLIDATOR_ROOT) / _ENTRY_PATH), run_name="__main__"
        )
"""


def _zip_timestamp(
    deterministic: bool,
) -> tuple[int, int, int, int, int, int]:
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if epoch.isdigit():
        moment = time.gmtime(int(epoch))
        return (
            max(moment.tm_year, 1980),
            moment.tm_mon,
            moment.tm_mday,
            moment.tm_hour,
            moment.tm_min,
            moment.tm_sec,
        )
    if deterministic:
        return _DETERMINISTIC_TIMESTAMP
    moment = time.localtime()
    return (
        moment.tm_year,
        moment.tm_mon,
        moment.tm_mday,
        moment.tm_hour,
        moment.tm_min,
        moment.tm_sec,
    )


def _archive_members(plan: ConsolidationPlan) -> dict[str, Path | bytes]:
    members: dict[str, Path | bytes] = {}
    if plan.entry_module is None:
        assert plan.entry_archive is not None
        members[plan.entry_archive] = plan.entry
    for module in sorted(plan.modules.values(), key=lambda m: m.archive_path):
        archive = module.archive_path
        existing = members.get(archive)
        if existing is not None and existing != module.source:
            msg = f"internal error: archive path {archive!r} maps to both {existing} and {module.source}"
            raise ConsolidatorError(msg)
        members[archive] = module.source
    for archive, source in sorted(plan.synthetic_files.items()):
        members[archive] = source
    for archive, source in sorted(plan.data_files.items()):
        if archive in members:
            continue
        members[archive] = source
    return members


def _render_header(plan: ConsolidationPlan, *, compression: str, deterministic: bool) -> str:
    top_names = sorted({module.name.split(".")[0] for module in plan.modules.values()})
    lines = [
        "# " + "=" * 74,
        "# consolidator self-contained bundle",
        f"# entry    : {plan.entry_module or plan.entry_archive or plan.entry}",
        f"# modules  : {len(plan.modules)} (top-level: {', '.join(top_names) or '-'})",
        f"# data     : {len(plan.data_files)} file(s)",
        f"# built by : consolidator(python {platform.python_version()}, {compression})",
    ]
    if plan.synthetic_files:
        lines.append("synthetic: " + ", ".join(sorted(plan.synthetic_files)))
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if not deterministic:
        lines.append(f"# built at : {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
    elif epoch:
        lines.append(f"# built at : SOURCE_DATE_EPOCH={epoch}")
    lines += [
        "#",
        "# Running this file extracts the bundle into a private temp dir,",
        "# prepends it to sys.path and executes the entry point unchanged.",
        "# Debug: CONSOLIDATOR_DEBUG=1   Keep files: CONSOLIDATOR_KEEP=1",
        "# " + "=" * 74,
    ]
    return "\n".join(lines)


def render(
    plan: ConsolidationPlan,
    *,
    python: str | None = None,
    compression: str = "deflate",
    runtime_debug: bool = False,
    keep_tmpdir: bool = False,
    deterministic: bool = True,
) -> str:
    if compression not in _COMPRESSIONS:
        msg = f"unknown compression {compression!r}; expected one of {sorted(_COMPRESSIONS)}"
        raise ConsolidatorError(msg)
    interpreter = python or sys.executable or "python3"
    members = _archive_members(plan)
    timestamp = _zip_timestamp(deterministic)
    compress_type = _COMPRESSIONS[compression]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compress_type) as archive:
        for archive_path, source in members.items():
            info = zipfile.ZipInfo(archive_path, date_time=timestamp)
            info.create_system = 3
            if isinstance(source, bytes):
                data = source
                mode = 0o644
            else:
                data = _read_bytes(source)
                mode = 0o755 if os.access(source, os.X_OK) else 0o644
            info.external_attr = mode << 16
            info.compress_type = compress_type
            archive.writestr(info, data)
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    chunks = "\n".join(f'    "{payload[index : index + 76]}"' for index in range(0, len(payload), 76))
    if not chunks:
        chunks = '    ""'

    body = (
        _BOOTSTRAP_TEMPLATE.replace("@@BUNDLE@@", chunks)
        .replace("@@ENTRY_PATH@@", repr(plan.entry_archive))
        .replace("@@ENTRY_MODULE@@", repr(plan.entry_module))
        .replace("@@DEBUG@@", "True" if runtime_debug else "False")
        .replace("@@KEEP@@", "True" if keep_tmpdir else "False")
    )
    header = _render_header(plan, compression=compression, deterministic=deterministic)
    return f"#!{interpreter}\n{header}\n{body}"


def write_output(text: str, output: str | Path | None) -> Path | None:
    "Write output."
    if output is None or str(output) == "-":
        sys.stdout.write(text)
        return None
    path = Path(output)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    if text.startswith("#!"):
        os.chmod(path, 0o755)
    return path


_ANALYZE_KEYS: frozenset[str] = frozenset(
    {
        "add_python_path",
        "module_mode",
        "include_packages",
        "site_paths",
        "exclude_modules",
        "include_data",
        "include_package_data",
        "exclude_data",
        "whole_package",
        "strict",
        "main_module",
    }
)
_RENDER_KEYS: frozenset[str] = frozenset({"python", "compression", "runtime_debug", "keep_tmpdir", "deterministic"})


def consolidate(
    entry: str | Path,
    output: str | Path | None = None,
    **options: object,
) -> Path | None:
    unknown = set(options) - _ANALYZE_KEYS - _RENDER_KEYS
    if unknown:
        msg = f"unknown consolidate() arguments: {sorted(unknown)}"
        raise TypeError(msg)
    analyze_options = {k: v for k, v in options.items() if k in _ANALYZE_KEYS}
    render_options = {k: v for k, v in options.items() if k in _RENDER_KEYS}

    plan = analyze(entry, **analyze_options)  # type: ignore[arg-type]
    for warning in plan.warnings:
        log.warning("%s", warning)
    text = render(plan, **render_options)  # type: ignore[arg-type]
    path = write_output(text, output)
    if path is not None:
        stem = path.stem
        if stem in sys.stdlib_module_names:
            log.warning(
                "output file %r shadows the standard-library module %r; "
                "imports of %r inside the bundle may pick up the bundle "
                "itself — pick another file name",
                path.name,
                stem,
                stem,
            )
        log.info(
            "wrote %s (%s, %d modules, %d synthetic, %d data files)",
            path,
            _human_size(len(text.encode("utf-8"))),
            len(plan.modules),
            len(plan.synthetic_files),
            len(plan.data_files),
        )
    return path


class _PackageDataAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        "Invoke the behavior represented by this object."
        names = list(values) if values else ["*"]  # type: ignore[arg-type]
        current = list(getattr(namespace, self.dest, None) or [])
        current.extend(names)
        setattr(namespace, self.dest, current)


def _default_output_name(entry: str) -> str:
    path = Path(entry)
    if path.is_dir():
        stem = _find_package_dir(path).name
    else:
        stem = path.stem or path.name
    if not stem or not stem.isidentifier():
        stem = re.sub(r"\W+", "_", stem) or "bundle"
    return str(Path("output") / f"{stem}_standalone.py")


def build_parser() -> argparse.ArgumentParser:
    "Build parser."
    parser = _ParserShim()
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    "Run the command-line interface."
    args = build_parser().parse_args(argv)
    if args.quiet:
        level = "ERROR"
    elif args.verbose:
        level = "DEBUG"
    else:
        level = "INFO"
    logger.remove()
    logger.add(sys.stderr, level=level, format="consolidator: {message}")
    try:
        if args.dry_run:
            plan = analyze(
                args.entry,
                add_python_path=args.add_python_path,
                module_mode=args.module,
                include_packages=args.include_package,
                site_paths=args.site_path,
                exclude_modules=args.exclude_module,
                include_data=args.include_data,
                include_package_data=args.include_package_data,
                exclude_data=args.exclude_data,
                whole_package=args.whole_package,
                strict=args.strict,
                main_module=args.main_module,
            )
            print(plan.summary())
            return 0
        consolidate(
            args.entry,
            output=(args.output or _default_output_name(args.entry)),
            add_python_path=args.add_python_path,
            module_mode=args.module,
            include_packages=args.include_package,
            site_paths=args.site_path,
            exclude_modules=args.exclude_module,
            include_data=args.include_data,
            include_package_data=args.include_package_data,
            exclude_data=args.exclude_data,
            whole_package=args.whole_package,
            strict=args.strict,
            main_module=args.main_module,
            python=args.python,
            compression=args.compression,
            runtime_debug=args.runtime_debug,
            keep_tmpdir=args.keep_tmpdir,
            deterministic=args.deterministic,
        )
        return 0
    except ConsolidatorError as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


def _run() -> None:
    raise SystemExit(main())


app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
)


@app.command(
    help="Consolidate a pure-Python package or script into a single self-contained file.",
    epilog="examples:\n  bundle the package in the current directory (saved under ./output/):\n    python consolidator.py .\n\n  bundle a script with its sibling modules:\n    python consolidator.py cli.py -o standalone.py\n\n  bundle a package and embed its data files:\n    python consolidator.py demo_app --include-package-data\n\n  auto-generate __main__.py for a package that has none:\n    python consolidator.py my_pkg/\n    python consolidator.py my_pkg/ --main-module cli\n\n  search extra source directories and embed matching data files:\n    python consolidator.py app/main.py -p src --include-data 'assets/*.json'\n\n  include a pure-Python third-party dependency:\n    python consolidator.py cli.py --include-package rich --site-path vendor\n\n  preview what would be bundled:\n    python consolidator.py demo_app --dry-run\n",
)
def cli(
    entry: Annotated[str, typer.Argument(help="entry script, package directory, or module name (with -m)")],
    output: Annotated[
        str | None,
        typer.Option("-o", "--output", help="output file ('-' for stdout); default: ./output/<name>_standalone.py"),
    ] = None,
    module: Annotated[
        bool, typer.Option("-m", "--module", help="treat ENTRY as a module name, emulating `python -m ENTRY`")
    ] = False,
    add_python_path: Annotated[
        list[str],
        typer.Option(
            "-p",
            "--add-python-path",
            help="extra directories to search for local packages (repeatable; ':'-separated lists allowed)",
            metavar="DIR",
        ),
    ] = [],
    include_package: Annotated[
        list[str],
        typer.Option(
            "--include-package", help="pure-Python third-party packages to bundle (name or path)", metavar="PKG"
        ),
    ] = [],
    site_path: Annotated[
        list[str],
        typer.Option("--site-path", help="extra directories searched for --include-package names", metavar="DIR"),
    ] = [],
    exclude_module: Annotated[
        list[str],
        typer.Option(
            "--exclude-module",
            help="exclude modules/packages (fnmatch on dotted names; a bare name excludes its whole subtree)",
            metavar="PAT",
        ),
    ] = [],
    include_data: Annotated[
        list[str],
        typer.Option(
            "--include-data",
            help="embed data files: 'PKG:GLOB', 'GLOB' (relative to the entry directory and --add-python-path roots), or an existing directory/file path",
            metavar="SPEC",
        ),
    ] = [],
    exclude_data: Annotated[
        list[str],
        typer.Option(
            "--exclude-data", help="exclude data files (glob matched against bundle-relative paths)", metavar="GLOB"
        ),
    ] = [],
    main_module: Annotated[
        str | None,
        typer.Option(
            "--main-module",
            help="module inside the entry package to run as its __main__ (dotted, relative to the package; auto-detected if omitted)",
            metavar="MOD",
        ),
    ] = None,
    whole_package: Annotated[
        bool,
        typer.Option("--no-whole-package", help="embed only statically reachable modules instead of whole packages"),
    ] = True,
    python: Annotated[
        str | None,
        typer.Option(
            "--python", help="shebang interpreter (default: the python running consolidator)", metavar="INTERP"
        ),
    ] = None,
    compression: Annotated[
        str, typer.Option("--compression", help="payload compression (default: deflate)")
    ] = "deflate",
    keep_tmpdir: Annotated[
        bool, typer.Option("--keep-tmpdir", help="the generated bundle keeps its extraction directory")
    ] = False,
    runtime_debug: Annotated[
        bool, typer.Option("--runtime-debug", help="the generated bundle prints its extraction directory to stderr")
    ] = False,
    strict: Annotated[bool, typer.Option("--strict", help="fail the build on unresolved imports")] = False,
    deterministic: Annotated[
        bool, typer.Option("--no-deterministic", help="embed the build date instead of a fixed timestamp")
    ] = True,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="print the consolidation plan and exit without writing")
    ] = False,
    quiet: Annotated[bool, typer.Option("-q", "--quiet", help="only report errors")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="log more detail while building")] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="show program's version number and exit"
        ),
    ] = False,
) -> None:
    if compression is not None and compression not in sorted(_COMPRESSIONS):
        msg = f"invalid choice: {compression!r} (choose from {sorted(_COMPRESSIONS)})"
        raise typer.BadParameter(msg)
    _STATE.update(
        entry=entry,
        output=output,
        module=module,
        add_python_path=add_python_path,
        include_package=include_package,
        site_path=site_path,
        exclude_module=exclude_module,
        include_data=include_data,
        exclude_data=exclude_data,
        main_module=main_module,
        whole_package=whole_package,
        python=python,
        compression=compression,
        keep_tmpdir=keep_tmpdir,
        runtime_debug=runtime_debug,
        strict=strict,
        deterministic=deterministic,
        dry_run=dry_run,
        quiet=quiet,
        verbose=verbose,
    )
    _run()


if __name__ == "__main__":
    app()
