#!/data/data/com.termux/files/usr/bin/env python
"""Strip tree-sitter comments and blank lines from source files in place.

Files are selected by extension or, for extensionless/unknown filenames, a
Bash or Zsh shebang. Every cleaned file is parsed again before atomic replace;
invalid results are left untouched. HTML script/style raw-text blocks are also
processed with their JavaScript/CSS grammars.

Usage::

    python tree_sitter_strip.py [--check] [PATH ...]
    python tree_sitter_strip.py src/ deploy.sh

Install the grammar wheels in ``requirements.txt`` to enable all published
bindings. A missing language binding affects only files of that language.
"""

import argparse
import importlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from atomicio import atomic_write
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text
from tree_sitter import Language, Node, Parser

_EXTENSION_TO_LANGUAGE: Final[dict[str, str]] = {
    ".rs": "rust",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "zsh",
    ".c": "c",
    ".h": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".css": "css",
    ".html": "html",
    ".htm": "html",
    ".toml": "toml",
    ".lua": "lua",
    ".vim": "vim",
}
_GRAMMAR_FACTORIES: Final[dict[str, tuple[str, str]]] = {
    "bash": ("tree_sitter_bash", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "css": ("tree_sitter_css", "language"),
    "html": ("tree_sitter_html", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "lua": ("tree_sitter_lua", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "toml": ("tree_sitter_toml", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "vim": ("tree_sitter_vim", "language"),
    "zsh": ("tree_sitter_zsh", "language"),
}
_SHEBANG_PATTERNS: Final[tuple[tuple[re.Pattern[bytes], str], ...]] = (
    (re.compile(rb"(?:^|[/ ])(?:ba|da|k|mk|pdk|a)?sh(?:[\s-]|$)", re.I), "bash"),
    (re.compile(rb"(?:^|[/ ])zsh(?:[\s-]|$)", re.I), "zsh"),
)
_COMMENT_NODE_TYPES: Final[frozenset[str]] = frozenset({"comment", "line_comment", "block_comment"})
_PARSERS: dict[str, Parser] = {}


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Outcome and byte-size statistics for one file."""

    path: Path
    original_bytes: int
    reduced_bytes: int
    status: str
    detail: str = ""


def _make_parser(language_key: str) -> Parser:
    """Create a parser for a supported grammar across tree-sitter API versions."""
    try:
        module_name, factory_name = _GRAMMAR_FACTORIES[language_key]
    except KeyError as exc:
        raise ValueError(f"Unsupported language: {language_key}") from exc
    grammar_module = importlib.import_module(module_name)
    language = Language(getattr(grammar_module, factory_name)())
    try:
        return Parser(language)
    except TypeError:
        parser = Parser()
        parser.language = language
        return parser


def get_parser(language_key: str) -> Parser:
    """Return a cached parser for ``language_key``."""
    if language_key not in _PARSERS:
        _PARSERS[language_key] = _make_parser(language_key)
    return _PARSERS[language_key]


def detect_language(path: Path, source: bytes | None = None) -> str | None:
    """Identify the parser from a supported shebang, falling back to extension."""
    by_extension = _EXTENSION_TO_LANGUAGE.get(path.suffix.lower())
    if source is None:
        try:
            with path.open("rb") as file_handle:
                source = file_handle.readline(512)
        except OSError:
            return by_extension
    first_line = source.splitlines()[0] if source.splitlines() else b""
    if first_line.startswith(b"#!"):
        for pattern, language_key in _SHEBANG_PATTERNS:
            if pattern.search(first_line[2:]):
                return language_key
    return by_extension


def _is_error(node: Node) -> bool:
    """Check a syntax tree recursively for error or missing nodes."""
    if node.type == "ERROR" or node.is_missing or getattr(node, "is_error", False):
        return True
    return any(_is_error(child) for child in node.children)


def is_valid(parser: Parser, source: bytes) -> bool:
    """Return whether the given source parses without tree-sitter errors."""
    try:
        return not _is_error(parser.parse(source).root_node)
    except Exception:
        return False


def collect_comment_ranges(root: Node, source: bytes) -> list[tuple[int, int]]:
    """Collect comment byte spans and preserve a leading interpreter shebang."""
    ranges: list[tuple[int, int]] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in _COMMENT_NODE_TYPES:
            start, end = node.start_byte, node.end_byte
            if not (start == 0 and source.startswith(b"#!")):
                ranges.append((start, end))
            continue
        stack.extend(reversed(node.children))
    return ranges


def _html_raw_text_blocks(root: Node, source: bytes) -> list[tuple[str, int, int]]:
    """Return embedded script/style spans and their respective grammar names."""
    blocks: list[tuple[str, int, int]] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in {"script_element", "style_element"}:
            embedded_language = "javascript" if node.type == "script_element" else "css"
            raw_text = next((child for child in node.children if child.type == "raw_text"), None)
            if raw_text is not None:
                blocks.append((embedded_language, raw_text.start_byte, raw_text.end_byte))
            else:
                opening_tag = next((child for child in node.children if child.type == "start_tag"), None)
                empty_offset = opening_tag.end_byte if opening_tag is not None else node.start_byte
                blocks.append((embedded_language, empty_offset, empty_offset))
        stack.extend(reversed(node.children))
    return blocks


def _html_embedded_comments(root: Node, source: bytes) -> tuple[list[tuple[int, int]], list[tuple[int, str, bytes]]]:
    """Collect JavaScript/CSS comments in HTML raw-text blocks for safe cleanup."""
    ranges: list[tuple[int, int]] = []
    blocks_to_validate: list[tuple[int, str, bytes]] = []
    for block_index, (language_key, start, end) in enumerate(_html_raw_text_blocks(root, source)):
        embedded = source[start:end]
        parser = get_parser(language_key)
        embedded_ranges = collect_comment_ranges(parser.parse(embedded).root_node, embedded)
        if embedded_ranges:
            ranges.extend((start + left, start + right) for left, right in embedded_ranges)
            blocks_to_validate.append((block_index, language_key, embedded))
    return ranges, blocks_to_validate


def remove_comment_ranges(source: bytes, ranges: list[tuple[int, int]]) -> bytes:
    """Delete comments, retaining line breaks and separators between tokens."""
    if not ranges:
        return source
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    output = bytearray()
    cursor = 0
    for start, end in merged:
        output.extend(source[cursor:start])
        comment = source[start:end]
        line_breaks = bytes(byte for byte in comment if byte in (0x0A, 0x0D))
        if not line_breaks and start > 0 and end < len(source):
            before = source[start - 1 : start]
            after = source[end : end + 1]
            if before not in b" \t\r\n" and after not in b" \t\r\n":
                output.extend(b" ")
        output.extend(line_breaks)
        cursor = end
    output.extend(source[cursor:])
    return bytes(output)


def remove_blank_lines(source: bytes) -> bytes:
    """Remove blank lines and trailing horizontal whitespace from retained lines."""
    retained: list[bytes] = []
    for line in source.splitlines(keepends=True):
        if line.endswith(b"\r\n"):
            body, ending = line[:-2], b"\r\n"
        elif line.endswith((b"\n", b"\r")):
            body, ending = line[:-1], line[-1:]
        else:
            body, ending = line, b""
        body = body.rstrip(b" \t")
        if body.strip():
            retained.append(body + ending)
    return b"".join(retained)


def process_file(path: Path, *, check_only: bool = False) -> ProcessResult:
    """Clean a file and validate its output before optionally writing in place."""
    try:
        if path.is_symlink() or not path.is_file():
            return ProcessResult(path, 0, 0, "skipped", "not a regular file")
        source = path.read_bytes()
        language_key = detect_language(path, source)
        if language_key is None:
            return ProcessResult(path, len(source), 0, "unsupported", "no supported extension or shebang")
        parser = get_parser(language_key)
        original_tree = parser.parse(source)
        comments = collect_comment_ranges(original_tree.root_node, source)
        embedded_blocks: list[tuple[int, str, bytes]] = []
        if language_key == "html":
            embedded_ranges, embedded_blocks = _html_embedded_comments(original_tree.root_node, source)
            comments.extend(embedded_ranges)
        cleaned = remove_blank_lines(remove_comment_ranges(source, comments))
        cleaned_tree = parser.parse(cleaned)
        if _is_error(cleaned_tree.root_node):
            return ProcessResult(path, len(source), 0, "invalid", "cleaned output failed tree-sitter validation")
        if embedded_blocks:
            cleaned_blocks = _html_raw_text_blocks(cleaned_tree.root_node, cleaned)
            for block_index, embedded_language, _original in embedded_blocks:
                if block_index >= len(cleaned_blocks):
                    return ProcessResult(path, len(source), 0, "invalid", "HTML embedded block changed during cleanup")
                cleaned_language, start, end = cleaned_blocks[block_index]
                if cleaned_language != embedded_language or not is_valid(
                    get_parser(embedded_language), cleaned[start:end]
                ):
                    return ProcessResult(
                        path, len(source), 0, "invalid", "cleaned embedded code failed tree-sitter validation"
                    )
        reduced = max(0, len(source) - len(cleaned))
        if reduced and not check_only:
            atomic_write(path, cleaned)
        return ProcessResult(path, len(source), reduced, "changed" if reduced else "unchanged")
    except Exception as exc:
        return ProcessResult(path, 0, 0, "error", str(exc))


def _is_candidate(path: Path) -> bool:
    """Return whether a file has a supported extension or interpreter shebang."""
    if path.suffix.lower() in _EXTENSION_TO_LANGUAGE:
        return True
    try:
        with path.open("rb") as file_handle:
            header = file_handle.readline(512)
    except OSError:
        return False
    return detect_language(path, header) is not None


def find_files(inputs: list[Path]) -> list[Path]:
    """Expand input paths to unique, sorted targets while skipping hidden trees."""
    found: dict[Path, Path] = {}
    for item in inputs:
        if item.is_file() and not item.is_symlink() and _is_candidate(item):
            found.setdefault(item.resolve(), item)
        elif item.is_dir():
            for candidate in item.rglob("*"):
                hidden_parent = any(part.startswith(".") for part in candidate.relative_to(item).parts[:-1])
                if (
                    candidate.is_file()
                    and not candidate.is_symlink()
                    and not hidden_parent
                    and _is_candidate(candidate)
                ):
                    found.setdefault(candidate.resolve(), candidate)
    return sorted(found.values(), key=lambda candidate: str(candidate).lower())


def build_table(results: list[ProcessResult], total: int, width: int) -> Table:
    """Build the terminal-width-adaptive Rich progress table."""
    table = Table(show_header=False, box=None, padding=(0, 1), expand=True, width=width)
    index_width = len(f"[{max(total, 1)}/{max(total, 1)}]")
    table.add_column("index", justify="right", no_wrap=True, width=index_width)
    table.add_column("filename", overflow="ellipsis", no_wrap=True, ratio=1)
    table.add_column("bytes", justify="right", no_wrap=True, width=10)
    for index, result in enumerate(results, start=1):
        style = "green" if result.reduced_bytes > 0 else "dim"
        table.add_row(f"[{index}/{total}]", str(result.path), Text(str(result.reduced_bytes), style=style))
    return table


def main(argv: list[str] | None = None) -> int:
    """Run the command line and return a shell-compatible exit code."""
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--check", action="store_true", help="report potential savings without writing")
    argument_parser.add_argument("paths", nargs="*", type=Path, help="files/directories (default: current directory)")
    args = argument_parser.parse_args(argv)
    files = find_files(args.paths or [Path.cwd()])
    console = Console()
    if not files:
        console.print("[yellow]No supported files found.[/]")
        return 0
    results: list[ProcessResult] = []
    total_freed = 0
    with Live(build_table(results, len(files), console.width), console=console, refresh_per_second=12) as live:
        for path in files:
            result = process_file(path, check_only=args.check)
            results.append(result)
            total_freed += result.reduced_bytes
            live.update(build_table(results, len(files), console.width))
    total_label = "Potential space freed" if args.check else "Total space freed"
    console.print(f"\n[bold]{total_label}:[/] [green]{total_freed}[/] bytes")
    errors = [result for result in results if result.status in {"invalid", "error"}]
    for result in errors:
        console.print(f"[red]{result.path}: {result.detail}[/]")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
