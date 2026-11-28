#!/data/data/com.termux/files/usr/bin/env python
"""
Recursively strip all shared libraries (.so, .so.N, .so.N.N.N, ...) in the
current directory, in parallel, using `rich` for nicely aligned output.

Requires:
    - binutils `strip` on PATH
    - rich   (pip install rich)
"""

from __future__ import annotations

from collections.abc import Iterator
import multiprocessing as mp
from pathlib import Path
import re
import subprocess

from rich.console import Console
from rich.text import Text
import os
import sys
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


SO_PATTERN = re.compile(r"\.so(\.\d+)*$")
NUM_WORKERS: int = 4


def walk_so_files(root: Path) -> Iterator[Path]:
    """Generator: yield every .so* regular file, skipping symlinks."""
    for p in root.rglob("*"):
        if p.is_symlink():
            continue
        if p.is_file() and SO_PATTERN.search(p.name):
            yield p


def strip_one(path_str: str):
    """Strip a single file.  Returns (path, orig, new, ok, err)."""
    p = Path(path_str)
    try:
        orig = p.stat().st_size
        proc = subprocess.run(
            ["strip", "--strip-unneeded", str(p)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip() or "strip failed"
            return (path_str, orig, orig, False, msg)
        new = p.stat().st_size
        return (path_str, orig, new, True, None)
    except FileNotFoundError:
        return (path_str, 0, 0, False, "'strip' not found in PATH")
    except Exception as e:
        return (path_str, 0, 0, False, f"{type(e).__name__}: {e}")


def fmt_bytes(n: int) -> str:
    if n == 0:
        return "0 B"
    sign = "-" if n < 0 else ""
    size = abs(n)
    if size < 1024:
        return f"{sign}{size} B"
    value = float(size)
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        value /= 1024
        if value < 1024:
            return f"{sign}{value:.2f} {unit}"
    return f"{sign}{value:.2f} EiB"


def main() -> None:
    """Run the command-line interface."""
    console = Console()
    term_width = console.size.width
    root = Path()

    files = list(walk_so_files(root))
    total = len(files)
    if total == 0:
        console.print("[yellow]No .so files found.[/yellow]")
        return

    # Bound the 3rd column width by the largest original size.
    try:
        max_size = max(p.stat().st_size for p in files)
    except OSError:
        max_size = 1 << 40  # fallback: 1 TiB
    bytes_w = max(len(str(max_size)), 4)

    ndigits = len(str(total))
    counter_w = 2 * ndigits + 3  # "[NN/NN]"
    sep = 2  # spaces between columns
    fixed = counter_w + 2 * sep + bytes_w
    fname_w = max(term_width - fixed, 10)

    max_fname = max(len(p.name) for p in files)
    pad_fname = min(fname_w, max_fname)  # truncate only if necessary

    total_saved = 0
    with mp.Pool(NUM_WORKERS) as pool:
        iterator = pool.imap_unordered(strip_one, (str(p) for p in files))
        for i, (path_str, orig, new, ok, err) in enumerate(iterator, 1):
            name = Path(path_str).name
            if len(name) > pad_fname:
                name_disp = name[: max(pad_fname - 1, 1)] + "…"
            else:
                name_disp = name.ljust(pad_fname)

            saved = (orig - new) if ok else 0
            counter = f"[{i:>{ndigits}}/{total}]"

            line = Text()
            line.append(counter, style="bold")
            line.append(" " * sep)
            line.append(name_disp)
            line.append(" " * sep)

            if not ok:
                line.append(str(saved).rjust(bytes_w), style="red")
                line.append(f"  ({err})", style="red dim")
            elif saved > 0:
                line.append(str(saved).rjust(bytes_w), style="green")
                total_saved += saved
            elif saved == 0:
                line.append(str(saved).rjust(bytes_w), style="dim")
            else:  # grew (very unusual)
                line.append(str(saved).rjust(bytes_w), style="yellow")

            console.print(line, soft_wrap=True)

    console.print()
    console.print(
        f"[bold]Total space freed:[/bold] "
        f"[green]{total_saved}[/green] B "
        f"([bold green]{fmt_bytes(total_saved)}[/bold green])"
    )


if __name__ == "__main__":
    main()
