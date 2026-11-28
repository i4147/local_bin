#!/data/data/com.termux/files/usr/bin/env python
"""Convert a vCard (.vcf) file to JSON or CSV.

CLI entry point; unescapes vCard property values and splits unquoted separators
before writing the chosen format."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import sys
from typing import Any


def split_unquoted(value: str, separator: str) -> list[str]:
    """Split unquoted."""
    parts: list[str] = []
    start = 0
    quoted = False
    escaped = False
    # (d) vCard quoting: split only outside double quotes; a backslash escapes the next char.
    for index, character in enumerate(value):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == separator and not quoted:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return parts


def decode_text(value: str) -> str:
    """Decode text."""
    replacements = {
        "n": "\n",
        "N": "\n",
        ",": ",",
        ";": ";",
        ":": ":",
        "\\": "\\",
    }
    # (d) Unescape vCard sequences: \n/\N newline, comma, semicolon, colon, backslash.
    return re.sub(r"\\([nN,;:\\])", lambda match: replacements[match.group(1)], value)


def parse_parameters(tokens: list[str]) -> dict[str, list[str]]:
    """Parse parameters."""
    parameters: dict[str, list[str]] = {}
    for token in tokens:
        if "=" not in token:
            continue
        name, raw_value = token.split("=", 1)
        values = split_unquoted(raw_value, ",")
        parameters[name.strip().upper()] = [decode_text(value.strip().strip('"')) for value in values]
    return parameters


def parse_property(line: str, line_number: int) -> tuple[str, dict[str, list[str]], str]:
    """Parse property."""
    if ":" not in line:
        msg = f"Malformed property on line {line_number}: missing ':'"
        raise ValueError(msg)
    header, value = line.split(":", 1)
    tokens = split_unquoted(header, ";")
    property_name = tokens[0].strip().upper()
    if not property_name:
        msg = f"Malformed property on line {line_number}: empty name"
        raise ValueError(msg)
    return property_name, parse_parameters(tokens[1:]), decode_text(value)


def parse_vcards(text: str) -> list[dict[str, list[dict[str, Any]]]]:
    """Parse vcards."""
    cards: list[dict[str, list[dict[str, Any]]]] = []
    current_card: dict[str, list[dict[str, Any]]] | None = None
    unfolded_lines: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.startswith((" ", "\t")) and unfolded_lines:
            previous_number, previous_line = unfolded_lines[-1]
            unfolded_lines[-1] = (previous_number, previous_line + line[1:])
        else:
            unfolded_lines.append((line_number, line))
    for line_number, line in unfolded_lines:
        marker = line.strip().upper()
        if marker == "BEGIN:VCARD":
            if current_card is not None:
                msg = f"Nested BEGIN:VCARD on line {line_number}"
                raise ValueError(msg)
            current_card = {}
            continue
        if marker == "END:VCARD":
            if current_card is None:
                msg = f"END:VCARD without BEGIN:VCARD on line {line_number}"
                raise ValueError(msg)
            cards.append(current_card)
            current_card = None
            continue
        if current_card is None or not line:
            continue
        property_name, parameters, value = parse_property(line, line_number)
        current_card.setdefault(property_name, []).append({"value": value, "parameters": parameters})
    if current_card is not None:
        msg = "Input ended before END:VCARD"
        raise ValueError(msg)
    if not cards:
        msg = "No vCard records found"
        raise ValueError(msg)
    return cards


def write_json(cards: list[dict[str, list[dict[str, Any]]]], output_path: Path, compact: bool) -> None:
    """Write json."""
    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        json.dump(
            cards,
            output_file,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
        )
        output_file.write("\n")


def write_csv(cards: list[dict[str, list[dict[str, Any]]]], output_path: Path) -> None:
    """Write csv."""
    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["card", "property", "parameters", "value"])
        for card_number, card in enumerate(cards, start=1):
            for property_name, entries in card.items():
                for entry in entries:
                    writer.writerow([
                        card_number,
                        property_name,
                        json.dumps(entry["parameters"], ensure_ascii=False),
                        entry["value"],
                    ])


def build_parser() -> argparse.ArgumentParser:
    """Build parser."""
    parser = argparse.ArgumentParser(
        description="Convert a vCard (.vcf) file to JSON or CSV.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to the input .vcf file",
    )
    format_group = parser.add_mutually_exclusive_group()
    format_group.add_argument(
        "--json",
        dest="format",
        action="store_const",
        const="json",
        default="json",
        help="Write JSON output (the default)",
    )
    format_group.add_argument(
        "--csv",
        dest="format",
        action="store_const",
        const="csv",
        help="Write CSV output",
    )
    parser.add_argument(
        "-r",
        "--remove",
        action="store_true",
        help="Remove the input file after a successful conversion",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path (default: beside the input file with the selected extension)",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="Input file encoding (default: utf-8-sig)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Write compact JSON instead of indented JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path: Path = args.input
    if not input_path.is_file():
        parser.error(f"input file does not exist: {input_path}")

    output_format: str = args.format
    extension = ".json" if output_format == "json" else ".csv"
    output_path: Path = args.output if args.output is not None else input_path.with_suffix(extension)

    if input_path.resolve() == output_path.resolve():
        parser.error("output path must not be the same as the input path")

    try:
        text = input_path.read_text(encoding=args.encoding)
        cards = parse_vcards(text)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_format == "json":
            write_json(cards, output_path, args.compact)
        else:
            write_csv(cards, output_path)
        if args.remove:
            input_path.unlink()
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Converted {len(cards)} vCard(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
