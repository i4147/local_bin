#!/usr/bin/env python3
"""Summarize concise `ty check` output by file and diagnostic rule."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

_DIAG_RE = re.compile(r"^(?P<file>[^:\s]+):\d+:\d+:\s+(?P<level>error|warning)\[(?P<rule>[^\]]+)\]\s+(?P<msg>.*)$")


def parse_report(lines: list[str]) -> tuple[Counter[str], Counter[str], Counter[str]]:
    """Parse concise `ty` diagnostic lines into file, rule, and level counts."""
    by_file: Counter[str] = Counter()
    by_rule: Counter[str] = Counter()
    by_level: Counter[str] = Counter()
    for line in lines:
        match = _DIAG_RE.match(line.strip())
        if match is None:
            continue
        by_file[match.group("file")] += 1
        by_rule[match.group("rule")] += 1
        by_level[match.group("level")] += 1
    return by_file, by_rule, by_level


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and print `ty` diagnostic statistics."""
    parser = argparse.ArgumentParser(description="Summarize concise `ty check` output.")
    parser.add_argument(
        "report",
        nargs="?",
        type=Path,
        help="Path to concise `ty` output file (default: read stdin).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Optional earlier concise report to compare against.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(),
        help="Repo root used to count total `*.py` files (default: cwd).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Maximum files/rules to display (default: 30; 0 for all).",
    )
    args = parser.parse_args(argv)

    raw = args.report.read_text(encoding="utf-8") if args.report else sys.stdin.read()
    by_file, by_rule, by_level = parse_report(raw.splitlines())

    total_diags = sum(by_file.values())
    total_py = len(list(args.root.glob("*.py")))
    dirty_files = len(by_file)
    clean_files = max(0, total_py - dirty_files)

    print(f"Diagnostics: {total_diags} ({by_level['error']} errors, {by_level['warning']} warnings)")
    if total_py:
        print(f"Files: {dirty_files}/{total_py} with diagnostics ({clean_files}/{total_py} clean)")
    else:
        print(f"Files with diagnostics: {dirty_files}")

    if args.baseline and args.baseline.exists():
        base_file, _, _ = parse_report(args.baseline.read_text(encoding="utf-8").splitlines())
        base_total = sum(base_file.values())
        delta = total_diags - base_total
        pct = (-delta / base_total * 100.0) if base_total else 0.0
        print(f"Delta vs {args.baseline}: {delta:+d} ({pct:.1f}% reduced from {base_total})")

    limit = args.top or None
    if by_file:
        print("\nBy file:")
        for fname, count in by_file.most_common(limit):
            print(f"  {count:4d}  {fname}")

    if by_rule:
        print("\nBy rule:")
        for rule, count in by_rule.most_common(limit):
            print(f"  {count:4d}  {rule}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
