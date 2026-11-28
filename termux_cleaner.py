#!/data/data/com.termux/files/usr/bin/env python
"Utilities for termux cleaner."

from __future__ import annotations

import glob
import os
import re
import shutil
import sys
from pathlib import Path

from dh import fsz, gsz
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


DIRS_TO_DELETE: list[str] = [
    "$PREFIX/lib/python3.*/site-packages/**/test",
    "$PREFIX/lib/python3.*/site-packages/**/tests",
    "$PREFIX/lib/python3.*/site-packages/**/__pycache__",
    "$PREFIX/share/man",
    "$PREFIX/share/doc",
    "$PREFIX/share/info",
    "$HOME/.cache/black",
    "$HOME/.cache/pip/http-2",
    "$HOME/.cache/fissix",
    "$HOME/.cache/ruff",
    "$HOME/.cargo/registry",
    "$HOME/**/__pycache__",
]

FILE_EXTENSIONS: list[str] = [
    ".tmp",
    ".bak",
    ".save",
    ".log",
    ".cache",
    # ".pyc", # Un-comment if you want to aggressively delete individual bytecode files outside __pycache__
]

# --- CONFIGURATION: Specific scan zones for the file extensions ---
# Scans your python libraries and your user home directory by default
SCAN_ZONES: list[str] = [
    "$PREFIX",
    "$HOME",
    "$SDCARD",
]


def _expand_env(value: str) -> str:
    return re.sub(
        r"\$(?:(\w+)|\{([^}]+)\})",
        lambda m: os.environ.get(m.group(1) or m.group(2), m.group(0)),
        value,
    )


def expand_and_resolve(pattern: str) -> list[str]:
    """Expands environment variables and resolves wildcards."""
    return glob.glob(_expand_env(pattern), recursive=True)


def purge_system() -> None:
    deleted_count = 0
    total = 0
    before = gsz("/data/data/com.termux")
    before += gsz("/sdcard")

    for pattern in DIRS_TO_DELETE:
        for path in expand_and_resolve(pattern):
            p = Path(path)
            if p.exists() and p.is_dir():
                try:
                    total += gsz(path)
                    shutil.rmtree(path)
                    print(f"[-] Removed Dir: {path}")
                    deleted_count += 1
                except Exception as e:
                    print(f"[!] Error removing dir {path}: {e}")

    for zone in SCAN_ZONES:
        expanded_zone = _expand_env(zone)
        for ext in FILE_EXTENSIONS:
            # Recursively match anything ending in the extension
            search_pattern = str(Path(expanded_zone) / f"**/*{ext}")
            for file_path in glob.glob(search_pattern, recursive=True):
                if Path(file_path).is_file():
                    try:
                        os.remove(file_path)
                        print(f"[-] Removed File: {file_path}")
                        deleted_count += 1
                    except Exception as e:
                        print(f"[!] Error removing file {file_path}: {e}")

    after = gsz("/data/data/com.termux")
    after += gsz("/sdcard")
    diff_size = before - after
    print(f"space freed: {fsz(diff_size)}")


if __name__ == "__main__":
    purge_system()
