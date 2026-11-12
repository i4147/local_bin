#!/data/data/com.termux/files/usr/bin/env python
"""argparse2typer - convert an argparse based script into a Typer based one.

Usage:
    python argparse2typer.py path/to/script.py

Writes the result next to the input as ``path/to/script_typer.py``.
Requires Python 3.12+ (Linux). The *generated* script needs ``typer`` (>= 0.12).

How it works
------------
The input is parsed with ``ast``; every ``add_argument`` call is translated to a
``typer.Option`` / ``typer.Argument`` parameter and the edits are spliced into the
original source text, so comments and formatting of the untouched code survive.

Two output shapes are produced:

* inline (preferred) - ``args = parser.parse_args()`` is removed, the enclosing
  function (or ``__main__`` block / module tail) becomes the Typer command and
  ``args.foo`` is rewritten to ``foo``.  If ``args`` is used in any other way
  (passed around, ``vars(args)`` ...) an ``args = SimpleNamespace(...)`` line is
  generated instead, so the rest of the code keeps working unchanged.
* bridge (fallback, also used for sub-parsers) - the parser object is replaced by a
  tiny shim whose ``parse_args()`` returns what Typer parsed; the old entry point is
  called from the generated Typer command(s).

Anything that cannot be converted faithfully is reported on stderr.
"""

from __future__ import annotations

import ast
import io
import json
import keyword
import re
import shutil
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

INDENT = "    "
MISSING = object()
FILE_TYPES = {
    "r": "typer.FileText",
    "w": "typer.FileTextWrite",
    "rb": "typer.FileBinaryRead",
    "wb": "typer.FileBinaryWrite",
}
IMPORT_SPECS = {  # name -> (group, import line)
    "os": ("std", "import os"),
    "sys": ("std", "import sys"),
    "Annotated": ("std", "from typing import Annotated"),
    "SimpleNamespace": ("std", "from types import SimpleNamespace"),
    "StrEnum": ("std", "from enum import StrEnum"),
    "click": ("3rd", "import click"),
    "typer": ("3rd", "import typer"),
}
PARSER_METHODS = {
    "add_argument",
    "add_subparsers",
    "add_parser",
    "add_argument_group",
    "add_mutually_exclusive_group",
    "set_defaults",
}
UNSUPPORTED_PARSE = {"parse_known_args", "parse_intermixed_args", "parse_known_intermixed_args"}

BRIDGE_DEFS = '''\
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
        return _Args(**_STATE)

    def error(self, message):
        raise typer.BadParameter(message)

    def print_help(self, file=None):
        typer.echo(click.get_current_context().get_help(), file=file)

    def print_usage(self, file=None):
        typer.echo(click.get_current_context().get_usage(), file=file)
'''


class ConversionError(Exception):
    pass


# --------------------------------------------------------------------------- helpers
def literal(node):
    if node is None:
        return MISSING
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return MISSING


def dotted(node) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def is_parser_ctor(node) -> bool:
    return isinstance(node, ast.Call) and dotted(node.func) in {"argparse.ArgumentParser", "ArgumentParser"}


def is_parse_call(node, var: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "parse_args"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == var
    )


def is_main_guard(node) -> bool:
    if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and not node.orelse):
        return False
    t = node.test
    if len(t.ops) != 1 or not isinstance(t.ops[0], ast.Eq):
        return False
    sides = [t.left, *t.comparators]
    return any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides) and any(
        isinstance(s, ast.Constant) and s.value == "__main__" for s in sides
    )


def py_name(name: str) -> str:
    n = re.sub(r"\W", "_", name) or "_"
    if n[0].isdigit():
        n = "_" + n
    if keyword.iskeyword(n):
        n += "_"
    return n


def q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def kwargs_src(items: list[tuple[str, str]]) -> str:
    out = []
    for key, value in items:
        if key.isidentifier() and not keyword.iskeyword(key):
            out.append(f"{key}={value}")
        else:
            out.append(f"**{{{q(key)}: {value}}}")
    return ", ".join(out)


def walk_scope(node):
    """Like ast.walk but does not descend into nested functions/classes/lambdas."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(n))


def identifiers_in(stmts) -> set[str]:
    names: set[str] = set()
    for st in stmts:
        for n in ast.walk(st):
            if isinstance(n, ast.Name):
                names.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            elif isinstance(n, ast.arg):
                names.add(n.arg)
            elif isinstance(n, ast.alias):
                names.add((n.asname or n.name).split(".")[0])
            elif isinstance(n, ast.ExceptHandler) and n.name:
                names.add(n.name)
    return names


def string_lines(text: str) -> set[int]:
    """1-based line numbers that begin inside a multi-line string literal."""
    inside: set[int] = set()
    fstack: list[int] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
            inside.update(range(tok.start[0] + 1, tok.end[0] + 1))
        elif tok.type == tokenize.FSTRING_START:
            fstack.append(tok.start[0])
        elif tok.type == tokenize.FSTRING_END:
            start = fstack.pop()
            if tok.end[0] > start:
                inside.update(range(start + 1, tok.end[0] + 1))
    return inside


# --------------------------------------------------------------------------- source edits
class Source:
    """Byte-offset based text splicing driven by AST positions."""

    def __init__(self, text: str):
        self.data = text.encode()
        self.line_starts = [0] + [m.end() for m in re.finditer(rb"\n", self.data)]
        self.edits: list[tuple[int, int, str]] = []

    def pos(self, line: int, col: int) -> int:
        return self.line_starts[line - 1] + col

    def start(self, node) -> int:
        return self.pos(node.lineno, node.col_offset)

    def end(self, node) -> int:
        return self.pos(node.end_lineno, node.end_col_offset)

    def seg(self, node) -> str:
        return self.data[self.start(node) : self.end(node)].decode()

    def line_start(self, lineno: int) -> int:
        return self.line_starts[lineno - 1]

    def line_end(self, lineno: int) -> int:
        return self.line_starts[lineno] if lineno < len(self.line_starts) else len(self.data)

    def replace(self, start: int, end: int, new: str) -> None:
        self.edits.append((start, end, new))

    def delete_stmt(self, node) -> None:
        self.replace(self.line_start(node.lineno), self.line_end(node.end_lineno), "")

    def after_line(self, lineno: int, text: str) -> None:
        """Insert text after a line, making sure the line is newline-terminated."""
        pos = self.line_end(lineno)
        if pos > 0 and self.data[pos - 1 : pos] != b"\n":
            text = "\n" + text
        self.replace(pos, pos, text)

    def apply(self) -> str:
        out, cur = [], 0
        for start, end, new in sorted(self.edits, key=lambda e: (e[0], e[1])):
            if start < cur:
                raise ConversionError("internal error: overlapping edits")
            out.append(self.data[cur:start])
            out.append(new.encode())
            cur = end
        out.append(self.data[cur:])
        return b"".join(out).decode()


class Ctx:
    def __init__(self, src: Source, tree: ast.Module):
        self.src = src
        self.warnings: list[str] = []
        self.needed: set[str] = {"typer", "Annotated"}
        self.extra_defs: list[str] = []
        self.taken = identifiers_in([tree])
        self.app = self.unique("app")

    def warn(self, node, msg: str) -> None:
        self.warnings.append(f"line {getattr(node, 'lineno', '?')}: {msg}")

    def need(self, *names: str) -> None:
        self.needed.update(names)

    def unique(self, base: str) -> str:
        name, i = base, 2
        while name in self.taken:
            name = f"{base}{i}"
            i += 1
        self.taken.add(name)
        return name


# --------------------------------------------------------------------------- model
@dataclass
class Param:
    name: str
    dest: str
    positional: bool
    ann: str
    default: str | None = None  # source text; None => required (no Python default)
    flags: list[str] = field(default_factory=list)
    kw: dict[str, str] = field(default_factory=dict)
    prologue: list[str] = field(default_factory=list)
    flag_value: bool | None = None  # True/False for store_true/store_false
    internal: bool = False  # not part of the argparse namespace (version)


@dataclass
class Cmd:
    name: str | None = None
    help: str | None = None
    epilog: str | None = None
    params: list[Param] = field(default_factory=list)
    defaults: dict[str, str] = field(default_factory=dict)
    exclusive: list[tuple[list[Param], bool]] = field(default_factory=list)
    has_subparsers: bool = False
    node: ast.AST | None = None


@dataclass
class Ref:
    kind: str  # parser | group | subparsers
    cmd: Cmd
    panel: str | None = None
    excl: list[Param] | None = None


@dataclass
class Loc:
    owner: ast.AST
    body: list[ast.stmt]
    creation: ast.Assign


@dataclass
class Analysis:
    root: Cmd
    subs: list[Cmd]
    sub_dest: str | None
    creation: ast.Assign
    parser_var: str
    table: dict[str, Ref]
    handled: list[ast.stmt]


# --------------------------------------------------------------------------- add_argument
def convert_type(node, ctx: Ctx) -> tuple[str, str | None]:
    d = dotted(node)
    if d in {"int", "float", "str", "bool", "Path", "pathlib.Path"}:
        return d, None
    if isinstance(node, ast.Call) and dotted(node.func) in {"argparse.FileType", "FileType"}:
        mode = literal(node.args[0]) if node.args else "r"
        if mode not in FILE_TYPES:
            ctx.warn(node, f"FileType mode {mode!r} mapped to typer.FileText - check it")
            mode = "r"
        return FILE_TYPES[mode], None
    ctx.warn(node, f"custom type {ctx.src.seg(node)!r} passed as parser= - please verify")
    return "str", ctx.src.seg(node)


def make_enum(dest: str, choices: list[str], ctx: Ctx) -> tuple[str, dict[str, str]]:
    cls = ctx.unique("".join(w.capitalize() for w in re.split(r"\W+", dest) if w) or "Choice")
    members: dict[str, str] = {}
    for c in choices:
        m = re.sub(r"\W", "_", c) or "empty"
        if m[0].isdigit() or m.startswith("_") or keyword.iskeyword(m) or m in {"name", "value", "mro"}:
            m = "v_" + m.lstrip("_")
        while m in members.values():
            m += "_"
        members[c] = m
    body = "".join(f"{INDENT}{m} = {q(c)}\n" for c, m in members.items())
    ctx.extra_defs.append(f"class {cls}(StrEnum):\n{body}")
    ctx.need("StrEnum")
    return cls, members


def help_expr(node, default_val, ctx: Ctx) -> tuple[str | None, bool]:
    """Returns (source expr, hidden)."""
    if node is None:
        return None, False
    if dotted(node) == "argparse.SUPPRESS":
        return None, True
    text = literal(node)
    if not isinstance(text, str):
        return ctx.src.seg(node), False
    if "%(default)" in text:
        if default_val is MISSING:
            ctx.warn(node, "help uses %(default)s with a non-literal default - left as is")
        else:
            text = re.sub(r"%\(default\)[sdr]", str(default_val), text)
    if re.search(r"%\(\w+\)", text):
        ctx.warn(node, "help contains argparse %(...)s placeholders that Typer does not expand")
    return q(text.replace("%%", "%")), False


def convert_add_argument(call: ast.Call, ctx: Ctx, panel: str | None) -> Param | None:
    src = ctx.src
    if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
        ctx.warn(call, "add_argument() with */** unpacking - skipped")
        return None
    names = [literal(a) for a in call.args]
    if not names or not all(isinstance(n, str) for n in names):
        ctx.warn(call, "add_argument() with non-literal names - skipped")
        return None
    kws = {k.arg: k.value for k in call.keywords}

    def lit(key, default=None):
        value = literal(kws.get(key))
        return default if value is MISSING else value

    action = "store"
    if "action" in kws:
        action = literal(kws["action"])
        if not isinstance(action, str):
            ctx.warn(call, "custom Action class - skipped")
            return None

    positional = not names[0].startswith("-")
    if positional:
        dest = names[0]
    else:
        dest = lit("dest")
        if not isinstance(dest, str):
            longs = [n for n in names if n.startswith("--")]
            dest = (longs[0] if longs else names[0]).lstrip("-").replace("-", "_")
    name = py_name(dest)
    if name != dest:
        ctx.warn(call, f"'{dest}' is not a usable parameter name - using '{name}'")

    elem, parser_kw = "str", None
    if "type" in kws:
        elem, parser_kw = convert_type(kws["type"], ctx)
    default_node = kws.get("default")
    default_src = src.seg(default_node) if default_node is not None else None
    default_val = literal(default_node)

    enum_used = False
    choices_node = kws.get("choices")
    if choices_node is not None:
        choices = literal(choices_node)
        if (
            isinstance(choices, (list, tuple, set, frozenset))
            and choices
            and all(isinstance(c, str) for c in choices)
            and elem == "str"
            and parser_kw is None
        ):
            elem, members = make_enum(name, list(choices), ctx)
            enum_used = True
            if isinstance(default_val, str) and default_val in members:
                default_src = f"{elem}.{members[default_val]}"

    p = Param(name=name, dest=dest, positional=positional, ann=elem)
    if not positional:
        p.flags = list(names)
    nargs = lit("nargs")
    required = bool(lit("required", False))

    if action in {"store_true", "store_false"}:
        on = action == "store_true"
        p.ann, p.flag_value = "bool", on
        p.default = default_src or ("False" if on else "True")
    elif action == "count":
        p.ann, p.default = "int", default_src or "0"
        p.kw["count"] = "True"
    elif action == "store_const":
        const = src.seg(kws["const"]) if "const" in kws else "None"
        p.ann, p.default = "bool", "False"
        p.prologue.append(f"{name} = {const} if {name} else {default_src or 'None'}")
    elif action == "version":
        text = literal(kws.get("version"))
        if isinstance(text, str) and "%(prog)s" in text:
            ctx.need("os", "sys")
            expr = f'{text!r}.replace("%(prog)s", os.path.basename(sys.argv[0]))'
        else:
            expr = src.seg(kws["version"]) if "version" in kws else '""'
        cb = ctx.unique("_version_callback")
        ctx.extra_defs.append(
            f"def {cb}(value: bool) -> None:\n    if value:\n        typer.echo({expr})\n        raise typer.Exit()\n"
        )
        p.ann, p.default, p.internal = "bool", "False", True
        p.kw["callback"], p.kw["is_eager"] = cb, "True"
    elif action in {"store", "append", "extend"}:
        multi = action != "store"
        arity = nargs if isinstance(nargs, int) and not isinstance(nargs, bool) and nargs > 1 else None
        if arity:
            if multi:
                ctx.warn(call, "append/extend with a fixed nargs is not supported - check manually")
            p.ann = f"tuple[{', '.join([elem] * arity)}]"
        if positional:
            if nargs in ("*", "+"):
                p.ann = f"list[{elem}]"
            if nargs == "?":
                p.default = default_src or "None"
            elif nargs == "*":
                p.default = default_src or "None"
                if default_src is None:
                    p.prologue.append(f"{name} = {name} or []")
        else:
            if multi:
                p.ann = f"list[{elem}]"
                if action == "extend":
                    ctx.warn(call, "action='extend' mapped to a repeatable option - check nargs handling")
            elif nargs in ("*", "+"):
                p.ann = f"list[{elem}]"
                ctx.warn(call, f"nargs={nargs!r} on an option becomes a repeatable option ('--x a --x b')")
            elif nargs == "?":
                ctx.warn(call, "nargs='?' on an option is not supported by Typer - plain option generated")
            p.default = None if required else (default_src or "None")
            if multi:
                p.default = default_src or "None"
        if choices_node is not None and not enum_used:
            cs = src.seg(choices_node)
            if p.ann.startswith(("list", "tuple")):
                ctx.warn(call, "choices on a multi-value argument are not enforced")
            else:
                p.prologue += [
                    f"if {name} is not None and {name} not in {cs}:",
                    f'{INDENT}raise typer.BadParameter(f"invalid choice: {{{name}!r}} (choose from {{{cs}}})")',
                ]
    else:
        ctx.warn(call, f"action={action!r} is not supported - skipped")
        return None

    # keyword arguments for typer.Option / typer.Argument
    help_src, hidden = help_expr(kws.get("help"), default_val, ctx)
    if help_src:
        p.kw["help"] = help_src
    elif action == "version":
        p.kw["help"] = q("show program's version number and exit")
    if hidden:
        p.kw["hidden"] = "True"
    metavar = lit("metavar")
    if isinstance(metavar, str):
        p.kw["metavar"] = q(metavar)
    if parser_kw:
        p.kw["parser"] = parser_kw
    if panel:
        p.kw["rich_help_panel"] = q(panel)
    return p


def dedupe(cmd: Cmd, ctx: Ctx) -> None:
    seen: dict[str, Param] = {}
    result = []
    for p in cmd.params:
        first = seen.get(p.name)
        if first is None:
            seen[p.name] = p
            result.append(p)
            continue
        first_long = next((f for f in first.flags if f.startswith("--") and "/" not in f), None)
        if (
            first.flag_value is not None
            and p.flag_value is not None
            and first.flag_value != p.flag_value
            and first_long
            and len(p.flags) == 1
            and p.flags[0].startswith("--")
        ):
            on, off = (first_long, p.flags[0]) if first.flag_value else (p.flags[0], first_long)
            first.flags = [f"{on}/{off}"] + [f for f in first.flags if f != first_long]
            first.flag_value = None
            continue
        ctx.warn(cmd.node, f"duplicate dest '{p.dest}' - second definition renamed to '{p.name}_2'")
        p.name = f"{p.name}_2"
        seen[p.name] = p
        result.append(p)
    cmd.params = result


# --------------------------------------------------------------------------- analysis
def str_literal(node) -> str | None:
    v = literal(node)
    return v if isinstance(v, str) else None


def locate_parser(tree: ast.Module) -> Loc:
    total = sum(1 for n in ast.walk(tree) if is_parser_ctor(n))
    if total == 0:
        raise ConversionError("no argparse.ArgumentParser(...) found - nothing to convert")
    if total > 1:
        raise ConversionError("more than one ArgumentParser is not supported")
    owners: list[ast.AST] = [tree]
    owners += [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    owners += [n for n in tree.body if is_main_guard(n)]
    for owner in owners:
        for st in owner.body:
            if (
                isinstance(st, ast.Assign)
                and is_parser_ctor(st.value)
                and len(st.targets) == 1
                and isinstance(st.targets[0], ast.Name)
            ):
                return Loc(owner, owner.body, st)
    raise ConversionError(
        "ArgumentParser(...) must be assigned to a plain variable at the top level of a function, "
        "the __main__ block or the module"
    )


def analyze(loc: Loc, ctx: Ctx) -> Analysis:
    creation = loc.creation
    kw = {k.arg: k.value for k in creation.value.keywords if k.arg}
    root = Cmd(help=str_literal(kw.get("description")), epilog=str_literal(kw.get("epilog")), node=creation)
    var = creation.targets[0].id
    table = {var: Ref("parser", root)}
    an = Analysis(root, [], None, creation, var, table, [])
    started = False
    for st in loc.body:
        if st is creation:
            started = True
            continue
        if not started:
            continue
        if (
            isinstance(st, ast.Assign)
            and len(st.targets) == 1
            and isinstance(st.targets[0], ast.Name)
            and isinstance(st.value, ast.Call)
        ):
            call, f, tgt = st.value, st.value.func, st.targets[0].id
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in table:
                ref = table[f.value.id]
                if f.attr in {"add_argument_group", "add_mutually_exclusive_group"} and ref.kind in {"parser", "group"}:
                    title = (
                        str_literal(call.args[0])
                        if call.args
                        else str_literal(next((k.value for k in call.keywords if k.arg == "title"), None))
                    )
                    excl = f.attr == "add_mutually_exclusive_group"
                    group = Ref(
                        "group", ref.cmd, panel=None if excl else (title or ref.panel), excl=[] if excl else None
                    )
                    if excl:
                        required = literal(next((k.value for k in call.keywords if k.arg == "required"), None)) is True
                        ref.cmd.exclusive.append((group.excl, required))
                    table[tgt] = group
                    an.handled.append(st)
                    continue
                if f.attr == "add_subparsers" and ref.kind == "parser":
                    if ref.cmd is not root:
                        raise ConversionError("nested sub-parsers are not supported")
                    kws = {k.arg: k.value for k in call.keywords if k.arg}
                    an.sub_dest = str_literal(kws.get("dest"))
                    root.has_subparsers = True
                    table[tgt] = Ref("subparsers", root)
                    an.handled.append(st)
                    continue
                if f.attr == "add_parser" and ref.kind == "subparsers":
                    kws = {k.arg: k.value for k in call.keywords if k.arg}
                    name = str_literal(call.args[0]) if call.args else None
                    if name is None:
                        raise ConversionError(f"line {st.lineno}: add_parser() needs a literal name")
                    sub = Cmd(
                        name=name,
                        node=st,
                        help=str_literal(kws.get("help")) or str_literal(kws.get("description")),
                        epilog=str_literal(kws.get("epilog")),
                    )
                    if "aliases" in kws:
                        ctx.warn(st, f"aliases of sub-command '{name}' are not converted")
                    an.subs.append(sub)
                    table[tgt] = Ref("parser", sub)
                    an.handled.append(st)
                    continue
        elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
            call, f = st.value, st.value.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in table:
                ref = table[f.value.id]
                if f.attr == "add_argument" and ref.kind in {"parser", "group"}:
                    p = convert_add_argument(call, ctx, ref.panel)
                    if p:
                        ref.cmd.params.append(p)
                        if ref.excl is not None:
                            ref.excl.append(p)
                    an.handled.append(st)
                    continue
                if f.attr == "set_defaults" and ref.kind == "parser":
                    for k in call.keywords:
                        if k.arg:
                            ref.cmd.defaults[k.arg] = ctx.src.seg(k.value)
                    an.handled.append(st)
                    continue
    return an


# --------------------------------------------------------------------------- rendering
def ordered(params: list[Param]) -> list[Param]:
    return [p for p in params if p.default is None] + [p for p in params if p.default is not None]


def render_param(p: Param) -> str:
    ann = f"{p.ann} | None" if p.default == "None" else p.ann
    args = [] if p.positional else [q(f) for f in p.flags]
    args += [f"{k}={v}" for k, v in p.kw.items()]
    kind = "typer.Argument" if p.positional else "typer.Option"
    text = f"{p.name}: Annotated[{ann}, {kind}({', '.join(args)})]"
    return text + (f" = {p.default}" if p.default is not None else "")


def render_sig(params: list[Param], ret: str = "") -> str:
    if not params:
        return f"(){ret}"
    return "(\n" + "".join(f"{INDENT}{render_param(p)},\n" for p in ordered(params)) + f"){ret}"


def app_def(ctx: Ctx, help_: str | None = None, epilog: str | None = None) -> str:
    parts = []
    if help_:
        parts.append(f"help={q(help_)}")
    if epilog:
        parts.append(f"epilog={q(epilog)}")
    parts += [
        "add_completion=False",
        'context_settings={"help_option_names": ["-h", "--help"]}',
        "pretty_exceptions_show_locals=False",
    ]
    return f"{ctx.app} = typer.Typer(\n" + "".join(f"{INDENT}{p},\n" for p in parts) + ")"


def cmd_kwargs(cmd: Cmd, name: str | None = None) -> str:
    parts = [q(name)] if name else []
    if cmd.help:
        parts.append(f"help={q(cmd.help)}")
    if cmd.epilog:
        parts.append(f"epilog={q(cmd.epilog)}")
    return ", ".join(parts)


def exclusive_checks(cmd: Cmd, ctx: Ctx) -> list[str]:
    out = []
    for group, required in cmd.exclusive:
        if len(group) < 2:
            continue
        if any(p.default not in (None, "None", "False") for p in group):
            ctx.warn(cmd.node, "mutually exclusive group with non-trivial defaults is not enforced")
            continue
        conds = ", ".join(
            f"bool({p.name})" if p.ann == "bool" or "count" in p.kw else f"{p.name} is not None" for p in group
        )
        label = ", ".join(p.flags[0] if p.flags else p.name for p in group)
        out += [f"if sum(({conds},)) > 1:", f"{INDENT}raise typer.BadParameter({q(label + ' are mutually exclusive')})"]
        if required:
            out += [
                f"if not any(({conds},)):",
                f"{INDENT}raise typer.BadParameter({q('one of ' + label + ' is required')})",
            ]
    return out


def cmd_prologue(cmd: Cmd, ctx: Ctx) -> list[str]:
    return exclusive_checks(cmd, ctx) + [line for p in cmd.params for line in p.prologue]


# --------------------------------------------------------------------------- inline mode
@dataclass
class InlinePlan:
    pst: ast.stmt
    var: str | None
    entry: ast.stmt | None = None
    exit_codes: bool = False
    header_end: int = 0
    ret: str = ""
    errors: list[ast.Expr] = field(default_factory=list)


def plan_inline(tree, src: Source, loc: Loc, an: Analysis) -> InlinePlan | str:
    if an.subs or an.root.has_subparsers:
        return "sub-parsers need the bridge layout"
    owner, body = loc.owner, loc.body
    idx = body.index(an.creation)
    pst, var = None, None
    for st in body[idx + 1 :]:
        if (
            isinstance(st, ast.Assign)
            and len(st.targets) == 1
            and isinstance(st.targets[0], ast.Name)
            and is_parse_call(st.value, an.parser_var)
        ):
            pst, var = st, st.targets[0].id
            break
        if isinstance(st, ast.Expr) and is_parse_call(st.value, an.parser_var):
            pst = st
            break
    if pst is None:
        return "parse_args() is not assigned to a variable in the same block as the parser"
    if sum(1 for n in ast.walk(tree) if is_parse_call(n, an.parser_var)) != 1:
        return "parse_args() is called more than once"
    pidx = body.index(pst)
    if any(body.index(h) > pidx for h in an.handled):
        return "parser is configured after parse_args()"

    allowed = {id(n) for st in [*an.handled, an.creation, pst] for n in ast.walk(st)}
    errors = []
    for st in body[pidx + 1 :]:
        for n in ast.walk(st):
            if (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Attribute)
                and n.value.func.attr == "error"
                and isinstance(n.value.func.value, ast.Name)
                and n.value.func.value.id in an.table
                and len(n.value.args) == 1
                and not n.value.keywords
            ):
                errors.append(n)
                allowed.update(id(x) for x in ast.walk(n.value.func))
    for st in body:
        for n in ast.walk(st):
            if isinstance(n, ast.Name) and n.id in an.table and id(n) not in allowed:
                return f"the parser object is used for something else (line {n.lineno})"

    plan = InlinePlan(pst=pst, var=var, errors=errors)
    if isinstance(owner, ast.AsyncFunctionDef):
        return "async entry point"
    if isinstance(owner, ast.FunctionDef):
        if owner.decorator_list:
            return "decorated function"
        a = owner.args
        params = [*a.posonlyargs, *a.args, *a.kwonlyargs]
        if a.vararg or a.kwarg or len(params) > 1:
            return "function takes parameters"
        if params:
            inside = {id(n) for n in ast.walk(pst.value)}
            if any(isinstance(n, ast.Name) and n.id == params[0].arg and id(n) not in inside for n in ast.walk(owner)):
                return "function parameter is used outside parse_args()"
        if owner not in tree.body:
            return "parser lives in a nested function"
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == owner.name
        ]
        refs = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == owner.name]
        if len(calls) != 1 or len(refs) != 1:
            return f"{owner.name}() is not called exactly once"
        cands = []
        for s in tree.body:
            cands.extend(s.body if is_main_guard(s) else [s])
        plan.entry = next((s for s in cands if s is not owner and any(n is calls[0] for n in ast.walk(s))), None)
        if plan.entry is None:
            return f"{owner.name}() is not called from module level / the __main__ block"
        e = plan.entry
        plan.exit_codes = (
            isinstance(e, ast.Expr)
            and isinstance(e.value, ast.Call)
            and dotted(e.value.func) in {"sys.exit", "exit", "quit"}
        ) or (isinstance(e, ast.Raise) and isinstance(e.exc, ast.Call) and dotted(e.exc.func) == "SystemExit")
        m = re.compile(r"(?:async\s+)?def\s+\w+\s*\([^)]*\)\s*(->[^:]*?)?\s*:", re.S).match(
            src.data[src.start(owner) :].decode()
        )
        if not m:
            return "could not parse the function signature"
        plan.header_end = src.start(owner) + len(m.group(0).encode())
        plan.ret = f" {m.group(1).strip()}" if m.group(1) else ""
    elif is_main_guard(owner):
        if owner.body[0].lineno <= owner.lineno:
            return "one-line __main__ block"
    else:  # module level
        tail = body[pidx + 1 :]
        if any(is_main_guard(s) for s in tail):
            return "parse_args() at module level followed by a __main__ block"
        if var and any(isinstance(n, ast.Name) and n.id == var for s in body[:pidx] for n in ast.walk(s)):
            return f"'{var}' is used by code defined before parse_args() (global namespace pattern)"
    return plan


def apply_inline(tree, src: Source, ctx: Ctx, loc: Loc, an: Analysis, plan: InlinePlan) -> None:
    owner, body, pst, var = loc.owner, loc.body, plan.pst, plan.var
    root = an.root
    pidx = body.index(pst)
    tail = body[pidx + 1 :]
    removed = {id(s) for s in an.handled} | {id(an.creation), id(pst)}
    kept = [s for s in body if id(s) not in removed]
    dests = {p.dest: p.name for p in root.params if not p.internal}

    # attribute rewrite or namespace object?
    attrs: list[ast.Attribute] = []
    mode = "none"
    if var:
        mode = "attr"
        ok = set()
        for st in tail:
            for n in ast.walk(st):
                if (
                    isinstance(n, ast.Attribute)
                    and isinstance(n.ctx, ast.Load)
                    and isinstance(n.value, ast.Name)
                    and n.value.id == var
                    and n.attr in dests
                ):
                    attrs.append(n)
                    ok.add(id(n.value))
        if any(isinstance(n, ast.Name) and n.id == var and id(n) not in ok for st in tail for n in ast.walk(st)):
            mode = "ns"
        elif set(dests.values()) & identifiers_in(kept):
            mode = "ns"
    lines = cmd_prologue(root, ctx)
    if mode == "ns":
        ctx.need("SimpleNamespace")
        lines.append(f"{var} = SimpleNamespace({kwargs_src(list(dests.items()))})")
    elif mode == "attr":
        for n in attrs:
            src.replace(src.start(n), src.end(n), dests[n.attr])
    if not kept and not lines:
        lines = ["pass"]

    for st in [*an.handled, an.creation]:
        src.delete_stmt(st)
    for st in plan.errors:
        src.replace(src.start(st), src.start(st.value.args[0]), "raise typer.BadParameter(")

    indent = " " * pst.col_offset
    body_text = "".join(f"{indent}{line}\n" for line in lines)
    sig = render_sig(root.params)
    deco = f"@{ctx.app}.command({cmd_kwargs(root)})"
    head = f"{app_def(ctx)}\n\n\n{deco}\n"
    entry_guard = f'\n\nif __name__ == "__main__":\n{INDENT}{ctx.app}()\n'

    if isinstance(owner, ast.FunctionDef):
        src.replace(src.start(owner), plan.header_end, f"{head}def {owner.name}{render_sig(root.params, plan.ret)}:")
        src.replace(src.line_start(pst.lineno), src.line_end(pst.end_lineno), body_text)
        src.replace(src.start(plan.entry), src.end(plan.entry), f"{ctx.app}()")
        if plan.exit_codes:
            for n in walk_scope(owner):
                if isinstance(n, ast.Return) and n.value is not None:
                    v = literal(n.value)
                    if isinstance(v, int) and not isinstance(v, bool):
                        src.replace(src.start(n), src.end(n), "return" if v == 0 else f"raise typer.Exit({v})")
                    elif v is not None:
                        ctx.warn(n, "returned value was an exit code - convert it to `raise typer.Exit(code)` manually")
    elif is_main_guard(owner):
        name = ctx.unique("main")
        src.replace(src.line_start(owner.lineno), src.line_start(owner.body[0].lineno), f"{head}def {name}{sig}:\n")
        src.replace(src.line_start(pst.lineno), src.line_end(pst.end_lineno), body_text)
        src.after_line(owner.end_lineno, entry_guard)
    else:  # module level: wrap everything after parse_args() into a function
        name = ctx.unique("main")
        if not tail and not lines:
            body_text = f"{INDENT}pass\n"
        else:
            body_text = "".join(f"{INDENT}{line}\n" for line in lines)
        src.replace(
            src.line_start(pst.lineno), src.line_end(pst.end_lineno), f"\n\n{head}def {name}{sig}:\n{body_text}"
        )
        if tail:
            inside = string_lines(src.data.decode())
            for ln in range(pst.end_lineno + 1, tail[-1].end_lineno + 1):
                start, end = src.line_start(ln), src.line_end(ln)
                if ln not in inside and src.data[start:end].strip():
                    src.replace(start, start, INDENT)
            src.after_line(tail[-1].end_lineno, entry_guard)
        else:
            src.after_line(pst.end_lineno, entry_guard)


# --------------------------------------------------------------------------- bridge mode
def find_entry(tree: ast.Module):
    for st in tree.body:
        if is_main_guard(st):
            return "guard", st
    last = tree.body[-1]
    if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call):
        return "call", last
    return None


def state_update(cmd: Cmd, extra: list[tuple[str, str]]) -> str | None:
    items = [(p.dest, p.name) for p in cmd.params if not p.internal] + extra + list(cmd.defaults.items())
    return f"_STATE.update({kwargs_src(items)})" if items else None


def render_bridge(ctx: Ctx, an: Analysis, run: str) -> str:
    root, out = an.root, []
    multi = bool(an.subs)
    out.append(app_def(ctx, root.help if multi else None, root.epilog if multi else None))

    def func(deco: str, name: str, cmd: Cmd, state: str | None) -> str:
        lines = cmd_prologue(cmd, ctx) + ([state] if state else [])
        if not multi or cmd is not root:
            lines.append(f"{run}()")
        body = "".join(f"{INDENT}{line}\n" for line in lines) or f"{INDENT}pass\n"
        return f"{deco}\ndef {name}{render_sig(cmd.params, ' -> None')}:\n{body}"

    if not multi:
        out.append(func(f"@{ctx.app}.command({cmd_kwargs(root)})", ctx.unique("cli"), root, state_update(root, [])))
    else:
        if root.params or root.defaults:
            out.append(func(f"@{ctx.app}.callback()", ctx.unique("_root"), root, state_update(root, [])))
        for sub in an.subs:
            extra = [(an.sub_dest, q(sub.name))] if an.sub_dest else []
            fname = ctx.unique("cmd_" + py_name(sub.name))
            out.append(func(f"@{ctx.app}.command({cmd_kwargs(sub, sub.name)})", fname, sub, state_update(sub, extra)))
    out.append(f'if __name__ == "__main__":\n{INDENT}{ctx.app}()')
    return "\n\n\n".join(part.rstrip() for part in out) + "\n"


def apply_bridge(tree, src: Source, ctx: Ctx, loc: Loc, an: Analysis, reason: str) -> None:
    entry = find_entry(tree)
    if loc.owner is tree or entry is None:
        raise ConversionError(
            f"unsupported layout ({reason}). Move the parser into a function (or the __main__ block) "
            "so it can be converted."
        )
    ctx.need("SimpleNamespace", "click")
    ctx.extra_defs.append(BRIDGE_DEFS)
    creation = an.creation
    src.replace(src.start(creation), src.end(creation), f"{creation.targets[0].id} = _ParserShim()")
    for st in an.handled:
        src.delete_stmt(st)
    run = ctx.unique("_run")
    kind, node = entry
    block = "\n\n" + render_bridge(ctx, an, run)
    if kind == "guard":
        if node.body[0].lineno <= node.lineno:
            raise ConversionError("one-line __main__ block is not supported")
        src.replace(src.line_start(node.lineno), src.line_start(node.body[0].lineno), f"def {run}() -> None:\n")
        src.after_line(node.end_lineno, block)
    else:
        text = "".join(INDENT + line for line in (src.seg(node) + "\n").splitlines(keepends=True))
        src.replace(src.line_start(node.lineno), src.line_end(node.end_lineno), f"\n\ndef {run}() -> None:\n{text}")
        src.after_line(node.end_lineno, block)
    ctx.warnings.append(
        f"note: used the bridge layout ({reason}); `args = parser.parse_args()` still exists in the output"
    )


# --------------------------------------------------------------------------- finishing
def check_leftovers(tree, an: Analysis, ctx: Ctx) -> None:
    handled = {id(n) for st in an.handled for n in ast.walk(st)}
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in PARSER_METHODS
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id in an.table
            and id(n) not in handled
        ):
            ctx.warn(n, f"{n.func.attr}() is not at the top level of the parser block - NOT converted")


def tidy(text: str) -> str:
    """Collapse runs of 3+ blank lines (outside string literals) to two."""
    inside = string_lines(text)
    out, blanks = [], 0
    for lineno, line in enumerate(text.splitlines(keepends=True), 1):
        if not line.strip() and lineno not in inside:
            blanks += 1
            if blanks > 2:
                continue
        else:
            blanks = 0
        out.append(line)
    return "".join(out)


def finalize(text: str, ctx: Ctx) -> str:
    tree = ast.parse(text)
    src = Source(text)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    bound: set[str] = set()
    anchor = doc = None
    for i, node in enumerate(tree.body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            anchor = node
        elif (
            i == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            doc = node
        else:
            break
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        keep = []
        for a in node.names:
            bname = a.asname or a.name.split(".")[0]
            is_argparse = (
                a.name.split(".")[0] == "argparse" if isinstance(node, ast.Import) else node.module == "argparse"
            )
            if is_argparse and a.name != "*" and bname not in used:
                continue
            keep.append(a)
            bound.add(bname)
        if len(keep) != len(node.names):
            if not keep:
                src.delete_stmt(node)
            else:
                new = (
                    ast.Import(names=keep)
                    if isinstance(node, ast.Import)
                    else ast.ImportFrom(module=node.module, names=keep, level=node.level)
                )
                src.replace(src.start(node), src.end(node), ast.unparse(new))

    missing = [n for n in ctx.needed if n not in bound]
    std = sorted(
        (IMPORT_SPECS[n][1] for n in missing if IMPORT_SPECS[n][0] == "std"), key=lambda s: (s.startswith("from"), s)
    )
    third = sorted(IMPORT_SPECS[n][1] for n in missing if IMPORT_SPECS[n][0] == "3rd")
    block = "".join(f"{line}\n" for line in std)
    if third:
        block += ("\n" if std else "") + "".join(f"{line}\n" for line in third)
    if ctx.extra_defs:
        block += "\n\n" + "\n\n".join(d.rstrip() + "\n" for d in ctx.extra_defs) + "\n\n"
    if anchor is not None:
        src.after_line(anchor.end_lineno, block)
    elif doc is not None:
        src.after_line(doc.end_lineno, "\n" + block)
    else:
        pos = 0
        for line in text.encode().splitlines(keepends=True):
            if line.startswith(b"#") or not line.strip():
                pos += len(line)
            else:
                break
        src.replace(pos, pos, block + "\n")
    result = tidy(src.apply())
    try:
        ast.parse(result)
    except SyntaxError as exc:
        raise ConversionError(f"internal error - generated code is not valid Python ({exc})") from exc
    return result


def convert(text: str) -> tuple[str, list[str]]:
    tree = ast.parse(text)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in UNSUPPORTED_PARSE:
            raise ConversionError(f"line {n.lineno}: {n.func.attr}() has no Typer equivalent")
    src = Source(text)
    ctx = Ctx(src, tree)
    loc = locate_parser(tree)
    an = analyze(loc, ctx)
    for cmd in [an.root, *an.subs]:
        dedupe(cmd, ctx)
    plan = plan_inline(tree, src, loc, an)
    if isinstance(plan, InlinePlan):
        apply_inline(tree, src, ctx, loc, an, plan)
    else:
        apply_bridge(tree, src, ctx, loc, an, plan)
    check_leftovers(tree, an, ctx)
    return finalize(src.apply(), ctx), ctx.warnings


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] in {"-h", "--help"}:
        print(__doc__.split("How it works")[0].strip(), file=sys.stderr)
        return 0 if len(sys.argv) == 2 else 2
    source_path = Path(sys.argv[1])
    if not source_path.is_file():
        print(f"error: {source_path} is not a file", file=sys.stderr)
        return 2
    try:
        result, warnings = convert(source_path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        print(f"error: {source_path} is not valid Python: {exc}", file=sys.stderr)
        return 1
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out_path = source_path.with_name(f"{source_path.stem}_typer{source_path.suffix}")
    out_path.write_text(result, encoding="utf-8")
    shutil.copymode(source_path, out_path)
    print(f"wrote {out_path}")
    if warnings:
        print("\nReview these items:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
