#!/data/data/com.termux/files/usr/bin/env python
from __future__ import absolute_import

import contextlib
import difflib
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from functools import partial
from multiprocessing import cpu_count
from multiprocessing.dummy import Pool
from pathlib import Path
from subprocess import run
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import typer
from autoflake import fix_code
from black import (
    DEFAULT_LINE_LENGTH,
    FileMode,
    NothingChanged,
    format_file_contents,
)
from isort import code as isort_code
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table

try:
    from black import TargetVersion  # type: ignore[attr-defined]

    _HAS_TARGET_VERSION = True
except ImportError:
    TargetVersion = None  # type: ignore[assignment,misc]
    _HAS_TARGET_VERSION = False

try:
    import tomllib  # type: ignore[import-not-found]
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef,import-not-found]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

try:
    import pathspec as _pathspec  # type: ignore[import-not-found]
except ImportError:
    _pathspec = None  # type: ignore[assignment]

try:
    from watchdog.events import FileSystemEventHandler  # type: ignore[import-not-found]
    from watchdog.observers import Observer  # type: ignore[import-not-found]

    _HAS_WATCHDOG = True
except ImportError:
    Observer = None  # type: ignore[assignment,misc]
    FileSystemEventHandler = object  # type: ignore[misc,assignment]
    _HAS_WATCHDOG = False


TARGET_VERSIONS: Dict[str, Any] = {}
if _HAS_TARGET_VERSION:
    for _name in (
        "PY33",
        "PY34",
        "PY35",
        "PY36",
        "PY37",
        "PY38",
        "PY39",
        "PY310",
        "PY311",
        "PY312",
        "PY313",
    ):
        if hasattr(TargetVersion, _name):
            TARGET_VERSIONS[_name.lower()] = getattr(TargetVersion, _name)

PROFILES: Dict[str, Dict[str, Any]] = {
    "default": {},
    "strict": {
        "line_length": 88,
        "string_normalization": True,
        "autoflake_remove_unused_variables": True,
        "strip_magics": True,
        "dedupe_cells": True,
        "drop_empty_cells": True,
        "strip_metadata": True,
        "normalize_outputs": True,
        "add_trailing_newline": True,
    },
    "minimal": {
        "autoflake": False,
        "isort": False,
        "black": True,
        "strip_magics": False,
        "dedupe_cells": False,
        "drop_empty_cells": False,
        "clear_output": False,
        "strip_metadata": False,
        "normalize_outputs": False,
    },
    "notebook-only": {
        "py": False,
        "ipynb": True,
    },
}

console: Console = Console()
log = logging.getLogger("clean-py")


@dataclass
class Config:
    py: bool = True
    ipynb: bool = True
    autoflake: bool = True
    isort: bool = True
    black: bool = True
    line_length: int = DEFAULT_LINE_LENGTH
    string_normalization: bool = True
    magic_trailing_comma: bool = True
    target_version: Optional[str] = None
    isort_profile: str = "black"
    autoflake_remove_unused_variables: bool = True
    autoflake_ignore_init_module_imports: bool = False
    check: bool = False
    diff: bool = False
    backup: bool = False
    backup_suffix: str = ".bak"
    quiet: bool = False
    fail_fast: bool = False
    clear_output: bool = True
    strip_magics: bool = True
    dedupe_cells: bool = False
    drop_empty_cells: bool = False
    strip_metadata: bool = False
    normalize_outputs: bool = False
    strict_cell_errors: bool = False
    skip_header: bool = False
    add_trailing_newline: bool = True
    remove_trailing_newline: bool = False
    respect_fmt_off: bool = True
    jobs: int = cpu_count()
    parallel: bool = True
    verbose: bool = False
    cache: bool = False
    include: Optional[str] = None
    exclude: Tuple[str, ...] = ()
    respect_gitignore: bool = False
    follow_symlinks: bool = False
    max_depth: Optional[int] = None
    extensions: Tuple[str, ...] = (".py", ".pyi", ".ipynb")
    notebook_version: Optional[int] = None


@dataclass
class Stats:
    scanned: int = 0
    changed: int = 0
    unchanged: int = 0
    errors: int = 0
    files: List[Dict[str, Any]] = field(default_factory=list)


def setup_logging(verbose: bool, quiet: bool, no_color: bool) -> None:
    global console
    if no_color:
        console = Console(no_color=True)
    level = logging.DEBUG if verbose else (logging.ERROR if quiet else logging.INFO)
    handler = RichHandler(rich_tracebacks=True, console=console)
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[handler],
        force=True,
    )


def _has_fmt_off(source: str) -> bool:
    return "# fmt: off" in source or "# yapf: disable" in source


def _build_file_mode(config: Config) -> FileMode:
    kwargs: Dict[str, Any] = {
        "line_length": config.line_length,
        "is_pyi": False,
        "string_normalization": config.string_normalization,
        "magic_trailing_comma": config.magic_trailing_comma,
    }
    if config.target_version:
        tv = TARGET_VERSIONS.get(config.target_version)
        if tv is not None:
            kwargs["target_versions"] = {tv}
    return FileMode(**kwargs)


def remove_duplicate_cells(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    result: List[Dict[str, Any]] = []
    for cell in cells:
        src = cell.get("source", "")
        key = "".join(src) if isinstance(src, list) else str(src)
        if key in seen:
            continue
        seen.add(key)
        result.append(cell)
    return result


def remove_empty_cells(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for cell in cells:
        src = cell.get("source", "")
        text = "".join(src) if isinstance(src, list) else str(src)
        if len(text.strip()) > 2:
            result.append(cell)
    return result


def remove_magics(source: str) -> str:
    invalid = ("%", "?")
    lines: List[str] = []
    for line in source.split("\n"):
        if not line:
            continue
        if line[0] in invalid:
            continue
        lines.append(line)
    return "\n".join(lines)


def clean_python_code(source: str, config: Config, is_notebook_cell: bool = False) -> str:
    if config.respect_fmt_off and _has_fmt_off(source):
        return source

    formatted = source

    if is_notebook_cell:
        if config.black:
            mode = _build_file_mode(config)
            with contextlib.suppress(NothingChanged):
                formatted = format_file_contents(formatted, fast=True, mode=mode)
        return formatted

    if config.autoflake:
        formatted = fix_code(
            formatted,
            expand_star_imports=True,
            remove_all_unused_imports=True,
            remove_duplicate_keys=True,
            remove_unused_variables=config.autoflake_remove_unused_variables,
            ignore_init_module_imports=config.autoflake_ignore_init_module_imports,
        )

    if config.isort:
        formatted = isort_code(
            formatted,
            profile=config.isort_profile,
            line_length=config.line_length,
        )

    if config.black:
        mode = _build_file_mode(config)
        with contextlib.suppress(NothingChanged):
            formatted = format_file_contents(formatted, fast=True, mode=mode)

    return formatted


def _print_diff(path: Path, old: str, new: str) -> None:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=str(path),
        tofile=str(path),
    )
    for line in diff:
        console.print(line.rstrip("\n"), highlight=False, markup=False)


def _split_header(source: str, config: Config) -> Tuple[str, str]:
    if not config.skip_header:
        return "", source
    if source.startswith("#!"):
        nl = source.find("\n")
        if nl != -1:
            return source[: nl + 1], source[nl + 1 :]
    if source.startswith("# -*- coding:") or source.startswith("# coding:"):
        nl = source.find("\n")
        if nl != -1:
            return source[: nl + 1], source[nl + 1 :]
    return "", source


def _finalize_newlines(source: str, config: Config) -> str:
    if config.remove_trailing_newline:
        return source.rstrip("\n")
    if config.add_trailing_newline and source:
        return source.rstrip("\n") + "\n"
    return source


def create_file(file_path: Path, contents: str, config: Config) -> bool:
    try:
        existing: Optional[str] = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = None

    if existing == contents:
        return False

    if config.backup and existing is not None:
        backup_path = file_path.with_suffix(file_path.suffix + config.backup_suffix)
        shutil.copy2(file_path, backup_path)

    if config.check or config.diff:
        if config.diff:
            _print_diff(file_path, existing or "", contents)
        return True

    file_path.touch(exist_ok=True)
    file_path.write_text(contents, encoding="utf-8")
    return True


def clean_py(py_file_path: Path, config: Config) -> bool:
    source = py_file_path.read_text(encoding="utf-8")
    header, body = _split_header(source, config)
    cleaned_body = clean_python_code(body, config)
    result = _finalize_newlines(header + cleaned_body, config)
    return create_file(py_file_path, result, config)


def clear_ipynb_output(ipynb_file_path: Path) -> None:
    run(
        (
            "jupyter",
            "nbconvert",
            "--ClearOutputPreprocessor.enabled=True",
            "--inplace",
            str(ipynb_file_path),
        ),
        check=True,
    )


def clean_ipynb_cell(cell_dict: Dict[str, Any], config: Config) -> Dict[str, Any]:
    if cell_dict.get("cell_type") != "code":
        return cell_dict
    try:
        source = cell_dict.get("source", "")
        if isinstance(source, list):
            source = "".join(source)

        if config.strip_magics:
            source = remove_magics(source)

        clean_source = clean_python_code(source, config, is_notebook_cell=True)

        clean_lines = clean_source.split("\n")
        if clean_lines and clean_lines[-1] == "":
            clean_lines = clean_lines[:-1]
        if clean_lines:
            clean_lines = [line + "\n" for line in clean_lines[:-1]] + [clean_lines[-1]]

        cell_dict["source"] = clean_lines

        if config.normalize_outputs:
            cell_dict["outputs"] = []
            cell_dict["execution_count"] = None
        return cell_dict
    except Exception as e:
        if config.strict_cell_errors:
            raise
        log.error("Error cleaning cell: %s", e)
        return cell_dict


def clean_ipynb(ipynb_file_path: Path, config: Config) -> bool:
    if config.clear_output:
        try:
            clear_ipynb_output(ipynb_file_path)
        except Exception as e:
            log.warning("Unable to clear outputs for %s: %s", ipynb_file_path, e)

    with open(ipynb_file_path, encoding="utf-8") as fh:
        ipynb_dict: Dict[str, Any] = json.load(fh)

    if config.dedupe_cells:
        ipynb_dict["cells"] = remove_duplicate_cells(ipynb_dict["cells"])
    if config.drop_empty_cells:
        ipynb_dict["cells"] = remove_empty_cells(ipynb_dict["cells"])

    cell_fn = partial(clean_ipynb_cell, config=config)
    if config.parallel and config.jobs > 1:
        with Pool(config.jobs) as pool:
            processed = pool.map(cell_fn, ipynb_dict["cells"])
    else:
        processed = [cell_fn(c) for c in ipynb_dict["cells"]]
    ipynb_dict["cells"] = list(processed)

    if config.strip_metadata:
        metadata = ipynb_dict.get("metadata", {})
        ipynb_dict["metadata"] = {k: v for k, v in metadata.items() if k in ("kernelspec", "language_info")}
        for cell in ipynb_dict["cells"]:
            cell.pop("id", None)

    if config.notebook_version is not None:
        ipynb_dict["nbformat_minor"] = config.notebook_version

    new_text = json.dumps(ipynb_dict, indent=1) + "\n"
    return create_file(ipynb_file_path, new_text, config)


def _matches_exclude(rel: Path, patterns: Sequence[str]) -> bool:
    for pat in patterns:
        if rel.match(pat):
            return True
        if pat in rel.parts:
            return True
    return False


def _load_gitignore(root: Path) -> Optional[Any]:
    if _pathspec is None:
        return None
    gi = root / ".gitignore"
    if not gi.exists():
        return None
    lines = gi.read_text(encoding="utf-8", errors="ignore").splitlines()
    return _pathspec.PathSpec.from_lines("gitwildmatch", lines)


def discover_files(paths: Sequence[Path], config: Config) -> List[Path]:
    result: List[Path] = []
    for root in paths:
        if root.is_file():
            result.append(root)
            continue
        spec = _load_gitignore(root) if config.respect_gitignore else None
        root_depth = len(root.parts)
        for path in root.rglob("*"):
            if path.is_symlink() and not config.follow_symlinks:
                continue
            if not path.is_file():
                continue
            if config.max_depth is not None and len(path.parts) - root_depth > config.max_depth:
                continue
            if path.suffix not in config.extensions:
                continue
            rel = path.relative_to(root)
            if config.exclude and _matches_exclude(rel, config.exclude):
                continue
            if config.include and not path.match(config.include):
                continue
            if spec is not None and spec.match_file(rel.as_posix()):
                continue
            result.append(path)
    return result


def _cache_key(path: Path) -> str:
    st = path.stat()
    return f"{path}:{st.st_mtime_ns}:{st.st_size}"


def _load_cache(cache_file: Path) -> Set[str]:
    if not cache_file.exists():
        return set()
    try:
        return set(json.loads(cache_file.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_cache(cache_file: Path, keys: Set[str]) -> None:
    try:
        cache_file.write_text(json.dumps(sorted(keys)), encoding="utf-8")
    except Exception as e:
        log.warning("Unable to save cache: %s", e)


def load_config_file(path: Path) -> Dict[str, Any]:
    if tomllib is None or not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Failed to parse config %s: %s", path, e)
        return {}
    for section in ("tool.clean-py", "tool.clean_py", "clean-py"):
        node: Any = data
        for part in section.split("."):
            if not isinstance(node, dict):
                break
            node = node.get(part, {})
        if isinstance(node, dict) and node:
            return node
    return data if isinstance(data, dict) else {}


def find_config_file(start: Path) -> Optional[Path]:
    for name in ("pyproject.toml", ".clean-py.toml", "clean-py.toml"):
        for parent in [start] + list(start.parents):
            candidate = parent / name
            if candidate.exists():
                return candidate
    return None


def _git_changed_files(rev: str, root: Path) -> List[Path]:
    try:
        result = run(
            ["git", "-C", str(root), "diff", "--name-only", "--diff-filter=ACMR", rev],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    return [(root / line).resolve() for line in result.stdout.splitlines() if line and (root / line).exists()]


def _emit_error_github(path: Path, message: str) -> None:
    console.print(f"::error file={path}::{message}", highlight=False, markup=False)


def run_doctor() -> None:
    import autoflake as _autoflake
    import black as _black
    import isort as _isort

    table = Table(title="clean-py doctor")
    table.add_column("Component")
    table.add_column("Version")
    table.add_row("clean-py", "0.2.0")
    table.add_row("black", getattr(_black, "__version__", "?"))
    table.add_row("isort", getattr(_isort, "__version__", "?"))
    table.add_row("autoflake", getattr(_autoflake, "__version__", "?"))
    table.add_row("pathspec", getattr(_pathspec, "__version__", "?") if _pathspec else "missing")
    table.add_row("tomllib", "available" if tomllib else "missing")
    table.add_row("watchdog", "available" if _HAS_WATCHDOG else "missing")
    table.add_row("TargetVersion", "available" if _HAS_TARGET_VERSION else "missing")
    console.print(table)


def run_linters(paths: Sequence[Path], linters: Sequence[str]) -> int:
    rc = 0
    for path in paths:
        for linter in linters:
            result = run([linter, str(path)], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                if result.stdout:
                    console.print(result.stdout.rstrip(), highlight=False, markup=False)
                if result.stderr:
                    console.print(result.stderr.rstrip(), highlight=False, markup=False)
                rc = result.returncode
    return rc


def clean_stdin(config: Config) -> None:
    source = sys.stdin.read()
    result = clean_python_code(source, config)
    sys.stdout.write(result)


def run_watch(paths: Sequence[Path], runner: Callable[[], None], config: Config) -> None:
    if not _HAS_WATCHDOG:
        console.print("[red]watchdog is required for --watch[/red]")
        raise typer.Exit(1)

    class _Handler(FileSystemEventHandler):  # type: ignore[misc,valid-type]
        def on_any_event(self, event: Any) -> None:
            if getattr(event, "is_directory", False):
                return
            src = getattr(event, "src_path", "")
            if not src.endswith(tuple(config.extensions)):
                return
            console.print(f"[dim]Change detected: {src}[/dim]")
            runner()

    runner()
    observer = Observer()  # type: ignore[operator]
    for p in paths:
        target = p if p.is_dir() else p.parent
        observer.schedule(_Handler(), str(target), recursive=True)
    observer.start()
    console.print("[green]Watching for changes (Ctrl+C to stop)...[/green]")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


app = typer.Typer(
    name="clean-py",
    help="Auto-lint .py and .ipynb files with autoflake, isort and black",
    add_completion=False,
)


@app.command()
def main(
    path: Optional[List[Path]] = typer.Argument(None, help="File or directory to clean"),
    stdin: bool = typer.Option(False, "--stdin", help="Read source from stdin, write to stdout"),
    py: bool = typer.Option(True, help="Apply to .py source"),
    ipynb: bool = typer.Option(True, help="Apply to .ipynb source"),
    autoflake: bool = typer.Option(True, help="Apply autoflake to source"),
    isort: bool = typer.Option(True, help="Apply isort to source"),
    black: bool = typer.Option(True, help="Apply black to source"),
    verbose: bool = typer.Option(False, help="Enable verbose output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress info output"),
    no_color: bool = typer.Option(False, "--no-color", help="Disable color output"),
    line_length: int = typer.Option(DEFAULT_LINE_LENGTH, "--line-length", help="Line length"),
    skip_string_normalization: bool = typer.Option(False, "--skip-string-normalization"),
    skip_magic_trailing_comma: bool = typer.Option(False, "--skip-magic-trailing-comma"),
    target_version: Optional[str] = typer.Option(None, "--target-version"),
    isort_profile: str = typer.Option("black", "--isort-profile"),
    autoflake_remove_unused_variables: bool = typer.Option(
        True, "--autoflake-remove-unused-variables/--no-autoflake-remove-unused-variables"
    ),
    autoflake_ignore_init_module_imports: bool = typer.Option(False, "--autoflake-ignore-init-module-imports"),
    check: bool = typer.Option(False, "--check", help="Do not write; fail if changes needed"),
    diff: bool = typer.Option(False, "--diff", help="Print unified diff"),
    backup: bool = typer.Option(False, "--backup", help="Backup files before writing"),
    backup_suffix: str = typer.Option(".bak", "--backup-suffix"),
    fail_fast: bool = typer.Option(False, "--fail-fast"),
    clear_output: bool = typer.Option(True, "--clear-output/--keep-output"),
    strip_magics: bool = typer.Option(True, "--strip-magics/--keep-magics"),
    dedupe_cells: bool = typer.Option(False, "--dedupe-cells/--no-dedupe-cells"),
    drop_empty_cells: bool = typer.Option(False, "--drop-empty-cells/--no-drop-empty-cells"),
    strip_metadata: bool = typer.Option(False, "--strip-metadata"),
    normalize_outputs: bool = typer.Option(False, "--normalize-outputs"),
    strict_cell_errors: bool = typer.Option(False, "--strict-cell-errors"),
    skip_header: bool = typer.Option(False, "--skip-header"),
    add_trailing_newline: bool = typer.Option(True, "--add-trailing-newline/--no-add-trailing-newline"),
    remove_trailing_newline: bool = typer.Option(False, "--remove-trailing-newline"),
    respect_fmt_off: bool = typer.Option(True, "--respect-fmt-off/--no-respect-fmt-off"),
    jobs: int = typer.Option(cpu_count(), "--jobs", "-j"),
    no_parallel: bool = typer.Option(False, "--no-parallel"),
    cache: bool = typer.Option(False, "--cache/--no-cache"),
    include: Optional[str] = typer.Option(None, "--include"),
    exclude: List[str] = typer.Option([], "--exclude"),
    respect_gitignore: bool = typer.Option(False, "--respect-gitignore"),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks/--no-follow-symlinks"),
    max_depth: Optional[int] = typer.Option(None, "--max-depth"),
    extensions: str = typer.Option(".py,.pyi,.ipynb", "--extensions"),
    notebook_version: Optional[int] = typer.Option(None, "--notebook-version"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
    profile: str = typer.Option("default", "--profile"),
    show_stats: bool = typer.Option(False, "--stats"),
    json_report: Optional[Path] = typer.Option(None, "--json-report"),
    progress: bool = typer.Option(False, "--progress"),
    list_files: bool = typer.Option(False, "--list-files"),
    show_unchanged: bool = typer.Option(False, "--show-unchanged"),
    error_format: str = typer.Option("plain", "--error-format"),
    require_changes: bool = typer.Option(False, "--require-changes"),
    since: Optional[str] = typer.Option(None, "--since"),
    watch: bool = typer.Option(False, "--watch"),
    linters: List[str] = typer.Option([], "--linters"),
    doctor: bool = typer.Option(False, "--doctor"),
) -> None:
    setup_logging(verbose, quiet, no_color)

    if doctor:
        run_doctor()
        return

    paths: List[Path] = list(path or [])

    if not paths and not stdin:
        console.print("[red]Error: No paths provided[/red]")
        raise typer.Exit(1)

    cfg = Config(
        py=py,
        ipynb=ipynb,
        autoflake=autoflake,
        isort=isort,
        black=black,
        line_length=line_length,
        string_normalization=not skip_string_normalization,
        magic_trailing_comma=not skip_magic_trailing_comma,
        target_version=target_version,
        isort_profile=isort_profile,
        autoflake_remove_unused_variables=autoflake_remove_unused_variables,
        autoflake_ignore_init_module_imports=autoflake_ignore_init_module_imports,
        check=check,
        diff=diff,
        backup=backup,
        backup_suffix=backup_suffix,
        quiet=quiet,
        fail_fast=fail_fast,
        clear_output=clear_output,
        strip_magics=strip_magics,
        dedupe_cells=dedupe_cells,
        drop_empty_cells=drop_empty_cells,
        strip_metadata=strip_metadata,
        normalize_outputs=normalize_outputs,
        strict_cell_errors=strict_cell_errors,
        skip_header=skip_header,
        add_trailing_newline=add_trailing_newline,
        remove_trailing_newline=remove_trailing_newline,
        respect_fmt_off=respect_fmt_off,
        jobs=jobs,
        parallel=not no_parallel,
        verbose=verbose,
        cache=cache,
        include=include,
        exclude=tuple(exclude),
        respect_gitignore=respect_gitignore,
        follow_symlinks=follow_symlinks,
        max_depth=max_depth,
        extensions=tuple(e.strip() for e in extensions.split(",") if e.strip()),
        notebook_version=notebook_version,
    )

    profile_overrides = PROFILES.get(profile)
    if profile_overrides:
        cfg = replace(cfg, **profile_overrides)

    if config_path is None:
        start = paths[0].resolve() if paths else Path.cwd()
        config_path = find_config_file(start)
    if config_path and config_path.exists():
        file_cfg = load_config_file(config_path)
        if file_cfg:
            valid = {k: v for k, v in file_cfg.items() if hasattr(cfg, k)}
            if valid:
                cfg = replace(cfg, **valid)

    if stdin:
        clean_stdin(cfg)
        return

    for p in paths:
        if not p.exists():
            console.print(f"[red]Error: Path '{p}' does not exist[/red]")
            raise typer.Exit(1)

    exts = list(cfg.extensions)
    if not cfg.py:
        exts = [e for e in exts if e not in (".py", ".pyi")]
    if not cfg.ipynb:
        exts = [e for e in exts if e != ".ipynb"]
    cfg = replace(cfg, extensions=tuple(exts))

    files = discover_files(paths, cfg)

    if since:
        changed_set = set(_git_changed_files(since, paths[0].resolve()))
        if not changed_set:
            if not cfg.quiet:
                console.print("[dim]No changed files since revision[/dim]")
            return
        files = [f for f in files if f.resolve() in changed_set]

    if list_files:
        for f in files:
            console.print(str(f))
        return

    cache_file = Path(tempfile.gettempdir()) / "clean-py-cache.json"
    cached_keys = _load_cache(cache_file) if cfg.cache else set()
    new_keys: Set[str] = set()
    stats = Stats()

    def process_file(f: Path) -> None:
        stats.scanned += 1
        key = _cache_key(f)
        if cfg.cache and key in cached_keys:
            return
        try:
            if f.suffix in (".py", ".pyi"):
                changed = clean_py(f, cfg)
            elif f.suffix == ".ipynb":
                try:
                    with open(f, encoding="utf-8") as fh:
                        json.load(fh)
                except json.JSONDecodeError:
                    console.print(f"[red]Error: Invalid notebook format in {f}[/red]")
                    stats.errors += 1
                    stats.files.append({"path": str(f), "status": "error", "error": "invalid json"})
                    if error_format == "github":
                        _emit_error_github(f, "Invalid notebook format")
                    if cfg.fail_fast:
                        raise
                    return
                changed = clean_ipynb(f, cfg)
            else:
                return

            if changed:
                stats.changed += 1
                stats.files.append({"path": str(f), "status": "changed"})
            else:
                stats.unchanged += 1
                if show_unchanged and not cfg.quiet:
                    console.print(f"[dim]Unchanged: {f}[/dim]")
            new_keys.add(key)
        except Exception as e:
            stats.errors += 1
            msg = f"Unable to clean {f}: {e}"
            console.print(f"[yellow]Warning: {msg}[/yellow]")
            stats.files.append({"path": str(f), "status": "error", "error": str(e)})
            if error_format == "github":
                _emit_error_github(f, str(e))
            if verbose:
                log.exception("Detailed error:")
            if cfg.fail_fast:
                raise

    def run_all() -> None:
        stats.scanned = 0
        stats.changed = 0
        stats.unchanged = 0
        stats.errors = 0
        stats.files = []
        if progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as bar:
                task = bar.add_task("Cleaning", total=len(files))
                for f in files:
                    process_file(f)
                    bar.advance(task)
        else:
            for f in files:
                if not cfg.quiet:
                    log.info("Cleaning file: %s", f)
                process_file(f)

    if watch:
        run_watch(paths, run_all, cfg)
        return

    try:
        run_all()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if verbose:
            log.exception("Detailed error:")
        raise typer.Exit(1)

    if cfg.cache:
        _save_cache(cache_file, new_keys)

    linter_rc = 0
    if linters:
        linter_rc = run_linters(files, linters)
        if linter_rc != 0 and stats.errors == 0:
            stats.errors += 1

    if show_stats or (not cfg.quiet and not cfg.diff and not cfg.check):
        table = Table(title="Summary")
        table.add_column("Metric")
        table.add_column("Value")
        table.add_row("Scanned", str(stats.scanned))
        table.add_row("Changed", str(stats.changed))
        table.add_row("Unchanged", str(stats.unchanged))
        table.add_row("Errors", str(stats.errors))
        console.print(table)

    if json_report:
        json_report.write_text(
            json.dumps(
                {
                    "scanned": stats.scanned,
                    "changed": stats.changed,
                    "unchanged": stats.unchanged,
                    "errors": stats.errors,
                    "files": stats.files,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    if check and stats.changed > 0:
        console.print(f"[red]{stats.changed} file(s) need formatting[/red]")
        raise typer.Exit(1)

    if require_changes and stats.changed == 0:
        console.print("[red]No changes made[/red]")
        raise typer.Exit(1)

    if stats.errors > 0 or linter_rc != 0:
        console.print("[yellow]Cleaning completed with some warnings.[/yellow]")
        raise typer.Exit(2)

    if not cfg.quiet:
        console.print("[green]Cleaning completed successfully![/green]")


if __name__ == "__main__":
    app()
