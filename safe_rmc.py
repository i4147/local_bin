#!/data/data/com.termux/files/usr/bin/env python
"""
strip_comments_docstrings.py

Safely strip comments and docstrings from Python source files using libcst.

Requires:  pip install libcst
"""

from __future__ import annotations
import argparse
from collections.abc import Iterable, Iterator, Sequence
import io
from pathlib import Path
import re
import sys
import tokenize
from typing import Any, TypeVar

from dh import atomic_write
import libcst as cst
import os
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)

_CmtNodeT = TypeVar("_CmtNodeT", cst.EmptyLine, cst.TrailingWhitespace)
_DefNodeT = TypeVar("_DefNodeT", cst.FunctionDef, cst.ClassDef)
_BodyItemT = TypeVar("_BodyItemT", bound=cst.CSTNode)


# --------------------------------------------------------------------------- #
# Comment classification
# --------------------------------------------------------------------------- #

# Comments that tools rely on; keep these even when stripping other comments.
_DIRECTIVE_RE = re.compile(
    r"^(?:#!"  # shebang                #!/usr/bin/env python
    r"|#\s*-\*-\s*coding[:=]"  # emacs coding cookie    # -*- coding: utf-8 -*-
    r"|#\s*coding[:=]"  # PEP 263 cookie         # coding: utf-8
    r"|#\s*type:"  # type comment (e.g. type-ignore)
    r"|#\s*noqa\b"  # flake8 / ruff
    r"|#\s*nosec\b"  # bandit
    r"|#\s*(?:fmt|isort|yapf|black|ruff)\s*:"
    r"|#\s*(?:mypy|pyright|pytype|pylint|flake8)\s*:"
    r"|#\s*pragma\s*:"
    r")",
    re.IGNORECASE,
)


def _is_directive(comment: str) -> bool:
    return bool(_DIRECTIVE_RE.match(comment.strip()))


# --------------------------------------------------------------------------- #
# Comment stripping
# --------------------------------------------------------------------------- #


class _CommentStripper(cst.CSTTransformer):
    """Remove comments except tool directives (which some tools read as code)."""

    def __init__(self, keep_directives: bool = True) -> None:
        super().__init__()
        self.keep_directives = keep_directives

    def _strip(self, node: _CmtNodeT) -> _CmtNodeT:
        if node.comment is None:
            return node
        if self.keep_directives and _is_directive(node.comment.value):
            return node
        return node.with_changes(comment=None)

    def leave_EmptyLine(self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine) -> cst.EmptyLine:
        return self._strip(updated_node)

    def leave_TrailingWhitespace(
        self,
        original_node: cst.TrailingWhitespace,
        updated_node: cst.TrailingWhitespace,
    ) -> cst.TrailingWhitespace:
        return self._strip(updated_node)


# --------------------------------------------------------------------------- #
# Docstring stripping
# --------------------------------------------------------------------------- #

_STRING_NODES: tuple[Any, ...] = (cst.SimpleString, cst.ConcatenatedString)


def _is_string_expr(node: cst.CSTNode) -> bool:
    return isinstance(node, cst.Expr) and isinstance(node.value, _STRING_NODES)


def _strip_body_docstring(body: Sequence[_BodyItemT]) -> tuple[Sequence[_BodyItemT], bool]:
    """
    Remove the leading docstring from a body sequence.

    `body` is either
      * Sequence[BaseStatement]      (Module.body / IndentedBlock.body), or
      * Sequence[BaseSmallStatement] (SimpleStatementSuite.body).

    Returns (new_body, removed_flag).
    """
    if not body:
        return body, False
    first = body[0]
    if isinstance(first, cst.SimpleStatementLine):
        if first.body and _is_string_expr(first.body[0]):
            rest_small = first.body[1:]
            if rest_small:  # e.g. ``\"doc\"; x = 1``
                new_line = first.with_changes(body=rest_small)
                return (new_line, *body[1:]), True
            return tuple(body[1:]), True
    elif _is_string_expr(first):  # SimpleStatementSuite case
        return tuple(body[1:]), True
    return body, False


class _DocstringStripper(cst.CSTTransformer):
    def __init__(
        self,
        strip_module: bool = True,
        strip_class: bool = True,
        strip_function: bool = True,
        replace_with_pass: bool = False,
    ) -> None:
        super().__init__()
        self.strip_module = strip_module
        self.strip_class = strip_class
        self.strip_function = strip_function
        self.replace_with_pass = replace_with_pass

    # Module ------------------------------------------------------------- #
    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        if not self.strip_module:
            return updated_node
        new_body, removed = _strip_body_docstring(updated_node.body)
        if not removed:
            return updated_node
        # NOTE: deliberately do NOT insert ``pass`` — that would break
        # ``from __future__ import ...`` which must be the first real statement.
        return updated_node.with_changes(body=new_body)

    # Functions & classes ------------------------------------------------- #
    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        return updated_node if not self.strip_function else self._strip_scoped_body(updated_node)

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        return updated_node if not self.strip_class else self._strip_scoped_body(updated_node)

    def _strip_scoped_body(self, node: _DefNodeT) -> _DefNodeT:
        body = node.body
        if isinstance(body, cst.IndentedBlock):
            new_body, removed = _strip_body_docstring(body.body)
            if not removed:
                return node
            if not new_body:  # body was only the docstring
                leading = body.body[0].leading_lines if isinstance(body.body[0], cst.SimpleStatementLine) else ()
                pas = cst.SimpleStatementLine(body=[cst.Pass()], leading_lines=leading)
                return node.with_changes(body=body.with_changes(body=(pas,)))
            if self.replace_with_pass:  # keep the (single) line
                first = body.body[0]
                pas = cst.SimpleStatementLine(
                    body=[cst.Pass()],
                    leading_lines=first.leading_lines if isinstance(first, cst.SimpleStatementLine) else (),
                )
                return node.with_changes(body=body.with_changes(body=(pas,) + tuple(new_body)))
            return node.with_changes(body=body.with_changes(body=tuple(new_body)))
        if isinstance(body, cst.SimpleStatementSuite):
            new_body, removed = _strip_body_docstring(body.body)
            if not removed:
                return node
            if not new_body:
                return node.with_changes(body=body.with_changes(body=(cst.Pass(),)))
            if self.replace_with_pass:
                return node.with_changes(body=body.with_changes(body=(cst.Pass(),) + tuple(new_body)))
            return node.with_changes(body=body.with_changes(body=tuple(new_body)))
        return node


# --------------------------------------------------------------------------- #
# Public transform
# --------------------------------------------------------------------------- #


def strip_source(
    source: str,
    *,
    strip_comments: bool = True,
    strip_docstrings: bool = True,
    keep_directives: bool = True,
    keep_module_doc: bool = False,
    keep_class_doc: bool = False,
    keep_function_doc: bool = False,
    replace_with_pass: bool = False,
) -> str:
    """Return ``source`` with comments and/or docstrings stripped."""
    module = cst.parse_module(source)
    if strip_comments:
        module = module.visit(_CommentStripper(keep_directives=keep_directives))
    if strip_docstrings:
        module = module.visit(
            _DocstringStripper(
                strip_module=not keep_module_doc,
                strip_class=not keep_class_doc,
                strip_function=not keep_function_doc,
                replace_with_pass=replace_with_pass,
            )
        )
    return module.code


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _iter_py_files(paths: Iterable[Path], recursive: bool) -> Iterator[Path]:
    for p in paths:
        if p.is_dir():
            if not recursive:
                raise SystemExit(f"{p}: is a directory (use -r to recurse)")
            for f in sorted(p.rglob("*.py")):
                if "__pycache__" in f.parts:
                    continue
                yield f
        elif p.is_file():
            yield p
        else:
            raise SystemExit(f"{p}: no such file or directory")


def _read_source(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding), encoding


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    ap = argparse.ArgumentParser(
        description="Strip comments and docstrings from Python source (libcst).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("-r", "--recursive", action="store_true")
    ap.add_argument("-i", "--in-place", action="store_true")
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write results under this directory, mirroring the input tree",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any file would change; write nothing",
    )
    ap.add_argument(
        "--print",
        dest="print_single",
        action="store_true",
        help="print the result of a single file to stdout",
    )
    ap.add_argument("--no-comments", action="store_true")
    ap.add_argument("--no-docstrings", action="store_true")
    ap.add_argument(
        "--no-keep-directives",
        action="store_true",
        help="also strip # type:, # noqa, # fmt:, coding cookies, shebangs, ...",
    )
    ap.add_argument("--keep-module-doc", action="store_true")
    ap.add_argument("--keep-class-doc", action="store_true")
    ap.add_argument("--keep-function-doc", action="store_true")
    ap.add_argument(
        "--replace-with-pass",
        action="store_true",
        help="replace stripped docstrings with `pass` to preserve line numbers "
        "(module docstrings are still removed to keep __future__ imports valid)",
    )
    ap.add_argument("-q", "--quiet", action="store_true")
    opts = ap.parse_args(argv)

    if opts.in_place and opts.output is not None:
        ap.error("-i/--in-place and -o/--output are mutually exclusive")
    if opts.check and (opts.in_place or opts.output is not None):
        ap.error("--check cannot be combined with -i/-o")

    files = list(_iter_py_files(opts.paths, opts.recursive))
    if not files:
        print("no Python files found", file=sys.stderr)
        return 2

    if opts.print_single and len(files) != 1:
        ap.error("--print requires exactly one input file")

    default_print = not opts.in_place and opts.output is None and not opts.check and len(files) == 1
    do_print = opts.print_single or default_print

    any_changed = False
    for path in files:
        try:
            text, encoding = _read_source(path)
            new_text = strip_source(
                text,
                strip_comments=not opts.no_comments,
                strip_docstrings=not opts.no_docstrings,
                keep_directives=not opts.no_keep_directives,
                keep_module_doc=opts.keep_module_doc,
                keep_class_doc=opts.keep_class_doc,
                keep_function_doc=opts.keep_function_doc,
                replace_with_pass=opts.replace_with_pass,
            )
        except Exception as e:  # parse/decode errors
            print(f"{path}: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

        if new_text == text:
            if not opts.quiet and not do_print:
                print(f"{path}: unchanged")
            continue
        any_changed = True

        if do_print:
            sys.stdout.write(new_text)
            continue
        if opts.check:
            if not opts.quiet:
                print(f"{path}: would change")
            continue

        if opts.in_place:
            dest = path
        else:
            try:
                rel = path.resolve().relative_to(Path.cwd().resolve())
            except ValueError:
                rel = Path(path.name)
            dest = opts.output / rel
            dest.parent.mkdir(parents=True, exist_ok=True)

        atomic_write(dest, new_text.encode(encoding))
        if not opts.quiet:
            print(f"{path}: {'rewritten' if opts.in_place else '-> ' + str(dest)}")

    return 1 if (opts.check and any_changed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
