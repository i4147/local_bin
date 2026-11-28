#!/usr/bin/env python3
"""AST-audit root *.py files for modernization and typer->argparse migration."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class FileAudit(ast.NodeVisitor):
    """Collect modernization signals from a single Python AST."""

    def __init__(self) -> None:
        self.imports_six: bool = False
        self.imports_logging: bool = False
        self.imports_loguru: bool = False
        self.imports_typer: bool = False
        self.imports_click: bool = False
        self.imports_argparse: bool = False
        self.imports_os_path: bool = False
        self.os_path_calls: list[str] = []
        self.logging_refs: int = 0
        self.loguru_config: bool = False
        self.has_parser_shim: bool = False
        self.typer_commands: int = 0
        self.typer_callbacks: int = 0
        self.typer_subapps: int = 0

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top == "six":
                self.imports_six = True
            elif top == "logging":
                self.imports_logging = True
            elif top == "loguru":
                self.imports_loguru = True
            elif top == "typer":
                self.imports_typer = True
            elif top == "click":
                self.imports_click = True
            elif top == "argparse":
                self.imports_argparse = True
            if alias.name == "os.path":
                self.imports_os_path = True
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        top = mod.split(".")[0]
        if top == "six":
            self.imports_six = True
        elif top == "logging":
            self.imports_logging = True
        elif top == "loguru":
            self.imports_loguru = True
        elif top == "typer":
            self.imports_typer = True
        elif top == "click":
            self.imports_click = True
        elif top == "argparse":
            self.imports_argparse = True
        if mod == "os.path" or (mod == "os" and any(a.name == "path" for a in node.names)):
            self.imports_os_path = True
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name == "_ParserShim":
            self.has_parser_shim = True
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Detect os.path.<attr>
        if (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == "path"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
        ):
            self.os_path_calls.append(node.attr)
        # Detect logging.<attr>
        if isinstance(node.value, ast.Name) and node.value.id == "logging":
            self.logging_refs += 1
        # Detect logger.add / logger.configure / logger.remove
        if isinstance(node.value, ast.Name) and node.value.id == "logger" and node.attr in {"add", "configure"}:
            self.loguru_config = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "command" and isinstance(node.func.value, ast.Name):
                if node.func.value.id in {"app", "cli", "typer_app"} or node.func.value.id.endswith("_app"):
                    self.typer_commands += 1
            elif node.func.attr == "callback" and isinstance(node.func.value, ast.Name):
                self.typer_callbacks += 1
            elif node.func.attr == "add_typer":
                self.typer_subapps += 1
        self.generic_visit(node)


def audit_file(path: Path) -> FileAudit:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = FileAudit()
    visitor.visit(tree)
    return visitor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detail", action="store_true", help="List files for each category")
    parser.add_argument("files", nargs="*", help="Optional subset of files (default: root *.py)")
    args = parser.parse_args(argv)

    paths = [Path(f) for f in args.files] if args.files else sorted(REPO_ROOT.glob("*.py"))
    results: dict[str, FileAudit] = {}
    for p in paths:
        results[p.name] = audit_file(p)

    six_files = [k for k, v in results.items() if v.imports_six]
    os_path_files = [k for k, v in results.items() if v.os_path_calls or v.imports_os_path]
    os_path_counter: Counter[str] = Counter()
    for v in results.values():
        os_path_counter.update(v.os_path_calls)

    stdlib_log_files = [k for k, v in results.items() if v.imports_logging]
    loguru_files = [k for k, v in results.items() if v.imports_loguru]
    no_log_files = [k for k, v in results.items() if not v.imports_logging and not v.imports_loguru]
    loguru_no_cfg = [k for k, v in results.items() if v.imports_loguru and not v.loguru_config]

    typer_files = [k for k, v in results.items() if v.imports_typer]
    typer_shim = [k for k in typer_files if results[k].has_parser_shim]
    typer_multi = [
        k
        for k in typer_files
        if not results[k].has_parser_shim
        and (results[k].typer_commands > 1 or results[k].typer_subapps > 0 or results[k].typer_callbacks > 0)
    ]
    typer_single = [k for k in typer_files if k not in typer_shim and k not in typer_multi]

    print(f"Total files scanned: {len(results)}")
    print(f"1. six imports:                  {len(six_files)}")
    print(f"2. os.path usage:                {len(os_path_files)} files ({sum(os_path_counter.values())} calls)")
    print(f"3. stdlib logging imports:       {len(stdlib_log_files)}")
    print(f"4. loguru imports:               {len(loguru_files)} (without logger.add/configure: {len(loguru_no_cfg)})")
    print(f"5. NO logging at all:            {len(no_log_files)}")
    print(f"6. typer imports:                {len(typer_files)}")
    print(f"     - _ParserShim style:        {len(typer_shim)}")
    print(f"     - native single-command:    {len(typer_single)}")
    print(f"     - native multi/subcommand:  {len(typer_multi)}")

    if os_path_counter:
        print("\nos.path calls breakdown:")
        for attr, cnt in os_path_counter.most_common():
            print(f"  {cnt:4d}  os.path.{attr}")

    if args.detail:
        sections: list[tuple[str, list[str]]] = [
            ("six", six_files),
            ("os.path", [f"{k}({len(results[k].os_path_calls)})" for k in os_path_files]),
            ("stdlib logging", stdlib_log_files),
            ("loguru without add/configure", loguru_no_cfg),
            ("NO logging", no_log_files),
            ("typer (_ParserShim)", typer_shim),
            ("typer (native single-command)", typer_single),
            ("typer (native multi/subcommand)", typer_multi),
        ]
        for title, items in sections:
            print(f"\n[{title}] ({len(items)}):")
            for item in items:
                print(f"  {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
