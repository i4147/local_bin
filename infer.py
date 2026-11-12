#!/data/data/com.termux/files/usr/bin/env python
import re
import sys
from pathlib import Path

import libcst as cst


def infer_from_expr(expr):
    """Infer a simple builtin type name from a literal-ish expression."""
    if isinstance(expr, cst.Integer):
        return "int"
    if isinstance(expr, cst.Float):
        return "float"
    if isinstance(expr, cst.Imaginary):
        return "complex"
    if isinstance(expr, cst.UnaryOperation) and isinstance(expr.operator, (cst.Minus, cst.Plus)):
        return infer_from_expr(expr.expression)
    if isinstance(expr, cst.SimpleString):
        prefix = re.match(r"[A-Za-z]*", expr.value).group(0).lower()
        return "bytes" if "b" in prefix else "str"
    if isinstance(expr, cst.FormattedString):
        return "str"
    if isinstance(expr, cst.ConcatenatedString):
        return infer_from_expr(expr.left)
    if isinstance(expr, cst.Name):
        if expr.value in ("True", "False"):
            return "bool"
        if expr.value == "None":
            return "None"
    if isinstance(expr, cst.List):
        return "list"
    if isinstance(expr, cst.Dict):
        return "dict"
    if isinstance(expr, cst.Set):
        return "set"
    if isinstance(expr, cst.Tuple):
        return "tuple"
    return None


class _ReturnCollector(cst.CSTVisitor):
    """Collects return values in a function body, skipping nested scopes."""

    def __init__(self) -> None:
        self.returns = []
        self.has_yield = False

    def visit_FunctionDef(self, node) -> bool:
        return False

    def visit_ClassDef(self, node) -> bool:
        return False

    def visit_Lambda(self, node) -> bool:
        return False

    def visit_Yield(self, node) -> None:
        self.has_yield = True

    def visit_Return(self, node) -> None:
        self.returns.append(node.value)


def _last_is_exit(body) -> bool:
    """True if the block's last statement is a return or raise."""
    if isinstance(body, cst.SimpleStatementSuite):
        last = body.body[-1] if body.body else None
        return isinstance(last, (cst.Return, cst.Raise))
    last = body.body[-1] if body.body else None
    return bool(
        isinstance(last, cst.SimpleStatementLine) and last.body and isinstance(last.body[-1], (cst.Return, cst.Raise))
    )


def infer_return(func) -> str | None:
    """Return an annotation string for the function, or None if unsure."""
    if func.returns is not None or func.asynchronous is None and False:
        return None
    collector = _ReturnCollector()
    func.body.visit(collector)
    if collector.has_yield:
        return None  # generators: too ambiguous for a heuristic

    values = collector.returns
    exits = _last_is_exit(func.body)

    if not values:
        # A function ending in raise with no returns is NoReturn; skip it.
        return None if exits else "None"

    has_value = any(v is not None for v in values)
    if not exits:
        # Possible implicit fall-through to None: too ambiguous to annotate.
        return None if has_value else "None"

    types = set()
    for value in values:
        if value is None:
            types.add("None")
            continue
        t = infer_from_expr(value)
        if t is None:
            return None
        types.add(t)

    non_none = types - {"None"}
    if not non_none:
        return "None"
    if len(non_none) == 1:
        t = non_none.pop()
        return f"{t} | None" if "None" in types else t
    return None  # mixed types, e.g. int and str


class AnnotateTransformer(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.changes = 0

    def _annotate_params(self, params):
        out = []
        for p in params:
            if p.annotation is None and p.default is not None:
                t = infer_from_expr(p.default)
                if t and t != "None":
                    p = p.with_changes(
                        annotation=cst.Annotation(cst.Name(t)),
                        equal=cst.AssignEqual(
                            whitespace_before=cst.SimpleWhitespace(" "),
                            whitespace_after=cst.SimpleWhitespace(" "),
                        ),
                    )
                    self.changes += 1
            out.append(p)
        return out

    def leave_FunctionDef(self, original_node, updated_node):
        params = updated_node.params
        new_node = updated_node.with_changes(
            params=params.with_changes(
                posonly_params=self._annotate_params(params.posonly_params),
                params=self._annotate_params(params.params),
                kwonly_params=self._annotate_params(params.kwonly_params),
            )
        )

        if new_node.returns is None:
            rt = infer_return(original_node)
            if rt:
                new_node = new_node.with_changes(returns=cst.Annotation(cst.parse_expression(rt)))
                self.changes += 1
        return new_node


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <file.py>")

    path = Path(sys.argv[1])
    source = path.read_text(encoding="utf-8")
    module = cst.parse_module(source)

    transformer = AnnotateTransformer()
    new_source = module.visit(transformer).code

    # Make sure the result is still valid Python before overwriting anything.
    compile(new_source, str(path), "exec")

    if new_source != source:
        path.write_text(new_source, encoding="utf-8")
    print(f"Added {transformer.changes} annotation(s) to {path}")


if __name__ == "__main__":
    main()
