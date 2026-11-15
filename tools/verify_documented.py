#!/usr/bin/env python3
"""Verify documented copies preserve executable AST and report coverage."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DOCUMENTED = ROOT / "documented"


def strip_docs_annotations(tree: ast.AST) -> ast.AST:
    """Remove docstrings and function annotations before AST comparison."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.returns = None
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if node.args.vararg:
                args.append(node.args.vararg)
            if node.args.kwarg:
                args.append(node.args.kwarg)
            for arg in args:
                arg.annotation = None
    return tree


def main() -> int:
    """Check all root scripts and print documentation/type-annotation totals."""
    originals = sorted(ROOT.glob("*.py"))
    missing = []
    mismatches = []
    parse_errors = []
    docs = annotations = 0
    public_total = public_documented = 0
    module_docs_added = 0
    for source in originals:
        target = DOCUMENTED / source.name
        if not target.is_file():
            missing.append(source.name)
            continue
        try:
            original_tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            copy_tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
        except SyntaxError as exc:
            parse_errors.append(f"{source.name}:{exc.lineno}: {exc.msg}")
            continue
        if ast.dump(
            strip_docs_annotations(ast.parse(source.read_text(encoding="utf-8"))), include_attributes=False
        ) != ast.dump(strip_docs_annotations(ast.parse(target.read_text(encoding="utf-8"))), include_attributes=False):
            mismatches.append(source.name)
        module_docs_added += int(not ast.get_docstring(original_tree) and bool(ast.get_docstring(copy_tree)))
        original_nodes = [
            n for n in ast.walk(original_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        copy_nodes = [
            n for n in ast.walk(copy_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        public_total += sum(not n.name.startswith("_") for n in copy_nodes)
        public_documented += sum(not n.name.startswith("_") and bool(ast.get_docstring(n)) for n in copy_nodes)
        docs += sum(not n.name.startswith("_") and bool(ast.get_docstring(n)) for n in copy_nodes) - sum(
            not n.name.startswith("_") and bool(ast.get_docstring(n)) for n in original_nodes
        )

        def annotation_count(tree: ast.Module) -> int:
            """Count function parameter and return annotations in an AST."""
            count = 0
            for function in ast.walk(tree):
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    count += sum(
                        arg.annotation is not None
                        for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
                    )
                    count += bool(function.args.vararg and function.args.vararg.annotation)
                    count += bool(function.args.kwarg and function.args.kwarg.annotation)
                    count += bool(function.returns)
            return count

        annotations += annotation_count(copy_tree) - annotation_count(original_tree)
    print(f"Python files: {len(originals)} originals; {len(originals) - len(missing)} documented copies")
    print(f"New public symbol docstrings: {docs}; new module docstrings: {module_docs_added}")
    print(f"New parameter/return annotations: {annotations}")
    print(f"Public symbols documented: {public_documented}/{public_total}")
    print(
        f"Missing copies: {len(missing)}; parse errors: {len(parse_errors)}; behavior AST mismatches: {len(mismatches)}"
    )
    if missing:
        print("Missing:", ", ".join(missing))
    for error in parse_errors:
        print("Parse error:", error)
    if mismatches:
        print("AST mismatch:", ", ".join(mismatches))
    return int(bool(missing or parse_errors or mismatches))


if __name__ == "__main__":
    raise SystemExit(main())
