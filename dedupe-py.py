#!/data/data/com.termux/files/usr/bin/env python
import argparse
import ast
import collections
import json
import sys
from multiprocessing import Pool
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

import astor
from loguru import logger
from rich.console import Console
from rich.table import Table

from dh import MAX_WORKERS

if TYPE_CHECKING:
    from multiprocessing.pool import AsyncResult

REPEATED_JSON_PATH: Final[Path] = Path("repeated.json")
DEFAULT_MIN_FILES: Final[int] = 3
REPORT_ROW_LIMIT: Final[int] = 50

DefinitionKey = tuple[str, str, str]
DefinitionsMap = collections.defaultdict[DefinitionKey, list[str]]
SourceMap = dict[DefinitionKey, str | None]
FileMap = dict[Path, list[str]]
Task = tuple[Path, list[str]]

console = Console()

IGNORED_CONSTANT_BASE_NAMES: Final[frozenset[str]] = frozenset({
    "LOG",
    "HIDE_CURSOR",
    "SHOW_CURSOR",
    "RESET",
    "CWD",
    "GREEN",
    "CHUNK_SIZE",
    "LOGGER",
    "CLEAR",
    "ENTER_ALT",
    "LEAVE_ALT",
    "TRANSLATOR",
    "BACKEND",
    "BACKEND_ERROR",
    "HALF_BLOCK",
    "ROOT",
    "REVERSE",
    "__VERSION__",
})


def _constant_name_variants(name: str) -> set[str]:
    parts = name.lower().split("_")
    pascal_case = "".join(part.capitalize() for part in parts)
    camel_case = pascal_case[:1].lower() + pascal_case[1:]
    return {name, name.lower(), pascal_case, camel_case}


IGNORED_CONSTANT_NAMES: Final[frozenset[str]] = frozenset(
    variant for name in IGNORED_CONSTANT_BASE_NAMES for variant in _constant_name_variants(name)
)


def get_source(node: ast.AST, content: str) -> str | None:
    return ast.get_source_segment(content, node)


def normalize_source(source: str | None) -> str:
    if not source:
        return ""
    return "\n".join(line.rstrip() for line in source.strip().splitlines())


def _iter_target_py_files(root: Path, exclude: list[str]) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if ".git" in path.parts:
            continue
        if any(pat in str(path) for pat in exclude):
            continue
        files.append(path)
    return files


def _collect_definitions(
    file_path: Path,
    definitions: DefinitionsMap,
    source_map: SourceMap,
) -> None:
    try:
        content: str = file_path.read_text(encoding="utf-8")
        tree: ast.Module = ast.parse(content)
    except Exception:
        return

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "main":
                continue

            source = get_source(node, content)
            norm = normalize_source(source)
            key: DefinitionKey = (type(node).__name__, node.name, norm)
            definitions[key].append(str(file_path))
            if key not in source_map:
                source_map[key] = source

        elif isinstance(node, ast.ClassDef):
            source = get_source(node, content)
            norm = normalize_source(source)
            key = (type(node).__name__, node.name, norm)
            definitions[key].append(str(file_path))
            if key not in source_map:
                source_map[key] = source

        elif isinstance(node, ast.Assign):
            source = get_source(node, content)
            norm = normalize_source(source)
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in IGNORED_CONSTANT_NAMES:
                    key = ("Constant", target.id, norm)
                    definitions[key].append(str(file_path))
                    if key not in source_map:
                        source_map[key] = source

        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in IGNORED_CONSTANT_NAMES:
                continue

            source = get_source(node, content)
            norm = normalize_source(source)
            key = ("Constant", node.target.id, norm)
            definitions[key].append(str(file_path))
            if key not in source_map:
                source_map[key] = source


def analyze_files(root: Path, min_files: int, exclude: list[str]) -> list[dict[str, Any]]:
    definitions: DefinitionsMap = collections.defaultdict(list)
    source_map: SourceMap = {}
    for py_file in _iter_target_py_files(root, exclude):
        _collect_definitions(py_file, definitions, source_map)
    repeated: list[dict[str, Any]] = []
    for key, paths in definitions.items():
        unique_paths: list[str] = list(set(paths))
        if len(unique_paths) >= min_files:
            repeated.append({
                "type": key[0],
                "name": key[1],
                "source": source_map[key],
                "count": len(unique_paths),
                "files": unique_paths,
            })
    repeated.sort(key=lambda item: item["count"], reverse=True)
    return repeated


def write_repeated_json(repeated: list[dict[str, Any]], output: Path) -> None:
    output.write_text(json.dumps(repeated, indent=2, ensure_ascii=False), encoding="utf-8")


def build_refactor_map(repeated: list[dict[str, Any]]) -> FileMap:
    file_to_objects: collections.defaultdict[Path, list[str]] = collections.defaultdict(list)
    for item in repeated:
        obj_name: str = item["name"]
        for file_path_str in item["files"]:
            file_to_objects[Path(file_path_str)].append(obj_name)
    return dict(file_to_objects)


class ASTStripper(ast.NodeTransformer):
    def __init__(self, target_names: list[str]) -> None:
        super().__init__()
        self.target_names: set[str] = set(target_names)
        self.removed_something: bool = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef | None:
        if node.name in self.target_names:
            self.removed_something = True
            return None
        return cast("ast.FunctionDef", self.generic_visit(node))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef | None:
        if node.name in self.target_names:
            self.removed_something = True
            return None
        return cast("ast.AsyncFunctionDef", self.generic_visit(node))

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef | None:
        if node.name in self.target_names:
            self.removed_something = True
            return None
        return cast("ast.ClassDef", self.generic_visit(node))

    def visit_Assign(self, node: ast.Assign) -> ast.Assign | None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in self.target_names:
                self.removed_something = True
                return None
        return cast("ast.Assign", self.generic_visit(node))


def refactor_single_file(file_path: Path, objects_to_remove: list[str]) -> bool:
    try:
        source_code: str = file_path.read_text(encoding="utf-8")
        tree: ast.Module = ast.parse(source_code)
    except Exception as exc:
        logger.error(f"❌ Error parsing {file_path.name}: {exc}")
        return False
    stripper: ASTStripper = ASTStripper(objects_to_remove)
    modified_tree: ast.AST = stripper.visit(tree)
    ast.fix_missing_locations(modified_tree)
    if not stripper.removed_something:
        print(f"➖ No matching structural nodes found inside {file_path.name}")
        return False
    import_names: str = ", ".join(sorted(objects_to_remove))
    import_statement: str = f"from dh import {import_names}\n"
    try:
        cleaned_source: str = astor.to_source(modified_tree)
    except Exception as exc:
        logger.error(f"❌ Failed to stringify AST for {file_path.name}: {exc}")
        return False
    lines: list[str] = cleaned_source.splitlines(keepends=True)
    insert_idx: int = 0
    if lines and lines[0].startswith("#!"):
        insert_idx = 1
    if len(lines) > insert_idx and (
        lines[insert_idx].strip().startswith('"""') or lines[insert_idx].strip().startswith("'''")  # editor: """ '''
    ):
        insert_idx += 1
    lines.insert(insert_idx, import_statement)
    try:
        file_path.write_text("".join(lines), encoding="utf-8")
        print(f"✅ Refactored {file_path.name}: Stripped {objects_to_remove} -> added 'dh' import")
        return True
    except Exception as exc:
        logger.error(f"❌ Error writing updates back to {file_path.name}: {exc}")
        return False


def print_report(repeated: list[dict[str, Any]]) -> None:
    table = Table(title="Duplicated Definitions")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="magenta")
    table.add_column("Count", justify="right", style="yellow")
    table.add_column("Files", justify="right")
    for item in repeated[:REPORT_ROW_LIMIT]:
        table.add_row(item["type"], item["name"], str(item["count"]), str(len(item["files"])))
    console.print(table)
    if len(repeated) > REPORT_ROW_LIMIT:
        console.print(f"[dim]... and {len(repeated) - REPORT_ROW_LIMIT} more[/]")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dupe-refactor",
        description="Find duplicated Python definitions and optionally strip them.",
    )
    parser.add_argument(
        "-a",
        "--apply",
        action="store_true",
        help="Apply changes (default: report only, source files are not modified).",
    )
    parser.add_argument(
        "-m",
        "--min-files",
        type=int,
        default=DEFAULT_MIN_FILES,
        metavar="N",
        help=f"Minimum number of files containing a duplicate (default: {DEFAULT_MIN_FILES}).",
    )
    parser.add_argument(
        "-j",
        "--json",
        type=Path,
        default=REPEATED_JSON_PATH,
        metavar="PATH",
        help=f"Path to write the JSON report (default: {REPEATED_JSON_PATH}).",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=MAX_WORKERS,
        metavar="N",
        help=f"Number of parallel workers when applying (default: {MAX_WORKERS}).",
    )
    parser.add_argument(
        "-r",
        "--root",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Root directory to scan (default: current directory).",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        action="append",
        default=None,
        metavar="PATTERN",
        help="Substring to exclude from scanning (repeatable).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress the summary table.",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Do not write the JSON report.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version="dupe-refactor 1.0.0",
    )
    args = parser.parse_args(argv)
    if args.exclude is None:
        args.exclude = []
    return args


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace = parse_args(argv if argv is not None else sys.argv[1:])
    root: Path = args.root
    exclude: list[str] = args.exclude
    if not args.quiet:
        console.print(f"🔎 Analyzing Python files under [bold]{root}[/] ...")
    repeated: list[dict[str, Any]] = analyze_files(root, args.min_files, exclude)
    if not repeated:
        logger.warning(f"No definitions duplicated in {args.min_files} or more files. Nothing to do.")
        return 0
    if not args.quiet:
        print_report(repeated)
    if not args.no_json:
        write_repeated_json(repeated, args.json)
        console.print(f"[green]Wrote[/] {len(repeated)} entries to [bold]{args.json}[/]")
    refactor_map: FileMap = build_refactor_map(repeated)
    tasks: list[Task] = [(p, objs) for p, objs in refactor_map.items() if p.is_file()]
    if not args.apply:
        if tasks:
            console.print(
                f"[yellow]Dry-run:[/] would refactor [bold]{len(tasks)}[/] file(s). "
                "Re-run with [bold]-a/--apply[/] to apply changes."
            )
        else:
            console.print("[yellow]Dry-run:[/] no existing files to refactor.")
        return 0
    if not tasks:
        console.print("No existing files to refactor.")
        return 0
    console.print(f"🚀 Refactoring [bold]{len(tasks)}[/] file(s) with [bold]{args.workers}[/] worker(s)...")
    with Pool(processes=args.workers) as pool:
        async_results: list[AsyncResult[bool]] = [
            pool.apply_async(refactor_single_file, (file_path, objects)) for file_path, objects in tasks
        ]
        for async_res in async_results:
            try:
                async_res.get()
            except Exception as exc:
                logger.error(f"❌ Worker raised: {exc}")
    console.print("🎉 Structural refactoring complete!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
