#!/data/data/com.termux/files/usr/bin/env python
"""check_clean.py — report remaining comments and docstrings in .py files.

Usage:
    python check_clean.py [DIR]

Exit status:
    0  — clean (or only shebang / encoding comments remain)
    1  — leftover comments or docstrings found
    2  — bad usage
"""

from __future__ import annotations

import ast
import sys
import tokenize
from pathlib import Path

SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".tox",
    "build",
    "dist",
    ".eggs",
    "node_modules",
    "site-packages",
}


def iter_py_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def _label_special(text: str) -> str | None:
    """Return 'shebang' / 'encoding' for comments that are normally OK to keep."""
    if text.startswith("#!"):
        return "shebang"
    if text.startswith("#") and ("coding:" in text[:30] or "-*-" in text[:20]):
        return "encoding"
    return None


def scan_comments(path: Path):
    ordinary, special = [], []
    try:
        with path.open("rb") as f:
            for tok in tokenize.tokenize(f.readline):
                if tok.type == tokenize.COMMENT:
                    label = _label_special(tok.string)
                    entry = (tok.start[0], tok.start[1], tok.string)
                    (special if label else ordinary).append(entry + ((label,) if label else ()))
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError) as e:
        print(f"  [warn] {path}: cannot tokenize ({e})", file=sys.stderr)
    return ordinary, special


def _is_docstring_stmt(stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _parse(path: Path):
    try:
        with tokenize.open(path) as f:  # respects encoding declarations
            src = f.read()
        return ast.parse(src, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError) as e:
        print(f"  [warn] {path}: cannot parse ({e})", file=sys.stderr)
        return None


def scan(path: Path):
    """Return (ordinary_comments, special_comments, docstrings, bare_strings)."""
    ordinary, special = scan_comments(path)

    tree = _parse(path)
    docstrings, bare_strings = [], []
    if tree is not None:
        docstring_nodes: set[int] = set()

        def first_stmt_docstring(node, kind, name):
            body = getattr(node, "body", None)
            if body and _is_docstring_stmt(body[0]):
                stmt = body[0]
                docstring_nodes.add(id(stmt))
                docstrings.append((stmt.lineno, kind, name, stmt.value.value))

        first_stmt_docstring(tree, "module", "<module>")
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                first_stmt_docstring(node, "class", node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                first_stmt_docstring(node, "function", node.name)

        # Anything else that is a bare string expression is suspicious:
        # it is *not* a Python docstring but looks like leftover doc content.
        for node in ast.walk(tree):
            if _is_docstring_stmt(node) and id(node) not in docstring_nodes:
                bare_strings.append((node.lineno, node.value.value))

    return ordinary, special, docstrings, bare_strings


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else Path(".")
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    total_issues = 0
    total_files = 0

    for path in iter_py_files(root):
        total_files += 1
        rel = path.relative_to(root)
        ordinary, special, docstrings, bare = scan(path)

        if not (ordinary or docstrings or bare or special):
            continue

        print(f"\n=== {rel} ===")

        for lineno, col, text in ordinary:
            print(f"  L{lineno}:{col}  comment: {text!r}")

        for lineno, kind, name, text in docstrings:
            snippet = (text.strip().splitlines() or [""])[0][:70]
            print(f"  L{lineno}  docstring [{kind} {name}]: {snippet!r}")

        for lineno, text in bare:
            snippet = (text.strip().splitlines() or [""])[0][:70]
            print(f"  L{lineno}  suspicious bare string (leftover?): {snippet!r}")

        for lineno, col, text, label in special:
            print(f"  L{lineno}:{col}  {label} comment (usually fine to keep): {text!r}")

        total_issues += len(ordinary) + len(docstrings) + len(bare)

    print()
    if total_issues == 0:
        print(f"✔ Clean — checked {total_files} file(s); no comments or docstrings found.")
        return 0
    print(f"✘ Found {total_issues} leftover item(s) across the checked files.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
