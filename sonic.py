#!/data/data/com.termux/files/usr/bin/env python

"Sort and deduplicate lines in a file with sort(1)/uniq(1) semantics; uses mmap and external merge sort for large files."

import argparse
from collections import Counter
import contextlib
import csv
from datetime import UTC, datetime
from enum import StrEnum
import heapq
import io
import json
import locale as _locale_module
import mmap
import os
from pathlib import Path
import random
import re
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Annotated, Any, Iterable, Iterator, TextIO

import click
import typer


class Keep(StrEnum):
    "Enumerate the supported Keep values."

    first = "first"
    last = "last"


class Output_format(StrEnum):
    "Enumerate the supported Output format values."

    text = "text"
    json = "json"
    csv = "csv"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"%(prog)s {VERSION}")
        raise typer.Exit


_STATE: dict[str, object] = {}


class _Args(SimpleNamespace):
    """argparse.Namespace look-alike: unknown attributes read as None."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


class _ParserShim:
    """Stand-in for the removed argparse parser; hands back what Typer parsed."""

    def parse_args(self, args=None, namespace=None):
        "Parse args."
        return _Args(**_STATE)

    def error(self, message):
        raise typer.BadParameter(message)

    def print_help(self, file=None):
        "Print help."
        typer.echo(click.get_current_context().get_help(), file=file)

    def print_usage(self, file=None):
        "Print usage."
        typer.echo(click.get_current_context().get_usage(), file=file)


IN_MEMORY_THRESHOLD_BYTES = 8 * 1024 * 1024
MMAP_THRESHOLD_BYTES = 1024 * 1024
DEFAULT_CHUNK_LINES = 100_000
DEFAULT_BUFFER_SIZE = 1 << 16
VERSION = "2.0.0"
Stats = dict[str, Any]


def fsz(size: float) -> str:
    size = float(size)
    if size < 1024:
        if size.is_integer():
            return f"{int(size)} B"
        return f"{size:.1f} B"
    for unit in ("KB", "MB", "GB", "TB", "PB", "EB", "ZB"):
        size /= 1024.0
        if size < 1024.0:
            return f"{size:.2f} {unit}"
    return f"{size:.2f} YB"


class _CountingIterator(Iterator[str]):
    __slots__ = ("_iterator", "count")

    def __init__(self, iterator: Iterator[str]) -> None:
        "Initialize the instance state from the supplied arguments."
        self._iterator = iterator
        self.count = 0

    def __iter__(self) -> "_CountingIterator":
        "Return an iterator over the object's items."
        return self

    def __next__(self) -> str:
        "Return the next item from the iterator."
        value = next(self._iterator)
        self.count += 1
        return value


_VERSION_TOKEN = re.compile(r"(\d+)")


def _version_key(text: str) -> list[tuple[int, Any]]:
    parts = _VERSION_TOKEN.split(text)
    out: list[tuple[int, Any]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            out.append((2, int(part)))
        else:
            out.append((1, part.lower()))
    return out


_HUMAN_MULT = {
    "": 1,
    "B": 1,
    "K": 1024,
    "M": 1024**2,
    "G": 1024**3,
    "T": 1024**4,
    "P": 1024**5,
    "E": 1024**6,
    "Z": 1024**7,
    "Y": 1024**8,
}
_HUMAN_TOKEN = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*([KMGTPEZY]?)i?[Bb]?\s*$", re.IGNORECASE)


def _human_key(text: str) -> float:
    match = _HUMAN_TOKEN.match(text)
    if not match:
        return float("inf")
    number = float(match.group(1))
    unit = match.group(2).upper()
    return number * _HUMAN_MULT.get(unit, 1)


_MONTHS = {
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


def _month_key(text: str) -> int:
    return _MONTHS.get(text.strip().lower()[:3], 0)


_NUMERIC_PREFIX = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)")


def _numeric_key(text: str) -> float:
    match = _NUMERIC_PREFIX.match(text)
    if match:
        return float(match.group(1))
    return 0.0


class SortKeyFactory:
    def __init__(
        self,
        *,
        numeric: bool = False,
        version: bool = False,
        human: bool = False,
        month: bool = False,
        ignore_case: bool = False,
        ignore_blanks: bool = False,
        dictionary: bool = False,
        by_length: bool = False,
        field: int | None = None,
        sep: str | None = None,
        locale_name: str | None = None,
        dedupe_by_key: int | None = None,
    ) -> None:
        "Initialize the instance state from the supplied arguments."
        self.numeric = numeric
        self.version = version
        self.human = human
        self.month = month
        self.ignore_case = ignore_case
        self.ignore_blanks = ignore_blanks
        self.dictionary = dictionary
        self.by_length = by_length
        self.field = field
        self.sep = sep
        self.dedupe_by_key = dedupe_by_key
        self.locale_name = locale_name
        if locale_name:
            try:
                _locale_module.setlocale(_locale_module.LC_COLLATE, locale_name)
            except _locale_module.Error as exc:
                msg = f"invalid locale {locale_name!r}: {exc}"
                raise ValueError(msg) from exc
        self._effective_field = dedupe_by_key or field
        self._has_modifiers = any((ignore_case, ignore_blanks, dictionary, self._effective_field))

    def _extract(self, line: str) -> str:
        if not self._effective_field:
            return line
        parts = line.split(self.sep) if self.sep else line.split()
        field = self._effective_field
        if 0 < field <= len(parts):
            return parts[field - 1]
        return ""

    def _normalize(self, text: str) -> str:
        if self.ignore_blanks:
            text = text.lstrip()
        if self.ignore_case:
            text = text.lower()
        if self.dictionary:
            text = re.sub(r"[^A-Za-z0-9 ]", "", text)
        return text

    def _primary(self, line: str) -> Any:
        text = self._normalize(self._extract(line))
        if self.version:
            return _version_key(text)
        if self.month:
            return _month_key(text)
        if self.human:
            return _human_key(text)
        if self.numeric:
            return _numeric_key(text)
        if self.by_length:
            return len(text)
        if self.locale_name:
            return _locale_module.strxfrm(text)
        return text

    def __call__(self, line: str) -> Any:
        "Invoke the behavior represented by this object."
        primary = self._primary(line)
        if self._has_modifiers or self.by_length:
            return (primary, line)
        return primary

    def group_key(self, line: str) -> Any:
        "Group key."
        if self.dedupe_by_key:
            return self._normalize(self._extract(line))
        return line


class LineProcessor:
    def __init__(self, *, verbose: bool = False) -> None:
        "Initialize the instance state from the supplied arguments."
        self.verbose = verbose

    def log(self, message: str) -> None:
        if self.verbose:
            print(f"[INFO] {message}", file=sys.stderr)

    @staticmethod
    def get_file_size(path: Path | str) -> int:
        "Return file size."
        return Path(path).stat().st_size


class LineReader(LineProcessor):
    def __init__(
        self,
        *,
        verbose: bool = False,
        use_mmap: bool = True,
        mmap_threshold: int = MMAP_THRESHOLD_BYTES,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__(verbose=verbose)
        self.use_mmap = use_mmap
        self.mmap_threshold = mmap_threshold
        self.buffer_size = buffer_size

    def _read_mmap(self, path: Path, encoding: str) -> Iterator[str]:
        size = self.get_file_size(path)
        if size == 0:
            return
        self.log(f"Reading {path} ({fsz(size)}) via mmap")
        with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            offset = 0
            length = len(mapped)
            while offset < length:
                newline = mapped.find(b"\n", offset)
                end = length if newline < 0 else newline
                raw = mapped[offset:end]
                offset = length if newline < 0 else newline + 1
                try:
                    yield raw.decode(encoding).rstrip("\r\n")
                except UnicodeDecodeError as exc:
                    self.log(f"Encoding error near offset {offset}: {exc}")

    def _read_regular(self, path: Path, encoding: str) -> Iterator[str]:
        self.log(f"Reading {path} (regular mode)")
        with path.open("r", encoding=encoding, buffering=self.buffer_size) as handle:
            for raw_line in handle:
                yield raw_line.rstrip("\r\n")

    def read_file(self, path: Path, encoding: str = "utf-8") -> Iterator[str]:
        "Read file."
        try:
            if self.use_mmap and self.get_file_size(path) > self.mmap_threshold:
                yield from self._read_mmap(path, encoding)
            else:
                yield from self._read_regular(path, encoding)
        except OSError as exc:
            msg = f"unable to read {path}"
            raise OSError(msg) from exc

    def read_stdin(self, encoding: str = "utf-8") -> Iterator[str]:
        "Read stdin."
        self.log("Reading from stdin")
        for raw_line in sys.stdin:
            yield raw_line.rstrip("\r\n")

    def read(self, source: str, encoding: str = "utf-8") -> Iterator[str]:
        if source == "-":
            yield from self.read_stdin(encoding)
        else:
            yield from self.read_file(Path(source), encoding)


class LineFilter(LineProcessor):
    def __init__(
        self,
        *,
        verbose: bool = False,
        match: str | None = None,
        invert_match: bool = False,
        min_length: int | None = None,
        max_length: int | None = None,
        strip: bool = False,
        ignore_comments: bool = False,
        comment_prefix: str = "#",
        normalize_whitespace: bool = False,
        skip_empty: bool = False,
        reverse_lines: bool = False,
    ) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__(verbose=verbose)
        self.regex = re.compile(match) if match else None
        self.invert_match = invert_match
        self.min_length = min_length
        self.max_length = max_length
        self.strip = strip
        self.ignore_comments = ignore_comments
        self.comment_prefix = comment_prefix
        self.normalize_whitespace = normalize_whitespace
        self.skip_empty = skip_empty
        self.reverse_lines = reverse_lines

    def apply(self, lines: Iterable[str]) -> Iterator[str]:
        if self.reverse_lines:
            lines = reversed(list(lines))
        for line in lines:
            if self.ignore_comments and line.startswith(self.comment_prefix):
                continue
            if self.strip:
                line = line.strip()
            if self.normalize_whitespace:
                line = re.sub(r"\s+", " ", line)
            if self.skip_empty and not line.strip():
                continue
            if self.min_length is not None and len(line) < self.min_length:
                continue
            if self.max_length is not None and len(line) > self.max_length:
                continue
            if self.regex is not None:
                hit = bool(self.regex.search(line))
                if self.invert_match:
                    if hit:
                        continue
                elif not hit:
                    continue
            yield line


class LineRangeFilter(LineProcessor):
    def __init__(self, *, start: int | None = None, end: int | None = None, verbose: bool = False) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__(verbose=verbose)
        self.start = start
        self.end = end

    def apply(self, lines: Iterable[str]) -> Iterator[str]:
        for index, line in enumerate(lines, start=1):
            if self.start is not None and index < self.start:
                continue
            if self.end is not None and index > self.end:
                continue
            yield line


class LineSorter(LineProcessor):
    def sort_in_memory(self, lines: list[str], *, key, reverse: bool) -> list[str]:
        "Sort in memory."
        self.log(f"Sorting {len(lines):,} lines in memory")
        return sorted(lines, key=key, reverse=reverse)

    def _write_chunk(self, chunk: list[str], temp_dir: Path, index: int, *, reverse: bool, key, encoding: str) -> Path:
        chunk.sort(key=key, reverse=reverse)
        temp_file = temp_dir / f".sort_chunk_{os.getpid()}_{index}.tmp"
        with temp_file.open("w", encoding=encoding, newline="\n") as handle:
            for line in chunk:
                handle.write(line)
                handle.write("\n")
        return temp_file

    def create_sorted_chunks(
        self,
        lines: Iterable[str],
        *,
        chunk_size: int,
        reverse: bool,
        key,
        encoding: str,
    ) -> list[Path]:
        "Create sorted chunks."
        self.log(f"External sort enabled (chunk size: {chunk_size:,} lines)")
        temp_dir = Path(tempfile.gettempdir())
        temp_files: list[Path] = []
        chunk: list[str] = []
        try:
            for line in lines:
                chunk.append(line)
                if len(chunk) >= chunk_size:
                    temp_files.append(
                        self._write_chunk(
                            chunk,
                            temp_dir,
                            len(temp_files),
                            reverse=reverse,
                            key=key,
                            encoding=encoding,
                        )
                    )
                    chunk = []
            if chunk:
                temp_files.append(
                    self._write_chunk(
                        chunk,
                        temp_dir,
                        len(temp_files),
                        reverse=reverse,
                        key=key,
                        encoding=encoding,
                    )
                )
        except BaseException:
            self.cleanup_temp_files(temp_files)
            raise
        self.log(f"Wrote {len(temp_files)} sorted chunk(s)")
        return temp_files

    @staticmethod
    def _strip_lines(handle: TextIO) -> Iterator[str]:
        for raw in handle:
            yield raw.rstrip("\n")

    def merge_sorted_files(
        self,
        temp_files: list[Path],
        *,
        reverse: bool,
        key,
        encoding: str,
    ) -> Iterator[str]:
        handles: list[TextIO] = []
        try:
            for temp_file in temp_files:
                handles.append(temp_file.open("r", encoding=encoding))
            streams = [self._strip_lines(h) for h in handles]
            if streams:
                yield from heapq.merge(*streams, key=key, reverse=reverse)
        finally:
            for handle in handles:
                handle.close()

    @staticmethod
    def cleanup_temp_files(temp_files: Iterable[Path]) -> None:
        for temp_file in temp_files:
            with contextlib.suppress(OSError):
                temp_file.unlink(missing_ok=True)


def _format_group_line(line: str, count: int, show_count: bool) -> str:
    if show_count:
        return f"{count:7d}\t{line}"
    return line


def _emit_group(
    group: list[str],
    mode: str,
    show_count: bool,
    keep: str = "first",
) -> Iterator[str]:
    count = len(group)
    if mode == "unique_only":
        if count != 1:
            return
    elif mode in ("repeated", "repeated_once"):
        if count <= 1:
            return
        if mode == "repeated":
            for _ in group:
                yield _format_group_line(group[0], count, show_count)
            return
    line = group[0] if keep == "first" else group[-1]
    yield _format_group_line(line, count, show_count)


def coalesce(
    lines: Iterable[str],
    *,
    group_key,
    mode: str,
    show_count: bool,
    keep: str = "first",
) -> Iterator[str]:
    group: list[str] = []
    prev_key: Any = object()
    for line in lines:
        k = group_key(line)
        if group and k != prev_key:
            yield from _emit_group(group, mode, show_count, keep)
            group = []
        group.append(line)
        prev_key = k
    if group:
        yield from _emit_group(group, mode, show_count, keep)


class LineDeduplicator(LineProcessor):
    def __init__(self, *, verbose: bool = False) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__(verbose=verbose)

    def __call__(self, lines: Iterable[str], *, group_key, mode: str, show_count: bool, keep: str) -> Iterator[str]:
        "Invoke the behavior represented by this object."
        yield from coalesce(
            lines,
            group_key=group_key,
            mode=mode,
            show_count=show_count,
            keep=keep,
        )

    def unsorted(self, lines: Iterable[str], *, group_key, mode: str, show_count: bool, keep: str) -> Iterator[str]:
        counter: Counter[str] = Counter()
        order: list[str] = []
        for line in lines:
            key = group_key(line) if group_key else line
            if key not in counter:
                order.append(line)
            counter[key] += 1
        for line in order:
            key = group_key(line) if group_key else line
            yield from _emit_group([line] * counter[key], mode, show_count, keep)


class LineTransformer(LineProcessor):
    def __init__(
        self,
        *,
        verbose: bool = False,
        lower: bool = False,
        upper: bool = False,
        prefix: str = "",
        suffix: str = "",
        number: bool = False,
    ) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__(verbose=verbose)
        self.lower = lower
        self.upper = upper
        self.prefix = prefix
        self.suffix = suffix
        self.number = number

    def apply(self, lines: Iterable[str]) -> Iterator[str]:
        for index, line in enumerate(lines, start=1):
            if self.lower:
                line = line.lower()
            if self.upper:
                line = line.upper()
            if self.prefix:
                line = self.prefix + line
            if self.suffix:
                line = line + self.suffix
            if self.number:
                line = f"{index:6d}\t{line}"
            yield line


class LineFormatter(LineProcessor):
    def __init__(
        self,
        *,
        verbose: bool = False,
        output_format: str = "text",
        null_terminated: bool = False,
        no_trailing_newline: bool = False,
        crlf: bool = False,
        stats: Stats | None = None,
    ) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__(verbose=verbose)
        self.output_format = output_format
        self.null_terminated = null_terminated
        self.no_trailing_newline = no_trailing_newline
        self.crlf = crlf
        self.stats = stats or {}

    def _line_sep(self) -> str:
        if self.null_terminated:
            return "\x00"
        if self.crlf:
            return "\r\n"
        return "\n"

    def serialize(self, lines: Iterable[str]) -> Iterator[str]:
        if self.output_format == "json":
            yield from self._as_json(lines)
            return
        if self.output_format == "csv":
            yield from self._as_csv(lines)
            return
        yield from self._as_text(lines)

    def _as_text(self, lines: Iterable[str]) -> Iterator[str]:
        sep = self._line_sep()
        first = True
        for line in lines:
            if not first and not self.no_trailing_newline:
                pass
            first = False
            if self.no_trailing_newline:
                yield line
            else:
                yield line + sep

    def _as_json(self, lines: Iterable[str]) -> Iterator[str]:
        data = list(lines)
        yield json.dumps({"lines": data, "stats": self.stats}, indent=2)
        yield "\n"

    def _as_csv(self, lines: Iterable[str]) -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator=self._line_sep())
        writer.writerow(["line"])
        for line in lines:
            writer.writerow([line])
        yield buffer.getvalue()


class OutputWriter(LineProcessor):
    def __init__(
        self,
        *,
        verbose: bool = False,
        dry_run: bool = False,
        fsync: bool = False,
        preserve_permissions: bool = False,
        preserve_mtime: bool = False,
        encoding: str = "utf-8",
    ) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__(verbose=verbose)
        self.dry_run = dry_run
        self.fsync = fsync
        self.preserve_permissions = preserve_permissions
        self.preserve_mtime = preserve_mtime
        self.encoding = encoding

    def write(
        self,
        chunks: Iterable[str],
        destination: str,
        *,
        source_for_meta: Path | None = None,
        is_binary_stdout: bool = False,
    ) -> tuple[int, int]:
        if self.dry_run:
            size = 0
            count = 0
            for chunk in chunks:
                count += 1
                size += len(chunk.encode(self.encoding))
            self.log(f"DRY RUN: would write {count:,} chunk(s) ({fsz(size)})")
            return count, size
        if destination == "-":
            if is_binary_stdout:
                buf = sys.stdout.buffer
                for chunk in chunks:
                    buf.write(chunk.encode(self.encoding))
                buf.flush()
            else:
                for chunk in chunks:
                    sys.stdout.write(chunk)
                sys.stdout.flush()
            return -1, -1
        output = Path(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent))
        tmp_path = Path(tmp_name)
        byte_count = 0
        try:
            with os.fdopen(fd, "w", encoding=self.encoding, newline="") as handle:
                for chunk in chunks:
                    handle.write(chunk)
                    byte_count += len(chunk.encode(self.encoding))
                handle.flush()
                if self.fsync:
                    os.fsync(handle.fileno())
            if source_for_meta is not None:
                src_stat = source_for_meta.stat()
                if self.preserve_permissions:
                    os.chmod(tmp_path, src_stat.st_mode)
                if self.preserve_mtime:
                    os.utime(tmp_path, (src_stat.st_atime, src_stat.st_mtime))
            os.replace(tmp_path, output)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return -1, byte_count


class FileSorter(LineProcessor):
    def __init__(
        self,
        *,
        verbose: bool = False,
        dry_run: bool = False,
        quiet: bool = False,
        no_stats: bool = False,
    ) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__(verbose=verbose)
        self.dry_run = dry_run
        self.quiet = quiet
        self.no_stats = no_stats

    @staticmethod
    def _build_range(args: argparse.Namespace, *, verbose: bool) -> LineRangeFilter:
        return LineRangeFilter(start=args.from_line, end=args.to_line, verbose=verbose)

    @staticmethod
    def _build_filter(args: argparse.Namespace, *, verbose: bool) -> LineFilter:
        return LineFilter(
            verbose=verbose,
            match=args.match,
            invert_match=args.invert_match,
            min_length=args.min_length,
            max_length=args.max_length,
            strip=args.strip,
            ignore_comments=args.ignore_comments,
            comment_prefix=args.comment_prefix,
            normalize_whitespace=args.normalize_whitespace,
            skip_empty=args.skip_empty,
            reverse_lines=args.reverse_lines,
        )

    @staticmethod
    def _build_transformer(args: argparse.Namespace, *, verbose: bool) -> LineTransformer:
        return LineTransformer(
            verbose=verbose,
            lower=args.lower,
            upper=args.upper,
            prefix=args.prefix or "",
            suffix=args.suffix or "",
            number=args.number,
        )

    @staticmethod
    def _dedupe_mode(args: argparse.Namespace) -> tuple[str, bool, str]:
        mode = "unique"
        if args.repeated:
            mode = "repeated"
        elif args.repeated_once:
            mode = "repeated_once"
        elif args.unique_only:
            mode = "unique_only"
        show_count = bool(args.count) or (args.output_format == "csv")
        return mode, show_count, args.keep

    @staticmethod
    def _dedupe_requested(args: argparse.Namespace) -> bool:
        return bool(args.unique or args.count or args.repeated or args.repeated_once or args.unique_only)

    def _write_stream(
        self,
        args: argparse.Namespace,
        lines: Iterable[str],
        destination: str,
        *,
        source_for_meta: Path | None,
        stats: Stats,
    ) -> tuple[int, int]:
        formatter = LineFormatter(
            verbose=self.verbose,
            output_format=args.output_format,
            null_terminated=args.null_terminated,
            no_trailing_newline=args.no_trailing_newline,
            crlf=args.crlf,
            stats=stats,
        )
        writer = OutputWriter(
            verbose=self.verbose,
            dry_run=self.dry_run,
            fsync=args.fsync,
            preserve_permissions=args.preserve_permissions,
            preserve_mtime=args.preserve_mtime,
            encoding=args.encoding,
        )
        return writer.write(
            formatter.serialize(lines),
            destination,
            source_for_meta=source_for_meta,
            is_binary_stdout=args.null_terminated,
        )

    def process_file(
        self,
        path: str,
        output_path: str | None = None,
        *,
        args: argparse.Namespace,
    ) -> Stats:
        "Process file."
        input_path = Path(path) if path != "-" else None
        if input_path is not None and not input_path.exists():
            msg = f"input file not found: {input_path}"
            raise FileNotFoundError(msg)
        output = output_path or ("-" if path == "-" else path)
        original_size = self.get_file_size(input_path) if input_path else 0
        start_time = time.monotonic()
        backup_path: Path | None = None
        if args.backup and not self.dry_run and input_path is not None and output == str(input_path):
            backup_path = input_path.with_name(input_path.name + args.backup_suffix)
            shutil.copy2(input_path, backup_path)
            self.log(f"Backup written: {backup_path}")
        reader = LineReader(
            verbose=self.verbose,
            use_mmap=not args.no_mmap,
            mmap_threshold=args.mmap_threshold,
            buffer_size=args.buffer_size,
        )
        source_iter = reader.read(path, args.encoding)
        counted = _CountingIterator(source_iter)
        range_filter = self._build_range(args, verbose=self.verbose)
        line_filter = self._build_filter(args, verbose=self.verbose)
        pipeline: Iterable[str] = counted
        pipeline = range_filter.apply(pipeline)
        pipeline = line_filter.apply(pipeline)
        key_factory = SortKeyFactory(
            numeric=args.numeric,
            version=args.version_sort,
            human=args.human_numeric,
            month=args.month_sort,
            ignore_case=args.ignore_case_sort or args.case_insensitive,
            ignore_blanks=args.ignore_leading_blanks,
            dictionary=args.dictionary_order,
            by_length=args.sort_by_length,
            field=args.key,
            sep=args.field_separator,
            locale_name=args.locale,
            dedupe_by_key=args.dedupe_by_key,
        )
        if args.check or args.check_only_duplicates:
            return self._run_check(
                args,
                pipeline,
                key_factory,
                counted,
                input_path,
                start_time,
            )
        temp_files: list[Path] = []
        merge_gen: Iterator[str] | None = None
        original_lines = 0
        try:
            if args.random:
                materialized = list(pipeline)
                original_lines = counted.count
                self.log(f"Random shuffle of {len(materialized):,} lines")
                random.shuffle(materialized)
                base_lines: Iterable[str] = materialized
            elif args.sort and (original_size > args.in_memory_threshold and args.from_line is None):
                lsorter = LineSorter(verbose=self.verbose)
                temp_files = lsorter.create_sorted_chunks(
                    pipeline,
                    chunk_size=args.chunk_lines,
                    reverse=args.reverse,
                    key=key_factory,
                    encoding=args.encoding,
                )
                original_lines = counted.count
                merge_gen = lsorter.merge_sorted_files(
                    temp_files,
                    reverse=args.reverse,
                    key=key_factory,
                    encoding=args.encoding,
                )
                base_lines = merge_gen
            elif args.sort:
                materialized = list(pipeline)
                original_lines = counted.count
                base_lines = sorted(
                    materialized,
                    key=key_factory,
                    reverse=args.reverse,
                )
            else:
                materialized = list(pipeline)
                original_lines = counted.count
                base_lines = materialized
            if self._dedupe_requested(args):
                mode, show_count, keep = self._dedupe_mode(args)
                group_key = key_factory.group_key
                dedupe = LineDeduplicator(verbose=self.verbose)
                if args.sort or args.random:
                    final_iter: Iterable[str] = dedupe(
                        base_lines,
                        group_key=group_key,
                        mode=mode,
                        show_count=show_count,
                        keep=keep,
                    )
                else:
                    final_iter = dedupe.unsorted(
                        base_lines,
                        group_key=group_key,
                        mode=mode,
                        show_count=show_count,
                        keep=keep,
                    )
            else:
                final_iter = iter(base_lines)
            if args.sample is not None:
                final_iter = _reservoir_sample(final_iter, args.sample)
            if args.sample_rate is not None:
                rate = args.sample_rate
                final_iter = (ln for ln in final_iter if random.random() < rate)
            limit = args.limit if args.limit is not None else args.head
            if limit is not None:
                final_iter = _take(final_iter, limit)
            transformer = self._build_transformer(args, verbose=self.verbose)
            final_iter = transformer.apply(final_iter)
            final_counter = _CountingIterator(iter(final_iter))
            final_iter = final_counter
            stats: Stats = {
                "input_file": path,
                "output_file": output,
                "original_lines": original_lines,
                "original_size_bytes": original_size,
                "original_size": fsz(original_size),
            }
            _, written_bytes = self._write_stream(
                args,
                final_iter,
                output,
                source_for_meta=input_path,
                stats=stats,
            )
        finally:
            if merge_gen is not None:
                with contextlib.suppress(Exception):
                    merge_gen.close()
            if temp_files:
                LineSorter.cleanup_temp_files(temp_files)
        elapsed = time.monotonic() - start_time

        stats.update(
            {
                "final_lines": final_counter.count,
                "after_bytes": max(written_bytes, 0),
                "after": fsz(written_bytes) if written_bytes >= 0 else "n/a",
                "processing_time": elapsed,
            }
        )

        if args.stats_file:
            self._write_stats_file(stats, args.stats_file)
        if backup_path is not None:
            stats["backup_file"] = str(backup_path)
        return stats

    def _run_check(
        self,
        args: argparse.Namespace,
        pipeline: Iterable[str],
        key_factory: SortKeyFactory,
        counted: _CountingIterator,
        input_path: Path | None,
        start_time: float,
    ) -> Stats:
        check_unique = bool(args.unique or args.unique_only or args.count or args.repeated or args.repeated_once)
        group_key = key_factory.group_key
        sorted_ok = True
        unique_ok = True
        if args.check_only_duplicates:
            counter: Counter[str] = Counter()
            prev_key: Any = None
            prev_gk: str | None = None
            first = True
            for line in pipeline:
                if args.check:
                    k = key_factory(line)
                    if not first and k < prev_key:
                        sorted_ok = False
                    prev_key = k
                gk = group_key(line)
                if not first and gk == prev_gk:
                    unique_ok = False
                prev_gk = gk
                counter[gk] += 1
                first = False
            dup_groups = sum(1 for v in counter.values() if v > 1)
            dup_lines = sum(v for v in counter.values() if v > 1)
            print(f"duplicate groups: {dup_groups}")
            print(f"duplicate lines: {dup_lines}")
            if args.check:
                print("sorted" if sorted_ok else "not sorted")
        else:
            prev_key = None
            prev_gk = None
            first = True
            for line in pipeline:
                if args.check:
                    k = key_factory(line)
                    if not first and k < prev_key:
                        sorted_ok = False
                    prev_key = k
                if check_unique:
                    gk = group_key(line)
                    if not first and gk == prev_gk:
                        unique_ok = False
                    prev_gk = gk
                first = False
            print("sorted" if sorted_ok else "not sorted")
            if check_unique:
                print("unique" if unique_ok else "duplicates present")
        elapsed = time.monotonic() - start_time
        return {
            "input_file": str(input_path) if input_path else "-",
            "check_sorted": sorted_ok if args.check else None,
            "check_unique": unique_ok if check_unique else None,
            "original_lines": counted.count,
            "processing_time": elapsed,
        }

    """
    def process_file(
        self,
        path: str,
        output_path: str | None = None,
        *,
        args: argparse.Namespace,
    ) -> Stats:
        input_path = Path(path) if path != "-" else None
        if input_path is not None and not input_path.exists():
            raise FileNotFoundError(f"input file not found: {input_path}")
        output = output_path or ("-" if path == "-" else path)
        original_size = self.get_file_size(input_path) if input_path else 0
        start_time = time.monotonic()
        backup_path: Path | None = None
        if args.backup and not self.dry_run and input_path is not None and output == str(input_path):
            backup_path = input_path.with_name(input_path.name + args.backup_suffix)
            shutil.copy2(input_path, backup_path)
            self.log(f"Backup written: {backup_path}")
        reader = LineReader(
            verbose=self.verbose,
            use_mmap=not args.no_mmap,
            mmap_threshold=args.mmap_threshold,
            buffer_size=args.buffer_size,
        )
        source_iter = reader.read(path, args.encoding)
        counted = _CountingIterator(source_iter)
        range_filter = self._build_range(args, verbose=self.verbose)
        line_filter = self._build_filter(args, verbose=self.verbose)
        pipeline: Iterable[str] = counted
        pipeline = range_filter.apply(pipeline)
        pipeline = line_filter.apply(pipeline)
        key_factory = SortKeyFactory(
            numeric=args.numeric,
            version=args.version_sort,
            human=args.human_numeric,
            month=args.month_sort,
            ignore_case=args.ignore_case_sort or args.case_insensitive,
            ignore_blanks=args.ignore_leading_blanks,
            dictionary=args.dictionary_order,
            by_length=args.sort_by_length,
            field=args.key,
            sep=args.field_separator,
            locale_name=args.locale,
            dedupe_by_key=args.dedupe_by_key,
        )
        if args.check or args.check_only_duplicates:
            return self._run_check(args, pipeline, key_factory, counted, input_path, start_time)
        if args.random:
            lines = list(pipeline)
            original_lines = counted.count
            self.log(f"Random shuffle of {len(lines):,} lines")
            random.shuffle(lines)
        elif args.sort:
            if original_size <= args.in_memory_threshold or args.from_line is not None:
                lines = list(pipeline)
                original_lines = counted.count
                lines = sorted(lines, key=key_factory, reverse=args.reverse)
            else:
                lines = list(pipeline)
                original_lines = counted.count
                lines = sorted(lines, key=key_factory, reverse=args.reverse)
        else:
            lines = list(pipeline)
            original_lines = counted.count
        if self._dedupe_requested(args):
            mode, show_count, keep = self._dedupe_mode(args)
            group_key = key_factory.group_key
            if args.sort or args.random:
                dedupe = LineDeduplicator(verbose=self.verbose)
                final_iter = dedupe(
                    lines,
                    group_key=group_key,
                    mode=mode,
                    show_count=show_count,
                    keep=keep,
                )
            else:
                dedupe = LineDeduplicator(verbose=self.verbose)
                final_iter = dedupe.unsorted(
                    lines,
                    group_key=group_key,
                    mode=mode,
                    show_count=show_count,
                    keep=keep,
                )
        else:
            final_iter = iter(lines)
        if args.sample is not None and args.sample < len(lines):
            final_iter = _reservoir_sample(final_iter, args.sample)
        if args.sample_rate is not None:
            final_iter = (line for line in final_iter if random.random() < args.sample_rate)
        limit = args.limit if args.limit is not None else args.head
        if limit is not None:
            final_iter = _take(final_iter, limit)
        transformer = self._build_transformer(args, verbose=self.verbose)
        final_iter = transformer.apply(final_iter)
        stats: Stats = {
            "input_file": path,
            "output_file": output,
            "original_lines": original_lines,
            "original_size_bytes": original_size,
            "original_size": fsz(original_size),
        }
        if args.check_only_duplicates:
            stats["duplicates_only"] = True
            return stats
        _, written_bytes = self._write_stream(
            args,
            final_iter,
            output,
            source_for_meta=input_path,
            stats=stats,
        )
        elapsed = time.monotonic() - start_time
        stats.update({
            "final_lines": -1,
            "after_bytes": written_bytes if written_bytes >= 0 else 0,
            "after": fsz(written_bytes) if written_bytes >= 0 else "n/a",
            "processing_time": elapsed,
        })
        if args.stats_file:
            self._write_stats_file(stats, args.stats_file)
        if backup_path is not None:
            stats["backup_file"] = str(backup_path)
        return stats
    def _run_check(
        self,
        args: argparse.Namespace,
        pipeline: Iterable[str],
        key_factory: SortKeyFactory,
        counted: _CountingIterator,
        input_path: Path | None,
        start_time: float,
    ) -> Stats:
        sorted_ok = True
        unique_ok = True
        prev_key: Any = object()
        prev_line: str | None = None
        seen: set[str] = set()
        check_unique = bool(args.unique or args.unique_only or args.count or args.repeated or args.repeated_once)
        group_key = key_factory.group_key
        for line in pipeline:
            if args.check:
                k = key_factory(line)
                if prev_line is not None and k < prev_key:
                    sorted_ok = False
            if check_unique:
                if group_key(line) in seen and args.check_only_duplicates:
                    continue
                seen.add(group_key(line))
            prev_key = key_factory(line)
            prev_line = line
        if args.check_only_duplicates:
            from collections import Counter
            counter = Counter()
            for line in pipeline:
                counter[group_key(line)] += 1
            duplicates = sum(1 for v in counter.values() if v > 1)
            print(f"duplicate groups: {duplicates}")
        else:
            print("sorted" if sorted_ok else "not sorted")
            if check_unique:
                print("unique" if unique_ok else "duplicates present")
        elapsed = time.monotonic() - start_time
        return {
            "input_file": str(input_path) if input_path else "-",
            "check_sorted": sorted_ok,
            "check_unique": unique_ok,
            "original_lines": counted.count,
            "processing_time": elapsed,
        }
        """

    def _write_stats_file(self, stats: Stats, path: str) -> None:
        try:
            with Path(path).open("w", encoding="utf-8") as handle:
                json.dump(stats, handle, indent=2)
            print(f"Stats saved: {path}", file=sys.stderr)
        except OSError as exc:
            print(f"Error saving stats: {exc}", file=sys.stderr)

    def print_stats(self, stats: Stats) -> None:
        "Print stats."
        if self.quiet or self.no_stats:
            return
        if "check_sorted" in stats:
            print(f"sorted: {stats['check_sorted']}, unique: {stats['check_unique']}")
            return
        dup = stats.get("original_lines", 0) - stats.get("final_lines", 0)
        if dup > 0:
            prefix = "would remove" if self.dry_run else "removed"
            print(f"{prefix} {dup} duplicate lines")
        else:
            print("no duplicate line")

    def save_report(self, stats: Stats, report_file: str | None = None) -> None:
        "Save report."
        if report_file is None:
            report_file = "sort_report.json"
        report = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "statistics": stats,
        }
        try:
            with Path(report_file).open("w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2)
            print(f"Report saved: {report_file}")
        except OSError as exc:
            print(f"Error saving report: {exc}", file=sys.stderr)


def _take(lines: Iterable[str], n: int) -> Iterator[str]:
    for i, line in enumerate(lines):
        if i >= n:
            return
        yield line


def _reservoir_sample(lines: Iterable[str], k: int) -> Iterator[str]:
    if k <= 0:
        return
    reservoir: list[str] = []
    for i, line in enumerate(lines):
        if i < k:
            reservoir.append(line)
        else:
            j = random.randint(0, i)
            if j < k:
                reservoir[j] = line
    yield from reservoir


class FileAnalyzer(LineProcessor):
    def __init__(self, *, verbose: bool = False) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__(verbose=verbose)
        self.reader = LineReader(verbose=verbose)

    def analyze_file(self, path: Path, encoding: str = "utf-8") -> dict[str, Any]:
        size = self.get_file_size(path)
        counter: Counter[str] = Counter()
        total = 0
        total_length = 0
        max_length = 0
        for line in self.reader.read_file(path, encoding):
            counter[line] += 1
            total += 1
            length = len(line)
            total_length += length
            max_length = max(max_length, length)
        duplicates = total - len(counter)
        return {
            "file": str(path),
            "size_bytes": size,
            "size": fsz(size),
            "total_lines": total,
            "unique_lines": len(counter),
            "duplicate_lines": duplicates,
            "duplicate_percentage": (duplicates / total * 100) if total else 0.0,
            "max_line_length": max_length,
            "avg_line_length": (total_length / total) if total else 0.0,
            "most_common_lines": counter.most_common(10),
        }

    def print_analysis(self, path: Path, encoding: str = "utf-8") -> None:
        "Print analysis."
        a = self.analyze_file(path, encoding)
        print(f"\n{'=' * 40}")
        print(f"File Analysis: {path.name}")
        print(f"{'=' * 40}")
        print("\nBasic Statistics:")
        print(f"  Size: {a['size']}")
        print(f"  Total lines: {a['total_lines']:,}")
        print(f"  Unique lines: {a['unique_lines']:,}")
        print(f"  Duplicate lines: {a['duplicate_lines']:,} ({a['duplicate_percentage']:.1f}%)")
        print("\nLine Length Statistics:")
        print(f"  Maximum: {a['max_line_length']} characters")
        print(f"  Average: {a['avg_line_length']:.1f} characters")
        if a["most_common_lines"]:
            print("\nMost Common Lines (Top 10):")
            for line, count in a["most_common_lines"]:
                display = line[:47] + "..." if len(line) > 50 else line
                print(f"  ({count}x) {display}")
        print(f"{'=' * 40}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = _ParserShim()
    return parser


def main(argv: list[str] | None = None) -> int:
    "Run the command-line interface."
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.analyze:
        analyzer = FileAnalyzer(verbose=args.verbose)
        for filename in args.filenames:
            if filename == "-":
                print("error: --analyze requires file paths, not stdin", file=sys.stderr)
                return 2
            try:
                analyzer.print_analysis(Path(filename), args.encoding)
            except FileNotFoundError:
                print(f"error: file not found: {filename}", file=sys.stderr)
                return 2
            except OSError as exc:
                print(f"error reading {filename}: {exc}", file=sys.stderr)
                return 2
        return 0
    if args.output and args.output_dir:
        print("error: --output and --output-dir are mutually exclusive", file=sys.stderr)
        return 2
    if args.output and len(args.filenames) > 1:
        print("error: --output cannot be used with multiple input files", file=sys.stderr)
        return 2
    if args.output_dir and "-" in args.filenames:
        print("error: --output-dir cannot be used with stdin", file=sys.stderr)
        return 2
    if args.sample_rate is not None and not 0.0 <= args.sample_rate <= 1.0:
        print("error: --sample-rate must be between 0 and 1", file=sys.stderr)
        return 2
    if args.from_line is not None and args.to_line is not None and args.from_line > args.to_line:
        print("error: --from-line must be <= --to-line", file=sys.stderr)
        return 2
    sorter = FileSorter(
        verbose=args.verbose,
        dry_run=args.dry_run,
        quiet=args.quiet,
        no_stats=args.no_stats,
    )
    exit_code = 0
    all_stats: list[Stats] = []
    for filename in args.filenames:
        try:
            output_path: str | None = args.output
            if args.output_dir:
                out_dir = Path(args.output_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                output_path = str(out_dir / Path(filename).name)
            stats = sorter.process_file(filename, output_path, args=args)
            all_stats.append(stats)
            if args.check:
                if stats.get("check_sorted") is False:
                    exit_code = max(exit_code, 1)
                if stats.get("check_unique") is False:
                    exit_code = max(exit_code, 1)
            elif not args.check_only_duplicates:
                sorter.print_stats(stats)
            """
            stats = sorter.process_file(filename, output_path, args=args)
            all_stats.append(stats)
            if args.check or args.check_only_duplicates:
                # `_run_check` records the verdict in the stats dict.
                if stats.get("check_sorted") is False:
                    exit_code = max(exit_code, 1)
                if stats.get("check_unique") is False:
                    exit_code = max(exit_code, 1)
            else:
                sorter.print_stats(stats)
                """
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            exit_code = 2
        except (OSError, ValueError) as exc:
            print(f"error processing {filename}: {exc}", file=sys.stderr)
            exit_code = 2
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 130
    if args.report_file and all_stats:
        payload = all_stats[0] if len(all_stats) == 1 else {"files": all_stats}
        sorter.save_report(payload, args.report_file)
    return exit_code


def _run() -> None:
    raise SystemExit(main())


app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
)


@app.command(
    help="Sort and deduplicate lines in a file with sort(1)/uniq(1) semantics; uses mmap and external merge sort for large files.",
    epilog="\nExamples:\n  # Basic in-place sort + unique with backup\n  python sort_unique_lines.py input.txt --backup\n\n  # Numeric sort, ignore case, write to stdout\n  python sort_unique_lines.py data.txt -n -f -o -\n\n  # Version sort (v2.9 < v2.10), dedupe case-insensitively\n  python sort_unique_lines.py releases.txt -V --dedupe-ignore-case\n\n  # Sort only lines 100-200 of the file by field 3 (\\t separated)\n  python sort_unique_lines.py big.tsv --from-line 100 --to-line 200 \\\n      -K 3 -t $'\\t' -c\n\n  # Filter, strip, shuffle\n  python sort_unique_lines.py log.txt -m 'ERROR' --strip -R\n\n  # Count duplicates only (like `sort | uniq -c | sort -rn`)\n  python sort_unique_lines.py data.txt -c | sort -k1,1nr\n\n  # Show only repeated lines, keeping first occurrence\n  python sort_unique_lines.py data.txt --repeated-once --keep first\n\n  # Read stdin, produce CSV\n  cat data.txt | python sort_unique_lines.py - -u --output-format csv\n\n  # Verify that a file is already sorted and unique\n  python sort_unique_lines.py data.txt --check -u\n\n  # Natural/version sort, human numeric sort of disk sizes\n  python sort_unique_lines.py sizes.txt --human-numeric\n  python sort_unique_lines.py tags.txt -V\n\n  # Sort by length, shortest first, prefix line numbers\n  python sort_unique_lines.py words.txt --sort-by-length -N\n",
)
def cli(
    filenames: Annotated[list[str], typer.Argument(help="Input file(s); use '-' for stdin", metavar="FILE")],
    output: Annotated[
        str | None,
        typer.Option(
            "-o", "--output", help="Output filename (default: overwrite each input; '-' for stdout)", metavar="FILE"
        ),
    ] = None,
    output_dir: Annotated[
        str | None, typer.Option("--output-dir", help="Write one output file per input inside DIR", metavar="DIR")
    ] = None,
    encoding: Annotated[str, typer.Option("--encoding", "-e", help="File encoding (default: utf-8)")] = "utf-8",
    from_line: Annotated[
        int | None, typer.Option("--from-line", help="Start processing at line X (1-based)", metavar="X")
    ] = None,
    to_line: Annotated[
        int | None, typer.Option("--to-line", help="Stop processing at line Y (1-based, inclusive)", metavar="Y")
    ] = None,
    sort: Annotated[bool, typer.Option("-s", "--sort", help="Sort lines (default: enabled)")] = True,
    unique: Annotated[bool, typer.Option("-u", "--unique", help="Coalesce duplicate lines (default: enabled)")] = True,
    reverse: Annotated[bool, typer.Option("-r", "--reverse", help="Reverse the sort order")] = False,
    case_insensitive: Annotated[
        bool, typer.Option("-i", "--case-insensitive", help="Case-insensitive sort (legacy alias for -f)")
    ] = False,
    numeric: Annotated[
        bool, typer.Option("-n", "--numeric", help="Compare lines as numbers", rich_help_panel="sort semantics")
    ] = False,
    version_sort: Annotated[
        bool,
        typer.Option(
            "-V", "--version-sort", help="Natural/version-aware sort (v2.9 < v2.10)", rich_help_panel="sort semantics"
        ),
    ] = False,
    random: Annotated[
        bool, typer.Option("-R", "--random", help="Shuffle lines instead of sorting", rich_help_panel="sort semantics")
    ] = False,
    ignore_case_sort: Annotated[
        bool,
        typer.Option(
            "-f",
            "--ignore-case-sort",
            help="Ignore case for sorting (does not affect dedupe)",
            rich_help_panel="sort semantics",
        ),
    ] = False,
    ignore_leading_blanks: Annotated[
        bool,
        typer.Option(
            "-b",
            "--ignore-leading-blanks",
            help="Trim leading whitespace before comparing",
            rich_help_panel="sort semantics",
        ),
    ] = False,
    dictionary_order: Annotated[
        bool,
        typer.Option("-d", "--dictionary-order", help="Compare only [A-Za-z0-9 ]", rich_help_panel="sort semantics"),
    ] = False,
    human_numeric: Annotated[
        bool, typer.Option("--human-numeric", help="Sort 1K < 2M < 1G < 2.5G etc.", rich_help_panel="sort semantics")
    ] = False,
    month_sort: Annotated[
        bool,
        typer.Option(
            "-M", "--month-sort", help="Sort by month abbreviation Jan..Dec", rich_help_panel="sort semantics"
        ),
    ] = False,
    key: Annotated[
        int | None,
        typer.Option(
            "-K", "--key", help="Sort by the Nth field (see -t)", metavar="N", rich_help_panel="sort semantics"
        ),
    ] = None,
    field_separator: Annotated[
        str | None,
        typer.Option(
            "-t",
            "--field-separator",
            help="Field separator for -K (default: whitespace)",
            metavar="SEP",
            rich_help_panel="sort semantics",
        ),
    ] = None,
    locale: Annotated[
        str | None,
        typer.Option(
            "--locale", help="Locale-aware collation (locale.strxfrm)", metavar="NAME", rich_help_panel="sort semantics"
        ),
    ] = None,
    sort_by_length: Annotated[
        bool,
        typer.Option("--sort-by-length", help="Sort by line length (shortest first)", rich_help_panel="sort semantics"),
    ] = False,
    count: Annotated[
        bool,
        typer.Option(
            "-c", "--count", help="Prefix each line with its occurrence count", rich_help_panel="dedupe semantics"
        ),
    ] = False,
    repeated: Annotated[
        bool,
        typer.Option(
            "-D", "--repeated", help="Show only duplicated lines (all copies)", rich_help_panel="dedupe semantics"
        ),
    ] = False,
    repeated_once: Annotated[
        bool,
        typer.Option(
            "--repeated-once", help="Show only duplicated lines (one copy each)", rich_help_panel="dedupe semantics"
        ),
    ] = False,
    unique_only: Annotated[
        bool,
        typer.Option(
            "-U", "--unique-only", help="Show only lines that appear exactly once", rich_help_panel="dedupe semantics"
        ),
    ] = False,
    dedupe_ignore_case: Annotated[
        bool,
        typer.Option(
            "--dedupe-ignore-case", help="Case-insensitive dedupe (ABC == abc)", rich_help_panel="dedupe semantics"
        ),
    ] = False,
    keep: Annotated[
        Keep,
        typer.Option("--keep", help="Keep first or last occurrence of each line", rich_help_panel="dedupe semantics"),
    ] = Keep.first,
    dedupe_by_key: Annotated[
        int | None,
        typer.Option(
            "--dedupe-by-key",
            help="Dedupe by the Nth field (needs -t)",
            metavar="N",
            rich_help_panel="dedupe semantics",
        ),
    ] = None,
    match: Annotated[
        str | None,
        typer.Option(
            "-m", "--match", help="Keep only lines matching REGEX", metavar="REGEX", rich_help_panel="filtering"
        ),
    ] = None,
    invert_match: Annotated[
        bool,
        typer.Option("-v", "--invert-match", help="Invert --match (drop matching lines)", rich_help_panel="filtering"),
    ] = False,
    min_length: Annotated[
        int | None,
        typer.Option("--min-length", help="Discard lines shorter than N", metavar="N", rich_help_panel="filtering"),
    ] = None,
    max_length: Annotated[
        int | None,
        typer.Option("--max-length", help="Discard lines longer than N", metavar="N", rich_help_panel="filtering"),
    ] = None,
    strip: Annotated[bool, typer.Option("--strip", help="str.strip() each line", rich_help_panel="filtering")] = False,
    ignore_comments: Annotated[
        bool, typer.Option("--ignore-comments", help="Drop comment lines", rich_help_panel="filtering")
    ] = False,
    comment_prefix: Annotated[
        str,
        typer.Option(
            "--comment-prefix",
            help="Comment prefix used by --ignore-comments (default '#')",
            metavar="S",
            rich_help_panel="filtering",
        ),
    ] = "#",
    normalize_whitespace: Annotated[
        bool,
        typer.Option(
            "--normalize-whitespace", help="Collapse runs of whitespace to one space", rich_help_panel="filtering"
        ),
    ] = False,
    skip_empty: Annotated[
        bool, typer.Option("--skip-empty", help="Skip blank lines", rich_help_panel="filtering")
    ] = False,
    lower: Annotated[bool, typer.Option("--lower", help="Lowercase output", rich_help_panel="filtering")] = False,
    upper: Annotated[bool, typer.Option("--upper", help="Uppercase output", rich_help_panel="filtering")] = False,
    prefix: Annotated[
        str, typer.Option("--prefix", help="Prepend S to every output line", metavar="S", rich_help_panel="filtering")
    ] = "",
    suffix: Annotated[
        str, typer.Option("--suffix", help="Append S to every output line", metavar="S", rich_help_panel="filtering")
    ] = "",
    reverse_lines: Annotated[
        bool, typer.Option("--reverse-lines", help="Reverse line order without sorting", rich_help_panel="filtering")
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Emit at most N output lines", metavar="N", rich_help_panel="filtering"),
    ] = None,
    head: Annotated[
        int | None, typer.Option("--head", help="Alias for --limit", metavar="N", rich_help_panel="filtering")
    ] = None,
    sample: Annotated[
        int | None, typer.Option("--sample", help="Reservoir-sample N lines", metavar="N", rich_help_panel="filtering")
    ] = None,
    sample_rate: Annotated[
        float | None,
        typer.Option(
            "--sample-rate", help="Keep each line with probability P (0..1)", metavar="P", rich_help_panel="filtering"
        ),
    ] = None,
    number: Annotated[
        bool, typer.Option("-N", "--number", help="Prefix each line with its 1-based index", rich_help_panel="output")
    ] = False,
    null_terminated: Annotated[
        bool,
        typer.Option(
            "-z", "--null-terminated", help="Separate lines with NUL instead of newline", rich_help_panel="output"
        ),
    ] = False,
    no_trailing_newline: Annotated[
        bool, typer.Option("--no-trailing-newline", help="Omit the final newline", rich_help_panel="output")
    ] = False,
    crlf: Annotated[bool, typer.Option("--crlf", help="Emit CRLF line endings", rich_help_panel="output")] = False,
    output_format: Annotated[
        Output_format,
        typer.Option("--output-format", help="Structured output format (default: text)", rich_help_panel="output"),
    ] = Output_format.text,
    quiet: Annotated[
        bool, typer.Option("-q", "--quiet", help="Suppress the trailing summary", rich_help_panel="output")
    ] = False,
    no_stats: Annotated[
        bool, typer.Option("--no-stats", help="Skip the trailing summary", rich_help_panel="output")
    ] = False,
    stats_file: Annotated[
        str | None,
        typer.Option("--stats-file", help="Write stats as JSON to FILE", metavar="FILE", rich_help_panel="output"),
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check", help="Exit non-zero if input is not sorted/unique", rich_help_panel="safety / verification"
        ),
    ] = False,
    check_only_duplicates: Annotated[
        bool,
        typer.Option(
            "--check-only-duplicates",
            help="Report duplicates without writing output",
            rich_help_panel="safety / verification",
        ),
    ] = False,
    backup: Annotated[
        bool,
        typer.Option(
            "--backup",
            help="Write a backup copy before modifying a file in place",
            rich_help_panel="safety / verification",
        ),
    ] = False,
    backup_suffix: Annotated[
        str,
        typer.Option(
            "--backup-suffix",
            help="Suffix used for backup files (default: .bak)",
            metavar="SUFFIX",
            rich_help_panel="safety / verification",
        ),
    ] = ".bak",
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Process everything but do not write any output", rich_help_panel="safety / verification"
        ),
    ] = False,
    fsync: Annotated[
        bool,
        typer.Option(
            "--fsync", help="fsync() the output file before the atomic rename", rich_help_panel="safety / verification"
        ),
    ] = False,
    preserve_permissions: Annotated[
        bool,
        typer.Option(
            "--preserve-permissions",
            help="Copy the input file mode onto the output",
            rich_help_panel="safety / verification",
        ),
    ] = False,
    preserve_mtime: Annotated[
        bool,
        typer.Option(
            "--preserve-mtime",
            help="Copy the input file mtime onto the output",
            rich_help_panel="safety / verification",
        ),
    ] = False,
    analyze: Annotated[
        bool,
        typer.Option(
            "--analyze", help="Print a per-file analysis report and exit", rich_help_panel="safety / verification"
        ),
    ] = False,
    report_file: Annotated[
        str | None,
        typer.Option(
            "--report-file",
            help="Write a JSON summary report to FILE",
            metavar="FILE",
            rich_help_panel="safety / verification",
        ),
    ] = None,
    no_mmap: Annotated[
        bool, typer.Option("--no-mmap", help="Disable mmap; always use buffered reads", rich_help_panel="performance")
    ] = False,
    mmap_threshold: Annotated[
        int,
        typer.Option(
            "--mmap-threshold",
            help=f"Files larger than BYTES use mmap (default: {MMAP_THRESHOLD_BYTES})",
            metavar="BYTES",
            rich_help_panel="performance",
        ),
    ] = MMAP_THRESHOLD_BYTES,
    buffer_size: Annotated[
        int,
        typer.Option(
            "--buffer-size",
            help=f"Read buffer size (default: {DEFAULT_BUFFER_SIZE})",
            metavar="BYTES",
            rich_help_panel="performance",
        ),
    ] = DEFAULT_BUFFER_SIZE,
    in_memory_threshold: Annotated[
        int,
        typer.Option(
            "--in-memory-threshold",
            help=f"Inputs larger than BYTES use external sort (default: {IN_MEMORY_THRESHOLD_BYTES})",
            metavar="BYTES",
            rich_help_panel="performance",
        ),
    ] = IN_MEMORY_THRESHOLD_BYTES,
    chunk_lines: Annotated[
        int,
        typer.Option(
            "--chunk-lines",
            help=f"Lines per external-sort chunk (default: {DEFAULT_CHUNK_LINES})",
            metavar="N",
            rich_help_panel="performance",
        ),
    ] = DEFAULT_CHUNK_LINES,
    verbose: Annotated[bool, typer.Option("--verbose", help="Emit progress diagnostics to stderr")] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="show program's version number and exit"
        ),
    ] = False,
) -> None:
    _STATE.update(
        filenames=filenames,
        output=output,
        output_dir=output_dir,
        encoding=encoding,
        from_line=from_line,
        to_line=to_line,
        sort=sort,
        unique=unique,
        reverse=reverse,
        case_insensitive=case_insensitive,
        numeric=numeric,
        version_sort=version_sort,
        random=random,
        ignore_case_sort=ignore_case_sort,
        ignore_leading_blanks=ignore_leading_blanks,
        dictionary_order=dictionary_order,
        human_numeric=human_numeric,
        month_sort=month_sort,
        key=key,
        field_separator=field_separator,
        locale=locale,
        sort_by_length=sort_by_length,
        count=count,
        repeated=repeated,
        repeated_once=repeated_once,
        unique_only=unique_only,
        dedupe_ignore_case=dedupe_ignore_case,
        keep=keep,
        dedupe_by_key=dedupe_by_key,
        match=match,
        invert_match=invert_match,
        min_length=min_length,
        max_length=max_length,
        strip=strip,
        ignore_comments=ignore_comments,
        comment_prefix=comment_prefix,
        normalize_whitespace=normalize_whitespace,
        skip_empty=skip_empty,
        lower=lower,
        upper=upper,
        prefix=prefix,
        suffix=suffix,
        reverse_lines=reverse_lines,
        limit=limit,
        head=head,
        sample=sample,
        sample_rate=sample_rate,
        number=number,
        null_terminated=null_terminated,
        no_trailing_newline=no_trailing_newline,
        crlf=crlf,
        output_format=output_format,
        quiet=quiet,
        no_stats=no_stats,
        stats_file=stats_file,
        check=check,
        check_only_duplicates=check_only_duplicates,
        backup=backup,
        backup_suffix=backup_suffix,
        dry_run=dry_run,
        fsync=fsync,
        preserve_permissions=preserve_permissions,
        preserve_mtime=preserve_mtime,
        analyze=analyze,
        report_file=report_file,
        no_mmap=no_mmap,
        mmap_threshold=mmap_threshold,
        buffer_size=buffer_size,
        in_memory_threshold=in_memory_threshold,
        chunk_lines=chunk_lines,
        verbose=verbose,
    )
    _run()


if __name__ == "__main__":
    app()
