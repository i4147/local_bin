#!/data/data/com.termux/files/usr/bin/env python
"""xztool - one consolidated, recursive compression toolkit.

This script is the product of merging 13 sibling scripts that all performed
"roughly the same job with small differences" (recursive LZMA/XZ/7Z style
compression and decompression).  Where the originals disagreed, the union of
their behaviours is exposed through explicit, documented CLI flags so that any
individual original workflow can be reproduced.

Mapping of the original scripts onto this tool
----------------------------------------------
==============================  ====================================================
Original script                 Covered by
==============================  ====================================================
``compress_files.py``           ``compress`` / ``decompress`` (recursive, parallel,
``compress_files2.py``          skip archives + media, ``--preset``/``--threads``/
``fast_xz.py``                  ``--keep-original``; ``fast_xz`` parity =
                                ``--include-media`` because it skipped archives only)
``lzmamter.py``                 ``compress`` (``--extreme``, skip-existing default,
                                ``-t`` tar-subdirs-first, ``--dry-run``, ``--verbose``)
``xz_compressor.py``            ``compress`` (``-e`` extension filter, ``--exclude``,
                                ``--dry-run``, ``--no-skip-compressed``, rich report)
``xzer.py`` / ``uxz.py``        ``decompress`` (``.tar.xz`` extraction into directories)
``pylzmaer.py``                 ``compress --backend lzma --min-size 1024
                                --only-if-smaller`` (chunked raw-LZMA1 container,
                                size guard, keep-only-if-smaller semantics)
``py7z.py``                     ``compress --backend 7z`` (files -> ``.7z``,
``compress_pylzma.py``          directories -> tar -> ``.tar.7z``, ``--out DIR``;
                                requires optional ``pylzma`` package)
``csubdirxz.py``                ``archive`` (verified ``.tar.xz`` per top-level
                                subdirectory, refuses overwrite, deletes source)
``ptrr.py``                     ``pack`` (whole directory -> ``../<name>.tar.xz``,
                                member verification, then source removal)
``xzer2.py``                    ``--dry-run`` (the simulation script, generalised)
==============================  ====================================================

Design notes
------------
* Stdlib first: everything runs on the Python standard library (``lzma``,
  ``tarfile``, ``argparse``, ``concurrent.futures``).  Optional third-party
  packages are used only when installed:
  - ``lzma_mt``  -> real multi-threaded XZ streams for the ``xz`` backend;
  - ``pylzma``   -> enables the ``7z`` backend;
  - ``rich``     -> prettier reports when stdout is a TTY (or ``--report rich``).
* The original scripts deleted originals by default; the ``--keep-original``
  flag preserves them (mirrors every original's ``--keep-orig``/``-k`` flag).
* A bug in ``csubdirxz.py`` (a failed run deleted a *pre-existing* archive it
  had refused to overwrite) is fixed here: only archives created by the failed
  attempt itself are removed.
* The obfuscated single-letter identifiers of the input scripts have been
  replaced by descriptive names throughout.

Typical usage::

    xztool compress                       # compress cwd recursively (xz, max)
    xztool compress ~/data -p 6 -w 8 -k   # keep originals, 8 workers
    xztool compress -t                    # tar top-level subdirs first
    xztool compress --backend lzma --min-size 1024 --only-if-smaller
    xztool decompress ~/data              # .xz/.tar.xz/.lzma back to original
    xztool archive ~/buckets              # one verified .tar.xz per subdir
    xztool pack ~/stuff                   # ../stuff.tar.xz + verify + delete
"""

from __future__ import annotations

import argparse
import importlib
import io
import lzma
import logging
import os
import shutil
import sys
import tarfile
import textwrap
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterable, Iterator, Sequence

__version__ = "1.0.0"

LOG = logging.getLogger("xztool")

# ---------------------------------------------------------------------------
# Optional third-party back-ends (stdlib fallbacks are always available).
# ---------------------------------------------------------------------------


def _try_import(name: str) -> ModuleType | None:
    """Import *name* and return the module, or ``None`` when unavailable."""
    try:
        return importlib.import_module(name)
    except Exception:  # pragma: no cover - depends on environment
        return None


_LZMA_MT: ModuleType | None = _try_import("lzma_mt")
_PYLZMA: ModuleType | None = _try_import("pylzma")


class BackendUnavailableError(RuntimeError):
    """Raised when a compression back-end's optional dependency is missing."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Archive / already-compressed extensions that are never re-compressed
#: (union of the exclusion sets of every original script).
ARCHIVE_EXTS: frozenset[str] = frozenset({
    ".7z",
    ".Z",
    ".br",
    ".bz",
    ".bz2",
    ".bz3",
    ".deb",
    ".deflate",
    ".egg",
    ".gz",
    ".ipa",
    ".jar",
    ".lz",
    ".lz4",
    ".lzma",
    ".lzo",
    ".rar",
    ".rpm",
    ".snappy",
    ".sz",
    ".tar",
    ".tbz",
    ".tbz2",
    ".tgz",
    ".tlz",
    ".txz",
    ".whl",
    ".xza",
    ".xz",
    ".zip",
    ".zlib",
    ".zst",
    ".apk",
})

#: Media / binary formats that compress poorly; skipped by default
#: (union of the "media" exclusion sets of the original scripts).
MEDIA_EXTS: frozenset[str] = frozenset({
    ".3gp",
    ".aac",
    ".alac",
    ".avi",
    ".bmp",
    ".class",
    ".dll",
    ".dmg",
    ".doc",
    ".docx",
    ".dylib",
    ".flac",
    ".flv",
    ".gif",
    ".heic",
    ".heif",
    ".ico",
    ".img",
    ".iso",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".o",
    ".obj",
    ".odp",
    ".ods",
    ".odt",
    ".ogg",
    ".opus",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".pyc",
    ".pyo",
    ".so",
    ".svg",
    ".tiff",
    ".wav",
    ".wavpack",
    ".wasm",
    ".webm",
    ".webp",
    ".wma",
    ".wmv",
    ".xls",
    ".xlsx",
    ".avif",
})

#: Output extensions produced by this tool itself; never re-compressed even
#: with ``--no-skip-compressed`` (mirrors ``xz_compressor.py``).
OWN_OUTPUT_EXTS: frozenset[str] = frozenset({".xz", ".lzma", ".7z"})

#: Default skip set: everything that will not benefit from LZMA.
DEFAULT_SKIP_EXTS: frozenset[str] = ARCHIVE_EXTS | MEDIA_EXTS

#: Directories never descended into (union of the originals' exclusion sets).
EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".env",
    "node_modules",
    "venv",
})

#: Suffixes considered decompressible by the ``decompress`` command.
DECOMPRESS_SUFFIXES: frozenset[str] = frozenset({".xz", ".lzma", ".7z"})

#: CLI back-end name -> output file suffix.
BACKEND_SUFFIX: dict[str, str] = {"xz": ".xz", "lzma": ".lzma", "7z": ".7z"}

#: Files smaller than this are left alone by the ``lzma`` back-end of
#: ``pylzmaer.py``; exposed here as ``--min-size`` (default 0 keeps the
#: behaviour of the other scripts).
MIN_SIZE_DEFAULT: int = 0

#: ``pylzmaer.py`` only switched to its chunked container for files of at
#: least this size; smaller files were stored as plain ``.lzma`` streams.
LZMA_CHUNK_THRESHOLD: int = 512 * 1024

#: Size of each chunk inside the chunked ``.lzma`` container (the original
#: used 32 KiB; the container format is self-describing so any size round-trips).
LZMA_CHUNK_SIZE: int = 1024 * 1024

#: Streaming block size used when reading files / pumping XZ data.
COPY_CHUNK: int = 1024 * 1024

#: Maximum plausible chunk count for the chunked ``.lzma`` container probe.
_LZMA_MAX_CHUNKS: int = 10_000_000

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    """Immutable run configuration shared by all operations.

    Instances are passed to worker processes, hence the choice of a picklable
    frozen dataclass with only immutable field defaults.
    """

    root: Path
    backend: str = "xz"
    preset: int = 9
    threads: int = 4
    workers: int = 0  # 0 -> automatic
    keep_original: bool = False
    dry_run: bool = False
    verbose: bool = False
    overwrite: bool = False
    extreme: bool = False
    only_if_smaller: bool = False
    min_size: int = MIN_SIZE_DEFAULT
    only_exts: frozenset[str] = frozenset()
    skip_exts: frozenset[str] = DEFAULT_SKIP_EXTS
    exclude: tuple[str, ...] = ()
    extract_tar: bool = True
    verify: bool = True
    out_dir: Path | None = None
    report: str = "auto"
    top_level_only: bool = False

    def effective_workers(self, item_count: int) -> int:
        """Return the worker count to use for *item_count* jobs."""
        base = self.workers if self.workers > 0 else default_workers()
        return max(1, min(base, max(1, item_count)))


@dataclass(frozen=True)
class FileResult:
    """Outcome of compressing or decompressing a single file."""

    source: Path
    target: Path | None
    status: str  # "ok" | "skipped" | "dryrun" | "error"
    message: str = ""
    original_size: int = 0
    result_size: int = 0
    elapsed: float = 0.0
    deleted: bool = False  # source removed
    tarred: bool = False  # source was a directory packed as tar first

    @property
    def ok(self) -> bool:
        """True when the operation fully succeeded."""
        return self.status == "ok"


@dataclass(frozen=True)
class SubdirResult:
    """Outcome of archiving one subdirectory (``archive`` command)."""

    subdir: str
    success: bool
    message: str
    start: str
    end: str
    elapsed: float
    original_size: int
    compressed_size: int
    ratio: float  # compressed / original


# ---------------------------------------------------------------------------
# Small generic helpers
# ---------------------------------------------------------------------------


def default_workers() -> int:
    """Return the default number of parallel workers (mirrors the originals: 8)."""
    return min(8, os.cpu_count() or 1)


def human_size(num: float) -> str:
    """Format *num* bytes using binary units, e.g. ``'1.50 MB'``.

    Same formatting convention as the shared ``dh.fsz`` helper of the
    original scripts.
    """
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def dir_size(path: Path) -> int:
    """Recursively sum the sizes of all regular files below *path*."""
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _now_stamp() -> str:
    """Current local time formatted for archive reports."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_exts(exts: Iterable[str]) -> frozenset[str]:
    """Normalise user supplied extensions to lowercase dotted form."""
    return frozenset(e.lower() if e.startswith(".") else f".{e.lower()}" for e in exts)


def _excluded(path: Path, root: Path, exclude: Sequence[str]) -> bool:
    """True when *path* sits in an excluded directory or matches an exclude pattern."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = Path(path.name)
    if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
        return True
    posix = rel.as_posix()
    return any(pattern in posix for pattern in exclude)


def _run_jobs(
    job: Callable[[object], object],
    items: Sequence[object],
    workers: int,
) -> list[object]:
    """Map *job* over *items*, in-process when a single worker is enough."""
    if not items:
        return []
    if workers <= 1 or len(items) == 1:
        return [job(item) for item in items]
    with ProcessPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(job, items))


def count_by_ext(paths: Iterable[Path]) -> dict[str, int]:
    """Count *paths* per (lowercase) extension, most frequent first."""
    counts: dict[str, int] = {}
    for path in paths:
        ext = path.suffix.lower() or "[no extension]"
        counts[ext] = counts.get(ext, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


# ---------------------------------------------------------------------------
# Target discovery
# ---------------------------------------------------------------------------


def find_compress_targets(root: Path, opts: Options) -> list[Path]:
    """Return the sorted list of files under *root* eligible for compression.

    Applies, in order: regular-file/symlink checks, excluded directories,
    user exclude patterns, the skip-extension set, the optional
    only-these-extensions filter and the minimum size threshold.
    """
    targets: list[Path] = []
    candidates: Iterator[Path] = root.iterdir() if opts.top_level_only else root.rglob("*")
    for path in candidates:
        try:
            if not path.is_file() or path.is_symlink():
                continue
        except OSError:
            continue
        if _excluded(path, root, opts.exclude):
            continue
        suffix = path.suffix.lower()
        if suffix and suffix in opts.skip_exts:
            continue
        if opts.only_exts and suffix not in opts.only_exts:
            continue
        if opts.min_size > 0:
            try:
                if path.stat().st_size < opts.min_size:
                    continue
            except OSError:
                continue
        targets.append(path)
    return sorted(targets)


def find_decompress_targets(
    root: Path,
    opts: Options,
    suffixes: frozenset[str] = DECOMPRESS_SUFFIXES,
) -> list[Path]:
    """Return the sorted list of archives under *root* eligible for decompression."""
    targets: list[Path] = []
    for path in root.rglob("*"):
        try:
            if not path.is_file() or path.is_symlink():
                continue
        except OSError:
            continue
        if _excluded(path, root, opts.exclude):
            continue
        if path.suffix.lower() not in suffixes:
            continue
        targets.append(path)
    return sorted(targets)


# ---------------------------------------------------------------------------
# Byte-level codecs
# ---------------------------------------------------------------------------


def _xz_filters(preset: int, extreme: bool) -> list[dict[str, int]]:
    """LZMA2 filter chain for the ``xz`` back-end."""
    level = preset | lzma.PRESET_EXTREME if extreme else preset
    return [{"id": lzma.FILTER_LZMA2, "preset": level}]


def _compress_xz_file(
    src: Path,
    dst: Path,
    preset: int,
    extreme: bool,
    threads: int,
) -> int:
    """Compress file *src* into *dst* using the ``xz`` back-end.

    Uses ``lzma_mt`` for genuine multi-threaded encoding when available and
    requested, otherwise streams through the stdlib incremental compressor
    (constant memory).  Returns the number of bytes written.
    """
    if _LZMA_MT is not None and threads > 1 and not extreme:
        payload = _LZMA_MT.compress(src.read_bytes(), preset=preset, threads=threads)
        dst.write_bytes(payload)
        return len(payload)
    compressor = lzma.LZMACompressor(
        format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, filters=_xz_filters(preset, extreme)
    )
    written = 0
    with src.open("rb") as fin, dst.open("wb") as fout:
        while chunk := fin.read(COPY_CHUNK):
            block = compressor.compress(chunk)
            if block:
                fout.write(block)
                written += len(block)
        tail = compressor.flush()
        if tail:
            fout.write(tail)
            written += len(tail)
    return written


def _lzma_alone_chunk(args: tuple[bytes, int, bool]) -> bytes:
    """Compress one chunk as a self-contained raw LZMA1 (``.lzma``) stream."""
    data, preset, extreme = args
    level = preset | lzma.PRESET_EXTREME if extreme else preset
    return lzma.compress(
        data,
        format=lzma.FORMAT_ALONE,
        filters=[{"id": lzma.FILTER_LZMA1, "preset": level}],
    )


def compress_bytes_lzma(
    data: bytes,
    *,
    preset: int = 9,
    extreme: bool = True,
    chunk_size: int = LZMA_CHUNK_SIZE,
    workers: int = 1,
) -> bytes:
    """Compress *data* with the raw LZMA1 back-end of ``pylzmaer.py``.

    Small inputs become a plain ``.lzma`` stream; inputs of at least
    ``LZMA_CHUNK_THRESHOLD`` bytes are split into independent chunks that are
    compressed (optionally in parallel) and packed into a self-describing
    container: ``[4B chunk count][8B length + chunk]...`` (all big-endian).
    """
    if len(data) < LZMA_CHUNK_THRESHOLD:
        return _lzma_alone_chunk((data, preset, extreme))
    chunks = [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]
    jobs: list[tuple[bytes, int, bool]] = [(c, preset, extreme) for c in chunks]
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            encoded = list(pool.map(_lzma_alone_chunk, jobs))
    else:
        encoded = [_lzma_alone_chunk(job) for job in jobs]
    out = bytearray(len(chunks).to_bytes(4, "big"))
    for blob in encoded:
        out += len(blob).to_bytes(8, "big")
        out += blob
    return bytes(out)


def _lzma_dechunk(data: bytes, count: int) -> bytes:
    """Decode the chunked ``.lzma`` container written by :func:`compress_bytes_lzma`.

    Layout: ``[4B chunk count][8B length + chunk bytes] * count`` (big-endian),
    identical to the reader in ``pylzmaer.py`` (lengths start at offset 4).
    """
    offset = 4
    parts: list[bytes] = []
    for _ in range(count):
        length = int.from_bytes(data[offset : offset + 8], "big")
        offset += 8
        parts.append(lzma.decompress(data[offset : offset + length]))
        offset += length
    if offset != len(data):
        raise ValueError("trailing bytes after last chunk")
    return b"".join(parts)


def decompress_bytes_lzma(data: bytes) -> bytes:
    """Decompress ``.lzma`` data, auto-detecting the chunked container.

    The probe mirrors ``pylzmaer.py``: the first four bytes are read as the
    chunk count and the next eight as the first chunk length; only if both
    are plausible is the container path attempted, with a plain-stream
    fallback.
    """
    if len(data) >= 12:
        count = int.from_bytes(data[:4], "big")
        first_len = int.from_bytes(data[4:12], "big")
        if 0 < count < _LZMA_MAX_CHUNKS and 0 < first_len <= len(data):
            try:
                return _lzma_dechunk(data, count)
            except Exception:
                pass  # fall through to the plain .lzma decoder
    return lzma.decompress(data)


SEVEN_Z_FILTERS: dict[str, int] = {
    "algorithm": 2,
    "fastBytes": 273,
    "dictionary": 64 * 1024 * 1024,
}


def compress_bytes_7z(data: bytes) -> bytes:
    """Compress *data* with ``pylzma`` (7-Zip LZMA), raising if unavailable."""
    if _PYLZMA is None:
        raise BackendUnavailableError("backend '7z' requires the optional 'pylzma' package (pip install pylzma)")
    return _PYLZMA.compress(data, **SEVEN_Z_FILTERS)


def decompress_bytes_7z(data: bytes) -> bytes:
    """Decompress ``pylzma`` produced data, raising if unavailable."""
    if _PYLZMA is None:
        raise BackendUnavailableError("decompressing '.7z' requires the optional 'pylzma' package (pip install pylzma)")
    return _PYLZMA.decompress(data)


# ---------------------------------------------------------------------------
# tar helpers
# ---------------------------------------------------------------------------


def make_tar(src_dir: Path, tar_path: Path) -> int:
    """Create an uncompressed ``.tar`` of *src_dir* (arcname = directory name)."""
    with tarfile.open(tar_path, "w") as tar:
        tar.add(src_dir, arcname=src_dir.name, recursive=True)
    return tar_path.stat().st_size


def make_tar_xz(src_dir: Path, target: Path, *, preset: int) -> int:
    """Stream *src_dir* into a ``.tar.xz`` archive (``csubdirxz.py`` style)."""
    with (
        lzma.open(target, "wb", preset=preset, check=lzma.CHECK_CRC64) as xz,
        tarfile.open(fileobj=xz, mode="w") as tar,
    ):
        tar.add(src_dir, arcname=src_dir.name, recursive=True)
    return target.stat().st_size


def verify_tar_xz(archive: Path) -> list[str]:
    """Fully read back *archive* to prove it decodes; return member names."""
    with tarfile.open(archive, "r:xz") as tar:
        names: list[str] = []
        for member in tar:
            names.append(member.name)
            if member.isfile():
                handle = tar.extractfile(member)
                if handle is None:
                    raise OSError(f"cannot read member: {member.name}")
                while handle.read(COPY_CHUNK):
                    pass
        return names


def extract_tar_payload(payload: bytes, dest: Path) -> None:
    """Extract an in-memory tar *payload* into *dest* (safe-members filter)."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tar:
        _extract_members(tar, dest)


def _extract_members(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract *tar* into *dest* using the safe ``data`` filter when available."""
    try:
        tar.extractall(path=dest, filter="data")
    except TypeError:  # Python < 3.12 has no filter= parameter
        tar.extractall(path=dest)


def _extract_tar_dir(payload: bytes, stem: str, parent: Path) -> Path:
    """Choose the natural extraction destination for a ``.tar.xz`` payload.

    Archives produced by ``archive``/``pack`` (and ``tar`` in general) store
    their members below the directory name, so extracting ``alpha.tar.xz``
    (members ``alpha/...``) directly into *parent* reproduces ``alpha/``.
    Payloads without that self-prefix go into an isolated ``parent/stem``
    directory instead.  Returns the chosen destination.
    """
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tar:
        top_segments = {member.name.split("/", 1)[0] for member in tar.getmembers()}
        dest = parent if top_segments == {stem} else parent / stem
        _extract_members(tar, dest)
    return dest


# ---------------------------------------------------------------------------
# Single-file operations (worker entry points)
# ---------------------------------------------------------------------------


def _output_parent(path: Path, opts: Options) -> Path:
    """Return the directory that must hold *path*'s archive.

    Normally the source directory itself; with ``opts.out_dir`` the relative
    location of *path* below ``opts.root`` is preserved inside the output
    directory (``--out`` never flattens the tree).
    """
    if opts.out_dir is None:
        return path.parent
    try:
        rel_parent = path.parent.relative_to(opts.root)
    except ValueError:
        rel_parent = Path()
    return opts.out_dir / rel_parent


def compress_file(path: Path, opts: Options) -> FileResult:
    """Compress a single file according to *opts*.

    The archive is written next to the source (or into ``opts.out_dir``,
    preserving relative paths) as ``<name><backend suffix>``.  Existing
    outputs are skipped unless ``opts.overwrite`` is set; the source is
    removed after success unless ``opts.keep_original``; with
    ``opts.only_if_smaller`` an archive that did not shrink the file is
    discarded and the original kept.
    """
    suffix = BACKEND_SUFFIX[opts.backend]
    target = _output_parent(path, opts) / (path.name + suffix)

    def result(status: str, message: str = "", **kw: object) -> FileResult:
        return FileResult(source=path, target=target, status=status, message=message, **kw)  # type: ignore[arg-type]

    try:
        if not path.is_file() or path.is_symlink():
            return result("error", "not a regular file")
    except OSError as exc:
        return result("error", f"cannot stat: {exc}")
    if target.exists() and not opts.overwrite:
        return result("skipped", f"{target.name} already exists")
    if opts.dry_run:
        return result("dryrun", f"would compress to {target.name}")

    started = time.perf_counter()
    try:
        original_size = path.stat().st_size
        target.parent.mkdir(parents=True, exist_ok=True)
        if opts.backend == "xz":
            result_size = _compress_xz_file(path, target, opts.preset, opts.extreme, opts.threads)
        elif opts.backend == "lzma":
            payload = compress_bytes_lzma(
                path.read_bytes(),
                preset=opts.preset,
                extreme=opts.extreme,
                workers=opts.effective_workers(4),
            )
            target.write_bytes(payload)
            result_size = len(payload)
        elif opts.backend == "7z":
            payload = compress_bytes_7z(path.read_bytes())
            target.write_bytes(payload)
            result_size = len(payload)
        else:
            raise ValueError(f"unknown backend: {opts.backend}")
    except BackendUnavailableError as exc:
        return result("error", str(exc))
    except Exception as exc:  # noqa: BLE001 - reported per file, like the originals
        target.unlink(missing_ok=True)
        return result("error", f"{type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - started

    if opts.only_if_smaller and result_size >= original_size:
        target.unlink(missing_ok=True)
        return result(
            "ok",
            "no space saved - original kept",
            original_size=original_size,
            elapsed=elapsed,
        )
    deleted = False
    if not opts.keep_original:
        path.unlink(missing_ok=True)
        deleted = True
    return result(
        "ok",
        f"compressed to {target.name}",
        original_size=original_size,
        result_size=result_size,
        elapsed=elapsed,
        deleted=deleted,
    )


def decompress_file(path: Path, opts: Options) -> FileResult:
    """Decompress a single ``.xz``/``.tar.xz``/``.lzma``/``.7z``/``.tar.7z`` file.

    ``.tar.xz`` / ``.tar.7z`` archives are extracted into a directory named
    after the archive stem when ``opts.extract_tar`` is set (``uxz.py`` /
    ``xzer.py`` behaviour); when the members already carry that ``stem/``
    prefix the archive is extracted into the parent directory instead, so
    no ``stem/stem/`` double nesting occurs.  With ``extract_tar`` disabled
    a plain ``.tar`` file is produced (``compress_files.py`` behaviour).
    """
    name = path.name
    lower = name.lower()
    if lower.endswith(".tar.xz"):
        kind, stem = "tar.xz", name[: -len(".tar.xz")]
    elif lower.endswith(".tar.7z"):
        kind, stem = "tar.7z", name[: -len(".tar.7z")]
    elif path.suffix.lower() == ".xz":
        kind, stem = "xz", name[: -len(".xz")]
    elif path.suffix.lower() == ".lzma":
        kind, stem = "lzma", name[: -len(".lzma")]
    elif path.suffix.lower() == ".7z":
        kind, stem = "7z", name[: -len(".7z")]
    else:
        return FileResult(source=path, target=None, status="error", message="unsupported archive type")
    if not stem:
        return FileResult(source=path, target=None, status="error", message="cannot derive output name")

    to_dir = kind in ("tar.xz", "tar.7z") and opts.extract_tar
    parent = _output_parent(path, opts)
    if to_dir:
        target = parent / stem
    elif kind in ("tar.xz", "tar.7z"):
        target = parent / (stem + ".tar")  # compress_files.py -d behaviour
    else:
        target = parent / stem

    def result(status: str, message: str = "", **kw: object) -> FileResult:
        return FileResult(source=path, target=target, status=status, message=message, **kw)  # type: ignore[arg-type]

    if target.exists() and not opts.overwrite:
        return result("skipped", f"{target.name} already exists")
    if opts.dry_run:
        return result("dryrun", f"would decompress to {target.name}")

    started = time.perf_counter()
    wrote_output = False
    try:
        original_size = path.stat().st_size
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind in ("xz", "tar.xz"):
            payload = lzma.decompress(path.read_bytes())
        elif kind == "lzma":
            payload = decompress_bytes_lzma(path.read_bytes())
        elif kind in ("7z", "tar.7z"):
            payload = decompress_bytes_7z(path.read_bytes())
        else:  # pragma: no cover - kind is always one of the above
            raise ValueError(f"unknown kind: {kind}")
        if to_dir:
            _extract_tar_dir(payload, stem, parent)
        else:
            target.write_bytes(payload)
        wrote_output = True
    except BackendUnavailableError as exc:
        return result("error", str(exc))
    except Exception as exc:  # noqa: BLE001 - reported per file, like the originals
        if wrote_output and target.is_file():
            target.unlink(missing_ok=True)
        return result("error", f"{type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - started

    result_size = dir_size(target) if target.is_dir() else target.stat().st_size
    deleted = False
    if not opts.keep_original:
        path.unlink(missing_ok=True)
        deleted = True
    return result(
        "ok",
        f"decompressed to {target.name}",
        original_size=original_size,
        result_size=result_size,
        elapsed=elapsed,
        deleted=deleted,
    )


# Worker shims: (path, Options) tuples keep the pool payloads picklable.


def _compress_job(args: tuple[Path, Options]) -> FileResult:
    """Process-pool entry point for :func:`compress_file`."""
    path, opts = args
    return compress_file(path, opts)


def _decompress_job(args: tuple[Path, Options]) -> FileResult:
    """Process-pool entry point for :func:`decompress_file`."""
    path, opts = args
    return decompress_file(path, opts)


def _archive_job(args: tuple[Path, Options]) -> SubdirResult:
    """Process-pool entry point: tar+compress+verify one subdirectory."""
    subdir, opts = args
    target = subdir.parent / (subdir.name + ".tar.xz")
    start = _now_stamp()
    started = time.perf_counter()
    created = False
    original = 0
    try:
        if not subdir.is_dir():
            raise NotADirectoryError(f"source directory missing: {subdir}")
        if target.exists():
            raise FileExistsError(f"refusing to overwrite existing archive: {target.name}")
        original = dir_size(subdir)
        make_tar_xz(subdir, target, preset=opts.preset)
        created = True
        compressed = target.stat().st_size
        if compressed <= 0:
            raise OSError("created archive is empty")
        if opts.verify:
            verify_tar_xz(target)
        if not opts.keep_original:
            shutil.rmtree(subdir)
        return SubdirResult(
            subdir=subdir.name,
            success=True,
            message=f"OK {subdir.name}",
            start=start,
            end=_now_stamp(),
            elapsed=time.perf_counter() - started,
            original_size=original,
            compressed_size=compressed,
            ratio=(compressed / original) if original else 0.0,
        )
    except Exception as exc:  # noqa: BLE001 - reported per directory
        # Only remove the archive if *this* attempt created it (the original
        # csubdirxz.py erroneously deleted pre-existing archives here too).
        if created:
            target.unlink(missing_ok=True)
        return SubdirResult(
            subdir=subdir.name,
            success=False,
            message=f"FAIL {subdir.name}: {exc}",
            start=start,
            end=_now_stamp(),
            elapsed=time.perf_counter() - started,
            original_size=original,
            compressed_size=0,
            ratio=0.0,
        )


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


def _progress_line(index: int, total: int, result: FileResult, root: Path) -> str:
    """Render one ``[  xx.x%] i/n ✓ rel/path: message`` progress line."""
    pct = index / total * 100 if total else 100.0
    glyph = {"ok": "✓", "skipped": "–", "dryrun": "·"}.get(result.status, "✗")
    try:
        rel = result.source.relative_to(root)
    except ValueError:
        rel = result.source
    return f"[{pct:5.1f}%] {index}/{total} {glyph} {rel}: {result.message}"


def run_file_compression(root: Path, opts: Options) -> list[FileResult]:
    """Compress every eligible file under *root* (optionally in parallel)."""
    targets = find_compress_targets(root, opts)
    if not targets:
        LOG.info("No files found to compress in %s", root)
        return []
    workers = opts.effective_workers(len(targets))
    LOG.info(
        "Compressing %d file(s) in %s (backend=%s, preset=%d, workers=%d)",
        len(targets),
        root,
        opts.backend,
        opts.preset,
        workers,
    )
    if opts.verbose:
        for ext, n in list(count_by_ext(targets).items())[:10]:
            print(f"  • {ext}: {n} file(s)")
        print(f"  total size: {human_size(sum(t.stat().st_size for t in targets))}")
    if opts.dry_run:
        return [
            FileResult(
                source=t,
                target=None,
                status="dryrun",
                message=f"would compress to {t.name}{BACKEND_SUFFIX[opts.backend]}",
            )
            for t in targets
        ]
    results = _run_jobs(_compress_job, [(t, opts) for t in targets], workers)
    for i, res in enumerate(results, 1):
        print(_progress_line(i, len(results), res, root))  # type: ignore[arg-type]
    return results  # type: ignore[return-value]


def run_file_decompression(root: Path, opts: Options) -> list[FileResult]:
    """Decompress every eligible archive under *root* (optionally in parallel)."""
    targets = find_decompress_targets(root, opts)
    if not targets:
        LOG.info("No archives found to decompress in %s", root)
        return []
    workers = opts.effective_workers(len(targets))
    LOG.info("Decompressing %d archive(s) in %s (workers=%d)", len(targets), root, workers)
    if opts.dry_run:
        return [FileResult(source=t, target=None, status="dryrun", message="would decompress") for t in targets]
    results = _run_jobs(_decompress_job, [(t, opts) for t in targets], workers)
    for i, res in enumerate(results, 1):
        print(_progress_line(i, len(results), res, root))  # type: ignore[arg-type]
    return results  # type: ignore[return-value]


def run_tar_subdirs_then_compress(root: Path, opts: Options) -> list[FileResult]:
    """``-t`` mode: tar every top-level subdirectory, compress the tar, then
    compress the loose files living directly in *root*.

    Mirrors ``lzmamter.py --tar-subdirs-first`` / ``xz_compressor.py -t``:
    the intermediate ``.tar`` is always consumed, and a source directory is
    only removed once its archive exists (unless ``--keep-original``).
    """
    results: list[FileResult] = []
    subdirs = sorted(d for d in root.iterdir() if d.is_dir() and not d.is_symlink() and not d.name.startswith("."))
    if subdirs:
        LOG.info("Tar-compressing %d top-level subdirectory/ies", len(subdirs))
    for subdir in subdirs:
        tar_path = subdir.parent / (subdir.name + ".tar")
        target = subdir.parent / (subdir.name + BACKEND_SUFFIX[opts.backend])
        if opts.dry_run:
            print(f"· {subdir.name}/ -> {target.name} (dry-run)")
            results.append(
                FileResult(
                    source=subdir,
                    target=target,
                    status="dryrun",
                    message=f"would tar+compress {subdir.name}/",
                    tarred=True,
                )
            )
            continue
        try:
            make_tar(subdir, tar_path)
        except Exception as exc:  # noqa: BLE001 - reported per directory
            tar_path.unlink(missing_ok=True)
            results.append(
                FileResult(
                    source=subdir,
                    target=None,
                    status="error",
                    message=f"tar failed: {type(exc).__name__}: {exc}",
                    tarred=True,
                )
            )
            continue
        # The intermediate tar is always consumed, regardless of --keep-original
        # (which refers to the source directory here).
        res = compress_file(tar_path, replace(opts, keep_original=False))
        deleted_dir = False
        if res.ok and res.target is not None and res.target.exists() and not opts.keep_original:
            shutil.rmtree(subdir)
            deleted_dir = True
        results.append(
            replace(
                res,
                source=subdir,
                tarred=True,
                deleted=deleted_dir,
                message=f"{subdir.name}/ -> {target.name}: {res.message}",
            )
        )
    results.extend(run_file_compression(root, replace(opts, top_level_only=True)))
    return results


def archive_subdirectories(root: Path, opts: Options) -> list[SubdirResult]:
    """Archive every top-level subdirectory of *root* into a verified ``.tar.xz``.

    This is the consolidated ``csubdirxz.py``: parallel workers, overwrite
    refusal, full read-back verification and source-directory removal on
    success.  Returns one :class:`SubdirResult` per subdirectory.
    """
    subdirs = sorted(d for d in root.iterdir() if d.is_dir() and not d.is_symlink() and not d.name.startswith("."))
    if not subdirs:
        LOG.info("No non-hidden top-level subdirectories found in %s", root)
        return []
    LOG.info(
        "Archiving %d subdirectory/ies of %s (preset=%d, workers=%d)",
        len(subdirs),
        root,
        opts.preset,
        opts.effective_workers(len(subdirs)),
    )
    results = _run_jobs(_archive_job, [(d, opts) for d in subdirs], opts.effective_workers(len(subdirs)))
    for res in results:  # type: ignore[union-attr]
        assert isinstance(res, SubdirResult)
        if res.success:
            print(
                f"✅ {res.message:<38} start={res.start}  end={res.end}  "
                f"took={res.elapsed:.1f}s  {human_size(res.original_size)} -> "
                f"{human_size(res.compressed_size)} ({res.ratio * 100:.1f}% of original)"
            )
        else:
            print(f"❌ {res.message:<38} start={res.start}  end={res.end}  took={res.elapsed:.1f}s")
    ok = sum(1 for r in results if r.success)  # type: ignore[union-attr]
    print(f"\nFinished: {ok} successful, {len(results) - ok} failed")
    return results  # type: ignore[return-value]


def pack_directory(root: Path, opts: Options) -> Path:
    """Pack the whole directory *root* into ``../<name>.tar.xz`` (``ptrr.py``).

    Steps: refuse to overwrite, collect regular files (warning about broken
    symlinks), write a PAX/XZ archive whose members all live under
    ``<name>/``, verify it (prefix + member count + full decode), and only
    then delete the source directory.
    """
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")
    if root == root.parent:
        raise ValueError("refusing to pack the filesystem root")
    archive = root.parent / (root.name + ".tar.xz")
    if archive.exists():
        raise FileExistsError(f"archive already exists: {archive}")

    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            if not path.exists():
                LOG.warning("Skipping broken symlink: %s", path)
            continue
        if path.is_file():
            files.append(path)
    if not files:
        raise RuntimeError(f"no files found to archive in: {root}")
    files.sort()

    prefix = root.name
    with tarfile.open(archive, mode="w:xz", preset=opts.preset, format=tarfile.PAX_FORMAT) as tar:
        for file in files:
            arcname = f"{prefix}/{file.relative_to(root).as_posix()}"
            info = tar.gettarinfo(name=str(file), arcname=arcname)
            with file.open("rb") as fh:
                tar.addfile(info, fh)

    try:
        if archive.stat().st_size <= 0:
            raise OSError("created archive is empty")
        with tarfile.open(archive, "r:xz") as tar:
            names = tar.getnames()
        expected_prefix = f"{prefix}/"
        outsiders = [n for n in names if not n.startswith(expected_prefix)]
        if outsiders:
            raise RuntimeError(f"archive contains members outside '{expected_prefix}': {outsiders[:5]}")
        if len(names) != len(files):
            raise RuntimeError(f"archive member count mismatch: expected {len(files)}, found {len(names)}")
    except Exception:
        archive.unlink(missing_ok=True)
        raise

    shutil.rmtree(root)
    return archive


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report_file_results(
    results: Sequence[FileResult],
    root: Path,
    *,
    mode: str,
    elapsed: float,
    report: str = "auto",
) -> None:
    """Print the summary block for a compress/decompress run.

    ``report`` selects the renderer: ``plain``, ``rich`` (requires the
    optional ``rich`` package) or ``auto`` (rich only on TTYs).
    """
    ok = [r for r in results if r.status == "ok"]
    skipped = [r for r in results if r.status == "skipped"]
    errors = [r for r in results if r.status == "error"]
    dryrun = [r for r in results if r.status == "dryrun"]
    original_total = sum(r.original_size for r in ok)
    result_total = sum(r.result_size for r in ok)
    freed = original_total - result_total
    pct = (freed / original_total * 100) if original_total else 0.0

    if report == "rich" or (report == "auto" and sys.stdout.isatty() and _try_import("rich")):
        _rich_report(results, root, mode=mode, elapsed=elapsed)
        return

    if errors:
        print("Failed items:")
        for res in errors[:10]:
            print(f"  ✗ {res.source}: {res.message}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more failures")
    print("─" * 60)
    verb = "compressed" if mode == "compress" else "decompressed"
    print(f"Total processed : {len(results)}")
    if dryrun:
        print(f"Dry-run items   : {len(dryrun)}")
    print(f"Total {verb:<11}: {len(ok)}")
    print(f"Total skipped   : {len(skipped)}")
    print(f"Total failed    : {len(errors)}")
    if ok:
        print(f"Total input size : {human_size(original_total)}")
        print(f"Total output size: {human_size(result_total)}")
        if mode == "compress":
            print(f"Disk space freed : {human_size(freed)} ({pct:.1f}%)")
        else:
            print(f"Disk space used  : {human_size(-freed)} ({-pct:.1f}% expansion)")
        print(f"Elapsed          : {elapsed:.2f}s (avg {elapsed / len(ok):.3f}s per item)")


def _rich_report(
    results: Sequence[FileResult],
    root: Path,
    *,
    mode: str,
    elapsed: float,
) -> None:
    """Render the run summary with :mod:`rich` tables (``xz_compressor.py`` style)."""
    rich = _try_import("rich")
    assert rich is not None
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = Console()
    ok = [r for r in results if r.status == "ok"]
    errors = [r for r in results if r.status == "error"]
    original_total = sum(r.original_size for r in ok)
    result_total = sum(r.result_size for r in ok)
    freed = original_total - result_total

    table = Table(
        title=f"🗜️  XZTOOL {mode.capitalize()} Results",
        box=box.ROUNDED,
        title_style="bold cyan",
        header_style="bold white",
    )
    table.add_column("Item", style="cyan", no_wrap=False)
    table.add_column("Original", justify="right", style="yellow")
    table.add_column("Result", justify="right", style="green")
    table.add_column("Time", justify="right", style="dim")
    table.add_column("Status", justify="center")
    for res in sorted(ok, key=lambda r: r.original_size, reverse=True)[:20]:
        try:
            rel = str(res.source.relative_to(root))
        except ValueError:
            rel = str(res.source)
        table.add_row(
            rel,
            human_size(res.original_size),
            human_size(res.result_size),
            f"{res.elapsed:.2f}s",
            "🗑️ ✅" if res.deleted else "✅",
        )
    if len(ok) > 20:
        table.add_row(f"... and {len(ok) - 20} more", "", "", "", "")
    console.print(table)
    for res in errors[:10]:
        console.print(f"[red]✗ {res.source}: {res.message}[/red]")

    summary = Text()
    summary.append("📊 Summary\n\n", style="bold cyan")
    summary.append(f"📁 Directory : {root}\n", style="bold white")
    summary.append(f"Processed: {len(results)}   Successful: {len(ok)}   Failed: {len(errors)}\n")
    summary.append(f"💾 Input size : {human_size(original_total)}\n", style="yellow")
    summary.append(f"📦 Output size: {human_size(result_total)}\n", style="green")
    summary.append(
        f"{'🎉 Freed' if mode == 'compress' else '💾 Used'}: {human_size(abs(freed))}\n",
        style="bold cyan",
    )
    summary.append(f"⏱️  Elapsed   : {elapsed:.2f}s", style="white")
    console.print(Panel(summary, border_style="cyan"))


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the ``xztool`` argument parser (subcommand based)."""
    parser = argparse.ArgumentParser(
        prog="xztool",
        description="Recursive LZMA/XZ/7Z compression toolkit merged from 13 sibling scripts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            commands:
              compress    recursively compress files (xz | lzma | 7z backends)
              decompress  recursively restore .xz/.tar.xz/.lzma/.7z files
              archive     one verified .tar.xz per top-level subdirectory
              pack        whole directory -> ../<name>.tar.xz, verified

            examples:
              xztool compress                          # cwd, xz, preset 9
              xztool compress ~/data -p 6 -w 8 -k      # keep originals
              xztool compress -t                       # tar subdirs first
              xztool compress --backend lzma --only-if-smaller --min-size 1024
              xztool compress -e txt log csv           # only those extensions
              xztool decompress ~/data --no-extract-tar
              xztool archive ~/buckets -w 4
              xztool pack ~/stuff -p 6
            """),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    pc = sub.add_parser("compress", aliases=["c"], help="compress files recursively")
    pc.add_argument("root", nargs="?", default=".", help="root directory (default: cwd)")
    pc.add_argument(
        "-f", "--backend", choices=sorted(BACKEND_SUFFIX), default="xz", help="compression backend (default: xz)"
    )
    pc.add_argument("-p", "--preset", type=int, choices=range(10), default=9, help="LZMA preset 0-9 (default: 9)")
    pc.add_argument(
        "-t", "--tar-subdirs", action="store_true", help="tar top-level subdirectories first, then compress them"
    )
    pc.add_argument("--threads", type=int, default=4, help="threads per file for the lzma_mt backend (default: 4)")
    pc.add_argument("-w", "--workers", type=int, default=0, help="parallel worker processes (default: auto)")
    pc.add_argument("-k", "--keep-original", action="store_true", help="keep original files after compression")
    pc.add_argument("--overwrite", action="store_true", help="overwrite existing outputs instead of skipping them")
    pc.add_argument("--extreme", action="store_true", help="enable LZMA PRESET_EXTREME (slower, slightly smaller)")
    pc.add_argument(
        "--only-if-smaller", action="store_true", help="discard the archive when it does not shrink the file"
    )
    pc.add_argument(
        "--min-size", type=int, default=MIN_SIZE_DEFAULT, help="only compress files of at least this many bytes"
    )
    pc.add_argument(
        "-e", "--ext", nargs="+", default=[], help="compress only files with these extensions (e.g. txt log)"
    )
    pc.add_argument("--exclude", nargs="+", default=[], help="exclude paths containing these substrings")
    pc.add_argument(
        "--include-media", action="store_true", help="also compress media/binary files (jpg, mp4, pdf, ...)"
    )
    pc.add_argument(
        "--no-skip-compressed", action="store_true", help="also compress foreign archives (own outputs stay skipped)"
    )
    pc.add_argument(
        "--out", type=Path, default=None, help="write archives into this directory (relative paths preserved)"
    )
    pc.add_argument(
        "--report", choices=("plain", "rich", "auto"), default="auto", help="summary renderer (default: auto)"
    )
    pc.add_argument("--dry-run", action="store_true", help="show what would happen")
    pc.add_argument("-v", "--verbose", action="store_true", help="chatty output")
    pc.set_defaults(handler=_cmd_compress)

    pd = sub.add_parser("decompress", aliases=["d"], help="decompress archives recursively")
    pd.add_argument("root", nargs="?", default=".", help="root directory (default: cwd)")
    pd.add_argument("-k", "--keep-original", action="store_true", help="keep the archive files after decompression")
    pd.add_argument(
        "--no-extract-tar", action="store_true", help="turn .tar.xz into .tar files instead of extracting them"
    )
    pd.add_argument("--overwrite", action="store_true", help="overwrite existing outputs instead of skipping them")
    pd.add_argument("-w", "--workers", type=int, default=0, help="parallel worker processes (default: auto)")
    pd.add_argument("--exclude", nargs="+", default=[], help="exclude paths containing these substrings")
    pd.add_argument(
        "--out", type=Path, default=None, help="write results into this directory (relative paths preserved)"
    )
    pd.add_argument(
        "--report", choices=("plain", "rich", "auto"), default="auto", help="summary renderer (default: auto)"
    )
    pd.add_argument("--dry-run", action="store_true", help="show what would happen")
    pd.add_argument("-v", "--verbose", action="store_true", help="chatty output")
    pd.set_defaults(handler=_cmd_decompress)

    pa = sub.add_parser("archive", help="verified .tar.xz per top-level subdirectory")
    pa.add_argument("root", nargs="?", default=".", help="directory containing subdirs")
    pa.add_argument("-p", "--preset", type=int, choices=range(10), default=9, help="xz preset (default: 9)")
    pa.add_argument("-w", "--workers", type=int, default=0, help="parallel worker processes (default: auto)")
    pa.add_argument("-k", "--keep-original", action="store_true", help="keep source directories after archiving")
    pa.add_argument("--no-verify", action="store_true", help="skip the read-back verification pass")
    pa.add_argument("-v", "--verbose", action="store_true", help="chatty output")
    pa.set_defaults(handler=_cmd_archive)

    pp = sub.add_parser("pack", help="pack one directory into ../<name>.tar.xz")
    pp.add_argument("root", nargs="?", default=".", help="directory to pack (default: cwd)")
    pp.add_argument(
        "-p",
        "--preset",
        type=int,
        choices=range(10),
        default=6,
        help="xz preset (default: 6, as in the original ptrr.py)",
    )
    pp.add_argument("-v", "--verbose", action="store_true", help="chatty output")
    pp.set_defaults(handler=_cmd_pack)
    return parser


def _configure_logging(verbose: bool) -> None:
    """Route xztool logs to stderr (results stay on stdout)."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname).1s %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _cmd_compress(args: argparse.Namespace) -> int:
    """Implement the ``compress`` subcommand."""
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        LOG.error("not a directory: %s", root)
        return 1
    if args.no_skip_compressed:
        skip: frozenset[str] = OWN_OUTPUT_EXTS
    else:
        merged = set(ARCHIVE_EXTS)
        if not args.include_media:
            merged |= MEDIA_EXTS
        skip = frozenset(merged)
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
    opts = Options(
        root=root,
        backend=args.backend,
        preset=args.preset,
        threads=args.threads,
        workers=args.workers,
        keep_original=args.keep_original,
        dry_run=args.dry_run,
        verbose=args.verbose,
        overwrite=args.overwrite,
        extreme=args.extreme,
        only_if_smaller=args.only_if_smaller,
        min_size=args.min_size,
        only_exts=_normalize_exts(args.ext),
        skip_exts=skip,
        exclude=tuple(args.exclude),
        out_dir=args.out,
        report=args.report,
    )
    started = time.perf_counter()
    if args.dry_run:
        print("[dry-run mode - no files will be modified]")
    results = run_tar_subdirs_then_compress(root, opts) if args.tar_subdirs else run_file_compression(root, opts)
    report_file_results(results, root, mode="compress", elapsed=time.perf_counter() - started, report=opts.report)
    return 1 if any(r.status == "error" for r in results) else 0


def _cmd_decompress(args: argparse.Namespace) -> int:
    """Implement the ``decompress`` subcommand."""
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        LOG.error("not a directory: %s", root)
        return 1
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
    opts = Options(
        root=root,
        keep_original=args.keep_original,
        dry_run=args.dry_run,
        verbose=args.verbose,
        overwrite=args.overwrite,
        workers=args.workers,
        exclude=tuple(args.exclude),
        extract_tar=not args.no_extract_tar,
        out_dir=args.out,
        report=args.report,
    )
    started = time.perf_counter()
    if args.dry_run:
        print("[dry-run mode - no files will be modified]")
    results = run_file_decompression(root, opts)
    report_file_results(results, root, mode="decompress", elapsed=time.perf_counter() - started, report=opts.report)
    return 1 if any(r.status == "error" for r in results) else 0


def _cmd_archive(args: argparse.Namespace) -> int:
    """Implement the ``archive`` subcommand."""
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        LOG.error("not a directory: %s", root)
        return 1
    opts = Options(
        root=root,
        preset=args.preset,
        workers=args.workers,
        keep_original=args.keep_original,
        verify=not args.no_verify,
        verbose=args.verbose,
    )
    results = archive_subdirectories(root, opts)
    return 0 if all(r.success for r in results) else 1


def _cmd_pack(args: argparse.Namespace) -> int:
    """Implement the ``pack`` subcommand."""
    root = Path(args.root).expanduser().resolve()
    opts = Options(root=root, preset=args.preset, verbose=args.verbose)
    try:
        archive = pack_directory(root, opts)
    except Exception as exc:  # noqa: BLE001 - single fatal error for pack
        LOG.error("pack failed: %s", exc)
        return 1
    print(f"✅ packed {root.name}/ -> {archive} ({human_size(archive.stat().st_size)})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "verbose", False))
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
