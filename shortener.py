#!/data/data/com.termux/files/usr/bin/env python
from codeutils import shorten_names
import sys
from pathlib import Path
from dh import get_pyfiles, mpf, rrs, gsz, fsz


def process_file(path):
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
