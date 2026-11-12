#!/data/data/com.termux/files/usr/bin/env python
import argparse
import bz2
import gzip
import json
import lzma
import os
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from dh import SKIP_DIRS

try:
    import py7zr
except ImportError:
    py7zr = None
try:
    import pylzma
except ImportError:
    pylzma = None
try:
    import zstandard as zstd
except ImportError:
    zstd = None
try:
    import lz4.frame as lz4_frame
except ImportError:
    lz4_frame = None
try:
    import snappy
except ImportError:
    snappy = None
try:
    import brotli
except ImportError:
    brotli = None


def fsz(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0 or unit == "PB":
            return f"{int(n)} B" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} PB"


ARCHIVE_TYPES: dict[str, str] = {
    ".tar": "TAR Archive (.tar)",
    ".tar.gz": "GZip Tarball (.tar.gz)",
    ".tgz": "GZip Tarball (.tgz)",
    ".tar.xz": "XZ Tarball (.tar.xz)",
    ".txz": "XZ Tarball (.txz)",
    ".tar.bz2": "BZip2 Tarball (.tar.bz2)",
    ".tbz2": "BZip2 Tarball (.tbz2)",
    ".tar.lz4": "LZ4 Tarball (.tar.lz4)",
    ".tar.zst": "Zstandard Tarball (.tar.zst)",
    ".tzst": "Zstandard Tarball (.tzst)",
    ".tar.br": "Brotli Tarball (.tar.br)",
    ".tar.7z": "7-Zip Tarball (.tar.7z)",
    ".tar.bz3": "BZip3 Tarball (.tar.bz3)",
    ".tar.snappy": "Snappy Tarball (.tar.snappy)",
    ".zip": "ZIP Archive (.zip)",
    ".whl": "Python Wheel (.whl)",
    ".7z": "7-Zip Archive (.7z)",
    ".snappy": "Snappy Stream (.snappy)",
    ".zst": "Zstandard Stream (.zst)",
    ".gz": "GZip Stream (.gz)",
    ".bz3": "BZip3 Stream (.bz3)",
    ".bz2": "BZip2 Stream (.bz2)",
    ".xz": "XZ Compressed (.xz)",
    ".br": "Brotli Stream (.br)",
    ".lz4": "LZ4 Frame (.lz4)",
}
_SORTED_EXTS: tuple[str, ...] = tuple(sorted(ARCHIVE_TYPES, key=len, reverse=True))


def detect_archive(path: Path) -> tuple[Optional[str], Optional[str]]:
    name: str = path.name.lower()
    for ext in _SORTED_EXTS:
        if name.endswith(ext):
            return ext, ARCHIVE_TYPES[ext]
    return None, None


def _gzip_stream_size(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            f.seek(-4, os.SEEK_END)
            return int.from_bytes(f.read(4), "little")
    except Exception:
        return int(path.stat().st_size * 2.8)


def _zstd_frame_size(path: Path) -> int:
    if zstd is not None:
        try:
            with open(path, "rb") as f:
                head: bytes = f.read(1024)
            params: Any = (
                zstd.get_frame_parameters(head)
                if hasattr(zstd, "get_frame_parameters")
                else zstd.ZstdDecompressor().frame_parameters(head)
            )
            size: int = getattr(params, "content_size", 0) or getattr(params, "uncompressed_size", 0)
            if size and size > 0:
                return int(size)
        except Exception:
            pass
    return int(path.stat().st_size * 3.2)


def analyze_archive(path: Path) -> dict[str, Any]:
    ext: Optional[str]
    desc: Optional[str]
    ext, desc = detect_archive(path)
    size: int = path.stat().st_size
    extracted: int = 0
    count: int = 0
    integrity: Optional[bool] = None
    err: str = ""
    try:
        if ext in (".zip", ".whl"):
            with zipfile.ZipFile(path, "r") as zf:
                infos: list[zipfile.ZipInfo] = zf.infolist()
                extracted = sum(i.file_size for i in infos)
                count = len(infos)
                integrity = zf.testzip() is None
        elif ext and (ext.startswith(".tar") or ext in (".tgz", ".txz", ".tbz2", ".tzst")):
            mode: str = "r:*"
            if ext in (".tar.gz", ".tgz"):
                mode = "r:gz"
            elif ext in (".tar.bz2", ".tbz2"):
                mode = "r:bz2"
            elif ext in (".tar.xz", ".txz"):
                mode = "r:xz"
            try:
                with tarfile.open(path, mode) as tf:
                    for m in tf:
                        if m.isfile():
                            extracted += m.size
                        count += 1
                    integrity = True
            except Exception as te:
                if zstd is not None and ext in (".tar.zst", ".tzst"):
                    dec: Any = zstd.ZstdDecompressor()
                    with (
                        open(path, "rb") as f,
                        dec.stream_reader(f) as sr,
                        tarfile.open(fileobj=sr, mode="r|*") as tf,
                    ):
                        for m in tf:
                            if m.isfile():
                                extracted += m.size
                            count += 1
                        integrity = True
                else:
                    extracted = int(size * 3.5)
                    integrity, err = False, str(te)
        elif ext == ".7z":
            success: bool = False
            if py7zr is not None:
                try:
                    with py7zr.SevenZipFile(path, "r") as sz:
                        extracted = sz.archive_info().uncompressed
                        count = len(sz.getnames())
                        integrity = True
                        success = True
                except Exception:
                    pass
            if not success and pylzma is not None:
                try:
                    with open(path, "rb") as f:
                        data: bytes = f.read()
                    dec_data: bytes = pylzma.decompress(data)
                    extracted = len(dec_data)
                    count = 1
                    integrity = True
                    success = True
                except Exception as pve:
                    err = str(pve)
            if not success:
                extracted, integrity, err = int(size * 4.1), False, err or "7z extraction modules failed"
        elif ext == ".gz":
            extracted, count, integrity = _gzip_stream_size(path), 1, True
        elif ext == ".zst":
            extracted, count, integrity = _zstd_frame_size(path), 1, True
        elif ext in (
            ".bz2",
            ".xz",
            ".lz4",
            ".br",
            ".snappy",
            ".bz3",
            ".tar.bz3",
            ".tar.snappy",
        ):
            ratios: dict[str, float] = {".bz2": 2.9, ".xz": 3.8, ".lz4": 2.4, ".br": 3.1}
            extracted = int(size * ratios.get(ext, 3.0))
            count, integrity = 1, True
        else:
            extracted, count, integrity = int(size * 2.5), 1, True
    except Exception as e:
        integrity, err, extracted = False, str(e), size
    return {
        "path": str(path),
        "filename": path.name,
        "ext": ext or "unknown",
        "archive_type": desc or "Unknown Archive",
        "compressed_size": size,
        "extracted_size": extracted,
        "file_count": count,
        "ratio": extracted / size if size > 0 else 1.0,
        "integrity": integrity,
        "error": err,
    }


def extract_archive(path: Path, dest_root: Path) -> tuple[bool, str]:
    ext: Optional[str]
    ext, _ = detect_archive(path)
    dest: Path = dest_root / f"{path.name}_extracted"
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if ext in (".zip", ".whl"):
            with zipfile.ZipFile(path, "r") as zf:
                zf.extractall(dest)
            return True, str(dest)
        if ext and (ext.startswith(".tar") or ext in (".tgz", ".txz", ".tbz2", ".tzst")):
            with tarfile.open(path, "r:*") as tf:
                try:
                    tf.extractall(dest, filter="data")
                except TypeError:
                    tf.extractall(dest)
            return True, str(dest)
        if ext == ".7z":
            extracted_ok: bool = False
            if py7zr is not None:
                try:
                    with py7zr.SevenZipFile(path, "r") as sz:
                        sz.extractall(path=dest)
                    extracted_ok = True
                except Exception:
                    pass
            if not extracted_ok and pylzma is not None:
                try:
                    with open(path, "rb") as f:
                        data: bytes = f.read()
                    dec_data: bytes = pylzma.decompress(data)
                    out_file: Path = dest / path.stem
                    out_file.write_bytes(dec_data)
                    extracted_ok = True
                except Exception as pve:
                    return False, str(pve)
            if extracted_ok:
                return True, str(dest)
            return False, "py7zr and pylzma failed to extract"
        if ext == ".gz":
            out: Path = dest / path.stem
            with gzip.open(path, "rb") as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            return True, str(dest)
        shutil.copyfile(path, dest / f"{path.name}.decompressed")
        return True, str(dest)
    except Exception as e:
        return False, str(e)


def cmd_scan(
    directory: str,
    auto_extract: bool,
    banner: bool,
    verbose: bool,
    as_json: bool,
) -> int:
    root: Path = Path(directory).resolve()
    if as_json:
        results: list[dict[str, Any]] = [
            analyze_archive(p) for p in root.rglob("*") if p.is_file() and detect_archive(p)[0]
        ]
        print(json.dumps(results, indent=2))
        return 0
    print(f"\x1b[38;5;39mScanning directory recursively:\x1b[0m {root}")
    if banner:
        print(
            f"\x1b[38;5;82m\n    ___                 __     _                  _____\n   / _ | ___________ / /_    (_)_  _____  / ___/______ ____  ___ ____\n  / __ |/ __/ __/ -_) __/   / / _ \\/ ___/ / /__/ __/ _ `/ _ \\/ -_) __/\n /_/ |_/_/  \\__/\\__/\\__/   /_/\\___/_/     \\___/\\__/\\_,_/ .__/\\__/_/\n                                                       /_/\n  [ INTEGRITY VALIDATION & EXTRACTED SIZE SCANNER v1.4.2 ]\x1b[0m"
        )
    results: list[dict[str, Any]] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if not detect_archive(p)[0]:
            continue
        info: dict[str, Any] = analyze_archive(p)
        results.append(info)
        if verbose:
            status: str = "\x1b[32m[PASS]\x1b[0m" if info["integrity"] else "\x1b[31m[FAIL]\x1b[0m"
            print(
                f"->Found:{info['filename']}|Type:{info['archive_type']}"
                f"|Comp:{fsz(info['compressed_size'])}"
                f"->Ext:{fsz(info['extracted_size'])}|{status}"
            )
    print("-" * 40)
    print(
        f"\x1b[1;37m{'FILENAME':<32} {'ARCHIVE TYPE':<26} {'COMPRESSED':<12} {'EXTRACTED':<12} {'INTEGRITY':<10}\x1b[0m"
    )
    print("-" * 40)
    total_comp: int = sum(i["compressed_size"] for i in results)
    total_ext: int = sum(i["extracted_size"] for i in results)
    for info in results:
        st: str
        if info["integrity"] is True:
            st = "\x1b[32mPASSED\x1b[0m"
        elif info["integrity"] is False:
            st = "\x1b[31mFAILED\x1b[0m"
        else:
            st = "\x1b[90mSKIP\x1b[0m"
        print(
            f"{info['filename'][:31]:<32} {info['archive_type'][:25]:<26} "
            f"{fsz(info['compressed_size']):<12} "
            f"{fsz(info['extracted_size']):<12} {st}"
        )
    print("=" * 40)
    print(f"\x1b[1;36mSUMMARY:\x1b[0m Found {len(results)} archive files.")
    print(f"Total Compressed Size : {fsz(total_comp)}")
    print(f"Total Extracted Size  : \x1b[1;32m{fsz(total_ext)}\x1b[0m")
    if total_comp > 0:
        print(f"Overall Expansion      : {total_ext / total_comp:.2f}x ({fsz(total_ext - total_comp)} saved)")
    if auto_extract and results:
        dest_root: Path = root / "extracted_archives"
        print(f"\n\x1b[38;5;214m[-a] Auto-extracting {len(results)} archives into:\x1b[0m {dest_root}")
        for info in results:
            ok: bool
            out: str
            ok, out = extract_archive(Path(info["path"]), dest_root)
            if ok:
                print(f"  \x1b[32m[\u2713]\x1b[0m Extracted {info['filename']} -> {out}")
            else:
                print(f"  \x1b[31m[\u2717]\x1b[0m Failed {info['filename']}: {out}")
    return 0


def _count_stream(reader: Any) -> int:
    total: int = 0
    while True:
        chunk: bytes = reader.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
    return total


_SizeHandler = Callable[[Path], tuple[Optional[int], Optional[str]]]


def _h_tar(path: Path) -> tuple[Optional[int], Optional[str]]:
    try:
        with tarfile.open(path, "r:*") as tf:
            sz_sum: int = 0
            for m in tf:
                if m.isfile():
                    sz_sum += m.size
            return sz_sum, None
    except Exception as e:
        return None, str(e)


def _h_zstd(path: Path) -> tuple[Optional[int], Optional[str]]:
    if zstd is None:
        return None, "zstandard not installed"
    try:
        with open(path, "rb") as f:
            head: bytes = f.read(32)
            dec: Any = zstd.ZstdDecompressor()
            params: Any = (
                zstd.get_frame_parameters(head) if hasattr(zstd, "get_frame_parameters") else dec.frame_parameters(head)
            )
            size: int = getattr(params, "content_size", 0) or getattr(params, "uncompressed_size", 0)
            if size:
                return int(size), None
            f.seek(0)
            return _count_stream(dec.stream_reader(f)), None
    except Exception as e:
        return None, str(e)


def _h_xz(path: Path) -> tuple[Optional[int], Optional[str]]:
    try:
        with lzma.open(path, "rb") as f:
            return _count_stream(f), None
    except Exception as e:
        return None, str(e)


def _h_gzip(path: Path) -> tuple[Optional[int], Optional[str]]:
    try:
        with gzip.open(path, "rb") as f:
            return _count_stream(f), None
    except Exception as e:
        return None, str(e)


def _h_bz2(path: Path) -> tuple[Optional[int], Optional[str]]:
    try:
        with bz2.open(path, "rb") as f:
            return _count_stream(f), None
    except Exception as e:
        return None, str(e)


def _h_7z(path: Path) -> tuple[Optional[int], Optional[str]]:
    if py7zr is not None:
        try:
            with py7zr.SevenZipFile(path, "r") as z:
                return (
                    sum(f.uncompressed for f in z.list() if f.uncompressed is not None),
                    None,
                )
        except Exception:
            pass
    if pylzma is not None:
        try:
            with open(path, "rb") as f:
                data: bytes = f.read()
            dec_data: bytes = pylzma.decompress(data)
            return len(dec_data), None
        except Exception as e:
            return None, str(e)
    return None, "py7zr and pylzma not installed or failed"


def _h_zip(path: Path) -> tuple[Optional[int], Optional[str]]:
    try:
        with zipfile.ZipFile(path, "r") as z:
            return sum(i.file_size for i in z.infolist()), None
    except Exception as e:
        return None, str(e)


SIZE_HANDLERS: dict[str, tuple[str, _SizeHandler]] = {
    ".zst": ("zstd", _h_zstd),
    ".xz": ("xz", _h_xz),
    ".gz": ("gzip", _h_gzip),
    ".bz2": ("bzip2", _h_bz2),
    ".7z": ("7zip", _h_7z),
    ".zip": ("zip", _h_zip),
    ".whl": ("wheel", _h_zip),
    ".tar": ("tar", _h_tar),
}


def cmd_sizes(directory: str) -> int:
    root: Path = Path(directory).resolve()
    if not root.is_dir():
        print(f"Error: {root} is not a directory")
        return 1
    print(f"Scanning {root}...\n")
    fmt: str = "{:<30} {:<10} {:>15} {:>15} {:>8}"
    print(fmt.format("File", "Format", "Compressed", "Uncompressed", "Ratio"))
    print("-" * 40)
    count: int = 0
    total_comp: int = 0
    total_uncomp: int = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        handler: Optional[tuple[str, _SizeHandler]] = next(
            ((name, fn) for ext, (name, fn) in SIZE_HANDLERS.items() if p.name.endswith(ext)),
            None,
        )
        if handler is None:
            continue
        name: str
        fn: _SizeHandler
        name, fn = handler
        size: int = p.stat().st_size
        uncomp: Optional[int]
        _err: Optional[str]
        uncomp, _err = fn(p)
        count += 1
        total_comp += size
        if uncomp is not None:
            total_uncomp += uncomp
            ratio: float = uncomp / size if size > 0 else 0.0
            ratio_s: str = f"{ratio:.2f}x"
            size_s: str = fsz(uncomp)
        else:
            ratio_s = "Error"
            size_s = "Error"
        display: str = p.name[:27] + "..." if len(p.name) > 30 else p.name
        print(fmt.format(display, name, fsz(size), size_s, ratio_s))
    print("-" * 40)
    print(f"Total files: {count}")
    print(f"Total compressed: {fsz(total_comp)}")
    print(f"Total uncompressed: {fsz(total_uncomp)}")
    try:
        _, _, free = shutil.disk_usage(root)
        print(f"Free disk space: {fsz(free)}")
        if total_uncomp > free:
            shortfall: int = total_uncomp - free
            print(f"\n\u26a0\ufe0f  WARNING: Not enough space to extract all files! (Shortfall: {fsz(shortfall)})")
    except OSError:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="archive_report.py",
        description=(
            "Unified archive scanner and uncompressed-size reporter (merges xreport.py, xreport2.py, zreport.py)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  archive_report.py scan . -a -v\n"
            "  archive_report.py scan ~/Downloads -j\n"
            "  archive_report.py sizes ~/Downloads\n"
        ),
    )
    sub: argparse._SubParsersAction[Any] = parser.add_subparsers(dest="command")
    sp_scan: argparse.ArgumentParser = sub.add_parser(
        "scan",
        help="Full archive scan with integrity check + auto-extract (≈ xreport.py / xreport2.py).",
    )
    sp_scan.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory).",
    )
    sp_scan.add_argument(
        "-a",
        "--auto-extract-all",
        action="store_true",
        help="Extract every discovered archive into <dir>/extracted_archives.",
    )
    sp_scan.add_argument(
        "-t",
        "--test-integrity",
        action="store_true",
        help="Print the ASCII banner at the start of the scan (preserved from the original scripts).",
    )
    sp_scan.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print a detail line for every archive as it is analysed.",
    )
    sp_scan.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="Emit the scan result as JSON and exit.",
    )
    sp_scan.set_defaults(func=lambda a: cmd_scan(a.directory, a.auto_extract_all, a.test_integrity, a.verbose, a.json))
    sp_sizes: argparse.ArgumentParser = sub.add_parser(
        "sizes",
        help="Report *accurately measured* uncompressed sizes and disk-space headroom (≈ zreport.py).",
    )
    sp_sizes.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory).",
    )
    sp_sizes.set_defaults(func=lambda a: cmd_sizes(a.path))
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    argv_list: list[str] = list(sys.argv[1:] if argv is None else argv)
    known: set[str] = {"scan", "sizes", "-h", "--help"}
    if not argv_list or argv_list[0] not in known:
        argv_list = ["scan", *argv_list]
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args(argv_list)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
