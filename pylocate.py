#!/data/data/com.termux/files/usr/bin/env python
"""Build and search a compressed file-name database, mlocate-style.

CLI entry point with separate build and search parsers; indexing stores paths in
SQLite for later pattern queries."""

from __future__ import annotations
import argparse
from collections.abc import Callable, Iterator
import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import signal
import sqlite3
import struct
import sys
import time
import zlib
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


DEFAULT_PRUNE: frozenset[str] = frozenset((
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/var/run",
    "/tmp",
    "/var/tmp",
    "/var/cache",
))

ZLIB_LEVEL: int = 9
_HEADER_STRUCT: struct.Struct = struct.Struct("<II")  # (path_count, payload_len)
_ENTRY_STRUCT: struct.Struct = struct.Struct("<HH")  # (prefix_len, suffix_len)

SCHEMA_SQL: str = """
PRAGMA page_size = 4096;
PRAGMA journal_mode = MEMORY;
PRAGMA synchronous = OFF;
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -65536;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE chunks (
    id   INTEGER PRIMARY KEY,
    data BLOB NOT NULL
);

CREATE TABLE chunk_filters (
    chunk_id INTEGER PRIMARY KEY,
    filter   BLOB NOT NULL
);
"""


@dataclass(frozen=True)
class MatcherConfig:
    """Store ignore_case, basename_only, use_regex, patterns."""

    ignore_case: bool
    basename_only: bool
    use_regex: bool
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class IndexTuning:
    """Store chunk_size, bloom_bytes."""

    chunk_size: int
    bloom_bytes: int

    @property
    def bloom_bits(self) -> int:
        """Return the bloom bits."""
        return self.bloom_bytes * 8


def _tune_for_path_count(total_paths: int) -> IndexTuning:
    """Select chunk_size and bloom_bytes dynamically based on total indexed paths."""
    if total_paths < 50_000:
        return IndexTuning(chunk_size=256, bloom_bytes=128)
    if total_paths <= 500_000:
        return IndexTuning(chunk_size=512, bloom_bytes=256)
    return IndexTuning(chunk_size=1024, bloom_bytes=512)


def _default_db() -> str:
    env = os.environ.get("LOCATE_DB")
    if env:
        return str(Path(env).expanduser())
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return str(base / "locate" / "locatedb.sqlite")


def _pick_roots(cli_roots: list[str]) -> list[str]:
    if cli_roots:
        raw = list(cli_roots)
    else:
        env = os.environ.get("LOCATE_ROOTS")
        if env:
            raw = shlex.split(env)
        else:
            raw = [str(Path.home())]
            prefix = os.environ.get("PREFIX")
            if prefix:
                raw.append(prefix)
            for extra in ("/sdcard", "/storage/emulated/0"):
                if Path(extra).is_dir():
                    raw.append(extra)

    candidates: list[str] = []
    for item in raw:
        normalized = Path(item).expanduser().absolute()
        if normalized.is_dir():
            candidates.append(str(normalized))
        else:
            print(f"locate: skipping non-directory root: {normalized}", file=sys.stderr)

    candidates.sort(key=len)
    roots: list[str] = []
    for candidate in candidates:
        if not any(candidate == existing or candidate.startswith(existing + os.sep) for existing in roots):
            roots.append(candidate)

    if not roots:
        sys.exit("locate: no usable roots to index")
    return roots


def _walk(roots: list[str], prune: frozenset[str]) -> Iterator[str]:
    yield from roots
    stack = list(roots)
    while stack:
        current_dir = stack.pop()
        try:
            entries = os.scandir(current_dir)
        except OSError:
            continue
        with entries:
            for entry in entries:
                path = entry.path
                yield path
                try:
                    if entry.is_dir(follow_symlinks=False) and path not in prune:
                        stack.append(path)
                except OSError:
                    pass


def _trigram_bits(trigram: bytes, bloom_bits: int) -> tuple[int, int]:
    """Map a 3-byte trigram to two bit indices in [0, bloom_bits)."""
    h1 = zlib.crc32(trigram) % bloom_bits
    h2 = zlib.adler32(trigram, 1) % bloom_bits
    if h2 == h1:
        h2 = (h2 + 1) % bloom_bits
    return h1, h2


def _set_bit(buf: bytearray, bit_idx: int) -> None:
    buf[bit_idx >> 3] |= 1 << (bit_idx & 7)


def _has_bit(buf: bytes | bytearray, bit_idx: int) -> bool:
    return bool(buf[bit_idx >> 3] & (1 << (bit_idx & 7)))


def _build_chunk_filter(paths: list[str], bloom_bytes: int) -> bytes:
    """Build a compressed chunk-level trigram Bloom filter across all paths in a chunk."""
    bloom_bits = bloom_bytes * 8
    bits = bytearray(bloom_bytes)
    for path in paths:
        variants = {os.fsencode(path), os.fsencode(path.casefold())}
        for raw in variants:
            if len(raw) < 3:
                continue
            for i in range(len(raw) - 2):
                b1, b2 = _trigram_bits(raw[i : i + 3], bloom_bits)
                _set_bit(bits, b1)
                _set_bit(bits, b2)
    return zlib.compress(bytes(bits), level=ZLIB_LEVEL)


def _encode_chunk(paths: list[str]) -> bytes:
    """Prefix-delta encode a sorted list of paths and compress with zlib."""
    raw_entries: list[tuple[int, bytes]] = []
    prev_bytes = b""
    for path in paths:
        cur_bytes = os.fsencode(path)
        max_prefix = min(len(prev_bytes), len(cur_bytes), 0xFFFF)
        prefix_len = 0
        while prefix_len < max_prefix and prev_bytes[prefix_len] == cur_bytes[prefix_len]:
            prefix_len += 1
        suffix = cur_bytes[prefix_len:]
        raw_entries.append((prefix_len, suffix))
        prev_bytes = cur_bytes

    payload = bytearray()
    for prefix_len, suffix in raw_entries:
        payload.extend(_ENTRY_STRUCT.pack(prefix_len, len(suffix)))
        payload.extend(suffix)

    header = _HEADER_STRUCT.pack(len(paths), len(payload))
    return zlib.compress(header + bytes(payload), level=ZLIB_LEVEL)


def _decode_chunk(blob: bytes) -> list[str]:
    """Decompress and reconstruct paths from a prefix-delta encoded chunk."""
    decompressed = zlib.decompress(blob)
    path_count, expected_payload_len = _HEADER_STRUCT.unpack_from(decompressed, 0)
    offset = _HEADER_STRUCT.size
    payload_end = offset + expected_payload_len
    if payload_end > len(decompressed):
        raise ValueError("corrupt chunk payload length")

    paths: list[str] = []
    prev_bytes = b""
    for _ in range(path_count):
        prefix_len, suffix_len = _ENTRY_STRUCT.unpack_from(decompressed, offset)
        offset += _ENTRY_STRUCT.size
        suffix = decompressed[offset : offset + suffix_len]
        offset += suffix_len
        cur_bytes = prev_bytes[:prefix_len] + suffix
        paths.append(os.fsdecode(cur_bytes))
        prev_bytes = cur_bytes

    return paths


def _read_bloom_bytes(conn: sqlite3.Connection) -> int | None:
    """Read the stored Bloom filter byte size from meta, or None if unavailable."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'bloom_bytes'").fetchone()
    if row is not None:
        try:
            val = int(row[0])
            return val if val > 0 else None
        except ValueError:
            return None
    bits_row = conn.execute("SELECT value FROM meta WHERE key = 'bloom_bits'").fetchone()
    if bits_row is not None:
        try:
            bits_val = int(bits_row[0])
            return (bits_val // 8) if bits_val >= 8 else None
        except ValueError:
            return None
    return None


def _pattern_trigram_sets(cfg: MatcherConfig, bloom_bits: int) -> list[list[tuple[int, int]]] | None:
    """Return trigram bit pairs per pattern if filter pruning is sound, else None."""
    if cfg.use_regex or not cfg.patterns:
        return None
    per_pattern: list[list[tuple[int, int]]] = []
    for pat in cfg.patterns:
        effective = pat.casefold() if cfg.ignore_case else pat
        raw = os.fsencode(effective)
        if len(raw) < 3:
            return None
        pairs = [_trigram_bits(raw[i : i + 3], bloom_bits) for i in range(len(raw) - 2)]
        per_pattern.append(pairs)
    return per_pattern


def _chunk_might_match(
    filter_blob: bytes,
    per_pattern: list[list[tuple[int, int]]],
    expected_bytes: int,
) -> bool:
    """Return True if at least one pattern's trigrams are all present in the chunk filter."""
    raw_filter = zlib.decompress(filter_blob)
    if len(raw_filter) != expected_bytes:
        return True
    for pairs in per_pattern:
        if all(_has_bit(raw_filter, b1) and _has_bit(raw_filter, b2) for b1, b2 in pairs):
            return True
    return False


def cmd_updatedb(args: argparse.Namespace) -> None:
    """Run the updatedb subcommand."""
    db_path = Path(args.db).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_name(f"{db_path.name}.tmp")

    for suffix in ("", "-wal", "-shm", "-journal"):
        with contextlib.suppress(FileNotFoundError):
            Path(f"{tmp_path}{suffix}").unlink()

    roots = _pick_roots(args.roots)
    extra_prune = (str(Path(p).expanduser().absolute()) for p in args.prune)
    prune = DEFAULT_PRUNE | frozenset(extra_prune)

    t0 = time.monotonic()
    seen: set[str] = set()
    for raw_path in _walk(roots, prune):
        try:
            os.fsencode(raw_path)
        except UnicodeEncodeError:
            continue
        seen.add(raw_path)

    sorted_paths = sorted(seen)
    total_paths = len(sorted_paths)
    seen.clear()

    tuning = _tune_for_path_count(total_paths)

    conn = sqlite3.connect(str(tmp_path))
    try:
        conn.executescript(SCHEMA_SQL)
        chunk_rows: list[tuple[bytes]] = []
        filter_rows: list[tuple[int, bytes]] = []

        chunk_id = 0
        for idx in range(0, total_paths, tuning.chunk_size):
            chunk_id += 1
            slice_paths = sorted_paths[idx : idx + tuning.chunk_size]
            chunk_rows.append((_encode_chunk(slice_paths),))
            filter_rows.append((chunk_id, _build_chunk_filter(slice_paths, tuning.bloom_bytes)))

        conn.executemany("INSERT INTO chunks(data) VALUES (?)", chunk_rows)
        conn.executemany("INSERT INTO chunk_filters(chunk_id, filter) VALUES (?, ?)", filter_rows)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("format", "plocate-lite-bloom-v2"),
                ("path_count", str(total_paths)),
                ("chunk_size", str(tuning.chunk_size)),
                ("bloom_bytes", str(tuning.bloom_bytes)),
                ("bloom_bits", str(tuning.bloom_bits)),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp_path, db_path)
    elapsed = time.monotonic() - t0

    if not args.quiet:
        size_bytes = db_path.stat().st_size
        print(
            f"locate: indexed {total_paths:,} paths from {len(roots)} root(s) in {elapsed:.1f}s "
            f"(chunk={tuning.chunk_size}, bloom={tuning.bloom_bytes}B), "
            f"db size {size_bytes / 1e6:.2f} MB",
            file=sys.stderr,
        )


def _build_predicate(cfg: MatcherConfig) -> Callable[[str], bool]:
    if cfg.use_regex:
        flags = re.IGNORECASE if cfg.ignore_case else 0
        compiled = [re.compile(pat, flags) for pat in cfg.patterns]

        def match_regex(path: str) -> bool:
            target = Path(path).name if cfg.basename_only else path
            return any(regex.search(target) is not None for regex in compiled)

        return match_regex

    needles = [pat.casefold() for pat in cfg.patterns] if cfg.ignore_case else list(cfg.patterns)

    def match_substring(path: str) -> bool:
        target = Path(path).name if cfg.basename_only else path
        if cfg.ignore_case:
            target = target.casefold()
        return any(needle in target for needle in needles)

    return match_substring


def _iter_candidate_paths(conn: sqlite3.Connection, cfg: MatcherConfig) -> Iterator[str]:
    bloom_bytes = _read_bloom_bytes(conn)
    per_pattern = _pattern_trigram_sets(cfg, bloom_bytes * 8) if bloom_bytes is not None else None
    if per_pattern is None or bloom_bytes is None:
        for (blob,) in conn.execute("SELECT data FROM chunks ORDER BY id"):
            yield from _decode_chunk(blob)
        return

    rows = conn.execute(
        """
        SELECT c.data, f.filter
        FROM chunks AS c
        JOIN chunk_filters AS f ON f.chunk_id = c.id
        ORDER BY c.id
        """
    )
    for blob, filter_blob in rows:
        if not _chunk_might_match(filter_blob, per_pattern, bloom_bytes):
            continue
        yield from _decode_chunk(blob)


def cmd_locate(args: argparse.Namespace) -> None:
    """Run the locate subcommand."""
    db_file = Path(args.db).expanduser()
    if not db_file.is_file():
        prog = Path(sys.argv[0]).name
        sys.exit(f"locate: database not found: {db_file} (run `{prog} updatedb` first)")

    cfg = MatcherConfig(
        ignore_case=bool(args.ignore_case),
        basename_only=bool(args.basename),
        use_regex=bool(args.regex),
        patterns=tuple(args.patterns),
    )

    try:
        predicate = _build_predicate(cfg)
    except re.error as exc:
        sys.exit(f"locate: invalid regular expression: {exc}")

    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        emitted = 0
        count = 0

        for path in _iter_candidate_paths(conn, cfg):
            if not predicate(path):
                continue
            if args.existing:
                p_obj = Path(path)
                if not (p_obj.exists() or p_obj.is_symlink()):
                    continue
            if args.count:
                count += 1
                continue
            if args.limit is not None and emitted >= args.limit:
                break

            if args.null:
                sys.stdout.buffer.write(os.fsencode(path) + b"\0")
            else:
                sys.stdout.write(f"{path}\n")
            emitted += 1

        if args.count:
            print(count)
    finally:
        conn.close()


def _add_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-d",
        "--db",
        default=_default_db(),
        help="database file (default: %(default)s)",
    )


def _parse_updatedb(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="locate.py updatedb",
        description="Build or rebuild the compressed file-name database.",
    )
    _add_db_arg(parser)
    parser.add_argument(
        "-r",
        "--root",
        dest="roots",
        action="append",
        default=[],
        metavar="DIR",
        help="directory to index (repeatable)",
    )
    parser.add_argument(
        "--prune",
        action="append",
        default=[],
        metavar="DIR",
        help="directory to skip (repeatable)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress the summary line",
    )
    return parser.parse_args(argv)


def _parse_locate(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="locate.py",
        description="Find files by name in a compressed database.",
    )
    _add_db_arg(parser)
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("-b", "--basename", action="store_true", help="match against the basename only")
    parser.add_argument("-r", "--regex", action="store_true", help="PATTERN is a Python regex")
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="stop after N matches",
    )
    parser.add_argument(
        "-c",
        "--count",
        action="store_true",
        help="print the number of matches instead of paths",
    )
    parser.add_argument(
        "-0",
        "--null",
        action="store_true",
        help="separate results with NUL instead of newline",
    )
    parser.add_argument(
        "-e",
        "--existing",
        action="store_true",
        help="only print paths that currently exist",
    )
    parser.add_argument("patterns", nargs="+", metavar="PATTERN")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("updatedb", "update"):
        cmd_updatedb(_parse_updatedb(args[1:]))
    elif args and args[0] == "locate":
        cmd_locate(_parse_locate(args[1:]))
    else:
        cmd_locate(_parse_locate(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
