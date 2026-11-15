#!/data/data/com.termux/files/usr/bin/env python
"""Build per-symbol documentation audit tables for root Python scripts.

The Markdown report records the pre-edit docstring/comment state for each
module, class, function, and method. Run from any directory with:

    python3 arena/documentation_inventory.py
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "documented" / "AUDIT.md"


def is_public(node: ast.AST) -> str:
    """Classify a named declaration as public or private by its name."""
    name = getattr(node, "name", "")
    return "Yes" if name and not name.startswith("_") else "No"


def has_comment(lines: list[str], lineno: int) -> bool:
    """Return whether the physical declaration line has a trailing comment."""
    return "#" in lines[lineno - 1]


def declaration_rows(tree: ast.Module, lines: list[str]) -> list[tuple[str, str, str, str, str, str]]:
    """Collect the table rows for module, class, function, and method nodes."""
    rows = [
        (
            "module",
            "module",
            "Yes",
            "Yes" if ast.get_docstring(tree) else "No",
            "No",
            "Module-level comments are not added unless a specific invariant needs explanation",
        )
    ]

    def visit(nodes: list[ast.stmt], class_depth: int = 0) -> None:
        """Visit nested suites and append one row per class or callable."""
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                rows.append((
                    node.name,
                    "class",
                    is_public(node),
                    "Yes" if ast.get_docstring(node) else "No",
                    "Review",
                    "Check for non-obvious invariants, ordering, or resource lifetime before adding a comment",
                ))
                visit(node.body, class_depth + 1)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = (
                    "method"
                    if class_depth
                    else ("async function" if isinstance(node, ast.AsyncFunctionDef) else "function")
                )
                rows.append((
                    node.name,
                    kind,
                    is_public(node),
                    "Yes" if ast.get_docstring(node) else "No",
                    "Review",
                    "Check for (a)-(e) cases; no comment is added without a specific, supported rationale",
                ))
                # Nested declarations are local implementation details, but included for complete auditing.
                visit(node.body, class_depth)
            else:
                # Include class/function declarations nested in control-flow blocks.
                body = getattr(node, "body", None)
                if isinstance(body, list):
                    visit(body, class_depth)
                for attr in ("orelse", "finalbody"):
                    nested = getattr(node, attr, None)
                    if isinstance(nested, list):
                        visit(nested, class_depth)
                handlers = getattr(node, "handlers", None)
                if handlers:
                    for handler in handlers:
                        visit(handler.body, class_depth)

    visit(tree.body)
    return rows


def main() -> None:
    """Write the pre-edit audit tables for all root .py files."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    report = [
        "# Pre-edit documentation audit",
        "",
        "Generated from the original root-level Python files. The 'Needs comment?' column flags bodies for review; comments are only added for the cases allowed by `doc.md` (a)-(e).",
        "",
    ]
    for path in sorted(ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        rows = declaration_rows(tree, text.splitlines())
        report += [
            f"## `{path.name}`",
            "",
            "| Item | Kind (module/class/function/method) | Public? | Has docstring? | Needs comment? | Reason if skipped |",
            "|---|---|---|---|---|---|",
        ]
        report += ["| " + " | ".join(row) + " |" for row in rows]
        report.append("")
    OUT.write_text("\n".join(report), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} with tables for {len(list(ROOT.glob('*.py')))} files")


if __name__ == "__main__":
    raise SystemExit(main())
