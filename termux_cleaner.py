#!/data/data/com.termux/files/usr/bin/env python
import os
import shutil
import glob
import subprocess

from dh import gsz, dsz

DIRS_TO_DELETE = [
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

FILE_EXTENSIONS = [
    ".tmp",
    ".bak",
    ".save",
    ".log",
    ".cache",
    # ".pyc", # Un-comment if you want to aggressively delete individual bytecode files outside __pycache__
]

# --- CONFIGURATION: Specific scan zones for the file extensions ---
# Scans your python libraries and your user home directory by default
SCAN_ZONES = [
    "$PREFIX",
    "$HOME",
    "$SDCARD",
]


def expand_and_resolve(pattern):
    """Expands environment variables and resolves wildcards."""
    return glob.glob(os.path.expandvars(pattern), recursive=True)


def purge_system():
    deleted_count = 0
    before = gsz("/data/data/com.termux")
    before += gsz("/sdcard")

    for pattern in DIRS_TO_DELETE:
        for path in expand_and_resolve(pattern):
            if os.path.exists(path) and os.path.isdir(path):
                try:
                    total += gsz(path)
                    shutil.rmtree(path)
                    print(f"[-] Removed Dir: {path}")
                    deleted_count += 1
                except Exception as e:
                    print(f"[!] Error removing dir {path}: {e}")

    for zone in SCAN_ZONES:
        expanded_zone = os.path.expandvars(zone)
        for ext in FILE_EXTENSIONS:
            # Recursively match anything ending in the extension
            search_pattern = os.path.join(expanded_zone, f"**/*{ext}")
            for file_path in glob.glob(search_pattern, recursive=True):
                if os.path.isfile(file_path):
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
