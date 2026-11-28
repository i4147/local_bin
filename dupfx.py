#!/data/data/com.termux/files/usr/bin/env python
"""Find and delete the duplicate files by content.

Pre-filters by size and a 4 KB head hash, confirms with a full xxhash and chooses which
copy to keep (``-k``: first, last, oldest, newest, largest); ``-r`` deletes, ``-n`` previews, ``--all``
includes ``.git``.

Usage: dupfx [-r] [-k KEEP] [--all] [--dry-run] [paths ...]

Options:
    --recursive
        Search directories recursively (default: True).
    -r, --remove
        Actually delete duplicate files. Without this flag, only report them.
    -a, --all
        Include .git directories in the search (symlinks are still skipped).
    -n, --dry-run
        Don't delete; just show what would be done.
    --follow-symlinks
        Follow symlinks to files.
    -k, --keep
        Which file to keep within duplicates.

Examples:
    $ dupfx ~/Music -r -k oldest

Command-line script: run ``python dupfx.py`` or the ``dupfx`` command. The module is importable as well.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from enum import StrEnum
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from loguru import logger
from xxhash import xxh64
import os
import sys

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


class Keep(StrEnum):
    "Enumerate the supported Keep values."

    first = "first"
    oldest = "oldest"
    newest = "newest"


QUICK_READ: int = 4096
CHUNK_SIZE: int = 65536
POOL_WORKERS: int = 8


def file_stat_key(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
        return (st.st_ino, st.st_dev)
    except OSError:
        return None


def quick_hash(path: Path, n: int = QUICK_READ) -> str:
    h = xxh64()
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(n)
            h.update(head)
            if size > n * 2:
                f.seek(max(size - n, 0))
                tail = f.read(n)
                h.update(tail)
            elif size > n:
                rest = f.read()
                h.update(rest)
    except OSError as e:
        msg = f"quick_hash error {path}: {e}"
        raise OSError(msg) from e
    return h.hexdigest()


def full_hash(path: Path) -> tuple:
    try:
        if not path.stat().st_size:
            return ("", path)
    except OSError:
        return ("", path)
    h = xxh64()
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return (h.hexdigest(), path)
    except OSError:
        return ("", path)


def iter_files(
    root: Path,
    recursive: bool,
    follow_symlinks: Any,
    include_git: bool = False,
):
    "Yield files from the supplied input."
    if recursive:
        iterator = root.rglob("*")
    else:
        iterator = root.iterdir()
    for p in iterator:
        if not include_git and ".git" in p.parts:
            continue
        if not p.is_file():
            continue
        if p.is_symlink() and not follow_symlinks:
            continue
        yield p


def choose_keep(files: list[Path], policy: str = "oldest"):
    "Choose keep."
    if not files:
        msg = "Empty file list"
        raise ValueError(msg)
    if policy == "first":
        return min(files, key=str)
    elif policy == "oldest":
        return min(files, key=lambda p: p.stat().st_mtime)
    elif policy == "newest":
        return max(files, key=lambda p: p.stat().st_mtime)
    else:
        return min(files, key=str)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Find and delete duplicate files by content.")
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Search directories recursively (default: True).",
    )
    parser.add_argument(
        "-r",
        "--remove",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Actually delete duplicate files. Without this flag, only report them.",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Include .git directories in the search (symlinks are still skipped).",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Don't delete; just show what would be done.",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinks to files.",
    )
    parser.add_argument(
        "-k",
        "--keep",
        choices=[k.value for k in Keep],
        default=Keep.oldest.value,
        help="Which file to keep within duplicates.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    "Run the command-line interface."
    args = build_parser().parse_args(argv)
    recursive: bool = args.recursive
    remove: bool = args.remove
    all: bool = args.all
    dry_run: bool = args.dry_run
    follow_symlinks: bool = args.follow_symlinks
    keep: Keep = Keep(args.keep)
    cwd = Path.cwd()
    root = Path.cwd()
    print("Phase 1: Scanning files and grouping by size...")
    size_groups = defaultdict(list)
    total_files = 0
    for f in iter_files(
        root,
        recursive,
        follow_symlinks,
        include_git=all,
    ):
        total_files += 1
        try:
            size = f.stat().st_size
            size_groups[size].append(f)
        except OSError:
            continue
    candidates = {s: lst for s, lst in size_groups.items() if len(lst) > 1}
    if not candidates:
        print(f"Scanned {total_files} files. No potential duplicates found.")
        return
    candidate_count = sum(len(v) for v in candidates.values())
    print(f"Phase 1 complete: {candidate_count} files in {len(candidates)} size-groups to examine.")
    print("Phase 2: Quick hash comparison...")
    quick_groups = defaultdict(list)
    with Pool(processes=POOL_WORKERS) as pool:
        futures = []
        for files in candidates.values():
            for fpath in files:
                futures.append((fpath, pool.apply_async(quick_hash, (fpath,))))
        for fpath, fut in futures:
            try:
                h = fut.get()
                key = (fpath.stat().st_size, h)
                quick_groups[key].append(fpath)
            except Exception as e:
                logger.warning(f"Skipping {fpath}: {e}")
    need_full = [group for group in quick_groups.values() if len(group) > 1]
    if not need_full:
        print("No duplicates found after quick hash comparison.")
        return
    full_candidates = sum(len(g) for g in need_full)
    print(f"Phase 2 complete: {full_candidates} files in {len(need_full)} groups need full hash.")
    print("Phase 3: Full hash comparison...")
    full_groups: defaultdict[str, list[tuple[Path, tuple[int, int] | None]]] = defaultdict(list)
    with Pool(processes=POOL_WORKERS) as pool:
        futures2 = []
        for group in need_full:
            for fpath in group:
                st_key = file_stat_key(fpath)
                futures2.append((fpath, st_key, pool.apply_async(full_hash, (fpath,))))
        for fpath, st_key, fut in futures2:
            try:
                h, _ = fut.get()
                if h:
                    full_groups[h].append((fpath, st_key))
            except Exception as e:
                logger.warning(f"Skipping {fpath}: {e}")
    print("Phase 4: Processing results...")
    to_delete: list[Path] = []
    for entries in full_groups.values():
        if len(entries) < 2:
            continue
        inode_map: defaultdict[tuple[int, int] | None, list[Path]] = defaultdict(list)
        for p_path, stk in entries:
            inode_map[stk].append(p_path)
        group_reps = [min(ps, key=str) for ps in inode_map.values()]
        if len(group_reps) < 2:
            continue
        keep_file = choose_keep(group_reps, policy=keep)
        for rep in group_reps:
            if rep != keep_file:
                to_delete.append(rep)
    if not to_delete:
        print("No duplicate files found.")
        return
    print(f"\nFound {len(to_delete)} duplicate files.")
    for p_del in to_delete:
        try:
            rel_path = p_del.relative_to(cwd)
        except ValueError:
            rel_path = p_del
        print(f"  {rel_path}")
    if not remove:
        print(
            f"\nReport-only mode. {len(to_delete)} files would be deleted.\n"
            "Run with -r/--remove to actually delete them."
        )
        return
    if dry_run:
        print(f"\nDry-run complete. {len(to_delete)} files would be deleted.")
        return
    removed = 0
    failed = 0
    freed_space = 0
    for p_del in to_delete:
        try:
            size = p_del.stat().st_size
            p_del.unlink()
            freed_space += size
            removed += 1
            try:
                print(f"Deleted: {p_del.relative_to(cwd)} ({size:,} bytes)")
            except ValueError:
                print(f"Deleted: {p_del} ({size:,} bytes)")
        except OSError as e:
            failed += 1
            try:
                logger.error(f"Failed: {p_del.relative_to(cwd)} - {e}")
            except ValueError:
                logger.error(f"Failed: {p_del} - {e}")
    print("\nSummary:")
    print(f"  Files scanned: {total_files}")
    print(f"  Duplicates found: {len(to_delete)}")
    print(f"  Successfully deleted: {removed}")
    if failed:
        print(f"  Failed to delete: {failed}")
    if freed_space:
        print(f"  Space freed: {freed_space:,} bytes ({freed_space / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
