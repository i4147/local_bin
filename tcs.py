#!/data/data/com.termux/files/usr/bin/env python

import ast
import builtins
import keyword
import string
import subprocess
import sys
from collections import Counter
from itertools import count, product
from pathlib import Path
from typing import Iterator

try:
    import libcst as cst
except ImportError:
    cst = None

TEXT1 = "Return full type-annotated code in one markdown block, no docstrings & no comments"
USAGE = """\
Usage: {prog} <path> [start [end]] [-m|--mangle | -x]
       {prog} <path> -s <line> [<line> ...] [-m|--mangle | -x]
  start/end  1-based inclusive line range (end defaults to last line)
  -s         copy only the listed lines, in the order given
  -m,--mangle
             compress Python code (strip docstrings/comments/annotations,
             shorten local names) and append a usage prompt.
  -x         strip comments, docstrings, shebang, and __future__ imports
             from a Python file using libcst (formatting preserved).
             Requires libcst; only applies to Python files.
Without -m/-x, content is copied as-is."""
RESERVED = set(dir(builtins)) | set(keyword.kwlist)
SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def send_to_clipboard(text: str) -> bool:
    try:
        proc = subprocess.run(
            ["termux-clipboard-set"],
            input=text.encode("utf-8"),
            capture_output=True,
        )
    except FileNotFoundError:
        print(
            "Warning: 'termux-clipboard-set' not found; skipping clipboard copy. "
            "Install the Termux:API app and run: pkg install termux-api",
            file=sys.stderr,
        )
        return False
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()
        print(f"Warning: failed to copy to clipboard: {err}", file=sys.stderr)
        return False
    return True


def _all_identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, SCOPE_NODES):
            names.add(node.name)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.update(node.name.split("."))
            if node.asname:
                names.add(node.asname)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _unsafe_names(tree: ast.AST) -> set[str]:
    unsafe: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            unsafe.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            unsafe.add(node.arg)
        elif isinstance(node, ast.alias):
            unsafe.update(node.name.split("."))
            if node.asname:
                unsafe.add(node.asname)
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, SCOPE_NODES):
                    unsafe.add(stmt.name)
                elif isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        for n in ast.walk(target):
                            if isinstance(n, ast.Name):
                                unsafe.add(n.id)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            unsafe.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            unsafe.add(node.rest)
    return unsafe


def _name_stats(tree: ast.AST) -> tuple[set[str], Counter]:
    defined: set[str] = set()
    freq: Counter = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            freq[node.id] += 1
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                defined.add(node.id)
        elif isinstance(node, ast.arg):
            freq[node.arg] += 1
            defined.add(node.arg)
        elif isinstance(node, SCOPE_NODES):
            freq[node.name] += 1
            defined.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            freq[node.name] += 1
            defined.add(node.name)
    return defined, freq


def _short_names(taken: set[str]) -> Iterator[str]:
    for size in count(1):
        for chars in product(string.ascii_letters, repeat=size):
            name = "".join(chars)
            if name not in taken:
                yield name


def _build_rename_mapping(tree: ast.AST) -> dict[str, str]:
    defined, freq = _name_stats(tree)
    unsafe = _unsafe_names(tree)
    targets = [n for n in defined if n not in unsafe and n not in RESERVED and not n.startswith("__")]
    targets.sort(key=lambda n: (-freq[n], n))
    taken = _all_identifiers(tree) | RESERVED | unsafe
    gen = _short_names(taken)
    return {old: next(gen) for old in targets}


if cst is not None:

    class CompressTransformer(cst.CSTTransformer):
        def __init__(self, mapping: dict[str, str]) -> None:
            self.m = mapping

        def _r(self, name: str) -> str:
            return self.m.get(name, name)

        @staticmethod
        def _is_docstring_expr(node: cst.BaseSmallStatement) -> bool:
            return isinstance(node, cst.Expr) and isinstance(node.value, (cst.SimpleString, cst.ConcatenatedString))

        def _is_docstring_stmt(self, stmt: cst.BaseStatement) -> bool:
            return (
                isinstance(stmt, cst.SimpleStatementLine)
                and len(stmt.body) == 1
                and self._is_docstring_expr(stmt.body[0])
            )

        def _strip_body(self, node):
            body = node.body
            if isinstance(body, cst.IndentedBlock):
                stmts = list(body.body)
                if stmts and self._is_docstring_stmt(stmts[0]):
                    stmts = stmts[1:]
                if not stmts:
                    stmts = [cst.SimpleStatementLine(body=[cst.Pass()])]
                return node.with_changes(body=body.with_changes(body=stmts))
            if isinstance(body, cst.SimpleStatementSuite):
                stmts = list(body.body)
                if stmts and self._is_docstring_expr(stmts[0]):
                    stmts = stmts[1:]
                if not stmts:
                    stmts = [cst.Pass()]
                return node.with_changes(body=body.with_changes(body=stmts))
            return node

        def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
            body = list(updated_node.body)
            if body and self._is_docstring_stmt(body[0]):
                body = body[1:]
            return updated_node.with_changes(body=body)

        def _strip_funcdef(self, node):
            if node.returns is not None:
                node = node.with_changes(returns=None)
            return self._strip_body(node)

        def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
            return self._strip_funcdef(updated_node)

        def leave_AsyncFunctionDef(
            self, original_node: cst.AsyncFunctionDef, updated_node: cst.AsyncFunctionDef
        ) -> cst.AsyncFunctionDef:
            return self._strip_funcdef(updated_node)

        def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
            return self._strip_body(updated_node)

        def leave_Param(self, original_node: cst.Param, updated_node: cst.Param) -> cst.Param:
            if updated_node.annotation is not None:
                return updated_node.with_changes(annotation=None)
            return updated_node

        def leave_AnnAssign(
            self, original_node: cst.AnnAssign, updated_node: cst.AnnAssign
        ) -> cst.Assign | cst.RemovalSentinel:
            if updated_node.value is None:
                return cst.RemoveFromParent()
            return cst.Assign(
                targets=[cst.AssignTarget(target=updated_node.target)],
                value=updated_node.value,
            )

        def leave_ImportFrom(
            self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
        ) -> cst.ImportFrom | cst.RemovalSentinel:
            m = updated_node.module
            if isinstance(m, cst.Name) and m.value == "__future__":
                return cst.RemoveFromParent()
            return updated_node

        def leave_SimpleStatementLine(
            self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
        ) -> cst.SimpleStatementLine | cst.RemovalSentinel:
            if not updated_node.body:
                return cst.RemoveFromParent()
            return updated_node

        def leave_SimpleStatementSuite(
            self, original_node: cst.SimpleStatementSuite, updated_node: cst.SimpleStatementSuite
        ) -> cst.SimpleStatementSuite:
            if not updated_node.body:
                return updated_node.with_changes(body=[cst.Pass()])
            return updated_node

        def leave_IndentedBlock(
            self, original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
        ) -> cst.IndentedBlock:
            if not updated_node.body:
                return updated_node.with_changes(body=[cst.SimpleStatementLine(body=[cst.Pass()])])
            return updated_node

        def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
            new = self._r(updated_node.value)
            if new != updated_node.value:
                return updated_node.with_changes(value=new)
            return updated_node

else:
    CompressTransformer = None  # type: ignore[assignment,misc]


def _shrink_indent(code: str) -> str:
    lines = code.splitlines()
    unit = 4
    for line in lines:
        if line and line[0] == " ":
            n = len(line) - len(line.lstrip(" "))
            if 0 < n < unit:
                unit = n
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip(" ")
        if stripped:
            n = len(line) - len(stripped)
            out.append(" " * (n // unit) + stripped)
    return "\n".join(out) + "\n" if out else ""


def compress_source(source: str) -> str | None:
    if CompressTransformer is None:
        return None
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    try:
        module = cst.parse_module(source)
    except Exception:
        return None
    mapping = _build_rename_mapping(tree)
    try:
        new_module = module.visit(CompressTransformer(mapping))
    except Exception:
        return None
    code = _shrink_indent(new_module.code)
    try:
        compile(code, "<compressed>", "exec")
    except SyntaxError:
        return None
    return code


def is_python_file(path: Path, source: str) -> bool:
    if path.suffix.lower() == ".py":
        return True
    if path.suffix == "":
        try:
            ast.parse(source)
            return True
        except (SyntaxError, ValueError):
            return False
    return False


def strip_x(source: str) -> str | None:
    if cst is None:
        return None

    def is_string_expr(expr: cst.Expr) -> bool:
        return isinstance(expr.value, (cst.SimpleString, cst.ConcatenatedString))

    def is_docstring_stmt(stmt: cst.BaseStatement) -> bool:
        if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
            return False
        s = stmt.body[0]
        return isinstance(s, cst.Expr) and is_string_expr(s)

    def is_docstring_small(s: cst.BaseSmallStatement) -> bool:
        return isinstance(s, cst.Expr) and is_string_expr(s)

    def is_future_import_line(stmt: cst.BaseStatement) -> bool:
        if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
            return False
        s = stmt.body[0]
        if not isinstance(s, cst.ImportFrom):
            return False
        m = s.module
        return isinstance(m, cst.Name) and m.value == "__future__"

    def strip_suite(suite):
        if isinstance(suite, cst.IndentedBlock):
            body = list(suite.body)
            if body and is_docstring_stmt(body[0]):
                body = body[1:]
            if not body:
                body = [cst.SimpleStatementLine(body=[cst.Pass()])]
            return suite.with_changes(body=body)
        if isinstance(suite, cst.SimpleStatementSuite):
            body = list(suite.body)
            if body and is_docstring_small(body[0]):
                body = body[1:]
            if not body:
                body = [cst.Pass()]
            return suite.with_changes(body=body)
        return suite

    class XStripper(cst.CSTTransformer):
        def leave_TrailingWhitespace(
            self, orig: cst.TrailingWhitespace, updated: cst.TrailingWhitespace
        ) -> cst.TrailingWhitespace:
            if updated.comment is not None:
                return updated.with_changes(
                    comment=None,
                    whitespace=cst.SimpleWhitespace(""),
                )
            return updated

        def leave_EmptyLine(self, orig: cst.EmptyLine, updated: cst.EmptyLine) -> cst.RemovalSentinel:
            return cst.RemoveFromParent()

        def leave_Module(self, orig: cst.Module, updated: cst.Module) -> cst.Module:
            body = list(updated.body)
            if body and is_docstring_stmt(body[0]):
                body = body[1:]
            body = [s for s in body if not is_future_import_line(s)]
            return updated.with_changes(
                body=body,
                header=[],
                footer=[],
            )

        def leave_ClassDef(self, orig: cst.ClassDef, updated: cst.ClassDef) -> cst.ClassDef:
            return updated.with_changes(body=strip_suite(updated.body))

        def leave_FunctionDef(self, orig: cst.FunctionDef, updated: cst.FunctionDef) -> cst.FunctionDef:
            return updated.with_changes(body=strip_suite(updated.body))

        def leave_AsyncFunctionDef(
            self, orig: cst.AsyncFunctionDef, updated: cst.AsyncFunctionDef
        ) -> cst.AsyncFunctionDef:
            return updated.with_changes(body=strip_suite(updated.body))

    try:
        module = cst.parse_module(source)
        return module.visit(XStripper()).code
    except Exception:
        return None


def build_payload(
    path: Path,
    source: str,
    ranged: bool,
    mangle: bool,
    strip: bool,
) -> str:
    if strip:
        if not is_python_file(path, source):
            print("Note: -x only applies to Python files; copied as-is.", file=sys.stderr)
            return source
        cleaned = strip_x(source)
        if cleaned is None:
            print(
                "Warning: libcst unavailable or parse failed; copied as-is. Install with: pip install libcst",
                file=sys.stderr,
            )
            return source
        return cleaned.rstrip("\n") + "\n\n" + TEXT1 + "\n"
    if not mangle:
        return source
    if ranged or is_python_file(path, source):
        if cst is None:
            print(
                "Warning: libcst required for -m/--mangle; copied as-is. Install with: pip install libcst",
                file=sys.stderr,
            )
            return source
        compressed = compress_source(source)
        if compressed is not None:
            return compressed.rstrip("\n") + "\n\n" + TEXT1 + "\n"
        if ranged:
            print("Note: selection is not valid Python; copied as-is.", file=sys.stderr)
        else:
            print("Warning: could not parse file; copied as-is.", file=sys.stderr)
    return source


def main(argv: list[str]) -> None:
    raw = argv[1:]
    mangle = False
    strip = False
    rest: list[str] = []
    for a in raw:
        if a in ("-m", "--mangle"):
            mangle = True
        elif a == "-x":
            strip = True
        else:
            rest.append(a)
    if mangle and strip:
        die("Error: -m/--mangle and -x are mutually exclusive.")
    args = [a for a in rest if a != "-s"]
    selective = len(args) != len(rest)
    if not args:
        die(USAGE.format(prog=Path(argv[0]).name))
    path = Path(args[0])
    if not path.is_file():
        die(f"Error: File not found at '{path}'")
    try:
        nums = [int(a) for a in args[1:]]
    except ValueError:
        die("Error: line numbers must be integers.")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        die(f"Error reading '{path}': {e}")
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if selective:
        if not nums:
            die("Error: -s needs at least one line number.")
        bad = [n for n in nums if not 1 <= n <= total]
        if bad:
            die(f"Error: line(s) {bad} out of range. File has {total} lines.")
        source, ranged = "".join(lines[n - 1] for n in nums), True

    elif not nums:
        source, ranged = text, False
    elif len(nums) == 1:
        n = nums[0]
        if not 1 <= n <= total:
            die(f"Error: line ({n}) out of range. File has {total} lines.")
        source, ranged = lines[n - 1], True
    elif len(nums) == 2:
        start, end = nums
        if not 1 <= start <= total:
            die(f"Error: start line ({start}) out of range. File has {total} lines.")
        if not start <= end <= total:
            die(f"Error: end line ({end}) must be between start line and {total}.")
        source, ranged = "".join(lines[start - 1 : end]), True
    else:
        die(USAGE.format(prog=Path(argv[0]).name))

    if not source:
        die("No content selected to copy.")
    payload = build_payload(path, source, ranged, mangle, strip)
    ok = send_to_clipboard(payload)
    status = "clipboard OK" if ok else "clipboard unavailable"
    print(
        f"Processed {len(payload)} chars (original {len(source)}); {status}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
