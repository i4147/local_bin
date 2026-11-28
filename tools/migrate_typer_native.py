#!/usr/bin/env python3
"""Migrate single-command native Typer scripts to stdlib ``argparse``.

Converts modules using ``app = typer.Typer(...)`` and ``@app.command(...)``
with ``param: Type = typer.Option(...)`` / ``typer.Argument(...)`` defaults
into clean ``argparse.ArgumentParser`` builders while preserving all flags,
types, bounds (``min``), ``count`` flags, ``BooleanOptionalAction`` /
asymmetric slash pairs, ``no_args_is_help``, ``typer.echo``, and ``typer.Exit``.

Usage:
    tools/migrate_typer_native.py [--dry-run] FILE [FILE ...]
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import re
import sys


@dataclass(frozen=True)
class _ParamSpec:
    name: str
    kind: str  # "argument" or "option"
    ann_src: str
    default_src: str
    flags: tuple[str, ...]
    help_src: str | None
    min_src: str | None
    count: bool


def _is_typer_call(node: ast.AST | None, attr: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "typer"
        and node.func.attr == attr
    )


def _kw_value(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _base_type_from_ann(ann_src: str, default_node: ast.expr | None) -> tuple[str, bool]:
    """Return ``(scalar_type_expr, is_list)`` for a parameter annotation."""
    s = ann_src.strip()
    # Strip outer "| None" or "None |"
    parts = [p.strip() for p in s.split("|") if p.strip() != "None"]
    core = parts[0] if parts else s
    m = re.fullmatch(r"list\[(.+)\]", core)
    if m:
        inner = m.group(1).strip()
        return inner, True
    if core in {"bool", "int", "float", "str", "Path"}:
        return core, False
    # Infer from default when annotation is Any or missing
    if isinstance(default_node, ast.Constant):
        if isinstance(default_node.value, bool):
            return "bool", False
        if isinstance(default_node.value, int):
            return "int", False
        if isinstance(default_node.value, float):
            return "float", False
        if isinstance(default_node.value, str):
            return "str", False
    if isinstance(default_node, ast.List):
        return "str", True
    if default_node is not None:
        ds = ast.unparse(default_node)
        if ds in {"DEFAULT_LINE_LENGTH", "cpu_count()"} or ds.endswith("_LENGTH"):
            return "int", False
        if ds.startswith("Path("):
            return "Path", False
    return "str", False


def _is_bool_default(default_node: ast.expr | None) -> tuple[bool, bool | None]:
    """Check if default represents a bool value (including `_d(key, True/False)`)."""
    if isinstance(default_node, ast.Constant) and isinstance(default_node.value, bool):
        return True, default_node.value
    if (
        isinstance(default_node, ast.Call)
        and len(default_node.args) >= 2
        and isinstance(default_node.args[1], ast.Constant)
        and isinstance(default_node.args[1].value, bool)
    ):
        return True, default_node.args[1].value
    return False, None


def _extract_command(tree: ast.Module) -> tuple[str, ast.Assign, ast.FunctionDef, ast.Call] | None:
    app_assign: ast.Assign | None = None
    app_var = ""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _is_typer_call(node.value, "Typer")
        ):
            app_assign = node
            app_var = node.targets[0].id
            break
    if app_assign is None:
        return None

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id == app_var
                    and dec.func.attr == "command"
                ):
                    return app_var, app_assign, node, dec
    return None


def _parse_params(fn: ast.FunctionDef) -> list[_ParamSpec]:
    args = fn.args.args
    defaults = fn.args.defaults
    offset = len(args) - len(defaults)
    specs: list[_ParamSpec] = []
    for idx, arg in enumerate(args):
        default = defaults[idx - offset] if idx >= offset else None
        ann_src = ast.unparse(arg.annotation) if arg.annotation else "Any"
        if _is_typer_call(default, "Argument"):
            assert isinstance(default, ast.Call)
            def_node = default.args[0] if default.args else None
            def_src = ast.unparse(def_node) if def_node is not None else "None"
            help_node = _kw_value(default, "help")
            specs.append(
                _ParamSpec(
                    name=arg.arg,
                    kind="argument",
                    ann_src=ann_src,
                    default_src=def_src,
                    flags=(),
                    help_src=ast.unparse(help_node) if help_node is not None else None,
                    min_src=None,
                    count=False,
                )
            )
        elif _is_typer_call(default, "Option"):
            assert isinstance(default, ast.Call)
            def_node = default.args[0] if default.args else None
            def_src = ast.unparse(def_node) if def_node is not None else "None"
            flags: list[str] = []
            for a in default.args[1:]:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    flags.append(a.value)
            help_node = _kw_value(default, "help")
            min_node = _kw_value(default, "min")
            count_node = _kw_value(default, "count")
            is_count = isinstance(count_node, ast.Constant) and bool(count_node.value)
            specs.append(
                _ParamSpec(
                    name=arg.arg,
                    kind="option",
                    ann_src=ann_src,
                    default_src=def_src,
                    flags=tuple(flags),
                    help_src=ast.unparse(help_node) if help_node is not None else None,
                    min_src=ast.unparse(min_node) if min_node is not None else None,
                    count=is_count,
                )
            )
        else:
            def_src = ast.unparse(default) if default is not None else "None"
            specs.append(
                _ParamSpec(
                    name=arg.arg,
                    kind="option",
                    ann_src=ann_src,
                    default_src=def_src,
                    flags=(),
                    help_src=None,
                    min_src=None,
                    count=False,
                )
            )
    return specs


def _emit_parser_code(
    specs: list[_ParamSpec],
    app_call: ast.Call,
    fn: ast.FunctionDef,
) -> tuple[str, bool]:
    """Build helper functions (if needed) + ``build_parser()``."""
    prog_node = _kw_value(app_call, "name")
    help_node = _kw_value(app_call, "help")
    no_args_node = _kw_value(app_call, "no_args_is_help")
    no_args_is_help = isinstance(no_args_node, ast.Constant) and bool(no_args_node.value)

    # Collect all explicitly declared flags to avoid --no-<x> collisions.
    all_explicit_flags: set[str] = set()
    for sp in specs:
        for fl in sp.flags:
            for tok in fl.split("/"):
                all_explicit_flags.add(tok)

    needs_min_int = False
    needs_min_float = False
    add_lines: list[str] = []

    for sp in specs:
        def_ast = ast.parse(sp.default_src, mode="eval").body
        base_type, is_list = _base_type_from_ann(sp.ann_src, def_ast)
        is_bool_def, bool_val = _is_bool_default(def_ast)

        if sp.kind == "argument":
            kw: list[str] = []
            if is_list:
                kw.append('nargs="*"')
                if base_type not in {"str", "Any"}:
                    kw.append(f"type={base_type}")
                if sp.default_src not in {"None", "..."}:
                    kw.append(f"default={sp.default_src}")
                else:
                    kw.append("default=None")
            else:
                if sp.default_src != "...":
                    kw.append('nargs="?"')
                    kw.append(f"default={sp.default_src}")
                if base_type not in {"str", "Any"}:
                    kw.append(f"type={base_type}")
            if sp.help_src:
                kw.append(f"help={sp.help_src}")
            add_lines.append(f"    parser.add_argument({sp.name!r}, {', '.join(kw)})")
            continue

        # Option
        slash_flags = [f for f in sp.flags if "/" in f]
        plain_flags = [f for f in sp.flags if "/" not in f]

        if slash_flags:
            pos_tok, neg_tok = slash_flags[0].split("/", 1)
            pos_long = pos_tok.strip()
            neg_long = neg_tok.strip()
            if pos_long.startswith("--") and neg_long == f"--no-{pos_long[2:]}":
                opts = [*plain_flags, pos_long]
                args_str = ", ".join(repr(o) for o in opts)
                kw = [
                    f"dest={sp.name!r}",
                    "action=argparse.BooleanOptionalAction",
                    f"default={sp.default_src}",
                ]
                if sp.help_src:
                    kw.append(f"help={sp.help_src}")
                add_lines.append(f"    parser.add_argument({args_str}, {', '.join(kw)})")
            else:
                pos_opts = [*plain_flags, pos_long]
                pos_args = ", ".join(repr(o) for o in pos_opts)
                kw_pos = [f"dest={sp.name!r}", 'action="store_true"', f"default={sp.default_src}"]
                if sp.help_src:
                    kw_pos.append(f"help={sp.help_src}")
                add_lines.append(f"    parser.add_argument({pos_args}, {', '.join(kw_pos)})")
                add_lines.append(
                    f"    parser.add_argument({neg_long!r}, dest={sp.name!r}, action=\"store_false\")"
                )
            continue

        # Determine flags if none explicitly passed
        if not plain_flags:
            kebab = "--" + sp.name.rstrip("_").replace("_", "-")
            opts = [kebab]
        else:
            # Sort short flags before long flags for clean argparse help
            opts = sorted(plain_flags, key=lambda f: (f.startswith("--"), len(f)))
        args_str = ", ".join(repr(o) for o in opts)

        if sp.count:
            kw = [f"dest={sp.name!r}", 'action="count"', f"default={sp.default_src}"]
            if sp.help_src:
                kw.append(f"help={sp.help_src}")
            add_lines.append(f"    parser.add_argument({args_str}, {', '.join(kw)})")
            continue

        if base_type == "bool" or is_bool_def:
            long_flags = [o for o in opts if o.startswith("--")]
            use_bool_opt = False
            if bool_val is True and long_flags:
                neg_candidate = f"--no-{long_flags[0][2:]}"
                if neg_candidate not in all_explicit_flags:
                    use_bool_opt = True
            if use_bool_opt:
                kw = [
                    f"dest={sp.name!r}",
                    "action=argparse.BooleanOptionalAction",
                    f"default={sp.default_src}",
                ]
            else:
                kw = [f"dest={sp.name!r}", 'action="store_true"', f"default={sp.default_src}"]
            if sp.help_src:
                kw.append(f"help={sp.help_src}")
            add_lines.append(f"    parser.add_argument({args_str}, {', '.join(kw)})")
            continue

        kw = [f"dest={sp.name!r}"]
        if is_list:
            kw.append('action="append"')
            kw.append(f"default={sp.default_src}")
            if base_type not in {"str", "Any"}:
                kw.append(f"type={base_type}")
        else:
            kw.append(f"default={sp.default_src}")
            if sp.min_src is not None:
                if base_type == "float":
                    needs_min_float = True
                    kw.append(f"type=_min_float({sp.min_src})")
                else:
                    needs_min_int = True
                    kw.append(f"type=_min_int({sp.min_src})")
            elif base_type not in {"str", "Any"}:
                kw.append(f"type={base_type}")
        if sp.help_src:
            kw.append(f"help={sp.help_src}")
        add_lines.append(f"    parser.add_argument({args_str}, {', '.join(kw)})")

    helper_blocks: list[str] = []
    if needs_min_int:
        helper_blocks.append(
            "def _min_int(minimum: int):\n"
            "    def _parse(value: str) -> int:\n"
            "        num = int(value)\n"
            "        if num < minimum:\n"
            '            raise argparse.ArgumentTypeError(f"must be >= {minimum}")\n'
            "        return num\n"
            "    return _parse\n"
        )
    if needs_min_float:
        helper_blocks.append(
            "def _min_float(minimum: float):\n"
            "    def _parse(value: str) -> float:\n"
            "        num = float(value)\n"
            "        if num < minimum:\n"
            '            raise argparse.ArgumentTypeError(f"must be >= {minimum}")\n'
            "        return num\n"
            "    return _parse\n"
        )

    ctor_args: list[str] = []
    if prog_node is not None:
        ctor_args.append(f"prog={ast.unparse(prog_node)}")
    if help_node is not None:
        ctor_args.append(f"description={ast.unparse(help_node)}")
    elif (doc := ast.get_docstring(fn)) is not None:
        ctor_args.append(f"description={doc!r}")

    parser_fn = [
        "def build_parser() -> argparse.ArgumentParser:",
        '    """Build the command-line argument parser."""',
        f"    parser = argparse.ArgumentParser({', '.join(ctor_args)})",
        *add_lines,
        "    return parser",
    ]
    out = "\n\n".join([*helper_blocks, "\n".join(parser_fn)])
    return out, no_args_is_help


def _rewrite_typer_calls(src: str) -> str:
    """Replace ``typer.echo(...)`` and ``typer.Exit(...)`` with stdlib equivalents."""
    # Rewrite typer.echo calls using AST span replacement from back to front.
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    line_offsets: list[int] = []
    pos = 0
    for ln in lines:
        line_offsets.append(pos)
        pos += len(ln)

    def offset(lineno: int, col: int) -> int:
        return line_offsets[lineno - 1] + col

    replacements: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if _is_typer_call(node, "echo"):
            assert isinstance(node, ast.Call)
            assert node.end_lineno is not None and node.end_col_offset is not None
            start = offset(node.lineno, node.col_offset)
            end = offset(node.end_lineno, node.end_col_offset)
            pos_args = [ast.unparse(a) for a in node.args]
            err_kw = _kw_value(node, "err")
            nl_kw = _kw_value(node, "nl")
            extra_kw: list[str] = []
            if isinstance(err_kw, ast.Constant) and bool(err_kw.value):
                extra_kw.append("file=sys.stderr")
            if isinstance(nl_kw, ast.Constant) and not bool(nl_kw.value):
                extra_kw.append('end=""')
            all_args = ", ".join([*pos_args, *extra_kw])
            replacements.append((start, end, f"print({all_args})"))
        elif _is_typer_call(node, "Exit"):
            assert isinstance(node, ast.Call)
            assert node.end_lineno is not None and node.end_col_offset is not None
            start = offset(node.lineno, node.col_offset)
            end = offset(node.end_lineno, node.end_col_offset)
            code_kw = _kw_value(node, "code")
            if code_kw is not None:
                code_expr = ast.unparse(code_kw)
            elif node.args:
                code_expr = ast.unparse(node.args[0])
            else:
                code_expr = "0"
            replacements.append((start, end, f"SystemExit({code_expr})"))
        elif (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Attribute)
            and isinstance(node.exc.value, ast.Name)
            and node.exc.value.id == "typer"
            and node.exc.attr == "Exit"
        ):
            assert node.exc.end_lineno is not None and node.exc.end_col_offset is not None
            start = offset(node.exc.lineno, node.exc.col_offset)
            end = offset(node.exc.end_lineno, node.exc.end_col_offset)
            replacements.append((start, end, "SystemExit(0)"))

    replacements.sort(key=lambda r: r[0], reverse=True)
    out = src
    for start, end, repl in replacements:
        out = out[:start] + repl + out[end:]
    return out


def migrate_source(src: str) -> str:
    """Convert a single-command native Typer source string to ``argparse``."""
    tree = ast.parse(src)
    extracted = _extract_command(tree)
    if extracted is None:
        raise ValueError("No single-command typer.Typer() / @app.command() found")
    app_var, app_assign, fn, dec = extracted
    assert isinstance(app_assign.value, ast.Call)
    specs = _parse_params(fn)
    parser_code, no_args_is_help = _emit_parser_code(specs, app_assign.value, fn)

    # Build clean parameter list for the function itself (plain defaults)
    param_lines: list[str] = []
    for sp in specs:
        param_lines.append(f"    {sp.name}: {sp.ann_src} = {sp.default_src},")
    ret_src = f" -> {ast.unparse(fn.returns)}" if fn.returns is not None else ""
    new_fn_header = f"def {fn.name}(\n" + "\n".join(param_lines) + f"\n){ret_src}:"

    # Slice lines: replace app_assign..fn signature (up to body[0])
    lines = src.splitlines(keepends=True)
    assert app_assign.end_lineno is not None
    dec_start = min(d.lineno for d in fn.decorator_list)
    body_start = fn.body[0].lineno

    # Ensure we replace from app_assign.lineno to body_start - 1 (inclusive)
    # preserving any code strictly between app_assign.end_lineno and dec_start.
    between = "".join(lines[app_assign.end_lineno : dec_start - 1]).strip("\n")
    replacement_mid = parser_code + "\n\n\n"
    if between:
        replacement_mid += between + "\n\n\n"
    replacement_mid += new_fn_header + "\n"

    before = "".join(lines[: app_assign.lineno - 1])
    after = "".join(lines[body_start - 1 :])
    out = before + replacement_mid + after

    # Rewrite typer.echo / typer.Exit
    out = _rewrite_typer_calls(out)

    # Remove `import typer` and ensure top-level `import argparse`
    out = re.sub(r"^import typer\s*\n", "", out, flags=re.MULTILINE)
    if not re.search(r"^import\s+argparse\b", out, flags=re.MULTILINE):
        m = re.search(r"^from __future__ import [^\n]+\n", out, flags=re.MULTILINE)
        if m:
            out = out[: m.end()] + "import argparse\n" + out[m.end() :]
        else:
            out = "import argparse\n" + out

    # Rewrite entry point: either `if __name__ == "__main__":\n    app()` or `_app()` call inside `def main(...)`
    dispatch_body = (
        f"    parser = build_parser()\n"
        f"    raw = sys.argv[1:] if argv is None else list(argv)\n"
        + (
            "    if not raw:\n        parser.print_help()\n        return 0\n"
            if no_args_is_help
            else ""
        )
        + f"    ns = parser.parse_args(raw)\n"
        f"    {fn.name}(**vars(ns))\n"
        f"    return 0\n"
    )
    app_call_pat = re.compile(rf"if __name__ == \"__main__\":\s*\n\s+{re.escape(app_var)}\(\)\s*$")
    if app_call_pat.search(out):
        cli_fn = (
            f"\n\ndef cli(argv: list[str] | None = None) -> int:\n"
            f'    """CLI entry point."""\n'
            f"{dispatch_body}\n\n"
            f'if __name__ == "__main__":\n'
            f"    raise SystemExit(cli())\n"
        )
        out = app_call_pat.sub(cli_fn.lstrip("\n"), out)
    else:
        # Handle pyrg.py-style `_app()` inside `main(argv)`
        inline_call = re.compile(rf"^(\s+){re.escape(app_var)}\(\)\s*$", flags=re.MULTILINE)
        if inline_call.search(out):
            def _repl(match: re.Match[str]) -> str:
                ind = match.group(1)
                lines_d = [
                    f"{ind}parser = build_parser()",
                    f"{ind}raw = sys.argv[1:]",
                ]
                if no_args_is_help:
                    lines_d.extend([
                        f"{ind}if not raw:",
                        f"{ind}    parser.print_help()",
                        f"{ind}    return 0",
                    ])
                lines_d.extend([
                    f"{ind}ns = parser.parse_args(raw)",
                    f"{ind}{fn.name}(**vars(ns))",
                ])
                return "\n".join(lines_d)

            out = inline_call.sub(_repl, out, count=1)

    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate single-command native Typer scripts to argparse.")
    parser.add_argument("--dry-run", action="store_true", help="Do not overwrite files.")
    parser.add_argument("files", nargs="+", type=Path, help="Python files to migrate.")
    args = parser.parse_args(argv)

    for path in args.files:
        src = path.read_text(encoding="utf-8")
        new_src = migrate_source(src)
        ast.parse(new_src, filename=str(path))
        if not args.dry_run:
            path.write_text(new_src, encoding="utf-8")
        print(f"{'DRY ' if args.dry_run else 'OK  '} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
