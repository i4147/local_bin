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

import fnmatch
import json
import os
import re
import shutil
import sys
import tempfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

__version__ = "2.0.0"

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
DEFAULT_OUTPUT_LIMIT = 320

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
    mode: str = "search"
    search: re.Pattern = None
    eliminate: Optional[re.Pattern] = None
    replace: Optional[str] = None
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
    max_depth: Optional[int] = None
    max_count: Optional[int] = None
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
    return bool(BACKUP_RE.search(name))


def line_matches(line: str, cfg: Config) -> bool:
    if cfg.eliminate and cfg.eliminate.search(line):
        return False
    hit = bool(cfg.search.search(line))
    return not hit if cfg.invert else hit


def matches_glob(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def make_console(color_mode: str) -> Console:
    if color_mode == "never":
        return Console(color_system=None, force_terminal=False, highlight=False)
    if color_mode == "always":
        return Console(color_system="standard", force_terminal=True, highlight=False)
    return Console(highlight=False)


def highlight_matches(line: str, pattern: re.Pattern) -> Text:
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
    if len(line) > cfg.output_limit:
        return
    out = Text()
    out.append(f"{lineno}-", style="dim")
    out.append(line, style="dim")
    console.print(out)


def print_only_matching(lineno: int, line: str, cfg: Config, console: Console) -> None:
    for m in cfg.search.finditer(line):
        out = Text()
        out.append(f"{lineno}:", style="dim")
        out.append(m.group(0), style="bold black on yellow")
        console.print(out)


# ------------------------------------------------------------- walk / filter


def should_process_file(fname: str, cfg: Config) -> bool:
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
    ext = os.path.splitext(fname)[1]
    if ext in cfg.disallow_exts:
        return False
    if cfg.allow_exts and ext not in cfg.allow_exts:
        return False
    return True


def should_enter_dir(dirname: str, cfg: Config) -> bool:
    if not cfg.no_ignore and dirname in IGNORE_DIRS:
        return False
    if not cfg.hidden and dirname.startswith("."):
        return False
    if dirname in cfg.exclude_dirs:
        return False
    if any(p.search(dirname) for p in cfg.exclude_patterns):
        return False
    if matches_glob(dirname + "/", cfg.ignore_file_patterns):
        return False
    return True


def iter_files(cfg: Config):
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

    if cfg.interactive:
        if not typer.confirm(f"Replace {count} occurrence(s) in {path}?", default=True):
            return 0

    new_data = cfg.search.sub(cfg.replace, data, count=cfg.max_count or 0)

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


def _next_backup_path(path: Path) -> Optional[Path]:
    for n in range(1000):
        candidate = path.with_name(f".{path.name}.{n:03d}~")
        if not candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------- CLI


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"sr {__version__}")
        raise typer.Exit()


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


app = typer.Typer(
    name="sr",
    help="Recursively search and replace in files.",
    add_completion=True,
    rich_markup_mode="rich",
)


@app.command(context_settings={"help_option_names": ["-h", "--help"]})
def main(
    args: Optional[list[str]] = typer.Argument(None, help="Pattern [and target paths] (pattern optional if -s given)"),
    search: Optional[str] = typer.Option(
        None, "-s", "--search", help="Pattern to search for (if omitted, first positional is used)"
    ),
    replace: Optional[str] = typer.Option(None, "-r", "--replace", help="Replacement string (enables replace mode)"),
    eliminate: Optional[str] = typer.Option(None, "-e", "--eliminate", help="Skip lines matching this pattern"),
    ignore_case: bool = typer.Option(False, "-i", "--ignore-case", help="Case-insensitive matching"),
    literal: bool = typer.Option(False, "-E", "--literal", help="Treat pattern as a literal string (no regex)"),
    word_regexp: bool = typer.Option(
        False, "-w", "--word-regexp", help=r"Match whole words only (wraps pattern in \b)"
    ),
    invert: bool = typer.Option(False, "--invert-match", help="Select non-matching lines (like grep -v)"),
    output_limit: Optional[int] = typer.Option(
        None, "-l", "--output-limit", help=f"Max line length before truncating (default: {DEFAULT_OUTPUT_LIMIT})"
    ),
    filenames: bool = typer.Option(
        False, "-n", "--filenames", "--files-with-matches", help="Print only names of files containing matches"
    ),
    count: bool = typer.Option(False, "-c", "--count", help="Print match count per file"),
    quiet: bool = typer.Option(
        False, "-q", "--quiet", help="No output; exit code indicates whether matches were found"
    ),
    only_matching: bool = typer.Option(
        False, "-o", "--only-matching", help="Print only the matching part of each line"
    ),
    context: Optional[int] = typer.Option(None, "-C", "--context", help="Show N lines of context around matches"),
    before_context: Optional[int] = typer.Option(None, "-B", "--before-context", help="Show N lines before matches"),
    after_context: Optional[int] = typer.Option(None, "-A", "--after-context", help="Show N lines after matches"),
    max_count: Optional[int] = typer.Option(None, "-m", "--max-count", help="Stop after N matches per file"),
    exclude: list[str] = typer.Option([], "-x", "--exclude", help="Skip files/dirs matching this regex (repeatable)"),
    exclude_dir: list[str] = typer.Option([], "--exclude-dir", help="Skip directories with this name (repeatable)"),
    include_type: list[str] = typer.Option(
        [], "-t", "--include-type", "--type", help="Only process files of this type (repeatable)"
    ),
    exclude_type: list[str] = typer.Option([], "-T", "--exclude-type", help="Skip files of this type (repeatable)"),
    flags: Optional[str] = typer.Option(None, "-f", "--flags", help="Extra regex flags as letters, e.g. 'ims'"),
    backup: bool = typer.Option(False, "-b", "--backup", help="Save .NNN~ backup before replacing"),
    dry_run: bool = typer.Option(False, "--dry-run", "--explain", help="Show what would change, don't modify files"),
    interactive: bool = typer.Option(False, "--interactive", help="Prompt before modifying each file"),
    ignore_file: Optional[Path] = typer.Option(None, "--ignore-file", help="Load glob patterns from this file"),
    no_ignore: bool = typer.Option(False, "--no-ignore", help="Don't use built-in ignore lists"),
    hidden: bool = typer.Option(False, "--hidden", help="Include hidden files and directories"),
    follow: bool = typer.Option(False, "--follow", help="Follow symbolic links"),
    max_depth: Optional[int] = typer.Option(None, "--max-depth", help="Maximum recursion depth"),
    json_output: bool = typer.Option(False, "--json", help="Emit results as JSON Lines"),
    stats: bool = typer.Option(False, "--stats", help="Print summary table at end"),
    color: str = typer.Option("auto", "--color", help="Color mode: auto|always|never"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose diagnostics"),
    version: Optional[bool] = typer.Option(
        None, "--version", "-V", callback=_version_callback, is_eager=True, help="Show version and exit"
    ),
    help_types: bool = typer.Option(False, "--help-types", "--list-types", help="List available file types and exit"),
) -> None:
    if help_types:
        _display_file_types()
        raise typer.Exit()

    color = color.lower()
    if color not in ("auto", "always", "never"):
        typer.echo(f"Error: --color must be auto|always|never, got {color!r}", err=True)
        raise typer.Exit(2)

    console = make_console(color)

    # ---- Resolve positional vs -s ----
    args = args or []
    if search is None:
        if not args:
            typer.echo("Error: no search pattern", err=True)
            raise typer.Exit(2)
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
                typer.echo(f"Error: unknown regex flag {ch!r}", err=True)
                raise typer.Exit(2)
            flag_bits |= val

    try:
        search_re = re.compile(pattern_str, flag_bits)
    except re.error as e:
        typer.echo(f"Error: invalid pattern: {e}", err=True)
        raise typer.Exit(2)

    eliminate_re = None
    if eliminate:
        try:
            eliminate_re = re.compile(eliminate, flag_bits)
        except re.error as e:
            typer.echo(f"Error: invalid --eliminate pattern: {e}", err=True)
            raise typer.Exit(2)

    # ---- Validate combos ----
    if replace is not None and (filenames or count):
        typer.echo("Error: --replace can't be combined with --filenames/--count", err=True)
        raise typer.Exit(2)
    if replace is not None and invert:
        typer.echo("Error: --replace can't be combined with --invert-match", err=True)
        raise typer.Exit(2)
    if replace is not None and only_matching:
        typer.echo("Error: --replace can't be combined with --only-matching", err=True)
        raise typer.Exit(2)
    if only_matching and invert:
        typer.echo("Error: --only-matching can't be combined with --invert-match", err=True)
        raise typer.Exit(2)

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
            typer.echo(f"Error: unknown file type {t!r}. See --help-types.", err=True)
            raise typer.Exit(2)
        cfg.allow_exts.update(FILE_TYPES[t])
    for t in exclude_type:
        if t not in FILE_TYPES:
            typer.echo(f"Error: unknown file type {t!r}. See --help-types.", err=True)
            raise typer.Exit(2)
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
        raise typer.Exit(1)


def _compile_or_die(pattern: str) -> re.Pattern:
    try:
        return re.compile(pattern)
    except re.error as e:
        typer.echo(f"Error: invalid regex {pattern!r}: {e}", err=True)
        raise typer.Exit(2)


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
        typer.echo(f"Error: cannot read ignore file {path}: {e}", err=True)
        raise typer.Exit(2)
    return patterns


def _display_file_types() -> None:
    typer.echo("Available file types (-t TYPE to include, -T TYPE to exclude):")
    for ftype in sorted(FILE_TYPES):
        typer.echo(f"  {ftype:12s}: {', '.join(FILE_TYPES[ftype])}")


def cli() -> None:
    """Entry point that preprocesses legacy --python / --nopython flags."""
    sys.argv[1:] = _preprocess_legacy_flags(sys.argv[1:])
    app()


if __name__ == "__main__":
    cli()
