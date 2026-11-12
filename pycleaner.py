#!/data/data/com.termux/files/usr/bin/env python

import argparse
import ast
import difflib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import textwrap
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from multiprocessing import Pool
from pathlib import Path
from typing import TYPE_CHECKING

from dh import SKIP_DIRS, MAX_WORKERS

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
try:
    import zstandard as zstd

    _HAS_ZSTD = True
except ImportError:
    _HAS_ZSTD = False
_USE_COLOR: bool = True

_SKIPDIRS_RE = re.compile(r"^(.+?):(\d+):\s+unused variable\s+['\"]SKIP_DIRS['\"]")


def _c(code: str, text: str) -> str:
    return text if not _USE_COLOR else f"\x1b[{code}m{text}\x1b[0m"


def bold(s: str) -> str:
    return _c("1", s)


def cyan(s: str) -> str:
    return _c("36", s)


def yellow(s: str) -> str:
    return _c("33", s)


def red(s: str) -> str:
    return _c("31", s)


def green(s: str) -> str:
    return _c("32", s)


def dim(s: str) -> str:
    return _c("2", s)


def _safe_read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _display_path(p: str) -> str:
    if "::" in p:
        return p
    try:
        return str(Path(p).relative_to(Path.cwd()))
    except ValueError:
        return p


def _iter_py_files(root: Path, skip_dirs: set[str]) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file() and not any(part in skip_dirs for part in p.parts)]


@dataclass
class UnusedImport:
    lineno: int
    col_offset: int
    original_stmt: str
    unused_names: list[str]


@dataclass
class _ImportReport:
    path: str
    unused: list[UnusedImport] = field(default_factory=list)
    error: str | None = None


def _collect_type_checking_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if is_tc:
            for sub in ast.walk(node):
                if hasattr(sub, "lineno"):
                    lines.add(sub.lineno)
    return lines


def _collect_all_exports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            names.add(elt.value)
    return names


def _collect_used_names(tree: ast.Module) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            head = node
            while isinstance(head, ast.Attribute):
                head = head.value
            if isinstance(head, ast.Name):
                used.add(head.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for tok in re.findall(r"\b([A-Za-z_]\w*)\b", node.value):
                used.add(tok)
    return used


def _parse_import_names(node: ast.Import | ast.ImportFrom) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for alias in node.names:
        full = alias.name
        bound = alias.asname or full.split(".")[0]
        pairs.append((full, bound))
    return pairs


def _analyse_imports(
    source: str,
    path: str,
    *,
    is_init: bool = False,
    ignore_init: bool = False,
) -> _ImportReport:
    report = _ImportReport(path=path)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        report.error = f"SyntaxError: {exc}"
        return report
    if ignore_init and is_init:
        return report
    tc_lines = _collect_type_checking_lines(tree)
    all_exports = _collect_all_exports(tree)
    used = _collect_used_names(tree)
    docstring = ""
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        docstring = tree.body[0].value.value
    src_lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if node.lineno in tc_lines:
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if any(alias.name == "*" for alias in node.names):
            continue
        if ignore_init and isinstance(node, ast.ImportFrom) and (node.level or 0) > 0:
            continue
        unused_names: list[str] = []
        for _full, bound in _parse_import_names(node):
            if bound in used or bound in all_exports or bound in docstring:
                continue
            unused_names.append(bound)
        if not unused_names:
            continue
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        stmt_src = "\n".join(src_lines[start:end])
        report.unused.append(
            UnusedImport(
                lineno=node.lineno,
                col_offset=node.col_offset,
                original_stmt=stmt_src,
                unused_names=unused_names,
            )
        )
    return report


def _bound_name(segment: str) -> str:
    m = re.match(r"^\s*[\w.]+\s+as\s+(\w+)\s*$", segment)
    if m:
        return m.group(1)
    return segment.strip().split(".")[0].strip()


def _rewrite_simple_import(stmt: str, unused: set[str]) -> str | None:
    m = re.match(r"^(\s*import\s+)(.+)$", stmt)
    if m:
        head, body = m.group(1), m.group(2)
        kept = [seg.strip() for seg in body.split(",") if _bound_name(seg.strip()) not in unused]
        if not kept:
            return None
        return head + ", ".join(kept)
    m = re.match(r"^(\s*from\s+[\w.]+\s+import\s+)(.+)$", stmt)
    if m:
        head, body = m.group(1), m.group(2)
        body = re.sub(r"\s*#.*$", "", body).rstrip(" \\")
        kept = [seg.strip() for seg in body.split(",") if seg.strip() and _bound_name(seg.strip()) not in unused]
        if not kept:
            return None
        return head + ", ".join(kept)
    return stmt


def _rewrite_multiline_import(stmt: str, unused: set[str]) -> str | None:
    m = re.match(r"^(\s*from\s+[\w.]+\s+import\s*$)(.*?)($.*)", stmt, re.DOTALL)
    if not m:
        return None
    head, body, tail = m.group(1), m.group(2), m.group(3)
    kept: list[str] = []
    for seg in re.split(r",\s*", body):
        clean = re.sub(r"#.*", "", seg).strip()
        if not clean:
            continue
        if _bound_name(clean) not in unused:
            kept.append(clean)
    if not kept:
        return None
    if len(kept) == 1 and "\n" not in stmt:
        return f"{head[:-1]}{kept[0]}{tail[1:]}"
    joined = ",\n    ".join(kept)
    return f"{head}\n    {joined},\n{tail}"


def _rewrite_source(source: str, unused: list[UnusedImport]) -> str:
    by_line: dict[int, set[str]] = {}
    end_of: dict[int, int] = {}
    for item in unused:
        by_line.setdefault(item.lineno, set()).update(item.unused_names)
        end_of[item.lineno] = item.lineno + item.original_stmt.count("\n")
    lines = source.splitlines(keepends=True)
    out: list[str] = []
    skip_until = -1
    i = 0
    while i < len(lines):
        lineno = i + 1
        if lineno <= skip_until:
            i += 1
            continue
        if lineno not in by_line:
            out.append(lines[i])
            i += 1
            continue
        names = by_line[lineno]
        end = end_of[lineno]
        stmt = "".join(lines[i:end])
        if "(" in stmt and "\n" in stmt:
            new = _rewrite_multiline_import(stmt, names)
        else:
            plain = stmt.strip("\n")
            new = _rewrite_simple_import(plain, names)
            if new is not None:
                new += "\n" if stmt.endswith("\n") else ""
        if new is not None:
            out.append(new)
        skip_until = end
        i += 1
    return "".join(out)


def _autoflake_one(path: Path, *, autofix: bool, diff: bool) -> None:
    if not path.exists():
        print(f"Error: The file `{path}` does not exist.")
        return
    cmd = [
        "autoflake",
        "--remove-all-unused-imports",
        "--ignore-init-module-imports",
        str(path),
    ]
    cmd.append("--in-place" if autofix else "--check")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("Error: `autoflake` is not installed. Run `pip install autoflake`.")
        return
    if proc.returncode == 0:
        return
    if proc.returncode == 1:
        if autofix:
            print(f"Successfully removed unused imports from `{path}`.")
        elif diff:
            original = path.read_text()
            fixed = subprocess.run(
                [
                    "autoflake",
                    "--remove-all-unused-imports",
                    "--ignore-init-module-imports",
                    "-",
                ],
                input=original,
                capture_output=True,
                text=True,
            )
            if fixed.returncode == 0 and fixed.stdout != original:
                udiff = difflib.unified_diff(
                    original.splitlines(keepends=True),
                    fixed.stdout.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                )
                print("".join(udiff), end="")
        else:
            for line in ((proc.stdout or "") + (proc.stderr or "")).splitlines():
                if line.strip():
                    print(f"{path}: {line}")


def _scan_zip(path: Path) -> Iterator[tuple[str, str]]:
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.endswith(".py"):
                    continue
                key = f"{path.name}::{name}"
                try:
                    yield key, zf.read(name).decode("utf-8", errors="replace")
                except Exception as exc:
                    yield key, f"__ERROR__:{exc}"
    except zipfile.BadZipFile as exc:
        yield f"{path.name}::?", f"__ERROR__:Bad zip: {exc}"


def _scan_tar(path: Path) -> Iterator[tuple[str, str]]:
    def _inner(tf: tarfile.TarFile) -> Iterator[tuple[str, str]]:
        for member in tf.getmembers():
            if not member.name.endswith(".py"):
                continue
            key = f"{path.name}::{member.name}"
            try:
                fobj = tf.extractfile(member)
                if fobj is None:
                    continue
                yield key, fobj.read().decode("utf-8", errors="replace")
            except Exception as exc:
                yield key, f"__ERROR__:{exc}"

    try:
        if _HAS_ZSTD:
            decomp = zstd.ZstdDecompressor()
            with open(path, "rb") as fh:
                stream = decomp.stream_reader(fh)
                with tarfile.open(fileobj=io.BytesIO(stream.read())) as tf:
                    yield from _inner(tf)
        else:
            with tarfile.open(path) as tf:
                yield from _inner(tf)
    except Exception as exc:
        yield f"{path.name}::?", f"__ERROR__:Archive error: {exc}"


_WORKER_IGNORE_INIT: bool = False


def _init_worker_imports(ignore_init: bool) -> None:
    global _WORKER_IGNORE_INIT
    _WORKER_IGNORE_INIT = ignore_init


def _worker_file(p: str) -> _ImportReport:
    path = Path(p)
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _ImportReport(path=p, error=f"OSError: {exc}")
    is_init = path.name == "__init__.py"
    return _analyse_imports(src, p, is_init=is_init, ignore_init=_WORKER_IGNORE_INIT)


def _worker_archive(item: tuple[str, str]) -> _ImportReport:
    key, src = item
    if src.startswith("__ERROR__:"):
        return _ImportReport(path=key, error=src[len("__ERROR__:") :])
    is_init = key.endswith(("/__init__.py", "\\__init__.py"))
    return _analyse_imports(src, key, is_init=is_init, ignore_init=_WORKER_IGNORE_INIT)


def _collect_import_targets(
    paths: Iterable[Path], excludes: list[re.Pattern[str]]
) -> tuple[list[str], list[tuple[str, str]]]:
    def _is_excluded(p: Path) -> bool:
        s = str(p)
        return any(pat.search(s) for pat in excludes)

    py_files: list[str] = []
    archive_members: list[tuple[str, str]] = []
    for p in paths:
        if not p.exists():
            print(red(f"warning: path does not exist: {p}"), file=sys.stderr)
            continue
        if p.is_file():
            if p.suffix == ".py":
                if not _is_excluded(p):
                    py_files.append(str(p))
            elif p.suffix == ".whl":
                if not _is_excluded(p):
                    archive_members.extend(_scan_zip(p))
            elif p.name.endswith(".tar.zst"):
                if not _is_excluded(p):
                    archive_members.extend(_scan_tar(p))
            else:
                print(yellow(f"warning: skipping unrecognised file: {p}"), file=sys.stderr)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if _is_excluded(f) or not f.is_file():
                    continue
                if f.suffix == ".py":
                    py_files.append(str(f))
                elif f.suffix == ".whl":
                    archive_members.extend(_scan_zip(f))
                elif f.name.endswith(".tar.zst"):
                    archive_members.extend(_scan_tar(f))
    return py_files, archive_members


def _print_report(rep: _ImportReport, *, verbose: bool) -> int:
    disp = _display_path(rep.path)
    if rep.error:
        print(f"  {red('error')} {bold(disp)}: {red(rep.error)}")
        return 0
    if not rep.unused:
        if verbose:
            print(f"  {green('✓')} {dim(disp)}")
        return 0
    for item in rep.unused:
        loc = cyan(f"line {item.lineno:>4}")
        first = yellow(item.original_stmt.splitlines()[0])
        print(f"  {bold(disp)}  -->  {loc}  {first}")
        print(f"{'':>50}  [unused: {red(', '.join(item.unused_names))}]")
    return len(rep.unused)


def _cmd_imports(args: argparse.Namespace) -> int:
    if args.backend == "autoflake":
        return _cmd_imports_autoflake(args)
    paths = [Path(p) for p in (args.paths or ["."])]
    excludes: list[re.Pattern[str]] = []
    for pat in args.exclude:
        try:
            excludes.append(re.compile(pat))
        except re.error as exc:
            print(red(f"error: invalid --exclude pattern {pat!r}: {exc}"), file=sys.stderr)
            return 2
    print(bold(f"\nScanning {len(paths)} path(s) with {MAX_WORKERS} worker(s) …\n"))
    py_files, archive_members = _collect_import_targets(paths, excludes)
    n_py, n_arc = len(py_files), len(archive_members)
    print(f"  {cyan(str(n_py))} .py file(s), {cyan(str(n_arc))} archive member(s) queued.\n")
    if n_py + n_arc == 0:
        print(yellow("No files to analyse."))
        return 0
    reports: list[_ImportReport] = []
    with Pool(
        processes=MAX_WORKERS,
        initializer=_init_worker_imports,
        initargs=(args.ignore_init,),
    ) as pool:
        for i, rep in enumerate(pool.imap_unordered(_worker_file, py_files), 1):
            reports.append(rep)
            if args.verbose:
                print(dim(f"  [{i}/{n_py}] analysed {_display_path(rep.path)}"), end="\r")
        if args.verbose and n_py:
            print()
        for rep in pool.imap_unordered(_worker_archive, archive_members):
            reports.append(rep)
    reports.sort(key=lambda r: r.path)
    total_unused = 0
    touched: set[str] = set()
    fixed_files = 0
    for rep in reports:
        n = _print_report(rep, verbose=args.verbose)
        total_unused += n
        if n:
            touched.add(rep.path)
        if (args.autofix or args.dry_run) and n and not rep.error:
            if "::" in rep.path:
                if args.verbose:
                    print(dim(f"  [skip fix — archive member: {rep.path}]"))
                continue
            p = Path(rep.path)
            try:
                src = p.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                print(f"  {red('SKIP autofix')} {bold(rep.path)} — {exc}")
                continue
            new_src = _rewrite_source(src, rep.unused)
            try:
                ast.parse(new_src, filename=rep.path)
            except SyntaxError as exc:
                print(f"  {red('SKIP autofix')} {bold(rep.path)} — result failed to parse: {exc}")
                continue
            if args.dry_run:
                print(f"  {cyan('would fix')} {bold(rep.path)}")
            else:
                st = p.stat()
                p.write_text(new_src, encoding="utf-8")
                os.chmod(p, st.st_mode)
                print(f"  {green('fixed')} {bold(rep.path)}")
                fixed_files += 1
    print()
    if total_unused == 0:
        print(green("✓ No unused imports found."))
    else:
        print(bold(f"Found {red(str(total_unused))} unused import(s) across {red(str(len(touched)))} file(s)."))
        if args.autofix and not args.dry_run:
            print(green(f"Fixed {fixed_files} file(s)."))
    return 0 if total_unused == 0 else 1


def _cmd_imports_autoflake(args: argparse.Namespace) -> int:
    paths = [Path(p) for p in (args.paths or ["."])]
    files: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix == ".py":
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(p.glob("*.py")))
    if not files:
        print(yellow("No .py files found."))
        return 0
    for f in files:
        _autoflake_one(f, autofix=args.autofix, diff=args.diff)
    return 0


@dataclass
class DefItem:
    name: str
    kind: str
    file: Path
    lineno: int
    end_lineno: int


@dataclass
class _DefScanResult:
    file: Path
    defs: list[DefItem] = field(default_factory=list)
    used_names: set[str] = field(default_factory=set)
    source: str = ""
    error: str | None = None


_NEVER_UNUSED = {"__all__", "__version__", "__author__"}


def _is_const_name(name: str) -> bool:
    return name.isupper() and not name.startswith("__")


class _DefCollector(ast.NodeVisitor):
    def __init__(self, file: Path) -> None:
        self.file = file
        self.defs: list[DefItem] = []

    def _add(self, name: str, kind: str, node: ast.AST) -> None:
        self.defs.append(
            DefItem(
                name=name,
                kind=kind,
                file=self.file,
                lineno=node.lineno,
                end_lineno=getattr(node, "end_lineno", node.lineno),
            )
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add(node.name, "func", node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add(node.name, "class", node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and _is_const_name(target.id):
                self._add(target.id, "const", node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and _is_const_name(node.target.id):
            self._add(node.target.id, "const", node)
        self.generic_visit(node)


class _UsedNameCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.used: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        self.used.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.used.add(node.attr)
        self.generic_visit(node)


def _scan_one_defs(path: Path) -> _DefScanResult:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        return _DefScanResult(file=path, error=str(exc))
    collector = _DefCollector(path)
    collector.visit(tree)
    user = _UsedNameCollector()
    user.visit(tree)
    return _DefScanResult(file=path, defs=collector.defs, used_names=user.used, source=src)


def _filter_unused_global(scans: list[_DefScanResult], kind_filter: str) -> list[DefItem]:
    all_defs: list[DefItem] = []
    all_used: set[str] = set()
    for s in scans:
        if s.error:
            continue
        all_defs.extend(s.defs)
        all_used |= s.used_names
    result: list[DefItem] = []
    for d in all_defs:
        if d.name in _NEVER_UNUSED:
            continue
        if d.name.startswith("__") and d.name.endswith("__"):
            continue
        if d.name == "main":
            continue
        if kind_filter not in ("all", d.kind):
            continue
        if d.name not in all_used:
            result.append(d)
    return result


def _filter_unused_per_file(scan: _DefScanResult, kind_filter: str) -> list[DefItem]:
    if scan.error:
        return []
    result: list[DefItem] = []
    for d in scan.defs:
        if d.name.startswith("_"):
            continue
        if kind_filter not in ("all", d.kind):
            continue
        if d.name not in scan.used_names:
            result.append(d)
    return result


def _remove_defs_from_source(source: str, names: set[str], only_top_level: bool) -> str:
    tree = ast.parse(source)
    if only_top_level:
        new_body = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names:
                continue
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
                continue
            new_body.append(node)
        tree.body = new_body
        return ast.unparse(tree)

    class _Remover(ast.NodeTransformer):
        def _maybe_drop(self, body: list[ast.stmt]) -> list[ast.stmt]:
            out: list[ast.stmt] = []
            for n in body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names:
                    continue
                out.append(self.visit(n))
            return out

        def visit_Module(self, node: ast.Module) -> ast.Module:
            node.body = self._maybe_drop(node.body)
            return node

        def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
            node.body = self._maybe_drop(node.body)
            return node

    return ast.unparse(_Remover().visit(tree))


_EXTRACT_DIRS = {"func": "func", "class": "classes", "const": "const"}


def _extract_one(item: DefItem, base: Path) -> str:
    target = base / _EXTRACT_DIRS[item.kind]
    target.mkdir(parents=True, exist_ok=True)
    src_lines = item.file.read_text(encoding="utf-8", errors="replace").splitlines()
    snippet = "\n".join(src_lines[item.lineno - 1 : item.end_lineno])
    stem = item.file.stem.replace(".", "_")
    out = target / f"{stem}__{item.name}.py"
    header = (
        f"# Extracted: {item.kind} '{item.name}'\n# Source: {item.file}\n# Lines: {item.lineno}-{item.end_lineno}\n\n"
    )
    out.write_text(header + snippet + "\n", encoding="utf-8")
    return str(out)


def _cmd_defs(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    files = _iter_py_files(root, SKIP_DIRS)
    if not files:
        print(yellow("No .py files found."))
        return 0
    print(bold(f"Scanning {len(files)} file(s) with {MAX_WORKERS} worker(s)..."))
    with Pool(processes=MAX_WORKERS) as pool:
        scans = list(pool.imap_unordered(_scan_one_defs, files))
    for s in scans:
        if s.error:
            print(yellow(f"[WARN] Failed to parse {s.file}: {s.error}"))
    if args.scope == "global":
        unused_items = _filter_unused_global(scans, args.kind)
    else:
        unused_items = []
        for s in scans:
            unused_items.extend(_filter_unused_per_file(s, args.kind))
    if not unused_items:
        print(green("\nNo unused functions/classes/constants found."))
        return 0
    unused_items.sort(key=lambda d: (str(d.file), d.lineno))
    print(bold(f"\nFound {len(unused_items)} unused object(s):\n"))
    for d in unused_items:
        try:
            rel = d.file.relative_to(root)
        except ValueError:
            rel = d.file
        print(f"  [{d.kind:5}] {d.name:30} {rel}:{d.lineno}")
    if args.extract:
        out_base = Path(args.extract_dir).resolve() if args.extract_dir else root / "output"
        print(bold(f"\nExtracting {len(unused_items)} object(s) into {out_base} ..."))
        with Pool(processes=MAX_WORKERS) as pool:
            written = list(pool.starmap(_extract_one, [(d, out_base) for d in unused_items]))
        for p in written:
            print(f"  wrote {p}")
        print(green("\nExtraction complete."))
    if args.remove:
        by_file: dict[Path, set[str]] = defaultdict(set)
        for d in unused_items:
            by_file[d.file].add(d.name)
        removed_count = 0
        for path, names in by_file.items():
            src = path.read_text(encoding="utf-8")
            try:
                new_src = _remove_defs_from_source(src, names, only_top_level=True)
                ast.parse(new_src)
            except Exception as exc:
                print(red(f"[ERROR] {path}: {exc}"))
                continue
            if args.dry_run:
                print(cyan(f"[DRY-RUN] Would remove {sorted(names)} from {path}"))
            else:
                if args.backup:
                    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
                path.write_text(new_src, encoding="utf-8")
                print(green(f"Removed {sorted(names)} from {path}") + (" (backup created)" if args.backup else ""))
                removed_count += 1
        if not args.dry_run:
            print(green(f"\nRemoved unused objects from {removed_count} file(s)."))
    return 0


_UNUSED_VAR_RE = re.compile(
    r"^(?P<path>.*?):(?P<lineno>\d+):\s*unused variable '(?P<var>[^']+)'",
    re.IGNORECASE,
)
_VULTURE_RE = re.compile(
    r"^(.+?):(\d+):\s+"
    r"(unused\s+(function|variable|class|attribute|method|import)\s+'([^']+)'"
    r"|unreachable code after '(\w+)'"
    r"|redundant if-condition"
    r"|unreachable 'else' block"
    r"|unused import '([^']+)'\s+\(\d+% confidence\))$"
)


def _parse_vulture_lines(lines: list[str], mode: str) -> dict[str, list[tuple[int, str, str]]]:
    issues: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    if mode == "skip-dirs":
        for raw in lines:
            m = _SKIPDIRS_RE.match(raw.strip())
            if m:
                issues[m.group(1)].append((int(m.group(2)), "unused_variable", "SKIP_DIRS"))
        return dict(issues)
    if mode == "comment-vars":
        for raw in lines:
            m = _UNUSED_VAR_RE.match(raw.rstrip("\n"))
            if m:
                issues[m.group("path")].append((int(m.group("lineno")), "unused_variable", m.group("var")))
        return dict(issues)
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        m = _VULTURE_RE.match(s)
        if not m:
            continue
        path, lineno = m.group(1), int(m.group(2))
        if m.group(4):
            kind = "unused_" + m.group(4)
            name = m.group(5)
        elif m.group(6):
            kind, name = "unreachable_after", m.group(6)
        elif "redundant if-condition" in s:
            kind, name = "redundant_if", ""
        elif "unreachable 'else' block" in s:
            kind, name = "unreachable_else", ""
        elif m.group(7):
            kind, name = "unused_import", m.group(7)
        else:
            kind, name = "other", ""
        issues[path].append((lineno, kind, name))
    return dict(issues)


def _comment_out(line: str, *, marker: bool = False) -> str:
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if not stripped:
        return indent + "#\n"
    if stripped.startswith("#"):
        return line
    prefix = "# REMOVED: " if marker else "# "
    return indent + prefix + stripped


def _apply_vulture_fixes(path: str, fixes: list[tuple[int, str, str]], mode: str) -> tuple[list[str], list[str]]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        original = f.readlines()
    lines = list(original)
    skip_indices: set[int] = set()
    for lineno, kind, _name in sorted(fixes, key=lambda x: x[0], reverse=True):
        idx = lineno - 1
        if idx < 0 or idx >= len(lines) or idx in skip_indices:
            continue
        if mode in {"comment-vars", "skip-dirs"}:
            lines[idx] = _comment_out(lines[idx])
        elif mode == "comment-all":
            lines[idx] = _comment_out(lines[idx], marker=True)
        elif mode == "remove-all":
            lines[idx] = _comment_out(lines[idx], marker=True)
    return original, lines


def _cmd_vulture(args: argparse.Namespace) -> int:
    src_file = args.file
    if src_file:
        try:
            with open(src_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            print(red(f"Error: file not found: {src_file}"), file=sys.stderr)
            return 2
    else:
        print(dim("Reading vulture output from stdin..."))
        lines = sys.stdin.readlines()
    if not lines:
        print(yellow("No input provided."))
        return 0
    issues = _parse_vulture_lines(lines, args.mode)
    if not issues:
        print(yellow("No matching vulture entries found."))
        return 0
    print(bold(f"Found issues in {len(issues)} file(s)."))
    for path, items in issues.items():
        print(f"  {path}: {len(items)} issue(s)")
    if not args.yes and args.apply:
        try:
            ans = input("\nProceed with fixes? (y/N): ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes"):
            print("Aborted.")
            return 0
    processed = 0
    modified = 0
    for path, fixes in issues.items():
        if not Path(path).exists():
            print(yellow(f"  [!] file not found: {path}, skipping"))
            continue
        orig, new = _apply_vulture_fixes(path, fixes, args.mode)
        processed += 1
        if orig == new:
            print(dim(f"  no changes for {path}"))
            continue
        modified += 1
        udiff = difflib.unified_diff(orig, new, fromfile=path, tofile=path, lineterm="")
        print("".join(line + "\n" for line in udiff))
        if args.apply:
            if args.backup:
                shutil.copy2(path, path + ".bak")
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(new)
                print(green(f"  [written] {path}"))
            except Exception as exc:
                print(red(f"  [!] failed to write {path}: {exc}"))
    if args.summary:
        print()
        print(bold(f"Summary: {processed} file(s) processed, {modified} modified."))
        if not args.apply:
            print(dim("Dry-run mode — use --apply to write changes."))
    return 0


_REPLACE_FUNC_NAME = "format_size"
_REPLACE_FUNC_NARGS = 1
_REPLACE_FUNC_BODY = textwrap.dedent("""
    def format_size(size_bytes: int) -> str:
        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"
""").lstrip("\n")
_REPLACE_FUNC_AST = ast.dump(ast.parse(_REPLACE_FUNC_BODY).body[0])


def _is_target_func(node: ast.AST, inspect_only: bool) -> bool:
    if not isinstance(node, ast.FunctionDef):
        return False
    if node.name != _REPLACE_FUNC_NAME:
        return False
    if inspect_only:
        return len(node.args.args) == _REPLACE_FUNC_NARGS
    return ast.dump(node) == _REPLACE_FUNC_AST


def _replace_func_in_file(path: Path, inspect_only: bool) -> tuple[Path, bool, str]:
    if path.name == "pycleaner.py":
        return path, False, "Skipped by filename"
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as exc:
        return path, False, f"Read error: {exc}"
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return path, False, "Original file has a syntax error"
    targets = [n for n in tree.body if _is_target_func(n, inspect_only)]
    if not targets:
        return path, False, "Target function not found"
    drop_lines: set[int] = set()
    for t in targets:
        start = t.lineno - 1
        if t.decorator_list:
            start = t.decorator_list[0].lineno - 1
        drop_lines.update(range(start, t.end_lineno))
    last_import = -1
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            last_import = max(last_import, n.end_lineno - 1)
    docstring_end = 0
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        docstring_end = tree.body[0].end_lineno
    new_lines: list[str] = []
    inserted = False
    for i, line in enumerate(src.splitlines(keepends=True)):
        if i in drop_lines:
            continue
        new_lines.append(line)
        if inserted:
            continue
        if last_import != -1 and i == last_import:
            new_lines.append(f"from dh import {_REPLACE_FUNC_NAME}\n")
            inserted = True
        elif last_import == -1 and i == docstring_end - 1:
            new_lines.append(f"from dh import {_REPLACE_FUNC_NAME}\n")
            inserted = True
    if not inserted:
        new_lines.insert(0, f"from dh import {_REPLACE_FUNC_NAME}\n")
    new_src = "".join(new_lines)
    try:
        ast.parse(new_src)
    except SyntaxError as exc:
        return path, False, f"Validation failed: {exc}"
    try:
        path.write_text(new_src, encoding="utf-8")
    except Exception as exc:
        return path, False, f"Write error: {exc}"
    return path, True, "Successfully updated"


def _cmd_replace_func(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    files = [p for p in root.rglob("*.py") if p.is_file() and p.resolve() != Path(__file__).resolve()]
    if not files:
        print(yellow("No Python files found."))
        return 0
    mode = "NAME/ARGUMENT CHECK" if args.inspect else "EXACT BODY CHECK"
    print(bold(f"Mode: {mode}"))
    print(f"Found {len(files)} Python files. Processing with {MAX_WORKERS} workers...")
    print("Changes will be applied automatically.\n")
    with Pool(MAX_WORKERS) as pool:
        for path, ok, msg in pool.starmap(_replace_func_in_file, [(f, args.inspect) for f in files]):
            if ok:
                print(green(f"[UPDATED] {path}: {msg}"))
            elif msg != "Target function not found":
                print(yellow(f"[SKIPPED] {path}: {msg}"))
    return 0


def _read_block(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(red(f"Failed to read {path}: {exc}"), file=sys.stderr)
        raise
    return [line.rstrip() for line in text.strip("\n").splitlines()]


def _find_block(lines: list[str], block: list[str]) -> tuple[int, int] | None:
    stripped = [line.rstrip("\n").rstrip() for line in lines]
    n, m = len(stripped), len(block)
    for i in range(n - m + 1):
        if stripped[i : i + m] == block:
            return i, i + m
    return None


def _already_has_import(tree: ast.Module, name: str) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "dh" and any(a.name == name for a in node.names):
            return True
        if isinstance(node, ast.Import) and any(a.name == name for a in node.names):
            return True
    return False


def _top_import_end(tree: ast.Module) -> int:
    end = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if node.end_lineno:
                end = max(end, node.end_lineno)
        else:
            break
    return end


def _replace_block_in_file(path: Path, block: list[str], import_line: str) -> None:
    if path.resolve() == Path(__file__).resolve():
        return
    try:
        src = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        print(yellow(f"Skipping {path}: {exc}"))
        return
    lines = src.splitlines(keepends=True)
    match = _find_block(lines, block)
    if match is None:
        return
    start, end = match
    if start > 0 and lines[start - 1].strip() == "":
        start -= 1
    if end < len(lines) and lines[end].strip() == "":
        end += 1
    del lines[start:end]
    new_src = "".join(lines)
    try:
        tree = ast.parse(new_src)
    except SyntaxError as exc:
        print(yellow(f"Skipping write for {path} (would break syntax): {exc}"))
        return
    import_name = import_line.strip().split()[-1]
    if _already_has_import(tree, import_name):
        if new_src != src:
            path.write_text(new_src, encoding="utf-8")
            print(green(f"Removed block: {path} (import already present)"))
        return
    body = new_src.splitlines(keepends=True)
    top = _top_import_end(tree)
    insert_at = top if top > 0 else (1 if body and body[0].startswith("#!") else 0)
    body.insert(insert_at, import_line if import_line.endswith("\n") else import_line + "\n")
    path.write_text("".join(body), encoding="utf-8")
    print(green(f"Removed block and added import: {path}"))


def _cmd_replace_block(args: argparse.Namespace) -> int:
    block_path = Path(args.block_file).expanduser()
    block = _read_block(block_path)
    if args.files:
        files = [Path(f) for f in args.files]
    else:
        files = _iter_py_files(Path.cwd(), SKIP_DIRS)
    for f in files:
        try:
            _replace_block_in_file(f, block, args.import_line)
        except Exception as exc:
            print(red(f"Error processing {f}: {exc}"))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pycleaner",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colour output.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_imp = sub.add_parser("imports", help="Detect (and optionally remove) unused imports.")
    p_imp.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Files, dirs, .whl, .tar.zst (default: current dir).",
    )
    p_imp.add_argument(
        "--backend",
        choices=["ast", "autoflake"],
        default="ast",
        help="Detection engine (default: ast).",
    )
    p_imp.add_argument("-a", "--autofix", action="store_true", help="Remove unused imports in-place.")
    p_imp.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")
    p_imp.add_argument("-v", "--verbose", action="store_true")
    p_imp.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Regex to exclude files (repeatable).",
    )
    p_imp.add_argument(
        "--ignore-init",
        action="store_true",
        help="Treat all imports in __init__.py as used.",
    )
    p_imp.add_argument(
        "--diff",
        action="store_true",
        help="(autoflake backend) show unified diff of changes.",
    )
    p_imp.set_defaults(func=_cmd_imports)
    p_def = sub.add_parser("defs", help="Detect/remove/extract unused functions, classes, constants.")
    p_def.add_argument("--dir", default=".", help="Root directory to scan (default: current dir).")
    p_def.add_argument(
        "--scope",
        choices=["global", "file"],
        default="global",
        help="'global' (cross-file, detect_unused.py) or 'file' (per-file, rmunused*). Default: global.",
    )
    p_def.add_argument(
        "--kind",
        choices=["all", "func", "class", "const"],
        default="all",
        help="Which symbol kinds to report/remove.",
    )
    p_def.add_argument(
        "--remove",
        action="store_true",
        help="Remove unused top-level functions/classes in-place.",
    )
    p_def.add_argument("--backup", action="store_true", help="Create .bak copies when removing.")
    p_def.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed (implies --remove).",
    )
    p_def.add_argument(
        "--extract",
        action="store_true",
        help="Extract unused objects into ./output/{func,classes,const}/.",
    )
    p_def.add_argument(
        "--extract-dir",
        default=None,
        help="Override extraction root dir (default: <dir>/output).",
    )
    p_def.set_defaults(func=_cmd_defs)
    p_vul = sub.add_parser("vulture", help="Process vulture output file and fix/annotate findings.")
    p_vul.add_argument("file", nargs="?", help="Vulture output file (default: stdin).")
    p_vul.add_argument(
        "--mode",
        choices=["comment-vars", "comment-all", "remove-all", "skip-dirs"],
        default="comment-vars",
        help="Which kind of fix to apply.",
    )
    p_vul.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes (default: show diff only).",
    )
    p_vul.add_argument("--backup", action="store_true", help="Save .bak copies before writing.")
    p_vul.add_argument("--yes", action="store_true", help="Do not prompt for confirmation.")
    p_vul.add_argument("--summary", action="store_true", help="Print summary at the end.")
    p_vul.set_defaults(func=_cmd_vulture)
    p_rep = sub.add_parser("replace", help="Replace/patch functions or code blocks across files.")
    rep_sub = p_rep.add_subparsers(dest="replace_mode", required=True)
    p_rf = rep_sub.add_parser("func", help="Replace `format_size` function with an import.")
    p_rf.add_argument("--dir", default=".")
    p_rf.add_argument(
        "-i",
        "--inspect",
        action="store_true",
        help="Match by name+argcount only (skip full body check).",
    )
    p_rf.set_defaults(func=_cmd_replace_func)
    p_rb = rep_sub.add_parser("block", help="Remove a fixed block of code from many files.")
    p_rb.add_argument("files", nargs="*", help="Target .py files (default: cwd rglob).")
    p_rb.add_argument(
        "--block-file",
        default=str(Path.home() / "lic"),
        help="Path to file containing the block to remove (default: ~/lic).",
    )
    p_rb.add_argument(
        "--import-line",
        default="from dh import cprint",
        help="Import line to ensure exists after removal (default: 'from dh import cprint').",
    )
    p_rb.set_defaults(func=_cmd_replace_block)
    return parser


def main(argv: list[str] | None = None) -> int:
    global _USE_COLOR
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "no_color", False) or not sys.stdout.isatty():
        _USE_COLOR = False
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
