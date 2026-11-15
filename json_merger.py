#!/data/data/com.termux/files/usr/bin/env python
"Merge JSON files with matching schemas, including bilingual string dictionaries."

import argparse
from enum import StrEnum
import glob
import json
import mmap
import os
from pathlib import Path
import secrets
import string
import sys
from types import SimpleNamespace
from typing import Annotated, Any, NoReturn, Sequence

import click
import typer


class Conflict(StrEnum):
    "Enumerate the supported Conflict values."

    last = "last"
    first = "first"
    list = "list"
    error = "error"


_STATE: dict[str, object] = {}


class _Args(SimpleNamespace):
    """argparse.Namespace look-alike: unknown attributes read as None."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


class _ParserShim:
    """Stand-in for the removed argparse parser; hands back what Typer parsed."""

    def parse_args(self, args=None, namespace=None):
        "Parse args."
        return _Args(**_STATE)

    def error(self, message):
        raise typer.BadParameter(message)

    def print_help(self, file=None):
        "Print help."
        typer.echo(click.get_current_context().get_help(), file=file)

    def print_usage(self, file=None):
        "Print usage."
        typer.echo(click.get_current_context().get_usage(), file=file)


MMAP_THRESHOLD = 5 * 1024 * 1024


def schema_of(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean",)
    if isinstance(value, (int, float)):
        return ("number",)
    if isinstance(value, str):
        return ("string",)
    if isinstance(value, list):
        item_schemas = sorted(
            {schema_of(item) for item in value},
            key=repr,
        )
        return ("array", tuple(item_schemas))
    if isinstance(value, dict):
        if all(isinstance(item, str) for item in value.values()):
            return ("string_dictionary",)
        properties = tuple((key, schema_of(item)) for key, item in sorted(value.items()))
        return ("object", properties)
    msg = f"Unsupported JSON value type: {type(value).__name__}"
    raise TypeError(msg)


def reject_nonstandard_constant(value: str) -> NoReturn:
    msg = f"Non-standard JSON constant: {value}"
    raise ValueError(msg)


def read_json(path: Path) -> tuple[Any | None, str | None]:
    "Read json."
    try:
        with path.open("rb") as file:
            file_size = os.fstat(file.fileno()).st_size
            if file_size > MMAP_THRESHOLD:
                with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                    content = mapped[:].decode("utf-8-sig")
            else:
                content = file.read().decode("utf-8-sig")

        value = json.loads(
            content,
            parse_constant=reject_nonstandard_constant,
        )
        return value, None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        return None, str(error)


def merge_values(
    left: Any,
    right: Any,
    conflict_policy: str,
    conflicts: list[str],
    path: str = "",
) -> Any:
    if isinstance(left, dict) and isinstance(right, dict):
        merged: dict[str, Any] = {}
        for key in left.keys() | right.keys():
            child_path = f"{path}.{key}" if path else key
            if key not in left:
                merged[key] = right[key]
            elif key not in right:
                merged[key] = left[key]
            else:
                merged[key] = merge_values(
                    left[key],
                    right[key],
                    conflict_policy,
                    conflicts,
                    child_path,
                )
        return merged

    if isinstance(left, list) and isinstance(right, list):
        return left + right

    if left == right and type(left) is type(right):
        return left

    conflicts.append(path or "<root>")

    if conflict_policy == "first":
        return left
    if conflict_policy == "last":
        return right
    if conflict_policy == "list":
        return [left, right]
    msg = f"Conflicting values for key: {path or '<root>'}"
    raise ValueError(msg)


def dedup_value(value: Any) -> Any:
    if isinstance(value, list):
        seen: set[str] = set()
        compact: list[Any] = []
        for item in value:
            normalized = dedup_value(item)
            marker = json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if marker not in seen:
                seen.add(marker)
                compact.append(normalized)
        return compact

    if isinstance(value, dict):
        return {key: dedup_value(value[key]) for key in sorted(value.keys(), key=str)}

    return value


def expand_inputs(input_specs: Sequence[str]) -> tuple[list[Path], list[tuple[Path, str]]]:
    paths: list[Path] = []
    failed: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    for input_spec in input_specs:
        expanded_spec = str(Path(input_spec).expanduser())

        if glob.has_magic(expanded_spec):
            matches = sorted(glob.glob(expanded_spec, recursive=True))
            if not matches:
                failed.append((Path(input_spec), "no files matched"))
                continue
        else:
            matches = [expanded_spec]

        for match in matches:
            path = Path(match)
            identity = path.absolute()
            if identity not in seen:
                paths.append(path)
                seen.add(identity)

    return paths, failed


def new_output_path(directory: Path) -> Path:
    alphabet = string.ascii_lowercase
    while True:
        filename = "".join(secrets.choice(alphabet) for _ in range(8)) + ".json"
        output_path = directory / filename
        if not output_path.exists():
            return output_path


def write_json_exclusively(path: Path, value: Any, sort_keys: bool = False) -> None:
    "Write json exclusively."
    created = False
    try:
        with path.open("x", encoding="utf-8", newline="\n") as file:
            created = True
            json.dump(
                value,
                file,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
                sort_keys=sort_keys,
            )
            file.write("\n")
    except (OSError, TypeError, ValueError, RecursionError):
        if created:
            path.unlink(missing_ok=True)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    "Parse args."
    parser = _ParserShim()
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    "Run the command-line interface."
    args = parse_args(argv)
    input_paths, failed = expand_inputs(args.inputs)

    groups: dict[tuple[Any, ...], list[tuple[Path, Any]]] = {}

    for input_path in input_paths:
        if not input_path.is_file():
            failed.append((input_path, "not a file"))
            continue

        value, error = read_json(input_path)
        if error is not None:
            failed.append((input_path, error))
            continue

        try:
            schema = schema_of(value)
        except (TypeError, RecursionError) as schema_error:
            failed.append((input_path, str(schema_error)))
            continue

        groups.setdefault(schema, []).append((input_path, value))

    for input_path, error in failed:
        print(f"Skipped {input_path}: {error}", file=sys.stderr)

    if not groups:
        print("Error: no valid JSON input files were found.", file=sys.stderr)
        return 1

    matching_files = max(groups.values(), key=len)
    if len(matching_files) < 2:
        print(
            "Error: fewer than two valid input files have compatible schemas.",
            file=sys.stderr,
        )
        return 1

    merged = matching_files[0][1]
    conflicts: list[str] = []

    try:
        for _, value in matching_files[1:]:
            merged = merge_values(
                merged,
                value,
                args.conflict,
                conflicts,
            )
    except (ValueError, RecursionError) as error:
        print(f"Error merging input files: {error}", file=sys.stderr)
        return 1

    if args.dedup:
        merged = dedup_value(merged)

    merged_paths = {path for path, _ in matching_files}
    valid_paths = {path for group in groups.values() for path, _ in group}
    different_schema_paths = valid_paths - merged_paths

    if args.dry_run:
        print(f"Would merge {len(matching_files)} file(s):")
        for path, _ in matching_files:
            print(f"  {path}")
        if conflicts:
            print(f"Found {len(conflicts)} conflicting key(s).")
        if different_schema_paths:
            print("Not selected because their schemas differ:")
            for path in sorted(different_schema_paths):
                print(f"  {path}")
        return 0

    try:
        if args.output is not None:
            output_path = args.output.expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            write_json_exclusively(output_path, merged, sort_keys=args.sort_keys)
        else:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            while True:
                output_path = new_output_path(args.output_dir)
                try:
                    write_json_exclusively(output_path, merged, sort_keys=args.sort_keys)
                    break
                except FileExistsError:
                    continue
    except (OSError, TypeError, ValueError, RecursionError) as error:
        print(f"Error writing output: {error}", file=sys.stderr)
        return 1

    print(f"Merged {len(matching_files)} file(s) into {output_path}")

    if conflicts:
        print(f"Resolved {len(conflicts)} conflicting key(s) using --conflict {args.conflict}.")

    if args.dedup:
        print("Removed duplicate values from merged output.")

    if args.sort_keys:
        print("Sorted object keys in output.")

    if different_schema_paths:
        print("Skipped files with a different schema:")
        for path in sorted(different_schema_paths):
            print(f"  {path}")

    return 0


def _run() -> None:
    raise SystemExit(main())


app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
)


@app.command(help="Merge JSON files with matching schemas, including bilingual string dictionaries.")
def cli(
    inputs: Annotated[
        list[str], typer.Argument(help="Input JSON files or glob patterns; quote patterns containing wildcards")
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory for a randomly named output file (default: current directory)"),
    ] = Path.cwd(),
    output: Annotated[
        Path | None, typer.Option("--output", help="Write to this exact path; existing files are not overwritten")
    ] = None,
    conflict: Annotated[
        Conflict, typer.Option("--conflict", help="How to handle keys with different values (default: last)")
    ] = Conflict.last,
    dedup: Annotated[
        bool, typer.Option("-d", "--dedup", help="Remove duplicate list items and repeated values after merging")
    ] = False,
    sort_keys: Annotated[bool, typer.Option("-s", "--sort-keys", help="Sort object keys in the output JSON")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report which files would be merged without writing an output file")
    ] = False,
) -> None:
    _STATE.update(
        inputs=inputs,
        output_dir=output_dir,
        output=output,
        conflict=conflict,
        dedup=dedup,
        sort_keys=sort_keys,
        dry_run=dry_run,
    )
    _run()


if __name__ == "__main__":
    app()
