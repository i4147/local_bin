#!/data/data/com.termux/files/usr/bin/env python
"""pip-search: fast substring search over PyPI package names.

Placate-style minimal SQLite DB (stdlib only):
- Plain WITHOUT ROWID table, no FTS5 trigram index (trigram index is
  ~5-10x the table size; a LIKE full-scan over ~500k short names is
  ~20ms in C and far smaller/faster to build).
- name TEXT PRIMARY KEY: names stored once, sorted, deduped for free.
- Counts as compact INTEGERs; ranking done in SQL (ORDER BY + LIMIT),
  no Python heapq over all matches.
- Build with small page_size, journal/sync off, sorted inserts, VACUUM.
- PEP 503 normalization on write and search (case + -_. equivalent).
- Old 3-column FTS5 DBs are auto-migrated on first search/--optimize.
"""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import urllib.error
import urllib.request


DB_PATH: Path = Path(os.environ.get("PIP_DB") or "/sdcard/data/pip.db")
DEFAULT_LIMIT: int = 20
DEFAULT_TABLE: str = "packages"
SIMPLE_API_URL: str = "https://pypi.org/simple/"
_PEP503_RE: re.Pattern[str] = re.compile(r"[-_.]+")


def normalize_name(name: str) -> str:
    """PEP 503 normalize: lowercase, runs of -_. -> single -."""
    return _PEP503_RE.sub("-", name.strip().lower())


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def connect_db(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    if readonly:
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("PRAGMA cache_size=-16000")
        con.execute("PRAGMA mmap_size=268435456")
    else:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("PRAGMA cache_size=-64000")
        con.execute("PRAGMA mmap_size=268435456")
    return con


def detect_table(con: sqlite3.Connection) -> tuple[str, str, str, str, bool] | None:
    """Return (table, name_col, month_col, all_col, is_fts5) or None."""
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (DEFAULT_TABLE,),
    ).fetchone()
    if row is not None:
        cols = con.execute(f'PRAGMA table_info("{DEFAULT_TABLE}")').fetchall()
        if len(cols) == 3:
            is_fts = bool(row[0] and "fts5" in str(row[0]).lower())
            return (
                DEFAULT_TABLE,
                str(cols[0][1]),
                str(cols[1][1]),
                str(cols[2][1]),
                is_fts,
            )
    rows = con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    for name, sql in rows:
        t = str(name)
        if t.endswith(("_data", "_idx", "_docsize", "_config")):
            continue
        cols = con.execute(f'PRAGMA table_info("{t}")').fetchall()
        if len(cols) == 3:
            is_fts = bool(sql and "fts5" in str(sql).lower())
            return t, str(cols[0][1]), str(cols[1][1]), str(cols[2][1]), is_fts
    return None


def migrate_if_needed(con: sqlite3.Connection) -> None:
    """Auto-migrate old 3-col (FTS5) schema to compact WITHOUT ROWID schema."""
    found = detect_table(con)
    if found is None:
        return
    table, name_col, month_col, all_col, is_fts = found
    if (
        table == DEFAULT_TABLE
        and name_col == "name"
        and month_col == "monthly"
        and all_col == "all_time"
        and not is_fts
    ):
        return
    print(
        "Migrating old schema to compact format (run -u for full rebuild)...",
        file=sys.stderr,
    )
    con.execute("PRAGMA page_size=1024")
    q_table = f'"{table}"'
    rows = con.execute(f"SELECT {name_col}, {month_col}, {all_col} FROM {q_table}").fetchall()
    best: dict[str, tuple[int, int]] = {}
    for r in rows:
        n = normalize_name(str(r[0]))
        if not n:
            continue
        try:
            m = int(r[1])
        except (TypeError, ValueError):
            m = 0
        try:
            a = int(r[2])
        except (TypeError, ValueError):
            a = 0
        prev = best.get(n)
        if prev is None or a > prev[1]:
            best[n] = (m, a)
    records = sorted((n, m, a) for n, (m, a) in best.items())
    con.execute(f"DROP TABLE IF EXISTS {q_table}")
    for suffix in ("_data", "_idx", "_docsize", "_config"):
        con.execute(f'DROP TABLE IF EXISTS "{table}{suffix}"')
    con.execute(
        "CREATE TABLE packages (name TEXT PRIMARY KEY, monthly INTEGER NOT NULL DEFAULT 0, "
        "all_time INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID"
    )
    con.executemany("INSERT INTO packages (name, monthly, all_time) VALUES (?, ?, ?)", records)
    con.commit()
    con.execute("VACUUM")


def resolve_json_path() -> Path:
    """Resolve json path."""
    candidates: tuple[Path, ...] = (
        Path("packages.json"),
        Path("pip.json"),
        DB_PATH.parent / "packages.json",
        DB_PATH.parent / "pip.json",
    )
    for p in candidates:
        if p.is_file():
            return p
    user_input: str = input("Neither packages.json nor pip.json found. Enter JSON file path: ").strip()
    if not user_input:
        sys.exit("No JSON file path provided.")
    p = Path(user_input.strip("\"'")).expanduser()
    if not p.is_file():
        sys.exit(f"File not found: {p}")
    return p


def load_json_records(json_path: Path) -> list[tuple[str, int, int]]:
    """Load [[name, monthly, all_time], ...], normalized + deduped (max all_time wins)."""
    try:
        with json_path.open("r", encoding="utf-8") as f:
            data: object = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"Failed to read JSON file: {e}")
    if not isinstance(data, list):
        sys.exit("Invalid JSON format: expected a top-level list.")
    best: dict[str, tuple[int, int]] = {}
    for item in data:
        if not isinstance(item, list) or len(item) < 3:
            continue
        name = normalize_name(str(item[0]))
        if not name:
            continue
        try:
            monthly = int(item[1])
        except (TypeError, ValueError):
            monthly = 0
        try:
            all_time = int(item[2])
        except (TypeError, ValueError):
            all_time = 0
        prev = best.get(name)
        if prev is None or all_time > prev[1]:
            best[name] = (monthly, all_time)
    return [(n, m, a) for n, (m, a) in best.items()]


def fetch_online_names(timeout: int = 30) -> list[str]:
    """Fetch all package names from the PyPI Simple API (JSON). Counts unavailable."""
    req = urllib.request.Request(
        SIMPLE_API_URL,
        headers={
            "Accept": "application/vnd.pypi.simple.v1+json",
            "User-Agent": "pip-search/1.0 (stdlib)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data: object = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        sys.exit(f"Online fetch failed: {e}")
    if not isinstance(data, dict):
        sys.exit("Online fetch failed: unexpected response.")
    pkgs = data.get("packages", [])
    names: set[str] = set()
    if isinstance(pkgs, list):
        for p in pkgs:
            if isinstance(p, dict):
                n = normalize_name(str(p.get("name", "")))
                if n:
                    names.add(n)
    return sorted(names)


def build_database(con: sqlite3.Connection, records: list[tuple[str, int, int]]) -> None:
    """Build database."""
    con.execute("PRAGMA page_size=1024")
    found = detect_table(con)
    if found is not None:
        old_table = found[0]
        con.execute(f'DROP TABLE IF EXISTS "{old_table}"')
        for suffix in ("_data", "_idx", "_docsize", "_config"):
            con.execute(f'DROP TABLE IF EXISTS "{old_table}{suffix}"')
    con.execute(f"DROP TABLE IF EXISTS {DEFAULT_TABLE}")
    for suffix in ("_data", "_idx", "_docsize", "_config"):
        con.execute(f'DROP TABLE IF EXISTS "{DEFAULT_TABLE}{suffix}"')
    con.execute(
        "CREATE TABLE packages (name TEXT PRIMARY KEY, monthly INTEGER NOT NULL DEFAULT 0, "
        "all_time INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID"
    )
    records.sort(key=lambda r: r[0])
    con.executemany("INSERT INTO packages (name, monthly, all_time) VALUES (?, ?, ?)", records)
    con.commit()
    con.execute("VACUUM")
    con.execute("PRAGMA optimize")


def update_database(con: sqlite3.Connection, records: list[tuple[str, int, int]]) -> None:
    """Update database."""
    print(f"Updating database with {len(records):,} packages...", file=sys.stderr)
    build_database(con, records)
    print("Database updated successfully.")


def _where_clause(
    keyword: str,
    *,
    exact: bool,
    prefix: bool,
    nonzero_only: bool,
    min_downloads: int,
) -> tuple[str, list[str | int]]:
    kw = normalize_name(keyword)
    clauses: list[str] = []
    params: list[str | int] = []
    if exact:
        clauses.append("name = ?")
        params.append(kw)
    elif prefix:
        clauses.append("name LIKE ? ESCAPE '\\'")
        params.append(_escape_like(kw) + "%")
    else:
        clauses.append("name LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(kw)}%")
    if nonzero_only:
        clauses.append("(monthly != 0 OR all_time != 0)")
    if min_downloads > 0:
        clauses.append("all_time >= ?")
        params.append(min_downloads)
    return " AND ".join(clauses), params


def search_db(
    con: sqlite3.Connection,
    keyword: str,
    limit: int,
    *,
    nonzero_only: bool = False,
    exact: bool = False,
    prefix: bool = False,
    min_downloads: int = 0,
    sort: str = "downloads",
) -> list[tuple[str, int, int]]:
    """Search for db."""
    if not normalize_name(keyword):
        return []
    where, params = _where_clause(
        keyword,
        exact=exact,
        prefix=prefix,
        nonzero_only=nonzero_only,
        min_downloads=min_downloads,
    )
    order = "name ASC" if sort == "name" else "all_time DESC, name ASC"
    sql = f"SELECT name, monthly, all_time FROM packages WHERE {where} ORDER BY {order} LIMIT ?"
    params.append(limit)
    return [(str(r[0]), int(r[1]), int(r[2])) for r in con.execute(sql, params)]


def count_db(
    con: sqlite3.Connection,
    keyword: str,
    *,
    nonzero_only: bool = False,
    exact: bool = False,
    prefix: bool = False,
    min_downloads: int = 0,
) -> int:
    """Count db."""
    if not normalize_name(keyword):
        return 0
    where, params = _where_clause(
        keyword,
        exact=exact,
        prefix=prefix,
        nonzero_only=nonzero_only,
        min_downloads=min_downloads,
    )
    row = con.execute(f"SELECT COUNT(*) FROM packages WHERE {where}", params).fetchone()
    return int(row[0]) if row else 0


def search_list(
    records: list[tuple[str, int, int]],
    keyword: str,
    limit: int,
    *,
    nonzero_only: bool = False,
    exact: bool = False,
    prefix: bool = False,
    min_downloads: int = 0,
    sort: str = "downloads",
) -> list[tuple[str, int, int]]:
    """Search for list."""
    kw = normalize_name(keyword)
    if not kw:
        return []
    out: list[tuple[str, int, int]] = []
    for name, monthly, all_time in records:
        if nonzero_only and monthly == 0 and all_time == 0:
            continue
        if all_time < min_downloads:
            continue
        if exact:
            if name != kw:
                continue
        elif prefix:
            if not name.startswith(kw):
                continue
        elif kw not in name:
            continue
        out.append((name, monthly, all_time))
    if sort == "name":
        out.sort(key=lambda r: r[0])
    else:
        out.sort(key=lambda r: (-r[2], r[0]))
    return out[:limit]


def print_db_stats(con: sqlite3.Connection, db_path: Path, table: str) -> None:
    """Print db stats."""
    row = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    count = int(row[0]) if row else 0
    size = db_path.stat().st_size if db_path.is_file() else 0
    page_count = int(con.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(con.execute("PRAGMA page_size").fetchone()[0])
    print(f"Database: {db_path}")
    print(f"Packages: {count:,}")
    print(f"Size: {size:,} bytes ({size / 1048576:.1f} MB)")
    print(f"Page size: {page_size}, Pages: {page_count:,}")


def optimize_database(con: sqlite3.Connection, db_path: Path) -> None:
    before = db_path.stat().st_size if db_path.is_file() else 0
    con.commit()
    con.execute("VACUUM")
    con.execute("PRAGMA optimize")
    after = db_path.stat().st_size if db_path.is_file() else 0
    print(f"Optimized {db_path}: {before:,} -> {after:,} bytes")


def emit_count(count: int, *, json_output: bool) -> None:
    """Emit count."""
    if json_output:
        print(json.dumps({"count": count}))
    else:
        print(count)


def emit_rows(rows: list[tuple[str, int, int]], keyword: str, *, json_output: bool) -> None:
    """Emit rows."""
    if json_output:
        print(json.dumps([{"name": n, "monthly": m, "all_time": a} for n, m, a in rows], indent=2))
        return
    if not rows:
        print(f"No matches for {keyword}.")
        return
    for name, monthly, all_time in rows:
        print(f"{name} {monthly} {all_time}")


def main() -> int:
    """Run the command-line interface."""
    ap = argparse.ArgumentParser(
        description="Search PyPI packages by substring (compact SQLite, placate-style).",
        epilog="Examples: pip-search django | pip-search --exact django | "
        "pip-search --prefix dja | pip-search --online django | pip-search -u | pip-search --stats",
    )
    ap.add_argument(
        "keyword",
        nargs="?",
        help="substring to search for (case-insensitive, PEP 503 normalized)",
    )
    ap.add_argument(
        "-n",
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"max results (default: {DEFAULT_LIMIT})",
    )
    ap.add_argument(
        "-z",
        "--nonzero",
        action="store_true",
        help="exclude packages with 0 monthly and 0 all-time downloads",
    )
    ap.add_argument(
        "-u",
        "--updatedb",
        "--update",
        action="store_true",
        help="rebuild database from JSON (or online with --online)",
    )
    ap.add_argument(
        "--online",
        action="store_true",
        help="search online via PyPI Simple API (no local DB needed); with -u, rebuild DB from online names (counts = 0)",
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"database path (default: {DB_PATH}; env PIP_DB)",
    )
    ap.add_argument(
        "--json-file",
        type=Path,
        default=None,
        help="JSON input file for -u (default: auto-detect packages.json/pip.json)",
    )
    ap.add_argument("-e", "--exact", action="store_true", help="exact name match only")
    ap.add_argument("--prefix", action="store_true", help="prefix match only")
    ap.add_argument(
        "--min-downloads",
        type=int,
        default=0,
        help="only packages with all-time downloads >= N",
    )
    ap.add_argument(
        "--sort",
        choices=["downloads", "name"],
        default="downloads",
        help="sort order (default: downloads)",
    )
    ap.add_argument("--count", action="store_true", help="print match count only")
    ap.add_argument("--json", action="store_true", dest="json_output", help="output as JSON")
    ap.add_argument("--stats", action="store_true", help="show database stats and exit")
    ap.add_argument(
        "--optimize",
        action="store_true",
        help="VACUUM database to reclaim space and exit",
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="online fetch timeout in seconds (default: 30)",
    )
    args = ap.parse_args()
    if args.limit < 0:
        ap.error("--limit must be >= 0")
    if args.min_downloads < 0:
        ap.error("--min-downloads must be >= 0")
    if args.exact and args.prefix:
        ap.error("--exact and --prefix are mutually exclusive")
    if args.timeout <= 0:
        ap.error("--timeout must be > 0")
    db_path: Path = args.db

    if args.stats:
        if not db_path.is_file():
            sys.exit(f"Database not found: {db_path}")
        con = connect_db(db_path, readonly=True)
        try:
            found = detect_table(con)
            if found is None:
                sys.exit("No package table found in database.")
            print_db_stats(con, db_path, found[0])
        finally:
            con.close()
        return 0

    if args.optimize:
        if not db_path.is_file():
            sys.exit(f"Database not found: {db_path}")
        con = connect_db(db_path, readonly=False)
        try:
            migrate_if_needed(con)
            optimize_database(con, db_path)
        finally:
            con.close()
        return 0

    if args.updatedb:
        if args.online:
            print("Fetching package names from PyPI (online)...", file=sys.stderr)
            names = fetch_online_names(timeout=args.timeout)
            records = [(n, 0, 0) for n in names]
            if not records:
                sys.exit("No packages fetched online.")
            print("Note: online rebuild has no download counts (all 0).", file=sys.stderr)
        else:
            if args.json_file is not None:
                json_path = args.json_file
                if not json_path.is_file():
                    sys.exit(f"File not found: {json_path}")
            else:
                json_path = resolve_json_path()
            print(f"Reading {json_path}...", file=sys.stderr)
            records = load_json_records(json_path)
            if not records:
                sys.exit("No valid package records found in JSON.")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = connect_db(db_path, readonly=False)
        try:
            update_database(con, records)
        finally:
            con.close()
        return 0

    keyword: str | None = args.keyword
    if not keyword:
        keyword = input("Search package: ").strip()
        if not keyword:
            print("No keyword given.")
            return 0

    if args.online:
        if args.nonzero or args.min_downloads > 0:
            print(
                "Note: online results have no download counts; -z/--min-downloads will filter everything out.",
                file=sys.stderr,
            )
        print("Searching online (PyPI Simple API)...", file=sys.stderr)
        names = fetch_online_names(timeout=args.timeout)
        records = [(n, 0, 0) for n in names]
        if args.count:
            all_rows = search_list(
                records,
                keyword,
                sys.maxsize,
                nonzero_only=args.nonzero,
                exact=args.exact,
                prefix=args.prefix,
                min_downloads=args.min_downloads,
                sort=args.sort,
            )
            emit_count(len(all_rows), json_output=args.json_output)
        else:
            rows = search_list(
                records,
                keyword,
                args.limit,
                nonzero_only=args.nonzero,
                exact=args.exact,
                prefix=args.prefix,
                min_downloads=args.min_downloads,
                sort=args.sort,
            )
            emit_rows(rows, keyword, json_output=args.json_output)
        return 0

    if db_path.is_file():
        con = connect_db(db_path, readonly=True)
        try:
            found = detect_table(con)
            if found is None:
                sys.exit("No package table found in database. Run with -u to initialize it.")
            table, name_col, month_col, all_col, is_fts = found
            needs_migrate = not (
                table == DEFAULT_TABLE
                and name_col == "name"
                and month_col == "monthly"
                and all_col == "all_time"
                and not is_fts
            )
            if needs_migrate:
                con.close()
                con = connect_db(db_path, readonly=False)
                migrate_if_needed(con)
                con.close()
                con = connect_db(db_path, readonly=True)
            if args.count:
                n = count_db(
                    con,
                    keyword,
                    nonzero_only=args.nonzero,
                    exact=args.exact,
                    prefix=args.prefix,
                    min_downloads=args.min_downloads,
                )
                emit_count(n, json_output=args.json_output)
            else:
                rows = search_db(
                    con,
                    keyword,
                    args.limit,
                    nonzero_only=args.nonzero,
                    exact=args.exact,
                    prefix=args.prefix,
                    min_downloads=args.min_downloads,
                    sort=args.sort,
                )
                emit_rows(rows, keyword, json_output=args.json_output)
        finally:
            con.close()
        return 0

    fallback_candidates: list[Path] = []
    if args.json_file is not None:
        fallback_candidates.append(args.json_file)
    fallback_candidates.append(db_path.parent / "pip.json")
    fallback_candidates.append(db_path.parent / "packages.json")
    fallback_json: Path | None = next((p for p in fallback_candidates if p.is_file()), None)
    if fallback_json is not None:
        print(f"DB not found, searching {fallback_json}...", file=sys.stderr)
        records = load_json_records(fallback_json)
        if args.count:
            all_rows = search_list(
                records,
                keyword,
                sys.maxsize,
                nonzero_only=args.nonzero,
                exact=args.exact,
                prefix=args.prefix,
                min_downloads=args.min_downloads,
                sort=args.sort,
            )
            emit_count(len(all_rows), json_output=args.json_output)
        else:
            rows = search_list(
                records,
                keyword,
                args.limit,
                nonzero_only=args.nonzero,
                exact=args.exact,
                prefix=args.prefix,
                min_downloads=args.min_downloads,
                sort=args.sort,
            )
            emit_rows(rows, keyword, json_output=args.json_output)
        return 0

    sys.exit(f"Index not found: {db_path} (and no fallback JSON). Run with -u to create it, or use --online.")


if __name__ == "__main__":
    raise SystemExit(main())
