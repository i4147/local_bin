#!/data/data/com.termux/files/usr/bin/env python

import argparse
import bz2
import hashlib
import importlib.util
import json
import lzma
import multiprocessing as mp
import os
import queue
import shutil
import struct
import sys
import tarfile
import tempfile
import time
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, wait
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from multiprocessing.queues import Queue as MPQueue
from pathlib import Path
from typing import Final, TypeAlias

from dh import MAX_WORKERS
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

CHUNK: Final[int] = 1 << 20
REPORT_INTERVAL: Final[float] = 0.1
POLL_INTERVAL: Final[float] = 0.05
EXTREME: Final[int] = lzma.PRESET_EXTREME
PPMD_MAGIC: Final[bytes] = b"PPMD"
PPMD_HEADER: Final[struct.Struct] = struct.Struct(">4sBIQ")
ORDER: Final[list[str]] = ["brotli", "pyppmd", "zstd", "xz", "py7zr", "bz2"]
SUFFIX: Final[dict[str, str]] = {
    "zstd": ".zst",
    "brotli": ".br",
    "py7zr": ".7z",
    "bz2": ".bz2",
    "xz": ".xz",
    "pyppmd": ".ppmd",
}
MODULE: Final[dict[str, str]] = {
    "zstd": "zstandard",
    "brotli": "brotli",
    "py7zr": "py7zr",
    "bz2": "bz2",
    "xz": "lzma",
    "pyppmd": "pyppmd",
}
LEVELS: Final[dict[str, dict[str, int]]] = {
    "max": {
        "zstd": 20,
        "brotli": 11,
        "xz": 9 | EXTREME,
        "bz2": 9,
        "py7zr": 9 | EXTREME,
        "pyppmd": 16,
    },
    "balanced": {
        "zstd": 19,
        "brotli": 9,
        "xz": 6,
        "bz2": 9,
        "py7zr": 6,
        "pyppmd": 8,
    },
    "fast": {
        "zstd": 3,
        "brotli": 5,
        "xz": 3,
        "bz2": 6,
        "py7zr": 3,
        "pyppmd": 6,
    },
}
Message: TypeAlias = tuple[str, int, int | None, str]
_queue: MPQueue[Message] | None = None


class CliError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Codec:
    feed: Callable[[bytes], bytes]
    finish: Callable[[], bytes]
    header: bytes = b""
    skip: int = 0


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    src: Path
    dst: Path
    size: int
    level: int
    digest: str | None


@dataclass(slots=True)
class Result:
    name: str
    path: str
    size: int
    seconds: float
    verified: bool | None
    error: str | None


def _init_worker(q: MPQueue[Message]) -> None:
    global _queue
    _queue = q


class Reporter:
    def __init__(self, name: str) -> None:
        self.name = name
        self._last = 0.0

    def __call__(
        self,
        done: int,
        total: int | None,
        status: str = "running",
        force: bool = False,
    ) -> None:
        now = time.perf_counter()
        if force or now - self._last >= REPORT_INTERVAL:
            self._last = now
            if _queue is not None:
                _queue.put((self.name, done, total, status))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def part_path(dst: Path) -> Path:
    return dst.with_name(dst.name + ".part")


def zstd_encoder(level: int, size: int) -> Codec:
    import zstandard as zstd

    cctx: zstd.ZstdCompressor | None = None
    if level >= 20:
        try:
            params = zstd.ZstdCompressionParameters.from_level(level, window_log=27, enable_ldm=1)
            cctx = zstd.ZstdCompressor(compression_params=params)
        except (TypeError, ValueError, zstd.ZstdError):
            cctx = None
    if cctx is None:
        cctx = zstd.ZstdCompressor(level=level)
    obj = cctx.compressobj(size=size)
    return Codec(obj.compress, obj.flush)


def zstd_decoder(path: Path) -> Codec:
    import zstandard as zstd

    obj = zstd.ZstdDecompressor(max_window_size=1 << 31).decompressobj()
    return Codec(obj.decompress, lambda: b"")


def brotli_encoder(level: int, size: int) -> Codec:
    import brotli

    comp = brotli.Compressor(quality=level, lgwin=24, mode=brotli.MODE_TEXT)
    return Codec(comp.process, comp.finish)


def brotli_decoder(path: Path) -> Codec:
    import brotli

    dec = brotli.Decompressor()
    return Codec(dec.process, lambda: b"")


def bz2_encoder(level: int, size: int) -> Codec:
    comp = bz2.BZ2Compressor(level)
    return Codec(comp.compress, comp.flush)


def bz2_decoder(path: Path) -> Codec:
    dec = bz2.BZ2Decompressor()
    return Codec(dec.decompress, lambda: b"")


def xz_encoder(level: int, size: int) -> Codec:
    comp = lzma.LZMACompressor(format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=level)
    return Codec(comp.compress, comp.flush)


def xz_decoder(path: Path) -> Codec:
    dec = lzma.LZMADecompressor()
    return Codec(dec.decompress, lambda: b"")


def ppmd_encoder(level: int, size: int) -> Codec:
    import pyppmd

    mem = min(max(16 << 20, size), 256 << 20)
    enc = pyppmd.Ppmd7Encoder(level, mem)
    header = PPMD_HEADER.pack(PPMD_MAGIC, level, mem, size)
    return Codec(enc.encode, lambda: enc.flush(endmark=True), header)


def ppmd_decoder(path: Path) -> Codec:
    import pyppmd

    with path.open("rb") as f:
        magic, order, mem, _ = PPMD_HEADER.unpack(f.read(PPMD_HEADER.size))
    if magic != PPMD_MAGIC:
        raise ValueError("invalid ppmd header")
    dec = pyppmd.Ppmd7Decoder(order, mem)
    return Codec(dec.decode, dec.flush, skip=PPMD_HEADER.size)


ENCODERS: Final[dict[str, Callable[[int, int], Codec]]] = {
    "zstd": zstd_encoder,
    "brotli": brotli_encoder,
    "bz2": bz2_encoder,
    "xz": xz_encoder,
    "pyppmd": ppmd_encoder,
}
DECODERS: Final[dict[str, Callable[[Path], Codec]]] = {
    "zstd": zstd_decoder,
    "brotli": brotli_decoder,
    "bz2": bz2_decoder,
    "xz": xz_decoder,
    "pyppmd": ppmd_decoder,
}


def stream_compress(src: Path, dst: Path, codec: Codec, total: int, report: Reporter) -> None:
    done = 0
    with src.open("rb") as fi, dst.open("wb") as fo:
        fo.write(codec.header)
        while chunk := fi.read(CHUNK):
            fo.write(codec.feed(chunk))
            done += len(chunk)
            report(done, total)
        fo.write(codec.finish())
    report(total, total, force=True)


def compress_7z(src: Path, dst: Path, level: int, report: Reporter) -> None:
    import py7zr

    filters = [{"id": py7zr.FILTER_LZMA2, "preset": level}]
    report(0, None, force=True)
    with py7zr.SevenZipFile(dst, "w", filters=filters) as z:
        z.write(src, arcname=src.name)


def verify_output(job: Job, path: Path, report: Reporter) -> bool:
    expected = job.digest
    if expected is None:
        raise ValueError("no digest available for verification")
    if job.name == "py7zr":
        import py7zr

        report(0, job.size, "verifying", force=True)
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            with py7zr.SevenZipFile(path) as z:
                z.extractall(path=tmp)
            return sha256_file(Path(tmp) / job.src.name) == expected
    codec = DECODERS[job.name](path)
    h = hashlib.sha256()
    done = 0
    with path.open("rb") as f:
        f.seek(codec.skip)
        while chunk := f.read(CHUNK):
            out = codec.feed(chunk)
            h.update(out)
            done += len(out)
            report(done, job.size, f"verifying {done * 100 // max(job.size, 1)}%")
        h.update(codec.finish())
    return h.hexdigest() == expected


def run_job(job: Job) -> Result:
    report = Reporter(job.name)
    part = part_path(job.dst)
    verified: bool | None = None
    start = time.perf_counter()
    elapsed = 0.0
    try:
        report(0, job.size, force=True)
        if job.name == "py7zr":
            compress_7z(job.src, part, job.level, report)
        else:
            codec = ENCODERS[job.name](job.level, job.size)
            stream_compress(job.src, part, codec, job.size, report)
        elapsed = time.perf_counter() - start
        if job.digest is not None:
            verified = False
            verified = verify_output(job, part, report)
            if not verified:
                raise ValueError("verification failed: checksum mismatch")
        os.replace(part, job.dst)
        return Result(job.name, str(job.dst), job.dst.stat().st_size, elapsed, verified, None)
    except Exception:
        part.unlink(missing_ok=True)
        return Result(
            job.name,
            str(job.dst),
            0,
            elapsed or time.perf_counter() - start,
            verified,
            traceback.format_exc(),
        )


def drain(
    q: MPQueue[Message],
    progress: Progress,
    tasks: dict[str, TaskID],
    started: set[str],
    finished: set[str],
) -> None:
    while True:
        try:
            name, done, total, status = q.get_nowait()
        except queue.Empty:
            return
        if name in finished:
            continue
        tid = tasks[name]
        if name not in started:
            progress.start_task(tid)
            started.add(name)
        if status == "running":
            progress.update(tid, completed=done, total=total, status="[cyan]running[/]")
        else:
            progress.update(tid, status=f"[yellow]{status}[/]")


def run_jobs(jobs: list[Job], workers: int, progress: Progress) -> list[Result]:
    ctx = mp.get_context("spawn")
    q: MPQueue[Message] = ctx.Queue()
    tasks: dict[str, TaskID] = {
        j.name: progress.add_task(
            j.name,
            total=None if j.name == "py7zr" else j.size,
            start=False,
            status="[dim]queued[/]",
        )
        for j in jobs
    }
    started: set[str] = set()
    finished: set[str] = set()
    results: dict[str, Result] = {}
    executor = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_init_worker,
        initargs=(q,),
    )
    pending: dict[Future[Result], Job] = {executor.submit(run_job, j): j for j in jobs}
    try:
        while pending:
            drain(q, progress, tasks, started, finished)
            done, _ = wait(list(pending), timeout=POLL_INTERVAL)
            for fut in done:
                job = pending.pop(fut)
                try:
                    res = fut.result()
                except Exception:
                    res = Result(job.name, str(job.dst), 0, 0.0, None, traceback.format_exc())
                results[job.name] = res
                finished.add(job.name)
                tid = tasks[job.name]
                if job.name not in started:
                    progress.start_task(tid)
                    started.add(job.name)
                if res.error:
                    progress.update(tid, status="[red]failed[/]")
                else:
                    tag = " + verified" if res.verified else ""
                    progress.update(
                        tid,
                        completed=job.size,
                        total=job.size,
                        status=f"[green]done{tag}[/]",
                    )
                progress.stop_task(tid)
    except KeyboardInterrupt:
        executor.shutdown(wait=False, cancel_futures=True)
        for job in jobs:
            if job.name not in finished:
                part_path(job.dst).unlink(missing_ok=True)
        raise
    executor.shutdown(wait=True)
    return [results[j.name] for j in jobs]


def ensure_space(directory: Path, needed: int) -> None:
    free = shutil.disk_usage(directory).free
    if free < needed + (needed >> 5) + (1 << 20):
        raise CliError(f"not enough free space in {directory}: need ~{needed / 1e6:,.0f} MB, have {free / 1e6:,.0f} MB")


def build_tar(folder: Path, dest: Path, root_name: str, console: Console) -> list[str]:
    entries = sorted(folder.rglob("*"))
    total = 0
    for p in entries:
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    ensure_space(dest.parent, total)
    skipped: list[str] = []
    columns = (
        TextColumn("[bold]tar[/]"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
    )
    with Progress(*columns, console=console) as progress:
        task = progress.add_task("tar", total=total)
        with tarfile.open(dest, "w") as tar:
            tar.add(folder, arcname=root_name, recursive=False)
            for p in entries:
                if p == dest:
                    continue
                try:
                    arc = str(Path(root_name) / p.relative_to(folder))
                    tar.add(p, arcname=arc, recursive=False)
                    if p.is_file() and not p.is_symlink():
                        progress.advance(task, p.stat().st_size)
                except OSError as exc:
                    skipped.append(f"{p}: {exc}")
    return skipped


def resolve_algos(requested: Sequence[str], console: Console) -> list[str]:
    wanted = [a for a in ORDER if a in set(requested)]
    available: list[str] = []
    for name in wanted:
        if importlib.util.find_spec(MODULE[name]) is None:
            console.print(f"[yellow]skipping {name}: module '{MODULE[name]}' is not installed[/]")
        else:
            available.append(name)
    if not available:
        raise CliError("no usable algorithms")
    return available


def print_report(console: Console, results: list[Result], orig: int, wall: float) -> None:
    ok = sorted((r for r in results if not r.error), key=lambda r: r.size)
    bad = [r for r in results if r.error]
    fastest = min(ok, key=lambda r: r.seconds).name if ok else None
    table = Table(header_style="bold", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("algorithm")
    table.add_column("size", justify="right")
    table.add_column("ratio", justify="right")
    table.add_column("saved", justify="right")
    table.add_column("time", justify="right")
    table.add_column("speed", justify="right")
    table.add_column("verified", justify="center")
    table.add_column("output", overflow="fold")
    for rank, r in enumerate(ok, 1):
        ratio = orig / r.size if r.size else 0.0
        saved = (1 - r.size / orig) * 100 if orig else 0.0
        speed = orig / r.seconds / 1e6 if r.seconds else 0.0
        size_cell = f"{r.size:,}"
        time_cell = f"{r.seconds:.1f}s"
        if rank == 1:
            size_cell = f"[bold green]{size_cell}[/]"
        if r.name == fastest:
            time_cell = f"[bold green]{time_cell}[/]"
        mark = "-" if r.verified is None else ("[green]yes[/]" if r.verified else "[red]no[/]")
        table.add_row(
            str(rank),
            r.name,
            size_cell,
            f"{ratio:.2f}x",
            f"{saved:.1f}%",
            time_cell,
            f"{speed:.1f} MB/s",
            mark,
            r.path,
        )
    for r in bad:
        table.add_row("-", r.name, "[red]ERROR[/]", "", "", f"{r.seconds:.1f}s", "", "", "")
    console.print(table)
    for r in bad:
        console.print(Panel(Text(r.error or ""), title=r.name, border_style="red"))
    console.print(f"Wall time: {wall:.1f}s")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="compress_txt.py")
    p.add_argument("path", type=Path)
    p.add_argument("-o", "--out-dir", default="output", type=Path)
    p.add_argument("-p", "--preset", choices=list(LEVELS), default="max")
    p.add_argument("-a", "--algos", nargs="+", choices=ORDER, default=ORDER, metavar="ALGO")
    p.add_argument("--verify", action="store_true", default=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--keep-tar", action="store_true")
    p.add_argument("--tmp-dir", type=Path, default=os.environ.get("TMPDIR"))
    p.add_argument("--json", type=Path, dest="json_path")
    return p.parse_args(argv)


def run(args: argparse.Namespace, console: Console) -> int:
    src: Path = args.path.expanduser().resolve()
    if not src.exists():
        raise CliError(f"not found: {src}")
    if not (src.is_file() or src.is_dir()):
        raise CliError(f"unsupported path type: {src}")
    if MAX_WORKERS < 1:
        raise CliError("--workers must be >= 1")
    out_dir: Path = args.out_dir.expanduser().resolve() if args.out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    names = resolve_algos(args.algos, console)
    base = src.name or "root"
    out_base = f"{base}.tar" if src.is_dir() else base
    outputs = {n: out_dir / f"{out_base}{SUFFIX[n]}" for n in names}
    clashes = [str(p) for p in outputs.values() if p.exists()]
    if clashes and not args.force:
        raise CliError("outputs already exist (use --force):\n  " + "\n  ".join(clashes))
    with ExitStack() as stack:
        if src.is_dir():
            if args.keep_tar:
                input_path = out_dir / out_base
            else:
                tmp_root = args.tmp_dir.expanduser().resolve() if args.tmp_dir else None
                tmp = stack.enter_context(tempfile.TemporaryDirectory(dir=tmp_root, prefix="compress_"))
                input_path = Path(tmp) / out_base
            console.print(f"Archiving folder: {src}")
            skipped = build_tar(src, input_path, base, console)
            for line in skipped:
                console.print(f"[yellow]skipped {line}[/]")
        else:
            input_path = src
        orig = input_path.stat().st_size
        console.print(f"Input   : {input_path}")
        console.print(f"Size    : {orig:,} bytes")
        console.print(
            f"Preset  : {args.preset}   Algorithms: {', '.join(names)}   Workers: {min(MAX_WORKERS, len(names))}"
        )
        digest: str | None = None
        if args.verify:
            with console.status("Hashing source for verification..."):
                digest = sha256_file(input_path)
        levels = LEVELS[args.preset]
        jobs = [Job(n, input_path, outputs[n], orig, levels[n], digest) for n in names]
        columns = (
            TextColumn("[bold]{task.description:<8}"),
            BarColumn(),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[status]}"),
        )
        t0 = time.perf_counter()
        with Progress(*columns, console=console, refresh_per_second=10) as progress:
            results = run_jobs(jobs, min(MAX_WORKERS, len(jobs)), progress)
        wall = time.perf_counter() - t0
    console.print()
    print_report(console, results, orig, wall)
    if args.json_path:
        payload = {
            "input": str(src),
            "size": orig,
            "preset": args.preset,
            "wall_seconds": wall,
            "results": [asdict(r) for r in results],
        }
        args.json_path.write_text(json.dumps(payload, indent=2))
    return 0 if all(r.error is None for r in results) else 2


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()
    try:
        return run(args, console)
    except CliError as exc:
        console.print(f"[red]error:[/] {exc}")
        return 1
    except KeyboardInterrupt:
        console.print("[yellow]interrupted[/]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
