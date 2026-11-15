#!/usr/bin/env python3
"""Run batch migration from standard library logging to loguru across root Python scripts."""

from __future__ import annotations
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent


def migrate_logging_in_file(path: Path) -> bool:
    content = path.read_text(encoding="utf-8")
    original = content

    if "import logging" in content:
        content = re.sub(r"import logging(\n|$)", r"from loguru import logger\1", content)
        content = re.sub(r"\b[A-Za-z0-9_]+\s*=\s*logging\.getLogger\([^)]*\)", "log = logger", content)

    if content != original:
        path.write_text(content, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed = []
    for p in sorted(ROOT.glob("*.py")):
        if p.is_symlink() or not p.is_file():
            continue
        if "import logging" in p.read_text(encoding="utf-8", errors="ignore"):
            if migrate_logging_in_file(p):
                changed.append(p.name)
    print(f"Migrated logging in {len(changed)} files: {', '.join(changed)}")


if __name__ == "__main__":
    main()
