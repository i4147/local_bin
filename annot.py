#!/data/data/com.termux/files/usr/bin/env python
"""Annotate or strip Python type annotations in place using LibCST.
By default the tool infers best-effort parameter, return, and variable
annotations for every ``.py`` file it visits, adding any imports the new
annotations require. Passing ``--strip`` (or ``--remove-annotations``)
reverses the operation: existing annotations are removed from the input
files and imports that only served those annotations are cleaned up. Both
modes validate every rewrite for CST-level semantic equivalence before a
file is touched.
"""

from __future__ import annotations
import argparse
import builtins
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
import difflib
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import TYPE_CHECKING, NotRequired, TypedDict, cast, override

import libcst as cst
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import AddImportsVisitor
from libcst.helpers import filter_node_fields
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from rich.progress import Progress


def _create_progress() -> Progress | None:
    """Build a ``rich`` progress bar, or return None when rich is unavailable.
    Importing rich lazily keeps startup fast on constrained environments such
    as Termux, where rich is typically not installed at all.
    """
    try:
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress as _RichProgress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
        )
    except ImportError:
        return None
    return _RichProgress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    )


DEFAULT_JOBS: int = 4
DEFAULT_PASSES: int = 6
MAX_INFER_DEPTH: int = 6
MAX_TUPLE_ELEMENTS: int = 4
MIN_CTOR_STORE_RATIO: float = 0.5
MIN_DIFF_MATCH_RATIO: float = 0.4
HIGH_EVIDENCE_SCORE: int = 10
CONFLICT_EVIDENCE_SCORE: int = 6
MIN_CONFLICT_SIGNALS: int = 3
ALWAYS_RAISES_MAX_STMTS: int = 2
MIN_FACTORY_BODY_STMTS: int = 2
SHORT_NAME_MAX_LEN: int = 2
TWO_ARGS_COUNT: int = 2
FALLBACK_PRIORITY: int = 99
DEFAULT_EXCLUDES: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
    "build",
    "dist",
    ".tox",
    ".nox",
})
BUILTIN_TYPES: set[str] = {
    "int",
    "float",
    "complex",
    "bool",
    "str",
    "bytes",
    "bytearray",
    "list",
    "dict",
    "set",
    "frozenset",
    "tuple",
    "object",
    "type",
    "range",
    "None",
}
NUMERIC: set[str] = {"int", "float", "complex"}
TYPING_NAMES: frozenset[str] = frozenset({
    "Any",
    "Optional",
    "Union",
    "Iterator",
    "Iterable",
    "Sequence",
    "Mapping",
    "MutableMapping",
    "TextIO",
    "IO",
    "Callable",
    "Generator",
    "AsyncIterator",
    "AsyncIterable",
    "NoReturn",
    "TypeVar",
    "ClassVar",
    "Final",
    "Literal",
    "TypedDict",
    "NamedTuple",
    "Protocol",
    "overload",
    "cast",
    "TYPE_CHECKING",
    "Self",
})
PATH_ATTRS: dict[str, int] = {
    "resolve": 6,
    "read_text": 8,
    "write_text": 8,
    "read_bytes": 8,
    "write_bytes": 8,
    "is_file": 6,
    "is_dir": 6,
    "exists": 6,
    "unlink": 8,
    "mkdir": 8,
    "rglob": 8,
    "iterdir": 8,
    "relative_to": 6,
    "as_posix": 6,
    "expanduser": 6,
    "with_suffix": 6,
    "with_name": 6,
    "is_symlink": 6,
    "glob": 6,
    "is_absolute": 6,
    "touch": 6,
    "chmod": 6,
}
STR_ATTRS: dict[str, int] = {
    "strip": 4,
    "lstrip": 4,
    "rstrip": 4,
    "splitlines": 5,
    "split": 3,
    "rsplit": 3,
    "lower": 5,
    "upper": 5,
    "casefold": 5,
    "startswith": 6,
    "endswith": 6,
    "zfill": 4,
    "isdigit": 5,
    "isalpha": 5,
    "isalnum": 5,
    "isspace": 5,
    "capitalize": 4,
    "title": 4,
    "removesuffix": 4,
    "removeprefix": 4,
}
RICH_STR_ATTRS: dict[str, int] = {
    "replace": 3,
    "join": 4,
    "encode": 5,
    "format": 2,
    "partition": 3,
    "rpartition": 3,
}
BYTES_ATTRS: dict[str, int] = {"decode": 8}
DICT_ATTRS: dict[str, int] = {"keys": 5, "setdefault": 6, "popitem": 5, "get": 2}
RICH_DICT_ATTRS: dict[str, int] = {"items": 3, "values": 2, "update": 2}
LIST_ATTRS: dict[str, int] = {
    "append": 5,
    "extend": 4,
    "insert": 4,
    "sort": 4,
    "reverse": 4,
}
SET_ATTRS: dict[str, int] = {
    "discard": 5,
    "issubset": 5,
    "issuperset": 5,
    "symmetric_difference": 5,
}
FILE_ATTRS: dict[str, int] = {
    "readlines": 8,
    "readline": 6,
    "writelines": 8,
    "fileno": 8,
    "readable": 8,
    "writable": 8,
    "truncate": 8,
    "read": 4,
    "write": 3,
    "seek": 4,
    "tell": 3,
}
FILE_STR_ATTRS: dict[str, int] = {"seek": 3, "tell": 3, "read": 2, "write": 2}
MATCH_ATTRS: dict[str, int] = {"groupdict": 8, "groups": 8, "span": 6}
MATCH_RICH: dict[str, int] = {"group": 4, "expand": 3}
PROCESS_ATTRS: dict[str, int] = {"returncode": 8, "check_returncode": 8}
PROCESS_RICH: dict[str, int] = {"stdout": 3, "stderr": 3}
REQUESTS_ATTRS: dict[str, int] = {
    "json": 4,
    "raise_for_status": 8,
    "content": 6,
    "iter_content": 8,
    "text": 2,
    "status_code": 6,
}
DT_ATTRS: dict[str, int] = {"isoformat": 8, "timestamp": 8, "strftime": 6}
ATTR_EVIDENCE: tuple[tuple[dict[str, int], str, bool], ...] = (
    (PATH_ATTRS, "Path", True),
    (STR_ATTRS, "str", True),
    (RICH_STR_ATTRS, "str", True),
    (BYTES_ATTRS, "bytes", True),
    (DICT_ATTRS, "dict", True),
    (RICH_DICT_ATTRS, "dict", True),
    (LIST_ATTRS, "list", True),
    (SET_ATTRS, "set", True),
    (FILE_ATTRS, "TextIO", True),
    (FILE_STR_ATTRS, "str", False),
    (MATCH_ATTRS, "Match", True),
    (MATCH_RICH, "Match", False),
    (PROCESS_ATTRS, "CompletedProcess", True),
    (PROCESS_RICH, "CompletedProcess", False),
    (REQUESTS_ATTRS, "Response", True),
    (DT_ATTRS, "datetime", True),
)
NAME_HINTS: dict[str, str] = {
    "name": "str",
    "text": "str",
    "msg": "str",
    "message": "str",
    "content": "str",
    "pattern": "str",
    "line": "str",
    "label": "str",
    "prefix": "str",
    "suffix": "str",
    "encoding": "str",
    "mode": "str",
    "command": "str",
    "cmd": "str",
    "title": "str",
    "url": "str",
    "html": "str",
    "markdown": "str",
    "source": "str",
    "target_lang": "str",
    "lang": "str",
    "lang_code": "str",
    "revision": "str",
    "branch": "str",
    "ref": "str",
    "rev": "str",
    "sha": "str",
    "hash": "str",
    "token": "str",
    "key": "str",
    "separator": "str",
    "sep": "str",
    "delimiter": "str",
    "ext": "str",
    "extension": "str",
    "new": "str",
    "old": "str",
    "reason": "str",
    "description": "str",
    "version": "str",
    "tag": "str",
    "username": "str",
    "email": "str",
    "password": "str",
    "host": "str",
    "user": "str",
    "dir_name": "str",
    "file_name": "str",
    "basename": "str",
    "module": "str",
    "attr": "str",
    "attribute": "str",
    "method": "str",
    "column": "str",
    "comment": "str",
    "raw": "str",
    "text_data": "str",
    "body": "str",
    "html_text": "str",
    "snippet": "str",
    "query": "str",
    "prompt": "str",
    "answer": "str",
    "response": "str",
    "count": "int",
    "n": "int",
    "num": "int",
    "index": "int",
    "idx": "int",
    "size": "int",
    "limit": "int",
    "offset": "int",
    "port": "int",
    "width": "int",
    "height": "int",
    "depth": "int",
    "level": "int",
    "retries": "int",
    "max_workers": "int",
    "workers": "int",
    "line_no": "int",
    "lineno": "int",
    "line_number": "int",
    "min_size": "int",
    "max_size": "int",
    "chunk_size": "int",
    "compression_level": "int",
    "min_mb": "float",
    "max_mb": "float",
    "mb": "float",
    "kb": "float",
    "gb": "float",
    "size_bytes": "int",
    "num_bytes": "int",
    "total_bytes": "int",
    "total_size": "int",
    "total_files": "int",
    "file_count": "int",
    "saved_count": "int",
    "n_files": "int",
    "radius": "float",
    "sigma": "float",
    "alpha": "float",
    "beta": "float",
    "gamma": "float",
    "spaces": "int",
    "indent": "int",
    "indent_size": "int",
    "tab_size": "int",
    "start": "int",
    "stop": "int",
    "step": "int",
    "end": "int",
    "r": "int",
    "g": "int",
    "b": "int",
    "timeout": "float",
    "delay": "float",
    "interval": "float",
    "ratio": "float",
    "threshold": "float",
    "factor": "float",
    "scale": "float",
    "ratio_threshold": "float",
    "verbose": "bool",
    "debug": "bool",
    "force": "bool",
    "enabled": "bool",
    "flag": "bool",
    "dry_run": "bool",
    "recursive": "bool",
    "quiet": "bool",
    "check": "bool",
    "capture": "bool",
    "overwrite": "bool",
    "include_hidden": "bool",
    "follow_links": "bool",
    "as_bytes": "bool",
    "append": "bool",
    "create": "bool",
    "strict": "bool",
    "absolute": "bool",
    "verify": "bool",
    "show": "bool",
    "write": "bool",
    "read": "bool",
    "clean": "bool",
    "sync": "bool",
    "trim": "bool",
    "use_cache": "bool",
}
PATH_LIKE_NAMES: set[str] = {
    "path",
    "src",
    "dst",
    "dest",
    "target",
    "dest_path",
    "src_path",
    "base",
    "base_dir",
    "root",
    "directory",
    "dir",
    "folder",
    "folder_path",
    "file",
    "file_name",
    "filename",
    "file_path",
    "filepath",
    "out",
    "output",
    "output_path",
    "output_dir",
    "input",
    "input_path",
    "source_path",
    "dest_dir",
    "target_dir",
    "workdir",
    "repo",
    "repo_path",
    "archive_path",
    "patch_file",
    "meta_file",
    "cwd",
    "parent",
    "archive",
    "src_dir",
    "tmpdir",
    "tmp_dir",
    "tempdir",
    "repos_dir",
    "clone_dir",
    "wheel_path",
    "script_path",
    "out_dir",
    "base_path",
    "trash_dir",
    "cache_dir",
    "temp_path",
}
EXPR_NAME_HINTS: dict[str, str] = {
    "node": "ast.AST",
    "expr": "ast.expr",
    "stmt": "ast.stmt",
    "tree": "ast.AST",
    "module_node": "ast.Module",
    "call_node": "ast.Call",
    "func_node": "ast.FunctionDef",
    "assign": "ast.Assign",
    "body": "ast.Module",
    "arguments": "ast.arguments",
    "arg_node": "ast.arg",
    "exc_node": "ast.ExceptHandler",
}
CONTAINER_NAMES: dict[str, str] = {
    "items": "list",
    "values": "list",
    "entries": "list",
    "files": "list",
    "paths": "list",
    "lines": "list",
    "rows": "list",
    "results": "list",
    "found": "list",
    "matches": "list",
    "ids": "list",
    "names": "list",
    "suffixes": "list",
    "parts": "list",
    "tokens": "list",
    "words": "list",
    "sents": "list",
    "sentences": "list",
    "records": "list",
    "args": "list",
    "argv": "list",
    "elts": "list",
    "elements": "list",
    "members": "list",
    "exts": "list",
    "keys": "list",
    "errors": "list",
    "warnings": "list",
    "targets": "list",
    "kwargs": "dict",
    "options": "dict",
    "config": "dict",
    "configs": "dict",
    "params": "dict",
    "settings": "dict",
    "env": "dict",
    "metadata": "dict",
    "mapping": "dict",
}
CALLABLE_HINTS: set[str] = {
    "callback",
    "func",
    "handler",
    "hook",
    "keyfunc",
    "predicate",
    "action",
    "convert",
    "transform",
    "factory",
    "visitor",
}
BUILTIN_CALL_RETURNS: dict[str, str] = {
    "len": "int",
    "int": "int",
    "float": "float",
    "str": "str",
    "bool": "bool",
    "bytes": "bytes",
    "bytearray": "bytearray",
    "repr": "str",
    "sorted": "list[Any]",
    "reversed": "list[Any]",
    "range": "range",
    "enumerate": "enumerate[Any]",
    "zip": "zip[Any]",
    "iter": "Iterator[Any]",
    "open": "TextIO",
    "input": "str",
    "isinstance": "bool",
    "issubclass": "bool",
    "hasattr": "bool",
    "callable": "bool",
    "print": "None",
    "type": "type",
    "id": "int",
    "ord": "int",
    "chr": "str",
    "round": "int",
    "format": "str",
    "any": "bool",
    "all": "bool",
    "hex": "str",
    "oct": "str",
    "bin": "str",
    "hash": "int",
    "exit": "None",
    "quit": "None",
    "sum": "Any",
    "max": "Any",
    "min": "Any",
    "abs": "Any",
    "divmod": "tuple[int, int]",
    "getattr": "Any",
    "next": "Any",
    "vars": "dict[str, Any]",
    "dir": "list[str]",
    "globals": "dict[str, Any]",
    "locals": "dict[str, Any]",
    "super": "Any",
    "property": "property",
    "staticmethod": "Any",
    "classmethod": "Any",
    "eval": "Any",
    "exec": "None",
    "compile": "Any",
    "memoryview": "memoryview",
    "object": "object",
    "frozenset": "frozenset[Any]",
    "tuple": "tuple[Any, ...]",
    "list": "list[Any]",
    "dict": "dict[Any, Any]",
    "set": "set[Any]",
}
CONTAINER_CALLS: dict[str, str] = {
    "list": "list[Any]",
    "dict": "dict[str, Any]",
    "set": "set[Any]",
    "tuple": "tuple[Any, ...]",
    "frozenset": "frozenset[Any]",
    "OrderedDict": "Any",
    "defaultdict": "Any",
}
METHOD_RETURNS: dict[str, str] = {
    "strip": "str",
    "lstrip": "str",
    "rstrip": "str",
    "lower": "str",
    "upper": "str",
    "casefold": "str",
    "replace": "str",
    "join": "str",
    "encode": "bytes",
    "decode": "str",
    "format": "str",
    "title": "str",
    "capitalize": "str",
    "removeprefix": "str",
    "removesuffix": "str",
    "zfill": "str",
    "center": "str",
    "ljust": "str",
    "rjust": "str",
    "expandtabs": "str",
    "splitlines": "list[str]",
    "partition": "tuple[str, str, str]",
    "rpartition": "tuple[str, str, str]",
    "split": "list[str]",
    "rsplit": "list[str]",
    "startswith": "bool",
    "endswith": "bool",
    "isdigit": "bool",
    "isalpha": "bool",
    "isalnum": "bool",
    "isspace": "bool",
    "is_file": "bool",
    "is_dir": "bool",
    "exists": "bool",
    "is_symlink": "bool",
    "is_absolute": "bool",
    "is_relative": "bool",
    "isupper": "bool",
    "islower": "bool",
    "istitle": "bool",
    "isnumeric": "bool",
    "isdecimal": "bool",
    "isidentifier": "bool",
    "isprintable": "bool",
    "find": "int",
    "rfind": "int",
    "count": "int",
    "index": "int",
    "getsize": "int",
    "hexdigest": "str",
    "digest": "bytes",
    "resolve": "Path",
    "absolute": "Path",
    "expanduser": "Path",
    "joinpath": "Path",
    "with_suffix": "Path",
    "with_name": "Path",
    "relative_to": "Path",
    "read_text": "str",
    "read_bytes": "bytes",
    "write_text": "int",
    "write_bytes": "int",
    "as_posix": "str",
    "as_uri": "str",
    "iterdir": "Iterator[Path]",
    "rglob": "Iterator[Path]",
    "glob": "Iterator[Path]",
    "stat": "Any",
    "open": "TextIO",
    "readlines": "list[str]",
    "readline": "str",
    "read": "str",
    "keys": "Any",
    "values": "Any",
    "items": "Any",
    "get": "Any",
    "pop": "Any",
    "setdefault": "Any",
    "copy": "Any",
    "append": "None",
    "extend": "None",
    "insert": "None",
    "sort": "None",
    "reverse": "None",
    "update": "None",
    "add": "None",
    "discard": "None",
    "remove": "None",
    "clear": "None",
    "close": "None",
    "flush": "None",
    "seek": "int",
    "tell": "int",
    "write": "int",
    "writelines": "None",
    "match": "Any",
    "search": "Any",
    "fullmatch": "Any",
    "findall": "Any",
    "finditer": "Any",
    "sub": "str",
    "compile": "Any",
    "dumps": "str",
    "dump": "None",
    "loads": "Any",
    "load": "Any",
    "run": "Any",
    "check_output": "bytes",
}
MODULE_CALL_RETURNS: dict[tuple[str, ...], str] = {
    ("subprocess", "run"): "subprocess.CompletedProcess[str]",
    ("subprocess", "check_output"): "bytes",
    ("subprocess", "check_call"): "int",
    ("os", "getcwd"): "str",
    ("os", "listdir"): "list[str]",
    ("os", "makedirs"): "None",
    ("os", "remove"): "None",
    ("os", "unlink"): "None",
    ("os", "rename"): "None",
    ("os", "chdir"): "None",
    ("os", "path", "join"): "str",
    ("os", "path", "exists"): "bool",
    ("os", "path", "isfile"): "bool",
    ("os", "path", "isdir"): "bool",
    ("os", "path", "basename"): "str",
    ("os", "path", "dirname"): "str",
    ("os", "path", "abspath"): "str",
    ("json", "dumps"): "str",
    ("json", "dump"): "None",
    ("json", "loads"): "Any",
    ("json", "load"): "Any",
    ("re", "sub"): "str",
    ("re", "match"): "re.Match[str] | None",
    ("re", "search"): "re.Match[str] | None",
    ("re", "fullmatch"): "re.Match[str] | None",
    ("re", "compile"): "re.Pattern[str]",
    ("re", "findall"): "list[Any]",
    ("re", "finditer"): "Iterator[re.Match[str]]",
    ("re", "escape"): "str",
    ("ast", "iter_child_nodes"): "Iterator[ast.AST]",
    ("ast", "walk"): "Iterator[ast.AST]",
    ("shutil", "which"): "str | None",
    ("shutil", "rmtree"): "None",
    ("shutil", "copy"): "str",
    ("shutil", "copy2"): "str",
    ("shutil", "copyfile"): "str",
    ("shutil", "move"): "str",
    ("tempfile", "mkdtemp"): "str",
    ("tempfile", "mkstemp"): "tuple[int, str]",
    ("time", "time"): "float",
    ("time", "strftime"): "str",
    ("pathlib", "Path"): "Path",
}
MODULE_ATTR_RETURNS: dict[tuple[str, str], str] = {
    ("os.path", "join"): "str",
    ("os.path", "basename"): "str",
    ("os.path", "dirname"): "str",
    ("os.path", "abspath"): "str",
    ("os.path", "realpath"): "str",
    ("os.path", "expanduser"): "str",
    ("os.path", "relpath"): "str",
    ("os.path", "normpath"): "str",
    ("os.path", "splitext"): "tuple[str, str]",
    ("os.path", "exists"): "bool",
    ("os.path", "isfile"): "bool",
    ("os.path", "isdir"): "bool",
    ("os.path", "islink"): "bool",
    ("os.path", "getsize"): "int",
    ("os", "getcwd"): "str",
    ("os", "listdir"): "list[str]",
}
DUNDER_RETURNS: dict[str, str] = {
    "__init__": "None",
    "__post_init__": "None",
    "__str__": "str",
    "__repr__": "str",
    "__format__": "str",
    "__eq__": "bool",
    "__ne__": "bool",
    "__lt__": "bool",
    "__le__": "bool",
    "__gt__": "bool",
    "__ge__": "bool",
    "__contains__": "bool",
    "__bool__": "bool",
    "__hash__": "int",
    "__len__": "int",
    "__index__": "int",
    "__next__": "Any",
}
BASE_METHOD_RETURNS: dict[tuple[str, str], str] = {
    ("ast.NodeVisitor", "generic_visit"): "None",
    ("ast.NodeTransformer", "generic_visit"): "ast.AST | None",
    ("cst.CSTVisitor", "generic_visit"): "bool | None",
    ("cst.CSTTransformer", "generic_visit"): "cst.CSTNode",
    ("HTMLParser", "getpos"): "tuple[int, int]",
    ("HTMLParser", "feed"): "None",
    ("HTMLParser", "close"): "None",
    ("HTMLParser", "handle_starttag"): "None",
    ("HTMLParser", "handle_endtag"): "None",
    ("HTMLParser", "handle_data"): "None",
    ("HTMLParser", "handle_comment"): "None",
    ("HTMLParser", "handle_decl"): "None",
    ("HTMLParser", "handle_pi"): "None",
    ("HTMLParser", "handle_entityref"): "None",
    ("HTMLParser", "handle_charref"): "None",
    ("HTMLParser", "handle_startendtag"): "None",
}
TS_ATTRS: set[str] = {
    "type",
    "children",
    "start_byte",
    "end_byte",
    "start_point",
    "end_point",
    "text",
    "child_by_field_name",
    "is_named",
    "named_children",
    "grammar_name",
    "has_error",
    "child_count",
    "field_name_for_child",
    "prev_named_sibling",
    "next_named_sibling",
}
AST_ATTRS: set[str] = {
    "body",
    "targets",
    "id",
    "lineno",
    "col_offset",
    "annotation",
    "returns",
    "decorator_list",
    "elts",
    "name",
    "func",
    "args",
    "value",
    "op",
    "attr",
    "ctx",
    "orelse",
    "handlers",
    "test",
    "iter",
    "generators",
    "keys",
    "values",
    "slice",
    "level",
    "module",
    "names",
}
PRIORITIES: list[str] = [
    "Response",
    "CompletedProcess",
    "datetime",
    "Match",
    "Path",
    "TextIO",
    "FileLike",
    "bytes",
    "set",
    "list",
    "dict",
    "Mapping",
    "Sequence",
    "Iterable",
    "Callable",
    "float",
    "int",
    "bool",
    "str",
]
ANY_ONLY: set[str] = {
    "Any",
    "tuple[Any, ...]",
    "dict[Any, Any]",
    "list[tuple[Any, ...]]",
    "Iterator[Any]",
    "list",
    "dict",
    "set",
    "tuple",
    "list[Any]",
    "set[Any]",
    "dict[str, Any]",
}
EVIDENCE_TYPES: dict[str, str] = {
    "Path": "Path",
    "Command": "str",
    "TextIO": "TextIO",
    "FileLike": "TextIO",
    "Match": "re.Match[str]",
    "Response": "Any",
    "CompletedProcess": "subprocess.CompletedProcess[str]",
    "datetime": "Any",
    "dict": "dict[str, Any]",
    "Mapping": "dict[str, Any]",
    "list": "list[Any]",
    "set": "set[Any]",
    "Sequence": "Sequence[Any]",
    "Iterable": "Iterable[Any]",
    "Callable": "Callable[..., Any]",
}
ANY_OBJECT_NAMES: tuple[str, ...] = (
    "event",
    "lock",
    "detector",
    "translator",
    "client",
    "session",
    "backend",
    "engine",
    "browser",
    "page",
    "driver",
    "log",
    "console",
    "progress",
    "bar",
    "task",
    "instance",
    "inst",
    "worker",
    "pool",
    "executor",
    "manager",
    "app",
    "frame",
    "img",
    "image",
    "tiles",
    "arr",
    "array",
)
ITERABLE_NAMES: tuple[str, ...] = (
    "it",
    "iterable",
    "iterator",
    "seq",
    "sequence",
    "gen",
    "generator",
    "stream",
    "rows",
)
BYTES_SUFFIXES: tuple[str, ...] = ("_bytes", "_blob", "_buf", "_buffer", "_raw")
BYTES_NAMES: tuple[str, ...] = (
    "data_bytes",
    "byte_content",
    "src_bytes",
    "raw_bytes",
    "blob",
    "buf",
    "buffer",
)
STR_SUFFIXES: tuple[str, ...] = (
    "_name",
    "_text",
    "_str",
    "_msg",
    "_message",
    "_url",
    "_key",
    "_pattern",
    "_label",
    "_title",
    "_lang",
    "_mode",
    "_prefix",
    "_suffix",
    "_ext",
    "_encoding",
    "_sep",
    "_tag",
    "_comment",
    "_reason",
    "_revision",
    "_branch",
    "_ref",
    "_sha",
    "_hash",
    "_token",
    "_user",
    "_username",
    "_email",
    "_host",
    "_version",
    "_format",
)
INT_SUFFIXES: tuple[str, ...] = (
    "_count",
    "_num",
    "_size",
    "_len",
    "_idx",
    "_index",
    "_level",
    "_depth",
    "_limit",
    "_port",
    "_number",
    "_width",
    "_height",
    "_offset",
    "_n",
)
BOOL_SUFFIXES: tuple[str, ...] = ("_flag", "_enabled", "_allowed")
BOOL_PREFIXES: tuple[str, ...] = (
    "is_",
    "has_",
    "should_",
    "use_",
    "allow_",
    "can_",
    "enable_",
)
CALL_EVIDENCE: dict[str, tuple[str, int, bool]] = {
    "Path": ("Path", 12, True),
    "open": ("Path", 6, True),
    "str": ("str", 8, False),
    "int": ("int", 8, True),
    "float": ("float", 8, True),
    "bool": ("bool", 8, True),
    "bytes": ("bytes", 8, True),
    "print": ("str", 2, False),
    "repr": ("str", 2, False),
    "range": ("int", 4, False),
}
SEQUENCE_CALLS: set[str] = {
    "len",
    "sorted",
    "list",
    "tuple",
    "set",
    "enumerate",
    "iter",
    "reversed",
    "any",
    "all",
}
ITER_CALLS: set[str] = {
    "iter",
    "list",
    "sorted",
    "set",
    "tuple",
    "enumerate",
    "reversed",
    "any",
    "all",
}
STR_PASS_CALLS: set[str] = {
    "os.path.join",
    "os.path.basename",
    "os.path.dirname",
    "os.path.abspath",
    "os.path.normpath",
}
FILE_PASS_CALLS: set[str] = {"json.dump", "json.load", "pickle.dump", "pickle.load"}
PATH_PASS_CALLS: set[str] = {
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.move",
}
COMMAND_PASS_CALLS: set[str] = {
    "subprocess.run",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
}
PATH_CALL_ATTRS: set[str] = {"exists", "is_file", "is_dir", "mkdir", "unlink"}
RISKY_BASES: tuple[str, ...] = (
    "NamedTuple",
    "TypedDict",
    "Enum",
    "Protocol",
    "App",
    "Widget",
    "ModalScreen",
    "Static",
    "Structure",
    "BaseTransformer",
    "urwid",
    "StreamResponse",
    "ScrollableContainer",
    "str",
)
DUNDER_CONST_OK: set[str] = {"__version__", "__all__"}
ANNOTATION_DRIVEN_DECORATORS: frozenset[str] = frozenset({"dataclass", "define", "frozen", "attrs"})
ANNOTATION_DRIVEN_BASES: frozenset[str] = frozenset({"NamedTuple", "TypedDict", "BaseModel", "DeclarativeBase"})
ERROR_STATUSES: set[str] = {
    "rejected",
    "compile-error",
    "undefined-name",
    "encoding-error",
    "infer-error",
    "parse-error",
    "write-error",
}
VARARG: str = "__VARARG__"
KWARG: str = "__KWARG__"
PEP604_RE = re.compile(r"\|\s*None\b")
_GENERIC_RE = re.compile(r"(\w+)\[(.+)\]")
_ITERABLE_RE = re.compile(r"(?:\w+\.)?(?:Iterator|Iterable|Sequence|list|set|tuple|Generator|AsyncIterator)\[(.+)\]")
_NAME_LEAF_RE = re.compile(r"[A-Za-z_][\w.]*")
_YIELD_FROM_CONTAINER_RE = re.compile(r"(?:list|set|tuple)\[(.+)\]")
_RE_USAGE_RE = re.compile(r"(?<![\w.])re\.")
_ATOM_CACHE: dict[str, re.Pattern[str]] = {}
ANSI_RESET: str = "\x1b[0m"
ANSI_BOLD: str = "\x1b[1m"
DIFF_STYLES: dict[str, str] = {
    "file": "\x1b[1;37m",
    "hunk": "\x1b[36m",
    "add": "\x1b[32m",
    "del": "\x1b[31m",
    "meta": "\x1b[33m",
    "ctx": "\x1b[90m",
}


def atom_re(name: str) -> re.Pattern[str]:
    """Compile and memoize a word-boundary matcher for an annotation name."""
    pat = _ATOM_CACHE.get(name)
    if pat is None:
        pat = re.compile(rf"(?<![\w.]){re.escape(name)}(?![\w.])")
        _ATOM_CACHE[name] = pat
    return pat


def is_param(node: cst.CSTNode | None, name: str) -> bool:
    """Return True when ``node`` is a direct reference to parameter ``name``."""
    return isinstance(node, cst.Name) and node.value == name


def node_code(tree: cst.Module, node: cst.CSTNode) -> str:
    """Render a CST node to source text using the module's formatting config."""
    return tree.code_for_node(node).strip()


def suite_stmts(suite: cst.BaseSuite) -> Sequence[cst.CSTNode]:
    """Return the direct statement sequence inside an indented or inline suite."""
    if isinstance(suite, cst.IndentedBlock):
        return suite.body
    if isinstance(suite, cst.SimpleStatementSuite):
        return suite.body
    return ()


def dotted_name(node: cst.CSTNode) -> str:
    """Render ``a.b.c`` for a chain of attribute accesses, or ``""`` otherwise."""
    parts: list[str] = []
    cur: cst.CSTNode = node
    while isinstance(cur, cst.Attribute):
        parts.append(cur.attr.value)
        cur = cur.value
    if isinstance(cur, cst.Name):
        parts.append(cur.value)
        return ".".join(reversed(parts))
    return ""


def literal_type(node: cst.CSTNode | None) -> str:
    """Return the Python type name of a literal CST node, or ``""``."""
    if node is None:
        return ""
    if isinstance(node, cst.Integer):
        return "int"
    if isinstance(node, cst.Float):
        return "float"
    if isinstance(node, cst.Imaginary):
        return "complex"
    if isinstance(node, cst.FormattedString):
        return "str"
    if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
        val = node.evaluated_value
        if isinstance(val, bytes):
            return "bytes"
        return "str"
    if isinstance(node, cst.Name):
        if node.value in ("True", "False"):
            return "bool"
        if node.value == "None":
            return "None"
    return ""


def _collect_target_names(node: cst.CSTNode) -> set[str]:
    """Collect variable names bound by an assignment, loop, or ``with`` target."""
    if isinstance(node, cst.Name):
        return {node.value}
    if isinstance(node, (cst.Tuple, cst.List)):
        names: set[str] = set()
        for elem in node.elements:
            names.update(_collect_target_names(elem.value))
        return names
    if isinstance(node, cst.StarredElement):
        return _collect_target_names(node.value)
    if isinstance(node, cst.Attribute):
        return _collect_target_names(node.value)
    if isinstance(node, cst.Subscript):
        names = _collect_target_names(node.value)
        for sub in node.slice:
            if isinstance(sub.slice, cst.Index):
                names.update(_collect_target_names(sub.slice.value))
        return names
    return set()


def iter_params(params: cst.Parameters) -> list[tuple[cst.Param, str]]:
    """Return all parameters in source order paired with their parameter kind."""
    ordered: list[tuple[cst.Param, str]] = [
        *((p, "pos") for p in params.posonly_params),
        *((p, "pos") for p in params.params),
    ]
    if isinstance(params.star_arg, cst.Param):
        ordered.append((params.star_arg, "vararg"))
    ordered.extend((p, "kwonly") for p in params.kwonly_params)
    if params.star_kwarg is not None:
        ordered.append((params.star_kwarg, "kwarg"))
    return ordered


def _has_future_annotations(tree: cst.Module) -> bool:
    """Return True if the module imports ``annotations`` from ``__future__``."""
    for stmt in tree.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for small in stmt.body:
            if not isinstance(small, cst.ImportFrom):
                continue
            if small.module is None or dotted_name(small.module) != "__future__":
                continue
            if isinstance(small.names, cst.ImportStar):
                continue
            if any(a.evaluated_name == "annotations" for a in small.names):
                return True
    return False


@dataclass(slots=True)
class FileCtx:
    """Per-file parsing and type-inference state."""

    path: Path
    text: str
    encoding: str
    tree: cst.Module
    future_annotations: bool
    style: str
    defined_names: set[str] = field(default_factory=set[str])
    needed: set[str] = field(default_factory=set[str])
    need_re: bool = False
    need_path: bool = False
    module_consts: dict[str, str] = field(default_factory=dict[str, str])
    class_bases: list[str] = field(default_factory=list[str])
    class_attr_types: dict[str, str] = field(default_factory=dict[str, str])
    class_method_returns: dict[str, str] = field(default_factory=dict[str, str])
    class_method_returns_all: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])
    module_classes: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    method_returns: dict[str, str] = field(default_factory=dict[str, str])
    local_returns: dict[str, str] = field(default_factory=dict[str, str])
    local_types: dict[str, str] = field(default_factory=dict[str, str])
    func: cst.FunctionDef | None = None
    param_types: dict[str, str] = field(default_factory=dict[str, str])

    def has_name(self, name: str) -> bool:
        """Return True when ``name`` is bound in this module or is a builtin type."""
        return name in self.defined_names or name in BUILTIN_TYPES

    def can_use(self, name: str) -> bool:
        """Return True when ``name`` can be emitted directly as an annotation."""
        if name in BUILTIN_TYPES:
            return True
        if name.startswith(("cst.", "re.", "subprocess.")):
            return True
        return name in self.defined_names

    def request(self, typ: str) -> str:
        """Record imports required by ``typ`` and return ``typ`` unchanged."""
        for name in TYPING_NAMES:
            if name not in self.needed and atom_re(name).search(typ):
                self.needed.add(name)
        if not self.need_path and not self.has_name("Path") and atom_re("Path").search(typ):
            self.need_path = True
        if not self.need_re and "re." in typ:
            self.need_re = True
        return typ


@dataclass(frozen=True, slots=True)
class Edit:
    """Summary of one annotation change applied to the CST."""

    kind: str
    detail: str


class FileResult(TypedDict):
    """Structured result dictionary emitted per processed Python file."""

    file: str
    status: str
    edits: list[str]
    imports: list[str]
    error: str
    diff: NotRequired[list[str]]
    added: NotRequired[int]
    removed: NotRequired[int]
    backup: NotRequired[str]


class _ModuleNameCollector(cst.CSTVisitor):
    """Collect every name bound anywhere in a CST module."""

    def __init__(self, *, include_builtins: bool = False) -> None:
        self.defined: set[str] = set(dir(builtins)) if include_builtins else set()

    @override
    def visit_Import(self, node: cst.Import) -> bool:
        for alias in node.names:
            bound = (alias.evaluated_alias or alias.evaluated_name).split(".")[0]
            if bound:
                self.defined.add(bound)
        return False

    @override
    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        if isinstance(node.names, cst.ImportStar):
            return False
        for alias in node.names:
            bound = alias.evaluated_alias or alias.evaluated_name
            if bound:
                self.defined.add(bound)
        return False

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self.defined.add(node.name.value)
        return True

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.defined.add(node.name.value)
        return True

    @override
    def visit_TypeAlias(self, node: cst.TypeAlias) -> bool:
        self.defined.add(node.name.value)
        return True

    @override
    def visit_Assign(self, node: cst.Assign) -> bool:
        for target in node.targets:
            self.defined.update(_collect_target_names(target.target))
        return True

    @override
    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        if isinstance(node.target, cst.Name):
            self.defined.add(node.target.value)
        return True

    @override
    def visit_For(self, node: cst.For) -> bool:
        self.defined.update(_collect_target_names(node.target))
        return True

    @override
    def visit_With(self, node: cst.With) -> bool:
        for item in node.items:
            if item.asname is not None:
                self.defined.update(_collect_target_names(item.asname.name))
        return True

    @override
    def visit_ExceptHandler(self, node: cst.ExceptHandler) -> bool:
        if node.name is not None and isinstance(node.name.name, cst.Name):
            self.defined.add(node.name.name.value)
        return True

    @override
    def visit_Param(self, node: cst.Param) -> bool:
        self.defined.add(node.name.value)
        return True


def collect_names(ctx: FileCtx) -> None:
    """Collect every name bound anywhere in the module."""
    collector = _ModuleNameCollector()
    ctx.tree.visit(collector)
    ctx.defined_names.update(collector.defined)


def names_defined_in(tree: cst.Module) -> set[str]:
    """Return all names defined or imported in ``tree``, plus builtins."""
    collector = _ModuleNameCollector(include_builtins=True)
    tree.visit(collector)
    return collector.defined


def collect_module_consts(ctx: FileCtx) -> None:
    """Record inferred types of top-level module constants for later lookups."""
    consts = ctx.module_consts
    for stmt in ctx.tree.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for small in stmt.body:
            if isinstance(small, cst.Assign):
                typ = infer_expr(ctx, small.value)
                if not typ:
                    continue
                for tgt in small.targets:
                    if isinstance(tgt.target, cst.Name):
                        consts[tgt.target.value] = typ
            elif isinstance(small, cst.AnnAssign) and isinstance(small.target, cst.Name):
                consts[small.target.value] = node_code(ctx.tree, small.annotation.annotation)


def build_ctx(
    path: Path,
    text: str,
    encoding: str,
    *,
    style_override: str = "auto",
    tree: cst.Module | None = None,
) -> FileCtx:
    """Parse source text with LibCST and construct an inference context."""
    parsed = cst.parse_module(text) if tree is None else tree
    future = _has_future_annotations(parsed)
    if style_override in ("pep604", "optional"):
        style = style_override
    else:
        n_opt = text.count("Optional[") + text.count("Union[")
        n_604 = len(PEP604_RE.findall(text))
        style = "pep604" if future or n_604 >= n_opt else "optional"
    ctx = FileCtx(
        path=path,
        text=text,
        encoding=encoding,
        tree=parsed,
        future_annotations=future,
        style=style,
    )
    collect_names(ctx)
    collect_module_consts(ctx)
    return ctx


def load_ctx(path: Path, *, style_override: str = "auto") -> FileCtx:
    """Read a Python file, detect its encoding via LibCST, and build a context."""
    raw = path.read_bytes()
    parsed = cst.parse_module(raw)
    return build_ctx(
        path,
        parsed.code,
        parsed.encoding,
        style_override=style_override,
        tree=parsed,
    )


def split_top_level(typ: str) -> list[str]:
    """Split a PEP 604 union annotation on top-level ``|`` separators only."""
    parts: list[str] = []
    depth = 0
    cur = ""
    for ch in typ:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == "|" and depth == 0:
            if cur.strip():
                parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _split_args(inner: str) -> list[str]:
    """Split a generic type argument list on top-level commas."""
    parts: list[str] = []
    depth = 0
    cur = ""
    for ch in inner:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def merge_generics(a: str, b: str) -> str | None:
    """Merge two related types via numeric promotion or generic element merging."""
    if a == b:
        return a
    ma, mb = _GENERIC_RE.fullmatch(a), _GENERIC_RE.fullmatch(b)
    if ma and mb and ma.group(1) == mb.group(1):
        base = ma.group(1)
        ea = [p.strip() for p in _split_args(ma.group(2))]
        eb = [p.strip() for p in _split_args(mb.group(2))]
        if len(ea) == len(eb):
            merged: list[str] = []
            for x, y in zip(ea, eb, strict=True):
                if x == y:
                    merged.append(x)
                elif "Any" in (x, y):
                    merged.append(y if x == "Any" else x)
                elif {x, y} <= NUMERIC:
                    merged.append("float")
                else:
                    return None
            return f"{base}[{', '.join(merged)}]"
    if {a, b} <= NUMERIC:
        return "float" if "float" in (a, b) else "int"
    if "Any" in (a, b):
        return b if a == "Any" else a
    return None


def _union_none(ctx: FileCtx, typ: str) -> str:
    """Render ``typ | None`` in the file's configured union style."""
    if typ == "Any" or typ.endswith("| None") or typ.startswith("Optional["):
        return typ
    if ctx.style == "pep604":
        return f"{typ} | None"
    ctx.needed.add("Optional")
    if "|" in typ:
        ctx.needed.add("Union")
        return f"Optional[Union[{typ}]]"
    return f"Optional[{typ}]"


def join_types(ctx: FileCtx, types: list[str]) -> str:
    """Collapse candidate types into a single annotation string."""
    flat: list[str] = []
    for t in types:
        if t:
            flat.extend(split_top_level(t))
    if any(t.startswith("tuple[") for t in flat) and len(flat) > 1:
        flat = [t for t in flat if not t.startswith("tuple[")] or ["tuple[Any, ...]"]
    seen: list[str] = []
    for t in flat:
        if t not in seen:
            seen.append(t)
    if not seen:
        return ""
    has_none = "None" in seen
    rest = [t for t in seen if t != "None"]
    if not rest:
        return "None"
    if set(rest) <= NUMERIC and len(rest) > 1:
        rest = ["float" if "float" in rest else "int"]
    if len(rest) > TWO_ARGS_COUNT:
        rest = ["Any"]
    elif len(rest) == TWO_ARGS_COUNT:
        merged = merge_generics(rest[0], rest[1])
        rest = [merged] if merged is not None else ["Any"]
    inner = rest[0]
    if not has_none:
        return inner
    return _union_none(ctx, inner)


def _infer_name_expr(ctx: FileCtx, node: cst.Name) -> str:
    """Infer the type of a variable or builtin constant name."""
    name = node.value
    if name in ("True", "False"):
        return "bool"
    if name == "None":
        return "None"
    t = ctx.param_types.get(name, "") or ctx.module_consts.get(name, "") or ctx.local_returns.get(name, "")
    if not t and ctx.func is not None:
        for param, _ in iter_params(ctx.func.params):
            if param.name.value == name and param.annotation is not None:
                t = node_code(ctx.tree, param.annotation.annotation)
                ctx.param_types[name] = t
                break
    if t:
        return t
    if name in ("__file__", "__name__"):
        return "str"
    return ""


def _infer_sequence_expr(
    ctx: FileCtx,
    node: cst.List | cst.Tuple | cst.Set,
    depth: int,
) -> str:
    """Infer the type of a list, tuple, or set literal expression."""
    elts = [
        e
        for e in (
            infer_expr(
                ctx,
                el.value if isinstance(el, cst.Element) else el,
                depth + 1,
            )
            for el in node.elements
        )
        if e
    ]
    if isinstance(node, cst.Tuple):
        if not elts:
            return "tuple[Any, ...]"
        if len(elts) == 1:
            return "tuple[Any, ...]" if "|" in elts[0] else f"tuple[{elts[0]},]"
        if len(elts) <= MAX_TUPLE_ELEMENTS and all("|" not in e for e in elts):
            return f"tuple[{', '.join(elts)}]"
        if len(set(elts)) == 1 and "|" not in elts[0]:
            return f"tuple[{elts[0]}, ...]"
        return "tuple[Any, ...]"
    base = "list" if isinstance(node, cst.List) else "set"
    inner = join_types(ctx, elts)
    if not elts or not inner or "|" in inner:
        return f"{base}[Any]"
    return f"{base}[{inner}]"


def _infer_dict_expr(ctx: FileCtx, node: cst.Dict, depth: int) -> str:
    """Infer the type of a dictionary literal expression."""
    if not node.elements:
        return "dict[str, Any]"
    ks = [
        k for k in (infer_expr(ctx, el.key, depth + 1) for el in node.elements if isinstance(el, cst.DictElement)) if k
    ]
    vs = [v for v in (infer_expr(ctx, el.value, depth + 1) for el in node.elements) if v]
    kt = join_types(ctx, ks) or "Any"
    vt = join_types(ctx, vs) or "Any"
    if "|" in kt:
        kt = "Any"
    if "|" in vt:
        vt = "Any"
    return f"dict[{kt}, {vt}]"


def _infer_comp_expr(
    ctx: FileCtx,
    node: cst.ListComp | cst.SetComp | cst.DictComp | cst.GeneratorExp,
    depth: int,
) -> str:
    """Infer the type of a comprehension or generator expression."""
    if isinstance(node, cst.DictComp):
        k = infer_expr(ctx, node.key, depth + 1) or "Any"
        v = infer_expr(ctx, node.value, depth + 1) or "Any"
        k_str = k if "|" not in k else "Any"
        v_str = v if "|" not in v else "Any"
        return f"dict[{k_str}, {v_str}]"
    inner = infer_expr(ctx, node.elt, depth + 1)
    if isinstance(node, cst.ListComp):
        return f"list[{inner}]" if inner else "list[Any]"
    if isinstance(node, cst.SetComp):
        return f"set[{inner}]" if inner else "set[Any]"
    return f"Iterator[{inner}]" if inner else "Iterator[Any]"


def _infer_op_expr(
    ctx: FileCtx,
    node: cst.BinaryOperation | cst.UnaryOperation | cst.BooleanOperation,
    depth: int,
) -> str:
    """Infer the type of binary, unary, or boolean operator expressions."""
    if isinstance(node, cst.BinaryOperation):
        left = infer_expr(ctx, node.left, depth + 1)
        right = infer_expr(ctx, node.right, depth + 1)
        if (left == "str" or right == "str") and isinstance(node.operator, (cst.Add, cst.Modulo)):
            return "str"
        if not left and isinstance(node.left, cst.Subscript) and right in NUMERIC:
            return right
        if left in NUMERIC and right in NUMERIC:
            if isinstance(node.operator, cst.Divide) or "float" in (left, right):
                return "float"
            return left or right
        return ""
    if isinstance(node, cst.UnaryOperation):
        if isinstance(node.operator, cst.Not):
            return "bool"
        inner = infer_expr(ctx, node.expression, depth + 1)
        return inner if inner in NUMERIC else ""
    parts = [
        p
        for p in (
            infer_expr(ctx, node.left, depth + 1),
            infer_expr(ctx, node.right, depth + 1),
        )
        if p
    ]
    if parts and all(p == "bool" for p in parts):
        return "bool"
    return join_types(ctx, parts)


def _infer_subscript_expr(ctx: FileCtx, node: cst.Subscript, depth: int) -> str:
    """Infer the element type of a subscript expression."""
    base = infer_expr(ctx, node.value, depth + 1)
    if base.startswith("dict["):
        parts = _split_args(base[len("dict[") : -1])
        return parts[-1].strip() if len(parts) == TWO_ARGS_COUNT else "Any"
    m = _GENERIC_RE.fullmatch(base)
    if m is None:
        return ""
    name, inner = str(m[1]), str(m[2])
    if name == "dict":
        return _split_args(inner)[-1].strip() if "," in inner else "Any"
    if name in ("list", "set"):
        return inner if "|" not in inner else "Any"
    if name == "tuple":
        return _split_args(inner)[0].strip()
    if name in ("str", "bytes"):
        return "str"
    return ""


def infer_expr(ctx: FileCtx, node: cst.CSTNode | None, depth: int = 0) -> str:
    """Infer the best-effort static type of an expression, or ``""``."""
    if node is None or depth > MAX_INFER_DEPTH:
        return ""
    if ctx.func is not None and isinstance(node, cst.Name):
        local = ctx.local_types.get(node.value, "")
        if local and node.value not in ctx.param_types:
            return local
    lit = literal_type(node)
    if lit:
        return lit
    if isinstance(node, cst.Ellipsis):
        return ""
    if isinstance(node, cst.FormattedStringExpression):
        return infer_expr(ctx, node.expression, depth + 1)
    if isinstance(node, cst.Name):
        return _infer_name_expr(ctx, node)
    if isinstance(node, (cst.List, cst.Tuple, cst.Set)):
        return _infer_sequence_expr(ctx, node, depth)
    if isinstance(node, cst.Dict):
        return _infer_dict_expr(ctx, node, depth)
    if isinstance(node, (cst.ListComp, cst.SetComp, cst.DictComp, cst.GeneratorExp)):
        return _infer_comp_expr(ctx, node, depth)
    if isinstance(node, (cst.BinaryOperation, cst.UnaryOperation, cst.BooleanOperation)):
        return _infer_op_expr(ctx, node, depth)
    if isinstance(node, cst.Comparison):
        return "bool"
    if isinstance(node, cst.IfExp):
        return join_types(
            ctx,
            [
                infer_expr(ctx, node.body, depth + 1),
                infer_expr(ctx, node.orelse, depth + 1),
            ],
        )
    if isinstance(node, cst.NamedExpr):
        return infer_expr(ctx, node.value, depth + 1)
    if isinstance(node, cst.Await):
        return infer_expr(ctx, node.expression, depth + 1)
    if isinstance(node, cst.Subscript):
        return _infer_subscript_expr(ctx, node, depth)
    if isinstance(node, cst.Attribute) and dotted_name(node) in (
        "sys.stderr",
        "sys.stdout",
        "sys.stdin",
    ):
        return "TextIO"
    if isinstance(node, cst.Call):
        has_pos = any(a.keyword is None for a in node.args)
        has_df = any(a.keyword is not None and a.keyword.value == "default_factory" for a in node.args)
        if isinstance(node.func, cst.Name) and not has_pos and node.func.value in CONTAINER_CALLS and not has_df:
            return CONTAINER_CALLS[node.func.value]
        return infer_call(ctx, node, depth)
    if isinstance(node, cst.Attribute):
        return infer_attr(ctx, node, depth)
    if isinstance(node, cst.StarredElement):
        return infer_element(ctx, node)
    return ""


def _infer_dict_get_call(ctx: FileCtx, node: cst.Call) -> str:
    """Infer the return type of a ``dict.get(...)`` call from its default argument."""
    for arg in node.args:
        if arg.keyword is not None and arg.keyword.value == "default":
            default_t = infer_expr(ctx, arg.value)
            if default_t and "|" not in default_t:
                return default_t
    pos_args = [a for a in node.args if a.keyword is None]
    if len(pos_args) >= TWO_ARGS_COUNT:
        default_t = infer_expr(ctx, pos_args[1].value)
        if default_t and "|" not in default_t:
            return default_t
    return "Any"


def _infer_match_method(attr: str) -> str:
    """Infer the return type of a ``re.Match`` method call."""
    if attr in ("group", "expand"):
        return "str"
    if attr == "groupdict":
        return "Any"
    if attr == "groups":
        return "tuple[Any, ...]"
    if attr == "span":
        return "tuple[int, int]"
    if attr in ("start", "end"):
        return "int"
    return ""


def _infer_receiver_method(
    ctx: FileCtx,
    node: cst.Call,
    func: cst.Attribute,
    recv: str,
    attr: str,
) -> str:
    """Infer the return type of ``recv.attr(...)`` based on the receiver type."""
    if recv == "Path" and attr in METHOD_RETURNS:
        return METHOD_RETURNS[attr]
    if recv.startswith("str") and attr in METHOD_RETURNS:
        return METHOD_RETURNS[attr]
    if recv == "bytes" and attr in ("decode", "hex"):
        return "str"
    if recv.startswith("re.Match") or recv == "Any":
        match_ret = _infer_match_method(attr)
        if match_ret:
            return match_ret
    if recv.startswith("dict") and attr == "get":
        return _infer_dict_get_call(ctx, node)
    if isinstance(func.value, cst.Name) and func.value.value in ("self", "cls") and attr in ctx.class_method_returns:
        return ctx.class_method_returns[attr]
    if isinstance(func.value, cst.Name) and func.value.value in ctx.module_classes:
        cls_returns = ctx.class_method_returns_all.get(func.value.value, {})
        if attr in cls_returns:
            return cls_returns[attr]
    if (
        isinstance(func.value, cst.Attribute)
        and isinstance(func.value.value, cst.Name)
        and func.value.value.value == "self"
        and attr in ctx.method_returns
    ):
        return ctx.method_returns[attr]
    if attr in METHOD_RETURNS:
        return METHOD_RETURNS[attr]
    return ctx.class_method_returns_all.get(recv, {}).get(attr, "")


def infer_call(ctx: FileCtx, node: cst.Call, depth: int) -> str:
    """Infer the return type of a call from builtins, modules, and receivers."""
    func = node.func
    if isinstance(func, cst.Name):
        name = func.value
        if name == "Path":
            ctx.need_path = True
            return "Path"
        if name in BUILTIN_CALL_RETURNS:
            return BUILTIN_CALL_RETURNS[name]
        if name in ctx.module_classes:
            return name
        if name in ctx.param_types and "Callable" in ctx.param_types[name]:
            return "Any"
        return ""
    if not isinstance(func, cst.Attribute):
        return ""
    dotted = dotted_name(func)
    if dotted and dotted in ctx.module_consts:
        return ctx.module_consts[dotted]
    parts = dotted.split(".") if dotted else []
    for i in range(len(parts), 0, -1):
        key = tuple(parts[:i])
        if key in MODULE_CALL_RETURNS:
            return MODULE_CALL_RETURNS[key]
    attr = func.attr.value
    if "." in dotted:
        mod, attr_name = dotted.rsplit(".", 1)
        if (mod, attr_name) in MODULE_ATTR_RETURNS:
            return MODULE_ATTR_RETURNS[(mod, attr_name)]
        if mod in ("str", "Path") and attr_name in METHOD_RETURNS:
            return METHOD_RETURNS[attr_name]
    recv = infer_expr(ctx, func.value, depth + 1)
    return _infer_receiver_method(ctx, node, func, recv, attr)


def infer_attr(ctx: FileCtx, node: cst.Attribute, depth: int) -> str:
    """Infer the type of a plain attribute access."""
    dotted = dotted_name(node)
    if dotted in ctx.module_consts:
        return ctx.module_consts[dotted]
    if dotted in (
        "os.sep",
        "os.linesep",
        "os.pathsep",
        "sys.platform",
        "sys.executable",
        "sys.version",
    ):
        return "str"
    if dotted == "sys.argv":
        return "list[str]"
    recv = infer_expr(ctx, node.value, depth + 1)
    attr = node.attr.value
    if recv == "Path":
        if attr == "parent":
            return "Path"
        if attr in ("name", "stem", "suffix", "anchor", "drive"):
            return "str"
        if attr == "suffixes":
            return "list[str]"
        if attr == "parts":
            return "tuple[str, ...]"
    if recv == "subprocess.CompletedProcess[str]":
        if attr == "returncode":
            return "int"
        if attr in ("stdout", "stderr"):
            return "str"
    if isinstance(node.value, cst.Name) and node.value.value in ("self", "cls"):
        return ctx.class_attr_types.get(attr, "")
    return ""


def iterable_element(typ: str) -> str:
    """Extract the element type from an iterable annotation, or ``""``."""
    m = _ITERABLE_RE.fullmatch(typ)
    if m is None:
        return ""
    inner = str(m[1])
    if inner.startswith(("tuple[", "dict[")):
        return ""
    first = inner.split(",", maxsplit=1)[0].strip()
    if not first or first == "Any" or "|" in first:
        return ""
    return first


def infer_element(ctx: FileCtx, node: cst.CSTNode | None) -> str:
    """Infer the type of an expression used as an iterable element."""
    if node is None:
        return ""
    if isinstance(node, cst.StarredElement):
        return iterable_element(infer_expr(ctx, node.value)) or infer_expr(ctx, node.value)
    typ = infer_expr(ctx, node)
    return iterable_element(typ) or typ


class Evidence:
    """Weighted hard and soft evidence gathered about one parameter's usage."""

    def __init__(self) -> None:
        """Initialize empty evidence counters."""
        self.counts: dict[str, int] = {}
        self.hard: dict[str, int] = {}

    def add(self, typ: str, weight: int = 1, *, hard: bool = False) -> None:
        """Record weighted evidence for candidate type ``typ``."""
        if not typ:
            return
        self.counts[typ] = self.counts.get(typ, 0) + weight
        if hard:
            self.hard[typ] = self.hard.get(typ, 0) + weight

    def score(self, typ: str) -> int:
        """Return total accumulated weight for candidate type ``typ``."""
        return self.counts.get(typ, 0)

    def signals(self) -> set[str]:
        """Return candidate type names with positive evidence excluding ``Any``."""
        return {k for k, v in self.counts.items() if v > 0 and k != "Any"}


def collect_call_evidence(ev: Evidence, node: cst.Call, param: str) -> None:
    """Record evidence from a call consuming parameter ``param``."""
    func = node.func
    positional = any(arg.keyword is None and is_param(arg.value, param) for arg in node.args)
    keywords = {arg.keyword.value for arg in node.args if arg.keyword is not None and is_param(arg.value, param)}
    if not positional and not keywords:
        return
    if isinstance(func, cst.Name):
        fn = func.value
        if fn == param:
            ev.add("Callable", HIGH_EVIDENCE_SCORE, hard=True)
            return
        if not positional:
            return
        hit = CALL_EVIDENCE.get(fn)
        if hit is not None:
            typ, weight, hard = hit
            ev.add(typ, weight, hard=hard)
        elif fn in SEQUENCE_CALLS:
            ev.add("Sequence", 3)
        return
    if not isinstance(func, cst.Attribute):
        return
    dotted = dotted_name(func)
    if dotted in COMMAND_PASS_CALLS:
        pos_args = [a for a in node.args if a.keyword is None]
        first = pos_args[0].value if pos_args else None
        if is_param(first, param) or "args" in keywords:
            ev.add("Command", 8, hard=True)
        return
    if dotted in STR_PASS_CALLS:
        ev.add("str", CONFLICT_EVIDENCE_SCORE, hard=True)
        return
    if dotted in FILE_PASS_CALLS:
        ev.add("FileLike", 8, hard=True)
        return
    if dotted in PATH_PASS_CALLS:
        ev.add("Path", 5, hard=True)
        ev.add("str", 2)
        return
    attr = func.attr.value
    if attr == "decode" and is_param(func.value, param):
        ev.add("bytes", HIGH_EVIDENCE_SCORE, hard=True)
    elif attr == "encode" and is_param(func.value, param):
        ev.add("str", HIGH_EVIDENCE_SCORE, hard=True)
    elif attr in PATH_CALL_ATTRS and not node.args and is_param(func.value, param):
        ev.add("Path", HIGH_EVIDENCE_SCORE, hard=True)
    elif positional and attr in ("open", "Path"):
        ev.add("Path", CONFLICT_EVIDENCE_SCORE, hard=True)


class _LoopAttrScanner(cst.CSTVisitor):
    """Scan a loop body for attribute accesses on loop target variables."""

    def __init__(self, target_names: set[str], ev: Evidence) -> None:
        self._target_names = target_names
        self._ev = ev

    @override
    def visit_Attribute(self, node: cst.Attribute) -> bool:
        if isinstance(node.value, cst.Name) and node.value.value in self._target_names:
            attr = node.attr.value
            if attr in PATH_ATTRS:
                self._ev.add("PathElem", 3)
            elif attr in STR_ATTRS:
                self._ev.add("StrElem", 3)
        return True


class _ParamEvidenceVisitor(cst.CSTVisitor):
    """Walk a function body to gather type evidence for one parameter."""

    def __init__(self, param: str, ev: Evidence) -> None:
        self._param = param
        self._ev = ev

    @override
    def visit_Lambda(self, node: cst.Lambda) -> bool:
        del node
        return False

    @override
    def visit_Annotation(self, node: cst.Annotation) -> bool:
        del node
        return False

    @override
    def visit_Attribute(self, node: cst.Attribute) -> bool:
        if is_param(node.value, self._param):
            attr = node.attr.value
            for table, typ, hard in ATTR_EVIDENCE:
                weight = table.get(attr)
                if weight is not None and weight > self._ev.hard.get(typ, 0):
                    self._ev.add(typ, weight, hard=hard)
        return True

    @override
    def visit_Call(self, node: cst.Call) -> bool:
        collect_call_evidence(self._ev, node, self._param)
        if (
            isinstance(node.func, cst.Name)
            and node.func.value in ITER_CALLS
            and node.args
            and node.args[0].keyword is None
            and is_param(node.args[0].value, self._param)
        ):
            self._ev.add("Iterable", 5, hard=True)
        return True

    @override
    def visit_Comparison(self, node: cst.Comparison) -> bool:
        operands = [node.left, *(c.comparator for c in node.comparisons)]
        for i, operand in enumerate(operands):
            if not is_param(operand, self._param):
                continue
            for j, other in enumerate(operands):
                if j == i:
                    continue
                t = literal_type(other)
                if t and t != "bool":
                    self._ev.add(t, 3)
        return True

    @override
    def visit_BinaryOperation(self, node: cst.BinaryOperation) -> bool:
        for side, other in ((node.left, node.right), (node.right, node.left)):
            if is_param(side, self._param):
                t = literal_type(other)
                if t in ("int", "str", "float"):
                    self._ev.add(t, 4)
        return True

    @override
    def visit_For(self, node: cst.For) -> bool:
        if is_param(node.iter, self._param):
            self._ev.add("Iterable", 4, hard=True)
            target_names = _collect_target_names(node.target)
            node.body.visit(_LoopAttrScanner(target_names, self._ev))
        return True

    @override
    def visit_CompFor(self, node: cst.CompFor) -> bool:
        if is_param(node.iter, self._param):
            self._ev.add("Iterable", 4, hard=True)
        return True

    @override
    def visit_Subscript(self, node: cst.Subscript) -> bool:
        if not is_param(node.value, self._param) or len(node.slice) != 1:
            return True
        elem = node.slice[0].slice
        if isinstance(elem, cst.Index):
            lit = literal_type(elem.value)
            if lit == "str":
                self._ev.add("Mapping", 5, hard=True)
                self._ev.add("dict", 5, hard=True)
            elif lit:
                self._ev.add("Sequence", 4)
        else:
            self._ev.add("Sequence", 4)
        return True

    @override
    def visit_FormattedStringExpression(self, node: cst.FormattedStringExpression) -> bool:
        if is_param(node.expression, self._param):
            self._ev.add("str", 3)
        return True

    @override
    def visit_Assign(self, node: cst.Assign) -> bool:
        for tgt in node.targets:
            if is_param(tgt.target, self._param):
                self._ev.add("Any", CONFLICT_EVIDENCE_SCORE)
        return True


def collect_param_evidence(func: cst.FunctionDef, param: str) -> Evidence:
    """Walk one function body and collect usage evidence for ``param``."""
    ev = Evidence()
    func.body.visit(_ParamEvidenceVisitor(param, ev))
    return ev


def evidence_to_type(ev: Evidence, ctx: FileCtx) -> str:
    """Resolve collected usage evidence into a concrete annotation string."""
    counts = {k: v for k, v in ev.counts.items() if k != "Any"}
    hard = {k: v for k, v in ev.hard.items() if k != "Any"}
    pool = hard or counts
    if not pool:
        return ""
    best = min(
        pool.items(),
        key=lambda kv: (
            -kv[1],
            PRIORITIES.index(kv[0]) if kv[0] in PRIORITIES else FALLBACK_PRIORITY,
        ),
    )[0]
    typ = EVIDENCE_TYPES.get(best, best)
    if typ == "Path" and not ctx.has_name("Path"):
        return "str"
    if typ == "Iterable[Any]" and ev.score("PathElem"):
        return "Sequence[Path]" if ctx.has_name("Path") else "Sequence[str]"
    return typ


def _name_path_hint(ctx: FileCtx, lname: str) -> str:
    """Return a Path/str type guess when ``lname`` resembles a path identifier."""
    if lname in (
        "paths",
        "dirs",
        "files",
        "folders",
        "repos",
        "archives",
        "sources",
        "destinations",
    ):
        return "list[Path]" if ctx.has_name("Path") else "list[str]"
    if lname in (
        "filename",
        "file_name",
        "fname",
        "basename",
        "dirname",
        "path_str",
        "str_path",
        "root_dir",
        "trash_dir",
        "dest_str",
        "src_str",
        "target_str",
    ):
        return "Path" if lname.endswith("_dir") and ctx.has_name("Path") else "str"
    if lname in PATH_LIKE_NAMES or lname.endswith(("_path", "_file", "_folder", "_dir")):
        return "Path" if ctx.has_name("Path") else "str"
    return ""


def name_based_type(ctx: FileCtx, name: str) -> str:
    """Guess a parameter annotation from conventional identifier naming."""
    lname = name.lower()
    if lname in EXPR_NAME_HINTS and ctx.has_name("ast"):
        return EXPR_NAME_HINTS[lname]
    if lname in ("node", "original_node", "updated_node") and ctx.has_name("cst"):
        return "cst.CSTNode"
    if lname == "tree" and ctx.has_name("cst"):
        return "cst.Module"
    if lname in ANY_OBJECT_NAMES:
        return "Any"
    if lname in ITERABLE_NAMES:
        return "Iterable[Any]"
    if lname.endswith(BYTES_SUFFIXES) or lname in BYTES_NAMES:
        return "bytes"
    path_guess = _name_path_hint(ctx, lname)
    if path_guess:
        return path_guess
    if lname in CONTAINER_NAMES:
        base = CONTAINER_NAMES[lname]
        if base == "list":
            return "list[str]" if lname == "argv" else "list[Any]"
        if base == "set":
            return "set[str]"
        return "dict[str, Any]"
    if lname in ("args", "argv", "arguments", "cli_args", "extra_args"):
        return "list[str]" if lname == "argv" else "list[Any]"
    if lname in NAME_HINTS:
        return NAME_HINTS[lname]
    if lname in (
        "pypi_index",
        "index",
        "cache",
        "lookup",
        "table",
        "registry",
        "packages",
        "imports",
        "deps",
    ):
        return "dict[str, Any]"
    if lname.endswith(STR_SUFFIXES):
        return "str"
    if lname.endswith(INT_SUFFIXES):
        return "int"
    if lname.endswith(BOOL_SUFFIXES) or lname.startswith(BOOL_PREFIXES):
        return "bool"
    if lname in ("signum", "sig", "_signum", "signal_num", "signo"):
        return "int"
    if lname in ("cls", "klass", "kls"):
        return "Any"
    if lname in CALLABLE_HINTS:
        return "Callable[..., Any]"
    if lname.startswith(("list_", "all_")):
        return "list[Any]"
    if lname.startswith(("dict_", "map_")):
        return "dict[str, Any]"
    return ""


class _PercentFormatChecker(cst.CSTVisitor):
    """Detect ``param % (...)`` old-style string formatting inside a function."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.found = False

    @override
    def visit_BinaryOperation(self, node: cst.BinaryOperation) -> bool:
        if self.found:
            return False
        if (
            isinstance(node.operator, cst.Modulo)
            and is_param(node.left, self._name)
            and isinstance(node.right, (cst.Tuple, cst.Dict, cst.List, cst.Name))
        ):
            self.found = True
            return False
        return True


def param_message_percent(func: cst.FunctionDef, name: str) -> bool:
    """Return True when ``name`` is formatted via the ``%`` operator."""
    checker = _PercentFormatChecker(name)
    func.body.visit(checker)
    return checker.found


def infer_default(ctx: FileCtx, default: cst.BaseExpression | None) -> str:
    """Infer an annotation from a parameter's default expression."""
    if default is None:
        return ""
    lit = literal_type(default)
    if lit:
        return "" if lit == "None" else lit
    if isinstance(default, cst.UnaryOperation) and isinstance(default.operator, (cst.Minus, cst.Plus)):
        return infer_expr(ctx, default.expression)
    if isinstance(default, cst.Name):
        return ctx.module_consts.get(default.value, "")
    if isinstance(default, cst.Attribute):
        return infer_attr(ctx, default, 0)
    if isinstance(default, (cst.Tuple, cst.List, cst.Set)):
        return infer_expr(ctx, default)
    if isinstance(default, cst.Call) and isinstance(default.func, cst.Name):
        fname = default.func.value
        if not default.args:
            if fname in ("list", "set"):
                return f"{fname}[Any]"
            if fname == "dict":
                return "dict[str, Any]"
            if fname == "tuple":
                return "tuple[Any, ...]"
        if fname == "Path":
            return "Path"
    return infer_expr(ctx, default)


def is_override(func: cst.FunctionDef) -> bool:
    """Return True when ``func`` carries an ``@override`` decorator."""
    return any(
        (isinstance(d.decorator, cst.Name) and d.decorator.value == "override")
        or (isinstance(d.decorator, cst.Attribute) and d.decorator.attr.value == "override")
        for d in func.decorators
    )


def _visitor_kind(func: cst.FunctionDef) -> str:
    """Extract the CST/AST node kind suffix from ``visit_*`` or ``leave_*``."""
    name = func.name.value
    for prefix in ("visit_", "leave_"):
        if name.startswith(prefix) and len(name) > len(prefix):
            kind = name[len(prefix) :]
            if kind[:1].isupper():
                return kind
    return ""


class _TsNodeChecker(cst.CSTVisitor):
    """Compare tree-sitter vs stdlib AST attribute accesses on one parameter."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.ts_score = 0
        self.ast_score = 0

    @override
    def visit_Attribute(self, node: cst.Attribute) -> bool:
        if isinstance(node.value, cst.Name) and node.value.value == self._name:
            if node.attr.value in TS_ATTRS:
                self.ts_score += 1
            elif node.attr.value in AST_ATTRS:
                self.ast_score += 1
        return True


def looks_like_ts_node(func: cst.FunctionDef, name: str) -> bool:
    """Return True when ``name`` mainly accesses tree-sitter node attributes."""
    checker = _TsNodeChecker(name)
    func.body.visit(checker)
    return checker.ts_score > checker.ast_score


def infer_param_type(
    ctx: FileCtx,
    func: cst.FunctionDef,
    param: cst.Param,
    default: cst.BaseExpression | None,
    default_repr: str,
    *,
    is_first: bool,
    in_class: bool,
) -> str:
    """Infer an annotation string for one parameter, or ``""`` to leave unannotated."""
    name = param.name.value
    if name in ("self", "cls", "mcs") and (in_class or is_first):
        return ""
    if in_class and name in ("original_node", "updated_node", "node") and ctx.has_name("cst"):
        kind = _visitor_kind(func)
        if kind and (name != "node" or ctx.class_bases):
            return f"cst.{kind}"
        return "cst.CSTNode"
    if in_class and name == "node" and any("NodeVisitor" in b or "NodeTransformer" in b for b in ctx.class_bases):
        kind = _visitor_kind(func) or "AST"
        return f"ast.{kind}" if ctx.has_name("ast") else ""
    if name in ("node", "root_node", "child", "parent_node") and looks_like_ts_node(func, name):
        return "Any"
    if default_repr in (VARARG, KWARG):
        if name.startswith("_"):
            return "Any"
        typ = evidence_to_type(collect_param_evidence(func, name), ctx)
        return typ if typ in ("str", "int", "float", "bool", "bytes") else "Any"
    if is_override(func):
        return ""
    if param_message_percent(func, name):
        return "Any"
    ev = collect_param_evidence(func, name)
    default_type = infer_default(ctx, default) if default is not None else ""
    ev_type = evidence_to_type(ev, ctx)
    none_default = isinstance(default, cst.Name) and default.value == "None"
    container_default = (
        default_type
        if default_type
        in (
            "list[Any]",
            "dict[str, Any]",
            "set[Any]",
            "tuple[Any, ...]",
            "frozenset[Any]",
            "dict[Any, Any]",
        )
        else ""
    )
    if default_type.startswith("dict[") and default_type != "dict[str, Any]":
        default_type = ""
    if default_type:
        if default_type == "str" and ev.score("Path") >= HIGH_EVIDENCE_SCORE:
            return ctx.request("Path")
        if default_type in ("str", "int", "float", "bytes") and ev_type and ev_type != default_type:
            return ctx.request(_union_none(ctx, ev_type) if none_default else ev_type)
        if default_type == "bool":
            if ev.score("str") >= HIGH_EVIDENCE_SCORE and ev.score("int") >= HIGH_EVIDENCE_SCORE:
                return "Any"
            return "bool"
        if default_type != "Any":
            return ctx.request(default_type)
    named = name_based_type(ctx, name)
    if named and len(name) <= SHORT_NAME_MAX_LEN and ev.hard:
        hard_type = evidence_to_type(ev, ctx)
        if hard_type and hard_type != "Any":
            named = ""
    if named:
        if none_default and named != "Any":
            return ctx.request(_union_none(ctx, named))
        return ctx.request(named)
    if ev_type:
        if ev_type == "dict[str, Any]" and CONTAINER_NAMES.get(name) == "list":
            return "list[Any]"
        if len(ev.signals()) >= MIN_CONFLICT_SIGNALS and (
            (ev.score("Path") >= CONFLICT_EVIDENCE_SCORE and ev.score("list") >= CONFLICT_EVIDENCE_SCORE)
            or (ev.score("str") >= CONFLICT_EVIDENCE_SCORE and ev.score("list") >= CONFLICT_EVIDENCE_SCORE)
            or (ev.score("dict") >= CONFLICT_EVIDENCE_SCORE and ev.score("list") >= CONFLICT_EVIDENCE_SCORE)
            or (ev.score("TextIO") >= CONFLICT_EVIDENCE_SCORE and ev.score("list") >= CONFLICT_EVIDENCE_SCORE)
        ):
            return "Any"
        if none_default:
            return ctx.request(_union_none(ctx, ev_type))
        return ctx.request(ev_type)
    if container_default:
        return ctx.request(container_default)
    if none_default and name in ("timezone", "tz"):
        return _union_none(ctx, "str")
    if name.startswith("_"):
        return ""
    return "Any"


class ReturnCollector(cst.CSTVisitor):
    """Collect ``return`` and ``yield`` nodes without entering nested scopes."""

    def __init__(self) -> None:
        """Initialize return and yield collection containers."""
        self.returns: list[cst.Return] = []
        self.yields: list[cst.Yield] = []
        self.has_yield = False

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        """Skip nested function definitions."""
        del node
        return False

    @override
    def visit_Lambda(self, node: cst.Lambda) -> bool:
        """Skip nested lambda expressions."""
        del node
        return False

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        """Skip nested class definitions."""
        del node
        return False

    @override
    def visit_Return(self, node: cst.Return) -> bool:
        """Record a direct return statement."""
        self.returns.append(node)
        return True

    @override
    def visit_Yield(self, node: cst.Yield) -> bool:
        """Record a direct yield expression."""
        self.has_yield = True
        self.yields.append(node)
        return True


def _is_exit_call(node: cst.CSTNode) -> bool:
    """Return True for calls that terminate execution without returning."""
    if not isinstance(node, cst.Call):
        return False
    dotted = dotted_name(node.func)
    return dotted in (
        "sys.exit",
        "os._exit",
        "parser.error",
        "ap.error",
    ) or dotted.endswith((".exit", ".error"))


class _BreakFinder(cst.CSTVisitor):
    """Detect whether a loop body contains a direct ``break`` statement."""

    def __init__(self) -> None:
        self.found = False

    @override
    def visit_For(self, node: cst.For) -> bool:
        del node
        return False

    @override
    def visit_While(self, node: cst.While) -> bool:
        del node
        return False

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        del node
        return False

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        del node
        return False

    @override
    def visit_Break(self, node: cst.Break) -> bool:
        del node
        self.found = True
        return False


def _contains_break(loop: cst.While) -> bool:
    """Return True when ``loop.body`` contains a direct ``break`` statement."""
    finder = _BreakFinder()
    loop.body.visit(finder)
    return finder.found


def small_stmt_falls_through(stmt: cst.BaseSmallStatement) -> bool:
    """Return True when a small statement can complete normally."""
    if isinstance(stmt, (cst.Return, cst.Raise)):
        return False
    return not (isinstance(stmt, cst.Expr) and _is_exit_call(stmt.value))


def stmt_falls_through(node: cst.CSTNode) -> bool:
    """Return True when control flow can reach the end of ``node`` normally."""
    if isinstance(node, cst.SimpleStatementLine):
        if not node.body:
            return True
        return small_stmt_falls_through(node.body[-1])
    if isinstance(node, cst.BaseSmallStatement):
        return small_stmt_falls_through(node)
    if isinstance(node, cst.If):
        if node.orelse is None:
            return True
        if isinstance(node.orelse, cst.Else):
            return suite_falls_through(node.body) or suite_falls_through(node.orelse.body)
        return suite_falls_through(node.body) or stmt_falls_through(node.orelse)
    if isinstance(node, cst.While):
        return not (isinstance(node.test, cst.Name) and node.test.value == "True" and not _contains_break(node))
    if isinstance(node, cst.With):
        return suite_falls_through(node.body)
    if isinstance(node, (cst.Try, cst.TryStar)):
        if node.finalbody is not None and not suite_falls_through(node.finalbody.body):
            return False
        ok = suite_falls_through(node.body)
        if node.handlers:
            ok = ok or any(suite_falls_through(h.body) for h in node.handlers)
        else:
            ok = True
        if node.orelse is not None:
            ok = ok or suite_falls_through(node.orelse.body)
        return ok
    if isinstance(node, cst.Match):
        return any(suite_falls_through(c.body) for c in node.cases) or not node.cases
    return True


def suite_falls_through(suite: cst.BaseSuite) -> bool:
    """Return True when a suite can finish without returning or raising."""
    stmts = suite_stmts(suite)
    if not stmts:
        return True
    return stmt_falls_through(stmts[-1])


def _is_raise_stmt(node: cst.CSTNode) -> bool:
    """Return True when ``node`` is or ends with a ``raise`` statement."""
    if isinstance(node, cst.Raise):
        return True
    return isinstance(node, cst.SimpleStatementLine) and bool(node.body) and isinstance(node.body[-1], cst.Raise)


def always_raises(suite: cst.BaseSuite) -> bool:
    """Return True when a suite unconditionally raises an exception."""
    stmts = suite_stmts(suite)
    if not stmts or len(stmts) > ALWAYS_RAISES_MAX_STMTS:
        return False
    last = stmts[-1]
    if _is_raise_stmt(last):
        return True
    if isinstance(last, cst.If):
        if isinstance(last.orelse, cst.Else):
            return always_raises(last.body) and always_raises(last.orelse.body)
        if isinstance(last.orelse, cst.If):
            return always_raises(last.body) and always_raises_stmt(last.orelse)
        return False
    if isinstance(last, cst.With):
        with_stmts = suite_stmts(last.body)
        return bool(with_stmts) and _is_raise_stmt(with_stmts[-1])
    return False


def always_raises_stmt(stmt: cst.If) -> bool:
    """Return True when an ``elif`` chain unconditionally raises."""
    if isinstance(stmt.orelse, cst.Else):
        return always_raises(stmt.body) and always_raises(stmt.orelse.body)
    if isinstance(stmt.orelse, cst.If):
        return always_raises(stmt.body) and always_raises_stmt(stmt.orelse)
    return False


def base_return_type(bases: list[str], name: str) -> str:
    """Return known framework callback return annotations for subclass methods."""
    for base in bases:
        if (base, name) in BASE_METHOD_RETURNS:
            return BASE_METHOD_RETURNS[(base, name)]
        if base.endswith(("NodeTransformer", "NodeVisitor")) and name.startswith("visit_"):
            return ""
        if base.endswith("CSTTransformer") and name.startswith("leave_"):
            return "cst.CSTNode"
        if base.endswith(("CSTTransformer", "CSTVisitor")) and name.startswith("visit_"):
            return "bool | None"
        if base.endswith("FileSystemEventHandler") and name.startswith(("on_", "on_any_event")):
            return "None"
    return ""


class _NestedFuncFinder(cst.CSTVisitor):
    """Find whether a nested function with a given name exists inside a statement."""

    def __init__(self, target_name: str) -> None:
        self._target_name = target_name
        self.found = False

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        if node.name.value == self._target_name:
            self.found = True
            return False
        return True


def infer_factory_return(func: cst.FunctionDef) -> str:
    """Detect decorator-factory functions that define and return an inner function."""
    stmts = suite_stmts(func.body)
    if len(stmts) < MIN_FACTORY_BODY_STMTS:
        return ""
    last = stmts[-1]
    ret: cst.Return | None = None
    if isinstance(last, cst.Return):
        ret = last
    elif isinstance(last, cst.SimpleStatementLine) and last.body and isinstance(last.body[-1], cst.Return):
        ret = last.body[-1]
    if ret is None or not isinstance(ret.value, cst.Name):
        return ""
    name = ret.value.value
    finder = _NestedFuncFinder(name)
    for stmt in stmts[:-1]:
        stmt.visit(finder)
        if finder.found:
            return "Callable[..., Any]"
    return ""


def _yield_inner(ctx: FileCtx, yields: list[cst.Yield]) -> str:
    """Infer the element type yielded by a generator function."""
    inner_types: list[str] = []
    for y in yields:
        if y.value is None:
            continue
        if isinstance(y.value, cst.From):
            t = infer_expr(ctx, y.value.item)
            m = _YIELD_FROM_CONTAINER_RE.fullmatch(t)
            if m is not None:
                inner_types.append(str(m[1]))
        else:
            inner_types.append(infer_element(ctx, y.value))
    return join_types(ctx, [t for t in inner_types if t])


def _shaped_tuple(ctx: FileCtx, returns: list[cst.Return]) -> str:
    """Fallback for partially inferred returns: keep tuple shape with ``Any`` slots."""
    non_empty = [r for r in returns if r.value is not None]
    shaped: list[cst.Tuple | cst.List] = [r.value for r in non_empty if isinstance(r.value, (cst.Tuple, cst.List))]
    if not shaped or len(shaped) != len(non_empty):
        return ""
    arities = {len(seq.elements) for seq in shaped}
    if len(arities) != 1:
        return "tuple[Any, ...]" if arities else ""
    arity_n = next(iter(arities))
    if arity_n == 1:
        return "tuple[Any, ...]"
    if arity_n >= TWO_ARGS_COUNT:
        if any(isinstance(el, cst.StarredElement) for el in shaped[0].elements):
            return "tuple[Any, ...]"
        members = [infer_expr(ctx, elt.value) or "Any" for elt in shaped[0].elements if isinstance(elt, cst.Element)]
        return "tuple[" + ", ".join(members) + "]"
    return ""


def infer_return_type(ctx: FileCtx, func: cst.FunctionDef, *, in_class: bool) -> str:
    """Infer a return annotation for ``func``, or ``""`` to leave unannotated."""
    name = func.name.value
    if in_class and ctx.class_bases:
        bt = base_return_type(ctx.class_bases, name)
        if bt:
            return bt
    factory = infer_factory_return(func)
    if factory:
        return factory
    if in_class and name in DUNDER_RETURNS:
        return DUNDER_RETURNS[name]
    rc = ReturnCollector()
    func.body.visit(rc)
    if rc.has_yield:
        inner = _yield_inner(ctx, rc.yields)
        if inner == "tuple[Any]":
            inner = "tuple[Any, ...]"
        if not inner or inner == "Any" or "|" in inner:
            return ""
        return ctx.request(f"Iterator[{inner}]")
    if not rc.returns:
        return "NoReturn" if always_raises(func.body) else "None"
    inferred: list[str] = []
    unknowns = 0
    for r in rc.returns:
        if r.value is None:
            inferred.append("None")
            continue
        t = infer_expr(ctx, r.value)
        if t:
            inferred.append(t)
        else:
            unknowns += 1
    if unknowns:
        known = {t for t in inferred if t != "None"}
        if len(known) == 1 and not ({"list[Any]", "dict[str, Any]"} & known):
            return ""
        return _shaped_tuple(ctx, rc.returns)
    if "None" in inferred and len(inferred) > 1:
        non_none = [t for t in inferred if t != "None"]
        if len(set(non_none)) >= TWO_ARGS_COUNT and not join_types(ctx, non_none).startswith("tuple["):
            return ""
    typ = join_types(ctx, inferred)
    if typ == "tuple[Any]":
        typ = "tuple[Any, ...]"
    if not typ or typ in ANY_ONLY:
        return ""
    if typ == "bool" and len(rc.returns) > 1:
        flagged = [r for r in rc.returns if r.value is not None and infer_expr(ctx, r.value) == "bool"]
        if len(flagged) < len(rc.returns):
            return ""
    if suite_falls_through(func.body) and typ != "None":
        typ = join_types(ctx, [typ, "None"])
    return typ


def param_annotations(
    ctx: FileCtx,
    func: cst.FunctionDef,
    *,
    in_class: bool,
) -> dict[str, str]:
    """Infer annotations for all unannotated parameters of ``func``."""
    types_for_params: dict[str, str] = {}
    seen: set[str] = set()
    for param, kind in iter_params(func.params):
        pname = param.name.value
        if pname in seen:
            continue
        seen.add(pname)
        if param.annotation is not None:
            continue
        is_first = len(seen) == 1 and kind == "pos"
        dflt = None if kind in ("vararg", "kwarg") else param.default
        dflt_repr = VARARG if kind == "vararg" else (KWARG if kind == "kwarg" else "")
        typ = infer_param_type(
            ctx,
            func,
            param,
            dflt,
            dflt_repr,
            is_first=is_first,
            in_class=in_class,
        )
        if typ:
            prefix = "*" if kind == "vararg" else ("**" if kind == "kwarg" else "")
            types_for_params[prefix + pname] = typ
    return types_for_params


def _base_is_risky(ctx: FileCtx, base: cst.Arg) -> bool:
    """Return True when a base class manages its own attributes."""
    name = node_code(ctx.tree, base.value)
    return any(r in name for r in RISKY_BASES)


class _SelfStoreCounter(cst.CSTVisitor):
    """Count ``self.x = ...`` assignments inside ``__init__``."""

    def __init__(self, params: set[str]) -> None:
        self._params = params
        self.stores = 0
        self.param_stores = 0

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        del node
        return False

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        del node
        return False

    @override
    def visit_Assign(self, node: cst.Assign) -> bool:
        for tgt in node.targets:
            target = tgt.target
            if (
                isinstance(target, cst.Attribute)
                and isinstance(target.value, cst.Name)
                and target.value.value == "self"
            ):
                self.stores += 1
                if isinstance(node.value, cst.Name) and node.value.value in self._params:
                    self.param_stores += 1
        return True


def ctor_heavy(ctx: FileCtx, cls_node: cst.ClassDef | None) -> bool:
    """Return True if ``__init__`` mainly stores constructor params on ``self``."""
    if cls_node is None or any(_base_is_risky(ctx, b) for b in cls_node.bases):
        return False
    init: cst.FunctionDef | None = None
    for stmt in suite_stmts(cls_node.body):
        if isinstance(stmt, cst.FunctionDef) and stmt.name.value == "__init__":
            init = stmt
            break
    if init is None:
        return False
    params = {
        p.name.value
        for p in (
            *init.params.posonly_params,
            *init.params.params,
            *init.params.kwonly_params,
        )
    }
    counter = _SelfStoreCounter(params)
    init.body.visit(counter)
    return counter.stores > 0 and counter.param_stores / counter.stores >= MIN_CTOR_STORE_RATIO


class _ClassAttrCollector(cst.CSTVisitor):
    """Collect attribute names already assigned or annotated inside a class."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    @override
    def visit_Assign(self, node: cst.Assign) -> bool:
        for tgt in node.targets:
            target = tgt.target
            if isinstance(target, cst.Name):
                self.names.add(target.value)
            elif isinstance(target, cst.Attribute):
                self.names.add(target.attr.value)
        return True

    @override
    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        target = node.target
        if isinstance(target, cst.Name):
            self.names.add(target.value)
        elif isinstance(target, cst.Attribute):
            self.names.add(target.attr.value)
        return True


def class_annotated_attrs(cls_node: cst.ClassDef | None) -> set[str]:
    """Return attribute names already assigned or annotated anywhere in ``cls_node``."""
    if cls_node is None:
        return set()
    collector = _ClassAttrCollector()
    cls_node.body.visit(collector)
    return collector.names


def attr_assign_type(ctx: FileCtx, node: cst.Assign, annotated: set[str]) -> tuple[str, str] | None:
    """Infer ``(attr_name, type_str)`` for a ``self.x = value`` assignment."""
    if len(node.targets) != 1:
        return None
    tgt = node.targets[0].target
    if not isinstance(tgt, cst.Attribute) or not isinstance(tgt.value, cst.Name) or tgt.value.value != "self":
        return None
    if tgt.lpar or tgt.rpar or "\n" in node_code(ctx.tree, tgt):
        return None
    attr = tgt.attr.value
    if attr in annotated:
        return None
    typ = infer_expr(ctx, node.value)
    if not typ or typ == "None" or "|" in typ:
        return None
    annotated.add(attr)
    return attr, ctx.request(typ)


def module_assign_type(ctx: FileCtx, node: cst.Assign) -> str:
    """Infer the annotation for a top-level module assignment, or ``""``."""
    if len(node.targets) != 1:
        return ""
    tgt = node.targets[0].target
    if not isinstance(tgt, cst.Name) or tgt.lpar or tgt.rpar:
        return ""
    name = tgt.value
    if name.startswith("__") and name.endswith("__") and name not in DUNDER_CONST_OK:
        return ""
    typ = infer_expr(ctx, node.value)
    if not typ or typ == "None" or ("|" in typ and ctx.style != "pep604"):
        return ""
    if isinstance(node.value, cst.Call) and not (
        isinstance(node.value.func, cst.Name) and node.value.func.value == "Path" and ctx.has_name("Path")
    ):
        return ""
    return "tuple[Any, ...]" if typ == "tuple[Any]" else typ


class _LocalTypesCollector(cst.CSTVisitor):
    """Collect inferred types of local variables inside one function body."""

    def __init__(self, ctx: FileCtx) -> None:
        self._ctx = ctx
        self.out: dict[str, str] = {}

    @override
    def visit_Assign(self, node: cst.Assign) -> bool:
        typ = infer_expr(self._ctx, node.value)
        if not typ or typ == "Any":
            return True
        for tgt in node.targets:
            if not isinstance(tgt.target, cst.Name):
                continue
            name = tgt.target.value
            prev = self.out.get(name)
            if prev is None:
                self.out[name] = typ
            elif prev != typ:
                merged = merge_generics(prev, typ)
                if merged is not None:
                    self.out[name] = merged
                elif {prev, typ} <= NUMERIC:
                    self.out[name] = "float"
                else:
                    self.out[name] = "Any"
        return True

    @override
    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        if isinstance(node.target, cst.Name):
            self.out[node.target.value] = node_code(self._ctx.tree, node.annotation.annotation)
        return True

    @override
    def visit_For(self, node: cst.For) -> bool:
        elem = iterable_element(infer_expr(self._ctx, node.iter))
        if elem:
            for name in _collect_target_names(node.target):
                self.out.setdefault(name, elem)
        return True

    @override
    def visit_With(self, node: cst.With) -> bool:
        for item in node.items:
            if item.asname is None:
                continue
            typ = infer_with_item(self._ctx, item.item)
            if typ:
                for name in _collect_target_names(item.asname.name):
                    self.out.setdefault(name, typ)
        return True


def collect_local_types(ctx: FileCtx, func: cst.FunctionDef) -> dict[str, str]:
    """Infer types of simple local variables inside ``func``."""
    collector = _LocalTypesCollector(ctx)
    func.body.visit(collector)
    return collector.out


def infer_with_item(ctx: FileCtx, expr: cst.BaseExpression) -> str:
    """Infer the context-manager variable type bound in a ``with`` statement."""
    if isinstance(expr, cst.Call):
        if isinstance(expr.func, cst.Name) and expr.func.value == "open":
            return "TextIO"
        if isinstance(expr.func, cst.Attribute) and expr.func.attr.value == "open":
            return "TextIO"
    typ = infer_expr(ctx, expr)
    return "" if typ.startswith(("list", "dict", "set", "tuple")) else typ


class _LocalReturnsCollector(cst.CSTVisitor):
    """Collect return types of nested functions inside an enclosing function."""

    def __init__(self, ctx: FileCtx) -> None:
        self._ctx = ctx
        self.out: dict[str, str] = {}

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        if node.returns is None:
            rt = infer_return_type(self._ctx, node, in_class=False)
            if rt:
                self.out.setdefault(node.name.value, rt)
        return True


def infer_local_returns(ctx: FileCtx, func: cst.FunctionDef) -> dict[str, str]:
    """Return inferred return types of nested functions inside ``func``."""
    collector = _LocalReturnsCollector(ctx)
    func.body.visit(collector)
    return collector.out


def set_class_attr_types(ctx: FileCtx, cls_node: cst.ClassDef | None) -> None:
    """Refresh ``ctx.class_attr_types`` from annotated class attributes."""
    ctx.class_attr_types = {}
    if cls_node is None:
        return
    for stmt in suite_stmts(cls_node.body):
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for small in stmt.body:
            if isinstance(small, cst.AnnAssign) and isinstance(small.target, cst.Name):
                ctx.class_attr_types[small.target.value] = node_code(ctx.tree, small.annotation.annotation)


def local_method_returns(ctx: FileCtx, cls_node: cst.ClassDef) -> dict[str, str]:
    """Return explicit or inferred return types for methods of ``cls_node``."""
    out: dict[str, str] = {}
    saved_bases, saved_attr = ctx.class_bases, ctx.class_attr_types
    ctx.class_bases = [node_code(ctx.tree, b.value) for b in cls_node.bases]
    set_class_attr_types(ctx, cls_node)
    for stmt in suite_stmts(cls_node.body):
        if isinstance(stmt, cst.FunctionDef):
            if stmt.returns is not None:
                out[stmt.name.value] = node_code(ctx.tree, stmt.returns.annotation)
            else:
                rt = infer_return_type(ctx, stmt, in_class=True)
                if rt:
                    out[stmt.name.value] = rt
    ctx.class_bases, ctx.class_attr_types = saved_bases, saved_attr
    return out


class _ModuleClassesCollector(cst.CSTVisitor):
    """Map each class in the module to its defined method names."""

    def __init__(self) -> None:
        self.classes: dict[str, set[str]] = {}

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        methods = {stmt.name.value for stmt in suite_stmts(node.body) if isinstance(stmt, cst.FunctionDef)}
        self.classes.setdefault(node.name.value, set()).update(methods)
        return True


def collect_module_classes(ctx: FileCtx) -> dict[str, set[str]]:
    """Map each class name in the module to the set of methods it defines."""
    collector = _ModuleClassesCollector()
    ctx.tree.visit(collector)
    return collector.classes


class _AllClassReturnsCollector(cst.CSTVisitor):
    """Collect per-class method return types across the module."""

    def __init__(self, ctx: FileCtx) -> None:
        self._ctx = ctx
        self.out: dict[str, dict[str, str]] = {}

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.out[node.name.value] = local_method_returns(self._ctx, node)
        return True


def collect_all_class_returns(ctx: FileCtx) -> dict[str, dict[str, str]]:
    """Collect method return types keyed by class name."""
    collector = _AllClassReturnsCollector(ctx)
    ctx.tree.visit(collector)
    return collector.out


class _CommonMethodReturnsCollector(cst.CSTVisitor):
    """Collect inferred method return types across classes for ``self.x()`` calls."""

    def __init__(self, ctx: FileCtx) -> None:
        self._ctx = ctx
        self._bases_stack: list[list[str]] = []
        self.seen: dict[str, list[str]] = {}

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self._bases_stack.append(self._ctx.class_bases)
        self._ctx.class_bases = [node_code(self._ctx.tree, b.value) for b in node.bases]
        return True

    @override
    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        del original_node
        self._ctx.class_bases = self._bases_stack.pop()

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        if node.returns is None:
            rt = infer_return_type(self._ctx, node, in_class=True)
            if rt and rt not in ("None", "Any"):
                self.seen.setdefault(node.name.value, []).append(rt)
        return True


def collect_method_returns(ctx: FileCtx) -> dict[str, str]:
    """Return the most common inferred return type for each method name."""
    collector = _CommonMethodReturnsCollector(ctx)
    ctx.tree.visit(collector)
    out: dict[str, str] = {}
    for name, types in collector.seen.items():
        counts: dict[str, int] = {}
        for t in types:
            counts[t] = counts.get(t, 0) + 1
        out[name] = max(counts.items(), key=lambda kv: kv[1])[0]
    return out


def _spaced_equal(equal: cst.AssignEqual) -> cst.AssignEqual:
    """Ensure ``=`` has separating spaces while preserving comment whitespace."""
    before = (
        cst.SimpleWhitespace(" ")
        if isinstance(equal.whitespace_before, cst.SimpleWhitespace) and not equal.whitespace_before.value
        else equal.whitespace_before
    )
    after = (
        cst.SimpleWhitespace(" ")
        if isinstance(equal.whitespace_after, cst.SimpleWhitespace) and not equal.whitespace_after.value
        else equal.whitespace_after
    )
    return equal.with_changes(whitespace_before=before, whitespace_after=after)


def _assign_to_annassign(
    ctx: FileCtx,
    assign: cst.Assign,
    typ: str,
) -> cst.AnnAssign:
    """Convert a single-target ``Assign`` node into an ``AnnAssign`` node."""
    tgt = assign.targets[0]
    before = cst.SimpleWhitespace(" ") if not tgt.whitespace_before_equal.value else tgt.whitespace_before_equal
    after = cst.SimpleWhitespace(" ") if not tgt.whitespace_after_equal.value else tgt.whitespace_after_equal
    return cst.AnnAssign(
        target=tgt.target,
        annotation=cst.Annotation(annotation=cst.parse_expression(typ, config=ctx.tree.config_for_parsing)),
        value=assign.value,
        equal=cst.AssignEqual(whitespace_before=before, whitespace_after=after),
        semicolon=assign.semicolon,
    )


class _InitAssignCollector(cst.CSTVisitor):
    """Collect ``self.attr = value`` assignments inside ``__init__``."""

    def __init__(
        self,
        ctx: FileCtx,
        annotated: set[str],
        out: dict[int, tuple[str, str]],
    ) -> None:
        self._ctx = ctx
        self._annotated = annotated
        self._out = out

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        del node
        return False

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        del node
        return False

    @override
    def visit_Assign(self, node: cst.Assign) -> bool:
        res = attr_assign_type(self._ctx, node, self._annotated)
        if res is not None:
            self._out[id(node)] = res
        return True


@dataclass(slots=True)
class _ClassState:
    bases: list[str]
    method_returns: dict[str, str]
    attr_types: dict[str, str]
    cls_node: cst.ClassDef | None
    ctor_heavy: bool


@dataclass(slots=True)
class _FuncState:
    func: cst.FunctionDef | None
    param_types: dict[str, str]
    local_returns: dict[str, str]
    local_types: dict[str, str]
    in_class: bool
    param_annots: dict[str, str]
    return_annot: str


class _AnnotateTransformer(cst.CSTTransformer):
    """LibCST transformer that inserts inferred annotations across a module."""

    def __init__(self, ctx: FileCtx) -> None:
        self.ctx = ctx
        self.edits: list[Edit] = []
        self._module_annots: dict[int, tuple[str, str]] = {}
        self._attr_annots: dict[int, tuple[str, str]] = {}
        self._class_stack: list[_ClassState] = []
        self._func_stack: list[_FuncState] = []
        self._current_cls: cst.ClassDef | None = None
        self._current_ctor_heavy = False
        self._in_class = False

    @override
    def visit_Module(self, node: cst.Module) -> bool:
        for stmt in node.body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for small in stmt.body:
                if not isinstance(small, cst.Assign):
                    continue
                typ = module_assign_type(self.ctx, small)
                if not typ:
                    continue
                tgt = small.targets[0].target
                if isinstance(tgt, cst.Name):
                    self._module_annots[id(small)] = (tgt.value, self.ctx.request(typ))
        return True

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self._class_stack.append(
            _ClassState(
                bases=self.ctx.class_bases,
                method_returns=self.ctx.class_method_returns,
                attr_types=self.ctx.class_attr_types,
                cls_node=self._current_cls,
                ctor_heavy=self._current_ctor_heavy,
            )
        )
        self.ctx.class_bases = [node_code(self.ctx.tree, b.value) for b in node.bases]
        self.ctx.class_method_returns = local_method_returns(self.ctx, node)
        set_class_attr_types(self.ctx, node)
        self._current_cls = node
        self._current_ctor_heavy = ctor_heavy(self.ctx, node)
        self._in_class = True
        return True

    @override
    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        del original_node
        prev = self._class_stack.pop()
        self.ctx.class_bases = prev.bases
        self.ctx.class_method_returns = prev.method_returns
        self.ctx.class_attr_types = prev.attr_types
        self._current_cls = prev.cls_node
        self._current_ctor_heavy = prev.ctor_heavy
        self._in_class = bool(self._class_stack)
        return updated_node

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._func_stack.append(
            _FuncState(
                func=self.ctx.func,
                param_types=self.ctx.param_types,
                local_returns=self.ctx.local_returns,
                local_types=self.ctx.local_types,
                in_class=self._in_class,
                param_annots={},
                return_annot="",
            )
        )
        self.ctx.func = node
        self.ctx.param_types = {
            p.name.value: node_code(self.ctx.tree, p.annotation.annotation)
            for p, _ in iter_params(node.params)
            if p.annotation is not None
        }
        self.ctx.local_returns = infer_local_returns(self.ctx, node)
        set_class_attr_types(self.ctx, self._current_cls)
        self.ctx.local_types = collect_local_types(self.ctx, node)
        p_annots = param_annotations(self.ctx, node, in_class=self._in_class)
        r_annot = infer_return_type(self.ctx, node, in_class=self._in_class) if node.returns is None else ""
        self._func_stack[-1].param_annots = p_annots
        self._func_stack[-1].return_annot = r_annot
        if (
            self._current_cls is not None
            and len(self._func_stack) == 1
            and node.name.value == "__init__"
            and self._current_ctor_heavy
        ):
            annotated = class_annotated_attrs(self._current_cls)
            node.body.visit(_InitAssignCollector(self.ctx, annotated, self._attr_annots))
        return True

    def _annotate_param(
        self,
        param: cst.Param,
        key: str,
        func_name: str,
        p_annots: dict[str, str],
    ) -> cst.Param:
        if param.annotation is not None:
            return param
        typ = p_annots.get(key, "")
        if not typ:
            return param
        ann_expr = cst.parse_expression(self.ctx.request(typ), config=self.ctx.tree.config_for_parsing)
        self.edits.append(Edit("param", f"{func_name}({key}): {typ}"))
        equal = (
            _spaced_equal(param.equal)
            if param.default is not None and isinstance(param.equal, cst.AssignEqual)
            else param.equal
        )
        return param.with_changes(
            annotation=cst.Annotation(annotation=ann_expr),
            equal=equal,
        )

    @override
    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        state = self._func_stack.pop()
        self.ctx.func = state.func
        self.ctx.param_types = state.param_types
        self.ctx.local_returns = state.local_returns
        self.ctx.local_types = state.local_types
        p_annots = state.param_annots
        fname = original_node.name.value
        params = updated_node.params
        new_posonly = [self._annotate_param(p, p.name.value, fname, p_annots) for p in params.posonly_params]
        new_pos = [self._annotate_param(p, p.name.value, fname, p_annots) for p in params.params]
        new_star_arg = (
            self._annotate_param(
                params.star_arg,
                f"*{params.star_arg.name.value}",
                fname,
                p_annots,
            )
            if isinstance(params.star_arg, cst.Param)
            else params.star_arg
        )
        new_kwonly = [self._annotate_param(p, p.name.value, fname, p_annots) for p in params.kwonly_params]
        new_star_kwarg = (
            self._annotate_param(
                params.star_kwarg,
                f"**{params.star_kwarg.name.value}",
                fname,
                p_annots,
            )
            if params.star_kwarg is not None
            else None
        )
        updated_params = params.with_changes(
            posonly_params=new_posonly,
            params=new_pos,
            star_arg=new_star_arg,
            kwonly_params=new_kwonly,
            star_kwarg=new_star_kwarg,
        )
        updated_returns = updated_node.returns
        if original_node.returns is None and state.return_annot:
            ret_expr = cst.parse_expression(
                self.ctx.request(state.return_annot),
                config=self.ctx.tree.config_for_parsing,
            )
            updated_returns = cst.Annotation(annotation=ret_expr)
            self.edits.append(Edit("return", f"{fname} -> {state.return_annot}"))
        return updated_node.with_changes(params=updated_params, returns=updated_returns)

    @override
    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.BaseSmallStatement:
        key = id(original_node)
        if key in self._module_annots:
            var_name, typ = self._module_annots[key]
            self.edits.append(Edit("module", f"{var_name}: {typ}"))
            return _assign_to_annassign(self.ctx, updated_node, typ)
        if key in self._attr_annots:
            attr_name, typ = self._attr_annots[key]
            self.edits.append(Edit("attr", f"self.{attr_name}: {typ}"))
            return _assign_to_annassign(self.ctx, updated_node, typ)
        return updated_node


def _apply_needed_imports(
    tree: cst.Module,
    *,
    typing_names: Sequence[str],
    need_re: bool,
    need_path: bool,
    need_future: bool,
) -> tuple[cst.Module, list[str]]:
    """Insert missing typing, ``re``, ``Path``, and ``__future__`` imports."""
    defined = names_defined_in(tree)
    missing_typing = sorted(n for n in typing_names if n and n not in defined)
    add_path = need_path and "Path" not in defined
    add_re = need_re and "re" not in defined
    add_future = need_future and not _has_future_annotations(tree)
    if not (missing_typing or add_path or add_re or add_future):
        return tree, []
    codemod_ctx = CodemodContext()
    added: list[str] = []
    if add_future:
        AddImportsVisitor.add_needed_import(codemod_ctx, "__future__", "annotations")
        added.append("from __future__ import annotations")
    for name in missing_typing:
        AddImportsVisitor.add_needed_import(codemod_ctx, "typing", name)
    if missing_typing:
        added.append(f"from typing import {', '.join(missing_typing)}")
    if add_re:
        AddImportsVisitor.add_needed_import(codemod_ctx, "re")
        added.append("import re")
    if add_path:
        AddImportsVisitor.add_needed_import(codemod_ctx, "pathlib", "Path")
        added.append("from pathlib import Path")
    updated = tree.visit(AddImportsVisitor(codemod_ctx))
    return updated, added


def _string_names(text: str) -> set[str]:
    """Extract root identifiers referenced inside a stringified annotation."""
    return {m.group(0).split(".")[0] for m in _NAME_LEAF_RE.finditer(text)}


class _AnnotationRefCollector(cst.CSTVisitor):
    """Collect root identifiers referenced within annotation expressions."""

    def __init__(self) -> None:
        self.used: set[str] = set()

    @override
    def visit_Attribute(self, node: cst.Attribute) -> bool:
        node.value.visit(self)
        return False

    @override
    def visit_Arg(self, node: cst.Arg) -> bool:
        node.value.visit(self)
        return False

    @override
    def visit_Name(self, node: cst.Name) -> bool:
        self.used.add(node.value)
        return False

    @override
    def visit_SimpleString(self, node: cst.SimpleString) -> bool:
        if isinstance(node.evaluated_value, str):
            self.used.update(_string_names(node.evaluated_value))
        return False

    @override
    def visit_ConcatenatedString(self, node: cst.ConcatenatedString) -> bool:
        if isinstance(node.evaluated_value, str):
            self.used.update(_string_names(node.evaluated_value))
        return False


class _ModuleAnnotationCollector(cst.CSTVisitor):
    """Walk all annotation positions in a CST module."""

    def __init__(self) -> None:
        self.used: set[str] = set()
        self._ref = _AnnotationRefCollector()

    @override
    def visit_Param(self, node: cst.Param) -> bool:
        if node.annotation is not None:
            node.annotation.annotation.visit(self._ref)
            self.used.update(self._ref.used)
            self._ref.used.clear()
        return True

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        if node.returns is not None:
            node.returns.annotation.visit(self._ref)
            self.used.update(self._ref.used)
            self._ref.used.clear()
        return True

    @override
    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        node.annotation.annotation.visit(self._ref)
        self.used.update(self._ref.used)
        self._ref.used.clear()
        return True


def annotations_in(tree: cst.Module) -> set[str]:
    """Return every root name referenced by annotations in ``tree``."""
    collector = _ModuleAnnotationCollector()
    tree.visit(collector)
    return collector.used


def repair_imports(text: str) -> tuple[str, list[str]]:
    """Re-add typing, ``Path``, or ``re`` imports missing for existing annotations."""
    tree = cst.parse_module(text)
    used = annotations_in(tree)
    defined = names_defined_in(tree)
    missing = sorted(n for n in used if n not in defined and n in TYPING_NAMES)
    uses_re = _RE_USAGE_RE.search(text) is not None
    need_path = "Path" in used and "Path" not in defined
    need_re = "re" not in defined and uses_re
    updated, added = _apply_needed_imports(
        tree,
        typing_names=missing,
        need_re=need_re,
        need_path=need_path,
        need_future=False,
    )
    return updated.code, added


def annotate_source(
    ctx: FileCtx,
    *,
    add_future: bool = True,
) -> tuple[str, list[Edit], list[str]]:
    """Run one annotation pass over ``ctx`` and return text, edits, and imports."""
    transformer = _AnnotateTransformer(ctx)
    updated_tree = ctx.tree.visit(transformer)
    edits = transformer.edits
    need_future = (
        add_future and any("|" in e.detail for e in edits) and not ctx.future_annotations and ctx.style == "pep604"
    )
    final_tree, imports_added = _apply_needed_imports(
        updated_tree,
        typing_names=sorted(ctx.needed),
        need_re=ctx.need_re,
        need_path=ctx.need_path,
        need_future=need_future,
    )
    return final_tree.code, edits, imports_added


def _annotation_driven_class(node: cst.ClassDef) -> bool:
    """Return True when a class body's annotations double as runtime API."""
    for dec in node.decorators:
        target = dec.decorator
        callee = target.func if isinstance(target, cst.Call) else target
        name = dotted_name(callee) if isinstance(callee, (cst.Name, cst.Attribute)) else ""
        if name and name.rsplit(".", 1)[-1] in ANNOTATION_DRIVEN_DECORATORS:
            return True
    for base in node.bases:
        name = dotted_name(base.value)
        if name and name.rsplit(".", 1)[-1] in ANNOTATION_DRIVEN_BASES:
            return True
    return False


class _AnnotationRemover(cst.CSTTransformer):
    """LibCST transformer that removes existing type annotations from a module.
    Class-field annotations of annotation-driven classes (``@dataclass``,
    ``NamedTuple``, ``TypedDict``, pydantic-style bases) are preserved because
    those frameworks read ``__annotations__`` at runtime and would break.
    """

    def __init__(self, module: cst.Module) -> None:
        self._module = module
        self.edits: list[Edit] = []
        self._func_names: list[str] = []
        self._sensitive: list[bool] = []

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self._sensitive.append(_annotation_driven_class(node))
        return True

    @override
    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        del original_node
        self._sensitive.pop()
        return updated_node

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._func_names.append(node.name.value)
        self._sensitive.append(False)
        return True

    @override
    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        fname = self._func_names.pop()
        self._sensitive.pop()
        if original_node.returns is not None:
            old = node_code(self._module, original_node.returns.annotation)
            self.edits.append(Edit("strip-return", f"{fname} -> {old}"))
            return updated_node.with_changes(returns=None)
        return updated_node

    @override
    def leave_Param(self, original_node: cst.Param, updated_node: cst.Param) -> cst.Param:
        if original_node.annotation is None:
            return updated_node
        fname = self._func_names[-1] if self._func_names else "<module>"
        pname = original_node.name.value
        old = node_code(self._module, original_node.annotation.annotation)
        self.edits.append(Edit("strip-param", f"{fname}({original_node.star}{pname}): {old}"))
        return updated_node.with_changes(annotation=None)

    @override
    def leave_AnnAssign(
        self, original_node: cst.AnnAssign, updated_node: cst.AnnAssign
    ) -> cst.BaseSmallStatement | cst.RemovalSentinel:
        if self._sensitive and self._sensitive[-1]:
            return updated_node
        target_repr = node_code(self._module, original_node.target)
        old = node_code(self._module, original_node.annotation.annotation)
        self.edits.append(Edit("strip-assign", f"{target_repr}: {old}"))
        if updated_node.value is None:
            return cst.RemoveFromParent()
        equal = updated_node.equal
        before = (
            equal.whitespace_before
            if isinstance(equal, cst.AssignEqual) and isinstance(equal.whitespace_before, cst.SimpleWhitespace)
            else cst.SimpleWhitespace(" ")
        )
        after = (
            equal.whitespace_after
            if isinstance(equal, cst.AssignEqual) and isinstance(equal.whitespace_after, cst.SimpleWhitespace)
            else cst.SimpleWhitespace(" ")
        )
        return cst.Assign(
            targets=[
                cst.AssignTarget(
                    target=updated_node.target,
                    whitespace_before_equal=before,
                    whitespace_after_equal=after,
                )
            ],
            value=updated_node.value,
            semicolon=updated_node.semicolon,
        )


class _CodeNameCollector(cst.CSTVisitor):
    """Collect identifiers referenced outside import statements.
    Annotations still present in the tree (the load-bearing ones the remover
    kept) are traversed on purpose so their imports survive cleanup.
    """

    def __init__(self) -> None:
        self.used: set[str] = set()

    @override
    def visit_Import(self, node: cst.Import) -> bool:
        del node
        return False

    @override
    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        del node
        return False

    @override
    def visit_Name(self, node: cst.Name) -> bool:
        self.used.add(node.value)
        return False


def code_names(tree: cst.Module) -> set[str]:
    """Return identifiers referenced in non-import code of ``tree``."""
    collector = _CodeNameCollector()
    tree.visit(collector)
    return collector.used


def _import_alias_bound(alias: cst.ImportAlias) -> str:
    """Return the name an import alias binds in the current namespace."""
    return (alias.evaluated_alias or alias.evaluated_name).split(".")[0]


def _drop_trailing_comma(aliases: list[cst.ImportAlias]) -> list[cst.ImportAlias]:
    """Reset a trailing comma left behind after narrowing an import list."""
    if aliases and isinstance(aliases[-1].comma, cst.Comma):
        aliases[-1] = aliases[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
    return aliases


def _narrow_import(
    node: cst.Import,
    annotation_refs: set[str],
    code_refs: set[str],
    edits: list[Edit],
) -> cst.Import | None:
    """Drop aliases of ``node`` that only served removed annotations."""
    kept: list[cst.ImportAlias] = []
    removed: list[str] = []
    for alias in node.names:
        bound = _import_alias_bound(alias)
        if bound in annotation_refs and bound not in code_refs:
            removed.append(bound)
        else:
            kept.append(alias)
    if not removed:
        return node
    edits.append(Edit("strip-import", f"import {', '.join(removed)}"))
    if not kept:
        return None
    return node.with_changes(names=_drop_trailing_comma(kept))


def _narrow_import_from(
    node: cst.ImportFrom,
    annotation_refs: set[str],
    code_refs: set[str],
    edits: list[Edit],
) -> cst.ImportFrom | None:
    """Drop aliases of ``node`` that only served removed annotations."""
    if isinstance(node.names, cst.ImportStar):
        return node
    is_future = node.module is not None and dotted_name(node.module) == "__future__"
    kept: list[cst.ImportAlias] = []
    removed: list[str] = []
    for alias in node.names:
        bound = _import_alias_bound(alias)
        if is_future:
            drop = bound == "annotations"
        else:
            drop = bound in annotation_refs and bound not in code_refs
        if drop:
            removed.append(bound)
        else:
            kept.append(alias)
    if not removed:
        return node
    label = f"from {dotted_name(node.module)} import" if node.module is not None else "import"
    edits.append(Edit("strip-import", f"{label} {', '.join(removed)}"))
    if not kept:
        return None
    return node.with_changes(names=_drop_trailing_comma(kept))


def _strip_unused_imports(
    tree: cst.Module,
    annotation_refs: set[str],
) -> tuple[cst.Module, list[Edit]]:
    """Remove top-level imports made dead by stripping annotations."""
    code_refs = code_names(tree)
    edits: list[Edit] = []
    kept_stmts: list[cst.SimpleStatementLine | cst.BaseCompoundStatement] = []
    for stmt in tree.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            kept_stmts.append(stmt)
            continue
        kept_small: list[cst.BaseSmallStatement] = []
        for small in stmt.body:
            if isinstance(small, cst.Import):
                small = _narrow_import(small, annotation_refs, code_refs, edits)
            elif isinstance(small, cst.ImportFrom):
                small = _narrow_import_from(small, annotation_refs, code_refs, edits)
            if small is not None:
                kept_small.append(small)
        if kept_small:
            kept_stmts.append(stmt.with_changes(body=kept_small))
    return tree.with_changes(body=kept_stmts), edits


def strip_source(ctx: FileCtx) -> tuple[str, list[Edit]]:
    """Remove every annotation from ``ctx`` and drop imports they orphaned."""
    remover = _AnnotationRemover(ctx.tree)
    stripped_tree = ctx.tree.visit(remover)
    cleaned_tree, import_edits = _strip_unused_imports(stripped_tree, annotations_in(ctx.tree))
    return cleaned_tree.code, remover.edits + import_edits


class _AnnotationStripper(cst.CSTTransformer):
    """Strip annotations and top-level imports to compare CST semantics."""

    @override
    def leave_Param(self, original_node: cst.Param, updated_node: cst.Param) -> cst.Param:
        del original_node
        return updated_node.with_changes(annotation=None)

    @override
    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        del original_node
        return updated_node.with_changes(returns=None)

    @override
    def leave_AnnAssign(
        self, original_node: cst.AnnAssign, updated_node: cst.AnnAssign
    ) -> cst.BaseSmallStatement | cst.RemovalSentinel:
        del original_node
        if updated_node.value is None:
            return cst.RemoveFromParent()
        return cst.Assign(
            targets=[cst.AssignTarget(target=updated_node.target)],
            value=updated_node.value,
        )

    @override
    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        del original_node
        kept_stmts: list[cst.SimpleStatementLine | cst.BaseCompoundStatement] = []
        for stmt in updated_node.body:
            if isinstance(stmt, cst.SimpleStatementLine):
                kept = [s for s in stmt.body if not isinstance(s, (cst.Import, cst.ImportFrom))]
                if kept:
                    kept_stmts.append(stmt.with_changes(body=kept))
            else:
                kept_stmts.append(stmt)
        return updated_node.with_changes(body=kept_stmts)


def _cst_structure(node: object) -> object:
    """Serialize a CST node tree ignoring formatting, defaults, and syntax tokens."""
    if isinstance(node, cst.CSTNode):
        fields = filter_node_fields(node, show_defaults=False, show_syntax=False, show_whitespace=False)
        return (
            type(node).__name__,
            tuple((f.name, _cst_structure(cast("object", getattr(node, f.name)))) for f in fields),
        )
    if isinstance(node, (list, tuple)):
        items = cast("list[object] | tuple[object, ...]", node)
        return tuple(_cst_structure(item) for item in items)
    return node


def _normalize_cst(tree: cst.Module) -> object:
    """Return a canonical semantic representation of ``tree`` without annotations."""
    stripped = tree.visit(_AnnotationStripper())
    # Re-parse the rendered code so that suite-emptying removals compare equal
    # to their parsed form: removing the only statement of a suite leaves an
    # empty block that renders (and re-parses) as ``pass``, and both sides of
    # the comparison must canonicalize that the same way.
    reparsed = cst.parse_module(stripped.code)
    return _cst_structure(reparsed)


def check_equivalence(old: cst.Module, new_text: str) -> tuple[bool, str]:
    """Verify that the transformed source preserves non-annotation semantics."""
    try:
        new_tree = cst.parse_module(new_text)
    except (cst.ParserSyntaxError, SyntaxError) as e:
        return False, f"syntax error after edit: {e}"
    if _normalize_cst(old) != _normalize_cst(new_tree):
        return False, "CST changed beyond annotations"
    return True, ""


class _TypeParamCollector(cst.CSTVisitor):
    """Collect PEP 695 type parameter names declared in the module."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    @override
    def visit_TypeParameters(self, node: cst.TypeParameters) -> bool:
        for tp in node.params:
            self.names.add(tp.param.name.value)
        return True


def type_param_names(tree: cst.Module) -> set[str]:
    """Return all PEP 695 type parameter names bound in ``tree``."""
    collector = _TypeParamCollector()
    tree.visit(collector)
    return collector.names


def introduced_annotation_names(old_tree: cst.Module, new_tree: cst.Module) -> set[str]:
    """Return annotation names added in ``new_tree`` that are undefined at runtime."""
    before = annotations_in(old_tree)
    after = annotations_in(new_tree)
    defined = names_defined_in(new_tree) | type_param_names(new_tree)
    return {name for name in after - before if name not in defined}


def encoding_problem(encoding: str, text: str) -> str:
    """Return an error message if ``text`` cannot round-trip through ``encoding``."""
    try:
        raw = text.encode(encoding)
    except (UnicodeEncodeError, LookupError) as e:
        return f"cannot encode as {encoding}: {e}"
    try:
        decoded = raw.decode(encoding)
    except UnicodeDecodeError as e:
        return f"cannot round-trip {encoding}: {e}"
    if decoded != text:
        return f"encoding round-trip through {encoding} altered the text"
    return ""


def validate_result(ctx: FileCtx, path: Path, new_text: str) -> tuple[str, str]:
    """Validate syntax, CST semantic equivalence, names, and encoding before write."""
    try:
        tree = cst.parse_module(new_text)
    except (cst.ParserSyntaxError, SyntaxError) as e:
        return "compile-error", f"syntax error after edit: {e}"
    if _normalize_cst(ctx.tree) != _normalize_cst(tree):
        return "rejected", "CST changed beyond annotations"
    try:
        compile(new_text, str(path), "exec")
    except SyntaxError as e:
        return "compile-error", f"result does not compile: {e}"
    introduced = introduced_annotation_names(ctx.tree, tree)
    if introduced:
        names_list = ", ".join(sorted(introduced))
        return "undefined-name", f"annotation(s) use undefined name(s): {names_list}"
    problem = encoding_problem(ctx.encoding, new_text)
    if problem:
        return "encoding-error", problem
    return "changed", ""


def merge_edits(first: list[Edit], second: list[Edit]) -> list[Edit]:
    """Concatenate edits from consecutive passes while removing duplicates."""
    seen = {(e.kind, e.detail) for e in first}
    out = list(first)
    for e in second:
        if (e.kind, e.detail) not in seen:
            seen.add((e.kind, e.detail))
            out.append(e)
    return out


def load_for_run(
    path: Path,
    text: str | None = None,
    encoding: str = "",
    *,
    style: str = "auto",
) -> FileCtx:
    """Load a ``FileCtx`` and precompute class and method lookup tables."""
    ctx = (
        load_ctx(path, style_override=style)
        if text is None
        else build_ctx(path, text, encoding or "utf-8", style_override=style)
    )
    ctx.module_classes = collect_module_classes(ctx)
    ctx.class_method_returns_all = collect_all_class_returns(ctx)
    ctx.method_returns = collect_method_returns(ctx)
    return ctx


def run_pass(
    ctx: FileCtx,
    passes: int,
    *,
    style: str = "auto",
    add_future: bool = True,
) -> tuple[str, list[Edit], list[str]]:
    """Iterate annotation passes until convergence or ``passes`` is reached."""
    text, edits, imports = annotate_source(ctx, add_future=add_future)
    all_edits, all_imports = list(edits), list(imports)
    for _ in range(max(0, passes - 1)):
        following = load_for_run(ctx.path, text, ctx.encoding, style=style)
        next_text, next_edits, next_imports = annotate_source(following, add_future=add_future)
        if next_text == text:
            break
        text = next_text
        all_edits = merge_edits(all_edits, next_edits)
        all_imports.extend(i for i in next_imports if i not in all_imports)
    return text, all_edits, all_imports


def infer_file_text(
    ctx: FileCtx,
    passes: int,
    *,
    style: str = "auto",
    add_future: bool = True,
) -> tuple[str, list[Edit], list[str]]:
    """Infer annotated source text for ``ctx`` and repair any missing imports."""
    merged_text, merged_edits, merged_imports = run_pass(ctx, passes, style=style, add_future=add_future)
    if not merged_edits:
        return merged_text, merged_edits, merged_imports
    repaired, extra = repair_imports(merged_text)
    if extra and repaired != merged_text:
        return repaired, merged_edits, merged_imports + extra
    return merged_text, merged_edits, merged_imports


def unified_diff(old_text: str, new_text: str, path: Path, context: int = 3, label: str = "annotated") -> list[str]:
    """Build a unified diff between original and rewritten source text."""
    return list(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"{path} ({label})",
            n=context,
        )
    )


def diff_stats(lines: list[str]) -> tuple[int, int]:
    """Count added and removed lines in a unified diff."""
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return added, removed


def color_enabled(mode: str, stream: object) -> bool:
    """Resolve ``--color`` against environment variables and stream TTY state."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    no_color = os.environ.get("NO_COLOR", "")
    if bool(no_color) or os.environ.get("TERM") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty()) if callable(isatty) else False


def _paint(text: str, code: str, *, enabled: bool) -> str:
    """Wrap one diff line in ANSI color codes, preserving its trailing newline."""
    body = text.rstrip("\r\n")
    ending = text[len(body) :]
    if not enabled or not body:
        return text
    return f"{code}{body}{ANSI_RESET}{ending}"


def _split_marker(line: str) -> tuple[str, str, str]:
    """Split a unified diff line into marker, body, and line ending."""
    body = line.rstrip("\r\n")
    return body[:1], body[1:], line[len(body) :]


def emphasize_pair(old_line: str, new_line: str, *, enabled: bool) -> tuple[str, str]:
    """Colorize a removed/added line pair, highlighting changed characters."""
    if not enabled:
        return old_line, new_line
    old_marker, old_body, old_end = _split_marker(old_line)
    new_marker, new_body, new_end = _split_marker(new_line)
    matcher = difflib.SequenceMatcher(None, old_body, new_body, autojunk=False)
    if matcher.ratio() < MIN_DIFF_MATCH_RATIO:
        return (
            _paint(old_line, DIFF_STYLES["del"], enabled=True),
            _paint(new_line, DIFF_STYLES["add"], enabled=True),
        )
    old_parts: list[str] = []
    new_parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_seg, new_seg = old_body[i1:i2], new_body[j1:j2]
        if tag == "equal":
            old_parts.append(old_seg)
            new_parts.append(new_seg)
            continue
        if old_seg:
            old_parts.append(f"{ANSI_BOLD}{old_seg}{ANSI_RESET}{DIFF_STYLES['del']}")
        if new_seg:
            new_parts.append(f"{ANSI_BOLD}{new_seg}{ANSI_RESET}{DIFF_STYLES['add']}")
    old_out = f"{DIFF_STYLES['del']}{old_marker}{''.join(old_parts)}{ANSI_RESET}{old_end}"
    new_out = f"{DIFF_STYLES['add']}{new_marker}{''.join(new_parts)}{ANSI_RESET}{new_end}"
    return old_out, new_out


def colorize_diff(lines: list[str], *, enabled: bool) -> list[str]:
    """Apply ANSI diff styling to unified diff lines."""
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        following = lines[index + 1] if index + 1 < len(lines) else ""
        paired = (
            line.startswith("-")
            and not line.startswith("---")
            and following.startswith("+")
            and not following.startswith("+++")
        )
        if paired:
            old_out, new_out = emphasize_pair(line, following, enabled=enabled)
            out.extend((old_out, new_out))
            index += 2
            continue
        if line.startswith(("---", "+++")):
            out.append(_paint(line, DIFF_STYLES["file"], enabled=enabled))
        elif line.startswith("@@"):
            out.append(_paint(line, DIFF_STYLES["hunk"], enabled=enabled))
        elif line.startswith("+"):
            out.append(_paint(line, DIFF_STYLES["add"], enabled=enabled))
        elif line.startswith("-"):
            out.append(_paint(line, DIFF_STYLES["del"], enabled=enabled))
        elif line.startswith("\\"):
            out.append(_paint(line, DIFF_STYLES["meta"], enabled=enabled))
        else:
            out.append(_paint(line, DIFF_STYLES["ctx"], enabled=enabled))
        index += 1
    return out


def emit_diff(result: FileResult, *, enabled: bool) -> None:
    """Write a single file's unified diff to stdout."""
    diff = result.get("diff", [])
    if not diff:
        return
    heading = f"{result['file']}  (+{result.get('added', 0)}/-{result.get('removed', 0)})"
    sys.stdout.write(_paint(heading + "\n", f"{ANSI_BOLD}{DIFF_STYLES['file']}", enabled=enabled))
    for line in colorize_diff(diff, enabled=enabled):
        sys.stdout.write(line if line.endswith("\n") else line + "\n")


def emit_diffs(results: list[FileResult], color: str) -> None:
    """Write all collected file diffs to stdout."""
    enabled = color_enabled(color, sys.stdout)
    for result in results:
        emit_diff(result, enabled=enabled)


def atomic_write(path: Path, payload: bytes) -> str:
    """Atomically replace ``path`` with ``payload`` and verify the bytes landed.
    The data goes to a temp file in the same directory, is flushed and fsynced,
    then renamed over the target, so a crash or process kill (common on
    Android/Termux) can never leave a truncated file behind. Symlinks are
    written through, matching ``Path.write_bytes`` semantics. Returns an error
    string on any failure, or an empty string on success.
    """
    target = Path(path).resolve()
    tmp: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass  # sdcardfs/FUSE on Android may refuse fsync; rename stays atomic
        os.replace(tmp, target)
        tmp = None
    except OSError as e:
        return f"write failed: {e}"
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        if target.read_bytes() != payload:
            return "post-write verification read back different bytes"
    except OSError as e:
        return f"post-write verification failed: {e}"
    return ""


def finalize_report(
    info: FileResult,
    path: Path,
    ctx: FileCtx,
    new_text: str,
    edits: list[Edit],
    imports: list[str],
    write: bool,
    want_diff: bool = False,
    *,
    backup: bool = False,
    label: str = "annotated",
) -> FileResult:
    """Validate rewritten output and optionally back up and overwrite the file."""
    if not edits:
        info["status"] = "no-change"
        return info
    status, reason = validate_result(ctx, path, new_text)
    if status != "changed":
        info["status"] = status
        info["error"] = reason + " (file left untouched)"
        info["edits"] = [f"{e.kind}:{e.detail}" for e in edits]
        return info
    info["status"] = "changed"
    info["edits"] = [f"{e.kind}:{e.detail}" for e in edits]
    info["imports"] = imports
    if want_diff:
        diff = unified_diff(ctx.text, new_text, path, label=label)
        added, removed = diff_stats(diff)
        info["diff"] = diff
        info["added"] = added
        info["removed"] = removed
    if write:
        if backup:
            backup_path = path.with_name(f"{path.name}.bak")
            shutil.copy2(path, backup_path)
            info["backup"] = str(backup_path)
        write_error = atomic_write(path, new_text.encode(ctx.encoding))
        if write_error:
            info["status"] = "write-error"
            info["error"] = write_error
            return info
    return info


def process_file(
    path: Path,
    write: bool,
    passes: int = DEFAULT_PASSES,
    want_diff: bool = False,
    *,
    backup: bool = False,
    style: str = "auto",
    add_future: bool = True,
    strip: bool = False,
) -> FileResult:
    """Annotate or strip one Python file and return a structured result."""
    info: FileResult = {
        "file": str(path),
        "status": "",
        "edits": [],
        "imports": [],
        "error": "",
    }
    try:
        ctx = load_ctx(path, style_override=style) if strip else load_for_run(path, style=style)
    except (cst.ParserSyntaxError, SyntaxError, ValueError, UnicodeDecodeError) as e:
        info["status"] = "parse-error"
        info["error"] = str(e)
        return info
    if strip:
        try:
            new_text, edits = strip_source(ctx)
            imports: list[str] = []
        except (
            cst.ParserSyntaxError,
            cst.CSTValidationError,
            SyntaxError,
            ValueError,
            RecursionError,
        ) as e:
            info["status"] = "infer-error"
            info["error"] = f"{type(e).__name__}: {e}"
            return info
    else:
        try:
            new_text, edits, imports = infer_file_text(ctx, passes, style=style, add_future=add_future)
        except (cst.ParserSyntaxError, SyntaxError, ValueError, RecursionError) as e:
            info["status"] = "infer-error"
            info["error"] = f"{type(e).__name__}: {e}"
            return info
    return finalize_report(
        info,
        path,
        ctx,
        new_text,
        edits,
        imports,
        write,
        want_diff,
        backup=backup,
        label="stripped" if strip else "annotated",
    )


def process_path(
    args: tuple[str, bool, int, bool, bool, str, bool, bool],
) -> FileResult:
    """Execute ``process_file`` in a worker process from a picklable tuple."""
    path, write, passes, want_diff, backup, style, add_future, strip = args
    return process_file(
        Path(path),
        write,
        passes,
        want_diff,
        backup=backup,
        style=style,
        add_future=add_future,
        strip=strip,
    )


SEQ_MAX_FILES: int = 2
SEQ_MAX_BYTES: int = 32768


def _batch_bytes(files: list[Path]) -> int:
    """Return the total size of a file batch, counting unreadable files as large."""
    total = 0
    for file_path in files:
        try:
            total += file_path.stat().st_size
        except OSError:
            total += SEQ_MAX_BYTES
    return total


def _use_pool(files: list[Path], jobs: int) -> bool:
    """Parallelize unless the batch is so tiny that worker startup dominates.
    Forking workers costs real time on low-power Android/Termux devices, but
    measurements show parallelism already wins for three or more files, so
    only micro-batches of two files below 32 KiB run sequentially.
    """
    if jobs <= 1 or len(files) <= 1:
        return False
    if len(files) > SEQ_MAX_FILES:
        return True
    return _batch_bytes(files) >= SEQ_MAX_BYTES


def process_files(
    files: list[Path],
    write: bool,
    jobs: int,
    passes: int,
    want_diff: bool = False,
    *,
    backup: bool = False,
    style: str = "auto",
    add_future: bool = True,
    strip: bool = False,
) -> Iterator[FileResult]:
    """Process files sequentially or in parallel and yield results as completed."""
    if not _use_pool(files, jobs):
        for file_path in files:
            yield process_file(
                file_path,
                write,
                passes,
                want_diff,
                backup=backup,
                style=style,
                add_future=add_future,
                strip=strip,
            )
        return
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = [
            pool.submit(
                process_path,
                (str(f), write, passes, want_diff, backup, style, add_future, strip),
            )
            for f in files
        ]
        for future in as_completed(futures):
            yield future.result()


def drain_with_progress(
    files: list[Path],
    write: bool,
    jobs: int,
    passes: int,
    want_diff: bool = False,
    *,
    backup: bool = False,
    style: str = "auto",
    add_future: bool = True,
    strip: bool = False,
    fail_fast: bool = False,
) -> list[FileResult]:
    """Process all files while displaying a ``rich`` progress bar if available."""
    progress = _create_progress()
    if progress is None:
        print(
            "note: --progress needs the 'rich' package (pip install rich); continuing without a progress bar",
            file=sys.stderr,
        )
        results: list[FileResult] = []
        for res in process_files(
            files,
            write,
            jobs,
            passes,
            want_diff,
            backup=backup,
            style=style,
            add_future=add_future,
            strip=strip,
        ):
            results.append(res)
            if fail_fast and res["status"] in ERROR_STATUSES:
                break
        return results
    results = []
    with progress:
        task = progress.add_task("annotating", total=len(files))
        for res in process_files(
            files,
            write,
            jobs,
            passes,
            want_diff,
            backup=backup,
            style=style,
            add_future=add_future,
            strip=strip,
        ):
            results.append(res)
            verb = "stripping" if strip else "annotating"
            progress.update(
                task,
                advance=1,
                description=f"{verb} {Path(res['file']).name}",
            )
            if fail_fast and res["status"] in ERROR_STATUSES:
                break
    return results


def report_failure(result: FileResult) -> None:
    """Print failed file statuses and reasons to stderr."""
    if result["status"] in ERROR_STATUSES:
        name = Path(result["file"]).name
        print(f"!! {name}: {result['status']}: {result['error']}", file=sys.stderr)


def summarize(results: list[FileResult]) -> tuple[int, int, int]:
    """Count changed files, total edits, and failed files."""
    changed = sum(1 for r in results if r["status"] == "changed")
    edits = sum(len(r["edits"]) for r in results)
    failed = sum(1 for r in results if r["status"] in ERROR_STATUSES)
    return changed, edits, failed


def _is_excluded(path: Path, patterns: Sequence[str]) -> bool:
    """Return True when ``path`` matches any user-supplied exclusion pattern."""
    if not patterns:
        return False
    posix = path.as_posix()
    name = path.name
    parts = path.parts
    return any(
        fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(posix, pat) or any(fnmatch.fnmatch(part, pat) for part in parts)
        for pat in patterns
    )


def _walk_python_files(
    root: Path,
    excludes: Sequence[str],
    *,
    use_default_excludes: bool,
) -> list[Path]:
    """Recursively collect ``.py`` files under ``root``, pruning excluded folders."""
    collected: list[Path] = []
    for dirpath_str, dirnames, filenames in os.walk(root):
        dirpath = Path(dirpath_str)
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            if use_default_excludes and d in DEFAULT_EXCLUDES:
                continue
            if _is_excluded(dirpath / d, excludes):
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            candidate = dirpath / fname
            if not _is_excluded(candidate, excludes):
                collected.append(candidate)
    return collected


def expand_paths(
    paths: list[str],
    limit: int = 0,
    excludes: Sequence[str] = (),
    *,
    use_default_excludes: bool = True,
) -> list[Path]:
    """Expand inputs (defaulting to ``.``) into deduplicated ``.py`` file paths."""
    targets = paths or ["."]
    files: list[Path] = []
    seen: set[Path] = set()
    for raw in targets:
        path = Path(raw)
        if path.is_dir():
            candidates = _walk_python_files(path, excludes, use_default_excludes=use_default_excludes)
        elif path.is_file() and not _is_excluded(path, excludes):
            candidates = [path]
        else:
            candidates = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(candidate)
    if limit > 0:
        files = files[:limit]
    return files


def resolve_workers(jobs: int, file_count: int) -> int:
    """Clamp worker count to a sensible positive bound for the workload."""
    if jobs <= 0:
        jobs = min(DEFAULT_JOBS, os.cpu_count() or 1)
    return max(1, min(jobs, file_count or 1))


def build_parser() -> argparse.ArgumentParser:
    """Create and configure the command-line argument parser."""
    desc = __doc__.splitlines()[0] if __doc__ else "annotate python files using LibCST"
    epilog = """examples:
  annotate.py src/                        infer annotations for a tree (writes in place)
  annotate.py --dry-run --diff src/       preview inferred annotations
  annotate.py --strip --backup pkg.py     remove annotations, keeping .bak copies
  annotate.py --check --json - src/       CI gate: exit 1 when files need changes
"""
    ap = argparse.ArgumentParser(
        description=desc,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = ap.add_argument_group("mode")
    mode.add_argument(
        "files",
        nargs="*",
        metavar="PATH",
        help=".py files or directories to process (default: recurse '.' in place)",
    )
    mode.add_argument(
        "--strip",
        "--remove-annotations",
        dest="strip",
        action="store_true",
        help="remove existing type annotations from input files instead of "
        "inferring new ones; imports that only served removed annotations "
        "and a dead 'from __future__ import annotations' are dropped too",
    )
    run = ap.add_argument_group("how results are applied")
    write_modes = run.add_mutually_exclusive_group()
    write_modes.add_argument(
        "-w",
        "--write",
        action="store_true",
        help="write changes in place (default unless --dry-run or --check is set)",
    )
    write_modes.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="run inference without writing any files to disk",
    )
    write_modes.add_argument(
        "--check",
        action="store_true",
        help="check if files would change without writing; exit 1 if needed",
    )
    run.add_argument(
        "--backup",
        action="store_true",
        help="write a .bak backup copy before overwriting a modified file",
    )
    run.add_argument(
        "--style",
        choices=("auto", "pep604", "optional"),
        default="auto",
        help="override union-with-None style (default: auto-detect per file)",
    )
    run.add_argument(
        "--no-future",
        action="store_true",
        help="do not insert 'from __future__ import annotations' for PEP 604 unions",
    )
    run.add_argument(
        "--passes",
        type=int,
        default=DEFAULT_PASSES,
        help="max inference passes per file (default: 6)",
    )
    output = ap.add_argument_group("output")
    output.add_argument(
        "--diff",
        action="store_true",
        help="print a colored unified diff of annotation changes",
    )
    output.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colorize --diff output (default: auto)",
    )
    output.add_argument(
        "--json",
        default="",
        metavar="FILE",
        help="write JSON report to path (or '-' for stdout)",
    )
    output.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print per-file edit and import details",
    )
    output.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress non-error summary output",
    )
    output.add_argument(
        "-p",
        "--progress",
        action="store_true",
        help="show a rich progress bar",
    )
    scope = ap.add_argument_group("file selection")
    scope.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="glob pattern or directory name to exclude (repeatable)",
    )
    scope.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="do not skip standard ignored directories (.git, .venv, __pycache__, ...)",
    )
    scope.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="process only the first N discovered files",
    )
    perf = ap.add_argument_group("performance")
    perf.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        metavar="N",
        help="parallel worker processes, 0 for auto (default: %(default)s); "
        "two-file micro-batches under 32 KiB run sequentially to avoid "
        "worker startup overhead",
    )
    perf.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop processing on the first file error",
    )
    return ap


def _emit_verbose(results: list[FileResult]) -> None:
    """Print detailed per-file status and edits to stdout."""
    for res in results:
        status = res["status"]
        file_str = res["file"]
        if status == "changed":
            count = len(res["edits"])
            extra = f" (+imports: {', '.join(res['imports'])})" if res["imports"] else ""
            print(f"changed: {file_str} ({count} edits){extra}")
        elif status == "no-change":
            print(f"ok: {file_str}")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI annotator and return a process exit code."""
    ap = build_parser()
    args = ap.parse_args(argv)
    write = not args.dry_run and not args.check
    backup = bool(args.backup and write)
    add_future = not args.no_future
    files = expand_paths(
        args.files,
        args.limit,
        args.exclude,
        use_default_excludes=not args.no_default_excludes,
    )
    jobs = 1 if args.fail_fast else resolve_workers(args.jobs, len(files))
    passes = max(1, args.passes)
    want_diff = bool(args.diff)
    results: list[FileResult]
    if args.progress:
        results = drain_with_progress(
            files,
            write,
            jobs,
            passes,
            want_diff,
            backup=backup,
            style=args.style,
            add_future=add_future,
            strip=args.strip,
            fail_fast=args.fail_fast,
        )
        for result in results:
            report_failure(result)
    else:
        results = []
        for result in process_files(
            files,
            write,
            jobs,
            passes,
            want_diff,
            backup=backup,
            style=args.style,
            add_future=add_future,
            strip=args.strip,
        ):
            results.append(result)
            report_failure(result)
            if args.fail_fast and result["status"] in ERROR_STATUSES:
                break
    results.sort(key=lambda r: r["file"])
    if want_diff:
        emit_diffs(results, args.color)
    if args.verbose and not args.quiet and args.json != "-":
        _emit_verbose(results)
    if args.json == "-":
        sys.stdout.write(json.dumps(results, indent=1) + "\n")
    elif args.json:
        Path(args.json).write_text(json.dumps(results, indent=1), encoding="utf-8")
    changed, edits, failed = summarize(results)
    if not args.quiet and args.json != "-":
        added = sum(r.get("added", 0) for r in results)
        removed = sum(r.get("removed", 0) for r in results)
        tally = f", +{added}/-{removed}" if want_diff else ""
        action = " [strip]" if args.strip else ""
        mode = " [check]" if args.check else (" [dry-run]" if args.dry_run else "")
        print(f"processed {len(results)} files, changed {changed}, edits {edits}, failed {failed}{tally}{action}{mode}")
    if failed or (args.check and changed > 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
