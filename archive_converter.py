#!/data/data/com.termux/files/usr/bin/env python
"""Convert archives and compressed files between formats with auto-detection.

This module exposes a Typer-based command line interface and reusable helpers for
extracting, repacking, recompressing, and verifying archive/compressed files.

Direct use of ``click`` has been removed. Typer remains the CLI framework.
"""

from __future__ import annotations

import bz2
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
import gzip
import hashlib
import importlib
import importlib.util
import io
import lzma
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import sys
import tarfile
import tempfile
import threading
import time
from types import ModuleType
from typing import IO, Any, Literal, cast
import zipfile

from loguru import logger
import typer


CHUNK: int = 1 << 20
"""Read/write chunk size in bytes."""
ZSTD_NATIVE: bool = sys.version_info >= (3, 14)
"""Whether the standard library provides ``compression.zstd``."""
log = logger
Status = Literal["ok", "skipped", "dry-run", "failed"]
"""Status values returned by a per-file conversion."""


class ToolError(Exception):
    """Expected, user-facing conversion error."""


class Codec(Enum):
    """Supported compression codecs."""

    GZ = "gz"
    BZ2 = "bz2"
    XZ = "xz"
    ZST = "zst"
    BR = "br"
    LZ4 = "lz4"

    @property
    def suffix(self) -> str:
        """Return the conventional file suffix for this codec."""
        return "." + self.value


class Container(Enum):
    """Supported archive/container formats."""

    RAW = "raw"
    TAR = "tar"
    ZIP = "zip"
    SEVENZ = "7z"


@dataclass(frozen=True, slots=True)
class Fmt:
    """A container/codec combination such as ``tar.gz`` or ``zip``."""

    container: Container = Container.RAW
    codec: Codec | None = None
    wheel: bool = False

    @property
    def key(self) -> tuple[Container, Codec | None]:
        """Return the container/codec identity used for detection comparisons."""
        return self.container, self.codec

    @property
    def suffix(self) -> str:
        """Return the file suffix for this format."""
        match self.container:
            case Container.ZIP:
                return ".whl" if self.wheel else ".zip"
            case Container.SEVENZ:
                return ".7z"
            case Container.TAR:
                return ".tar" + (self.codec.suffix if self.codec else "")
            case Container.RAW:
                return self.codec.suffix if self.codec else ""

    @property
    def label(self) -> str:
        """Return a human-readable label without the leading dot."""
        return self.suffix.lstrip(".")


@dataclass(frozen=True, slots=True)
class Options:
    """Runtime options for a conversion run."""

    to: str
    remove: bool = False
    output_dir: Path | None = None
    force: bool = False
    level: int | None = None
    dry_run: bool = False
    verify: bool = False
    strict: bool = False
    tmpdir: Path | None = None


@dataclass(slots=True)
class Result:
    """Outcome of converting one source file."""

    src: Path
    status: Status
    out: Path | None = None
    message: str = ""


CODEC_ALIASES: dict[str, Codec] = {
    "gz": Codec.GZ,
    "gzip": Codec.GZ,
    "bz2": Codec.BZ2,
    "bzip2": Codec.BZ2,
    "xz": Codec.XZ,
    "zst": Codec.ZST,
    "zstd": Codec.ZST,
    "br": Codec.BR,
    "brotli": Codec.BR,
    "lz4": Codec.LZ4,
}
TAR_SHORT: dict[str, Codec] = {
    "tgz": Codec.GZ,
    "tbz": Codec.BZ2,
    "tbz2": Codec.BZ2,
    "txz": Codec.XZ,
    "tzst": Codec.ZST,
    "tzstd": Codec.ZST,
    "tlz4": Codec.LZ4,
    "tbr": Codec.BR,
}
CODEC_LEVELS: dict[Codec, tuple[int, int, int]] = {
    Codec.GZ: (0, 9, 6),
    Codec.BZ2: (1, 9, 9),
    Codec.XZ: (0, 9, 6),
    Codec.ZST: (1, 22, 3),
    Codec.BR: (0, 11, 6),
    Codec.LZ4: (0, 16, 0),
}
MAGIC: tuple[tuple[bytes, Codec], ...] = (
    (b"\x1f\x8b", Codec.GZ),
    (b"\xfd7zXZ\x00", Codec.XZ),
    (b"\x28\xb5\x2f\xfd", Codec.ZST),
    (b"\x04\x22\x4d\x18", Codec.LZ4),
)
ZIP_MAGICS: tuple[bytes, ...] = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
SEVENZ_MAGIC: bytes = b"7z\xbc\xaf\x27\x1c"


def require(module: str, pip: str) -> ModuleType:
    """Import *module* or raise :class:`ToolError` with an install hint."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        msg = f"missing dependency: pip install {pip}"
        raise ToolError(msg) from exc


def zstd_backend() -> ModuleType:
    """Return the available zstd backend module."""
    if ZSTD_NATIVE:
        return importlib.import_module("compression.zstd")
    return require("zstandard", "zstandard")


def human(size: float) -> str:
    """Format a byte count using binary units."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


class BrotliReader(io.RawIOBase):
    """Streaming brotli decompressor backed by a file path."""

    def __init__(self, path: Path) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__()
        self._decoder = require("brotli", "brotli").Decompressor()
        self._raw = path.open("rb")
        self._pending = b""
        self._pos = 0
        self._eof = False

    def readable(self) -> bool:
        """Return whether this stream is readable."""
        return True

    def readinto(self, buffer: Any) -> int:
        """Read decompressed bytes into *buffer*."""
        while self._pos >= len(self._pending) and not self._eof:
            chunk = self._raw.read(CHUNK)
            if chunk:
                self._pending = self._decoder.process(chunk)
                self._pos = 0
            else:
                self._eof = True
                if not self._decoder.is_finished():
                    msg = "truncated brotli stream"
                    raise EOFError(msg)
        size = min(len(buffer), len(self._pending) - self._pos)
        buffer[:size] = self._pending[self._pos : self._pos + size]
        self._pos += size
        return size

    def close(self) -> None:
        """Close the underlying file handle."""
        if not self.closed:
            self._raw.close()
        super().close()


class BrotliWriter(io.RawIOBase):
    """Streaming brotli compressor backed by a file path."""

    def __init__(self, path: Path, quality: int) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__()
        self._encoder = require("brotli", "brotli").Compressor(quality=quality, lgwin=24)
        self._raw = path.open("wb")

    def writable(self) -> bool:
        """Return whether this stream is writable."""
        return True

    def write(self, data: Any) -> int:
        """Compress and write *data*, returning the input byte count."""
        payload = bytes(data)
        produced = self._encoder.process(payload)
        if produced:
            self._raw.write(produced)
        return len(payload)

    def close(self) -> None:
        """Finish the brotli stream and close the underlying file handle."""
        if self.closed:
            return
        try:
            self._raw.write(self._encoder.finish())
        finally:
            self._raw.close()
            super().close()


def open_reader(path: Path, codec: Codec | None) -> IO[bytes]:
    """Open *path* for binary reading, optionally decompressing *codec*."""
    match codec:
        case None:
            stream = path.open("rb")
        case Codec.GZ:
            stream = gzip.open(path, "rb")
        case Codec.BZ2:
            stream = bz2.open(path, "rb")
        case Codec.XZ:
            stream = lzma.open(path, "rb")
        case Codec.ZST:
            zstd = zstd_backend()
            if ZSTD_NATIVE:
                stream = zstd.open(path, "rb")
            else:
                stream = zstd.ZstdDecompressor().stream_reader(
                    path.open("rb"),
                    read_across_frames=True,
                    closefd=True,
                )
        case Codec.LZ4:
            stream = require("lz4.frame", "lz4").open(path, "rb")
        case Codec.BR:
            stream = io.BufferedReader(BrotliReader(path), buffer_size=CHUNK)
        case _:
            msg = f"unsupported reader codec: {codec!r}"
            raise ToolError(msg)
    return cast("IO[bytes]", stream)


def open_writer(path: Path, codec: Codec | None, level: int) -> IO[bytes]:
    """Open *path* for binary writing, optionally compressing with *codec*."""
    match codec:
        case None:
            stream = path.open("wb")
        case Codec.GZ:
            stream = gzip.open(path, "wb", compresslevel=level)
        case Codec.BZ2:
            stream = bz2.open(path, "wb", compresslevel=level)
        case Codec.XZ:
            stream = lzma.open(path, "wb", preset=level, check=lzma.CHECK_CRC64)
        case Codec.ZST:
            zstd = zstd_backend()
            if ZSTD_NATIVE:
                stream = zstd.open(
                    path,
                    "wb",
                    level=level,
                    options={zstd.CompressionParameter.checksum_flag: 1},
                )
            else:
                stream = zstd.ZstdCompressor(
                    level=level,
                    write_checksum=True,
                ).stream_writer(path.open("wb"), closefd=True)
        case Codec.LZ4:
            stream = require("lz4.frame", "lz4").open(
                path,
                "wb",
                compression_level=level,
                content_checksum=True,
            )
        case Codec.BR:
            stream = io.BufferedWriter(BrotliWriter(path, level), buffer_size=CHUNK)
        case _:
            msg = f"unsupported writer codec: {codec!r}"
            raise ToolError(msg)
    return cast("IO[bytes]", stream)


def read_full(stream: IO[bytes], size: int) -> bytes:
    """Read up to *size* bytes, tolerating short reads."""
    parts: list[bytes] = []
    got = 0
    while got < size:
        chunk = stream.read(size - got)
        if not chunk:
            break
        parts.append(chunk)
        got += len(chunk)
    return b"".join(parts)


def peek(path: Path, codec: Codec | None, size: int = 512) -> bytes:
    """Read and decompress the first *size* bytes of *path*."""
    with open_reader(path, codec) as stream:
        return read_full(stream, size)


def is_tar_block(block: bytes) -> bool:
    """Return whether *block* starts with a plausible tar header."""
    if len(block) < 512:
        return False
    try:
        info = tarfile.TarInfo.frombuf(
            block[:512],
            tarfile.ENCODING,
            "surrogateescape",
        )
    except tarfile.TarError:
        return False
    return bool(info.name)


def codec_from_magic(head: bytes) -> Codec | None:
    """Detect a compression codec from magic bytes."""
    for magic, codec in MAGIC:
        if head.startswith(magic):
            return codec
    if len(head) > 3 and head[:3] == b"BZh" and 0x31 <= head[3] <= 0x39:
        return Codec.BZ2
    if len(head) >= 4 and head[1:4] == b"\x2a\x4d\x18" and 0x50 <= head[0] <= 0x5F:
        return Codec.ZST
    return None


def fmt_from_name(name: str) -> tuple[Fmt, str] | None:
    """Infer a format and output stem from a filename."""
    low = name.lower()
    for suffix, container, wheel in (
        (".zip", Container.ZIP, False),
        (".whl", Container.ZIP, True),
        (".7z", Container.SEVENZ, False),
        (".tar", Container.TAR, False),
    ):
        if low.endswith(suffix):
            return Fmt(container, None, wheel), name[: -len(suffix)]
    dot = low.rfind(".")
    if dot < 0:
        return None
    ext = low[dot + 1 :]
    stem = name[:dot]
    if ext in TAR_SHORT:
        return Fmt(Container.TAR, TAR_SHORT[ext]), stem
    codec = CODEC_ALIASES.get(ext)
    if codec is None:
        return None
    if stem.lower().endswith(".tar"):
        return Fmt(Container.TAR, codec), stem[:-4]
    return Fmt(Container.RAW, codec), stem


def sniff(path: Path, hint: Codec | None) -> tuple[Container, Codec | None] | None:
    """Detect container/codec from file content, using *hint* for ambiguous data."""
    with path.open("rb") as handle:
        head = read_full(handle, 512)
    if head.startswith(ZIP_MAGICS):
        return Container.ZIP, None
    if head.startswith(SEVENZ_MAGIC):
        return Container.SEVENZ, None
    codec = codec_from_magic(head)
    guessed = False
    if codec is None:
        if is_tar_block(head):
            return Container.TAR, None
        if hint is not Codec.BR:
            return None
        codec, guessed = Codec.BR, True
    try:
        inner = peek(path, codec)
    except Exception as exc:
        if guessed:
            return None
        msg = f"cannot read {codec.value} stream: {exc}"
        raise ToolError(msg) from exc
    return (Container.TAR if is_tar_block(inner) else Container.RAW), codec


def detect(path: Path, strict: bool) -> tuple[Fmt, str]:
    """Detect the format of *path* and return it with the output stem."""
    named = fmt_from_name(path.name)
    hint = named[0].codec if named else None
    sniffed = sniff(path, hint)
    if named is None:
        if sniffed is None:
            msg = "unrecognized format (unknown extension, no matching magic bytes)"
            raise ToolError(msg)
        return Fmt(*sniffed), path.name
    fmt, stem = named
    if sniffed is None:
        msg = f"extension says {fmt.label} but magic bytes do not confirm it"
        raise ToolError(msg)
    if fmt.key == sniffed:
        return fmt, stem
    actual = Fmt(*sniffed)
    message = f"extension says {fmt.label} but content is {actual.label}"
    if strict:
        raise ToolError(message)
    log.warning("warn     %s: %s; trusting content", path.name, message)
    return actual, stem


def parse_target(token: str, src: Fmt) -> Fmt:
    """Parse a user-supplied target format token."""
    value = token.strip().lower().lstrip(".")
    fixed: dict[str, Fmt] = {
        "zip": Fmt(Container.ZIP),
        "whl": Fmt(Container.ZIP, None, True),
        "7z": Fmt(Container.SEVENZ),
        "tar": Fmt(Container.TAR),
    }
    if value in fixed:
        return fixed[value]
    if value in TAR_SHORT:
        return Fmt(Container.TAR, TAR_SHORT[value])
    if value.startswith("tar."):
        codec = CODEC_ALIASES.get(value[4:])
        if codec is not None:
            return Fmt(Container.TAR, codec)
    elif value in CODEC_ALIASES:
        container = Container.RAW if src.container is Container.RAW else Container.TAR
        return Fmt(container, CODEC_ALIASES[value])
    msg = f"unsupported target format: {token!r}"
    raise ToolError(msg)


def level_spec(dst: Fmt) -> tuple[int, int, int] | None:
    """Return (minimum, maximum, default) compression level for *dst*."""
    if dst.codec is not None:
        return CODEC_LEVELS[dst.codec]
    if dst.container in (Container.ZIP, Container.SEVENZ):
        return 0, 9, 6
    return None


def resolve_level(spec: tuple[int, int, int] | None, requested: int | None) -> int:
    """Validate and resolve a requested compression level."""
    if spec is None:
        return 0
    low, high, default = spec
    if requested is None:
        return default
    if not low <= requested <= high:
        msg = f"level must be within {low}..{high} for this target"
        raise ToolError(msg)
    return requested


def check_name(name: str) -> PurePosixPath:
    """Validate an archive member name and return its POSIX path form."""
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute() or PureWindowsPath(name).drive or ".." in pure.parts:
        msg = f"unsafe path in archive: {name!r}"
        raise ToolError(msg)
    return pure


def safe_path(root: Path, pure: PurePosixPath) -> Path:
    """Create parent directories and return a path guaranteed under *root*."""
    target = root.joinpath(*pure.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.resolve().is_relative_to(root.resolve()):
        msg = f"unsafe path in archive: {pure.as_posix()!r}"
        raise ToolError(msg)
    return target


def extract_zip(src: Path, root: Path) -> None:
    """Extract a ZIP archive, rejecting traversal and unsafe symlinks."""
    with zipfile.ZipFile(src) as archive:
        for info in archive.infolist():
            pure = check_name(info.filename)
            if not pure.parts:
                continue
            target = safe_path(root, pure)
            mode = info.external_attr >> 16
            if info.is_dir():
                target.mkdir(exist_ok=True)
                continue
            if stat.S_ISLNK(mode):
                link = archive.read(info).decode("utf-8", "surrogateescape")
                if (target.parent / link).resolve().is_relative_to(root.resolve()):
                    target.unlink(missing_ok=True)
                    os.symlink(link, target)
                else:
                    log.warning("warn     skipping unsafe symlink %s", info.filename)
                continue
            with archive.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, CHUNK)
            os.chmod(target, ((stat.S_IMODE(mode) & 0o777) or 0o644) | 0o600)
            stamp = time.mktime((*info.date_time, 0, 0, -1))
            os.utime(target, (stamp, stamp))


def extract_tar(src: Path, codec: Codec | None, root: Path) -> None:
    """Extract a tar stream using Python's safe data filter."""
    if not hasattr(tarfile, "data_filter"):
        msg = "this Python lacks safe tar extraction filters; update Python"
        raise ToolError(msg)
    with (
        open_reader(src, codec) as stream,
        tarfile.open(fileobj=stream, mode="r|") as archive,
    ):
        archive.extractall(root, filter="data")


def extract_7z(src: Path, root: Path) -> None:
    """Extract a 7z archive after validating member names."""
    py7zr = require("py7zr", "py7zr")
    with py7zr.SevenZipFile(src, "r") as archive:
        for name in archive.getnames():
            check_name(name)
        archive.extractall(path=root)


def extract_raw(src: Path, codec: Codec | None, root: Path, stem: str) -> None:
    """Decompress a bare compressed stream into a single output file."""
    with (
        open_reader(src, codec) as source,
        (root / (stem or "data")).open("wb") as sink,
    ):
        shutil.copyfileobj(source, sink, CHUNK)


def extract(src: Path, fmt: Fmt, stem: str, root: Path) -> None:
    """Extract/decompress *src* into *root* according to *fmt*."""
    match fmt.container:
        case Container.ZIP:
            extract_zip(src, root)
        case Container.SEVENZ:
            extract_7z(src, root)
        case Container.TAR:
            extract_tar(src, fmt.codec, root)
        case Container.RAW:
            extract_raw(src, fmt.codec, root, stem)


def entries(root: Path) -> list[Path]:
    """Return archive entries under *root* in deterministic order."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        base = Path(dirpath)
        found.extend(base / name for name in dirnames)
        found.extend(base / name for name in sorted(filenames))
    return found


def scrub(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Normalize tar metadata for reproducible output."""
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def zip_time(stamp: float) -> tuple[int, int, int, int, int, int]:
    """Convert a timestamp into the tuple expected by ``ZipInfo``."""
    moment = time.localtime(max(stamp, 315532800))
    return (
        moment.tm_year,
        moment.tm_mon,
        moment.tm_mday,
        moment.tm_hour,
        moment.tm_min,
        moment.tm_sec,
    )


def pack_tar(
    root: Path,
    items: list[Path],
    part: Path,
    codec: Codec | None,
    level: int,
) -> None:
    """Pack *items* into a tar stream, optionally compressed by *codec*."""
    with (
        open_writer(part, codec, level) as stream,
        tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as archive,
    ):
        for item in items:
            archive.add(
                item,
                arcname=item.relative_to(root).as_posix(),
                recursive=False,
                filter=scrub,
            )


def pack_zip(root: Path, items: list[Path], part: Path, level: int) -> None:
    """Pack *items* into a ZIP archive, preserving symlinks."""
    method = zipfile.ZIP_DEFLATED if level > 0 else zipfile.ZIP_STORED
    with zipfile.ZipFile(
        part,
        "w",
        compression=method,
        compresslevel=level,
        allowZip64=True,
        strict_timestamps=False,
    ) as archive:
        for item in items:
            arcname = item.relative_to(root).as_posix()
            if item.is_symlink():
                info = zipfile.ZipInfo(arcname, zip_time(item.lstat().st_mtime))
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, os.readlink(item))
            else:
                archive.write(item, arcname)


def pack_7z(root: Path, items: list[Path], part: Path, level: int) -> None:
    """Pack *items* into a 7z archive."""
    py7zr = require("py7zr", "py7zr")
    filters = [{"id": lzma.FILTER_LZMA2, "preset": level}]
    with py7zr.SevenZipFile(part, "w", filters=filters) as archive:
        for item in items:
            archive.write(item, arcname=item.relative_to(root).as_posix())


def pack(root: Path, items: list[Path], dst: Fmt, part: Path, level: int) -> None:
    """Pack *items* into the target container/codec *dst*."""
    match dst.container:
        case Container.TAR:
            pack_tar(root, items, part, dst.codec, level)
        case Container.ZIP:
            pack_zip(root, items, part, level)
        case Container.SEVENZ:
            pack_7z(root, items, part, level)
        case Container.RAW:
            msg = "cannot pack multiple entries into a bare compressed file"
            raise ToolError(msg)


def recode(
    src: Path,
    src_codec: Codec | None,
    part: Path,
    dst_codec: Codec | None,
    level: int,
) -> str:
    """Stream-recompress *src* and return the SHA-256 of the decoded payload."""
    digest = hashlib.sha256()
    with (
        open_reader(src, src_codec) as reader,
        open_writer(part, dst_codec, level) as writer,
    ):
        while chunk := reader.read(CHUNK):
            digest.update(chunk)
            writer.write(chunk)
    return digest.hexdigest()


def staged(
    src: Path,
    fmt: Fmt,
    stem: str,
    dst: Fmt,
    part: Path,
    level: int,
    base: Path,
) -> None:
    """Extract *src* to a temporary directory, then pack into *dst*."""
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".archconv-", dir=base) as workdir:
        root = Path(workdir).resolve()
        extract(src, fmt, stem, root)
        pack(root, entries(root), dst, part, level)


def verify_output(out: Path, dst: Fmt, digest: str | None) -> None:
    """Verify that *out* is readable and, when possible, matches *digest*."""
    if digest is not None:
        check = hashlib.sha256()
        with open_reader(out, dst.codec) as reader:
            while chunk := reader.read(CHUNK):
                check.update(chunk)
        if check.hexdigest() != digest:
            msg = "verification failed: decompressed content differs from source"
            raise ToolError(msg)
        return
    match dst.container:
        case Container.ZIP:
            with zipfile.ZipFile(out) as archive:
                bad = archive.testzip()
            if bad:
                msg = f"verification failed: corrupt entry {bad!r}"
                raise ToolError(msg)
        case Container.SEVENZ:
            with require("py7zr", "py7zr").SevenZipFile(out, "r") as archive:
                bad = archive.testzip()
            if bad:
                msg = f"verification failed: corrupt entry {bad!r}"
                raise ToolError(msg)
        case Container.TAR:
            with (
                open_reader(out, dst.codec) as reader,
                tarfile.open(fileobj=reader, mode="r|") as archive,
            ):
                for member in archive:
                    if member.isreg():
                        handle = archive.extractfile(member)
                        if handle is not None:
                            while handle.read(CHUNK):
                                pass
        case Container.RAW:
            pass


def streamable(src: Fmt, dst: Fmt) -> bool:
    """Return whether *src* can be converted by streaming without staging."""
    return src.container is dst.container and src.container in (
        Container.RAW,
        Container.TAR,
    )


def convert_one(src: Path, opts: Options) -> Result:
    """Convert a single file according to *opts*."""
    if not src.is_file():
        msg = "not a regular file"
        raise ToolError(msg)
    fmt, stem = detect(src, opts.strict)
    dst = parse_target(opts.to, fmt)
    out_dir = opts.output_dir or src.parent
    out = out_dir / (stem + dst.suffix)
    if out.resolve() == src.resolve():
        return Result(src, "skipped", out, f"already {dst.label}")
    if out.exists() and not opts.force:
        return Result(src, "skipped", out, f"{out.name} exists (use --force)")
    level = resolve_level(level_spec(dst), opts.level)
    direct = streamable(fmt, dst)
    plan = f"{src.name} [{fmt.label}] -> {out.name} [{dst.label}] ({'stream' if direct else 'staged'})"
    if opts.dry_run:
        return Result(src, "dry-run", out, plan)
    out_dir.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.name}.{os.getpid()}.{threading.get_ident()}.part")
    finished = False
    try:
        digest: str | None = None
        if direct:
            digest = recode(src, fmt.codec, part, dst.codec, level)
        else:
            staged(src, fmt, stem, dst, part, level, opts.tmpdir or out_dir)
        if opts.verify or opts.remove:
            verify_output(part, dst, digest)
        os.replace(part, out)
        finished = True
    finally:
        if not finished:
            part.unlink(missing_ok=True)
    message = f"{src.name} -> {out.name}  ({human(src.stat().st_size)} -> {human(out.stat().st_size)})"
    if opts.remove:
        src.unlink()
        message += "  [source removed]"
    return Result(src, "ok", out, message)


def convert(src: Path, opts: Options) -> Result:
    """Convert *src*, capturing expected and unexpected failures as a result."""
    try:
        return convert_one(src, opts)
    except Exception as exc:
        log.debug("traceback", exc_info=True)
        text = str(exc) if isinstance(exc, ToolError) else f"{type(exc).__name__}: {exc}"
        return Result(src, "failed", None, text)


def report(result: Result) -> Result:
    """Log a conversion result and return it unchanged."""
    match result.status:
        case "ok":
            log.info("ok       %s", result.message)
        case "dry-run":
            log.info("plan     %s", result.message)
        case "skipped":
            log.warning("skip     %s: %s", result.src.name, result.message)
        case "failed":
            log.error("FAILED   %s: %s", result.src.name, result.message)
    return result


def module_available(name: str) -> bool:
    """Return whether an importable module named *name* exists."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def print_formats() -> None:
    """Print supported target formats and dependency availability."""
    rows: list[tuple[str, str | None]] = [
        ("zip", None),
        ("whl", None),
        ("tar", None),
        ("gz", None),
        ("bz2", None),
        ("xz", None),
        ("zst", "compression.zstd" if ZSTD_NATIVE else "zstandard"),
        ("br", "brotli"),
        ("lz4", "lz4.frame"),
        ("7z", "py7zr"),
    ]
    for name, module in rows:
        ok = module is None or module_available(module)
        note = "" if module is None else f"  ({module})"
        print(f"{name:<5}{'available' if ok else 'missing  '}{note}")
    print("tar variants: tar.<codec>, tgz, tbz2, txz, tzst, tlz4, tbr")


def main(
    *,
    inputs: list[Path],
    to: str | None,
    remove: bool = False,
    output_dir: Path | None = None,
    force: bool = False,
    level: int | None = None,
    dry_run: bool = False,
    jobs: int = 1,
    verify: bool = False,
    strict: bool = False,
    tmpdir: Path | None = None,
    list_formats: bool = False,
    verbose: bool = False,
    quiet: bool = False,
) -> int:
    """Run conversions and return a process exit code."""
    if list_formats:
        print_formats()
        return 0
    if not inputs:
        msg = "no input files given"
        raise typer.BadParameter(msg)
    if not to:
        msg = "--to is required"
        raise typer.BadParameter(msg)
    logger.remove()
    log_level = "ERROR" if quiet else ("DEBUG" if verbose else "INFO")
    logger.add(sys.stderr, level=log_level, format="{message}")
    opts = Options(
        to=to,
        remove=remove,
        output_dir=output_dir,
        force=force,
        level=level,
        dry_run=dry_run,
        verify=verify,
        strict=strict,
        tmpdir=tmpdir,
    )
    seen: set[Path] = set()
    sources: list[Path] = []
    for path in inputs:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            sources.append(path)

    def run(path: Path) -> Result:
        return report(convert(path, opts))

    job_count = max(1, min(jobs or os.cpu_count() or 1, len(sources)))
    if job_count == 1:
        results = [run(path) for path in sources]
    else:
        with ThreadPoolExecutor(max_workers=job_count) as pool:
            results = list(pool.map(run, sources))
    states: tuple[Status, ...] = ("ok", "skipped", "dry-run", "failed")
    counts: dict[Status, int] = {state: sum(1 for item in results if item.status == state) for state in states}
    if len(results) > 1:
        log.info(
            "done: %d converted, %d skipped, %d planned, %d failed",
            counts["ok"],
            counts["skipped"],
            counts["dry-run"],
            counts["failed"],
        )
    return 1 if counts["failed"] else 0


app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
)


@app.command(help="Convert archives and compressed files between formats with auto-detected input.")
def cli(
    inputs: list[Path] = typer.Argument(None, help="Input archive or compressed files."),
    to: str | None = typer.Option(None, "--to", "-t", help="Target format, e.g. zip, tar.gz, zst."),
    remove: bool = typer.Option(False, "--remove", help="Remove source after successful conversion."),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o", help="Directory for outputs."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing output files."),
    level: int | None = typer.Option(None, "--level", "-l", help="Compression level (format dependent)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the conversion plan without writing."),
    jobs: int = typer.Option(1, "--jobs", "-j", help="Number of files to convert in parallel."),
    verify: bool = typer.Option(False, "--verify", help="Verify output after conversion."),
    strict: bool = typer.Option(False, "--strict", help="Fail when extension and content disagree."),
    tmpdir: Path | None = typer.Option(None, "--tmpdir", help="Temporary directory for staged conversions."),
    list_formats: bool = typer.Option(False, "--list-formats", help="List supported formats and exit."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only log errors."),
) -> None:
    """Typer entry point. Delegates to :func:`main`."""
    if verbose and quiet:
        msg = "-v and -q are mutually exclusive"
        raise typer.BadParameter(msg)
    raise typer.Exit(
        code=main(
            inputs=list(inputs or []),
            to=to,
            remove=remove,
            output_dir=output_dir,
            force=force,
            level=level,
            dry_run=dry_run,
            jobs=jobs,
            verify=verify,
            strict=strict,
            tmpdir=tmpdir,
            list_formats=list_formats,
            verbose=verbose,
            quiet=quiet,
        )
    )


if __name__ == "__main__":
    app()
