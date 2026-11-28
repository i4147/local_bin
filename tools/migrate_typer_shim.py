#!/usr/bin/env python3
"""Migrate argparse2typer-generated _ParserShim scripts back to clean argparse."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
import sys
from typing import Any


@dataclass
class ParamSpec:
    name: str
    is_option: bool
    flags: list[str]
    base_type: str
    is_optional: bool
    is_list: bool
    tuple_arity: int | None
    default_expr: str | None
    required: bool
    help_expr: str | None = None
    metavar_expr: str | None = None
    count: bool = False
    hidden: bool = False
    parser_expr: str | None = None
    is_version: bool = False
    choices_expr: str | None = None
    enum_name: str | None = None
    post_default_expr: str | None = None


@dataclass
class CommandSpec:
    fn_name: str
    cmd_name: str | None
    help_expr: str | None
    params: list[ParamSpec]
    extra_defaults: dict[str, str] = field(default_factory=dict)
    is_callback: bool = False


def _unwrap_annotated(ann: ast.expr) -> tuple[ast.expr, ast.Call | None]:
    if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name) and ann.value.id == "Annotated":
        if isinstance(ann.slice, ast.Tuple) and len(ann.slice.elts) >= 2:
            meta = ann.slice.elts[1]
            return ann.slice.elts[0], meta if isinstance(meta, ast.Call) else None
    return ann, None


def _analyze_type(t_node: ast.expr) -> tuple[str, bool, bool, int | None]:
    """Return (base_type_str, is_optional, is_list, tuple_arity)."""
    is_optional = False
    inner = t_node
    if isinstance(t_node, ast.BinOp) and isinstance(t_node.op, ast.BitOr):
        left_s = ast.unparse(t_node.left)
        right_s = ast.unparse(t_node.right)
        if right_s == "None":
            is_optional = True
            inner = t_node.left
        elif left_s == "None":
            is_optional = True
            inner = t_node.right
    if isinstance(inner, ast.Subscript) and isinstance(inner.value, ast.Name):
        if inner.value.id == "list":
            return ast.unparse(inner.slice), is_optional, True, None
        if inner.value.id == "tuple" and isinstance(inner.slice, ast.Tuple):
            elts = inner.slice.elts
            return ast.unparse(elts[0]), is_optional, False, len(elts)
    return ast.unparse(inner), is_optional, False, None


def _extract_typer_call(
    arg: ast.arg,
    default_node: ast.expr | None,
    enum_values: dict[str, list[str]],
) -> ParamSpec:
    ann = arg.annotation
    meta_call: ast.Call | None = None
    base_node: ast.expr = ast.Name(id="str", ctx=ast.Load())
    if ann is not None:
        base_node, meta_call = _unwrap_annotated(ann)
    default_expr: str | None = ast.unparse(default_node) if default_node is not None else None
    if meta_call is None and isinstance(default_node, ast.Call):
        if (
            isinstance(default_node.func, ast.Attribute)
            and isinstance(default_node.func.value, ast.Name)
            and default_node.func.value.id == "typer"
        ):
            meta_call = default_node
            if default_node.args:
                first = default_node.args[0]
                default_expr = (
                    None if (isinstance(first, ast.Constant) and first.value is Ellipsis) else ast.unparse(first)
                )
            else:
                default_expr = None

    base_type, is_optional, is_list, tuple_arity = _analyze_type(base_node)
    is_option = True
    flags: list[str] = []
    help_expr: str | None = None
    metavar_expr: str | None = None
    count = False
    hidden = False
    parser_expr: str | None = None
    is_version = False

    if meta_call is not None and isinstance(meta_call.func, ast.Attribute):
        is_option = meta_call.func.attr == "Option"
        for a in meta_call.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                flags.append(a.value)
        for kw in meta_call.keywords:
            if kw.arg == "help":
                help_expr = ast.unparse(kw.value)
            elif kw.arg == "metavar":
                metavar_expr = ast.unparse(kw.value)
            elif kw.arg == "count" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                count = True
            elif kw.arg == "hidden" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                hidden = True
            elif kw.arg == "parser":
                parser_expr = ast.unparse(kw.value)
            elif kw.arg == "callback":
                is_version = True

    if is_option and not flags:
        flags = ["--" + arg.arg.replace("_", "-")]

    enum_name = base_type if base_type in enum_values else None
    choices_expr: str | None = None
    if enum_name is not None:
        choices_expr = repr(enum_values[enum_name])
        if default_expr is not None and default_expr.startswith(f"{enum_name}."):
            member_name = default_expr.split(".", 1)[1]
            # Convert enum default like Format.json -> its string value via lookup later
            default_expr = f"__ENUM__{enum_name}__{member_name}"

    required = default_node is None and not is_version
    return ParamSpec(
        name=arg.arg,
        is_option=is_option,
        flags=flags,
        base_type=base_type,
        is_optional=is_optional,
        is_list=is_list,
        tuple_arity=tuple_arity,
        default_expr=default_expr,
        required=required,
        help_expr=help_expr,
        metavar_expr=metavar_expr,
        count=count,
        hidden=hidden,
        parser_expr=parser_expr,
        is_version=is_version,
        choices_expr=choices_expr,
        enum_name=enum_name,
    )


def _extract_version_expr(fn: ast.FunctionDef) -> str | None:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "echo" and node.args:
                return ast.unparse(node.args[0])
    return None


def _extract_body_hints(fn: ast.FunctionDef) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return (choices_by_param, post_default_by_param, extra_state_defaults)."""
    choices: dict[str, str] = {}
    post_defaults: dict[str, str] = {}
    extra_defaults: dict[str, str] = {}
    pnames = {a.arg for a in fn.args.args + fn.args.kwonlyargs}

    for stmt in fn.body:
        # Pattern: if x is not None and x not in <choices>: raise typer.BadParameter(...)
        if isinstance(stmt, ast.If):
            u = ast.unparse(stmt)
            if "BadParameter" in u and isinstance(stmt.test, ast.BoolOp) and len(stmt.test.values) == 2:
                cmp2 = stmt.test.values[1]
                if isinstance(cmp2, ast.Compare) and len(cmp2.ops) == 1 and isinstance(cmp2.ops[0], ast.NotIn):
                    if isinstance(cmp2.left, ast.Name):
                        choices[cmp2.left.id] = ast.unparse(cmp2.comparators[0])
            # Pattern: if x is None: x = <expr>
            elif (
                isinstance(stmt.test, ast.Compare)
                and len(stmt.test.ops) == 1
                and isinstance(stmt.test.ops[0], ast.Is)
                and isinstance(stmt.test.left, ast.Name)
                and isinstance(stmt.test.comparators[0], ast.Constant)
                and stmt.test.comparators[0].value is None
                and len(stmt.body) == 1
                and isinstance(stmt.body[0], ast.Assign)
                and len(stmt.body[0].targets) == 1
                and isinstance(stmt.body[0].targets[0], ast.Name)
                and stmt.body[0].targets[0].id == stmt.test.left.id
            ):
                post_defaults[stmt.test.left.id] = ast.unparse(stmt.body[0].value)
        # Pattern: x = x or []
        elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            tname = stmt.targets[0].id
            if isinstance(stmt.value, ast.BoolOp) and isinstance(stmt.value.op, ast.Or) and len(stmt.value.values) == 2:
                if isinstance(stmt.value.values[0], ast.Name) and stmt.value.values[0].id == tname:
                    post_defaults[tname] = ast.unparse(stmt.value.values[1])
        # Pattern: _STATE.update(...)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "update":
                if isinstance(call.func.value, ast.Name) and call.func.value.id == "_STATE":
                    for kw in call.keywords:
                        if kw.arg is None:
                            continue
                        val = ast.unparse(kw.value)
                        if kw.arg not in pnames:
                            extra_defaults[kw.arg] = val
                        elif val.startswith("list(") and " or []" in val:
                            post_defaults[kw.arg] = "[]"

    return choices, post_defaults, extra_defaults


def _emit_add_argument(
    target_var: str,
    p: ParamSpec,
    version_expr: str | None,
    all_flag_strings: set[str],
    enum_members: dict[str, dict[str, str]],
    indent: str,
) -> list[str]:
    lines: list[str] = []
    # Resolve enum default placeholder if any
    default_expr = p.default_expr
    if default_expr is not None and default_expr.startswith("__ENUM__"):
        ename, mname = default_expr.removeprefix("__ENUM__").split("__", 1)
        default_expr = repr(enum_members[ename][mname])

    if p.post_default_expr is not None and default_expr == "None":
        default_expr = p.post_default_expr

    # Handle --version callback
    if p.is_version:
        flag_args = ", ".join(repr(f) for f in p.flags)
        kw = ['action="version"', f"version={version_expr or repr('unknown')}"]
        if p.help_expr:
            kw.append(f"help={p.help_expr}")
        lines.append(f"{indent}{target_var}.add_argument({flag_args}, {', '.join(kw)})")
        return lines

    # Handle positional Argument
    if not p.is_option:
        kw: list[str] = []
        if p.is_list:
            kw.append('nargs="+"' if (p.required and p.post_default_expr is None) else 'nargs="*"')
        elif not p.required:
            kw.append('nargs="?"')
            if default_expr is not None:
                kw.append(f"default={default_expr}")
        if p.parser_expr:
            kw.append(f"type={p.parser_expr}")
        elif p.base_type in ("int", "float", "Path"):
            kw.append(f"type={p.base_type}")
        if p.choices_expr:
            kw.append(f"choices={p.choices_expr}")
        if p.metavar_expr:
            kw.append(f"metavar={p.metavar_expr}")
        if p.help_expr:
            kw.append(f"help={p.help_expr}")
        kw_str = (", " + ", ".join(kw)) if kw else ""
        lines.append(f"{indent}{target_var}.add_argument({p.name!r}{kw_str})")
        return lines

    # Handle Option
    # Check for --pos/--neg slash syntax
    slash_flags = [f for f in p.flags if "/" in f]
    non_slash_flags = [f for f in p.flags if "/" not in f]
    if slash_flags:
        pos_flag, neg_flag = slash_flags[0].split("/", 1)
        std_neg = "--no-" + pos_flag.lstrip("-")
        if neg_flag == std_neg:
            flags_out = [*non_slash_flags, pos_flag]
            flag_args = ", ".join(repr(f) for f in flags_out)
            kw = ["action=argparse.BooleanOptionalAction"]
            if default_expr is not None:
                kw.append(f"default={default_expr}")
            if _needs_dest(flags_out, p.name):
                kw.append(f"dest={p.name!r}")
            if p.help_expr:
                kw.append(f"help={p.help_expr}")
            lines.append(f"{indent}{target_var}.add_argument({flag_args}, {', '.join(kw)})")
            return lines
        # Asymmetric pair like --animate/--still or --enable-scripts/--no-scripts
        pos_flags = [*non_slash_flags, pos_flag]
        pos_args = ", ".join(repr(f) for f in pos_flags)
        kw_pos = [f"dest={p.name!r}", 'action="store_true"']
        if default_expr is not None:
            kw_pos.append(f"default={default_expr}")
        if p.help_expr:
            kw_pos.append(f"help={p.help_expr}")
        lines.append(f"{indent}{target_var}.add_argument({pos_args}, {', '.join(kw_pos)})")
        lines.append(f'{indent}{target_var}.add_argument({neg_flag!r}, dest={p.name!r}, action="store_false")')
        return lines

    flag_args = ", ".join(repr(f) for f in p.flags)
    kw = []
    if _needs_dest(p.flags, p.name):
        kw.append(f"dest={p.name!r}")

    if p.count:
        kw.append('action="count"')
        kw.append(f"default={default_expr or '0'}")
    elif p.base_type == "bool" and not p.is_list:
        if default_expr == "True":
            if any(f.startswith("--no-") for f in p.flags):
                kw.append('action="store_false"')
                kw.append("default=True")
            else:
                long_flags = [f for f in p.flags if f.startswith("--")]
                neg_candidate = ("--no-" + long_flags[0][2:]) if long_flags else None
                if neg_candidate and neg_candidate not in all_flag_strings:
                    kw.append("action=argparse.BooleanOptionalAction")
                    kw.append("default=True")
                else:
                    kw.append('action="store_true"')
                    kw.append("default=True")
        elif default_expr == "None":
            kw.append('action="store_true"')
            kw.append("default=None")
        else:
            kw.append('action="store_true"')
            kw.append("default=False")
    elif p.tuple_arity is not None:
        kw.append(f"nargs={p.tuple_arity}")
        if p.parser_expr:
            kw.append(f"type={p.parser_expr}")
        elif p.base_type in ("int", "float", "Path"):
            kw.append(f"type={p.base_type}")
        if default_expr is not None:
            kw.append(f"default={default_expr}")
        elif p.required:
            kw.append("required=True")
    elif p.is_list:
        kw.append('action="append"')
        if p.parser_expr:
            kw.append(f"type={p.parser_expr}")
        elif p.base_type in ("int", "float", "Path"):
            kw.append(f"type={p.base_type}")
        if p.choices_expr:
            kw.append(f"choices={p.choices_expr}")
        if default_expr is not None:
            kw.append(f"default={default_expr}")
        elif p.required:
            kw.append("required=True")
    else:
        if p.parser_expr:
            kw.append(f"type={p.parser_expr}")
        elif p.base_type in ("int", "float", "Path"):
            kw.append(f"type={p.base_type}")
        if p.choices_expr:
            kw.append(f"choices={p.choices_expr}")
        if p.required:
            kw.append("required=True")
        elif default_expr is not None:
            kw.append(f"default={default_expr}")

    if p.metavar_expr:
        kw.append(f"metavar={p.metavar_expr}")
    if p.hidden:
        kw.append("help=argparse.SUPPRESS")
    elif p.help_expr:
        kw.append(f"help={p.help_expr}")

    lines.append(f"{indent}{target_var}.add_argument({flag_args}, {', '.join(kw)})")
    return lines


def _needs_dest(flags: list[str], param_name: str) -> bool:
    longs = [f for f in flags if f.startswith("--")]
    if longs:
        inferred = longs[0][2:].replace("-", "_")
    else:
        inferred = flags[0].lstrip("-").replace("-", "_")
    return inferred != param_name


def _find_cli_only_enums(
    tree: ast.Module, cli_fn_nodes: set[int]
) -> tuple[dict[str, list[str]], dict[str, dict[str, str]], set[str]]:
    enum_values: dict[str, list[str]] = {}
    enum_members: dict[str, dict[str, str]] = {}
    enum_nodes: dict[str, ast.ClassDef] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any("Enum" in ast.unparse(b) for b in node.bases):
            vals: list[str] = []
            members: dict[str, str] = {}
            ok = True
            for st in node.body:
                if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                    if isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
                        members[st.targets[0].id] = st.value.value
                        vals.append(st.value.value)
                    else:
                        ok = False
                elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
                    continue
                else:
                    ok = False
            if ok and vals:
                enum_values[node.name] = vals
                enum_members[node.name] = members
                enum_nodes[node.name] = node

    # Determine which enum classes are ONLY referenced inside cli_fn_nodes
    cli_only: set[str] = set()
    for ename, enode in enum_nodes.items():
        used_outside = False
        for node in tree.body:
            if node is enode or id(node) in cli_fn_nodes:
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id == ename:
                    used_outside = True
                    break
            if used_outside:
                break
        if not used_outside:
            cli_only.add(ename)
    return enum_values, enum_members, cli_only


def migrate_file(path: Path, *, dry_run: bool = False) -> bool:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src, filename=str(path))

    if not any(isinstance(n, ast.ClassDef) and n.name == "_ParserShim" for n in tree.body):
        return False

    # Identify Typer boilerplate nodes at module top level
    remove_ranges: list[tuple[int, int]] = []
    cli_fn_nodes: set[int] = set()
    commands: list[CommandSpec] = []
    callback_cmd: CommandSpec | None = None
    version_expr: str | None = None
    app_help_expr: str | None = None
    run_body_lines: list[str] = ["raise SystemExit(main())"]

    # First pass: collect cli_fn_nodes and _version_callback
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if any(".command" in ast.unparse(d) or ".callback" in ast.unparse(d) for d in node.decorator_list):
                cli_fn_nodes.add(id(node))
            elif node.name == "_version_callback":
                version_expr = _extract_version_expr(node)

    enum_values, enum_members, cli_only_enums = _find_cli_only_enums(tree, cli_fn_nodes)

    # Second pass: inspect top-level nodes
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
            if names in (["typer"], ["click"]):
                remove_ranges.append((node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module in ("typer", "click"):
            remove_ranges.append((node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_STATE":
            remove_ranges.append((node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            tname = node.targets[0].id
            if tname == "_STATE":
                remove_ranges.append((node.lineno, node.end_lineno or node.lineno))
            elif tname == "app" and isinstance(node.value, ast.Call) and "typer.Typer" in ast.unparse(node.value.func):
                for kw in node.value.keywords:
                    if kw.arg == "help":
                        app_help_expr = ast.unparse(kw.value)
                remove_ranges.append((node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.ClassDef) and node.name in ("_Args", "_ParserShim"):
            remove_ranges.append((node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.ClassDef) and node.name in cli_only_enums:
            remove_ranges.append((node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.FunctionDef):
            if node.name == "_version_callback":
                remove_ranges.append((node.lineno, node.end_lineno or node.lineno))
            elif node.name in ("_run", "_run2") and len(node.args.args) == 0:
                start = node.lineno
                end = node.end_lineno or node.lineno
                # Capture indented body
                body_src = "".join(lines[node.body[0].lineno - 1 : end])
                dedented = [ln[4:] if ln.startswith("    ") else ln for ln in body_src.splitlines()]
                run_body_lines = [ln for ln in dedented if ln.strip()]
                remove_ranges.append((start, end))
            elif id(node) in cli_fn_nodes:
                start = min([d.lineno for d in node.decorator_list] + [node.lineno])
                end = node.end_lineno or node.lineno
                remove_ranges.append((start, end))

                dec = node.decorator_list[0]
                is_cb = ".callback" in ast.unparse(dec)
                cmd_name: str | None = None
                cmd_help: str | None = None
                if isinstance(dec, ast.Call):
                    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                        cmd_name = dec.args[0].value
                    for kw in dec.keywords:
                        if kw.arg == "help":
                            cmd_help = ast.unparse(kw.value)

                choices_hints, post_defaults, extra_defaults = _extract_body_hints(node)
                all_args = node.args.args + node.args.kwonlyargs
                pos_defs: list[ast.expr | None] = [None] * (len(node.args.args) - len(node.args.defaults)) + list(
                    node.args.defaults
                )
                all_defs = pos_defs + list(node.args.kw_defaults)

                params: list[ParamSpec] = []
                for a, d in zip(all_args, all_defs):
                    ps = _extract_typer_call(a, d, enum_values)
                    if ps.name in choices_hints and ps.choices_expr is None:
                        ps.choices_expr = choices_hints[ps.name]
                    if ps.name in post_defaults:
                        ps.post_default_expr = post_defaults[ps.name]
                    params.append(ps)

                spec = CommandSpec(
                    fn_name=node.name,
                    cmd_name=cmd_name,
                    help_expr=cmd_help,
                    params=params,
                    extra_defaults=extra_defaults,
                    is_callback=is_cb,
                )
                if is_cb:
                    callback_cmd = spec
                else:
                    commands.append(spec)
        elif isinstance(node, ast.If):
            u = ast.unparse(node.test)
            if u == "__name__ == '__main__'" and len(node.body) == 1 and ast.unparse(node.body[0]) == "app()":
                remove_ranges.append((node.lineno, node.end_lineno or node.lineno))

    # Build the real parser construction body lines (indented by 4 spaces)
    parser_body: list[str] = []
    desc_kw = f"description={app_help_expr}" if app_help_expr else ""
    parser_body.append(f"    parser = argparse.ArgumentParser({desc_kw})")

    is_multi = len(commands) > 1 or (len(commands) == 1 and commands[0].cmd_name is not None)

    if callback_cmd is not None:
        all_flags = {f for p in callback_cmd.params for f in p.flags}
        for p in callback_cmd.params:
            parser_body.extend(_emit_add_argument("parser", p, version_expr, all_flags, enum_members, "    "))

    if not is_multi and len(commands) == 1:
        cmd = commands[0]
        if cmd.help_expr and not app_help_expr:
            parser_body[0] = f"    parser = argparse.ArgumentParser(description={cmd.help_expr})"
        all_flags = {f for p in cmd.params for f in p.flags}
        for p in cmd.params:
            parser_body.extend(_emit_add_argument("parser", p, version_expr, all_flags, enum_members, "    "))
        if cmd.extra_defaults:
            defs = ", ".join(f"{k}={v}" for k, v in cmd.extra_defaults.items())
            parser_body.append(f"    parser.set_defaults({defs})")
    elif is_multi:
        # Determine subparsers dest
        sub_dest = "command"
        for cmd in commands:
            for k, v in cmd.extra_defaults.items():
                if cmd.cmd_name and v == repr(cmd.cmd_name):
                    sub_dest = k
                    break
        parser_body.append(f"    subparsers = parser.add_subparsers(dest={sub_dest!r})")
        for idx, cmd in enumerate(commands):
            cname = cmd.cmd_name or cmd.fn_name
            sub_var = f"sub_{idx}"
            help_kw = f", help={cmd.help_expr}" if cmd.help_expr else ""
            parser_body.append(f"    {sub_var} = subparsers.add_parser({cname!r}{help_kw})")
            all_flags = {f for p in cmd.params for f in p.flags}
            for p in cmd.params:
                parser_body.extend(_emit_add_argument(sub_var, p, version_expr, all_flags, enum_members, "    "))
            # Set non-command defaults (like func=... or handler=...)
            non_dest_defaults = {k: v for k, v in cmd.extra_defaults.items() if k != sub_dest}
            if non_dest_defaults:
                defs = ", ".join(f"{k}={v}" for k, v in non_dest_defaults.items())
                parser_body.append(f"    {sub_var}.set_defaults({defs})")

    parser_body.append("    return parser")

    # Find where _ParserShim() is instantiated
    shim_fn: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and id(node) not in cli_fn_nodes:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "_ParserShim":
                    shim_fn = node
                    break
            if shim_fn is not None:
                break

    # Build replacement map keyed by 1-based line spans
    replacements: list[tuple[int, int, str]] = [(s, e, "") for s, e in remove_ranges]

    if shim_fn is not None:
        if shim_fn.name in ("build_parser", "build_arg_parser", "_build_argument_parser", "_build_parser"):
            doc = ast.get_docstring(shim_fn)
            new_fn_lines = [f"def {shim_fn.name}() -> argparse.ArgumentParser:"]
            if doc:
                new_fn_lines.append(f'    """{doc}"""')
            new_fn_lines.extend(parser_body)
            replacements.append((shim_fn.lineno, shim_fn.end_lineno or shim_fn.lineno, "\n".join(new_fn_lines) + "\n"))
        elif shim_fn.name in ("parse_args", "_parse_args"):
            # Build helper _build_parser() + keep parse_args signature calling it
            sig_line = lines[shim_fn.lineno - 1].rstrip("\n")
            sig_line = re.sub(r"->\s*_Args\s*:", "-> argparse.Namespace:", sig_line)
            arg_names = [a.arg for a in shim_fn.args.args]
            call_arg = arg_names[0] if arg_names else ""
            new_block = [
                "def _build_parser() -> argparse.ArgumentParser:",
                *parser_body,
                "",
                "",
                sig_line,
                f"    return _build_parser().parse_args({call_arg})",
            ]
            replacements.append((shim_fn.lineno, shim_fn.end_lineno or shim_fn.lineno, "\n".join(new_block) + "\n"))
        elif shim_fn.name == "main":
            # Insert def _build_parser() right before main(), and replace _ParserShim() inside main()
            insert_block = "\n".join(["def _build_parser() -> argparse.ArgumentParser:", *parser_body, "", ""]) + "\n"
            main_src = "".join(lines[shim_fn.lineno - 1 : shim_fn.end_lineno])
            main_src = main_src.replace("_ParserShim()", "_build_parser()")
            replacements.append((shim_fn.lineno, shim_fn.end_lineno or shim_fn.lineno, insert_block + main_src))
        else:
            # e.g. make_parser in sortplan.py — replace _ParserShim() with generated parser
            doc = ast.get_docstring(shim_fn)
            new_fn_lines = [f"def {shim_fn.name}() -> argparse.ArgumentParser:"]
            if doc:
                new_fn_lines.append(f'    """{doc}"""')
            new_fn_lines.extend(parser_body)
            replacements.append((shim_fn.lineno, shim_fn.end_lineno or shim_fn.lineno, "\n".join(new_fn_lines) + "\n"))

    # Apply non-overlapping line replacements from bottom to top
    replacements.sort(key=lambda r: r[0], reverse=True)
    out_lines = list(lines)
    for start, end, text in replacements:
        out_lines[start - 1 : end] = [text] if text else []

    result = "".join(out_lines)

    # Ensure top-level `import argparse` exists (not only under `if TYPE_CHECKING:`)
    if not re.search(r"^import\s+argparse\b", result, flags=re.MULTILINE):
        # Insert after `from __future__ import ...` or at top of imports
        m = re.search(r"^(from __future__ import .+\n)", result, flags=re.MULTILINE)
        if m:
            pos = m.end()
            result = result[:pos] + "\nimport argparse\n" + result[pos:]
        else:
            result = "import argparse\n" + result

    # Append clean if __name__ == "__main__": block
    main_guard = '\n\nif __name__ == "__main__":\n' + "\n".join(f"    {ln}" for ln in run_body_lines) + "\n"
    result = result.rstrip() + main_guard

    # Collapse excessive blank lines (3+ -> 2)
    result = re.sub(r"\n{4,}", "\n\n\n", result)

    # Verify syntax before writing
    ast.parse(result, filename=str(path))
    if not dry_run:
        path.write_text(result, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    count = 0
    for f in args.files:
        if migrate_file(f, dry_run=args.dry_run):
            print(f"Migrated {f}")
            count += 1
    print(f"Done: {count} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
