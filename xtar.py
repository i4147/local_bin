#!/data/data/com.termux/files/usr/bin/env python
import argparse
import gzip
import io
import lzma
import shutil
import sys
import tarfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from multiprocessing import Pool
from pathlib import Path
from typing import BinaryIO

import lz4.block
import lz4.frame
import py7zr
import zstandard

try:
    import brotli
except ImportError:
    import brotlicffi as brotli

MAX_WORKERS: int = 4
CHUNK_SIZE: int = 1024 * 1024

SUFFIX_TYPES: tuple[tuple[str, str], ...] = (
    (".tar.gz", "gz"),
    (".tgz", "gz"),
    (".tar.xz", "xz"),
    (".tar.zst", "tar.zst"),
    (".tar.br", "tar.br"),
    (".tar.bz2", "bz2"),
    (".tar.lz4", "tar.lz4"),
    (".tar.7z", "7z"),
    (".tar", "tar"),
    (".zip", "zip"),
    (".whl", "zip"),
    (".zst", "zst"),
    (".br", "br"),
    (".lz4", "lz4"),
    (".7z", "7z"),
)
SUPPORTED_EXTENSIONS: set[str] = {suffix for suffix, _ in SUFFIX_TYPES}
TARFILE_MODES: dict[str, str] = {"tar": "r:", "gz": "r:gz", "xz": "r:xz", "bz2": "r:bz2"}
STREAM_BACKENDS: dict[str, str] = {
    "tar.zst": "zstd",
    "zst": "zstd",
    "tar.br": "brotli",
    "br": "brotli",
    "tar.lz4": "lz4",
    "lz4": "lz4",
}
RAW_TYPES: frozenset[str] = frozenset({"zst", "br", "lz4"})
PY7ZR_ERRORS: tuple[type[BaseException], ...] = tuple(
    getattr(py7zr.exceptions, name)
    for name in ("Py7zrError", "ArchiveError", "Bad7zFile")
    if hasattr(py7zr.exceptions, name)
)
ARCHIVE_ERRORS: tuple[type[BaseException], ...] = (
    tarfile.TarError,
    lzma.LZMAError,
    gzip.BadGzipFile,
    zipfile.BadZipFile,
    OSError,
    EOFError,
    ValueError,
    RuntimeError,
    *PY7ZR_ERRORS,
    zstandard.ZstdError,
    brotli.error,
    lz4.block.LZ4BlockError,
)


def get_compression_type(path: Path) -> str | None:
    name: str = path.name.lower()
    for suffix, compression_type in SUFFIX_TYPES:
        if name.endswith(suffix):
            return compression_type
    return None


class BrotliStreamReader(io.RawIOBase):
    def __init__(self, source: BinaryIO) -> None:
        super().__init__()
        self._source: BinaryIO = source
        self._decompressor: brotli.Decompressor = brotli.Decompressor()
        self._buffer: bytes = b""
        self._finished: bool = False

    def readable(self) -> bool:
        return True

    def _fill(self) -> None:
        chunk: bytes = self._source.read(CHUNK_SIZE)
        if not chunk:
            if not self._decompressor.is_finished():
                raise brotli.error("Truncated brotli stream")
            self._finished = True
            return
        self._buffer += self._decompressor.process(chunk)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            while not self._finished:
                self._fill()
            data: bytes = self._buffer
            self._buffer = b""
            return data
        while len(self._buffer) < size and not self._finished:
            self._fill()
        data = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return data


def zstd_reader(source: BinaryIO) -> BinaryIO:
    dctx: zstandard.ZstdDecompressor = zstandard.ZstdDecompressor()
    try:
        return dctx.stream_reader(source, read_across_frames=True)
    except TypeError:
        return dctx.stream_reader(source)


@contextmanager
def decompression_stream(path: Path, compression_type: str) -> Iterator[BinaryIO]:
    backend: str | None = STREAM_BACKENDS.get(compression_type)
    if backend is None:
        raise ValueError(f"Unsupported stream compression: {compression_type}")
    if backend == "lz4":
        with lz4.frame.open(str(path), "rb") as source:
            yield source
        return
    source: BinaryIO = open(path, "rb")
    try:
        if backend == "zstd":
            reader: BinaryIO = zstd_reader(source)
        else:
            reader = BrotliStreamReader(source)
        try:
            yield reader
        finally:
            reader.close()
    finally:
        source.close()


@contextmanager
def open_tar(path: Path, compression_type: str) -> Iterator[tarfile.TarFile]:
    mode: str | None = TARFILE_MODES.get(compression_type)
    if mode is not None:
        with tarfile.open(path, mode) as tar:
            yield tar
        return
    with decompression_stream(path, compression_type) as reader:
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            yield tar


def check_integrity(archive_path: Path) -> bool:
    compression_type: str | None = get_compression_type(archive_path)
    if compression_type is None:
        return False
    try:
        if compression_type == "zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                return zf.testzip() is None
        if compression_type == "7z":
            with py7zr.SevenZipFile(archive_path, mode="r") as z:
                z.test()
            return True
        if compression_type in RAW_TYPES:
            with decompression_stream(archive_path, compression_type) as reader:
                while reader.read(CHUNK_SIZE):
                    pass
            return True
        with open_tar(archive_path, compression_type) as tar:
            tar.getmembers()
        return True
    except ARCHIVE_ERRORS as e:
        print(f"Integrity check failed for {archive_path.name}: {e}")
        return False


def scan_archives(directory: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in SUPPORTED_EXTENSIONS:
        found.update(path for path in directory.rglob(f"*{pattern}") if path.is_file())
    return sorted(found)


def nested_candidates(extract_dir: Path, names: list[str]) -> list[Path]:
    base: Path = extract_dir.resolve()
    candidates: set[Path] = set()
    for name in names:
        path: Path = extract_dir / name
        if not path.is_file():
            continue
        if get_compression_type(path) is None:
            continue
        if not path.resolve().is_relative_to(base):
            continue
        candidates.add(path)
    return sorted(candidates)


def extract_archive(archive_path: Path) -> bool:
    archive_path = Path(archive_path)
    if not archive_path.exists():
        return False
    print(f"Processing: {archive_path.name}")
    if not check_integrity(archive_path):
        print(f"Skipping {archive_path.name} (integrity check failed)")
        return False
    compression_type: str | None = get_compression_type(archive_path)
    if compression_type is None:
        print(f"Skipping {archive_path.name} (unsupported format)")
        return False
    extract_dir: Path = archive_path.parent
    names: list[str] = []
    try:
        if compression_type == "zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(path=extract_dir)
                names = zf.namelist()
        elif compression_type == "7z":
            with py7zr.SevenZipFile(archive_path, mode="r") as z:
                z.extractall(path=extract_dir)
                names = z.getnames()
        elif compression_type in RAW_TYPES:
            target: Path = archive_path.with_suffix("")
            if target == archive_path:
                raise ValueError(f"Cannot derive decompressed name for {archive_path.name}")
            src: BinaryIO
            dst: BinaryIO
            with decompression_stream(archive_path, compression_type) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            names = [target.name]
        else:
            with open_tar(archive_path, compression_type) as tar:
                tar.extractall(path=extract_dir, filter="data")
                names = [member.name for member in tar.members if member.isfile()]
        archive_path.unlink(missing_ok=True)
        print(f"Successfully extracted and removed: {archive_path.name}")
    except ARCHIVE_ERRORS as e:
        print(f"Extraction failed for {archive_path.name}: {e}")
        return False
    for nested in nested_candidates(extract_dir, names):
        extract_archive(nested)
    return True


def find_archives(directory: str = ".") -> list[Path]:
    return scan_archives(Path(directory))


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Extract supported archives; originals are removed after successful extraction."
    )
    parser.add_argument(
        "archives",
        nargs="*",
        type=Path,
        help="archive files to extract; defaults to a recursive scan of the current directory",
    )
    args: argparse.Namespace = parser.parse_args()
    archives: list[Path]
    if args.archives:
        archives = sorted({path.resolve() for path in args.archives if path.is_file()})
    else:
        archives = find_archives()
    if not archives:
        print("No archive files found or specified.")
        print(f"Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        parser.print_usage()
        sys.exit(1)
    print(f"Found {len(archives)} archive(s) to process")
    print(f"Using {MAX_WORKERS} workers")
    print("-" * 40)
    with Pool(processes=MAX_WORKERS) as pool:
        results: list[bool] = pool.map(extract_archive, archives)
    successful: int = sum(1 for result in results if result)
    failed: int = sum(1 for result in results if not result)
    print("-" * 40)
    print(f"Summary: {successful} successful, {failed} failed/skipped")


if __name__ == "__main__":
    main()
