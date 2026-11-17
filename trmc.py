#!/data/data/com.termux/files/usr/bin/env python
"Strip comments (and optionally docstrings/blank lines) from Python sources."

from __future__ import annotations
import ast
from dataclasses import dataclass
import io
import multiprocessing as mp
import os
from pathlib import Path
import re
import sys
import tokenize
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any

import click
import typer
from dh import SKIP_DIRS, MAX_WORKERS

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterable, Sequence


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"{PROG} {__version__}")
        raise typer.Exit


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
        "Parse args."
        return _Args(**_STATE)

    def error(self, message):
        raise typer.BadParameter(message)

    def print_help(self, file=None):
        "Print help."
        typer.echo(click.get_current_context().get_help(), file=file)

    def print_usage(self, file=None):
        "Print usage."
        typer.echo(click.get_current_context().get_usage(), file=file)


try:
    import libcst as cst
except ImportError:
    sys.stderr.write("strip_comments: error: libcst is required. Install with: pip install libcst\n")
    raise SystemExit(2)

__version__ = "2.1.0"
PROG = "strip_comments"
BACKUP_SUFFIX = ".bak"
PY_SUFFIXES = frozenset({".py", ".pyi", ".pyw"})
PARALLEL_MIN_FILES = 4


@dataclass(frozen=True)
class TransformOptions:
    "Store remove_docstrings, collapse_blanks."

    remove_docstrings: bool = False
    collapse_blanks: bool = False


_PROTECTED_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^#!"),
    re.compile(r"\bcoding[:=]"),
    re.compile(r"-\*-.*coding.*-\*-"),
    re.compile(r"#\s*noqa\b", re.IGNORECASE),
    re.compile(r"#\s*nosec\b", re.IGNORECASE),
    re.compile(r"#\s*pragma\b", re.IGNORECASE),
    re.compile(r"#\s*fmt:"),
    re.compile(r"#\s*isort:"),
    re.compile(r"#\s*mypy:"),
    re.compile(r"#\s*pyright:"),
    re.compile(r"#\s*pytype:"),
    re.compile(r"#\s*pylint:"),
    re.compile(r"#\s*ruff:"),
    re.compile(r"#\s*flake8:"),
    re.compile(r"#\s*yapf:"),
)
_TYPE_PROTECTED = re.compile(r"#\s*type:(?!\s*ignore\b)")


def _is_protected_comment(value: str) -> bool:
    if _TYPE_PROTECTED.search(value):
        return True
    return any(rx.search(value) for rx in _PROTECTED_RES)


def _is_docstring_stmt(stmt: cst.CSTNode) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine):
        return False
    if len(stmt.body) != 1:
        return False
    expr = stmt.body[0]
    if not isinstance(expr, cst.Expr):
        return False
    if not isinstance(expr.value, cst.SimpleString):
        return False
    prefix = (expr.value.prefix or "").lower()
    return "b" not in prefix


def _strip_docstring_seq(body: Sequence[Any]) -> Sequence[Any]:
    if body and _is_docstring_stmt(body[0]):
        return body[1:]
    return body


def _strip_docstring_body(body: cst.CSTNode) -> cst.CSTNode:
    if isinstance(body, cst.IndentedBlock):
        new_body = _strip_docstring_seq(body.body)
        if new_body is not body.body:
            return body.with_changes(body=new_body)
    return body


class _Transformer(cst.CSTTransformer):
    def __init__(self, opts: TransformOptions) -> None:
        "Initialize the instance state from the supplied arguments."
        self.opts = opts

    def leave_TrailingWhitespace(
        self,
        original_node: cst.TrailingWhitespace,
        updated_node: cst.TrailingWhitespace,
    ) -> cst.TrailingWhitespace:
        comment = updated_node.comment
        if comment is not None and not _is_protected_comment(comment.value):
            return updated_node.with_changes(
                comment=None,
                whitespace=cst.SimpleWhitespace(""),
            )
        return updated_node

    def leave_EmptyLine(
        self,
        original_node: cst.EmptyLine,
        updated_node: cst.EmptyLine,
    ) -> cst.EmptyLine:
        comment = updated_node.comment
        if comment is not None and not _is_protected_comment(comment.value):
            return updated_node.with_changes(comment=None)
        return updated_node

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        if not self.opts.remove_docstrings:
            return updated_node
        new_body = _strip_docstring_seq(updated_node.body)
        if new_body is updated_node.body:
            return updated_node
        return updated_node.with_changes(body=new_body)

    def leave_ClassDef(
        self,
        original_node: cst.ClassDef,
        updated_node: cst.ClassDef,
    ) -> cst.ClassDef:
        if not self.opts.remove_docstrings:
            return updated_node
        new_body = _strip_docstring_body(updated_node.body)
        if new_body is updated_node.body:
            return updated_node
        return updated_node.with_changes(body=new_body)

    def leave_FunctionDef(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.FunctionDef:
        if not self.opts.remove_docstrings:
            return updated_node
        new_body = _strip_docstring_body(updated_node.body)
        if new_body is updated_node.body:
            return updated_node
        return updated_node.with_changes(body=new_body)


_STRING_TOKEN_TYPES: set[int] = {tokenize.STRING}
for _tname in ("FSTRING_MIDDLE", "TSTRING_MIDDLE"):
    _tv = getattr(tokenize, _tname, None)
    if _tv is not None:
        _STRING_TOKEN_TYPES.add(_tv)


def _multiline_string_lines(source: str) -> set[int]:
    protected: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type in _STRING_TOKEN_TYPES and tok.start[0] != tok.end[0]:
                for ln in range(tok.start[0], tok.end[0] + 1):
                    protected.add(ln)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    return protected


def _collapse_blank_lines(source: str) -> str:
    protected = _multiline_string_lines(source)
    lines = source.split("\n")
    ends_with_newline = source.endswith("\n")
    last_idx = len(lines)
    out: list[str] = []
    pending_blank = False
    for i, line in enumerate(lines):
        idx = i + 1
        if ends_with_newline and i == len(lines) - 1:
            if pending_blank:
                out.append("")
            pending_blank = False
            out.append(line)
            continue
        if idx in protected:
            if pending_blank:
                out.append("")
                pending_blank = False
            out.append(line)
            continue
        if line.strip() == "":
            pending_blank = True
        else:
            if pending_blank:
                out.append("")
                pending_blank = False
            out.append(line)
    if pending_blank:
        out.append("")
    return "\n".join(out)


def _transform(source: str, opts: TransformOptions) -> str:
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError as e:
        msg = f"syntax error: {e}"
        raise ValueError(msg) from e
    wrapped = module.visit(_Transformer(opts))
    result = wrapped.code
    if opts.collapse_blanks:
        result = _collapse_blank_lines(result)
    try:
        ast.parse(result)
    except SyntaxError as e:
        msg = f"transformed source failed to parse: {e}"
        raise ValueError(msg) from e
    return result


def _read_source(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    except (SyntaxError, UnicodeDecodeError):
        encoding = "utf-8"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding = "utf-8"
        text = raw.decode(encoding, errors="replace")
    return text, encoding


@dataclass(frozen=True)
class _Result:
    path: str
    status: str
    detail: str | None = None


def _process_one(
    path_str: str,
    opts: TransformOptions,
    dry_run: bool,
    backup: bool,
) -> _Result:
    path = Path(path_str)
    try:
        original, encoding = _read_source(path)
    except OSError as e:
        return _Result(path_str, "error", f"I/O error reading: {e}")
    try:
        new = _transform(original, opts)
    except ValueError as e:
        return _Result(path_str, "error", str(e))
    except Exception as e:
        return _Result(path_str, "error", f"{type(e).__name__}: {e}")
    if new == original:
        return _Result(path_str, "unchanged")
    if dry_run:
        return _Result(path_str, "would-modify")
    try:
        if backup:
            backup_path = path.with_name(path.name + BACKUP_SUFFIX)
            backup_path.write_bytes(original.encode(encoding))
        path.write_bytes(new.encode(encoding))
    except OSError as e:
        return _Result(path_str, "error", f"I/O error writing: {e}")
    return _Result(path_str, "modified")


def _worker(args: tuple[str, TransformOptions, bool, bool]) -> _Result:
    path_str, opts, dry_run, backup = args
    return _process_one(path_str, opts, dry_run, backup)


def _discover(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_file():
            if resolved.suffix in PY_SUFFIXES and resolved not in seen:
                seen.add(resolved)
                result.append(resolved)
        elif resolved.is_dir():
            for root, dirs, files in os.walk(resolved):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for fname in files:
                    p = (Path(root) / fname).resolve()
                    if p.suffix in PY_SUFFIXES and p not in seen:
                        seen.add(p)
                        result.append(p)
        else:
            sys.stderr.write(f"{PROG}: warning: skipping non-file/non-dir: {path}\n")
    return result


_COLORS: dict[str, str] = {
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "cyan": "\033[36m",
    "reset": "\033[0m",
}


def _color(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_COLORS[code]}{text}{_COLORS['reset']}"


def _build_parser() -> argparse.ArgumentParser:
    parser = _ParserShim()
    return parser


def _format_result(r: _Result, color: bool, verbose: int) -> str | None:
    if r.status == "modified":
        return f"{_color('modified', 'green', color)}: {r.path}"
    if r.status == "would-modify":
        return f"{_color('would modify', 'yellow', color)}: {r.path}"
    if r.status == "error":
        return f"{_color('error', 'red', color)}: {r.path}: {r.detail}"
    if r.status == "unchanged" and verbose:
        return f"unchanged: {r.path}"
    if r.status == "skipped" and verbose:
        return f"skipped: {r.path}: {r.detail}"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    "Run the command-line interface."
    parser = _build_parser()
    args = parser.parse_args(argv)

    opts = TransformOptions(
        remove_docstrings=args.remove_all,
        collapse_blanks=args.collapse_blanks,
    )

    try:
        stdout_tty = sys.stdout.isatty()
    except AttributeError:
        stdout_tty = False
    color = (not args.no_color) and stdout_tty

    files = _discover(args.paths)
    if not files:
        sys.stderr.write(f"{PROG}: no Python files found\n")
        return 1

    backup = not args.no_backup
    dry_run = args.dry_run
    total = len(files)

    print(
        _color(
            f"{PROG} {__version__}: scanning {total} file"
            f"{'s' if total != 1 else ''}" + (" (dry-run)" if dry_run else ""),
            "cyan",
            color,
        )
    )

    counts: dict[str, int] = {
        "modified": 0,
        "unchanged": 0,
        "would-modify": 0,
        "error": 0,
        "skipped": 0,
    }
    errors: list[_Result] = []

    def report(r: _Result) -> None:
        counts[r.status] = counts.get(r.status, 0) + 1
        if r.status == "error":
            errors.append(r)
        if not args.quiet:
            line = _format_result(r, color, args.verbose)
            if line is not None:
                print(line)

    use_parallel = not args.no_parallel and total >= PARALLEL_MIN_FILES and args.jobs > 1

    try:
        if use_parallel:
            chunksize = min(500, math.ceil(total / (4 * MAX_WORKERS)))
            task_args = [(str(f), opts, dry_run, backup) for f in files]
            with mp.Pool(MAX_WORKERS) as pool:
                for r in pool.imap_unordered(_worker, task_args, chunksize=chunksize):
                    report(r)
        else:
            for f in files:
                report(_process_one(str(f), opts, dry_run, backup))
    except KeyboardInterrupt:
        sys.stderr.write(f"\n{PROG}: interrupted\n")
        return 130

    print()
    parts = [
        _color(f"scanned: {total}", "cyan", color),
        _color(f"modified: {counts['modified']}", "green", color),
    ]
    if counts["would-modify"]:
        parts.append(_color(f"would modify: {counts['would-modify']}", "yellow", color))
    parts.append(_color(f"unchanged: {counts['unchanged']}", "cyan", color))
    if counts["error"]:
        parts.append(_color(f"errors: {counts['error']}", "red", color))
    print(" | ".join(parts))

    if dry_run:
        print(_color("(dry-run: no files were written)", "yellow", color))

    return 1 if counts["error"] else 0


def _run() -> None:
    raise SystemExit(main())


app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
)


@app.command(help="Strip comments (and optionally docstrings/blank lines) from Python sources.")
def cli(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to process")],
    remove_all: Annotated[bool, typer.Option("--remove-all", "-a", help="Also strip docstrings")] = False,
    collapse_blanks: Annotated[
        bool, typer.Option("--collapse-blanks", "-b", help="Collapse runs of blank lines to a single blank line")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Report changes without writing files")] = False,
    no_backup: Annotated[bool, typer.Option("--no-backup", help="Do not write .pystripbak backup files")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable colorized output")] = False,
    verbose: Annotated[int, typer.Option("-v", "--verbose", count=True, help="Increase verbosity (repeatable)")] = 0,
    quiet: Annotated[bool, typer.Option("-q", "--quiet", help="Suppress per-file output, only show summary")] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="show program's version number and exit"
        ),
    ] = False,
    jobs: Annotated[int, typer.Option("-j", "--jobs", help="Max worker processes (default: 8)")] = 8,
    chunksize: Annotated[int, typer.Option("--chunksize", help="Chunk size for parallel processing (default: 8)")] = 8,
    no_parallel: Annotated[bool, typer.Option("--no-parallel", help="Disable parallel processing")] = False,
) -> None:
    _STATE.update(
        paths=paths,
        remove_all=remove_all,
        collapse_blanks=collapse_blanks,
        dry_run=dry_run,
        no_backup=no_backup,
        no_color=no_color,
        verbose=verbose,
        quiet=quiet,
        jobs=jobs,
        chunksize=chunksize,
        no_parallel=no_parallel,
    )
    _run()


if __name__ == "__main__":
    app()
