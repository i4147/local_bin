#!/usr/bin/env python3
"""Add a top-level ``loguru`` configuration to Python scripts lacking one.

Inspects each target file via AST. Any file that does not already configure
``loguru`` (via ``logger.add(...)``, ``logger.configure(...)``, or
``logger.remove(...)``) gets:

- top-level ``import os`` and ``import sys`` if not already imported,
- top-level ``from loguru import logger`` if not already imported, and
- a top-level stderr sink configuration honoring ``LOGURU_LEVEL`` (default
  ``"INFO"``).

Usage:
    tools/add_loguru_config.py [--dry-run] [FILE ...]
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

LOGURU_CONFIG_BLOCK = (
    "logger.remove()\n"
    "logger.add(\n"
    "    sys.stderr,\n"
    '    level=os.environ.get("LOGURU_LEVEL", "INFO"),\n'
    '    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",\n'
    ")\n"
)


def _has_loguru_config(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
            and node.func.attr in {"add", "configure", "remove"}
        ):
            return True
    return False


def _is_version_guard(node: ast.stmt) -> bool:
    """Return True for a top-level ``if sys.version_info < ...:`` guard."""
    if not isinstance(node, ast.If):
        return False
    src = ast.unparse(node.test)
    return "sys.version_info" in src


def _header_end_line(tree: ast.Module) -> tuple[int, set[str], bool]:
    """Find the end line (1-based) of the top-level import header.

    Returns ``(insert_after_line, imported_modules, has_logger_import)``.
    """
    imported_modules: set[str] = set()
    has_logger = False
    last_import_line = 0
    doc_end_line = 0

    for idx, node in enumerate(tree.body):
        if (
            idx == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            doc_end_line = node.end_lineno or node.lineno
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is None:
                    imported_modules.add(alias.name)
            last_import_line = max(last_import_line, node.end_lineno or node.lineno)
            continue
        if isinstance(node, ast.ImportFrom):
            if node.module == "loguru" and any(a.name == "logger" and a.asname is None for a in node.names):
                has_logger = True
            last_import_line = max(last_import_line, node.end_lineno or node.lineno)
            continue
        if _is_version_guard(node):
            last_import_line = max(last_import_line, node.end_lineno or node.lineno)
            continue
        break

    insert_after = last_import_line or doc_end_line
    return insert_after, imported_modules, has_logger


def add_config_to_source(src: str) -> tuple[str, bool, str]:
    """Return ``(new_src, changed, reason)``."""
    tree = ast.parse(src)
    if _has_loguru_config(tree):
        return src, False, "already-configured"

    insert_after, imported_modules, has_logger = _header_end_line(tree)

    missing_imports: list[str] = []
    if "os" not in imported_modules:
        missing_imports.append("import os\n")
    if "sys" not in imported_modules:
        missing_imports.append("import sys\n")
    if not has_logger:
        missing_imports.append("from loguru import logger\n")

    lines = src.splitlines(keepends=True)
    # Preserve shebang / encoding comment when there is no docstring or import yet
    if insert_after == 0:
        while insert_after < len(lines) and lines[insert_after].startswith("#"):
            insert_after += 1

    before = "".join(lines[:insert_after])
    after = "".join(lines[insert_after:])

    parts: list[str] = [before]
    if before and not before.endswith("\n"):
        parts.append("\n")
    if missing_imports:
        parts.extend(missing_imports)
    if not (parts[-1].endswith("\n\n") if parts else False):
        parts.append("\n")
    parts.append(LOGURU_CONFIG_BLOCK)
    if after and not after.startswith("\n"):
        parts.append("\n")
    parts.append(after)

    new_src = "".join(parts)
    ast.parse(new_src)
    reason = "added-config-only" if has_logger else "added-import-and-config"
    return new_src, True, reason


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add top-level loguru configuration to Python scripts lacking one.")
    parser.add_argument("--dry-run", action="store_true", help="Do not modify files.")
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Python files to process (default: root *.py).",
    )
    args = parser.parse_args(argv)

    targets = sorted(args.files) if args.files else sorted(Path(".").glob("*.py"))
    counts = {"added-import-and-config": 0, "added-config-only": 0, "already-configured": 0}
    for path in targets:
        src = path.read_text(encoding="utf-8")
        new_src, changed, reason = add_config_to_source(src)
        counts[reason] = counts.get(reason, 0) + 1
        if changed and not args.dry_run:
            path.write_text(new_src, encoding="utf-8")
    prefix = "[DRY-RUN] " if args.dry_run else ""
    print(
        f"{prefix}processed={len(targets)} "
        f"added_import_and_config={counts['added-import-and-config']} "
        f"added_config_only={counts['added-config-only']} "
        f"already_configured={counts['already-configured']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
