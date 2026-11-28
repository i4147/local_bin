#!/data/data/com.termux/files/usr/bin/env python
"""Strip tree-sitter comments and blank lines from source files in place.

Files are selected by extension or, for extensionless/unknown filenames, a
Bash or Zsh shebang. Every cleaned file is parsed again before atomic replace;
the rewrite is refused if cleanup makes the syntax tree worse than the input.
HTML ``<script>``/``<style>`` raw-text blocks are parsed with their JavaScript,
TypeScript, or CSS grammar and cleaned the same way, while non-code blocks
(JSON-LD, import maps, text templates) and preformatted regions (``<pre>``,
``<textarea>``, template literals, heredocs, raw strings) are left untouched.

Usage::

    python tree_sitter_strip.py [--check] [PATH ...]
    python tree_sitter_strip.py src/ deploy.sh

Install the grammar wheels in ``requirements.txt`` to enable all published
bindings. A missing language binding affects only files of that language.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
import re
from typing import Annotated, Final

from dh import atomic_write
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text
from tree_sitter import Language, Node, Parser
import typer


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
    (
        re.compile(rb"(?:^|[/ ])(?:ba|da|k|mk|pdk|a)?sh(?:[\s-]|$)", re.IGNORECASE),
        "bash",
    ),
    (re.compile(rb"(?:^|[/ ])zsh(?:[\s-]|$)", re.IGNORECASE), "zsh"),
)
# ``js_comment`` is what tree-sitter-css calls a ``//`` comment; without it,
# line comments inside <style> were silently never removed.
_COMMENT_NODE_TYPES: Final[frozenset[str]] = frozenset({"comment", "line_comment", "block_comment", "js_comment"})
# Script Parser: ``<script type="...">`` values that hold real code we may clean.
# Anything else (application/ld+json, importmap, speculationrules, text/x-template,
# text/html, ...) is data or markup, not JavaScript, and is left untouched.
_SCRIPT_TYPE_LANGUAGES: Final[dict[str, str]] = {
    "": "javascript",
    "module": "javascript",
    "text/javascript": "javascript",
    "application/javascript": "javascript",
    "text/ecmascript": "javascript",
    "application/ecmascript": "javascript",
    "application/x-javascript": "javascript",
    "text/x-javascript": "javascript",
    "text/jscript": "javascript",
    "text/livescript": "javascript",
    "text/babel": "javascript",
    "text/jsx": "javascript",
    "text/typescript": "typescript",
    "application/typescript": "typescript",
    "text/tsx": "tsx",
    "text/ts": "typescript",
}
_STYLE_TYPE_LANGUAGES: Final[dict[str, str]] = {
    "": "css",
    "text/css": "css",
}
# Node types whose bytes are literal data: blank-line stripping must not touch them.
_PROTECTED_NODE_TYPES: Final[frozenset[str]] = frozenset({
    "template_string",
    "heredoc_body",
    "raw_string_literal",
    "string_literal",
    "concatenated_string",
    "verbatim_string_literal",
    "string",
    "block_string",
})
_HTML_PREFORMATTED_TAGS: Final[frozenset[bytes]] = frozenset({b"pre", b"textarea"})
_PARSERS: dict[str, Parser] = {}


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Outcome and byte-size statistics for one file."""

    path: Path
    original_bytes: int
    reduced_bytes: int
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class _EmbeddedBlock:
    """One ``<script>``/``<style>`` body, with ``language=None`` when not code."""

    language: str | None
    start: int
    end: int


def _make_parser(language_key: str) -> Parser:
    """Create a parser for a supported grammar across tree-sitter API versions."""
    try:
        module_name, factory_name = _GRAMMAR_FACTORIES[language_key]
    except KeyError as exc:
        msg = f"Unsupported language: {language_key}"
        raise ValueError(msg) from exc
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


def _defect_counts(node: Node) -> tuple[int, int]:
    """Count visible ERROR and missing nodes in a subtree."""
    errors = 0
    missing = 0
    stack: list[Node] = [node]
    while stack:
        current = stack.pop()
        if current.type == "ERROR":
            errors += 1
        elif current.is_missing:
            missing += 1
        stack.extend(current.children)
    return errors, missing


def _regressed(before: Node, after: Node) -> bool:
    """Return whether ``after`` is syntactically worse than ``before``.

    Real-world HTML is often already imperfect (unclosed ``<li>``/``<p>``,
    template placeholders, a stray ``<`` in text), and the HTML grammar reports
    those as errors. Refusing to clean every such file is what made the tool
    look broken, so the check is relative: a clean input must stay clean, and a
    dirty input may not accumulate extra ERROR/missing nodes.
    """
    if not after.has_error:
        return False
    if not before.has_error:
        return True
    before_errors, before_missing = _defect_counts(before)
    after_errors, after_missing = _defect_counts(after)
    return after_errors > before_errors or after_missing > before_missing


def is_valid(parser: Parser, source: bytes) -> bool:
    """Return whether the given source parses without tree-sitter errors."""
    try:
        return not parser.parse(source).root_node.has_error
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


def _attribute_text(node: Node) -> bytes:
    """Return an HTML attribute's value bytes without surrounding quotes."""
    for child in node.children:
        if child.type == "quoted_attribute_value":
            inner = next(
                (item for item in child.children if item.type == "attribute_value"),
                None,
            )
            return inner.text or b"" if inner is not None else b""
        if child.type == "attribute_value":
            return child.text or b""
    return b""


def _embedded_language(start_tag: Node | None, kind: str) -> str | None:
    """Map a ``<script>``/``<style>`` ``type`` attribute to a grammar name."""
    if start_tag is None:
        return None
    table = _SCRIPT_TYPE_LANGUAGES if kind == "script" else _STYLE_TYPE_LANGUAGES
    value = b""
    for attribute in start_tag.children:
        if attribute.type != "attribute":
            continue
        name = next(
            (child for child in attribute.children if child.type == "attribute_name"),
            None,
        )
        if name is None or (name.text or b"").lower() != b"type":
            continue
        value = _attribute_text(attribute)
        break
    mime = value.split(b";")[0].strip().lower().decode("utf-8", "replace")
    return table.get(mime)


def _html_raw_text_blocks(root: Node, source: bytes) -> list[_EmbeddedBlock]:
    """Return embedded script/style spans and their respective grammar names.

    Unclosed tags make tree-sitter-html fold the rest of the document into a
    single ERROR node, dropping the ``script_element``/``style_element``
    wrapper. Those bodies are recovered from the ``start_tag`` + ``raw_text``
    sibling pair so their comments are still cleaned.
    """
    blocks: list[_EmbeddedBlock] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in {"script_element", "style_element"}:
            kind = "script" if node.type == "script_element" else "style"
            start_tag = next((child for child in node.children if child.type == "start_tag"), None)
            raw_text = next((child for child in node.children if child.type == "raw_text"), None)
            if raw_text is not None:
                start, end = raw_text.start_byte, raw_text.end_byte
            else:
                start = start_tag.end_byte if start_tag is not None else node.start_byte
                end = start
            blocks.append(_EmbeddedBlock(_embedded_language(start_tag, kind), start, end))
            continue
        if node.type == "ERROR":
            for index, child in enumerate(node.children):
                if child.type != "start_tag":
                    continue
                name = next((item for item in child.children if item.type == "tag_name"), None)
                kind = (name.text or b"").lower().decode("utf-8", "replace") if name is not None else ""
                if kind not in {"script", "style"}:
                    continue
                following = node.children[index + 1] if index + 1 < len(node.children) else None
                if following is None or following.type != "raw_text":
                    continue
                blocks.append(
                    _EmbeddedBlock(
                        _embedded_language(child, kind),
                        following.start_byte,
                        following.end_byte,
                    )
                )
            stack.extend(reversed(node.children))
            continue
        stack.extend(reversed(node.children))
    return blocks


def _legacy_html_comment_ranges(embedded: bytes, root: Node) -> list[tuple[int, int]]:
    """Return whole-line ``<!--``/``-->`` wrapper tokens used in inline scripts.

    These exist only for ancient browser escaping. Only tokens that occupy an
    entire line are removed: deleting an inline ``<!--`` (``var x = 1; <!-- ok``)
    would turn commented-out code into live code.
    """
    ranges: list[tuple[int, int]] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type == "html_comment":
            line_start = embedded.rfind(b"\n", 0, node.start_byte) + 1
            line_end = embedded.find(b"\n", node.end_byte)
            if line_end == -1:
                line_end = len(embedded)
            if not embedded[line_start : node.start_byte].strip() and not embedded[node.end_byte : line_end].strip():
                ranges.append((line_start, node.end_byte))
            continue
        stack.extend(reversed(node.children))
    return ranges


def _html_embedded_comments(
    root: Node, source: bytes
) -> tuple[list[tuple[int, int]], list[tuple[int, str, bytes, bool]]]:
    """Collect JavaScript/CSS comments in HTML raw-text blocks for safe cleanup."""
    ranges: list[tuple[int, int]] = []
    blocks_to_validate: list[tuple[int, str, bytes, bool]] = []
    for block_index, block in enumerate(_html_raw_text_blocks(root, source)):
        if block.language is None or block.end <= block.start:
            continue
        embedded = source[block.start : block.end]
        parser = get_parser(block.language)
        embedded_tree = parser.parse(embedded)
        embedded_ranges = collect_comment_ranges(embedded_tree.root_node, embedded)
        if block.language in {"javascript", "typescript", "tsx"}:
            embedded_ranges.extend(_legacy_html_comment_ranges(embedded, embedded_tree.root_node))
        if embedded_ranges:
            ranges.extend((block.start + left, block.start + right) for left, right in embedded_ranges)
            blocks_to_validate.append((
                block_index,
                block.language,
                embedded,
                not embedded_tree.root_node.has_error,
            ))
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


def _element_name(node: Node) -> bytes:
    """Return a lowercase tag name for an HTML element node."""
    start_tag = next((child for child in node.children if child.type == "start_tag"), None)
    if start_tag is None:
        return b""
    name = next((child for child in start_tag.children if child.type == "tag_name"), None)
    return (name.text or b"").lower() if name is not None else b""


def _protected_spans(root: Node, source: bytes, language_key: str) -> list[tuple[int, int]]:
    """Return byte spans whose whitespace is data and must not be rewritten."""
    spans: list[tuple[int, int]] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in _PROTECTED_NODE_TYPES:
            if b"\n" in source[node.start_byte : node.end_byte]:
                spans.append((node.start_byte, node.end_byte))
            continue
        if language_key == "html" and node.type == "element" and _element_name(node) in _HTML_PREFORMATTED_TAGS:
            start_tag = next((child for child in node.children if child.type == "start_tag"), None)
            end_tag = next((child for child in node.children if child.type == "end_tag"), None)
            open_end = start_tag.end_byte if start_tag is not None else node.start_byte
            close_start = end_tag.start_byte if end_tag is not None else node.end_byte
            if close_start > open_end:
                spans.append((open_end, close_start))
            continue
        stack.extend(node.children)
    return spans


def _protected_regions(root: Node, source: bytes, language_key: str) -> list[tuple[int, int]]:
    """Return all spans that must keep their whitespace, embedded blocks included."""
    spans = _protected_spans(root, source, language_key)
    if language_key != "html":
        return spans
    for block in _html_raw_text_blocks(root, source):
        if block.language is None or block.end <= block.start:
            continue
        embedded = source[block.start : block.end]
        embedded_root = get_parser(block.language).parse(embedded).root_node
        spans.extend(
            (block.start + left, block.start + right)
            for left, right in _protected_spans(embedded_root, embedded, block.language)
        )
    return spans


def remove_blank_lines(source: bytes, protected: list[tuple[int, int]] | None = None) -> bytes:
    """Remove blank lines and trailing horizontal whitespace from retained lines."""
    spans = sorted(protected or ())
    retained: list[bytes] = []
    offset = 0
    span_index = 0
    for line in source.splitlines(keepends=True):
        start = offset
        end = offset + len(line)
        offset = end
        while span_index < len(spans) and spans[span_index][1] <= start:
            span_index += 1
        if span_index < len(spans) and spans[span_index][0] < end:
            retained.append(line)
            continue
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


def _significant_bytes(data: bytes) -> bytes:
    """Return the non-whitespace byte skeleton used to verify no data was lost."""
    return b"".join(data.split())


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
        embedded_blocks: list[tuple[int, str, bytes, bool]] = []
        if language_key == "html":
            embedded_ranges, embedded_blocks = _html_embedded_comments(original_tree.root_node, source)
            comments.extend(embedded_ranges)
        comment_free = remove_comment_ranges(source, comments)
        stripped_tree = parser.parse(comment_free)
        cleaned = remove_blank_lines(
            comment_free,
            _protected_regions(stripped_tree.root_node, comment_free, language_key),
        )
        if _significant_bytes(cleaned) != _significant_bytes(comment_free):
            return ProcessResult(
                path,
                len(source),
                0,
                "invalid",
                "cleanup would have dropped non-whitespace bytes",
            )
        cleaned_tree = parser.parse(cleaned)
        if _regressed(original_tree.root_node, cleaned_tree.root_node):
            return ProcessResult(
                path,
                len(source),
                0,
                "invalid",
                "cleaned output failed tree-sitter validation",
            )
        if embedded_blocks:
            original_block_count = len(_html_raw_text_blocks(original_tree.root_node, source))
            cleaned_blocks = _html_raw_text_blocks(cleaned_tree.root_node, cleaned)
            if len(cleaned_blocks) != original_block_count:
                return ProcessResult(
                    path,
                    len(source),
                    0,
                    "invalid",
                    "HTML embedded block changed during cleanup",
                )
            for block_index, embedded_language, _original, was_valid in embedded_blocks:
                if block_index >= len(cleaned_blocks):
                    return ProcessResult(
                        path,
                        len(source),
                        0,
                        "invalid",
                        "HTML embedded block changed during cleanup",
                    )
                block = cleaned_blocks[block_index]
                if block.language != embedded_language:
                    return ProcessResult(
                        path,
                        len(source),
                        0,
                        "invalid",
                        "HTML embedded block changed during cleanup",
                    )
                if was_valid and not is_valid(get_parser(embedded_language), cleaned[block.start : block.end]):
                    return ProcessResult(
                        path,
                        len(source),
                        0,
                        "invalid",
                        "cleaned embedded code failed tree-sitter validation",
                    )
        reduced = max(0, len(source) - len(cleaned))
        changed = cleaned != source
        if changed and not check_only:
            atomic_write(path, cleaned)
        return ProcessResult(path, len(source), reduced, "changed" if changed else "unchanged")
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
        table.add_row(
            f"[{index}/{total}]",
            str(result.path),
            Text(str(result.reduced_bytes), style=style),
        )
    return table


app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
)


@app.command()
def main(
    check: Annotated[bool, typer.Option("--check", help="report potential savings without writing")] = False,
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help="files/directories (default: current directory)"),
    ] = None,
) -> None:
    """Run the command line and exit non-zero when any file failed."""
    files = find_files(paths or [Path.cwd()])
    console = Console()
    if not files:
        console.print("[yellow]No supported files found.[/]")
        raise typer.Exit(code=0)
    results: list[ProcessResult] = []
    total_freed = 0
    with Live(
        build_table(results, len(files), console.width),
        console=console,
        refresh_per_second=12,
    ) as live:
        for path in files:
            result = process_file(path, check_only=check)
            results.append(result)
            total_freed += result.reduced_bytes
            live.update(build_table(results, len(files), console.width))
    total_label = "Potential space freed" if check else "Total space freed"
    console.print(f"\n[bold]{total_label}:[/] [green]{total_freed}[/] bytes")
    errors = [result for result in results if result.status in {"invalid", "error"}]
    for result in errors:
        console.print(f"[red]{result.path}: {result.detail}[/]")
    # typer/click ignore a function's return value, so the exit code must be raised.
    raise typer.Exit(code=1 if errors else 0)


if __name__ == "__main__":
    app()
