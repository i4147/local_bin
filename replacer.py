#!/data/data/com.termux/files/usr/bin/env python
from __future__ import annotations
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
from typing import Callable, Iterator, List, Optional, Sequence

from dh import SKIP_DIRS
from isbinary import is_binary
import typer

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
    max_depth: Optional[int] = None
    max_filesize: int = 10 * 1024 * 1024
    types: list[str] = field(default_factory=list)
    ignore_file: Optional[str] = None
    no_ignore: bool = False
    preserve_case: bool = False
    upper: bool = False
    lower: bool = False
    capitalize: bool = False
    env_expand: bool = False
    replace_from: Optional[str] = None
    literal_replace: bool = False
    max_count: Optional[int] = None
    prompt: bool = False
    backup: Optional[str] = None
    backup_dir: Optional[str] = None
    undo_journal: Optional[str] = None
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
    rules_file: Optional[str] = None
    files_from: Optional[str] = None
    encoding: Optional[str] = None


@dataclass
class Rule:
    pattern: re.Pattern[str]
    replacement: str
    preserve_case: bool = False
    upper: bool = False
    lower: bool = False
    capitalize: bool = False
    max_count: Optional[int] = None


@dataclass
class FileResult:
    path: str
    matches: int = 0
    changed: bool = False
    skipped: bool = False
    reason: Optional[str] = None
    diff: Optional[str] = None


def _colorize(text: str, color: str, when: str) -> str:
    if when == "never":
        return text
    if when == "auto" and (not sys.stdout.isatty() or os.environ.get("NO_COLOR")):
        return text
    codes = {
        "red": "31",
        "green": "32",
        "yellow": "33",
        "blue": "34",
        "magenta": "35",
        "cyan": "36",
        "bold": "1",
    }
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
    flags = 0
    if opts.ignore_case or (opts.smart_case and not any(c.isupper() for c in search_text)):
        flags |= re.IGNORECASE
    if opts.multiline:
        flags |= re.MULTILINE | re.DOTALL
    src = search_text if opts.regex else re.escape(search_text)
    if opts.word:
        src = r"\b" + src + r"\b"
    return re.compile(src, flags)


def _read_with_meta(path: Path, encoding: Optional[str]) -> Optional[tuple[str, str, bool, bytes]]:
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
        with contextlib.suppress(OSError):
            shutil.copystat(path, tmp)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
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
    return not is_binary(path)


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
    text, enc, _bom, raw = meta
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


def _normalize_config(data: dict) -> dict:
    return {k.replace("-", "_"): v for k, v in data.items()}


def _load_config(path: Optional[Path]) -> dict:
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
    if path.suffix in (".toml", "") and _toml is not None:
        try:
            return _normalize_config(_toml.loads(text))
        except Exception:
            pass
    try:
        return _normalize_config(json.loads(text))
    except json.JSONDecodeError:
        return {}


def _load_rules_file(path: Path) -> list[Rule]:
    text = path.read_text(encoding="utf-8")
    data: dict = {}
    if path.suffix in (".toml", "") and _toml is not None:
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


def _version_callback(value: bool) -> None:
    if value:
        typer.echo("replacer 2.0")
        raise typer.Exit


def _fail(msg: str, code: int = EXIT_USAGE) -> None:
    typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(code)


app = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    no_args_is_help=False,
)


@app.command()
def main(
    search: str = typer.Argument("", help="Text to search for"),
    replace: str = typer.Argument("", help="Replacement text (defaults to empty)"),
    remove: bool = typer.Option(False, "-r", "--remove", help="Remove the search text"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without applying"),
    apply: bool = typer.Option(False, "--apply", help="Invert --dry-run default"),
    files: list[str] = typer.Option([], "-f", "--file", help="Process only these files (repeatable)"),
    files_from: Optional[str] = typer.Option(None, "--files-from", help="Read file list from a file or '-'"),
    globs: list[str] = typer.Option([], "-g", "--glob", help="Include files matching glob (repeatable)"),
    excludes: list[str] = typer.Option([], "--exclude", help="Exclude files matching glob (repeatable)"),
    types: list[str] = typer.Option([], "--type", help="Only process files of these types (repeatable)"),
    include_hidden: Optional[bool] = typer.Option(None, "--include-hidden/--no-hidden", help="Include dotfiles"),
    follow_symlinks: Optional[bool] = typer.Option(
        None, "--follow-symlinks/--no-follow-symlinks", help="Follow symlinks"
    ),
    symlink_policy: Optional[str] = typer.Option(None, "--symlink-policy", help="skip|follow|error"),
    max_depth: Optional[int] = typer.Option(None, "--max-depth", help="Maximum directory depth"),
    max_filesize: Optional[int] = typer.Option(None, "--max-filesize", help="Skip files larger than N bytes"),
    ignore_file: Optional[str] = typer.Option(None, "--ignore-file", help="Path to .ignore file"),
    no_ignore: bool = typer.Option(False, "--no-ignore", help=f"Disable {DEFAULT_IGNORE_FILENAME}"),
    fixed_strings: bool = typer.Option(False, "-F", "--fixed-strings", help="Literal string search (default)"),
    regex: bool = typer.Option(False, "-E", "--regex", help="Treat SEARCH as regex"),
    ignore_case: bool = typer.Option(False, "-i", "--ignore-case", help="Case-insensitive"),
    case_sensitive: bool = typer.Option(False, "-s", "--case-sensitive", help="Explicit case-sensitive"),
    smart_case: bool = typer.Option(False, "-S", "--smart-case", help="Smart-case (insensitive unless uppercase)"),
    word: bool = typer.Option(False, "-w", "--word", help="Word-boundary matching"),
    multiline: bool = typer.Option(False, "-U", "--multiline", help="Multiline mode"),
    preserve_case: bool = typer.Option(False, "--preserve-case", help="Preserve matched text case"),
    upper: bool = typer.Option(False, "--upper", help="Uppercase replacement"),
    lower: bool = typer.Option(False, "--lower", help="Lowercase replacement"),
    capitalize: bool = typer.Option(False, "--capitalize", help="Capitalize replacement"),
    env_expand: bool = typer.Option(False, "--env", help="Expand $VAR in replacement"),
    replace_from: Optional[str] = typer.Option(None, "--replace-from", help="Read replacement from file"),
    literal_replace: bool = typer.Option(False, "--literal-replace", help="Don't expand backreferences"),
    max_count: Optional[int] = typer.Option(None, "--max-count", help="Max replacements per file"),
    rules_file: Optional[str] = typer.Option(None, "--rules", help="Load rules from TOML/JSON file"),
    prompt: bool = typer.Option(False, "-p", "--prompt", help="Interactive y/n/a/q per match"),
    backup: Optional[str] = typer.Option(None, "--backup", help="Create backup with given suffix (e.g. .bak)"),
    backup_dir: Optional[str] = typer.Option(None, "--backup-dir", help="Backup directory (implies --backup)"),
    undo_journal: Optional[str] = typer.Option(None, "--undo-journal", help="Journal path for --undo"),
    undo: bool = typer.Option(False, "--undo", help="Undo from journal"),
    require_clean_git: bool = typer.Option(False, "--require-clean-git", help="Refuse if git dirty"),
    confirm: bool = typer.Option(False, "--confirm", help="Prompt before applying"),
    diff: bool = typer.Option(False, "--diff", help="Show unified diff (implies --dry-run)"),
    color: Optional[str] = typer.Option(None, "--color", help="auto|always|never"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress non-error output"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
    count: bool = typer.Option(False, "-c", "--count", help="Print match count per file"),
    stats: bool = typer.Option(False, "--stats", help="Show statistics"),
    context: Optional[int] = typer.Option(None, "--context", help="Context chars for previews"),
    exit_code: bool = typer.Option(False, "--exit-code", help="Exit 1 if no matches/changes"),
    check: bool = typer.Option(False, "--check", help="Exit 0 if matches, 1 otherwise"),
    files_with_matches: bool = typer.Option(False, "-l", "--files-with-matches", help="Only print paths with matches"),
    files_without_match: bool = typer.Option(
        False, "-L", "--files-without-match", help="Only print paths without matches"
    ),
    jobs: Optional[int] = typer.Option(None, "-j", "--jobs", help="Parallel workers"),
    config: Optional[str] = typer.Option(None, "--config", help="Path to config file"),
    encoding: Optional[str] = typer.Option(None, "--encoding", help="Force file encoding"),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit"
    ),
) -> None:
    if fixed_strings and regex:
        _fail("--fixed-strings and --regex are mutually exclusive")
    if sum([ignore_case, case_sensitive, smart_case]) > 1:
        _fail("-i/--ignore-case, -s/--case-sensitive, -S/--smart-case are mutually exclusive")
    if files_with_matches and files_without_match:
        _fail("-l/--files-with-matches and -L/--files-without-match are mutually exclusive")
    if dry_run and apply:
        _fail("--dry-run and --apply are mutually exclusive")

    cfg = _load_config(Path(config) if config else None)

    def _b(val: bool, key: str) -> bool:
        if val:
            return True
        return bool(cfg.get(key, False))

    def _tb(val: Optional[bool], key: str, default: bool) -> bool:
        if val is not None:
            return val
        if key in cfg:
            return bool(cfg[key])
        return default

    def _s(val: Optional[str], key: str, default: Optional[str] = None) -> Optional[str]:
        if val is not None:
            return val
        return cfg.get(key, default)

    def _i(val: Optional[int], key: str, default: Optional[int] = None) -> Optional[int]:
        if val is not None:
            return val
        return cfg.get(key, default)

    def _l(val: list[str], key: str) -> list[str]:
        if val:
            return val
        return list(cfg.get(key, []))

    effective_undo_journal = _s(undo_journal, "undo_journal")
    if undo:
        if not effective_undo_journal:
            _fail("--undo requires --undo-journal")
        raise typer.Exit(_do_undo(Path(effective_undo_journal)))

    effective_color = _s(color, "color", "auto") or "auto"
    if effective_color not in ("auto", "always", "never"):
        _fail(f"invalid --color value: {effective_color}")

    effective_symlink_policy = _s(symlink_policy, "symlink_policy", "skip") or "skip"
    if effective_symlink_policy not in ("skip", "follow", "error"):
        _fail(f"invalid --symlink-policy value: {effective_symlink_policy}")

    effective_follow = _tb(follow_symlinks, "follow_symlinks", False)
    if effective_follow or effective_symlink_policy == "follow":
        effective_follow = True
        effective_symlink_policy = "follow"

    effective_backup = _s(backup, "backup")
    effective_backup_dir = _s(backup_dir, "backup_dir")
    if effective_backup_dir and not effective_backup:
        effective_backup = ".bak"

    effective_ignore_file = _s(ignore_file, "ignore_file")
    if not effective_ignore_file and not no_ignore and not cfg.get("no_ignore", False):
        default_ignore = Path(DEFAULT_IGNORE_FILENAME)
        if default_ignore.is_file():
            effective_ignore_file = str(default_ignore)

    effective_jobs = _i(jobs, "jobs", 1) or 1
    effective_jobs = max(effective_jobs, 1)
    if prompt and effective_jobs > 1:
        typer.echo("--prompt with --jobs>1 unsupported; forcing jobs=1", err=True)
        effective_jobs = 1

    effective_dry_run = (dry_run and not apply) or (bool(cfg.get("dry_run", False)) and not apply)
    if diff:
        effective_dry_run = True

    effective_include_hidden = _tb(include_hidden, "include_hidden", True)
    effective_max_filesize = _i(max_filesize, "max_filesize", 10 * 1024 * 1024) or 10 * 1024 * 1024
    effective_context = _i(context, "context", 20) or 20

    effective_rules_file = _s(rules_file, "rules_file")
    effective_search = search or cfg.get("search", "")
    if not effective_search and not effective_rules_file:
        _fail("provide SEARCH or --rules")

    opts = Options(
        search=effective_search,
        replace=replace or cfg.get("replace", ""),
        regex=regex or cfg.get("regex", False),
        ignore_case=ignore_case or cfg.get("ignore_case", False),
        smart_case=smart_case or cfg.get("smart_case", False),
        word=_b(word, "word"),
        multiline=_b(multiline, "multiline"),
        remove=_b(remove, "remove"),
        dry_run=effective_dry_run,
        apply=apply,
        files=_l(files, "files"),
        globs=_l(globs, "globs"),
        excludes=_l(excludes, "excludes"),
        include_hidden=effective_include_hidden,
        follow_symlinks=effective_follow,
        symlink_policy=effective_symlink_policy,
        max_depth=_i(max_depth, "max_depth"),
        max_filesize=effective_max_filesize,
        types=_l(types, "types"),
        ignore_file=effective_ignore_file,
        no_ignore=_b(no_ignore, "no_ignore"),
        preserve_case=_b(preserve_case, "preserve_case"),
        upper=_b(upper, "upper"),
        lower=_b(lower, "lower"),
        capitalize=_b(capitalize, "capitalize"),
        env_expand=_b(env_expand, "env_expand"),
        replace_from=_s(replace_from, "replace_from"),
        literal_replace=_b(literal_replace, "literal_replace"),
        max_count=_i(max_count, "max_count"),
        prompt=_b(prompt, "prompt"),
        backup=effective_backup,
        backup_dir=effective_backup_dir,
        undo_journal=effective_undo_journal,
        undo=undo,
        require_clean_git=_b(require_clean_git, "require_clean_git"),
        confirm=_b(confirm, "confirm"),
        diff=_b(diff, "diff"),
        color=effective_color,
        quiet=_b(quiet, "quiet"),
        verbose=_b(verbose, "verbose"),
        json_out=_b(json_out, "json_out"),
        count=_b(count, "count"),
        stats=_b(stats, "stats"),
        files_with_matches=files_with_matches,
        files_without_match=files_without_match,
        context=effective_context,
        exit_code=_b(exit_code, "exit_code"),
        check=_b(check, "check"),
        jobs=effective_jobs,
        rules_file=effective_rules_file,
        files_from=_s(files_from, "files_from"),
        encoding=_s(encoding, "encoding"),
    )

    if opts.require_clean_git and not _git_is_clean():
        typer.echo("Working tree is not clean; aborting", err=True)
        raise typer.Exit(EXIT_ABORT)

    rules = _build_rules(opts)
    if not rules:
        typer.echo("No rules to apply", err=True)
        raise typer.Exit(EXIT_USAGE)

    if opts.files == ["-"]:
        raise typer.Exit(_process_stdin(rules, opts))

    if opts.confirm and not opts.dry_run:
        typer.echo("Proceed with modifications? [y/N] ", err=True, nl=False)
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            raise typer.Exit(EXIT_ABORT)

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
        raise typer.Exit(EXIT_OK if matched else EXIT_NO_MATCH)
    if opts.exit_code:
        if opts.dry_run:
            raise typer.Exit(EXIT_OK if matched else EXIT_NO_MATCH)
        raise typer.Exit(EXIT_OK if changed else EXIT_NO_MATCH)
    raise typer.Exit(EXIT_OK)


if __name__ == "__main__":
    app()
