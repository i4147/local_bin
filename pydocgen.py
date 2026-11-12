#!/data/data/com.termux/files/usr/bin/env python
from __future__ import annotations

import argparse
import ast
import importlib
import inspect
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import count
from multiprocessing import get_context
from pathlib import Path
from typing import Any

DOC_SUFFIXES: frozenset[str] = frozenset({".py", ".pyi", ".pyx", ".pxd"})
SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".eggs",
    ".idea",
    ".vscode",
    "node_modules",
    "build",
    "dist",
})
FENCE: str = "```"
FileTask = tuple[str, str, str, str]
ModuleTask = tuple[str, str, str]


@dataclass
class FunctionInfo:
    name: str
    signature: str
    decorators: tuple[str, ...] = ()
    doc: str = ""


@dataclass
class ClassInfo:
    name: str
    signature: str
    decorators: tuple[str, ...] = ()
    doc: str = ""
    methods: list[FunctionInfo] = field(default_factory=list)


@dataclass
class ModuleInfo:
    name: str = ""
    doc: str = ""
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.doc or self.functions or self.classes)


def _ast_render_arg(arg: ast.arg, default: ast.expr | None) -> str:
    text = arg.arg
    if arg.annotation is not None:
        text += f": {ast.unparse(arg.annotation)}"
    if default is not None:
        text += f" = {ast.unparse(default)}" if arg.annotation is not None else f"={ast.unparse(default)}"
    return text


def _ast_render_args(args: ast.arguments) -> str:
    parts: list[str] = []
    positional = args.posonlyargs + args.args
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for index, arg in enumerate(positional):
        parts.append(_ast_render_arg(arg, defaults[index]))
        if args.posonlyargs and index == len(args.posonlyargs) - 1:
            parts.append("/")
    if args.vararg is not None:
        parts.append(f"*{_ast_render_arg(args.vararg, None)}")
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(_ast_render_arg(arg, default))
    if args.kwarg is not None:
        parts.append(f"**{_ast_render_arg(args.kwarg, None)}")
    return ", ".join(parts)


def _ast_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{keyword} {node.name}({_ast_render_args(node.args)}){returns}"


def _ast_class_signature(node: ast.ClassDef) -> str:
    items = [ast.unparse(base) for base in node.bases]
    items.extend(
        f"{keyword.arg}={ast.unparse(keyword.value)}" if keyword.arg is not None else f"**{ast.unparse(keyword.value)}"
        for keyword in node.keywords
    )
    if not items:
        return f"class {node.name}"
    return f"class {node.name}({', '.join(items)})"


def _ast_collect(
    statements: list[ast.stmt], prefix: str, functions: list[FunctionInfo], classes: list[ClassInfo]
) -> None:
    for node in statements:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = prefix + node.name
            decorators = tuple(ast.unparse(decorator) for decorator in node.decorator_list)
            functions.append(
                FunctionInfo(name, _ast_function_signature(node), decorators, ast.get_docstring(node) or "")
            )
            _ast_collect(node.body, f"{name}.", functions, classes)
        elif isinstance(node, ast.ClassDef):
            name = prefix + node.name
            decorators = tuple(ast.unparse(decorator) for decorator in node.decorator_list)
            info = ClassInfo(name, _ast_class_signature(node), decorators, ast.get_docstring(node) or "")
            classes.append(info)
            _ast_collect(node.body, f"{name}.", info.methods, classes)


def extract_ast(source: str) -> ModuleInfo:
    tree = ast.parse(source)
    info = ModuleInfo(doc=ast.get_docstring(tree) or "")
    _ast_collect(tree.body, "", info.functions, info.classes)
    return info


def _libcst_code(module: Any, node: Any) -> str:
    text: str = module.code_for_node(node).strip().rstrip(",").strip()
    return text


def _libcst_suite_statements(body: Any) -> list[Any]:
    import libcst as cst

    if isinstance(body, (cst.IndentedBlock, cst.SimpleStatementSuite)):
        return list(body.body)
    return []


def _libcst_parameters(module: Any, params: Any) -> str:
    import libcst as cst

    parts: list[str] = []
    posonly = list(params.posonly_params)
    parts.extend(_libcst_code(module, item) for item in posonly)
    if posonly:
        parts.append("/")
    parts.extend(_libcst_code(module, item) for item in params.params)
    star_arg = params.star_arg
    if isinstance(star_arg, cst.Param):
        parts.append(_libcst_code(module, star_arg))
    elif isinstance(star_arg, cst.ParamStar) and params.kwonly_params:
        parts.append("*")
    parts.extend(_libcst_code(module, item) for item in params.kwonly_params)
    if params.star_kwarg is not None:
        parts.append(_libcst_code(module, params.star_kwarg))
    return ", ".join(parts)


def _libcst_function_signature(module: Any, node: Any) -> str:
    keyword = "async def" if node.asynchronous is not None else "def"
    returns = f" -> {_libcst_code(module, node.returns.annotation)}" if node.returns is not None else ""
    return f"{keyword} {node.name.value}({_libcst_parameters(module, node.params)}){returns}"


def _libcst_class_signature(module: Any, node: Any) -> str:
    items = [_libcst_code(module, base) for base in node.bases]
    items.extend(_libcst_code(module, keyword) for keyword in node.keywords)
    if not items:
        return f"class {node.name.value}"
    return f"class {node.name.value}({', '.join(items)})"


def _libcst_decorators(module: Any, node: Any) -> tuple[str, ...]:
    return tuple(_libcst_code(module, decorator.decorator) for decorator in node.decorators)


def _libcst_collect(
    statements: list[Any], prefix: str, functions: list[FunctionInfo], classes: list[ClassInfo], module: Any
) -> None:
    import libcst as cst

    for node in statements:
        if isinstance(node, cst.FunctionDef):
            name = prefix + node.name.value
            functions.append(
                FunctionInfo(
                    name,
                    _libcst_function_signature(module, node),
                    _libcst_decorators(module, node),
                    node.get_docstring() or "",
                )
            )
            _libcst_collect(_libcst_suite_statements(node.body), f"{name}.", functions, classes, module)
        elif isinstance(node, cst.ClassDef):
            name = prefix + node.name.value
            info = ClassInfo(
                name,
                _libcst_class_signature(module, node),
                _libcst_decorators(module, node),
                node.get_docstring() or "",
            )
            classes.append(info)
            _libcst_collect(_libcst_suite_statements(node.body), f"{name}.", info.methods, classes, module)


def extract_libcst(source: str) -> ModuleInfo:
    import libcst as cst

    module = cst.parse_module(source)
    info = ModuleInfo(doc=module.get_docstring() or "")
    _libcst_collect(list(module.body), "", info.functions, info.classes, module)
    return info


_TS_PARSER: Any = None


def _ts_parser() -> Any:
    global _TS_PARSER
    if _TS_PARSER is None:
        import tree_sitter_python as tspy
        from tree_sitter import Language, Parser

        language = Language(tspy.language())
        try:
            _TS_PARSER = Parser(language)
        except TypeError:
            _TS_PARSER = Parser()
            _TS_PARSER.set_language(language)
    return _TS_PARSER


def _ts_text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _ts_signature(node: Any, source: bytes) -> str:
    end: int | None = None
    for child in node.children:
        if child.type == ":":
            end = child.start_byte
    if end is None:
        body = node.child_by_field_name("body")
        end = body.start_byte if body is not None else node.end_byte
    return source[node.start_byte : end].decode("utf-8", errors="replace").rstrip()


def _ts_string_value(raw: str) -> str:
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        stripped = raw.strip()
        for quote in ('"""', "'''", '"', "'"):
            if len(stripped) >= 2 * len(quote) and stripped.startswith(quote) and stripped.endswith(quote):
                return inspect.cleandoc(stripped[len(quote) : -len(quote)])
        return ""
    if isinstance(value, str):
        return inspect.cleandoc(value)
    return ""


def _ts_docstring(body: Any, source: bytes) -> str:
    if body is None:
        return ""
    for statement in body.named_children:
        if statement.type == "comment":
            continue
        if statement.type != "expression_statement" or not statement.named_children:
            return ""
        value = statement.named_children[0]
        if value.type != "string":
            return ""
        return _ts_string_value(_ts_text(value, source))
    return ""


def _ts_decorators(node: Any, source: bytes) -> tuple[str, ...]:
    texts: list[str] = []
    for child in node.children:
        if child.type == "decorator":
            text = _ts_text(child, source)
            texts.append(text[1:] if text.startswith("@") else text)
    return tuple(texts)


def _ts_function(
    node: Any,
    source: bytes,
    prefix: str,
    decorators: tuple[str, ...],
    functions: list[FunctionInfo],
    classes: list[ClassInfo],
) -> None:
    body = node.child_by_field_name("body")
    name_node = node.child_by_field_name("name")
    if body is None or name_node is None:
        return
    name = prefix + _ts_text(name_node, source)
    functions.append(FunctionInfo(name, _ts_signature(node, source), decorators, _ts_docstring(body, source)))
    _ts_collect(body.named_children, source, f"{name}.", functions, classes)


def _ts_class(node: Any, source: bytes, prefix: str, decorators: tuple[str, ...], classes: list[ClassInfo]) -> None:
    body = node.child_by_field_name("body")
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = prefix + _ts_text(name_node, source)
    signature = _ts_signature(node, source) if body is not None else _ts_text(node, source).rstrip()
    info = ClassInfo(name, signature, decorators, _ts_docstring(body, source) if body is not None else "")
    classes.append(info)
    if body is not None:
        _ts_collect(body.named_children, source, f"{name}.", info.methods, classes)


def _ts_collect(
    statements: Sequence[Any], source: bytes, prefix: str, functions: list[FunctionInfo], classes: list[ClassInfo]
) -> None:
    for node in statements:
        if node.has_error:
            continue
        node_type = node.type
        if node_type == "decorated_definition":
            decorators = _ts_decorators(node, source)
            inner = node.child_by_field_name("definition")
            if inner is None:
                continue
            if inner.type == "function_definition":
                _ts_function(inner, source, prefix, decorators, functions, classes)
            elif inner.type == "class_definition":
                _ts_class(inner, source, prefix, decorators, classes)
        elif node_type == "function_definition":
            _ts_function(node, source, prefix, (), functions, classes)
        elif node_type == "class_definition":
            _ts_class(node, source, prefix, (), classes)


def extract_tree_sitter(source: str) -> ModuleInfo:
    encoded = source.encode("utf-8")
    root = _ts_parser().parse(encoded).root_node
    info = ModuleInfo(doc=_ts_docstring(root, encoded))
    _ts_collect(root.named_children, encoded, "", info.functions, info.classes)
    return info


BACKENDS: dict[str, Callable[[str], ModuleInfo]] = {
    "libcst": extract_libcst,
    "tree-sitter": extract_tree_sitter,
    "ast": extract_ast,
}


def _code_block(lines: Sequence[str]) -> list[str]:
    return [FENCE + "python", *lines, FENCE]


def _signature_lines(decorators: tuple[str, ...], signature: str) -> list[str]:
    lines = [f"@{decorator}" for decorator in decorators]
    lines.append(f"{signature}: ...")
    return lines


def render_markdown(info: ModuleInfo) -> str:
    lines: list[str] = [f"# `{info.name}`", ""]
    if info.doc:
        lines.extend(info.doc.splitlines())
        lines.append("")
    if info.functions:
        lines.extend(("## Functions", ""))
        for function in info.functions:
            lines.extend((f"### `{function.name}()`", ""))
            lines.extend(_code_block(_signature_lines(function.decorators, function.signature)))
            lines.append("")
            if function.doc:
                lines.extend(function.doc.splitlines())
            lines.append("")
    if info.classes:
        lines.extend(("## Classes", ""))
        for klass in info.classes:
            lines.extend((f"### `{klass.name}`", ""))
            lines.extend(_code_block(_signature_lines(klass.decorators, klass.signature)))
            lines.append("")
            if klass.doc:
                lines.extend(klass.doc.splitlines())
            lines.append("")
            for method in klass.methods:
                lines.extend((f"#### `{method.name}()`", ""))
                lines.extend(_code_block(_signature_lines(method.decorators, method.signature)))
                lines.append("")
                if method.doc:
                    lines.extend(method.doc.splitlines())
                lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    collapsed: list[str] = []
    for line in lines:
        if not line and (not collapsed or collapsed[-1] == ""):
            continue
        collapsed.append(line)
    return "\n".join(collapsed) + "\n"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in count(1):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise AssertionError


def save_markdown(out_dir: Path, parts: Sequence[str], content: str) -> Path:
    target = out_dir.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = unique_path(target)
    target.write_text(content, encoding="utf-8")
    return target


def module_display_name(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return path.stem
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return root.resolve().name or path.stem
    return ".".join(parts)


def markdown_parts(path: Path, root: Path) -> list[str]:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return [f"{path.stem}.md"]
    return list(relative.with_suffix(".md").parts)


def walk_python_files(directory: Path, out_dir: Path) -> Iterator[Path]:
    stack: list[Path] = [directory]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                if child.name in SKIP_DIRS or child.name.startswith("."):
                    continue
                try:
                    if child.resolve() == out_dir:
                        continue
                except OSError:
                    pass
                stack.append(child)
            elif child.suffix in DOC_SUFFIXES:
                yield child


def expand_files(file_roots: Sequence[tuple[Path, Path]], out_dir: Path) -> list[tuple[Path, Path]]:
    seen: set[Path] = set()
    files: list[tuple[Path, Path]] = []
    for path, root in file_roots:
        candidates: Iterator[Path] = walk_python_files(path, out_dir) if path.is_dir() else iter((path,))
        for candidate in candidates:
            key = candidate.resolve()
            if key in seen:
                continue
            seen.add(key)
            files.append((candidate, root))
    files.sort(key=lambda item: str(item[0]))
    return files


def classify_targets(targets: Sequence[str]) -> tuple[list[tuple[Path, Path]], list[str]]:
    file_roots: list[tuple[Path, Path]] = []
    module_names: list[str] = []
    for target in targets:
        candidate = Path(target)
        if candidate.exists():
            file_roots.append((candidate, candidate if candidate.is_dir() else candidate.parent))
        elif target and all(part.isidentifier() for part in target.split(".")):
            module_names.append(target)
        else:
            print(f"skipped {target}: not an existing path or importable module name")
    return file_roots, module_names


def process_file_task(task: FileTask) -> bool:
    file_str, root_str, backend_name, out_dir_str = task
    path = Path(file_str)
    root = Path(root_str)
    out_dir = Path(out_dir_str)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"skipped {path}: {exc}")
        return False
    try:
        info = BACKENDS[backend_name](source)
    except Exception as exc:
        print(f"skipped {path}: {exc}")
        return False
    if info.is_empty():
        return False
    info.name = module_display_name(path, root)
    output = save_markdown(out_dir, markdown_parts(path, root), render_markdown(info))
    print(f"wrote {output}")
    return True


def process_module_task(task: ModuleTask) -> bool:
    module_name, backend_name, out_dir_str = task
    print(f"processing module {module_name}")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        print(f"skipped module {module_name}: {exc}")
        return False
    info: ModuleInfo | None = None
    try:
        info = BACKENDS[backend_name](inspect.getsource(module))
    except Exception:
        info = None
    if info is None:
        info = ModuleInfo(name=module_name, doc=inspect.getdoc(module) or "")
    if info.is_empty():
        return False
    info.name = module_name
    parts = module_name.split(".")
    output = save_markdown(Path(out_dir_str), [*parts[:-1], f"{parts[-1]}.md"], render_markdown(info))
    print(f"wrote {output}")
    return True


def resolve_backend(name: str) -> str:
    if name == "libcst":
        try:
            import libcst
        except ImportError:
            print("warning: libcst is not installed, falling back to the ast backend")
            return "ast"
    elif name == "tree-sitter":
        try:
            import tree_sitter
            import tree_sitter_python
        except ImportError:
            print("warning: tree-sitter is not installed, falling back to the ast backend")
            return "ast"
    return name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate readable markdown documentation from Python sources (libcst, tree-sitter or ast backend)."
    )
    parser.add_argument(
        "targets", nargs="*", help="files, directories or dotted module names (default: current directory)"
    )
    parser.add_argument(
        "-b", "--backend", choices=tuple(BACKENDS), default="libcst", help="parsing backend (default: %(default)s)"
    )
    parser.add_argument("-o", "--out", default=None, help="output directory (default: ./<cwd-name>_doc)")
    parser.add_argument("-j", "--workers", type=int, default=8, help="worker processes (default: %(default)s)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    backend_name = resolve_backend(args.backend)
    cwd = Path.cwd()
    out_dir = (Path(args.out) if args.out else Path(f"{cwd.name}_doc")).resolve()
    file_roots, module_names = classify_targets(args.targets or [str(cwd)])
    files = expand_files(file_roots, out_dir)
    file_tasks: list[FileTask] = [(str(path), str(root), backend_name, str(out_dir)) for path, root in files]
    module_tasks: list[ModuleTask] = [(name, backend_name, str(out_dir)) for name in module_names]
    total = len(file_tasks) + len(module_tasks)
    if total == 0:
        print("nothing to process")
        return 0
    workers = max(1, min(args.workers, total))
    print(f"processing {total} target(s) with backend {backend_name} using {workers} worker(s)")
    documented: int
    if workers == 1:
        documented = sum(process_file_task(task) for task in file_tasks)
        documented += sum(process_module_task(task) for task in module_tasks)
    else:
        with get_context("spawn").Pool(workers) as pool:
            documented = sum(pool.imap_unordered(process_file_task, file_tasks, chunksize=1)) if file_tasks else 0
            documented += (
                sum(pool.imap_unordered(process_module_task, module_tasks, chunksize=1)) if module_tasks else 0
            )
    print(f"documented {documented}/{total} target(s) into {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
