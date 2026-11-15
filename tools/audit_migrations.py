#!/data/data/com.termux/files/usr/bin/env python
"""Audit helper to check migration status for:
- argparse -> typer
- os.path -> pathlib
- stdlib logging -> loguru
"""

from __future__ import annotations
import ast
import os
from pathlib import Path


def audit_repo(root: Path = Path()) -> dict[str, list[str]]:
    results = {
        "argparse": [],
        "os_path": [],
        "stdlib_logging": [],
    }

    for path in sorted(root.glob("*.py")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if "import argparse" in content or "from argparse import" in content:
            results["argparse"].append(path.name)
        if "os.path." in content:
            results["os_path"].append(path.name)
        if "import logging" in content or "from logging import" in content:
            results["stdlib_logging"].append(path.name)

    return results


def main() -> None:
    results = audit_repo()
    print("=== Migration Audit ===")
    print(f"Files still using argparse ({len(results['argparse'])}):")
    for f in results["argparse"]:
        print(f"  - {f}")
    print(f"\nFiles still using os.path ({len(results['os_path'])}):")
    for f in results["os_path"]:
        print(f"  - {f}")
    print(f"\nFiles still using stdlib logging ({len(results['stdlib_logging'])}):")
    for f in results["stdlib_logging"]:
        print(f"  - {f}")


if __name__ == "__main__":
    main()
