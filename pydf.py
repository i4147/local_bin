#!/data/data/com.termux/files/usr/bin/env python
"Utilities for pydf."

from math import log
import os
from pathlib import Path
import re
import struct
import sys
from typing import Any, Optional
import unicodedata

import typer

app = typer.Typer(add_completion=False)

colours: dict[str, str] = {
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
        "Initialize the instance state from the supplied arguments."
        self.percentage = percentage
        self.width = width
        self.header = header

    def __len__(self) -> int:
        "Return the number of items represented by the object."
        return self.width

    def __str__(self) -> str:
        "Return the object's display string."
        return self.format("l")

    def format(self, pos: str) -> str:
        if self.header:
            return " " * self.width
        size = round(self.percentage * (self.width - 2))
        return "[" + manglestring(size * barchar, self.width - 2, pos, bar_fillchar) + "]"


class DumbStatus:
    f_bsize = f_frsize = f_blocks = f_bfree = f_bavail = f_files = f_ffree = f_favail = f_flag = f_namemax = 0


def get_terminal_width_termios() -> Optional[int]:
    "Return terminal width termios."
    try:
        import fcntl
        import termios
    except ImportError:
        return None
    s = struct.pack("HHHH", 0, 0, 0, 0)
    try:
        _, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, s))
    except (IOError, AttributeError):
        return None
    return cols


def get_terminal_width_resize() -> Optional[int]:
    "Return terminal width resize."
    import subprocess

    status, output = subprocess.getstatusoutput("resize")
    if status != 0:
        return None
    c = output.split("\n")
    c = [x for x in c if x.startswith("COLUMNS=")]
    if c:
        c = c[0]
        _, c = c.split("=", 1)
        if c and c[-1] == ";":
            c = c[:-1]
    if c:
        return int(c)
    return None


def get_terminal_width_dumb() -> int:
    "Return terminal width dumb."
    return 80


def get_terminal_width() -> int:
    "Return terminal width."
    for handler in [get_terminal_width_termios, get_terminal_width_resize, get_terminal_width_dumb]:
        width = handler()
        if width:
            return width
    return 80


def find_mountpoint(path: str) -> Optional[str]:
    "Find mountpoint."
    if not Path(path).exists():
        sys.stderr.write(f"pydf: {path!r}: No such file or directory\n")
        return None
    while not Path(path).is_mount():
        path = str(Path(path).parent)
    return path


def sanitize_output(s: str) -> str:
    r = ""
    for c in s:
        code = ord(c)
        if code < 32:
            r += f"\\x{code:02x}"
        elif 0xDC80 <= code <= 0xDCFF:
            r += f"\\x{code - 0xDC00:02x}"
        elif unicodedata.category(c)[0] == "C" or unicodedata.category(c) in ("Zl", "Zp"):
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


def hfnum(size: int, base: int) -> str:
    if size == 0:
        return "0"
    if size < 0:
        return "?"
    units = [""] if inodes else ["B"]
    units += ["k", "M", "G", "T", "P", "Z", "Y"]
    power = int(log(size) / log(base))
    power = max(power, 0)
    if power >= len(units):
        power = len(units) - 1
    nsize = round(1.0 * size / (base**power))
    if nsize < 10 and power >= 1:
        power -= 1
        nsize = round(1.0 * size / (base**power))
    return str(nsize) + units[power]


def myformat(number: float, sizeformat: str, fs_blocksize: int) -> str:
    size = int(number) * fs_blocksize
    if blocksize:
        return str(round(1.0 * size / blocksize))
    if sizeformat == "-k":
        return str(round(size / 1024.0))
    elif sizeformat == "-m":
        return str(round(size / (1024.0 * 1024)))
    elif sizeformat == "-g":
        return str(round(size / (1024.0 * 1024 * 1024)))
    elif sizeformat == "-h":
        return hfnum(size, 1024)
    elif sizeformat == "-H":
        return hfnum(size, 1000)
    elif sizeformat == "--blocks":
        return str(number)
    else:
        msg = f"Impossible error, sizeformat={sizeformat!r}"
        raise ValueError(msg)


def manglestring(s: str, l: int, pos: str, fillchar: str = " ") -> str:
    if pos == "r":
        ns = s.rjust(l, fillchar)
    elif pos == "l":
        ns = s.ljust(l, fillchar)
    elif pos == "c":
        ns = s.center(l, fillchar)
    else:
        msg = "Error in manglestring"
        raise ValueError(msg)
    if len(ns) > l:
        ns = ns[: int(l / 2)] + "~" + ns[-int(l / 2) + 1 :]
    return ns


def makecolour(clist: str | list[str]) -> str:
    s = ""
    lclist = [clist] if isinstance(clist, str) else clist
    for i in lclist:
        s += colours.get(i, "")
    return s


def get_all_mountpoints() -> dict[str, tuple[str, str, list[str]]]:
    "Return all mountpoints."
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

    if not f:
        import subprocess

        status, mout = subprocess.getstatusoutput("mount")
        if status != 0:
            return dummy_result
        mlines = mout.split("\n")
        r = {}
        for line in mlines:
            if " on " not in line:
                continue
            device, on = line.split(" on ", 1)
            device = device.split()[0]
            onparts = on.split()
            on = onparts[0]
            opts = onparts[-1][1:-1].split(",")
            r[on] = (device, "", opts)
        return r or dummy_result

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


def niceprint_fs(fs: Any) -> Any:
    matchObj = re.search(r"^\/dev\/mapper\/(.*)-(.*)", str(fs))
    if matchObj:
        return "/dev/" + matchObj.group(1) + "/" + matchObj.group(2)
    return fs


def is_remote_fs(fs: str) -> bool:
    "Return whether remote fs."
    return fs.lower() in ["nfs", "smbfs", "cifs", "ncpfs", "afs", "coda", "ftpfs", "mfs", "sshfs", "fuse.sshfs", "nfs4"]


def is_special_fs(fs: str) -> bool:
    "Return whether special fs."
    return fs.lower() in ["tmpfs", "devpts", "devtmpfs", "proc", "sysfs", "usbfs", "devfs", "fdescfs", "linprocfs"]


def get_row_mp(mp: Optional[str]) -> Optional[list[tuple[str, Any]]]:
    "Return row mp."
    global \
        local_fs_colour, \
        remote_fs_colour, \
        special_fs_colour, \
        readonly_fs_colour, \
        full_fs_colour, \
        filled_fs_colour, \
        header_colour
    if mp:
        if mp in mountpoints:
            device, fstype, opts = mountpoints[mp]
            device = niceprint_fs(device)
        else:
            device, fstype, opts = "-", "-", []
        rdonly = "ro" in opts or fstype in ("iso9660", "udf")
        bind = "bind" in opts or "rbind" in opts

        try:
            status = os.statvfs(mp)
        except (OSError, IOError):
            status = DumbStatus()

        fs_blocksize = status.f_bsize if status.f_bsize != 0 else status.f_frsize
        free = status.f_bfree
        size = status.f_blocks
        avail = status.f_bavail
        inodes_free = status.f_ffree
        inodes_size = status.f_files
        inodes_avail = status.f_favail

        if (size == 0 or is_special_fs(fstype)) and not allfss:
            return None
        if bind and hidebinds:
            return None

        used = size - free
        inodes_used = inodes_size - inodes_free

        if inodes:
            size_f = myformat(inodes_size, sizeformat, 1)
            used_f = myformat(inodes_used, sizeformat, 1)
            avail_f = myformat(inodes_avail, sizeformat, 1)
            try:
                perc = round(100.0 * inodes_used / inodes_size, 1)
                perc_f = str(perc)
            except ZeroDivisionError:
                perc = 0
                perc_f = "-"
        else:
            size_f = myformat(size, sizeformat, fs_blocksize)
            used_f = myformat(used, sizeformat, fs_blocksize)
            avail_f = myformat(avail, sizeformat, fs_blocksize)
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
        perc = 0.0

    row = []
    width = 2
    for j in format_spec:
        if j[0] == "bar":
            width = j[1]
            if 0 < width < 1:
                width = int(width * terminal_width) - 1

        if mp:
            if j[0] in ["perc", "avail", "bar"]:
                if rdonly:
                    current_colour = readonly_fs_colour
                elif perc > FULL_THRESH:
                    current_colour = full_fs_colour
                elif perc > FILL_THRESH:
                    current_colour = filled_fs_colour
            if j[0] == "bar":
                info["bar"] = Bar(perc / 100.0, width)

            text = info[j[0]]
            if not isinstance(text, Bar):
                text = sanitize_output(text)
        else:
            text = header[j[0]]
            if j[0] == "bar":
                text.width = width

        row.append([current_colour, text])

    return row


def get_table(mps: list[str]) -> list[list[tuple[str, Any]]]:
    "Return table."
    rows = [get_row_mp(None)]
    for mp in mps:
        row = get_row_mp(mp)
        if row is not None:
            rows.append(row)
    return rows


def squeeze_table(table: list[list[tuple[str, Any]]], desired_width: int) -> None:
    cols = len(table[0])
    minrow = []
    for j in format_spec:
        width = j[1]
        if 0 < width < 1:
            width = int(width * terminal_width) - 1
        minrow.append(width)

    maxrow = [0] * cols
    for row in table:
        for col in range(cols):
            maxrow[col] = max(maxrow[col], len(row[col][1]))

    deltarow = [maxrow[i] - minrow[i] for i in range(cols)]
    deltas = sorted(zip(deltarow, range(cols)), reverse=True)

    to_reduce = sum(maxrow) + (cols - 1) * len(column_separator) - desired_width
    to_stretch = int(-to_reduce * stretch_screen) if to_reduce < 0 and stretch_screen else 0

    new_maxrow = maxrow[:]
    for delta, i in deltas:
        if to_reduce < 0:
            break
        if delta >= to_reduce:
            new_maxrow[i] -= to_reduce
            to_reduce = 0
            break
        new_maxrow[i] -= delta
        to_reduce -= delta

    if to_reduce > 0:
        new_maxrow = maxrow

    for row in table:
        for col in range(cols):
            cell_content = row[col][1]
            if isinstance(cell_content, Bar):
                cell_content.width += to_stretch
                formatted = cell_content.format(format_spec[col][2])
            else:
                formatted = manglestring(cell_content, new_maxrow[col], format_spec[col][2])
            row[col][1] = formatted


def display_table(table: list[list[tuple[str, Any]]], term_width: int) -> None:
    squeeze_table(table, term_width - 1)
    colsepcol = makecolour(column_separator_colour)
    for row in table:
        firstcol = True
        for colourcode, text in row:
            if firstcol:
                firstcol = False
            else:
                out(colsepcol)
                out(column_separator)
            out(makecolour(colourcode))
            out(text)
        out(row_separator)
        out(makecolour(normal_colour))
        out("\n")


def display_termux_list(table: list[list[tuple[str, Any]]]) -> None:
    data_rows = table[1:]
    sys.stdout.write("Filesystems (Termux Environment):\n")
    for row in data_rows:
        fs_name = row[0][1]
        size = row[1][1]
        used = row[2][1]
        avail = row[3][1]
        perc = row[4][1]
        mount = row[6][1]
        sys.stdout.write(f"  - {mount}\n")
        sys.stdout.write(f"    Device: {fs_name} | Size: {size} | Used: {used} ({perc}%) | Avail: {avail}\n")


normal_colour = "default"
header_colour = "yellow"
local_fs_colour = "default"
remote_fs_colour = "green"
special_fs_colour = "blue"
readonly_fs_colour = "cyan"
filled_fs_colour = "red"
full_fs_colour = "on_red"

sizeformat = "-h"
column_separator = " "
column_separator_colour = "none"
row_separator = ""
hidebinds = True
stretch_screen = 0.3
FILL_THRESH = 95.0
FULL_THRESH = 99.0

format_spec = [
    ("fs", 10, "l"),
    ("size", 5, "r"),
    ("used", 5, "r"),
    ("avail", 5, "r"),
    ("perc", 4, "r"),
    ("bar", 0.1, "l"),
    ("on", 11, "l"),
]

barchar = "#"
bar_fillchar = "."
mountfile: str | list[str] = ["/etc/mtab", "/etc/mnttab", "/proc/mounts"]

header = {
    "fs": "Filesystem",
    "size": "Size",
    "used": "Used",
    "avail": "Avail",
    "on": "Mounted on",
    "fstype": "Type",
    "perc": "Use%",
    "bar": Bar(header=True),
}

blocksize = 0
allfss = False
localonly = False
inodes = False
terminal_width = 80
mountpoints: dict[str, tuple[str, str, list[str]]] = {}

for conffile in ["/etc/pydfrc", str(Path("~/.pydfrc").expanduser())]:
    if Path(conffile).is_file():
        with open(conffile, "rb") as cf:
            exec(compile(cf.read(), conffile, "exec"))


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    args: Optional[list[str]] = typer.Argument(None, help="Files or mountpoints"),
    show_all: bool = typer.Option(False, "-a", "--all", help="include filesystems having 0 blocks"),
    human_readable: bool = typer.Option(False, "-h", "--human-readable", help="print sizes in human readable format"),
    si: bool = typer.Option(False, "-H", "--si", help="use powers of 1000 not 1024"),
    block_size: int = typer.Option(0, "-b", "--block-size", help="use BLOCKSIZE-byte blocks"),
    local: bool = typer.Option(False, "-l", "--local", help="limit listing to local filesystems"),
    kilobytes: bool = typer.Option(False, "-k", "--kilobytes", help="like --block-size=1024"),
    megabytes: bool = typer.Option(False, "-m", "--megabytes", help="like --block-size=1048576"),
    gigabytes: bool = typer.Option(False, "-g", "--gigabytes", help="like --block-size=1073741824"),
    blocks: bool = typer.Option(False, "--blocks", help="use filesystem native block size"),
    bw: bool = typer.Option(False, "--bw", help="do not use colours"),
    mounts_file: Optional[str] = typer.Option(None, "--mounts", help="File to get mount information from"),
    show_binds: bool = typer.Option(True, "-B", "--show-binds", help="show 'mount --bind' mounts"),
    inodes_opt: bool = typer.Option(False, "-i", "--inodes", help="show inode instead of block usage"),
):
    "Run the command-line interface."
    global \
        blocksize, \
        allfss, \
        localonly, \
        hidebinds, \
        inodes, \
        sizeformat, \
        normal_colour, \
        header_colour, \
        local_fs_colour, \
        remote_fs_colour, \
        special_fs_colour, \
        filled_fs_colour, \
        full_fs_colour, \
        mountfile, \
        terminal_width, \
        mountpoints

    blocksize = block_size
    allfss = show_all
    localonly = local
    hidebinds = not show_binds
    inodes = inodes_opt

    if inodes:
        header["size"] = "Nodes"

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

    if bw:
        normal_colour = header_colour = local_fs_colour = remote_fs_colour = special_fs_colour = filled_fs_colour = (
            full_fs_colour
        ) = "none"

    if mounts_file:
        mountfile = mounts_file

    terminal_width = get_terminal_width()
    mountpoints = get_all_mountpoints()

    if args:
        mp_to_display = [find_mountpoint(str(Path(x).resolve())) for x in args]
        mp_to_display = [x for x in mp_to_display if x is not None]
    else:
        mp_to_display = list(mountpoints.keys())
        if localonly:
            mp_to_display = [x for x in mp_to_display if not is_remote_fs(mountpoints[x][1])]
        mp_to_display.sort()

    table = get_table(mp_to_display)

    is_termux = "TERMUX_VERSION" in os.environ or "com.termux" in os.environ.get("PREFIX", "")
    if is_termux:
        display_termux_list(table)
    else:
        display_table(table, terminal_width)


if __name__ == "__main__":
    app()
