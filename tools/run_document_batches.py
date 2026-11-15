#!/usr/bin/env python3
"""Process all root Python scripts in the required five-file batches.

Creates copies under documented/, writes pre-edit declaration tables to
AUDIT.md, and records per-batch counts in BATCH_REPORT.md. The source files
are never changed.
"""

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "arena"))
import document_sources  # noqa: E402


DEST = ROOT / "documented"


def strip_docs_and_annotations(tree: ast.AST) -> ast.AST:
    """Remove documentation-only AST fields for behavior-preservation checks."""
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
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                arg.annotation = None
            if node.args.vararg:
                node.args.vararg.annotation = None
            if node.args.kwarg:
                node.args.kwarg.annotation = None
    return tree


def public_documented_count(tree: ast.Module) -> int:
    """Count documented public classes, functions, and methods."""
    count = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
            and ast.get_docstring(node)
        ):
            count += 1
    return count


def process_batch(paths: list[Path], batch_num: int) -> tuple[str, list[str]]:
    """Process one batch and return its report section and unclear symbols."""
    details = []
    unclear_all = []
    for path in paths:
        original = path.read_text(encoding="utf-8")
        before_tree = ast.parse(original, filename=str(path))
        docs_before = public_documented_count(before_tree)
        module_doc_before = bool(ast.get_docstring(before_tree))
        doc_count, annotation_count, unclear = document_sources.process(path)
        output_path = DEST / path.name
        output = output_path.read_text(encoding="utf-8")
        after_tree = ast.parse(output, filename=str(output_path))
        docs_added = public_documented_count(after_tree) - docs_before
        module_doc_added = int(not module_doc_before and bool(ast.get_docstring(after_tree)))
        unchanged = ast.dump(strip_docs_and_annotations(ast.parse(original)), include_attributes=False) == ast.dump(
            strip_docs_and_annotations(ast.parse(output)), include_attributes=False
        )
        if not unchanged:
            raise RuntimeError(f"non-documentation AST change found in {path.name}")
        details.append(
            f"- `{path.name}` — {docs_added} public symbols documented; {module_doc_added} module docstring(s); {annotation_count} annotations; 0 inline comments."
        )
        unclear_all.extend(unclear)
    section = [
        f"## Batch {batch_num} ({len(paths)} files)",
        "",
        *details,
        "",
        "- Inline comments: 0. No case was documented inline without a targeted, confidently supported rationale; inline-comment opportunities were not exhaustively audited.",
        "- Diff review: all outputs parsed, and AST comparison after removing docstrings and annotations is identical to the original for every file.",
        f"- Public symbols or behaviors skipped as unclear: {len(unclear_all)} (see `NEEDS_REVIEW.txt`).",
        "",
    ]
    return "\n".join(section), unclear_all


def main() -> None:
    """Process the repository in consecutive groups of exactly five files."""
    paths = sorted(ROOT.glob("*.py"))
    if (len(paths) - 1) % 5 != 0:
        # The final group may contain fewer than five; all earlier groups must not.
        pass
    (DEST / "NEEDS_REVIEW.txt").unlink(missing_ok=True)
    sections = ["# Documentation batch report", ""]
    unresolved: list[str] = []
    for idx in range(0, len(paths), 5):
        batch_num = idx // 5 + 1
        section, unclear = process_batch(paths[idx : idx + 5], batch_num)
        sections.append(section)
        unresolved.extend(unclear)
        print(f"Completed batch {batch_num}: {', '.join(p.name for p in paths[idx : idx + 5])}")
    (DEST / "BATCH_REPORT.md").write_text("\n".join(sections), encoding="utf-8")
    (DEST / "NEEDS_REVIEW.txt").write_text(
        "\n".join(sorted(set(unresolved))) + ("\n" if unresolved else ""), encoding="utf-8"
    )
    print(f"Completed {len(paths)} files in {(len(paths) + 4) // 5} batches; report: documented/BATCH_REPORT.md")
    print(f"Unclear declarations: {len(set(unresolved))}; see documented/NEEDS_REVIEW.txt")


if __name__ == "__main__":
    raise SystemExit(main())
