#!/data/data/com.termux/files/usr/bin/env python
"""pyrg — a ripgrep-like recursive regex search in Python.

This module performs a recursive search over files (or a targeted subset)
using either a regular expression or a fixed string. It supports case folding,
inversion, multiline patterns, context lines, only-matching output, in-output
replacement, glob/extension/type filtering, ignore-file handling, JSON output,
counting, quiet mode, and parallel processing via a worker pool.

The command-line interface is built on :mod:`typer`. File discovery uses
:mod:`pathlib` (with :func:`os.scandir` for speed). Diagnostics are routed
through :mod:`loguru`. Highlighting uses black-on-yellow for file paths and
black-on-cyan for matched text.

Example::

    $ pyrg 'def main' -t py -i
    $ pyrg TODO -e md --replace 'DONE'

"""

from __future__ import annotations
import bisect
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
import fnmatch
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import TYPE_CHECKING, Any, Optional, TextIO

from dh import MAX_WORKERS, SKIP_DIRS, is_binary
from loguru import logger
import typer

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_PATH: Path = Path.home() / "tmp" / "apps" / "pyrg.log"


def _setup_logging() -> None:
    """Configure loguru: a debug log file, plus stderr fallback."""
    logger.remove()
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        logger.add(_LOG_PATH, level="DEBUG", encoding="utf-8")
    except OSError:
        logger.add(sys.stderr, level="WARNING", format="{message}")


_setup_logging()


# ---------------------------------------------------------------------------
# Type presets and colour palette
# ---------------------------------------------------------------------------

TYPE_PRESETS: dict[str, set[str]] = {
    "py": {".py", ".pyi", ".pyx", ".pyw"},
    "md": {".md", ".markdown", ".mdx"},
    "json": {".json", ".jsonl", ".geojson"},
    "log": {".log"},
    "js": {".js", ".jsx", ".mjs", ".cjs"},
    "ts": {".ts", ".tsx"},
    "rust": {".rs"},
    "go": {".go"},
    "c": {".c", ".h"},
    "cpp": {".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".hh"},
    "java": {".java"},
    "rb": {".rb"},
    "php": {".php"},
    "html": {".html", ".htm"},
    "css": {".css", ".scss", ".sass", ".less"},
    "yaml": {".yaml", ".yml"},
    "toml": {".toml"},
    "sh": {".sh", ".bash", ".zsh"},
    "sql": {".sql"},
    "xml": {".xml"},
    "csv": {".csv", ".tsv"},
}

ANSI_RESET: str = "\x1b[0m"
ANSI_FILENAME: str = "\x1b[30;43m"  # black on yellow background
ANSI_HIGHLIGHT: str = "\x1b[30;46m"  # black on cyan background
ANSI_LINE_NO: str = "\x1b[32m"  # green line numbers


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def normalize_extension(value: str) -> str:
    """Normalise a single extension token to ``.ext`` form (dotless input ok)."""
    v = value.strip().lower().rstrip(".")
    if not v:
        return ""
    if not v.startswith("."):
        v = "." + v
    return v


def parse_extension_args(raw_exts: Optional[list[str]]) -> set[str]:
    """Parse repeated/comma-separated ``-e`` values into a set of extensions."""
    out: set[str] = set()
    if not raw_exts:
        return out
    for raw in raw_exts:
        for piece in raw.split(","):
            ext = normalize_extension(piece)
            if ext:
                out.add(ext)
    return out


def resolve_type_presets(types: Optional[list[str]]) -> set[str]:
    """Expand ``-t`` type preset names (and comma lists) into extensions."""
    out: set[str] = set()
    if not types:
        return out
    for raw in types:
        for piece in raw.split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if key not in TYPE_PRESETS:
                typer.echo(
                    f"unknown type preset {key!r}; known presets: {', '.join(sorted(TYPE_PRESETS))}",
                    err=True,
                )
                raise typer.Exit(2)
            out |= TYPE_PRESETS[key]
    return out


def matches_any_glob(path: Path, patterns: list[str]) -> bool:
    """Return ``True`` if ``path`` matches any of ``patterns`` (name or full)."""
    name = path.name
    s = str(path)
    return any(fnmatch.fnmatch(s, p) or fnmatch.fnmatch(name, p) for p in patterns)


def _open_output(path: Optional[str]) -> tuple[TextIO, bool]:
    """Open an output stream (stdout, or a file). Returns ``(fh, should_close)``."""
    if not path:
        return sys.stdout, False
    return open(path, "w", encoding="utf-8", newline=""), True


def _should_color(color_mode: str, is_stdout: bool) -> bool:
    """Decide whether coloured output should be emitted."""
    if color_mode == "never":
        return False
    if color_mode == "always":
        return True
    if not is_stdout:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


# ---------------------------------------------------------------------------
# Ignore-file matcher
# ---------------------------------------------------------------------------


class IgnoreMatcher:
    """Loads ``.gitignore``/``.ignore`` rules per directory and tests paths."""

    def __init__(self) -> None:
        self._cache: dict[str, list[tuple[re.Pattern[str], bool, bool]]] = {}

    def _load(self, d: Path) -> list[tuple[re.Pattern[str], bool, bool]]:
        """Return compiled ``(regex, negated, dir_only)`` rules for directory ``d``."""
        key = str(d)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        rules: list[tuple[re.Pattern[str], bool, bool]] = []
        for fname in (".gitignore", ".ignore"):
            f = d / fname
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for raw in text.splitlines():
                line = raw.rstrip()
                if not line or line.lstrip().startswith("#"):
                    continue
                if line.startswith(("\\#", "\\!")):
                    line = line[1:]
                negated = line.startswith("!")
                if negated:
                    line = line[1:]
                dir_only = line.endswith("/")
                if dir_only:
                    line = line[:-1]
                anchored = line.startswith("/")
                if anchored:
                    line = line[1:]
                if not line:
                    continue
                try:
                    rx = re.compile(self._glob_to_regex(line, anchored))
                except re.error:
                    continue
                rules.append((rx, negated, dir_only))

        self._cache[key] = rules
        return rules

    @staticmethod
    def _glob_to_regex(pattern: str, anchored: bool) -> str:
        """Translate a gitignore-style glob into a regex string."""
        out: list[str] = [r"^" if anchored else r"(?:^|.*/)"]
        i, n = 0, len(pattern)
        while i < n:
            c = pattern[i]
            if c == "*":
                if i + 1 < n and pattern[i + 1] == "*":
                    i += 2
                    if i < n and pattern[i] == "/":
                        i += 1
                        out.append(r"(?:.*/)?")
                    else:
                        out.append(r".*")
                else:
                    out.append(r"[^/]*")
                    i += 1
            elif c == "?":
                out.append(r"[^/]")
                i += 1
            elif c == "[":
                j = i + 1
                if j < n and pattern[j] in "!^":
                    j += 1
                if j < n and pattern[j] == "]":
                    j += 1
                while j < n and pattern[j] != "]":
                    j += 1
                if j >= n:
                    out.append(re.escape(c))
                    i += 1
                else:
                    cls = pattern[i + 1 : j]
                    if cls.startswith("!"):
                        cls = "^" + cls[1:]
                    out.append("[" + cls + "]")
                    i = j + 1
            else:
                out.append(re.escape(c))
                i += 1
        out.append(r"(?:/.*)?$")
        return "".join(out)

    def is_ignored(self, path: Path, root: Path) -> bool:
        """Return ``True`` if ``path`` is ignored according to any loaded rules."""
        try:
            rel = path.relative_to(root)
        except ValueError:
            return False
        parts = rel.parts
        for i in range(1, len(parts) + 1):
            is_dir = i < len(parts)
            ignored = False
            for j in range(i):
                d = root if j == 0 else root / Path(*parts[:j])
                rel_from_d = Path(*parts[j:i]).as_posix()
                for rx, neg, dir_only in self._load(d):
                    if dir_only and not is_dir:
                        continue
                    if rx.search(rel_from_d):
                        ignored = not neg
            if ignored:
                return True
        return False


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

OutputLine = tuple[int, str, bool, list[tuple[int, int]]]


@dataclass(slots=True)
class FileResult:
    """Result of processing one file."""

    path: str
    groups: list[list[OutputLine]] = field(default_factory=list)
    match_count: int = 0
    error: Optional[str] = None


@dataclass(slots=True)
class RenderOptions:
    """Options controlling how a :class:`FileResult` is printed."""

    show_path_only: bool = False
    show_count: bool = False
    as_json: bool = False
    show_line_number: bool = True
    color: bool = True
    heading: bool = True


# ---------------------------------------------------------------------------
# Directory walker
# ---------------------------------------------------------------------------


def _walk_dir(
    root: Path,
    *,
    max_depth: Optional[int],
    follow_symlinks: bool,
    exclude_dirs: set[str],
    ignore_matcher: Optional[IgnoreMatcher],
    search_hidden: bool,
) -> Iterator[Path]:
    """Recursively yield files under ``root`` honouring ignore/exclude rules."""
    seen: set[tuple[int, int]] = set()
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for entry in entries:
            try:
                is_symlink = entry.is_symlink()
            except OSError:
                continue
            if is_symlink and not follow_symlinks:
                continue
            name = entry.name
            if not search_hidden and name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                is_file = entry.is_file(follow_symlinks=follow_symlinks)
            except OSError:
                continue
            if is_dir:
                if name in exclude_dirs or name in SKIP_DIRS:
                    continue
                if max_depth is not None and depth + 1 > max_depth:
                    continue
                p = Path(entry.path)
                if ignore_matcher is not None and ignore_matcher.is_ignored(p, root):
                    continue
                if follow_symlinks:
                    try:
                        st = entry.stat(follow_symlinks=True)
                    except OSError:
                        continue
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                stack.append((p, depth + 1))
            elif is_file:
                p = Path(entry.path)
                if ignore_matcher is not None and ignore_matcher.is_ignored(p, root):
                    continue
                yield p


def get_files(
    paths: list[str],
    include_globs: list[str],
    exclude_globs: list[str],
    exclude_dirs: set[str],
    search_hidden: bool,
    max_size: int,
    extensions: set[str],
    max_depth: Optional[int],
    follow_symlinks: bool,
    ignore_matcher: Optional[IgnoreMatcher],
) -> Iterator[Path]:
    """Yield files from ``paths`` filtered by extension, size, and globs."""

    def _post_filter(p: Path) -> bool:
        if extensions and p.suffix.lower() not in extensions:
            return False
        if max_size:
            try:
                if p.stat().st_size > max_size:
                    return False
            except OSError:
                return False
        if include_globs and not matches_any_glob(p, include_globs):
            return False
        return not (exclude_globs and matches_any_glob(p, exclude_globs))

    for p_str in paths:
        p = Path(p_str)
        if p.is_file():
            if _post_filter(p):
                yield p
            continue
        if not p.is_dir():
            continue
        for f in _walk_dir(
            p,
            max_depth=max_depth,
            follow_symlinks=follow_symlinks,
            exclude_dirs=exclude_dirs,
            ignore_matcher=ignore_matcher,
            search_hidden=search_hidden,
        ):
            if _post_filter(f):
                yield f


# ---------------------------------------------------------------------------
# Matching primitives
# ---------------------------------------------------------------------------


def _spans_for_line(
    line: str,
    regex: Optional[re.Pattern[str]],
    fixed: str,
    ignore_case: bool,
) -> list[tuple[int, int]]:
    """Return match spans for a single line."""
    if regex is not None:
        return [(m.start(), m.end()) for m in regex.finditer(line)]
    if not fixed:
        return []
    hay = line.lower() if ignore_case else line
    needle = fixed.lower() if ignore_case else fixed
    spans: list[tuple[int, int]] = []
    start = 0
    L = max(1, len(needle))
    while True:
        idx = hay.find(needle, start)
        if idx == -1:
            break
        spans.append((idx, idx + len(needle)))
        start = idx + L
    return spans


def _apply_replace_line(
    line: str,
    matches: list[re.Match[str]],
    template: str,
) -> tuple[str, list[tuple[int, int]]]:
    """Apply a regex replacement to one line, returning the new text and spans."""
    if not matches:
        return line, []
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    pos = 0
    out_len = 0
    for m in matches:
        prefix = line[pos : m.start()]
        parts.append(prefix)
        out_len += len(prefix)
        try:
            repl = m.expand(template)
        except re.error:
            repl = template
        spans.append((out_len, out_len + len(repl)))
        parts.append(repl)
        out_len += len(repl)
        pos = m.end()
    parts.append(line[pos:])
    return "".join(parts), spans


def _replace_fixed(text: str, needle: str, repl: str, ignore_case: bool) -> str:
    """Replace literal ``needle`` with ``repl`` in ``text``."""
    if not needle:
        return text
    if ignore_case:
        return re.sub(re.escape(needle), lambda _m: repl, text, flags=re.IGNORECASE)
    return text.replace(needle, repl)


def _replace_spans(
    line: str,
    spans: list[tuple[int, int]],
    template: str,
) -> tuple[str, list[tuple[int, int]]]:
    """Replace fixed ``spans`` in ``line`` with ``template``, returning new spans."""
    if not spans:
        return line, []
    parts: list[str] = []
    new_spans: list[tuple[int, int]] = []
    pos = 0
    out_len = 0
    for s, e in sorted(spans):
        prefix = line[pos:s]
        parts.append(prefix)
        out_len += len(prefix)
        parts.append(template)
        new_spans.append((out_len, out_len + len(template)))
        out_len += len(template)
        pos = e
    parts.append(line[pos:])
    return "".join(parts), new_spans


def _build_groups(
    lines: list[str],
    match_lines: dict[int, tuple[str, list[tuple[int, int]]]],
    context_before: int,
    context_after: int,
) -> list[list[OutputLine]]:
    """Group matching lines together with surrounding context."""
    context_set: set[int] = set()
    for lineno in match_lines:
        start = max(1, lineno - context_before)
        end = min(len(lines), lineno + context_after)
        for c in range(start, end + 1):
            context_set.add(c)
    groups: list[list[OutputLine]] = []
    current: list[OutputLine] = []
    prev: Optional[int] = None
    for c in sorted(context_set):
        if prev is not None and c != prev + 1:
            groups.append(current)
            current = []
        if c in match_lines:
            text, spans = match_lines[c]
            current.append((c, text, True, spans))
        else:
            current.append((c, lines[c - 1], False, []))
        prev = c
    if current:
        groups.append(current)
    return groups


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------


def _process_lines(
    rel_path: str,
    lines: list[str],
    regex: Optional[re.Pattern[str]],
    fixed: str,
    ignore_case: bool,
    invert: bool,
    context_before: int,
    context_after: int,
    max_count: int,
    replace: Optional[str],
    only_matching: bool,
) -> FileResult:
    """Match line-by-line, honouring inversion, replacement, and context."""
    hits: list[tuple[int, list[tuple[int, int]]]] = []
    for idx, line in enumerate(lines):
        spans = _spans_for_line(line, regex, fixed, ignore_case)
        matched = bool(spans) != invert
        if not matched:
            continue
        if invert:
            spans = []
        hits.append((idx + 1, spans))
        if max_count and len(hits) >= max_count:
            break

    if not hits:
        return FileResult(path=rel_path)

    match_count = len(hits)

    if only_matching:
        current: list[OutputLine] = []
        for lineno, spans in hits:
            line = lines[lineno - 1]
            if not spans:
                current.append((lineno, line, True, []))
                continue
            for s, e in spans:
                piece = line[s:e]
                if replace is not None and regex is not None:
                    m = next(
                        (mm for mm in regex.finditer(line) if mm.start() == s and mm.end() == e),
                        None,
                    )
                    if m is not None:
                        try:
                            piece = m.expand(replace)
                        except re.error:
                            piece = replace
                elif replace is not None and regex is None:
                    piece = replace
                current.append((lineno, piece, True, [(0, len(piece))]))
        return FileResult(
            path=rel_path,
            groups=[current] if current else [],
            match_count=match_count,
        )

    match_lines: dict[int, tuple[str, list[tuple[int, int]]]] = {}
    for lineno, spans in hits:
        text = lines[lineno - 1]
        if replace is not None and regex is not None:
            matches = list(regex.finditer(text))
            new_text, new_spans = _apply_replace_line(text, matches, replace)
            match_lines[lineno] = (new_text, new_spans)
        elif replace is not None and regex is None:
            new_text = _replace_fixed(text, fixed, replace, ignore_case)
            match_lines[lineno] = (new_text, [])
        else:
            match_lines[lineno] = (text, spans)

    groups = _build_groups(lines, match_lines, context_before, context_after)
    return FileResult(path=rel_path, groups=groups, match_count=match_count)


def _process_multiline(
    rel_path: str,
    text: str,
    regex: re.Pattern[str],
    context_before: int,
    context_after: int,
    max_count: int,
    replace: Optional[str],
) -> FileResult:
    """Match across lines in DOTALL mode, mapping matches back to line spans."""
    lines = text.splitlines()
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    def to_line_idx(offset: int) -> int:
        return max(0, bisect.bisect_right(line_starts, offset) - 1)

    hits: dict[int, list[tuple[int, int]]] = {}
    order: list[int] = []
    for m in regex.finditer(text):
        if m.start() == m.end():
            continue
        ls = to_line_idx(m.start())
        le = to_line_idx(max(m.start(), m.end() - 1))
        for li in range(ls, le + 1):
            line_start = line_starts[li]
            line_text = lines[li] if li < len(lines) else ""
            span_start = max(0, m.start() - line_start)
            span_end = min(len(line_text), m.end() - line_start)
            if li not in hits:
                hits[li] = []
                order.append(li)
            if span_start < span_end:
                hits[li].append((span_start, span_end))
            else:
                hits[li].append((0, len(line_text)))
        if max_count and len(order) >= max_count:
            break

    if not order:
        return FileResult(path=rel_path)

    match_lines: dict[int, tuple[str, list[tuple[int, int]]]] = {}
    for li in order:
        line_text = lines[li]
        spans = hits[li]
        if replace is not None:
            new_text, new_spans = _replace_spans(line_text, spans, replace)
            match_lines[li + 1] = (new_text, new_spans)
        else:
            match_lines[li + 1] = (line_text, spans)

    groups = _build_groups(lines, match_lines, context_before, context_after)
    return FileResult(path=rel_path, groups=groups, match_count=len(order))


def worker(job: dict[str, Any]) -> FileResult:
    """Process a single file described by ``job`` and return the result."""
    path_str: str = job["path"]
    cwd_str: str = job["cwd"]
    regex_pattern: Optional[str] = job["regex"]
    fixed: str = job["fixed"]
    ignore_case: bool = job["ignore_case"]
    invert: bool = job["invert"]
    before: int = job["before"]
    after: int = job["after"]
    max_count: int = job["max_count"]
    multiline: bool = job["multiline"]
    encoding: str = job["encoding"]
    replace: Optional[str] = job["replace"]
    only_matching: bool = job["only_matching"]

    path = Path(path_str)
    cwd = Path(cwd_str)
    try:
        rel_path = str(path.relative_to(cwd))
    except ValueError:
        rel_path = path_str

    compiled: Optional[re.Pattern[str]] = None
    if regex_pattern is not None:
        flags = re.MULTILINE
        if ignore_case:
            flags |= re.IGNORECASE
        if multiline:
            flags |= re.DOTALL
        try:
            compiled = re.compile(regex_pattern, flags)
        except re.error as ex:
            return FileResult(path=rel_path, error=f"invalid regex: {ex}")

    try:
        if is_binary(path):
            return FileResult(path=rel_path)
    except Exception:
        pass

    try:
        with path.open(encoding=encoding, errors="replace", newline="") as fh:
            text = fh.read()
    except OSError as ex:
        return FileResult(path=rel_path, error=ex.strerror or str(ex))
    except Exception as ex:
        return FileResult(path=rel_path, error=f"{type(ex).__name__}: {ex}")

    try:
        if multiline and compiled is not None and not invert:
            return _process_multiline(rel_path, text, compiled, before, after, max_count, replace)
        return _process_lines(
            rel_path,
            text.splitlines(),
            compiled,
            fixed,
            ignore_case,
            invert,
            before,
            after,
            max_count,
            replace,
            only_matching,
        )
    except re.error as ex:
        return FileResult(path=rel_path, error=f"regex error: {ex}")
    except Exception as ex:
        return FileResult(path=rel_path, error=f"{type(ex).__name__}: {ex}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _colorize_line(line: str, spans: list[tuple[int, int]]) -> str:
    """Return ``line`` with ``spans`` wrapped in the highlight colour."""
    if not spans:
        return line
    chars = list(line)
    for s, e in sorted(spans, key=lambda x: x[0], reverse=True):
        chars.insert(e, ANSI_RESET)
        chars.insert(s, ANSI_HIGHLIGHT)
    return "".join(chars)


def emit_result(result: FileResult, opts: RenderOptions, out_fh: TextIO) -> None:
    """Print a :class:`FileResult` in the configured mode."""
    reset = ANSI_RESET if opts.color else ""
    path_col = ANSI_FILENAME if opts.color else ""

    if opts.show_path_only:
        print(f"{path_col}{result.path}{reset}", file=out_fh)
        return
    if opts.show_count:
        print(f"{result.path}:{result.match_count}", file=out_fh)
        return
    if opts.as_json:
        for group in result.groups:
            for lineno, text, is_match, spans in group:
                if not is_match:
                    continue
                col = spans[0][0] + 1 if spans else 1
                print(
                    json.dumps(
                        {"path": result.path, "line": lineno, "col": col, "text": text},
                        ensure_ascii=False,
                    ),
                    file=out_fh,
                )
        return

    if opts.heading:
        print(f"{path_col}{result.path}{reset}", file=out_fh)
        indent = "  "
    else:
        indent = ""

    first_group = True
    for group in result.groups:
        if not first_group:
            print("--", file=out_fh)
        first_group = False
        for lineno, text, is_match, spans in group:
            body = _colorize_line(text, spans) if (opts.color and spans) else text
            sep = ":" if is_match else "-"
            if opts.heading:
                prefix = indent
            else:
                prefix = f"{path_col}{result.path}{reset}{sep}"
            if opts.show_line_number:
                if opts.color:
                    print(f"{prefix}{ANSI_LINE_NO}{lineno}{reset}{sep}{body}", file=out_fh)
                else:
                    print(f"{prefix}{lineno}{sep}{body}", file=out_fh)
            else:
                print(f"{prefix}{body}", file=out_fh)


# ---------------------------------------------------------------------------
# File list mode
# ---------------------------------------------------------------------------


def _collect_files(
    *,
    paths: list[str],
    extensions: list[str],
    types: list[str],
    globs: list[str],
    excludes: list[str],
    exclude_dirs: list[str],
    hidden: bool,
    max_size: int,
    max_depth: Optional[int],
    follow: bool,
    no_ignore: bool,
) -> list[Path]:
    """Resolve CLI filters into a concrete list of files to search."""
    exts = parse_extension_args(extensions) | resolve_type_presets(types)
    matcher = None if no_ignore else IgnoreMatcher()
    return list(
        get_files(
            paths=paths,
            include_globs=globs,
            exclude_globs=excludes,
            exclude_dirs=set(exclude_dirs),
            search_hidden=hidden,
            max_size=max_size,
            extensions=exts,
            max_depth=max_depth,
            follow_symlinks=follow,
            ignore_matcher=matcher,
        )
    )


def _run_files_mode(files: list[Path], out_file: Optional[str], sort: str) -> int:
    """Print the paths that would be searched and exit."""
    if sort == "path":
        files.sort(key=str)
    out_fh, should_close = _open_output(out_file)
    try:
        cwd = Path.cwd()
        for f in files:
            try:
                rel = f.relative_to(cwd)
            except ValueError:
                rel = f
            print(str(rel), file=out_fh)
    finally:
        if should_close:
            out_fh.close()
    return 0


# ---------------------------------------------------------------------------
# Quiet search
# ---------------------------------------------------------------------------


def _search_quiet(
    files: list[Path],
    cwd: Path,
    regex_pattern: Optional[str],
    fixed: str,
    ignore_case: bool,
    invert_match: bool,
    multiline: bool,
    encoding: str,
) -> bool:
    """Return ``True`` if any file contains a match (fast-exit)."""
    jobs = [
        {
            "path": str(p),
            "cwd": str(cwd),
            "regex": regex_pattern,
            "fixed": fixed,
            "ignore_case": ignore_case,
            "invert": invert_match,
            "before": 0,
            "after": 0,
            "max_count": 1,
            "multiline": multiline and not invert_match,
            "encoding": encoding,
            "replace": None,
            "only_matching": False,
        }
        for p in files
    ]
    if MAX_WORKERS <= 1:
        return any(worker(j).match_count for j in jobs)
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(worker, j) for j in jobs]
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception:
                continue
            if r.match_count:
                for f in futures:
                    f.cancel()
                return True
    return False


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


def _pyrg_command(
    pattern: Optional[str] = typer.Argument(None, help="Regex pattern (or use -r/--regexp)"),
    paths: Optional[list[str]] = typer.Argument(None, help="Files or directories to search (default: .)"),
    regexp: Optional[str] = typer.Option(
        None, "-r", "--regexp", help="Pattern (alternative to the positional argument)"
    ),
    extensions: list[str] = typer.Option(
        [],
        "-e",
        "--extension",
        help="Only search files with the given extension(s) (dotless, dotted or comma-separated; repeatable)",
    ),
    types: list[str] = typer.Option(
        [],
        "-t",
        "--type",
        help=f"Only search files matching a curated type preset. Known: {', '.join(sorted(TYPE_PRESETS))}",
    ),
    ignore_case: bool = typer.Option(False, "-i", "--ignore-case", help="Case-insensitive matching"),
    fixed_strings: bool = typer.Option(False, "-F", "--fixed-strings", help="Treat PATTERN as a literal string"),
    invert_match: bool = typer.Option(False, "-v", "--invert-match", help="Show non-matching lines"),
    multiline: bool = typer.Option(False, "--multiline", help="Allow the pattern to span multiple lines (DOTALL)"),
    only_matching: bool = typer.Option(False, "-o", "--only-matching", help="Print only the matched parts of lines"),
    replace: Optional[str] = typer.Option(
        None, "--replace", help="Replace matches with REPL in output (\\1 backrefs). Does NOT modify files."
    ),
    line_number_flag: bool = typer.Option(True, "-n", "--line-number", help="Show line numbers (default)"),
    no_line_number: bool = typer.Option(False, "--no-line-number", help="Hide line numbers"),
    files_with_matches: bool = typer.Option(False, "-l", "--files-with-matches", help="Print only paths with matches"),
    files_without_match: bool = typer.Option(False, "--files-without-match", help="Print only paths with no matches"),
    count: bool = typer.Option(False, "-c", "--count", help="Print match count per file"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress output; exit status indicates match"),
    json_out: bool = typer.Option(False, "--json", help="Emit one JSON object per match"),
    heading_flag: bool = typer.Option(False, "--heading", help="Force headings above each file's matches"),
    no_heading: bool = typer.Option(False, "--no-heading", help="Suppress headings"),
    sort: str = typer.Option("none", "--sort", help="Sort results: 'path' or 'none' (default)"),
    color: str = typer.Option("auto", "--color", help="Colour output: auto (default), always, never"),
    no_color: bool = typer.Option(False, "--no-color", help="Alias for --color never"),
    out_file: Optional[str] = typer.Option(None, "--out-file", help="Write matches to FILE instead of stdout"),
    after_context: int = typer.Option(0, "-A", "--after-context", help="Show N lines after each match"),
    before_context: int = typer.Option(0, "-B", "--before-context", help="Show N lines before each match"),
    context: Optional[int] = typer.Option(None, "-C", "--context", help="Shorthand for -A N -B N"),
    hidden: bool = typer.Option(False, "--hidden", help="Search hidden files/dirs"),
    glob_patterns: list[str] = typer.Option([], "-g", "--glob", help="Only search paths matching GLOB (repeatable)"),
    exclude: list[str] = typer.Option([], "-x", "--exclude", help="Exclude paths matching GLOB (repeatable)"),
    exclude_dirs: list[str] = typer.Option([], "--exclude-dir", help="Exclude directories named NAME (repeatable)"),
    max_depth: Optional[int] = typer.Option(None, "--max-depth", help="Maximum directory depth"),
    max_filesize: int = typer.Option(10_000_000, "--max-filesize", help="Skip files larger than BYTES"),
    max_count: int = typer.Option(0, "-M", "--max-count", help="Stop after N matches per file"),
    no_ignore: bool = typer.Option(False, "--no-ignore", help="Do not honour .gitignore / .ignore files"),
    follow: bool = typer.Option(False, "-L", "--follow", help="Follow symlinks (with cycle detection)"),
    encoding: str = typer.Option("utf-8", "--encoding", help="Text encoding to use"),
    list_files: bool = typer.Option(False, "--files", help="List files that would be searched, then exit"),
    stats: bool = typer.Option(False, "--stats", help="Print a summary to stderr at the end"),
    no_messages: bool = typer.Option(False, "--no-messages", help="Suppress per-file error reporting"),
) -> None:
    """pyrg — a ripgrep-like recursive search in Python."""
    if sort not in ("path", "none"):
        typer.echo(f"invalid --sort value: {sort}; expected 'path' or 'none'", err=True)
        raise typer.Exit(2)

    effective_color = "never" if no_color else color
    if effective_color not in ("auto", "always", "never"):
        typer.echo(
            f"invalid --color value: {effective_color}; expected auto, always, never",
            err=True,
        )
        raise typer.Exit(2)

    if heading_flag and no_heading:
        typer.echo("--heading and --no-heading are mutually exclusive", err=True)
        raise typer.Exit(2)

    if context is not None:
        after_context = context
        before_context = context

    line_number = not no_line_number
    heading: Optional[bool] = True if heading_flag else (False if no_heading else None)

    cwd = Path.cwd()
    effective_paths: list[str] = list(paths) if paths else ["."]

    # --files mode ---------------------------------------------------------
    if list_files:
        files = _collect_files(
            paths=effective_paths,
            extensions=extensions,
            types=types,
            globs=glob_patterns,
            excludes=exclude,
            exclude_dirs=exclude_dirs,
            hidden=hidden,
            max_size=max_filesize,
            max_depth=max_depth,
            follow=follow,
            no_ignore=no_ignore,
        )
        raise typer.Exit(_run_files_mode(files, out_file, sort))

    # Pattern selection and validation ------------------------------------
    pat = regexp or pattern
    if not pat:
        typer.echo(
            "No pattern provided. Use positional PATTERN or -r/--regexp PATTERN.",
            err=True,
        )
        raise typer.Exit(2)

    if fixed_strings:
        regex_pattern: Optional[str] = None
        fixed = pat
    else:
        regex_pattern = pat
        fixed = ""
        flags = re.MULTILINE
        if ignore_case:
            flags |= re.IGNORECASE
        if multiline:
            flags |= re.DOTALL
        try:
            re.compile(pat, flags)
        except re.error as ex:
            typer.echo(f"Invalid regex: {ex}", err=True)
            raise typer.Exit(2)

    # File discovery ------------------------------------------------------
    files = _collect_files(
        paths=effective_paths,
        extensions=extensions,
        types=types,
        globs=glob_patterns,
        excludes=exclude,
        exclude_dirs=exclude_dirs,
        hidden=hidden,
        max_size=max_filesize,
        max_depth=max_depth,
        follow=follow,
        no_ignore=no_ignore,
    )
    if sort == "path":
        files.sort(key=str)
    if not files:
        if not no_messages:
            typer.echo("pyrg: no files matched the search filters", err=True)
        raise typer.Exit(1)

    # Output setup --------------------------------------------------------
    out_fh, should_close = _open_output(out_file)
    use_color = _should_color(effective_color, out_fh is sys.stdout) and not json_out
    effective_heading = heading if heading is not None else use_color
    render = RenderOptions(
        show_path_only=files_with_matches,
        show_count=count,
        as_json=json_out,
        show_line_number=line_number,
        color=use_color,
        heading=effective_heading,
    )

    # Quiet mode fast path ------------------------------------------------
    if quiet:
        try:
            any_match = _search_quiet(
                files,
                cwd,
                regex_pattern,
                fixed,
                ignore_case,
                invert_match,
                multiline,
                encoding,
            )
        finally:
            if should_close:
                out_fh.close()
        raise typer.Exit(0 if any_match else 1)

    # Normal search -------------------------------------------------------
    t0 = time.monotonic()
    jobs: list[dict[str, Any]] = [
        {
            "path": str(p),
            "cwd": str(cwd),
            "regex": regex_pattern,
            "fixed": fixed,
            "ignore_case": ignore_case,
            "invert": invert_match,
            "before": before_context,
            "after": after_context,
            "max_count": max_count,
            "multiline": multiline and not invert_match,
            "encoding": encoding,
            "replace": replace,
            "only_matching": only_matching,
        }
        for p in files
    ]

    files_searched = 0
    files_matched = 0
    files_errored = 0
    total_matches = 0
    any_match = False

    def _consume(results: Iterator[FileResult]) -> None:
        nonlocal files_searched, files_matched, files_errored, total_matches, any_match
        for result in results:
            if result.error:
                files_errored += 1
                if not no_messages:
                    print(f"{result.path}: {result.error}", file=sys.stderr)
                continue
            files_searched += 1
            if result.match_count:
                any_match = True
                files_matched += 1
                total_matches += result.match_count
                emit_result(result, render, out_fh)
            elif files_without_match:
                reset = ANSI_RESET if use_color else ""
                pcol = ANSI_FILENAME if use_color else ""
                print(f"{pcol}{result.path}{reset}", file=out_fh)

    try:
        if MAX_WORKERS <= 1:
            _consume(worker(j) for j in jobs)
        else:
            with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futures = [ex.submit(worker, j) for j in jobs]

                def _gen() -> Iterator[FileResult]:
                    for fut in as_completed(futures):
                        try:
                            yield fut.result()
                        except Exception as exc:
                            yield FileResult(path="<worker>", error=str(exc))

                _consume(_gen())
    except KeyboardInterrupt:
        typer.echo("\nSearch cancelled.", err=True)
        if should_close:
            out_fh.close()
        raise typer.Exit(130)
    finally:
        if should_close and not out_fh.closed:
            out_fh.close()
            if out_file:
                typer.echo(f"Results written to: {out_file}", err=True)

    # Summary -------------------------------------------------------------
    elapsed = time.monotonic() - t0
    if stats:
        parts = [
            f"{files_searched} files searched",
            f"{files_matched} matched",
            f"{total_matches} matches",
            f"{elapsed:.2f}s",
        ]
        if files_errored:
            parts.append(f"{files_errored} errors")
        typer.echo(", ".join(parts), err=True)

    raise typer.Exit(0 if any_match else 1)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _run(argv: Optional[list[str]] = None) -> int:
    """Programmatic entry point that returns the process exit code."""
    from typer.testing import CliRunner

    _app = typer.Typer(
        add_completion=False,
        no_args_is_help=False,
        pretty_exceptions_enable=False,
    )
    _app.command()(_pyrg_command)
    args = list(argv) if argv is not None else sys.argv[1:]
    return CliRunner().invoke(_app, args).exit_code


if __name__ == "__main__":
    typer.run(_pyrg_command)
