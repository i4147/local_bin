#!/data/data/com.termux/files/usr/bin/env python
"Utilities for shortener."

from pathlib import Path

from codeutils import shorten_names
from dh import get_pyfiles, gsz, mpf, rrs


def process_file(path):
    "Process file."
    before = gsz(path)
    try:
        shorten_names(path, backup=False)
        after = gsz(path)
        rrs(path, before, after)
    except Exception:
        pass


if __name__ == "__main__":
    cwd = Path.cwd()
    files = get_pyfiles(cwd)
    mpf(process_file, files, method="imap_unordered")
