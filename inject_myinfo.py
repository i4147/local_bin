#!/data/data/com.termux/files/usr/bin/env python
"""
Inject personal info from ~/.myinfo into pyproject.toml, setup.cfg, and setup.py.

~/.myinfo:
{
  "name": "Isaac Onagh",
  "email": "yesnacoc@gmail.com",
  "github_username": "i4147"
}

What gets updated
-----------------
pyproject.toml
    [project].authors   -> [{ name = ..., email = ... }]
    [project].urls      -> Homepage / Repository / Issues
                           (uses github_username + the existing [project].name)

setup.cfg (if present)
    [metadata]
        author       = <name>
        author_email = <email>
        url          = https://github.com/<github_username>

setup.py (if present and pyproject.toml is absent, or --force-setuppy)
    setup(author=..., author_email=..., url=...) via AST-based source edit.
    Existing keywords are replaced in place; missing ones are appended.
    Falls back to directory name when `name=` is not a literal string.

Usage
-----
    pip install tomlkit
    python inject_myinfo.py
    python inject_myinfo.py --pyproject path/to/pyproject.toml --force-setuppy
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import tomlkit
except ImportError:
    sys.exit("This script needs 'tomlkit'. Install with: pip install tomlkit")


INFO_PATH = Path.home() / ".myinfo"
DEFAULT_PYPROJECT = Path("pyproject.toml")
DEFAULT_SETUPCFG = Path("setup.cfg")
DEFAULT_SETUPPY = Path("setup.py")
REQUIRED_KEYS = ("name", "email", "github_username")

Info = dict[str, str]
Edit = tuple[int, int, str]  # (start_offset, end_offset, replacement)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
def load_info(path: Path) -> Info:
    if not path.exists():
        sys.exit(f"Missing {path}")
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid JSON in {path}: {e}")
    if not isinstance(data, dict):
        sys.exit(f"{path} must contain a JSON object")
    missing = [k for k in REQUIRED_KEYS if not data.get(k)]
    if missing:
        sys.exit(f"Missing/empty keys in {path}: {missing}")
    return {k: str(data[k]) for k in REQUIRED_KEYS}


# ---------------------------------------------------------------------------
# pyproject.toml (via tomlkit -> preserves formatting/comments)
# ---------------------------------------------------------------------------
def _pyproject_slug(doc: Any) -> str:
    try:
        name = doc["project"]["name"]
        if name:
            return str(name)
    except (KeyError, TypeError):
        pass
    return Path.cwd().name


def update_pyproject(info: Info, path: Path) -> None:
    if not path.exists():
        print(f"Skipping {path} (not found)")
        return

    doc = tomlkit.parse(path.read_text(encoding="utf-8"))

    if "project" not in doc:
        doc["project"] = tomlkit.table()
    project = doc["project"]

    # [project].authors  -> array of tables
    author = tomlkit.table()
    author["name"] = info["name"]
    author["email"] = info["email"]
    authors = tomlkit.aot()
    authors.append(author)
    project["authors"] = authors

    # [project].urls
    slug = _pyproject_slug(doc)
    user = info["github_username"]
    base = f"https://github.com/{user}/{slug}"
    if "urls" not in project:
        project["urls"] = tomlkit.table()
    urls = project["urls"]
    urls["Homepage"] = base
    urls["Repository"] = base
    urls["Issues"] = f"{base}/issues"

    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    print(f"Updated {path}  ->  authors={info['email']}, urls={base}")


# ---------------------------------------------------------------------------
# setup.cfg (line-based edit; preserves comments/layout)
# ---------------------------------------------------------------------------
_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KEY_RE = re.compile(r"^([A-Za-z0-9_\-]+)\s*=")


def _update_ini_section(text: str, section: str, updates: dict[str, str]) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_section = False
    found_section = False
    written: set[str] = set()

    def flush_missing() -> None:
        for k, v in updates.items():
            if k not in written:
                out.append(f"{k} = {v}\n")
                written.add(k)

    for line in lines:
        h = _HEADER_RE.match(line.rstrip("\n"))
        if h:
            if in_section:
                flush_missing()
            in_section = h.group(1).strip() == section
            found_section = found_section or in_section
            out.append(line)
            continue
        if in_section:
            km = _KEY_RE.match(line)
            if km and km.group(1) in updates:
                out.append(f"{km.group(1)} = {updates[km.group(1)]}\n")
                written.add(km.group(1))
                continue
        out.append(line)

    if in_section:
        flush_missing()

    if not found_section:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        if out and out[-1].strip():
            out.append("\n")
        out.append(f"[{section}]\n")
        for k, v in updates.items():
            out.append(f"{k} = {v}\n")

    return "".join(out)


def update_setupcfg(info: Info, path: Path) -> None:
    if not path.exists():
        print(f"Skipping {path} (not found)")
        return
    text = path.read_text(encoding="utf-8")
    updates = {
        "author": info["name"],
        "author_email": info["email"],
        "url": f"https://github.com/{info['github_username']}",
    }
    path.write_text(_update_ini_section(text, "metadata", updates), encoding="utf-8")
    print(f"Updated {path}  ->  [metadata] author/author_email/url")


# ---------------------------------------------------------------------------
# setup.py (AST-based targeted edit; preserves formatting and comments)
# ---------------------------------------------------------------------------
def _offset(src: str, lineno: int, col: int) -> int:
    """Convert an ast (lineno, col_offset) pair into an absolute char offset."""
    if lineno <= 1:
        return col
    lines = src.splitlines(keepends=True)
    return sum(len(line) for line in lines[: lineno - 1]) + col


def _find_setup_call(src: str) -> tuple[ast.Call | None, str | None]:
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return None, f"syntax error: {e}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if (isinstance(fn, ast.Name) and fn.id == "setup") or (
                isinstance(fn, ast.Attribute) and fn.attr == "setup"
            ):
                return node, None
    return None, "no setup() call found"


def _guess_indent(src: str, call: ast.Call) -> str:
    """Best-effort indentation used by the first arg/keyword of `call`."""
    if call.keywords:
        kw = call.keywords[0]
        if kw.arg is None:
            return "    "
        val_start = _offset(src, kw.value.lineno, kw.value.col_offset)
        eq_pos = src.rfind("=", 0, val_start)
        if eq_pos < 0:
            return "    "
        p = eq_pos - 1
        while p >= 0 and src[p] in " \t":
            p -= 1
        arg_start = p - len(kw.arg) + 1
        if arg_start < 0 or src[arg_start : arg_start + len(kw.arg)] != kw.arg:
            return "    "
        line_start = src.rfind("\n", 0, arg_start) + 1
        indent = src[line_start:arg_start]
        return indent if indent.strip() == "" else "    "
    if call.args:
        arg = call.args[0]
        arg_start = _offset(src, arg.lineno, arg.col_offset)
        line_start = src.rfind("\n", 0, arg_start) + 1
        indent = src[line_start:arg_start]
        return indent if indent.strip() == "" else "    "
    return "    "


def _setup_slug(src: str, call: ast.Call) -> str:
    for kw in call.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return Path.cwd().name


def _apply_edits(src: str, edits: list[Edit]) -> str:
    """Apply edits from highest offset to lowest so earlier offsets stay valid."""
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        src = src[:start] + repl + src[end:]
    return src


def update_setuppy(info: Info, path: Path) -> None:
    if not path.exists():
        print(f"Skipping {path} (not found)")
        return

    src = path.read_text(encoding="utf-8")
    call, err = _find_setup_call(src)
    if call is None:
        print(f"Warning: {path}: {err}; skipping")
        return

    if any(kw.arg is None for kw in call.keywords):
        print(f"Warning: {path}: setup(**kwargs) present; skipping to avoid conflict")
        return

    slug = _setup_slug(src, call)
    new_values: dict[str, str] = {
        "author": info["name"],
        "author_email": info["email"],
        "url": f"https://github.com/{info['github_username']}/{slug}",
    }

    existing: dict[str, ast.keyword] = {kw.arg: kw for kw in call.keywords if kw.arg}
    edits: list[Edit] = []

    # 1) Replace existing keyword values in place.
    for key, new_val in new_values.items():
        kw = existing.get(key)
        if kw is not None:
            v = kw.value
            start = _offset(src, v.lineno, v.col_offset)
            end = _offset(src, v.end_lineno, v.end_col_offset)
            edits.append((start, end, repr(new_val)))

    # 2) Append missing keywords.
    missing = [(k, v) for k, v in new_values.items() if k not in existing]
    if missing:
        close = _offset(src, call.end_lineno, call.end_col_offset) - 1  # index of ')'
        if close < 0 or src[close] != ")":
            print(f"Warning: {path}: could not locate closing paren; skipping")
            return

        single_line = call.lineno == call.end_lineno
        if single_line:
            j = close - 1
            while j >= 0 and src[j] in " \t":
                j -= 1
            if src[j] == "(":
                sep = ""
            elif src[j] == ",":
                sep = " "
            else:
                sep = ", "
            insert = sep + ", ".join(f"{k}={v!r}" for k, v in missing)
            edits.append((close, close, insert))
        else:
            line_start = src.rfind("\n", 0, close) + 1
            leading = src[line_start:close]
            indent = _guess_indent(src, call)
            if leading.strip() == "":
                # ')' is alone on its line -> insert new lines before it.
                # Ensure the previous arg ends with a comma.
                j = line_start - 1
                while j >= 0 and src[j] in " \t":
                    j -= 1
                if j >= 0 and src[j] == "\n":
                    j -= 1
                    while j >= 0 and src[j] in " \t":
                        j -= 1
                if j >= 0 and src[j] not in "(,":
                    edits.append((j + 1, j + 1, ","))
                insert = "".join(f"{indent}{k}={v!r},\n" for k, v in missing)
                edits.append((line_start, line_start, insert))
            else:
                # ')' shares a line with content -> insert before ')'.
                j = close - 1
                while j >= 0 and src[j] in " \t\n\r":
                    j -= 1
                prefix = "" if j < 0 or src[j] in "(," else ","
                insert = prefix + "".join(f"\n{indent}{k}={v!r}," for k, v in missing)
                edits.append((close, close, insert))

    path.write_text(_apply_edits(src, edits), encoding="utf-8")
    print(f"Updated {path}  ->  setup(author/author_email/url)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--info", default=str(INFO_PATH), help="path to ~/.myinfo")
    p.add_argument("--pyproject", default=str(DEFAULT_PYPROJECT))
    p.add_argument("--setupcfg", default=str(DEFAULT_SETUPCFG))
    p.add_argument("--setuppy", default=str(DEFAULT_SETUPPY))
    p.add_argument(
        "--force-setuppy",
        action="store_true",
        help="inject into setup.py even when pyproject.toml exists",
    )
    args = p.parse_args(argv)

    info = load_info(Path(args.info))
    pyproject_path = Path(args.pyproject)
    setuppy_path = Path(args.setuppy)

    update_pyproject(info, pyproject_path)
    update_setupcfg(info, Path(args.setupcfg))

    if pyproject_path.exists() and not args.force_setuppy:
        print(f"Skipping {setuppy_path} (pyproject.toml present; pass --force-setuppy to override)")
    else:
        update_setuppy(info, setuppy_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
