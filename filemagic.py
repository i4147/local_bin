#!/data/data/com.termux/files/usr/bin/env python
"""Small, dependency-free content classifier for Python 3.11+.

This is NOT a libmagic replacement or a security/file-validation boundary.
The corrupt historical signature database has been replaced with a curated one;
obscure legacy formats are not all retained. No filename-extension guessing,
archive extraction, decompression, or unbounded reads are performed.

Custom magic syntax (a deliberately limited subset of file(5)):
    [>...]offset type[&mask] [operator]value description
Types: string, byte, short, long, quad; le/be and optional u numeric prefixes.
Unqualified numbers are unsigned, native-endian, FIXED-width (1/2/4/8 bytes).
Operators: = != < > & ^; x is a numeric wildcard. & requires all bits;
^ requires not all bits. Strings support = and != only. Numeric literals support
0x, 0o, 0b, historical leading-zero octal, and backslash-prefixed octal.
Escapes: \\xHH, up to three octal digits, \\n, \\r, \\t, \\b, \\f, \\v,
\\\\ and escaped spaces/punctuation. Strings are literal Latin-1 bytes.
Continuation offsets are absolute. Indirect/relative offsets, directives,
search/regex types, string modifiers and libmagic formatting are unsupported
and rejected with a filename/line-number diagnostic. Messages are literal.
A matched root and all matched descendants contribute space-separated messages.
Use libmagic with a maintained database for comprehensive format coverage.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import shlex
import stat
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Literal
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)

__version__ = "1.0.0"
DEFAULT_READ_SIZE = 65536
MAX_READ_SIZE = 16 * 1024 * 1024


def parse_number(text: str) -> int:
    """Parse decimal, explicit-base, or historical octal integers."""
    text = text.strip()
    if not text:
        raise ValueError("empty number")
    sign = -1 if text.startswith("-") else 1
    body = text.lstrip("+-")
    if not body:
        raise ValueError("missing digits")
    if body.startswith("\\"):
        return sign * int(body[1:], 8)
    if body.lower().startswith("x"):
        return sign * int(body[1:], 16)
    if body.lower().startswith(("0x", "0o", "0b")):
        return sign * int(body, 0)
    return sign * int(body, 8 if len(body) > 1 and body.startswith("0") else 10)


def unescape(text: str) -> bytes:
    """Decode magic strings once, without repeatedly interpreting backslashes."""
    out = bytearray()
    escapes = {"n": 10, "r": 13, "t": 9, "b": 8, "f": 12, "v": 11}
    i = 0
    while i < len(text):
        char = text[i]
        i += 1
        if char != "\\":
            value = ord(char)
        else:
            if i == len(text):
                raise ValueError("trailing backslash")
            char = text[i]
            i += 1
            if char == "x":
                digits = text[i : i + 2]
                if len(digits) != 2 or not re.fullmatch(r"[0-9a-fA-F]{2}", digits):
                    raise ValueError("expected two hex digits after \\x")
                value = int(digits, 16)
                i += 2
            elif char in "01234567":
                digits = char
                while i < len(text) and len(digits) < 3 and text[i] in "01234567":
                    digits += text[i]
                    i += 1
                value = int(digits, 8)
            else:
                value = escapes.get(char, ord(char))
        if value > 255:
            raise ValueError("magic strings must contain byte-sized characters")
        out.append(value)
    return bytes(out)


@dataclass(frozen=True, slots=True)
class Result:
    mime: str
    description: str
    source: str = "signature"


@dataclass(frozen=True, slots=True)
class Signature:
    """Every (offset, byte string) part must match."""

    parts: tuple[tuple[int, bytes], ...]
    result: Result

    def matches(self, data: bytes) -> bool:
        return all(data.startswith(value, offset) for offset, value in self.parts)


def _signatures() -> tuple[Signature, ...]:
    rules: list[Signature] = []

    def add(mime: str, description: str, *parts: tuple[int, bytes]) -> None:
        rules.append(Signature(parts, Result(mime, description)))

    # Specific containers precede generic ones. RIFF alone does NOT mean WAV.
    for subtype, mime, description in (
        (b"WAVE", "audio/wav", "WAVE audio"),
        (b"AVI ", "video/x-msvideo", "AVI video"),
        (b"WEBP", "image/webp", "WebP image"),
    ):
        add(mime, description, (0, b"RIFF"), (8, subtype))
    for subtype in (b"AIFF", b"AIFC"):
        add("audio/aiff", "AIFF audio", (0, b"FORM"), (8, subtype))
    add("application/vnd.debian.binary-package", "Debian package", (0, b"!<arch>\ndebian-binary"))
    for prefix, mime, description in (
        (b"\x89PNG\r\n\x1a\n", "image/png", "PNG image"),
        (b"GIF87a", "image/gif", "GIF image"),
        (b"GIF89a", "image/gif", "GIF image"),
        (b"\xff\xd8\xff", "image/jpeg", "JPEG image"),
        (b"II*\x00", "image/tiff", "TIFF image"),
        (b"MM\x00*", "image/tiff", "TIFF image"),
        (b"II+\x00\x08\x00\x00\x00", "image/tiff", "BigTIFF image"),
        (b"MM\x00+\x00\x08\x00\x00", "image/tiff", "BigTIFF image"),
        (b"BM", "image/bmp", "BMP image"),
        (b"\x00\x00\x01\x00", "image/vnd.microsoft.icon", "Windows icon"),
        (b"%PDF-", "application/pdf", "PDF document"),
        (b"{\\rtf", "application/rtf", "RTF document"),
        (b"%!", "application/postscript", "PostScript document"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage", "OLE compound document"),
        (b"PK\x03\x04", "application/zip", "ZIP archive"),
        (b"PK\x05\x06", "application/zip", "Empty ZIP archive"),
        (b"PK\x07\x08", "application/zip", "Spanned ZIP archive"),
        (b"\x1f\x8b\x08", "application/gzip", "gzip compressed data"),
        (b"BZh", "application/x-bzip2", "bzip2 compressed data"),
        (b"\xfd7zXZ\x00", "application/x-xz", "XZ compressed data"),
        (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed", "7-Zip archive"),
        (b"Rar!\x1a\x07\x00", "application/vnd.rar", "RAR archive"),
        (b"Rar!\x1a\x07\x01\x00", "application/vnd.rar", "RAR 5 archive"),
        (b"\x28\xb5\x2f\xfd", "application/zstd", "Zstandard compressed data"),
        (b"\x04\x22\x4d\x18", "application/x-lz4", "LZ4 frame"),
        (b"!<arch>\n", "application/x-archive", "ar archive"),
        (b"070701", "application/x-cpio", "cpio archive"),
        (b"070702", "application/x-cpio", "cpio archive"),
        (b"070707", "application/x-cpio", "cpio archive"),
        (b"\x7fELF", "application/x-elf", "ELF binary"),
        (b"\x00asm\x01\x00\x00\x00", "application/wasm", "WebAssembly module"),
        (b"SQLite format 3\x00", "application/vnd.sqlite3", "SQLite 3 database"),
        (b"fLaC", "audio/flac", "FLAC audio"),
        (b"OggS\x00", "application/ogg", "Ogg container"),
        (b"MThd\x00\x00\x00\x06", "audio/midi", "MIDI audio"),
        (b".snd", "audio/basic", "Sun/NeXT audio"),
        (b"\x1aE\xdf\xa3", "application/x-ebml", "EBML container (e.g. Matroska/WebM)"),
        (b"\xac\xed\x00\x05", "application/x-java-serialized-object", "Java serialized object"),
        (b"wOFF", "font/woff", "WOFF font"),
        (b"wOF2", "font/woff2", "WOFF2 font"),
        (b"OTTO", "font/otf", "OpenType font"),
        (b"\x00\x01\x00\x00", "font/ttf", "TrueType font"),
        (b"TZif", "application/tzif", "Time zone data"),
        (b"gimp xcf ", "image/x-xcf", "GIMP image"),
        (b"regf", "application/x-windows-registry", "Windows registry hive"),
        (b"\xa1\xb2\xc3\xd4", "application/vnd.tcpdump.pcap", "pcap capture (big endian)"),
        (b"\xd4\xc3\xb2\xa1", "application/vnd.tcpdump.pcap", "pcap capture (little endian)"),
        (b"\xa1\xb2\x3c\x4d", "application/vnd.tcpdump.pcap", "pcap capture (nanoseconds, big endian)"),
        (b"\x4d\x3c\xb2\xa1", "application/vnd.tcpdump.pcap", "pcap capture (nanoseconds, little endian)"),
        (b"\x0a\x0d\x0d\x0a", "application/x-pcapng", "pcapng capture"),
    ):
        add(mime, description, (0, prefix))
    for prefix in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"):
        add("application/x-mach-binary", "Mach-O binary", (0, prefix))
    add("application/x-tar", "POSIX tar archive", (257, b"ustar\x00"))
    add("application/x-tar", "GNU tar archive", (257, b"ustar  \x00"))
    return tuple(rules)


SIGNATURES = _signatures()
_NUMERIC = re.compile(r"(?P<unsigned>u?)(?P<endian>le|be)?(?P<kind>byte|short|long|quad)\Z")
_SIZES = {"byte": 1, "short": 2, "long": 4, "quad": 8}


@dataclass(slots=True)
class MagicRule:
    offset: int
    kind: str
    operator: str
    value: bytes | int | None
    message: str
    mask: int | None = None
    children: list[MagicRule] = field(default_factory=list)
    size: int = field(init=False)
    byteorder: Literal["little", "big"] = field(init=False, default=sys.byteorder)

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("negative offsets are unsupported")
        if self.kind == "string":
            if not isinstance(self.value, bytes) or not self.value:
                raise ValueError("string rules require a nonempty byte string")
            if self.operator not in {"=", "!="} or self.mask is not None:
                raise ValueError("strings support only = and !=, without masks")
            self.size = len(self.value)
            return
        match = _NUMERIC.fullmatch(self.kind)
        if not match:
            raise ValueError(f"unsupported type: {self.kind!r}")
        self.size = _SIZES[match["kind"]]
        endian_map: dict[str | None, Literal["little", "big"]] = {
            "le": "little",
            "be": "big",
            None: sys.byteorder,
        }
        self.byteorder = endian_map[match["endian"]]
        limit = (1 << (8 * self.size)) - 1
        if self.value is not None:
            if not isinstance(self.value, int) or not 0 <= self.value <= limit:
                raise ValueError(f"value outside unsigned {8 * self.size}-bit range")
        if self.mask is not None and not 0 <= self.mask <= limit:
            raise ValueError("mask outside numeric type range")
        if self.operator not in {"=", "!=", "<", ">", "&", "^", "x"}:
            raise ValueError(f"unsupported operator: {self.operator!r}")
        if (self.operator == "x") != (self.value is None):
            raise ValueError("only wildcard rules may omit a numeric value")

    @property
    def required_bytes(self) -> int:
        return max(self.offset + self.size, max((child.required_bytes for child in self.children), default=0))

    def evaluate(self, data: bytes) -> list[str] | None:
        if self.offset + self.size > len(data):
            return None
        if self.kind == "string":
            assert isinstance(self.value, bytes)
            equal = data.startswith(self.value, self.offset)
            matched = equal if self.operator == "=" else not equal
        else:
            actual = int.from_bytes(data[self.offset : self.offset + self.size], self.byteorder)
            if self.mask is not None:  # A zero mask is meaningful.
                actual &= self.mask
            if self.operator == "x":
                matched = True
            else:
                assert isinstance(self.value, int)
                value = self.value
                match self.operator:
                    case "=":
                        matched = actual == value
                    case "!=":
                        matched = actual != value
                    case "<":
                        matched = actual < value
                    case ">":
                        matched = actual > value
                    case "&":
                        matched = actual & value == value
                    case "^":
                        matched = actual & value != value
                    case _:
                        raise AssertionError("unvalidated operator")
        if not matched:
            return None
        messages = [self.message] if self.message else []
        for child in self.children:
            child_messages = child.evaluate(data)
            if child_messages is not None:
                messages.extend(child_messages)
        return messages


# Tokens keep escaped whitespace intact; do not use split(r'\s*', ...).
_RULE_LINE = re.compile(r"^\s*(\S+)\s+(\S+)\s+((?:\\.|[^\s\\])+)\s*(.*)$")


def load_rules(path: str | os.PathLike[str]) -> tuple[MagicRule, ...]:
    """Load custom rules transactionally; reject unsupported syntax explicitly."""
    roots: list[MagicRule] = []
    stack: list[MagicRule] = []
    with open(path, encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                match = _RULE_LINE.fullmatch(line.rstrip("\r\n"))
                if not match:
                    raise ValueError("expected offset, type, value, and optional message")
                offset, kind, token, message = match.groups()
                level = len(offset) - len(offset.lstrip(">"))
                if level > 32:
                    raise ValueError("continuation depth exceeds 32")
                if level > len(stack):
                    raise ValueError("continuation has no parent")
                offset = offset[level:]
                if not offset or offset[0] in "(&":
                    raise ValueError("indirect/relative offsets are unsupported")
                kind, separator, mask_text = kind.partition("&")
                mask = parse_number(mask_text) if separator else None
                operator = "="
                if token == "x" and kind != "string":
                    operator, value = "x", None
                else:
                    op = re.match(r"!=|[=<> &^!]", token)
                    if op:
                        operator = "!=" if op[0] == "!" else op[0]
                        token = token[len(op[0]) :]
                    value = unescape(token) if kind == "string" else parse_number(token)
                rule = MagicRule(parse_number(offset), kind, operator, value, message, mask)
                (roots if level == 0 else stack[level - 1].children).append(rule)
                stack[level:] = [rule]
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: {exc}") from exc
    return tuple(roots)


_SCRIPT_TYPES = {
    "python": ("text/x-python", "Python script"),
    "perl": ("text/x-perl", "Perl script"),
    "ruby": ("text/x-ruby", "Ruby script"),
    "node": ("text/javascript", "JavaScript script"),
    "nodejs": ("text/javascript", "JavaScript script"),
    "sh": ("application/x-sh", "Shell script"),
    "bash": ("application/x-sh", "Bash script"),
    "dash": ("application/x-sh", "Dash script"),
    "ash": ("application/x-sh", "Ash script"),
    "ksh": ("application/x-sh", "Korn shell script"),
    "zsh": ("application/x-sh", "Z shell script"),
    "csh": ("application/x-csh", "C shell script"),
    "tcsh": ("application/x-csh", "C shell script"),
    "awk": ("text/x-awk", "AWK script"),
    "gawk": ("text/x-awk", "AWK script"),
    "nawk": ("text/x-awk", "AWK script"),
}


def _text_result(data: bytes) -> Result | None:
    # Incremental decoders tolerate a bounded sample ending within a character.
    encoding = "utf-8-sig"
    for bom, name in (
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(bom):
            encoding = name
            break
    try:
        text = codecs.getincrementaldecoder(encoding)(errors="strict").decode(data, final=False)
    except UnicodeDecodeError:
        return None
    if not text or any((ord(c) < 32 and c not in "\t\n\r\f\b") or 0x7F <= ord(c) <= 0x9F for c in text):
        return None
    if text.startswith("#!"):
        try:
            words = shlex.split(text.splitlines()[0][2:])
        except ValueError:
            words = []
        if words:
            interpreter = words[0].rsplit("/", 1)[-1]
            if interpreter == "env":
                # Recognize common env forms; never execute the command.
                args = words[1:]
                while args:
                    arg = args.pop(0)
                    if arg in {"-u", "--unset", "-C", "--chdir"}:
                        if args:
                            args.pop(0)
                    elif arg == "--":
                        if args:
                            interpreter = args[0].rsplit("/", 1)[-1]
                        break
                    elif arg.startswith("-") or "=" in arg:
                        continue
                    else:
                        interpreter = arg.rsplit("/", 1)[-1]
                        break
            interpreter = re.sub(r"\d+(?:\.\d+)*$", "", interpreter)
            if interpreter in _SCRIPT_TYPES:
                return Result(*_SCRIPT_TYPES[interpreter], source="heuristic")
        return Result("text/x-script", "Script text", "heuristic")
    start = text.lstrip()
    if re.match(r"(?is)(?:<!doctype\s+html\b|<html\b|<head\b|<title\b)", start):
        return Result("text/html", "HTML document", "heuristic")
    if start.startswith("<?xml"):
        return Result("application/xml", "XML document", "heuristic")
    label = "ASCII" if data.isascii() else encoding.upper()
    return Result("text/plain", f"{label} text", "heuristic")


class Detector:
    def __init__(
        self, rules: Iterable[MagicRule] = (), *, read_size: int = DEFAULT_READ_SIZE, text: bool = True
    ) -> None:
        if not 1 <= read_size <= MAX_READ_SIZE:
            raise ValueError(f"read_size must be between 1 and {MAX_READ_SIZE}")
        self.rules = tuple(rules)
        required = max((rule.required_bytes for rule in self.rules), default=0)
        if required > read_size:
            raise ValueError(f"custom rules require {required} bytes; increase --bytes")
        self.read_size = read_size
        self.text = text

    def identify(self, data: bytes | bytearray | memoryview) -> Result:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("identify() requires bytes, bytearray, or memoryview")
        data = bytes(data[: self.read_size])
        if not data:
            return Result("application/x-empty", "Empty file")
        for rule in self.rules:
            messages = rule.evaluate(data)
            if messages:
                return Result("application/octet-stream", " ".join(messages), "custom")
        # e_lfanew is a variable offset, NOT a fixed 128-byte PE location.
        if data.startswith(b"MZ"):
            if len(data) >= 64:
                offset = int.from_bytes(data[60:64], "little")
                if offset >= 64 and data.startswith(b"PE\x00\x00", offset):
                    return Result("application/vnd.microsoft.portable-executable", "Windows PE executable")
            return Result("application/x-dosexec", "DOS MZ executable (possibly PE beyond sample)")
        # CAFEBABE is shared by Java classes and Mach-O universal binaries.
        if data.startswith(b"\xca\xfe\xba\xbe"):
            if len(data) >= 8 and int.from_bytes(data[6:8], "big") >= 45:
                return Result("application/java-vm", "Probable Java class", "heuristic")
            return Result("application/octet-stream", "Ambiguous Java/Mach-O fat header")
        if data.startswith(b"\xbe\xba\xfe\xca"):
            return Result("application/x-mach-binary", "Mach-O universal binary (little endian)")
        for signature in SIGNATURES:
            if signature.matches(data):
                return signature.result
        if len(data) >= 12 and data[4:8] == b"ftyp":
            brand = data[8:12]
            if brand in {b"avif", b"avis"}:
                return Result("image/avif", "AVIF image")
            if brand in {b"heic", b"heix", b"hevc", b"hevx"}:
                return Result("image/heic", "HEIC image")
            if brand in {b"mif1", b"msf1"}:
                return Result("image/heif", "HEIF image")
            if brand == b"qt  ":
                return Result("video/quicktime", "QuickTime container")
            if brand in {b"M4A ", b"M4B ", b"M4P "}:
                return Result("audio/mp4", "MPEG-4 audio container")
            return Result("application/octet-stream", "ISO Base Media container (ftyp)")
        if self.text and (result := _text_result(data)) is not None:
            return result
        return Result("application/octet-stream", "Binary data", "fallback")

    def identify_file(self, path: str | os.PathLike[str], *, follow_symlinks: bool = True) -> Result:
        """Classify regular files; do not intentionally read devices or FIFOs.

        Path checks are not a race-free security boundary. Use an independently
        secured descriptor in adversarial directories, then call identify().
        """
        info = os.stat(path, follow_symlinks=follow_symlinks)
        if stat.S_ISLNK(info.st_mode):
            return Result("inode/symlink", "Symbolic link", "filesystem")
        if stat.S_ISDIR(info.st_mode):
            return Result("inode/directory", "Directory", "filesystem")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("refusing to read a non-regular file")
        # Nonblocking open also prevents a replaced FIFO from hanging this call.
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        if not follow_symlinks:
            flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("refusing to read a non-regular file")
            return self.identify(stream.read(self.read_size))


_DEFAULT = Detector()


def whatis(data: bytes | bytearray | memoryview) -> str:
    """Compatibility wrapper; returns a description, not sometimes a MIME type."""
    return _DEFAULT.identify(data).description


def file(path: str | os.PathLike[str]) -> str:
    """Compatibility wrapper for the historical file() function."""
    return _DEFAULT.identify_file(path).description


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("paths", nargs="*", help="files/directories; '-' reads standard input")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-m",
        "--magic",
        action="append",
        default=[],
        metavar="FILE",
        help="load custom rules before built-ins (repeatable)",
    )
    parser.add_argument("-b", "--brief", action="store_true", help="omit filename labels")
    parser.add_argument("--mime", action="store_true", help="print MIME type instead of description")
    parser.add_argument("--json", action="store_true", help="emit one JSON object per path (JSON Lines)")
    parser.add_argument(
        "--bytes",
        type=int,
        default=DEFAULT_READ_SIZE,
        metavar="N",
        help=f"maximum sample size, 1..{MAX_READ_SIZE} (default: %(default)s)",
    )
    parser.add_argument("--no-text", action="store_true", help="disable text/script heuristics")
    parser.add_argument("--no-follow-symlinks", action="store_true", help="report links without opening targets")
    args = parser.parse_args(argv)
    if not args.paths:
        parser.error("at least one path is required (use '-' for standard input)")
    if args.paths.count("-") > 1:
        parser.error("standard input may only be specified once")
    try:
        rules = tuple(rule for path in args.magic for rule in load_rules(path))
        detector = Detector(rules, read_size=args.bytes, text=not args.no_text)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    status = 0
    for path in args.paths:
        try:
            result = (
                detector.identify(sys.stdin.buffer.read(detector.read_size))
                if path == "-"
                else detector.identify_file(path, follow_symlinks=not args.no_follow_symlinks)
            )
        except (OSError, ValueError) as exc:
            status = 1
            if args.json:
                print(json.dumps({"path": path, "error": str(exc)}, ensure_ascii=True))
            else:
                print(f"{path!r}: {exc}", file=sys.stderr)
            continue
        if args.json:
            print(json.dumps({"path": path, **asdict(result)}, ensure_ascii=True))
        else:
            message = result.mime if args.mime else result.description
            # repr prevents terminal control characters in filenames being emitted.
            print(message if args.brief else f"{path!r}: {message}")
    return status


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        os._exit(0)
    except KeyboardInterrupt:
        raise SystemExit(130)
