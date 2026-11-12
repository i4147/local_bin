#!/data/data/com.termux/files/usr/bin/env python
import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

MONTH_NAMES = {
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
DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}

FIELD_RANGES = {
    "minute": (0, 59),
    "hour": (0, 23),
    "day_of_month": (1, 31),
    "month": (1, 12),
    "day_of_week": (0, 6),
}


def normalize_dow(value: int) -> int:
    if value == 7:
        return 0
    return value


def humanize_list(values: Sequence[int], names: Optional[dict] = None) -> str:
    if not values:
        return "never"
    # Use names only when the field is month/day-of-week and we may want readable names.
    if names:
        text = [names.get(v, str(v)) for v in values]
        return ", ".join(text)
    return ", ".join(str(v) for v in values)


def field_summary(
    field_name: str, values: Set[int], *, names: Optional[dict] = None, use_wildcard: bool = False
) -> str:
    ordered = sorted(values)
    if field_name == "minute":
        if use_wildcard:
            return "every minute"
        if ordered == list(range(0, 60)):
            return "every minute"
        if len(ordered) == 1:
            return f"at minute {ordered[0]}"
        return f"at minutes {humanize_list(ordered)}"
    if field_name == "hour":
        if use_wildcard:
            return "every hour"
        if ordered == list(range(0, 24)):
            return "every hour"
        if len(ordered) == 1:
            return f"at hour {ordered[0]}"
        return f"at hours {humanize_list(ordered)}"
    if field_name == "month":
        if use_wildcard:
            return "every month"
        if ordered == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]:
            return "every month"
        return f"in months {humanize_list(ordered, names=names)}"
    if field_name == "day_of_month":
        if use_wildcard:
            return "every day of the month"
        if len(ordered) == 1:
            return f"on day {ordered[0]} of the month"
        return f"on days {humanize_list(ordered)} of the month"
    if field_name == "day_of_week":
        if use_wildcard:
            return "every day of the week"
        if len(ordered) == 1:
            return f"on {humanize_list(ordered, names=names)}"
        return f"on {humanize_list(ordered, names=names)}"
    if use_wildcard:
        return "every possible value"
    return f"at values {humanize_list(ordered)}"


def parse_token(field_name: str, token: str) -> int:
    token = token.strip().lower()
    if field_name == "month" and token in MONTH_NAMES:
        return MONTH_NAMES[token]
    if field_name == "day_of_week" and token in DOW_NAMES:
        return DOW_NAMES[token]
    if field_name == "day_of_week" and token == "7":
        return 0
    if token.isdigit():
        value = int(token)
        if field_name == "day_of_week":
            return normalize_dow(value)
        return value
    raise ValueError(f"Unsupported token '{token}' in field '{field_name}'")


def parse_field(field_name: str, text: str) -> Set[int]:
    lo, hi = FIELD_RANGES[field_name]
    values: Set[int] = set()

    if text == "*":
        return set(range(lo, hi + 1))

    for part in text.split(","):
        part = part.strip()
        if not part:
            continue

        # Handle step: */n, 1-5/2, 3/10
        if "/" in part:
            base, step_text = part.split("/", 1)
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ValueError(f"Bad step value '{part}' in field '{field_name}'") from exc
            if step <= 0:
                raise ValueError(f"Step must be > 0 in field '{field_name}'")
            if base == "*":
                start, end = lo, hi
            elif "-" in base:
                start_text, end_text = base.split("-", 1)
                start = parse_token(field_name, start_text)
                end = parse_token(field_name, end_text)
            else:
                start = parse_token(field_name, base)
                end = hi
            for v in range(start, end + 1, step):
                if lo <= v <= hi:
                    values.add(v)
            continue

        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = parse_token(field_name, start_text)
            end = parse_token(field_name, end_text)
            if start > end:
                start, end = end, start
            values.update(range(start, end + 1))
            continue

        value = parse_token(field_name, part)
        values.add(value)

    # Range validate after parse
    for value in values:
        if not (lo <= value <= hi):
            raise ValueError(f"Value {value} out of range for {field_name}")
    return values


@dataclass
class CronSpec:
    minute: Set[int]
    hour: Set[int]
    day_of_month: Set[int]
    month: Set[int]
    day_of_week: Set[int]
    raw: str

    @property
    def month_wildcard(self) -> bool:
        return self.month == set(range(1, 13))

    @property
    def dom_wildcard(self) -> bool:
        return self.day_of_month == set(range(1, 32))

    @property
    def dow_wildcard(self) -> bool:
        return self.day_of_week == set(range(0, 7))


def parse_cron(expr: str) -> CronSpec:
    expr = expr.strip()
    if not expr or expr.startswith("#"):
        raise ValueError("Empty expression")
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5 cron fields, got {len(parts)}: '{expr}'")
    minute, hour, dom, month, dow = parts

    spec = CronSpec(
        minute=parse_field("minute", minute),
        hour=parse_field("hour", hour),
        day_of_month=parse_field("day_of_month", dom),
        month=parse_field("month", month),
        day_of_week=parse_field("day_of_week", dow),
        raw=expr,
    )
    return spec


def matches(spec: CronSpec, dt: datetime) -> bool:
    month_ok = dt.month in spec.month
    if not month_ok:
        return False

    day_match = dt.day in spec.day_of_month
    dow_match = dt.weekday() in spec.day_of_week

    # Standard cron semantics: if both DOM and DOW are restricted, either one may match.
    if not spec.dom_wildcard and not spec.dow_wildcard:
        dom_or_dow = day_match or dow_match
    elif spec.dom_wildcard and spec.dow_wildcard:
        dom_or_dow = True
    elif spec.dom_wildcard:
        dom_or_dow = True
    elif spec.dow_wildcard:
        dom_or_dow = True
    else:
        dom_or_dow = True

    return dt.minute in spec.minute and dt.hour in spec.hour and dom_or_dow and month_ok


def explain_spec(spec: CronSpec) -> str:
    minute_desc = field_summary("minute", spec.minute, use_wildcard=(spec.minute == set(range(0, 60))))
    hour_desc = field_summary("hour", spec.hour, use_wildcard=(spec.hour == set(range(0, 24))))
    dom_desc = field_summary("day_of_month", spec.day_of_month, use_wildcard=spec.dom_wildcard)
    month_desc = field_summary("month", spec.month, names=MONTH_NAMES, use_wildcard=spec.month_wildcard)
    dow_desc = field_summary(
        "day_of_week",
        spec.day_of_week,
        names={0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"},
        use_wildcard=spec.dow_wildcard,
    )

    if not spec.dom_wildcard and not spec.dow_wildcard:
        dom_dow_note = (
            "Because both day-of-month and day-of-week are restricted, this job runs when either field matches."
        )
    elif spec.dom_wildcard and spec.dow_wildcard:
        dom_dow_note = "Day-of-month and day-of-week are both unrestricted."
    elif spec.dom_wildcard:
        dom_dow_note = "Day-of-month is unrestricted; day-of-week is the active filter."
    elif spec.dow_wildcard:
        dom_dow_note = "Day-of-week is unrestricted; day-of-month is the active filter."
    else:
        dom_dow_note = "Cron semantics apply."

    return f"Runs {minute_desc}, {hour_desc}, {dom_desc}, {month_desc}, {dow_desc}. {dom_dow_note}"


def iter_next_runs(
    spec: CronSpec, tz: ZoneInfo, after: Optional[datetime] = None, count: int = 5, max_steps: int = 1_000_000
) -> List[datetime]:
    if after is None:
        after = datetime.now(tz)
    after = after.astimezone(tz)
    # Start from the next minute boundary
    candidate = after.replace(second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(minutes=1)

    results: List[datetime] = []
    steps = 0
    while steps < max_steps and len(results) < count:
        if matches(spec, candidate):
            results.append(candidate)
        candidate += timedelta(minutes=1)
        steps += 1

    return results


def is_dst_quirky(dt: datetime, tz: ZoneInfo) -> bool:
    # Check if the wall time is representable after a round trip through UTC.
    # If not, it usually indicates a DST gap/overlap edge case.
    try:
        local = dt.replace(tzinfo=tz)
        round_trip = local.astimezone(tz)
        return round_trip.replace(tzinfo=None) != dt
    except Exception:
        return False


def dump_cron_file(path: str) -> List[str]:
    entries: List[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            # Ignore environment variables like FOO=bar
            if "=" in line and not re.match(r"^\\s*\\S+\\s+\\S+\\s+\\S+\\s+\\S+\\s+\\S+\\s+\\S+", line):
                continue
            # Strip inline comments
            line = line.split("#", 1)[0].strip()
            if line:
                entries.append(line)
    return entries


def detect_overlaps(specs: Sequence[CronSpec], tz: ZoneInfo, horizon_days: int = 30) -> List[dict]:
    now = datetime.now(tz)
    horizon_minutes = horizon_days * 24 * 60
    results: List[dict] = []

    occurrences_by_spec: List[Set[datetime]] = []
    for spec in specs:
        times = set()
        for dt in iter_next_runs(spec, tz, after=now, count=200, max_steps=horizon_minutes):
            times.add(dt)
        occurrences_by_spec.append(times)

    for i in range(len(specs)):
        for j in range(i + 1, len(specs)):
            match_times = occurrences_by_spec[i] & occurrences_by_spec[j]
            if match_times:
                results.append({
                    "spec_a": specs[i].raw,
                    "spec_b": specs[j].raw,
                    "shared_runs": sorted(dt.strftime("%Y-%m-%d %H:%M %Z") for dt in match_times),
                })
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explain cron expressions in plain English and estimate next runs.")
    parser.add_argument("expression", nargs="?", help="A single cron expression like '*/5 * * * *'")
    parser.add_argument("--file", help="Crontab file to parse instead of one expression")
    parser.add_argument("--tz", default="UTC", help="Timezone to evaluate scheduling in, e.g. Europe/London")
    parser.add_argument("--next", type=int, default=5, help="How many future run times to show")
    parser.add_argument(
        "--overlaps", action="store_true", help="Detect duplicate/overlapping schedules in a crontab file"
    )
    parser.add_argument("--dst-warnings", action="store_true", help="Call out times near DST transitions")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--horizon-days", type=int, default=30, help="Horizon used for overlap detection (days)")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        tz = ZoneInfo(args.tz)
    except Exception as exc:
        print(f"Invalid timezone '{args.tz}': {exc}", file=sys.stderr)
        return 2

    if args.file:
        lines = dump_cron_file(args.file)
        if not lines:
            print("No cron entries found in file.", file=sys.stderr)
            return 1
        specs = [parse_cron(line) for line in lines]
        if args.overlaps:
            overlaps = detect_overlaps(specs, tz, horizon_days=args.horizon_days)
            if args.json:
                print(json.dumps({"entries": [s.raw for s in specs], "overlaps": overlaps}, indent=2))
            else:
                print(f"Loaded {len(specs)} cron entries from {args.file}")
                if not overlaps:
                    print("No duplicate/overlapping schedules found in the next horizon.")
                else:
                    print("Duplicate/overlapping schedules found:")
                    for item in overlaps:
                        print(f"  - {item['spec_a']} overlaps {item['spec_b']} at: {', '.join(item['shared_runs'])}")
            return 0

        if args.dst_warnings:
            now = datetime.now(tz)
            for idx, spec in enumerate(specs, 1):
                print(f"[{idx}] {spec.raw}")
                print("    " + explain_spec(spec))
                runs = iter_next_runs(spec, tz, after=now, count=args.next)
                for run in runs:
                    if is_dst_quirky(run, tz):
                        print(
                            f"    DST warning: {run.strftime('%Y-%m-%d %H:%M %Z')} may be skipped or repeated due to DST transition."
                        )
                print("    Next runs:")
                for run in runs:
                    print(f"      - {run.strftime('%Y-%m-%d %H:%M %Z')}")
            return 0

        if args.json:
            payload = [
                {
                    "expression": s.raw,
                    "explanation": explain_spec(s),
                    "next_runs": [
                        dt.strftime("%Y-%m-%d %H:%M %Z")
                        for dt in iter_next_runs(s, tz, after=datetime.now(tz), count=args.next)
                    ],
                }
                for s in specs
            ]
            print(json.dumps(payload, indent=2))
        else:
            for idx, spec in enumerate(specs, 1):
                print(f"[{idx}] {spec.raw}")
                print("    " + explain_spec(spec))
                print("    Next runs:")
                for dt in iter_next_runs(spec, tz, after=datetime.now(tz), count=args.next):
                    print(f"      - {dt.strftime('%Y-%m-%d %H:%M %Z')}")
        return 0

    if not args.expression:
        parser.error("You must supply a cron expression or --file")
    try:
        spec = parse_cron(args.expression)
    except ValueError as exc:
        print(f"Invalid cron expression: {exc}", file=sys.stderr)
        return 2

    now = datetime.now(tz)
    next_runs = iter_next_runs(spec, tz, after=now, count=args.next)

    if args.json:
        payload = {
            "expression": spec.raw,
            "timezone": args.tz,
            "explanation": explain_spec(spec),
            "next_runs": [dt.strftime("%Y-%m-%d %H:%M %Z") for dt in next_runs],
            "dst_warnings": [
                {"time": dt.strftime("%Y-%m-%d %H:%M %Z"), "warning": "Potential DST edge case"}
                for dt in next_runs
                if is_dst_quirky(dt, tz)
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"Expression: {spec.raw}")
        print(f"Timezone: {args.tz}")
        print("Explanation: " + explain_spec(spec))
        print("Next runs:")
        if not next_runs:
            print("  (no future matches found in the search window)")
        else:
            for dt in next_runs:
                if args.dst_warnings and is_dst_quirky(dt, tz):
                    print(f"  - {dt.strftime('%Y-%m-%d %H:%M %Z')}  [DST warning: may skip or repeat]")
                else:
                    print(f"  - {dt.strftime('%Y-%m-%d %H:%M %Z')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
