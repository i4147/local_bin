#!/data/data/com.termux/files/usr/bin/env python
import re
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

from dh import fsz, gsz, runcmd
from rich.console import Console

SO_RE = re.compile(r"\.so(\.\d+)*$", re.IGNORECASE)
WHL_SUFFIX = ".whl"
console = Console()


def process_file(path: Path) -> int:
    before = path.stat().st_size
    runcmd(["strip", str(path)], show_output=True)
    return before - path.stat().st_size


def process_whl(whl_path: Path) -> int:
    before = whl_path.stat().st_size
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        with ZipFile(whl_path, "r") as zf:
            zf.extractall(tmpdir_path)
        so_files = [p for p in tmpdir_path.rglob("*") if SO_RE.search(p.name) and p.is_file()]
        for so_file in so_files:
            process_file(so_file)
        with ZipFile(whl_path, "w") as zf:
            for path in tmpdir_path.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(tmpdir_path))
    return before - whl_path.stat().st_size


def collect_files(cwd: Path, args: list[str]) -> list[Path]:
    if args:
        return [Path(p) for p in args]
    so_files = [p for p in cwd.rglob("*") if SO_RE.search(p.name) and p.is_file()]
    whl_files = [p for p in cwd.rglob("*") if p.is_file() and p.suffix == WHL_SUFFIX]
    return so_files + whl_files


def has_so(path: str | Path) -> bool:
    path = Path(path)
    with ZipFile(path, "r") as zf:
        return any(SO_RE.search(name) for name in zf.namelist())


def print_row(idx: int, total: int, name: str, saved: int) -> None:
    idx_str = f"[{idx}/{total}]"
    saved_str = str(saved)
    name_width = max(console.width - len(idx_str) - len(saved_str) - 4, 10)
    if len(name) > name_width:
        name = name[: name_width - 1] + "…"
    style = "green" if saved > 0 else "dim"
    console.print(f"{idx_str}  {name:<{name_width}}  [{style}]{saved_str}[/]")


if __name__ == "__main__":
    cwd = Path.cwd()
    args = sys.argv[1:]
    files = collect_files(cwd, args)
    so_files = [f for f in files if SO_RE.search(f.name) and f.is_file() and f.suffix != WHL_SUFFIX]
    whl_files = [f for f in files if f.is_file() and f.suffix == WHL_SUFFIX and has_so(f)]
    files_to_process = so_files + whl_files
    total = len(files_to_process)
    before = gsz(files_to_process)
    saved_total = 0
    idx = 0
    for so_file in so_files:
        idx += 1
        saved = process_file(so_file)
        saved_total += saved
        print_row(idx, total, so_file.name, saved)
    for whl_file in whl_files:
        idx += 1
        saved = process_whl(whl_file)
        saved_total += saved
        print_row(idx, total, whl_file.name, saved)
    after = gsz(files_to_process)
    console.print(f"[bold cyan]Num:[/] [bold yellow]{total}[/]")
    console.print(f"[bold cyan]Size:[/] [bold yellow]{fsz(before - after)}[/]")
