#!/data/data/com.termux/files/usr/bin/env python
"""Find duplicate files by content, then optionally remove or symlink them.

CLI entry point; scans trees with a thread pool and can re-verify a saved JSON
report before deleting anything."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Sequence
import contextlib
from datetime import UTC, datetime
import filecmp
from fnmatch import fnmatch
from functools import partial
import json
from multiprocessing.pool import ThreadPool
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, NamedTuple, TypeAlias

from dh import MAX_WORKERS, SKIP_DIRS, atomic_write, fsz
from loguru import logger
from xxhash import xxh64


CHUNKSIZE: int = 262144
HEAD_SIZE: int = 65536
DEFAULT_JSON: str = ".cache.json"
SIZE_UNITS: dict[str, int] = {
    "": 1,
    "b": 1,
    "k": 1 << 10,
    "kb": 1 << 10,
    "m": 1 << 20,
    "mb": 1 << 20,
    "g": 1 << 30,
    "gb": 1 << 30,
    "t": 1 << 40,
    "tb": 1 << 40,
}
HashResult: TypeAlias = tuple[str | None, str]
SizeIndex: TypeAlias = dict[int, list[str]]


class Group(NamedTuple):
    """Store digest, size, paths."""

    digest: str
    size: int
    paths: list[str]


class Entry(NamedTuple):
    """Store path, size, mtime_ns, dev, ino and related state."""

    path: str
    size: int
    mtime_ns: int
    dev: int
    ino: int
    nlink: int


class ScanOptions(NamedTuple):
    """Store min_size, max_size, excludes, skip_hidden."""

    min_size: int
    max_size: int | None
    excludes: tuple[str, ...]
    skip_hidden: bool


def emit(message: str, quiet: bool) -> None:
    if not quiet:
        logger.info(message)


def parse_size(text: str) -> int:
    """Parse size."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?b?)\s*", text.lower())
    if match is None:
        raise argparse.ArgumentTypeError(f"invalid size: {text!r}")
    return int(float(match[1]) * SIZE_UNITS[match[2]])


def scan(root: str, skip: str, opts: ScanOptions) -> tuple[SizeIndex, int]:
    by_size: defaultdict[int, list[str]] = defaultdict(list)
    seen_inodes: set[tuple[int, int]] = set()
    scanned: int = 0
    stack: list[str] = [root]
    while stack:
        current: str = stack.pop()
        try:
            iterator = os.scandir(current)
        except OSError as exc:
            logger.warning(f"Cannot scan {current}: {exc}")
            continue
        with iterator:
            for entry in iterator:
                name: str = entry.name
                if name in SKIP_DIRS:
                    continue
                if opts.skip_hidden and name.startswith("."):
                    continue
                if opts.excludes and any(fnmatch(name, pattern) for pattern in opts.excludes):
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    logger.error(f"Error accessing {entry.path}: {exc}")
                    continue
                size: int = st.st_size
                if size < opts.min_size or (opts.max_size is not None and size > opts.max_size):
                    continue
                if entry.path == skip:
                    continue
                if st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    if key in seen_inodes:
                        continue
                    seen_inodes.add(key)
                scanned += 1
                by_size[size].append(entry.path)
    return {size: paths for size, paths in by_size.items() if len(paths) > 1}, scanned


def hash_file(path: str, limit: int | None = None) -> HashResult:
    try:
        h = xxh64()
        with open(path, "rb", buffering=0) as f:
            if limit is not None:
                h.update(f.read(limit))
            else:
                while chunk := f.read(CHUNKSIZE):
                    h.update(chunk)
        return h.hexdigest(), path
    except OSError as exc:
        logger.warning(f"Cannot hash {path}: {exc}")
        return None, path


def hash_many(pool: ThreadPool, paths: Sequence[str], limit: int | None, workers: int) -> list[HashResult]:
    if not paths:
        return []
    chunksize: int = max(1, min(64, len(paths) // (workers * 4)))
    return pool.map(partial(hash_file, limit=limit), paths, chunksize=chunksize)


def group_hashes(results: Sequence[HashResult], sizes: dict[str, int]) -> list[Group]:
    """Group hashes."""
    buckets: defaultdict[tuple[int, str], list[str]] = defaultdict(list)
    for digest, path in results:
        if digest is not None:
            buckets[(sizes[path], digest)].append(path)
    return [Group(digest, size, sorted(paths)) for (size, digest), paths in buckets.items() if len(paths) > 1]


def find_groups(by_size: SizeIndex, workers: int) -> list[Group]:
    """Find groups."""
    sizes: dict[str, int] = {path: size for size, paths in by_size.items() for path in paths}
    candidates: list[str] = list(sizes)
    with ThreadPool(workers) as pool:
        stage_one: list[Group] = group_hashes(hash_many(pool, candidates, HEAD_SIZE, workers), sizes)
        final: list[Group] = [group for group in stage_one if group.size <= HEAD_SIZE]
        pending: list[str] = sorted(
            (path for group in stage_one if group.size > HEAD_SIZE for path in group.paths),
            key=sizes.__getitem__,
            reverse=True,
        )
        final.extend(group_hashes(hash_many(pool, pending, None, workers), sizes))
    return sorted(final, key=lambda group: (-group.size, group.paths[0]))


def save_report(output: Path, root: Path, groups: Sequence[Group]) -> None:
    """Save report."""
    payload: dict[str, Any] = {
        "root": str(root),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "groups": {group.digest: [Path(path).relative_to(root).as_posix() for path in group.paths] for group in groups},
    }
    atomic_write(output, json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"saved {len(groups)} group(s) to {output}")


def load_report(output: Path) -> tuple[Path, list[str]] | None:
    """Load report."""
    paths: set[str] = set()
    try:
        data: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
        root = Path(os.path.normpath(data["root"]))
        for rels in data["groups"].values():
            for rel in rels:
                full = Path(os.path.normpath(root / rel))
                if full.is_relative_to(root):
                    paths.add(str(full))
                else:
                    logger.warning(f"ignoring path outside root: {rel}")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        logger.error(f"Cannot read {output}: {exc}")
        return None
    return root, sorted(paths)


def stat_entries(paths: Sequence[str]) -> dict[str, Entry]:
    entries: dict[str, Entry] = {}
    for path in paths:
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            entries[path] = Entry(path, st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino, st.st_nlink)
    return entries


def freed_bytes(removed: Sequence[Entry]) -> int:
    counts: Counter[tuple[int, int]] = Counter()
    total: int = 0
    for entry in removed:
        key = (entry.dev, entry.ino)
        counts[key] += 1
        if counts[key] == entry.nlink:
            total += entry.size
    return total


def replace_with_symlink(target: str, link: str) -> None:
    temp: str = f"{link}.{os.getpid()}.tmp"
    try:
        relative: str = os.path.relpath(target, os.path.dirname(link))
    except ValueError:
        relative = target
    try:
        os.symlink(relative, temp)
        os.replace(temp, link)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def find_duplicates(
    root: Path,
    output: Path,
    opts: ScanOptions,
    workers: int,
    quiet: bool,
    dry_run: bool,
) -> int:
    """Find duplicates."""
    start: float = time.perf_counter()
    by_size, scanned = scan(str(root), str(output), opts)
    candidates: int = sum(len(paths) for paths in by_size.values())
    logger.info(f"scanned {scanned} file(s), {candidates} share a size with another file")
    groups: list[Group] = find_groups(by_size, workers) if by_size else []
    elapsed: float = time.perf_counter() - start
    if not groups:
        logger.info(f"NO DUPS ({elapsed:.2f}s)")
        return 0
    for group in groups:
        emit(f"hash {group.digest} | {fsz(group.size)} x {len(group.paths)}", quiet)
        for path in group.paths:
            emit(f" - {os.path.relpath(path, root)}", quiet)
    reclaimable: int = sum(group.size * (len(group.paths) - 1) for group in groups)
    logger.info(f"groups : {len(groups)} | reclaimable : {fsz(reclaimable)} | {elapsed:.2f}s")
    if dry_run:
        logger.info("dry-run : report not written")
    else:
        save_report(output, root, groups)
    return 0


def apply_report(output: Path, workers: int, args: argparse.Namespace) -> int:
    """Apply report."""
    start: float = time.perf_counter()
    if not output.is_file():
        logger.error(f"{output} not found, run without --apply first")
        return 1
    loaded = load_report(output)
    if loaded is None:
        return 1
    root, listed = loaded
    entries: dict[str, Entry] = stat_entries(listed)
    if missing := len(listed) - len(entries):
        logger.warning(f"{missing} listed file(s) no longer exist")
    sizes: dict[str, int] = {path: entry.size for path, entry in entries.items()}
    with ThreadPool(workers) as pool:
        ordered_paths: list[str] = sorted(entries, key=sizes.__getitem__, reverse=True)
        groups: list[Group] = group_hashes(hash_many(pool, ordered_paths, None, workers), sizes)
    if not groups:
        logger.info("NO DUPS (nothing left to process)")
        if not args.dry_run:
            output.unlink(missing_ok=True)
        return 0
    sign: int = 1 if args.keep == "oldest" else -1
    verb: str = "symlink" if args.symlink else "remove"
    plan: list[tuple[Entry, Entry]] = []
    for group in groups:
        members: list[Entry] = sorted(
            (entries[path] for path in group.paths),
            key=lambda entry: (sign * entry.mtime_ns, entry.path),
        )
        keeper: Entry = members[0]
        emit(f"keep    {os.path.relpath(keeper.path, root)}", args.quiet)
        for extra in members[1:]:
            if (extra.dev, extra.ino) == (keeper.dev, keeper.ino):
                emit(
                    f"skip    {os.path.relpath(extra.path, root)} (hardlink)",
                    args.quiet,
                )
                continue
            emit(f"{verb:<7} {os.path.relpath(extra.path, root)}", args.quiet)
            plan.append((extra, keeper))
    if not plan:
        logger.info("nothing to do")
        return 0
    expected: int = freed_bytes([extra for extra, _ in plan])
    if args.dry_run:
        logger.info(f"dry-run : would {verb} {len(plan)} file(s), freeing {fsz(expected)}")
        return 0
    if not args.yes:
        try:
            answer: str = input(f"{verb.capitalize()} {len(plan)} file(s), freeing {fsz(expected)}? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "y":
            logger.info("aborted")
            return 1
    done: list[Entry] = []
    failed: int = 0
    for extra, keeper in plan:
        try:
            if args.verify and not filecmp.cmp(keeper.path, extra.path, shallow=False):
                raise OSError("content differs from kept file")
            if args.symlink:
                replace_with_symlink(keeper.path, extra.path)
            else:
                os.unlink(extra.path)
        except OSError as exc:
            logger.error(f"Cannot {verb} {extra.path}: {exc}")
            failed += 1
            continue
        done.append(extra)
    past: str = "symlinked" if args.symlink else "removed"
    suffix: str = f" | {failed} failed" if failed else ""
    logger.info(
        f"{past} {len(done)} file(s) | freed {fsz(freed_bytes(done))}{suffix} | {time.perf_counter() - start:.2f}s"
    )
    if failed:
        return 1
    output.unlink(missing_ok=True)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse args."""
    parser = argparse.ArgumentParser(
        description="Find duplicate files by content, then optionally remove or symlink them."
    )
    parser.add_argument(
        "-a",
        "--apply",
        action="store_true",
        help="Re-verify files listed in the JSON report and remove duplicates.",
    )
    parser.add_argument(
        "-s",
        "--symlink",
        action="store_true",
        help="Replace duplicates with symlinks to the kept file instead of deleting (implies --apply).",
    )
    parser.add_argument(
        "-k",
        "--keep",
        choices=("oldest", "newest"),
        default="oldest",
        help="Which file to keep in each group (default: oldest, ties by path).",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Change nothing: no report on scan, no removal on apply.",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument(
        "-V",
        "--verify",
        action="store_true",
        help="Byte-compare each duplicate with the kept file before touching it.",
    )
    parser.add_argument(
        "-r",
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Directory to scan (default: current directory).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(DEFAULT_JSON),
        help=f"JSON report path (default: {DEFAULT_JSON}).",
    )
    parser.add_argument(
        "-m",
        "--min-size",
        type=parse_size,
        default=1,
        help="Skip files smaller than this, e.g. 4K, 1M (default: 1 byte).",
    )
    parser.add_argument(
        "-M",
        "--max-size",
        type=parse_size,
        default=None,
        help="Skip files larger than this, e.g. 500M, 2G.",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Skip file or directory names matching this glob (repeatable).",
    )
    parser.add_argument(
        "-H",
        "--skip-hidden",
        action="store_true",
        help="Skip files and directories starting with a dot.",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Number of hashing threads (default: {MAX_WORKERS}).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only print summaries, not every file.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    args: argparse.Namespace = parse_args(argv)
    workers: int = max(1, args.workers)
    output: Path = args.output.resolve()
    if args.apply or args.symlink:
        return apply_report(output, workers, args)
    root: Path = args.root.resolve()
    if not root.is_dir():
        logger.error(f"{root} is not a directory")
        return 1
    opts = ScanOptions(max(args.min_size, 1), args.max_size, tuple(args.exclude), args.skip_hidden)
    return find_duplicates(root, output, opts, workers, args.quiet, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
