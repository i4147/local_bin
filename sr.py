#!/data/data/com.termux/files/usr/bin/env python
"""
sr — recursive search & replace (modern Python 3.10+ / Typer rewrite).

Examples:
    sr "TODO"                          # search current dir
    sr "TODO" src/ tests/              # explicit targets
    sr "TODO" -r "FIXME" -t python     # replace in .py files
    sr --python "TODO" -r "FIXME"      # legacy flag style (still works)
    sr -w -i "hello"                   # whole-word, case-insensitive
    sr --json "TODO" | jq              # machine-readable
    sr -C 2 "error" -t py -t js        # grep-style context
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import sys
import tempfile
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


__version__: str = "2.0.0"

# ---------------------------------------------------------------- constants

IGNORE_DIRS = frozenset({
    "autom4te.cache",
    "blib",
    "_build",
    ".bzr",
    ".cdv",
    "cover_db",
    "CVS",
    "_darcs",
    "~.dep",
    "~.dot",
    ".git",
    ".hg",
    "~.nib",
    ".pc",
    "~.plst",
    "RCS",
    "SCCS",
    "_sgbak",
    ".svn",
})

IGNORE_FILE_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"~$",
        r"#.+#$",
        r"[._].*\.swp$",
        r"core\.\d+$",
        r"\.pyc$",
    )
)

BACKUP_RE = re.compile(r"\.\d{3}~$")
DEFAULT_OUTPUT_LIMIT: int = 320

FILE_TYPES: dict[str, list[str]] = {
    "actionscript": [".as", ".mxml"],
    "asm": [".asm", ".s"],
    "batch": [".bat", ".cmd"],
    "cc": [".c", ".h", ".xs"],
    "cfmx": [".cfc", ".cfm", ".cfml"],
    "cpp": [".cpp", ".cc", ".cxx", ".m", ".hpp", ".hh", ".h", ".hxx"],
    "csharp": [".cs"],
    "css": [".css"],
    "elisp": [".el"],
    "erlang": [".erl"],
    "fortran": [".f", ".f77", ".f90", ".f95", ".f03", ".for", ".ftn", ".fpp"],
    "haskell": [".hs", ".lhs"],
    "hh": [".h"],
    "html": [".htm", ".html", ".shtml", ".xhtml"],
    "java": [".java", ".properties"],
    "js": [".js"],
    "jsp": [".jsp", ".jspx", ".jhtm", ".jhtml"],
    "lisp": [".lisp", ".lsp"],
    "lua": [".lua"],
    "mason": [".mas", ".mhtml", ".mpl", ".mtxt"],
    "objc": [".m", ".h"],
    "objcpp": [".mm", ".h"],
    "ocaml": [".ml", ".mli"],
    "parrot": [".pir", ".pasm", ".pmc", ".ops", ".pod", ".pg", ".tg"],
    "perl": [".pl", ".pm", ".pod", ".t"],
    "php": [".php", ".phpt", ".php3", ".php4", ".php5"],
    "plone": [".pt", ".cpt", ".metadata", ".cpy"],
    "python": [".py"],
    "ruby": [".rb", ".rhtml", ".rjs", ".rxml", ".erb"],
    "scheme": [".scm"],
    "shell": [".sh", ".bash", ".csh", ".tcsh", ".ksh", ".zsh"],
    "smalltalk": [".st"],
    "sql": [".sql", ".ctl"],
    "tcl": [".tcl", ".itcl", ".itk"],
    "tex": [".tex", ".cls", ".sty"],
    "tt": [".tt", ".tt2", ".ttml"],
    "vb": [".bas", ".cls", ".frm", ".ctl", ".vb", ".resx"],
    "vim": [".vim"],
    "xml": [".xml", ".dtd", ".xslt", ".ent"],
    "yaml": [".yaml", ".yml"],
}

# ------------------------------------------------------------------ config


@dataclass
class Config:
    "Store mode, search, eliminate, replace, filenames and related state."

    mode: str = "search"
    search: re.Pattern[str] = field(default_factory=lambda: re.compile(""))
    eliminate: re.Pattern[str] | None = None
    replace: str | None = None
    filenames: bool = False
    explain: bool = False
    backup: bool = False
    interactive: bool = False
    output_limit: int = DEFAULT_OUTPUT_LIMIT
    targets: list[Path] = field(default_factory=list)
    exclude_patterns: list[re.Pattern] = field(default_factory=list)
    exclude_dirs: set[str] = field(default_factory=set)
    allow_exts: set[str] = field(default_factory=set)
    disallow_exts: set[str] = field(default_factory=set)
    ignore_file_patterns: list[str] = field(default_factory=list)
    no_ignore: bool = False
    hidden: bool = False
    follow: bool = False
    max_depth: int | None = None
    max_count: int | None = None
    before_context: int = 0
    after_context: int = 0
    only_matching: bool = False
    invert: bool = False
    quiet: bool = False
    count: bool = False
    json_output: bool = False
    stats: bool = False


# ------------------------------------------------------------------ helpers


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def isbackup(name: str) -> bool:
    """Return whether isbackup."""
    return bool(BACKUP_RE.search(name))


def line_matches(line: str, cfg: Config) -> bool:
    if cfg.eliminate and cfg.eliminate.search(line):
        return False
    hit = bool(cfg.search.search(line))
    return not hit if cfg.invert else hit


def matches_glob(name: str, patterns: list[str]) -> bool:
    """Return whether matches glob."""
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def make_console(color_mode: str) -> Console:
    "Create console."
    if color_mode == "never":
        return Console(color_system=None, force_terminal=False, highlight=False)
    if color_mode == "always":
        return Console(color_system="standard", force_terminal=True, highlight=False)
    return Console(highlight=False)


def highlight_matches(line: str, pattern: re.Pattern) -> Text:
    """Highlight matches."""
    text = Text()
    last = 0
    for m in pattern.finditer(line):
        if m.start() > last:
            text.append(line[last : m.start()])
        text.append(m.group(0), style="bold black on yellow")
        last = m.end()
    if last < len(line):
        text.append(line[last:])
    return text


def print_match_line(lineno: int, line: str, cfg: Config, console: Console) -> None:
    "Print match line."
    if len(line) > cfg.output_limit:
        console.print(f"[dim]{lineno}:[/dim]LINE IS TOO LONG (>{cfg.output_limit})")
        return
    out = Text()
    out.append(f"{lineno}:", style="dim")
    if cfg.invert:
        out.append(line)
    else:
        out.append(highlight_matches(line, cfg.search))
    console.print(out)


def print_context_line(lineno: int, line: str, cfg: Config, console: Console) -> None:
    "Print context line."
    if len(line) > cfg.output_limit:
        return
    out = Text()
    out.append(f"{lineno}-", style="dim")
    out.append(line, style="dim")
    console.print(out)


def print_only_matching(lineno: int, line: str, cfg: Config, console: Console) -> None:
    "Print only matching."
    for m in cfg.search.finditer(line):
        out = Text()
        out.append(f"{lineno}:", style="dim")
        out.append(m.group(0), style="bold black on yellow")
        console.print(out)


# ------------------------------------------------------------- walk / filter


def should_process_file(fname: str, cfg: Config) -> bool:
    """Return whether should process file."""
    if isbackup(fname):
        return False
    if not cfg.hidden and fname.startswith("."):
        return False
    if any(p.search(fname) for p in IGNORE_FILE_PATTERNS):
        return False
    if any(p.search(fname) for p in cfg.exclude_patterns):
        return False
    if matches_glob(fname, cfg.ignore_file_patterns):
        return False
    ext = Path(fname).suffix
    if ext in cfg.disallow_exts:
        return False
    return not (cfg.allow_exts and ext not in cfg.allow_exts)


def should_enter_dir(dirname: str, cfg: Config) -> bool:
    """Return whether should enter dir."""
    if not cfg.no_ignore and dirname in IGNORE_DIRS:
        return False
    if not cfg.hidden and dirname.startswith("."):
        return False
    if dirname in cfg.exclude_dirs:
        return False
    if any(p.search(dirname) for p in cfg.exclude_patterns):
        return False
    return not matches_glob(dirname + "/", cfg.ignore_file_patterns)


def iter_files(cfg: Config) -> Iterator[Path]:
    "Yield files from the supplied input."
    for target in cfg.targets:
        try:
            target = target.resolve()
        except OSError:
            continue
        if target.is_file():
            if (cfg.follow or not target.is_symlink()) and should_process_file(target.name, cfg):
                yield target
        elif target.is_dir():
            yield from walk_dir(target, cfg)
        else:
            print(f"Warning: {target} does not exist", file=sys.stderr)


def walk_dir(root: Path, cfg: Config):
    "Walk dir."
    for dirpath, dirnames, filenames in os.walk(root, followlinks=cfg.follow):
        if cfg.max_depth is not None:
            try:
                depth = len(Path(dirpath).relative_to(root).parts)
            except ValueError:
                depth = 0
            if depth >= cfg.max_depth:
                dirnames[:] = []
                continue

        dirnames[:] = [d for d in dirnames if should_enter_dir(d, cfg)]

        for fname in filenames:
            if not should_process_file(fname, cfg):
                continue
            fpath = Path(dirpath) / fname
            if fpath.is_file() and (cfg.follow or not fpath.is_symlink()):
                yield fpath


# ------------------------------------------------------------------- search


def search_file(path: Path, cfg: Config, console: Console) -> tuple[int, bool]:
    "Search for file."
    if cfg.json_output:
        return _search_file_json(path, cfg)

    before_buf: deque[tuple[int, str]] = deque(maxlen=cfg.before_context)
    after_remaining = 0
    last_printed = 0
    match_count = 0
    header_printed = False

    try:
        f = open(path, "r", encoding="utf-8", errors="surrogateescape")
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 0, False

    with f:
        for lineno, raw in enumerate(f, 1):
            line = raw.rstrip("\r\n")
            is_match = line_matches(line, cfg)

            if is_match:
                match_count += 1
                if cfg.quiet:
                    return match_count, True

                if cfg.count or cfg.filenames:
                    if cfg.max_count and match_count >= cfg.max_count:
                        break
                    if cfg.before_context:
                        before_buf.append((lineno, line))
                    continue

                if not header_printed:
                    console.print(relpath(path), style="green bold")
                    header_printed = True

                for bl_no, bl in before_buf:
                    if bl_no > last_printed:
                        print_context_line(bl_no, bl, cfg, console)
                        last_printed = bl_no

                if cfg.only_matching:
                    print_only_matching(lineno, line, cfg, console)
                else:
                    print_match_line(lineno, line, cfg, console)
                last_printed = lineno
                after_remaining = cfg.after_context

                if cfg.max_count and match_count >= cfg.max_count:
                    break

            elif after_remaining > 0 and not (cfg.count or cfg.filenames):
                print_context_line(lineno, line, cfg, console)
                last_printed = lineno
                after_remaining -= 1

            if cfg.before_context:
                before_buf.append((lineno, line))

    if cfg.count and match_count > 0:
        console.print(f"[green]{relpath(path)}[/green]:{match_count}")
    if cfg.filenames and match_count > 0:
        console.print(relpath(path), style="green")

    printed = header_printed or (match_count > 0 and (cfg.count or cfg.filenames))
    return match_count, printed


def _search_file_json(path: Path, cfg: Config) -> tuple[int, bool]:
    match_count = 0
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.rstrip("\r\n")
                if not line_matches(line, cfg):
                    continue
                match_count += 1
                if cfg.filenames:
                    break
                if not cfg.count:
                    print(json.dumps({"path": str(path), "line": lineno, "text": line}))
                if cfg.max_count and match_count >= cfg.max_count:
                    break
    except OSError as e:
        print(json.dumps({"error": str(e), "path": str(path)}), file=sys.stderr)
        return 0, False

    if cfg.filenames and match_count > 0:
        print(json.dumps({"path": str(path)}))
    elif cfg.count and match_count > 0:
        print(json.dumps({"path": str(path), "count": match_count}))
    return match_count, match_count > 0


# ------------------------------------------------------------------ replace


def replace_file(path: Path, cfg: Config, console: Console) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            data = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        return 0

    matches = list(cfg.search.finditer(data))
    count = len(matches)
    if cfg.max_count:
        count = min(count, cfg.max_count)
    if count == 0:
        return 0

    if cfg.json_output:
        print(json.dumps({"path": str(path), "replacements": count, "dry_run": cfg.explain}))
    else:
        noun = "replacement" if count == 1 else "replacements"
        console.print(f"[green]{relpath(path)}[/green] ({count} {noun})")

    if cfg.explain:
        return count

    if cfg.interactive and not Confirm.ask(f"Replace {count} occurrence(s) in {path}?", default=True):
        return 0

    replacement = "" if cfg.replace is None else cfg.replace
    new_data = cfg.search.sub(replacement, data, count=cfg.max_count or 0)

    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".sr~")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as tf:
                tf.write(new_data)

            if cfg.backup:
                backup = _next_backup_path(path)
                if backup is None:
                    console.print(f"[red]Can't create backup for {path}; skipping[/red]")
                    return 0
                shutil.copy2(path, backup)

            os.replace(tmp_path, path)
        finally:
            tmp_path.unlink(missing_ok=True)
    except OSError as e:
        print(f"Error writing {path}: {e}", file=sys.stderr)
        return 0

    return count


def _next_backup_path(path: Path) -> Path | None:
    for n in range(1000):
        candidate = path.with_name(f".{path.name}.{n:03d}~")
        if not candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------- CLI


def _preprocess_legacy_flags(argv: list[str]) -> list[str]:
    """Rewrite --python / --nopython → --include-type=python / --exclude-type=python."""
    out = []
    for arg in argv:
        if len(arg) > 4 and arg.startswith("--no") and arg[4:] in FILE_TYPES:
            out.append(f"--exclude-type={arg[4:]}")
        elif len(arg) > 2 and arg.startswith("--") and arg[2:] in FILE_TYPES:
            out.append(f"--include-type={arg[2:]}")
        else:
            out.append(arg)
    return out


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="sr",
        description="Recursively search and replace in files.",
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="Pattern [and target paths] (pattern optional if -s given)",
    )
    parser.add_argument(
        "-s",
        "--search",
        default=None,
        help="Pattern to search for (if omitted, first positional is used)",
    )
    parser.add_argument(
        "-r",
        "--replace",
        default=None,
        help="Replacement string (enables replace mode)",
    )
    parser.add_argument(
        "-e",
        "--eliminate",
        default=None,
        help="Skip lines matching this pattern",
    )
    parser.add_argument("-i", "--ignore-case", action="store_true", help="Case-insensitive matching")
    parser.add_argument(
        "-E",
        "--literal",
        action="store_true",
        help="Treat pattern as a literal string (no regex)",
    )
    parser.add_argument(
        "-w",
        "--word-regexp",
        action="store_true",
        help=r"Match whole words only (wraps pattern in \b)",
    )
    parser.add_argument(
        "--invert-match",
        dest="invert",
        action="store_true",
        help="Select non-matching lines (like grep -v)",
    )
    parser.add_argument(
        "-l",
        "--output-limit",
        type=int,
        default=None,
        help=f"Max line length before truncating (default: {DEFAULT_OUTPUT_LIMIT})",
    )
    parser.add_argument(
        "-n",
        "--filenames",
        "--files-with-matches",
        dest="filenames",
        action="store_true",
        help="Print only names of files containing matches",
    )
    parser.add_argument("-c", "--count", action="store_true", help="Print match count per file")
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="No output; exit code indicates whether matches were found",
    )
    parser.add_argument(
        "-o",
        "--only-matching",
        action="store_true",
        help="Print only the matching part of each line",
    )
    parser.add_argument(
        "-C",
        "--context",
        type=int,
        default=None,
        help="Show N lines of context around matches",
    )
    parser.add_argument(
        "-B",
        "--before-context",
        type=int,
        default=None,
        help="Show N lines before matches",
    )
    parser.add_argument(
        "-A",
        "--after-context",
        type=int,
        default=None,
        help="Show N lines after matches",
    )
    parser.add_argument(
        "-m",
        "--max-count",
        type=int,
        default=None,
        help="Stop after N matches per file",
    )
    parser.add_argument(
        "-x",
        "--exclude",
        action="append",
        default=[],
        help="Skip files/dirs matching this regex (repeatable)",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Skip directories with this name (repeatable)",
    )
    parser.add_argument(
        "-t",
        "--include-type",
        "--type",
        dest="include_type",
        action="append",
        default=[],
        help="Only process files of this type (repeatable)",
    )
    parser.add_argument(
        "-T",
        "--exclude-type",
        action="append",
        default=[],
        help="Skip files of this type (repeatable)",
    )
    parser.add_argument(
        "-f",
        "--flags",
        default=None,
        help="Extra regex flags as letters, e.g. 'ims'",
    )
    parser.add_argument(
        "-b",
        "--backup",
        action="store_true",
        help="Save .NNN~ backup before replacing",
    )
    parser.add_argument(
        "--dry-run",
        "--explain",
        dest="dry_run",
        action="store_true",
        help="Show what would change, don't modify files",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt before modifying each file",
    )
    parser.add_argument(
        "--ignore-file",
        type=Path,
        default=None,
        help="Load glob patterns from this file",
    )
    parser.add_argument(
        "--no-ignore",
        action="store_true",
        help="Don't use built-in ignore lists",
    )
    parser.add_argument(
        "--hidden",
        action="store_true",
        help="Include hidden files and directories",
    )
    parser.add_argument("--follow", action="store_true", help="Follow symbolic links")
    parser.add_argument("--max-depth", type=int, default=None, help="Maximum recursion depth")
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit results as JSON Lines",
    )
    parser.add_argument("--stats", action="store_true", help="Print summary table at end")
    parser.add_argument("--color", default="auto", help="Color mode: auto|always|never")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose diagnostics")
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"sr {__version__}",
        help="Show version and exit",
    )
    parser.add_argument(
        "--help-types",
        "--list-types",
        dest="help_types",
        action="store_true",
        help="List available file types and exit",
    )
    return parser


def main(
    args: list[str] | None = None,
    search: str | None = None,
    replace: str | None = None,
    eliminate: str | None = None,
    ignore_case: bool = False,
    literal: bool = False,
    word_regexp: bool = False,
    invert: bool = False,
    output_limit: int | None = None,
    filenames: bool = False,
    count: bool = False,
    quiet: bool = False,
    only_matching: bool = False,
    context: int | None = None,
    before_context: int | None = None,
    after_context: int | None = None,
    max_count: int | None = None,
    exclude: list[str] | None = None,
    exclude_dir: list[str] | None = None,
    include_type: list[str] | None = None,
    exclude_type: list[str] | None = None,
    flags: str | None = None,
    backup: bool = False,
    dry_run: bool = False,
    interactive: bool = False,
    ignore_file: Path | None = None,
    no_ignore: bool = False,
    hidden: bool = False,
    follow: bool = False,
    max_depth: int | None = None,
    json_output: bool = False,
    stats: bool = False,
    color: str = "auto",
    verbose: bool = False,
    help_types: bool = False,
) -> None:
    "Run the command-line interface."
    exclude = exclude or []
    exclude_dir = exclude_dir or []
    include_type = include_type or []
    exclude_type = exclude_type or []

    if help_types:
        _display_file_types()
        raise SystemExit(0)

    color = color.lower()
    if color not in ("auto", "always", "never"):
        print(f"Error: --color must be auto|always|never, got {color!r}", file=sys.stderr)
        raise SystemExit(2)

    console = make_console(color)

    # ---- Resolve positional vs -s ----
    args = args or []
    if search is None:
        if not args:
            print("Error: no search pattern", file=sys.stderr)
            raise SystemExit(2)
        pattern_str = args[0]
        target_strs = args[1:]
    else:
        pattern_str = search
        target_strs = args
    if not target_strs:
        target_strs = ["."]

    # ---- Build regex ----
    if literal:
        pattern_str = re.escape(pattern_str)
    if word_regexp:
        pattern_str = rf"\b(?:{pattern_str})\b"

    flag_bits = 0
    if ignore_case:
        flag_bits |= re.IGNORECASE
    if flags:
        for ch in flags:
            try:
                val = getattr(re, ch.upper())
                if not isinstance(val, re.RegexFlag):
                    raise AttributeError
            except AttributeError:
                print(f"Error: unknown regex flag {ch!r}", file=sys.stderr)
                raise SystemExit(2)
            flag_bits |= val

    try:
        search_re = re.compile(pattern_str, flag_bits)
    except re.error as e:
        print(f"Error: invalid pattern: {e}", file=sys.stderr)
        raise SystemExit(2)

    eliminate_re = None
    if eliminate:
        try:
            eliminate_re = re.compile(eliminate, flag_bits)
        except re.error as e:
            print(f"Error: invalid --eliminate pattern: {e}", file=sys.stderr)
            raise SystemExit(2)

    # ---- Validate combos ----
    if replace is not None and (filenames or count):
        print("Error: --replace can't be combined with --filenames/--count", file=sys.stderr)
        raise SystemExit(2)
    if replace is not None and invert:
        print("Error: --replace can't be combined with --invert-match", file=sys.stderr)
        raise SystemExit(2)
    if replace is not None and only_matching:
        print("Error: --replace can't be combined with --only-matching", file=sys.stderr)
        raise SystemExit(2)
    if only_matching and invert:
        print("Error: --only-matching can't be combined with --invert-match", file=sys.stderr)
        raise SystemExit(2)

    # ---- Build config ----
    cfg = Config(
        mode="replace" if replace is not None else "search",
        search=search_re,
        eliminate=eliminate_re,
        replace=replace,
        filenames=filenames,
        explain=dry_run,
        backup=backup,
        interactive=interactive,
        output_limit=output_limit if output_limit is not None else DEFAULT_OUTPUT_LIMIT,
        targets=[Path(t) for t in target_strs],
        exclude_patterns=[_compile_or_die(p) for p in exclude],
        exclude_dirs=set(exclude_dir),
        ignore_file_patterns=_load_ignore_file(ignore_file) if ignore_file else [],
        no_ignore=no_ignore,
        hidden=hidden,
        follow=follow,
        max_depth=max_depth,
        max_count=max_count,
        before_context=before_context or context or 0,
        after_context=after_context or context or 0,
        only_matching=only_matching,
        invert=invert,
        quiet=quiet,
        count=count,
        json_output=json_output,
        stats=stats,
    )

    for t in include_type:
        if t not in FILE_TYPES:
            print(f"Error: unknown file type {t!r}. See --help-types.", file=sys.stderr)
            raise SystemExit(2)
        cfg.allow_exts.update(FILE_TYPES[t])
    for t in exclude_type:
        if t not in FILE_TYPES:
            print(f"Error: unknown file type {t!r}. See --help-types.", file=sys.stderr)
            raise SystemExit(2)
        cfg.disallow_exts.update(FILE_TYPES[t])

    if verbose:
        console.print(f"allow exts:    {sorted(cfg.allow_exts)}")
        console.print(f"disallow exts: {sorted(cfg.disallow_exts)}")
        console.print(f"targets:       {[str(t) for t in cfg.targets]}")

    # ---- Run ----
    total_files = matched_files = total_matches = 0

    for path in iter_files(cfg):
        total_files += 1
        if cfg.mode == "search":
            n, _ = search_file(path, cfg, console)
        else:
            n = replace_file(path, cfg, console)
        total_matches += n
        if n > 0:
            matched_files += 1

    if cfg.stats:
        if cfg.json_output:
            print(
                json.dumps({
                    "summary": {
                        "files_scanned": total_files,
                        "files_matched": matched_files,
                        "total_matches": total_matches,
                    }
                }),
                file=sys.stderr,
            )
        else:
            table = Table(title="Summary", show_header=False)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="bold")
            table.add_row("Files scanned", str(total_files))
            table.add_row("Files matched", str(matched_files))
            table.add_row("Total matches", str(total_matches))
            console.print(table)

    if (cfg.quiet or cfg.count or cfg.filenames) and total_matches == 0:
        raise SystemExit(1)


def _compile_or_die(pattern: str) -> re.Pattern:
    try:
        return re.compile(pattern)
    except re.error as e:
        print(f"Error: invalid regex {pattern!r}: {e}", file=sys.stderr)
        raise SystemExit(2)


def _load_ignore_file(path: Path) -> list[str]:
    patterns: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line)
    except OSError as e:
        print(f"Error: cannot read ignore file {path}: {e}", file=sys.stderr)
        raise SystemExit(2)
    return patterns


def _display_file_types() -> None:
    print("Available file types (-t TYPE to include, -T TYPE to exclude):")
    for ftype in sorted(FILE_TYPES):
        print(f"  {ftype:12s}: {', '.join(FILE_TYPES[ftype])}")


def cli(argv: list[str] | None = None) -> None:
    """Entry point that preprocesses legacy --python / --nopython flags."""
    raw = sys.argv[1:] if argv is None else argv
    ns = build_parser().parse_args(_preprocess_legacy_flags(raw))
    main(**vars(ns))


if __name__ == "__main__":
    cli()
