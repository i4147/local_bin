#!/data/data/com.termux/files/usr/bin/env python
"""replacer-pure 2.1 - fast, safe find & replace over directory trees.

Pure-Python variant of replacer 2.1: NO compiled dependencies (no rignore
Rust wheel needed) - directory traversal uses only os.scandir().  Ideal
for environments where binary wheels are unavailable (termux, py3.14).

Rewrite of replacer 2.0 (typer) for CPython 3.10+ (primary target: termux, py3.14).

Changes vs 2.0
--------------
CLI
- Migrated typer -> argparse (stdlib only, no click/typer dependency).
- New flags: --no-py-check (disable the .py syntax guard),
  --no-fast-scan (disable the literal-bytes prefilter, for benchmarking).

Speed
- Traversal: pure-python `os.scandir` walker (iterative DFS, cycle-safe).
  Symlinks are skipped by default (opt in with --follow-symlinks or
  --symlink-policy follow|error). A built-in set of junk directories
  (.git, node_modules, __pycache__, ...) is pruned; extend it with
  --skip-dir NAME or disable it with --no-default-skip-dirs.
- Single read: each candidate file is read exactly once (2.0 stat'ed, then
  is_binary() re-read, then read_bytes() again). Binary sniff is now an
  in-memory NUL-byte check on the raw buffer.
- Prefilter ("fast scan"): when SEARCH is a literal ASCII string (the default
  non-regex mode), a byte-level `probe in raw` test runs before decoding and
  regex work. Files that cannot match are skipped without decoding. The probe
  is conservative: it is only used when it provably cannot produce false
  negatives (ASCII literal, no BOM/NUL in raw). `--no-fast-scan` disables it.
- subn() instead of finditer()+sub() in the non-interactive path (one scan
  instead of two).
- Parallelism: -j N>1 now uses a ProcessPoolExecutor (GIL-free), falling back
  to threads if process pools are unavailable (e.g. restricted termux/Android).
- The undo journal is written once at the end of the run instead of a
  read-modify-write of the whole JSON file per modified file (O(N) vs O(N^2)).
- cli output: mp mode uses executor.map, which also makes output order stable.

Safety
- .py/.pyi files are syntax-checked with ast.parse before being written: a
  replacement that turns valid Python into invalid Python is refused
  (reason=py_syntax). Only regressions are blocked - if the original file was
  already invalid the write is allowed.
- Atomic writes, backups and the undo journal are kept from 2.0. Journal now
  accepts both the old single-JSON format and newline-delimited JSON.

Fixes (bugs found in 2.0)
- 2.0 passed `count=rule.max_count or 0` to re.sub: max_count=0 meant
  "unlimited" (inherited re.sub semantics) - documented, kept.
- 2.0 counted files_skipped only for futures that had already completed in
  thread mode; every yielded file is now counted.
- 2.0's --jobs used a ThreadPoolExecutor, which gives no speedup for
  CPU-bound regex/decode work under the GIL; see ProcessPool above.
- 2.0 re-read every file for is_binary(); see single-read above.
- Options.apply existed but was never used beyond the mutual-exclusion check;
  kept for config compatibility.
"""

from __future__ import annotations
import argparse
import ast
from collections.abc import Callable, Iterator, Sequence
import concurrent.futures
import contextlib
from dataclasses import dataclass, field
import difflib
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, NoReturn, TypeAlias, TypedDict
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


# Optional third-party helpers ---------------------------------------------

# charset-normalizer: better encoding detection for non-utf8 files.
_detect_encoding_fn: Callable[[bytes], Any] | None
try:
    from charset_normalizer import from_bytes as _detect_encoding_fn
except ImportError:
    _detect_encoding_fn = None

# tomllib (3.11+) / tomli fallback for TOML configs and rules files.
try:
    import tomllib as _toml  # type: ignore[import-not-found]
except ImportError:
    try:
        import tomli as _toml
    except ImportError:
        _toml = None

# dh: the author's helper module. Only SKIP_DIRS is required; a default set
# keeps the tool standalone when dh is not installed.
_DEFAULT_SKIP_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    ".direnv",
    ".eggs",
    "build",
    "dist",
})
SKIP_DIRS: frozenset[str] = _DEFAULT_SKIP_DIRS
try:
    from dh import SKIP_DIRS as _dh_skip_dirs  # type: ignore[import-not-found]

    if _dh_skip_dirs:
        SKIP_DIRS = frozenset(_dh_skip_dirs)
except Exception:  # ImportError or anything broken in dh itself
    pass

EXIT_OK: int = 0
EXIT_NO_MATCH: int = 1
EXIT_USAGE: int = 2
EXIT_IO: int = 3
EXIT_ABORT: int = 4

UNDO_BLOB_DIR_SUFFIX: str = ".d"
DEFAULT_CONFIG_FILENAME: str = ".replacerrc"
DEFAULT_IGNORE_FILENAME: str = ".replacer_ignore"
VERSION: str = "replacer-pure 2.1"

PY_SUFFIXES: frozenset[str] = frozenset({".py", ".pyi"})

TYPE_MAP: dict[str, set[str]] = {
    "py": {".py", ".pyi"},
    "js": {".js", ".mjs", ".cjs", ".jsx"},
    "ts": {".ts", ".tsx"},
    "rs": {".rs"},
    "go": {".go"},
    "c": {".c", ".h"},
    "cpp": {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"},
    "java": {".java"},
    "kt": {".kt", ".kts"},
    "rb": {".rb"},
    "php": {".php"},
    "html": {".html", ".htm"},
    "css": {".css", ".scss", ".sass", ".less"},
    "md": {".md", ".markdown"},
    "json": {".json"},
    "yaml": {".yaml", ".yml"},
    "toml": {".toml"},
    "xml": {".xml", ".svg"},
    "sh": {".sh", ".bash", ".zsh"},
    "txt": {".txt", ""},
}

_color_lock: threading.Lock = threading.Lock()

# Shared state for interactive --prompt mode (single-threaded only).
PromptState: TypeAlias = dict[str, bool]

# (text, encoding, had_bom, raw_bytes) for a successfully decoded file.
ReadMeta: TypeAlias = "tuple[str, str, bool, bytes]"

WorkerArgs: TypeAlias = "tuple[str, list[Rule], Options, str, bytes | None, bool]"


class JournalEntry(TypedDict):
    """One undo record inside the journal file; blobs live beside it."""

    path: str
    blob: str
    old_hash: str
    new_hash: str
    ts: float


@dataclass
class Options:
    """Fully resolved run options (CLI + config file merged)."""

    search: str = ""
    replace: str = ""
    regex: bool = False
    ignore_case: bool = False
    smart_case: bool = False
    word: bool = False
    multiline: bool = False
    remove: bool = False
    dry_run: bool = False
    apply: bool = False
    files: list[str] = field(default_factory=list)
    globs: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    include_hidden: bool = True
    follow_symlinks: bool = False
    symlink_policy: str = "skip"
    max_depth: int | None = None
    max_filesize: int = 10 * 1024 * 1024
    types: list[str] = field(default_factory=list)
    ignore_file: str | None = None
    no_ignore: bool = False
    preserve_case: bool = False
    upper: bool = False
    lower: bool = False
    capitalize: bool = False
    env_expand: bool = False
    replace_from: str | None = None
    literal_replace: bool = False
    max_count: int | None = None
    prompt: bool = False
    backup: str | None = None
    backup_dir: str | None = None
    undo_journal: str | None = None
    undo: bool = False
    require_clean_git: bool = False
    confirm: bool = False
    diff: bool = False
    color: str = "auto"
    quiet: bool = False
    verbose: bool = False
    json_out: bool = False
    count: bool = False
    stats: bool = False
    files_with_matches: bool = False
    files_without_match: bool = False
    context: int = 20
    exit_code: bool = False
    check: bool = False
    jobs: int = 1
    rules_file: str | None = None
    files_from: str | None = None
    encoding: str | None = None
    no_py_check: bool = False
    no_fast_scan: bool = False
    skip_dirs: list[str] = field(default_factory=list)
    no_default_skip_dirs: bool = False


@dataclass
class Rule:
    """One search/replace rule (from CLI or from a --rules file)."""

    pattern: re.Pattern[str]
    replacement: str
    preserve_case: bool = False
    upper: bool = False
    lower: bool = False
    capitalize: bool = False
    max_count: int | None = None
    literal: bool = False


@dataclass
class FileResult:
    """Outcome of processing one file."""

    path: str
    matches: int = 0
    changed: bool = False
    skipped: bool = False
    reason: str | None = None
    diff: str | None = None
    undo_entry: JournalEntry | None = None


# ----------------------------------------------------------------- output ----

_ANSI_CODES: dict[str, str] = {
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "bold": "1",
}


def _colorize(text: str, color: str, when: str) -> str:
    """Wrap *text* in an ANSI color when *when* is auto tty / always."""
    if when == "never":
        return text
    if when == "auto" and (not sys.stdout.isatty() or os.environ.get("NO_COLOR")):
        return text
    c = _ANSI_CODES.get(color, "")
    if not c:
        return text
    return f"\x1b[{c}m{text}\x1b[0m"


def _safe_print(*a: Any, **k: Any) -> None:
    """print() under a lock so workers (threads) never interleave lines."""
    with _color_lock:
        print(*a, **k)


# ------------------------------------------------------------- replacement ---


def _apply_case(matched: str, replacement: str) -> str:
    """Mirror the case shape of *matched* onto *replacement*."""
    if not matched:
        return replacement
    if matched.isupper():
        return replacement.upper()
    if matched.islower():
        return replacement.lower()
    if matched[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _make_replacement_fn(rule: Rule) -> Callable[[re.Match[str]], str]:
    """Build the match->replacement callable honoring backrefs and casing."""

    def fn(m: re.Match[str]) -> str:
        text = rule.replacement
        if not rule.literal:
            with contextlib.suppress(re.error):
                text = m.expand(text)
        if rule.preserve_case:
            text = _apply_case(m.group(0), text)
        if rule.upper:
            text = text.upper()
        if rule.lower:
            text = text.lower()
        if rule.capitalize:
            text = text.capitalize()
        return text

    return fn


def _build_pattern(search_text: str, opts: Options) -> re.Pattern[str]:
    """Compile the SEARCH pattern honoring -i/-S/-w/-U and --regex."""
    flags: int = 0
    if opts.ignore_case or (opts.smart_case and not any(c.isupper() for c in search_text)):
        flags |= re.IGNORECASE
    if opts.multiline:
        flags |= re.MULTILINE | re.DOTALL
    src = search_text if opts.regex else re.escape(search_text)
    if opts.word:
        src = r"\b" + src + r"\b"
    return re.compile(src, flags)


# ------------------------------------------------------------- reading -------

_NULL_SCAN_LIMIT: int = 8192  # bytes inspected by the in-memory binary sniff


def _looks_binary(raw: bytes) -> bool:
    """True when the first 8 KiB contains a NUL byte (rg's own heuristic)."""
    return b"\x00" in raw[:_NULL_SCAN_LIMIT]


def _decode_meta(raw: bytes, encoding: str | None) -> ReadMeta | None:
    """Decode already-read bytes; return (text, encoding, had_bom, raw)."""
    bom = raw.startswith(b"\xef\xbb\xbf")
    enc: str | None = encoding
    if enc is None:
        if bom:
            enc = "utf-8-sig"
        else:
            try:
                raw.decode("utf-8")
                enc = "utf-8"
            except UnicodeDecodeError:
                if _detect_encoding_fn is not None:
                    best = _detect_encoding_fn(raw).best()
                    enc = best.encoding if best else "latin-1"
                else:
                    enc = "latin-1"
    try:
        text = raw.decode(enc)
    except (UnicodeDecodeError, LookupError):
        return None
    return text, enc, bom, raw


def _read_with_meta(path: Path, encoding: str | None) -> ReadMeta | None:
    """Read *path* once; return (text, encoding, had_bom, raw) or None."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return _decode_meta(raw, encoding)


def _fast_probe_is_safe(search_text: str, opts: Options, raw: bytes, enc: str | None) -> bytes | None:
    """Return a byte probe usable to prefilter, or None when unsafe.

    Only a plain (non-regex, non-word-boundary) search made of ASCII
    characters can be probed in raw bytes. We must also rule out encodings
    where the probe could appear inside another character's encoding
    (utf-16/32) and files that still contain a NUL byte (binary content).
    When None is returned the caller must fall back to full processing.
    """
    if opts.no_fast_scan or opts.regex or opts.word or opts.remove:
        return None
    if opts.ignore_case or opts.smart_case:
        return None
    if not search_text:
        return None
    if any(ord(c) > 127 for c in search_text):
        return None
    if enc is not None and not enc.lower().replace("-", "").startswith("utf8"):
        # non-utf-8 encodings (latin-1 etc.) are byte-transparent for ASCII
        # only when the file has no high-bit bytes that decode differently;
        # keep it simple and only trust utf-8/latin-1/ascii family.
        if enc.lower() not in ("latin-1", "iso-8859-1", "ascii", "us-ascii"):
            return None
    if b"\x00" in raw[:_NULL_SCAN_LIMIT]:
        return None
    return search_text.encode("ascii")


def _make_diff(path: Path, old: str, new: str, context: int) -> str:
    """Unified diff of one file's change (context lines scaled like 2.0)."""
    n_lines = max(0, context // 20)
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
            n=n_lines,
        )
    )


# ---------------------------------------------------------------- filters ----


def _file_allowed(
    path: Path,
    opts: Options,
    root: Path,
    *,
    st: os.stat_result | None = None,
    raw: bytes | None = None,
) -> bool:
    """Decide whether *path* may be processed, using data already in hand.

    Applies the symlink policy, regular-file check, max_filesize, hidden
    filter, globs, excludes and --type. *st*/*raw* let callers avoid
    re-statting / re-reading; the binary sniff uses *raw* when provided.
    """
    try:
        if path.is_symlink():
            if opts.symlink_policy == "skip":
                return False
            if opts.symlink_policy == "error":
                _safe_print(f"Error: symlink encountered: {path}", file=sys.stderr)
                return False
        if st is None:
            st = path.stat()
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    if st.st_size > opts.max_filesize:
        return False
    name = path.name
    if not opts.include_hidden and name.startswith("."):
        return False
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    spath = str(path)
    if opts.globs and not any(
        fnmatch.fnmatch(name, g) or fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(spath, g) for g in opts.globs
    ):
        return False
    if opts.excludes and any(
        fnmatch.fnmatch(name, e) or fnmatch.fnmatch(rel, e) or fnmatch.fnmatch(spath, e) for e in opts.excludes
    ):
        return False
    if opts.types:
        suffix = path.suffix
        if not any(suffix in TYPE_MAP.get(t, set()) for t in opts.types):
            return False
    if raw is not None:
        return not _looks_binary(raw)
    try:
        return not _looks_binary(path.read_bytes())
    except OSError:
        return False


def _load_ignore_patterns(opts: Options) -> list[str]:
    """Parse the ignore file (glob per line, '#' comments)."""
    if opts.no_ignore or not opts.ignore_file:
        return []
    p = Path(opts.ignore_file)
    if not p.is_file():
        return []
    out: list[str] = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _is_ignored(path: Path, patterns: list[str], root: Path) -> bool:
    """Last-wins glob matching with '!' negation, like a tiny .gitignore.

    Trailing slashes are stripped so gitignore-style directory patterns
    ('build/', 'node_modules/') match the directory name during descent;
    this also excludes a regular file of the same name, accepted as a
    close-enough approximation of gitignore semantics.
    """
    if not patterns:
        return False
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    ignored = False
    for pat in patterns:
        negate = pat.startswith("!")
        if negate:
            pat = pat[1:]
        pat = pat.rstrip("/")
        if pat and (fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(path.name, pat)):
            ignored = not negate
    return ignored


# ------------------------------------------------------------- traversal -----


def _walk_dir(root: Path, opts: Options, ignore_patterns: list[str], start_depth: int) -> Iterator[Path]:
    """Pure-python scandir traversal (iterative DFS, cycle-safe).

    Skips: built-in junk dirs (unless --no-default-skip-dirs), any names
    given via --skip-dir, dotfiles (unless --include-hidden), and, unless
    --follow-symlinks, symlinked directories (they fall through to the file
    branch and are rejected there by the symlink policy).
    """
    skip_dirs: frozenset[str] = (
        frozenset(opts.skip_dirs) if opts.no_default_skip_dirs else frozenset(SKIP_DIRS) | frozenset(opts.skip_dirs)
    )
    stack: list[tuple[Path, int]] = [(root, start_depth)]
    seen_dirs: set[str] = set()
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for entry in entries:
            if entry.name in skip_dirs:
                continue
            if not opts.include_hidden and entry.name.startswith("."):
                continue
            p = Path(entry.path)
            if _is_ignored(p, ignore_patterns, root):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=opts.follow_symlinks)
            except OSError:
                continue
            if is_dir:
                if opts.max_depth is not None and depth >= opts.max_depth:
                    continue
                try:
                    key = str(p.resolve())
                except OSError:
                    key = str(p)
                if key in seen_dirs:
                    continue
                seen_dirs.add(key)
                stack.append((p, depth + 1))
            else:
                yield p


def _iter_files(opts: Options) -> Iterator[Path]:
    """Yield candidate files from --files, --files-from, or the cwd walk."""
    root = Path.cwd()
    ignore_patterns = _load_ignore_patterns(opts)
    if opts.files_from:
        if opts.files_from == "-":
            for line in sys.stdin:
                line = line.rstrip("\n")
                if not line:
                    continue
                p = Path(line)
                if p.is_file() or p.is_symlink():
                    yield p
        else:
            try:
                fh = open(opts.files_from, encoding="utf-8")
            except OSError as exc:
                _safe_print(f"Cannot read files-from: {exc}", file=sys.stderr)
                return
            with fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    p = Path(line)
                    if p.is_file() or p.is_symlink():
                        yield p
        return
    if opts.files:
        for f in opts.files:
            if f == "-":
                continue
            p = Path(f)
            if p.is_file() or p.is_symlink():
                yield p
            elif p.is_dir():
                yield from _walk_dir(p, opts, ignore_patterns, 0)
            else:
                _safe_print(f"Skip (not found): {p}", file=sys.stderr)
        return
    yield from _walk_dir(root, opts, ignore_patterns, 0)


# ---------------------------------------------------------------- undo -------


def _append_journal_entries(journal_path: Path, entries: Sequence[JournalEntry]) -> bool:
    """Append undo records; writes once per run instead of per file."""
    if not entries:
        return True
    blob_dir = journal_path.with_name(journal_path.name + UNDO_BLOB_DIR_SUFFIX)
    try:
        blob_dir.mkdir(parents=True, exist_ok=True)
        for e in entries:
            blob_path = blob_dir / e["blob"]
            if not blob_path.exists():
                # blob written earlier by the worker; nothing to do
                pass
        existing: list[JournalEntry] = _read_journal(journal_path)
        all_entries: list[JournalEntry] = existing + list(entries)
        journal_path.write_text(json.dumps(all_entries, indent=2), encoding="utf-8")
    except OSError as exc:
        _safe_print(f"Journal write failed: {exc}", file=sys.stderr)
        return False
    return True


def _read_journal(journal_path: Path) -> list[JournalEntry]:
    """Read the journal (single JSON list, or newline-delimited JSON)."""
    if not journal_path.is_file():
        return []
    try:
        text = journal_path.read_text(encoding="utf-8").strip()
    except OSError:
        return []
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            out_json: list[JournalEntry] = [e for e in data if isinstance(e, dict)]  # type: ignore[misc]
            return out_json
        return []
    except json.JSONDecodeError:
        pass
    out: list[JournalEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            out.append(e)  # type: ignore[arg-type]
    return out


def _do_undo(journal_path: Path) -> int:
    """Restore every journaled file whose content still matches new_hash."""
    if not journal_path.is_file():
        _safe_print(f"No journal at {journal_path}", file=sys.stderr)
        return EXIT_IO
    entries = _read_journal(journal_path)
    if not entries:
        _safe_print(f"Empty journal at {journal_path}", file=sys.stderr)
        return EXIT_IO
    blob_dir = journal_path.with_name(journal_path.name + UNDO_BLOB_DIR_SUFFIX)
    restored = 0
    for e in reversed(entries):
        p = Path(e.get("path", ""))
        blob = blob_dir / e.get("blob", "")
        if not blob.is_file():
            _safe_print(f"Missing blob: {blob}", file=sys.stderr)
            continue
        try:
            new_hash = e.get("new_hash", "")
            if p.is_file():
                current = hashlib.sha256(p.read_bytes()).hexdigest()
                if current != new_hash:
                    _safe_print(f"Skip (modified since): {p}", file=sys.stderr)
                    continue
            _atomic_write(p, blob.read_bytes())
            restored += 1
        except OSError as exc:
            _safe_print(f"Restore failed {p}: {exc}", file=sys.stderr)
    _safe_print(f"Restored {restored} file(s)")
    return EXIT_OK


# ------------------------------------------------------- misc interactive ---


def _git_is_clean() -> bool:
    """True when `git status --porcelain` succeeds and is empty."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        return r.returncode == 0 and not r.stdout.strip()
    except (FileNotFoundError, OSError):
        return True


def _prompt_decision(path: Path, line_no: int, context: str) -> str:
    """Ask y/n/a/q for one match; returns the first letter of the answer."""
    while True:
        sys.stderr.write(f"\n{path}:{line_no}\n  {context}\nApply? [y]es/[n]o/[a]ll/[q]uit: ")
        sys.stderr.flush()
        ans = sys.stdin.readline().strip().lower()
        if ans in ("y", "yes", "n", "no", "a", "all", "q", "quit"):
            return ans[:1]
        if not ans:
            return "q"


def _line_of(text: str, pos: int) -> int:
    """1-based line number of byte offset *pos* in *text*."""
    return text.count("\n", 0, pos) + 1


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via tempfile+rename in the target directory (2.0 behavior)."""
    fd, tmp = tempfile.mkstemp(prefix=".replacer_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        with contextlib.suppress(OSError):
            shutil.copystat(path, tmp)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ------------------------------------------------------------ file worker ----

# Character classes whose insertion/removal can plausibly change Python
# syntax: string delimiters, brackets, statement separators, escapes, and
# comment starters. If neither the searched text nor any replacement
# involves these, an edit inside a token cannot break syntax structure.
_PY_RISKY: frozenset[str] = frozenset("\"'`#\\\n()[]{}:;=")


def _edit_syntactically_neutral(search: str, replacements: Sequence[str]) -> bool:
    """Heuristic: can this search/replace pair never alter .py syntax?

    True when neither side contains any syntax-significant character, the
    lengths' bracket balance cannot change, and no newline is involved, so
    the edit stays inside a single token on a single line. In that case the
    file's syntax cannot change and ast.parse can be skipped entirely.
    """
    if any(ch in _PY_RISKY for ch in search):
        return False
    for rep in replacements:
        if any(ch in _PY_RISKY for ch in rep):
            return False
    return True


def _py_check_ok(new_text: str, path: str) -> bool:
    """Return True if new_text is valid Python syntax.

    The guard's job is to block *regressions*: callers only refuse a write
    when the ORIGINAL parsed fine and the edited version does not. Files
    that were already broken stay allowed.
    """
    try:
        ast.parse(new_text)
        return True
    except SyntaxError:
        return False


def _process_one(
    path: Path,
    rules: Sequence[Rule],
    opts: Options,
    root: Path,
    prompt_state: PromptState,
    pre_meta: ReadMeta | None = None,
    pre_raw: bytes | None = None,
) -> FileResult:
    """Apply all rules to one file and (unless dry-run) write it back."""
    res = FileResult(path=str(path))
    if prompt_state.get("quit"):
        res.skipped = True
        res.reason = "prompt_quit"
        return res
    meta = pre_meta
    if meta is None:
        if pre_raw is not None:
            raw = pre_raw
        else:
            raw_read: bytes | None
            try:
                raw_read = path.read_bytes()
            except OSError:
                res.skipped = True
                res.reason = "unreadable"
                return res
            # Fast prefilter: for a plain ASCII literal search, a miss in the
            # raw bytes proves there is no match, so skip decoding entirely.
            probe = _fast_probe_is_safe(opts.search, opts, raw_read, None)
            if probe is not None and probe not in raw_read:
                res.skipped = True
                res.reason = "prefilter"
                return res
            raw = raw_read
        if pre_raw is not None:
            probe = _fast_probe_is_safe(opts.search, opts, pre_raw, None)
            if probe is not None and probe not in pre_raw:
                res.skipped = True
                res.reason = "prefilter"
                return res
        meta = _decode_meta(raw, opts.encoding)
        if meta is None:
            res.skipped = True
            res.reason = "unreadable"
            return res
    text, enc, _bom, raw = meta

    original_text = text
    total_matches = 0
    for rule in rules:
        fn = _make_replacement_fn(rule)
        if opts.prompt and not prompt_state.get("all"):
            matches = list(rule.pattern.finditer(text))
            if not matches:
                continue
            total_matches += len(matches)

            def rep(m: re.Match[str]) -> str:
                return "" if opts.remove else fn(m)

            chunks: list[str] = []
            last = 0
            quit_now = False
            auto_all = prompt_state.get("all", False)
            for m in matches:
                if auto_all:
                    chunks.append(text[last : m.start()])
                    chunks.append(rep(m))
                    last = m.end()
                    continue
                line = _line_of(text, m.start())
                s = max(0, m.start() - opts.context)
                e = min(len(text), m.end() + opts.context)
                ctx = text[s:e].replace("\n", " ").strip()
                ans = _prompt_decision(path, line, ctx)
                if ans == "q":
                    prompt_state["quit"] = True
                    quit_now = True
                    break
                if ans == "a":
                    prompt_state["all"] = True
                    auto_all = True
                if ans in ("y", "a"):
                    chunks.append(text[last : m.start()])
                    chunks.append(rep(m))
                    last = m.end()
            if quit_now:
                res.skipped = True
                res.reason = "prompt_quit"
                return res
            chunks.append(text[last:])
            text = "".join(chunks)
        else:
            if opts.remove:

                def rep_del(m: re.Match[str]) -> str:
                    return ""

                text, n = rule.pattern.subn(rep_del, text, count=rule.max_count or 0)
            else:
                text, n = rule.pattern.subn(fn, text, count=rule.max_count or 0)
            total_matches += n
    res.matches = total_matches

    if opts.check:
        return res
    if opts.files_with_matches:
        if total_matches and not opts.json_out:
            _safe_print(str(path))
        return res
    if opts.files_without_match:
        if not total_matches and not opts.json_out:
            _safe_print(str(path))
        return res
    if opts.count:
        if not opts.json_out:
            _safe_print(f"{path}:{total_matches}")
        return res
    if total_matches == 0 or text == original_text:
        res.skipped = True
        res.reason = "unchanged"
        return res
    res.changed = True

    if opts.diff:
        res.diff = _make_diff(path, original_text, text, opts.context)
        _safe_print(res.diff)
    if opts.dry_run:
        if not opts.quiet and not opts.diff and not opts.json_out:
            _safe_print(f"[DRY] {path}: {total_matches} match(es)")
        return res
    try:
        new_bytes = text.encode(enc)
    except (UnicodeEncodeError, LookupError) as exc:
        res.skipped = True
        res.reason = f"encode: {exc}"
        return res

    if (
        not opts.no_py_check
        and path.suffix in PY_SUFFIXES
        and not _edit_syntactically_neutral(opts.search, [r.replacement for r in rules if r.replacement])
    ):
        # The full check runs on the NEW text; the original is parsed only
        # when the new one failed, so the common path costs one parse at
        # most, and zero when the edit is syntactically neutral.
        if not _py_check_ok(text, str(path)) and _py_check_ok(original_text, str(path)):
            res.skipped = True
            res.reason = "py_syntax"
            if not opts.quiet:
                _safe_print(
                    _colorize(
                        f"Refusing to write {path}: result is invalid Python",
                        "red",
                        opts.color,
                    ),
                    file=sys.stderr,
                )
            return res

    if opts.backup:
        if opts.backup_dir:
            bdir = Path(opts.backup_dir)
            try:
                bdir.mkdir(parents=True, exist_ok=True)
                rel = path.relative_to(root)
            except (OSError, ValueError):
                rel = Path(path.name)
            bpath = bdir / rel
            bpath.parent.mkdir(parents=True, exist_ok=True)
            bpath.write_bytes(raw)
        else:
            path.with_name(path.name + opts.backup).write_bytes(raw)
    if opts.undo_journal:
        blob_dir = Path(opts.undo_journal).with_name(Path(opts.undo_journal).name + UNDO_BLOB_DIR_SUFFIX)
        try:
            blob_dir.mkdir(parents=True, exist_ok=True)
            blob_name = hashlib.sha256(str(path).encode() + b":" + str(time.time_ns()).encode()).hexdigest()[:16]
            (blob_dir / blob_name).write_bytes(raw)
            res.undo_entry = JournalEntry(
                path=str(path),
                blob=blob_name,
                old_hash=hashlib.sha256(raw).hexdigest(),
                new_hash=hashlib.sha256(new_bytes).hexdigest(),
                ts=time.time(),
            )
        except OSError as exc:
            _safe_print(f"Journal write failed for {path}: {exc}", file=sys.stderr)
    try:
        _atomic_write(path, new_bytes)
    except OSError as exc:
        res.skipped = True
        res.reason = f"write: {exc}"
        return res
    if not opts.quiet:
        _safe_print(f"Updated: {path}")
    return res


def _work(args: WorkerArgs) -> FileResult:
    """Top-level worker for the process pool (everything picklable).

    pre_raw is None for files the parent could not cheaply read; the worker
    then reads the file itself. It is never a bytes copy of the whole file:
    the parent only passes the raw buffer for files that PASSED the probe
    (a hit), which is exactly the set the worker would have to read anyway.
    """
    path_str, rules, opts, root_str, pre_raw, allowed = args
    path = Path(path_str)
    root = Path(root_str)
    prompt_state: PromptState = {}
    if not allowed:
        return FileResult(path=str(path), skipped=True, reason="filtered")
    return _process_one(path, rules, opts, root, prompt_state, pre_raw=pre_raw)


def _process_stdin(rules: Sequence[Rule], opts: Options) -> int:
    """Replace across stdin -> stdout (2.0 behavior, plus subn counting)."""
    text = sys.stdin.read()
    original = text
    for rule in rules:
        fn = _make_replacement_fn(rule)
        if opts.remove:

            def rep_del(m: re.Match[str]) -> str:
                return ""

            text = rule.pattern.subn(rep_del, text, count=rule.max_count or 0)[0]
        else:
            text = rule.pattern.subn(fn, text, count=rule.max_count or 0)[0]
    if opts.check:
        return EXIT_OK if text != original else EXIT_NO_MATCH
    sys.stdout.write(text)
    return EXIT_OK


# --------------------------------------------------------- config / rules ----


def _normalize_config(data: dict[str, Any]) -> dict[str, Any]:
    """Map hyphenated config keys onto Options' underscore fields."""
    return {k.replace("-", "_"): v for k, v in data.items()}


def _load_config(path: Path | None) -> dict[str, Any]:
    """Find and parse .replacerrc (toml or json), walking up from cwd."""
    if path is None:
        cwd = Path.cwd()
        for p in (cwd, *cwd.parents):
            f = p / DEFAULT_CONFIG_FILENAME
            if f.is_file():
                path = f
                break
    if path is None or not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if path.suffix in (".toml", "") and _toml is not None:
        try:
            return _normalize_config(_toml.loads(text))
        except Exception:
            pass
    try:
        return _normalize_config(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        return {}


def _load_rules_file(path: Path) -> list[Rule]:
    """Load a --rules file (toml/json; `rules` or `rule` list of dicts)."""
    text = path.read_text(encoding="utf-8")
    data: dict[str, Any] = {}
    if path.suffix in (".toml", "") and _toml is not None:
        try:
            data = _toml.loads(text)
        except Exception:
            data = {}
    if not data:
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = {}
    rules_data: Any = data.get("rules", data.get("rule", []))
    if isinstance(rules_data, dict):
        rules_data = [rules_data]
    out: list[Rule] = []
    if not isinstance(rules_data, list):
        return out
    for rd in rules_data:
        if not isinstance(rd, dict):
            continue
        search = rd.get("search") or rd.get("pattern")
        if not search:
            continue
        replacement = rd.get("replace", rd.get("replacement", ""))
        if not isinstance(replacement, str):
            replacement = str(replacement)
        regex_mode = rd.get("regex", True)
        flags: int = 0
        if rd.get("ignore_case"):
            flags |= re.IGNORECASE
        if rd.get("multiline"):
            flags |= re.MULTILINE | re.DOTALL
        src = str(search) if regex_mode else re.escape(str(search))
        if rd.get("word"):
            src = r"\b" + src + r"\b"
        try:
            pattern = re.compile(src, flags)
        except re.error:
            _safe_print(f"Bad rule pattern: {src!r}", file=sys.stderr)
            continue
        mc = rd.get("max_count")
        out.append(
            Rule(
                pattern=pattern,
                replacement=replacement,
                preserve_case=bool(rd.get("preserve_case", False)),
                upper=bool(rd.get("upper", False)),
                lower=bool(rd.get("lower", False)),
                capitalize=bool(rd.get("capitalize", False)),
                max_count=int(mc) if mc is not None else None,
                literal=bool(rd.get("literal_replace", False)),
            )
        )
    return out


def _build_rules(opts: Options) -> list[Rule]:
    """Build the rule list from --rules or from the CLI search/replace."""
    if opts.rules_file:
        return _load_rules_file(Path(opts.rules_file))
    replacement = opts.replace
    if opts.remove:
        replacement = ""
    if opts.replace_from:
        try:
            replacement = Path(opts.replace_from).read_text(encoding="utf-8")
        except OSError as exc:
            _fail(f"cannot read --replace-from: {exc}", EXIT_USAGE)
    if opts.env_expand:
        replacement = re.sub(
            r"\$(?:(\w+)|\{([^}]+)\})",
            lambda m: os.environ.get(m.group(1) or m.group(2), m.group(0)),
            replacement,
        )
    try:
        pattern = _build_pattern(opts.search, opts)
    except re.error as exc:
        _fail(f"invalid pattern: {exc}", EXIT_USAGE)
    return [
        Rule(
            pattern=pattern,
            replacement=replacement,
            preserve_case=opts.preserve_case,
            upper=opts.upper,
            lower=opts.lower,
            capitalize=opts.capitalize,
            max_count=opts.max_count,
            literal=opts.literal_replace,
        )
    ]


# ------------------------------------------------------------------- cli -----


def _fail(msg: str, code: int = EXIT_USAGE) -> NoReturn:
    """Print an error and exit with *code* (replaces typer's Exit)."""
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _build_parser() -> argparse.ArgumentParser:
    """argparse port of the 2.0 typer CLI (same flags and semantics)."""
    p = argparse.ArgumentParser(
        prog="replacer",
        description="Fast, safe find & replace over directory trees.",
        allow_abbrev=False,
    )
    p.add_argument("search", nargs="?", default="", help="Text to search for")
    p.add_argument("replace", nargs="?", default="", help="Replacement text (defaults to empty)")
    p.add_argument("-r", "--remove", action="store_true", help="Remove the search text")
    p.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    p.add_argument("--apply", action="store_true", help="Invert --dry-run default")
    p.add_argument(
        "-f",
        "--file",
        dest="files",
        action="append",
        default=[],
        metavar="FILE",
        help="Process only these files (repeatable)",
    )
    p.add_argument("--files-from", default=None, help="Read file list from a file or '-'")
    p.add_argument(
        "-g",
        "--glob",
        dest="globs",
        action="append",
        default=[],
        metavar="GLOB",
        help="Include files matching glob (repeatable)",
    )
    p.add_argument(
        "--exclude",
        dest="excludes",
        action="append",
        default=[],
        metavar="GLOB",
        help="Exclude files matching glob (repeatable)",
    )
    p.add_argument(
        "--type",
        dest="types",
        action="append",
        default=[],
        metavar="TYPE",
        help=f"Only process files of these types ({', '.join(sorted(TYPE_MAP))})",
    )
    p.add_argument(
        "--include-hidden",
        dest="include_hidden",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Include dotfiles (--no-include-hidden to skip them)",
    )
    p.add_argument(
        "--follow-symlinks",
        dest="follow_symlinks",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Follow symlinks (--no-follow-symlinks to skip them)",
    )
    p.add_argument("--symlink-policy", default=None, help="skip|follow|error")
    p.add_argument("--max-depth", type=int, default=None, help="Maximum directory depth")
    p.add_argument(
        "--max-filesize",
        type=int,
        default=None,
        metavar="BYTES",
        help="Skip files larger than N bytes",
    )
    p.add_argument("--ignore-file", default=None, help="Path to .ignore file")
    p.add_argument("--no-ignore", action="store_true", help=f"Disable {DEFAULT_IGNORE_FILENAME}")
    p.add_argument(
        "--skip-dir",
        dest="skip_dirs",
        action="append",
        default=[],
        metavar="NAME",
        help="Additionally skip directories with this name anywhere in the tree "
        "(repeatable; combines with the built-in set)",
    )
    p.add_argument(
        "--no-default-skip-dirs",
        action="store_true",
        help=f"Do not skip the built-in directory set ({', '.join(sorted(_DEFAULT_SKIP_DIRS)[:6])}, ...)",
    )
    p.add_argument(
        "-F",
        "--fixed-strings",
        action="store_true",
        help="Literal string search (default)",
    )
    p.add_argument("-E", "--regex", action="store_true", help="Treat SEARCH as regex")
    p.add_argument("-i", "--ignore-case", action="store_true", help="Case-insensitive")
    p.add_argument("-s", "--case-sensitive", action="store_true", help="Explicit case-sensitive")
    p.add_argument(
        "-S",
        "--smart-case",
        action="store_true",
        help="Smart-case (insensitive unless uppercase)",
    )
    p.add_argument("-w", "--word", action="store_true", help="Word-boundary matching")
    p.add_argument("-U", "--multiline", action="store_true", help="Multiline mode")
    p.add_argument("--preserve-case", action="store_true", help="Preserve matched text case")
    p.add_argument("--upper", action="store_true", help="Uppercase replacement")
    p.add_argument("--lower", action="store_true", help="Lowercase replacement")
    p.add_argument("--capitalize", action="store_true", help="Capitalize replacement")
    p.add_argument(
        "--env",
        dest="env_expand",
        action="store_true",
        help="Expand $VAR in replacement",
    )
    p.add_argument("--replace-from", default=None, help="Read replacement from file")
    p.add_argument("--literal-replace", action="store_true", help="Don't expand backreferences")
    p.add_argument("--max-count", type=int, default=None, help="Max replacements per file")
    p.add_argument(
        "--rules",
        dest="rules_file",
        default=None,
        help="Load rules from TOML/JSON file",
    )
    p.add_argument("-p", "--prompt", action="store_true", help="Interactive y/n/a/q per match")
    p.add_argument(
        "--backup",
        default=None,
        metavar="SUFFIX",
        help="Create backup with given suffix (e.g. .bak)",
    )
    p.add_argument("--backup-dir", default=None, help="Backup directory (implies --backup)")
    p.add_argument("--undo-journal", default=None, help="Journal path for --undo")
    p.add_argument("--undo", action="store_true", help="Undo from journal")
    p.add_argument("--require-clean-git", action="store_true", help="Refuse if git dirty")
    p.add_argument("--confirm", action="store_true", help="Prompt before applying")
    p.add_argument("--diff", action="store_true", help="Show unified diff (implies --dry-run)")
    p.add_argument("--color", default=None, help="auto|always|never")
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress non-error output")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    p.add_argument("--json", dest="json_out", action="store_true", help="JSON output")
    p.add_argument("-c", "--count", action="store_true", help="Print match count per file")
    p.add_argument("--stats", action="store_true", help="Show statistics")
    p.add_argument("--context", type=int, default=None, help="Context chars for previews")
    p.add_argument("--exit-code", action="store_true", help="Exit 1 if no matches/changes")
    p.add_argument("--check", action="store_true", help="Exit 0 if matches, 1 otherwise")
    p.add_argument(
        "-l",
        "--files-with-matches",
        action="store_true",
        help="Only print paths with matches",
    )
    p.add_argument(
        "-L",
        "--files-without-match",
        action="store_true",
        help="Only print paths without matches",
    )
    p.add_argument("-j", "--jobs", type=int, default=None, help="Parallel workers")
    p.add_argument("--config", default=None, help="Path to config file")
    p.add_argument("--encoding", default=None, help="Force file encoding")
    p.add_argument(
        "--no-py-check",
        action="store_true",
        help="Disable the .py syntax guard for .py/.pyi files",
    )
    p.add_argument(
        "--no-fast-scan",
        action="store_true",
        help="Disable the literal-bytes prefilter (debugging/benchmarking)",
    )
    p.add_argument("--version", action="version", version=VERSION, help="Show version and exit")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = _build_parser().parse_args(argv)

    if args.fixed_strings and args.regex:
        _fail("--fixed-strings and --regex are mutually exclusive")
    if sum([args.ignore_case, args.case_sensitive, args.smart_case]) > 1:
        _fail("-i/--ignore-case, -s/--case-sensitive, -S/--smart-case are mutually exclusive")
    if args.files_with_matches and args.files_without_match:
        _fail("-l/--files-with-matches and -L/--files-without-match are mutually exclusive")
    if args.dry_run and args.apply:
        _fail("--dry-run and --apply are mutually exclusive")

    cfg: dict[str, Any] = _load_config(Path(args.config) if args.config else None)

    def _b(val: bool, key: str) -> bool:
        return True if val else bool(cfg.get(key, False))

    def _tb(val: bool | None, key: str, default: bool) -> bool:
        if val is not None:
            return val
        if key in cfg:
            return bool(cfg[key])
        return default

    def _s(val: str | None, key: str, default: str | None = None) -> str | None:
        return val if val is not None else cfg.get(key, default)

    def _i(val: int | None, key: str, default: int | None = None) -> int | None:
        return val if val is not None else cfg.get(key, default)

    def _l(val: list[str], key: str) -> list[str]:
        return val if val else list(cfg.get(key, []))

    effective_undo_journal = _s(args.undo_journal, "undo_journal")
    if args.undo:
        if not effective_undo_journal:
            _fail("--undo requires --undo-journal")
        return _do_undo(Path(effective_undo_journal))

    effective_color = _s(args.color, "color", "auto") or "auto"
    if effective_color not in ("auto", "always", "never"):
        _fail(f"invalid --color value: {effective_color}")

    effective_symlink_policy = _s(args.symlink_policy, "symlink_policy", "skip") or "skip"
    if effective_symlink_policy not in ("skip", "follow", "error"):
        _fail(f"invalid --symlink-policy value: {effective_symlink_policy}")

    effective_follow = _tb(args.follow_symlinks, "follow_symlinks", False)
    if effective_follow or effective_symlink_policy == "follow":
        effective_follow = True
        effective_symlink_policy = "follow"

    effective_backup = _s(args.backup, "backup")
    effective_backup_dir = _s(args.backup_dir, "backup_dir")
    if effective_backup_dir and not effective_backup:
        effective_backup = ".bak"

    effective_ignore_file = _s(args.ignore_file, "ignore_file")
    if not effective_ignore_file and not args.no_ignore and not cfg.get("no_ignore", False):
        default_ignore = Path(DEFAULT_IGNORE_FILENAME)
        if default_ignore.is_file():
            effective_ignore_file = str(default_ignore)

    effective_jobs = _i(args.jobs, "jobs", 1) or 1
    effective_jobs = max(effective_jobs, 1)
    effective_prompt = _b(args.prompt, "prompt")
    if effective_prompt and effective_jobs > 1:
        print("--prompt with --jobs>1 unsupported; forcing jobs=1", file=sys.stderr)
        effective_jobs = 1

    effective_dry_run = (args.dry_run and not args.apply) or (bool(cfg.get("dry_run", False)) and not args.apply)
    if args.diff:
        effective_dry_run = True

    effective_include_hidden = _tb(args.include_hidden, "include_hidden", True)
    effective_max_filesize = _i(args.max_filesize, "max_filesize", 10 * 1024 * 1024) or 10 * 1024 * 1024
    effective_context = _i(args.context, "context", 20) or 20

    effective_rules_file = _s(args.rules_file, "rules_file")
    effective_search = args.search or cfg.get("search", "")
    if not effective_search and not effective_rules_file:
        _fail("provide SEARCH or --rules")

    opts = Options(
        search=effective_search,
        replace=args.replace or cfg.get("replace", ""),
        regex=args.regex or cfg.get("regex", False),
        ignore_case=args.ignore_case or cfg.get("ignore_case", False),
        smart_case=args.smart_case or cfg.get("smart_case", False),
        word=_b(args.word, "word"),
        multiline=_b(args.multiline, "multiline"),
        remove=_b(args.remove, "remove"),
        dry_run=effective_dry_run,
        apply=args.apply,
        files=_l(args.files, "files"),
        globs=_l(args.globs, "globs"),
        excludes=_l(args.excludes, "excludes"),
        include_hidden=effective_include_hidden,
        follow_symlinks=effective_follow,
        symlink_policy=effective_symlink_policy,
        max_depth=_i(args.max_depth, "max_depth"),
        max_filesize=effective_max_filesize,
        types=_l(args.types, "types"),
        ignore_file=effective_ignore_file,
        no_ignore=_b(args.no_ignore, "no_ignore"),
        preserve_case=_b(args.preserve_case, "preserve_case"),
        upper=_b(args.upper, "upper"),
        lower=_b(args.lower, "lower"),
        capitalize=_b(args.capitalize, "capitalize"),
        env_expand=_b(args.env_expand, "env_expand"),
        replace_from=_s(args.replace_from, "replace_from"),
        literal_replace=_b(args.literal_replace, "literal_replace"),
        max_count=_i(args.max_count, "max_count"),
        prompt=effective_prompt,
        backup=effective_backup,
        backup_dir=effective_backup_dir,
        undo_journal=effective_undo_journal,
        undo=args.undo,
        require_clean_git=_b(args.require_clean_git, "require_clean_git"),
        confirm=_b(args.confirm, "confirm"),
        diff=_b(args.diff, "diff"),
        color=effective_color,
        quiet=_b(args.quiet, "quiet"),
        verbose=_b(args.verbose, "verbose"),
        json_out=_b(args.json_out, "json_out"),
        count=_b(args.count, "count"),
        stats=_b(args.stats, "stats"),
        files_with_matches=args.files_with_matches,
        files_without_match=args.files_without_match,
        context=effective_context,
        exit_code=_b(args.exit_code, "exit_code"),
        check=_b(args.check, "check"),
        jobs=effective_jobs,
        rules_file=effective_rules_file,
        files_from=_s(args.files_from, "files_from"),
        encoding=_s(args.encoding, "encoding"),
        no_py_check=_b(args.no_py_check, "no_py_check"),
        no_fast_scan=_b(args.no_fast_scan, "no_fast_scan"),
        skip_dirs=_l(args.skip_dirs, "skip_dirs"),
        no_default_skip_dirs=_b(args.no_default_skip_dirs, "no_default_skip_dirs"),
    )

    if opts.require_clean_git and not _git_is_clean():
        print("Working tree is not clean; aborting", file=sys.stderr)
        return EXIT_ABORT

    rules = _build_rules(opts)
    if not rules:
        print("No rules to apply", file=sys.stderr)
        return EXIT_USAGE

    if opts.files == ["-"]:
        return _process_stdin(rules, opts)

    if opts.confirm and not opts.dry_run:
        print("Proceed with modifications? [y/N] ", end="", file=sys.stderr, flush=True)
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            return EXIT_ABORT

    if opts.dry_run and not opts.json_out:
        _safe_print(_colorize("--- DRY RUN (no files will be modified) ---", "yellow", opts.color))

    root = Path.cwd()
    results: list[FileResult] = []
    prompt_state: PromptState = {"all": False, "quit": False}
    t0 = time.perf_counter()
    files_scanned = 0
    files_skipped = 0

    def _record(r: FileResult) -> None:
        nonlocal files_scanned, files_skipped
        results.append(r)
        files_scanned += 1
        if r.skipped:
            files_skipped += 1

    def _work_serial(p: Path) -> FileResult:
        if not _file_allowed(p, opts, root):
            return FileResult(path=str(p), skipped=True, reason="filtered")
        return _process_one(p, rules, opts, root, prompt_state)

    if opts.jobs > 1:
        # Parent-side fast scan: for a provably-safe literal probe the parent
        # reads each file once; misses are recorded directly (never decoded,
        # never sent to a worker), hits go to the pool WITH their raw bytes so
        # workers do not re-read from disk.
        use_prefilter = (
            not opts.no_fast_scan
            and not opts.regex
            and not opts.word
            and not opts.remove
            and not opts.ignore_case
            and not opts.smart_case
            and opts.search.isascii()
            and opts.search != ""
        )

        wargs: list[WorkerArgs] = []
        for p in _iter_files(opts):
            allowed = _file_allowed(p, opts, root)
            if not allowed:
                _record(FileResult(path=str(p), skipped=True, reason="filtered"))
                continue
            pre_raw: bytes | None = None
            if use_prefilter:
                try:
                    raw = p.read_bytes()
                except OSError:
                    _record(FileResult(path=str(p), skipped=True, reason="unreadable"))
                    continue
                if _looks_binary(raw):
                    _record(FileResult(path=str(p), skipped=True, reason="binary"))
                    continue
                if opts.search.encode("ascii") not in raw:
                    _record(FileResult(path=str(p), skipped=True, reason="prefilter"))
                    continue
                pre_raw = raw
            wargs.append((str(p), rules, opts, str(root), pre_raw, True))
        try:
            with concurrent.futures.ProcessPoolExecutor(max_workers=opts.jobs) as ex:
                for r in ex.map(_work, wargs, chunksize=8):
                    _record(r)
        except (OSError, ImportError, ValueError) as exc:
            # process pools are unavailable (restricted sandbox / termux);
            # fall back to threads, which still help on I/O-bound trees.
            print(f"process pool unavailable ({exc}); using threads", file=sys.stderr)
            with concurrent.futures.ThreadPoolExecutor(max_workers=opts.jobs) as ex_t:
                futures = [ex_t.submit(_work, a) for a in wargs]
                for fut in concurrent.futures.as_completed(futures):
                    try:
                        r = fut.result()
                    except Exception as exc2:  # noqa: BLE001 - report, don't die
                        idx = futures.index(fut)
                        r = FileResult(path=wargs[idx][0], skipped=True, reason=f"error: {exc2}")
                    _record(r)
    else:
        for p in _iter_files(opts):
            r = _work_serial(p)
            _record(r)
            if opts.verbose and r.skipped and r.reason:
                _safe_print(
                    _colorize(f"skip {p}: {r.reason}", "yellow", opts.color),
                    file=sys.stderr,
                )

    if opts.undo_journal:
        _append_journal_entries(
            Path(opts.undo_journal),
            [r.undo_entry for r in results if r.undo_entry is not None],
        )

    changed = [r for r in results if r.changed]
    matched = [r for r in results if r.matches > 0]
    elapsed = time.perf_counter() - t0

    if opts.json_out:
        _safe_print(
            json.dumps(
                {
                    "files_scanned": files_scanned,
                    "files_skipped": files_skipped,
                    "files_changed": len(changed),
                    "files_matched": len(matched),
                    "elapsed_seconds": round(elapsed, 3),
                    "results": [
                        {
                            "path": r.path,
                            "matches": r.matches,
                            "changed": r.changed,
                            "skipped": r.skipped,
                            "reason": r.reason,
                            "diff": r.diff,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )

    if opts.stats and not opts.json_out:
        _safe_print(
            _colorize(
                f"\nStats: scanned={files_scanned} skipped={files_skipped} "
                f"matched={len(matched)} changed={len(changed)} elapsed={elapsed:.2f}s",
                "cyan",
                opts.color,
            )
        )

    if opts.check:
        return EXIT_OK if matched else EXIT_NO_MATCH
    if opts.exit_code:
        if opts.dry_run:
            return EXIT_OK if matched else EXIT_NO_MATCH
        return EXIT_OK if changed else EXIT_NO_MATCH
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
