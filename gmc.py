#!/data/data/com.termux/files/usr/bin/env python
"""Single-file GNU Midnight Commander clone built on curses.

CLI entry point; opens a full-screen two-panel file manager over the local
filesystem."""

from __future__ import annotations

import argparse
import curses
import datetime
import fnmatch
import functools
import grp
import hashlib
import json
import locale
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import termios
import time
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from dh import atomic_write
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


try:
    from compression import zstd as _stdlib_zstd
except ImportError:
    _stdlib_zstd = None
APP_NAME: str = "Midnight Commander (mc.py)"
APP_VERSION: str = "1.0.0"
MIN_PYTHON: tuple[int, int] = (3, 14)
ARCHIVE_SUFFIXES: tuple[str, ...] = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".tar.zst",
    ".tzst",
)


def safe_str(value: str | bytes) -> str:
    text = os.fsdecode(value) if isinstance(value, bytes) else value
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xDC80 <= code <= 0xDCFF or code < 32 or code == 127:
            out.append("?")
        else:
            out.append(ch)
    return "".join(out)


def char_width(ch: str) -> int:
    if not ch:
        return 0
    if ord(ch) < 32 or ord(ch) == 127:
        return 1
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 2
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 1


def display_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def truncate_right(text: str, max_cols: int) -> str:
    """Truncate right."""
    if max_cols <= 0:
        return ""
    if display_width(text) <= max_cols:
        return text
    if max_cols == 1:
        return "~"
    acc: list[str] = []
    w = 0
    for ch in text:
        cw = char_width(ch)
        if w + cw > max_cols - 1:
            break
        acc.append(ch)
        w += cw
    acc.append("~")
    return "".join(acc)


def truncate_middle(text: str, max_cols: int) -> str:
    """Truncate middle."""
    if max_cols <= 0:
        return ""
    if display_width(text) <= max_cols:
        return text
    if max_cols <= 3:
        return truncate_right(text, max_cols)
    left_budget = (max_cols - 1) // 2
    right_budget = max_cols - 1 - left_budget
    left_chars: list[str] = []
    lw = 0
    for ch in text:
        cw = char_width(ch)
        if lw + cw > left_budget:
            break
        left_chars.append(ch)
        lw += cw
    right_chars: list[str] = []
    rw = 0
    for ch in reversed(text):
        cw = char_width(ch)
        if rw + cw > right_budget:
            break
        right_chars.append(ch)
        rw += cw
    right_chars.reverse()
    return "".join(left_chars) + "~" + "".join(right_chars)


def pad_right(text: str, width: int) -> str:
    """Pad right."""
    s = truncate_right(text, width)
    rem = max(0, width - display_width(s))
    return s + (" " * rem)


def pad_left(text: str, width: int) -> str:
    """Pad left."""
    s = truncate_right(text, width)
    rem = max(0, width - display_width(s))
    return (" " * rem) + s


def format_size_compact(size: int, width: int = 7) -> str:
    """Format size compact."""
    if size < 0:
        return pad_left("?", width)
    s = str(size)
    if len(s) <= width:
        return pad_left(s, width)
    size_f = float(size)
    for unit in ("K", "M", "G", "T", "P"):
        size_f /= 1024.0
        if size_f < 1000:
            candidate = f"{size_f:.1f}{unit}" if size_f < 10 else f"{round(size_f)}{unit}"
            if len(candidate) <= width:
                return pad_left(candidate, width)
        else:
            candidate = f"{round(size_f)}{unit}"
            if len(candidate) <= width:
                return pad_left(candidate, width)
    return pad_left(f"{round(size_f)}P", width)


def format_human_bytes(num: int) -> str:
    """Format human bytes."""
    if num < 1024:
        return f"{num} B"
    val = float(num)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        val /= 1024.0
        if val < 1024.0 or unit == "TiB":
            return f"{val:.1f} {unit}"
    return f"{num} B"


def format_mtime(ts: float) -> str:
    """Format mtime."""
    if ts <= 0:
        return "            "
    try:
        dt = datetime.datetime.fromtimestamp(ts)
    except (OSError, OverflowError, ValueError):
        return "            "
    now = datetime.datetime.now()
    if abs((now - dt).total_seconds()) > 180 * 86400:
        return dt.strftime("%b %d  %Y")
    return dt.strftime("%b %d %H:%M")


@functools.lru_cache(maxsize=256)
def lookup_user(uid: int) -> str:
    if uid < 0:
        return "-"
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


@functools.lru_cache(maxsize=256)
def lookup_group(gid: int) -> str:
    if gid < 0:
        return "-"
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def shorten_home(path_str: str) -> str:
    """Shorten home."""
    home = str(Path.home())
    if path_str == home:
        return "~"
    if path_str.startswith(home + os.sep):
        return "~" + path_str[len(home) :]
    return path_str


class PanelMode(StrEnum):
    """Enumerate the supported PanelMode values."""

    LISTING = "listing"
    INFO = "info"
    QUICK_VIEW = "quick_view"


class ListingFormat(StrEnum):
    """Enumerate the supported ListingFormat values."""

    FULL = "full"
    BRIEF = "brief"
    LONG = "long"


class SortField(StrEnum):
    """Enumerate the supported SortField values."""

    NAME = "name"
    EXT = "extension"
    SIZE = "size"
    MTIME = "mtime"
    UNSORTED = "unsorted"


class ColorTheme(StrEnum):
    """Enumerate the supported ColorTheme values."""

    CLASSIC = "classic"
    DARK = "dark"
    MONO = "mono"


class FileKind(StrEnum):
    """Enumerate the supported FileKind values."""

    PARENT = "parent"
    DIR = "dir"
    EXEC = "exec"
    SYMLINK = "symlink"
    BROKEN_LINK = "broken_link"
    ARCHIVE = "archive"
    FIFO = "fifo"
    SOCKET = "socket"
    DEVICE = "device"
    REGULAR = "regular"


@dataclass(slots=True, kw_only=True)
class FileEntry:
    """Store name, path, kind, size, mtime and related state."""

    name: str
    path: Path
    kind: FileKind
    size: int = 0
    mtime: float = 0.0
    mode: int = 0
    uid: int = -1
    gid: int = -1
    nlink: int = 1
    link_target: str | None = None
    is_hidden: bool = False
    is_dir_like: bool = False
    vfs_member: str | None = None

    @property
    def prefix_sigil(self) -> str:
        """Return the prefix sigil."""
        match self.kind:
            case FileKind.PARENT | FileKind.DIR:
                return "/"
            case FileKind.SYMLINK:
                return "@" if not self.is_dir_like else "~"
            case FileKind.BROKEN_LINK:
                return "!"
            case FileKind.EXEC:
                return "*"
            case FileKind.FIFO:
                return "|"
            case FileKind.SOCKET:
                return "="
            case FileKind.DEVICE:
                return "#"
            case _:
                return " "


@dataclass(slots=True, kw_only=True)
class VfsLocation:
    """Store archive_path, archive_kind, inner_path."""

    archive_path: Path
    archive_kind: Literal["zip", "tar"]
    inner_path: str = ""

    def display_path(self) -> str:
        base = shorten_home(str(self.archive_path))
        tail = f"/{self.inner_path}" if self.inner_path else ""
        return f"{base}#vfs{tail}"


@dataclass(slots=True, kw_only=True)
class PanelState:
    """Store side, cwd, vfs, entries, cursor and related state."""

    side: Literal["left", "right"]
    cwd: Path
    vfs: VfsLocation | None = None
    entries: list[FileEntry] = field(default_factory=list)
    cursor: int = 0
    scroll: int = 0
    selected: set[str] = field(default_factory=set)
    mode: PanelMode = PanelMode.LISTING
    listing_format: ListingFormat = ListingFormat.FULL
    sort_field: SortField = SortField.NAME
    sort_reverse: bool = False
    filter_glob: str = "*"
    show_hidden: bool = True
    quick_search: str = ""
    last_dir_name: str | None = None

    def current_entry(self) -> FileEntry | None:
        if 0 <= self.cursor < len(self.entries):
            return self.entries[self.cursor]
        return None

    def display_cwd(self) -> str:
        if self.vfs is not None:
            return self.vfs.display_path()
        return shorten_home(str(self.cwd))

    def target_entries(self) -> list[FileEntry]:
        if self.selected:
            return [e for e in self.entries if e.name in self.selected and e.kind != FileKind.PARENT]
        cur = self.current_entry()
        if cur and cur.kind != FileKind.PARENT:
            return [cur]
        return []


@dataclass(slots=True, kw_only=True)
class AppConfig:
    """Store show_hidden, confirm_delete, confirm_overwrite, confirm_exit, ascii_lines and related state."""

    show_hidden: bool = True
    confirm_delete: bool = True
    confirm_overwrite: bool = True
    confirm_exit: bool = False
    ascii_lines: bool = False
    theme: ColorTheme = ColorTheme.CLASSIC
    mouse_enabled: bool = True
    nosubshell: bool = False
    config_dir: Path = field(default_factory=lambda: Path.home() / ".config" / "mc_py")

    @property
    def hotlist_file(self) -> Path:
        """Return the hotlist file."""
        return self.config_dir / "hotlist.json"

    def load_hotlist(self) -> list[dict[str, str]]:
        """Load hotlist."""
        try:
            if self.hotlist_file.exists():
                data = json.loads(self.hotlist_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict) and "path" in x]
        except Exception:
            pass
        return [
            {"label": "Home", "path": str(Path.home())},
            {"label": "Root", "path": "/"},
            {"label": "Temp", "path": "/tmp"},
            {"label": "Etc", "path": "/etc"},
        ]

    def save_hotlist(self, items: list[dict[str, str]]) -> None:
        """Save hotlist."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(self.hotlist_file, json.dumps(items, indent=2), encoding="utf-8")
        except OSError:
            pass


def is_archive_name(name: str) -> bool:
    """Return whether archive name."""
    lower = name.lower()
    return any(lower.endswith(sfx) for sfx in ARCHIVE_SUFFIXES)


def open_tar_archive(path: Path) -> tarfile.TarFile:
    """Open tar archive."""
    return tarfile.open(path, mode="r:*")


def classify_local_entry(entry: os.DirEntry[str]) -> FileEntry:
    """Classify local entry."""
    name = safe_str(entry.name)
    full_path = Path(entry.path)
    is_hidden = name.startswith(".")
    link_target: str | None = None
    is_dir_like = False
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return FileEntry(
            name=name,
            path=full_path,
            kind=FileKind.REGULAR,
            is_hidden=is_hidden,
        )
    mode = st.st_mode
    kind = FileKind.REGULAR
    if stat.S_ISLNK(mode):
        try:
            link_target = safe_str(os.readlink(entry.path))
        except OSError:
            link_target = "?"
        try:
            target_st = entry.stat(follow_symlinks=True)
            is_dir_like = stat.S_ISDIR(target_st.st_mode)
            kind = FileKind.SYMLINK
        except OSError:
            kind = FileKind.BROKEN_LINK
    elif stat.S_ISDIR(mode):
        kind = FileKind.DIR
        is_dir_like = True
    elif stat.S_ISFIFO(mode):
        kind = FileKind.FIFO
    elif stat.S_ISSOCK(mode):
        kind = FileKind.SOCKET
    elif stat.S_ISBLK(mode) or stat.S_ISCHR(mode):
        kind = FileKind.DEVICE
    elif is_archive_name(name):
        kind = FileKind.ARCHIVE
    elif mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        kind = FileKind.EXEC
    return FileEntry(
        name=name,
        path=full_path,
        kind=kind,
        size=st.st_size,
        mtime=st.st_mtime,
        mode=mode,
        uid=st.st_uid,
        gid=st.st_gid,
        nlink=st.st_nlink,
        link_target=link_target,
        is_hidden=is_hidden,
        is_dir_like=is_dir_like,
    )


def sort_entries(entries: list[FileEntry], field_name: SortField, reverse: bool) -> list[FileEntry]:
    """Sort entries."""
    parents = [e for e in entries if e.kind == FileKind.PARENT]
    dirs = [e for e in entries if e.kind != FileKind.PARENT and e.is_dir_like]
    files = [e for e in entries if e.kind != FileKind.PARENT and not e.is_dir_like]

    def key_fn(e: FileEntry) -> tuple[object, ...]:
        match field_name:
            case SortField.NAME:
                return (e.name.lower(), e.name)
            case SortField.EXT:
                ext = Path(e.name).suffix
                return (ext.lower(), e.name.lower())
            case SortField.SIZE:
                return (e.size, e.name.lower())
            case SortField.MTIME:
                return (e.mtime, e.name.lower())
            case _:
                return (0,)

    if field_name != SortField.UNSORTED:
        dirs.sort(key=key_fn, reverse=reverse)
        files.sort(key=key_fn, reverse=reverse)
    elif reverse:
        dirs.reverse()
        files.reverse()
    return parents + dirs + files


def scan_local_directory(panel: PanelState) -> tuple[list[FileEntry], str | None]:
    """Scan local directory."""
    entries: list[FileEntry] = []
    cwd = panel.cwd
    if cwd != cwd.parent:
        try:
            pst = cwd.parent.stat()
            parent_mtime = pst.st_mtime
            parent_mode = pst.st_mode
        except OSError:
            parent_mtime = 0.0
            parent_mode = stat.S_IFDIR | 0o755
        entries.append(
            FileEntry(
                name="..",
                path=cwd.parent,
                kind=FileKind.PARENT,
                size=0,
                mtime=parent_mtime,
                mode=parent_mode,
                is_dir_like=True,
            )
        )
    err_msg: str | None = None
    scanned: list[FileEntry] = []
    try:
        with os.scandir(cwd) as it:
            for item in it:
                fe = classify_local_entry(item)
                if not panel.show_hidden and fe.is_hidden:
                    continue
                if (panel.filter_glob and panel.filter_glob != "*" and not fe.is_dir_like) and not fnmatch.fnmatch(
                    fe.name, panel.filter_glob
                ):
                    continue
                scanned.append(fe)
    except OSError as exc:
        err_msg = str(exc)
    entries.extend(scanned)
    return sort_entries(entries, panel.sort_field, panel.sort_reverse), err_msg


def scan_vfs_directory(panel: PanelState) -> tuple[list[FileEntry], str | None]:
    """Scan vfs directory."""
    vfs = panel.vfs
    assert vfs is not None
    entries: list[FileEntry] = [
        FileEntry(
            name="..",
            path=vfs.archive_path,
            kind=FileKind.PARENT,
            is_dir_like=True,
        )
    ]
    prefix = vfs.inner_path.strip("/")
    if prefix:
        prefix += "/"
    seen_dirs: dict[str, FileEntry] = {}
    seen_files: dict[str, FileEntry] = {}
    err_msg: str | None = None
    try:
        if vfs.archive_kind == "zip":
            with zipfile.ZipFile(vfs.archive_path, "r") as zf:
                for info in zf.infolist():
                    norm = info.filename.lstrip("/")
                    if not norm or not norm.startswith(prefix):
                        continue
                    rel = norm[len(prefix) :].rstrip("/")
                    if not rel:
                        continue
                    if "/" in rel:
                        top_dir = rel.split("/", 1)[0]
                        if top_dir not in seen_dirs:
                            seen_dirs[top_dir] = FileEntry(
                                name=safe_str(top_dir),
                                path=vfs.archive_path,
                                kind=FileKind.DIR,
                                mode=stat.S_IFDIR | 0o755,
                                is_dir_like=True,
                                is_hidden=top_dir.startswith("."),
                                vfs_member=prefix + top_dir,
                            )
                    elif info.is_dir():
                        if rel not in seen_dirs:
                            seen_dirs[rel] = FileEntry(
                                name=safe_str(rel),
                                path=vfs.archive_path,
                                kind=FileKind.DIR,
                                mode=stat.S_IFDIR | 0o755,
                                mtime=time.mktime((*info.date_time, 0, 0, -1)),
                                is_dir_like=True,
                                is_hidden=rel.startswith("."),
                                vfs_member=info.filename,
                            )
                    else:
                        ext_attr = (info.external_attr >> 16) & 0xFFFF
                        mode = ext_attr if ext_attr else (stat.S_IFREG | 0o644)
                        is_exec = bool(mode & 0o111)
                        seen_files[rel] = FileEntry(
                            name=safe_str(rel),
                            path=vfs.archive_path,
                            kind=FileKind.EXEC if is_exec else FileKind.REGULAR,
                            size=info.file_size,
                            mtime=time.mktime((*info.date_time, 0, 0, -1)),
                            mode=mode,
                            is_hidden=rel.startswith("."),
                            vfs_member=info.filename,
                        )
        else:
            with open_tar_archive(vfs.archive_path) as tf:
                for member in tf:
                    norm = member.name.lstrip("./")
                    if not norm or not norm.startswith(prefix):
                        continue
                    rel = norm[len(prefix) :].rstrip("/")
                    if not rel:
                        continue
                    if "/" in rel:
                        top_dir = rel.split("/", 1)[0]
                        if top_dir not in seen_dirs:
                            seen_dirs[top_dir] = FileEntry(
                                name=safe_str(top_dir),
                                path=vfs.archive_path,
                                kind=FileKind.DIR,
                                mode=stat.S_IFDIR | 0o755,
                                is_dir_like=True,
                                is_hidden=top_dir.startswith("."),
                                vfs_member=prefix + top_dir,
                            )
                    elif member.isdir():
                        seen_dirs[rel] = FileEntry(
                            name=safe_str(rel),
                            path=vfs.archive_path,
                            kind=FileKind.DIR,
                            mode=stat.S_IFDIR | (member.mode or 0o755),
                            mtime=float(member.mtime),
                            is_dir_like=True,
                            is_hidden=rel.startswith("."),
                            vfs_member=member.name,
                        )
                    else:
                        kind = (
                            FileKind.SYMLINK
                            if member.issym()
                            else (FileKind.EXEC if (member.mode & 0o111) else FileKind.REGULAR)
                        )
                        seen_files[rel] = FileEntry(
                            name=safe_str(rel),
                            path=vfs.archive_path,
                            kind=kind,
                            size=member.size,
                            mtime=float(member.mtime),
                            mode=stat.S_IFREG | (member.mode or 0o644),
                            link_target=member.linkname if member.issym() else None,
                            is_hidden=rel.startswith("."),
                            vfs_member=member.name,
                        )
    except Exception as exc:
        err_msg = str(exc)
    combined = list(seen_dirs.values()) + [f for f in seen_files.values() if f.name not in seen_dirs]
    if not panel.show_hidden:
        combined = [e for e in combined if not e.is_hidden]
    if panel.filter_glob and panel.filter_glob != "*":
        combined = [e for e in combined if e.is_dir_like or fnmatch.fnmatch(e.name, panel.filter_glob)]
    entries.extend(combined)
    return sort_entries(entries, panel.sort_field, panel.sort_reverse), err_msg


def read_vfs_bytes(vfs: VfsLocation, member_name: str, max_bytes: int | None = None) -> bytes:
    """Read vfs bytes."""
    if vfs.archive_kind == "zip":
        with zipfile.ZipFile(vfs.archive_path, "r") as zf:
            with zf.open(member_name, "r") as fp:
                return fp.read(max_bytes) if max_bytes is not None else fp.read()
    with open_tar_archive(vfs.archive_path) as tf:
        for m in tf:
            if m.name == member_name and m.isfile():
                fp = tf.extractfile(m)
                if fp is None:
                    return b""
                with fp:
                    return fp.read(max_bytes) if max_bytes is not None else fp.read()
    raise FileNotFoundError(f"Archive member not found: {member_name}")


def extract_vfs_member(vfs: VfsLocation, entry: FileEntry, dest_dir: Path) -> None:
    """Extract vfs member."""
    if entry.vfs_member is None:
        return
    if vfs.archive_kind == "zip":
        with zipfile.ZipFile(vfs.archive_path, "r") as zf:
            if entry.is_dir_like:
                prefix = entry.vfs_member.rstrip("/") + "/"
                for info in zf.infolist():
                    if info.filename.startswith(prefix) and not info.is_dir():
                        rel = info.filename[len(prefix) :]
                        out = (dest_dir / entry.name / rel).resolve()
                        if not str(out).startswith(str(dest_dir.resolve())):
                            continue
                        out.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as src, open(out, "wb") as dst:
                            shutil.copyfileobj(src, dst)
            else:
                out = (dest_dir / entry.name).resolve()
                if str(out).startswith(str(dest_dir.resolve())):
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(entry.vfs_member) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        with open_tar_archive(vfs.archive_path) as tf:
            if entry.is_dir_like:
                prefix = entry.vfs_member.lstrip("./").rstrip("/") + "/"
                for m in tf:
                    norm = m.name.lstrip("./")
                    if norm.startswith(prefix) and m.isfile():
                        rel = norm[len(prefix) :]
                        out = (dest_dir / entry.name / rel).resolve()
                        if not str(out).startswith(str(dest_dir.resolve())):
                            continue
                        out.parent.mkdir(parents=True, exist_ok=True)
                        fp = tf.extractfile(m)
                        if fp:
                            with fp, open(out, "wb") as dst:
                                shutil.copyfileobj(fp, dst)
            else:
                data = read_vfs_bytes(vfs, entry.vfs_member)
                out = (dest_dir / entry.name).resolve()
                if str(out).startswith(str(dest_dir.resolve())):
                    out.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(out, data)


class Palette:
    PANEL_BG = 1
    PANEL_HEADER_ACTIVE = 2
    PANEL_HEADER_INACTIVE = 3
    PANEL_DIR = 4
    PANEL_EXEC = 5
    PANEL_SYMLINK = 6
    PANEL_BROKEN = 7
    PANEL_ARCHIVE = 8
    PANEL_SELECTED = 9
    PANEL_CURSOR = 10
    PANEL_CURSOR_SEL = 11
    PANEL_COLUMN_HDR = 12
    MENU_BAR = 13
    MENU_HOTKEY = 14
    FN_NUM = 15
    FN_LABEL = 16
    CMD_LINE = 17
    DIALOG_BG = 18
    DIALOG_TITLE = 19
    DIALOG_BUTTON_ACT = 20
    DIALOG_INPUT = 21
    VIEWER_STATUS = 22
    SYNTAX_KEYWORD = 23
    SYNTAX_STRING = 24
    SYNTAX_COMMENT = 25
    SYNTAX_NUMBER = 26
    MARK_BLOCK = 27
    ERROR_BG = 28


class ThemeManager:
    def __init__(self, theme: ColorTheme) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.theme = theme
        self.has_colors = False
        self.pairs: dict[int, int] = {}
        self.apply(theme)

    def apply(self, theme: ColorTheme) -> None:
        self.theme = theme
        if theme == ColorTheme.MONO or not curses.has_colors():
            self.has_colors = False
            return
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        self.has_colors = True
        if theme == ColorTheme.CLASSIC:
            bg = curses.COLOR_BLUE
            dlg_bg = curses.COLOR_WHITE
            dlg_fg = curses.COLOR_BLACK
            curses.init_pair(Palette.PANEL_BG, curses.COLOR_WHITE, bg)
            curses.init_pair(Palette.PANEL_HEADER_ACTIVE, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(Palette.PANEL_HEADER_INACTIVE, curses.COLOR_CYAN, bg)
            curses.init_pair(Palette.PANEL_DIR, curses.COLOR_WHITE, bg)
            curses.init_pair(Palette.PANEL_EXEC, curses.COLOR_GREEN, bg)
            curses.init_pair(Palette.PANEL_SYMLINK, curses.COLOR_CYAN, bg)
            curses.init_pair(Palette.PANEL_BROKEN, curses.COLOR_RED, bg)
            curses.init_pair(Palette.PANEL_ARCHIVE, curses.COLOR_MAGENTA, bg)
            curses.init_pair(Palette.PANEL_SELECTED, curses.COLOR_YELLOW, bg)
            curses.init_pair(Palette.PANEL_CURSOR, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(Palette.PANEL_CURSOR_SEL, curses.COLOR_YELLOW, curses.COLOR_CYAN)
            curses.init_pair(Palette.PANEL_COLUMN_HDR, curses.COLOR_YELLOW, bg)
            curses.init_pair(Palette.MENU_BAR, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(Palette.MENU_HOTKEY, curses.COLOR_YELLOW, curses.COLOR_CYAN)
            curses.init_pair(Palette.FN_NUM, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(Palette.FN_LABEL, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(Palette.CMD_LINE, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(Palette.DIALOG_BG, dlg_fg, dlg_bg)
            curses.init_pair(Palette.DIALOG_TITLE, curses.COLOR_BLUE, dlg_bg)
            curses.init_pair(Palette.DIALOG_BUTTON_ACT, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(Palette.DIALOG_INPUT, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(Palette.VIEWER_STATUS, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(Palette.SYNTAX_KEYWORD, curses.COLOR_YELLOW, bg)
            curses.init_pair(Palette.SYNTAX_STRING, curses.COLOR_GREEN, bg)
            curses.init_pair(Palette.SYNTAX_COMMENT, curses.COLOR_CYAN, bg)
            curses.init_pair(Palette.SYNTAX_NUMBER, curses.COLOR_MAGENTA, bg)
            curses.init_pair(Palette.MARK_BLOCK, curses.COLOR_WHITE, curses.COLOR_CYAN)
            curses.init_pair(Palette.ERROR_BG, curses.COLOR_WHITE, curses.COLOR_RED)
        else:
            bg = curses.COLOR_BLACK
            dlg_bg = curses.COLOR_BLUE
            dlg_fg = curses.COLOR_WHITE
            curses.init_pair(Palette.PANEL_BG, curses.COLOR_WHITE, bg)
            curses.init_pair(Palette.PANEL_HEADER_ACTIVE, curses.COLOR_BLACK, curses.COLOR_GREEN)
            curses.init_pair(Palette.PANEL_HEADER_INACTIVE, curses.COLOR_WHITE, bg)
            curses.init_pair(Palette.PANEL_DIR, curses.COLOR_CYAN, bg)
            curses.init_pair(Palette.PANEL_EXEC, curses.COLOR_GREEN, bg)
            curses.init_pair(Palette.PANEL_SYMLINK, curses.COLOR_CYAN, bg)
            curses.init_pair(Palette.PANEL_BROKEN, curses.COLOR_RED, bg)
            curses.init_pair(Palette.PANEL_ARCHIVE, curses.COLOR_MAGENTA, bg)
            curses.init_pair(Palette.PANEL_SELECTED, curses.COLOR_YELLOW, bg)
            curses.init_pair(Palette.PANEL_CURSOR, curses.COLOR_BLACK, curses.COLOR_WHITE)
            curses.init_pair(Palette.PANEL_CURSOR_SEL, curses.COLOR_RED, curses.COLOR_WHITE)
            curses.init_pair(Palette.PANEL_COLUMN_HDR, curses.COLOR_YELLOW, bg)
            curses.init_pair(Palette.MENU_BAR, curses.COLOR_BLACK, curses.COLOR_WHITE)
            curses.init_pair(Palette.MENU_HOTKEY, curses.COLOR_RED, curses.COLOR_WHITE)
            curses.init_pair(Palette.FN_NUM, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(Palette.FN_LABEL, curses.COLOR_BLACK, curses.COLOR_GREEN)
            curses.init_pair(Palette.CMD_LINE, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(Palette.DIALOG_BG, dlg_fg, dlg_bg)
            curses.init_pair(Palette.DIALOG_TITLE, curses.COLOR_YELLOW, dlg_bg)
            curses.init_pair(Palette.DIALOG_BUTTON_ACT, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(Palette.DIALOG_INPUT, curses.COLOR_BLACK, curses.COLOR_WHITE)
            curses.init_pair(Palette.VIEWER_STATUS, curses.COLOR_BLACK, curses.COLOR_GREEN)
            curses.init_pair(Palette.SYNTAX_KEYWORD, curses.COLOR_YELLOW, bg)
            curses.init_pair(Palette.SYNTAX_STRING, curses.COLOR_GREEN, bg)
            curses.init_pair(Palette.SYNTAX_COMMENT, curses.COLOR_CYAN, bg)
            curses.init_pair(Palette.SYNTAX_NUMBER, curses.COLOR_MAGENTA, bg)
            curses.init_pair(Palette.MARK_BLOCK, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(Palette.ERROR_BG, curses.COLOR_WHITE, curses.COLOR_RED)

    def attr(self, role: int, extra: int = 0) -> int:
        if not self.has_colors:
            if role in (
                Palette.PANEL_CURSOR,
                Palette.PANEL_CURSOR_SEL,
                Palette.PANEL_HEADER_ACTIVE,
                Palette.MENU_BAR,
                Palette.FN_LABEL,
                Palette.DIALOG_BUTTON_ACT,
                Palette.VIEWER_STATUS,
                Palette.MARK_BLOCK,
            ):
                return curses.A_REVERSE | extra
            if role in (
                Palette.PANEL_DIR,
                Palette.PANEL_SELECTED,
                Palette.DIALOG_TITLE,
            ):
                return curses.A_BOLD | extra
            return extra
        return curses.color_pair(role) | extra


class BoxChars:
    def __init__(self, ascii_only: bool) -> None:
        """Initialize the instance state from the supplied arguments."""
        if ascii_only:
            self.tl, self.tr, self.bl, self.br = "+", "+", "+", "+"
            self.h, self.v = "-", "|"
            self.lt, self.rt, self.tt, self.bt = "+", "+", "+", "+"
            self.block, self.track = "#", ":"
        else:
            self.tl, self.tr, self.bl, self.br = "┌", "┐", "└", "┘"
            self.h, self.v = "─", "│"
            self.lt, self.rt, self.tt, self.bt = "├", "┤", "┬", "┴"
            self.block, self.track = "█", "░"


class Canvas:
    def __init__(self, stdscr: curses.window, theme: ThemeManager, ascii_only: bool) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.win = stdscr
        self.theme = theme
        self.box = BoxChars(ascii_only)
        self.height, self.width = stdscr.getmaxyx()

    def refresh_size(self) -> tuple[int, int]:
        self.height, self.width = self.win.getmaxyx()
        return self.height, self.width

    def set_ascii(self, ascii_only: bool) -> None:
        """Set ascii."""
        self.box = BoxChars(ascii_only)

    def draw(self, y: int, x: int, text: str, attr: int = 0) -> None:
        if y < 0 or y >= self.height or x >= self.width:
            return
        if x < 0:
            skip = -x
            skipped = 0
            idx = 0
            for i, ch in enumerate(text):
                if skipped >= skip:
                    idx = i
                    break
                skipped += char_width(ch)
            else:
                return
            text = text[idx:]
            x = 0
        avail = self.width - x
        if avail <= 0 or not text:
            return
        clipped = truncate_right(text, avail)
        if y == self.height - 1 and x + display_width(clipped) >= self.width:
            clipped = truncate_right(clipped, max(0, avail - 1))
        if not clipped:
            return
        try:
            self.win.addstr(y, x, clipped, attr)
        except curses.error:
            pass

    def fill_rect(self, y: int, x: int, h: int, w: int, attr: int, ch: str = " ") -> None:
        line = ch * max(0, w)
        for row in range(y, min(self.height, y + h)):
            self.draw(row, x, line, attr)

    def draw_box(
        self,
        y: int,
        x: int,
        h: int,
        w: int,
        attr: int,
        title: str = "",
        title_attr: int | None = None,
        fill: bool = False,
    ) -> None:
        """Draw box."""
        if h < 2 or w < 2:
            return
        if fill:
            self.fill_rect(y, x, h, w, attr)
        b = self.box
        top = b.tl + (b.h * (w - 2)) + b.tr
        bot = b.bl + (b.h * (w - 2)) + b.br
        self.draw(y, x, top, attr)
        self.draw(y + h - 1, x, bot, attr)
        for row in range(y + 1, y + h - 1):
            self.draw(row, x, b.v, attr)
            self.draw(row, x + w - 1, b.v, attr)
        if title:
            max_t = max(1, w - 4)
            t = truncate_middle(title, max_t)
            tx = x + max(1, (w - display_width(t)) // 2)
            self.draw(y, tx, f" {t} ", title_attr if title_attr is not None else attr)


def read_key(win: curses.window) -> int | tuple[str, str]:
    """Read key."""
    ch = win.getch()
    if ch == 27:
        win.nodelay(True)
        try:
            nxt = win.getch()
        finally:
            win.nodelay(False)
        if nxt == -1:
            return 27
        if nxt == 10 or nxt == 13:
            return ("ALT", "\n")
        if 0 <= nxt < 256:
            return ("ALT", chr(nxt))
        return nxt
    return ch


def configure_termios_no_ixon() -> list[Any] | None:
    """Configure termios no ixon."""
    if not sys.stdin.isatty():
        return None
    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[0] &= ~termios.IXON
        termios.tcsetattr(fd, termios.TCSANOW, new)
        return old
    except Exception:
        return None


def restore_termios(old: list[Any] | None) -> None:
    """Restore termios."""
    if old is None or not sys.stdin.isatty():
        return
    try:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, old)
    except Exception:
        pass


class TextInput:
    def __init__(self, initial: str = "", history: list[str] | None = None) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.text = initial
        self.cursor = len(initial)
        self.scroll = 0
        self.history: list[str] = history if history is not None else []
        self.hist_idx = len(self.history)

    def set_text(self, value: str) -> None:
        """Set text."""
        self.text = value
        self.cursor = len(value)
        self.scroll = 0

    def handle_key(self, key: int | tuple[str, str]) -> bool:
        """Handle key."""
        if isinstance(key, tuple):
            return False
        match key:
            case curses.KEY_LEFT:
                if self.cursor > 0:
                    self.cursor -= 1
                return True
            case curses.KEY_RIGHT:
                if self.cursor < len(self.text):
                    self.cursor += 1
                return True
            case curses.KEY_HOME | 1:
                self.cursor = 0
                return True
            case curses.KEY_END | 5:
                self.cursor = len(self.text)
                return True
            case 21:
                self.text = self.text[self.cursor :]
                self.cursor = 0
                return True
            case 11:
                self.text = self.text[: self.cursor]
                return True
            case 23:
                if self.cursor > 0:
                    i = self.cursor
                    while i > 0 and self.text[i - 1].isspace():
                        i -= 1
                    while i > 0 and not self.text[i - 1].isspace():
                        i -= 1
                    self.text = self.text[:i] + self.text[self.cursor :]
                    self.cursor = i
                return True
            case curses.KEY_BACKSPACE | 127 | 8:
                if self.cursor > 0:
                    self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
                    self.cursor -= 1
                return True
            case curses.KEY_DC:
                if self.cursor < len(self.text):
                    self.text = self.text[: self.cursor] + self.text[self.cursor + 1 :]
                return True
            case _:
                if 32 <= key <= 0x10FFFF and key != 127:
                    ch = chr(key)
                    if ch.isprintable():
                        self.text = self.text[: self.cursor] + ch + self.text[self.cursor :]
                        self.cursor += 1
                        return True
        return False

    def history_prev(self) -> None:
        if not self.history:
            return
        if self.hist_idx > 0:
            self.hist_idx -= 1
            self.set_text(self.history[self.hist_idx])

    def history_next(self) -> None:
        if not self.history:
            return
        if self.hist_idx < len(self.history) - 1:
            self.hist_idx += 1
            self.set_text(self.history[self.hist_idx])
        else:
            self.hist_idx = len(self.history)
            self.set_text("")

    def push_history(self, cmd: str) -> None:
        if cmd and (not self.history or self.history[-1] != cmd):
            self.history.append(cmd)
        self.hist_idx = len(self.history)

    def render(
        self,
        canvas: Canvas,
        y: int,
        x: int,
        width: int,
        attr: int,
        place_cursor: bool = False,
    ) -> None:
        if width <= 0:
            return
        self.scroll = min(self.scroll, self.cursor)
        while display_width(self.text[self.scroll : self.cursor]) >= width:
            self.scroll += 1
        visible = self.text[self.scroll :]
        canvas.draw(y, x, pad_right(visible, width), attr)
        if place_cursor:
            cx = x + display_width(self.text[self.scroll : self.cursor])
            cx = min(max(x, cx), x + width - 1)
            try:
                canvas.win.move(y, cx)
            except curses.error:
                pass


def draw_fn_bar(canvas: Canvas, labels: list[tuple[int, str]]) -> None:
    """Draw fn bar."""
    y = canvas.height - 1
    if y < 0:
        return
    num_attr = canvas.theme.attr(Palette.FN_NUM, curses.A_BOLD)
    lbl_attr = canvas.theme.attr(Palette.FN_LABEL)
    count = len(labels)
    if count == 0:
        return
    slot_w = max(6, canvas.width // count)
    x = 0
    for idx, (num, title) in enumerate(labels):
        w = (canvas.width - x) if idx == count - 1 else min(slot_w, canvas.width - x)
        if w <= 0:
            break
        num_str = str(num)
        canvas.draw(y, x, num_str, num_attr)
        lbl_w = max(0, w - len(num_str))
        canvas.draw(y, x + len(num_str), pad_right(title, lbl_w), lbl_attr)
        x += w


def modal_message(
    canvas: Canvas,
    title: str,
    lines: list[str],
    buttons: list[str] | None = None,
    is_error: bool = False,
) -> int:
    if buttons is None:
        buttons = ["OK"]
    theme = canvas.theme
    bg = theme.attr(Palette.ERROR_BG if is_error else Palette.DIALOG_BG)
    title_attr = theme.attr(Palette.ERROR_BG if is_error else Palette.DIALOG_TITLE, curses.A_BOLD)
    btn_act = theme.attr(Palette.DIALOG_BUTTON_ACT, curses.A_BOLD)
    btn_line_len = sum(len(b) + 5 for b in buttons)
    max_line = max((display_width(line) for line in lines), default=20)
    w = min(canvas.width - 4, max(36, max_line + 6, btn_line_len + 6, len(title) + 6))
    h = min(canvas.height - 2, len(lines) + 6)
    y = max(0, (canvas.height - h) // 2)
    x = max(0, (canvas.width - w) // 2)
    focus = 0
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    while True:
        canvas.draw_box(y, x, h, w, bg, title=title, title_attr=title_attr, fill=True)
        for i, line in enumerate(lines[: h - 5]):
            canvas.draw(y + 2 + i, x + 2, truncate_right(line, w - 4), bg)
        bx = x + max(2, (w - btn_line_len) // 2)
        by = y + h - 2
        btn_positions: list[tuple[int, int, int]] = []
        for idx, b in enumerate(buttons):
            label = f"[ {b} ]"
            attr = btn_act if idx == focus else bg
            canvas.draw(by, bx, label, attr)
            btn_positions.append((bx, bx + len(label) - 1, idx))
            bx += len(label) + 1
        canvas.win.refresh()
        key = read_key(canvas.win)
        if key in (27, curses.KEY_F10):
            return -1
        if key in (10, 13, curses.KEY_ENTER, ord(" ")):
            return focus
        if key in (curses.KEY_LEFT, curses.KEY_UP, 9):
            focus = (focus - 1) % len(buttons)
        elif key in (curses.KEY_RIGHT, curses.KEY_DOWN):
            focus = (focus + 1) % len(buttons)
        elif key == curses.KEY_MOUSE:
            try:
                _, mx, my, _, bstate = curses.getmouse()
                if my == by and (bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED)):
                    for x1, x2, idx in btn_positions:
                        if x1 <= mx <= x2:
                            return idx
            except curses.error:
                pass
        elif isinstance(key, int) and 32 <= key < 127:
            ch = chr(key).lower()
            for idx, b in enumerate(buttons):
                if b and b[0].lower() == ch:
                    return idx


def modal_input(
    canvas: Canvas,
    title: str,
    prompt: str,
    initial: str = "",
    checkboxes: list[tuple[str, bool]] | None = None,
) -> tuple[str | None, list[bool]]:
    theme = canvas.theme
    bg = theme.attr(Palette.DIALOG_BG)
    title_attr = theme.attr(Palette.DIALOG_TITLE, curses.A_BOLD)
    inp_attr = theme.attr(Palette.DIALOG_INPUT)
    btn_act = theme.attr(Palette.DIALOG_BUTTON_ACT, curses.A_BOLD)
    checks = [[lbl, val] for lbl, val in (checkboxes or [])]
    w = min(canvas.width - 4, max(48, len(prompt) + 8, len(title) + 10))
    h = 8 + len(checks)
    y = max(0, (canvas.height - h) // 2)
    x = max(0, (canvas.width - w) // 2)
    ti = TextInput(initial)
    focus = 0
    total_focus = 3 + len(checks)
    while True:
        canvas.draw_box(y, x, h, w, bg, title=title, title_attr=title_attr, fill=True)
        canvas.draw(y + 2, x + 2, truncate_right(prompt, w - 4), bg)
        ti.render(canvas, y + 3, x + 2, w - 4, inp_attr, place_cursor=(focus == 0))
        for idx, (lbl, val) in enumerate(checks):
            cy = y + 4 + idx
            mark = "[x]" if val else "[ ]"
            attr = btn_act if focus == idx + 1 else bg
            canvas.draw(cy, x + 2, f"{mark} {lbl}", attr)
        by = y + h - 2
        ok_lbl, cancel_lbl = "[ OK ]", "[ Cancel ]"
        ok_x = x + (w // 2) - len(ok_lbl) - 1
        ca_x = x + (w // 2) + 1
        canvas.draw(by, ok_x, ok_lbl, btn_act if focus == 1 + len(checks) else bg)
        canvas.draw(by, ca_x, cancel_lbl, btn_act if focus == 2 + len(checks) else bg)
        try:
            curses.curs_set(1 if focus == 0 else 0)
        except curses.error:
            pass
        if focus == 0:
            ti.render(canvas, y + 3, x + 2, w - 4, inp_attr, place_cursor=True)
        canvas.win.refresh()
        key = read_key(canvas.win)
        if key in (27, curses.KEY_F10):
            return None, [bool(c[1]) for c in checks]
        if key == 9:
            focus = (focus + 1) % total_focus
            continue
        if key == curses.KEY_BTAB:
            focus = (focus - 1) % total_focus
            continue
        if key == curses.KEY_UP:
            focus = max(0, focus - 1)
            continue
        if key == curses.KEY_DOWN:
            focus = min(total_focus - 1, focus + 1)
            continue
        if key in (10, 13, curses.KEY_ENTER):
            if focus == 2 + len(checks):
                return None, [bool(c[1]) for c in checks]
            if 1 <= focus <= len(checks):
                checks[focus - 1][1] = not checks[focus - 1][1]
                continue
            return ti.text, [bool(c[1]) for c in checks]
        if focus == 0:
            ti.handle_key(key)
        elif 1 <= focus <= len(checks) and key == ord(" "):
            checks[focus - 1][1] = not checks[focus - 1][1]
        elif focus in (1 + len(checks), 2 + len(checks)):
            if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
                focus = (2 + len(checks)) if focus == (1 + len(checks)) else (1 + len(checks))
            elif key == ord(" "):
                if focus == 2 + len(checks):
                    return None, [bool(c[1]) for c in checks]
                return ti.text, [bool(c[1]) for c in checks]


class FileViewer:
    def __init__(
        self,
        canvas: Canvas,
        title: str,
        data: bytes | None = None,
        file_path: Path | None = None,
    ) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.canvas = canvas
        self.title = title
        self.file_path = file_path
        if data is not None:
            self.raw = data
        elif file_path is not None:
            try:
                self.raw = file_path.read_bytes()
            except OSError as exc:
                self.raw = f"<Error reading file: {exc}>\n".encode()
        else:
            self.raw = b""
        sample = self.raw[:4096]
        is_binary = b"\x00" in sample
        self.hex_mode = is_binary
        self.wrap_mode = True
        self.lines: list[str] = self._decode_lines(self.raw)
        self.top_line = 0
        self.horiz_offset = 0
        self.search_query = ""
        self.last_match_line = -1

    @staticmethod
    def _decode_lines(raw: bytes) -> list[str]:
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        if len(lines) > 1 and lines[-1] == "":
            lines.pop()
        return [ln.expandtabs(4) for ln in lines] or [""]

    def _wrapped_rows(self, width: int) -> list[tuple[int, str]]:
        if not self.wrap_mode:
            return [(idx, ln) for idx, ln in enumerate(self.lines)]
        rows: list[tuple[int, str]] = []
        w = max(10, width)
        for idx, ln in enumerate(self.lines):
            if not ln:
                rows.append((idx, ""))
                continue
            cur = ln
            while cur:
                chunk = []
                cw = 0
                split_i = 0
                for i, ch in enumerate(cur):
                    wch = char_width(ch)
                    if cw + wch > w and i > 0:
                        break
                    chunk.append(ch)
                    cw += wch
                    split_i = i + 1
                rows.append((idx, "".join(chunk)))
                cur = cur[split_i:]
        return rows

    def _hex_rows(self) -> list[str]:
        rows: list[str] = []
        total = len(self.raw)
        for offset in range(0, max(1, total), 16):
            chunk = self.raw[offset : offset + 16]
            hex_left = " ".join(f"{b:02X}" for b in chunk[:8])
            hex_right = " ".join(f"{b:02X}" for b in chunk[8:])
            hex_col = f"{hex_left:<23}  {hex_right:<23}"
            ascii_col = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            rows.append(f"{offset:08X}: {hex_col} |{ascii_col}|")
        return rows

    def _do_search(self, forward: bool = True) -> None:
        if not self.search_query:
            return
        q = self.search_query.lower()
        if self.hex_mode:
            rows = self._hex_rows()
            start = max(0, self.top_line + (1 if forward else -1))
            rng = range(start, len(rows)) if forward else range(start, -1, -1)
            for idx in rng:
                if q in rows[idx].lower():
                    self.top_line = idx
                    self.last_match_line = idx
                    return
        else:
            start = max(0, self.top_line + (1 if forward else -1))
            rng = range(start, len(self.lines)) if forward else range(start, -1, -1)
            for idx in rng:
                if q in self.lines[idx].lower():
                    self.top_line = idx
                    self.last_match_line = idx
                    return
        modal_message(self.canvas, "Search", [f"Pattern not found: {self.search_query}"])

    def run(self) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        fn_labels = [
            (1, "Help"),
            (2, "Wrap"),
            (3, "Quit"),
            (4, "Hex"),
            (5, "Goto"),
            (6, ""),
            (7, "Search"),
            (8, "Raw"),
            (9, ""),
            (10, "Quit"),
        ]
        while True:
            h, w = self.canvas.refresh_size()
            theme = self.canvas.theme
            bg = theme.attr(Palette.PANEL_BG)
            hdr = theme.attr(Palette.VIEWER_STATUS)
            hl = theme.attr(Palette.PANEL_SELECTED, curses.A_BOLD)
            body_h = max(1, h - 2)
            self.canvas.fill_rect(1, 0, body_h, w, bg)
            if self.hex_mode:
                rows = self._hex_rows()
                total_rows = len(rows)
                self.top_line = max(0, min(self.top_line, max(0, total_rows - body_h)))
                pct = 100 if total_rows <= body_h else int((self.top_line + body_h) * 100 / total_rows)
                status = f" {truncate_middle(self.title, max(10, w - 42))} | {len(self.raw)} B | Hex | {min(100, pct)}%"
                self.canvas.draw(0, 0, pad_right(status, w), hdr)
                for r in range(body_h):
                    idx = self.top_line + r
                    if idx >= total_rows:
                        break
                    line = rows[idx][self.horiz_offset :]
                    self.canvas.draw(1 + r, 0, line, bg)
            else:
                wrapped = self._wrapped_rows(w)
                total_rows = len(wrapped)
                self.top_line = max(0, min(self.top_line, max(0, total_rows - body_h)))
                cur_src_line = wrapped[self.top_line][0] + 1 if wrapped else 1
                pct = 100 if total_rows <= body_h else int((self.top_line + body_h) * 100 / total_rows)
                mode_tag = "Wrap" if self.wrap_mode else "Unwrap"
                status = (
                    f" {truncate_middle(self.title, max(10, w - 48))} | "
                    f"Ln {cur_src_line}/{len(self.lines)} | {len(self.raw)} B | {mode_tag} | {min(100, pct)}%"
                )
                self.canvas.draw(0, 0, pad_right(status, w), hdr)
                for r in range(body_h):
                    idx = self.top_line + r
                    if idx >= total_rows:
                        break
                    _src_idx, txt = wrapped[idx]
                    visible = txt if self.wrap_mode else txt[self.horiz_offset :]
                    attr = hl if (self.search_query and self.search_query.lower() in visible.lower()) else bg
                    self.canvas.draw(1 + r, 0, visible, attr)
            draw_fn_bar(self.canvas, fn_labels)
            self.canvas.win.refresh()
            key = read_key(self.canvas.win)
            if key in (27, curses.KEY_F3, curses.KEY_F10, ord("q"), ord("Q")):
                return
            if key == curses.KEY_RESIZE:
                continue
            if key in (curses.KEY_UP, ord("k")):
                self.top_line = max(0, self.top_line - 1)
            elif key in (curses.KEY_DOWN, ord("j"), 10, 13):
                self.top_line += 1
            elif key in (curses.KEY_PPAGE, ord("b")):
                self.top_line = max(0, self.top_line - max(1, body_h - 1))
            elif key in (curses.KEY_NPAGE, ord(" ")):
                self.top_line += max(1, body_h - 1)
            elif key in (curses.KEY_HOME, ord("g")):
                self.top_line = 0
                self.horiz_offset = 0
            elif key in (curses.KEY_END, ord("G")):
                self.top_line = 10**9
            elif key == curses.KEY_LEFT:
                self.horiz_offset = max(0, self.horiz_offset - 4)
            elif key == curses.KEY_RIGHT:
                if not self.wrap_mode or self.hex_mode:
                    self.horiz_offset += 4
            elif key == curses.KEY_F2:
                self.wrap_mode = not self.wrap_mode
                self.horiz_offset = 0
            elif key == curses.KEY_F4:
                self.hex_mode = not self.hex_mode
                self.top_line = 0
                self.horiz_offset = 0
            elif key == curses.KEY_F5:
                val, _ = modal_input(
                    self.canvas,
                    "Goto",
                    "Enter hex offset (0x...) or line number:" if self.hex_mode else "Enter line number:",
                )
                if val:
                    try:
                        if val.lower().startswith("0x") or self.hex_mode:
                            off = int(val, 16 if val.lower().startswith("0x") else 10)
                            self.top_line = max(0, off // 16 if self.hex_mode else off - 1)
                        else:
                            self.top_line = max(0, int(val) - 1)
                    except ValueError:
                        modal_message(self.canvas, "Goto", ["Invalid number."], is_error=True)
            elif key in (curses.KEY_F7, ord("/")):
                q, _ = modal_input(self.canvas, "Search", "Search text:", initial=self.search_query)
                if q is not None:
                    self.search_query = q
                    self._do_search(forward=True)
            elif key in (ord("n"), curses.KEY_F17):
                self._do_search(forward=True)
            elif key == ord("N"):
                self._do_search(forward=False)
            elif key == curses.KEY_F1:
                modal_message(
                    self.canvas,
                    "Viewer Help",
                    [
                        "Up/Down, PgUp/PgDn, Home/End : Scroll",
                        "F2                           : Toggle Wrap/Unwrap",
                        "F4                           : Toggle Text / Hex Dump",
                        "F5                           : Goto line or hex offset",
                        "F7 or /                      : Search text",
                        "n / N                        : Next / Previous match",
                        "F3 / F10 / Esc / q           : Exit viewer",
                    ],
                )


_PY_KEYWORDS = frozenset({
    "def",
    "class",
    "return",
    "if",
    "elif",
    "else",
    "for",
    "while",
    "break",
    "continue",
    "import",
    "from",
    "as",
    "try",
    "except",
    "finally",
    "raise",
    "with",
    "yield",
    "async",
    "await",
    "match",
    "case",
    "type",
    "lambda",
    "pass",
    "True",
    "False",
    "None",
    "and",
    "or",
    "not",
    "in",
    "is",
    "function",
    "const",
    "let",
    "var",
    "struct",
    "enum",
    "fn",
    "pub",
})


class TextEditor:
    def __init__(self, canvas: Canvas, file_path: Path) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.canvas = canvas
        self.file_path = file_path
        self.lines: list[str] = [""]
        self.modified = False
        self.read_only = False
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                self.lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                if not self.lines:
                    self.lines = [""]
            except OSError as exc:
                self.lines = [f"# Could not open file: {exc}"]
                self.read_only = True
        self.cy = 0
        self.cx = 0
        self.scroll_y = 0
        self.scroll_x = 0
        self.overwrite = False
        self.mark_start: int | None = None
        self.mark_end: int | None = None
        self.undo_stack: list[tuple[list[str], int, int]] = []
        self.last_search = ""
        self.status_note = ""

    def _snapshot(self) -> None:
        self.undo_stack.append((list(self.lines), self.cy, self.cx))
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self.modified = True

    def _undo(self) -> None:
        if not self.undo_stack:
            self.status_note = "Nothing to undo"
            return
        lines, cy, cx = self.undo_stack.pop()
        self.lines = lines
        self.cy = min(cy, len(self.lines) - 1)
        self.cx = min(cx, len(self.lines[self.cy]))
        self.status_note = "Undid last edit"

    def _marked_range(self) -> tuple[int, int] | None:
        if self.mark_start is None:
            return None
        end = self.cy if self.mark_end is None else self.mark_end
        lo, hi = min(self.mark_start, end), max(self.mark_start, end)
        return lo, min(hi, len(self.lines) - 1)

    def _save(self) -> bool:
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.file_path, "\n".join(self.lines), encoding="utf-8")
            self.modified = False
            self.status_note = f"Saved {self.file_path.name}"
            return True
        except OSError as exc:
            modal_message(self.canvas, "Save Error", [str(exc)], is_error=True)
            return False

    def _draw_highlighted_line(self, y: int, x: int, width: int, line: str, is_marked: bool) -> None:
        theme = self.canvas.theme
        base = theme.attr(Palette.MARK_BLOCK if is_marked else Palette.PANEL_BG)
        if is_marked or not theme.has_colors:
            visible = line[self.scroll_x :]
            self.canvas.draw(y, x, pad_right(visible, width), base)
            return
        kw_attr = theme.attr(Palette.SYNTAX_KEYWORD, curses.A_BOLD)
        str_attr = theme.attr(Palette.SYNTAX_STRING)
        cmt_attr = theme.attr(Palette.SYNTAX_COMMENT)
        num_attr = theme.attr(Palette.SYNTAX_NUMBER)
        self.canvas.draw(y, x, " " * width, base)
        token_re = re.compile(
            r'(#.*$|//.*$)|("(?:\\.|[^"])*"|\'(?:\\.|[^\'])*\')|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_][A-Za-z0-9_]*)\b'
        )
        col_pos = 0
        last_end = 0

        def emit_segment(seg: str, attr: int) -> None:
            nonlocal col_pos
            if not seg:
                return
            seg_end = col_pos + len(seg)
            if seg_end > self.scroll_x and col_pos < self.scroll_x + width:
                s_start = max(0, self.scroll_x - col_pos)
                s_end = min(len(seg), self.scroll_x + width - col_pos)
                draw_x = x + max(0, col_pos - self.scroll_x)
                self.canvas.draw(y, draw_x, seg[s_start:s_end], attr)
            col_pos = seg_end

        for m in token_re.finditer(line):
            if m.start() > last_end:
                emit_segment(line[last_end : m.start()], base)
            tok = m.group(0)
            if m.group(1):
                attr = cmt_attr
            elif m.group(2):
                attr = str_attr
            elif m.group(3):
                attr = num_attr
            elif m.group(4) in _PY_KEYWORDS:
                attr = kw_attr
            else:
                attr = base
            emit_segment(tok, attr)
            last_end = m.end()
        if last_end < len(line):
            emit_segment(line[last_end:], base)

    def run(self) -> None:
        fn_labels = [
            (1, "Help"),
            (2, "Save"),
            (3, "Mark"),
            (4, "Replac"),
            (5, "Copy"),
            (6, "Move"),
            (7, "Search"),
            (8, "Delete"),
            (9, "Undo"),
            (10, "Quit"),
        ]
        while True:
            h, w = self.canvas.refresh_size()
            theme = self.canvas.theme
            bg = theme.attr(Palette.PANEL_BG)
            hdr = theme.attr(Palette.VIEWER_STATUS)
            body_h = max(1, h - 2)
            self.cy = max(0, min(self.cy, len(self.lines) - 1))
            self.cx = max(0, min(self.cx, len(self.lines[self.cy])))
            if self.cy < self.scroll_y:
                self.scroll_y = self.cy
            elif self.cy >= self.scroll_y + body_h:
                self.scroll_y = self.cy - body_h + 1
            gutter_w = max(5, len(str(len(self.lines))) + 2)
            text_w = max(10, w - gutter_w)
            if self.cx < self.scroll_x:
                self.scroll_x = self.cx
            elif self.cx >= self.scroll_x + text_w:
                self.scroll_x = self.cx - text_w + 1
            mod_flag = "[Mod]" if self.modified else "     "
            ins_flag = "OVR" if self.overwrite else "INS"
            cur_ord = ord(self.lines[self.cy][self.cx]) if self.cx < len(self.lines[self.cy]) else 10
            note = f" | {self.status_note}" if self.status_note else ""
            status = (
                f" {truncate_middle(str(self.file_path.name), max(10, w - 58))} "
                f"{mod_flag} Ln {self.cy + 1}/{len(self.lines)} Col {self.cx + 1} "
                f"0x{cur_ord:02X} {ins_flag}{note}"
            )
            self.canvas.draw(0, 0, pad_right(status, w), hdr)
            marked = self._marked_range()
            gutter_attr = theme.attr(Palette.PANEL_COLUMN_HDR)
            for row in range(body_h):
                li = self.scroll_y + row
                if li < len(self.lines):
                    self.canvas.draw(1 + row, 0, f"{li + 1:>{gutter_w - 1}} ", gutter_attr)
                    is_m = marked is not None and marked[0] <= li <= marked[1]
                    self._draw_highlighted_line(1 + row, gutter_w, text_w, self.lines[li], is_m)
                else:
                    self.canvas.draw(1 + row, 0, pad_right("~", w), bg)
            draw_fn_bar(self.canvas, fn_labels)
            try:
                curses.curs_set(1)
                self.canvas.win.move(1 + self.cy - self.scroll_y, gutter_w + self.cx - self.scroll_x)
            except curses.error:
                pass
            self.canvas.win.refresh()
            self.status_note = ""
            key = read_key(self.canvas.win)
            if key == curses.KEY_RESIZE:
                continue
            if key in (27, curses.KEY_F10):
                if self.modified:
                    res = modal_message(
                        self.canvas,
                        "Close Editor",
                        [f"Save changes to {self.file_path.name}?"],
                        buttons=["Yes", "No", "Cancel"],
                    )
                    if res == 0:
                        if self._save():
                            return
                    elif res == 1:
                        return
                else:
                    return
                continue
            if key == curses.KEY_F1:
                modal_message(
                    self.canvas,
                    "Editor Help",
                    [
                        "F2       : Save file",
                        "F3       : Mark / unmark line block",
                        "F4       : Search and Replace",
                        "F5 / F6  : Copy / Move marked block here",
                        "F7       : Search text",
                        "F8       : Delete marked block or current line",
                        "F9/Ctrl+Z: Undo",
                        "F10/Esc  : Quit editor",
                    ],
                )
            elif key == curses.KEY_F2 or key == 19:
                self._save()
            elif key == curses.KEY_F3:
                if self.mark_start is None:
                    self.mark_start = self.cy
                    self.mark_end = None
                    self.status_note = f"Mark started at line {self.cy + 1}"
                elif self.mark_end is None:
                    self.mark_end = self.cy
                    self.status_note = "Block marked"
                else:
                    self.mark_start = None
                    self.mark_end = None
                    self.status_note = "Mark cleared"
            elif key == curses.KEY_F4:
                find_q, _ = modal_input(self.canvas, "Replace", "Find text:", initial=self.last_search)
                if find_q:
                    repl_q, _ = modal_input(self.canvas, "Replace", f"Replace '{find_q}' with:")
                    if repl_q is not None:
                        self._snapshot()
                        count = 0
                        for i in range(len(self.lines)):
                            if find_q in self.lines[i]:
                                count += self.lines[i].count(find_q)
                                self.lines[i] = self.lines[i].replace(find_q, repl_q)
                        self.last_search = find_q
                        self.status_note = f"Replaced {count} occurrence(s)"
            elif key == curses.KEY_F5 and marked is not None:
                lo, hi = marked
                self._snapshot()
                block = list(self.lines[lo : hi + 1])
                self.lines[self.cy : self.cy] = block
                self.mark_start = self.mark_end = None
                self.status_note = f"Copied {len(block)} line(s)"
            elif key == curses.KEY_F6 and marked is not None:
                lo, hi = marked
                self._snapshot()
                block = list(self.lines[lo : hi + 1])
                del self.lines[lo : hi + 1]
                insert_at = self.cy - len(block) if self.cy > hi else self.cy
                insert_at = max(0, min(insert_at, len(self.lines)))
                self.lines[insert_at:insert_at] = block
                self.cy = insert_at
                self.mark_start = self.mark_end = None
                self.status_note = f"Moved {len(block)} line(s)"
            elif key == curses.KEY_F7:
                q, _ = modal_input(self.canvas, "Search", "Search text:", initial=self.last_search)
                if q:
                    self.last_search = q
                    found = False
                    for offset in range(1, len(self.lines) + 1):
                        idx = (self.cy + offset) % len(self.lines)
                        pos = self.lines[idx].lower().find(q.lower())
                        if pos != -1:
                            self.cy = idx
                            self.cx = pos
                            found = True
                            break
                    if not found:
                        self.status_note = f"Not found: {q}"
            elif key == curses.KEY_F8:
                self._snapshot()
                if marked is not None:
                    lo, hi = marked
                    del self.lines[lo : hi + 1]
                    if not self.lines:
                        self.lines = [""]
                    self.cy = min(lo, len(self.lines) - 1)
                    self.mark_start = self.mark_end = None
                else:
                    if len(self.lines) > 1:
                        del self.lines[self.cy]
                        self.cy = min(self.cy, len(self.lines) - 1)
                    else:
                        self.lines[0] = ""
                    self.cx = min(self.cx, len(self.lines[self.cy]))
            elif key in (curses.KEY_F9, 26, 21):
                self._undo()
            elif key == curses.KEY_IC:
                self.overwrite = not self.overwrite
            elif key == curses.KEY_UP:
                self.cy = max(0, self.cy - 1)
            elif key == curses.KEY_DOWN:
                self.cy = min(len(self.lines) - 1, self.cy + 1)
            elif key == curses.KEY_LEFT:
                if self.cx > 0:
                    self.cx -= 1
                elif self.cy > 0:
                    self.cy -= 1
                    self.cx = len(self.lines[self.cy])
            elif key == curses.KEY_RIGHT:
                if self.cx < len(self.lines[self.cy]):
                    self.cx += 1
                elif self.cy < len(self.lines) - 1:
                    self.cy += 1
                    self.cx = 0
            elif key in (curses.KEY_HOME, 1):
                self.cx = 0
            elif key in (curses.KEY_END, 5):
                self.cx = len(self.lines[self.cy])
            elif key == curses.KEY_PPAGE:
                self.cy = max(0, self.cy - body_h)
            elif key == curses.KEY_NPAGE:
                self.cy = min(len(self.lines) - 1, self.cy + body_h)
            elif not self.read_only:
                if key in (10, 13, curses.KEY_ENTER):
                    self._snapshot()
                    cur = self.lines[self.cy]
                    indent = ""
                    for ch in cur:
                        if ch in (" ", "\t"):
                            indent += ch
                        else:
                            break
                    self.lines[self.cy] = cur[: self.cx]
                    self.lines.insert(self.cy + 1, indent + cur[self.cx :])
                    self.cy += 1
                    self.cx = len(indent)
                elif key == 9:
                    self._snapshot()
                    cur = self.lines[self.cy]
                    self.lines[self.cy] = cur[: self.cx] + "    " + cur[self.cx :]
                    self.cx += 4
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    self._snapshot()
                    if self.cx > 0:
                        cur = self.lines[self.cy]
                        self.lines[self.cy] = cur[: self.cx - 1] + cur[self.cx :]
                        self.cx -= 1
                    elif self.cy > 0:
                        prev_len = len(self.lines[self.cy - 1])
                        self.lines[self.cy - 1] += self.lines[self.cy]
                        del self.lines[self.cy]
                        self.cy -= 1
                        self.cx = prev_len
                elif key == curses.KEY_DC:
                    self._snapshot()
                    cur = self.lines[self.cy]
                    if self.cx < len(cur):
                        self.lines[self.cy] = cur[: self.cx] + cur[self.cx + 1 :]
                    elif self.cy < len(self.lines) - 1:
                        self.lines[self.cy] += self.lines[self.cy + 1]
                        del self.lines[self.cy + 1]
                elif isinstance(key, int) and 32 <= key <= 0x10FFFF and key != 127:
                    ch = chr(key)
                    if ch.isprintable():
                        self._snapshot()
                        cur = self.lines[self.cy]
                        if self.overwrite and self.cx < len(cur):
                            self.lines[self.cy] = cur[: self.cx] + ch + cur[self.cx + 1 :]
                        else:
                            self.lines[self.cy] = cur[: self.cx] + ch + cur[self.cx :]
                        self.cx += 1


class MidnightCommander:
    def __init__(
        self,
        stdscr: curses.window,
        left_dir: Path,
        right_dir: Path,
        config: AppConfig,
    ) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.stdscr = stdscr
        self.config = config
        self.theme = ThemeManager(config.theme)
        self.canvas = Canvas(stdscr, self.theme, config.ascii_lines)
        self.left: PanelState = PanelState(side="left", cwd=left_dir, show_hidden=config.show_hidden)
        self.right: PanelState = PanelState(side="right", cwd=right_dir, show_hidden=config.show_hidden)
        self.active_side: Literal["left", "right"] = "left"
        self.cmdline = TextInput("")
        self.cmd_history: list[str] = []
        self.cmdline.history = self.cmd_history
        self.last_command_output: str = ""
        self.quick_search_active = False
        self.status_message = ""
        self.running = True
        self._last_click_time = 0.0
        self._last_click_row = -1
        self._last_click_side = ""
        self._init_curses()
        self.rescan_all()

    def _init_curses(self) -> None:
        curses.noecho()
        curses.cbreak()
        self.stdscr.keypad(True)
        try:
            curses.set_escdelay(25)
        except Exception:
            pass
        if self.config.mouse_enabled:
            try:
                curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
            except curses.error:
                pass
        else:
            try:
                curses.mousemask(0)
            except curses.error:
                pass

    @property
    def active(self) -> PanelState:
        """Return the active."""
        return self.left if self.active_side == "left" else self.right

    @property
    def inactive(self) -> PanelState:
        """Return the inactive."""
        return self.right if self.active_side == "left" else self.left

    def rescan_panel(self, panel: PanelState, preserve_name: str | None = None) -> None:
        prev_name = preserve_name
        if prev_name is None:
            cur = panel.current_entry()
            prev_name = cur.name if cur else None
        if panel.vfs is not None:
            entries, err = scan_vfs_directory(panel)
        else:
            entries, err = scan_local_directory(panel)
        panel.entries = entries
        valid_names = {e.name for e in entries}
        panel.selected &= valid_names
        if prev_name:
            for idx, e in enumerate(entries):
                if e.name == prev_name:
                    panel.cursor = idx
                    break
            else:
                panel.cursor = min(panel.cursor, max(0, len(entries) - 1))
        else:
            panel.cursor = 0
        if err:
            self.status_message = f"Warning: {err}"

    def rescan_all(self) -> None:
        self.rescan_panel(self.left)
        self.rescan_panel(self.right)

    def _layout(self) -> tuple[int, int, int, int]:
        h, w = self.canvas.refresh_size()
        panel_top = 1
        panel_bottom = max(panel_top + 4, h - 2)
        panel_h = panel_bottom - panel_top
        left_w = w // 2
        right_w = w - left_w
        return panel_top, panel_h, left_w, right_w

    def _entry_attr(self, entry: FileEntry, is_cursor: bool, is_selected: bool) -> int:
        if is_cursor:
            return self.theme.attr(
                Palette.PANEL_CURSOR_SEL if is_selected else Palette.PANEL_CURSOR,
                curses.A_BOLD if is_selected else 0,
            )
        if is_selected:
            return self.theme.attr(Palette.PANEL_SELECTED, curses.A_BOLD)
        match entry.kind:
            case FileKind.PARENT | FileKind.DIR:
                return self.theme.attr(Palette.PANEL_DIR, curses.A_BOLD)
            case FileKind.EXEC:
                return self.theme.attr(Palette.PANEL_EXEC, curses.A_BOLD)
            case FileKind.SYMLINK:
                return self.theme.attr(Palette.PANEL_SYMLINK)
            case FileKind.BROKEN_LINK:
                return self.theme.attr(Palette.PANEL_BROKEN)
            case FileKind.ARCHIVE:
                return self.theme.attr(Palette.PANEL_ARCHIVE, curses.A_BOLD)
            case _:
                return self.theme.attr(Palette.PANEL_BG)

    def _ensure_scroll(self, panel: PanelState, visible_rows: int) -> None:
        if visible_rows <= 0:
            panel.scroll = 0
            return
        if panel.listing_format == ListingFormat.BRIEF:
            cols = 2
            per_page = visible_rows * cols
            if panel.cursor < panel.scroll:
                panel.scroll = (panel.cursor // visible_rows) * visible_rows
            elif panel.cursor >= panel.scroll + per_page:
                panel.scroll = ((panel.cursor // visible_rows) - (cols - 1)) * visible_rows
            panel.scroll = max(0, panel.scroll)
        else:
            if panel.cursor < panel.scroll:
                panel.scroll = panel.cursor
            elif panel.cursor >= panel.scroll + visible_rows:
                panel.scroll = panel.cursor - visible_rows + 1
            panel.scroll = max(0, panel.scroll)

    def _draw_menu_bar(self, width: int) -> None:
        bar_attr = self.theme.attr(Palette.MENU_BAR)
        self.canvas.draw(0, 0, " " * width, bar_attr)
        items = [
            ("Left", 2),
            ("File", 10),
            ("Command", 18),
            ("Options", 29),
            ("Right", 40),
        ]
        for label, x in items:
            if x < width:
                self.canvas.draw(0, x, f" {label} ", bar_attr)

    def _draw_panel(self, panel: PanelState, top: int, x: int, h: int, w: int) -> None:
        if w < 12 or h < 6:
            return
        is_active = panel.side == self.active_side
        bg = self.theme.attr(Palette.PANEL_BG)
        col_hdr_attr = self.theme.attr(Palette.PANEL_COLUMN_HDR, curses.A_BOLD)
        hdr_attr = self.theme.attr(
            Palette.PANEL_HEADER_ACTIVE if is_active else Palette.PANEL_HEADER_INACTIVE,
            curses.A_BOLD if is_active else 0,
        )
        self.canvas.draw_box(top, x, h, w, bg, fill=True)
        path_label = panel.display_cwd()
        if panel.filter_glob and panel.filter_glob != "*":
            path_label += f" ({panel.filter_glob})"
        max_title = max(6, w - 6)
        title_text = f" {truncate_middle(path_label, max_title)} "
        self.canvas.draw(top, x + 2, title_text, hdr_attr)
        if panel.mode == PanelMode.INFO:
            self._draw_info_panel(panel, top, x, h, w)
            return
        if panel.mode == PanelMode.QUICK_VIEW:
            self._draw_quick_view_panel(panel, top, x, h, w)
            return
        inner_w = w - 2
        list_top = top + 2
        mini_status_y = top + h - 2
        divider_y = top + h - 3
        list_rows = max(1, divider_y - list_top)
        self._ensure_scroll(panel, list_rows)
        b = self.canvas.box
        if panel.listing_format == ListingFormat.BRIEF:
            col_w = max(8, inner_w // 2)
            self.canvas.draw(
                top + 1,
                x + 1,
                pad_right("Name", col_w) + b.v + pad_right("Name", inner_w - col_w - 1),
                col_hdr_attr,
            )
            for row in range(list_rows):
                self.canvas.draw(list_top + row, x + 1 + col_w, b.v, bg)
                for col in range(2):
                    idx = panel.scroll + col * list_rows + row
                    if idx >= len(panel.entries):
                        continue
                    e = panel.entries[idx]
                    is_cur = is_active and idx == panel.cursor
                    is_sel = e.name in panel.selected
                    attr = self._entry_attr(e, is_cur, is_sel)
                    cw = col_w if col == 0 else (inner_w - col_w - 1)
                    cx = x + 1 + (0 if col == 0 else col_w + 1)
                    self.canvas.draw(
                        list_top + row,
                        cx,
                        pad_right(f"{e.prefix_sigil}{e.name}", cw),
                        attr,
                    )
        elif panel.listing_format == ListingFormat.LONG:
            fixed = 10 + 1 + 3 + 1 + 8 + 1 + 7 + 1 + 12 + 1
            name_w = max(8, inner_w - fixed)
            hdr_line = (
                pad_right("Name", name_w)
                + " "
                + pad_left("Size", 7)
                + " "
                + pad_right("Perm", 10)
                + " "
                + pad_right("Owner", 8)
                + " "
                + pad_right("MTime", 12)
            )
            self.canvas.draw(top + 1, x + 1, pad_right(hdr_line, inner_w), col_hdr_attr)
            for row in range(list_rows):
                idx = panel.scroll + row
                if idx >= len(panel.entries):
                    break
                e = panel.entries[idx]
                is_cur = is_active and idx == panel.cursor
                is_sel = e.name in panel.selected
                attr = self._entry_attr(e, is_cur, is_sel)
                sz = (
                    "UP--DIR"
                    if e.kind == FileKind.PARENT
                    else ("SUB-DIR" if e.kind == FileKind.DIR else format_size_compact(e.size, 7))
                )
                perm = stat.filemode(e.mode) if e.mode else "----------"
                own = lookup_user(e.uid)[:8]
                mt = format_mtime(e.mtime)
                line = (
                    pad_right(f"{e.prefix_sigil}{e.name}", name_w)
                    + " "
                    + pad_left(sz, 7)
                    + " "
                    + pad_right(perm, 10)
                    + " "
                    + pad_right(own, 8)
                    + " "
                    + pad_right(mt, 12)
                )
                self.canvas.draw(list_top + row, x + 1, pad_right(line, inner_w), attr)
        else:
            size_w = 7
            time_w = 12
            name_w = max(8, inner_w - size_w - time_w - 2)
            sort_arrow = "v" if not panel.sort_reverse else "^"
            n_hdr = f".{sort_arrow}Name" if panel.sort_field == SortField.NAME else "Name"
            s_hdr = f"{sort_arrow}Size" if panel.sort_field == SortField.SIZE else "Size"
            t_hdr = f"{sort_arrow}Modify time" if panel.sort_field == SortField.MTIME else "Modify time"
            hdr_line = pad_right(n_hdr, name_w) + b.v + pad_left(s_hdr, size_w) + b.v + pad_right(t_hdr, time_w)
            self.canvas.draw(top + 1, x + 1, hdr_line, col_hdr_attr)
            for row in range(list_rows):
                idx = panel.scroll + row
                if idx < len(panel.entries):
                    e = panel.entries[idx]
                    is_cur = is_active and idx == panel.cursor
                    is_sel = e.name in panel.selected
                    attr = self._entry_attr(e, is_cur, is_sel)
                    if e.kind == FileKind.PARENT:
                        sz = "UP--DIR"
                    elif e.kind == FileKind.DIR:
                        sz = "SUB-DIR"
                    else:
                        sz = format_size_compact(e.size, size_w)
                    mt = format_mtime(e.mtime)
                    line = (
                        pad_right(f"{e.prefix_sigil}{e.name}", name_w)
                        + b.v
                        + pad_left(sz, size_w)
                        + b.v
                        + pad_right(mt, time_w)
                    )
                    self.canvas.draw(list_top + row, x + 1, line, attr)
                else:
                    empty_line = (" " * name_w) + b.v + (" " * size_w) + b.v + (" " * time_w)
                    self.canvas.draw(list_top + row, x + 1, empty_line, bg)
        total = len(panel.entries)
        if total > list_rows > 2:
            thumb_y = list_top + int(panel.cursor * (list_rows - 1) / max(1, total - 1))
            for r in range(list_rows):
                ch = b.block if (list_top + r) == thumb_y else b.track
                self.canvas.draw(list_top + r, x + w - 1, ch, bg)
        self.canvas.draw(divider_y, x, b.lt + (b.h * inner_w) + b.rt, bg)
        if panel.selected:
            sel_bytes = sum(e.size for e in panel.entries if e.name in panel.selected)
            mini = f"{format_human_bytes(sel_bytes)} in {len(panel.selected)} file(s)"
            mini_attr = self.theme.attr(Palette.PANEL_SELECTED, curses.A_BOLD)
        else:
            cur = panel.current_entry()
            if cur is None:
                mini = ""
            elif cur.link_target is not None:
                mini = f"-> {cur.link_target}"
            elif cur.kind == FileKind.PARENT:
                mini = "UP--DIR"
            else:
                perm = stat.filemode(cur.mode) if cur.mode else ""
                own = f"{lookup_user(cur.uid)}:{lookup_group(cur.gid)}"
                mini = f"{cur.name}  {perm}  {own}"
            mini_attr = bg
        self.canvas.draw(mini_status_y, x + 1, pad_right(mini, inner_w), mini_attr)
        if is_active and self.quick_search_active:
            qs = f" Search: {panel.quick_search}_ "
            self.canvas.draw(top + h - 1, x + 2, truncate_right(qs, w - 4), hdr_attr)

    def _draw_info_panel(self, panel: PanelState, top: int, x: int, h: int, w: int) -> None:
        other = self.right if panel.side == "left" else self.left
        bg = self.theme.attr(Palette.PANEL_BG)
        hi = self.theme.attr(Palette.PANEL_SELECTED, curses.A_BOLD)
        cur = other.current_entry()
        inner_w = w - 2
        lines: list[tuple[str, int]] = [
            (f" Midnight Commander {APP_VERSION}", hi),
            ("", bg),
        ]
        if cur is not None:
            lines.append((f" Entry: {cur.name}", bg))
            lines.append((f" Kind : {cur.kind.value}", bg))
            lines.append((f" Size : {cur.size} bytes ({format_human_bytes(cur.size)})", bg))
            if cur.mode:
                lines.append((
                    f" Mode : {stat.filemode(cur.mode)} (0{stat.S_IMODE(cur.mode):03o})",
                    bg,
                ))
            lines.append((f" Owner: {lookup_user(cur.uid)}:{lookup_group(cur.gid)}", bg))
            lines.append((f" Links: {cur.nlink}", bg))
            if cur.mtime:
                dt = datetime.datetime.fromtimestamp(cur.mtime).strftime("%Y-%m-%d %H:%M:%S")
                lines.append((f" MTime: {dt}", bg))
            if cur.link_target:
                lines.append((f" Target: -> {cur.link_target}", bg))
        lines.append(("", bg))
        try:
            vfs = os.statvfs(other.cwd)
            total_b = vfs.f_frsize * vfs.f_blocks
            free_b = vfs.f_frsize * vfs.f_bavail
            pct = int((total_b - free_b) * 100 / total_b) if total_b else 0
            lines.append((f" Filesystem: {other.cwd}", hi))
            lines.append((
                f" Free space: {format_human_bytes(free_b)} / {format_human_bytes(total_b)} ({100 - pct}% free)",
                bg,
            ))
            lines.append((f" Free inodes: {vfs.f_favail} / {vfs.f_files}", bg))
        except OSError:
            pass
        for idx, (text, attr) in enumerate(lines[: max(0, h - 2)]):
            self.canvas.draw(top + 1 + idx, x + 1, pad_right(text, inner_w), attr)

    def _draw_quick_view_panel(self, panel: PanelState, top: int, x: int, h: int, w: int) -> None:
        other = self.right if panel.side == "left" else self.left
        bg = self.theme.attr(Palette.PANEL_BG)
        hi = self.theme.attr(Palette.PANEL_COLUMN_HDR, curses.A_BOLD)
        cur = other.current_entry()
        inner_w = w - 2
        max_rows = max(0, h - 2)
        if cur is None or cur.kind == FileKind.PARENT:
            self.canvas.draw(top + 1, x + 1, pad_right(" No file selected", inner_w), bg)
            return
        if cur.is_dir_like:
            self.canvas.draw(top + 1, x + 1, pad_right(f" Directory: {cur.name}", inner_w), hi)
            try:
                items = list(os.scandir(cur.path))
                self.canvas.draw(top + 2, x + 1, pad_right(f" Items: {len(items)}", inner_w), bg)
                for i, it in enumerate(items[: max(0, max_rows - 3)]):
                    prefix = "/" if it.is_dir(follow_symlinks=False) else " "
                    self.canvas.draw(
                        top + 4 + i,
                        x + 1,
                        pad_right(f"  {prefix}{safe_str(it.name)}", inner_w),
                        bg,
                    )
            except OSError as exc:
                self.canvas.draw(top + 2, x + 1, pad_right(f" Error: {exc}", inner_w), bg)
            return
        try:
            if other.vfs is not None and cur.vfs_member:
                raw = read_vfs_bytes(other.vfs, cur.vfs_member, max_bytes=4096)
            else:
                with open(cur.path, "rb") as fp:
                    raw = fp.read(4096)
        except Exception as exc:
            self.canvas.draw(top + 1, x + 1, pad_right(f" Read error: {exc}", inner_w), bg)
            return
        if b"\x00" in raw:
            self.canvas.draw(
                top + 1,
                x + 1,
                pad_right(f" Binary preview ({cur.size} B):", inner_w),
                hi,
            )
            for row in range(max(0, max_rows - 2)):
                off = row * 8
                chunk = raw[off : off + 8]
                if not chunk:
                    break
                hx = " ".join(f"{b:02X}" for b in chunk)
                asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                self.canvas.draw(
                    top + 2 + row,
                    x + 1,
                    pad_right(f" {off:04X}: {hx:<23} {asc}", inner_w),
                    bg,
                )
        else:
            lines = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
            for row, ln in enumerate(lines[:max_rows]):
                self.canvas.draw(top + 1 + row, x + 1, pad_right(ln.expandtabs(4), inner_w), bg)

    def render(self) -> None:
        h, w = self.canvas.refresh_size()
        panel_top, panel_h, left_w, right_w = self._layout()
        self._draw_menu_bar(w)
        self._draw_panel(self.left, panel_top, 0, panel_h, left_w)
        self._draw_panel(self.right, panel_top, left_w, panel_h, right_w)
        cmd_y = h - 2
        if self.status_message:
            msg_attr = self.theme.attr(Palette.VIEWER_STATUS)
            self.canvas.draw(cmd_y, 0, pad_right(f" {self.status_message}", w), msg_attr)
        else:
            cmd_attr = self.theme.attr(Palette.CMD_LINE)
            prompt = f"{self.active.display_cwd()}$ "
            prompt = truncate_middle(prompt, max(8, w // 3))
            self.canvas.draw(cmd_y, 0, prompt, cmd_attr)
            self.cmdline.render(
                self.canvas,
                cmd_y,
                display_width(prompt),
                max(1, w - display_width(prompt)),
                cmd_attr,
            )
        fn_labels = [
            (1, "Help"),
            (2, "Menu"),
            (3, "View"),
            (4, "Edit"),
            (5, "Copy"),
            (6, "RenMov"),
            (7, "Mkdir"),
            (8, "Delete"),
            (9, "PullDn"),
            (10, "Quit"),
        ]
        draw_fn_bar(self.canvas, fn_labels)
        if not self.status_message:
            prompt_w = display_width(truncate_middle(f"{self.active.display_cwd()}$ ", max(8, w // 3)))
            try:
                curses.curs_set(1)
                self.cmdline.render(
                    self.canvas,
                    cmd_y,
                    prompt_w,
                    max(1, w - prompt_w),
                    self.theme.attr(Palette.CMD_LINE),
                    place_cursor=True,
                )
            except curses.error:
                pass
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
        self.canvas.win.refresh()

    def change_directory(self, panel: PanelState, target: Path, select_name: str | None = None) -> None:
        try:
            resolved = target.resolve()
            if not resolved.is_dir():
                self.status_message = f"Not a directory: {target}"
                return
            panel.vfs = None
            panel.cwd = resolved
            if panel.mode != PanelMode.LISTING:
                panel.mode = PanelMode.LISTING
            if panel is self.active:
                try:
                    os.chdir(resolved)
                except OSError:
                    pass
            self.rescan_panel(panel, preserve_name=select_name)
        except OSError as exc:
            self.status_message = f"Cannot enter directory: {exc}"

    def enter_item(self) -> None:
        import shlex
        p = self.active
        entry = p.current_entry()
        if entry is None:
            return
        if p.vfs is not None:
            if entry.kind == FileKind.PARENT:
                if p.vfs.inner_path:
                    parent_inner = p.vfs.inner_path.rsplit("/", 1)[0] if "/" in p.vfs.inner_path else ""
                    child_name = p.vfs.inner_path.rsplit("/", 1)[-1]
                    p.vfs.inner_path = parent_inner
                    self.rescan_panel(p, preserve_name=child_name)
                else:
                    arch_name = p.vfs.archive_path.name
                    p.vfs = None
                    self.rescan_panel(p, preserve_name=arch_name)
            elif entry.is_dir_like:
                p.vfs.inner_path = f"{p.vfs.inner_path}/{entry.name}".strip("/")
                self.rescan_panel(p)
            else:
                self.action_view()
            return
        if entry.kind == FileKind.PARENT:
            prev_name = p.cwd.name
            self.change_directory(p, p.cwd.parent, select_name=prev_name)
        elif entry.is_dir_like:
            self.change_directory(p, entry.path)
        elif entry.kind == FileKind.ARCHIVE:
            lower = entry.name.lower()
            akind: Literal["zip", "tar"] = "zip" if lower.endswith(".zip") else "tar"
            p.vfs = VfsLocation(archive_path=entry.path, archive_kind=akind, inner_path="")
            self.rescan_panel(p)
        elif entry.kind == FileKind.EXEC:
            self.run_shell_command(shlex.quote(str(entry.path)), pause=True)
        else:
            self.action_view()

    def navigate_parent(self) -> None:
        p = self.active
        if p.vfs is not None:
            if p.vfs.inner_path:
                child = p.vfs.inner_path.rsplit("/", 1)[-1]
                p.vfs.inner_path = p.vfs.inner_path.rsplit("/", 1)[0] if "/" in p.vfs.inner_path else ""
                self.rescan_panel(p, preserve_name=child)
            else:
                arch_name = p.vfs.archive_path.name
                p.vfs = None
                self.rescan_panel(p, preserve_name=arch_name)
        elif p.cwd != p.cwd.parent:
            prev_name = p.cwd.name
            self.change_directory(p, p.cwd.parent, select_name=prev_name)

    def move_cursor(self, delta: int) -> None:
        """Move cursor."""
        p = self.active
        if not p.entries:
            p.cursor = 0
            return
        p.cursor = max(0, min(len(p.entries) - 1, p.cursor + delta))

    def toggle_select_current(self) -> None:
        p = self.active
        cur = p.current_entry()
        if cur is None or cur.kind == FileKind.PARENT:
            self.move_cursor(1)
            return
        if cur.name in p.selected:
            p.selected.remove(cur.name)
        else:
            p.selected.add(cur.name)
        self.move_cursor(1)

    def select_group(self, select: bool) -> None:
        """Select group."""
        title = "Select" if select else "Unselect"
        pat, _ = modal_input(self.canvas, title, "Enter glob pattern:", initial="*")
        if pat is None:
            return
        for e in self.active.entries:
            if e.kind == FileKind.PARENT:
                continue
            if fnmatch.fnmatch(e.name, pat):
                if select:
                    self.active.selected.add(e.name)
                else:
                    self.active.selected.discard(e.name)

    def invert_selection(self) -> None:
        p = self.active
        new_sel: set[str] = set()
        for e in p.entries:
            if e.kind == FileKind.PARENT or e.is_dir_like:
                continue
            if e.name not in p.selected:
                new_sel.add(e.name)
        p.selected = new_sel

    def run_shell_command(self, cmd: str, pause: bool = True) -> None:
        """Run shell command."""
        stripped = cmd.strip()
        if not stripped:
            return
        self.cmdline.push_history(stripped)
        if stripped == "cd" or stripped.startswith("cd "):
            arg = stripped[2:].strip()
            target = Path.home() if not arg else Path(arg).expanduser()
            if not target.is_absolute():
                target = self.active.cwd / target
            self.change_directory(self.active, target)
            return
        curses.def_prog_mode()
        curses.endwin()
        try:
            print(f"\r\n[{self.active.cwd}]$ {stripped}")
            proc = subprocess.run(
                stripped,
                shell=True,
                cwd=str(self.active.cwd),
                text=True,
            )
            self.last_command_output = f"$ {stripped}\n(exit code {proc.returncode})"
            if pause:
                print(
                    "\nPress Enter to return to Midnight Commander...",
                    end="",
                    flush=True,
                )
                try:
                    input()
                except EOFError:
                    pass
        except Exception as exc:
            self.last_command_output = f"Error: {exc}"
        finally:
            curses.reset_prog_mode()
            self.stdscr.refresh()
            self.rescan_all()

    def open_subshell(self) -> None:
        """Open subshell."""
        if self.config.nosubshell:
            lines = self.last_command_output.splitlines() if self.last_command_output else ["Subshell disabled (-u)."]
            modal_message(self.canvas, "Command Output", lines)
            return
        shell = os.environ.get("SHELL", "/bin/bash")
        curses.def_prog_mode()
        curses.endwin()
        try:
            print("\r\n--- Midnight Commander Subshell (type 'exit' or Ctrl+D to return) ---")
            subprocess.run([shell], cwd=str(self.active.cwd))
        except Exception as exc:
            self.status_message = f"Subshell error: {exc}"
        finally:
            curses.reset_prog_mode()
            self.stdscr.refresh()
            self.rescan_all()

    def action_help(self) -> None:
        help_text = """GNU Midnight Commander (mc.py) — Quick Reference
======================================================================
PANEL NAVIGATION
  Tab              Switch between Left and Right panels
  Up / Down        Move highlight bar
  PgUp / PgDn      Scroll one page
  Home / End       Jump to top / bottom
  Enter            Enter directory / archive (VFS) / run command line
  Backspace        Go to parent directory (..) when command line empty
  Ctrl+R           Rescan active panel
  Ctrl+U           Swap Left and Right panels
  Alt+I / Esc i    Set opposite panel to active directory
  Alt+O / Esc o    Open highlighted directory in opposite panel
  Alt+. / Ctrl+H   Toggle hidden files (dotfiles)
  Ctrl+S / Alt+S   Quick incremental filename search in active panel
SELECTION
  Insert / Ctrl+T  Tag / untag current file and advance
  +                Select files matching glob pattern
  -                Unselect files matching glob pattern
  *                Invert file selection
FUNCTION KEYS
  F1               Help
  F2               User Menu (tar.gz, sha256, disk usage, compare)
  F3               Internal File Viewer (Text / Hex / Search / Wrap)
  F4               Internal Text Editor (Syntax / Undo / Block / Replace)
  F5               Copy selected files / directories (or extract VFS)
  F6               Rename / Move selected files / directories
  F7               Create new directory (mkdir)
  F8               Delete selected files / directories
  F9               Open top pull-down menu bar
  F10              Quit Midnight Commander
TOOLS & SHELL
  Ctrl+\\           Directory Hotlist (bookmarks)
  Alt+? / Esc ?    Find File (by glob & content regex)
  Ctrl+X d         Compare directories across panels
  Ctrl+X c         Chmod (interactive permissions dialog)
  Ctrl+X s         Create symbolic link
  Ctrl+X i         Toggle Info panel on opposite pane
  Ctrl+X q         Toggle Quick View preview on opposite pane
  Ctrl+O           Open interactive subshell ($SHELL)
  Alt+Enter        Insert highlighted filename into command line
""".encode()
        FileViewer(self.canvas, "Midnight Commander Help", data=help_text).run()

    def action_view(self) -> None:
        cur = self.active.current_entry()
        if cur is None or cur.kind == FileKind.PARENT:
            return
        if cur.is_dir_like:
            self.enter_item()
            return
        if self.active.vfs is not None and cur.vfs_member:
            try:
                data = read_vfs_bytes(self.active.vfs, cur.vfs_member)
                FileViewer(self.canvas, f"{cur.name} (VFS)", data=data).run()
            except Exception as exc:
                modal_message(self.canvas, "View Error", [str(exc)], is_error=True)
            return
        FileViewer(self.canvas, str(cur.path), file_path=cur.path).run()

    def action_edit(self) -> None:
        if self.active.vfs is not None:
            modal_message(
                self.canvas,
                "Edit",
                ["Cannot edit files inside read-only VFS archives."],
                is_error=True,
            )
            return
        cur = self.active.current_entry()
        if cur is None or cur.kind == FileKind.PARENT or cur.is_dir_like:
            name, _ = modal_input(self.canvas, "New File", "Enter filename to create/edit:")
            if not name:
                return
            target = (self.active.cwd / name).resolve()
        else:
            target = cur.path
        TextEditor(self.canvas, target).run()
        self.rescan_all()

    def _draw_progress(self, title: str, item_name: str, done: int, total: int) -> None:
        h, w = self.canvas.refresh_size()
        theme = self.canvas.theme
        bg = theme.attr(Palette.DIALOG_BG)
        t_attr = theme.attr(Palette.DIALOG_TITLE, curses.A_BOLD)
        bar_attr = theme.attr(Palette.DIALOG_BUTTON_ACT)
        bw = min(w - 6, 56)
        bh = 7
        by = max(0, (h - bh) // 2)
        bx = max(0, (w - bw) // 2)
        self.canvas.draw_box(by, bx, bh, bw, bg, title=title, title_attr=t_attr, fill=True)
        self.canvas.draw(by + 2, bx + 2, pad_right(truncate_middle(item_name, bw - 4), bw - 4), bg)
        pct = 100 if total <= 0 else min(100, int(done * 100 / total))
        bar_w = bw - 10
        filled = int(bar_w * pct / 100)
        bar = ("#" * filled) + ("-" * (bar_w - filled))
        self.canvas.draw(by + 4, bx + 2, f"[{bar}] {pct:3d}%", bar_attr if filled else bg)
        self.canvas.win.refresh()

    def _copy_or_move(self, is_move: bool) -> None:
        targets = self.active.target_entries()
        if not targets:
            return
        if is_move and self.active.vfs is not None:
            modal_message(
                self.canvas,
                "Move",
                ["Cannot move files out of read-only VFS archives. Use Copy (F5) to extract."],
                is_error=True,
            )
            return
        if self.inactive.vfs is not None:
            modal_message(
                self.canvas,
                "Destination Error",
                ["Cannot copy/move into a VFS archive."],
                is_error=True,
            )
            return
        action_name = "Move" if is_move else "Copy"
        default_dest = str(self.inactive.cwd)
        if len(targets) == 1 and is_move:
            default_dest = str(self.inactive.cwd / targets[0].name)
        prompt = (
            f"{action_name} '{targets[0].name}' to:"
            if len(targets) == 1
            else f"{action_name} {len(targets)} item(s) to:"
        )
        checks = [("Preserve attributes", True), ("Follow symlinks", False)]
        dest_str, check_vals = modal_input(self.canvas, action_name, prompt, initial=default_dest, checkboxes=checks)
        if not dest_str:
            return
        preserve, follow_symlinks = check_vals
        dest_path = Path(dest_str).expanduser()
        if not dest_path.is_absolute():
            dest_path = self.active.cwd / dest_path
        if self.active.vfs is not None:
            dest_path.mkdir(parents=True, exist_ok=True)
            for idx, entry in enumerate(targets, 1):
                self._draw_progress("Extracting", entry.name, idx - 1, len(targets))
                try:
                    extract_vfs_member(self.active.vfs, entry, dest_path)
                except Exception as exc:
                    modal_message(self.canvas, "Extract Error", [str(exc)], is_error=True)
            self.active.selected.clear()
            self.rescan_all()
            return
        overwrite_policy: str | None = None
        total = len(targets)
        for idx, entry in enumerate(targets, 1):
            src = entry.path
            if len(targets) == 1 and not dest_path.is_dir():
                target_out = dest_path
                target_out.parent.mkdir(parents=True, exist_ok=True)
            else:
                dest_path.mkdir(parents=True, exist_ok=True)
                target_out = dest_path / entry.name
            if src.resolve() == target_out.resolve():
                continue
            if target_out.exists() or target_out.is_symlink():
                if overwrite_policy == "none":
                    continue
                if overwrite_policy != "all" and self.config.confirm_overwrite:
                    ans = modal_message(
                        self.canvas,
                        "File Exists",
                        ["Target already exists:", str(target_out)],
                        buttons=["Overwrite", "Skip", "All", "None", "Abort"],
                    )
                    if ans == 1:
                        continue
                    if ans == 2:
                        overwrite_policy = "all"
                    elif ans == 3:
                        overwrite_policy = "none"
                        continue
                    elif ans in (4, -1):
                        break
            self._draw_progress(f"{action_name}ing", entry.name, idx - 1, total)
            try:
                if is_move:
                    if target_out.exists() and not target_out.is_dir():
                        target_out.unlink()
                    shutil.move(str(src), str(target_out))
                else:
                    if src.is_dir() and not (src.is_symlink() and not follow_symlinks):
                        shutil.copytree(
                            src,
                            target_out,
                            symlinks=not follow_symlinks,
                            dirs_exist_ok=True,
                        )
                    elif src.is_symlink() and not follow_symlinks:
                        if target_out.exists() or target_out.is_symlink():
                            target_out.unlink()
                        os.symlink(os.readlink(src), target_out)
                    else:
                        if preserve:
                            shutil.copy2(src, target_out)
                        else:
                            shutil.copyfile(src, target_out)
            except Exception as exc:
                modal_message(
                    self.canvas,
                    f"{action_name} Error",
                    [f"{entry.name}: {exc}"],
                    is_error=True,
                )
        self.active.selected.clear()
        self.rescan_all()

    def action_copy(self) -> None:
        self._copy_or_move(is_move=False)

    def action_move(self) -> None:
        self._copy_or_move(is_move=True)

    def action_mkdir(self) -> None:
        if self.active.vfs is not None:
            modal_message(
                self.canvas,
                "Mkdir",
                ["Cannot create directories inside read-only VFS archives."],
                is_error=True,
            )
            return
        name, _ = modal_input(self.canvas, "Create Directory", "Enter directory name:")
        if not name:
            return
        target = self.active.cwd / name
        try:
            target.mkdir(parents=True, exist_ok=False)
            self.rescan_all()
            self.rescan_panel(self.active, preserve_name=name.split("/")[0])
        except OSError as exc:
            modal_message(self.canvas, "Mkdir Error", [str(exc)], is_error=True)

    def action_delete(self) -> None:
        if self.active.vfs is not None:
            modal_message(
                self.canvas,
                "Delete",
                ["Cannot delete inside read-only VFS archives."],
                is_error=True,
            )
            return
        targets = self.active.target_entries()
        if not targets:
            return
        if self.config.confirm_delete:
            msg = (
                [f"Delete '{targets[0].name}'?"] if len(targets) == 1 else [f"Delete {len(targets)} selected item(s)?"]
            )
            ans = modal_message(self.canvas, "Delete", msg, buttons=["Yes", "No"])
            if ans != 0:
                return
        for idx, entry in enumerate(targets, 1):
            self._draw_progress("Deleting", entry.name, idx - 1, len(targets))
            try:
                if entry.path.is_symlink() or not entry.path.is_dir():
                    entry.path.unlink()
                else:
                    shutil.rmtree(entry.path)
            except OSError as exc:
                modal_message(self.canvas, "Delete Error", [f"{entry.name}: {exc}"], is_error=True)
        self.active.selected.clear()
        self.rescan_all()

    def action_hotlist(self) -> None:
        items = self.config.load_hotlist()
        sel = 0
        while True:
            h, w = self.canvas.refresh_size()
            theme = self.canvas.theme
            bg = theme.attr(Palette.DIALOG_BG)
            t_attr = theme.attr(Palette.DIALOG_TITLE, curses.A_BOLD)
            act = theme.attr(Palette.DIALOG_BUTTON_ACT, curses.A_BOLD)
            bw = min(w - 4, 72)
            bh = min(h - 4, max(12, len(items) + 6))
            by = max(0, (h - bh) // 2)
            bx = max(0, (w - bw) // 2)
            self.canvas.draw_box(
                by,
                bx,
                bh,
                bw,
                bg,
                title="Directory Hotlist",
                title_attr=t_attr,
                fill=True,
            )
            list_h = bh - 4
            sel = max(0, min(sel, max(0, len(items) - 1)))
            for i in range(min(list_h, len(items))):
                it = items[i]
                line = f" {it.get('label', ''):<16} {it.get('path', '')}"
                self.canvas.draw(by + 1 + i, bx + 1, pad_right(line, bw - 2), act if i == sel else bg)
            self.canvas.draw(
                by + bh - 2,
                bx + 2,
                pad_right("Enter: Jump | a: Add current | d: Delete | Esc: Close", bw - 4),
                bg,
            )
            self.canvas.win.refresh()
            key = read_key(self.canvas.win)
            if key in (27, curses.KEY_F10, ord("q")):
                return
            if key == curses.KEY_UP:
                sel = max(0, sel - 1)
            elif key == curses.KEY_DOWN:
                sel = min(max(0, len(items) - 1), sel + 1)
            elif key in (10, 13, curses.KEY_ENTER) and items:
                self.change_directory(self.active, Path(items[sel]["path"]))
                return
            elif key in (ord("a"), ord("A")):
                lbl, _ = modal_input(
                    self.canvas,
                    "Add Hotlist",
                    "Bookmark label:",
                    initial=self.active.cwd.name or "/",
                )
                if lbl is not None:
                    items.append({
                        "label": lbl or str(self.active.cwd),
                        "path": str(self.active.cwd),
                    })
                    self.config.save_hotlist(items)
            elif key in (ord("d"), ord("D"), curses.KEY_DC) and items:
                items.pop(sel)
                self.config.save_hotlist(items)

    def action_find_file(self) -> None:
        start_dir, _ = modal_input(self.canvas, "Find File", "Start directory:", initial=str(self.active.cwd))
        if not start_dir:
            return
        name_pat, _ = modal_input(self.canvas, "Find File", "Filename glob pattern:", initial="*")
        if name_pat is None:
            return
        content_pat, _ = modal_input(self.canvas, "Find File", "Content regex (optional):", initial="")
        if content_pat is None:
            return
        compiled: re.Pattern[str] | None = None
        if content_pat:
            try:
                compiled = re.compile(content_pat, re.IGNORECASE)
            except re.error as exc:
                modal_message(self.canvas, "Regex Error", [str(exc)], is_error=True)
                return
        root = Path(start_dir).expanduser()
        results: list[Path] = []
        self._draw_progress("Find File", f"Scanning {root}...", 0, 1)
        for cur_root, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".git")]
            for fn in files:
                if name_pat and not fnmatch.fnmatch(fn, name_pat):
                    continue
                fp = Path(cur_root) / fn
                if compiled is not None:
                    try:
                        if fp.stat().st_size > 2 * 1024 * 1024:
                            continue
                        txt = fp.read_text(encoding="utf-8", errors="ignore")
                        if not compiled.search(txt):
                            continue
                    except OSError:
                        continue
                results.append(fp)
                if len(results) >= 500:
                    break
            if len(results) >= 500:
                break
        if not results:
            modal_message(self.canvas, "Find File", ["No matching files found."])
            return
        sel = 0
        scroll = 0
        while True:
            h, w = self.canvas.refresh_size()
            theme = self.canvas.theme
            bg = theme.attr(Palette.DIALOG_BG)
            t_attr = theme.attr(Palette.DIALOG_TITLE, curses.A_BOLD)
            act = theme.attr(Palette.DIALOG_BUTTON_ACT, curses.A_BOLD)
            bw = min(w - 4, 90)
            bh = min(h - 4, 24)
            by = max(0, (h - bh) // 2)
            bx = max(0, (w - bw) // 2)
            list_h = bh - 4
            if sel < scroll:
                scroll = sel
            elif sel >= scroll + list_h:
                scroll = sel - list_h + 1
            self.canvas.draw_box(
                by,
                bx,
                bh,
                bw,
                bg,
                title=f"Find File ({len(results)} match(es))",
                title_attr=t_attr,
                fill=True,
            )
            for row in range(list_h):
                idx = scroll + row
                if idx >= len(results):
                    break
                line = shorten_home(str(results[idx]))
                self.canvas.draw(
                    by + 1 + row,
                    bx + 1,
                    pad_right(f" {line}", bw - 2),
                    act if idx == sel else bg,
                )
            self.canvas.draw(
                by + bh - 2,
                bx + 2,
                pad_right("Enter: Go to panel | F3: View | F4: Edit | Esc: Close", bw - 4),
                bg,
            )
            self.canvas.win.refresh()
            key = read_key(self.canvas.win)
            if key in (27, curses.KEY_F10):
                return
            if key == curses.KEY_UP:
                sel = max(0, sel - 1)
            elif key == curses.KEY_DOWN:
                sel = min(len(results) - 1, sel + 1)
            elif key == curses.KEY_PPAGE:
                sel = max(0, sel - list_h)
            elif key == curses.KEY_NPAGE:
                sel = min(len(results) - 1, sel + list_h)
            elif key in (10, 13, curses.KEY_ENTER):
                chosen = results[sel]
                self.change_directory(self.active, chosen.parent, select_name=chosen.name)
                return
            elif key == curses.KEY_F3:
                FileViewer(self.canvas, str(results[sel]), file_path=results[sel]).run()
            elif key == curses.KEY_F4:
                TextEditor(self.canvas, results[sel]).run()

    def action_chmod(self) -> None:
        if self.active.vfs is not None:
            modal_message(
                self.canvas,
                "Chmod",
                ["Not supported inside VFS archives."],
                is_error=True,
            )
            return
        cur = self.active.current_entry()
        if cur is None or cur.kind == FileKind.PARENT:
            return
        mode = stat.S_IMODE(cur.mode)
        bits = [
            ("Owner Read   (u+r)", stat.S_IRUSR),
            ("Owner Write  (u+w)", stat.S_IWUSR),
            ("Owner Exec   (u+x)", stat.S_IXUSR),
            ("Group Read   (g+r)", stat.S_IRGRP),
            ("Group Write  (g+w)", stat.S_IWGRP),
            ("Group Exec   (g+x)", stat.S_IXGRP),
            ("Other Read   (o+r)", stat.S_IROTH),
            ("Other Write  (o+w)", stat.S_IWOTH),
            ("Other Exec   (o+x)", stat.S_IXOTH),
        ]
        state = [bool(mode & b) for _, b in bits]
        focus = 0
        while True:
            h, w = self.canvas.refresh_size()
            theme = self.canvas.theme
            bg = theme.attr(Palette.DIALOG_BG)
            t_attr = theme.attr(Palette.DIALOG_TITLE, curses.A_BOLD)
            act = theme.attr(Palette.DIALOG_BUTTON_ACT, curses.A_BOLD)
            bw, bh = min(w - 4, 44), 16
            by, bx = max(0, (h - bh) // 2), max(0, (w - bw) // 2)
            oct_val = sum(b for (_, b), on in zip(bits, state) if on)
            self.canvas.draw_box(
                by,
                bx,
                bh,
                bw,
                bg,
                title=f"Chmod: {cur.name}",
                title_attr=t_attr,
                fill=True,
            )
            self.canvas.draw(by + 1, bx + 2, f"Octal mode: 0o{oct_val:03o}", bg)
            for idx, ((lbl, _), on) in enumerate(zip(bits, state)):
                mark = "[x]" if on else "[ ]"
                self.canvas.draw(by + 3 + idx, bx + 2, f"{mark} {lbl}", act if focus == idx else bg)
            self.canvas.draw(by + bh - 2, bx + 6, "[ Apply ]", act if focus == len(bits) else bg)
            self.canvas.draw(
                by + bh - 2,
                bx + 20,
                "[ Cancel ]",
                act if focus == len(bits) + 1 else bg,
            )
            self.canvas.win.refresh()
            key = read_key(self.canvas.win)
            if key in (27, curses.KEY_F10):
                return
            if key in (curses.KEY_UP,):
                focus = (focus - 1) % (len(bits) + 2)
            elif key in (curses.KEY_DOWN, 9):
                focus = (focus + 1) % (len(bits) + 2)
            elif key == ord(" ") and focus < len(bits):
                state[focus] = not state[focus]
            elif key in (10, 13, curses.KEY_ENTER):
                if focus < len(bits):
                    state[focus] = not state[focus]
                elif focus == len(bits):
                    try:
                        os.chmod(cur.path, oct_val)
                        self.rescan_all()
                    except OSError as exc:
                        modal_message(self.canvas, "Chmod Error", [str(exc)], is_error=True)
                    return
                else:
                    return

    def action_symlink(self) -> None:
        if self.active.vfs is not None:
            modal_message(
                self.canvas,
                "Symlink",
                ["Not supported inside VFS archives."],
                is_error=True,
            )
            return
        cur = self.active.current_entry()
        target_init = str(cur.path) if cur and cur.kind != FileKind.PARENT else ""
        target, _ = modal_input(self.canvas, "Symbolic Link", "Existing target path:", initial=target_init)
        if not target:
            return
        link_name, _ = modal_input(
            self.canvas,
            "Symbolic Link",
            "Symlink path (in inactive panel or relative):",
            initial=str(self.inactive.cwd / Path(target).name),
        )
        if not link_name:
            return
        try:
            os.symlink(target, link_name)
            self.rescan_all()
        except OSError as exc:
            modal_message(self.canvas, "Symlink Error", [str(exc)], is_error=True)

    def action_compare_dirs(self) -> None:
        right_map = {e.name: e for e in self.right.entries if e.kind != FileKind.PARENT}
        left_map = {e.name: e for e in self.left.entries if e.kind != FileKind.PARENT}
        self.left.selected.clear()
        self.right.selected.clear()
        for name, le in left_map.items():
            if le.is_dir_like:
                continue
            re_entry = right_map.get(name)
            if re_entry is None or le.size != re_entry.size or le.mtime > re_entry.mtime + 2:
                self.left.selected.add(name)
        for name, re_entry in right_map.items():
            if re_entry.is_dir_like:
                continue
            le = left_map.get(name)
            if le is None or le.size != re_entry.size or re_entry.mtime > le.mtime + 2:
                self.right.selected.add(name)
        self.status_message = f"Compared: {len(self.left.selected)} left, {len(self.right.selected)} right"

    def action_user_menu(self) -> None:
        options = [
            "1  Create .tar.gz archive from tagged/current item",
            "2  Compute SHA-256 checksum of current file",
            "3  Show disk usage (du -sh) of current item",
            "4  Compare directories across panels",
            "5  Touch / create empty file",
        ]
        sel = 0
        while True:
            h, w = self.canvas.refresh_size()
            theme = self.canvas.theme
            bg = theme.attr(Palette.DIALOG_BG)
            t_attr = theme.attr(Palette.DIALOG_TITLE, curses.A_BOLD)
            act = theme.attr(Palette.DIALOG_BUTTON_ACT, curses.A_BOLD)
            bw, bh = min(w - 4, 60), len(options) + 4
            by, bx = max(0, (h - bh) // 2), max(0, (w - bw) // 2)
            self.canvas.draw_box(by, bx, bh, bw, bg, title="User Menu", title_attr=t_attr, fill=True)
            for idx, opt in enumerate(options):
                self.canvas.draw(
                    by + 2 + idx,
                    bx + 2,
                    pad_right(opt, bw - 4),
                    act if idx == sel else bg,
                )
            self.canvas.win.refresh()
            key = read_key(self.canvas.win)
            if key in (27, curses.KEY_F10):
                return
            if key == curses.KEY_UP:
                sel = (sel - 1) % len(options)
            elif key == curses.KEY_DOWN:
                sel = (sel + 1) % len(options)
            elif key in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
                sel = int(chr(key)) - 1
                break
            elif key in (10, 13, curses.KEY_ENTER):
                break
        cur = self.active.current_entry()
        match sel:
            case 0:
                targets = self.active.target_entries()
                if not targets or self.active.vfs is not None:
                    return
                out_name, _ = modal_input(
                    self.canvas,
                    "Create .tar.gz",
                    "Archive filename:",
                    initial=f"{targets[0].name}.tar.gz",
                )
                if out_name:
                    out_path = self.active.cwd / out_name
                    try:
                        with tarfile.open(out_path, "w:gz") as tf:
                            for t in targets:
                                tf.add(t.path, arcname=t.name)
                        self.rescan_all()
                        self.status_message = f"Created {out_name}"
                    except Exception as exc:
                        modal_message(self.canvas, "Archive Error", [str(exc)], is_error=True)
            case 1:
                if cur and not cur.is_dir_like and cur.kind != FileKind.PARENT:
                    try:
                        if self.active.vfs is not None and cur.vfs_member:
                            digest = hashlib.sha256(read_vfs_bytes(self.active.vfs, cur.vfs_member)).hexdigest()
                        else:
                            h_obj = hashlib.sha256()
                            with open(cur.path, "rb") as fp:
                                while chunk := fp.read(65536):
                                    h_obj.update(chunk)
                            digest = h_obj.hexdigest()
                        modal_message(self.canvas, "SHA-256 Checksum", [cur.name, digest])
                    except Exception as exc:
                        modal_message(self.canvas, "Error", [str(exc)], is_error=True)
            case 2:
                if cur and cur.kind != FileKind.PARENT and self.active.vfs is None:
                    total_bytes = 0
                    total_files = 0
                    if cur.is_dir_like:
                        for r, _, fs in os.walk(cur.path):
                            for f in fs:
                                total_files += 1
                                try:
                                    total_bytes += (Path(r) / f).lstat().st_size
                                except OSError:
                                    pass
                    else:
                        total_files = 1
                        total_bytes = cur.size
                    modal_message(
                        self.canvas,
                        "Disk Usage",
                        [
                            f"Path : {cur.path}",
                            f"Files: {total_files}",
                            f"Total: {format_human_bytes(total_bytes)} ({total_bytes:,} bytes)",
                        ],
                    )
            case 3:
                self.action_compare_dirs()
            case 4:
                if self.active.vfs is None:
                    fn, _ = modal_input(self.canvas, "Touch File", "Filename:")
                    if fn:
                        try:
                            (self.active.cwd / fn).touch()
                            self.rescan_all()
                        except OSError as exc:
                            modal_message(self.canvas, "Error", [str(exc)], is_error=True)

    def open_pulldown_menu(self, initial_col: int = 0) -> None:
        """Open pulldown menu."""

        def set_listing(p: PanelState, fmt: ListingFormat) -> None:
            p.mode = PanelMode.LISTING
            p.listing_format = fmt

        def set_sort(p: PanelState, fld: SortField) -> None:
            if p.sort_field == fld:
                p.sort_reverse = not p.sort_reverse
            else:
                p.sort_field = fld
                p.sort_reverse = False
            self.rescan_panel(p)

        def set_filter(p: PanelState) -> None:
            pat, _ = modal_input(self.canvas, "Filter", "Show files matching:", initial=p.filter_glob)
            if pat is not None:
                p.filter_glob = pat or "*"
                self.rescan_panel(p)

        def toggle_hidden() -> None:
            self.config.show_hidden = not self.config.show_hidden
            self.left.show_hidden = self.config.show_hidden
            self.right.show_hidden = self.config.show_hidden
            self.rescan_all()

        def cycle_theme() -> None:
            order = [ColorTheme.CLASSIC, ColorTheme.DARK, ColorTheme.MONO]
            nxt = order[(order.index(self.config.theme) + 1) % len(order)]
            self.config.theme = nxt
            self.theme.apply(nxt)

        def toggle_ascii() -> None:
            self.config.ascii_lines = not self.config.ascii_lines
            self.canvas.set_ascii(self.config.ascii_lines)

        def panel_menu(p: PanelState) -> list[tuple[str, Callable[[], None]]]:
            return [
                ("Listing: Full", lambda: set_listing(p, ListingFormat.FULL)),
                ("Listing: Brief", lambda: set_listing(p, ListingFormat.BRIEF)),
                ("Listing: Long", lambda: set_listing(p, ListingFormat.LONG)),
                ("Mode: Info", lambda: setattr(p, "mode", PanelMode.INFO)),
                ("Mode: Quick View", lambda: setattr(p, "mode", PanelMode.QUICK_VIEW)),
                ("Sort: Name", lambda: set_sort(p, SortField.NAME)),
                ("Sort: Extension", lambda: set_sort(p, SortField.EXT)),
                ("Sort: Size", lambda: set_sort(p, SortField.SIZE)),
                ("Sort: Modify time", lambda: set_sort(p, SortField.MTIME)),
                ("Filter...", lambda: set_filter(p)),
                ("Rescan         Ctrl+R", lambda: self.rescan_panel(p)),
            ]

        menus: list[tuple[str, int, list[tuple[str, Callable[[], None]]]]] = [
            ("Left", 2, panel_menu(self.left)),
            (
                "File",
                10,
                [
                    ("View           F3", self.action_view),
                    ("Edit           F4", self.action_edit),
                    ("Copy           F5", self.action_copy),
                    ("Rename/Move    F6", self.action_move),
                    ("Mkdir          F7", self.action_mkdir),
                    ("Delete         F8", self.action_delete),
                    ("Symlink    Ctrl+X s", self.action_symlink),
                    ("Chmod      Ctrl+X c", self.action_chmod),
                    ("Select group   +", lambda: self.select_group(True)),
                    ("Unselect group -", lambda: self.select_group(False)),
                    ("Invert sel     *", self.invert_selection),
                    ("Quit           F10", self.request_quit),
                ],
            ),
            (
                "Command",
                18,
                [
                    ("User menu      F2", self.action_user_menu),
                    ("Find file      Alt+?", self.action_find_file),
                    ("Hotlist        Ctrl+\\", self.action_hotlist),
                    ("Compare dirs   Ctrl+X d", self.action_compare_dirs),
                    ("Swap panels    Ctrl+U", self.swap_panels),
                    ("Subshell       Ctrl+O", self.open_subshell),
                ],
            ),
            (
                "Options",
                29,
                [
                    (
                        f"[{'x' if self.config.show_hidden else ' '}] Show hidden files",
                        toggle_hidden,
                    ),
                    (
                        f"[{'x' if self.config.confirm_delete else ' '}] Confirm delete",
                        lambda: setattr(
                            self.config,
                            "confirm_delete",
                            not self.config.confirm_delete,
                        ),
                    ),
                    (
                        f"[{'x' if self.config.confirm_overwrite else ' '}] Confirm overwrite",
                        lambda: setattr(
                            self.config,
                            "confirm_overwrite",
                            not self.config.confirm_overwrite,
                        ),
                    ),
                    (
                        f"[{'x' if self.config.ascii_lines else ' '}] ASCII box lines",
                        toggle_ascii,
                    ),
                    (f"Theme: {self.config.theme.value}", cycle_theme),
                ],
            ),
            ("Right", 40, panel_menu(self.right)),
        ]
        col = max(0, min(len(menus) - 1, initial_col))
        row = 0
        while True:
            self.render()
            _, mx_x, items = menus[col]
            mw = max(len(lbl) for lbl, _ in items) + 4
            mh = len(items) + 2
            mx_x = min(mx_x, max(0, self.canvas.width - mw))
            bg = self.theme.attr(Palette.MENU_BAR)
            act = self.theme.attr(Palette.FN_NUM, curses.A_BOLD)
            self.canvas.draw_box(1, mx_x, mh, mw, bg, fill=True)
            row = max(0, min(len(items) - 1, row))
            for idx, (lbl, _) in enumerate(items):
                self.canvas.draw(
                    2 + idx,
                    mx_x + 1,
                    pad_right(f" {lbl}", mw - 2),
                    act if idx == row else bg,
                )
            self.canvas.win.refresh()
            key = read_key(self.canvas.win)
            if key in (27, curses.KEY_F9, curses.KEY_F10):
                return
            if key == curses.KEY_LEFT:
                col = (col - 1) % len(menus)
                row = 0
            elif key == curses.KEY_RIGHT:
                col = (col + 1) % len(menus)
                row = 0
            elif key == curses.KEY_UP:
                row = (row - 1) % len(items)
            elif key == curses.KEY_DOWN:
                row = (row + 1) % len(items)
            elif key in (10, 13, curses.KEY_ENTER):
                items[row][1]()
                return

    def swap_panels(self) -> None:
        self.left, self.right = self.right, self.left
        self.left.side = "left"
        self.right.side = "right"
        self.active_side = "right" if self.active_side == "left" else "left"

    def request_quit(self) -> None:
        if self.config.confirm_exit and (
            modal_message(
                self.canvas,
                "Quit",
                ["Exit Midnight Commander?"],
                buttons=["Yes", "No"],
            )
            != 0
        ):
            return
        self.running = False

    def handle_ctrl_x(self) -> None:
        """Handle ctrl x."""
        self.status_message = "Ctrl-X ..."
        self.render()
        key = read_key(self.canvas.win)
        self.status_message = ""
        if isinstance(key, tuple):
            return
        ch = chr(key).lower() if 0 <= key < 256 else ""
        match ch:
            case "c":
                self.action_chmod()
            case "d":
                self.action_compare_dirs()
            case "s":
                self.action_symlink()
            case "i":
                self.inactive.mode = PanelMode.LISTING if self.inactive.mode == PanelMode.INFO else PanelMode.INFO
            case "q":
                self.inactive.mode = (
                    PanelMode.LISTING if self.inactive.mode == PanelMode.QUICK_VIEW else PanelMode.QUICK_VIEW
                )
            case _:
                pass

    def handle_mouse(self) -> None:
        """Handle mouse."""
        try:
            _, mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return
        h, w = self.canvas.refresh_size()
        panel_top, panel_h, left_w, _ = self._layout()
        if my == 0:
            menu_hits = [(2, 7, 0), (10, 15, 1), (18, 26, 2), (29, 37, 3), (40, 46, 4)]
            for x1, x2, col in menu_hits:
                if x1 <= mx <= x2:
                    self.open_pulldown_menu(col)
                    return
            return
        if my == h - 1:
            slot_w = max(6, w // 10)
            fn_idx = min(9, mx // slot_w) + 1
            fn_actions: dict[int, Callable[[], None]] = {
                1: self.action_help,
                2: self.action_user_menu,
                3: self.action_view,
                4: self.action_edit,
                5: self.action_copy,
                6: self.action_move,
                7: self.action_mkdir,
                8: self.action_delete,
                9: lambda: self.open_pulldown_menu(0),
                10: self.request_quit,
            }
            fn_actions[fn_idx]()
            return
        side: Literal["left", "right"] = "left" if mx < left_w else "right"
        panel = self.left if side == "left" else self.right
        self.active_side = side
        if bstate & curses.BUTTON4_PRESSED:
            self.move_cursor(-3)
            return
        if bstate & getattr(curses, "BUTTON5_PRESSED", 0x200000):
            self.move_cursor(3)
            return
        list_top = panel_top + 2
        divider_y = panel_top + panel_h - 3
        if list_top <= my < divider_y and panel.mode == PanelMode.LISTING:
            row = my - list_top
            if panel.listing_format == ListingFormat.BRIEF:
                inner_w = (left_w if side == "left" else (w - left_w)) - 2
                col_w = max(8, inner_w // 2)
                rel_x = mx - (1 if side == "left" else left_w + 1)
                col = 0 if rel_x < col_w else 1
                idx = panel.scroll + col * (divider_y - list_top) + row
            else:
                idx = panel.scroll + row
            if 0 <= idx < len(panel.entries):
                now = time.monotonic()
                is_double = (bstate & curses.BUTTON1_DOUBLE_CLICKED) or (
                    self._last_click_side == side
                    and self._last_click_row == idx
                    and (now - self._last_click_time) < 0.45
                )
                panel.cursor = idx
                self._last_click_time = now
                self._last_click_row = idx
                self._last_click_side = side
                if is_double:
                    self.enter_item()

    def handle_quick_search_key(self, key: int | tuple[str, str]) -> None:
        """Handle quick search key."""
        p = self.active
        if key in (27, 10, 13, curses.KEY_ENTER):
            self.quick_search_active = False
            p.quick_search = ""
            return
        if key in (curses.KEY_BACKSPACE, 127, 8):
            p.quick_search = p.quick_search[:-1]
        elif isinstance(key, int) and 32 <= key < 127:
            p.quick_search += chr(key)
        else:
            self.quick_search_active = False
            p.quick_search = ""
            self.dispatch_key(key)
            return
        if p.quick_search:
            q = p.quick_search.lower()
            for idx, e in enumerate(p.entries):
                if e.name.lower().startswith(q):
                    p.cursor = idx
                    break
            else:
                for idx, e in enumerate(p.entries):
                    if q in e.name.lower():
                        p.cursor = idx
                        break

    def dispatch_key(self, key: int | tuple[str, str]) -> None:
        """Dispatch key."""
        self.status_message = ""
        if self.quick_search_active:
            self.handle_quick_search_key(key)
            return
        if isinstance(key, tuple) and key[0] == "ALT":
            ch = key[1]
            match ch.lower():
                case "s":
                    self.quick_search_active = True
                    self.active.quick_search = ""
                case "?":
                    self.action_find_file()
                case ".":
                    self.config.show_hidden = not self.config.show_hidden
                    self.left.show_hidden = self.config.show_hidden
                    self.right.show_hidden = self.config.show_hidden
                    self.rescan_all()
                case "c":
                    target, _ = modal_input(self.canvas, "Quick cd", "Change directory to:")
                    if target:
                        p = Path(target).expanduser()
                        if not p.is_absolute():
                            p = self.active.cwd / p
                        self.change_directory(self.active, p)
                case "i":
                    if self.active.vfs is None:
                        self.change_directory(self.inactive, self.active.cwd)
                case "o":
                    cur = self.active.current_entry()
                    if cur and cur.is_dir_like and self.active.vfs is None:
                        self.change_directory(self.inactive, cur.path)
                case "p":
                    self.cmdline.history_prev()
                case "n":
                    self.cmdline.history_next()
                case "\n":
                    cur = self.active.current_entry()
                    if cur and cur.kind != FileKind.PARENT:
                        ins = cur.name + " "
                        self.cmdline.text = (
                            self.cmdline.text[: self.cmdline.cursor] + ins + self.cmdline.text[self.cmdline.cursor :]
                        )
                        self.cmdline.cursor += len(ins)
                case _:
                    pass
            return
        assert isinstance(key, int)
        _, panel_h, _, _ = self._layout()
        page_sz = max(1, panel_h - 5)
        match key:
            case curses.KEY_RESIZE:
                return
            case curses.KEY_MOUSE:
                self.handle_mouse()
            case 9:
                self.active_side = "right" if self.active_side == "left" else "left"
                if self.active.vfs is None:
                    try:
                        os.chdir(self.active.cwd)
                    except OSError:
                        pass
            case curses.KEY_UP:
                self.move_cursor(-1)
            case curses.KEY_DOWN:
                self.move_cursor(1)
            case curses.KEY_PPAGE:
                self.move_cursor(-page_sz)
            case curses.KEY_NPAGE:
                self.move_cursor(page_sz)
            case curses.KEY_HOME if not self.cmdline.text:
                self.active.cursor = 0
            case curses.KEY_END if not self.cmdline.text:
                self.active.cursor = max(0, len(self.active.entries) - 1)
            case curses.KEY_LEFT | curses.KEY_RIGHT if self.cmdline.text:
                self.cmdline.handle_key(key)
            case curses.KEY_LEFT if self.active.listing_format == ListingFormat.BRIEF:
                self.move_cursor(-page_sz)
            case curses.KEY_RIGHT if self.active.listing_format == ListingFormat.BRIEF:
                self.move_cursor(page_sz)
            case 10 | 13 | curses.KEY_ENTER:
                if self.cmdline.text.strip():
                    cmd = self.cmdline.text
                    self.cmdline.set_text("")
                    self.run_shell_command(cmd, pause=True)
                else:
                    self.enter_item()
            case curses.KEY_BACKSPACE | 127 | 8:
                if self.cmdline.text:
                    self.cmdline.handle_key(key)
                else:
                    self.navigate_parent()
            case curses.KEY_IC | 20:
                self.toggle_select_current()
            case 19:
                self.quick_search_active = True
                self.active.quick_search = ""
            case 18:
                self.rescan_all()
                self.status_message = "Rescanned panels"
            case 21 if not self.cmdline.text:
                self.swap_panels()
            case 15:
                self.open_subshell()
            case 28:
                self.action_hotlist()
            case 24:
                self.handle_ctrl_x()
            case curses.KEY_F1:
                self.action_help()
            case curses.KEY_F2:
                self.action_user_menu()
            case curses.KEY_F3:
                self.action_view()
            case curses.KEY_F4:
                self.action_edit()
            case curses.KEY_F5:
                self.action_copy()
            case curses.KEY_F6:
                self.action_move()
            case curses.KEY_F7:
                self.action_mkdir()
            case curses.KEY_F8:
                self.action_delete()
            case curses.KEY_F9:
                self.open_pulldown_menu(0)
            case curses.KEY_F10:
                self.request_quit()
            case _:
                if not self.cmdline.text and key in (ord("+"), ord("-"), ord("*")):
                    if key == ord("+"):
                        self.select_group(True)
                    elif key == ord("-"):
                        self.select_group(False)
                    else:
                        self.invert_selection()
                else:
                    self.cmdline.handle_key(key)

    def run(self) -> Path:
        while self.running:
            self.render()
            key = read_key(self.stdscr)
            self.dispatch_key(key)
        return self.active.cwd


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse args."""
    parser = argparse.ArgumentParser(
        prog="mc",
        description="GNU Midnight Commander clone in a single-file Python 3.14+ script.",
    )
    parser.add_argument("left_dir", nargs="?", default=None, help="Initial directory for left panel")
    parser.add_argument("right_dir", nargs="?", default=None, help="Initial directory for right panel")
    parser.add_argument("-v", "--view", metavar="FILE", help="Start internal file viewer on FILE")
    parser.add_argument("-e", "--edit", metavar="FILE", help="Start internal text editor on FILE")
    parser.add_argument(
        "-P",
        "--printwd",
        metavar="FILE",
        help="Write last working directory to FILE on exit",
    )
    parser.add_argument(
        "-b",
        "--nocolor",
        action="store_true",
        help="Run in monochrome (black & white) mode",
    )
    parser.add_argument("-c", "--color", action="store_true", help="Force color mode")
    parser.add_argument(
        "-a",
        "--stickchars",
        action="store_true",
        help="Use ASCII characters (+, -, |) for borders",
    )
    parser.add_argument("-u", "--nosubshell", action="store_true", help="Disable interactive subshell")
    parser.add_argument("-d", "--nomouse", action="store_true", help="Disable mouse support")
    parser.add_argument("-x", "--xterm", action="store_true", help="Force xterm mouse reporting")
    parser.add_argument("-V", "--version", action="store_true", help="Show version and exit")
    return parser.parse_args(argv)


def _curses_main(stdscr: curses.window, args: argparse.Namespace) -> Path:
    theme_mode = ColorTheme.MONO if args.nocolor else ColorTheme.CLASSIC
    config = AppConfig(
        ascii_lines=args.stickchars,
        theme=theme_mode,
        mouse_enabled=not args.nomouse,
        nosubshell=args.nosubshell,
    )
    if args.view:
        theme = ThemeManager(config.theme)
        canvas = Canvas(stdscr, theme, config.ascii_lines)
        p = Path(args.view).expanduser().resolve()
        FileViewer(canvas, str(p), file_path=p).run()
        return Path.cwd()
    if args.edit:
        theme = ThemeManager(config.theme)
        canvas = Canvas(stdscr, theme, config.ascii_lines)
        p = Path(args.edit).expanduser().resolve()
        TextEditor(canvas, p).run()
        return Path.cwd()
    cwd = Path.cwd()
    left = Path(args.left_dir).expanduser().resolve() if args.left_dir else cwd
    right = Path(args.right_dir).expanduser().resolve() if args.right_dir else cwd
    if not left.is_dir():
        left = cwd
    if not right.is_dir():
        right = cwd
    app = MidnightCommander(stdscr, left, right, config)
    return app.run()


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    args = parse_args(argv)
    if args.version:
        zstd_status = "enabled (stdlib compression.zstd)" if _stdlib_zstd else "unavailable (<3.14)"
        print(
            f"{APP_NAME} v{APP_VERSION}\n"
            f"Python {sys.version.split()[0]} (target >={MIN_PYTHON[0]}.{MIN_PYTHON[1]})\n"
            f"Zstandard VFS: {zstd_status}"
        )
        return 0
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    os.environ.setdefault("ESCDELAY", "25")
    old_termios = configure_termios_no_ixon()
    last_wd = Path.cwd()
    try:
        last_wd = curses.wrapper(_curses_main, args)
    except KeyboardInterrupt:
        return 130
    finally:
        restore_termios(old_termios)
    if args.printwd:
        try:
            atomic_write(Path(args.printwd), str(last_wd) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"mc: failed to write printwd file: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
