#!/data/data/com.termux/files/usr/bin/env python
import argparse
import ast
import re
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn, Optional, Union

from loguru import logger
from dh import MAX_WORKERS

LOGURU_LOGFORMAT: str = "{time:YYYY-MM-DD HH:mm:ss.SSS} {level} {file.name}:{line} {message}"
MERGED_FILE_HEADER_RE: re.Pattern[str] = re.compile(r"^#\s*File:\s*(.+?)\s*$", re.MULTILINE)
STDLIB_MODULES: frozenset[str] = frozenset(getattr(sys, "stdlib_module_names", ()))
ModuleKey = tuple[str, ...]
Renames = dict[tuple[ModuleKey, str], str]
FuncNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]
FUNC_NODES: tuple[type, ...] = (ast.FunctionDef, ast.AsyncFunctionDef)
DEFINITION_NODES: tuple[type, ...] = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_DEF_NODES: tuple[type, ...] = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_EXECUTOR_NAMES: set[str] = {"ProcessPoolExecutor", "ThreadPoolExecutor"}
_STANDARD_HEADER: list[str] = [
    "import sys",
    "import multiprocessing as mp",
    "from pathlib import Path",
    "from loguru import logger",
]
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


@dataclass
class ModuleInfo:
    key: ModuleKey
    label: str
    package: ModuleKey
    tree: ast.Module
    names: dict[str, tuple[ModuleKey, str]] = field(default_factory=dict)
    modules: dict[str, ModuleKey] = field(default_factory=dict)
    defined: set[str] = field(default_factory=set)
    external: set[str] = field(default_factory=set)


class LoadNames(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.add(node.id)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.scan_defaults(node.args)

    def visit_FunctionDef(self, node: FuncNode) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.scan_defaults(node.args)
        args = node.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg is not None and arg.annotation is not None:
                self.visit(arg.annotation)
        if node.returns is not None:
            self.visit(node.returns)

    visit_AsyncFunctionDef = visit_FunctionDef

    def scan_defaults(self, args: ast.arguments) -> None:
        for default in [*args.defaults, *args.kw_defaults]:
            if default is not None:
                self.visit(default)


class LocalImportResolver(ast.NodeTransformer):
    def __init__(self, info: ModuleInfo, index: dict[ModuleKey, ModuleInfo], root_name: str) -> None:
        super().__init__()
        self.info = info
        self.index = index
        self.root_name = root_name

    def locate(self, parts: ModuleKey, relative: bool) -> Optional[ModuleKey]:
        if parts in self.index:
            return parts
        if not relative and self.root_name and parts[:1] == (self.root_name,) and parts[1:] in self.index:
            return parts[1:]
        return None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Optional[ast.AST]:
        tail: ModuleKey = tuple(node.module.split(".")) if node.module else ()
        relative = bool(node.level)
        if relative:
            package = self.info.package
            cut = node.level - 1
            if cut > len(package):
                logger.warning("{}: relative import beyond top level: {}", self.info.label, ast.unparse(node))
                return None
            base: ModuleKey = package[: len(package) - cut] + tail
        else:
            base = tail
        target = self.locate(base, relative)
        subs = {alias.name: self.locate(base + (alias.name,), relative) for alias in node.names}
        if not relative and target is None and all(value is None for value in subs.values()):
            return node
        for alias in node.names:
            local = alias.asname or alias.name
            sub = subs[alias.name]
            if alias.name == "*":
                logger.warning("{}: star import from local module dropped: {}", self.info.label, ast.unparse(node))
            elif sub is not None:
                self.info.modules[local] = sub
            elif target is not None:
                self.info.names[local] = (target, alias.name)
            else:
                logger.warning("{}: unresolved local import {}", self.info.label, ast.unparse(node))
        return None

    def visit_Import(self, node: ast.Import) -> Optional[ast.AST]:
        kept: list[ast.alias] = []
        for alias in node.names:
            parts: ModuleKey = tuple(alias.name.split("."))
            target = self.locate(parts, False)
            if target is None:
                kept.append(alias)
            elif alias.asname:
                self.info.modules[alias.asname] = target
            elif len(parts) == 1:
                self.info.modules[alias.name] = target
            else:
                logger.warning("{}: unsupported dotted local import kept: {}", self.info.label, alias.name)
                kept.append(alias)
        if not kept:
            return None
        node.names = kept
        return node


class NameRewriter(ast.NodeTransformer):
    def __init__(
        self,
        info: ModuleInfo,
        infos: dict[ModuleKey, ModuleInfo],
        final: Renames,
        rewrite: dict[str, str],
    ) -> None:
        super().__init__()
        self.info = info
        self.infos = infos
        self.final = final
        self.rewrite = rewrite

    def visit_Name(self, node: ast.Name) -> ast.AST:
        new = self.rewrite.get(node.id)
        if new is not None:
            node.id = new
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        value = node.value
        if isinstance(value, ast.Name) and value.id in self.info.modules and isinstance(node.ctx, ast.Load):
            resolved = resolve_name(self.infos, self.info.modules[value.id], node.attr, self.final)
            if resolved is not None:
                return ast.copy_location(ast.Name(id=resolved, ctx=ast.Load()), node)
            logger.warning("{}: cannot resolve {}.{}", self.info.label, value.id, node.attr)
        self.generic_visit(node)
        return node


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
            fmt: ast.expr = ast.Constant(value=LOGURU_LOGFORMAT)
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


def fail(msg: str) -> NoReturn:
    logger.error(msg)
    raise SystemExit(msg)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def parse_source(src: str, filename: str) -> ast.Module:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            tree = ast.parse(src, filename=filename)
        except SyntaxError as exc:
            fail(f"Syntax error in {filename}: {exc}")
    for item in caught:
        logger.warning("{}:{} {}", filename, item.lineno, item.message)
    return tree


def make_info(rel: Path, tree: ast.Module) -> ModuleInfo:
    parts = tuple(p for p in rel.with_suffix("").parts if p not in (".", "..", "/", "\\"))
    is_init = bool(parts) and parts[-1] == "__init__"
    key: ModuleKey = parts[:-1] if is_init else parts
    package: ModuleKey = key if is_init else key[:-1]
    return ModuleInfo(key=key, label=rel.as_posix(), package=package, tree=tree)


def collect_python_files(root: Path) -> list[Path]:
    if root.is_file():
        if root.suffix != ".py":
            fail(f"{root} is not a .py file")
        return [root]
    files = [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and not any(part.startswith(".") for part in p.relative_to(root).parts)
    ]
    if not files:
        fail(f"No .py files found under {root}")
    return sorted(files)


def load_directory(root: Path) -> list[ModuleInfo]:
    base = root if root.is_dir() else root.parent
    infos: list[ModuleInfo] = []
    for path in collect_python_files(root):
        tree = parse_source(path.read_text(encoding="utf-8"), str(path))
        infos.append(make_info(path.relative_to(base), tree))
    return infos


def load_merged(path: Path) -> list[ModuleInfo]:
    text = path.read_text(encoding="utf-8")
    matches = list(MERGED_FILE_HEADER_RE.finditer(text))
    if not matches:
        fail(f"No '# File: <path>' headers found in {path}; use directory mode for loose .py files.")
    infos: list[ModuleInfo] = []
    for i, m in enumerate(matches):
        header = m.group(1)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end]
        if body.startswith("\r\n"):
            body = body[2:]
        elif body.startswith("\n"):
            body = body[1:]
        infos.append(make_info(Path(header), parse_source(body, f"{path}:{header}")))
    return infos


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


def is_string_expr(node: ast.AST) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _is_named_assign(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    )


def _make_assign(name: str, value: ast.expr) -> ast.Assign:
    return ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=value)


def _wrap_path(arg: ast.expr) -> ast.Call:
    return ast.Call(func=ast.Name(id="Path", ctx=ast.Load()), args=[arg], keywords=[])


def _is_executor_ref(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _EXECUTOR_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in _EXECUTOR_NAMES
    return False


def _module_imports_path_bare(module: ast.Module) -> bool:
    for node in module.body:
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for a in node.names:
                if a.name == "path":
                    return True
    return False


def strip_docstrings(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, _DEF_NODES):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if is_string_expr(first):
                node.body = body[1:] or [ast.Pass()]


def inject_standard_imports(module: ast.Module) -> ast.Module:
    prefix: list[ast.stmt] = []
    for line in _STANDARD_HEADER:
        prefix.extend(ast.parse(line).body)
    module.body = prefix + module.body
    return module


def fix_empty_bodies(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Module):
            continue
        body = getattr(node, "body", None)
        if isinstance(body, list) and not body:
            body.append(ast.Pass())


def defined_names(node: ast.stmt) -> list[str]:
    if isinstance(node, DEFINITION_NODES):
        return [node.name]
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            names.append(child.id)
    return names


def top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        names.update(defined_names(node))
    return names


def bound_import_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def unique_name(base: str, taken: set[str]) -> str:
    candidate = base
    counter = 2
    while candidate in taken:
        candidate = f"{base}_{counter}"
        counter += 1
    taken.add(candidate)
    return candidate


def module_prefix(info: ModuleInfo) -> str:
    raw = info.key[-1] if info.key else "root"
    return re.sub(r"\W+", "_", raw).strip("_") or "mod"


def plan_renames(modules: list[ModuleInfo]) -> Renames:
    groups: dict[str, list[tuple[ModuleInfo, ast.stmt]]] = {}
    taken: set[str] = set()
    for info in modules:
        taken |= info.defined | info.external
        for node in info.tree.body:
            if isinstance(node, DEFINITION_NODES):
                groups.setdefault(node.name, []).append((info, node))
    final: Renames = {}
    for name, entries in groups.items():
        first_info, first_node = entries[0]
        first_dump = ast.dump(first_node)
        for info, node in entries[1:]:
            if info is first_info or ast.dump(node) == first_dump or (info.key, name) in final:
                continue
            base = f"{module_prefix(info)}_{name.lstrip('_')}"
            if name.startswith("_"):
                base = f"_{base}"
            new_name = unique_name(base, taken)
            final[(info.key, name)] = new_name
            logger.info("Renamed {}: {} -> {}", info.label, name, new_name)
    return final


def resolve_name(
    infos: dict[ModuleKey, ModuleInfo],
    key: ModuleKey,
    name: str,
    final: Renames,
    seen: Optional[set[tuple[ModuleKey, str]]] = None,
) -> Optional[str]:
    info = infos.get(key)
    if info is None:
        return None
    ident = (key, name)
    if ident in final:
        return final[ident]
    if name in info.defined or name in info.external:
        return name
    visited = seen if seen is not None else set()
    if ident in visited:
        return None
    visited.add(ident)
    target = info.names.get(name)
    if target is None:
        return None
    return resolve_name(infos, target[0], target[1], final, visited)


def apply_renames(info: ModuleInfo, infos: dict[ModuleKey, ModuleInfo], final: Renames) -> None:
    rewrite: dict[str, str] = {}
    for node in info.tree.body:
        if isinstance(node, DEFINITION_NODES):
            new = final.get((info.key, node.name))
            if new is not None:
                rewrite[node.name] = new
                node.name = new
    for alias, (target_key, target_name) in info.names.items():
        resolved = resolve_name(infos, target_key, target_name, final)
        if resolved is None:
            logger.warning("{}: cannot resolve imported name {} ({})", info.label, alias, target_name)
        elif resolved != alias:
            rewrite[alias] = resolved
    if rewrite or info.modules:
        info.tree = apply_transformer(NameRewriter(info, infos, final, rewrite), info.tree)


def apply_transformer(transformer: ast.NodeTransformer, module: ast.Module) -> ast.Module:
    result = transformer.visit(module)
    if not isinstance(result, ast.Module):
        fail(f"{type(transformer).__name__} did not return a module")
    return result


def merge_modules(modules: list[ModuleInfo]) -> list[ast.stmt]:
    merged: list[ast.stmt] = []
    for info in modules:
        for node in info.tree.body:
            if (
                is_string_expr(node)
                or is_future_import(node)
                or is_six_import(node)
                or is_logging_import(node)
                or _is_named_assign(node, "__all__")
            ):
                continue
            merged.append(node)
    return merged


def dependencies(node: ast.stmt) -> set[str]:
    visitor = LoadNames()
    visitor.visit(node)
    return visitor.names


def constant_name(node: ast.stmt) -> Optional[str]:
    if isinstance(node, ast.Assign):
        if node.targets and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
            first = node.targets[0]
            if isinstance(first, ast.Name):
                return first.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id.isupper():
        return node.target.id
    return None


def is_main_guard(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    )


def is_import_block(node: ast.stmt) -> bool:
    if isinstance(node, ast.Try):
        return bool(node.body) and all(isinstance(n, (ast.Import, ast.ImportFrom)) for n in node.body)
    if isinstance(node, ast.If):
        return (
            bool(node.body)
            and not node.orelse
            and all(isinstance(n, (ast.Import, ast.ImportFrom)) for n in node.body)
            and "TYPE_CHECKING" in ast.dump(node.test)
        )
    return False


def is_stdlib(module: str) -> bool:
    return module.split(".")[0] in STDLIB_MODULES


def unique_nodes(body: list[ast.stmt]) -> list[ast.stmt]:
    seen: set[str] = set()
    result: list[ast.stmt] = []
    for node in body:
        signature = ast.dump(node)
        if signature in seen:
            continue
        seen.add(signature)
        result.append(node)
    return result


def merge_imports(nodes: list[ast.stmt]) -> list[list[ast.stmt]]:
    plain: dict[str, set[Optional[str]]] = {}
    froms: dict[str, set[tuple[str, Optional[str]]]] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                plain.setdefault(alias.name, set()).add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            froms.setdefault(node.module or "", set()).update((a.name, a.asname) for a in node.names)
    groups: list[list[ast.stmt]] = []
    for stdlib in (True, False):
        group: list[ast.stmt] = []
        for name in sorted(plain):
            if is_stdlib(name) is stdlib:
                for asname in sorted(plain[name], key=lambda value: value or ""):
                    group.append(ast.Import(names=[ast.alias(name=name, asname=asname)]))
        for module in sorted(froms):
            if is_stdlib(module) is stdlib:
                entries = froms[module]
                if any(entry_name == "*" for entry_name, _ in entries):
                    aliases = [ast.alias(name="*")]
                else:
                    aliases = [
                        ast.alias(name=entry_name, asname=entry_as)
                        for entry_name, entry_as in sorted(entries, key=lambda item: (item[0], item[1] or ""))
                    ]
                group.append(ast.ImportFrom(module=module, names=aliases, level=0))
        if group:
            groups.append(group)
    return groups


def order_by_dependencies(items: list[ast.stmt]) -> list[ast.stmt]:
    owners: dict[str, ast.stmt] = {}
    for item in items:
        for name in defined_names(item):
            owners.setdefault(name, item)
    deps = {id(item): dependencies(item) for item in items}
    done: set[int] = set()
    active: set[int] = set()
    ordered: list[ast.stmt] = []

    def emit(item: ast.stmt) -> None:
        if id(item) in done or id(item) in active:
            return
        active.add(id(item))
        for name in sorted(deps[id(item)]):
            owner = owners.get(name)
            if owner is not None and owner is not item:
                emit(owner)
        active.discard(id(item))
        done.add(id(item))
        ordered.append(item)

    for item in items:
        emit(item)
    return ordered


def render_module(body: list[ast.stmt]) -> str:
    unique = unique_nodes(body)
    import_nodes = [n for n in unique if isinstance(n, (ast.Import, ast.ImportFrom))]
    import_blocks = [n for n in unique if is_import_block(n)]
    guards = [n for n in unique if is_main_guard(n)]
    skipped = {id(n) for n in (*import_nodes, *import_blocks, *guards)}
    rest = [n for n in unique if id(n) not in skipped]
    classes = [n for n in rest if isinstance(n, ast.ClassDef)]
    funcs = [n for n in rest if isinstance(n, FUNC_NODES)]
    simple = [n for n in rest if not isinstance(n, DEFINITION_NODES)]
    callables = {n.name for n in (*classes, *funcs)}
    late_ids = {id(n) for n in simple if dependencies(n) & callables}
    late = [n for n in simple if id(n) in late_ids]
    early = [n for n in simple if id(n) not in late_ids]
    early_consts = sorted(
        (n for n in early if constant_name(n) is not None),
        key=lambda n: constant_name(n) or "",
    )
    early_others = [n for n in early if constant_name(n) is None]
    classes.sort(key=lambda n: n.name)
    funcs.sort(key=lambda n: n.name)
    ordered = order_by_dependencies([*early_others, *early_consts, *classes, *funcs, *late])
    header_parts = ["\n".join(ast.unparse(n) for n in group) for group in merge_imports(import_nodes)]
    header_parts.extend(ast.unparse(n) for n in import_blocks)
    chunks: list[tuple[str, str]] = []
    if header_parts:
        chunks.append(("header", "\n\n".join(header_parts)))
    for node in [*ordered, *guards]:
        kind = "def" if isinstance(node, DEFINITION_NODES) or is_main_guard(node) else "stmt"
        chunks.append((kind, ast.unparse(node)))
    out = ""
    previous: Optional[str] = None
    for kind, text in chunks:
        if previous is None:
            separator = ""
        elif "def" in (previous, kind):
            separator = "\n\n\n"
        elif previous == "header":
            separator = "\n\n"
        else:
            separator = "\n"
        out += separator + text
        previous = kind
    return out + "\n"


def refactor(modules: list[ModuleInfo], output: Path, root_name: str) -> None:
    started = time.perf_counter()
    logger.info("Merging {} module(s) into {}", len(modules), output)
    index: dict[ModuleKey, ModuleInfo] = {info.key: info for info in modules}
    for info in modules:
        logger.debug("Module {} -> {}", info.label, ".".join(info.key) or "<root>")
        info.tree = apply_transformer(LocalImportResolver(info, index, root_name), info.tree)
        fix_empty_bodies(info.tree)
        info.defined = top_level_names(info.tree)
        info.external = bound_import_names(info.tree)
    final = plan_renames(modules)
    for info in modules:
        apply_renames(info, index, final)
    module = ast.Module(body=merge_modules(modules), type_ignores=[])
    bare_path = _module_imports_path_bare(module)
    for transformer in (SixImportRewriter(), SixTransformer(), LoggingTransformer()):
        module = apply_transformer(transformer, module)
    module = apply_transformer(OsPathTransformer(bare_path=bare_path), module)
    module = apply_transformer(ExecutorTransformer(), module)
    module.body = [n for n in module.body if not _is_named_assign(n, "MAX_WORKERS")]
    module.body.insert(0, _make_assign("MAX_WORKERS", ast.Constant(value=MAX_WORKERS)))
    module = inject_standard_imports(module)
    strip_docstrings(module)
    ast.fix_missing_locations(module)
    src = render_module(module.body)
    try:
        ast.parse(src)
    except SyntaxError as exc:
        check_path = unique_path(output.with_suffix(".check.py"))
        check_path.write_text(src, encoding="utf-8")
        fail(f"Generated code failed to parse: {exc}. Partial output written to {check_path}")
    try:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(src)
    except FileExistsError:
        fail(f"Refusing to overwrite existing file {output}")
    logger.success(
        "Wrote {} ({} bytes) in {:.2f}s",
        output,
        output.stat().st_size,
        time.perf_counter() - started,
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
        help="output file (default: <current folder name>.py in cwd; never overwrites, adds _1, _2, ...)",
    )
    parser.add_argument(
        "-l",
        "--log-file",
        type=Path,
        default="/data/data/com.termux/files/home/tmp/apps/consolidate.log",
        help="loguru log file (default: next to the output file with .log suffix)",
    )
    args = parser.parse_args(argv)
    if args.merged_file is not None and args.input is not None:
        parser.error("Provide either a positional INPUT or -f/--merged-file, not both")
    cwd = Path.cwd()
    requested: Path = args.output if args.output is not None else cwd / f"{cwd.name or 'merged'}.py"
    output = unique_path(requested)
    log_path: Path = args.log_file if args.log_file is not None else output.with_suffix(".log")
    logger.remove()
    logger.add(log_path, format=LOGURU_LOGFORMAT, level="DEBUG", encoding="utf-8")
    if output != requested:
        logger.info("{} already exists; using {}", requested, output)
    root_name = ""
    if args.merged_file is not None:
        merged_path: Path = args.merged_file
        if not merged_path.exists():
            fail(f"{merged_path} does not exist")
        if not merged_path.is_file():
            fail(f"{merged_path} is not a file")
        modules = load_merged(merged_path)
    else:
        input_path: Path = args.input if args.input is not None else Path()
        if not input_path.exists():
            fail(f"{input_path} does not exist")
        modules = load_directory(input_path)
        if input_path.is_dir():
            root_name = input_path.resolve().name
    refactor(modules, output, root_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
