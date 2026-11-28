#!/data/data/com.termux/files/usr/bin/env python
"""
cron-explain: Advanced Crontab Parser, Explainer, and DST Simulator.
Fully type-annotated and self-contained for Python 3.12+.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


@dataclasses.dataclass(frozen=True)
class CronSpec:
    minutes: set[int]
    hours: set[int]
    days_of_month: set[int]
    months: set[int]
    days_of_week: set[int]
    dom_restricted: bool
    dow_restricted: bool
    raw_expression: str
    user: str = "root"
    command: str = ""
    source_file: str = "stdin"
    line_number: int = 1
    macro_name: str | None = None


@dataclasses.dataclass(frozen=True)
class RunInstance:
    at: datetime
    kind: str
    note: str | None = None


@dataclasses.dataclass(frozen=True)
class CronAnalysis:
    source: str
    line: int
    user: str
    schedule: str
    explanation: str
    runs: list[RunInstance]
    warnings: list[str]


MACRO_MAP: dict[str, str] = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
MONTH_NAMES: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
DOW_NAMES: dict[str, int] = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def parse_field(field: str, min_val: int, max_val: int, name_map: dict[str, int] | None = None) -> set[int]:
    """Parses a single cron field with ranges, steps, and lists."""
    results: set[int] = set()
    if field == "*":
        return set(range(min_val, max_val + 1))
    parts = field.split(",")
    for part in parts:
        step = 1
        if "/" in part:
            part, step_str = part.split("/", 1)
            try:
                step = int(step_str)
            except ValueError as e:
                msg = f"Invalid step value '{step_str}' in field '{field}'"
                raise ValueError(msg) from e
        if part == "*":
            sub_range = range(min_val, max_val + 1)
        elif "-" in part:
            start_str, end_str = part.split("-", 1)
            start = resolve_token(start_str, min_val, max_val, name_map)
            end = resolve_token(end_str, min_val, max_val, name_map)
            if start > end:
                sub_range = list(range(start, max_val + 1)) + list(range(min_val, end + 1))
            else:
                sub_range = range(start, end + 1)
        else:
            val = resolve_token(part, min_val, max_val, name_map)
            sub_range = [val]
        for i, val in enumerate(sub_range):
            if i % step == 0:
                results.add(val)
    return results


def resolve_token(token: str, min_val: int, max_val: int, name_map: dict[str, int] | None) -> int:
    "Resolve token."
    token_lower = token.lower()
    if name_map and token_lower in name_map:
        return name_map[token_lower]
    try:
        val = int(token)
        if not (min_val <= val <= max_val):
            if max_val == 6 and min_val == 0 and val == 7:
                return 0
            msg = f"Value {val} out of range [{min_val}, {max_val}]"
            raise ValueError(msg)
        return val
    except ValueError as e:
        if name_map:
            msg = f"Invalid token '{token}'"
            raise ValueError(msg) from e
        raise


def parse_cron_line(line: str, source_file: str = "stdin", line_number: int = 1) -> CronSpec | None:
    "Parse cron line."
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "=" in line and not line.startswith("@"):
        return None
    macro_name = None
    if line.startswith("@"):
        parts = line.split(None, 1)
        macro = parts[0].lower()
        if macro == "@reboot":
            cmd = parts[1] if len(parts) > 1 else ""
            return CronSpec(
                minutes={0},
                hours={0},
                days_of_month={1},
                months={1},
                days_of_week=set(range(7)),
                dom_restricted=False,
                dow_restricted=False,
                raw_expression=macro,
                user="root",
                command=cmd,
                source_file=source_file,
                line_number=line_number,
                macro_name="@reboot",
            )
        if macro in MACRO_MAP:
            line = MACRO_MAP[macro] + (" " + parts[1] if len(parts) > 1 else "")
            macro_name = macro
        else:
            msg = f"Unknown cron macro: {macro}"
            raise ValueError(msg)
    tokens = line.split(None, 5)
    if len(tokens) < 5:
        msg = f"Malformed cron line (less than 5 fields): '{line}'"
        raise ValueError(msg)
    min_s, hour_s, dom_s, mon_s, dow_s = tokens[:5]
    command = tokens[5] if len(tokens) > 5 else ""
    user = "root"
    if len(tokens) == 6:
        pass
    minutes = parse_field(min_s, 0, 59)
    hours = parse_field(hour_s, 0, 23)
    dom_restricted = dom_s != "*"
    days_of_month = parse_field(dom_s, 1, 31)
    months = parse_field(mon_s, 1, 12, MONTH_NAMES)
    dow_restricted = dow_s != "*"
    days_of_week = parse_field(dow_s, 0, 6, DOW_NAMES)
    if 7 in days_of_week:
        days_of_week.remove(7)
        days_of_week.add(0)
    raw = f"{min_s} {hour_s} {dom_s} {mon_s} {dow_s}"
    return CronSpec(
        minutes=minutes,
        hours=hours,
        days_of_month=days_of_month,
        months=months,
        days_of_week=days_of_week,
        dom_restricted=dom_restricted,
        dow_restricted=dow_restricted,
        raw_expression=raw,
        user=user,
        command=command,
        source_file=source_file,
        line_number=line_number,
        macro_name=macro_name,
    )


def explain_cron(spec: CronSpec) -> str:
    "Explain cron."
    if spec.macro_name == "@reboot":
        return "Runs once at startup / system boot."
    parts = []
    parts.append(f"Minutes: {explain_field_vals(spec.minutes, 0, 59, 'minute')}")
    parts.append(f"Hours: {explain_field_vals(spec.hours, 0, 23, 'hour')}")
    if spec.dom_restricted and spec.dow_restricted:
        parts.append(f"Days of month {set_to_str(spec.days_of_month)} OR days of week {set_to_str(spec.days_of_week)}")
    elif spec.dom_restricted:
        parts.append(f"Days of month: {set_to_str(spec.days_of_month)}")
    elif spec.dow_restricted:
        parts.append(f"Days of week: {set_to_str(spec.days_of_week)}")
    else:
        parts.append("Every day")
    parts.append(f"Months: {set_to_str(spec.months)}")
    return ", ".join(parts)


def explain_field_vals(vals: set[int], min_v: int, max_v: int, label: str) -> str:
    "Explain field vals."
    if len(vals) == (max_v - min_v + 1):
        return f"every {label}"
    return set_to_str(vals)


def set_to_str(vals: set[int]) -> str:
    "Set to str."
    sorted_v = sorted(vals)
    if not sorted_v:
        return "none"
    ranges = []
    start = prev = sorted_v[0]
    for v in sorted_v[1:]:
        if v == prev + 1:
            prev = v
        else:
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = v
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)


def matches_spec(dt: datetime, spec: CronSpec) -> bool:
    if dt.minute not in spec.minutes:
        return False
    if dt.hour not in spec.hours:
        return False
    if dt.month not in spec.months:
        return False
    dom_match = dt.day in spec.days_of_month
    dow_match = dt.weekday() in spec.days_of_week
    cron_dow = (dt.weekday() + 1) % 7
    dow_match = cron_dow in spec.days_of_week
    if spec.dom_restricted and spec.dow_restricted:
        return dom_match or dow_match
    elif spec.dom_restricted:
        return dom_match
    elif spec.dow_restricted:
        return dow_match
    return True


def compute_next_runs(
    spec: CronSpec, tz: ZoneInfo, start_after: datetime, n: int = 5
) -> tuple[list[RunInstance], list[str]]:
    "Compute next runs."
    runs: list[RunInstance] = []
    warnings: list[str] = []
    if start_after.tzinfo is None:
        start_after = start_after.replace(tzinfo=UTC)
    local_start = start_after.astimezone(tz)
    current = local_start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    horizon = current + timedelta(days=730)
    checked_days: set[str] = set()
    while len(runs) < n and current < horizon:
        naive_dt = current.replace(tzinfo=None)
        try:
            (naive_dt.replace(tzinfo=tz, fold=0) if hasattr(datetime, "fold") else naive_dt.replace(tzinfo=tz))
        except Exception:
            pass
        try:
            candidate = datetime(
                naive_dt.year,
                naive_dt.month,
                naive_dt.day,
                naive_dt.hour,
                naive_dt.minute,
                tzinfo=tz,
            )
            rt = candidate.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
            is_gap = rt != naive_dt
        except Exception:
            is_gap = True
            candidate = None
        day_str = current.strftime("%Y-%m-%d")
        if day_str not in checked_days:
            checked_days.add(day_str)
        if candidate and not is_gap:
            if matches_spec(candidate, spec):
                runs.append(RunInstance(at=candidate, kind="normal"))
        elif is_gap and candidate:
            runs.append(
                RunInstance(
                    at=candidate,
                    kind="shifted",
                    note="DST gap (spring forward); Vixie cron runs once during catch-up",
                )
            )
        current += timedelta(minutes=1)
    for run in runs:
        run.at.strftime("%Y-%m-%d")
    if any("fall back" in (r.note or "") for r in runs):
        warnings.append("Ambiguous time encountered due to DST fall back.")
    return runs, warnings


def analyze_overlaps(specs: list[CronSpec], default_duration_minutes: int = 30) -> list[str]:
    """Analyze overlaps."""
    warnings = []
    if len(specs) < 2:
        return warnings
    for i in range(len(specs)):
        for j in range(i + 1, len(specs)):
            if specs[i].command and specs[i].command == specs[j].command:
                warnings.append(
                    f"Potential overlap: duplicate command '{specs[i].command}' in lines {specs[i].line_number} and {specs[j].line_number}"
                )
    return warnings


def discover_sources(file_path: str | None) -> list[tuple[str, int, str]]:
    "Discover sources."
    lines = []
    if file_path:
        if not Path(file_path).exists():
            msg = f"File not found: {file_path}"
            raise FileNotFoundError(msg)
        if "cron.d" in file_path:
            stat = os.stat(file_path)
            mode = stat.st_mode & 0o777
            if mode != 0o644:
                print(
                    f"Warning: {file_path} has permissions {oct(mode)}, expected 0644",
                    file=sys.stderr,
                )
        with open(file_path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                lines.append((file_path, idx, line))
    else:
        for idx, line in enumerate(sys.stdin, 1):
            lines.append(("stdin", idx, line))
    return lines


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Advanced Crontab Explainer & DST Simulator")
    parser.add_argument(
        "expression",
        nargs="?",
        default=None,
        help="Cron expression string (e.g., '*/5 9-17 * * MON-FRI')",
    )
    parser.add_argument("--file", default=None, help="Path to crontab or cron.d file")
    parser.add_argument(
        "--tz",
        default="UTC",
        help="Target timezone (e.g., America/Toronto, Europe/London)",
    )
    parser.add_argument("--next", type=int, default=5, help="Number of next runs to compute")
    parser.add_argument(
        "--dst-warnings",
        action="store_true",
        help="Enable strict DST gap/fold analysis",
    )
    parser.add_argument(
        "--overlaps",
        action="store_true",
        help="Check for overlapping job executions",
    )
    parser.add_argument(
        "--min-duration",
        default="30m",
        help="Assumed job duration for overlap analysis",
    )
    parser.add_argument(
        "--json",
        dest="json",
        action="store_true",
        help="Output results in JSON format",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    "Run the command-line interface."
    args = build_parser().parse_args(argv)
    try:
        tz_info = ZoneInfo(args.tz)
    except ZoneInfoNotFoundError:
        print(f"Error: Timezone '{args.tz}' not found.", file=sys.stderr)
        sys.exit(2)
    raw_lines = []
    if args.expression:
        raw_lines.append(("cli", 1, args.expression))
    else:
        try:
            raw_lines = discover_sources(args.file)
        except Exception as e:
            print(f"Error reading sources: {e}", file=sys.stderr)
            sys.exit(1)
    specs: list[CronSpec] = []
    for source, line_num, text in raw_lines:
        try:
            spec = parse_cron_line(text, source_file=source, line_number=line_num)
            if spec:
                specs.append(spec)
        except Exception as e:
            print(f"Parser error in {source}:{line_num}: {e}", file=sys.stderr)
    all_analyses = []
    has_warnings = False
    for spec in specs:
        explanation = explain_cron(spec)
        runs, warnings = compute_next_runs(spec, tz_info, datetime.now(tz_info), n=args.next)
        if warnings:
            has_warnings = True
        all_analyses.append(
            CronAnalysis(
                source=spec.source_file,
                line=spec.line_number,
                user=spec.user,
                schedule=spec.raw_expression,
                explanation=explanation,
                runs=runs,
                warnings=warnings,
            )
        )
    if args.overlaps:
        overlap_warns = analyze_overlaps(specs)
        if overlap_warns:
            has_warnings = True
    if args.json:
        output_data = []
        for a in all_analyses:
            output_data.append({
                "source": a.source,
                "line": a.line,
                "user": a.user,
                "schedule": a.schedule,
                "explanation": a.explanation,
                "runs": [{"at": r.at.isoformat(), "kind": r.kind, "note": r.note} for r in a.runs],
                "warnings": a.warnings,
            })
        print(json.dumps(output_data, indent=2))
    else:
        for a in all_analyses:
            print(f"=== Source: {a.source}:{a.line} ({a.user}) ===")
            print(f"Schedule:   {a.schedule}")
            print(f"Explain:    {a.explanation}")
            print("Next Runs:")
            for r in a.runs:
                note_str = f" [{r.note}]" if r.note else ""
                print(f"  - {r.at.isoformat()} ({r.kind}){note_str}")
            if a.warnings:
                print(f"Warnings:   {', '.join(a.warnings)}")
            print()
    if has_warnings:
        sys.exit(3)
    sys.exit(0)


if __name__ == "__main__":
    main()
