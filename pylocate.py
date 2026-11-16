#!/data/data/com.termux/files/usr/bin/env python
"""
locate.py — a `locate` clone backed by SQLite FTS5 with the trigram tokenizer.

Requires Python >= 3.9 and SQLite >= 3.34.0 (FTS5 + trigram).
Tested on Termux with Python 3.14.

  locate.py updatedb  [-d DB] [-r ROOT]... [--prune DIR]... [--stats] [-q]
  locate.py           [-d DB] [-i] [-b] [-r] [-l N] [-c] [-0] [-e] PATTERN...

Env:
  LOCATE_DB     default database path
  LOCATE_ROOTS  shell-quoted root list for `updatedb`
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import signal
import sqlite3
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------- config

DEFAULT_PRUNE = (
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/var/run",
    "/tmp",
    "/var/tmp",
    "/var/cache",
)

# The trigram tokenizer is case-sensitive so that a case-sensitive LIKE
# (with PRAGMA case_sensitive_like=ON) can be answered from the index.
# If you need to save disk space and only ever search case-sensitively,
# add `detail=none` to the tokenize string below — it drops positions
# that a substring index does not need.
SCHEMA_SQL = """
CREATE TABLE files (
    id   INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE
);
CREATE VIRTUAL TABLE files_fts USING fts5(
    path,
    content       = 'files',
    content_rowid = 'id',
    tokenize      = 'trigram case_sensitive 1'
);
"""

# ----------------------------------------------------------------------- util


def _check_sqlite() -> None:
    if sqlite3.sqlite_version_info < (3, 34, 0):
        sys.exit(f"locate: SQLite {sqlite3.sqlite_version} is too old; need >= 3.34.0 for the FTS5 trigram tokenizer")


def _default_db() -> str:
    env = os.environ.get("LOCATE_DB")
    if env:
        return os.path.expanduser(env)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = xdg if xdg else os.path.expanduser("~/.local/share")
    return os.path.join(base, "locate", "locatedb.sqlite")


# --------------------------------------------------------------------- updatedb


def _pick_roots(cli_roots: list[str]) -> list[str]:
    if cli_roots:
        raw = list(cli_roots)
    else:
        env = os.environ.get("LOCATE_ROOTS")
        if env:
            raw = shlex.split(env)
        else:
            raw = [os.path.expanduser("~")]
            prefix = os.environ.get("PREFIX")  # Termux: .../files/usr
            if prefix:
                raw.append(prefix)
            for extra in ("/sdcard", "/storage/emulated/0"):
                if os.path.isdir(extra):
                    raw.append(extra)

    candidates: list[str] = []
    for r in raw:
        r = os.path.abspath(os.path.expanduser(r))
        if os.path.isdir(r):
            candidates.append(r)
        else:
            print(f"locate: skipping non-directory root: {r}", file=sys.stderr)

    # Drop roots that live inside another root (e.g. $HOME inside $PREFIX).
    candidates.sort(key=len)
    roots: list[str] = []
    for r in candidates:
        if not any(r == x or r.startswith(x + os.sep) for x in roots):
            roots.append(r)

    if not roots:
        sys.exit("locate: no usable roots to index")
    return roots


def _walk(roots: list[str], prune: frozenset[str]):
    """Yield every path under `roots`; never follow symlinked dirs."""
    for r in roots:
        yield r
    stack = list(roots)
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                p = e.path
                yield p
                try:
                    if e.is_dir(follow_symlinks=False) and p not in prune:
                        stack.append(p)
                except OSError:
                    pass


def cmd_updatedb(args) -> None:
    _check_sqlite()

    db = Path(args.db).expanduser()
    db.parent.mkdir(parents=True, exist_ok=True)

    tmp = db.with_name(db.name + ".tmp")
    for side in ("", "-wal", "-shm", "-journal"):
        try:
            Path(str(tmp) + side).unlink()
        except FileNotFoundError:
            pass

    roots = _pick_roots(args.roots)
    prune = frozenset(DEFAULT_PRUNE) | frozenset(os.path.abspath(os.path.expanduser(p)) for p in args.prune)

    t0 = time.monotonic()
    conn = sqlite3.connect(str(tmp))
    try:
        conn.executescript(
            "PRAGMA journal_mode=MEMORY;"  # no side files; temp DB is disposable
            "PRAGMA synchronous=OFF;"
            "PRAGMA temp_store=MEMORY;"
            "PRAGMA cache_size=-131072;"  # ~128 MB page cache
        )
        conn.executescript(SCHEMA_SQL)

        insert_sql = "INSERT OR IGNORE INTO files(path) VALUES (?)"
        n = 0
        batch: list[tuple[str]] = []

        def _flush() -> None:
            nonlocal n
            if not batch:
                return
            try:
                conn.executemany(insert_sql, batch)
                n += len(batch)
            except UnicodeEncodeError:
                # A filename is not valid UTF-8.  Retry one at a time so
                # a single bad name does not lose the whole batch.
                for row in batch:
                    try:
                        conn.execute(insert_sql, row)
                        n += 1
                    except sqlite3.Error:
                        pass
            batch.clear()

        for p in _walk(roots, prune):
            batch.append((p,))
            if len(batch) >= 4096:
                _flush()
        _flush()
        conn.commit()

        # Bulk-build the FTS index (much faster than per-row triggers).
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('optimize')")
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp, db)  # atomic swap
    elapsed = time.monotonic() - t0

    if not args.quiet:
        size = db.stat().st_size
        print(
            f"locate: indexed {n:,} paths from {len(roots)} root(s) in {elapsed:.1f}s, db size {size / 1e6:.1f} MB",
            file=sys.stderr,
        )


# ------------------------------------------------------------------------ locate


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _make_matchers(args):
    if args.regex:
        flags = re.IGNORECASE if args.ignore_case else 0
        return [re.compile(p, flags) for p in args.patterns]
    if args.ignore_case:
        return [p.casefold() for p in args.patterns]
    return list(args.patterns)


def _path_matches(path: str, matchers, args) -> bool:
    target = os.path.basename(path) if args.basename else path
    if args.regex:
        return any(m.search(target) for m in matchers)
    if args.ignore_case:
        target = target.casefold()
        return any(m in target for m in matchers)
    return any(m in target for m in matchers)


def _iter_candidates(conn: sqlite3.Connection, args):
    if args.regex or args.ignore_case:
        # The trigram index only accelerates *case-sensitive* substring
        # lookups.  Correctness over speed: scan `files` and filter in Python.
        yield from (row[0] for row in conn.execute("SELECT path FROM files"))
        return

    # Case-sensitive substring: hand the LIKE to FTS5.  With >=3 literal
    # characters, the trigram tokenizer answers it from the index.
    placeholders = " OR ".join(["path LIKE ? ESCAPE '\\'"] * len(args.patterns))
    params = ["%" + _escape_like(p) + "%" for p in args.patterns]
    sql = f"SELECT path FROM files_fts WHERE {placeholders}"
    yield from (row[0] for row in conn.execute(sql, params))


def cmd_locate(args) -> None:
    _check_sqlite()
    db = os.path.expanduser(args.db)
    if not os.path.isfile(db):
        prog = Path(sys.argv[0]).name
        sys.exit(f"locate: database not found: {db} (run `{prog} updatedb` first)")

    try:
        matchers = _make_matchers(args)
    except re.error as exc:
        sys.exit(f"locate: invalid regular expression: {exc}")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        # Must agree with `case_sensitive 1` in the tokenizer, or SQLite
        # will refuse to use the trigram index for LIKE.
        conn.execute("PRAGMA case_sensitive_like=ON")
        conn.execute("PRAGMA query_only=ON")

        out = sys.stdout.buffer if args.null else sys.stdout
        emitted = 0
        count = 0

        for path in _iter_candidates(conn, args):
            if not _path_matches(path, matchers, args):
                continue
            if args.existing and not os.path.lexists(path):
                continue
            if args.count:
                count += 1
                continue
            if args.limit is not None and emitted >= args.limit:
                break
            if args.null:
                out.write(os.fsencode(path) + b"\0")
            else:
                out.write(path + "\n")
            emitted += 1

        if args.count:
            print(count)
    finally:
        conn.close()


# ------------------------------------------------------------------------ CLI


def _add_db_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("-d", "--db", default=_default_db(), help="database file (default: %(default)s)")


def _parse_updatedb(argv):
    p = argparse.ArgumentParser(
        prog="locate.py updatedb",
        description="Build or rebuild the file-name database.",
    )
    _add_db_arg(p)
    p.add_argument(
        "-r", "--root", dest="roots", action="append", default=[], metavar="DIR", help="directory to index (repeatable)"
    )
    p.add_argument("--prune", action="append", default=[], metavar="DIR", help="directory to skip (repeatable)")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress the summary line")
    return p.parse_args(argv)


def _parse_locate(argv):
    p = argparse.ArgumentParser(
        prog="locate.py",
        description="Find files by name using a SQLite FTS5 trigram index.",
    )
    _add_db_arg(p)
    p.add_argument("-i", "--ignore-case", action="store_true")
    p.add_argument("-b", "--basename", action="store_true", help="match against the basename only")
    p.add_argument("-r", "--regex", action="store_true", help="PATTERN is a Python regex (full scan)")
    p.add_argument("-l", "--limit", type=int, default=None, metavar="N", help="stop after N matches")
    p.add_argument("-c", "--count", action="store_true", help="print the number of matches instead of the paths")
    p.add_argument("-0", "--null", action="store_true", help="separate results with NUL instead of newline")
    p.add_argument("-e", "--existing", action="store_true", help="only print paths that currently exist")
    p.add_argument("patterns", nargs="+", metavar="PATTERN")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Behave like a normal Unix tool when piped into `head`.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in ("updatedb", "update"):
        cmd_updatedb(_parse_updatedb(argv[1:]))
    elif argv and argv[0] == "locate":
        cmd_locate(_parse_locate(argv[1:]))
    else:
        cmd_locate(_parse_locate(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
