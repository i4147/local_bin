#!/data/data/com.termux/files/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any


def split_unquoted(value: str, separator: str) -> list[str]:
    parts: list[str] = []
    start = 0
    quoted = False
    escaped = False

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
    replacements = {
        "n": "\n",
        "N": "\n",
        ",": ",",
        ";": ";",
        ":": ":",
        "\\": "\\",
    }
    return re.sub(r"\\([nN,;:\\])", lambda match: replacements[match.group(1)], value)


def parse_parameters(tokens: list[str]) -> dict[str, list[str]]:
    parameters: dict[str, list[str]] = {}

    for token in tokens:
        if "=" not in token:
            continue

        name, raw_value = token.split("=", 1)
        values = split_unquoted(raw_value, ",")
        parameters[name.strip().upper()] = [decode_text(value.strip().strip('"')) for value in values]

    return parameters


def parse_property(line: str, line_number: int) -> tuple[str, dict[str, list[str]], str]:
    if ":" not in line:
        raise ValueError(f"Malformed property on line {line_number}: missing ':'")

    header, value = line.split(":", 1)
    tokens = split_unquoted(header, ";")
    property_name = tokens[0].strip().upper()

    if not property_name:
        raise ValueError(f"Malformed property on line {line_number}: empty name")

    return property_name, parse_parameters(tokens[1:]), decode_text(value)


def parse_vcards(text: str) -> list[dict[str, list[dict[str, Any]]]]:
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
                raise ValueError(f"Nested BEGIN:VCARD on line {line_number}")
            current_card = {}
            continue

        if marker == "END:VCARD":
            if current_card is None:
                raise ValueError(f"END:VCARD without BEGIN:VCARD on line {line_number}")
            cards.append(current_card)
            current_card = None
            continue

        if current_card is None or not line:
            continue

        property_name, parameters, value = parse_property(line, line_number)
        current_card.setdefault(property_name, []).append({"value": value, "parameters": parameters})

    if current_card is not None:
        raise ValueError("Input ended before END:VCARD")

    if not cards:
        raise ValueError("No vCard records found")

    return cards


def write_json(cards: list[dict[str, list[dict[str, Any]]]], output_path: Path, compact: bool) -> None:
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
    parser = argparse.ArgumentParser(description="Convert a vCard (.vcf) file to JSON or CSV.")
    parser.add_argument("input", type=Path, help="Path to the input .vcf file")

    formats = parser.add_mutually_exclusive_group()
    formats.add_argument(
        "--json",
        dest="format",
        action="store_const",
        const="json",
        help="Write JSON output (the default)",
    )
    formats.add_argument(
        "--csv",
        dest="format",
        action="store_const",
        const="csv",
        help="Write CSV output",
    )

    parser.set_defaults(format="json")
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


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    input_path: Path = args.input

    if not input_path.is_file():
        parser.error(f"input file does not exist: {input_path}")

    extension = ".json" if args.format == "json" else ".csv"
    output_path: Path = args.output or input_path.with_suffix(extension)

    if input_path.resolve() == output_path.resolve():
        parser.error("output path must not be the same as the input path")

    try:
        text = input_path.read_text(encoding=args.encoding)
        cards = parse_vcards(text)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if args.format == "json":
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
