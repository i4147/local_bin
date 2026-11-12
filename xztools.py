#!/data/data/com.termux/files/usr/bin/env python

import argparse
import contextlib
import io
import lzma
import os
import shutil
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Optional, Sequence

from dh import SKIP_DIRS, MAX_WORKERS
from loguru import logger

try:
    import lzma_mt

    _HAS_LZMA_MT: bool = True
except ImportError:
    _HAS_LZMA_MT = False
try:
    import pylzma

    _HAS_PYLZMA: bool = True
except ImportError:
    _HAS_PYLZMA = False
try:
    from loguru import logger

    _HAS_LOGURU = True
except ImportError:
    _HAS_LOGURU = False

    class _FallbackLogger:
        def info(self, m: str) -> None:
            print(m)

        def warning(self, m: str) -> None:
            print(f"WARN: {m}", file=sys.stderr)

        def error(self, m: str) -> None:
            print(f"ERROR: {m}", file=sys.stderr)

        def success(self, m: str) -> None:
            print(m)

        def debug(self, m: str) -> None:
            pass

        def remove(self) -> None:
            pass

        def add(self, *a: Any, **k: Any) -> None:
            pass

    logger = _FallbackLogger()
DEFAULT_SKIP_EXTS: set[str] = {
    ".zip",
    ".br",
    ".xz",
    ".gz",
    ".bz2",
    ".bz3",
    ".zst",
    ".7z",
    ".lz4",
    ".rar",
    ".tar",
    ".tgz",
    ".tbz",
    ".tbz2",
    ".z",
    ".lz",
    ".lzma",
    ".xza",
    ".mkv",
    ".mp4",
    ".webm",
    ".avi",
    ".mov",
    ".flv",
    ".wmv",
    ".m4v",
    ".mpg",
    ".mpeg",
    ".mp3",
    ".aac",
    ".flac",
    ".wav",
    ".m4a",
    ".opus",
    ".ogg",
    ".wma",
    ".alac",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".svg",
    ".tiff",
    ".ico",
    ".heic",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".iso",
    ".img",
    ".pyc",
    ".pyo",
    ".class",
    ".o",
    ".obj",
}
DEFAULT_THREADS: int = 4
DEFAULT_PRESET: int = 9
DEFAULT_CHUNK_SIZE: int = 1024 * 1024
DEFAULT_PYLZMA_CHUNK: int = 512 * 1024
DEFAULT_PYLZMA_DICT: int = 256 * 1024 * 1024
PYLZMA_MEM_THRESHOLD: int = 512 * 1024
XZ_SUFFIX: str = ".xz"
SEVENZ_SUFFIX: str = ".7z"
TAR_SUFFIX: str = ".tar"
TAR_XZ_SUFFIX: str = ".tar.xz"
LZMA_SUFFIX: str = ".lzma"
if _HAS_PYLZMA:
    _PYLZMA_FILTERS = [
        {
            "id": pylzma.FILTER_LZMA1,
            "preset": 9 | pylzma.PRESET_EXTREME,
            "dict_size": DEFAULT_PYLZMA_DICT,
        },
    ]
else:
    _PYLZMA_FILTERS = []


def format_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} PB"


def dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _is_skipped_dir(path: Path, skip_dirs: set[str]) -> bool:
    return any(part in skip_dirs for part in path.parts)


def _matches_exclude(path: Path, patterns: Sequence[str]) -> bool:
    if not patterns:
        return False
    s = str(path)
    return any(p in s for p in patterns)


def discover_files(
    root: Path,
    *,
    mode: str = "compress",
    extensions: Optional[Sequence[str]] = None,
    skip_extensions: Optional[set[str]] = None,
    exclude_patterns: Optional[Sequence[str]] = None,
    skip_dirs: Optional[set[str]] = None,
    recursive: bool = True,
    skip_symlinks: bool = True,
) -> list[Path]:
    skip_dirs = skip_dirs or SKIP_DIRS
    exclude_patterns = list(exclude_patterns or [])
    ext_filter: set[str] = set()
    if extensions:
        for e in extensions:
            ext_filter.add((e if e.startswith(".") else "." + e).lower())
    iterator = root.rglob("*") if recursive else root.glob("*")
    out: list[Path] = []
    for p in iterator:
        if skip_symlinks and p.is_symlink():
            continue
        if not p.is_file():
            continue
        if _is_skipped_dir(p, skip_dirs):
            continue
        if _matches_exclude(p, exclude_patterns):
            continue
        suffix = p.suffix.lower()
        if mode == "decompress":
            if suffix != XZ_SUFFIX:
                continue
        else:
            if ext_filter and suffix not in ext_filter:
                continue
            if skip_extensions and suffix in skip_extensions:
                continue
        out.append(p)
    return sorted(out)


def compress_bytes(
    data: bytes,
    *,
    preset: int = DEFAULT_PRESET,
    threads: int = DEFAULT_THREADS,
    backend: str = "auto",
) -> bytes:
    if backend in ("auto", "lzma_mt", "lzmamt") and _HAS_LZMA_MT:
        return lzma_mt.compress(data, preset=preset, threads=threads)
    if backend in ("lzma_mt", "lzmamt"):
        msg = "lzma_mt requested but not installed. `pip install lzma_mt` or pass --backend lzma"
        raise RuntimeError(msg)
    lvl = preset | lzma.PRESET_EXTREME if preset == 9 else preset
    return lzma.compress(data, format=lzma.FORMAT_XZ, preset=lvl)


def decompress_bytes(data: bytes, *, backend: str = "auto") -> bytes:
    if backend in ("auto", "lzma_mt", "lzmamt") and _HAS_LZMA_MT:
        return lzma_mt.decompress(data)
    if backend in ("lzma_mt", "lzmamt"):
        msg = "lzma_mt requested but not installed."
        raise RuntimeError(msg)
    return lzma.decompress(data, format=lzma.FORMAT_XZ)


@dataclass
class FileResult:
    path: Path
    success: bool
    message: str = ""
    original_size: int = 0
    processed_size: int = 0
    duration: float = 0.0
    was_tarred: bool = False


def _worker_compress_xz(args: tuple[str, int, int, str, bool, bool]) -> FileResult:
    path_str, preset, threads, backend, remove_orig, dry_run = args
    src = Path(path_str)
    dst = src.with_name(src.name + XZ_SUFFIX)
    t0 = time.monotonic()
    if dry_run:
        return FileResult(src, True, f"[dry-run] would compress → {dst.name}")
    if dst.exists():
        return FileResult(src, False, f"{dst.name} already exists — skipped")
    try:
        data = src.read_bytes()
        cdata = compress_bytes(data, preset=preset, threads=threads, backend=backend)
        dst.write_bytes(cdata)
        if remove_orig:
            src.unlink()
        return FileResult(
            path=src,
            success=True,
            message=f"→ {dst.name}",
            original_size=len(data),
            processed_size=len(cdata),
            duration=time.monotonic() - t0,
        )
    except Exception as exc:
        with contextlib.suppress(OSError):
            if dst.exists():
                dst.unlink()
        return FileResult(src, False, f"Error: {exc}", duration=time.monotonic() - t0)


def _worker_decompress_xz(args: tuple[str, bool, bool]) -> FileResult:
    path_str, remove_orig, handle_tar_xz = args
    src = Path(path_str)
    t0 = time.monotonic()
    if src.suffix.lower() != XZ_SUFFIX:
        return FileResult(src, False, "not a .xz file")
    try:
        if handle_tar_xz and src.name.endswith(TAR_XZ_SUFFIX):
            out_dir = src.with_name(src.name[: -len(TAR_XZ_SUFFIX)])
            out_dir.mkdir(exist_ok=True)
            with tarfile.open(src, "r:xz") as tar:
                tar.extractall(path=out_dir, filter="data")
            if remove_orig:
                src.unlink()
            size = dir_size(out_dir)
            return FileResult(
                src,
                True,
                f"extracted → {out_dir.name}/",
                original_size=src.stat().st_size if src.exists() else 0,
                processed_size=size,
                duration=time.monotonic() - t0,
            )
        data = src.read_bytes()
        out = decompress_bytes(data)
        dst = src.with_suffix("")
        dst.write_bytes(out)
        if remove_orig:
            src.unlink()
        return FileResult(
            path=src,
            success=True,
            message=f"→ {dst.name}",
            original_size=len(data),
            processed_size=len(out),
            duration=time.monotonic() - t0,
        )
    except Exception as exc:
        return FileResult(src, False, f"Error: {exc}", duration=time.monotonic() - t0)


def _worker_tar_dir(args: tuple[str, int, bool, bool]) -> dict[str, Any]:
    dir_str, preset, verify, remove_orig = args
    src = Path(dir_str)
    dst = src.with_name(src.name + TAR_XZ_SUFFIX)
    t0 = time.monotonic()
    start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    orig_size = 0
    try:
        if not src.is_dir():
            msg = f"Not a directory: {src}"
            raise FileNotFoundError(msg)
        if dst.exists():
            msg = f"archive exists: {dst.name}"
            raise FileExistsError(msg)
        orig_size = dir_size(src)
        with (
            lzma.open(dst, "wb", preset=preset, check=lzma.CHECK_CRC64) as xz_f,
            tarfile.open(fileobj=xz_f, mode="w", dereference=False) as tar,
        ):
            tar.add(src, arcname=src.name, recursive=True)
        if verify:
            with tarfile.open(dst, "r:xz") as tar:
                for member in tar:
                    if member.isfile():
                        fh = tar.extractfile(member)
                        if fh is not None:
                            while fh.read(1024 * 1024):
                                pass
        comp_size = dst.stat().st_size
        if comp_size <= 0:
            msg = "Created archive is empty"
            raise OSError(msg)
        if remove_orig:
            shutil.rmtree(src)
        return {
            "subdir": src.name,
            "success": True,
            "message": f"OK   {src.name}",
            "start": start,
            "end": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed": time.monotonic() - t0,
            "original_size": orig_size,
            "compressed_size": comp_size,
        }
    except Exception as exc:
        with contextlib.suppress(OSError):
            if dst.exists():
                dst.unlink()
        return {
            "subdir": src.name,
            "success": False,
            "message": f"FAIL {src.name}: {exc}",
            "start": start,
            "end": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed": time.monotonic() - t0,
            "original_size": orig_size,
            "compressed_size": 0,
        }


def _read_file_bytes(args: tuple[str]) -> tuple[str, bytes]:
    (path_str,) = args
    return (path_str, Path(path_str).read_bytes())


def _pylzma_compress_blob(data: bytes) -> bytes:
    if not _HAS_PYLZMA:
        msg = "pylzma is not installed (`pip install pylzma`)."
        raise RuntimeError(msg)
    return pylzma.compress(data, filters=_PYLZMA_FILTERS)


def _pylzma_decompress_blob(data: bytes) -> bytes:
    if not _HAS_PYLZMA:
        msg = "pylzma is not installed."
        raise RuntimeError(msg)
    return pylzma.decompress(data)


def _worker_pylzma_7z(args: tuple[str, bool, Optional[str], bool]) -> FileResult:
    path_str, keep, out_dir_str, _tar_subdirs = args
    src = Path(path_str)
    out_dir = Path(out_dir_str) if out_dir_str else None
    t0 = time.monotonic()
    try:
        if src.is_dir():
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                tar.add(src, arcname=src.name)
            cdata = _pylzma_compress_blob(buf.getvalue())
            dst = (
                (out_dir / f"{src.name}{TAR_SUFFIX}{SEVENZ_SUFFIX}")
                if out_dir
                else src.with_name(f"{src.name}{TAR_SUFFIX}{SEVENZ_SUFFIX}")
            )
            dst.write_bytes(cdata)
            if not keep:
                shutil.rmtree(src)
            return FileResult(
                src,
                True,
                f"→ {dst}",
                original_size=dir_size(src) if src.exists() else 0,
                processed_size=len(cdata),
                duration=time.monotonic() - t0,
                was_tarred=True,
            )
        else:
            data = src.read_bytes()
            cdata = _pylzma_compress_blob(data)
            dst = (out_dir / f"{src.name}{SEVENZ_SUFFIX}") if out_dir else src.with_name(f"{src.name}{SEVENZ_SUFFIX}")
            dst.write_bytes(cdata)
            if not keep:
                src.unlink()
            return FileResult(
                src,
                True,
                f"→ {dst}",
                original_size=len(data),
                processed_size=len(cdata),
                duration=time.monotonic() - t0,
            )
    except Exception as exc:
        return FileResult(src, False, f"Error: {exc}", duration=time.monotonic() - t0)


def _worker_pylzma_un7z(args: tuple[str, bool, Optional[str], bool]) -> FileResult:
    path_str, keep, out_dir_str, _ = args
    src = Path(path_str)
    out_dir = Path(out_dir_str) if out_dir_str else None
    t0 = time.monotonic()
    try:
        if not src.is_file():
            return FileResult(src, False, "not a file")
        data = src.read_bytes()
        raw = _pylzma_decompress_blob(data)
        if src.name.endswith(f"{TAR_SUFFIX}{SEVENZ_SUFFIX}"):
            stem = src.name[: -len(f"{TAR_SUFFIX}{SEVENZ_SUFFIX}")]
            target_dir = (out_dir / stem) if out_dir else src.with_name(stem)
            target_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tar:
                tar.extractall(path=target_dir, filter="data")
            if not keep:
                src.unlink()
            return FileResult(
                src,
                True,
                f"→ {target_dir}/",
                original_size=len(data),
                processed_size=dir_size(target_dir),
                duration=time.monotonic() - t0,
                was_tarred=True,
            )
        if src.name.endswith(SEVENZ_SUFFIX):
            stem = src.name[: -len(SEVENZ_SUFFIX)]
            dst = (out_dir / stem) if out_dir else src.with_name(stem)
            dst.write_bytes(raw)
            if not keep:
                src.unlink()
            return FileResult(
                src,
                True,
                f"→ {dst}",
                original_size=len(data),
                processed_size=len(raw),
                duration=time.monotonic() - t0,
            )
        return FileResult(src, False, "not a .7z / .tar.7z file")
    except Exception as exc:
        return FileResult(src, False, f"Error: {exc}", duration=time.monotonic() - t0)


def _pylzma_compress_chunk(chunk: bytes) -> bytes:
    return _pylzma_compress_blob(chunk)


def _chunked_compress_file(src: Path, dst: Path, chunk_size: int, workers: int) -> bool:
    data = src.read_bytes()
    total = len(data)
    n_chunks = (total + chunk_size - 1) // chunk_size
    chunks = [data[i * chunk_size : (i + 1) * chunk_size] for i in range(n_chunks)]
    with Pool(processes=workers) as pool:
        compressed = pool.map(_pylzma_compress_chunk, chunks)
    with dst.open("wb") as fh:
        fh.write(n_chunks.to_bytes(4, "big"))
        for blob in compressed:
            fh.write(len(blob).to_bytes(8, "big"))
            fh.write(blob)
    return True


def _chunked_decompress_file(src: Path, dst: Path) -> bool:
    with src.open("rb") as fin, dst.open("wb") as fout:
        header = fin.read(4)
        if len(header) != 4:
            msg = "invalid chunked header"
            raise ValueError(msg)
        n_chunks = int.from_bytes(header, "big")
        for _ in range(n_chunks):
            size_raw = fin.read(8)
            if len(size_raw) != 8:
                msg = "truncated chunk size"
                raise ValueError(msg)
            size = int.from_bytes(size_raw, "big")
            blob = fin.read(size)
            if len(blob) != size:
                msg = "truncated chunk"
                raise ValueError(msg)
            fout.write(_pylzma_decompress_blob(blob))
    return True


def _worker_lzma_chunk_compress(args: tuple[str, int, int, bool]) -> FileResult:
    path_str, chunk_size, workers, remove_orig = args
    src = Path(path_str)
    dst = src.with_suffix(src.suffix + LZMA_SUFFIX)
    t0 = time.monotonic()
    if dst.exists():
        return FileResult(src, False, f"{dst.name} exists — skipped")
    try:
        size = src.stat().st_size
        if size < PYLZMA_MEM_THRESHOLD:
            cdata = _pylzma_compress_blob(src.read_bytes())
            dst.write_bytes(cdata)
        else:
            _chunked_compress_file(src, dst, chunk_size, workers)
        csize = dst.stat().st_size
        if csize == 0:
            dst.unlink()
            return FileResult(src, False, "empty output")
        if csize >= size:
            dst.unlink()
            return FileResult(src, False, "no space saved — discarded")
        if remove_orig:
            src.unlink()
        return FileResult(
            src,
            True,
            f"→ {dst.name}",
            original_size=size,
            processed_size=csize,
            duration=time.monotonic() - t0,
        )
    except Exception as exc:
        with contextlib.suppress(OSError):
            if dst.exists():
                dst.unlink()
        return FileResult(src, False, f"Error: {exc}", duration=time.monotonic() - t0)


def _worker_lzma_chunk_decompress(args: tuple[str, bool]) -> FileResult:
    path_str, remove_orig = args
    src = Path(path_str)
    if not src.name.endswith(LZMA_SUFFIX):
        return FileResult(src, False, "not a .lzma file")
    dst = src.with_suffix("")
    t0 = time.monotonic()
    try:
        data_head = src.read_bytes()[:12]
        is_chunked = False
        if len(data_head) == 12:
            n = int.from_bytes(data_head[:4], "big")
            first = int.from_bytes(data_head[4:12], "big")
            if 0 < n < 10_000_000 and 0 < first <= src.stat().st_size:
                is_chunked = True
        if is_chunked:
            _chunked_decompress_file(src, dst)
        else:
            dst.write_bytes(_pylzma_decompress_blob(src.read_bytes()))
        if remove_orig:
            src.unlink()
        return FileResult(
            src,
            True,
            f"→ {dst.name}",
            original_size=src.stat().st_size if src.exists() else 0,
            processed_size=dst.stat().st_size,
            duration=time.monotonic() - t0,
        )
    except Exception as exc:
        with contextlib.suppress(OSError):
            if dst.exists():
                dst.unlink()
        return FileResult(src, False, f"Error: {exc}", duration=time.monotonic() - t0)


def _print_file_result(root: Path, r: FileResult, index: int = 0, total: int = 0) -> None:
    prefix = ""
    if total:
        pct = index / total * 100
        prefix = f"[{pct:5.1f}%] {index}/{total} "
    mark = "✓" if r.success else "✗"
    try:
        rel = r.path.relative_to(root)
    except ValueError:
        rel = r.path
    extra = ""
    if r.success and r.original_size and r.processed_size:
        ratio = r.processed_size / r.original_size * 100
        extra = f"  ({format_size(r.original_size)} → {format_size(r.processed_size)}, {ratio:.1f}%)"
    print(f"{prefix}{mark} {rel}: {r.message}{extra}")


def _print_summary(results: Sequence[FileResult]) -> None:
    ok = [r for r in results if r.success]
    fail = [r for r in results if not r.success]
    print("─" * 60)
    print(f"Total successful: {len(ok)}")
    print(f"Total failed:     {len(fail)}")
    if ok:
        orig = sum(r.original_size for r in ok)
        proc = sum(r.processed_size for r in ok)
        if orig:
            print(f"Total original size:   {format_size(orig)}")
            print(f"Total processed size:  {format_size(proc)}")
            if proc <= orig:
                print(f"Space saved:           {format_size(orig - proc)} ({(orig - proc) / orig * 100:.1f}%)")
            else:
                print(f"Size increase:         {format_size(proc - orig)} ({(proc - orig) / orig * 100:.1f}%)")


def _tar_top_level_dirs(root: Path, preset: int, workers: int) -> list[dict[str, Any]]:
    subdirs = [
        p
        for p in root.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.name.startswith(".") and p.name not in SKIP_DIRS
    ]
    if not subdirs:
        return []
    print(f"Taring {len(subdirs)} top-level subdirectories...")
    args = [(str(d), preset, True, True) for d in subdirs]
    with Pool(processes=min(workers, len(subdirs))) as pool:
        results = pool.map(_worker_tar_dir, args)
    for r in results:
        mark = "✅" if r["success"] else "❌"
        print(f"  {mark} {r['message']} ({format_size(r['original_size'])} → {format_size(r['compressed_size'])})")
    return results


def cmd_compress(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    if not root.is_dir():
        logger.error(f"{root} is not a directory")
        return 1
    if args.dry_run:
        print("[dry-run] no files will be modified")
    if args.tar_subdirs or args.auto_tar_dirs:
        _tar_top_level_dirs(root, args.preset, MAX_WORKERS)
    if args.no_skip_compressed:
        skip_exts: set[str] = {XZ_SUFFIX}
    else:
        skip_exts = DEFAULT_SKIP_EXTS
    files = discover_files(
        root,
        mode="compress",
        extensions=args.extensions,
        skip_extensions=skip_exts,
        exclude_patterns=args.exclude,
        recursive=not args.auto_tar_dirs,
    )
    if not files:
        print("No files found to compress")
        return 0
    print(f"Compressing {len(files)} file(s) with {'sequential mode' if args.sequential else f'{MAX_WORKERS} workers'}")
    print(f"Preset: {args.preset}, threads/job: {args.threads}, backend: {args.backend}")
    if args.dry_run:
        for f in files:
            print(f"  [dry-run] would compress {f.relative_to(root)}")
        return 0
    worker_args = [(str(f), args.preset, args.threads, args.backend, not args.keep_orig, False) for f in files]
    results: list[FileResult] = []
    if args.sequential or len(files) == 1:
        for i, wa in enumerate(worker_args, 1):
            r = _worker_compress_xz(wa)
            results.append(r)
            if args.verbose:
                _print_file_result(root, r, i, len(files))
    else:
        with Pool(processes=MAX_WORKERS) as pool:
            futures = [pool.apply_async(_worker_compress_xz, (wa,)) for wa in worker_args]
            for i, fut in enumerate(futures, 1):
                r = fut.get()
                results.append(r)
                if args.verbose:
                    _print_file_result(root, r, i, len(files))
    if not args.no_size_stats:
        _print_summary(results)
    return 0


def cmd_decompress(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    if not root.is_dir():
        logger.error(f"{root} is not a directory")
        return 1
    files = discover_files(
        root,
        mode="decompress",
        exclude_patterns=args.exclude,
    )
    if not files:
        print("No .xz files found to decompress")
        return 0
    print(f"Decompressing {len(files)} file(s)...")
    worker_args = [(str(f), not args.keep_orig, args.handle_tar_xz) for f in files]
    results: list[FileResult] = []
    if args.sequential or len(files) == 1:
        for i, wa in enumerate(worker_args, 1):
            r = _worker_decompress_xz(wa)
            results.append(r)
            _print_file_result(root, r, i, len(files))
    else:
        with Pool(processes=MAX_WORKERS) as pool:
            futures = [pool.apply_async(_worker_decompress_xz, (wa,)) for wa in worker_args]
            for i, fut in enumerate(futures, 1):
                r = fut.get()
                results.append(r)
                _print_file_result(root, r, i, len(files))
    _print_summary(results)
    return 0


def cmd_tar_dirs(args: argparse.Namespace) -> int:
    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        logger.error(f"Not a directory: {root}")
        return 1
    subdirs = sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")),
        key=lambda p: p.name.lower(),
    )
    if not subdirs:
        print(f"No non-hidden top-level subdirectories found in: {root}")
        return 0
    workers = min(MAX_WORKERS, len(subdirs))
    print(f"Directory:      {root}")
    print(f"Subdirectories: {len(subdirs)}")
    print(f"Workers:        {workers}")
    print(f"xz preset:      {args.preset}")
    print()
    worker_args = [(str(d), args.preset, not args.no_verify, not args.keep_orig) for d in subdirs]
    ok = fail = 0
    with Pool(processes=workers) as pool:
        for r in pool.imap_unordered(_worker_tar_dir, worker_args):
            mark = "✅" if r["success"] else "❌"
            if r["success"]:
                ratio = r["compressed_size"] / r["original_size"] * 100 if r["original_size"] else 0.0
                print(
                    f"{mark} {r['message']:<45} took={r['elapsed']:.1f}s "
                    f"{format_size(r['original_size'])} → "
                    f"{format_size(r['compressed_size'])} ({ratio:.1f}%)"
                )
                ok += 1
            else:
                print(f"{mark} {r['message']}  took={r['elapsed']:.1f}s")
                fail += 1
    print()
    print("Finished")
    print(f"Successful: {ok}")
    print(f"Failed:     {fail}")
    return 0 if fail == 0 else 1


def cmd_archive_cwd(args: argparse.Namespace) -> int:
    cwd = Path.cwd().resolve()
    parent = cwd.parent
    archive = parent / f"{cwd.name}{TAR_XZ_SUFFIX}"
    print(f"Current directory: {cwd}")
    print(f"Target archive:    {archive}")
    if archive.exists():
        logger.error(f"Archive already exists: {archive}")
        return 1
    files: list[Path] = []
    for p in cwd.rglob("*"):
        if p.is_file() and not p.is_symlink():
            files.append(p)
        elif p.is_symlink() and not p.exists():
            logger.warning(f"Skipping broken symlink: {p}")
    if not files:
        logger.error(f"No files found in {cwd}")
        return 1
    print(f"Reading {len(files)} file(s) with {MAX_WORKERS} worker(s)...")
    with Pool(processes=MAX_WORKERS) as pool:
        payload = pool.map(_read_file_bytes, [(str(f),) for f in files])
    print(f"Writing archive: {archive}")
    try:
        with tarfile.open(name=str(archive), mode="w:xz", preset=6, format=tarfile.PAX_FORMAT) as tar:
            for path_str, data in payload:
                src = Path(path_str)
                try:
                    rel = src.relative_to(cwd)
                except ValueError:
                    rel = Path(src.name)
                arcname = Path(cwd.name) / rel
                info = tarfile.TarInfo(name=str(arcname))
                info.size = len(data)
                info.mtime = src.stat().st_mtime
                tar.addfile(info, io.BytesIO(data))
    except Exception as exc:
        logger.exception(f"Writing archive failed: {exc}")
        with contextlib.suppress(OSError):
            archive.unlink()
        return 1
    try:
        with tarfile.open(archive, "r:xz") as tar:
            names = tar.getnames()
    except Exception as exc:
        logger.error(f"Verification failed: {exc}")
        with contextlib.suppress(OSError):
            archive.unlink()
        return 1
    prefix = f"{cwd.name}/"
    bad = [n for n in names if not n.startswith(prefix)]
    if bad:
        logger.error(f"Archive contains members outside '{prefix}': {bad[:5]}")
        archive.unlink()
        return 1
    if len(names) != len(files):
        logger.error(f"Member count mismatch: expected {len(files)}, got {len(names)}")
        archive.unlink()
        return 1
    os.chdir(parent)
    print(f"Removing original directory: {cwd}")
    shutil.rmtree(cwd)
    logger.success(f"Done. Archive: {archive}")
    return 0


def _collect_7z_targets(
    paths: Sequence[str],
    mode: str,
    cwd: Path,
    output_dir: Optional[Path],
) -> list[Path]:
    targets: list[Path] = []

    def add_compress_tree(base: Path) -> None:
        for p in base.rglob("*"):
            if p.is_file() and not p.name.endswith(SEVENZ_SUFFIX):
                targets.append(p.resolve())

    def add_decompress_tree(base: Path) -> None:
        for p in base.rglob(f"*{SEVENZ_SUFFIX}"):
            if p.is_file():
                targets.append(p.resolve())

    if not paths:
        if mode == "compress":
            add_compress_tree(cwd)
        else:
            add_decompress_tree(cwd)
    else:
        for raw in paths:
            p = Path(raw)
            if not p.exists():
                logger.warning(f"{p} does not exist, skipping")
                continue
            rp = p.resolve()
            if rp == cwd and p.is_dir():
                if mode == "compress":
                    add_compress_tree(cwd)
                else:
                    add_decompress_tree(cwd)
            elif mode == "decompress" and p.is_dir():
                add_decompress_tree(rp)
            else:
                targets.append(rp)
    if output_dir is not None:
        out_res = output_dir.resolve()
        targets = [t for t in targets if out_res not in t.parents and t != out_res]
    seen: set[Path] = set()
    ordered: list[Path] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def cmd_7z(args: argparse.Namespace) -> int:
    if not _HAS_PYLZMA:
        logger.error("pylzma is required for the `7z` subcommand. Install it with `pip install pylzma`.")
        return 1
    mode = "decompress" if args.decompress else "compress"
    out_dir: Optional[Path] = None
    if args.output_dir:
        out_dir = Path(args.output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
    targets = _collect_7z_targets(args.paths, mode, Path.cwd(), out_dir)
    if not targets:
        print(f"No items found to {mode}")
        return 0
    print(f"{mode.capitalize()}ing {len(targets)} item(s) with {MAX_WORKERS} workers...")
    if out_dir:
        print(f"Output directory: {out_dir}")
    worker = _worker_pylzma_un7z if mode == "decompress" else _worker_pylzma_7z
    worker_args = [(str(t), args.keep, str(out_dir) if out_dir else None, args.tar_subdirs) for t in targets]
    with Pool(processes=MAX_WORKERS) as pool:
        for r in pool.imap_unordered(worker, worker_args):
            mark = "✓" if r.success else "✗"
            print(f"  {mark} {r.path}: {r.message}")
    return 0


def cmd_lzma_chunk(args: argparse.Namespace) -> int:
    if not _HAS_PYLZMA:
        logger.error("pylzma is required for the `lzma-chunk` subcommand.")
        return 1
    cwd = Path.cwd()
    if args.decompress:
        files = [p for p in cwd.glob(f"*{LZMA_SUFFIX}") if p.is_file() and not p.is_symlink()]
        if not files:
            print("No .lzma files to decompress")
            return 0
        print(f"Decompressing {len(files)} archive(s)...")
        worker_args = [(str(f), not args.keep_orig) for f in files]
        worker = _worker_lzma_chunk_decompress
    else:
        files = [
            p
            for p in cwd.glob("*")
            if p.is_file()
            and not p.is_symlink()
            and not any(p.name.endswith(s) for s in (LZMA_SUFFIX, XZ_SUFFIX, SEVENZ_SUFFIX, ".gz", ".bz2", ".zip"))
            and p.stat().st_size >= 1024
        ]
        if not files:
            print("No files to compress")
            return 0
        print(f"Compressing {len(files)} file(s) with chunk size {format_size(args.chunk_size)}...")
        worker_args = [(str(f), args.chunk_size, MAX_WORKERS, not args.keep_orig) for f in files]
        worker = _worker_lzma_chunk_compress
    ok = 0
    total_orig = total_new = 0
    with Pool(processes=MAX_WORKERS) as pool:
        for r in pool.imap_unordered(worker, worker_args):
            mark = "✓" if r.success else "✗"
            extra = ""
            if r.success and r.original_size and r.processed_size:
                pct = (1 - r.processed_size / r.original_size) * 100
                extra = f" ({format_size(r.original_size)} → {format_size(r.processed_size)}, {pct:.1f}% saved)"
            print(f"  {mark} {r.path.name}: {r.message}{extra}")
            if r.success:
                ok += 1
                total_orig += r.original_size
                total_new += r.processed_size
    print("─" * 60)
    print(f"Succeeded: {ok} / {len(files)}")
    if ok and total_orig:
        saved = total_orig - total_new
        pct = saved / total_orig * 100
        print(f"Total original:   {format_size(total_orig)}")
        print(f"Total processed:  {format_size(total_new)}")
        print(f"Space saved:      {format_size(abs(saved))} ({pct:.1f}%)")
    return 0


def _add_common_xz_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory to process (default: current directory)",
    )
    p.add_argument(
        "--sequential",
        action="store_true",
        help="Disable multiprocessing (run in-process)",
    )
    p.add_argument("--keep-orig", action="store_true", help="Keep original files after processing")
    p.add_argument(
        "--exclude",
        nargs="+",
        default=[],
        metavar="PAT",
        help="Skip paths containing any of these substrings",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xz_tool.py",
        description="Unified compression / decompression CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  xz_tool.py compress                              "
            "# recursive .xz of cwd\n"
            "  xz_tool.py compress /data --preset 6 --threads 8\n"
            "  xz_tool.py compress --tar-subdirs --verbose\n"
            "  xz_tool.py compress --auto-tar-dirs --chunk-size 1048576\n"
            "  xz_tool.py decompress --handle-tar-xz /data/archives\n"
            "  xz_tool.py tar-dirs /data --workers 4\n"
            "  xz_tool.py archive-cwd\n"
            "  xz_tool.py 7z -k path1 path2 -k\n"
            "  xz_tool.py 7z --output-dir ./compressed\n"
            "  xz_tool.py lzma-chunk -c\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_c = sub.add_parser("compress", help="Recursively compress files to .xz")
    _add_common_xz_opts(p_c)
    p_c.add_argument(
        "--preset",
        type=int,
        default=DEFAULT_PRESET,
        choices=range(10),
        help=f"xz preset 0-9 (default: {DEFAULT_PRESET})",
    )
    p_c.add_argument(
        "--threads",
        type=int,
        default=DEFAULT_THREADS,
        help=f"Threads per compression job (default: {DEFAULT_THREADS})",
    )
    p_c.add_argument(
        "--backend",
        choices=["auto", "lzma_mt", "lzmamt", "lzma"],
        default="auto",
        help="Compression backend (default: auto)",
    )
    p_c.add_argument(
        "-e",
        "--extensions",
        nargs="+",
        help="Only compress files with these extensions",
    )
    p_c.add_argument(
        "--no-skip-compressed",
        action="store_true",
        help="Do not skip already-compressed extensions",
    )
    p_c.add_argument(
        "--tar-subdirs",
        action="store_true",
        help="Tar top-level subdirs first, then compress",
    )
    p_c.add_argument(
        "--auto-tar-dirs",
        action="store_true",
        help="(xzer.py) Same as --tar-subdirs but only top-level files are subsequently compressed",
    )
    p_c.add_argument("--dry-run", action="store_true", help="Show what would be done; modify nothing")
    p_c.add_argument("--verbose", action="store_true", help="Print per-file results")
    p_c.add_argument("--no-size-stats", action="store_true", help="Skip the size summary at the end")
    p_c.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Chunk size for large files (default: {DEFAULT_CHUNK_SIZE})",
    )
    p_c.set_defaults(func=cmd_compress)
    p_d = sub.add_parser("decompress", help="Recursively decompress .xz files")
    _add_common_xz_opts(p_d)
    p_d.add_argument(
        "--handle-tar-xz",
        action="store_true",
        help="Extract `*.tar.xz` files into directories",
    )
    p_d.set_defaults(func=cmd_decompress)
    p_t = sub.add_parser("tar-dirs", help="Pack each top-level subdirectory into .tar.xz")
    p_t.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory containing the subdirectories (default: cwd)",
    )
    p_t.add_argument(
        "--preset",
        type=int,
        default=DEFAULT_PRESET,
        choices=range(10),
        help=f"xz preset 0-9 (default: {DEFAULT_PRESET})",
    )
    p_t.add_argument("--keep-orig", action="store_true", help="Keep the source subdirectory")
    p_t.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip archive verification (faster, riskier)",
    )
    p_t.set_defaults(func=cmd_tar_dirs)
    p_a = sub.add_parser("archive-cwd", help="Archive cwd to ../<cwd>.tar.xz and delete it")
    p_a.set_defaults(func=cmd_archive_cwd)
    p_7 = sub.add_parser("7z", help="pylzma-based .7z / .tar.7z codec")
    p_7.add_argument("paths", nargs="*", help="Files / directories (default: cwd recursively)")
    p_7.add_argument("-d", "--decompress", action="store_true", help="Decompress mode")
    p_7.add_argument("-k", "--keep", action="store_true", help="Keep originals after processing")
    p_7.add_argument(
        "--output-dir",
        default=None,
        help="Write outputs into this directory (compress_pylzma.py behaviour)",
    )
    p_7.add_argument(
        "--tar-subdirs",
        action="store_true",
        help="(Accepted for parity; directories are always tarred)",
    )
    p_7.set_defaults(func=cmd_7z)
    p_l = sub.add_parser("lzma-chunk", help="pylzma chunked .lzma codec (max compression)")
    p_l.add_argument("-c", "--compress", action="store_true", default=True, help="Compress (default)")
    p_l.add_argument("-d", "--decompress", action="store_true", help="Decompress .lzma files")
    p_l.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_PYLZMA_CHUNK,
        help=f"Chunk size in bytes (default: {DEFAULT_PYLZMA_CHUNK})",
    )
    p_l.add_argument("--keep-orig", action="store_true", help="Keep original files after processing")
    p_l.set_defaults(func=cmd_lzma_chunk)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.error(f"Unexpected error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
