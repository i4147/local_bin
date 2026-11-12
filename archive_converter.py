#!/data/data/com.termux/files/usr/bin/env python
from __future__ import annotations

import argparse
import bz2
import gzip
import hashlib
import importlib
import importlib.util
import io
import logging
import lzma
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import IO, Any, Literal, cast

CHUNK = 1 << 20
ZSTD_NATIVE = sys.version_info >= (3, 14)
log = logging.getLogger("archconv")


class ToolError(Exception):
    pass


class Codec(Enum):
    GZ = "gz"
    BZ2 = "bz2"
    XZ = "xz"
    ZST = "zst"
    BR = "br"
    LZ4 = "lz4"

    @property
    def suffix(self) -> str:
        return "." + self.value


class Container(Enum):
    RAW = "raw"
    TAR = "tar"
    ZIP = "zip"
    SEVENZ = "7z"


@dataclass(frozen=True, slots=True)
class Fmt:
    container: Container
    codec: Codec | None = None
    wheel: bool = False

    @property
    def key(self) -> tuple[Container, Codec | None]:
        return self.container, self.codec

    @property
    def suffix(self) -> str:
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
        return self.suffix.lstrip(".")


@dataclass(frozen=True, slots=True)
class Options:
    to: str
    remove: bool
    output_dir: Path | None
    force: bool
    level: int | None
    dry_run: bool
    verify: bool
    strict: bool
    tmpdir: Path | None


@dataclass(slots=True)
class Result:
    src: Path
    status: Literal["ok", "skipped", "dry-run", "failed"]
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
SEVENZ_MAGIC = b"7z\xbc\xaf\x27\x1c"


def require(module: str, pip: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ToolError(f"missing dependency: pip install {pip}") from exc


def zstd_backend() -> Any:
    if ZSTD_NATIVE:
        return importlib.import_module("compression.zstd")
    return require("zstandard", "zstandard")


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


class BrotliReader(io.RawIOBase):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._decoder = require("brotli", "brotli").Decompressor()
        self._raw: IO[bytes] = path.open("rb")
        self._pending = b""
        self._pos = 0
        self._eof = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while self._pos >= len(self._pending) and not self._eof:
            chunk = self._raw.read(CHUNK)
            if chunk:
                self._pending = self._decoder.process(chunk)
                self._pos = 0
            else:
                self._eof = True
                if not self._decoder.is_finished():
                    raise EOFError("truncated brotli stream")
        size = min(len(buffer), len(self._pending) - self._pos)
        buffer[:size] = self._pending[self._pos : self._pos + size]
        self._pos += size
        return size

    def close(self) -> None:
        if not self.closed:
            self._raw.close()
        super().close()


class BrotliWriter(io.RawIOBase):
    def __init__(self, path: Path, quality: int) -> None:
        super().__init__()
        self._encoder = require("brotli", "brotli").Compressor(quality=quality, lgwin=24)
        self._raw: IO[bytes] = path.open("wb")

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        payload = bytes(data)
        produced = self._encoder.process(payload)
        if produced:
            self._raw.write(produced)
        return len(payload)

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._raw.write(self._encoder.finish())
        finally:
            self._raw.close()
            super().close()


def open_reader(path: Path, codec: Codec | None) -> IO[bytes]:
    stream: Any
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
                stream = zstd.ZstdDecompressor().stream_reader(path.open("rb"), read_across_frames=True, closefd=True)
        case Codec.LZ4:
            stream = require("lz4.frame", "lz4").open(path, "rb")
        case Codec.BR:
            stream = io.BufferedReader(BrotliReader(path), buffer_size=CHUNK)
    return cast("IO[bytes]", stream)


def open_writer(path: Path, codec: Codec | None, level: int) -> IO[bytes]:
    stream: Any
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
                stream = zstd.ZstdCompressor(level=level, write_checksum=True).stream_writer(
                    path.open("wb"), closefd=True
                )
        case Codec.LZ4:
            stream = require("lz4.frame", "lz4").open(path, "wb", compression_level=level, content_checksum=True)
        case Codec.BR:
            stream = io.BufferedWriter(BrotliWriter(path, level), buffer_size=CHUNK)
    return cast("IO[bytes]", stream)


def read_full(stream: IO[bytes], size: int) -> bytes:
    parts: list[bytes] = []
    got = 0
    while got < size:
        chunk = stream.read(size - got)
        if not chunk:
            break
        parts.append(chunk)
        got += len(chunk)
    return b"".join(parts)


def peek(path: Path, codec: Codec, size: int = 512) -> bytes:
    with open_reader(path, codec) as stream:
        return read_full(stream, size)


def is_tar_block(block: bytes) -> bool:
    if len(block) < 512:
        return False
    try:
        info = tarfile.TarInfo.frombuf(block[:512], tarfile.ENCODING, "surrogateescape")
    except tarfile.TarError:
        return False
    return bool(info.name)


def codec_from_magic(head: bytes) -> Codec | None:
    for magic, codec in MAGIC:
        if head.startswith(magic):
            return codec
    if len(head) > 3 and head[:3] == b"BZh" and 0x31 <= head[3] <= 0x39:
        return Codec.BZ2
    if len(head) >= 4 and head[1:4] == b"\x2a\x4d\x18" and 0x50 <= head[0] <= 0x5F:
        return Codec.ZST
    return None


def fmt_from_name(name: str) -> tuple[Fmt, str] | None:
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
        raise ToolError(f"cannot read {codec.value} stream: {exc}") from exc
    return (Container.TAR if is_tar_block(inner) else Container.RAW), codec


def detect(path: Path, strict: bool) -> tuple[Fmt, str]:
    named = fmt_from_name(path.name)
    hint = named[0].codec if named else None
    sniffed = sniff(path, hint)
    if named is None:
        if sniffed is None:
            raise ToolError("unrecognized format (unknown extension, no matching magic bytes)")
        return Fmt(*sniffed), path.name
    fmt, stem = named
    if sniffed is None:
        raise ToolError(f"extension says {fmt.label} but magic bytes do not confirm it")
    if fmt.key == sniffed:
        return fmt, stem
    actual = Fmt(*sniffed)
    message = f"extension says {fmt.label} but content is {actual.label}"
    if strict:
        raise ToolError(message)
    log.warning("warn     %s: %s; trusting content", path.name, message)
    return actual, stem


def parse_target(token: str, src: Fmt) -> Fmt:
    value = token.strip().lower().lstrip(".")
    fixed = {
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
    raise ToolError(f"unsupported target format: {token!r}")


def level_spec(dst: Fmt) -> tuple[int, int, int] | None:
    if dst.codec is not None:
        return CODEC_LEVELS[dst.codec]
    if dst.container in (Container.ZIP, Container.SEVENZ):
        return 0, 9, 6
    return None


def resolve_level(spec: tuple[int, int, int] | None, requested: int | None) -> int:
    if spec is None:
        return 0
    low, high, default = spec
    if requested is None:
        return default
    if not low <= requested <= high:
        raise ToolError(f"level must be within {low}..{high} for this target")
    return requested


def check_name(name: str) -> PurePosixPath:
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute() or PureWindowsPath(name).drive or ".." in pure.parts:
        raise ToolError(f"unsafe path in archive: {name!r}")
    return pure


def safe_path(root: Path, pure: PurePosixPath) -> Path:
    target = root.joinpath(*pure.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.resolve().is_relative_to(root):
        raise ToolError(f"unsafe path in archive: {pure.as_posix()!r}")
    return target


def extract_zip(src: Path, root: Path) -> None:
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
                if (target.parent / link).resolve().is_relative_to(root):
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
    if not hasattr(tarfile, "data_filter"):
        raise ToolError("this Python lacks safe tar extraction filters; update Python")
    with open_reader(src, codec) as stream, tarfile.open(fileobj=stream, mode="r|") as archive:
        archive.extractall(root, filter="data")


def extract_7z(src: Path, root: Path) -> None:
    py7zr = require("py7zr", "py7zr")
    with py7zr.SevenZipFile(src, "r") as archive:
        for name in archive.getnames():
            check_name(name)
        archive.extractall(path=root)


def extract_raw(src: Path, codec: Codec, root: Path, stem: str) -> None:
    with open_reader(src, codec) as source, (root / (stem or "data")).open("wb") as sink:
        shutil.copyfileobj(source, sink, CHUNK)


def extract(src: Path, fmt: Fmt, stem: str, root: Path) -> None:
    match fmt.container:
        case Container.ZIP:
            extract_zip(src, root)
        case Container.SEVENZ:
            extract_7z(src, root)
        case Container.TAR:
            extract_tar(src, fmt.codec, root)
        case Container.RAW:
            extract_raw(src, cast(Codec, fmt.codec), root, stem)


def entries(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        base = Path(dirpath)
        found.extend(base / name for name in dirnames)
        found.extend(base / name for name in sorted(filenames))
    return found


def scrub(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def zip_time(stamp: float) -> tuple[int, int, int, int, int, int]:
    moment = time.localtime(max(stamp, 315532800))
    return moment.tm_year, moment.tm_mon, moment.tm_mday, moment.tm_hour, moment.tm_min, moment.tm_sec


def pack_tar(root: Path, items: list[Path], part: Path, codec: Codec | None, level: int) -> None:
    with (
        open_writer(part, codec, level) as stream,
        tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as archive,
    ):
        for item in items:
            archive.add(item, arcname=item.relative_to(root).as_posix(), recursive=False, filter=scrub)


def pack_zip(root: Path, items: list[Path], part: Path, level: int) -> None:
    method = zipfile.ZIP_DEFLATED if level > 0 else zipfile.ZIP_STORED
    with zipfile.ZipFile(
        part, "w", compression=method, compresslevel=level, allowZip64=True, strict_timestamps=False
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
    py7zr = require("py7zr", "py7zr")
    filters = [{"id": lzma.FILTER_LZMA2, "preset": level}]
    with py7zr.SevenZipFile(part, "w", filters=filters) as archive:
        for item in items:
            archive.write(item, arcname=item.relative_to(root).as_posix())


def pack(root: Path, items: list[Path], dst: Fmt, part: Path, level: int) -> None:
    match dst.container:
        case Container.TAR:
            pack_tar(root, items, part, dst.codec, level)
        case Container.ZIP:
            pack_zip(root, items, part, level)
        case Container.SEVENZ:
            pack_7z(root, items, part, level)
        case Container.RAW:
            raise ToolError("cannot pack multiple entries into a bare compressed file")


def recode(src: Path, src_codec: Codec | None, part: Path, dst_codec: Codec | None, level: int) -> str:
    digest = hashlib.sha256()
    with open_reader(src, src_codec) as reader, open_writer(part, dst_codec, level) as writer:
        while chunk := reader.read(CHUNK):
            digest.update(chunk)
            writer.write(chunk)
    return digest.hexdigest()


def staged(src: Path, fmt: Fmt, stem: str, dst: Fmt, part: Path, level: int, base: Path) -> None:
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".archconv-", dir=base) as workdir:
        root = Path(workdir).resolve()
        extract(src, fmt, stem, root)
        pack(root, entries(root), dst, part, level)


def verify_output(out: Path, dst: Fmt, digest: str | None) -> None:
    if digest is not None:
        check = hashlib.sha256()
        with open_reader(out, dst.codec) as reader:
            while chunk := reader.read(CHUNK):
                check.update(chunk)
        if check.hexdigest() != digest:
            raise ToolError("verification failed: decompressed content differs from source")
        return
    match dst.container:
        case Container.ZIP:
            with zipfile.ZipFile(out) as archive:
                bad = archive.testzip()
            if bad:
                raise ToolError(f"verification failed: corrupt entry {bad!r}")
        case Container.SEVENZ:
            with require("py7zr", "py7zr").SevenZipFile(out, "r") as archive:
                bad = archive.testzip()
            if bad:
                raise ToolError(f"verification failed: corrupt entry {bad!r}")
        case Container.TAR:
            with open_reader(out, dst.codec) as reader, tarfile.open(fileobj=reader, mode="r|") as archive:
                for member in archive:
                    if member.isreg():
                        handle = archive.extractfile(member)
                        if handle is not None:
                            while handle.read(CHUNK):
                                pass
        case Container.RAW:
            pass


def streamable(src: Fmt, dst: Fmt) -> bool:
    return src.container is dst.container and src.container in (Container.RAW, Container.TAR)


def convert_one(src: Path, opts: Options) -> Result:
    if not src.is_file():
        raise ToolError("not a regular file")
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
    try:
        return convert_one(src, opts)
    except Exception as exc:
        log.debug("traceback", exc_info=True)
        text = str(exc) if isinstance(exc, ToolError) else f"{type(exc).__name__}: {exc}"
        return Result(src, "failed", None, text)


def report(result: Result) -> Result:
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
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def print_formats() -> None:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="archconv",
        description="Convert archives and compressed files between formats with auto-detected input.",
    )
    parser.add_argument("inputs", nargs="*", type=Path, help="files to convert")
    parser.add_argument(
        "--to", metavar="FORMAT", help="target: zip whl 7z tar gz bz2 xz zst br lz4 tar.<codec> tgz txz ..."
    )
    parser.add_argument(
        "-r", "--remove", action="store_true", help="remove the original after a verified successful conversion"
    )
    parser.add_argument("-o", "--output-dir", type=Path, help="write outputs here instead of next to the input")
    parser.add_argument("-f", "--force", action="store_true", help="overwrite existing outputs")
    parser.add_argument("-l", "--level", type=int, help="compression level for the target format")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what would happen without writing")
    parser.add_argument("-j", "--jobs", type=int, default=1, help="parallel files (0 = all CPUs)")
    parser.add_argument("--verify", action="store_true", help="integrity-check the output (implied by -r)")
    parser.add_argument("--strict", action="store_true", help="fail when extension and magic bytes disagree")
    parser.add_argument("--tmpdir", type=Path, help="scratch directory for staged conversions")
    parser.add_argument("--list-formats", action="store_true", help="show supported formats and dependency status")
    noise = parser.add_mutually_exclusive_group()
    noise.add_argument("-v", "--verbose", action="store_true")
    noise.add_argument("-q", "--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_formats:
        print_formats()
        return 0
    if not args.inputs:
        parser.error("no input files given")
    if not args.to:
        parser.error("--to is required")
    logging.basicConfig(
        level=logging.ERROR if args.quiet else logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    opts = Options(
        to=args.to,
        remove=args.remove,
        output_dir=args.output_dir,
        force=args.force,
        level=args.level,
        dry_run=args.dry_run,
        verify=args.verify,
        strict=args.strict,
        tmpdir=args.tmpdir,
    )
    seen: set[Path] = set()
    sources: list[Path] = []
    for path in args.inputs:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            sources.append(path)

    def run(path: Path) -> Result:
        return report(convert(path, opts))

    jobs = max(1, min(args.jobs or os.cpu_count() or 1, len(sources)))
    if jobs == 1:
        results = [run(path) for path in sources]
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(run, sources))
    counts = {
        state: sum(1 for item in results if item.status == state) for state in ("ok", "skipped", "dry-run", "failed")
    }
    if len(results) > 1:
        log.info(
            "done: %d converted, %d skipped, %d planned, %d failed",
            counts["ok"],
            counts["skipped"],
            counts["dry-run"],
            counts["failed"],
        )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
