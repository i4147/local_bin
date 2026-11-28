#!/data/data/com.termux/files/usr/bin/env python
from __future__ import annotations
import argparse
from collections.abc import Callable
import dataclasses
from dataclasses import dataclass
import gzip
import multiprocessing as mp
import os
from pathlib import Path
import pydoc
import re
import shutil
import sys
import textwrap
import time
import tomllib
from typing import Any

import argcomplete
from loguru import logger
from pygments import highlight
from pygments.formatters import Terminal256Formatter
from pygments.lexer import Lexer
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.styles import get_all_styles
from pygments.util import ClassNotFound


__version__ = "2.1.0"

DEFAULT_CONFIG: Path = Path.home() / ".config" / "pager" / "config.toml"
STDIN: Path = Path("-")
ANSI_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
CONFIGURABLE: frozenset[str] = frozenset({
    "encoding",
    "strict",
    "number",
    "wrap",
    "tabs",
    "context",
    "workers",
    "pager",
    "color",
    "theme",
})
STYLES: list[str] = sorted(get_all_styles())

Entry = tuple[int, str]
Painter = Callable[[str], str]


# ---------------------------------------------------------------------------
# Options and argument validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Options:
    encoding: str
    strict: bool
    max_bytes: int | None
    lines: tuple[int | None, int | None] | None
    goto: int | None
    head: int | None
    tail: int | None
    grep: str | None
    ignore_case: bool
    context: int
    number: bool
    wrap: bool
    tabs: int
    raw: bool
    colour: bool
    theme: str
    pager: str | None
    no_pager: bool
    follow: bool
    workers: int

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Options:
        return cls(
            encoding=args.encoding,
            strict=args.strict,
            max_bytes=args.max_bytes,
            lines=args.lines,
            goto=args.goto,
            head=args.head,
            tail=args.tail,
            grep=args.grep,
            ignore_case=args.ignore_case,
            context=args.context,
            number=args.number,
            wrap=args.wrap,
            tabs=args.tabs,
            raw=args.raw,
            colour=resolve_colour(args.color),
            theme=args.theme,
            pager=args.pager,
            no_pager=args.no_pager,
            follow=args.follow,
            workers=args.workers,
        )


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return number


def non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value}")
    return number


def parse_range(spec: str) -> tuple[int | None, int | None]:
    start_text, sep, end_text = spec.partition(":")
    if not sep:
        raise argparse.ArgumentTypeError("expected START:END, e.g. 10:50 or 10: or :50")
    start = int(start_text) if start_text else None
    end = int(end_text) if end_text else None
    if start is not None and start < 1:
        raise argparse.ArgumentTypeError("line numbers start at 1")
    if start is not None and end is not None and end < start:
        raise argparse.ArgumentTypeError("END must be greater than or equal to START")
    return start, end


def resolve_colour(mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


# ---------------------------------------------------------------------------
# CLI construction and configuration
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Display text files through the system pager, with filtering, colour, and rendering options.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="FILE",
        help="Files to display; '-' reads standard input.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    reading = parser.add_argument_group("input")
    reading.add_argument("-e", "--encoding", default="utf-8", help="Text encoding (default: utf-8).")
    reading.add_argument(
        "--strict",
        action="store_true",
        help="Fail on decoding errors instead of replacing them.",
    )
    reading.add_argument(
        "--max-bytes",
        type=positive_int,
        default=None,
        help="Read at most N bytes per file.",
    )
    reading.add_argument(
        "--workers",
        type=positive_int,
        default=4,
        help="Worker processes for multiple files (default: 4).",
    )
    reading.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="TOML config file with default options.",
    )

    selection = parser.add_argument_group("selection")
    selection.add_argument(
        "--lines",
        type=parse_range,
        default=None,
        metavar="START:END",
        help="Show a line range (1-based, inclusive).",
    )
    selection.add_argument(
        "--goto",
        type=positive_int,
        default=None,
        metavar="LINE",
        help="Start display at LINE.",
    )
    selection.add_argument(
        "--head",
        type=positive_int,
        default=None,
        metavar="N",
        help="Show the first N selected lines.",
    )
    selection.add_argument(
        "--tail",
        type=positive_int,
        default=None,
        metavar="N",
        help="Show the last N selected lines.",
    )
    selection.add_argument(
        "-g",
        "--grep",
        default=None,
        metavar="PATTERN",
        help="Show only lines matching a regular expression.",
    )
    selection.add_argument("-i", "--ignore-case", action="store_true", help="Case-insensitive grep.")
    selection.add_argument(
        "-C",
        "--context",
        type=non_negative_int,
        default=0,
        metavar="N",
        help="Lines of context around grep matches.",
    )
    selection.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Keep watching a single file for appended content.",
    )

    display = parser.add_argument_group("display")
    display.add_argument(
        "-n",
        "--number",
        action="store_true",
        help="Prefix each line with its line number.",
    )
    display.add_argument(
        "--wrap",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Wrap long lines (default: on).",
    )
    display.add_argument(
        "--tabs",
        type=positive_int,
        default=4,
        metavar="N",
        help="Tab width (default: 4).",
    )
    display.add_argument("--raw", action="store_true", help="Keep ANSI escape sequences from the input.")
    display.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Syntax colouring (default: auto; honours NO_COLOR).",
    )
    display.add_argument(
        "--theme",
        choices=STYLES,
        default="monokai",
        help="Pygments colour style (default: monokai).",
    )
    display.add_argument("--pager", default=None, metavar="CMD", help="Pager command, e.g. 'less -R'.")
    display.add_argument("--no-pager", action="store_true", help="Write to stdout instead of paging.")

    operations = parser.add_argument_group("operations")
    operations.add_argument("--dry-run", action="store_true", help="List the resolved inputs and exit.")
    operations.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    operations.add_argument("--json-logs", action="store_true", help="Emit log records as JSON lines.")
    operations.add_argument(
        "--log-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Also write logs to a rotating file.",
    )
    return parser


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return {key: value for key, value in data.items() if key in CONFIGURABLE}


def configure_logging(verbose: bool, json_logs: bool, log_file: Path | None) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "WARNING"
    if json_logs:
        logger.add(sys.stderr, level=level, serialize=True)
    else:
        logger.add(sys.stderr, level=level, format="<level>{level: <8}</level> | {message}")
    if log_file is not None:
        logger.add(
            str(log_file),
            level="DEBUG",
            rotation="10 MB",
            retention="7 days",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_bytes(path: Path, limit: int) -> bytes:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as gz:
            return gz.read(limit)
    with path.open("rb") as fh:
        return fh.read(limit)


def load_text(source: Path, opts: Options) -> str:
    limit = -1 if opts.max_bytes is None else opts.max_bytes
    raw = sys.stdin.buffer.read(limit) if source == STDIN else read_bytes(source, limit)
    errors = "strict" if opts.strict else "replace"
    return raw.decode(opts.encoding, errors=errors)


@dataclass(frozen=True, slots=True)
class Result:
    index: int
    source: Path
    text: str | None
    error: str | None


def load_task(task: tuple[int, Path, Options]) -> Result:
    index, source, opts = task
    try:
        return Result(index, source, load_text(source, opts), None)
    except (OSError, LookupError, UnicodeDecodeError) as exc:
        return Result(index, source, None, str(exc))


def load_all(paths: list[Path], opts: Options) -> list[Result]:
    tasks = [(index, path, opts) for index, path in enumerate(paths)]
    if len(paths) == 1 or opts.workers == 1:
        return [load_task(task) for task in tasks]
    with mp.Pool(processes=opts.workers) as pool:
        results = list(pool.imap_unordered(load_task, tasks))
    return sorted(results, key=lambda result: result.index)


# ---------------------------------------------------------------------------
# Selection and rendering
# ---------------------------------------------------------------------------


def select_entries(text: str, opts: Options) -> list[Entry]:
    entries: list[Entry] = list(enumerate(text.splitlines(), start=1))

    if opts.lines is not None or opts.goto is not None:
        start, end = opts.lines if opts.lines is not None else (opts.goto, None)
        entries = [
            entry for entry in entries if (start is None or entry[0] >= start) and (end is None or entry[0] <= end)
        ]

    if opts.grep is not None:
        pattern = re.compile(opts.grep, re.IGNORECASE if opts.ignore_case else 0)
        keep: set[int] = set()
        for index, (_, line) in enumerate(entries):
            if pattern.search(line):
                keep.update(
                    range(
                        max(0, index - opts.context),
                        min(len(entries), index + opts.context + 1),
                    )
                )
        entries = [entries[index] for index in sorted(keep)]

    if opts.head is not None:
        entries = entries[: opts.head]
    if opts.tail is not None:
        entries = entries[-opts.tail :]
    return entries


def make_painter(source: Path, opts: Options) -> Painter:
    if not opts.colour:
        return _plain
    try:
        lexer: Lexer = TextLexer() if source == STDIN else get_lexer_for_filename(source.name)
    except ClassNotFound:
        lexer = TextLexer()
    formatter = Terminal256Formatter(style=opts.theme)

    def paint(text: str) -> str:
        return highlight(text, lexer, formatter).rstrip("\n")

    return paint


def _plain(text: str) -> str:
    return text


def render(entries: list[Entry], opts: Options, paint: Painter) -> str:
    if not entries:
        return ""
    width = len(str(entries[-1][0]))
    columns = shutil.get_terminal_size((80, 24)).columns
    output: list[str] = []
    previous: int | None = None

    for lineno, text in entries:
        if opts.grep is not None and previous is not None and lineno != previous + 1:
            output.append("--")
        previous = lineno

        if not opts.raw:
            text = ANSI_RE.sub("", text)
        text = text.expandtabs(opts.tabs)

        prefix = f"{lineno:>{width}} | " if opts.number else ""
        if opts.wrap:
            chunks = textwrap.wrap(
                text,
                width=max(1, columns - len(prefix)),
                break_long_words=True,
                replace_whitespace=False,
                drop_whitespace=False,
            ) or [""]
        else:
            chunks = [text]

        for position, chunk in enumerate(chunks):
            lead = prefix if position == 0 else " " * len(prefix)
            output.append(lead + paint(chunk))
    return "\n".join(output)


def emit(text: str, opts: Options) -> None:
    if opts.no_pager or not sys.stdout.isatty():
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        return
    if opts.pager:
        pydoc.pipepager(text, opts.pager)
    else:
        pydoc.pager(text)


def follow_file(path: Path, opts: Options) -> int:
    logger.info("Following {} (Ctrl+C to stop)", path)
    position = path.stat().st_size
    errors = "strict" if opts.strict else "replace"
    paint = make_painter(path, opts)
    try:
        while True:
            size = path.stat().st_size
            if size < position:
                logger.warning("File was truncated; reading from the start")
                position = 0
            if size > position:
                with path.open("rb") as fh:
                    fh.seek(position)
                    chunk = fh.read()
                position += len(chunk)
                text = chunk.decode(opts.encoding, errors=errors)
                for line in text.splitlines():
                    sys.stdout.write(paint(line) + "\n")
                sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    # Pre-parse --config so the file can supply defaults before full parsing.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    known, _ = pre.parse_known_args(argv)

    config_error: str | None = None
    try:
        config = load_config(known.config)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        config = {}
        config_error = f"Ignoring config {known.config}: {exc}"

    parser = build_parser()
    parser.set_defaults(**config)
    argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)
    configure_logging(args.verbose, args.json_logs, args.log_file)
    if config_error:
        logger.warning(config_error)

    paths: list[Path] = [Path(item) for item in args.paths] or [STDIN]
    if len(paths) > 1 and STDIN in paths:
        parser.error("standard input ('-') cannot be combined with other files")

    if args.dry_run:
        for path in paths:
            sys.stdout.write(f"{path}\n")
        return 0

    opts = Options.from_args(args)
    if opts.follow and (len(paths) != 1 or paths[0] == STDIN or paths[0].suffix == ".gz"):
        parser.error("--follow needs exactly one plain (non-gzip) file")

    results = load_all(paths, opts)
    failures = [result for result in results if result.error is not None]
    for failure in failures:
        logger.error("{}: {}", failure.source, failure.error)

    sections: list[str] = []
    for result in results:
        if result.text is None:
            continue
        paint = make_painter(result.source, opts)
        body = render(select_entries(result.text, opts), opts, paint)
        if len(paths) > 1:
            body = f"==> {result.source} <==\n{body}"
        sections.append(body)

    if sections:
        if opts.follow:
            emit("\n\n".join(sections), dataclasses.replace(opts, no_pager=True))
            return follow_file(paths[0], opts)
        emit("\n\n".join(sections), opts)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
