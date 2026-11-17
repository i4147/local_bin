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

import bisect
import fnmatch
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from collections import deque
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Pattern, Match, TextIO

import typer

try:
    from loguru import logger as _loguru_logger  # type: ignore[import-not-found]

    logger = _loguru_logger
    HAS_LOGURU = True
except Exception:  # pragma: no cover - fallback when loguru is unavailable
    HAS_LOGURU = False
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s: %(message)s")

    class _StubLogger:
        """Minimal loguru-compatible shim."""

        def __init__(self) -> None:
            self._log = logging.getLogger("pyrg")

        def add(self, *args: Any, **kwargs: Any) -> int:
            return 0

        def remove(self, *args: Any, **kwargs: Any) -> None:
            return None

        def debug(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._log.debug(str(message), *args)

        def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._log.info(str(message), *args)

        def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._log.warning(str(message), *args)

        def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._log.error(str(message), *args)

        def exception(self, message: Any, *args: Any, **kwargs: Any) -> None:
            self._log.exception(str(message), *args)

    logger = _StubLogger()


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

__version__: str = "0.3.0"

#: Default worker count for parallel searches.
MAX_WORKERS: int = min(64, max(1, (os.cpu_count() or 2)) + 4)

#: Directory names always skipped during recursion.
SKIP_DIRS: frozenset[str] = frozenset(
    {
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
    }
)

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


def _setup_logging(log_path_override: Optional[str] = None, level: str = "DEBUG") -> None:
    """Configure loguru (or the stdlib shim): a debug log file plus stderr fallback."""
    if HAS_LOGURU:
        logger.remove()
        if log_path_override is not None and log_path_override == "":
            logger.add(sys.stderr, level=level if level != "DEBUG" else "WARNING", format="{message}")
            return
        path = _LOG_PATH_DEFAULT if log_path_override is None else Path(log_path_override)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            logger.add(str(path), level=level, encoding="utf-8", rotation="10 MB", retention=5)
        except OSError:
            logger.add(sys.stderr, level="WARNING", format="{message}")
    else:
        logging.basicConfig(
            stream=sys.stderr,
            level=getattr(logging, level.upper(), logging.WARNING),
            format="%(levelname)s: %(message)s",
        )


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


def parse_extension_args(raw_exts: Optional[list[str]]) -> set[str]:
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


def resolve_type_presets(types: Optional[list[str]]) -> set[str]:
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
                typer.echo(f"unknown type preset {key!r}; known presets: {known}", err=True)
                raise typer.Exit(2)
            out |= TYPE_PRESETS[key]
    return out


def _resolve_type_not(types: Optional[list[str]]) -> set[str]:
    out: set[str] = set()
    if not types:
        return out
    for raw in types:
        for piece in raw.split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if key not in TYPE_PRESETS:
                typer.echo(f"unknown type preset for --type-not: {key!r}", err=True)
                raise typer.Exit(2)
            out |= TYPE_PRESETS[key]
    return out


def _apply_type_add(spec: str) -> None:
    if ":" not in spec:
        typer.echo(f"invalid --type-add spec {spec!r}; expected NAME:EXT[,EXT...]", err=True)
        raise typer.Exit(2)
    name, exts = spec.split(":", 1)
    key = name.strip().lower()
    if not key:
        typer.echo("invalid --type-add: empty name", err=True)
        raise typer.Exit(2)
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

    def __init__(self, extra_files: Optional[list[Path]] = None) -> None:
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
    path: str
    line: int
    column: int
    byte_offset: int
    match: str
    replacement: Optional[str]
    line_text: str
    context_before: list[str]
    context_after: list[str]
    is_context: bool


@dataclass(slots=True)
class FileResult:
    path: str
    groups: list[list[OutputLine]] = field(default_factory=list)
    match_count: int = 0
    error: Optional[str] = None
    records: list[MatchRecord] = field(default_factory=list)
    new_content: Optional[bytes] = None
    old_text: Optional[str] = None
    had_bom: bool = False
    detected_encoding: str = "utf-8"
    line_ending: str = "\n"
    replacement_count: int = 0
    hit_lines: list[int] = field(default_factory=list)


@dataclass(slots=True)
class RenderOptions:
    show_path_only: bool = False
    show_count: bool = False
    as_json: bool = False
    as_json_seq: bool = False
    show_line_number: bool = True
    color: bool = True
    heading: bool = True
    show_filename_override: Optional[bool] = None
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
    max_depth: Optional[int] = None
    follow_symlinks: bool = False
    exclude_dirs: set[str] = field(default_factory=set)
    ignore_matcher: Optional[IgnoreMatcher] = None
    search_hidden: bool = False


@dataclass(slots=True)
class Job:
    path: str
    cwd: str
    compiled: Optional[Pattern[str]]
    pattern_str: Optional[str]
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
    replace: Optional[str]
    only_matching: bool
    crlf: bool
    literal_replace: bool
    preserve_case: bool
    case_mode: Optional[str]
    text_mode: bool
    use_mmap: bool
    mmap_threshold: int
    buffer_size: int
    process_lines: bool
    in_place: bool
    record_context: bool
    byte_offset: bool
    pre_cmd: Optional[str]
    pre_glob: Optional[str]


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


def matches_any_glob(
    path: Path, patterns: list[str], ignore_case: bool = False
) -> bool:
    """Return ``True`` if ``path`` matches any of ``patterns`` (name or full)."""
    name = path.name
    s = str(path)
    if ignore_case:
        s_l = s.lower()
        name_l = name.lower()
        return any(
            fnmatch.fnmatch(s_l, p.lower()) or fnmatch.fnmatch(name_l, p.lower())
            for p in patterns
        )
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
    max_depth: Optional[int],
    follow_symlinks: bool,
    ignore_matcher: Optional[IgnoreMatcher],
    iglob: Optional[list[str]] = None,
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
        if exclude_globs and matches_any_glob(p, exclude_globs, glob_case_insensitive):
            return False
        return True

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


def _git_tracked_paths(cwd: Path) -> Optional[set[Path]]:
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
    extensions: Optional[list[str]],
    types: Optional[list[str]],
    type_not: Optional[list[str]],
    globs: Optional[list[str]],
    iglob: Optional[list[str]],
    glob_case_insensitive: bool,
    excludes: Optional[list[str]],
    exclude_dirs: Optional[list[str]],
    hidden: bool,
    max_size: int,
    max_depth: Optional[int],
    follow: bool,
    no_ignore: bool,
    ignore_files: Optional[list[str]] = None,
    files_from: Optional[str] = None,
    require_git: bool = False,
) -> list[Path]:
    exts = parse_extension_args(extensions) | resolve_type_presets(types)
    excluded_exts = _resolve_type_not(type_not)
    extra_ignore_paths: list[Path] = []
    if ignore_files:
        for p in ignore_files:
            fp = Path(p)
            if not fp.is_file():
                typer.echo(f"error: --ignore-file {p!r} not found", err=True)
                raise typer.Exit(3)
            extra_ignore_paths.append(fp)
    matcher: Optional[IgnoreMatcher] = None
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
            typer.echo("error: --require-git requires a git repo with git installed", err=True)
            raise typer.Exit(3)
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
        typer.echo(f"error: cannot read {path_str!r}: {ex}", err=True)
        raise typer.Exit(3)


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


def _open_output(path: Optional[str]) -> tuple[TextIO, bool]:
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


def _load_config(path: Optional[str], no_config: bool) -> dict[str, Any]:
    if no_config:
        return {}
    target: Optional[Path] = None
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


def _env_str(name: str) -> Optional[str]:
    v = os.environ.get(name)
    if v is None or v == "":
        return None
    return v


def _env_int(name: str) -> Optional[int]:
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
            typer.echo(
                "error: --engine regex requires the 'regex' module (pip install regex)",
                err=True,
            )
            raise typer.Exit(3)
        return "regex"
    return "re"


def _engine_flags(engine: str, ignore_case: bool, multiline_dotall: bool, no_unicode: bool) -> int:
    try:
        import regex as _regex_mod  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        _regex_mod = None
    if engine == "regex":
        flags = _regex_mod.MULTILINE  # type: ignore[union-attr]
        if ignore_case:
            flags |= _regex_mod.IGNORECASE  # type: ignore[union-attr]
        if multiline_dotall:
            flags |= _regex_mod.DOTALL  # type: ignore[union-attr]
        if no_unicode:
            flags |= _regex_mod.ASCII  # type: ignore[union-attr]
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
    compiled: Optional[_Pattern],
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
    compiled: Optional[_Pattern],
    fixed: str,
    ignore_case: bool,
    no_unicode: bool,
    invert: bool,
    context_before: int,
    context_after: int,
    max_count: int,
    replace: Optional[str],
    only_matching: bool,
    crlf: bool,
    literal_replace: bool,
    preserve_case: bool,
    case_mode: Optional[str],
    record_context: bool,
    byte_tracker: Optional[list[int]] = None,
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
                    replacement_text: Optional[str] = None
                    if replace is not None:
                        if compiled is not None and not literal_replace:
                            m_obj = next(
                                (
                                    mm
                                    for mm in compiled.finditer(line)
                                    if mm.start() == s and mm.end() == e
                                ),
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
                            (
                                mm
                                for mm in compiled.finditer(line)
                                if mm.start() == s and mm.end() == e
                            ),
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
    stop_event: Optional[Any] = None,
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
    compiled: Optional[_Pattern],
    fixed: str,
    ignore_case: bool,
    no_unicode: bool,
    invert: bool,
    context_before: int,
    context_after: int,
    max_count: int,
    replace: Optional[str],
    encoding: str,
    buffer_size: int,
    crlf: bool,
    literal_replace: bool,
    preserve_case: bool,
    case_mode: Optional[str],
    stop_event: Optional[Any] = None,
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
                    match_lines[lineno] = (_replace_fixed(line, fixed, replace, ignore_case), [])
            else:
                match_lines[lineno] = (line, spans)
            if spans:
                for s, e in spans:
                    mtext = line[s:e]
                    rtext: Optional[str] = None
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
    replace: Optional[str],
    literal_replace: bool,
    preserve_case: bool,
    case_mode: Optional[str],
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
        replacement_text: Optional[str] = None
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
        with path.open(encoding=effective_encoding, errors="replace", newline="", buffering=buffer_size) as fh:
            text = fh.read()
    if text.startswith("\ufeff"):
        text = text[1:]
    line_ending = _detect_line_ending(text)
    return text, had_bom, effective_encoding, line_ending


def _apply_replacement_to_text(
    text: str,
    compiled: Optional[_Pattern],
    fixed: str,
    ignore_case: bool,
    replace_template: str,
    literal_replace: bool,
    preserve_case: bool,
    case_mode: Optional[str],
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
        byte_tracker: Optional[list[int]] = None
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
    reset = ANSI_RESET if opts.color else ""
    path_col = ANSI_FILENAME if opts.color else ""
    sep_end = "\x00" if opts.null_sep else "\n"
    if opts.as_json or opts.as_json_seq:
        for rec in result.records:
            out_fh.write(_render_json_record(rec, opts.as_json_seq) + "\n")
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


def _backup_file(path: Path, suffix: str, backup_dir: Optional[Path], cwd: Path) -> None:
    try:
        data = path.read_bytes()
    except OSError as ex:
        typer.echo(f"backup failed for {path}: {ex}", err=True)
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
            target.write_bytes(data)
        except OSError as ex:
            typer.echo(f"backup failed for {path}: {ex}", err=True)
    else:
        target = path.with_name(path.name + suffix)
        try:
            target.write_bytes(data)
        except OSError as ex:
            typer.echo(f"backup failed for {path}: {ex}", err=True)


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
        blob_path.write_bytes(old_bytes)
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
        typer.echo(f"error: undo journal {jpath} not found", err=True)
        raise typer.Exit(3)
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
            typer.echo(f"skip {path}: hash mismatch", err=True)
            continue
        try:
            data = blob.read_bytes()
        except OSError:
            typer.echo(f"skip {path}: blob missing", err=True)
            continue
        try:
            _atomic_write(path, data)
            restored += 1
        except OSError as ex:
            typer.echo(f"undo failed for {path}: {ex}", err=True)
    typer.echo(f"Restored {restored} file(s)")
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


_COMPLETIONS_BASH = """_pyrg_completion() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local opts="--help --version --files --files-with-matches --files-without-match --count --quiet --json --json-seq --in-place --dry-run --diff --backup --undo --undo-journal --replace --pattern-file --type --type-add --type-not --type-list --ext --glob --iglob --exclude-dir --max-depth --max-filesize --max-count --context --before-context --after-context --word-regexp --line-regexp --multiline --fixed-strings --ignore-case --smart-case --case-sensitive --pcre2 --engine --no-unicode --crlf --column --byte-offset --null --passthru --pretty --max-columns --max-columns-preview --stats --stats-only --check --exit-code --threads --no-parallel --mmap --no-mmap --buffer-size --process-lines --sort --sortr --generate --config --no-config --debug --trace --log-file --no-messages --legacy-flags"
    COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
}
complete -F _pyrg_completion pyrg
"""

_COMPLETIONS_ZSH = """#compdef pyrg
_pyrg() {
    local -a opts
    opts=(--help --version --files --in-place --dry-run --diff --json --json-seq --type --glob --type-add --type-not --type-list --ext --iglob --exclude-dir --max-depth --max-count --context --word-regexp --line-regexp --multiline --ignore-case --smart-case --case-sensitive --pcre2 --engine --sort --sortr --generate --config --no-config --debug --trace)
    _arguments '*: :(${opts})'
}
_pyrg "$@"
"""

_COMPLETIONS_FISH = """complete -c pyrg -l help -d 'Show help'
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

_COMPLETIONS_POWERSHELL = """Register-ArgumentCompleter -Native -CommandName 'pyrg' -ScriptBlock {
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
        typer.echo(f"error: unknown shell {shell!r}; expected bash, zsh, fish, powershell", err=True)
        return 2
    sys.stdout.write(script)
    return 0


# --------------------------------------------------------------------------- #
# Quiet / early-exit search
# --------------------------------------------------------------------------- #


def _search_quiet(
    files: list[Path],
    cwd: Path,
    compiled: Optional[_Pattern],
    pattern_str: Optional[str],
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
    base_job = {
        "path": "",
        "cwd": str(cwd),
        "compiled": compiled,
        "pattern_str": pattern_str,
        "engine": engine,
        "flags": flags,
        "fixed": fixed,
        "ignore_case": ignore_case,
        "no_unicode": no_unicode,
        "invert": invert_match,
        "before": 0,
        "after": 0,
        "max_count": 1,
        "multiline": multiline and not invert_match,
        "encoding": encoding,
        "replace": None,
        "only_matching": False,
        "crlf": False,
        "literal_replace": False,
        "preserve_case": False,
        "case_mode": None,
        "text_mode": text_mode,
        "use_mmap": use_mmap,
        "mmap_threshold": mmap_threshold,
        "buffer_size": buffer_size,
        "process_lines": False,
        "in_place": False,
        "record_context": False,
        "byte_offset": False,
        "pre_cmd": None,
        "pre_glob": None,
    }
    jobs = [Job(**{**base_job, "path": str(p)}) for p in files]
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

_app = typer.Typer(
    name="pyrg",
    help=(
        "pyrg — a ripgrep-like recursive regex search tool.\n\n"
        "Search files for a regular expression (or fixed string) with support for "
        "case folding, inversion, multiline patterns, context lines, only-matching "
        "output, in-place replacement, glob/extension/type filtering, gitignore-style "
        "ignore files, JSON output, counting, quiet mode, and parallel processing."
    ),
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode="rich",
)


@_app.command()
def _pyrg_command(
    pattern: Optional[str] = typer.Argument(
        None, help="Regex pattern (or use -r/--regexp). Required unless --files/--type-list/--generate/--version/--undo."
    ),
    paths: Optional[list[str]] = typer.Argument(
        None, help="Files or directories to search (default: current directory)."
    ),
    regexp: Optional[str] = typer.Option(
        None, "-r", "--regexp", help="Pattern (alternative to positional PATTERN)."
    ),
    e_opt: Optional[str] = typer.Option(
        None, "-e", help="Alias for -r/--regexp (extension mode under --legacy-flags)."
    ),
    pattern_file: Optional[str] = typer.Option(
        None, "-f", "--pattern-file", help="Read patterns from PATH (one per line)."
    ),
    extensions: Optional[list[str]] = typer.Option(
        None, "--ext", help="Only search files with given extension(s) (repeatable, comma ok)."
    ),
    types: Optional[list[str]] = typer.Option(
        None, "-t", "--type", help="Only search files matching a type preset (repeatable)."
    ),
    type_not: Optional[list[str]] = typer.Option(
        None, "-T", "--type-not", help="Exclude files of a given type preset (repeatable)."
    ),
    type_add: Optional[list[str]] = typer.Option(
        None, "--type-add", help="Register preset NAME:EXT[,EXT...] (repeatable)."
    ),
    type_clear: Optional[list[str]] = typer.Option(
        None, "--type-clear", help="Remove preset NAME (repeatable)."
    ),
    type_list: bool = typer.Option(False, "--type-list", help="Print known presets and exit."),
    ignore_case: bool = typer.Option(False, "-i", "--ignore-case", help="Case-insensitive matching."),
    smart_case: bool = typer.Option(False, "-S", "--smart-case", help="Case-insensitive unless pattern has uppercase."),
    case_sensitive: bool = typer.Option(False, "-s", "--case-sensitive", help="Force case-sensitive matching."),
    fixed_strings: bool = typer.Option(False, "-F", "--fixed-strings", help="Treat PATTERN as a literal string."),
    word_regexp: bool = typer.Option(False, "-w", "--word-regexp", help="Match whole words only."),
    line_regexp_x: bool = typer.Option(False, "--line-regexp", help="Match whole lines only."),
    invert_match: bool = typer.Option(False, "-v", "--invert-match", help="Show non-matching lines."),
    multiline: bool = typer.Option(False, "-U", "--multiline", help="Allow pattern to span lines (DOTALL)."),
    no_unicode: bool = typer.Option(False, "--no-unicode", help="Disable Unicode (re.ASCII)."),
    crlf: bool = typer.Option(False, "--crlf", help="Strip CR before matching; re-append on write."),
    pcre2: bool = typer.Option(False, "-P", "--pcre2", help="Use the 'regex' module (alias for --engine regex)."),
    engine: str = typer.Option("re", "--engine", help="Regex engine: 're' (default) or 'regex'."),
    only_matching: bool = typer.Option(False, "-o", "--only-matching", help="Print only matched parts."),
    replace: Optional[str] = typer.Option(
        None, "--replace", help="Replacement template (\\1 backreferences)."
    ),
    literal_replace: bool = typer.Option(False, "--literal-replace", help="Do not expand backreferences in replacement."),
    replace_from: Optional[str] = typer.Option(
        None, "--replace-from", help="Read replacement template from PATH (-: stdin)."
    ),
    line_number_flag: bool = typer.Option(True, "-n", "--line-number", help="Show line numbers (default on)."),
    no_line_number: bool = typer.Option(False, "--no-line-number", help="Hide line numbers."),
    files_with_matches: bool = typer.Option(False, "-l", "--files-with-matches", help="Print only paths with matches."),
    files_without_match: bool = typer.Option(
        False, "-L", "--files-without-match", help="Print only paths without matches."
    ),
    count: bool = typer.Option(False, "-c", "--count", help="Print match count per file."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress output; exit status indicates match."),
    check: bool = typer.Option(False, "--check", help="Exit 0 on match, 1 on none, 2 on error."),
    exit_code: bool = typer.Option(False, "--exit-code", help="Exit 1 when no matches found."),
    json_out: bool = typer.Option(False, "--json", help="Emit one JSON object per match."),
    json_seq: bool = typer.Option(False, "--json-seq", help="Emit one JSON array per match."),
    heading_flag: bool = typer.Option(False, "--heading", help="Force headings above each file's matches."),
    no_heading: bool = typer.Option(False, "--no-heading", help="Suppress headings."),
    with_filename: bool = typer.Option(False, "-H", "--with-filename", help="Force filename in output."),
    no_filename: bool = typer.Option(False, "--no-filename", help="Suppress filename in output."),
    column: bool = typer.Option(False, "--column", help="Print 1-based column of first match."),
    byte_offset: bool = typer.Option(False, "-b", "--byte-offset", help="Print byte offset of match."),
    null_sep: bool = typer.Option(False, "-0", "--null", help="NUL after paths in -l/-L/-c/--files."),
    passthru: bool = typer.Option(False, "--passthru", help="Print non-matching lines alongside matches."),
    pretty: bool = typer.Option(False, "-p", "--pretty", help="Shorthand for --color always --heading --line-number."),
    max_columns: int = typer.Option(0, "--max-columns", help="Truncate printed lines to N characters."),
    max_columns_preview: bool = typer.Option(False, "--max-columns-preview", help="Show truncation preview."),
    stats_only: bool = typer.Option(False, "--stats-only", help="Print only stats summary (implies -q)."),
    context_separator: Optional[str] = typer.Option(
        None, "--context-separator", help="Separator between context groups (default: --)."
    ),
    field_match_separator: str = typer.Option(
        ":", "--field-match-separator", help="Separator between path/line/text for matches."
    ),
    field_context_separator: str = typer.Option(
        "-", "--field-context-separator", help="Separator for context lines."
    ),
    after_context: int = typer.Option(0, "-A", "--after-context", help="Show N lines after each match."),
    before_context: int = typer.Option(0, "-B", "--before-context", help="Show N lines before each match."),
    context: Optional[int] = typer.Option(None, "-C", "--context", help="Shorthand for -A N -B N."),
    hidden: bool = typer.Option(False, "--hidden", help="Search hidden files/dirs."),
    unrestricted: int = typer.Option(
        0, "-u", "--unrestricted", count=True, help="Reduce filtering: -u no-ignore, -uu +hidden, -uuu +text."
    ),
    text_flag: bool = typer.Option(False, "-a", "--text", help="Treat binary files as text."),
    binary_flag: bool = typer.Option(False, "--binary", help="Search binary files, escape NULs."),
    require_git: bool = typer.Option(False, "--require-git", help="Only search files tracked by git."),
    require_clean_git: bool = typer.Option(
        False, "--require-clean-git", help="Refuse if git working tree is dirty."
    ),
    glob_patterns: Optional[list[str]] = typer.Option(
        None, "-g", "--glob", help="Only search paths matching GLOB (repeatable)."
    ),
    iglob: Optional[list[str]] = typer.Option(
        None, "--iglob", help="Case-insensitive include glob (repeatable)."
    ),
    glob_case_insensitive: bool = typer.Option(
        False, "--glob-case-insensitive", help="Make -g/--iglob/--exclude-dir case-insensitive."
    ),
    exclude: Optional[list[str]] = typer.Option(
        None, "--exclude", help="Exclude paths matching GLOB (repeatable)."
    ),
    exclude_dirs: Optional[list[str]] = typer.Option(
        None, "--exclude-dir", help="Exclude directory NAME (repeatable)."
    ),
    max_depth: Optional[int] = typer.Option(None, "--max-depth", help="Maximum directory depth."),
    max_filesize: int = typer.Option(
        10_000_000, "--max-filesize", help="Skip files larger than BYTES."
    ),
    max_count: int = typer.Option(0, "-m", "-M", "--max-count", help="Stop after N matching lines per file."),
    no_ignore: bool = typer.Option(False, "--no-ignore", help="Do not honour .gitignore / .ignore."),
    ignore_file: Optional[list[str]] = typer.Option(
        None, "--ignore-file", help="Additional ignore file (repeatable)."
    ),
    follow: bool = typer.Option(False, "--follow", help="Follow symlinks (with cycle detection)."),
    files_from: Optional[str] = typer.Option(None, "--files-from", help="Read file list from PATH (-: stdin)."),
    encoding: str = typer.Option("utf-8", "--encoding", help="Text encoding to use."),
    list_files: bool = typer.Option(False, "--files", help="List files that would be searched, then exit."),
    sort: str = typer.Option("none", "--sort", help="Sort: path|modified|accessed|created|none."),
    sortr: bool = typer.Option(False, "--sortr", help="Reverse sort order."),
    pre_cmd: Optional[str] = typer.Option(
        None, "--pre", help="Pipe each file through CMD and search its stdout."
    ),
    pre_glob: Optional[str] = typer.Option(
        None, "--pre-glob", help="Only preprocess files matching GLOB."
    ),
    threads: int = typer.Option(MAX_WORKERS, "-j", "--threads", help="Worker count."),
    no_parallel: bool = typer.Option(False, "--no-parallel", help="Force single-threaded execution."),
    mmap_flag: bool = typer.Option(True, "--mmap/--no-mmap", help="Use mmap for large files (default on)."),
    mmap_threshold: int = typer.Option(
        1_048_576, "--mmap-threshold", help="mmap files >= BYTES."
    ),
    buffer_size: int = typer.Option(65_536, "--buffer-size", help="Buffered reader size."),
    process_lines: bool = typer.Option(False, "--process-lines", help="Stream lines instead of reading whole file."),
    regex_size_limit: int = typer.Option(
        10_000_000, "--regex-size-limit", help="Regex size limit (engine=regex only)."
    ),
    dfa_size_limit: int = typer.Option(
        10_000_000, "--dfa-size-limit", help="DFA size limit (engine=regex only)."
    ),
    in_place: bool = typer.Option(False, "-W", "--in-place", help="Modify files in place (requires --replace)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing (implies --diff)."),
    diff: bool = typer.Option(False, "--diff", help="Print unified diff (implies --dry-run)."),
    backup: Optional[str] = typer.Option(None, "--backup", help="Save backup with SUFFIX (default .bak)."),
    backup_dir: Optional[str] = typer.Option(None, "--backup-dir", help="Mirror backups into DIR."),
    undo_journal: Optional[str] = typer.Option(None, "--undo-journal", help="JSON undo journal path."),
    undo: bool = typer.Option(False, "--undo", help="Replay --undo-journal in reverse."),
    prompt: bool = typer.Option(False, "--prompt", help="Interactive per-file [y]/[n]/[a]/[q] prompt."),
    confirm: bool = typer.Option(False, "--confirm", help="Ask global [y/N] confirmation before writing."),
    atomic_write: bool = typer.Option(
        True, "--atomic-write/--no-atomic-write", help="Atomic writes (default on for --in-place)."
    ),
    preserve_case: bool = typer.Option(False, "--preserve-case", help="Preserve match case in replacement."),
    upper: bool = typer.Option(False, "--upper", help="Uppercase the replacement."),
    lower: bool = typer.Option(False, "--lower", help="Lowercase the replacement."),
    capitalize: bool = typer.Option(False, "--capitalize", help="Capitalize the replacement."),
    color: str = typer.Option("auto", "--color", help="Colour output: auto|always|never."),
    no_color: bool = typer.Option(False, "--no-color", help="Alias for --color never."),
    out_file: Optional[str] = typer.Option(None, "--out-file", help="Write results to FILE."),
    stats: int = typer.Option(0, "--stats", count=True, help="Print summary to stderr (repeat for more detail)."),
    no_messages: bool = typer.Option(
        False, "--no-messages", help="Suppress per-file error reporting and no-match message."
    ),
    legacy_flags: bool = typer.Option(False, "--legacy-flags", help="Restore old short-flag meanings (-e/-x/-L)."),
    config_path: Optional[str] = typer.Option(None, "--config", help="Load defaults from TOML/JSON config."),
    no_config: bool = typer.Option(False, "--no-config", help="Disable config auto-discovery."),
    debug_flag: bool = typer.Option(False, "--debug", help="Enable DEBUG logging (stderr)."),
    trace_flag: bool = typer.Option(False, "--trace", help="Enable TRACE logging (stderr)."),
    log_file: Optional[str] = typer.Option(None, "--log-file", help="Override log file path ('' to disable)."),
    version_flag: bool = typer.Option(False, "--version", help="Print version and exit."),
    generate: Optional[str] = typer.Option(
        None, "--generate", help="Emit shell completions for bash|zsh|fish|powershell."
    ),
) -> None:
    """Search FILES for PATTERN.

    Recursively search files (default: current directory) for a regular
    expression (or fixed string with -F). By default, matching lines are
    printed with the file path, line number, and highlighted match.

    Use --help to see all options. The tool exits 0 if any match is found,
    1 if none, and 2 on usage/error.
    """
    if version_flag:
        typer.echo(f"pyrg {__version__}")
        raise typer.Exit(0)
    if generate is not None:
        raise typer.Exit(_emit_completion(generate))
    if type_list:
        for name in sorted(TYPE_PRESETS):
            exts = " ".join(sorted(TYPE_PRESETS[name]))
            typer.echo(f"{name}: {exts}")
        raise typer.Exit(0)

    cfg = _load_config(config_path or _env_str("PYRG_CONFIG"), no_config)
    if log_file is not None:
        _setup_logging(log_file, "DEBUG" if not (debug_flag or trace_flag) else ("TRACE" if trace_flag else "DEBUG"))
    elif debug_flag or trace_flag:
        logger.remove() if HAS_LOGURU else None
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
        typer.echo(f"invalid --color value: {effective_color}; expected auto, always, never", err=True)
        raise typer.Exit(2)
    if pretty:
        effective_color = "always"
        heading_flag = True
    if sort not in ("path", "modified", "accessed", "created", "none"):
        typer.echo(f"invalid --sort value: {sort}; expected path|modified|accessed|created|none", err=True)
        raise typer.Exit(2)
    if heading_flag and no_heading:
        typer.echo("--heading and --no-heading are mutually exclusive", err=True)
        raise typer.Exit(2)
    if with_filename and no_filename:
        typer.echo("-H/--with-filename and --no-filename are mutually exclusive", err=True)
        raise typer.Exit(2)
    if context is not None:
        after_context = context
        before_context = context
    if line_number_flag and no_line_number:
        typer.echo("error: -n and --no-line-number are mutually exclusive", err=True)
        raise typer.Exit(2)
    if files_with_matches and files_without_match:
        typer.echo("error: -l/--files-with-matches and -L/--files-without-match are mutually exclusive", err=True)
        raise typer.Exit(2)
    if ignore_case and smart_case:
        typer.echo("error: -i/--ignore-case and -S/--smart-case are mutually exclusive", err=True)
        raise typer.Exit(2)
    if smart_case and case_sensitive:
        typer.echo("error: -S/--smart-case and -s/--case-sensitive are mutually exclusive", err=True)
        raise typer.Exit(2)
    if ignore_case and case_sensitive:
        typer.echo("error: -i/--ignore-case and -s/--case-sensitive are mutually exclusive", err=True)
        raise typer.Exit(2)
    if replace is not None and replace_from is not None:
        typer.echo("error: --replace and --replace-from are mutually exclusive", err=True)
        raise typer.Exit(2)
    if passthru and (files_with_matches or files_without_match or count or quiet or json_out or json_seq):
        typer.echo("error: --passthru is mutually exclusive with -l/-L/-c/-q/--json", err=True)
        raise typer.Exit(2)
    if in_place:
        if replace is None and replace_from is None:
            typer.echo("error: -W/--in-place requires --replace or --replace-from", err=True)
            raise typer.Exit(2)
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
            typer.echo(
                "error: --in-place is mutually exclusive with -l/-L/-c/-q/--json/--passthru/--files/--files-from",
                err=True,
            )
            raise typer.Exit(2)
        if prompt and threads > 1:
            typer.echo("error: --prompt requires -j 1", err=True)
            raise typer.Exit(2)
    if context_separator is not None and json_out:
        typer.echo("error: --context-separator is mutually exclusive with --json", err=True)
        raise typer.Exit(2)
    if pattern_file is not None and (regexp is not None or e_opt is not None):
        typer.echo("error: -f/--pattern-file is mutually exclusive with -r/--regexp", err=True)
        raise typer.Exit(2)
    if pattern_file is not None and fixed_strings and replace is not None:
        typer.echo("error: -f + -F + --replace is not supported (backreferences ambiguous)", err=True)
        raise typer.Exit(2)
    if multiline and invert_match:
        logger.warning("--multiline ignored with --invert-match")
        multiline = False
    if sum([preserve_case, upper, lower, capitalize]) > 1:
        typer.echo("error: --preserve-case/--upper/--lower/--capitalize are mutually exclusive", err=True)
        raise typer.Exit(2)
    case_mode: Optional[str] = None
    if upper:
        case_mode = "upper"
    elif lower:
        case_mode = "lower"
    elif capitalize:
        case_mode = "capitalize"

    if undo:
        if not undo_journal:
            typer.echo("error: --undo requires --undo-journal", err=True)
            raise typer.Exit(2)
        raise typer.Exit(_undo_from_journal(Path(undo_journal), Path.cwd()))

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
        typer.echo("error: --require-clean-git: git working tree is dirty", err=True)
        raise typer.Exit(3)

    if no_line_number:
        line_number_flag = False
    else:
        line_number_flag = True

    effective_paths: list[str] = list(paths) if paths else ["."]

    legacy = legacy_flags
    if not legacy and e_opt is not None:
        typer.echo(
            "note: -e is --regexp (alias for -r). Use --ext for extensions; --legacy-flags restores old behaviour.",
            err=True,
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

    resolved_replace: Optional[str] = replace
    if replace_from is not None:
        resolved_replace = _read_replace_from(replace_from)

    compiled: Optional[_Pattern] = None
    pattern_str: Optional[str] = None
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
                typer.echo(f"Invalid regex: {ex}", err=True)
                raise typer.Exit(2)

    eff_sort = sort
    if sortr and sort != "none":
        eff_sort = f"{sort}_r"

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
            attr = {"modified": "st_mtime", "accessed": "st_atime", "created": "st_ctime"}[sort]
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
        raise typer.Exit(0)

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
            typer.echo("pyrg: no files matched the search filters", err=True)
        raise typer.Exit(1)

    out_fh, should_close = _open_output(out_file)
    use_color = _should_color(effective_color, out_fh is sys.stdout) and not json_out and not json_seq
    effective_heading = heading_flag if heading_flag else (False if no_heading else None)
    if effective_heading is None:
        effective_heading = use_color and not files_from and with_filename
    if pretty:
        effective_heading = True

    show_filename_override: Optional[bool] = None
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
        raise typer.Exit(0 if any_match else exit_no_match)

    t0 = time.monotonic()
    base_job = {
        "cwd": str(Path.cwd()),
        "compiled": compiled,
        "pattern_str": pattern_str,
        "engine": engine,
        "flags": pattern_flags,
        "fixed": fixed,
        "ignore_case": effective_ignore_case,
        "no_unicode": no_unicode,
        "invert": invert_match,
        "before": before_context,
        "after": after_context,
        "max_count": max_count,
        "multiline": multiline and not invert_match,
        "encoding": encoding,
        "replace": resolved_replace,
        "only_matching": only_matching,
        "crlf": crlf,
        "literal_replace": literal_replace,
        "preserve_case": preserve_case,
        "case_mode": case_mode,
        "text_mode": text_mode,
        "use_mmap": mmap_flag,
        "mmap_threshold": mmap_threshold,
        "buffer_size": buffer_size,
        "process_lines": process_lines,
        "in_place": in_place,
        "record_context": bool(json_out or json_seq or byte_offset or column),
        "byte_offset": byte_offset,
        "pre_cmd": pre_cmd,
        "pre_glob": pre_glob,
    }
    jobs = [Job(**{**base_job, "path": str(p)}) for p in files]

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
        typer.echo("\nSearch cancelled.", err=True)
        if should_close:
            out_fh.close()
        raise typer.Exit(130)
    finally:
        if should_close and not out_fh.closed:
            out_fh.close()
            if out_file:
                typer.echo(f"Results written to: {out_file}", err=True)

    if in_place:
        cwd = Path.cwd()
        write_count = 0
        backup_suffix = backup if backup is not None else (".bak" if backup_dir else None)
        backup_dir_path: Optional[Path] = Path(backup_dir) if backup_dir else None
        journal_path: Optional[Path] = Path(undo_journal) if undo_journal else None
        applicable = [r for r in results_buffer if r.new_content is not None and r.match_count > 0]
        if dry_run or diff:
            pass
        else:
            confirmed = True
            if confirm and applicable:
                total_apply = sum(r.match_count for r in applicable)
                typer.echo(f"About to modify {len(applicable)} file(s), {total_apply} replacement(s).")
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
                            typer.echo(f"write failed for {p}: {ex}", err=True)
                            files_errored += 1
                            continue
                    else:
                        try:
                            p.write_bytes(r.new_content or b"")
                        except OSError as ex:
                            typer.echo(f"write failed for {p}: {ex}", err=True)
                            files_errored += 1
                            continue
                    write_count += 1
                typer.echo(f"Modified {write_count} file(s)", err=True)

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
        typer.echo(", ".join(parts), err=True)
        if stats >= 2:
            for d, s in sorted(per_dir_stats.items()):
                typer.echo(
                    f"  {d}: {s['files']} files, {s['matched']} matched, {s['matches']} matches",
                    err=True,
                )

    exit_no_match = 1 if (check or exit_code) else 0
    raise typer.Exit(0 if any_match else exit_no_match)


def _build_pattern_source(
    *,
    pattern: Optional[str],
    regexp: Optional[str],
    pattern_file: Optional[str],
    fixed_strings: bool,
    word_regexp: bool,
    line_regexp: bool,
    smart_case: bool,
    ignore_case: bool,
    case_sensitive: bool,
    multiline: bool,
    engine: str,
    no_unicode: bool,
) -> tuple[Optional[str], str, bool, int]:
    if pattern_file is not None:
        pats = _read_pattern_file(pattern_file)
        if not pats:
            typer.echo(f"error: --pattern-file {pattern_file!r} contains no patterns", err=True)
            raise typer.Exit(2)
        if fixed_strings:
            combined = "|".join(re.escape(p) for p in pats)
        else:
            combined = "|".join(f"(?:{p})" for p in pats)
    else:
        pat = regexp or pattern
        if not pat:
            typer.echo(
                "No pattern provided. Use positional PATTERN or -r/--regexp PATTERN.",
                err=True,
            )
            raise typer.Exit(2)
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
        return None, combined, effective_ignore_case, _engine_flags(engine, effective_ignore_case, False, no_unicode)
    wrapped = combined
    if word_regexp:
        wrapped = r"\b(?:" + wrapped + r")\b"
    if line_regexp:
        wrapped = "^(?:" + wrapped + ")$"
    flags = _engine_flags(engine, effective_ignore_case, multiline, no_unicode)
    return wrapped, "", effective_ignore_case, flags


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point used by ``python -m pyrg`` and tests. Returns a process exit code."""
    old_argv = sys.argv[1:]
    if argv is not None:
        sys.argv[1:] = list(argv)
    try:
        _app()
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv[1:] = old_argv


if __name__ == "__main__":
    sys.exit(main())
