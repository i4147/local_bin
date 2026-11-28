#!/data/data/com.termux/files/usr/bin/env python
"""pyrg — a ripgrep-like recursive regex search tool.

Recursively search files for a regular expression (or fixed string) with
support for case folding, inversion, multiline patterns, context lines,
only-matching output, in-place replacement, glob/extension/type filtering,
gitignore-style ignore files, JSON output, counting, quiet mode, and parallel
processing.

The command-line interface is built on :mod:`typer`; file discovery uses
:mod:`pathlib` (with :func:`os.scandir` for speed); diagnostics use
:mod:`loguru` when available, falling back to :mod:`logging`.

Examples::

    $ pyrg 'def main' -t py -i
    $ pyrg 'TODO' --replace 'DONE' -W --dry-run -t py
    $ pyrg -l -t md 'FIXME'
"""

from __future__ import annotations
import argparse
import bisect
from collections import deque
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace as dc_replace
import fnmatch
import functools
import hashlib
import json
import os
from pathlib import Path
import re
from re import Match, Pattern
import shutil
import sys
import tempfile
import time
from typing import Any, TextIO

from dh import atomic_write
from loguru import logger
# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

__version__: str = "0.3.0"

#: Default worker count for parallel searches.
MAX_WORKERS: int = min(64, max(1, (os.cpu_count() or 2)) + 4)

#: Directory names always skipped during recursion.
SKIP_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".bzr",
    "__pycache__",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
})

_LOG_PATH_DEFAULT: Path = Path.home() / "tmp" / "apps" / "pyrg.log"

ANSI_RESET: str = "\x1b[0m"
ANSI_FILENAME: str = "\x1b[30;43m"  # black on yellow
ANSI_HIGHLIGHT: str = "\x1b[30;46m"  # black on cyan
ANSI_LINE_NO: str = "\x1b[32m"  # green

_COMPILE_CACHE_LIMIT: int = 128

#: A compiled regular expression (either engine).
_Pattern = Any

OutputLine = tuple[int, str, bool, list[tuple[int, int]]]

TYPE_PRESETS: dict[str, frozenset[str]] = {
    "py": frozenset({".py", ".pyi", ".pyx", ".pyw"}),
    "md": frozenset({".md", ".markdown", ".mdx"}),
    "json": frozenset({".json", ".jsonl", ".geojson"}),
    "log": frozenset({".log"}),
    "js": frozenset({".js", ".jsx", ".mjs", ".cjs"}),
    "ts": frozenset({".ts", ".tsx"}),
    "rust": frozenset({".rs"}),
    "go": frozenset({".go"}),
    "c": frozenset({".c", ".h"}),
    "cpp": frozenset({".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".hh"}),
    "java": frozenset({".java"}),
    "rb": frozenset({".rb"}),
    "php": frozenset({".php"}),
    "html": frozenset({".html", ".htm"}),
    "css": frozenset({".css", ".scss", ".sass", ".less"}),
    "yaml": frozenset({".yaml", ".yml"}),
    "toml": frozenset({".toml"}),
    "sh": frozenset({".sh", ".bash", ".zsh"}),
    "sql": frozenset({".sql"}),
    "xml": frozenset({".xml"}),
    "csv": frozenset({".csv", ".tsv"}),
}

# Backwards-compatible mutable views (presets can still be extended via --type-add).
TYPE_PRESETS_MUTABLE: dict[str, set[str]] = {k: set(v) for k, v in TYPE_PRESETS.items()}


def _preset(name: str) -> set[str]:
    return TYPE_PRESETS_MUTABLE.setdefault(name, set())


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #


def _setup_logging(log_path_override: str | None = None, level: str = "DEBUG") -> None:
    """Configure loguru: a debug log file plus stderr fallback."""
    logger.remove()
    if log_path_override is not None and log_path_override == "":
        logger.add(
            sys.stderr,
            level=level if level != "DEBUG" else "WARNING",
            format="{message}",
        )
        return
    path = _LOG_PATH_DEFAULT if log_path_override is None else Path(log_path_override)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(str(path), level=level, encoding="utf-8", rotation="10 MB", retention=5)
    except OSError:
        logger.add(sys.stderr, level="WARNING", format="{message}")


_setup_logging()


# --------------------------------------------------------------------------- #
# Extension / type-preset helpers
# --------------------------------------------------------------------------- #


def normalize_extension(value: str) -> str:
    """Normalise a single extension token to ``.ext`` form (dotless input ok)."""
    v = value.strip().lower().rstrip(".")
    if not v:
        return ""
    if not v.startswith("."):
        v = "." + v
    return v


def parse_extension_args(raw_exts: list[str] | None) -> set[str]:
    """Parse repeated/comma-separated extension values into a set of ``.ext``."""
    out: set[str] = set()
    if not raw_exts:
        return out
    for raw in raw_exts:
        for piece in raw.split(","):
            ext = normalize_extension(piece)
            if ext:
                out.add(ext)
    return out


def resolve_type_presets(types: list[str] | None) -> set[str]:
    """Expand ``-t`` type preset names (and comma lists) into extensions."""
    out: set[str] = set()
    if not types:
        return out
    known = ", ".join(sorted(TYPE_PRESETS))
    for raw in types:
        for piece in raw.split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if key not in TYPE_PRESETS:
                print(f"unknown type preset {key!r}; known presets: {known}", file=sys.stderr)
                raise SystemExit(2)
            out |= TYPE_PRESETS[key]
    return out


def _resolve_type_not(types: list[str] | None) -> set[str]:
    out: set[str] = set()
    if not types:
        return out
    for raw in types:
        for piece in raw.split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if key not in TYPE_PRESETS:
                print(f"unknown type preset for --type-not: {key!r}", file=sys.stderr)
                raise SystemExit(2)
            out |= TYPE_PRESETS[key]
    return out


def _apply_type_add(spec: str) -> None:
    if ":" not in spec:
        print(f"invalid --type-add spec {spec!r}; expected NAME:EXT[,EXT...]", file=sys.stderr)
        raise SystemExit(2)
    name, exts = spec.split(":", 1)
    key = name.strip().lower()
    if not key:
        print("invalid --type-add: empty name", file=sys.stderr)
        raise SystemExit(2)
    extset: set[str] = set()
    for piece in exts.split(","):
        e = normalize_extension(piece)
        if e:
            extset.add(e)
    _preset(key).update(extset)


# --------------------------------------------------------------------------- #
# Binary / filesystem helpers
# --------------------------------------------------------------------------- #


def is_binary(path: Path) -> bool:
    """Heuristic: a file is binary if its first bytes contain a NUL byte."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(4096)
    except OSError:
        return False
    return b"\x00" in chunk


# --------------------------------------------------------------------------- #
# Ignore-file matcher (gitignore / .ignore)
# --------------------------------------------------------------------------- #


class IgnoreMatcher:
    """Loads ``.gitignore``/``.ignore`` rules per directory and tests paths."""

    def __init__(self, extra_files: list[Path] | None = None) -> None:
        """Initialize the instance state from the supplied arguments."""
        self._cache: dict[str, list[tuple[Pattern[str], bool, bool]]] = {}
        self._extra: list[tuple[Pattern[str], bool, bool]] = []
        if extra_files:
            for f in extra_files:
                self._extra.extend(self._parse_ignore_file(f))

    def _parse_ignore_file(self, f: Path) -> list[tuple[Pattern[str], bool, bool]]:
        rules: list[tuple[Pattern[str], bool, bool]] = []
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return rules
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
        return rules

    def _load(self, d: Path) -> list[tuple[Pattern[str], bool, bool]]:
        key = str(d)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        rules: list[tuple[Pattern[str], bool, bool]] = []
        for fname in (".gitignore", ".ignore"):
            f = d / fname
            if not f.is_file():
                continue
            rules.extend(self._parse_ignore_file(f))
        self._cache[key] = rules
        return rules

    @staticmethod
    def _glob_to_regex(pattern: str, anchored: bool) -> str:
        """Translate a gitignore-style glob into a regex string."""
        out: list[str] = ["^" if anchored else "(?:^|.*/)"]
        i = 0
        n = len(pattern)
        while i < n:
            c = pattern[i]
            if c == "*":
                if i + 1 < n and pattern[i + 1] == "*":
                    i += 2
                    if i < n and pattern[i] == "/":
                        i += 1
                        out.append("(?:.*/)?")
                    else:
                        out.append(".*")
                else:
                    out.append("[^/]*")
                    i += 1
            elif c == "?":
                out.append("[^/]")
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
        out.append("(?:/.*)?$")
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
            if i == len(parts):
                for rx, neg, dir_only in self._extra:
                    if dir_only and not is_dir:
                        continue
                    if rx.search(rel.as_posix()):
                        ignored = not neg
                if ignored:
                    return True
        return False


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class MatchRecord:
    """Store path, line, column, byte_offset, match and related state."""

    path: str
    line: int
    column: int
    byte_offset: int
    match: str
    replacement: str | None
    line_text: str
    context_before: list[str]
    context_after: list[str]
    is_context: bool


@dataclass(slots=True)
class FileResult:
    """Store path, groups, match_count, error, records and related state."""

    path: str
    groups: list[list[OutputLine]] = field(default_factory=list)
    match_count: int = 0
    error: str | None = None
    records: list[MatchRecord] = field(default_factory=list)
    new_content: bytes | None = None
    old_text: str | None = None
    had_bom: bool = False
    detected_encoding: str = "utf-8"
    line_ending: str = "\n"
    replacement_count: int = 0
    hit_lines: list[int] = field(default_factory=list)


@dataclass(slots=True)
class RenderOptions:
    """Store show_path_only, show_count, as_json, as_json_seq, show_line_number and related state."""

    show_path_only: bool = False
    show_count: bool = False
    as_json: bool = False
    as_json_seq: bool = False
    show_line_number: bool = True
    color: bool = True
    heading: bool = True
    show_filename_override: bool | None = None
    column: bool = False
    byte_offset: bool = False
    null_sep: bool = False
    passthru: bool = False
    max_columns: int = 0
    max_columns_preview: bool = False
    context_separator: str = "--"
    field_match_separator: str = ":"
    field_context_separator: str = "-"
    separator: str = "\n"


@dataclass(slots=True)
class WalkOptions:
    """Store max_depth, follow_symlinks, exclude_dirs, ignore_matcher, search_hidden."""

    max_depth: int | None = None
    follow_symlinks: bool = False
    exclude_dirs: set[str] = field(default_factory=set)
    ignore_matcher: IgnoreMatcher | None = None
    search_hidden: bool = False


@dataclass(slots=True)
class Job:
    """Store path, cwd, compiled, pattern_str, engine and related state."""

    path: str
    cwd: str
    compiled: Pattern[str] | None
    pattern_str: str | None
    engine: str
    flags: int
    fixed: str
    ignore_case: bool
    no_unicode: bool
    invert: bool
    before: int
    after: int
    max_count: int
    multiline: bool
    encoding: str
    replace: str | None
    only_matching: bool
    crlf: bool
    literal_replace: bool
    preserve_case: bool
    case_mode: str | None
    text_mode: bool
    use_mmap: bool
    mmap_threshold: int
    buffer_size: int
    process_lines: bool
    in_place: bool
    record_context: bool
    byte_offset: bool
    pre_cmd: str | None
    pre_glob: str | None


# --------------------------------------------------------------------------- #
# Directory walking
# --------------------------------------------------------------------------- #


def _walk_dir(root: Path, opts: WalkOptions) -> Iterator[Path]:
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
            if is_symlink and not opts.follow_symlinks:
                continue
            name = entry.name
            if not opts.search_hidden and name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=opts.follow_symlinks)
                is_file = entry.is_file(follow_symlinks=opts.follow_symlinks)
            except OSError:
                continue
            if is_dir:
                if name in opts.exclude_dirs or name in SKIP_DIRS:
                    continue
                if opts.max_depth is not None and depth + 1 > opts.max_depth:
                    continue
                p = Path(entry.path)
                if opts.ignore_matcher is not None and opts.ignore_matcher.is_ignored(p, root):
                    continue
                if opts.follow_symlinks:
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
                if opts.ignore_matcher is not None and opts.ignore_matcher.is_ignored(p, root):
                    continue
                yield p


def matches_any_glob(path: Path, patterns: list[str], ignore_case: bool = False) -> bool:
    """Return ``True`` if ``path`` matches any of ``patterns`` (name or full)."""
    name = path.name
    s = str(path)
    if ignore_case:
        s_l = s.lower()
        name_l = name.lower()
        return any(fnmatch.fnmatch(s_l, p.lower()) or fnmatch.fnmatch(name_l, p.lower()) for p in patterns)
    return any(fnmatch.fnmatch(s, p) or fnmatch.fnmatch(name, p) for p in patterns)


def get_files(
    paths: list[str],
    include_globs: list[str],
    exclude_globs: list[str],
    exclude_dirs: set[str],
    search_hidden: bool,
    max_size: int,
    extensions: set[str],
    excluded_extensions: set[str],
    max_depth: int | None,
    follow_symlinks: bool,
    ignore_matcher: IgnoreMatcher | None,
    iglob: list[str] | None = None,
    glob_case_insensitive: bool = False,
) -> Iterator[Path]:
    """Yield files from ``paths`` filtered by extension, size, and globs."""

    def _post_filter(p: Path) -> bool:
        suf = p.suffix.lower()
        if extensions and suf not in extensions:
            return False
        if excluded_extensions and suf in excluded_extensions:
            return False
        if max_size:
            try:
                if p.stat().st_size > max_size:
                    return False
            except OSError:
                return False
        if include_globs and not matches_any_glob(p, include_globs, glob_case_insensitive):
            return False
        if iglob and not matches_any_glob(p, iglob, True):
            return False
        return not (exclude_globs and matches_any_glob(p, exclude_globs, glob_case_insensitive))

    for p_str in paths:
        p = Path(p_str)
        if p.is_file():
            if _post_filter(p):
                yield p
            continue
        if not p.is_dir():
            continue
        walk_opts = WalkOptions(
            max_depth=max_depth,
            follow_symlinks=follow_symlinks,
            exclude_dirs=exclude_dirs,
            ignore_matcher=ignore_matcher,
            search_hidden=search_hidden,
        )
        for f in _walk_dir(p, walk_opts):
            if _post_filter(f):
                yield f


def _git_tracked_paths(cwd: Path) -> set[Path] | None:
    try:
        proc = subprocess_run(["git", "ls-files", "-z"], cwd=str(cwd))
    except (OSError, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    tracked: set[Path] = set()
    for chunk in proc.stdout.split(b"\x00"):
        if not chunk:
            continue
        rel = chunk.decode("utf-8", errors="replace")
        tracked.add((cwd / rel).resolve())
    return tracked


def _collect_files(
    *,
    paths: list[str],
    extensions: list[str] | None,
    types: list[str] | None,
    type_not: list[str] | None,
    globs: list[str] | None,
    iglob: list[str] | None,
    glob_case_insensitive: bool,
    excludes: list[str] | None,
    exclude_dirs: list[str] | None,
    hidden: bool,
    max_size: int,
    max_depth: int | None,
    follow: bool,
    no_ignore: bool,
    ignore_files: list[str] | None = None,
    files_from: str | None = None,
    require_git: bool = False,
) -> list[Path]:
    exts = parse_extension_args(extensions) | resolve_type_presets(types)
    excluded_exts = _resolve_type_not(type_not)
    extra_ignore_paths: list[Path] = []
    if ignore_files:
        for p in ignore_files:
            fp = Path(p)
            if not fp.is_file():
                print(f"error: --ignore-file {p!r} not found", file=sys.stderr)
                raise SystemExit(3)
            extra_ignore_paths.append(fp)
    matcher: IgnoreMatcher | None = None
    if not no_ignore or extra_ignore_paths:
        matcher = IgnoreMatcher(extra_ignore_paths if extra_ignore_paths else None)
    if files_from is not None:
        listed = _read_list_source(files_from)
        search_paths = [p for p in listed if p.strip()]
    else:
        search_paths = paths
    candidates = list(
        get_files(
            paths=search_paths,
            include_globs=list(globs or []),
            exclude_globs=list(excludes or []),
            exclude_dirs=set(exclude_dirs or []),
            search_hidden=hidden,
            max_size=max_size,
            extensions=exts,
            excluded_extensions=excluded_exts,
            max_depth=max_depth,
            follow_symlinks=follow,
            ignore_matcher=matcher,
            iglob=list(iglob or []),
            glob_case_insensitive=glob_case_insensitive,
        )
    )
    if require_git:
        tracked = _git_tracked_paths(Path.cwd())
        if tracked is None:
            print("error: --require-git requires a git repo with git installed", file=sys.stderr)
            raise SystemExit(3)
        candidates = [f for f in candidates if f.resolve() in tracked]
    return candidates


# --------------------------------------------------------------------------- #
# Config / env
# --------------------------------------------------------------------------- #


def _read_list_source(path_str: str) -> list[str]:
    if path_str == "-":
        return [line.rstrip("\n") for line in sys.stdin]
    try:
        with open(path_str, "r", encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\n") for line in fh]
    except OSError as ex:
        print(f"error: cannot read {path_str!r}: {ex}", file=sys.stderr)
        raise SystemExit(3)


def _read_pattern_file(path_str: str) -> list[str]:
    raw = _read_list_source(path_str)
    pats: list[str] = []
    for line in raw:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pats.append(stripped)
    return pats


def _read_replace_from(path_str: str) -> str:
    raw = _read_list_source(path_str)
    return "\n".join(raw)


def _open_output(path: str | None) -> tuple[TextIO, bool]:
    if not path:
        return sys.stdout, False
    return open(path, "w", encoding="utf-8", newline=""), True


def _should_color(color_mode: str, is_stdout: bool) -> bool:
    if color_mode == "never":
        return False
    if color_mode == "always":
        return True
    if not is_stdout:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _load_config(path: str | None, no_config: bool) -> dict[str, Any]:
    if no_config:
        return {}
    target: Path | None = None
    if path:
        target = Path(path)
    else:
        cur = Path.cwd()
        while True:
            for cand in (".pyrgrc", ".pyrgrc.toml", ".pyrgrc.json"):
                p = cur / cand
                if p.is_file():
                    target = p
                    break
            if target is not None:
                break
            parent = cur.parent
            if parent == cur:
                break
            cur = parent
    if target is None or not target.is_file():
        return {}
    try:
        raw = target.read_bytes()
    except OSError as ex:
        logger.warning(f"could not read config {target}: {ex}")
        return {}
    if target.suffix == ".json" or (target.suffix == "" and raw.lstrip()[:1] == b"{"):
        try:
            return json.loads(raw)
        except Exception as ex:
            logger.warning(f"invalid JSON config {target}: {ex}")
            return {}
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            tomllib = None  # type: ignore[assignment]
    if tomllib is None:
        logger.warning(f"config {target} requires tomllib (Python 3.11+)")
        return {}
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except Exception as ex:
        logger.warning(f"invalid TOML config {target}: {ex}")
        return {}


def _env_str(name: str) -> str | None:
    v = os.environ.get(name)
    if v is None or v == "":
        return None
    return v


def _env_int(name: str) -> int | None:
    v = _env_str(name)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Subprocess helper (centralised so it can be monkeypatched in tests)
# --------------------------------------------------------------------------- #


def subprocess_run(cmd: list[str], **kwargs: Any) -> Any:
    import subprocess

    return subprocess.run(cmd, capture_output=True, check=False, **kwargs)


# --------------------------------------------------------------------------- #
# Pattern building
# --------------------------------------------------------------------------- #


@functools.lru_cache(maxsize=_COMPILE_CACHE_LIMIT)
def _compile_cached(pat: str, flags: int, engine: str) -> _Pattern:
    try:
        import regex as _regex_mod  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        _regex_mod = None
    if engine == "regex":
        if _regex_mod is None:
            raise RuntimeError("regex module not available")
        return _regex_mod.compile(pat, flags)
    return re.compile(pat, flags)


def _resolve_engine(engine: str) -> str:
    if engine == "regex":
        try:
            import regex as _regex_mod  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover
            _regex_mod = None
        if _regex_mod is None:
            print("error: --engine regex requires the 'regex' module (pip install regex)", file=sys.stderr)
            raise SystemExit(3)
        return "regex"
    return "re"


def _engine_flags(engine: str, ignore_case: bool, multiline_dotall: bool, no_unicode: bool) -> int:
    try:
        import regex as _regex_mod  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        _regex_mod = None
    if engine == "regex" and _regex_mod is not None:
        flags = _regex_mod.MULTILINE
        if ignore_case:
            flags |= _regex_mod.IGNORECASE
        if multiline_dotall:
            flags |= _regex_mod.DOTALL
        if no_unicode:
            flags |= _regex_mod.ASCII
        return flags
    flags = re.MULTILINE
    if ignore_case:
        flags |= re.IGNORECASE
    if multiline_dotall:
        flags |= re.DOTALL
    if no_unicode:
        flags |= re.ASCII
    return flags


def _bom_and_encoding(raw_head: bytes, requested_encoding: str) -> tuple[bool, str, str]:
    if raw_head.startswith(b"\xef\xbb\xbf"):
        return True, "utf-8-sig", "\ufeff"
    if raw_head.startswith(b"\xff\xfe"):
        return True, "utf-16-le", "\ufeff"
    if raw_head.startswith(b"\xfe\xff"):
        return True, "utf-16-be", "\ufeff"
    return False, requested_encoding, ""


def _detect_line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _spans_for_line(
    line: str,
    compiled: _Pattern | None,
    fixed: str,
    ignore_case: bool,
    no_unicode: bool = False,
) -> list[tuple[int, int]]:
    """Return match spans for a single line."""
    if compiled is not None:
        return [(m.start(), m.end()) for m in compiled.finditer(line)]
    if not fixed:
        return []
    hay = line.lower() if ignore_case else line
    needle = fixed.lower() if ignore_case else fixed
    if no_unicode:
        hay = hay.encode("ascii", errors="ignore").decode("ascii", errors="ignore")
        needle = needle.encode("ascii", errors="ignore").decode("ascii", errors="ignore")
    spans: list[tuple[int, int]] = []
    start = 0
    step = max(1, len(needle))
    while True:
        idx = hay.find(needle, start)
        if idx == -1:
            break
        spans.append((idx, idx + len(needle)))
        start = idx + step
    return spans


def _apply_replace_line(
    line: str,
    compiled: _Pattern,
    template: str,
    literal: bool,
) -> tuple[str, list[tuple[int, int]]]:
    matches = list(compiled.finditer(line))
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
        if literal:
            repl = template
        else:
            try:
                repl = m.expand(template)
            except (re.error, Exception):
                repl = template
        spans.append((out_len, out_len + len(repl)))
        parts.append(repl)
        out_len += len(repl)
        pos = m.end()
    parts.append(line[pos:])
    return "".join(parts), spans


def _replace_fixed(text: str, needle: str, repl: str, ignore_case: bool) -> str:
    if not needle:
        return text
    if ignore_case:
        return re.sub(re.escape(needle), lambda _m: repl, text, flags=re.IGNORECASE)
    return text.replace(needle, repl)


def _build_groups(
    lines: list[str],
    match_lines: dict[int, tuple[str, list[tuple[int, int]]]],
    context_before: int,
    context_after: int,
) -> list[list[OutputLine]]:
    context_set: set[int] = set()
    for lineno in match_lines:
        start = max(1, lineno - context_before)
        end = min(len(lines), lineno + context_after)
        for c in range(start, end + 1):
            context_set.add(c)
    groups: list[list[OutputLine]] = []
    current: list[OutputLine] = []
    prev: int | None = None
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


def _case_transform(text: str, mode: str) -> str:
    if mode == "upper":
        return text.upper()
    if mode == "lower":
        return text.lower()
    if mode == "capitalize":
        return text.capitalize()
    return text


def _preserve_case(replacement: str, original: str) -> str:
    if not original:
        return replacement
    if original.isupper():
        return replacement.upper()
    if original.islower():
        return replacement.lower()
    if len(original) > 1 and original[0].isupper() and original[1:].islower():
        return replacement.capitalize()
    if original[0].isupper():
        return replacement.capitalize()
    return replacement


# --------------------------------------------------------------------------- #
# Line-based processing
# --------------------------------------------------------------------------- #


def _process_lines(
    rel_path: str,
    lines: list[str],
    compiled: _Pattern | None,
    fixed: str,
    ignore_case: bool,
    no_unicode: bool,
    invert: bool,
    context_before: int,
    context_after: int,
    max_count: int,
    replace: str | None,
    only_matching: bool,
    crlf: bool,
    literal_replace: bool,
    preserve_case: bool,
    case_mode: str | None,
    record_context: bool,
    byte_tracker: list[int] | None = None,
    encoding: str = "utf-8",
) -> FileResult:
    hits: list[tuple[int, list[tuple[int, int]], str]] = []
    for idx, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r") if crlf else raw_line
        spans = _spans_for_line(line, compiled, fixed, ignore_case, no_unicode)
        matched = bool(spans) != invert
        if not matched:
            continue
        if invert:
            spans = []
        hits.append((idx + 1, spans, line))
        if max_count and len(hits) >= max_count:
            break
    if not hits:
        return FileResult(path=rel_path)
    match_count = len(hits)
    records: list[MatchRecord] = []
    if record_context or replace is not None or only_matching:
        for lineno, spans, line in hits:
            if spans:
                for s, e in spans:
                    matched_text = line[s:e]
                    replacement_text: str | None = None
                    if replace is not None:
                        if compiled is not None and not literal_replace:
                            m_obj = next(
                                (mm for mm in compiled.finditer(line) if mm.start() == s and mm.end() == e),
                                None,
                            )
                            replacement_text = m_obj.expand(replace) if m_obj is not None else replace
                        else:
                            replacement_text = replace
                        if preserve_case:
                            replacement_text = _preserve_case(replacement_text, matched_text)
                        elif case_mode:
                            replacement_text = _case_transform(replacement_text, case_mode)
                    records.append(
                        MatchRecord(
                            path=rel_path,
                            line=lineno,
                            column=s + 1,
                            byte_offset=(byte_tracker[lineno - 1] if byte_tracker else 0)
                            + len(line[:s].encode(encoding, errors="replace")),
                            match=matched_text,
                            replacement=replacement_text,
                            line_text=line,
                            context_before=lines[max(0, lineno - 1 - context_before) : lineno - 1],
                            context_after=lines[lineno : lineno + context_after],
                            is_context=False,
                        )
                    )
    if only_matching:
        current: list[OutputLine] = []
        for lineno, spans, line in hits:
            if not spans:
                current.append((lineno, line, True, []))
                continue
            for s, e in spans:
                piece = line[s:e]
                if replace is not None:
                    if compiled is not None and not literal_replace:
                        m_obj = next(
                            (mm for mm in compiled.finditer(line) if mm.start() == s and mm.end() == e),
                            None,
                        )
                        piece = m_obj.expand(replace) if m_obj is not None else replace
                    else:
                        piece = replace
                    if preserve_case:
                        piece = _preserve_case(piece, line[s:e])
                    elif case_mode:
                        piece = _case_transform(piece, case_mode)
                current.append((lineno, piece, True, [(0, len(piece))]))
        return FileResult(
            path=rel_path,
            groups=[current] if current else [],
            match_count=match_count,
            records=records,
            hit_lines=[h[0] for h in hits],
        )
    match_lines: dict[int, tuple[str, list[tuple[int, int]]]] = {}
    for lineno, spans, line in hits:
        if replace is not None:
            if compiled is not None:
                new_text_parts: list[str] = []
                new_spans: list[tuple[int, int]] = []
                pos = 0
                out_len = 0
                for m in compiled.finditer(line):
                    prefix = line[pos : m.start()]
                    new_text_parts.append(prefix)
                    out_len += len(prefix)
                    try:
                        repl = m.expand(replace) if not literal_replace else replace
                    except Exception:
                        repl = replace
                    if preserve_case:
                        repl = _preserve_case(repl, m.group(0))
                    elif case_mode:
                        repl = _case_transform(repl, case_mode)
                    new_text_parts.append(repl)
                    new_spans.append((out_len, out_len + len(repl)))
                    out_len += len(repl)
                    pos = m.end()
                new_text_parts.append(line[pos:])
                new_text = "".join(new_text_parts)
                match_lines[lineno] = (new_text, new_spans)
            else:
                new_text = _replace_fixed(line, fixed, replace, ignore_case)
                match_lines[lineno] = (new_text, [])
        else:
            match_lines[lineno] = (line, spans)
    groups = _build_groups(lines, match_lines, context_before, context_after)
    return FileResult(
        path=rel_path,
        groups=groups,
        match_count=match_count,
        records=records,
        hit_lines=[h[0] for h in hits],
        replacement_count=match_count if replace is not None else 0,
    )


def _iter_file_lines(
    path: Path,
    encoding: str,
    buffer_size: int,
    stop_event: Any | None = None,
) -> Iterator[tuple[int, str, int]]:
    with path.open("rb") as raw_fh:
        head = raw_fh.read(8192)
    had_bom, effective_encoding, _bom_char = _bom_and_encoding(head, encoding)
    offset = 3 if had_bom else 0
    with path.open(encoding=effective_encoding, errors="replace", newline="", buffering=buffer_size) as fh:
        lineno = 0
        for line in fh:
            if stop_event is not None and stop_event.is_set():
                return
            lineno += 1
            stripped = line.rstrip("\n")
            yield lineno, stripped, offset
            offset += len(line.encode(effective_encoding, errors="replace"))


def _process_lines_streaming(
    rel_path: str,
    path: Path,
    compiled: _Pattern | None,
    fixed: str,
    ignore_case: bool,
    no_unicode: bool,
    invert: bool,
    context_before: int,
    context_after: int,
    max_count: int,
    replace: str | None,
    encoding: str,
    buffer_size: int,
    crlf: bool,
    literal_replace: bool,
    preserve_case: bool,
    case_mode: str | None,
    stop_event: Any | None = None,
) -> FileResult:
    before_buf: deque[tuple[int, str]] = deque(maxlen=max(context_before, 1))
    records: list[MatchRecord] = []
    match_lines: dict[int, tuple[str, list[tuple[int, int]]]] = {}
    after_remaining = 0
    hit_count = 0
    byte_tracker: dict[int, int] = {}
    stream = _iter_file_lines(path, encoding, buffer_size, stop_event)
    lines_all: dict[int, str] = {}
    for lineno, line, offset in stream:
        if crlf:
            line = line.rstrip("\r")
        lines_all[lineno] = line
        byte_tracker[lineno] = offset
        if after_remaining > 0:
            match_lines[lineno] = (line, [])
            after_remaining -= 1
        spans = _spans_for_line(line, compiled, fixed, ignore_case, no_unicode)
        matched = bool(spans) != invert
        if matched:
            if max_count and hit_count >= max_count:
                break
            hit_count += 1
            for lbl, ltxt in before_buf:
                if lbl not in match_lines:
                    match_lines[lbl] = (ltxt, [])
            if invert:
                spans = []
            if replace is not None:
                if compiled is not None:
                    new_text, new_spans = _apply_replace_line(line, compiled, replace, literal_replace)
                    match_lines[lineno] = (new_text, new_spans)
                else:
                    match_lines[lineno] = (
                        _replace_fixed(line, fixed, replace, ignore_case),
                        [],
                    )
            else:
                match_lines[lineno] = (line, spans)
            if spans:
                for s, e in spans:
                    mtext = line[s:e]
                    rtext: str | None = None
                    if replace is not None:
                        rtext = replace
                        if preserve_case:
                            rtext = _preserve_case(rtext, mtext)
                        elif case_mode:
                            rtext = _case_transform(rtext, case_mode)
                    records.append(
                        MatchRecord(
                            path=rel_path,
                            line=lineno,
                            column=s + 1,
                            byte_offset=offset + len(line[:s].encode(encoding, errors="replace")),
                            match=mtext,
                            replacement=rtext,
                            line_text=line,
                            context_before=[],
                            context_after=[],
                            is_context=False,
                        )
                    )
            after_remaining = context_after
        elif after_remaining > 0:
            after_remaining -= 1
        before_buf.append((lineno, line))
    if not match_lines:
        return FileResult(path=rel_path)
    ordered_lines = [lines_all[i] for i in sorted(lines_all)]
    id_to_idx = {lid: i + 1 for i, lid in enumerate(sorted(lines_all))}
    shifted = {id_to_idx[k]: v for k, v in match_lines.items()}
    groups = _build_groups(ordered_lines, shifted, context_before, context_after)
    return FileResult(
        path=rel_path,
        groups=groups,
        match_count=hit_count,
        records=records,
        hit_lines=sorted(match_lines),
        replacement_count=hit_count if replace is not None else 0,
    )


# --------------------------------------------------------------------------- #
# Multiline processing
# --------------------------------------------------------------------------- #


def _process_multiline(
    rel_path: str,
    text: str,
    compiled: _Pattern,
    context_before: int,
    context_after: int,
    max_count: int,
    replace: str | None,
    literal_replace: bool,
    preserve_case: bool,
    case_mode: str | None,
    crlf: bool,
) -> FileResult:
    lines = text.splitlines()
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    def to_line_idx(offset: int) -> int:
        return max(0, bisect.bisect_right(line_starts, offset) - 1)

    hits: dict[int, list[tuple[int, int]]] = {}
    order: list[int] = []
    records: list[MatchRecord] = []
    for m in compiled.finditer(text):
        if m.start() == m.end():
            continue
        ls = to_line_idx(m.start())
        le = to_line_idx(max(m.start(), m.end() - 1))
        for li in range(ls, le + 1):
            line_start = line_starts[li]
            line_text = lines[li] if li < len(lines) else ""
            if crlf:
                line_text = line_text.rstrip("\r")
            span_start = max(0, m.start() - line_start)
            span_end = min(len(line_text), m.end() - line_start)
            if li not in hits:
                hits[li] = []
                order.append(li)
            if span_start < span_end:
                hits[li].append((span_start, span_end))
            else:
                hits[li].append((0, len(line_text)))
        matched_text = m.group(0)
        replacement_text: str | None = None
        if replace is not None:
            if literal_replace:
                replacement_text = replace
            else:
                try:
                    replacement_text = m.expand(replace)
                except Exception:
                    replacement_text = replace
            if preserve_case:
                replacement_text = _preserve_case(replacement_text, matched_text)
            elif case_mode:
                replacement_text = _case_transform(replacement_text, case_mode)
        records.append(
            MatchRecord(
                path=rel_path,
                line=ls + 1,
                column=(m.start() - line_starts[ls]) + 1,
                byte_offset=len(text[: m.start()].encode("utf-8", errors="replace")),
                match=matched_text,
                replacement=replacement_text,
                line_text=lines[ls] if ls < len(lines) else "",
                context_before=[],
                context_after=[],
                is_context=False,
            )
        )
        if max_count and len(order) >= max_count:
            break
    if not order:
        return FileResult(path=rel_path)
    match_lines: dict[int, tuple[str, list[tuple[int, int]]]] = {}
    for li in order:
        line_text = lines[li]
        if crlf:
            line_text = line_text.rstrip("\r")
        spans = hits[li]
        if replace is not None:
            parts: list[str] = []
            new_spans: list[tuple[int, int]] = []
            pos = 0
            out_len = 0
            for s, e in sorted(spans):
                prefix = line_text[pos:s]
                parts.append(prefix)
                out_len += len(prefix)
                rep = replace if literal_replace else replace
                if preserve_case:
                    rep = _preserve_case(rep, line_text[s:e])
                elif case_mode:
                    rep = _case_transform(rep, case_mode)
                parts.append(rep)
                new_spans.append((out_len, out_len + len(rep)))
                out_len += len(rep)
                pos = e
            parts.append(line_text[pos:])
            match_lines[li + 1] = ("".join(parts), new_spans)
        else:
            match_lines[li + 1] = (line_text, spans)
    groups = _build_groups(lines, match_lines, context_before, context_after)
    return FileResult(
        path=rel_path,
        groups=groups,
        match_count=len(order),
        records=records,
        hit_lines=[li + 1 for li in order],
        replacement_count=len(order) if replace is not None else 0,
    )


# --------------------------------------------------------------------------- #
# File reading
# --------------------------------------------------------------------------- #


def _read_file_text(
    path: Path,
    encoding: str,
    use_mmap: bool,
    mmap_threshold: int,
    buffer_size: int,
) -> tuple[str, bool, str, str]:
    with path.open("rb") as raw_fh:
        head = raw_fh.read(8192)
    had_bom, effective_encoding, _bom_char = _bom_and_encoding(head, encoding)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if use_mmap and size >= mmap_threshold:
        import mmap

        with path.open("rb") as raw_fh:
            with mmap.mmap(raw_fh.fileno(), length=0, access=mmap.ACCESS_READ) as mm:
                data = mm.read()
        text = data.decode(effective_encoding, errors="replace")
    else:
        with path.open(
            encoding=effective_encoding,
            errors="replace",
            newline="",
            buffering=buffer_size,
        ) as fh:
            text = fh.read()
    text = text.removeprefix("\ufeff")
    line_ending = _detect_line_ending(text)
    return text, had_bom, effective_encoding, line_ending


def _apply_replacement_to_text(
    text: str,
    compiled: _Pattern | None,
    fixed: str,
    ignore_case: bool,
    replace_template: str,
    literal_replace: bool,
    preserve_case: bool,
    case_mode: str | None,
) -> tuple[str, int]:
    count = 0
    if compiled is not None:

        def _repl(m: Match[str]) -> str:
            nonlocal count
            count += 1
            if literal_replace:
                rep = replace_template
            else:
                try:
                    rep = m.expand(replace_template)
                except Exception:
                    rep = replace_template
            if preserve_case:
                rep = _preserve_case(rep, m.group(0))
            elif case_mode:
                rep = _case_transform(rep, case_mode)
            return rep

        new_text = compiled.sub(_repl, text)
        return new_text, count
    if not fixed:
        return text, 0
    if ignore_case:
        pat = re.compile(re.escape(fixed), re.IGNORECASE)
        new_text, n = pat.subn(lambda _m: replace_template, text)
        return new_text, n
    new_text = text.replace(fixed, replace_template)
    return new_text, text.count(fixed)


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #


def worker(job: Job) -> FileResult:
    path = Path(job.path)
    cwd = Path(job.cwd)
    try:
        rel_path = str(path.relative_to(cwd))
    except ValueError:
        rel_path = job.path

    compiled = job.compiled
    if compiled is None and job.pattern_str is not None:
        try:
            compiled = _compile_cached(job.pattern_str, job.flags, job.engine)
        except Exception as ex:
            return FileResult(path=rel_path, error=f"invalid regex: {ex}")

    try:
        if not job.text_mode:
            try:
                if is_binary(path):
                    return FileResult(path=rel_path)
            except OSError:
                pass
    except OSError:
        pass

    pre_used = False
    text = ""
    if job.pre_cmd:
        if job.pre_glob is None or fnmatch.fnmatch(path.name, job.pre_glob) or fnmatch.fnmatch(str(path), job.pre_glob):
            try:
                proc = subprocess_run(["sh", "-c", f'{job.pre_cmd} "$1"', "_", str(path)])
            except OSError as ex:
                return FileResult(path=rel_path, error=f"pre: {ex}")
            text = proc.stdout.decode(job.encoding, errors="replace")
            pre_used = True
        else:
            text = ""

    had_bom = False
    effective_encoding = job.encoding
    line_ending = "\n"

    if not pre_used:
        if job.process_lines and not job.multiline and job.replace is None:
            try:
                return _process_lines_streaming(
                    rel_path,
                    path,
                    compiled,
                    job.fixed,
                    job.ignore_case,
                    job.no_unicode,
                    job.invert,
                    job.before,
                    job.after,
                    job.max_count,
                    job.replace,
                    job.encoding,
                    job.buffer_size,
                    job.crlf,
                    job.literal_replace,
                    job.preserve_case,
                    job.case_mode,
                )
            except OSError as ex:
                return FileResult(path=rel_path, error=str(ex))
        try:
            text, had_bom, effective_encoding, line_ending = _read_file_text(
                path, job.encoding, job.use_mmap, job.mmap_threshold, job.buffer_size
            )
        except OSError as ex:
            return FileResult(path=rel_path, error=str(ex))

    try:
        lines = text.splitlines()
        if job.crlf:
            lines = [ln.rstrip("\r") for ln in lines]
        byte_tracker: list[int] | None = None
        if job.byte_offset or job.record_context:
            byte_tracker = []
            running = 3 if had_bom else 0
            for ln in lines:
                byte_tracker.append(running)
                running += len((ln + line_ending).encode(effective_encoding, errors="replace"))
        if job.multiline and compiled is not None and not job.invert:
            result = _process_multiline(
                rel_path,
                text,
                compiled,
                job.before,
                job.after,
                job.max_count,
                job.replace,
                job.literal_replace,
                job.preserve_case,
                job.case_mode,
                job.crlf,
            )
        else:
            result = _process_lines(
                rel_path,
                lines,
                compiled,
                job.fixed,
                job.ignore_case,
                job.no_unicode,
                job.invert,
                job.before,
                job.after,
                job.max_count,
                job.replace,
                job.only_matching,
                job.crlf,
                job.literal_replace,
                job.preserve_case,
                job.case_mode,
                job.record_context,
                byte_tracker,
                effective_encoding,
            )
        result.had_bom = had_bom
        result.detected_encoding = effective_encoding
        result.line_ending = line_ending
        if job.in_place and job.replace is not None and result.match_count > 0:
            new_text, n = _apply_replacement_to_text(
                text,
                compiled,
                job.fixed,
                job.ignore_case,
                job.replace,
                job.literal_replace,
                job.preserve_case,
                job.case_mode,
            )
            if n > 0:
                encoded = new_text.encode(effective_encoding, errors="replace")
                if had_bom and not encoded.startswith(b"\xef\xbb\xbf"):
                    encoded = b"\xef\xbb\xbf" + encoded
                result.new_content = encoded
                result.old_text = text
        return result
    except (re.error, Exception) as ex:
        return FileResult(path=rel_path, error=f"{type(ex).__name__}: {ex}")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _colorize_line(line: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return line
    chars = list(line)
    for s, e in sorted(spans, key=lambda x: x[0], reverse=True):
        if 0 <= s <= e <= len(chars):
            chars.insert(e, ANSI_RESET)
            chars.insert(s, ANSI_HIGHLIGHT)
    return "".join(chars)


def _truncate_line(text: str, max_cols: int, preview: bool, spans: list[tuple[int, int]]) -> str:
    if max_cols <= 0 or len(text) <= max_cols:
        return text
    head = text[:max_cols]
    if preview:
        more = len(text) - max_cols
        return f"{head}…(…{more} more chars…)"
    return head


def _render_json_record(rec: MatchRecord, as_seq: bool) -> str:
    obj: dict[str, Any] = {
        "path": rec.path,
        "line": rec.line,
        "column": rec.column,
        "byte_offset": rec.byte_offset,
        "match": rec.match,
        "replacement": rec.replacement,
        "line_text": rec.line_text,
        "context_before": rec.context_before,
        "context_after": rec.context_after,
        "is_context": rec.is_context,
    }
    if as_seq:
        return json.dumps([obj], ensure_ascii=False)
    return json.dumps(obj, ensure_ascii=False)


def emit_result(result: FileResult, opts: RenderOptions, out_fh: TextIO) -> None:
    """Emit result."""
    reset = ANSI_RESET if opts.color else ""
    path_col = ANSI_FILENAME if opts.color else ""
    sep_end = "\x00" if opts.null_sep else "\n"
    if opts.as_json or opts.as_json_seq:
        out_fh.writelines(_render_json_record(rec, opts.as_json_seq) + "\n" for rec in result.records)
        return
    if opts.show_path_only:
        out_fh.write(f"{path_col}{result.path}{reset}{sep_end}")
        return
    if opts.show_count:
        out_fh.write(f"{path_col}{result.path}{reset}:{result.match_count}{sep_end}")
        return
    show_path = True
    if opts.show_filename_override is not None:
        show_path = opts.show_filename_override
    if opts.passthru:
        for group in result.groups:
            for lineno, text, is_match, spans in group:
                body = _colorize_line(text, spans) if (opts.color and spans and is_match) else text
                body = _truncate_line(body, opts.max_columns, opts.max_columns_preview, spans)
                out_fh.write(body + "\n")
        return
    if opts.heading:
        out_fh.write(f"{path_col}{result.path}{reset}\n")
        indent = "  "
    else:
        indent = ""
    first_group = True
    for group in result.groups:
        if not first_group:
            out_fh.write(opts.context_separator + "\n")
        first_group = False
        for lineno, text, is_match, spans in group:
            body = _colorize_line(text, spans) if (opts.color and spans) else text
            body = _truncate_line(body, opts.max_columns, opts.max_columns_preview, spans)
            sep = opts.field_match_separator if is_match else opts.field_context_separator
            prefix = indent if (opts.heading or not show_path) else f"{path_col}{result.path}{reset}{sep}"
            if opts.show_line_number:
                if opts.column and is_match and spans:
                    col = spans[0][0] + 1
                    if opts.color:
                        out_fh.write(f"{prefix}{ANSI_LINE_NO}{lineno}{reset}:{col}{sep}{body}\n")
                    else:
                        out_fh.write(f"{prefix}{lineno}:{col}{sep}{body}\n")
                else:
                    if opts.color:
                        out_fh.write(f"{prefix}{ANSI_LINE_NO}{lineno}{reset}{sep}{body}\n")
                    else:
                        out_fh.write(f"{prefix}{lineno}{sep}{body}\n")
            else:
                out_fh.write(f"{prefix}{body}\n")


# --------------------------------------------------------------------------- #
# In-place replacement support
# --------------------------------------------------------------------------- #


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".pyrg_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            shutil.copystat(path, tmp)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _backup_file(path: Path, suffix: str, backup_dir: Path | None, cwd: Path) -> None:
    try:
        data = path.read_bytes()
    except OSError as ex:
        print(f"backup failed for {path}: {ex}", file=sys.stderr)
        return
    if backup_dir is not None:
        try:
            rel = path.relative_to(cwd)
        except ValueError:
            rel = Path(path.name)
        target = backup_dir / rel
        target = target.with_name(target.name + suffix)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target, data)
        except OSError as ex:
            print(f"backup failed for {path}: {ex}", file=sys.stderr)
    else:
        target = path.with_name(path.name + suffix)
        try:
            atomic_write(target, data)
        except OSError as ex:
            print(f"backup failed for {path}: {ex}", file=sys.stderr)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _undo_journal_paths(journal: Path) -> tuple[Path, Path]:
    return journal, journal.with_name(journal.name + ".d")


def _append_undo_journal(journal: Path, path: Path, old_bytes: bytes, new_bytes: bytes, cwd: Path) -> None:
    jpath, blob_dir = _undo_journal_paths(journal)
    blob_dir.mkdir(parents=True, exist_ok=True)
    old_hash = _sha256(old_bytes)
    new_hash = _sha256(new_bytes)
    blob_path = blob_dir / f"{old_hash}.bin"
    if not blob_path.exists():
        atomic_write(blob_path, old_bytes)
    try:
        rel = str(path.relative_to(cwd))
    except ValueError:
        rel = str(path)
    entry = {
        "path": rel,
        "blob": str(blob_path),
        "old_hash": old_hash,
        "new_hash": new_hash,
        "ts": time.time(),
    }
    with open(jpath, "a", encoding="utf-8") as jf:
        jf.write(json.dumps(entry) + "\n")


def _undo_from_journal(journal: Path, cwd: Path) -> int:
    jpath, _blob_dir = _undo_journal_paths(journal)
    if not jpath.is_file():
        print(f"error: undo journal {jpath} not found", file=sys.stderr)
        raise SystemExit(3)
    entries: list[dict[str, Any]] = []
    with open(jpath, "r", encoding="utf-8") as jf:
        for line in jf:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    restored = 0
    for entry in reversed(entries):
        path = cwd / entry["path"]
        blob = Path(entry["blob"])
        try:
            current = path.read_bytes()
        except OSError:
            continue
        if _sha256(current) != entry["new_hash"]:
            print(f"skip {path}: hash mismatch", file=sys.stderr)
            continue
        try:
            data = blob.read_bytes()
        except OSError:
            print(f"skip {path}: blob missing", file=sys.stderr)
            continue
        try:
            _atomic_write(path, data)
            restored += 1
        except OSError as ex:
            print(f"undo failed for {path}: {ex}", file=sys.stderr)
    print(f"Restored {restored} file(s)")
    return 0


def _unified_diff(path: Path, old: str, new: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
    )


def _git_clean(cwd: Path) -> bool:
    try:
        proc = subprocess_run(["git", "status", "--porcelain"], cwd=str(cwd))
    except (OSError, FileNotFoundError):
        return False
    if proc.returncode != 0:
        return False
    return proc.stdout.strip() == b""


def _prompt_yes_no(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "n"


# --------------------------------------------------------------------------- #
# Shell completions
# --------------------------------------------------------------------------- #


_COMPLETIONS_BASH: str = """_pyrg_completion() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local opts="--help --version --files --files-with-matches --files-without-match --count --quiet --json --json-seq --in-place --dry-run --diff --backup --undo --undo-journal --replace --pattern-file --type --type-add --type-not --type-list --ext --glob --iglob --exclude-dir --max-depth --max-filesize --max-count --context --before-context --after-context --word-regexp --line-regexp --multiline --fixed-strings --ignore-case --smart-case --case-sensitive --pcre2 --engine --no-unicode --crlf --column --byte-offset --null --passthru --pretty --max-columns --max-columns-preview --stats --stats-only --check --exit-code --threads --no-parallel --mmap --no-mmap --buffer-size --process-lines --sort --sortr --generate --config --no-config --debug --trace --log-file --no-messages --legacy-flags"
    COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
}
complete -F _pyrg_completion pyrg
"""

_COMPLETIONS_ZSH: str = """#compdef pyrg
_pyrg() {
    local -a opts
    opts=(--help --version --files --in-place --dry-run --diff --json --json-seq --type --glob --type-add --type-not --type-list --ext --iglob --exclude-dir --max-depth --max-count --context --word-regexp --line-regexp --multiline --ignore-case --smart-case --case-sensitive --pcre2 --engine --sort --sortr --generate --config --no-config --debug --trace)
    _arguments '*: :(${opts})'
}
_pyrg "$@"
"""

_COMPLETIONS_FISH: str = """complete -c pyrg -l help -d 'Show help'
complete -c pyrg -l version -d 'Show version'
complete -c pyrg -l files -d 'List files'
complete -c pyrg -l in-place -d 'Modify files'
complete -c pyrg -l dry-run -d 'Preview changes'
complete -c pyrg -l diff -d 'Show diff'
complete -c pyrg -l json -d 'JSON output'
complete -c pyrg -l type -d 'Type preset'
complete -c pyrg -l glob -d 'Include glob'
complete -c pyrg -l ext -d 'Extension'
"""

_COMPLETIONS_POWERSHELL: str = """Register-ArgumentCompleter -Native -CommandName 'pyrg' -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $opts = @('--help','--version','--files','--in-place','--dry-run','--diff','--json','--json-seq','--type','--glob','--ext','--sort','--config','--debug','--trace')
    $opts | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterName', $_)
    }
}
"""


def _emit_completion(shell: str) -> int:
    mapping = {
        "bash": _COMPLETIONS_BASH,
        "zsh": _COMPLETIONS_ZSH,
        "fish": _COMPLETIONS_FISH,
        "powershell": _COMPLETIONS_POWERSHELL,
    }
    script = mapping.get(shell)
    if script is None:
        print(f"error: unknown shell {shell!r}; expected bash, zsh, fish, powershell", file=sys.stderr)
        return 2
    sys.stdout.write(script)
    return 0


# --------------------------------------------------------------------------- #
# Quiet / early-exit search
# --------------------------------------------------------------------------- #


def _search_quiet(
    files: list[Path],
    cwd: Path,
    compiled: _Pattern | None,
    pattern_str: str | None,
    flags: int,
    engine: str,
    fixed: str,
    ignore_case: bool,
    no_unicode: bool,
    invert_match: bool,
    multiline: bool,
    encoding: str,
    text_mode: bool,
    use_mmap: bool,
    mmap_threshold: int,
    buffer_size: int,
    threads: int,
) -> bool:
    base_job = Job(
        path="",
        cwd=str(cwd),
        compiled=compiled,
        pattern_str=pattern_str,
        engine=engine,
        flags=flags,
        fixed=fixed,
        ignore_case=ignore_case,
        no_unicode=no_unicode,
        invert=invert_match,
        before=0,
        after=0,
        max_count=1,
        multiline=multiline and not invert_match,
        encoding=encoding,
        replace=None,
        only_matching=False,
        crlf=False,
        literal_replace=False,
        preserve_case=False,
        case_mode=None,
        text_mode=text_mode,
        use_mmap=use_mmap,
        mmap_threshold=mmap_threshold,
        buffer_size=buffer_size,
        process_lines=False,
        in_place=False,
        record_context=False,
        byte_offset=False,
        pre_cmd=None,
        pre_glob=None,
    )
    jobs = [dc_replace(base_job, path=str(p)) for p in files]
    if threads <= 1:
        return any(worker(j).match_count for j in jobs)
    with ProcessPoolExecutor(max_workers=threads) as ex:
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


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="pyrg",
        description="pyrg — a ripgrep-like recursive regex search tool.\n\nSearch files for a regular expression (or fixed string) with support for case folding, inversion, multiline patterns, context lines, only-matching output, in-place replacement, glob/extension/type filtering, gitignore-style ignore files, JSON output, counting, quiet mode, and parallel processing.",
    )
    parser.add_argument(
        "pattern",
        nargs="?",
        default=None,
        help="Regex pattern (or use -r/--regexp). Required unless --files/--type-list/--generate/--version/--undo.",
    )
    parser.add_argument(
        "paths", nargs="*", default=None, help="Files or directories to search (default: current directory)."
    )
    parser.add_argument(
        "-r", "--regexp", dest="regexp", default=None, help="Pattern (alternative to positional PATTERN)."
    )
    parser.add_argument(
        "-e", dest="e_opt", default=None, help="Alias for -r/--regexp (extension mode under --legacy-flags)."
    )
    parser.add_argument(
        "-f", "--pattern-file", dest="pattern_file", default=None, help="Read patterns from PATH (one per line)."
    )
    parser.add_argument(
        "--ext",
        dest="extensions",
        action="append",
        default=None,
        help="Only search files with given extension(s) (repeatable, comma ok).",
    )
    parser.add_argument(
        "-t",
        "--type",
        dest="types",
        action="append",
        default=None,
        help="Only search files matching a type preset (repeatable).",
    )
    parser.add_argument(
        "-T",
        "--type-not",
        dest="type_not",
        action="append",
        default=None,
        help="Exclude files of a given type preset (repeatable).",
    )
    parser.add_argument(
        "--type-add",
        dest="type_add",
        action="append",
        default=None,
        help="Register preset NAME:EXT[,EXT...] (repeatable).",
    )
    parser.add_argument(
        "--type-clear", dest="type_clear", action="append", default=None, help="Remove preset NAME (repeatable)."
    )
    parser.add_argument(
        "--type-list", dest="type_list", action="store_true", default=False, help="Print known presets and exit."
    )
    parser.add_argument(
        "-i", "--ignore-case", dest="ignore_case", action="store_true", default=False, help="Case-insensitive matching."
    )
    parser.add_argument(
        "-S",
        "--smart-case",
        dest="smart_case",
        action="store_true",
        default=False,
        help="Case-insensitive unless pattern has uppercase.",
    )
    parser.add_argument(
        "-s",
        "--case-sensitive",
        dest="case_sensitive",
        action="store_true",
        default=False,
        help="Force case-sensitive matching.",
    )
    parser.add_argument(
        "-F",
        "--fixed-strings",
        dest="fixed_strings",
        action="store_true",
        default=False,
        help="Treat PATTERN as a literal string.",
    )
    parser.add_argument(
        "-w", "--word-regexp", dest="word_regexp", action="store_true", default=False, help="Match whole words only."
    )
    parser.add_argument(
        "--line-regexp", dest="line_regexp_x", action="store_true", default=False, help="Match whole lines only."
    )
    parser.add_argument(
        "-v", "--invert-match", dest="invert_match", action="store_true", default=False, help="Show non-matching lines."
    )
    parser.add_argument(
        "-U",
        "--multiline",
        dest="multiline",
        action="store_true",
        default=False,
        help="Allow pattern to span lines (DOTALL).",
    )
    parser.add_argument(
        "--no-unicode", dest="no_unicode", action="store_true", default=False, help="Disable Unicode (re.ASCII)."
    )
    parser.add_argument(
        "--crlf", dest="crlf", action="store_true", default=False, help="Strip CR before matching; re-append on write."
    )
    parser.add_argument(
        "-P",
        "--pcre2",
        dest="pcre2",
        action="store_true",
        default=False,
        help="Use the 'regex' module (alias for --engine regex).",
    )
    parser.add_argument("--engine", dest="engine", default="re", help="Regex engine: 're' (default) or 'regex'.")
    parser.add_argument(
        "-o",
        "--only-matching",
        dest="only_matching",
        action="store_true",
        default=False,
        help="Print only matched parts.",
    )
    parser.add_argument("--replace", dest="replace", default=None, help="Replacement template (\\1 backreferences).")
    parser.add_argument(
        "--literal-replace",
        dest="literal_replace",
        action="store_true",
        default=False,
        help="Do not expand backreferences in replacement.",
    )
    parser.add_argument(
        "--replace-from", dest="replace_from", default=None, help="Read replacement template from PATH (-: stdin)."
    )
    parser.add_argument(
        "-n",
        "--line-number",
        dest="line_number_flag",
        action="store_true",
        default=True,
        help="Show line numbers (default on).",
    )
    parser.add_argument(
        "--no-line-number", dest="no_line_number", action="store_true", default=False, help="Hide line numbers."
    )
    parser.add_argument(
        "-l",
        "--files-with-matches",
        dest="files_with_matches",
        action="store_true",
        default=False,
        help="Print only paths with matches.",
    )
    parser.add_argument(
        "-L",
        "--files-without-match",
        dest="files_without_match",
        action="store_true",
        default=False,
        help="Print only paths without matches.",
    )
    parser.add_argument(
        "-c", "--count", dest="count", action="store_true", default=False, help="Print match count per file."
    )
    parser.add_argument(
        "-q",
        "--quiet",
        dest="quiet",
        action="store_true",
        default=False,
        help="Suppress output; exit status indicates match.",
    )
    parser.add_argument(
        "--check", dest="check", action="store_true", default=False, help="Exit 0 on match, 1 on none, 2 on error."
    )
    parser.add_argument(
        "--exit-code", dest="exit_code", action="store_true", default=False, help="Exit 1 when no matches found."
    )
    parser.add_argument(
        "--json", dest="json_out", action="store_true", default=False, help="Emit one JSON object per match."
    )
    parser.add_argument(
        "--json-seq", dest="json_seq", action="store_true", default=False, help="Emit one JSON array per match."
    )
    parser.add_argument(
        "--heading",
        dest="heading_flag",
        action="store_true",
        default=False,
        help="Force headings above each file's matches.",
    )
    parser.add_argument(
        "--no-heading", dest="no_heading", action="store_true", default=False, help="Suppress headings."
    )
    parser.add_argument(
        "-H",
        "--with-filename",
        dest="with_filename",
        action="store_true",
        default=False,
        help="Force filename in output.",
    )
    parser.add_argument(
        "--no-filename", dest="no_filename", action="store_true", default=False, help="Suppress filename in output."
    )
    parser.add_argument(
        "--column", dest="column", action="store_true", default=False, help="Print 1-based column of first match."
    )
    parser.add_argument(
        "-b",
        "--byte-offset",
        dest="byte_offset",
        action="store_true",
        default=False,
        help="Print byte offset of match.",
    )
    parser.add_argument(
        "-0", "--null", dest="null_sep", action="store_true", default=False, help="NUL after paths in -l/-L/-c/--files."
    )
    parser.add_argument(
        "--passthru",
        dest="passthru",
        action="store_true",
        default=False,
        help="Print non-matching lines alongside matches.",
    )
    parser.add_argument(
        "-p",
        "--pretty",
        dest="pretty",
        action="store_true",
        default=False,
        help="Shorthand for --color always --heading --line-number.",
    )
    parser.add_argument(
        "--max-columns", dest="max_columns", default=0, type=int, help="Truncate printed lines to N characters."
    )
    parser.add_argument(
        "--max-columns-preview",
        dest="max_columns_preview",
        action="store_true",
        default=False,
        help="Show truncation preview.",
    )
    parser.add_argument(
        "--stats-only",
        dest="stats_only",
        action="store_true",
        default=False,
        help="Print only stats summary (implies -q).",
    )
    parser.add_argument(
        "--context-separator",
        dest="context_separator",
        default=None,
        help="Separator between context groups (default: --).",
    )
    parser.add_argument(
        "--field-match-separator",
        dest="field_match_separator",
        default=":",
        help="Separator between path/line/text for matches.",
    )
    parser.add_argument(
        "--field-context-separator", dest="field_context_separator", default="-", help="Separator for context lines."
    )
    parser.add_argument(
        "-A", "--after-context", dest="after_context", default=0, type=int, help="Show N lines after each match."
    )
    parser.add_argument(
        "-B", "--before-context", dest="before_context", default=0, type=int, help="Show N lines before each match."
    )
    parser.add_argument("-C", "--context", dest="context", default=None, type=int, help="Shorthand for -A N -B N.")
    parser.add_argument("--hidden", dest="hidden", action="store_true", default=False, help="Search hidden files/dirs.")
    parser.add_argument(
        "-u",
        "--unrestricted",
        dest="unrestricted",
        action="count",
        default=0,
        help="Reduce filtering: -u no-ignore, -uu +hidden, -uuu +text.",
    )
    parser.add_argument(
        "-a", "--text", dest="text_flag", action="store_true", default=False, help="Treat binary files as text."
    )
    parser.add_argument(
        "--binary", dest="binary_flag", action="store_true", default=False, help="Search binary files, escape NULs."
    )
    parser.add_argument(
        "--require-git",
        dest="require_git",
        action="store_true",
        default=False,
        help="Only search files tracked by git.",
    )
    parser.add_argument(
        "--require-clean-git",
        dest="require_clean_git",
        action="store_true",
        default=False,
        help="Refuse if git working tree is dirty.",
    )
    parser.add_argument(
        "-g",
        "--glob",
        dest="glob_patterns",
        action="append",
        default=None,
        help="Only search paths matching GLOB (repeatable).",
    )
    parser.add_argument(
        "--iglob", dest="iglob", action="append", default=None, help="Case-insensitive include glob (repeatable)."
    )
    parser.add_argument(
        "--glob-case-insensitive",
        dest="glob_case_insensitive",
        action="store_true",
        default=False,
        help="Make -g/--iglob/--exclude-dir case-insensitive.",
    )
    parser.add_argument(
        "--exclude", dest="exclude", action="append", default=None, help="Exclude paths matching GLOB (repeatable)."
    )
    parser.add_argument(
        "--exclude-dir", dest="exclude_dirs", action="append", default=None, help="Exclude directory NAME (repeatable)."
    )
    parser.add_argument("--max-depth", dest="max_depth", default=None, type=int, help="Maximum directory depth.")
    parser.add_argument(
        "--max-filesize", dest="max_filesize", default=10000000, type=int, help="Skip files larger than BYTES."
    )
    parser.add_argument(
        "-m", "-M", "--max-count", dest="max_count", default=0, type=int, help="Stop after N matching lines per file."
    )
    parser.add_argument(
        "--no-ignore", dest="no_ignore", action="store_true", default=False, help="Do not honour .gitignore / .ignore."
    )
    parser.add_argument(
        "--ignore-file", dest="ignore_file", action="append", default=None, help="Additional ignore file (repeatable)."
    )
    parser.add_argument(
        "--follow", dest="follow", action="store_true", default=False, help="Follow symlinks (with cycle detection)."
    )
    parser.add_argument("--files-from", dest="files_from", default=None, help="Read file list from PATH (-: stdin).")
    parser.add_argument("--encoding", dest="encoding", default="utf-8", help="Text encoding to use.")
    parser.add_argument(
        "--files",
        dest="list_files",
        action="store_true",
        default=False,
        help="List files that would be searched, then exit.",
    )
    parser.add_argument("--sort", dest="sort", default="none", help="Sort: path|modified|accessed|created|none.")
    parser.add_argument("--sortr", dest="sortr", action="store_true", default=False, help="Reverse sort order.")
    parser.add_argument("--pre", dest="pre_cmd", default=None, help="Pipe each file through CMD and search its stdout.")
    parser.add_argument("--pre-glob", dest="pre_glob", default=None, help="Only preprocess files matching GLOB.")
    parser.add_argument("-j", "--threads", dest="threads", default=MAX_WORKERS, type=int, help="Worker count.")
    parser.add_argument(
        "--no-parallel", dest="no_parallel", action="store_true", default=False, help="Force single-threaded execution."
    )
    parser.add_argument(
        "--mmap",
        dest="mmap_flag",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use mmap for large files (default on).",
    )
    parser.add_argument(
        "--mmap-threshold", dest="mmap_threshold", default=1048576, type=int, help="mmap files >= BYTES."
    )
    parser.add_argument("--buffer-size", dest="buffer_size", default=65536, type=int, help="Buffered reader size.")
    parser.add_argument(
        "--process-lines",
        dest="process_lines",
        action="store_true",
        default=False,
        help="Stream lines instead of reading whole file.",
    )
    parser.add_argument(
        "--regex-size-limit",
        dest="regex_size_limit",
        default=10000000,
        type=int,
        help="Regex size limit (engine=regex only).",
    )
    parser.add_argument(
        "--dfa-size-limit",
        dest="dfa_size_limit",
        default=10000000,
        type=int,
        help="DFA size limit (engine=regex only).",
    )
    parser.add_argument(
        "-W",
        "--in-place",
        dest="in_place",
        action="store_true",
        default=False,
        help="Modify files in place (requires --replace).",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Preview changes without writing (implies --diff).",
    )
    parser.add_argument(
        "--diff", dest="diff", action="store_true", default=False, help="Print unified diff (implies --dry-run)."
    )
    parser.add_argument("--backup", dest="backup", default=None, help="Save backup with SUFFIX (default .bak).")
    parser.add_argument("--backup-dir", dest="backup_dir", default=None, help="Mirror backups into DIR.")
    parser.add_argument("--undo-journal", dest="undo_journal", default=None, help="JSON undo journal path.")
    parser.add_argument(
        "--undo", dest="undo", action="store_true", default=False, help="Replay --undo-journal in reverse."
    )
    parser.add_argument(
        "--prompt",
        dest="prompt",
        action="store_true",
        default=False,
        help="Interactive per-file [y]/[n]/[a]/[q] prompt.",
    )
    parser.add_argument(
        "--confirm",
        dest="confirm",
        action="store_true",
        default=False,
        help="Ask global [y/N] confirmation before writing.",
    )
    parser.add_argument(
        "--atomic-write",
        dest="atomic_write",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Atomic writes (default on for --in-place).",
    )
    parser.add_argument(
        "--preserve-case",
        dest="preserve_case",
        action="store_true",
        default=False,
        help="Preserve match case in replacement.",
    )
    parser.add_argument("--upper", dest="upper", action="store_true", default=False, help="Uppercase the replacement.")
    parser.add_argument("--lower", dest="lower", action="store_true", default=False, help="Lowercase the replacement.")
    parser.add_argument(
        "--capitalize", dest="capitalize", action="store_true", default=False, help="Capitalize the replacement."
    )
    parser.add_argument("--color", dest="color", default="auto", help="Colour output: auto|always|never.")
    parser.add_argument(
        "--no-color", dest="no_color", action="store_true", default=False, help="Alias for --color never."
    )
    parser.add_argument("--out-file", dest="out_file", default=None, help="Write results to FILE.")
    parser.add_argument(
        "--stats", dest="stats", action="count", default=0, help="Print summary to stderr (repeat for more detail)."
    )
    parser.add_argument(
        "--no-messages",
        dest="no_messages",
        action="store_true",
        default=False,
        help="Suppress per-file error reporting and no-match message.",
    )
    parser.add_argument(
        "--legacy-flags",
        dest="legacy_flags",
        action="store_true",
        default=False,
        help="Restore old short-flag meanings (-e/-x/-L).",
    )
    parser.add_argument("--config", dest="config_path", default=None, help="Load defaults from TOML/JSON config.")
    parser.add_argument(
        "--no-config", dest="no_config", action="store_true", default=False, help="Disable config auto-discovery."
    )
    parser.add_argument(
        "--debug", dest="debug_flag", action="store_true", default=False, help="Enable DEBUG logging (stderr)."
    )
    parser.add_argument(
        "--trace", dest="trace_flag", action="store_true", default=False, help="Enable TRACE logging (stderr)."
    )
    parser.add_argument("--log-file", dest="log_file", default=None, help="Override log file path ('' to disable).")
    parser.add_argument(
        "--version", dest="version_flag", action="store_true", default=False, help="Print version and exit."
    )
    parser.add_argument(
        "--generate", dest="generate", default=None, help="Emit shell completions for bash|zsh|fish|powershell."
    )
    return parser


def _pyrg_command(
    pattern: str | None = None,
    paths: list[str] | None = None,
    regexp: str | None = None,
    e_opt: str | None = None,
    pattern_file: str | None = None,
    extensions: list[str] | None = None,
    types: list[str] | None = None,
    type_not: list[str] | None = None,
    type_add: list[str] | None = None,
    type_clear: list[str] | None = None,
    type_list: bool = False,
    ignore_case: bool = False,
    smart_case: bool = False,
    case_sensitive: bool = False,
    fixed_strings: bool = False,
    word_regexp: bool = False,
    line_regexp_x: bool = False,
    invert_match: bool = False,
    multiline: bool = False,
    no_unicode: bool = False,
    crlf: bool = False,
    pcre2: bool = False,
    engine: str = "re",
    only_matching: bool = False,
    replace: str | None = None,
    literal_replace: bool = False,
    replace_from: str | None = None,
    line_number_flag: bool = True,
    no_line_number: bool = False,
    files_with_matches: bool = False,
    files_without_match: bool = False,
    count: bool = False,
    quiet: bool = False,
    check: bool = False,
    exit_code: bool = False,
    json_out: bool = False,
    json_seq: bool = False,
    heading_flag: bool = False,
    no_heading: bool = False,
    with_filename: bool = False,
    no_filename: bool = False,
    column: bool = False,
    byte_offset: bool = False,
    null_sep: bool = False,
    passthru: bool = False,
    pretty: bool = False,
    max_columns: int = 0,
    max_columns_preview: bool = False,
    stats_only: bool = False,
    context_separator: str | None = None,
    field_match_separator: str = ":",
    field_context_separator: str = "-",
    after_context: int = 0,
    before_context: int = 0,
    context: int | None = None,
    hidden: bool = False,
    unrestricted: int = 0,
    text_flag: bool = False,
    binary_flag: bool = False,
    require_git: bool = False,
    require_clean_git: bool = False,
    glob_patterns: list[str] | None = None,
    iglob: list[str] | None = None,
    glob_case_insensitive: bool = False,
    exclude: list[str] | None = None,
    exclude_dirs: list[str] | None = None,
    max_depth: int | None = None,
    max_filesize: int = 10000000,
    max_count: int = 0,
    no_ignore: bool = False,
    ignore_file: list[str] | None = None,
    follow: bool = False,
    files_from: str | None = None,
    encoding: str = "utf-8",
    list_files: bool = False,
    sort: str = "none",
    sortr: bool = False,
    pre_cmd: str | None = None,
    pre_glob: str | None = None,
    threads: int = MAX_WORKERS,
    no_parallel: bool = False,
    mmap_flag: bool = True,
    mmap_threshold: int = 1048576,
    buffer_size: int = 65536,
    process_lines: bool = False,
    regex_size_limit: int = 10000000,
    dfa_size_limit: int = 10000000,
    in_place: bool = False,
    dry_run: bool = False,
    diff: bool = False,
    backup: str | None = None,
    backup_dir: str | None = None,
    undo_journal: str | None = None,
    undo: bool = False,
    prompt: bool = False,
    confirm: bool = False,
    atomic_write: bool = True,
    preserve_case: bool = False,
    upper: bool = False,
    lower: bool = False,
    capitalize: bool = False,
    color: str = "auto",
    no_color: bool = False,
    out_file: str | None = None,
    stats: int = 0,
    no_messages: bool = False,
    legacy_flags: bool = False,
    config_path: str | None = None,
    no_config: bool = False,
    debug_flag: bool = False,
    trace_flag: bool = False,
    log_file: str | None = None,
    version_flag: bool = False,
    generate: str | None = None,
) -> None:
    """Search FILES for PATTERN.

    Recursively search files (default: current directory) for a regular
    expression (or fixed string with -F). By default, matching lines are
    printed with the file path, line number, and highlighted match.

    Use --help to see all options. The tool exits 0 if any match is found,
    1 if none, and 2 on usage/error.
    """
    if version_flag:
        print(f"pyrg {__version__}")
        raise SystemExit(0)
    if generate is not None:
        raise SystemExit(_emit_completion(generate))
    if type_list:
        for name in sorted(TYPE_PRESETS):
            exts = " ".join(sorted(TYPE_PRESETS[name]))
            print(f"{name}: {exts}")
        raise SystemExit(0)

    cfg = _load_config(config_path or _env_str("PYRG_CONFIG"), no_config)
    if log_file is not None:
        _setup_logging(
            log_file,
            "DEBUG" if not (debug_flag or trace_flag) else ("TRACE" if trace_flag else "DEBUG"),
        )
    elif debug_flag or trace_flag:
        logger.remove()
        logger.add(sys.stderr, level="TRACE" if trace_flag else "DEBUG", format="{message}")

    if threads == MAX_WORKERS:
        env_threads = _env_int("PYRG_JOBS")
        if env_threads is not None and env_threads > 0:
            threads = env_threads
    if no_parallel:
        threads = 1
    if threads <= 0:
        threads = 1

    if color == "auto":
        env_color = _env_str("PYRG_COLOR")
        if env_color is not None:
            color = env_color
    if color == "auto" and cfg.get("color"):
        color = str(cfg["color"])
    effective_color = "never" if no_color else color
    if effective_color not in ("auto", "always", "never"):
        print(f"invalid --color value: {effective_color}; expected auto, always, never", file=sys.stderr)
        raise SystemExit(2)
    if pretty:
        effective_color = "always"
        heading_flag = True
    if sort not in ("path", "modified", "accessed", "created", "none"):
        print(f"invalid --sort value: {sort}; expected path|modified|accessed|created|none", file=sys.stderr)
        raise SystemExit(2)
    if heading_flag and no_heading:
        print("--heading and --no-heading are mutually exclusive", file=sys.stderr)
        raise SystemExit(2)
    if with_filename and no_filename:
        print("-H/--with-filename and --no-filename are mutually exclusive", file=sys.stderr)
        raise SystemExit(2)
    if context is not None:
        after_context = context
        before_context = context
    if line_number_flag and no_line_number:
        print("error: -n and --no-line-number are mutually exclusive", file=sys.stderr)
        raise SystemExit(2)
    if files_with_matches and files_without_match:
        print("error: -l/--files-with-matches and -L/--files-without-match are mutually exclusive", file=sys.stderr)
        raise SystemExit(2)
    if ignore_case and smart_case:
        print("error: -i/--ignore-case and -S/--smart-case are mutually exclusive", file=sys.stderr)
        raise SystemExit(2)
    if smart_case and case_sensitive:
        print("error: -S/--smart-case and -s/--case-sensitive are mutually exclusive", file=sys.stderr)
        raise SystemExit(2)
    if ignore_case and case_sensitive:
        print("error: -i/--ignore-case and -s/--case-sensitive are mutually exclusive", file=sys.stderr)
        raise SystemExit(2)
    if replace is not None and replace_from is not None:
        print("error: --replace and --replace-from are mutually exclusive", file=sys.stderr)
        raise SystemExit(2)
    if passthru and (files_with_matches or files_without_match or count or quiet or json_out or json_seq):
        print("error: --passthru is mutually exclusive with -l/-L/-c/-q/--json", file=sys.stderr)
        raise SystemExit(2)
    if in_place:
        if replace is None and replace_from is None:
            print("error: -W/--in-place requires --replace or --replace-from", file=sys.stderr)
            raise SystemExit(2)
        if (
            files_with_matches
            or files_without_match
            or count
            or quiet
            or json_out
            or json_seq
            or passthru
            or list_files
            or files_from is not None
        ):
            print(
                "error: --in-place is mutually exclusive with -l/-L/-c/-q/--json/--passthru/--files/--files-from",
                file=sys.stderr,
            )
            raise SystemExit(2)
        if prompt and threads > 1:
            print("error: --prompt requires -j 1", file=sys.stderr)
            raise SystemExit(2)
    if context_separator is not None and json_out:
        print("error: --context-separator is mutually exclusive with --json", file=sys.stderr)
        raise SystemExit(2)
    if pattern_file is not None and (regexp is not None or e_opt is not None):
        print("error: -f/--pattern-file is mutually exclusive with -r/--regexp", file=sys.stderr)
        raise SystemExit(2)
    if pattern_file is not None and fixed_strings and replace is not None:
        print("error: -f + -F + --replace is not supported (backreferences ambiguous)", file=sys.stderr)
        raise SystemExit(2)
    if multiline and invert_match:
        logger.warning("--multiline ignored with --invert-match")
        multiline = False
    if sum([preserve_case, upper, lower, capitalize]) > 1:
        print("error: --preserve-case/--upper/--lower/--capitalize are mutually exclusive", file=sys.stderr)
        raise SystemExit(2)
    case_mode: str | None = None
    if upper:
        case_mode = "upper"
    elif lower:
        case_mode = "lower"
    elif capitalize:
        case_mode = "capitalize"

    if undo:
        if not undo_journal:
            print("error: --undo requires --undo-journal", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(_undo_from_journal(Path(undo_journal), Path.cwd()))

    if pcre2:
        engine = "regex"
    engine = _resolve_engine(engine)
    if engine == "re" and (regex_size_limit != 10_000_000 or dfa_size_limit != 10_000_000):
        logger.warning("--regex-size-limit / --dfa-size-limit ignored when --engine re")

    if type_add:
        for spec in type_add:
            _apply_type_add(spec)
    if type_clear:
        for name in type_clear:
            for piece in name.split(","):
                TYPE_PRESETS_MUTABLE.pop(piece.strip().lower(), None)
                TYPE_PRESETS.pop(piece.strip().lower(), None)

    if require_clean_git and not _git_clean(Path.cwd()):
        print("error: --require-clean-git: git working tree is dirty", file=sys.stderr)
        raise SystemExit(3)

    if no_line_number:
        line_number_flag = False
    else:
        line_number_flag = True

    effective_paths: list[str] = list(paths) if paths else ["."]

    legacy = legacy_flags
    if not legacy and e_opt is not None:
        print(
            "note: -e is --regexp (alias for -r). Use --ext for extensions; --legacy-flags restores old behaviour.",
            file=sys.stderr,
        )
        if regexp is None:
            regexp = e_opt
    if legacy and e_opt is not None:
        if extensions is None:
            extensions = []
        extensions = list(extensions) + [e_opt]

    if no_ignore or unrestricted >= 1:
        no_ignore = True
    if unrestricted >= 2:
        hidden = True
    text_mode = text_flag or unrestricted >= 3 or binary_flag

    extensions = list(extensions or [])
    types = list(types or [])
    type_not = list(type_not or [])
    glob_patterns = list(glob_patterns or [])
    iglob = list(iglob or [])
    exclude = list(exclude or [])
    exclude_dirs = list(exclude_dirs or [])
    ignore_file = list(ignore_file or [])

    resolved_replace: str | None = replace
    if replace_from is not None:
        resolved_replace = _read_replace_from(replace_from)

    compiled: _Pattern | None = None
    pattern_str: str | None = None
    fixed = ""
    pattern_flags = 0
    effective_ignore_case = ignore_case

    if not list_files:
        pattern_str, fixed, effective_ignore_case, pattern_flags = _build_pattern_source(
            pattern=pattern,
            regexp=regexp,
            pattern_file=pattern_file,
            fixed_strings=fixed_strings,
            word_regexp=word_regexp,
            line_regexp=line_regexp_x,
            smart_case=smart_case,
            ignore_case=ignore_case,
            case_sensitive=case_sensitive,
            multiline=multiline,
            engine=engine,
            no_unicode=no_unicode,
        )
        if fixed_strings:
            pattern_str = None
        elif pattern_str is not None:
            try:
                compiled = _compile_cached(pattern_str, pattern_flags, engine)
            except Exception as ex:
                print(f"Invalid regex: {ex}", file=sys.stderr)
                raise SystemExit(2)

    if sortr and sort != "none":
        pass

    files = _collect_files(
        paths=effective_paths,
        extensions=extensions,
        types=types,
        type_not=type_not,
        globs=glob_patterns,
        iglob=iglob,
        glob_case_insensitive=glob_case_insensitive,
        excludes=exclude,
        exclude_dirs=exclude_dirs,
        hidden=hidden,
        max_size=max_filesize,
        max_depth=max_depth,
        follow=follow,
        no_ignore=no_ignore,
        ignore_files=ignore_file,
        files_from=files_from,
        require_git=require_git,
    )

    if list_files:
        if sort in ("path",):
            files.sort(key=str)
        elif sort in ("modified", "accessed", "created"):
            attr = {
                "modified": "st_mtime",
                "accessed": "st_atime",
                "created": "st_ctime",
            }[sort]
            try:
                files.sort(key=lambda p: getattr(p.stat(), attr))
            except OSError:
                files.sort(key=str)
            if sortr:
                files.reverse()
        out_fh_list, should_close_list = _open_output(out_file)
        try:
            cwd = Path.cwd()
            sep_end = "\x00" if null_sep else "\n"
            for f in files:
                try:
                    rel = f.relative_to(cwd)
                except ValueError:
                    rel = f
                out_fh_list.write(f"{rel}{sep_end}")
        finally:
            if should_close_list:
                out_fh_list.close()
        raise SystemExit(0)

    if sort in ("path",):
        files.sort(key=str)
    elif sort in ("modified", "accessed", "created"):
        attr = {"modified": "st_mtime", "accessed": "st_atime", "created": "st_ctime"}[sort]
        try:
            files.sort(key=lambda p: getattr(p.stat(), attr))
        except OSError:
            files.sort(key=str)
        if sortr:
            files.reverse()

    if not files:
        if not no_messages:
            print("pyrg: no files matched the search filters", file=sys.stderr)
        raise SystemExit(1)

    out_fh, should_close = _open_output(out_file)
    use_color = _should_color(effective_color, out_fh is sys.stdout) and not json_out and not json_seq
    effective_heading = heading_flag if heading_flag else (False if no_heading else None)
    if effective_heading is None:
        effective_heading = use_color and not files_from and with_filename
    if pretty:
        effective_heading = True

    show_filename_override: bool | None = None
    if with_filename:
        show_filename_override = True
    elif no_filename:
        show_filename_override = False

    render = RenderOptions(
        show_path_only=files_with_matches,
        show_count=count,
        as_json=json_out,
        as_json_seq=json_seq,
        show_line_number=line_number_flag,
        color=use_color,
        heading=bool(effective_heading),
        show_filename_override=show_filename_override,
        column=column,
        byte_offset=byte_offset,
        null_sep=null_sep,
        passthru=passthru,
        max_columns=max_columns,
        max_columns_preview=max_columns_preview,
        context_separator=context_separator or "--",
        field_match_separator=field_match_separator,
        field_context_separator=field_context_separator,
    )

    if quiet or check or stats_only:
        any_match = _search_quiet(
            files,
            Path.cwd(),
            compiled,
            pattern_str,
            pattern_flags,
            engine,
            fixed,
            effective_ignore_case,
            no_unicode,
            invert_match,
            multiline,
            encoding,
            text_mode,
            mmap_flag,
            mmap_threshold,
            buffer_size,
            threads,
        )
        if should_close:
            out_fh.close()
        exit_no_match = 1 if (check or exit_code or quiet) else 0
        raise SystemExit(0 if any_match else exit_no_match)

    t0 = time.monotonic()
    base_job = Job(
        path="",
        cwd=str(Path.cwd()),
        compiled=compiled,
        pattern_str=pattern_str,
        engine=engine,
        flags=pattern_flags,
        fixed=fixed,
        ignore_case=effective_ignore_case,
        no_unicode=no_unicode,
        invert=invert_match,
        before=before_context,
        after=after_context,
        max_count=max_count,
        multiline=multiline and not invert_match,
        encoding=encoding,
        replace=resolved_replace,
        only_matching=only_matching,
        crlf=crlf,
        literal_replace=literal_replace,
        preserve_case=preserve_case,
        case_mode=case_mode,
        text_mode=text_mode,
        use_mmap=mmap_flag,
        mmap_threshold=mmap_threshold,
        buffer_size=buffer_size,
        process_lines=process_lines,
        in_place=in_place,
        record_context=bool(json_out or json_seq or byte_offset or column),
        byte_offset=byte_offset,
        pre_cmd=pre_cmd,
        pre_glob=pre_glob,
    )
    jobs = [dc_replace(base_job, path=str(p)) for p in files]

    files_searched = 0
    files_matched = 0
    files_errored = 0
    total_matches = 0
    any_match = False
    results_buffer: list[FileResult] = []
    per_dir_stats: dict[str, dict[str, int]] = {}

    def _consume(results: Iterator[FileResult]) -> None:
        nonlocal files_searched, files_matched, files_errored, total_matches, any_match
        for result in results:
            if result.error:
                files_errored += 1
                if not no_messages:
                    print(f"{result.path}: {result.error}", file=sys.stderr)
                continue
            files_searched += 1
            dkey = str(Path(result.path).parent)
            per_dir_stats.setdefault(dkey, {"files": 0, "matched": 0, "matches": 0})
            per_dir_stats[dkey]["files"] += 1
            if in_place:
                results_buffer.append(result)
                if result.match_count:
                    any_match = True
                    files_matched += 1
                    total_matches += result.match_count
                    per_dir_stats[dkey]["matched"] += 1
                    per_dir_stats[dkey]["matches"] += result.match_count
                    if diff or dry_run:
                        old_text = result.old_text or ""
                        try:
                            new_text = (result.new_content or b"").decode(result.detected_encoding, errors="replace")
                        except Exception:
                            new_text = ""
                        sys.stdout.write(_unified_diff(Path(result.path), old_text, new_text))
                continue
            if result.match_count:
                any_match = True
                files_matched += 1
                total_matches += result.match_count
                per_dir_stats[dkey]["matched"] += 1
                per_dir_stats[dkey]["matches"] += result.match_count
                emit_result(result, render, out_fh)
            elif files_without_match:
                reset = ANSI_RESET if use_color else ""
                pcol = ANSI_FILENAME if use_color else ""
                sep_end = "\x00" if null_sep else "\n"
                out_fh.write(f"{pcol}{result.path}{reset}{sep_end}")

    try:
        if threads <= 1:
            _consume(worker(j) for j in jobs)
        else:
            with ProcessPoolExecutor(max_workers=threads) as ex:
                futures = [ex.submit(worker, j) for j in jobs]

                def _gen() -> Iterator[FileResult]:
                    for fut in as_completed(futures):
                        try:
                            yield fut.result()
                        except Exception as exc:
                            yield FileResult(path="<worker>", error=str(exc))

                _consume(_gen())
    except KeyboardInterrupt:
        print("\nSearch cancelled.", file=sys.stderr)
        if should_close:
            out_fh.close()
        raise SystemExit(130)
    finally:
        if should_close and not out_fh.closed:
            out_fh.close()
            if out_file:
                print(f"Results written to: {out_file}", file=sys.stderr)

    if in_place:
        cwd = Path.cwd()
        write_count = 0
        backup_suffix = backup if backup is not None else (".bak" if backup_dir else None)
        backup_dir_path: Path | None = Path(backup_dir) if backup_dir else None
        journal_path: Path | None = Path(undo_journal) if undo_journal else None
        applicable = [r for r in results_buffer if r.new_content is not None and r.match_count > 0]
        if dry_run or diff:
            pass
        else:
            confirmed = True
            if confirm and applicable:
                total_apply = sum(r.match_count for r in applicable)
                print(f"About to modify {len(applicable)} file(s), {total_apply} replacement(s).")
                answer = _prompt_yes_no("Proceed? [y/N] ")
                confirmed = answer in ("y", "yes")
            if confirmed:
                for r in applicable:
                    p = Path(r.path)
                    if not p.is_absolute():
                        p = cwd / p
                    choice = "y"
                    if prompt:
                        old_text = r.old_text or ""
                        try:
                            new_text = (r.new_content or b"").decode(r.detected_encoding, errors="replace")
                        except Exception:
                            new_text = ""
                        sys.stdout.write(_unified_diff(p, old_text, new_text))
                        choice = _prompt_yes_no(f"Apply changes to {r.path}? [y]es/[n]o/[a]ll/[q]uit: ")
                        if choice == "q":
                            break
                        if choice == "n":
                            continue
                        if choice == "a":
                            prompt = False
                    if backup_suffix is not None:
                        try:
                            old_bytes = p.read_bytes()
                            _backup_file(p, backup_suffix, backup_dir_path, cwd)
                        except OSError:
                            old_bytes = b""
                    else:
                        try:
                            old_bytes = p.read_bytes()
                        except OSError:
                            old_bytes = b""
                    if journal_path is not None:
                        _append_undo_journal(journal_path, p, old_bytes, r.new_content or b"", cwd)
                    if atomic_write:
                        try:
                            _atomic_write(p, r.new_content or b"")
                        except OSError as ex:
                            print(f"write failed for {p}: {ex}", file=sys.stderr)
                            files_errored += 1
                            continue
                    else:
                        try:
                            p.write_bytes(r.new_content or b"")
                        except OSError as ex:
                            print(f"write failed for {p}: {ex}", file=sys.stderr)
                            files_errored += 1
                            continue
                    write_count += 1
                print(f"Modified {write_count} file(s)", file=sys.stderr)

    elapsed = time.monotonic() - t0
    if stats > 0 or stats_only:
        parts = [
            f"{files_searched} files searched",
            f"{files_matched} matched",
            f"{total_matches} matches",
            f"{elapsed:.2f}s",
        ]
        if files_errored:
            parts.append(f"{files_errored} errors")
        print(", ".join(parts), file=sys.stderr)
        if stats >= 2:
            for d, s in sorted(per_dir_stats.items()):
                print(f"  {d}: {s['files']} files, {s['matched']} matched, {s['matches']} matches", file=sys.stderr)

    exit_no_match = 1 if (check or exit_code) else 0
    raise SystemExit(0 if any_match else exit_no_match)


def _build_pattern_source(
    *,
    pattern: str | None,
    regexp: str | None,
    pattern_file: str | None,
    fixed_strings: bool,
    word_regexp: bool,
    line_regexp: bool,
    smart_case: bool,
    ignore_case: bool,
    case_sensitive: bool,
    multiline: bool,
    engine: str,
    no_unicode: bool,
) -> tuple[str | None, str, bool, int]:
    if pattern_file is not None:
        pats = _read_pattern_file(pattern_file)
        if not pats:
            print(f"error: --pattern-file {pattern_file!r} contains no patterns", file=sys.stderr)
            raise SystemExit(2)
        if fixed_strings:
            combined = "|".join(re.escape(p) for p in pats)
        else:
            combined = "|".join(f"(?:{p})" for p in pats)
    else:
        pat = regexp or pattern
        if not pat:
            print("No pattern provided. Use positional PATTERN or -r/--regexp PATTERN.", file=sys.stderr)
            raise SystemExit(2)
        combined = pat

    effective_ignore_case = ignore_case
    if case_sensitive:
        effective_ignore_case = False
    elif smart_case:
        effective_ignore_case = not any(ch.isupper() for ch in combined)

    if fixed_strings:
        escaped = re.escape(combined)
        if word_regexp:
            escaped = r"\b" + escaped + r"\b"
        if line_regexp:
            escaped = "^(?:" + escaped + ")$"
        return (
            None,
            combined,
            effective_ignore_case,
            _engine_flags(engine, effective_ignore_case, False, no_unicode),
        )
    wrapped = combined
    if word_regexp:
        wrapped = r"\b(?:" + wrapped + r")\b"
    if line_regexp:
        wrapped = "^(?:" + wrapped + ")$"
    flags = _engine_flags(engine, effective_ignore_case, multiline, no_unicode)
    return wrapped, "", effective_ignore_case, flags


def main(argv: list[str] | None = None) -> int:
    """Entry point used by ``python -m pyrg`` and tests. Returns a process exit code."""
    old_argv = sys.argv[1:]
    if argv is not None:
        sys.argv[1:] = list(argv)
    try:
        parser = build_parser()
        raw = sys.argv[1:]
        if not raw:
            parser.print_help()
            return 0
        ns = parser.parse_args(raw)
        _pyrg_command(**vars(ns))
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv[1:] = old_argv


if __name__ == "__main__":
    sys.exit(main())
