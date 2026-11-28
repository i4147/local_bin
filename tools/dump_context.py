#!/usr/bin/env python3
"""Dump numbered source context around explicit line ranges or `ty` diagnostics."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import re

_DIAG_RE = re.compile(r"^(?P<file>[^:\s]+):(?P<line>\d+):\d+:\s+(?P<rest>.*)$")


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Coalesce overlapping or adjacent 1-based inclusive line ranges."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def dump_file_ranges(path: Path, ranges: list[tuple[int, int]], highlight: set[int] | None = None) -> None:
    """Print numbered source lines for each range in `path`."""
    lines = path.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    markers = highlight or set()
    for start, end in merge_ranges(ranges):
        lo = max(1, start)
        hi = min(total, end)
        print(f"=== {path} {lo}-{hi} ===")
        for idx in range(lo, hi + 1):
            mark = ">" if idx in markers else " "
            print(f"{mark} {idx:4d}: {lines[idx - 1]}")


def main(argv: list[str] | None = None) -> int:
    """Dump source context from either `file:start-end` specs or a `ty` report."""
    parser = argparse.ArgumentParser(
        description="Dump numbered source lines for explicit ranges or `ty` diagnostics."
    )
    parser.add_argument(
        "specs",
        nargs="*",
        help="Explicit `file:start-end` or `file:line` specs (or filenames when using --report).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Concise `ty check` output file to extract diagnostic lines from.",
    )
    parser.add_argument(
        "-C",
        "--context",
        type=int,
        default=8,
        help="Lines of context before and after each diagnostic line (default: 8).",
    )
    args = parser.parse_args(argv)

    if args.report:
        wanted = set(args.specs)
        by_file: dict[str, list[int]] = defaultdict(list)
        for line in args.report.read_text(encoding="utf-8").splitlines():
            match = _DIAG_RE.match(line.strip())
            if match is None:
                continue
            fname = match.group("file")
            if wanted and fname not in wanted:
                continue
            by_file[fname].append(int(match.group("line")))
        for fname, line_nums in by_file.items():
            ranges = [(ln - args.context, ln + args.context) for ln in line_nums]
            dump_file_ranges(Path(fname), ranges, highlight=set(line_nums))
        return 0

    by_spec: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for spec in args.specs:
        if ":" not in spec:
            parser.error(f"expected FILE:START-END or FILE:LINE, got {spec!r}")
        fname, span = spec.rsplit(":", 1)
        if "-" in span:
            s_str, e_str = span.split("-", 1)
            by_spec[fname].append((int(s_str), int(e_str)))
        else:
            ln = int(span)
            by_spec[fname].append((ln - args.context, ln + args.context))

    for fname, ranges in by_spec.items():
        dump_file_ranges(Path(fname), ranges)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
