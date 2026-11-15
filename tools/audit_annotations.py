#!/usr/bin/env python3
"""List unresolved parameter and return annotations in documented copies."""

import ast
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DOCUMENTED = ROOT / "documented"


def missing_annotations(path: Path) -> tuple[list[str], Counter[str]]:
    """Return unresolved function annotations and per-file parameter counts."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    missing: list[str] = []
    counts: Counter[str] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg:
            args.append(node.args.vararg)
        if node.args.kwarg:
            args.append(node.args.kwarg)
        names = [arg.arg for arg in args if arg.arg not in {"self", "cls"} and arg.annotation is None]
        if names:
            counts["parameters"] += len(names)
            missing.append(
                f"- Line {node.lineno}, `{node.name}` parameters: {', '.join(f'`{name}`' for name in names)}"
            )
        if node.returns is None:
            counts["returns"] += 1
            missing.append(f"- Line {node.lineno}, `{node.name}` return type: not inferred safely")
    return missing, counts


def main() -> None:
    """Write the annotation gap report for all generated Python copies."""
    sections = [
        "# Type annotation gaps",
        "",
        "This report lists annotations left out because the type could not be determined confidently from the signature and local syntax. These are flagged for review rather than filled with `Any` or guessed types. Line numbers refer to files in `documented/`.",
        "",
    ]
    totals: Counter[str] = Counter()
    for path in sorted(DOCUMENTED.glob("*.py")):
        rows, counts = missing_annotations(path)
        totals.update(counts)
        if rows:
            sections += [
                f"## `{path.name}` — {counts['parameters']} untyped parameters; {counts['returns']} untyped returns",
                "",
                *rows,
                "",
            ]
    sections += [
        "## Totals",
        "",
        f"- Untyped parameters: {totals['parameters']}",
        f"- Untyped returns: {totals['returns']}",
        "- Reason: inferred type would require assumptions about caller contracts, external objects, or control-flow behavior.",
        "",
    ]
    out = DOCUMENTED / "ANNOTATIONS_REVIEW.md"
    out.write_text("\n".join(sections), encoding="utf-8")
    print(
        f"Wrote {out.relative_to(ROOT)}: {totals['parameters']} untyped parameters, {totals['returns']} untyped returns"
    )


if __name__ == "__main__":
    raise SystemExit(main())
