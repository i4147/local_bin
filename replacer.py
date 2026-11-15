#!/data/data/com.termux/files/usr/bin/env python
'Utilities for replacer.'
from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Sequence

from dh import SKIP_DIRS
from isbinary import is_binary
from enum import StrEnum
from types import SimpleNamespace
from typing import Annotated

import click
import typer


class Symlink_policy(StrEnum):
    'Enumerate the supported Symlink policy values.'
    skip = "skip"
    follow = "follow"
    error = "error"


class Color(StrEnum):
    'Enumerate the supported Color values.'
    auto = "auto"
    always = "always"
    never = "never"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo("replacer 2.0")
        raise typer.Exit()


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
        'Parse args.'
        return _Args(**_STATE)

    def error(self, message):
        raise typer.BadParameter(message)

    def print_help(self, file=None):
        'Print help.'
        typer.echo(click.get_current_context().get_help(), file=file)

    def print_usage(self, file=None):
        'Print usage.'
        typer.echo(click.get_current_context().get_usage(), file=file)


try:
    from charset_normalizer import from_bytes as _detect_encoding_fn
except ImportError:
    _detect_encoding_fn = None

try:
    import tomllib as _toml
except ImportError:
    try:
        import tomli as _toml
    except ImportError:
        _toml = None

EXIT_OK = 0
EXIT_NO_MATCH = 1
EXIT_USAGE = 2
EXIT_IO = 3
EXIT_ABORT = 4

UNDO_BLOB_DIR_SUFFIX = ".d"
DEFAULT_CONFIG_FILENAME = ".replacerrc"
DEFAULT_IGNORE_FILENAME = ".replacer_ignore"

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

_color_lock = threading.Lock()


@dataclass
class Options:
    'Store search, replace, regex, ignore_case, smart_case and related state.'
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


@dataclass
class Rule:
    'Store pattern, replacement, preserve_case, upper, lower and related state.'
    pattern: re.Pattern[str]
    replacement: str
    preserve_case: bool = False
    upper: bool = False
    lower: bool = False
    capitalize: bool = False
    max_count: int | None = None


@dataclass
class FileResult:
    'Store path, matches, changed, skipped, reason and related state.'
    path: str
    matches: int = 0
    changed: bool = False
    skipped: bool = False
    reason: str | None = None
    diff: str | None = None


def _colorize(text: str, color: str, when: str) -> str:
    if when == "never":
        return text
    if when == "auto":
        if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
            return text
    codes = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "bold": "1"}
    c = codes.get(color, "")
    if not c:
        return text
    return f"\x1b[{c}m{text}\x1b[0m"


def _safe_print(*a, **k) -> None:
    with _color_lock:
        print(*a, **k)


def _apply_case(matched: str, replacement: str) -> str:
    if not matched:
        return replacement
    if matched.isupper():
        return replacement.upper()
    if matched.islower():
        return replacement.lower()
    if matched[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _make_replacement_fn(rule: Rule, literal: bool) -> Callable[[re.Match[str]], str]:
    def fn(m: re.Match[str]) -> str:
        text = rule.replacement
        if not literal:
            try:
                text = m.expand(text)
            except re.error:
                pass
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
    flags = 0
    if opts.ignore_case or (opts.smart_case and not any(c.isupper() for c in search_text)):
        flags |= re.IGNORECASE
    if opts.multiline:
        flags |= re.MULTILINE | re.DOTALL
    src = search_text if opts.regex else re.escape(search_text)
    if opts.word:
        src = r"\b" + src + r"\b"
    return re.compile(src, flags)


def _read_with_meta(path: Path, encoding: str | None) -> tuple[str, str, bool, bytes] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    bom = raw.startswith(b"\xef\xbb\xbf")
    enc = encoding
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


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".replacer_", dir=str(path.parent))
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


def _file_allowed(path: Path, opts: Options, root: Path) -> bool:
    try:
        if path.is_symlink():
            if opts.symlink_policy == "skip":
                return False
            if opts.symlink_policy == "error":
                _safe_print(f"Error: symlink encountered: {path}", file=sys.stderr)
                return False
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
    if opts.globs and not any(
        fnmatch.fnmatch(name, g) or fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(str(path), g) for g in opts.globs
    ):
        return False
    if opts.excludes and any(
        fnmatch.fnmatch(name, e) or fnmatch.fnmatch(rel, e) or fnmatch.fnmatch(str(path), e) for e in opts.excludes
    ):
        return False
    if opts.types:
        ok = False
        for t in opts.types:
            exts = TYPE_MAP.get(t)
            if exts and path.suffix in exts:
                ok = True
                break
        if not ok:
            return False
    if is_binary(path):
        return False
    return True


def _load_ignore_patterns(opts: Options) -> list[str]:
    if opts.no_ignore or not opts.ignore_file:
        return []
    p = Path(opts.ignore_file)
    if not p.is_file():
        return []
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _is_ignored(path: Path, patterns: list[str], root: Path) -> bool:
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
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(path.name, pat):
            ignored = not negate
    return ignored


def _walk_dir(root: Path, opts: Options, ignore_patterns: list[str], start_depth: int) -> Iterator[Path]:
    stack: list[tuple[Path, int]] = [(root, start_depth)]
    seen_dirs: set[str] = set()
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for entry in entries:
            if entry.name in SKIP_DIRS:
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
            with open(opts.files_from, encoding="utf-8") as fh:
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


def _make_diff(path: Path, old: str, new: str, context: int) -> str:
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


def _save_for_undo(journal_path: Path, file_path: Path, original: bytes, new_hash: str) -> None:
    blob_dir = journal_path.with_name(journal_path.name + UNDO_BLOB_DIR_SUFFIX)
    blob_dir.mkdir(parents=True, exist_ok=True)
    blob_name = hashlib.sha256(str(file_path).encode() + b":" + str(time.time_ns()).encode()).hexdigest()[:16]
    (blob_dir / blob_name).write_bytes(original)
    entry = {
        "path": str(file_path),
        "blob": blob_name,
        "old_hash": hashlib.sha256(original).hexdigest(),
        "new_hash": new_hash,
        "ts": time.time(),
    }
    entries: list[dict] = []
    if journal_path.is_file():
        try:
            entries = json.loads(journal_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []
    entries.append(entry)
    journal_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _do_undo(journal_path: Path) -> int:
    if not journal_path.is_file():
        _safe_print(f"No journal at {journal_path}", file=sys.stderr)
        return EXIT_IO
    try:
        entries = json.loads(journal_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _safe_print(f"Bad journal: {exc}", file=sys.stderr)
        return EXIT_IO
    blob_dir = journal_path.with_name(journal_path.name + UNDO_BLOB_DIR_SUFFIX)
    restored = 0
    for e in reversed(entries):
        p = Path(e["path"])
        blob = blob_dir / e["blob"]
        if not blob.is_file():
            _safe_print(f"Missing blob: {blob}", file=sys.stderr)
            continue
        if p.is_file():
            current = hashlib.sha256(p.read_bytes()).hexdigest()
            if current != e["new_hash"]:
                _safe_print(f"Skip (modified since): {p}", file=sys.stderr)
                continue
        _atomic_write(p, blob.read_bytes())
        restored += 1
    _safe_print(f"Restored {restored} file(s)")
    return EXIT_OK


def _git_is_clean() -> bool:
    try:
        r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)
        return r.returncode == 0 and not r.stdout.strip()
    except FileNotFoundError:
        return True


def _prompt_decision(path: Path, line_no: int, context: str) -> str:
    while True:
        sys.stderr.write(f"\n{path}:{line_no}\n  {context}\nApply? [y]es/[n]o/[a]ll/[q]uit: ")
        sys.stderr.flush()
        ans = sys.stdin.readline().strip().lower()
        if ans in ("y", "yes", "n", "no", "a", "all", "q", "quit"):
            return ans[:1]
        if not ans:
            return "q"


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _process_one(
    path: Path,
    rules: Sequence[Rule],
    opts: Options,
    root: Path,
    prompt_state: dict,
) -> FileResult:
    res = FileResult(path=str(path))
    if prompt_state.get("quit"):
        res.skipped = True
        res.reason = "prompt_quit"
        return res
    meta = _read_with_meta(path, opts.encoding)
    if meta is None:
        res.skipped = True
        res.reason = "unreadable"
        return res
    text, enc, bom, raw = meta
    original_text = text
    total_matches = 0
    for rule in rules:
        matches = list(rule.pattern.finditer(text))
        if not matches:
            continue
        total_matches += len(matches)
        fn = _make_replacement_fn(rule, opts.literal_replace)

        def rep(m: re.Match[str]) -> str:
            return "" if opts.remove else fn(m)

        if opts.prompt and not prompt_state.get("all"):
            chunks: list[str] = []
            last = 0
            quit_now = False
            auto_all = False
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
                    chunks.append(text[last : m.start()])
                    chunks.append(rep(m))
                    last = m.end()
                    continue
                if ans == "y":
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
            text = rule.pattern.sub(rep, text, count=rule.max_count or 0)
    res.matches = total_matches
    if opts.check:
        return res
    if opts.files_with_matches:
        if total_matches:
            _safe_print(str(path))
        return res
    if opts.files_without_match:
        if not total_matches:
            _safe_print(str(path))
        return res
    if opts.count:
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
        if not opts.quiet and not opts.diff:
            _safe_print(f"[DRY] {path}: {total_matches} match(es)")
        return res
    try:
        new_bytes = text.encode(enc)
    except (UnicodeEncodeError, LookupError) as exc:
        res.skipped = True
        res.reason = f"encode: {exc}"
        return res
    if opts.backup:
        if opts.backup_dir:
            bdir = Path(opts.backup_dir)
            bdir.mkdir(parents=True, exist_ok=True)
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = Path(path.name)
            bpath = bdir / rel
            bpath.parent.mkdir(parents=True, exist_ok=True)
            bpath.write_bytes(raw)
        else:
            path.with_name(path.name + opts.backup).write_bytes(raw)
    if opts.undo_journal:
        try:
            _save_for_undo(Path(opts.undo_journal), path, raw, hashlib.sha256(new_bytes).hexdigest())
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


def _process_stdin(rules: Sequence[Rule], opts: Options) -> int:
    text = sys.stdin.read()
    original = text
    for rule in rules:
        fn = _make_replacement_fn(rule, opts.literal_replace)
        repl: Callable[[re.Match[str]], str] = (lambda m: "") if opts.remove else fn
        text = rule.pattern.sub(repl, text, count=rule.max_count or 0)
    if opts.check:
        return EXIT_OK if text != original else EXIT_NO_MATCH
    sys.stdout.write(text)
    return EXIT_OK


def _load_config(path: Path | None) -> dict:
    if path is None:
        cwd = Path.cwd()
        for p in (cwd, *cwd.parents):
            f = p / DEFAULT_CONFIG_FILENAME
            if f.is_file():
                path = f
                break
    if path is None or not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".toml", ""):
        if _toml is not None:
            try:
                return _toml.loads(text)
            except Exception:
                pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _load_rules_file(path: Path) -> list[Rule]:
    text = path.read_text(encoding="utf-8")
    data: dict = {}
    if path.suffix in (".toml", ""):
        if _toml is not None:
            try:
                data = _toml.loads(text)
            except Exception:
                data = {}
    if not data:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
    rules_data = data.get("rules", data.get("rule", []))
    if isinstance(rules_data, dict):
        rules_data = [rules_data]
    out: list[Rule] = []
    for rd in rules_data:
        search = rd.get("search") or rd.get("pattern")
        if not search:
            continue
        replacement = rd.get("replace", rd.get("replacement", ""))
        regex_mode = rd.get("regex", True)
        flags = 0
        if rd.get("ignore_case"):
            flags |= re.IGNORECASE
        src = search if regex_mode else re.escape(search)
        out.append(
            Rule(
                pattern=re.compile(src, flags),
                replacement=replacement,
                preserve_case=rd.get("preserve_case", False),
                upper=rd.get("upper", False),
                lower=rd.get("lower", False),
                capitalize=rd.get("capitalize", False),
                max_count=rd.get("max_count"),
            )
        )
    return out


def _build_rules(opts: Options) -> list[Rule]:
    if opts.rules_file:
        return _load_rules_file(Path(opts.rules_file))
    replacement = opts.replace
    if opts.remove:
        replacement = ""
    if opts.replace_from:
        replacement = Path(opts.replace_from).read_text(encoding="utf-8")
    if opts.env_expand:
        replacement = os.path.expandvars(replacement)
    return [
        Rule(
            pattern=_build_pattern(opts.search, opts),
            replacement=replacement,
            preserve_case=opts.preserve_case,
            upper=opts.upper,
            lower=opts.lower,
            capitalize=opts.capitalize,
            max_count=opts.max_count,
        )
    ]


def _build_parser() -> argparse.ArgumentParser:
    p = _ParserShim()


    return p


def _merge_config(args: argparse.Namespace, cfg: dict) -> argparse.Namespace:
    for k, v in cfg.items():
        k_norm = k.replace("-", "_")
        if hasattr(args, k_norm) and getattr(args, k_norm) in (None, False, [], ""):
            if v is not None:
                setattr(args, k_norm, v)
    return args


def main() -> int:
    'Run the command-line interface.'
    parser = _build_parser()
    args = parser.parse_args()
    cfg = _load_config(Path(args.config) if args.config else None)
    if cfg:
        args = _merge_config(args, cfg)

    if args.undo:
        if not args.undo_journal:
            parser.error("--undo requires --undo-journal")
        return _do_undo(Path(args.undo_journal))

    if args.dry_run and args.apply:
        parser.error("--dry-run and --apply are mutually exclusive")
    dry_run = args.dry_run and not args.apply
    if args.diff:
        dry_run = True

    if not args.search and not args.rules_file:
        parser.error("provide SEARCH or --rules")

    if args.backup_dir and not args.backup:
        args.backup = ".bak"

    if not args.ignore_file and not args.no_ignore:
        default_ignore = Path(DEFAULT_IGNORE_FILENAME)
        if default_ignore.is_file():
            args.ignore_file = str(default_ignore)

    if args.follow_symlinks:
        args.symlink_policy = "follow"
    elif args.symlink_policy == "follow":
        args.follow_symlinks = True

    if args.prompt and (args.jobs or 1) > 1:
        _safe_print("--prompt with --jobs>1 is unsupported; forcing jobs=1", file=sys.stderr)
        args.jobs = 1

    opts = Options(
        search=args.search,
        replace=args.replace,
        regex=args.regex,
        ignore_case=args.ignore_case,
        smart_case=args.smart_case,
        word=args.word,
        multiline=args.multiline,
        remove=args.remove,
        dry_run=dry_run,
        apply=args.apply,
        files=list(args.files),
        globs=list(args.globs),
        excludes=list(args.excludes),
        include_hidden=args.include_hidden,
        follow_symlinks=args.follow_symlinks,
        symlink_policy=args.symlink_policy,
        max_depth=args.max_depth,
        max_filesize=args.max_filesize if args.max_filesize is not None else 10 * 1024 * 1024,
        types=list(args.types),
        ignore_file=args.ignore_file,
        no_ignore=args.no_ignore,
        preserve_case=args.preserve_case,
        upper=args.upper,
        lower=args.lower,
        capitalize=args.capitalize,
        env_expand=args.env_expand,
        replace_from=args.replace_from,
        literal_replace=args.literal_replace,
        max_count=args.max_count,
        prompt=args.prompt,
        backup=args.backup,
        backup_dir=args.backup_dir,
        undo_journal=args.undo_journal,
        undo=args.undo,
        require_clean_git=args.require_clean_git,
        confirm=args.confirm,
        diff=args.diff,
        color=args.color,
        quiet=args.quiet,
        verbose=args.verbose,
        json_out=args.json_out,
        count=args.count,
        stats=args.stats,
        files_with_matches=args.files_with_matches,
        files_without_match=args.files_without_match,
        context=args.context if args.context is not None else 20,
        exit_code=args.exit_code,
        check=args.check,
        jobs=max(1, args.jobs or 1),
        rules_file=args.rules_file,
        files_from=args.files_from,
        encoding=args.encoding,
    )

    if opts.require_clean_git and not _git_is_clean():
        _safe_print("Working tree is not clean; aborting", file=sys.stderr)
        return EXIT_ABORT

    rules = _build_rules(opts)
    if not rules:
        _safe_print("No rules to apply", file=sys.stderr)
        return EXIT_USAGE

    if opts.files == ["-"]:
        return _process_stdin(rules, opts)

    if opts.confirm and not opts.dry_run:
        sys.stderr.write("Proceed with modifications? [y/N] ")
        sys.stderr.flush()
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            return EXIT_ABORT

    if opts.dry_run:
        _safe_print(_colorize("--- DRY RUN (no files will be modified) ---", "yellow", opts.color))

    root = Path.cwd()
    results: list[FileResult] = []
    prompt_state: dict = {"all": False, "quit": False}
    t0 = time.time()
    files_scanned = 0
    files_skipped = 0

    def _work(path: Path) -> FileResult:
        if not _file_allowed(path, opts, root):
            return FileResult(path=str(path), skipped=True, reason="filtered")
        return _process_one(path, rules, opts, root, prompt_state)

    if opts.jobs > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=opts.jobs) as ex:
            futures = {ex.submit(_work, p): p for p in _iter_files(opts)}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    r = fut.result()
                except Exception as exc:
                    r = FileResult(path=str(futures[fut]), skipped=True, reason=f"error: {exc}")
                results.append(r)
                files_scanned += 1
                if r.skipped:
                    files_skipped += 1
    else:
        for p in _iter_files(opts):
            files_scanned += 1
            r = _work(p)
            results.append(r)
            if r.skipped:
                files_skipped += 1
            if opts.verbose and r.skipped and r.reason:
                _safe_print(_colorize(f"skip {p}: {r.reason}", "yellow", opts.color), file=sys.stderr)

    changed = [r for r in results if r.changed]
    matched = [r for r in results if r.matches > 0]
    elapsed = time.time() - t0

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


def _run() -> None:
    raise SystemExit(main())


app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
)


@app.command(help="Recursively replace or remove text in files.")
def cli(
    search: Annotated[str, typer.Argument(help="Text to search for")] = "",
    replace: Annotated[str, typer.Argument(help="Replacement text")] = "",
    remove: Annotated[bool, typer.Option("-r", "--remove")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    apply: Annotated[bool, typer.Option("--apply")] = False,
    files: Annotated[list[str], typer.Option("-f", "--file")] = [],
    files_from: Annotated[str | None, typer.Option("--files-from")] = None,
    globs: Annotated[list[str], typer.Option("-g", "--glob")] = [],
    excludes: Annotated[list[str], typer.Option("--exclude")] = [],
    types: Annotated[list[str], typer.Option("--type")] = [],
    include_hidden: Annotated[bool, typer.Option("--include-hidden/--no-hidden")] = True,
    follow_symlinks: Annotated[bool, typer.Option("--follow-symlinks")] = False,
    symlink_policy: Annotated[Symlink_policy, typer.Option("--symlink-policy")] = Symlink_policy.skip,
    max_depth: Annotated[int | None, typer.Option("--max-depth")] = None,
    max_filesize: Annotated[int | None, typer.Option("--max-filesize")] = None,
    ignore_file: Annotated[str | None, typer.Option("--ignore-file")] = None,
    no_ignore: Annotated[bool, typer.Option("--no-ignore")] = False,
    regex: Annotated[bool, typer.Option("-F", "--fixed-strings")] = True,
    regex_2: Annotated[bool, typer.Option("-E", "--regex")] = False,
    ignore_case: Annotated[bool, typer.Option("-i", "--ignore-case")] = False,
    ignore_case_2: Annotated[bool, typer.Option("-s", "--case-sensitive")] = True,
    smart_case: Annotated[bool, typer.Option("-S", "--smart-case")] = False,
    word: Annotated[bool, typer.Option("-w", "--word")] = False,
    multiline: Annotated[bool, typer.Option("-U", "--multiline")] = False,
    preserve_case: Annotated[bool, typer.Option("--preserve-case")] = False,
    upper: Annotated[bool, typer.Option("--upper")] = False,
    lower: Annotated[bool, typer.Option("--lower")] = False,
    capitalize: Annotated[bool, typer.Option("--capitalize")] = False,
    env_expand: Annotated[bool, typer.Option("--env")] = False,
    replace_from: Annotated[str | None, typer.Option("--replace-from")] = None,
    literal_replace: Annotated[bool, typer.Option("--literal-replace")] = False,
    max_count: Annotated[int | None, typer.Option("--max-count")] = None,
    rules_file: Annotated[str | None, typer.Option("--rules")] = None,
    prompt: Annotated[bool, typer.Option("-p", "--prompt")] = False,
    backup: Annotated[str | None, typer.Option("--backup")] = None,
    backup_dir: Annotated[str | None, typer.Option("--backup-dir")] = None,
    undo_journal: Annotated[str | None, typer.Option("--undo-journal")] = None,
    undo: Annotated[bool, typer.Option("--undo")] = False,
    require_clean_git: Annotated[bool, typer.Option("--require-clean-git")] = False,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    diff: Annotated[bool, typer.Option("--diff")] = False,
    color: Annotated[Color, typer.Option("--color")] = Color.auto,
    quiet: Annotated[bool, typer.Option("-q", "--quiet")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose")] = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    count: Annotated[bool, typer.Option("-c", "--count")] = False,
    stats: Annotated[bool, typer.Option("--stats")] = False,
    context: Annotated[int | None, typer.Option("--context")] = None,
    exit_code: Annotated[bool, typer.Option("--exit-code")] = False,
    check: Annotated[bool, typer.Option("--check")] = False,
    files_with_matches: Annotated[bool, typer.Option("-l", "--files-with-matches")] = False,
    files_without_match: Annotated[bool, typer.Option("-L", "--files-without-match")] = False,
    jobs: Annotated[int | None, typer.Option("-j", "--jobs")] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
    encoding: Annotated[str | None, typer.Option("--encoding")] = None,
    version: Annotated[bool, typer.Option("--version", callback=_version_callback, is_eager=True, help="show program's version number and exit")] = False,
) -> None:
    if sum((bool(files_with_matches), bool(files_without_match),)) > 1:
        raise typer.BadParameter("-l, -L are mutually exclusive")
    _STATE.update(search=search, replace=replace, remove=remove, dry_run=dry_run, apply=apply, files=files, files_from=files_from, globs=globs, excludes=excludes, types=types, include_hidden=include_hidden, follow_symlinks=follow_symlinks, symlink_policy=symlink_policy, max_depth=max_depth, max_filesize=max_filesize, ignore_file=ignore_file, no_ignore=no_ignore, regex=regex, ignore_case=ignore_case, smart_case=smart_case, word=word, multiline=multiline, preserve_case=preserve_case, upper=upper, lower=lower, capitalize=capitalize, env_expand=env_expand, replace_from=replace_from, literal_replace=literal_replace, max_count=max_count, rules_file=rules_file, prompt=prompt, backup=backup, backup_dir=backup_dir, undo_journal=undo_journal, undo=undo, require_clean_git=require_clean_git, confirm=confirm, diff=diff, color=color, quiet=quiet, verbose=verbose, json_out=json_out, count=count, stats=stats, context=context, exit_code=exit_code, check=check, files_with_matches=files_with_matches, files_without_match=files_without_match, jobs=jobs, config=config, encoding=encoding)
    _run()


if __name__ == "__main__":
    app()
