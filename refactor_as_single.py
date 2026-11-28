#!/data/data/com.termux/files/usr/bin/env python
"""
Consolidate a multi-file package into one module. Merges the modules of a package (or of a # File:-sectioned dump), rewrites the relative imports, the six imports, the os.path calls and the logging setup to their current equivalents, strips the docstrings and injects the standard imports into the resulting single file.

Usage:
    rassingle [package] [-o single.py]

Positional arguments:
    input Package directory or .py file (default: current directory)

Options:
    -f, --merged-file Read source from a single merged file containing '# File: <path>' section headers -o, --output Output file (default: <source_name>_single.py in cwd)

Examples:
    $ rassingle src/mypkg -o mypkg_single.py

Command-line script:
    run python rassingle.py or the rassingle command.

"""

from __future__ import annotations
import argparse
import ast
from pathlib import Path
import re
from typing import Any, Optional

from dh import MAX_WORKERS, atomic_write
import os
import sys
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


LOGURU_LOGFORMAT: str = "{time:YYYY-MM-DD HH:mm:ss.SSS} {level} {file.name}:{line} {message}"
MERGED_FILE_HEADER_RE = re.compile(r"^#\s*File:\s*(.+?)\s*$", re.MULTILINE)


def collect_python_files(root: Path) -> list[Path]:
    if root.is_file():
        if root.suffix != ".py":
            msg = f"{root} is not a .py file"
            raise SystemExit(msg)
        return [root]
    files = [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and not any(part.startswith(".") for part in p.parts)
    ]
    if not files:
        msg = f"No .py files found under {root}"
        raise SystemExit(msg)
    return sorted(files)


def parse_file(path: Path) -> ast.Module:
    src = path.read_text(encoding="utf-8")
    try:
        return ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        msg = f"Syntax error in {path}: {exc}"
        raise SystemExit(msg)


def parse_merged_file(path: Path) -> list[tuple[str, ast.Module]]:
    text = path.read_text(encoding="utf-8")
    matches = list(MERGED_FILE_HEADER_RE.finditer(text))
    if not matches:
        msg = (
            f"No '# File: <path>' headers found in {path}; "
            "expected a merged file such as:\n"
            "  # File: __init__.py\n"
            "  ...\n"
            "  # File: sub/mod.py\n"
            "  ...\n"
            "Use directory mode instead if you have loose .py files."
        )
        raise SystemExit(msg)
    modules: list[tuple[str, ast.Module]] = []
    for i, m in enumerate(matches):
        header_path = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        if body.startswith("\r\n"):
            body = body[2:]
        elif body.startswith("\n"):
            body = body[1:]
        try:
            tree = ast.parse(body, filename=header_path)
        except SyntaxError as exc:
            msg = f"Syntax error in {path} section '{header_path}': {exc}"
            raise SystemExit(msg)
        modules.append((header_path, tree))
    return modules


def is_relative_import(node: ast.AST) -> bool:
    return isinstance(node, ast.ImportFrom) and bool(node.level)


def is_future_import(node: ast.AST) -> bool:
    return isinstance(node, ast.ImportFrom) and node.module == "__future__"


def is_six_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(a.name == "six" or a.name.startswith("six.") for a in node.names)
    if isinstance(node, ast.ImportFrom):
        m = node.module or ""
        return m == "six" or m.startswith("six.")
    return False


def is_logging_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(a.name == "logging" for a in node.names)
    if isinstance(node, ast.ImportFrom):
        m = node.module or ""
        return m == "logging" or m.startswith("logging.")
    return False


def _is_named_assign(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    )


def _make_assign(name: str, value: ast.expr) -> ast.Assign:
    return ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=value)


def _module_has_import_from_src(module: ast.Module, src_fragment: str) -> bool:
    target = src_fragment.strip()
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            try:
                if ast.unparse(node).strip() == target:
                    return True
            except Exception:
                continue
    return False


def merge_modules(modules: list[tuple[str, ast.Module]]) -> list[ast.stmt]:
    merged: list[ast.stmt] = []
    seen_imports: set[str] = set()
    for _name, tree in modules:
        for node in tree.body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                continue
            if is_future_import(node):
                continue
            if is_six_import(node) or is_logging_import(node):
                continue
            if is_relative_import(node):
                continue
            if _is_named_assign(node, "__all__"):
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                key = ast.dump(node)
                if key in seen_imports:
                    continue
                seen_imports.add(key)
            merged.append(node)
    return merged


SIX_ATTR_MAP: dict[str, str] = {
    "text_type": "str",
    "binary_type": "bytes",
    "class_types": "type",
    "integer_types": "int",
    "string_types": "str",
}
SIX_MOVES_MAP: dict[str, str] = {
    "six.moves.urllib.request": "urllib.request",
    "six.moves.urllib.error": "urllib.error",
    "six.moves.urllib.parse": "urllib.parse",
    "six.moves.urllib.response": "urllib.response",
    "six.moves.urllib": "urllib",
    "six.moves.http_client": "http.client",
    "six.moves.cPickle": "pickle",
    "six.moves.copyreg": "copyreg",
    "six.moves.queue": "queue",
    "six.moves.range": "builtins",
    "six.moves.map": "builtins",
    "six.moves.zip": "builtins",
    "six.moves.input": "builtins",
    "six.moves": "builtins",
}


class SixImportRewriter(ast.NodeTransformer):
    def visit_ImportFrom(self, node: ast.ImportFrom) -> Optional[ast.AST]:
        self.generic_visit(node)
        m = node.module or ""
        if m in SIX_MOVES_MAP:
            mapped = SIX_MOVES_MAP[m]
            if mapped == "builtins":
                return None
            node.module = mapped
            return node
        if m == "six":
            return None
        return node

    def visit_Import(self, node: ast.Import) -> Optional[ast.AST]:
        self.generic_visit(node)
        kept = [a for a in node.names if a.name != "six"]
        if not kept:
            return None
        node.names = kept
        return node


class SixTransformer(ast.NodeTransformer):
    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id == "six":
            if node.attr == "PY2":
                return ast.Constant(value=False)
            if node.attr == "PY3":
                return ast.Constant(value=True)
            if node.attr in SIX_ATTR_MAP:
                return ast.Name(id=SIX_ATTR_MAP[node.attr], ctx=node.ctx)
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "six"):
            return node
        method = func.attr
        if method in ("iteritems", "iterkeys", "itervalues") and len(node.args) == 1:
            mapped = {"iteritems": "items", "iterkeys": "keys", "itervalues": "values"}[method]
            return ast.Call(
                func=ast.Attribute(value=node.args[0], attr=mapped, ctx=ast.Load()),
                args=[],
                keywords=[],
            )
        if method == "u" and len(node.args) == 1:
            return node.args[0]
        if method == "string_types" and not node.args:
            return ast.Name(id="str", ctx=ast.Load())
        return node


OSPATH_PROPERTY_MAP: dict[str, str] = {
    "exists": "exists",
    "isdir": "is_dir",
    "isfile": "is_file",
    "islink": "is_symlink",
    "dirname": "parent",
    "basename": "name",
}
OSPATH_METHOD_MAP: dict[str, str] = {
    "abspath": "resolve",
    "realpath": "resolve",
    "expanduser": "expanduser",
    "remove": "unlink",
    "unlink": "unlink",
    "stat": "stat",
    "mkdir": "mkdir",
}
OSPATH_MAKEDIRS: str = "makedirs"
OSPATH_GETSIZE: str = "getsize"


def _wrap_path(arg: ast.expr) -> ast.Call:
    return ast.Call(func=ast.Name(id="Path", ctx=ast.Load()), args=[arg], keywords=[])


class OsPathTransformer(ast.NodeTransformer):
    def __init__(self, bare_path: bool = False) -> None:
        super().__init__()
        self.bare_path = bare_path

    def _is_ospath(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and node.attr == "path":
                return True
            if self.bare_path and node.value.id == "path":
                return True
        return False

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func = node.func
        if not isinstance(func, ast.Attribute) or not self._is_ospath(func.value):
            return node
        method = func.attr
        args = node.args
        if method == "join" and len(args) >= 1:
            base = _wrap_path(args[0])
            if len(args) == 1:
                return base
            return ast.Call(
                func=ast.Attribute(value=base, attr="joinpath", ctx=ast.Load()),
                args=list(args[1:]),
                keywords=[],
            )
        if method in OSPATH_PROPERTY_MAP and len(args) == 1:
            return ast.Attribute(
                value=_wrap_path(args[0]),
                attr=OSPATH_PROPERTY_MAP[method],
                ctx=ast.Load(),
            )
        if method in OSPATH_METHOD_MAP and len(args) == 1:
            return ast.Call(
                func=ast.Attribute(
                    value=_wrap_path(args[0]),
                    attr=OSPATH_METHOD_MAP[method],
                    ctx=ast.Load(),
                ),
                args=[],
                keywords=[],
            )
        if method == OSPATH_MAKEDIRS and len(args) == 1:
            return ast.Call(
                func=ast.Attribute(value=_wrap_path(args[0]), attr="mkdir", ctx=ast.Load()),
                args=[],
                keywords=[ast.keyword(arg="parents", value=ast.Constant(value=True))],
            )
        if method == OSPATH_GETSIZE and len(args) == 1:
            stat_call = ast.Call(
                func=ast.Attribute(value=_wrap_path(args[0]), attr="stat", ctx=ast.Load()),
                args=[],
                keywords=[],
            )
            return ast.Attribute(value=stat_call, attr="st_size", ctx=ast.Load())
        return node

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        self.generic_visit(node)
        slice_ = node.slice
        if not (isinstance(slice_, ast.Constant) and slice_.value == 1):
            return node
        call = node.value
        if not (isinstance(call, ast.Call) and len(call.args) == 1):
            return node
        if not (
            isinstance(call.func, ast.Attribute) and call.func.attr == "splitext" and self._is_ospath(call.func.value)
        ):
            return node
        return ast.Attribute(
            value=_wrap_path(call.args[0]),
            attr="suffix",
            ctx=ast.Load(),
        )


_EXECUTOR_NAMES: set[str] = {"ProcessPoolExecutor", "ThreadPoolExecutor"}


class _ExecutorMethodRenamer(ast.NodeTransformer):
    def __init__(self, var: str) -> None:
        super().__init__()
        self.var = var

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == self.var
            and f.attr == "map"
        ):
            f.attr = "imap_unordered"
            f.value = ast.Name(id="_pool", ctx=ast.Load())
        return node


def _is_executor_ref(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _EXECUTOR_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in _EXECUTOR_NAMES
    return False


class ExecutorTransformer(ast.NodeTransformer):
    def visit_With(self, node: ast.With) -> ast.AST | list[ast.stmt]:
        self.generic_visit(node)
        if len(node.items) != 1:
            return node
        item = node.items[0]
        ctx = item.context_expr
        if not (isinstance(ctx, ast.Call) and _is_executor_ref(ctx.func)):
            return node
        var = item.optional_vars
        if not isinstance(var, ast.Name):
            return node
        pool_assign = ast.Assign(
            targets=[ast.Name(id="_pool", ctx=ast.Store())],
            value=ast.Call(
                func=ast.Attribute(value=ast.Name(id="mp", ctx=ast.Load()), attr="Pool", ctx=ast.Load()),
                args=[ast.Name(id="MAX_WORKERS", ctx=ast.Load())],
                keywords=[],
            ),
        )
        renamer = _ExecutorMethodRenamer(var.id)
        new_body = [renamer.visit(stmt) for stmt in node.body]
        cleanup: list[ast.stmt] = [
            ast.Expr(
                ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id="_pool", ctx=ast.Load()),
                        attr="close",
                        ctx=ast.Load(),
                    ),
                    args=[],
                    keywords=[],
                )
            ),
            ast.Expr(
                ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id="_pool", ctx=ast.Load()),
                        attr="join",
                        ctx=ast.Load(),
                    ),
                    args=[],
                    keywords=[],
                )
            ),
        ]
        try_node = ast.Try(body=new_body, handlers=[], orelse=[], finalbody=cleanup)
        return [pool_assign, try_node]


class LoggingTransformer(ast.NodeTransformer):
    def visit_Assign(self, node: ast.Assign) -> Optional[ast.AST]:
        self.generic_visit(node)
        v = node.value
        if (
            isinstance(v, ast.Call)
            and isinstance(v.func, ast.Attribute)
            and isinstance(v.func.value, ast.Name)
            and v.func.value.id == "logging"
            and v.func.attr == "getLogger"
        ):
            return None
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id == "logging" and node.attr.isupper():
            return ast.Constant(value=node.attr)
        return node

    def visit_Expr(self, node: ast.Expr) -> ast.AST | list[ast.stmt]:
        self.generic_visit(node)
        call = node.value
        if not isinstance(call, ast.Call):
            return node
        f = call.func
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "logging"
            and f.attr == "basicConfig"
        ):
            fmt = ast.Constant(value=LOGURU_LOGFORMAT)
            for kw in call.keywords:
                if kw.arg == "format":
                    fmt = kw.value
                    break
            return [
                ast.Expr(
                    ast.Call(
                        func=ast.Attribute(
                            value=ast.Name(id="logger", ctx=ast.Load()),
                            attr="remove",
                            ctx=ast.Load(),
                        ),
                        args=[],
                        keywords=[],
                    )
                ),
                ast.Expr(
                    ast.Call(
                        func=ast.Attribute(
                            value=ast.Name(id="logger", ctx=ast.Load()),
                            attr="add",
                            ctx=ast.Load(),
                        ),
                        args=[
                            ast.Attribute(
                                value=ast.Name(id="sys", ctx=ast.Load()),
                                attr="stderr",
                                ctx=ast.Load(),
                            )
                        ],
                        keywords=[ast.keyword(arg="format", value=fmt)],
                    )
                ),
            ]
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "logger"
            and f.attr == "setLevel"
            and call.args
        ):
            return ast.Expr(
                ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id="logger", ctx=ast.Load()),
                        attr="add",
                        ctx=ast.Load(),
                    ),
                    args=[
                        ast.Attribute(
                            value=ast.Name(id="sys", ctx=ast.Load()),
                            attr="stderr",
                            ctx=ast.Load(),
                        )
                    ],
                    keywords=[ast.keyword(arg="level", value=call.args[0])],
                )
            )
        return node


_DEF_NODES: tuple[Any, ...] = (
    ast.Module,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)


def strip_docstrings(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, _DEF_NODES):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]


_STANDARD_HEADER: list[str] = [
    "import sys",
    "import multiprocessing as mp",
    "from pathlib import Path",
    "from loguru import logger",
]


def inject_standard_imports(module: ast.Module) -> ast.Module:
    prefix: list[ast.stmt] = []
    for line in _STANDARD_HEADER:
        if not _module_has_import_from_src(module, line):
            prefix.extend(ast.parse(line).body)
    module.body = prefix + module.body
    return module


def _module_imports_path_bare(module: ast.Module) -> bool:
    for node in module.body:
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for a in node.names:
                if a.name == "path":
                    return True
    return False


def refactor(
    modules: list[tuple[str, ast.Module]],
    output: Path,
    source_label: str,
) -> None:
    merged = merge_modules(modules)
    module = ast.Module(body=merged, type_ignores=[])
    bare_path = _module_imports_path_bare(module)
    for T in (SixImportRewriter, SixTransformer, LoggingTransformer):
        module = T().visit(module)
    module = OsPathTransformer(bare_path=bare_path).visit(module)
    module = ExecutorTransformer().visit(module)
    module.body = [n for n in module.body if not _is_named_assign(n, "MAX_WORKERS")]
    module.body.insert(0, _make_assign("MAX_WORKERS", ast.Constant(value=MAX_WORKERS)))
    module = inject_standard_imports(module)
    strip_docstrings(module)
    ast.fix_missing_locations(module)
    src = ast.unparse(module)
    src = re.sub(r"\n{3,}", "\n\n\n", src)
    try:
        ast.parse(src)
    except SyntaxError as exc:
        check_path = output.with_suffix(".check.py")
        atomic_write(check_path, src + "\n", encoding="utf-8")
        msg = f"Generated code failed to parse: {exc}\nPartial output written to {check_path} for manual fixing."
        raise SystemExit(msg)
    atomic_write(output, src + "\n", encoding="utf-8")
    print(
        f"Refactored {len(modules)} module(s) from {source_label} into {output} "
        f"({len(src)} bytes).\n"
        "Note: PEP 484 annotations were preserved but not inferred; "
        "add missing ones by hand."
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Consolidate a multi-file Python package into one module.")
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help="package directory or .py file (default: current directory)",
    )
    parser.add_argument(
        "-f",
        "--merged-file",
        type=Path,
        default=None,
        help="read source from a single merged file containing '# File: <path>' section headers",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output file (default: <source_name>_single.py in cwd)",
    )
    args = parser.parse_args(argv)
    if args.merged_file is not None and args.input is not None:
        msg = "Provide either a positional INPUT or -f/--merged-file, not both"
        raise SystemExit(msg)
    if args.merged_file is not None:
        merged_path: Path = args.merged_file
        if not merged_path.exists():
            msg = f"{merged_path} does not exist"
            raise SystemExit(msg)
        if not merged_path.is_file():
            msg = f"{merged_path} is not a file"
            raise SystemExit(msg)
        modules = parse_merged_file(merged_path)
        source_label = str(merged_path)
        source_default = merged_path.stem
    else:
        input_path: Path = args.input if args.input is not None else Path()
        if not input_path.exists():
            msg = f"{input_path} does not exist"
            raise SystemExit(msg)
        files = collect_python_files(input_path)
        modules = [(str(p), parse_file(p)) for p in files]
        source_label = str(input_path)
        source_default = input_path.stem if input_path.is_file() else input_path.name
    output: Optional[Path] = args.output
    if output is None:
        output = Path.cwd() / f"{source_default}_single.py"
    if args.merged_file is not None and output.resolve() == args.merged_file.resolve():
        msg = "Refusing to overwrite the input merged file"
        raise SystemExit(msg)
    if args.input is not None and output.resolve() == args.input.resolve():
        msg = "Refusing to overwrite the input path"
        raise SystemExit(msg)
    refactor(modules, output, source_label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
