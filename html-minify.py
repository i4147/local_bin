#!/data/data/com.termux/files/usr/bin/env python
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import NamedTuple
from dh import get_files, runcmd, mpf


class FileResult(NamedTuple):
    path: Path
    before: int | None
    after: int | None
    returncode: int
    error: str


def minify_file(job: tuple[str, str]) -> FileResult:
    binary, filename = job
    path = Path(filename)

    try:
        before = path.stat().st_size
    except OSError as error:
        return FileResult(path, None, None, 1, str(error))

    try:
        returncode, txt, err = runcmd(
            [
                binary,
                "--allow-noncompliant-unquoted-attribute-values",
                "--allow-optimal-entities",
                "--allow-removing-spaces-between-attributes",
                "--minify-css",
                "--minify-js",
                "--minify-doctype",
                "--remove-bangs",
                "--remove-processing-instructions",
                "--preserve-brace-template-syntax",
                "--preserve-chevron-percent-template-syntax",
                str(path),
            ],
            show_output=False,
        )
    except OSError as error:
        return FileResult(path, before, None, 1, str(error))

    if returncode != 0:
        details = (err or "").strip() or f"{binary} exited with status {returncode}"
        return FileResult(path, before, None, returncode, details)

    # Write minified output to a temp file in the same directory, then atomically replace
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as tmp_file:
                tmp_file.write(txt)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as error:
        return FileResult(path, before, None, 1, str(error))

    try:
        after = path.stat().st_size
    except OSError as error:
        return FileResult(path, before, None, 1, str(error))

    return FileResult(path, before, after, 0, "")


def format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)

    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value):,} {unit}"
            return f"{value:,.2f} {unit}"
        value /= 1024

    return f"{size:,} B"


def find_html_files(directory: Path) -> list[Path]:
    return get_files(directory, ext=[".html"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minify HTML files in the current directory in place.")
    parser.add_argument(
        "--binary",
        default="min-html",
        help="min-html executable name or path (default: min-html)",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=max(1, os.cpu_count() or 1),
        help="number of files to process concurrently",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.workers < 1:
        print("Error: --workers must be at least 1.", file=sys.stderr)
        return 2

    files = find_html_files(Path.cwd())
    if not files:
        print("No .html or .htm files found in the current directory.")
        return 0

    jobs = [(args.binary, str(path)) for path in files]
    with ProcessPoolExecutor(max_workers=min(args.workers, len(jobs))) as pool:
        results = list(pool.map(minify_file, jobs))

    total_before = 0
    total_after = 0
    has_errors = False

    for result in results:
        if result.before is not None:
            total_before += result.before
        if result.after is not None:
            total_after += result.after

        if result.returncode != 0 or result.after is None:
            has_errors = True
            print(f"ERROR {result.path.name}: {result.error or 'minification failed'}")
            continue

        change = result.before - result.after
        if change >= 0:
            reduction = f"saved {format_bytes(change)}"
        else:
            reduction = f"grew by {format_bytes(-change)}"

        print(f"{result.path.name}: {format_bytes(result.before or 0)} → {format_bytes(result.after)} ({reduction})")

    net_saved = total_before - total_after
    label = "Total space freed" if net_saved >= 0 else "Total size increase"
    print(f"{label}: {format_bytes(abs(net_saved))}")

    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
