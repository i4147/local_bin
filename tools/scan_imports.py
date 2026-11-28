#!/usr/bin/env python3
"""Scan root Python files via AST and list non-stdlib, non-local top-level imports."""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path


def scan_third_party_imports(root: Path) -> dict[str, list[str]]:
    """Return a mapping of top-level third-party module name -> importing filenames."""
    stdlib = set(sys.stdlib_module_names)
    py_files = sorted(root.glob("*.py"))
    local_mods = {p.stem for p in py_files}
    found: dict[str, set[str]] = defaultdict(set)

    for path in py_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".", 1)[0]
                    if mod not in stdlib and mod not in local_mods:
                        found[mod].add(path.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mod = node.module.split(".", 1)[0]
                if mod not in stdlib and mod not in local_mods:
                    found[mod].add(path.name)

    return {mod: sorted(files) for mod, files in sorted(found.items())}


def main(argv: list[str] | None = None) -> int:
    """List third-party imports found in `*.py` files under `--root`."""
    parser = argparse.ArgumentParser(description="Scan root `*.py` files for third-party imports.")
    parser.add_argument("--root", type=Path, default=Path(), help="Directory to scan (default: cwd).")
    parser.add_argument("--with-files", action="store_true", help="Also print importing filenames.")
    args = parser.parse_args(argv)

    imports = scan_third_party_imports(args.root)
    for mod, files in imports.items():
        if args.with_files:
            print(f"{mod}: {', '.join(files)}")
        else:
            print(mod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
