#!/data/data/com.termux/files/usr/bin/env python
"""Convert JSON from a file or stdin into a Markdown table or list."""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from itertools import product
from pathlib import Path
from typing import Any, Sequence, TextIO


def parse_header(value: str) -> tuple[str, str]:
    """Parse a --header argument in PATH=LABEL form."""
    path, separator, label = value.partition("=")
    if not separator or not path or not label:
        raise argparse.ArgumentTypeError("headers must use PATH=LABEL, for example user.name=User")
    return path, label


def read_json(source: str) -> Any:
    """Read and decode JSON from a file or standard input."""
    if source == "-":
        return json.load(sys.stdin)
    with Path(source).open("r", encoding="utf-8") as file:
        return json.load(file)


def expand_arrays(value: Any, array_mode: str) -> list[Any]:
    """Expand arrays into multiple values, including arrays nested in objects."""
    if isinstance(value, list):
        if array_mode != "expand":
            return [value]
        if not value:
            return [[]]
        return [expanded for item in value for expanded in expand_arrays(item, array_mode)]

    if isinstance(value, dict):
        keys = list(value)
        expanded_values = [expand_arrays(value[key], array_mode) for key in keys]
        combinations: list[Any] = []
        for combination in product(*expanded_values):
            combinations.append(dict(zip(keys, combination, strict=True)))
        return combinations

    return [value]


def flatten_object(
    value: Any,
    *,
    separator: str,
    array_mode: str,
    prefix: str = "",
) -> dict[str, Any]:
    """Flatten nested objects into path/value pairs."""
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}{separator}{key}" if prefix else str(key)
            flattened.update(
                flatten_object(
                    child,
                    separator=separator,
                    array_mode=array_mode,
                    prefix=path,
                )
            )
        if not value and prefix:
            flattened[prefix] = value
        return flattened

    if prefix:
        return {prefix: value}

    return {"value": value}


def make_rows(
    data: Any,
    *,
    separator: str,
    array_mode: str,
) -> list[dict[str, Any]]:
    """Turn a JSON value into rows, flattening objects and optionally expanding arrays."""
    source_items = data if isinstance(data, list) else [data]
    rows: list[dict[str, Any]] = []

    for item in source_items:
        for expanded in expand_arrays(item, array_mode):
            rows.append(
                flatten_object(
                    expanded,
                    separator=separator,
                    array_mode=array_mode,
                )
            )

    return rows


def stringify(value: Any, *, array_mode: str, null_text: str) -> str:
    """Convert a JSON value into display text."""
    if value is None:
        return null_text
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list) and array_mode == "join":
        return ", ".join(stringify(item, array_mode="json", null_text=null_text) for item in value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def escape_markdown_cell(value: str) -> str:
    """Escape characters that would break a Markdown table cell."""
    return (
        value
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
        .replace("\r", "<br>")
    )


def get_columns(
    rows: Sequence[dict[str, Any]],
    *,
    headers: dict[str, str],
    excludes: Sequence[str],
    sort_columns: bool,
) -> list[str]:
    """Collect columns in first-seen order, optionally sorting and excluding them."""
    columns: list[str] = []
    seen: set[str] = set()

    for row in rows:
        for column in row:
            if column not in seen and not any(fnmatch.fnmatchcase(column, pattern) for pattern in excludes):
                columns.append(column)
                seen.add(column)

    if sort_columns:
        columns.sort(key=lambda column: headers.get(column, column).casefold())
    return columns


def render_table(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[str],
    *,
    headers: dict[str, str],
    array_mode: str,
    null_text: str,
    no_header: bool,
) -> str:
    """Render rows as a GitHub-flavored Markdown table."""
    if not columns:
        return ""

    def cell(row: dict[str, Any], column: str) -> str:
        value = stringify(row.get(column), array_mode=array_mode, null_text=null_text)
        return escape_markdown_cell(value)

    output: list[str] = []
    if not no_header:
        labels = [escape_markdown_cell(headers.get(column, column)) for column in columns]
        output.append("| " + " | ".join(labels) + " |")
        output.append("| " + " | ".join("---" for _ in columns) + " |")

    for row in rows:
        output.append("| " + " | ".join(cell(row, column) for column in columns) + " |")

    return "\n".join(output)


def render_list(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[str],
    *,
    headers: dict[str, str],
    array_mode: str,
    null_text: str,
) -> str:
    """Render each row as a Markdown bullet list."""
    output: list[str] = []

    for index, row in enumerate(rows, start=1):
        output.append(f"- **Item {index}**")
        for column in columns:
            label = escape_markdown_cell(headers.get(column, column))
            value = escape_markdown_cell(stringify(row.get(column), array_mode=array_mode, null_text=null_text))
            output.append(f"  - **{label}:** {value}")

    return "\n".join(output)


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="json-to-markdown",
        description="Convert JSON from a file or stdin into a Markdown table or list.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        help="JSON input file; defaults to stdin",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="write Markdown to this file instead of stdout",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=("table", "list"),
        default="table",
        help="output format (default: table)",
    )
    parser.add_argument(
        "--array-mode",
        choices=("json", "join", "expand"),
        default="json",
        help=(
            "how to display or handle arrays: compact JSON, join scalar values, "
            "or expand array items into rows (default: json)"
        ),
    )
    parser.add_argument(
        "--separator",
        default=".",
        help="separator used in flattened column paths (default: .)",
    )
    parser.add_argument(
        "--header",
        action="append",
        type=parse_header,
        default=[],
        metavar="PATH=LABEL",
        help="rename a flattened column; may be supplied more than once",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="exclude columns matching a glob pattern; may be supplied more than once",
    )
    parser.add_argument(
        "--sort-columns",
        action="store_true",
        help="sort columns alphabetically by their displayed header",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="omit the header and separator rows (table format only)",
    )
    parser.add_argument(
        "--null-text",
        default="—",
        help="text to use for JSON null values (default: —)",
    )
    return parser


def run(args: argparse.Namespace, *, stdout: TextIO) -> None:
    """Convert the input and write the resulting Markdown."""
    data = read_json(args.input)
    headers = dict(args.header)
    rows = make_rows(
        data,
        separator=args.separator,
        array_mode=args.array_mode,
    )
    columns = get_columns(
        rows,
        headers=headers,
        excludes=args.exclude,
        sort_columns=args.sort_columns,
    )

    if args.format == "table":
        markdown = render_table(
            rows,
            columns,
            headers=headers,
            array_mode=args.array_mode,
            null_text=args.null_text,
            no_header=args.no_header,
        )
    else:
        markdown = render_list(
            rows,
            columns,
            headers=headers,
            array_mode=args.array_mode,
            null_text=args.null_text,
        )

    if args.output:
        Path(args.output).write_text(markdown + "\n", encoding="utf-8")
    else:
        stdout.write(markdown + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return an exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.separator:
        parser.error("--separator cannot be empty")
    if args.no_header and args.format != "table":
        parser.error("--no-header is only valid with --format table")

    try:
        run(args, stdout=sys.stdout)
    except (OSError, json.JSONDecodeError) as error:
        print(f"json-to-markdown: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
