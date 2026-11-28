#!/data/data/com.termux/files/usr/bin/env python
"""Bundle a regular Python package into a portable single Python file.

Usage:
    python consolidate.py path/to/package
    python consolidate.py path/to/package --verify
    python consolidate.py path/to/package -o /somewhere/package.py

The output retains separate module namespaces. This is essential for correct
Python name resolution: flattening definitions and hoisting conditional imports
can silently change behavior. Relative imports are rewritten as absolute imports
in embedded Python sources; imports inside those sources retain their original
execution points. Package resources are embedded and extracted on first import.

Requires libcst (python -m pip install libcst). Uses four worker processes.
The input must be trusted. --verify imports and executes both copies in fresh
Python processes. See --help for limitations.
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


try:
    import libcst as cst
except ImportError:
    msg = "libcst is required; install it with: python -m pip install libcst"
    raise SystemExit(msg)


class RewriteRelativeImports(cst.CSTTransformer):
    def __init__(self, package: str, filename: str):
        self.package = package
        self.filename = filename

    def leave_ImportFrom(self, original_node, updated_node):
        level = len(updated_node.relative)
        if not level:
            return updated_node
        parts = self.package.split(".") if self.package else []
        if level > len(parts):
            msg = f"{self.filename}: relative import beyond top-level package"
            raise ValueError(msg)
        prefix = parts[: len(parts) - level + 1]
        if updated_node.module is not None:
            prefix.append(cst.Module([]).code_for_node(updated_node.module))
        return updated_node.with_changes(module=cst.parse_expression(".".join(prefix)), relative=())


def process_module(task):
    path_string, relative_path, name, is_package = task
    path = Path(path_string)
    package = name if is_package else name.rpartition(".")[0]
    try:
        import tokenize

        with tokenize.open(path) as f:
            text = f.read()
        tree = cst.parse_module(text)
        text = tree.visit(RewriteRelativeImports(package, str(path))).code
        compile(text, str(path), "exec", dont_inherit=True)
    except (SyntaxError, UnicodeError, ValueError, cst.ParserSyntaxError) as exc:
        msg = f"Cannot process {path}: {exc}"
        raise ValueError(msg) from exc
    return name, {"path": relative_path, "package": is_package, "source": text}


def collect(root: Path, output: Path):
    if not (root / "__init__.py").is_file():
        msg = "Input must be a regular package directory containing __init__.py"
        raise ValueError(msg)
    if not root.name.isidentifier():
        msg = "Package directory name must be a Python identifier"
        raise ValueError(msg)
    modules = {}
    data = {}
    tasks = []
    seen = set()
    # An output directory inside the package is a build artifact, not input.
    excluded_dirs = {root / "output"}
    if output.parent != root and output.parent.is_relative_to(root):
        excluded_dirs.add(output.parent)
    for path in sorted(root.rglob("*")):
        if path == output or any(path.is_relative_to(d) for d in excluded_dirs):
            continue
        if path.is_symlink():
            msg = f"Symlinks are not supported: {path}"
            raise ValueError(msg)
        if not path.is_file() or "__pycache__" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(root)
        if path.suffix in (".so", ".pyd", ".dll", ".dylib"):
            msg = f"Native libraries cannot be bundled: {path}"
            raise ValueError(msg)
        if path.suffix == ".py":
            pieces = list(rel.parts)
            is_package = pieces[-1] == "__init__.py"
            pieces[-1] = pieces[-1][:-3]
            if is_package:
                pieces.pop()
            if not all(p.isidentifier() for p in pieces):
                msg = f"Invalid module path: {rel}"
                raise ValueError(msg)
            name = ".".join([root.name, *pieces])
            if name in seen:
                msg = f"A module and package have the same name: {name}"
                raise ValueError(msg)
            seen.add(name)
            tasks.append((str(path), rel.as_posix(), name, is_package))
        elif path.suffix not in (".pyc", ".pyo"):
            data[rel.as_posix()] = base64.b64encode(path.read_bytes()).decode("ascii")
    with ProcessPoolExecutor(max_workers=4) as pool:
        modules = dict(pool.map(process_module, tasks))
    return modules, data


RUNTIME = r"""
# ===== bundle runtime =====
import base64 as _bundle_base64
import hashlib as _bundle_hashlib
import importlib.abc as _bundle_abc
import importlib.machinery as _bundle_machinery
import importlib.util as _bundle_util
import os as _bundle_os
import pathlib as _bundle_pathlib
import sys as _bundle_sys
import tempfile as _bundle_tempfile
import threading as _bundle_threading

_bundle_lock = _bundle_threading.RLock()
_bundle_temp = None

def _bundle_materialize():
    global _bundle_temp
    with _bundle_lock:
        if _bundle_temp is None:
            # Keep the TemporaryDirectory alive for the life of the interpreter.
            _bundle_temp = _bundle_tempfile.TemporaryDirectory(prefix='pybundle-')
            root = _bundle_pathlib.Path(_bundle_temp.name) / _bundle_name
            root.mkdir()
            for item in _bundle_modules.values():
                target = root / item['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item['source'], encoding='utf-8')
            for name, value in _bundle_data.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(_bundle_base64.b64decode(value))
        return _bundle_pathlib.Path(_bundle_temp.name) / _bundle_name

class _BundleLoader(_bundle_abc.SourceLoader):
    def __init__(self, name):
        self.name = name
    def get_filename(self, fullname):
        if fullname != self.name:
            raise ImportError(fullname)
        return str(_bundle_materialize() / _bundle_modules[fullname]['path'])
    def get_data(self, path):
        return _bundle_pathlib.Path(path).read_bytes()
    def get_source(self, fullname):
        if fullname != self.name:
            raise ImportError(fullname)
        return _bundle_modules[fullname]['source']
    def is_package(self, fullname):
        return _bundle_modules[fullname]['package']
    def exec_module(self, module):
        source = self.get_source(module.__spec__.name)
        exec(compile(source, self.get_filename(module.__spec__.name), 'exec', dont_inherit=True), module.__dict__)
    def get_resource_reader(self, fullname):
        if not self.is_package(fullname):
            return None
        # Standard reader supports both legacy APIs and importlib.resources.files.
        from importlib.readers import FileReader
        return FileReader(self)

class _BundleFinder(_bundle_abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        item = _bundle_modules.get(fullname)
        if item is None:
            return None
        loader = _BundleLoader(fullname)
        return _bundle_util.spec_from_file_location(fullname, loader.get_filename(fullname), loader=loader,
            submodule_search_locations=[str(_bundle_pathlib.Path(loader.get_filename(fullname)).parent)] if item['package'] else None)

# Register before running __init__: it can import its own submodules.
_bundle_finder = _BundleFinder()
_bundle_sys.meta_path.insert(0, _bundle_finder)
_bundle_root = _bundle_materialize()
_bundle_root_spec = _bundle_util.spec_from_file_location(
    _bundle_name, str(_bundle_root / '__init__.py'), loader=_BundleLoader(_bundle_name),
    submodule_search_locations=[str(_bundle_root)])

if __name__ == '__main__':
    # Match python -m package: initialize the package, then run package.__main__.
    import importlib as _bundle_importlib
    import runpy as _bundle_runpy
    _bundle_pkg = _bundle_util.module_from_spec(_bundle_root_spec)
    _bundle_sys.modules[_bundle_name] = _bundle_pkg
    _bundle_root_spec.loader.exec_module(_bundle_pkg)
    if _bundle_name + '.__main__' not in _bundle_modules:
        raise SystemExit('This package has no __main__.py')
    _bundle_runpy.run_module(_bundle_name + '.__main__', run_name='__main__', alter_sys=True)
else:
    # Execute the original initializer in an isolated dictionary, then publish it.
    # Runtime helpers remain in the loader's globals, not the package namespace.
    _bundle_real = _bundle_util.module_from_spec(_bundle_root_spec)
    _bundle_sys.modules[_bundle_name] = _bundle_real
    try:
        _bundle_root_spec.loader.exec_module(_bundle_real)
    except BaseException:
        _bundle_sys.modules.pop(_bundle_name, None)
        raise
"""


def render(name, modules, data):
    # Independent module dictionaries prevent collisions even for private names.
    root_tree = cst.parse_module(modules[name]["source"])
    doc = root_tree.get_docstring()
    lines = [
        (repr(doc) if doc else '"""Single-file package bundle."""'),
        "# Generated by consolidate.py; do not edit embedded sources.",
        f"_bundle_name = {name!r}",
        "_bundle_modules = {}",
    ]
    for mod, entry in modules.items():
        lines.extend(["", f"# ===== {mod} =====", f"_bundle_modules[{mod!r}] = {entry!r}"])
    lines += [
        "",
        "# ===== package data (base64) =====",
        f"_bundle_data = {data!r}",
        RUNTIME,
    ]
    return "\n".join(lines) + "\n"


def verify(root: Path, output: Path):
    # A differential smoke test, not proof of full behavioral equivalence.
    probe = r"""import importlib, json, sys
m=importlib.import_module(sys.argv[1])
print(json.dumps({'name':m.__name__,'doc':m.__doc__,'public':sorted(x for x in dir(m) if not x.startswith('_')), 'all':getattr(m,'__all__',None)}, sort_keys=True, default=repr))"""
    with tempfile.TemporaryDirectory(prefix="bundle-check-") as td:
        # Copy just the output into a clean directory to prevent accidental original imports.
        import shutil

        target = Path(td) / (root.name + ".py")
        shutil.copy2(output, target)
        results = []
        for location in (root.parent, Path(td)):
            env = os.environ.copy()
            env["PYTHONPATH"] = str(location)
            p = subprocess.run(
                [sys.executable, "-c", probe, root.name],
                cwd=td,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if p.returncode:
                msg = f"Verification import failed for {location}:\n{p.stderr}"
                raise RuntimeError(msg)
            results.append(p.stdout)
        if results[0] != results[1]:
            msg = f"Verification mismatch:\noriginal: {results[0]}\nbundle: {results[1]}"
            raise RuntimeError(msg)
    print(
        "Verification passed: initializer import and public-name smoke test.",
        file=sys.stderr,
    )
    print(
        "Run the package’s own test suite against the bundle for stronger verification.",
        file=sys.stderr,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Limitations: no native extensions/shared libraries; distribution metadata, entry-point registration, namespace root packages, external files and dynamic relative imports are not bundled. Import timing is preserved rather than hoisted. LibCST preserves source comments and formatting when rewriting imports. Uses four worker processes. Resource files are extracted to a temporary directory. Verification is a smoke test only.",
    )
    parser.add_argument(
        "package",
        type=Path,
        nargs="?",
        default=Path(),
        help="path to the regular package directory (default: current directory)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output file, named <package>.py (default: ./output/<package>.py)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="compare root import and public names in subprocesses",
    )
    args = parser.parse_args(argv)
    try:
        root = args.package.resolve(strict=True)
        output = (args.output if args.output is not None else Path("output") / (root.name + ".py")).resolve()
        if not root.is_dir():
            msg = "Package path must be a directory"
            raise ValueError(msg)
        if output.name != root.name + ".py":
            msg = f"Output must be named {root.name}.py so imports resolve correctly"
            raise ValueError(msg)
        modules, data = collect(root, output)
        content = render(root.name, modules, data)
        compile(content, str(output), "exec")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        if args.verify:
            verify(root, output)
        print(f"Wrote {output} ({len(modules)} modules, {len(data)} data files)")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
