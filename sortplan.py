#!/usr/bin/env python3
"""sortplan: plan, apply and undo rule-based file organization (Python 3.12+).

The standard-library-only core never deletes duplicate content: duplicates are
moved into a journaled quarantine. Optional: python-magic, pypdf and Pillow.
See ``python sortplan.py --help`` and ``sortplan-demo.py`` for runnable usage.

Configuration: TOML ``[general]`` accepts root, min_age_days (default 1), and
on_conflict (rename, skip, conflicts_dir). Each ``[[rule]]`` has name, dest and
match. The match table supports glob, ext, filename (regex), mime, min_size,
max_size and text (PDF first-page regex); all conditions are ANDed. Rules are
first-match-wins, in file order. Glob and ext accept strings or lists. Regex
named groups and {year}, {month}, {day}, {ext} are destination placeholders.
Date comes from the filename/captured year, then supported metadata, then mtime;
partial dates may borrow fields from lower-priority sources, as labeled in plan.
Metadata readers use killable processes; a timeout can return no metadata. The
budget does not bound disk I/O, hashing, libmagic, process startup or cleanup.
Filename/content metadata parsing is best-effort, not a security sandbox.

Plans hash all eligible bytes (including files later left unsorted), so neither a
fixed five-second limit nor fast hashing of tens of gigabytes can be guaranteed.
Duplicate copies go into state/quarantine and use disk space until independently
removed; deleting them destroys the ability to undo. Default state is
~/.sortplan; use the same --state-dir for undo. Planning writes state only.

Apply requires --yes, exclusively creates a journal, and fsyncs each intent
before a move. Same-filesystem hardlink+unlink and cross-device exclusive copy
avoid the overwrite race in os.rename/shutil.move; source and output are hashed
before source removal. Undo refuses changed content and never overwrites. If
both paths exist after an interrupted copy/link, manual review is required. An
interruption after creating a directory but before logging its creation may
leave an empty directory. Hardlink+unlink temporarily adds a link. These steps
are not transactions against other programs modifying the same files; run on a
quiescent tree. Network/unusual filesystems and power-loss durability vary.
Undo restores file bytes, paths and mtime, not original inode, atime, owner,
ACLs, xattrs, hard-link relationships or every directory timestamp. Symlink
opt-in moves the link itself; relative links may not resolve at their new path.
The journal and plan contain full paths and hashes; protect them from tampering.
Case-insensitive collisions are conservatively checked at planning and apply,
but case-fold races cannot be prevented on case-sensitive filesystems.

CLI examples (state defaults to ~/.sortplan):
    python sortplan.py plan ~/Downloads --config sort.toml
    python sortplan.py apply --plan ~/.sortplan/plans/PLAN_ID.json --yes
    python sortplan.py undo --last
    python sortplan.py dedupe ~/Downloads --by content --keep oldest
    python sortplan.py explain ~/Downloads/example.pdf --config sort.toml
"""

import argparse
import contextlib
import datetime as dt
import errno
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
import tomllib
import uuid
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Callable, Iterator, Mapping, Sequence

VERSION = 1
DEFAULT_STATE = Path.home() / ".sortplan"
CHUNK = 1024 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
DATE_RE = re.compile(
    r"(?<!\d)(?P<year>19\d{2}|20\d{2})[-_. ](?P<month>0?[1-9]|1[0-2])[-_. ](?P<day>0?[1-9]|[12]\d|3[01])(?!\d)"
)


def absolute(path: str | Path) -> Path:
    """Expand user home and make an absolute path without resolving symlinks."""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def safe_id(value: str) -> str:
    """Reject traversal and malformed plan IDs."""
    if not ID_RE.fullmatch(value):
        raise ValueError("invalid plan id")
    return value


def utc_now() -> str:
    """Return a UTC timestamp suitable for an audit record."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(path: Path) -> str:
    """SHA-256 of regular file bytes, or of a symlink's stored target string."""
    h = hashlib.sha256()
    if path.is_symlink():
        h.update(os.fsencode(os.readlink(path)))
    else:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(CHUNK), b""):
                h.update(block)
    return h.hexdigest()


def fingerprint(path: Path) -> dict[str, Any]:
    """Capture and verify the identity of a file without following symlinks."""
    before = path.lstat()
    kind = "symlink" if stat.S_ISLNK(before.st_mode) else "file"
    if kind == "file" and not stat.S_ISREG(before.st_mode):
        raise ValueError(f"not a regular file: {path}")
    sha = digest(path)
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RuntimeError(f"file changed while hashing: {path}")
    return {"sha256": sha, "size": after.st_size, "mtime_ns": after.st_mtime_ns, "kind": kind}


def same_content(path: Path, item: Mapping[str, Any], *, mtime: bool = False) -> bool:
    """Check kind, length and hash (and optionally mtime), returning false on IO errors."""
    try:
        info = fingerprint(path)
        return all(info[k] == item[k] for k in ("kind", "size", "sha256")) and (
            not mtime or info["mtime_ns"] == item["mtime_ns"]
        )
    except (OSError, ValueError, RuntimeError):
        return False


def size_value(value: str | int | float) -> int:
    """Parse non-negative sizes such as 20MB, 12 MiB and integer bytes."""
    if isinstance(value, bool):
        raise ValueError("boolean is not a size")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError("negative size")
        return int(value)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|KIB|MIB|GIB|TIB)?\s*", value, re.I)
    if not m:
        raise ValueError(f"invalid size: {value!r}")
    unit = (m.group(2) or "B").upper()
    multipliers = {
        "B": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KIB": 1024,
        "MIB": 1024**2,
        "GIB": 1024**3,
        "TIB": 1024**4,
    }
    return int(float(m.group(1)) * multipliers[unit])


def load_config(path: str | Path | None) -> dict[str, Any]:
    """Load and validate the supported TOML settings and ordered rules."""
    config: dict[str, Any] = {"general": {}, "rule": []}
    if path:
        with open(path, "rb") as f:
            config = tomllib.load(f)
    general = config.setdefault("general", {})
    rules = config.setdefault("rule", [])
    if not isinstance(general, dict) or not isinstance(rules, list):
        raise ValueError("invalid configuration")
    if general.get("on_conflict", "rename") not in ("rename", "skip", "conflicts_dir"):
        raise ValueError("on_conflict must be rename, skip or conflicts_dir")
    if float(general.get("min_age_days", 1)) < 0:
        raise ValueError("min_age_days must be nonnegative")
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("name"), str)
            or not isinstance(rule.get("dest"), str)
            or not isinstance(rule.get("match"), dict)
        ):
            raise ValueError("every rule requires name, dest and a match table")
        match = rule["match"]
        if not match or set(match) - {"glob", "ext", "filename", "mime", "min_size", "max_size", "text"}:
            raise ValueError(f"unsupported or empty match in {rule['name']!r}")
        for key in ("filename", "text"):
            if key in match:
                re.compile(match[key])
        for key in ("min_size", "max_size"):
            if key in match:
                size_value(match[key])
    return config


def sniff(path: Path) -> str:
    """Return a best-effort MIME type from magic bytes; optional libmagic fallback."""
    with path.open("rb") as f:
        data = f.read(4096)
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x7fELF"):
        return "application/x-executable"
    if data.startswith(b"MZ"):
        return "application/x-dosexec"
    if data.startswith(b"PK\x03\x04"):
        return "application/zip"
    if len(data) > 262 and data[257:262] == b"ustar":
        return "application/x-tar"
    if data.startswith((b"BZh", b"\x1f\x8b", b"\xfd7zXZ\x00")):
        # Inspect only 4 KiB of compressed input and at most 512 decompressed bytes.
        try:
            if data.startswith(b"BZh"):
                import bz2

                head = bz2.BZ2Decompressor().decompress(data, max_length=512)
                fallback = "application/x-bzip2"
            elif data.startswith(b"\x1f\x8b"):
                import zlib

                head = zlib.decompressobj(31).decompress(data, 512)
                fallback = "application/gzip"
            else:
                import lzma

                head = lzma.LZMADecompressor(memlimit=16 * 1024 * 1024).decompress(data, max_length=512)
                fallback = "application/x-xz"
            if len(head) > 262 and head[257:262] == b"ustar":
                return "application/x-tar"
            return fallback
        except Exception:
            if data.startswith(b"BZh"):
                return "application/x-bzip2"
            if data.startswith(b"\x1f\x8b"):
                return "application/gzip"
            return "application/x-xz"
    if data.startswith(b"ID3"):
        return "audio/mpeg"
    try:
        import magic  # type: ignore[import-not-found]

        value = magic.from_buffer(data, mime=True)
        if value and value != "application/octet-stream":
            return str(value)
    except Exception:
        pass
    return "application/octet-stream"


def _metadata_worker(path: str, conn: Any) -> None:
    """Extract a bounded amount of optional metadata in an isolated process."""
    result: dict[str, str] = {}
    p = Path(path)
    try:
        ext = p.suffix.lower()
        if ext == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore[import-not-found]

                reader = PdfReader(str(p))
                if reader.metadata and reader.metadata.get("/CreationDate"):
                    result["date"] = str(reader.metadata["/CreationDate"])
                if reader.pages:
                    result["text"] = (reader.pages[0].extract_text() or "")[:100_000]
            except Exception:
                pass
        elif ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"):
            try:
                from PIL import Image  # type: ignore[import-not-found]

                with Image.open(p) as im:
                    exif = im.getexif()
                    raw = exif.get(36867)
                    if not raw and hasattr(exif, "get_ifd"):
                        raw = exif.get_ifd(34665).get(36867)
                    if raw:
                        result["date"] = str(raw)
            except Exception:
                pass
        elif ext in (".docx", ".xlsx", ".pptx"):
            try:
                with zipfile.ZipFile(p) as z:
                    info = z.getinfo("docProps/core.xml")
                    if info.file_size <= 1_000_000:
                        root = ET.fromstring(z.read(info))
                        for el in root.iter():
                            if el.tag.endswith("}created") and el.text:
                                result["date"] = el.text
                                break
            except Exception:
                pass
        elif ext == ".mp3":
            # Minimal ID3v2.3/v2.4 text-date reader, bounded to 256 KiB.
            with p.open("rb") as f:
                data = f.read(262144)
            if data[:3] == b"ID3" and len(data) >= 10 and data[3] in (3, 4):
                pos = 10
                while pos + 10 <= len(data):
                    key, rawlen = data[pos : pos + 4], data[pos + 4 : pos + 8]
                    n = (
                        int.from_bytes(rawlen, "big")
                        if data[3] == 3
                        else sum((b & 127) << (7 * (3 - i)) for i, b in enumerate(rawlen))
                    )
                    if not n or n > len(data) - pos - 10:
                        break
                    raw = data[pos + 10 : pos + 10 + n]
                    if key in (b"TDRC", b"TYER") and raw:
                        encoding = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(raw[0], "utf-8")
                        result["date"] = raw[1:].decode(encoding, errors="replace").strip("\x00")
                        break
                    pos += 10 + n
    except Exception:
        pass
    try:
        conn.send(result)
    except Exception:
        pass
    finally:
        conn.close()


def metadata(path: Path, budget_ms: int) -> tuple[dict[str, str], str]:
    """Run optional parsers in a killable subprocess with a wall-clock time budget.

    Process launch/cleanup has overhead outside the wait; OS scheduling cannot
    guarantee a strict end-to-end deadline on a stalled filesystem.
    """
    if budget_ms <= 0 or path.suffix.lower() not in (
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
        ".docx",
        ".xlsx",
        ".pptx",
        ".mp3",
    ):
        return {}, "not inspected"
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_metadata_worker, args=(str(path), child), daemon=True)
    try:
        proc.start()
        child.close()
        if parent.poll(budget_ms / 1000):
            try:
                result = parent.recv()
            except (EOFError, OSError):
                result = {}
            return result, "inspected"
        return {}, "inspection timed out"
    except Exception as exc:
        return {}, f"inspection unavailable: {type(exc).__name__}"
    finally:
        parent.close()
        child.close()
        if proc.pid is not None:
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=0.1)
            if proc.is_alive():
                proc.kill()
                proc.join()


def parsed_date(value: str) -> dict[str, str]:
    """Parse common filename, EXIF, PDF and ISO date representations."""
    m = re.search(r"(19\d{2}|20\d{2})[-:_. ]?(\d{2})[-:_. ]?(\d{2})", value)
    if not m:
        year = re.fullmatch(r"(19\d{2}|20\d{2})", value.strip())
        return {"year": year.group(1)} if year else {}
    try:
        day = dt.date(*(int(x) for x in m.groups()))
    except ValueError:
        return {}
    return {"year": f"{day.year:04d}", "month": f"{day.month:02d}", "day": f"{day.day:02d}"}


def _values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return value
    raise ValueError("expected a string or list of strings")


def classify(path: Path, config: Mapping[str, Any], budget_ms: int) -> dict[str, Any]:
    """Evaluate rules in configuration order; conditions within one rule are ANDed."""
    info = path.lstat()
    mime: str | None = None
    meta: dict[str, str] | None = None
    meta_status = "not inspected"
    for rule in config["rule"]:
        match = rule["match"]
        groups: dict[str, str] = {}
        if "glob" in match and not any(fnmatch.fnmatchcase(path.name, p) for p in _values(match["glob"])):
            continue
        if "ext" in match and path.suffix.lower().lstrip(".") not in [
            x.lower().lstrip(".") for x in _values(match["ext"])
        ]:
            continue
        if "min_size" in match and info.st_size < size_value(match["min_size"]):
            continue
        if "max_size" in match and info.st_size > size_value(match["max_size"]):
            continue
        if "filename" in match:
            found = re.search(match["filename"], path.name)
            if not found:
                continue
            groups.update({k: v for k, v in found.groupdict().items() if v is not None})
        if "mime" in match:
            if mime is None:
                mime = sniff(path)
            if mime not in _values(match["mime"]):
                continue
        if "text" in match:
            if meta is None:
                meta, meta_status = metadata(path, budget_ms)
            found = re.search(match["text"], meta.get("text", ""))
            if not found:
                continue
            groups.update({k: v for k, v in found.groupdict().items() if v is not None})
        filename_date = parsed_date(path.name)
        date = filename_date
        date_source = "filename" if date else ""
        # A year captured by a rule is a higher-priority signal, even without a full date.
        if groups.get("year") and re.fullmatch(r"\d{4}", groups["year"]):
            date = dict(date)
            date["year"] = groups["year"]
            date_source = "rule capture"
        if not date.get("year") or not date.get("month") or not date.get("day"):
            if meta is None:
                meta, meta_status = metadata(path, budget_ms)
            extracted = parsed_date(meta.get("date", ""))
            if extracted:
                if not date:
                    date_source = "embedded metadata"
                date = {**extracted, **date}
        fallback = dt.datetime.fromtimestamp(info.st_mtime).astimezone()
        if not date:
            date_source = "mtime fallback"
        elif set(("year", "month", "day")) - date.keys():
            date_source += " (missing fields from mtime)"
        date = {"year": f"{fallback.year:04d}", "month": f"{fallback.month:02d}", "day": f"{fallback.day:02d}", **date}
        return {
            "rule": rule["name"],
            "dest_template": rule["dest"],
            "groups": groups,
            "date": date,
            "date_source": date_source,
            "mime": mime,
            "inspection": meta_status,
            "matched": dict(match),
        }
    return {"rule": None, "groups": {}, "reason": "no matching rule", "mime": mime, "inspection": meta_status}


def destination(path: Path, result: Mapping[str, Any]) -> Path:
    """Render a destination directory with path-safe capture groups and date fields."""
    fields = {"ext": path.suffix.lower().lstrip(".") or "no-extension", **result["date"], **result["groups"]}
    for key, value in fields.items():
        if not isinstance(value, str) or value in ("", ".", "..") or "/" in value or "\\" in value or "\x00" in value:
            raise ValueError(f"unsafe template value for {key}")
    try:
        directory = str(result["dest_template"]).format_map(fields)
    except (KeyError, ValueError, AttributeError, IndexError) as exc:
        raise ValueError(f"bad destination template: {exc}") from exc
    return absolute(directory) / path.name


def available(path: Path, reserved: set[str]) -> bool:
    """Conservatively treat case-fold-equivalent directory entries as collisions."""
    key = str(path).casefold()
    if key in reserved or os.path.lexists(path):
        return False
    try:
        return not any(entry.name.casefold() == path.name.casefold() for entry in os.scandir(path.parent))
    except FileNotFoundError:
        return True


def suffix_name(name: str, n: int) -> str:
    """Add a collision suffix while respecting the common 255-byte component limit."""
    suffix = Path(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    addition = f" ({n})"
    limit = 255 - len(os.fsencode(addition + suffix))
    while len(os.fsencode(stem)) > limit:
        stem = stem[:-1]
    if not stem:
        raise ValueError("cannot construct collision filename")
    return stem + addition + suffix


def choose_destination(target: Path, policy: str, reserved: set[str], state: Path, plan_id: str) -> Path | None:
    """Reserve a deterministic unused output name during planning."""
    if available(target, reserved):
        reserved.add(str(target).casefold())
        return target
    if policy == "skip":
        return None
    if policy == "conflicts_dir":
        target = state / "conflicts" / plan_id / target.name
    if available(target, reserved):
        reserved.add(str(target).casefold())
        return target
    for n in range(1, 100000):
        candidate = target.with_name(suffix_name(target.name, n))
        if available(candidate, reserved):
            reserved.add(str(candidate).casefold())
            return candidate
    raise RuntimeError("too many naming collisions")


def scan(root: Path, recursive: bool, follow_symlinks: bool, state: Path) -> Iterator[Path]:
    """Yield files in a stable order; never traverse symlink directories."""
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")

    def visit(folder: Path) -> Iterator[Path]:
        with os.scandir(folder) as it:
            entries = sorted(it, key=lambda e: (e.name.casefold(), e.name))
        for e in entries:
            p = Path(e.path)
            if p == state or p.name == ".sortplan":
                continue
            try:
                if e.is_symlink():
                    if follow_symlinks and e.is_file(follow_symlinks=True):
                        yield p
                elif e.is_file(follow_symlinks=False):
                    yield p
                elif recursive and e.is_dir(follow_symlinks=False):
                    yield from visit(p)
            except OSError:
                continue

    yield from visit(root)


def _atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".plan-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def sync_dir(path: Path) -> None:
    """Best-effort directory fsync; not all platforms support directory handles."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def build_plan(
    root: str | Path,
    config: Mapping[str, Any],
    *,
    state: str | Path = DEFAULT_STATE,
    recursive: bool = False,
    follow_symlinks: bool = False,
    keep: str = "oldest",
    budget_ms: int = 200,
    dedupe_only: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Create and persist a plan; source and destination file bytes are untouched.

    Hashes every eligible regular file to identify duplicate groups and validate
    apply. Disk throughput, not only file count, determines planning latency.
    """
    root = absolute(root)
    state = absolute(state)
    if root == state:
        raise ValueError("state directory cannot be the scan root")
    if keep not in ("oldest", "newest", "shallowest"):
        raise ValueError("invalid keep policy")
    if budget_ms < 0:
        raise ValueError("inspection budget must be nonnegative")
    plan_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    policy = config.get("general", {}).get("on_conflict", "rename")
    age = float(config.get("general", {}).get("min_age_days", 1)) * 86400
    now = time.time() if now is None else now
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for path in scan(root, recursive, follow_symlinks, state):
        try:
            if now - path.lstat().st_mtime < age:
                skipped.append({"path": str(path), "reason": "younger than min_age_days"})
                continue
            fp = fingerprint(path)
            candidates.append({"path": str(path), **fp})
        except (OSError, ValueError, RuntimeError) as exc:
            skipped.append({"path": str(path), "reason": str(exc)})
    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for f in candidates:
        if f["kind"] == "file":
            groups.setdefault((f["size"], f["sha256"]), []).append(f)
    duplicates: list[dict[str, Any]] = []
    dupe_paths: set[str] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        if keep == "oldest":
            key: Callable[[dict[str, Any]], Any] = lambda x: (x["mtime_ns"], x["path"])
        elif keep == "newest":
            key = lambda x: (-x["mtime_ns"], x["path"])
        else:
            key = lambda x: (len(Path(x["path"]).relative_to(root).parts), x["path"])
        ordered = sorted(members, key=key)
        duplicates.append({
            "keep": ordered[0]["path"],
            "copies": [x["path"] for x in ordered[1:]],
            "reclaimable_bytes": sum(x["size"] for x in ordered[1:]),
        })
        dupe_paths.update(x["path"] for x in ordered[1:])
    reserved: set[str] = set()
    moves: list[dict[str, Any]] = []
    unsorted: list[dict[str, Any]] = []
    for f in candidates:
        path = Path(f["path"])
        try:
            if f["path"] in dupe_paths:
                rel = path.relative_to(root)
                target = state / "quarantine" / plan_id / rel
                reason: dict[str, Any] = {"rule": "duplicate-quarantine", "date_source": None, "groups": {}}
            elif dedupe_only:
                continue
            else:
                reason = classify(path, config, budget_ms)
                if reason["rule"] is None:
                    unsorted.append({"path": str(path), **reason})
                    continue
                target = destination(path, reason)
                if target == path:
                    unsorted.append({"path": str(path), "reason": "already at destination"})
                    continue
            chosen = choose_destination(target, policy, reserved, state, plan_id)
            if chosen is None:
                unsorted.append({"path": str(path), "reason": "destination collision (skip)"})
                continue
            moves.append({
                "src": str(path),
                "dst": str(chosen),
                **{k: f[k] for k in ("sha256", "size", "mtime_ns", "kind")},
                "rule": reason["rule"],
                "details": reason,
                "conflict": str(chosen) != str(target),
            })
        except (OSError, ValueError, RuntimeError) as exc:
            unsorted.append({"path": str(path), "reason": str(exc)})
    result: dict[str, Any] = {
        "version": VERSION,
        "id": plan_id,
        "created": utc_now(),
        "root": str(root),
        "state_dir": str(state),
        "moves": moves,
        "duplicates": duplicates,
        "unsorted": unsorted,
        "skipped": skipped,
        "keep": keep,
        "recursive": recursive,
        "follow_symlinks": follow_symlinks,
    }
    plan_path = state / "plans" / (plan_id + ".json")
    result["plan_path"] = str(plan_path)
    _atomic_json(plan_path, result)
    return result


def append_record(journal: Path, record: Mapping[str, Any]) -> None:
    """Append and fsync one JSONL record before/after an operation."""
    journal.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(record), ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("short journal write")
            remaining = remaining[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    sync_dir(journal.parent)


def read_records(journal: Path) -> list[dict[str, Any]]:
    """Read a journal, tolerating only an incomplete final line from a crash."""
    raw = journal.read_bytes()
    lines = raw.splitlines(keepends=True)
    records = []
    for i, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            if i == len(lines) - 1 and not line.endswith(b"\n"):
                break
            raise ValueError(f"corrupt journal at line {i + 1}")
    return records


def repair_journal_tail(journal: Path) -> None:
    """Finish or discard a torn final JSONL record before appending new records."""
    with journal.open("r+b") as stream:
        data = stream.read()
        if not data or data.endswith(b"\n"):
            return
        boundary = data.rfind(b"\n") + 1
        try:
            json.loads(data[boundary:])
        except (json.JSONDecodeError, UnicodeDecodeError):
            stream.truncate(boundary)
        else:
            stream.seek(0, os.SEEK_END)
            stream.write(b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def make_destination_dirs(path: Path, journal: Path) -> None:
    """Create and journal missing destination directories, shallowest first."""
    missing: list[Path] = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        if parent == parent.parent:
            break
        parent = parent.parent
    for directory in reversed(missing):
        append_record(journal, {"ts": utc_now(), "op": "mkdir_intent", "path": str(directory)})
        try:
            directory.mkdir()
        except FileExistsError:
            if not directory.is_dir():
                raise
        else:
            append_record(journal, {"ts": utc_now(), "op": "mkdir_created", "path": str(directory)})
            sync_dir(directory.parent)


def move_noreplace(src: Path, dst: Path, expected: Mapping[str, Any]) -> None:
    """Move an entry without clobbering an existing path.

    Hard-link+unlink gives no-replace semantics on the same filesystem. On EXDEV
    or filesystems that disallow hardlinks, an exclusive-created copy is fsynced
    and verified before unlinking the source. A symlink itself, not its target,
    is moved. This intentionally avoids unsafe POSIX rename/shutil.move, both of
    which can overwrite after a race. Never pass filenames through a shell.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not available(dst, set()):
        raise FileExistsError(f"destination exists: {dst}")
    try:
        os.link(src, dst, follow_symlinks=False)
    except OSError as exc:
        if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EACCES, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise
        if expected["kind"] == "symlink":
            os.symlink(os.readlink(src), dst)
            try:
                os.utime(dst, ns=(src.lstat().st_atime_ns, expected["mtime_ns"]), follow_symlinks=False)
            except (OSError, NotImplementedError):
                pass
        else:
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
                    shutil.copyfileobj(inp, out, CHUNK)
                    out.flush()
                    os.fsync(out.fileno())
                shutil.copystat(src, dst, follow_symlinks=False)
                if not same_content(dst, expected):
                    raise RuntimeError("copied file failed verification")
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(dst)
                raise
    # Recheck immediately before removing source; never delete a changed source.
    if not same_content(src, expected, mtime=True) or not same_content(dst, expected):
        raise RuntimeError("source or destination changed during move; both retained")
    sync_dir(dst.parent)
    os.unlink(src)
    sync_dir(src.parent)


def _journal_path(state: Path, plan_id: str) -> Path:
    return state / "journals" / (safe_id(plan_id) + ".jsonl")


def _apply_plan_unlocked(plan_path: str | Path, *, yes: bool = False, fail_after: int | None = None) -> dict[str, Any]:
    """Apply a persisted plan once; a failure leaves a journal recoverable by undo.

    ``fail_after`` is a test hook that raises after N completed moves. No prompt
    is performed by this API; confirmation must be explicit via ``yes=True``.
    """
    if not yes:
        raise PermissionError("apply requires explicit --yes")
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    if plan.get("version") != VERSION:
        raise ValueError("unsupported plan version")
    state = absolute(plan["state_dir"])
    plan_id = safe_id(plan["id"])
    journal = _journal_path(state, plan_id)
    journal.parent.mkdir(parents=True, exist_ok=True)
    # Atomic creation prevents concurrent/repeated application of the same plan.
    fd = os.open(journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.fsync(fd)
    os.close(fd)
    sync_dir(journal.parent)
    append_record(
        journal,
        {
            "ts": utc_now(),
            "op": "begin",
            "plan_id": plan_id,
            "plan_path": str(absolute(plan_path)),
            "started_ns": time.time_ns(),
        },
    )
    completed = 0
    errors: list[dict[str, str]] = []
    if fail_after is not None and fail_after <= 0:
        raise RuntimeError("simulated interruption")
    for i, item in enumerate(plan["moves"]):
        src, dst = Path(item["src"]), Path(item["dst"])
        if not src.is_absolute() or not dst.is_absolute():
            raise ValueError("plan paths must be absolute")
        if not same_content(src, item, mtime=True):
            errors.append({"src": str(src), "error": "source missing or changed since planning"})
            continue
        if not available(dst, set()):
            errors.append({"src": str(src), "error": "destination now exists; re-plan"})
            continue
        try:
            make_destination_dirs(dst.parent, journal)
        except OSError as exc:
            errors.append({"src": str(src), "error": str(exc)})
            continue
        record = {
            "ts": utc_now(),
            "op": "move",
            "index": i,
            "src": str(src),
            "dst": str(dst),
            **{k: item[k] for k in ("sha256", "size", "mtime_ns", "kind", "rule")},
            "conflict": item.get("conflict", False),
        }
        append_record(journal, record)  # write-ahead, durable before move
        try:
            move_noreplace(src, dst, item)
            completed += 1
            append_record(journal, {"ts": utc_now(), "op": "moved", "index": i})
        except (OSError, RuntimeError) as exc:
            errors.append({"src": str(src), "error": str(exc)})
            append_record(journal, {"ts": utc_now(), "op": "move_error", "index": i, "error": str(exc)})
        if fail_after is not None and completed >= fail_after:
            raise RuntimeError("simulated interruption")
    append_record(journal, {"ts": utc_now(), "op": "apply_end", "completed": completed, "errors": errors})
    return {"plan_id": plan_id, "journal": str(journal), "completed": completed, "errors": errors}


def _undo_plan_unlocked(plan_id: str, *, state: str | Path = DEFAULT_STATE) -> dict[str, Any]:
    """Reverse journaled moves in reverse order; never overwrite or lose modified data.

    If both ends exist (e.g. interruption between cross-device copy and unlink),
    or a destination has changed, report a conflict and leave both untouched.
    Repeating undo safely retries only entries that still need recovery.
    """
    journal = _journal_path(absolute(state), plan_id)
    repair_journal_tail(journal)
    records = read_records(journal)
    moves = [r for r in records if r.get("op") == "move"]
    restored = 0
    already = 0
    errors: list[dict[str, str]] = []
    for item in reversed(moves):
        src, dst = Path(item["src"]), Path(item["dst"])
        src_exists, dst_exists = os.path.lexists(src), os.path.lexists(dst)
        if src_exists and not dst_exists:
            if same_content(src, item):
                already += 1
            else:
                errors.append({"src": str(src), "error": "original path exists but differs; refusing"})
            continue
        if src_exists and dst_exists:
            errors.append({"src": str(src), "error": "both paths exist; manual resolution required"})
            continue
        if not src_exists and not dst_exists:
            errors.append({"src": str(src), "error": "both paths missing"})
            continue
        if not same_content(dst, item):
            errors.append({"src": str(src), "error": "destination modified; refusing to undo"})
            continue
        append_record(
            journal, {"ts": utc_now(), "op": "undo_intent", "index": item["index"], "src": str(dst), "dst": str(src)}
        )
        try:
            # Use the destination's current mtime for safe move verification, then restore original.
            current = dict(item)
            current["mtime_ns"] = dst.lstat().st_mtime_ns
            move_noreplace(dst, src, current)
            st = src.lstat()
            os.utime(src, ns=(st.st_atime_ns, item["mtime_ns"]), follow_symlinks=False)
            restored += 1
            append_record(journal, {"ts": utc_now(), "op": "undone", "index": item["index"]})
        except (OSError, RuntimeError, NotImplementedError) as exc:
            errors.append({"src": str(src), "error": str(exc)})
    # Remove only directories this apply recorded creating, and only if empty.
    for record in reversed(records):
        if record.get("op") == "mkdir_created":
            folder = Path(record["path"])
            try:
                folder.rmdir()
                sync_dir(folder.parent)
            except OSError:
                pass  # nonempty, already gone, or inaccessible: never force-remove
    append_record(
        journal, {"ts": utc_now(), "op": "undo_end", "restored": restored, "already": already, "errors": errors}
    )
    return {"plan_id": plan_id, "restored": restored, "already": already, "errors": errors}


@contextlib.contextmanager
def operation_lock(state: Path, plan_id: str) -> Iterator[None]:
    """Prevent simultaneous apply/undo of one plan on POSIX/Termux."""
    import fcntl

    folder = state / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / (safe_id(plan_id) + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another operation is using this journal") from exc
        yield
    finally:
        os.close(fd)


def apply_plan(plan_path: str | Path, *, yes: bool = False, fail_after: int | None = None) -> dict[str, Any]:
    """Lock and execute a plan; confirmation is mandatory."""
    if not yes:
        raise PermissionError("apply requires explicit --yes")
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    state, plan_id = absolute(plan["state_dir"]), safe_id(plan["id"])
    with operation_lock(state, plan_id):
        return _apply_plan_unlocked(plan_path, yes=True, fail_after=fail_after)


def undo_plan(plan_id: str, *, state: str | Path = DEFAULT_STATE) -> dict[str, Any]:
    """Lock and safely reverse a plan's recorded moves."""
    state_path = absolute(state)
    with operation_lock(state_path, plan_id):
        return _undo_plan_unlocked(plan_id, state=state_path)


def last_plan_id(state: str | Path = DEFAULT_STATE) -> str:
    """Find the most recently created journal (including interrupted applies)."""
    folder = absolute(state) / "journals"
    files = list(folder.glob("*.jsonl")) if folder.exists() else []
    if not files:
        raise FileNotFoundError("no journals found")

    def started(path: Path) -> tuple[int, str]:
        try:
            records = read_records(path)
            begin = next((r for r in records if r.get("op") == "begin"), {})
            return (int(begin.get("started_ns", 0)), path.name)
        except (OSError, ValueError, TypeError):
            return (0, path.name)

    return max(files, key=started).stem


def print_plan(plan: Mapping[str, Any]) -> None:
    """Print a human-readable, newline-safe plan summary."""
    print(f"WOULD MOVE ({len(plan['moves'])} files, {sum(m['size'] for m in plan['moves'])} bytes)")
    for m in plan["moves"]:
        print(f"  {m['dst']!r} <- {m['src']!r} [{m['rule']}; date={m['details'].get('date_source')}]")
    print(
        f"DUPLICATES ({len(plan['duplicates'])} groups; {sum(d['reclaimable_bytes'] for d in plan['duplicates'])} bytes in extra copies; quarantined, not reclaimed)"
    )
    for d in plan["duplicates"]:
        print(f"  keep {d['keep']!r}; quarantine {d['copies']!r}")
    print(f"UNSORTED ({len(plan['unsorted'])}); SKIPPED ({len(plan['skipped'])})")
    for u in plan["unsorted"]:
        print(f"  {u['path']!r}: {u.get('reason', 'unsorted')}")
    for s in plan["skipped"]:
        print(f"  skipped {s['path']!r}: {s['reason']}")
    print(f"Apply: python sortplan.py apply --plan {plan['plan_path']!r} --yes")


def make_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def state_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--state-dir", default=str(DEFAULT_STATE), help="state/journal directory (default: ~/.sortplan)")

    def scan_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", help="TOML config")
        p.add_argument(
            "--recursive", action="store_true", help="scan subdirectories, never traverse directory symlinks"
        )
        p.add_argument(
            "--follow-symlinks",
            action="store_true",
            help="inspect file symlinks and move the link itself, never its target",
        )
        p.add_argument("--keep", choices=("oldest", "newest", "shallowest"), default="oldest")
        p.add_argument("--max-inspect-ms", type=int, default=200, help="metadata worker timeout; 0 disables metadata")
        p.add_argument("--json", action="store_true", help="print machine-readable result")
        state_arg(p)

    p = sub.add_parser("plan", help="create a persistent dry-run plan")
    p.add_argument("root", nargs="?", help="scan root; overrides general.root")
    scan_args(p)
    p = sub.add_parser("dedupe", help="plan duplicate quarantine (does not move anything)")
    p.add_argument("root", nargs="?", help="scan root; overrides general.root")
    p.add_argument("--by", choices=("content",), default="content")
    scan_args(p)
    p = sub.add_parser("apply", help="execute a plan with a durable undo journal")
    p.add_argument("--plan", required=True)
    p.add_argument("--yes", action="store_true", help="required confirmation")
    p = sub.add_parser("undo", help="undo a journal")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--last", action="store_true")
    g.add_argument("--plan-id")
    state_arg(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("explain", help="explain a rule match for an existing file")
    p.add_argument("file")
    p.add_argument("--config", required=True)
    p.add_argument("--max-inspect-ms", type=int, default=200)
    p.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; return zero on success and nonzero for partial failures."""
    args = make_parser().parse_args(argv)
    try:
        if args.command in ("plan", "dedupe"):
            config = load_config(args.config)
            root = args.root or config["general"].get("root", "~/Downloads")
            plan = build_plan(
                root,
                config,
                state=args.state_dir,
                recursive=args.recursive,
                follow_symlinks=args.follow_symlinks,
                keep=args.keep,
                budget_ms=args.max_inspect_ms,
                dedupe_only=args.command == "dedupe",
            )
            if args.json:
                print(json.dumps(plan, indent=2, ensure_ascii=True))
            else:
                print_plan(plan)
            return 0
        if args.command == "apply":
            result = apply_plan(args.plan, yes=args.yes)
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 1 if result["errors"] else 0
        if args.command == "undo":
            plan_id = last_plan_id(args.state_dir) if args.last else args.plan_id
            result = undo_plan(plan_id, state=args.state_dir)
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 1 if result["errors"] else 0
        if args.command == "explain":
            path = absolute(args.file)
            result = classify(path, load_config(args.config), args.max_inspect_ms)
            if result["rule"] is not None:
                result["destination"] = str(destination(path, result))
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        print(f"sortplan: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
