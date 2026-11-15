#!/data/data/com.termux/files/usr/bin/env python
import os
import shutil
from typing import List, Optional
import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

app = typer.Typer(add_help_option=False, help="Termux disk usage utility with pretty list view")
console = Console()


def hfnum(size: int) -> str:
    if size == 0:
        return "0B"
    units = ["B", "K", "M", "G", "T", "P", "E"]
    power = 0
    while size >= 1024 and power < len(units) - 1:
        size /= 1024.0  # type: ignore
        power += 1
    return f"{round(size, 1)}{units[power]}" if power > 0 else f"{int(size)}B"


@app.command()
def main(
    human_readable: bool = typer.Option(True, "--human-readable", "-h", help="Print sizes in human readable format"),
    paths: Optional[List[str]] = typer.Argument(None, help="Paths or mount points to check"),
) -> None:
    target_paths = (
        paths
        if paths
        else [
            os.path.expanduser("~"),
            "/storage/emulated/0",
            "/",
        ]
    )

    console.print("[bold magenta]📱 Termux Storage Overview[/bold magenta]\n")

    for path in target_paths:
        expanded_path = os.path.expanduser(path)
        if not os.path.exists(expanded_path):
            continue
        try:
            usage = shutil.disk_usage(expanded_path)
            total = usage.total
            used = usage.used
            free = usage.free

            percent = (used / total) * 100 if total > 0 else 0.0

            if human_readable:
                size_str = hfnum(total)
                used_str = hfnum(used)
                avail_str = hfnum(free)
            else:
                size_str = str(total)
                used_str = str(used)
                avail_str = str(free)

            # Color code usage percentage
            pct_color = "green" if percent < 75 else ("yellow" if percent < 90 else "red")

            content = Text()
            content.append(f"• Total Size: ", style="dim")
            content.append(f"{size_str}\n", style="bold green")
            content.append(f"• Used Space: ", style="dim")
            content.append(f"{used_str} ", style="bold yellow")
            content.append(f"({percent:.1f}%)\n", style=f"bold {pct_color}")
            content.append(f"• Available : ", style="dim")
            content.append(f"{avail_str}", style="bold blue")

            panel = Panel(
                content,
                title=f"[cyan]{expanded_path}[/cyan]",
                title_align="left",
                border_style="magenta",
                padding=(1, 2),
            )
            console.print(panel)
        except Exception as e:
            console.print(f"[bold red]Error accessing {expanded_path}: {e}[/bold red]")


if __name__ == "__main__":
    app()
