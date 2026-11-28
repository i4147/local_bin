#!/data/data/com.termux/files/usr/bin/env python
"""
mc.py - Minimal Midnight Commander clone for Termux.
Usage:
    python mc.py [start_directory]
Keys:
    Tab            Switch active panel
    Up/Down j/k    Move cursor
    PgUp/PgDn      Page
    Home/End g/G   Top / bottom
    Enter / -> / l Enter directory or view file
    <- / BS / h    Parent directory
    Space / Ins    Toggle selection, move down
    a / u          Select all / Unselect
    r              Refresh
    F1 / ?         Help
    F3 / v         View file
    F4 / e         Edit in $EDITOR
    F5 / c         Copy to other panel
    F6 / m         Move / rename
    F7 / d         New directory
    F8 / x         Delete (asks confirmation)
    F10 / q        Quit
"""

from __future__ import annotations

from collections.abc import Sequence
import curses
from datetime import datetime
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Any, NoReturn
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


def safe_addstr(scr: Any, y: int, x: int, s: str, attr: int = 0) -> None:
    """addstr that never raises on edge-of-screen writes."""
    try:
        h, w = scr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        scr.addstr(y, x, str(s)[: w - x], attr)
    except curses.error:
        pass


def format_size(n: int) -> str:
    """Format size."""
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n}{unit}"
        n //= 1024
    return f"{n}P"


def safe_mtime(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "?"


def center_box(app: Any, height: int, width: int) -> tuple[int, int, int, int]:
    h, w = app.stdscr.getmaxyx()
    width = max(20, min(width, w - 4))
    height = max(4, min(height, h - 4))
    y = max(0, (h - height) // 2)
    x = max(0, (w - width) // 2)
    return y, x, height, width


def draw_box(scr: Any, y: Any, x: int, h: int, w: int, title: str) -> None:
    """Draw box."""
    attr = curses.A_BOLD
    safe_addstr(scr, y, x, "┌" + "─" * (w - 2) + "┐", attr)
    for i in range(1, h - 1):
        safe_addstr(scr, y + i, x, "│" + " " * (w - 2) + "│", attr)
    safe_addstr(scr, y + h - 1, x, "└" + "─" * (w - 2) + "┘", attr)
    safe_addstr(scr, y, x + 2, f" {title} "[: w - 4], attr | curses.A_REVERSE)


class FileEntry:
    __slots__ = (
        "is_dir",
        "is_exec",
        "is_link",
        "mode",
        "mtime",
        "name",
        "ok",
        "path",
        "size",
    )

    def __init__(self, path: Path) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.path = path
        self.name = path.name or str(path)
        self.is_dir = self.is_link = self.is_exec = False
        self.size = 0
        self.mtime = 0.0
        self.mode = 0
        self.ok = False
        try:
            st = path.lstat()
            self.size = st.st_size
            self.mtime = st.st_mtime
            self.mode = st.st_mode
            self.is_link = stat.S_ISLNK(st.st_mode)
            self.is_dir = stat.S_ISDIR(st.st_mode)
            self.is_exec = bool(st.st_mode & 0o111) and not self.is_dir
            self.ok = True
        except OSError:
            pass

    @property
    def display_name(self) -> str:
        """Return the display name."""
        if self.name == "..":
            return "../"
        if self.is_link:
            try:
                return f"{self.name}@ -> {os.readlink(self.path)}"
            except OSError:
                return f"{self.name}@"
        if self.is_dir:
            return self.name + "/"
        if self.is_exec:
            return self.name + "*"
        return self.name

    @property
    def size_str(self) -> str:
        """Return the size str."""
        return "<DIR>" if self.is_dir else format_size(self.size)


class Panel:
    def __init__(self, start: Path) -> None:
        """Initialize the instance state from the supplied arguments."""
        try:
            p = start.resolve()
            self.path = p if p.is_dir() else Path.home()
        except OSError:
            self.path = Path.home()
        self.entries: list[FileEntry] = []
        self.cursor = 0
        self.scroll = 0
        self.selected: set[Path] = set()
        self.refresh()

    def refresh(self) -> None:
        try:
            self.path = self.path.resolve()
        except OSError:
            self.path = Path.home()
        entries: list[FileEntry] = []
        if self.path.parent != self.path:
            entries.append(FileEntry(self.path / ".."))
        try:
            children = list(self.path.iterdir())
        except OSError:
            children = []
        dirs, files = [], []
        for child in children:
            e = FileEntry(child)
            try:
                as_dir = e.is_dir or (e.is_link and child.is_dir())
            except OSError:
                as_dir = e.is_dir
            (dirs if as_dir else files).append(e)
        dirs.sort(key=lambda e: e.name.lower())
        files.sort(key=lambda e: e.name.lower())
        self.entries = entries + dirs + files
        self.selected = {p for p in self.selected if p.exists()}
        if self.entries:
            self.cursor = max(0, min(self.cursor, len(self.entries) - 1))
        else:
            self.cursor = 0

    def current(self) -> FileEntry | None:
        if 0 <= self.cursor < len(self.entries):
            return self.entries[self.cursor]
        return None

    def move_cursor(self, delta: int) -> None:
        """Move cursor."""
        if self.entries:
            self.cursor = max(0, min(len(self.entries) - 1, self.cursor + delta))

    def change_dir(self, target: Path) -> None:
        try:
            target = target.resolve()
        except OSError:
            return
        if not target.is_dir():
            return
        self.path = target
        self.cursor = 0
        self.scroll = 0
        self.selected.clear()
        self.refresh()

    def toggle_select(self) -> None:
        e = self.current()
        if e and e.name != "..":
            self.selected.symmetric_difference_update({e.path})
        self.move_cursor(1)

    def select_all(self) -> None:
        """Select all."""
        for e in self.entries:
            if e.name != "..":
                self.selected.add(e.path)

    def clear_selection(self) -> None:
        """Clear selection."""
        self.selected.clear()

    def targets(self) -> list[Path]:
        if self.selected:
            return list(self.selected)
        e = self.current()
        if e and e.name != "..":
            return [e.path]
        return []


class Dialog:
    needs_cursor = False

    def __init__(self, app: Any, title: str) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.app = app
        self.title = title
        self.result = None

    def run(self):
        try:
            curses.curs_set(1 if self.needs_cursor else 0)
        except curses.error:
            pass
        try:
            while self.result is None:
                self.draw()
                self.handle_key(self.app.stdscr.getch())
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
        return self.result

    def draw(self) -> None:
        raise NotImplementedError

    def handle_key(self, key: int) -> None:
        """Handle key."""
        raise NotImplementedError


class InputDialog(Dialog):
    needs_cursor = True

    def __init__(self, app: Any, title: str, prompt: str, default: str = "") -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__(app, title)
        self.prompt = prompt
        self.text = default
        self.pos = len(default)

    def draw(self) -> None:
        y, x, hh, ww = center_box(self.app, 6, 70)
        self.app.stdscr.erase()
        draw_box(self.app.stdscr, y, x, hh, ww, self.title)
        safe_addstr(self.app.stdscr, y + 1, x + 2, self.prompt[: ww - 4])
        fy, fx, fw = y + 2, x + 2, ww - 4
        if self.pos < fw - 1:
            start, off = 0, self.pos
        else:
            start, off = self.pos - fw + 2, fw - 2
        disp = self.text[start:]
        safe_addstr(self.app.stdscr, fy, fx, disp[:fw].ljust(fw), curses.A_UNDERLINE)
        safe_addstr(self.app.stdscr, y + hh - 2, x + 2, "Enter=OK  Esc=Cancel"[: ww - 4])
        try:
            self.app.stdscr.move(fy, fx + min(off, fw - 1))
        except curses.error:
            pass
        self.app.stdscr.refresh()

    def handle_key(self, key: int) -> None:
        """Handle key."""
        if key == 27:
            self.result = None
            return
        if key in (curses.KEY_ENTER, 10, 13):
            self.result = self.text
            return
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if self.pos > 0:
                self.text = self.text[: self.pos - 1] + self.text[self.pos :]
                self.pos -= 1
        elif key == curses.KEY_DC:
            if self.pos < len(self.text):
                self.text = self.text[: self.pos] + self.text[self.pos + 1 :]
        elif key == curses.KEY_LEFT:
            self.pos = max(0, self.pos - 1)
        elif key == curses.KEY_RIGHT:
            self.pos = min(len(self.text), self.pos + 1)
        elif key == curses.KEY_HOME:
            self.pos = 0
        elif key == curses.KEY_END:
            self.pos = len(self.text)
        elif 32 <= key < 0x110000:
            try:
                ch = chr(key)
            except (ValueError, OverflowError):
                return
            self.text = self.text[: self.pos] + ch + self.text[self.pos :]
            self.pos += 1


class ConfirmDialog(Dialog):
    def __init__(self, app: Any, title: str, message: str) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__(app, title)
        self.message = message
        self.choice = 1

    def draw(self) -> None:
        lines = self.message.split("\n")
        box_h = max(7, 4 + len(lines))
        box_w = min(72, max(40, max(len(l) for l in lines) + 6))
        y, x, hh, ww = center_box(self.app, box_h, box_w)
        self.app.stdscr.erase()
        draw_box(self.app.stdscr, y, x, hh, ww, self.title)
        for i, ln in enumerate(lines[: hh - 4]):
            safe_addstr(self.app.stdscr, y + 2 + i, x + 2, ln[: ww - 4])
        yes = curses.A_REVERSE if self.choice == 0 else 0
        no = curses.A_REVERSE if self.choice == 1 else 0
        safe_addstr(self.app.stdscr, y + hh - 2, x + 2, "[ Yes ]", yes)
        safe_addstr(self.app.stdscr, y + hh - 2, x + 12, "[ No ]", no)
        self.app.stdscr.refresh()

    def handle_key(self, key: int) -> None:
        """Handle key."""
        if key == 27:
            self.result = False
        elif key in (curses.KEY_LEFT, curses.KEY_RIGHT, 9):
            self.choice = 1 - self.choice
        elif key in (ord("y"), ord("Y")):
            self.result = True
        elif key in (ord("n"), ord("N")):
            self.result = False
        elif key in (curses.KEY_ENTER, 10, 13):
            self.result = self.choice == 0


class MessageDialog(Dialog):
    def __init__(self, app: Any, title: str, message: str) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__(app, title)
        self.message = message

    def draw(self) -> None:
        lines = self.message.split("\n")
        box_h = max(7, 4 + len(lines))
        box_w = min(72, max(40, max(len(l) for l in lines) + 4))
        y, x, hh, ww = center_box(self.app, box_h, box_w)
        self.app.stdscr.erase()
        draw_box(self.app.stdscr, y, x, hh, ww, self.title)
        for i, ln in enumerate(lines[: hh - 4]):
            safe_addstr(self.app.stdscr, y + 2 + i, x + 2, ln[: ww - 4])
        safe_addstr(self.app.stdscr, y + hh - 2, x + 2, "[ OK ]", curses.A_REVERSE)
        self.app.stdscr.refresh()

    def handle_key(self, key: int) -> None:
        """Handle key."""
        if key in (27, curses.KEY_ENTER, 10, 13, ord(" "), ord("q")):
            self.result = True


class ViewerDialog(Dialog):
    def __init__(self, app: Any, path: Path) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__(app, f"View: {path.name}")
        self.path = path
        self.scroll = 0
        try:
            raw = path.read_bytes()[: 512 * 1024]
            self.content = raw.decode("utf-8", errors="replace").split("\n")
        except Exception as ex:
            self.content = [f"Cannot read file: {ex}"]

    def draw(self) -> None:
        h, w = self.app.stdscr.getmaxyx()
        y, x, hh, ww = center_box(self.app, h - 2, w - 4)
        self.app.stdscr.erase()
        draw_box(self.app.stdscr, y, x, hh, ww, f" {self.path} ")
        for i in range(hh - 3):
            idx = self.scroll + i
            if idx >= len(self.content):
                break
            ln = self.content[idx].replace("\t", "    ").replace("\r", "")
            safe_addstr(self.app.stdscr, y + 1 + i, x + 2, ln[: ww - 4])
        n = len(self.content)
        safe_addstr(
            self.app.stdscr,
            y + hh - 2,
            x + 2,
            f"Line {min(self.scroll + 1, n)}/{n}  q=exit"[: ww - 4],
            curses.A_REVERSE,
        )
        self.app.stdscr.refresh()

    def handle_key(self, key: int) -> None:
        """Handle key."""
        page = max(1, self.app.stdscr.getmaxyx()[0] - 6)
        n = len(self.content)
        if key in (ord("q"), 27, curses.KEY_F3, curses.KEY_F10):
            self.result = True
        elif key in (curses.KEY_UP, ord("k")):
            self.scroll = max(0, self.scroll - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.scroll = min(max(0, n - 1), self.scroll + 1)
        elif key == curses.KEY_PPAGE:
            self.scroll = max(0, self.scroll - page)
        elif key == curses.KEY_NPAGE:
            self.scroll = min(max(0, n - 1), self.scroll + page)
        elif key == curses.KEY_HOME:
            self.scroll = 0
        elif key == curses.KEY_END:
            self.scroll = max(0, n - 1)


HELP_TEXT: str = """\
Midnight Commander (Python) - Quick Help
  Tab           Switch panel
  Up/Down j/k   Move cursor
  PgUp/PgDn     Page
  Home/End      Top / bottom
  Enter / ->    Enter dir / view file
  <- / BS / h   Parent dir
  Space / Ins   Toggle selection
  a / u         Select all / none
  r             Refresh
  F1  ?         This help
  F3  v         View file
  F4  e         Edit file in $EDITOR
  F5  c         Copy to other panel
  F6  m         Move / rename
  F7  d         Make directory
  F8  x         Delete (asks confirmation)
  F10 q         Quit
"""


class FileManager:
    def __init__(self, stdscr: Any, start: Path) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.stdscr = stdscr
        self.left = Panel(start)
        self.right = Panel(start)
        self.active_side = "left"
        self.entries_h = 1
        self.message = ""

    @property
    def active(self) -> Panel:
        """Return the active."""
        return self.left if self.active_side == "left" else self.right

    @property
    def inactive(self) -> Panel:
        """Return the inactive."""
        return self.right if self.active_side == "left" else self.left

    def run(self) -> None:
        while True:
            self.draw()
            if not self.handle_key(self.stdscr.getch()):
                break

    def draw(self) -> None:
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if h < 10 or w < 40:
            safe_addstr(self.stdscr, 0, 0, "Terminal too small (need >=40x10)")
            self.stdscr.refresh()
            return
        panel_h = h - 2
        mid = w // 2
        self.entries_h = max(1, panel_h - 3)
        self._draw_panel(self.left, 0, 0, panel_h, mid, "left")
        self._draw_panel(self.right, 0, mid, panel_h, w - mid, "right")
        if self.message:
            safe_addstr(self.stdscr, h - 2, 0, (" " + self.message)[:w], curses.A_REVERSE)
            self.message = ""
        fkeys = "1Help  2Menu  3View  4Edit  5Copy  6Move  7Mkdir 8Delete 9Menu 10Quit"
        safe_addstr(self.stdscr, h - 1, 0, fkeys[:w], curses.A_REVERSE)
        self.stdscr.refresh()

    def _draw_panel(self, panel: Panel, y: int, x: int, h: int, w: int, side: Any) -> None:
        if w < 4 or h < 3:
            return
        active = side == self.active_side
        safe_addstr(self.stdscr, y, x, "┌" + "─" * (w - 2) + "┐")
        for i in range(1, h - 1):
            safe_addstr(self.stdscr, y + i, x, "│" + " " * (w - 2) + "│")
        safe_addstr(self.stdscr, y + h - 1, x, "└" + "─" * (w - 2) + "┘")
        title = f" {panel.path} "[: w - 4]
        safe_addstr(
            self.stdscr,
            y,
            x + 2,
            title,
            curses.A_BOLD | (curses.A_REVERSE if active else 0),
        )
        entries_h = max(1, h - 3)
        if panel.cursor < panel.scroll:
            panel.scroll = panel.cursor
        elif panel.cursor >= panel.scroll + entries_h:
            panel.scroll = panel.cursor - entries_h + 1
        for i in range(entries_h):
            idx = panel.scroll + i
            if idx >= len(panel.entries):
                continue
            e = panel.entries[idx]
            prefix = "*" if e.path in panel.selected else " "
            body = self._format_entry(e, w - 3)
            line = (prefix + body)[: w - 2].ljust(w - 2)
            if idx == panel.cursor and active:
                attr = curses.A_REVERSE
            elif idx == panel.cursor:
                attr = curses.A_UNDERLINE
            elif e.path in panel.selected:
                attr = curses.A_BOLD
            else:
                attr = curses.A_NORMAL
            safe_addstr(self.stdscr, y + 1 + i, x + 1, line, attr)
        cur = panel.current()
        status = f"{cur.size_str:>6} {safe_mtime(cur.mtime)}  {stat.filemode(cur.mode)}" if cur else ""
        safe_addstr(
            self.stdscr,
            y + h - 2,
            x + 1,
            (" " + status)[: w - 1].ljust(w - 2),
            curses.A_DIM if active else curses.A_NORMAL,
        )

    @staticmethod
    def _format_entry(e: FileEntry, avail: int) -> str:
        size_w, mtime_w = 6, 16
        name_w = max(5, avail - size_w - mtime_w - 2)
        name = e.display_name
        if len(name) > name_w:
            name = name[: name_w - 1] + "~"
        return f"{name:<{name_w}} {e.size_str:>6} {safe_mtime(e.mtime):>16}"

    def handle_key(self, key: int) -> bool:
        """Handle key."""

        def F(n: int) -> int:
            return curses.KEY_F0 + n

        if key == F(10) or key == ord("q"):
            return False
        if key in (F(1), ord("?")):
            MessageDialog(self, "Help", HELP_TEXT).run()
        elif key in (F(2), F(9), ord("r")):
            self._refresh_both()
        elif key in (F(3), ord("v")):
            self._view_file()
        elif key in (F(4), ord("e")):
            self._edit_file()
        elif key in (F(5), ord("c")):
            self._copy_files()
        elif key in (F(6), ord("m")):
            self._move_files()
        elif key in (F(7), ord("d")):
            self._make_dir()
        elif key in (F(8), ord("x")):
            self._delete_files()
        elif key == 9:
            self.active_side = "right" if self.active_side == "left" else "left"
        elif key in (curses.KEY_UP, ord("k")):
            self.active.move_cursor(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.active.move_cursor(1)
        elif key == curses.KEY_PPAGE:
            self.active.move_cursor(-self.entries_h)
        elif key == curses.KEY_NPAGE:
            self.active.move_cursor(self.entries_h)
        elif key in (curses.KEY_HOME, ord("g")):
            self.active.cursor = 0
        elif key in (curses.KEY_END, ord("G")):
            self.active.cursor = max(0, len(self.active.entries) - 1)
        elif key in (curses.KEY_ENTER, 10, 13, curses.KEY_RIGHT, ord("l")):
            self._enter()
        elif key in (curses.KEY_BACKSPACE, 127, 8, curses.KEY_LEFT, ord("h")):
            self.active.change_dir(self.active.path.parent)
        elif key in (curses.KEY_IC, ord(" ")):
            self.active.toggle_select()
        elif key == ord("a"):
            self.active.select_all()
        elif key == ord("u") or key == 27:
            self.active.clear_selection()
        return True

    def _refresh_both(self) -> None:
        self.left.refresh()
        self.right.refresh()

    def _enter(self) -> None:
        e = self.active.current()
        if e is None:
            return
        try:
            if e.path.is_dir():
                self.active.change_dir(e.path)
                return
        except OSError:
            pass
        self._view_file()

    def _view_file(self) -> None:
        e = self.active.current()
        if e is None or not e.path.is_file():
            self.message = "Not a regular file"
            return
        ViewerDialog(self, e.path).run()

    def _edit_file(self) -> None:
        e = self.active.current()
        if e is None or not e.path.is_file():
            self.message = "Not a regular file"
            return
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        try:
            curses.def_prog_mode()
            curses.endwin()
            subprocess.run([editor, str(e.path)])
        except Exception as ex:
            self.message = f"Editor error: {ex}"
        finally:
            try:
                curses.reset_prog_mode()
            except curses.error:
                pass
            self.stdscr.clear()
            self.stdscr.refresh()
        self.active.refresh()

    def _make_dir(self) -> None:
        name = InputDialog(self, "Create Directory", "Directory name:").run()
        if not name:
            return
        target = self.active.path / name
        try:
            target.mkdir()
            self.active.refresh()
            self.message = f"Created {target}"
        except Exception as ex:
            self.message = f"mkdir: {ex}"

    def _copy_files(self) -> None:
        sources = self.active.targets()
        if not sources:
            self.message = "Nothing selected"
            return
        dest = InputDialog(
            self,
            "Copy",
            f"Copy {len(sources)} item(s) to:",
            default=str(self.inactive.path),
        ).run()
        if not dest:
            return
        dest_path = Path(dest).expanduser()
        try:
            dest_path = dest_path.resolve()
        except OSError:
            pass
        if not dest_path.is_dir():
            self.message = f"Not a directory: {dest_path}"
            return
        if all(s.parent == dest_path for s in sources):
            self.message = "Source and destination are identical"
            return
        existing = [s for s in sources if (dest_path / s.name).exists()]
        if (
            existing
            and not ConfirmDialog(self, "Overwrite", f"{len(existing)} item(s) already exist.\nOverwrite?").run()
        ):
            return
        ok = 0
        for src in sources:
            dst = dest_path / src.name
            try:
                if dst.exists() or dst.is_symlink():
                    if dst.is_dir() and not dst.is_symlink():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                if src.is_dir() and not src.is_symlink():
                    shutil.copytree(src, dst, symlinks=True)
                else:
                    shutil.copy2(src, dst, follow_symlinks=False)
                ok += 1
            except Exception as ex:
                self.message = f"Copy error on {src.name}: {ex}"
                break
        self.active.clear_selection()
        self._refresh_both()
        if ok and not self.message:
            self.message = f"Copied {ok} item(s)"

    def _move_files(self) -> None:
        sources = self.active.targets()
        if not sources:
            self.message = "Nothing selected"
            return
        dest = InputDialog(
            self,
            "Move / Rename",
            f"Move {len(sources)} item(s) to:",
            default=str(self.inactive.path),
        ).run()
        if not dest:
            return
        dest_path = Path(dest).expanduser()
        if len(sources) == 1 and not dest_path.is_dir():
            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(sources[0]), str(dest_path))
                self.active.clear_selection()
                self._refresh_both()
                self.message = f"Renamed to {dest_path.name}"
            except Exception as ex:
                self.message = f"Move error: {ex}"
            return
        try:
            dest_path = dest_path.resolve()
        except OSError:
            pass
        if not dest_path.is_dir():
            self.message = f"Not a directory: {dest_path}"
            return
        existing = [s for s in sources if (dest_path / s.name).exists()]
        if (
            existing
            and not ConfirmDialog(self, "Overwrite", f"{len(existing)} item(s) already exist.\nOverwrite?").run()
        ):
            return
        ok = 0
        for src in sources:
            dst = dest_path / src.name
            try:
                if dst.exists() or dst.is_symlink():
                    if dst.is_dir() and not dst.is_symlink():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                shutil.move(str(src), str(dst))
                ok += 1
            except Exception as ex:
                self.message = f"Move error on {src.name}: {ex}"
                break
        self.active.clear_selection()
        self._refresh_both()
        if ok and not self.message:
            self.message = f"Moved {ok} item(s)"

    def _delete_files(self) -> None:
        sources = self.active.targets()
        if not sources:
            self.message = "Nothing selected"
            return
        for src in sources:
            try:
                rs = src.resolve()
            except OSError:
                rs = src
            for p in (self.left.path, self.right.path):
                try:
                    if rs == p or rs in p.parents:
                        self.message = f"Refusing to delete {src.name}: contains current directory"
                        return
                except OSError:
                    pass
        listing = "\n".join("  " + s.name for s in sources[:10])
        if len(sources) > 10:
            listing += f"\n  ... and {len(sources) - 10} more"
        if not ConfirmDialog(
            self,
            "Delete",
            f"Delete {len(sources)} item(s)?\n\n{listing}\n\nThis cannot be undone.",
        ).run():
            return
        for src in sources:
            try:
                if src.is_dir() and not src.is_symlink():
                    shutil.rmtree(src)
                else:
                    src.unlink()
            except Exception as ex:
                self.message = f"Delete error on {src.name}: {ex}"
        self.active.clear_selection()
        self._refresh_both()


def main(stdscr: Any) -> None:
    """Run the command-line interface."""
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    start = Path.cwd()
    if len(sys.argv) > 1:
        try:
            start = Path(sys.argv[1]).expanduser().resolve()
        except OSError:
            start = Path.cwd()
    if not start.is_dir():
        start = Path.home()
    FileManager(stdscr, start).run()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
