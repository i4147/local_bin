#!/data/data/com.termux/files/usr/bin/env python
"""
fileview — page through files with head-by-default display.

Highlights
----------
* Head-only by default; ``-f/--full`` for complete content.
* ``-b/--backend {rich,pydoc}`` — rich (default) with fallback to pydoc.
* Rich filtering (ext/include/exclude/hidden/max-depth/size/mtime/gitignore).
* Content policy per file (``--full-for``, ``--head-for``, ``--skip-for``).
* Lightweight grep (``--grep``, ``-C``, ``-v``, ``-F``, ``--highlight``).
* Slice by ``--range A:B``, ``--head N``, ``--tail N``, ``--max-lines N``.
* Structured output (``--list-only``, ``--count``, ``--json``, ``--format``).
* Non-interactive workflow (``--no-pager``, ``--output``, ``--null``).
* Integration: ``--config``, env vars (``FILEVIEW_*``), ``--editor``,
  ``--pager``, ``--git {modified,staged,diff,untracked}``, ``--diff A B``.
* Quality-of-life: ``--hash``, ``--unique``, ``--copy-path``, ``--interactive``.

Out of scope for this refactor: ``--watch``, ``--tree``,
``--generate-completion``, ``--generate-man``. These are best shipped as
separate files/scripts rather than bloating the main CLI.
"""

from __future__ import annotations
import argparse
import base64
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass
from datetime import datetime
import difflib
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import pydoc
import re
import signal
import subprocess
import sys
import time
from typing import Any, ContextManager
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


try:  # optional: TOML for config files
    import tomllib  # py3.11+
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib
    except ImportError:  # pragma: no cover
        tomllib = None

try:  # optional: rich backend
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    Console = Panel = Syntax = Text = None
    RICH_AVAILABLE = False

try:  # optional: clipboard
    import pyperclip

    PYPERCLIP_AVAILABLE = True
except ImportError:  # pragma: no cover
    pyperclip = None
    PYPERCLIP_AVAILABLE = False

try:  # optional: encoding detection
    import chardet

    CHARDET_AVAILABLE = True
except ImportError:  # pragma: no cover
    chardet = None
    CHARDET_AVAILABLE = False


__version__: str = "2.0.0"

DEFAULT_HEAD_LINES: int = 10
DEFAULT_MAX_BYTES: int = 1_000_000
DEFAULT_ENCODING: str = "utf-8"
DEFAULT_BACKEND: str = "rich"
DEFAULT_TAB_WIDTH: int = 4
DEFAULT_BINARY_BYTES: int = 512

CONFIG_PATHS: tuple[Path, ...] = (
    Path.home() / ".config" / "fileview" / "config.toml",
    Path.home() / ".fileviewrc.toml",
    Path(".fileviewrc.toml"),
)

COMMENT_PREFIXES: tuple[str, str, str, str] = ("#", "//", ";", "--")


# ---------------------------------------------------------------------------
# Config / env
# ---------------------------------------------------------------------------


def load_config(explicit: Path | None = None) -> dict[str, Any]:
    """Load TOML config: explicit path, or first existing of CONFIG_PATHS."""
    if tomllib is None:
        return {}
    candidates = (explicit,) if explicit else CONFIG_PATHS
    for path in candidates:
        if path is not None and path.is_file():
            try:
                with path.open("rb") as f:
                    return tomllib.load(f)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                print(f"warning: could not read config {path}: {exc}", file=sys.stderr)
    return {}


def _apply_defaults(parser: argparse.ArgumentParser, defaults: dict[str, Any]) -> None:
    """Apply defaults (from config or env) to parser actions."""
    actions = {a.dest: a for a in parser._actions}
    for key, val in defaults.items():
        action = actions.get(key)
        if action is None:
            continue
        if isinstance(action, argparse._AppendAction):
            action.default = list(val) if isinstance(val, list) else [val]
        else:
            action.default = val


def _apply_env_defaults(parser: argparse.ArgumentParser) -> None:
    """FILEVIEW_<DEST_UPPER> env vars override config defaults."""
    for action in parser._actions:
        if not action.dest or action.dest == argparse.SUPPRESS:
            continue
        env_key = f"FILEVIEW_{action.dest.upper()}"
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            action.default = raw.lower() in ("1", "true", "yes", "on")
        elif isinstance(action, argparse._AppendAction):
            action.default = [raw]
        elif action.type is int:
            with suppress(ValueError):
                action.default = int(raw)
        else:
            action.default = raw


# ---------------------------------------------------------------------------
# Value parsers
# ---------------------------------------------------------------------------


def parse_size(spec: str) -> int:
    m = re.match(r"^(\d+)([KMG]?)$", spec.strip().upper())
    if not m:
        msg = f"invalid size: {spec!r}"
        raise argparse.ArgumentTypeError(msg)
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}[m.group(2)]
    return int(m.group(1)) * mult


def parse_time(spec: str) -> float:
    """Accept ``7d``/``12h``/``30m``/``2w`` or an absolute date."""
    s = spec.strip()
    m = re.match(r"^(\d+)([smhdw])$", s)
    if m:
        n = int(m.group(1))
        secs = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
        return time.time() - n * secs
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    msg = f"invalid time: {spec!r}"
    raise argparse.ArgumentTypeError(msg)


def parse_range(spec: str) -> tuple[int | None, int | None]:
    if ":" not in spec:
        msg = f"--range expects A:B, got {spec!r}"
        raise argparse.ArgumentTypeError(msg)
    a, b = spec.split(":", 1)
    try:
        start = int(a) if a.strip() else None
        end = int(b) if b.strip() else None
    except ValueError as exc:
        msg = f"invalid --range: {spec!r}"
        raise argparse.ArgumentTypeError(msg) from exc
    return start, end


def parse_glob_eq(spec: str) -> tuple[str, int]:
    if "=" not in spec:
        msg = f"--head-for expects GLOB=N, got {spec!r}"
        raise argparse.ArgumentTypeError(msg)
    glob_pat, n_str = spec.rsplit("=", 1)
    try:
        return glob_pat, int(n_str)
    except ValueError as exc:
        msg = f"--head-for: invalid N in {spec!r}"
        raise argparse.ArgumentTypeError(msg) from exc


def normalize_exts(specs: Sequence[str]) -> list[str]:
    out: list[str] = []
    for spec in specs:
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.startswith("."):
                part = "." + part
            out.append(part.lower())
    return out


# ---------------------------------------------------------------------------
# Timeout helper
# ---------------------------------------------------------------------------


@contextmanager
def io_timeout(seconds: float | None) -> Generator[None]:
    if seconds is None or seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handler(_signum: int, _frame: Any) -> None:
        msg = "read timed out"
        raise TimeoutError(msg)

    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(int(seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


# ---------------------------------------------------------------------------
# Git & gitignore
# ---------------------------------------------------------------------------


def git_files(mode: str, root: Path) -> list[Path]:
    cmds = {
        "modified": ["git", "diff", "--name-only"],
        "staged": ["git", "diff", "--cached", "--name-only"],
        "diff": ["git", "diff", "--name-only", "HEAD"],
        "untracked": ["git", "ls-files", "--others", "--exclude-standard"],
    }
    try:
        result = subprocess.run(cmds[mode], cwd=root, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        msg = f"git {mode} failed: {exc}"
        raise SystemExit(msg) from exc
    return [root / line for line in result.stdout.splitlines() if line.strip()]


def load_gitignore(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.is_file():
        return []
    try:
        return [
            ln.strip()
            for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
    except OSError:
        return []


def _gitignore_pattern_matches(pat: str, rel: str, parts: list[str]) -> bool:
    if pat.startswith("/"):
        pat = pat[1:]
        return fnmatch.fnmatch(rel, pat) or rel.startswith(pat.rstrip("/") + "/")
    if pat.endswith("/"):
        base = pat.rstrip("/")
        return any(fnmatch.fnmatch(part, base) for part in parts[:-1])
    if "/" in pat:
        return fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, "*/" + pat)
    return any(fnmatch.fnmatch(part, pat) for part in parts)


def gitignore_match(rel_path: str, patterns: list[str]) -> bool:
    matched = False
    rel_norm = rel_path.replace("\\", "/")
    parts = rel_norm.split("/")
    for raw in patterns:
        neg = raw.startswith("!")
        pat = raw[1:] if neg else raw
        if not pat:
            continue
        if _gitignore_pattern_matches(pat, rel_norm, parts):
            matched = not neg
    return matched


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def _iter_files(root: Path, recursive: bool, follow: bool, max_depth: int) -> Iterator[Path]:
    if not recursive:
        try:
            for entry in root.iterdir():
                try:
                    if entry.is_file():
                        yield entry
                except OSError:
                    continue
        except OSError:
            return
        return

    visited: set[tuple[int, int]] = set()

    def walk(directory: Path, depth: int) -> Iterator[Path]:
        if max_depth >= 0 and depth > max_depth:
            return
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_symlink() and not follow:
                    if entry.is_file():
                        yield entry
                    continue
                if follow:
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    key = (st.st_dev, st.st_ino)
                    if key in visited:
                        continue
                    visited.add(key)
                if entry.is_dir():
                    yield from walk(entry, depth + 1)
                elif entry.is_file():
                    yield entry
            except OSError:
                continue

    yield from walk(root, 0)


def _matches_any(s: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(s, p) for p in patterns)


def collect_files(root: Path, args: argparse.Namespace, gitignore_pats: list[str]) -> list[Path]:
    """Apply all selection filters to produce the final candidate set."""
    inc_ext = normalize_exts(args.ext)
    exc_ext = normalize_exts(args.exclude_ext)
    name_re = re.compile(str(args.name)) if args.name else None
    seen: set[str] = set()
    result: list[Path] = []

    for path in _iter_files(root, args.recursive, args.follow_symlinks, args.max_depth):
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        rel_str = str(rel).replace("\\", "/")

        try:
            st = path.stat()
        except OSError:
            continue

        # Hidden
        if not args.hidden and any(part.startswith(".") for part in rel.parts):
            continue

        # Size
        if st.st_size < args.min_size or st.st_size > args.max_size:
            continue

        # mtime
        if args.newer_than is not None and st.st_mtime < args.newer_than:
            continue
        if args.older_than is not None and st.st_mtime > args.older_than:
            continue

        # Extension
        ext = path.suffix.lower()
        if inc_ext and ext not in inc_ext:
            continue
        if exc_ext and ext in exc_ext:
            continue

        # Glob include / exclude
        if args.include and not _matches_any(rel_str, args.include):
            continue
        if args.exclude and _matches_any(rel_str, args.exclude):
            continue

        # Regex name
        if name_re is not None and not name_re.search(rel_str):
            continue

        # gitignore
        if args.gitignore and gitignore_pats and gitignore_match(rel_str, gitignore_pats):
            continue

        # dedupe by realpath
        if args.dedupe:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)

        result.append(path)

    return sort_files(result, args.sort, args.reverse)


def collect_explicit(root: Path, args: argparse.Namespace) -> list[Path]:
    """Paths from --stdin / --files-from; bypass traversal filters except dedupe."""
    stdin_lines: list[str] = []
    need_stdin = args.stdin or "-" in args.files_from
    if need_stdin:
        stdin_lines = [ln.strip() for ln in sys.stdin if ln.strip()]

    raw: list[str] = []
    if args.stdin:
        raw.extend(stdin_lines)
    for src in args.files_from:
        if src == "-":
            raw.extend(stdin_lines)
        else:
            try:
                with open(src, encoding="utf-8") as f:
                    raw.extend(ln.strip() for ln in f if ln.strip())
            except OSError as exc:
                print(f"warning: --files-from {src}: {exc}", file=sys.stderr)

    seen: set[str] = set()
    result: list[Path] = []
    for p in raw:
        full = Path(p)
        if not full.is_absolute():
            full = root / full
        if not full.is_file():
            print(f"warning: not a file: {p}", file=sys.stderr)
            continue
        if args.dedupe:
            try:
                key = str(full.resolve())
            except OSError:
                key = str(full)
            if key in seen:
                continue
            seen.add(key)
        result.append(full)

    return sort_files(result, args.sort, args.reverse)


def sort_files(files: list[Path], key: str, reverse: bool) -> list[Path]:
    if key == "none":
        return list(files)

    def _attr(p: Path, attr: str) -> float:
        try:
            return float(getattr(p.stat(), attr))
        except OSError:
            return 0.0

    if key == "size":
        return sorted(files, key=lambda p: _attr(p, "st_size"), reverse=reverse)
    if key == "mtime":
        return sorted(files, key=lambda p: _attr(p, "st_mtime"), reverse=reverse)
    return sorted(files, key=lambda p: str(p).lower(), reverse=reverse)


# ---------------------------------------------------------------------------
# Content policy
# ---------------------------------------------------------------------------


@dataclass
class ContentPolicy:
    head: int | None = None  # None = unlimited head
    tail: int | None = None
    range_start: int | None = None
    range_end: int | None = None
    grep: re.Pattern[str] | None = None
    invert: bool = False
    context: int = 0
    strip_trailing: bool = False
    strip_comments: bool = False
    max_lines: int | None = None


@dataclass
class LoadedFile:
    path: Path
    relative: Path
    all_lines: list[str]
    size: int
    error: str | None = None
    binary: bool = False
    binary_dump: str | None = None
    sha256: str | None = None
    policy: ContentPolicy | None = None  # None → skip


def _decode(raw: bytes, encoding: str, detect: bool) -> tuple[str, str | None]:
    try:
        return raw.decode(encoding), None
    except (UnicodeDecodeError, LookupError):
        pass
    if detect and CHARDET_AVAILABLE and chardet is not None:
        guess = chardet.detect(raw).get("encoding")
        if guess:
            try:
                return raw.decode(guess, errors="replace"), f"decoded as {guess}"
            except (UnicodeDecodeError, LookupError):
                pass
    return raw.decode(encoding, errors="replace"), "decoded with errors='replace'"


def _hex_dump(raw: bytes) -> str:
    lines = []
    for off in range(0, len(raw), 16):
        chunk = raw[off : off + 16]
        hexes = " ".join(f"{b:02x}" for b in chunk)
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hexes:<47}  {ascii_}")
    return "\n".join(lines)


def _b64_dump(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def load_file(path: Path, root: Path, args: argparse.Namespace) -> LoadedFile:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path

    try:
        size = path.stat().st_size
    except OSError as exc:
        return LoadedFile(path, rel, [], 0, error=str(exc))

    try:
        with io_timeout(args.timeout):
            raw = path.read_bytes()
    except TimeoutError:
        return LoadedFile(path, rel, [], size, error=f"read timed out after {args.timeout}s")
    except OSError as exc:
        return LoadedFile(path, rel, [], size, error=str(exc))

    sha = hashlib.sha256(raw).hexdigest() if args.hash else None
    binary = b"\x00" in raw[:8192]

    if binary:
        if args.binary == "skip":
            return LoadedFile(path, rel, [], size, binary=True, sha256=sha)
        head = raw[: args.binary_bytes]
        dump = _hex_dump(head) if args.binary == "hex" else _b64_dump(head)
        return LoadedFile(path, rel, [], size, binary=True, binary_dump=dump, sha256=sha)

    text, note = _decode(raw, args.encoding, args.encoding_detect)
    return LoadedFile(
        path,
        rel,
        text.splitlines(),
        size,
        error=note if note and "errors='replace'" in (note or "") else None,
        sha256=sha,
    )


def resolve_policy(rel: Path, args: argparse.Namespace, grep: re.Pattern[str] | None) -> ContentPolicy | None:
    rel_str = str(rel).replace("\\", "/")

    if any(fnmatch.fnmatch(rel_str, p) for p in args.skip_for):
        return None

    head: int | None = args.head
    if args.full:
        head = None
    for pat, n in args.head_for:
        if fnmatch.fnmatch(rel_str, pat):
            head = n
            break
    if any(fnmatch.fnmatch(rel_str, p) for p in args.full_for):
        head = None

    return ContentPolicy(
        head=head,
        tail=args.tail,
        range_start=args.range[0] if args.range else None,
        range_end=args.range[1] if args.range else None,
        grep=grep,
        invert=args.invert_match,
        context=args.context,
        strip_trailing=args.strip_trailing_ws,
        strip_comments=args.no_comments,
        max_lines=args.max_lines,
    )


def _is_comment_line(line: str) -> bool:
    s = line.lstrip()
    return any(s.startswith(p) for p in COMMENT_PREFIXES)


def _grep_filter(
    items: list[tuple[int, str]],
    pattern: re.Pattern[str],
    invert: bool,
    context: int,
) -> list[tuple[int, str]]:
    if not items:
        return []
    texts = [t for _, t in items]
    keep: set[int] = set()
    for i, line in enumerate(texts):
        hit = bool(pattern.search(line))
        if hit != invert:  # XOR
            for j in range(max(0, i - context), min(len(texts), i + context + 1)):
                keep.add(j)
    return [items[i] for i in sorted(keep)]


def apply_content_policy(all_lines: list[str], policy: ContentPolicy) -> tuple[list[tuple[int, str]], int, int]:
    """Return (items, matched_count, total_lines)."""
    total = len(all_lines)
    items = list(enumerate(all_lines, 1))

    if policy.strip_comments:
        items = [(i, t) for i, t in items if not _is_comment_line(t)]
    if policy.strip_trailing:
        items = [(i, t.rstrip()) for i, t in items]

    if policy.range_start is not None or policy.range_end is not None:
        items = [
            (i, t)
            for i, t in items
            if (policy.range_start is None or i >= policy.range_start)
            and (policy.range_end is None or i <= policy.range_end)
        ]

    if policy.grep is not None:
        items = _grep_filter(items, policy.grep, policy.invert, policy.context)

    matched = len(items)

    if policy.range_start is None and policy.range_end is None:
        if policy.head is not None and len(items) > policy.head:
            items = items[: policy.head]
        elif policy.tail is not None and len(items) > policy.tail:
            items = items[-policy.tail :]

    if policy.max_lines is not None and len(items) > policy.max_lines:
        items = items[: policy.max_lines]

    return items, matched, total


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _format_line(idx: int, text: str, args: argparse.Namespace, highlight: re.Pattern[str] | None) -> str:
    if args.tab_width:
        text = text.expandtabs(args.tab_width)
    if args.truncate_lines and len(text) > args.truncate_lines:
        text = text[: max(0, args.truncate_lines - 1)] + "…"
    if highlight is not None and args.color != "never":
        text = highlight.sub(lambda m: f"\033[1;31m{m.group(0)}\033[0m", text)
    if args.line_numbers:
        if args.number_style == "hex":
            return f"{idx:>5x}  {text}"
        if args.number_style == "right":
            return f"{text}  {idx}"
        return f"{idx:>5}  {text}"
    return text


def _header_lines(
    view: LoadedFile,
    items: list[tuple[int, str]],
    matched: int,
    args: argparse.Namespace,
) -> list[str]:
    if args.compact:
        return []
    hdr: list[str] = []
    if not args.no_header:
        title = f"FILE: {view.relative}"
        if args.show_size:
            title += f"  ({view.size} bytes)"
        if args.show_mtime:
            try:
                mtime = datetime.fromtimestamp(view.path.stat().st_mtime).isoformat(timespec="seconds")
                title += f"  [{mtime}]"
            except OSError:
                pass
        hdr.append("=" * 40)
        hdr.append(title)
        if view.error:
            hdr.append(f"ERROR: {view.error}")
        if view.binary:
            hdr.append("[binary file — dump shown]")
        if view.policy and not view.binary:
            notes = []
            if view.policy.grep is not None and matched < len(view.all_lines):
                notes.append(f"{matched} of {len(view.all_lines)} lines match")
            if len(items) < matched:
                notes.append(f"showing {len(items)} of {matched}")
                if len(view.all_lines) > matched:
                    notes.append(f"({len(view.all_lines)} total in file)")
            if notes:
                hdr.append("(" + "; ".join(notes) + "; use -f/--full to see all)")
        hdr.append("=" * 40)
    return hdr


def render_plain(
    views: Sequence[LoadedFile],
    args: argparse.Namespace,
) -> tuple[str, int]:
    """Render to a single string. Returns (text, total_items_shown)."""
    sections: list[str] = []
    total_shown = 0
    for view in views:
        parts: list[str] = []
        if view.policy is None:
            continue
        if view.binary:
            parts.extend(_header_lines(view, [], 0, args))
            parts.append(view.binary_dump or "")
            sections.append("\n".join(parts))
            continue

        items, matched, _ = apply_content_policy(view.all_lines, view.policy)
        total_shown += len(items)
        parts.extend(_header_lines(view, items, matched, args))
        if view.error and not items:
            sections.append("\n".join(parts))
            continue
        rendered = [_format_line(i, t, args, view.policy.grep if args.highlight else None) for i, t in items]
        if view.policy.head is not None and len(items) < matched:
            rendered.append("...")
        parts.extend(rendered)
        sections.append("\n".join(parts))

    separator = "\0" if args.null else ("\n\f\n" if args.page_break else "\n")
    return separator.join(sections), total_shown


def render_rich(views: Sequence[LoadedFile], args: argparse.Namespace) -> int:
    assert Console is not None
    console = Console(
        force_terminal=(args.color == "always") or None,
        no_color=(args.color == "never") or None,
    )
    ctx: ContextManager[Any] = nullcontext() if args.no_pager else console.pager(styles=True)
    total_shown = 0
    with ctx:
        for view in views:
            if view.policy is None:
                continue
            console.print()
            if not args.no_header and not args.compact:
                title = str(view.relative)
                if args.show_size:
                    title += f"  ({view.size} bytes)"
                if args.show_mtime:
                    try:
                        mtime = datetime.fromtimestamp(view.path.stat().st_mtime).isoformat(timespec="seconds")
                        title += f"  [{mtime}]"
                    except OSError:
                        pass
                console.rule(f"[bold]{title}[/bold]")
                if view.error:
                    console.print(f"[red]ERROR: {view.error}[/red]")
                if view.binary:
                    console.print("[yellow]binary file — dump shown[/yellow]")

            if view.binary:
                console.print(view.binary_dump or "", markup=False, highlight=False)
                continue

            items, matched, _ = apply_content_policy(view.all_lines, view.policy)
            total_shown += len(items)
            if view.policy and not args.no_header:
                notes = []
                if view.policy.grep is not None and matched < len(view.all_lines):
                    notes.append(f"{matched} of {len(view.all_lines)} lines match")
                if len(items) < matched:
                    notes.append(f"showing {len(items)} of {matched}")
                    if len(view.all_lines) > matched:
                        notes.append(f"({len(view.all_lines)} total in file)")
                if notes:
                    console.print(f"[dim]({'; '.join(notes)}; use -f/--full)[/dim]")

            if not items:
                continue

            # Compose body from item lines
            body_lines = [_format_line(i, t, args, None) for i, t in items]
            body = "\n".join(body_lines)
            if view.policy.head is not None and len(items) < matched:
                body += "\n[dim]...[/dim]"

            content_renderable: Any = body
            if args.syntax and Syntax is not None and not args.panel:
                try:
                    lexer = Syntax.guess_lexer(str(view.path), body)
                    content_renderable = Syntax(
                        body,
                        lexer,
                        line_numbers=False,
                        word_wrap=args.wrap,
                        theme=args.theme,
                    )
                except Exception:  # noqa: BLE001
                    content_renderable = Text(body) if Text is not None else body

            if args.panel and Panel is not None:
                console.print(Panel(content_renderable, title=str(view.relative), border_style="dim"))
            elif isinstance(content_renderable, str):
                console.print(content_renderable, markup=False, highlight=False)
            else:
                console.print(content_renderable)
    return total_shown


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------


def _record(view: LoadedFile) -> dict[str, Any]:
    items: list[tuple[int, str]] = []
    matched = 0
    if view.policy is not None and not view.binary:
        items, matched, _ = apply_content_policy(view.all_lines, view.policy)
    return {
        "path": str(view.relative),
        "size": view.size,
        "lines": len(view.all_lines),
        "matched": matched,
        "shown": len(items),
        "truncated": len(items) < matched,
        "binary": view.binary,
        "error": view.error,
        "sha256": view.sha256,
    }


def output_structured(views: Sequence[LoadedFile], args: argparse.Namespace) -> str:
    if args.json:
        return json.dumps([_record(v) for v in views], indent=2, ensure_ascii=False)

    sep = "\0" if args.null else "\n"
    parts: list[str] = []
    for view in views:
        if args.list_only:
            parts.append(str(view.relative))
        elif args.count:
            count = len(view.all_lines) if not view.binary else 0
            parts.append(f"{view.relative}\t{count}")
        elif args.format:
            rec = _record(view)
            try:
                parts.append(args.format.format(**rec))
            except (KeyError, IndexError) as exc:
                msg = f"--format error: {exc}"
                raise SystemExit(msg) from exc
    return sep.join(parts)


# ---------------------------------------------------------------------------
# Paging / streaming
# ---------------------------------------------------------------------------


def run_pager(text: str, cmd: str | None) -> None:
    if cmd:
        try:
            subprocess.run(cmd, input=text, text=True, shell=True, check=False)
            return
        except FileNotFoundError:
            print(f"warning: pager {cmd!r} not found; using pydoc", file=sys.stderr)
    pydoc.pager(text)


# ---------------------------------------------------------------------------
# Auxiliary integrations
# ---------------------------------------------------------------------------


def open_in_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    try:
        subprocess.run([editor, str(path)], check=False)
    except FileNotFoundError:
        print(f"warning: editor {editor!r} not found", file=sys.stderr)


def copy_paths(paths: Sequence[str]) -> bool:
    if not PYPERCLIP_AVAILABLE or pyperclip is None:
        print("warning: --copy-path requires pyperclip", file=sys.stderr)
        return False
    try:
        pyperclip.copy("\n".join(paths))
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"warning: clipboard copy failed: {exc}", file=sys.stderr)
        return False


def show_diff(path_a: Path, path_b: Path) -> None:
    try:
        a = path_a.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        b = path_b.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError as exc:
        msg = f"cannot read for diff: {exc}"
        raise SystemExit(msg) from exc
    diff = difflib.unified_diff(a, b, fromfile=str(path_a), tofile=str(path_b))
    sys.stdout.writelines(diff)


def filter_unique(views: list[LoadedFile]) -> list[LoadedFile]:
    seen: set[str] = set()
    out: list[LoadedFile] = []
    for v in views:
        if v.sha256 is None:
            try:
                v.sha256 = hashlib.sha256(v.path.read_bytes()).hexdigest()
            except OSError:
                out.append(v)
                continue
        if v.sha256 in seen:
            continue
        seen.add(v.sha256)
        out.append(v)
    return out


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------


def interactive_loop(views: list[LoadedFile], args: argparse.Namespace) -> None:
    if not views:
        print("No files to show.", file=sys.stderr)
        return
    overrides: dict[int, int | None] = {}
    i = 0
    while 0 <= i < len(views):
        view = views[i]
        if view.policy is None:
            i += 1
            continue
        policy = view.policy
        if i in overrides:
            policy = dataclass_replace(policy, head=overrides[i])

        # Render single file
        single = LoadedFile(
            view.path,
            view.relative,
            view.all_lines,
            view.size,
            error=view.error,
            binary=view.binary,
            binary_dump=view.binary_dump,
            sha256=view.sha256,
            policy=policy,
        )
        if RICH_AVAILABLE and args.backend == "rich":
            saved = args.no_pager
            args.no_pager = True
            try:
                render_rich([single], args)
            finally:
                args.no_pager = saved
        else:
            text, _ = render_plain([single], args)
            print(text)

        prompt = f"[{i + 1}/{len(views)}] (n)ext (p)rev (f)ull (e)dit (s)kip (c)opy (q)uit > "
        try:
            cmd = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if cmd in ("", "n", "next"):
            i += 1
        elif cmd in ("p", "prev"):
            i = max(0, i - 1)
        elif cmd == "f":
            overrides[i] = None  # force full
        elif cmd == "e":
            open_in_editor(view.path)
        elif cmd == "s":
            i += 1
        elif cmd == "c":
            copy_paths([str(view.relative)])
            i += 1
        elif cmd == "q":
            return


def dataclass_replace(obj: ContentPolicy, **kw: Any) -> ContentPolicy:
    data = {**obj.__dict__, **kw}
    return ContentPolicy(**data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fileview",
        description=(
            "Page through files. By default only the head of each file is shown; use -f/--full for complete content."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("root", nargs="?", default=".", help="root directory to scan")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")

    # Display / mode
    p.add_argument(
        "-f",
        "--full",
        action="store_true",
        help="show full file content instead of just the head",
    )
    p.add_argument(
        "-n",
        "--head",
        type=int,
        default=DEFAULT_HEAD_LINES,
        help="number of head lines to show",
    )
    p.add_argument("--tail", type=int, default=None, help="show last N lines instead of head")
    p.add_argument(
        "--range",
        type=parse_range,
        default=None,
        metavar="A:B",
        help="show only lines A–B (1-indexed, inclusive; either side optional)",
    )
    p.add_argument(
        "--max-lines",
        type=int,
        default=None,
        help="hard cap on lines shown per file (after other filters)",
    )
    p.add_argument(
        "-b",
        "--backend",
        choices=("rich", "pydoc"),
        default=DEFAULT_BACKEND,
        help="pager backend",
    )
    p.add_argument(
        "--no-pager",
        action="store_true",
        help="write to stdout instead of using a pager",
    )
    p.add_argument(
        "--stream",
        action="store_true",
        help="stream output to stdout/file (implies --no-pager)",
    )
    p.add_argument(
        "--output",
        metavar="FILE",
        help="write plain rendered output to FILE (disables pager)",
    )
    p.add_argument("--pager", metavar="CMD", help="use custom pager command (forces plain backend)")

    # Display formatting
    p.add_argument("--line-numbers", action="store_true", help="prefix lines with numbers")
    p.add_argument(
        "--number-style",
        choices=("left", "right", "hex"),
        default="left",
        help="line-number formatting",
    )
    p.add_argument(
        "--tab-width",
        type=int,
        default=DEFAULT_TAB_WIDTH,
        help="tab expansion width (0 disables)",
    )
    p.add_argument(
        "--truncate-lines",
        type=int,
        default=None,
        metavar="N",
        help="truncate long lines to N chars (with …)",
    )
    p.add_argument(
        "--wrap",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="soft-wrap long lines (rich backend)",
    )
    p.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="color output control",
    )
    p.add_argument("--theme", default=None, help="syntax theme (rich backend)")
    p.add_argument("--syntax", action="store_true", help="enable syntax highlighting (rich)")
    p.add_argument("--panel", action="store_true", help="draw a box around each file (rich)")
    p.add_argument("--no-header", action="store_true", help="omit per-file headers")
    p.add_argument(
        "--compact",
        action="store_true",
        help="compact mode: no headers, single separator between files",
    )
    p.add_argument("--page-break", action="store_true", help="insert form feeds between files")
    p.add_argument("--separator", default=None, help="override inter-file separator (default: \\n)")
    p.add_argument("--show-size", action="store_true", help="include file size in header")
    p.add_argument("--show-mtime", action="store_true", help="include mtime in header")
    p.add_argument(
        "--null",
        action="store_true",
        help="use NUL as output separator (for --list-only/--format)",
    )

    # Selection
    p.add_argument("-r", "--recursive", action="store_true", help="recurse into subdirectories")
    p.add_argument(
        "--max-depth",
        type=int,
        default=-1,
        metavar="N",
        help="limit recursion depth (-1 = unlimited)",
    )
    p.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="only include files matching GLOB (repeatable)",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="exclude files matching GLOB (repeatable)",
    )
    p.add_argument(
        "--ext",
        action="append",
        default=[],
        metavar="EXT",
        help="include by extension (repeatable, comma-separated ok)",
    )
    p.add_argument(
        "--exclude-ext",
        action="append",
        default=[],
        metavar="EXT",
        help="exclude by extension",
    )
    p.add_argument(
        "--name",
        default=None,
        metavar="REGEX",
        help="regex filter on relative filename",
    )
    p.add_argument("--hidden", action="store_true", help="include hidden files/directories")
    p.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="follow symlinks (off by default)",
    )
    p.add_argument(
        "--min-size",
        type=parse_size,
        default=0,
        metavar="N[KMG]",
        help="minimum file size",
    )
    p.add_argument(
        "--max-size",
        type=parse_size,
        default=DEFAULT_MAX_BYTES,
        metavar="N[KMG]",
        help="maximum file size",
    )
    p.add_argument(
        "--newer-than",
        type=parse_time,
        default=None,
        metavar="TIME",
        help="only files newer than TIME (e.g. 7d, 2024-01-01)",
    )
    p.add_argument(
        "--older-than",
        type=parse_time,
        default=None,
        metavar="TIME",
        help="only files older than TIME",
    )
    p.add_argument(
        "--gitignore",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="respect .gitignore in root",
    )
    p.add_argument(
        "--dedupe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="drop duplicate paths (by realpath)",
    )
    p.add_argument("--stdin", action="store_true", help="read newline-separated paths from stdin")
    p.add_argument(
        "--files-from",
        action="append",
        default=[],
        metavar="FILE",
        help="read paths from FILE ('-' for stdin)",
    )
    p.add_argument(
        "--git",
        choices=("modified", "staged", "diff", "untracked"),
        default=None,
        help="restrict to git-changed files",
    )
    p.add_argument(
        "--sort",
        choices=("name", "size", "mtime", "none"),
        default="name",
        help="sort order",
    )
    p.add_argument("--reverse", action="store_true", help="reverse sort order")

    # Content filtering
    p.add_argument(
        "--grep",
        default=None,
        metavar="PATTERN",
        help="only emit lines matching PATTERN (regex)",
    )
    p.add_argument("-C", "--context", type=int, default=0, help="context lines around matches")
    p.add_argument("-v", "--invert-match", action="store_true", help="invert --grep match")
    p.add_argument(
        "-F",
        "--fixed-strings",
        action="store_true",
        help="treat --grep pattern as literal",
    )
    p.add_argument("--highlight", action="store_true", help="highlight grep matches")
    p.add_argument(
        "--strip-trailing-ws",
        action="store_true",
        help="strip trailing whitespace from each line",
    )
    p.add_argument(
        "--no-comments",
        action="store_true",
        help="drop lines that start with #, //, ;, or --",
    )

    # Per-file policy
    p.add_argument(
        "--full-for",
        action="append",
        default=[],
        metavar="GLOB",
        help="force full content for files matching GLOB",
    )
    p.add_argument(
        "--head-for",
        action="append",
        default=[],
        type=parse_glob_eq,
        metavar="GLOB=N",
        help="override head length for matching files",
    )
    p.add_argument(
        "--skip-for",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip files matching GLOB",
    )

    # Reading
    p.add_argument("--encoding", default=DEFAULT_ENCODING, help="file encoding")
    p.add_argument(
        "--encoding-detect",
        action="store_true",
        help="fall back to charset detection if --encoding fails",
    )
    p.add_argument(
        "--binary",
        choices=("skip", "hex", "base64"),
        default="skip",
        help="how to handle binary files",
    )
    p.add_argument(
        "--binary-bytes",
        type=int,
        default=DEFAULT_BINARY_BYTES,
        help="bytes to dump for --binary hex/base64",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="per-file read timeout in seconds (Unix only)",
    )

    # Structured output
    p.add_argument("--list-only", action="store_true", help="print paths only")
    p.add_argument("--count", action="store_true", help="print path and line count per file")
    p.add_argument("--json", action="store_true", help="emit JSON metadata")
    p.add_argument(
        "--format",
        default=None,
        metavar="TPL",
        help="custom output template (fields: path,size,lines,shown,truncated,error,sha256)",
    )

    # Modes / integrations
    p.add_argument(
        "--diff",
        nargs=2,
        metavar=("A", "B"),
        default=None,
        help="show unified diff of two files (bypasses normal view)",
    )
    p.add_argument(
        "--editor",
        action="store_true",
        help="open each selected file in $EDITOR before paging",
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="prompt per file: next/prev/full/edit/skip/copy/quit",
    )
    p.add_argument("--copy-path", action="store_true", help="copy all selected paths to clipboard")
    p.add_argument("--hash", action="store_true", help="compute SHA-256 for each file")
    p.add_argument("--unique", action="store_true", help="collapse files with identical content")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help="explicit config file path",
    )
    p.add_argument("--debug", action="store_true", help="print resolved settings to stderr")

    return p


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()

    # Defaults: config → env → CLI
    config = load_config(Path(argv[-1]) if argv and "--config" in argv else None)
    if config:
        _apply_defaults(parser, config)
    _apply_env_defaults(parser)

    args = parser.parse_args(argv)

    # Normalize interactions
    if args.stream:
        args.no_pager = True
    if args.compact:
        args.no_header = True
    if args.separator is not None:
        # Apply to render path via args, keep page_break ignored
        pass
    if args.pager:
        args.backend = "pydoc"  # custom pager only works with plain rendering
    if args.output:
        args.no_pager = True

    # Conflict checks
    structured = args.list_only or args.count or args.json or bool(args.format)
    if args.diff:
        a, b = (Path(x) for x in args.diff)
        show_diff(a, b)
        return 0

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    if not args.full and args.head is not None and args.head < 0:
        print("error: --head must be >= 0", file=sys.stderr)
        return 2

    if args.debug:
        print(f"root={root}", file=sys.stderr)
        print(f"args={vars(args)}", file=sys.stderr)

    # Collect candidates
    if args.git:
        files = git_files(args.git, root)
        if not args.dedupe:
            pass
        else:
            seen: set[str] = set()
            uniq = []
            for p in files:
                try:
                    k = str(p.resolve())
                except OSError:
                    k = str(p)
                if k not in seen:
                    seen.add(k)
                    uniq.append(p)
            files = uniq
        files = sort_files(files, args.sort, args.reverse)
    elif args.stdin or args.files_from:
        files = collect_explicit(root, args)
    else:
        gitignore_pats = load_gitignore(root) if args.gitignore else []
        files = collect_files(root, args, gitignore_pats)

    if not files:
        print("No files found.", file=sys.stderr)
        return 0

    # --list-only short-circuits file loading
    if args.list_only:
        sep = "\0" if args.null else "\n"
        sys.stdout.write(sep.join(str(f.relative_to(root)) if f.is_relative_to(root) else str(f) for f in files) + sep)
        return 0

    # Build grep regex
    grep_re: re.Pattern[str] | None = None
    if args.grep is not None:
        pattern = re.escape(args.grep) if args.fixed_strings else args.grep
        try:
            grep_re = re.compile(pattern)
        except re.error as exc:
            print(f"error: invalid --grep pattern: {exc}", file=sys.stderr)
            return 2

    # Editor hook before loading
    if args.editor:
        for f in files:
            open_in_editor(f)

    # Load all views
    views: list[LoadedFile] = []
    for path in files:
        view = load_file(path, root, args)
        view.policy = resolve_policy(view.relative, args, grep_re)
        if view.policy is None:
            continue
        views.append(view)

    if args.unique:
        views = filter_unique(views)

    if not views:
        print("No files to display after filtering.", file=sys.stderr)
        return 0

    # Structured outputs (no paging)
    if structured:
        text = output_structured(views, args)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"wrote {args.output}", file=sys.stderr)
        else:
            print(text)
        return 0

    # Interactive
    if args.interactive:
        interactive_loop(views, args)
        if args.copy_path:
            copy_paths([str(v.relative) for v in views])
        return 0

    # Normal render
    if args.copy_path:
        copy_paths([str(v.relative) for v in views])

    if args.backend == "rich" and not RICH_AVAILABLE:
        print("warning: rich is not installed; falling back to pydoc", file=sys.stderr)
        args.backend = "pydoc"

    if args.syntax and args.backend != "rich":
        print("warning: --syntax requires the rich backend; ignoring", file=sys.stderr)
        args.syntax = False

    if args.backend == "rich":
        render_rich(views, args)
        return 0

    text, _ = render_plain(views, args)
    if args.separator is not None:
        # Re-join with custom separator, cheaper than threading it through
        sections = text.split("\n\f\n" if args.page_break else "\n")
        text = args.separator.join(sections)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    elif args.no_pager:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        run_pager(text, args.pager)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
