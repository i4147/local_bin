#!/data/data/com.termux/files/usr/bin/env python
"Utilities for tcleaner."

from __future__ import annotations

from collections.abc import Iterator
import glob
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table


console = Console()

# --- CONFIGURATION: Directories to delete entirely ---
DIRS_TO_DELETE: list[str] = [
    "$PREFIX/lib/python3.*/site-packages/**/test",
    "$PREFIX/lib/python3.*/site-packages/**/tests",
    "$PREFIX/lib/python3.*/site-packages/**/__pycache__",
    "$PREFIX/share/man",
    "$PREFIX/share/doc",
    "$PREFIX/share/info",
    "$HOME/.cache/*",
]

# --- CONFIGURATION: Strict exemptions to NEVER delete ---
KEEP_EXEMPTIONS: list[Any] = [
    os.path.expandvars("$HOME/.cache/pip/wheels"),
    os.path.expandvars("$HOME/.cache/pip"),
]

# --- CONFIGURATION: File Extensions to hunt down and delete ---
FILE_EXTENSIONS: list[str] = [
    ".tmp",
    ".bak",
    ".save",
    ".log",
    ".cache",
]

# --- CONFIGURATION: Specific scan zones for the file extensions ---
SCAN_ZONES: list[str] = [
    "$PREFIX/lib/python3.*/site-packages",
    "$HOME",
]


def expand_and_resolve(pattern: str) -> Iterator[Path]:
    """Expand and resolve."""
    return glob.glob(os.path.expandvars(pattern), recursive=True)


def is_exempt(path: Path) -> bool:
    "Return whether exempt."
    normalized_path = str(Path(path).resolve())
    for exemption in KEEP_EXEMPTIONS:
        norm_exemption = str(Path(exemption).resolve())
        if normalized_path == norm_exemption or normalized_path.startswith(norm_exemption + os.sep):
            return True
        if norm_exemption.startswith(normalized_path + os.sep):
            return True
    return False


def get_size(path: Path):
    "Return size."
    try:
        p = Path(path)
        if p.is_symlink():
            return 0
        if p.is_file():
            return p.stat().st_size

        total_size = 0
        for item in p.rglob("*"):
            if not item.is_symlink() and item.is_file():
                total_size += item.stat().st_size
        return total_size
    except Exception:
        return 0


def purge_system() -> None:
    console.print(Panel("[bold cyan]🧹 Termux Storage Cleanup System[/bold cyan]", expand=False))

    deleted_count = 0
    total_bytes_freed = 0

    # Custom track bar progress
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        # Task 1: Clean targeted dirs
        dir_task = progress.add_task("[yellow]Purging targeted directories...", total=None)
        resolved_dirs = []
        for pattern in DIRS_TO_DELETE:
            resolved_dirs.extend(expand_and_resolve(pattern))

        progress.update(dir_task, total=len(resolved_dirs) if resolved_dirs else 1)

        for path in resolved_dirs:
            p = Path(path)
            if p.exists() and not is_exempt(path) and p.is_dir():
                try:
                    dir_size = get_size(path)
                    shutil.rmtree(path)
                    total_bytes_freed += dir_size
                    deleted_count += 1
                except Exception:
                    pass
            progress.advance(dir_task)
        progress.update(
            dir_task,
            completed=len(resolved_dirs) if resolved_dirs else 1,
            description="[green]📁 Targeted directories cleaned!",
        )

        # Task 2: Match extensions
        file_task = progress.add_task("[yellow]Scanning for extension junk...", total=None)
        match_files = []
        for zone in SCAN_ZONES:
            expanded_zone = os.path.expandvars(zone)
            for ext in FILE_EXTENSIONS:
                match_files.extend(str(p) for p in Path(expanded_zone).rglob(f"*{ext}"))

        progress.update(file_task, total=len(match_files) if match_files else 1)

        for file_path in match_files:
            if Path(file_path).is_file() and not is_exempt(file_path):
                try:
                    file_size = get_size(file_path)
                    os.remove(file_path)
                    total_bytes_freed += file_size
                    deleted_count += 1
                except Exception:
                    pass
            progress.advance(file_task)
        progress.update(
            file_task,
            completed=len(match_files) if match_files else 1,
            description="[green]📄 Junk extension files purged!",
        )

        # Task 3: Package updates
        pkg_task = progress.add_task("[yellow]Clearing package manager caches...", total=1)
        subprocess.run(["pkg", "clean"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        progress.update(pkg_task, completed=1, description="[green]📦 System pkg cache wiped!")

    # Final visual metrics display
    space_freed_mb = total_bytes_freed / (1024 * 1024)

    summary_table = Table(title="Cleanup Summary Metrics", show_header=False, box=None)
    summary_table.add_row(
        "[bold white]Total Items Removed:[/bold white]",
        f"[bold yellow]{deleted_count}[/bold yellow]",
    )
    summary_table.add_row(
        "[bold white]Total Storage Freed:[/bold white]",
        f"[bold green]{space_freed_mb:.2f} MB[/bold green]",
    )

    # Get current footprint size
    resolved_site_pkgs = glob.glob(os.path.expandvars("$PREFIX/lib/python3.*/site-packages"))
    if resolved_site_pkgs:
        size_check = subprocess.run(["du", "-sh", resolved_site_pkgs[0]], capture_output=True, text=True)
        current_size = size_check.stdout.split()[0] if size_check.returncode == 0 else "Unknown"
        summary_table.add_row(
            "[bold white]Current Site-Packages Size:[/bold white]",
            f"[cyan]{current_size}[/cyan]",
        )

    console.print("\n")
    console.print(
        Panel(
            summary_table,
            border_style="green",
            title="[bold green]✨ Run Successful[/bold green]",
            expand=False,
        )
    )


if __name__ == "__main__":
    purge_system()
