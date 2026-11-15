#!/usr/bin/env python3
"""Run batch migration from os.path to pathlib across root Python scripts."""

from __future__ import annotations
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent


def migrate_os_path_in_file(path: Path) -> bool:
    content = path.read_text(encoding="utf-8")
    original = content

    if "from pathlib import Path" not in content:
        if "import os\n" in content:
            content = content.replace("import os\n", "import os\nfrom pathlib import Path\n")

    # Common replacements
    content = re.sub(r"os\.path\.exists\(([^)]+)\)", r"Path(\1).exists()", content)
    content = re.sub(r"os\.path\.isfile\(([^)]+)\)", r"Path(\1).is_file()", content)
    content = re.sub(r"os\.path\.isdir\(([^)]+)\)", r"Path(\1).is_dir()", content)
    content = re.sub(r"os\.path\.islink\(([^)]+)\)", r"Path(\1).is_symlink()", content)

    if content != original:
        path.write_text(content, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed = []
    for p in sorted(ROOT.glob("*.py")):
        if p.is_symlink() or not p.is_file():
            continue
        if migrate_os_path_in_file(p):
            changed.append(p.name)
    print(f"Migrated os.path in {len(changed)} files: {', '.join(changed)}")


if __name__ == "__main__":
    main()
