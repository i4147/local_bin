#!/usr/bin/env python3
"""Create documentation-enriched copies of root Python scripts.

Only docstrings and statically certain annotations are added. Original files
are never modified. Ambiguous declarations are listed for human review rather
than assigned invented parameter or return types.

Usage:
    python3 arena/document_sources.py [file.py ...]
    python3 arena/document_sources.py --all
"""

import argparse
import ast
from pathlib import Path
import re

import libcst as cst


ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "documented"

# Name prefixes whose ordinary meaning gives a conservative summary.
VERBS = {
    "get": "Return",
    "is": "Return whether",
    "has": "Return whether",
    "can": "Return whether",
    "check": "Check",
    "validate": "Validate",
    "parse": "Parse",
    "load": "Load",
    "read": "Read",
    "write": "Write",
    "save": "Save",
    "build": "Build",
    "make": "Create",
    "create": "Create",
    "convert": "Convert",
    "extract": "Extract",
    "find": "Find",
    "search": "Search for",
    "normalize": "Normalize",
    "format": "Format",
    "render": "Render",
    "run": "Run",
    "iter": "Yield",
    "walk": "Walk",
    "collect": "Collect",
    "discover": "Discover",
    "detect": "Detect",
    "infer": "Infer",
    "resolve": "Resolve",
    "select": "Select",
    "choose": "Choose",
    "ensure": "Ensure",
    "prepare": "Prepare",
    "apply": "Apply",
    "remove": "Remove",
    "delete": "Delete",
    "clean": "Clean",
    "clear": "Clear",
    "compare": "Compare",
    "match": "Match",
    "sort": "Sort",
    "group": "Group",
    "count": "Count",
    "scan": "Scan",
    "split": "Split",
    "join": "Join",
    "encode": "Encode",
    "decode": "Decode",
    "escape": "Escape",
    "unescape": "Unescape",
    "generate": "Generate",
    "update": "Update",
    "set": "Set",
    "open": "Open",
    "close": "Close",
    "print": "Print",
    "show": "Show",
    "report": "Report",
    "handle": "Handle",
    "process": "Process",
    "send": "Send",
    "fetch": "Fetch",
    "download": "Download",
    "upload": "Upload",
    "install": "Install",
    "cache": "Cache",
    "test": "Test",
    "verify": "Verify",
    "estimate": "Estimate",
    "calculate": "Calculate",
    "compute": "Compute",
    "summarize": "Summarize",
    "explain": "Explain",
    "tokenize": "Tokenize",
    "transform": "Transform",
    "rewrite": "Rewrite",
    "strip": "Strip",
    "wrap": "Wrap",
    "unwrap": "Unwrap",
    "copy": "Copy",
    "move": "Move",
    "rename": "Rename",
    "backup": "Back up",
    "restore": "Restore",
    "initialize": "Initialize",
    "init": "Initialize",
    "setup": "Set up",
    "configure": "Configure",
    "add": "Add",
    "append": "Append",
    "remove": "Remove",
    "map": "Map",
    "convert_to": "Convert",
    "emit": "Emit",
    "write_to": "Write",
    "read_from": "Read",
    "main": "Run the command-line interface",
}


def words(name: str) -> str:
    """Turn a snake_case identifier into a short phrase."""
    return re.sub(r"\s+", " ", name.strip("_").replace("_", " ")).strip()


def summary(name: str) -> str | None:
    """Build a concise summary only where the identifier conveys an operation."""
    if name == "main":
        return "Run the command-line interface."
    special = {
        "__init__": "Initialize the instance state from the supplied arguments.",
        "__enter__": "Enter the managed context and return its resource.",
        "__exit__": "Release resources acquired by the managed context.",
        "__str__": "Return the object's display string.",
        "__repr__": "Return the object's developer-facing representation.",
        "__iter__": "Return an iterator over the object's items.",
        "__next__": "Return the next item from the iterator.",
        "__len__": "Return the number of items represented by the object.",
        "__getitem__": "Return the item selected by the supplied key.",
        "__call__": "Invoke the behavior represented by this object.",
    }
    if name in special:
        return special[name]
    parts = name.strip("_").split("_")
    if not parts:
        return None
    verb = VERBS.get(parts[0])
    if verb is None:
        return None
    subject = " ".join(parts[1:])
    if not subject:
        if parts[0] == "normalize":
            subject = "input"
        else:
            return None
    if verb == "Yield":
        return f"Yield {subject} from the supplied input."
    return f"{verb} {subject}."


def class_summary(node: ast.ClassDef) -> str | None:
    """Describe a class only when its base, decorator, or fields clarify its role."""
    name = words(node.name)
    bases = {ast.unparse(base).split(".")[-1] for base in node.bases}
    enum_base = bases & {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}
    if enum_base:
        qualifier = (
            "compression codecs"
            if "Codec" in node.name
            else "container formats"
            if "Container" in node.name
            else f"{name} values"
        )
        return f"Enumerate the supported {qualifier}."
    if "Exception" in bases:
        return "Signal an error raised by this module."
    dataclass_decorator = any(
        isinstance(dec, ast.Name)
        and dec.id == "dataclass"
        or isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Name)
        and dec.func.id == "dataclass"
        for dec in node.decorator_list
    )
    if dataclass_decorator:
        fields = [n.target.id for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
        if fields:
            return f"Store {', '.join(fields[:5])}{' and related state' if len(fields) > 5 else ''}."
    if "RawIOBase" in bases:
        action = "decompression" if "Reader" in node.name else "compression" if "Writer" in node.name else "raw I/O"
        return f"Provide a raw I/O stream for {action}."
    return None


def literal_type(node: ast.expr) -> str | None:
    """Infer a parameter type only when its default gives an unambiguous type."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return "bool"
        if isinstance(node.value, str):
            return "str"
        if isinstance(node.value, bytes):
            return "bytes"
        if isinstance(node.value, int):
            return "int"
        if isinstance(node.value, float):
            return "float"
        # None's useful union type cannot be inferred from the default alone.
        return None
    if isinstance(node, (ast.List, ast.ListComp)):
        return "list"
    if isinstance(node, ast.Tuple):
        return "tuple"
    if isinstance(node, (ast.Dict, ast.DictComp)):
        return "dict"
    if isinstance(node, (ast.Set, ast.SetComp)):
        return "set"
    return None


def expr_type(node: ast.expr) -> str | None:
    """Infer a return annotation from a direct, unambiguous expression."""
    if isinstance(node, ast.Constant):
        return literal_type(node)
    if isinstance(node, (ast.List, ast.ListComp)):
        return "list"
    if isinstance(node, ast.Tuple):
        return "tuple"
    if isinstance(node, (ast.Dict, ast.DictComp)):
        return "dict"
    if isinstance(node, (ast.Set, ast.SetComp)):
        return "set"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return (
            node.func.id
            if node.func.id in {"str", "int", "float", "bool", "bytes", "list", "tuple", "dict", "set"}
            else None
        )
    return None


def inferred_return(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return a type only when all non-empty return expressions agree."""

    class ReturnVisitor(ast.NodeVisitor):
        """Collect returns without entering nested functions or lambdas."""

        def __init__(self) -> None:
            self.values: list[ast.expr | None] = []
            self.has_yield = False

        def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
            """Skip nested function bodies when collecting outer returns."""
            return

        def visit_AsyncFunctionDef(self, child: ast.AsyncFunctionDef) -> None:
            """Skip nested async-function bodies when collecting returns."""
            return

        def visit_Lambda(self, child: ast.Lambda) -> None:
            """Skip lambda bodies because they are separate callables."""
            return

        def visit_Return(self, child: ast.Return) -> None:
            """Record a return in the function currently being analyzed."""
            self.values.append(child.value)

        def visit_Yield(self, child: ast.Yield) -> None:
            """Mark the function as a generator whose return type is unknown."""
            self.has_yield = True

        def visit_YieldFrom(self, child: ast.YieldFrom) -> None:
            """Mark the function as a delegating generator."""
            self.has_yield = True

    visitor = ReturnVisitor()
    for statement in node.body:
        visitor.visit(statement)
    if visitor.has_yield:
        return None
    types: set[str] = set()
    has_empty = False
    for value in visitor.values:
        if value is None:
            has_empty = True
        else:
            inferred = expr_type(value)
            if inferred is None:
                return None
            types.add(inferred)
    if not types:
        return "None" if has_empty else None
    if len(types) == 1:
        only = next(iter(types))
        return f"{only} | None" if has_empty else only
    return None


class Enricher(cst.CSTTransformer):
    """Insert conservative docstrings and infer obvious literal-based annotations."""

    METADATA_DEPENDENCIES = (cst.metadata.PositionProvider,)

    def __init__(self, source: str, filename: str) -> None:
        """Index source declarations and initialize transformation statistics."""
        self.source = source
        self.filename = filename
        self.tree = ast.parse(source, filename=filename)
        self.functions = {
            node.lineno: node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.classes = {node.lineno: node for node in ast.walk(self.tree) if isinstance(node, ast.ClassDef)}
        self.added_docs = 0
        self.added_annotations = 0
        self.unclear: list[str] = []
        self._module_doc_added = False

    @staticmethod
    def _doc_node(text: str) -> cst.SimpleStatementLine:
        """Create a string-expression statement suitable for a docstring."""
        return cst.SimpleStatementLine(body=[cst.Expr(value=cst.SimpleString(value=repr(text)))])

    def _modify_body(
        self, body: cst.IndentedBlock | cst.SimpleStatementSuite, doc: str | None
    ) -> cst.IndentedBlock | cst.SimpleStatementSuite:
        """Prepend a missing docstring without altering existing body statements."""
        if isinstance(body, cst.IndentedBlock):
            statements = list(body.body)
            if doc and statements and not self._has_docstring(statements[0]):
                statements.insert(0, self._doc_node(doc))
                self.added_docs += 1
            return body.with_changes(body=statements)
        if doc:
            # Convert one-line suites only when a docstring is actually needed.
            return cst.IndentedBlock(body=[self._doc_node(doc), cst.SimpleStatementLine(body=list(body.body))])
        return body

    @staticmethod
    def _has_docstring(statement: cst.BaseStatement) -> bool:
        """Check whether a CST statement is a standalone string expression."""
        return (
            isinstance(statement, cst.SimpleStatementLine)
            and len(statement.body) == 1
            and isinstance(statement.body[0], cst.Expr)
            and isinstance(statement.body[0].value, cst.SimpleString)
        )

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        """Add a brief module docstring only when the source lacks one."""
        first = updated_node.body[0] if updated_node.body else None
        if first and self._has_docstring(first):
            return updated_node
        tree_doc = ast.get_docstring(self.tree)
        if tree_doc:
            return updated_node
        # Prefer a description already supplied to ArgumentParser.
        description = None
        for call in ast.walk(self.tree):
            if isinstance(call, ast.Call):
                fn = ast.unparse(call.func)
                if fn.endswith("ArgumentParser"):
                    for kw in call.keywords:
                        if (
                            kw.arg == "description"
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)
                        ):
                            description = kw.value.value
                            break
            if description:
                break
        title = description or f"Utilities for {Path(self.filename).stem.replace('_', ' ')}."
        doc = self._doc_node(title)
        body = list(updated_node.body)
        # A module docstring precedes all statements, including future imports.
        body.insert(0, doc)
        self.added_docs += 1
        self._module_doc_added = True
        return updated_node.with_changes(body=body)

    def _func_doc(self, original: cst.FunctionDef) -> str | None:
        """Return a conservative summary for an undocumented public function."""
        pos = self.get_metadata(cst.metadata.PositionProvider, original, None)
        info = self.functions.get(pos.start.line if pos else -1)
        if info is None or ast.get_docstring(info):
            return None
        if info.name.startswith("_") and info.name not in {
            "__init__",
            "__enter__",
            "__exit__",
            "__iter__",
            "__next__",
            "__getitem__",
            "__len__",
            "__str__",
            "__repr__",
            "__call__",
        }:
            return None
        text = summary(info.name)
        if text is None:
            self.unclear.append(f"{self.filename}:{info.lineno} {info.name}: no conservative summary from its name")
        return text

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        """Visit function bodies so nested declarations are also documented."""
        return True

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        """Add eligible docstrings and safe parameter/return annotations."""
        doc = self._func_doc(original_node)
        params = updated_node.params
        ast_info = self.functions.get(self.get_metadata(cst.metadata.PositionProvider, original_node).start.line)
        defaults: dict[str, ast.expr] = {}
        if ast_info:
            positional = [*ast_info.args.posonlyargs, *ast_info.args.args]
            aligned = positional[len(positional) - len(ast_info.args.defaults) :]
            defaults.update((arg.arg, value) for arg, value in zip(aligned, ast_info.args.defaults))
            defaults.update(
                (arg.arg, value)
                for arg, value in zip(ast_info.args.kwonlyargs, ast_info.args.kw_defaults)
                if value is not None
            )

        def annotate(param: cst.Param) -> cst.Param:
            """Add a literal-default annotation when its type is unambiguous."""
            if param.annotation is not None or param.name.value in {"self", "cls"}:
                return param
            default = defaults.get(param.name.value)
            ann = literal_type(default) if default is not None else None
            if ann:
                self.added_annotations += 1
                return param.with_changes(annotation=cst.Annotation(cst.parse_expression(ann)))
            return param

        updated_params = params.with_changes(
            params=[annotate(param) for param in params.params],
            posonly_params=[annotate(param) for param in params.posonly_params],
            kwonly_params=[annotate(param) for param in params.kwonly_params],
            star_arg=annotate(params.star_arg) if isinstance(params.star_arg, cst.Param) else params.star_arg,
            star_kwarg=annotate(params.star_kwarg) if isinstance(params.star_kwarg, cst.Param) else params.star_kwarg,
        )
        updated_node = updated_node.with_changes(params=updated_params)
        if updated_node.returns is None and ast_info:
            ann = inferred_return(ast_info)
            if ann:
                updated_node = updated_node.with_changes(returns=cst.Annotation(cst.parse_expression(ann)))
                self.added_annotations += 1
        return updated_node.with_changes(body=self._modify_body(updated_node.body, doc))

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        """Add a class docstring only when its role is supported by its structure."""
        pos = self.get_metadata(cst.metadata.PositionProvider, original_node).start.line
        info = self.classes.get(pos)
        doc = class_summary(info) if info and not info.name.startswith("_") and not ast.get_docstring(info) else None
        if info and not info.name.startswith("_") and not ast.get_docstring(info) and doc is None:
            self.unclear.append(
                f"{self.filename}:{info.lineno} class {info.name}: role is not clear from its declaration"
            )
        return updated_node.with_changes(body=self._modify_body(updated_node.body, doc))


def process(path: Path) -> tuple[int, int, list[str]]:
    """Create one documented copy and return docstring/annotation/review counts."""
    source = path.read_text(encoding="utf-8")
    transformer = Enricher(source, path.name)
    module = cst.parse_module(source)
    wrapper = cst.MetadataWrapper(module)
    result = wrapper.visit(transformer)
    out = DEST / path.name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.code, encoding="utf-8")
    out.chmod(path.stat().st_mode & 0o777)
    # Fail closed if generated output is not valid Python for the source's grammar.
    ast.parse(result.code, filename=str(out))
    return transformer.added_docs, transformer.added_annotations, transformer.unclear


def main() -> None:
    """Create copies for the selected files and print per-file statistics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="root .py filenames")
    parser.add_argument("--all", action="store_true", help="process all root .py files")
    args = parser.parse_args()
    paths = sorted(ROOT.glob("*.py")) if args.all else [ROOT / name for name in args.files]
    if not paths:
        parser.error("provide filenames or --all")
    docs = annotations = 0
    unresolved = []
    for path in paths:
        if path.parent != ROOT or not path.is_file() or path.suffix != ".py":
            parser.error(f"not a root Python file: {path}")
        d, a, u = process(path)
        docs += d
        annotations += a
        unresolved.extend(u)
        print(f"{path.name}: {d} docstrings, {a} annotations, {len(u)} unclear public functions")
    print(f"TOTAL: {docs} docstrings, {annotations} annotations, {len(unresolved)} unclear public functions")
    if unresolved:
        review = DEST / "NEEDS_REVIEW.txt"
        prior = review.read_text(encoding="utf-8").splitlines() if review.exists() else []
        review.write_text("\n".join(sorted(set(prior + unresolved))) + "\n", encoding="utf-8")
        print(f"Unclear declarations recorded in {review.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
