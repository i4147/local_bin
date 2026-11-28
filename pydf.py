#!/data/data/com.termux/files/usr/bin/env python
"""df-style filesystem usage report with colourised terminal output.

CLI entry point; reads mounted filesystems via os.statvfs and renders a coloured
table."""
from __future__ import annotations

from math import log
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
from typing import Any
import unicodedata

import typer


app = typer.Typer(add_help_option=False)

COLOURS: dict[str, str] = {
    "none": "",
    "default": "\033[0m",
    "bold": "\033[1m",
    "underline": "\033[4m",
    "blink": "\033[5m",
    "reverse": "\033[7m",
    "concealed": "\033[8m",
    "black": "\033[30m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "on_black": "\033[40m",
    "on_red": "\033[41m",
    "on_green": "\033[42m",
    "on_yellow": "\033[43m",
    "on_blue": "\033[44m",
    "on_magenta": "\033[45m",
    "on_cyan": "\033[46m",
    "on_white": "\033[47m",
    "beep": "\007",
}


class Bar:
    def __init__(self, percentage: float = 0.0, width: int = 2, header: bool = False) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.percentage = percentage
        self.width = width
        self.header = header

    def __len__(self) -> int:
        """Return the number of items represented by the object."""
        return self.width

    def __str__(self) -> str:
        """Return the object's display string."""
        return self.format("l")

    def format(self, pos: str) -> str:
        if self.header:
            return " " * self.width
        size = round(self.percentage * (self.width - 2))
        return "[" + manglestring(size * BARCHAR, self.width - 2, pos, BAR_FILLCHAR) + "]"


def get_terminal_width_termios() -> int | None:
    """Return terminal width termios."""
    try:
        import fcntl
        import termios
    except ImportError:
        return None
    s = struct.pack("HHHH", 0, 0, 0, 0)
    try:
        _lines, cols, _xpixels, _ypixels = struct.unpack(
            "HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, s)
        )
    except (OSError, AttributeError):
        return None
    return cols


def get_terminal_width_resize() -> int | None:
    """Return terminal width resize."""
    status, output = subprocess.getstatusoutput("resize")
    if status != 0:
        return None
    c = [x for x in output.split("\n") if x.startswith("COLUMNS=")]
    if c:
        val = c[0].split("=", 1)[1]
        val = val.removesuffix(";")
        try:
            return int(val)
        except ValueError:
            return None
    return None


def get_terminal_width() -> int:
    """Return terminal width."""
    for handler in [get_terminal_width_termios, get_terminal_width_resize]:
        width = handler()
        if width:
            return width
    return 80


def find_mountpoint(path: str) -> str | None:
    """Find mountpoint."""
    if not os.path.lexists(path):
        sys.stderr.write(f"pydf: {path!r}: No such file or directory\n")
        return None
    while not os.path.ismount(path):
        path = os.path.dirname(path)
    return path


def sanitize_output(s: str) -> str:
    """Sanitize output."""
    r = ""
    for c in s:
        code = ord(c)
        if code < 32:
            r += f"\\x{code:02x}"
        elif 0xDC80 <= code <= 0xDCFF:
            r += f"\\x{code - 0xDC00:02x}"
        elif unicodedata.category(c)[0] == "C" or unicodedata.category(c) in (
            "Zl",
            "Zp",
        ):
            if code <= 0xFFFF:
                r += f"\\u{code:04X}"
            else:
                r += f"\\U{code:08X}"
        else:
            r += c
    return r


def out(s: str) -> None:
    try:
        sys.stdout.write(s)
    except UnicodeEncodeError:
        sys.stdout.write(s.encode("ascii", "ignore").decode())


class DumbStatus:
    f_bsize = f_frsize = f_blocks = f_bfree = f_bavail = f_files = f_ffree = f_favail = f_flag = f_namemax = 0


def hfnum(size: int, base: int, inodes: bool) -> str:
    if size == 0:
        return "0"
    if size < 0:
        return "?"
    units = [""] if inodes else ["B"]
    units += ["k", "M", "G", "T", "P", "Z", "Y"]
    power = int(log(size) / log(base)) if size > 0 else 0
    power = max(0, min(power, len(units) - 1))
    nsize = round(1.0 * size / (base**power))
    if nsize < 10 and power >= 1:
        power -= 1
        nsize = round(1.0 * size / (base**power))
    return str(nsize) + units[power]


def myformat(number: int, sizeformat: str, fs_blocksize: int, blocksize: int, inodes: bool) -> str:
    size = int(number) * fs_blocksize
    if blocksize:
        sn = round(1.0 * size / blocksize)
        return str(int(sn))
    if sizeformat == "-k":
        return str(round(size / 1024.0))
    elif sizeformat == "-m":
        return str(round(size / (1024.0 * 1024)))
    elif sizeformat == "-g":
        return str(round(size / (1024.0 * 1024 * 1024)))
    elif sizeformat == "-h":
        return hfnum(size, 1024, inodes)
    elif sizeformat == "-H":
        return hfnum(size, 1000, inodes)
    elif sizeformat == "--blocks":
        return str(number)
    else:
        raise ValueError(f"Impossible error, sizeformat={sizeformat!r}")


def manglestring(s: str, l: int, pos: str, fillchar: str = " ") -> str:
    if pos == "r":
        ns = s.rjust(l, fillchar)
    elif pos == "l":
        ns = s.ljust(l, fillchar)
    elif pos == "c":
        ns = s.center(l, fillchar)
    else:
        raise ValueError("Error in manglestring")
    if len(ns) > l:
        ns = ns[: int(l / 2)] + "~" + ns[-int(l / 2) + 1 :]
    return ns


def makecolour(clist: str | list[str]) -> str:
    if isinstance(clist, str):
        lclist = [clist]
    else:
        lclist = clist
    return "".join(COLOURS[i] for i in lclist if i in COLOURS)


def get_all_mountpoints(
    mountfile: str | list[str],
) -> dict[str, tuple[str, str, list[str]]]:
    """Return all mountpoints."""
    dummy_result = {"/": ("/", "", [])}
    f = None
    if isinstance(mountfile, str):
        if Path(mountfile).exists():
            f = open(mountfile, "rb")
    else:
        for i in mountfile:
            if Path(i).exists():
                f = open(i, "rb")
                break

    if f is None:
        status, mout = subprocess.getstatusoutput("mount")
        if status != 0:
            return dummy_result
        r = {}
        for line in mout.split("\n"):
            if " on " not in line:
                continue
            device, on = line.split(" on ", 1)
            device = device.split()[0]
            onparts = on.split()
            on = onparts[0]
            opts = onparts[-1][1:-1].split(",")
            r[on] = (device, "", opts)
        return r if r else dummy_result

    mountlines = f.readlines()
    f.close()
    errhandler = "surrogateescape" if sys.version_info >= (3, 1) else "replace"
    mountlines = [x.decode(sys.stdin.encoding or "utf-8", errhandler) for x in mountlines]
    r = {}
    for l in mountlines:
        spl = l.split()
        if len(spl) < 4:
            continue
        device, mp, typ, opts = spl[0:4]
        r[mp] = (device, typ, opts.split(","))
    return r


def niceprint_fs(fs: str) -> str:
    match_obj = re.search(r"^\/dev\/mapper\/(.*)-(.*)", str(fs))
    if match_obj:
        return f"/dev/{match_obj.group(1)}/{match_obj.group(2)}"
    return fs


def is_remote_fs(fs: str) -> bool:
    """Return whether remote fs."""
    return fs.lower() in [
        "nfs",
        "smbfs",
        "cifs",
        "ncpfs",
        "afs",
        "coda",
        "ftpfs",
        "mfs",
        "sshfs",
        "fuse.sshfs",
        "nfs4",
    ]


def is_special_fs(fs: str) -> bool:
    """Return whether special fs."""
    return fs.lower() in [
        "tmpfs",
        "devpts",
        "devtmpfs",
        "proc",
        "sysfs",
        "usbfs",
        "devfs",
        "fdescfs",
        "linprocfs",
    ]


BARCHAR: str = "#"
BAR_FILLCHAR: str = "."


@app.command()
def main(
    all_fss: bool = typer.Option(False, "--all", "-a", help="include filesystems having 0 blocks"),
    human_readable: bool = typer.Option(False, "--human-readable", "-h", help="print sizes in human readable format"),
    si: bool = typer.Option(False, "--si", "-H", help="use powers of 1000 not 1024"),
    blocksize: int = typer.Option(0, "--block-size", "-b", help="use BLOCKSIZE-byte blocks"),
    local_only: bool = typer.Option(False, "--local", "-l", help="limit listing to local filesystems"),
    kilobytes: bool = typer.Option(False, "--kilobytes", "-k", help="like --block-size=1024"),
    megabytes: bool = typer.Option(False, "--megabytes", "-m", help="like --block-size=1048576"),
    gigabytes: bool = typer.Option(False, "--gigabytes", "-g", help="like --block-size=1073741824"),
    blocks: bool = typer.Option(False, "--blocks", help="use filesystem native block size"),
    bw: bool = typer.Option(False, "--bw", help="do not use colours"),
    mounts_file: str | None = typer.Option(None, "--mounts", help="File to get mount information from"),
    show_binds: bool = typer.Option(True, "--show-binds", "-B", help="show 'mount --bind' mounts"),
    inodes: bool = typer.Option(False, "--inodes", "-i", help="show inode instead of block usage"),
    args: list[str] = typer.Argument(None),
) -> None:
    """Run the command-line interface."""
    sizeformat = "-h"
    if human_readable:
        sizeformat = "-h"
    elif si:
        sizeformat = "-H"
    elif kilobytes:
        sizeformat = "-k"
    elif megabytes:
        sizeformat = "-m"
    elif gigabytes:
        sizeformat = "-g"
    elif blocks:
        sizeformat = "--blocks"

    normal_colour = "default"
    header_colour = "yellow"
    local_fs_colour = "default"
    remote_fs_colour = "green"
    special_fs_colour = "blue"
    readonly_fs_colour = "cyan"
    filled_fs_colour = "red"
    full_fs_colour = "on_red"

    if bw:
        normal_colour = header_colour = local_fs_colour = remote_fs_colour = special_fs_colour = filled_fs_colour = (
            full_fs_colour
        ) = "none"

    mountfile = mounts_file or ["/etc/mtab", "/etc/mnttab", "/proc/mounts"]
    terminal_width = get_terminal_width()
    mountpoints = get_all_mountpoints(mountfile)

    fmt_def = [
        ("fs", 10, "l"),
        ("size", 5, "r"),
        ("used", 5, "r"),
        ("avail", 5, "r"),
        ("perc", 4, "r"),
        ("bar", 0.1, "l"),
        ("on", 11, "l"),
    ]

    header_dict = {
        "fs": "Filesystem",
        "size": "Nodes" if inodes else "Size",
        "used": "Used",
        "avail": "Avail",
        "on": "Mounted on",
        "fstype": "Type",
        "perc": "Use%",
        "bar": Bar(header=True),
    }

    def get_row_mp(mp: str | None) -> list[tuple[str, Any]] | None:
        if mp:
            if mp in mountpoints:
                device, fstype, opts = mountpoints[mp]
                device = niceprint_fs(device)
            else:
                device, fstype, opts = "-", "-", "-"
            rdonly = "ro" in opts or fstype in ("iso9660", "udf")
            bind = "bind" in opts or "rbind" in opts

            try:
                status = os.statvfs(mp)
            except OSError:
                status = DumbStatus()
            fs_blocksize = status.f_bsize if status.f_bsize != 0 else status.f_frsize
            free = status.f_bfree
            size = status.f_blocks
            avail = status.f_bavail
            inodes_free = status.f_ffree
            inodes_size = status.f_files
            inodes_avail = status.f_favail

            if (size == 0 or is_special_fs(fstype)) and not all_fss:
                return None
            if bind and not show_binds:
                return None

            used = size - free
            inodes_used = inodes_size - inodes_free

            if inodes:
                size_f = myformat(inodes_size, sizeformat, 1, blocksize, inodes)
                used_f = myformat(inodes_used, sizeformat, 1, blocksize, inodes)
                avail_f = myformat(inodes_avail, sizeformat, 1, blocksize, inodes)
                try:
                    perc = round(100.0 * inodes_used / inodes_size, 1)
                    perc_f = str(perc)
                except ZeroDivisionError:
                    perc = 0
                    perc_f = "-"
            else:
                size_f = myformat(size, sizeformat, fs_blocksize, blocksize, inodes)
                used_f = myformat(used, sizeformat, fs_blocksize, blocksize, inodes)
                avail_f = myformat(avail, sizeformat, fs_blocksize, blocksize, inodes)
                try:
                    perc = round(100.0 * used / size, 1)
                    perc_f = str(perc)
                except ZeroDivisionError:
                    perc = 0
                    perc_f = "-"

            info = {
                "fs": device,
                "size": size_f,
                "used": used_f,
                "avail": avail_f,
                "on": mp,
                "fstype": fstype,
                "perc": perc_f,
                "bar": None,
            }
            current_colour = local_fs_colour
            if is_remote_fs(fstype):
                current_colour = remote_fs_colour
            elif size == 0 or is_special_fs(fstype):
                current_colour = special_fs_colour
        else:
            current_colour = header_colour

        row = []
        for j in fmt_def:
            col_name, width_val, _pos = j
            if col_name == "bar" and 0 < width_val < 1:
                width_val = int(width_val * terminal_width) - 1

            if mp:
                if col_name in ["perc", "avail", "bar"]:
                    if rdonly:
                        current_colour = readonly_fs_colour
                    elif perc > 99.0:
                        current_colour = full_fs_colour
                    elif perc > 95.0:
                        current_colour = filled_fs_colour
                if col_name == "bar":
                    info["bar"] = Bar(perc / 100.0, int(width_val))

                text = info[col_name]
                if not isinstance(text, Bar):
                    text = sanitize_output(str(text))
            else:
                text = header_dict[col_name]
                if col_name == "bar":
                    text.width = int(width_val)

            row.append([current_colour, text])
        return row

    def squeeze_table(table: list[list[tuple[str, Any]]], desired_width: int) -> None:
        cols = len(table[0])
        minrow = []
        for j in fmt_def:
            w = j[1]
            if 0 < w < 1:
                w = int(w * terminal_width) - 1
            minrow.append(int(w))

        maxrow = [0] * cols
        for row in table:
            for col in range(cols):
                maxrow[col] = max(maxrow[col], len(str(row[col][1])))

        deltarow = [maxrow[i] - minrow[i] for i in range(cols)]
        deltas = sorted(zip(deltarow, range(cols)), reverse=True)

        to_reduce = sum(maxrow) + (cols - 1) * len(" ") - desired_width
        new_maxrow = maxrow[:]
        for delta, i in deltas:
            if to_reduce < 0:
                break
            if delta >= to_reduce:
                new_maxrow[i] -= to_reduce
                to_reduce = 0
                break
            else:
                new_maxrow[i] -= delta
                to_reduce -= delta

        if to_reduce > 0:
            new_maxrow = maxrow

        for row in table:
            for col in range(cols):
                cell_content = row[col][1]
                if isinstance(cell_content, Bar):
                    cell_content.width += int(-to_reduce * 0.3) if to_reduce < 0 else 0
                    row[col][1] = cell_content.format(fmt_def[col][2])
                else:
                    row[col][1] = manglestring(str(cell_content), new_maxrow[col], fmt_def[col][2])

    def display_table(table: list[list[tuple[str, Any]]]) -> None:
        squeeze_table(table, terminal_width - 1)
        colsepcol = makecolour("none")
        for row in table:
            firstcol = True
            for colourcode, text in row:
                if firstcol:
                    firstcol = False
                else:
                    out(colsepcol)
                    out(" ")
                out(makecolour(colourcode))
                out(str(text))
            out("")
            out(makecolour(normal_colour))
            out("\n")

    if args:
        mp_to_display = [find_mountpoint(os.path.realpath(x)) for x in args]
        mp_to_display = [x for x in mp_to_display if x is not None]
    else:
        mp_to_display = list(mountpoints.keys())
        if local_only:
            mp_to_display = [x for x in mp_to_display if not is_remote_fs(mountpoints[x][1])]
        mp_to_display.sort()

    table = [get_row_mp(None)]
    for mp in mp_to_display:
        r = get_row_mp(mp)
        if r is not None:
            table.append(r)

    display_table(table)


if __name__ == "__main__":
    app()
