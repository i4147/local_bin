#!/data/data/com.termux/files/usr/bin/env python
"Minify HTML files in the current directory in place."

from __future__ import annotations
from concurrent.futures import ProcessPoolExecutor
import contextlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any, NamedTuple, NoReturn

import click
from dh import get_files, runcmd
import typer


if TYPE_CHECKING:
    import argparse


_STATE: dict[str, object] = {}


class _Args(SimpleNamespace):
    """argparse.Namespace look-alike: unknown attributes read as None."""

    def __getattr__(self, name: str) -> None:
        if name.startswith("__"):
            raise AttributeError(name)


class _ParserShim:
    """Stand-in for the removed argparse parser; hands back what Typer parsed."""

    def parse_args(self, args: list[Any] | None = None, namespace: Any = None) -> _Args:
        "Parse args."
        return _Args(**_STATE)

    def error(self, message: str) -> NoReturn:
        raise typer.BadParameter(message)

    def print_help(self, file: Path | None = None) -> None:
        "Print help."
        typer.echo(click.get_current_context().get_help(), file=file)

    def print_usage(self, file: Path | None = None) -> None:
        "Print usage."
        typer.echo(click.get_current_context().get_usage(), file=file)


class FileResult(NamedTuple):
    """Store path, before, after, returncode, error."""

    path: Path
    before: int | None
    after: int | None
    returncode: int
    error: str


def minify_file(job: tuple[str, str]) -> FileResult:
    """Minify file."""
    binary, filename = job
    path = Path(filename)

    try:
        before = path.stat().st_size
    except OSError as error:
        return FileResult(path, None, None, 1, str(error))

    try:
        returncode, txt, err = runcmd(
            [
                binary,
                "--allow-noncompliant-unquoted-attribute-values",
                "--allow-optimal-entities",
                "--allow-removing-spaces-between-attributes",
                "--minify-css",
                "--minify-js",
                "--minify-doctype",
                "--remove-bangs",
                "--remove-processing-instructions",
                "--preserve-brace-template-syntax",
                "--preserve-chevron-percent-template-syntax",
                str(path),
            ],
            show_output=False,
        )
    except OSError as error:
        return FileResult(path, before, None, 1, str(error))

    if returncode != 0:
        details = (err or "").strip() or f"{binary} exited with status {returncode}"
        return FileResult(path, before, None, returncode, details)

    # Write minified output to a temp file in the same directory, then atomically replace
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as tmp_file:
                tmp_file.write(txt)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except OSError as error:
        return FileResult(path, before, None, 1, str(error))

    try:
        after = path.stat().st_size
    except OSError as error:
        return FileResult(path, before, None, 1, str(error))

    return FileResult(path, before, after, 0, "")


def format_bytes(size: int) -> str:
    "Format bytes."
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)

    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value):,} {unit}"
            return f"{value:,.2f} {unit}"
        value /= 1024

    return f"{size:,} B"


def find_html_files(directory: Path) -> list[Path]:
    "Find html files."
    return get_files(directory, ext=[".html"])


def parse_args() -> argparse.Namespace:
    "Parse args."
    parser = _ParserShim()
    return parser.parse_args()


def main() -> int:
    "Run the command-line interface."
    args = parse_args()

    if args.workers < 1:
        print("Error: --workers must be at least 1.", file=sys.stderr)
        return 2

    files = find_html_files(Path.cwd())
    if not files:
        print("No .html or .htm files found in the current directory.")
        return 0

    jobs = [(args.binary, str(path)) for path in files]
    with ProcessPoolExecutor(max_workers=min(args.workers, len(jobs))) as pool:
        results = list(pool.map(minify_file, jobs))

    total_before = 0
    total_after = 0
    has_errors = False

    for result in results:
        if result.before is not None:
            total_before += result.before
        if result.after is not None:
            total_after += result.after

        if result.returncode != 0 or result.after is None:
            has_errors = True
            print(f"ERROR {result.path.name}: {result.error or 'minification failed'}")
            continue

        change = result.before - result.after
        if change >= 0:
            reduction = f"saved {format_bytes(change)}"
        else:
            reduction = f"grew by {format_bytes(-change)}"

        print(f"{result.path.name}: {format_bytes(result.before or 0)} → {format_bytes(result.after)} ({reduction})")

    net_saved = total_before - total_after
    label = "Total space freed" if net_saved >= 0 else "Total size increase"
    print(f"{label}: {format_bytes(abs(net_saved))}")

    return 1 if has_errors else 0


def _run() -> None:
    raise SystemExit(main())


app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
)


@app.command(help="Minify HTML files in the current directory in place.")
def cli(
    binary: Annotated[
        str,
        typer.Option("--binary", help="min-html executable name or path (default: min-html)"),
    ] = "min-html",
    workers: Annotated[
        int,
        typer.Option("-j", "--workers", help="number of files to process concurrently"),
    ] = max(1, os.cpu_count() or 1),
) -> None:
    """Run the command-line interface."""
    _STATE.update(binary=binary, workers=workers)
    _run()


if __name__ == "__main__":
    app()
