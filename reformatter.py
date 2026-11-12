#!/data/data/com.termux/files/usr/bin/env python
"""Automatic Python indentation and newline reconstruction tool.

This module repairs Python source files whose newlines and/or leading
indentations have been stripped or collapsed onto single lines (for example,
when copied from logs, chat transcripts, or broken formatters).

Architecture
------------
The repair pipeline operates in three compiler-validated stages:

1. **Lexical Pre-Segmentation**:
   - Scans raw text while tracking Python string literals (including raw,
     byte, f-string, single-quoted, and triple-quoted strings) so characters
     inside string literals are never altered.
   - Isolates embedded shell heredoc transition bridges (such as
     ``PY cd ... && python3 - <<'EOF'``) into non-destructive comment lines.
   - Splits top-level physical lines only outside strings, outside brackets
     ``(...)`` / ``[...]`` / ``{...}``, and outside backslash continuations.
   - Uses a two-pass comment strategy: multi-line inputs first preserve
     existing ``#`` comment boundaries; collapsed single-line inputs recover
     code glued onto the end of ``#`` comments by finding the earliest
     syntactically valid Python continuation.

2. **Grammar-Exact Statement & Header Segmentation**:
   - Tokenizes code chunks with :mod:`tokenize` while tracking bracket depth
     and ``lambda`` parameter colons.
   - Distinguishes statement ``if ...:`` headers from ternary
     ``expr if cond else expr`` expressions at bracket depth 0 via exact
     ``if``/``else`` balance counting.
   - Distinguishes ``@decorator`` lines from ``A @ B`` matrix multiplication.
   - Segments runs of collapsed simple statements using memoized dynamic
     programming validated by :func:`ast.parse`.

3. **Dataflow-Guided Beam Search Indentation Reconstruction**:
   - Analyzes each logical line's AST for variable loads, stores, attribute
     accesses, ``isinstance``/``hasattr`` type guards, loop/function/async
     legality requirements, method receivers (``self``/``cls``), self-calls,
     and terminal control-flow transfers (``return``, ``raise``, ``break``,
     ``continue``).
   - Performs beam search over immutable block-frame stacks enforcing all hard
     Python suite and clause rules (``if``/``elif``/``else``,
     ``try``/``except``/``else``/``finally``, ``match``/``case``, loops,
     decorators) while scoring variable scope and control-flow idioms.
   - Verifies the winning reconstruction with both :func:`ast.parse` and
     :func:`compile`.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import functools
import io
import json
import pathlib
import re
import sys
import time
import tokenize
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace

BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins))

STRING_PREFIXES: frozenset[str] = frozenset({"r", "u", "f", "b", "fr", "rf", "br", "rb"})

ALWAYS_COMPOUND_KEYWORDS: frozenset[str] = frozenset({
    "def",
    "class",
    "elif",
    "try",
    "except",
    "finally",
    "while",
    "with",
    "async",
    "for",
})

METHOD_DECORATOR_NAMES: frozenset[str] = frozenset({
    "staticmethod",
    "classmethod",
    "property",
    "cached_property",
    "abstractmethod",
    "overload",
})

HEREDOC_BRIDGE_RE: re.Pattern[str] = re.compile(
    r"(PY|EOF|HEREDOC|END_PY|END_PYTHON)\s+[^\n]*?\bpython3?\b[^\n]*?"
    r"<<-?\s*(?:'[A-Za-z_]\w*'|\"[A-Za-z_]\w*\"|[A-Za-z_]\w*)"
)

EXCLUDED_SCAN_DIRS: frozenset[str] = frozenset({
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "result",
    "tests",
})


class ReformatterError(ValueError):
    """Raised when a source file cannot be reconstructed into valid Python."""


@dataclass(frozen=True)
class StructuralCounts:
    """AST structural counts collected from a valid Python module."""

    ast_nodes: int
    statements: int
    functions: int
    classes: int
    loops: int
    conditionals: int
    try_blocks: int


@dataclass(frozen=True)
class FileStats:
    """Detailed statistics for a single processed Python file."""

    path: str
    status: str
    changed: bool
    was_valid_before: bool
    is_valid_after: bool
    original_lines: int
    fixed_lines: int
    original_bytes: int
    fixed_bytes: int
    logical_statements: int
    comments_preserved: int
    inline_comments_split: int
    shell_bridges_commented: int
    max_indent_level: int
    functions: int
    classes: int
    loops: int
    conditionals: int
    try_blocks: int
    ast_nodes: int
    elapsed_ms: float
    error: str = ""


@dataclass(frozen=True)
class BatchStats:
    """Aggregate statistics across a batch of processed Python files."""

    total_files: int
    fixed_files: int
    unchanged_files: int
    failed_files: int
    original_total_lines: int
    fixed_total_lines: int
    original_total_bytes: int
    fixed_total_bytes: int
    total_logical_statements: int
    total_comments_preserved: int
    total_inline_comments_split: int
    total_shell_bridges_commented: int
    max_indent_observed: int
    total_functions: int
    total_classes: int
    total_loops: int
    total_conditionals: int
    total_try_blocks: int
    total_ast_nodes: int
    total_elapsed_ms: float
    files: tuple[FileStats, ...]


@dataclass(frozen=True)
class LogicalChunk:
    """A single reconstructed logical line with optional trailing comment."""

    code: str
    inline_comment: str = ""
    blank_lines_before: int = 0


@dataclass(frozen=True)
class ParsedLine:
    """Static AST & dataflow summary for one logical line."""

    text: str
    inline_comment: str
    kind: str
    subkind: str
    defined_name: str = ""
    reads: frozenset[str] = frozenset()
    attr_reads: frozenset[tuple[str, str]] = frozenset()
    calls: frozenset[str] = frozenset()
    writes: frozenset[str] = frozenset()
    frame_bound: frozenset[str] = frozenset()
    cond_vars: frozenset[str] = frozenset()
    isinstance_vars: frozenset[str] = frozenset()
    hasattr_pairs: frozenset[tuple[str, str]] = frozenset()
    cond_sig: tuple[str, str] | None = None
    global_decl: frozenset[str] = frozenset()
    is_terminal: bool = False
    is_return_or_raise: bool = False
    needs_func: bool = False
    needs_nonlocal: bool = False
    is_global_stmt: bool = False
    needs_async: bool = False
    needs_loop: bool = False
    is_method_def: bool = False
    is_method_decorator: bool = False
    is_main_guard: bool = False
    is_valueless_ann: bool = False
    is_ann_assign: bool = False
    is_docstring: bool = False
    is_import: bool = False
    blank_lines_before: int = 0


@dataclass(frozen=True)
class BlockFrame:
    """Immutable representation of an open compound block on the indent stack."""

    kind: str
    clause: str = "body"
    had_except: bool = False
    name: str = ""
    params: frozenset[str] = frozenset()
    bound_vars: frozenset[str] = frozenset()
    cond_vars: frozenset[str] = frozenset()
    local_vars: frozenset[str] = frozenset()
    shared_vars: frozenset[str] = frozenset()
    unread_writes: frozenset[str] = frozenset()
    global_vars: frozenset[str] = frozenset()
    isinstance_vars: frozenset[str] = frozenset()
    hasattr_pairs: frozenset[tuple[str, str]] = frozenset()
    cond_sig: tuple[str, str] | None = None
    stmt_count: int = 0
    only_docstring: bool = False
    has_methods: bool = False
    terminal: bool = False


@dataclass(frozen=True)
class SearchState:
    """Beam-search state tracking open block frames, scope bindings, and score."""

    stack: tuple[BlockFrame, ...]
    module_vars: frozenset[str]
    module_unread: frozenset[str]
    pending_dec: int | None
    pending_method_dec: bool
    score: float
    indents: tuple[int, ...]


@dataclass(frozen=True)
class ReformatResult:
    """Result of reconstructing a Python source string in memory."""

    source: str
    was_valid_before: bool
    changed: bool
    logical_statements: int
    comments_preserved: int
    inline_comments_split: int
    shell_bridges_commented: int
    max_indent_level: int
    counts: StructuralCounts


# ---------------------------------------------------------------------------
# Stage 0: Compilation & AST Structural Inspection Helpers
# ---------------------------------------------------------------------------


def is_compilable_python(source: str, filename: str = "<source>") -> bool:
    """Return ``True`` if ``source`` both parses and compiles cleanly."""
    try:
        tree = ast.parse(source, filename=filename)
        compile(tree, filename=filename, mode="exec")
        return True
    except (SyntaxError, ValueError):
        return False


def collect_structural_counts(tree: ast.AST) -> StructuralCounts:
    """Count AST nodes, statements, definitions, loops, conditionals, and try blocks."""
    ast_nodes = 0
    statements = 0
    functions = 0
    classes = 0
    loops = 0
    conditionals = 0
    try_blocks = 0

    try_types: tuple[type[ast.AST], ...] = (ast.Try, ast.TryStar) if hasattr(ast, "TryStar") else (ast.Try,)

    for node in ast.walk(tree):
        ast_nodes += 1
        if isinstance(node, ast.stmt):
            statements += 1
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions += 1
        elif isinstance(node, ast.ClassDef):
            classes += 1
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            loops += 1
        elif isinstance(node, (ast.If, ast.Match)):
            conditionals += 1
        elif isinstance(node, try_types):
            try_blocks += 1

    return StructuralCounts(
        ast_nodes=ast_nodes,
        statements=statements,
        functions=functions,
        classes=classes,
        loops=loops,
        conditionals=conditionals,
        try_blocks=try_blocks,
    )


def count_physical_lines(text: str) -> int:
    """Return the number of physical lines in ``text``."""
    if not text:
        return 0
    return len(text.splitlines())


# ---------------------------------------------------------------------------
# Stage 1: String-Aware Lexical Pre-Segmentation
# ---------------------------------------------------------------------------


def _match_string_literal(s: str, i: int, *, require_closed: bool = True) -> int | None:
    """Return the end index of a Python string literal starting at ``s[i]``, or ``None``."""
    n = len(s)
    if i > 0 and (s[i - 1].isalnum() or s[i - 1] == "_"):
        return None

    p_end = i
    while p_end < n and p_end - i < 2 and s[p_end].isalpha():
        p_end += 1
    prefix = s[i:p_end].lower()
    if prefix and prefix not in STRING_PREFIXES:
        return None
    if p_end >= n or s[p_end] not in ("'", '"'):
        return None

    quote = s[p_end : p_end + 3] if s[p_end : p_end + 3] in ("'''", '"""') else s[p_end]
    j = p_end + len(quote)
    while j < n:
        if s[j] == "\\":
            j += 2
            continue
        if len(quote) == 1 and s[j] == "\n":
            return None if require_closed else j
        if s.startswith(quote, j):
            return j + len(quote)
        j += 1
    return None if require_closed else n


def isolate_shell_heredoc_bridges(raw: str) -> tuple[str, int]:
    """Turn embedded shell heredoc transitions outside Python strings into comments."""
    i = 0
    n = len(raw)
    buf: list[str] = []
    bridges = 0

    while i < n:
        if raw.startswith("# [shell]", i):
            nl = raw.find("\n", i)
            end = n if nl == -1 else nl + 1
            buf.append(raw[i:end])
            i = end
            continue

        str_end = _match_string_literal(raw, i, require_closed=True)
        if str_end is not None:
            buf.append(raw[i:str_end])
            i = str_end
            continue

        if i == 0 or raw[i - 1].isspace():
            m = HEREDOC_BRIDGE_RE.match(raw, i)
            if m is not None:
                bridge_text = " ".join(m.group(0).strip().splitlines())
                buf.append(f"\n# [shell] {bridge_text}\n")
                i = m.end()
                bridges += 1
                continue

        buf.append(raw[i])
        i += 1

    return "".join(buf), bridges


def split_top_level_physical_lines(source: str) -> list[str]:
    """Split ``source`` on physical newlines outside strings, brackets, and ``\\`` continuations."""
    n = len(source)
    chunks: list[str] = []
    start = 0
    depth = 0
    in_comment = False
    i = 0

    while i < n:
        if in_comment:
            if source[i] == "\n":
                in_comment = False
                if depth == 0:
                    chunks.append(source[start:i])
                    start = i + 1
            i += 1
            continue

        str_end = _match_string_literal(source, i, require_closed=False)
        if str_end is not None:
            i = str_end
            continue

        ch = source[i]
        if ch == "#":
            in_comment = True
            i += 1
            continue
        if ch in ("(", "[", "{"):
            depth += 1
        elif ch in (")", "]", "}"):
            depth = max(0, depth - 1)
        elif ch == "\\" and i + 1 < n and source[i + 1] == "\n":
            i += 2
            continue
        elif ch == "\\" and i + 2 < n and source[i + 1] == "\r" and source[i + 2] == "\n":
            i += 3
            continue
        elif ch == "\n" and depth == 0:
            chunks.append(source[start:i])
            start = i + 1
        i += 1

    if start < n:
        chunks.append(source[start:])
    return chunks


def _find_hashes_outside_strings(
    line: str,
    *,
    final_line_only: bool = False,
) -> list[int]:
    """Return character offsets of top-level ``#`` outside string literals in ``line``."""
    positions: list[int] = []
    last_nl = line.rfind("\n") if final_line_only else -1
    depth = 0
    i = 0
    n = len(line)
    while i < n:
        str_end = _match_string_literal(line, i, require_closed=True)
        if str_end is not None:
            i = str_end
            continue
        ch = line[i]
        if ch in ("(", "[", "{"):
            depth += 1
        elif ch in (")", "]", "}"):
            depth = max(0, depth - 1)
        elif ch == "#":
            if final_line_only:
                if i > last_nl and depth == 0:
                    positions.append(i)
                else:
                    nl = line.find("\n", i)
                    i = n if nl == -1 else nl + 1
                    continue
            else:
                positions.append(i)
        i += 1
    return positions


# ---------------------------------------------------------------------------
# Stage 2: Token & AST-Validated Statement / Header Segmentation
# ---------------------------------------------------------------------------


def _tokenize_clean(code: str) -> list[tokenize.TokenInfo] | None:
    """Tokenize ``code`` into significant lexical tokens, returning ``None`` on lexical error."""
    try:
        toks: list[tokenize.TokenInfo] = []
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type == tokenize.ERRORTOKEN:
                return None
            if tok.type in (
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENCODING,
                tokenize.ENDMARKER,
                tokenize.COMMENT,
            ):
                continue
            toks.append(tok)
        return toks
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None


def _reconstruct_slice(
    code: str,
    toks: Sequence[tokenize.TokenInfo],
    start_idx: int,
    end_idx: int,
) -> str:
    """Extract exact source substring spanning ``toks[start_idx : end_idx]``."""
    lines = code.splitlines(keepends=True)
    if not lines:
        return ""
    s_row, s_col = toks[start_idx].start
    e_row, e_col = toks[end_idx - 1].end
    if s_row == e_row:
        return lines[s_row - 1][s_col:e_col].strip()
    parts = [lines[s_row - 1][s_col:]]
    for row in range(s_row, e_row - 1):
        parts.append(lines[row])
    parts.append(lines[e_row - 1][:e_col])
    return "".join(parts).strip()


def _compute_depths(toks: Sequence[tokenize.TokenInfo]) -> tuple[list[int], list[int]]:
    """Compute bracket depth immediately before and immediately after each token."""
    before: list[int] = []
    after: list[int] = []
    depth = 0
    for tok in toks:
        before.append(depth)
        if tok.type == tokenize.OP and tok.string in ("(", "[", "{"):
            depth += 1
        elif tok.type == tokenize.OP and tok.string in (")", "]", "}"):
            depth -= 1
        after.append(depth)
    return before, after


def _find_lambda_colons(
    toks: Sequence[tokenize.TokenInfo],
    before: Sequence[int],
) -> set[int]:
    """Return token indices of ``:`` that terminate ``lambda`` parameter lists."""
    colons: set[int] = set()
    n = len(toks)
    for i, tok in enumerate(toks):
        if tok.type == tokenize.NAME and tok.string == "lambda":
            lam_depth = before[i]
            for j in range(i + 1, n):
                if before[j] < lam_depth:
                    break
                if before[j] == lam_depth and toks[j].type == tokenize.OP and toks[j].string == ":":
                    colons.add(j)
                    break
    return colons


def _is_statement_if(
    toks: Sequence[tokenize.TokenInfo],
    before: Sequence[int],
    lam_colons: set[int],
    idx: int,
) -> int | None:
    """Return the suite ``:`` index if ``toks[idx]`` is a statement ``if``, else ``None``."""
    balance = 1
    n = len(toks)
    for j in range(idx + 1, n):
        if before[j] != 0:
            continue
        tj = toks[j]
        if tj.type == tokenize.NAME and tj.string == "if":
            balance += 1
        elif tj.type == tokenize.NAME and tj.string == "else":
            is_else_suite = (
                j + 1 < n and before[j + 1] == 0 and toks[j + 1].type == tokenize.OP and toks[j + 1].string == ":"
            )
            if is_else_suite:
                return None
            balance -= 1
            if balance == 0:
                return None
        elif tj.type == tokenize.OP and tj.string == ":" and j not in lam_colons:
            return j
    return None


def _find_header_colon(
    toks: Sequence[tokenize.TokenInfo],
    before: Sequence[int],
    lam_colons: set[int],
    start: int,
) -> int | None:
    """Return the index of the first depth-0 non-lambda ``:`` after ``start``."""
    n = len(toks)
    for j in range(start + 1, n):
        if before[j] == 0 and toks[j].type == tokenize.OP and toks[j].string == ":" and j not in lam_colons:
            return j
    return None


def _indent_probe_block(header_src: str, spaces: int) -> str:
    """Indent all lines of ``header_src`` by ``spaces`` spaces for synthetic AST probing."""
    pad = " " * spaces
    return "\n".join(pad + ln for ln in header_src.splitlines())


def _parse_header_probe(header_src: str) -> ast.Module | None:
    """Validate and parse a candidate compound header string by attaching a dummy suite."""
    m = re.match(r"^[A-Za-z_]\w*", header_src)
    if m is None:
        return None
    kw = m.group(0)
    if kw == "try":
        probe = f"{header_src}\n    pass\nfinally:\n    pass\n"
    elif kw in ("elif", "else"):
        probe = f"if True:\n    pass\n{header_src}\n    pass\n"
    elif kw in ("except", "finally"):
        probe = f"try:\n    pass\n{header_src}\n    pass\n"
    elif kw == "case":
        indented = _indent_probe_block(header_src, 4)
        probe = f"match _subj:\n{indented}\n        pass\n"
    elif kw == "match":
        probe = f"{header_src}\n    case _:\n        pass\n"
    elif kw == "async":
        indented = _indent_probe_block(header_src, 4)
        probe = f"async def _w():\n{indented}\n        pass\n"
    else:
        probe = f"{header_src}\n    pass\n"

    try:
        return ast.parse(probe)
    except SyntaxError:
        return None


def _is_valid_simple_line(
    stmt_src: str,
    *,
    is_split: bool = False,
    from_comment: bool = False,
) -> bool:
    """Return ``True`` if ``stmt_src`` parses as one or more valid simple statements."""
    if not stmt_src:
        return False
    try:
        tree = ast.parse(stmt_src)
        body = tree.body
    except SyntaxError:
        try:
            indented = _indent_probe_block(stmt_src, 4)
            wrapper = ast.parse(f"async def _w():\n{indented}\n")
            fn = wrapper.body[0]
            if not isinstance(fn, ast.AsyncFunctionDef):
                return False
            body = fn.body
        except SyntaxError:
            return False

    if not body:
        return False

    for node in body:
        if not isinstance(
            node,
            (
                ast.Assign,
                ast.AugAssign,
                ast.AnnAssign,
                ast.Expr,
                ast.Import,
                ast.ImportFrom,
                ast.Return,
                ast.Raise,
                ast.Assert,
                ast.Delete,
                ast.Pass,
                ast.Break,
                ast.Continue,
                ast.Global,
                ast.Nonlocal,
            ),
        ):
            if type(node).__name__ != "TypeAlias":
                return False

        if from_comment and isinstance(node, ast.AnnAssign) and node.value is None:
            return False

        if (is_split or from_comment) and isinstance(node, ast.Expr):
            val = node.value
            if isinstance(val, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)):
                continue
            if (
                not from_comment
                and isinstance(val, ast.Constant)
                and (isinstance(val.value, str) or val.value is Ellipsis)
            ):
                continue
            return False

    return True


def _segment_simple_run(
    code: str,
    toks: Sequence[tokenize.TokenInfo],
    after: Sequence[int],
    start: int,
    end: int,
    *,
    from_comment: bool = False,
) -> list[str] | None:
    """Segment ``toks[start:end]`` into simple statements via DP backed by ``ast.parse``."""
    if start >= end:
        return []
    if not from_comment:
        whole = _reconstruct_slice(code, toks, start, end)
        if _is_valid_simple_line(whole, is_split=False, from_comment=False):
            return [whole]

    @functools.lru_cache(maxsize=None)
    def solve(i: int, first_piece: bool) -> tuple[int, tuple[str, ...]] | None:
        """Return ``(count, segments)`` for ``toks[i:end]``, or ``None``."""
        if i == end:
            return (0, ())
        ti = toks[i]
        if i > start and ti.type == tokenize.OP and ti.string not in ("~", "*", "..."):
            return None

        for j in range(end, i, -1):
            if after[j - 1] != 0:
                continue
            if j < end and toks[j].type == tokenize.OP and toks[j].string in ("(", "["):
                continue
            src_ij = _reconstruct_slice(code, toks, i, j)
            splitting = (i > start) or (j < end)
            if not _is_valid_simple_line(
                src_ij,
                is_split=splitting,
                from_comment=(from_comment and first_piece),
            ):
                continue
            rest = solve(j, False)
            if rest is not None:
                return (1 + rest[0], (src_ij,) + rest[1])
        return None

    ans = solve(start, True)
    return list(ans[1]) if ans is not None else None


def segment_code_chunk(code: str, *, from_comment: bool = False) -> list[str] | None:
    """Segment a comment-free Python chunk into decorators, headers, and simple statements."""
    if not code.strip():
        return []
    toks = _tokenize_clean(code)
    if toks is None:
        return None
    if not toks:
        return []

    before, after = _compute_depths(toks)
    if after[-1] != 0 or any(d < 0 for d in after):
        return None

    lam_colons = _find_lambda_colons(toks, before)
    n = len(toks)
    out: list[str] = []
    simple_start = 0
    i = 0

    def flush_simple(upto: int) -> bool:
        """Segment and append any pending simple-statement tokens before ``upto``."""
        if simple_start < upto:
            segs = _segment_simple_run(
                code,
                toks,
                after,
                simple_start,
                upto,
                from_comment=(from_comment and not out),
            )
            if segs is None:
                return False
            out.extend(segs)
        return True

    while i < n:
        if before[i] != 0:
            i += 1
            continue
        tok = toks[i]

        # Distinguish `@decorator` from binary matrix multiplication `A @ B`
        if tok.type == tokenize.OP and tok.string == "@":
            dec_end: int | None = None
            for j in range(i + 1, n):
                if before[j] != 0:
                    continue
                if (toks[j].type == tokenize.OP and toks[j].string == "@") or (
                    toks[j].type == tokenize.NAME and toks[j].string in ("def", "async", "class")
                ):
                    dec_end = j
                    break

            # Standalone decorator on its own physical line (e.g. `@property\ndef f(self):`)
            if dec_end is None and i == 0:
                dec_end = n

            if dec_end is not None and dec_end > i + 1:
                has_space_after_at = tok.end != toks[i + 1].start
                is_matmult = False
                if i > simple_start and has_space_after_at:
                    mat_candidate = _reconstruct_slice(code, toks, simple_start, dec_end)
                    if _is_valid_simple_line(mat_candidate):
                        is_matmult = True

                if not is_matmult:
                    dec_src = _reconstruct_slice(code, toks, i, dec_end)
                    try:
                        ast.parse(f"{dec_src}\ndef _f():\n    pass\n")
                        valid_dec = True
                    except SyntaxError:
                        valid_dec = False
                    if valid_dec:
                        if not flush_simple(i):
                            return None
                        out.append(dec_src)
                        i = dec_end
                        simple_start = i
                        continue

        header_colon: int | None = None
        if tok.type == tokenize.NAME:
            kw = tok.string
            if kw in ALWAYS_COMPOUND_KEYWORDS:
                header_colon = _find_header_colon(toks, before, lam_colons, i)
            elif kw == "else":
                if i + 1 < n and before[i + 1] == 0 and toks[i + 1].type == tokenize.OP and toks[i + 1].string == ":":
                    header_colon = i + 1
            elif kw == "if":
                header_colon = _is_statement_if(toks, before, lam_colons, i)
            elif kw in ("match", "case"):
                # Soft keywords `match` and `case`: check if valid compound header
                c = _find_header_colon(toks, before, lam_colons, i)
                if c is not None:
                    hdr_candidate = _reconstruct_slice(code, toks, i, c + 1)
                    if _parse_header_probe(hdr_candidate) is not None:
                        if kw == "case":
                            if i == simple_start or flush_simple_possible(
                                code, toks, after, simple_start, i, from_comment and not out
                            ):
                                header_colon = c
                        elif (
                            c + 1 < n and toks[c + 1].type == tokenize.NAME and toks[c + 1].string == "case"
                        ) or c == n - 1:
                            if i == simple_start or flush_simple_possible(
                                code, toks, after, simple_start, i, from_comment and not out
                            ):
                                header_colon = c

        if header_colon is not None:
            if not flush_simple(i):
                return None
            hdr_src = _reconstruct_slice(code, toks, i, header_colon + 1)
            if _parse_header_probe(hdr_src) is None:
                return None
            out.append(hdr_src)
            i = header_colon + 1
            simple_start = i
            continue

        i += 1

    if not flush_simple(n):
        return None
    return out


def flush_simple_possible(
    code: str,
    toks: Sequence[tokenize.TokenInfo],
    after: Sequence[int],
    start: int,
    upto: int,
    from_comment: bool,
) -> bool:
    """Return ``True`` if ``toks[start:upto]`` can be segmented into valid simple statements."""
    return _segment_simple_run(code, toks, after, start, upto, from_comment=from_comment) is not None


def extract_logical_chunks(
    source_after_bridges: str,
    *,
    split_inline_comments: bool,
) -> tuple[list[LogicalChunk], int] | None:
    """Segment pre-processed source into logical chunks.

    Args:
        source_after_bridges: Source text with shell heredoc bridges already isolated.
        split_inline_comments: If ``True``, recover Python statements collapsed onto
            the end of ``# ...`` comments; if ``False``, treat ``# ...`` to end-of-line
            as intact comments.

    Returns:
        Tuple ``(chunks, inline_comments_split_count)`` on success, or ``None`` if
        any code segment cannot be lexed/parsed into valid statements.
    """
    phys_lines = split_top_level_physical_lines(source_after_bridges)
    chunks: list[LogicalChunk] = []
    comments_split = 0
    pending_blanks = 0

    for phys in phys_lines:
        stripped = phys.strip()
        if not stripped:
            pending_blanks += 1
            continue
        blanks = pending_blanks
        pending_blanks = 0

        if stripped.startswith("# [shell]"):
            chunks.append(LogicalChunk(code=stripped, blank_lines_before=blanks))
            continue

        is_multiline_chunk = "\n" in stripped
        hashes = _find_hashes_outside_strings(
            stripped,
            final_line_only=(is_multiline_chunk and not split_inline_comments),
        )
        if not hashes:
            segs = segment_code_chunk(stripped)
            if segs is None:
                return None
            for idx_s, s in enumerate(segs):
                chunks.append(LogicalChunk(code=s, blank_lines_before=(blanks if idx_s == 0 else 0)))
            continue

        if not split_inline_comments:
            prefix = stripped[: hashes[0]].strip()
            comment_tail = stripped[hashes[0] :].strip()
            if not prefix:
                chunks.append(LogicalChunk(code=comment_tail, blank_lines_before=blanks))
            else:
                segs = segment_code_chunk(prefix)
                if segs is None or not segs:
                    return None
                for idx_s, s in enumerate(segs[:-1]):
                    chunks.append(LogicalChunk(code=s, blank_lines_before=(blanks if idx_s == 0 else 0)))
                chunks.append(
                    LogicalChunk(
                        code=segs[-1],
                        inline_comment=comment_tail,
                        blank_lines_before=(blanks if len(segs) == 1 else 0),
                    )
                )
            continue

        # Collapsed-comment recovery mode
        prefix = stripped[: hashes[0]].strip()
        if prefix:
            segs = segment_code_chunk(prefix)
            if segs is None:
                return None
            chunks.extend(LogicalChunk(code=s) for s in segs)

        h_idx = 0
        span_start = hashes[0]
        while h_idx < len(hashes):
            span_end = hashes[h_idx + 1] if h_idx + 1 < len(hashes) else len(stripped)
            span = stripped[span_start:span_end]

            split_ok = False
            for m_ws in re.finditer(r"\s+", span):
                comment_part = span[: m_ws.start()].strip()
                if comment_part == "#":
                    continue
                code_part = span[m_ws.end() :].strip()
                if not code_part:
                    continue
                segs = segment_code_chunk(code_part, from_comment=True)
                if segs is not None and segs:
                    chunks.append(LogicalChunk(code=comment_part))
                    chunks.extend(LogicalChunk(code=s) for s in segs)
                    comments_split += 1
                    split_ok = True
                    break

            if split_ok:
                h_idx += 1
                if h_idx < len(hashes):
                    span_start = hashes[h_idx]
            else:
                # If another `#` follows on the same physical line without valid code
                # in between (e.g. `# issue #42: fix bug x = 1`), merge into one span.
                if h_idx + 1 < len(hashes):
                    h_idx += 1
                else:
                    chunks.append(LogicalChunk(code=span.strip()))
                    h_idx += 1

    return chunks, comments_split


# ---------------------------------------------------------------------------
# Stage 3: AST Dataflow Analysis & Beam Search Indentation Reconstruction
# ---------------------------------------------------------------------------


def _extract_expr_reads_and_attrs(
    roots: Sequence[ast.AST],
) -> tuple[frozenset[str], frozenset[tuple[str, str]], frozenset[str]]:
    """Extract outer variable loads, ``(var, attr)`` reads, and direct ``fn(...)`` calls."""
    loads: set[str] = set()
    attrs: set[tuple[str, str]] = set()
    calls: set[str] = set()

    def collect_targets(target: ast.AST, out: set[str]) -> None:
        """Collect identifier names bound by an assignment or comprehension target."""
        if isinstance(target, ast.Name):
            out.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                collect_targets(elt, out)
        elif isinstance(target, ast.Starred):
            collect_targets(target.value, out)

    def visit(node: ast.AST, local_bound: frozenset[str]) -> None:
        """Recursively collect outer loads, attribute accesses, and direct calls."""
        if isinstance(node, ast.Lambda):
            args = node.args
            lbound = set(local_bound)
            for p in args.posonlyargs + args.args + args.kwonlyargs:
                lbound.add(p.arg)
            if args.vararg:
                lbound.add(args.vararg.arg)
            if args.kwarg:
                lbound.add(args.kwarg.arg)
            for def_val in args.defaults:
                visit(def_val, local_bound)
            for kw_def in args.kw_defaults:
                if kw_def is not None:
                    visit(kw_def, local_bound)
            visit(node.body, frozenset(lbound))
            return

        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            cur = set(local_bound)
            for gen in node.generators:
                visit(gen.iter, frozenset(cur))
                collect_targets(gen.target, cur)
                for cond in gen.ifs:
                    visit(cond, frozenset(cur))
            if isinstance(node, ast.DictComp):
                visit(node.key, frozenset(cur))
                visit(node.value, frozenset(cur))
            else:
                visit(node.elt, frozenset(cur))
            return

        if isinstance(node, ast.NamedExpr):
            visit(node.value, local_bound)
            return

        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in local_bound and node.id not in BUILTIN_NAMES:
                loads.add(node.id)
            return

        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id not in local_bound
                and node.value.id not in BUILTIN_NAMES
            ):
                attrs.add((node.value.id, node.attr))

        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id not in local_bound
                and node.func.id not in BUILTIN_NAMES
            ):
                calls.add(node.func.id)

        for child in ast.iter_child_nodes(node):
            visit(child, local_bound)

    for root in roots:
        visit(root, frozenset())

    return frozenset(loads), frozenset(attrs), frozenset(calls)


def _extract_target_stores(
    target: ast.AST,
    stores: set[str],
    expr_roots: list[ast.AST],
) -> None:
    """Record stored names in ``stores`` and receiver expressions in ``expr_roots``."""
    if isinstance(target, ast.Name):
        stores.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _extract_target_stores(elt, stores, expr_roots)
    elif isinstance(target, ast.Starred):
        _extract_target_stores(target.value, stores, expr_roots)
    elif isinstance(target, ast.Attribute):
        expr_roots.append(target.value)
    elif isinstance(target, ast.Subscript):
        expr_roots.append(target.value)
        expr_roots.append(target.slice)


def _analyze_condition(
    test: ast.AST,
) -> tuple[frozenset[str], frozenset[tuple[str, str]], tuple[str, str] | None, bool]:
    """Analyze an ``if``/``elif``/``while`` test for type narrowing and ``__main__`` guard."""
    isinst: set[str] = set()
    hasat: set[tuple[str, str]] = set()
    sig: tuple[str, str] | None = None
    is_main = False

    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
    ):
        left, right = test.left, test.comparators[0]
        if (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
        ) or (
            isinstance(right, ast.Name)
            and right.id == "__name__"
            and isinstance(left, ast.Constant)
            and left.value == "__main__"
        ):
            is_main = True

    for node in ast.walk(test):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "isinstance" and len(node.args) >= 2 and isinstance(node.args[0], ast.Name):
                isinst.add(node.args[0].id)
                if sig is None:
                    sig = ("isinstance", node.args[0].id)
            elif (
                node.func.id == "hasattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                hasat.add((node.args[0].id, node.args[1].value))
                if sig is None:
                    sig = ("hasattr", node.args[0].id)

    if sig is None:
        if isinstance(test, ast.Name):
            sig = ("truthy", test.id)
        elif isinstance(test, ast.Attribute) and isinstance(test.value, ast.Name):
            sig = ("attr", test.value.id)

    return frozenset(isinst), frozenset(hasat), sig, is_main


def analyze_chunk(chunk: LogicalChunk) -> ParsedLine:
    """Build a :class:`ParsedLine` descriptor for a segmented :class:`LogicalChunk`."""
    text = chunk.code
    inline_comment = chunk.inline_comment

    if text.startswith("#"):
        return ParsedLine(
            text=text,
            inline_comment=inline_comment,
            kind="comment",
            subkind="comment",
            blank_lines_before=chunk.blank_lines_before,
        )

    if text.startswith("@"):
        tree = ast.parse(f"{text}\ndef _d():\n    pass\n")
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        dec_expr = fn.decorator_list[0]
        if isinstance(dec_expr, ast.Call):
            dec_head = dec_expr.func
        else:
            dec_head = dec_expr
        is_method_dec = False
        if isinstance(dec_head, ast.Name) and dec_head.id in METHOD_DECORATOR_NAMES:
            is_method_dec = True
        elif isinstance(dec_head, ast.Attribute) and (
            dec_head.attr in METHOD_DECORATOR_NAMES or dec_head.attr in ("setter", "deleter", "getter")
        ):
            is_method_dec = True
        loads, attrs, calls = _extract_expr_reads_and_attrs(list(fn.decorator_list))
        return ParsedLine(
            text=text,
            inline_comment=inline_comment,
            kind="decorator",
            subkind="decorator",
            reads=loads,
            attr_reads=attrs,
            calls=calls,
            is_method_decorator=is_method_dec,
            blank_lines_before=chunk.blank_lines_before,
        )

    if text.endswith(":"):
        m_kw = re.match(r"^[A-Za-z_]\w*", text)
        kw = m_kw.group(0) if m_kw is not None else ""
        probe = _parse_header_probe(text)
        if probe is not None and (kw in ("elif", "else", "except", "finally", "case") or text.startswith("except*")):
            expr_roots: list[ast.AST] = []
            frame_bound: set[str] = set()
            isinst: frozenset[str] = frozenset()
            hasat: frozenset[tuple[str, str]] = frozenset()
            sig: tuple[str, str] | None = None

            if kw == "elif":
                if_node = probe.body[0]
                assert isinstance(if_node, ast.If)
                elif_node = if_node.orelse[0]
                assert isinstance(elif_node, ast.If)
                expr_roots.append(elif_node.test)
                isinst, hasat, sig, _ = _analyze_condition(elif_node.test)
                for sub_w in ast.walk(elif_node.test):
                    if isinstance(sub_w, ast.NamedExpr) and isinstance(sub_w.target, ast.Name):
                        frame_bound.add(sub_w.target.id)
            elif kw == "except":
                try_node = probe.body[0]
                assert isinstance(
                    try_node,
                    (ast.Try, ast.TryStar) if hasattr(ast, "TryStar") else (ast.Try,),
                )
                handler = try_node.handlers[0]
                if handler.type is not None:
                    expr_roots.append(handler.type)
                if handler.name:
                    frame_bound.add(handler.name)
            elif kw == "case":
                match_node = probe.body[0]
                assert isinstance(match_node, ast.Match)
                case_node = match_node.cases[0]
                if case_node.guard is not None:
                    expr_roots.append(case_node.guard)
                for pat_sub in ast.walk(case_node.pattern):
                    if isinstance(pat_sub, ast.MatchAs) and pat_sub.name:
                        frame_bound.add(pat_sub.name)
                    elif isinstance(pat_sub, ast.MatchStar) and pat_sub.name:
                        frame_bound.add(pat_sub.name)
                    elif isinstance(pat_sub, ast.MatchMapping) and pat_sub.rest:
                        frame_bound.add(pat_sub.rest)

            loads, attrs, calls = _extract_expr_reads_and_attrs(expr_roots)
            return ParsedLine(
                text=text,
                inline_comment=inline_comment,
                kind="clause",
                subkind=kw,
                reads=loads,
                attr_reads=attrs,
                calls=calls,
                frame_bound=frozenset(frame_bound),
                isinstance_vars=isinst,
                hasattr_pairs=hasat,
                cond_sig=sig,
                blank_lines_before=chunk.blank_lines_before,
            )

        if probe is not None:
            stmt: ast.AST = probe.body[0]
            needs_async = False
            if kw == "async":
                assert isinstance(stmt, ast.AsyncFunctionDef)
                if text.startswith("async def ") or text.startswith("async def\n"):
                    subkind = "async_def"
                else:
                    stmt = stmt.body[0]
                    needs_async = True
                    subkind = "async_for" if isinstance(stmt, ast.AsyncFor) else "async_with"
            else:
                subkind = kw

            expr_roots = []
            stores: set[str] = set()
            frame_bound = set()
            defined_name = ""
            is_method = False
            isinst = frozenset()
            hasat = frozenset()
            sig = None
            is_main = False

            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined_name = stmt.name
                stores.add(stmt.name)
                args = stmt.args
                pos_params = args.posonlyargs + args.args
                if pos_params and pos_params[0].arg in ("self", "cls", "mcls", "mcs"):
                    is_method = True
                for param in pos_params + args.kwonlyargs:
                    frame_bound.add(param.arg)
                    if param.annotation is not None:
                        expr_roots.append(param.annotation)
                if args.vararg is not None:
                    frame_bound.add(args.vararg.arg)
                    if args.vararg.annotation is not None:
                        expr_roots.append(args.vararg.annotation)
                if args.kwarg is not None:
                    frame_bound.add(args.kwarg.arg)
                    if args.kwarg.annotation is not None:
                        expr_roots.append(args.kwarg.annotation)
                expr_roots.extend(args.defaults)
                for kwd in args.kw_defaults:
                    if kwd is not None:
                        expr_roots.append(kwd)
                if stmt.returns is not None:
                    expr_roots.append(stmt.returns)
            elif isinstance(stmt, ast.ClassDef):
                defined_name = stmt.name
                stores.add(stmt.name)
                expr_roots.extend(stmt.bases)
                for kw_node in stmt.keywords:
                    expr_roots.append(kw_node.value)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                expr_roots.append(stmt.iter)
                _extract_target_stores(stmt.target, frame_bound, expr_roots)
            elif isinstance(stmt, ast.While):
                expr_roots.append(stmt.test)
                isinst, hasat, sig, _ = _analyze_condition(stmt.test)
                for sub_w in ast.walk(stmt.test):
                    if isinstance(sub_w, ast.NamedExpr) and isinstance(sub_w.target, ast.Name):
                        frame_bound.add(sub_w.target.id)
            elif isinstance(stmt, ast.If):
                expr_roots.append(stmt.test)
                isinst, hasat, sig, is_main = _analyze_condition(stmt.test)
                for sub_w in ast.walk(stmt.test):
                    if isinstance(sub_w, ast.NamedExpr) and isinstance(sub_w.target, ast.Name):
                        frame_bound.add(sub_w.target.id)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    expr_roots.append(item.context_expr)
                    if item.optional_vars is not None:
                        _extract_target_stores(item.optional_vars, frame_bound, expr_roots)
            elif isinstance(stmt, ast.Match):
                expr_roots.append(stmt.subject)

            loads, attrs, calls = _extract_expr_reads_and_attrs(expr_roots)
            cond_vars = loads if subkind == "while" else frozenset()
            return ParsedLine(
                text=text,
                inline_comment=inline_comment,
                kind="primary",
                subkind=subkind,
                defined_name=defined_name,
                reads=loads,
                attr_reads=attrs,
                calls=calls,
                writes=frozenset(stores),
                frame_bound=frozenset(frame_bound),
                cond_vars=cond_vars,
                isinstance_vars=isinst,
                hasattr_pairs=hasat,
                cond_sig=sig,
                needs_async=needs_async,
                is_method_def=is_method,
                is_main_guard=is_main,
                blank_lines_before=chunk.blank_lines_before,
            )

    # Simple statement(s)
    try:
        mod = ast.parse(text)
        body = mod.body
    except SyntaxError:
        indented = _indent_probe_block(text, 4)
        wrapper = ast.parse(f"async def _w():\n{indented}\n")
        fn_wrap = wrapper.body[0]
        assert isinstance(fn_wrap, ast.AsyncFunctionDef)
        body = fn_wrap.body

    expr_roots = []
    stores = set()
    g_decl: set[str] = set()
    needs_func = False
    needs_nonlocal = False
    is_global_stmt = False
    needs_async = False
    needs_loop = False
    is_valueless_ann = False
    is_ann_assign = False
    is_docstring = (
        len(body) == 1
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )
    is_import = bool(body) and all(isinstance(s, (ast.Import, ast.ImportFrom)) for s in body)

    for st in body:
        if isinstance(st, ast.Assign):
            expr_roots.append(st.value)
            for tgt in st.targets:
                _extract_target_stores(tgt, stores, expr_roots)
        elif isinstance(st, ast.AugAssign):
            expr_roots.append(st.value)
            expr_roots.append(st.target)
            if isinstance(st.target, ast.Name):
                stores.add(st.target.id)
                expr_roots.append(ast.Name(id=st.target.id, ctx=ast.Load()))
        elif isinstance(st, ast.AnnAssign):
            is_ann_assign = True
            expr_roots.append(st.annotation)
            if st.value is not None:
                expr_roots.append(st.value)
            else:
                is_valueless_ann = True
            _extract_target_stores(st.target, stores, expr_roots)
        elif isinstance(st, ast.Expr):
            expr_roots.append(st.value)
        elif isinstance(st, ast.Return):
            needs_func = True
            if st.value is not None:
                expr_roots.append(st.value)
        elif isinstance(st, ast.Raise):
            if st.exc is not None:
                expr_roots.append(st.exc)
            if st.cause is not None:
                expr_roots.append(st.cause)
        elif isinstance(st, ast.Assert):
            expr_roots.append(st.test)
            if st.msg is not None:
                expr_roots.append(st.msg)
        elif isinstance(st, ast.Delete):
            expr_roots.extend(st.targets)
        elif isinstance(st, ast.Import):
            for alias in st.names:
                stores.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(st, ast.ImportFrom):
            for alias in st.names:
                if alias.name != "*":
                    stores.add(alias.asname or alias.name)
        elif isinstance(st, (ast.Break, ast.Continue)):
            needs_loop = True
        elif isinstance(st, ast.Global):
            is_global_stmt = True
            g_decl.update(st.names)
        elif isinstance(st, ast.Nonlocal):
            needs_nonlocal = True
            g_decl.update(st.names)

        for sub in ast.walk(st):
            if isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name):
                stores.add(sub.target.id)
            elif isinstance(sub, (ast.Yield, ast.YieldFrom)):
                needs_func = True
            elif isinstance(sub, ast.Await):
                needs_async = True
                needs_func = True

    last_st = body[-1]
    is_terminal = isinstance(last_st, (ast.Return, ast.Raise, ast.Break, ast.Continue))
    is_return_or_raise = isinstance(last_st, (ast.Return, ast.Raise))
    loads, attrs, calls = _extract_expr_reads_and_attrs(expr_roots)

    return ParsedLine(
        text=text,
        inline_comment=inline_comment,
        kind="simple",
        subkind="simple",
        reads=loads,
        attr_reads=attrs,
        calls=calls,
        writes=frozenset(stores),
        global_decl=frozenset(g_decl),
        is_terminal=is_terminal,
        is_return_or_raise=is_return_or_raise,
        needs_func=needs_func,
        needs_nonlocal=needs_nonlocal,
        is_global_stmt=is_global_stmt,
        needs_async=needs_async,
        needs_loop=needs_loop,
        is_valueless_ann=is_valueless_ann,
        is_ann_assign=is_ann_assign,
        is_docstring=is_docstring,
        is_import=is_import,
        blank_lines_before=chunk.blank_lines_before,
    )


def _has_enclosing_func(stack: Sequence[BlockFrame]) -> bool:
    """Return ``True`` if the innermost function/class scope in ``stack`` is a function."""
    for frame in reversed(stack):
        if frame.kind in ("def", "async_def"):
            return True
        if frame.kind == "class":
            return False
    return False


def _has_enclosing_nonlocal_func(stack: Sequence[BlockFrame]) -> bool:
    """Return ``True`` if any outer frame in ``stack`` is a ``def`` or ``async def``."""
    return any(f.kind in ("def", "async_def") for f in stack)


def _has_enclosing_async_func(stack: Sequence[BlockFrame]) -> bool:
    """Return ``True`` if the innermost function/class scope in ``stack`` is ``async def``."""
    for frame in reversed(stack):
        if frame.kind == "async_def":
            return True
        if frame.kind in ("def", "class"):
            return False
    return False


def _has_enclosing_loop(stack: Sequence[BlockFrame]) -> bool:
    """Return ``True`` if ``stack`` has an active loop body without an intervening ``def`` or ``class``."""
    for frame in reversed(stack):
        if frame.kind in ("for", "async_for", "while") and frame.clause == "body":
            return True
        if frame.kind in ("def", "async_def", "class"):
            return False
    return False


def _loop_has_guard(stack: Sequence[BlockFrame]) -> bool:
    """Return ``True`` if a conditional/try/case guard sits inside the innermost loop."""
    for frame in reversed(stack):
        if frame.kind in ("if", "try", "case"):
            return True
        if frame.kind in ("for", "async_for", "while"):
            return False
    return True


def _can_pop_frame(frame: BlockFrame) -> bool:
    """Return ``True`` if ``frame`` can legally close at the current point."""
    if frame.stmt_count == 0:
        return False
    if frame.kind == "try" and frame.clause == "body":
        return False
    return True


def _pop_frames_with_try_lift(
    module_vars: frozenset[str],
    stack: tuple[BlockFrame, ...],
    target_depth: int,
) -> tuple[frozenset[str], tuple[BlockFrame, ...]]:
    """Pop frames down to ``target_depth``, lifting ``try`` variables to their parent frame."""
    cur_list = list(stack)
    mod_set = set(module_vars)
    while len(cur_list) > target_depth:
        top = cur_list.pop()
        if top.kind == "try":
            lifted = top.local_vars | top.shared_vars
            if cur_list:
                pf = cur_list[-1]
                outer = _same_lexical_scope_vars(frozenset(mod_set), cur_list, len(cur_list) - 1)
                cur_list[-1] = replace(
                    pf,
                    local_vars=pf.local_vars | (lifted - outer - pf.global_vars),
                )
            else:
                mod_set.update(lifted)
    return frozenset(mod_set), tuple(cur_list)


def _clear_read_vars(
    stack: Sequence[BlockFrame],
    reads: frozenset[str],
) -> list[BlockFrame]:
    """Remove variables in ``reads`` from ``unread_writes`` across ``stack`` frames."""
    if not reads:
        return list(stack)
    return [replace(f, unread_writes=f.unread_writes - reads) if (f.unread_writes & reads) else f for f in stack]


def _same_lexical_scope_vars(
    module_vars: frozenset[str],
    stack: Sequence[BlockFrame],
    frame_idx: int,
) -> set[str]:
    """Return variables already defined in the same Python lexical scope above ``stack[frame_idx]``."""
    pf = stack[frame_idx]
    if pf.kind in ("def", "async_def", "class"):
        return set(pf.params | pf.bound_vars)

    res: set[str] = set()
    for idx in range(frame_idx - 1, -1, -1):
        anc = stack[idx]
        res.update(anc.params | anc.bound_vars | anc.local_vars | anc.shared_vars)
        if anc.kind in ("def", "async_def", "class"):
            return res

    res.update(module_vars)
    return res


def _outer_defined_vars(
    module_vars: frozenset[str],
    stack: Sequence[BlockFrame],
    upto: int,
) -> set[str]:
    """Collect all variable names accessible in module + ``stack[:upto]``."""
    res = set(module_vars)
    for idx in range(upto):
        if stack[idx].kind != "class":
            res.update(stack[idx].bound_vars)
            res.update(stack[idx].local_vars)
            res.update(stack[idx].shared_vars)
    return res


def render_chunk_at_indent(chunk: LogicalChunk, indent_level: int) -> str:
    """Render ``chunk`` at ``indent_level`` (4 spaces per level) without altering string contents."""
    base = "    " * indent_level
    code = chunk.code
    if "\n" not in code:
        line = base + code
        if chunk.inline_comment:
            line += "  " + chunk.inline_comment
        return line

    # Multi-line expression or multi-line string: protect newlines inside string literals!
    protected_newlines: set[int] = set()
    i = 0
    n = len(code)
    while i < n:
        if code[i] == "#":
            nl = code.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        se = _match_string_literal(code, i, require_closed=False)
        if se is not None:
            for pos in range(i, se):
                if code[pos] == "\n":
                    protected_newlines.add(pos)
            i = se
            continue
        i += 1

    out_parts: list[str] = [base]
    i = 0
    while i < n:
        if code[i] == "\n":
            out_parts.append("\n")
            if i in protected_newlines:
                i += 1
                continue
            # Continuation line outside strings: strip old leading spaces and re-indent
            i += 1
            while i < n and code[i] in (" ", "\t"):
                i += 1
            if i < n and code[i] in (")", "]", "}"):
                out_parts.append(base)
            elif i < n:
                out_parts.append(base + "    ")
            continue
        out_parts.append(code[i])
        i += 1

    rendered = "".join(out_parts)
    if chunk.inline_comment:
        rendered += "  " + chunk.inline_comment
    return rendered


def render_chunks(chunks: Sequence[LogicalChunk], indents: Sequence[int]) -> str:
    """Render all logical chunks with their inferred indentation levels."""
    if not chunks:
        return ""
    return "\n".join(render_chunk_at_indent(chunks[i], indents[i]) for i in range(len(chunks))) + "\n"


def _build_lookahead_requirements(
    parsed: Sequence[ParsedLine],
    code_indices: Sequence[int],
) -> tuple[list[bool], list[bool], list[bool], list[bool], list[bool]]:
    """Compute exact backward grammar-legality lookahead flags for each code step."""
    k_len = len(code_indices)
    req_func = [False] * k_len
    req_async = [False] * k_len
    req_loop = [False] * k_len
    req_try = [False] * k_len
    req_match = [False] * k_len

    f_fn = False
    f_as = False
    f_lp = False
    f_tr = False
    f_ma = False

    for step in range(k_len - 1, -1, -1):
        req_func[step] = f_fn
        req_async[step] = f_as
        req_loop[step] = f_lp
        req_try[step] = f_tr
        req_match[step] = f_ma

        cpl = parsed[code_indices[step]]
        if cpl.kind == "primary" and cpl.subkind in ("def", "async_def"):
            f_fn = cpl.needs_func or cpl.needs_nonlocal
        elif cpl.needs_func or cpl.needs_nonlocal:
            f_fn = True

        if cpl.kind == "primary" and cpl.subkind == "async_def":
            f_as = cpl.needs_async
        elif cpl.needs_async:
            f_as = True

        if cpl.kind == "primary" and cpl.subkind in ("for", "async_for", "while"):
            f_lp = cpl.needs_loop
        elif cpl.needs_loop:
            f_lp = True

        if cpl.kind == "primary" and cpl.subkind == "try":
            f_tr = False
        elif cpl.kind == "clause" and cpl.subkind in ("except", "finally"):
            f_tr = True

        if cpl.kind == "primary" and cpl.subkind == "match":
            f_ma = False
        elif cpl.kind == "clause" and cpl.subkind == "case":
            f_ma = True

    return req_func, req_async, req_loop, req_try, req_match


def infer_indents(
    chunks: Sequence[LogicalChunk],
    *,
    beam_width: int = 64,
    filename: str = "<reconstructed>",
) -> list[int] | None:
    """Infer optimal 4-space indentation levels for ``chunks`` via beam search."""
    parsed = [analyze_chunk(c) for c in chunks]
    code_indices = [i for i, p in enumerate(parsed) if p.kind != "comment"]
    if not code_indices:
        return [0] * len(chunks)

    (
        fut_func,
        fut_async,
        fut_loop,
        fut_try,
        fut_match,
    ) = _build_lookahead_requirements(parsed, code_indices)

    init = SearchState(
        stack=(),
        module_vars=frozenset(),
        module_unread=frozenset(),
        pending_dec=None,
        pending_method_dec=False,
        score=0.0,
        indents=(),
    )
    beam: list[SearchState] = [init]

    for step_idx, line_idx in enumerate(code_indices):
        pl = parsed[line_idx]
        need_fn_after = fut_func[step_idx]
        need_as_after = fut_async[step_idx]
        need_lp_after = fut_loop[step_idx]
        need_tr_after = fut_try[step_idx]
        need_ma_after = fut_match[step_idx]

        next_states: dict[tuple[object, ...], SearchState] = {}

        def register(st: SearchState) -> None:
            """Keep the highest-scoring state per equivalent stack signature."""
            stk = st.stack
            if need_fn_after and not any(f.kind in ("def", "async_def") for f in stk):
                return
            if need_as_after and not any(f.kind == "async_def" for f in stk):
                return
            if need_lp_after and not any(f.kind in ("for", "async_for", "while") and f.clause == "body" for f in stk):
                return
            if need_tr_after and not any(f.kind == "try" for f in stk):
                return
            if need_ma_after and not any(f.kind == "match" for f in stk):
                return

            sig = (
                tuple(
                    (
                        f.kind,
                        f.clause,
                        f.had_except,
                        f.name,
                        f.stmt_count > 0,
                        f.only_docstring,
                        f.has_methods,
                        f.terminal,
                        f.bound_vars,
                        f.local_vars,
                        f.shared_vars,
                        f.unread_writes,
                        f.isinstance_vars,
                        f.cond_sig,
                    )
                    for f in stk
                ),
                st.pending_dec,
                st.pending_method_dec,
            )
            prev = next_states.get(sig)
            if prev is None or st.score > prev.score:
                next_states[sig] = st

        for st in beam:
            depth = len(st.stack)

            # Case 1: Immediately following a `@decorator` at `d_dec`
            if st.pending_dec is not None:
                d_dec = st.pending_dec
                if pl.kind == "decorator":
                    register(
                        replace(
                            st,
                            module_unread=st.module_unread - pl.reads,
                            pending_method_dec=(st.pending_method_dec or pl.is_method_decorator),
                            score=st.score + 5.0,
                            indents=st.indents + (d_dec,),
                        )
                    )
                elif pl.kind == "primary" and pl.subkind in (
                    "def",
                    "async_def",
                    "class",
                ):
                    dec_bonus = 10.0
                    if pl.subkind in ("def", "async_def") and d_dec > 0:
                        if st.stack[d_dec - 1].kind == "class":
                            if pl.is_method_def or st.pending_method_dec:
                                dec_bonus += 35.0
                            else:
                                dec_bonus -= 30.0
                    new_mod = st.module_vars | (pl.writes if d_dec == 0 else frozenset())
                    new_mod_unread = st.module_unread - pl.reads
                    new_stack_list = [replace(f, unread_writes=f.unread_writes - pl.reads) for f in st.stack]
                    if d_dec > 0:
                        pf = new_stack_list[d_dec - 1]
                        outer = _same_lexical_scope_vars(new_mod, st.stack, d_dec - 1)
                        added_local = pl.writes - outer - pf.global_vars
                        new_stack_list[d_dec - 1] = replace(
                            pf,
                            local_vars=pf.local_vars | added_local,
                            stmt_count=pf.stmt_count + 1,
                            only_docstring=False,
                            has_methods=(pf.has_methods or pl.subkind in ("def", "async_def")),
                            terminal=False,
                        )
                    new_frame = BlockFrame(
                        kind=pl.subkind,
                        name=pl.defined_name,
                        params=(pl.frame_bound if pl.subkind in ("def", "async_def") else frozenset()),
                        bound_vars=pl.frame_bound,
                    )
                    new_stack_list.append(new_frame)
                    register(
                        SearchState(
                            stack=tuple(new_stack_list),
                            module_vars=new_mod,
                            module_unread=new_mod_unread,
                            pending_dec=None,
                            pending_method_dec=False,
                            score=st.score + dec_bonus,
                            indents=st.indents + (d_dec,),
                        )
                    )
                continue

            min_d = 0
            for m in range(depth - 1, -1, -1):
                if not _can_pop_frame(st.stack[m]):
                    min_d = m + 1
                    break

            # Directly after `match ...:`, Python requires `case ...:` at `depth`
            if depth > 0 and st.stack[-1].kind == "match" and st.stack[-1].stmt_count == 0:
                if pl.kind == "clause" and pl.subkind == "case":
                    cleaned = tuple(replace(f, unread_writes=f.unread_writes - pl.reads) for f in st.stack[:-1])
                    mf = replace(
                        st.stack[-1],
                        unread_writes=st.stack[-1].unread_writes - pl.reads,
                        stmt_count=1,
                    )
                    cf = BlockFrame(kind="case", clause="case", bound_vars=pl.frame_bound)
                    register(
                        replace(
                            st,
                            module_unread=st.module_unread - pl.reads,
                            stack=cleaned + (mf, cf),
                            score=st.score + 20.0,
                            indents=st.indents + (depth,),
                        )
                    )
                continue

            # Case 2: Continuation clauses (`elif`, `else`, `except`, `finally`, `case`)
            if pl.kind == "clause":
                kw = pl.subkind
                for d in range(depth - 1, -1, -1):
                    if d + 1 < min_d:
                        break
                    mod_after_pop, stack_upto_target = _pop_frames_with_try_lift(st.module_vars, st.stack, d + 1)
                    target = stack_upto_target[d]
                    popped_clauses = st.stack[d + 1 :]

                    if kw == "case":
                        if target.kind == "match" and d + 1 <= depth:
                            if all(_can_pop_frame(st.stack[m]) for m in range(d + 1, depth)):
                                cleaned_anc = tuple(
                                    replace(f, unread_writes=f.unread_writes - pl.reads) for f in stack_upto_target[:d]
                                )
                                mf = replace(
                                    target,
                                    unread_writes=target.unread_writes - pl.reads,
                                    stmt_count=target.stmt_count + 1,
                                )
                                cf = BlockFrame(
                                    kind="case",
                                    clause="case",
                                    bound_vars=pl.frame_bound,
                                )
                                register(
                                    replace(
                                        st,
                                        module_vars=mod_after_pop,
                                        module_unread=st.module_unread - pl.reads,
                                        stack=cleaned_anc + (mf, cf),
                                        score=st.score + 15.0,
                                        indents=st.indents + (d + 1,),
                                    )
                                )
                        continue

                    if target.stmt_count == 0:
                        continue

                    compatible = False
                    new_clause = kw
                    new_had_exc = target.had_except
                    bonus = 0.0
                    unclosed_inner_ifs = sum(
                        1 for pf in popped_clauses if pf.kind == "if" and pf.clause in ("body", "elif")
                    )

                    for pf in popped_clauses:
                        if pf.only_docstring and pf.kind in (
                            "def",
                            "async_def",
                            "class",
                        ):
                            bonus -= 55.0

                    if kw == "elif":
                        if target.kind == "if" and target.clause in ("body", "elif"):
                            compatible = True
                            if (
                                target.cond_sig is not None
                                and pl.cond_sig is not None
                                and target.cond_sig == pl.cond_sig
                            ):
                                bonus += 55.0
                            else:
                                bonus -= 10.0 * unclosed_inner_ifs
                    elif kw == "else":
                        if target.kind == "if" and target.clause in ("body", "elif"):
                            compatible = True
                            bonus += 20.0 - 22.0 * unclosed_inner_ifs
                        elif target.kind in ("for", "async_for", "while") and target.clause == "body":
                            compatible = True
                            bonus += 2.0 - 22.0 * unclosed_inner_ifs
                        elif target.kind == "try" and target.clause == "except" and target.had_except:
                            compatible = True
                            bonus += 5.0 - 22.0 * unclosed_inner_ifs
                    elif kw == "except":
                        if target.kind == "try" and target.clause in ("body", "except"):
                            compatible = True
                            new_had_exc = True
                            bonus += 30.0
                    elif kw == "finally":
                        if target.kind == "try" and target.clause in (
                            "body",
                            "except",
                            "else",
                        ):
                            compatible = True
                            bonus += 30.0

                    if not compatible:
                        continue

                    carried_shared = target.shared_vars | (target.local_vars if target.kind == "try" else frozenset())
                    cleaned_anc = tuple(
                        replace(f, unread_writes=f.unread_writes - pl.reads) for f in stack_upto_target[:d]
                    )
                    updated = BlockFrame(
                        kind=target.kind,
                        clause=new_clause,
                        had_except=new_had_exc,
                        name=target.name,
                        params=target.params,
                        bound_vars=pl.frame_bound,
                        cond_vars=target.cond_vars,
                        local_vars=frozenset(),
                        shared_vars=carried_shared,
                        unread_writes=frozenset(),
                        global_vars=target.global_vars,
                        isinstance_vars=pl.isinstance_vars,
                        hasattr_pairs=pl.hasattr_pairs,
                        cond_sig=pl.cond_sig or target.cond_sig,
                        stmt_count=0,
                        only_docstring=False,
                        has_methods=target.has_methods,
                        terminal=False,
                    )
                    register(
                        replace(
                            st,
                            module_vars=mod_after_pop,
                            module_unread=st.module_unread - pl.reads,
                            stack=cleaned_anc + (updated,),
                            score=st.score + bonus,
                            indents=st.indents + (d,),
                        )
                    )
                continue

            # Case 3: Decorator, Primary Header, or Simple Statement
            for d in range(min_d, depth + 1):
                mod_at_d, kept = _pop_frames_with_try_lift(st.module_vars, st.stack, d)
                popped = st.stack[d:]

                if pl.needs_func and not _has_enclosing_func(kept):
                    continue
                if pl.needs_nonlocal and not _has_enclosing_nonlocal_func(kept):
                    continue
                if pl.needs_async and not _has_enclosing_async_func(kept):
                    continue
                if pl.needs_loop and not _has_enclosing_loop(kept):
                    continue
                if d > 0 and kept[-1].kind == "match":
                    continue

                delta = 0.0

                if d > 0 and kept[-1].terminal:
                    delta -= 500.0

                if pl.is_main_guard:
                    delta += 80.0 if d == 0 else -80.0

                if pl.blank_lines_before >= 2 and d == 0:
                    delta += 6.0

                if pl.is_global_stmt:
                    delta += 40.0 if _has_enclosing_func(kept) else -60.0

                if (
                    pl.is_docstring
                    and d > 0
                    and kept[-1].stmt_count == 0
                    and kept[-1].kind in ("def", "async_def", "class")
                ):
                    delta += 30.0

                if pl.needs_loop:
                    delta += 35.0 if _loop_has_guard(kept) else -35.0
                elif pl.is_return_or_raise and _has_enclosing_loop(kept) and not _loop_has_guard(kept):
                    delta -= 45.0

                if pl.kind == "decorator" and pl.is_method_decorator:
                    delta += 50.0 if (d > 0 and kept[-1].kind == "class") else -50.0

                if pl.kind == "primary" and pl.subkind in ("def", "async_def"):
                    if pl.is_method_def:
                        delta += 50.0 if (d > 0 and kept[-1].kind == "class") else -50.0
                    elif d > 0 and kept[-1].kind == "class":
                        delta -= 42.0

                if pl.is_valueless_ann and d > 0 and kept[-1].kind == "class":
                    delta += 25.0

                # PEP-8 ALL_CAPS constant assignments strongly belong at module/class scope
                if (
                    pl.kind == "simple"
                    and pl.writes
                    and not pl.is_import
                    and all(len(w) >= 2 and w.isupper() for w in pl.writes)
                ):
                    if d == 0 or (d > 0 and kept[-1].kind == "class"):
                        delta += 18.0
                    elif _has_enclosing_func(kept):
                        delta -= 12.0

                # Pure overwrite vs dead-store / conditional flag-mutation scoring
                pure_writes = (pl.writes - pl.reads) if (pl.kind == "simple" and not pl.is_import) else frozenset()
                for vw in pure_writes:
                    if vw == "_" or vw.isupper():
                        continue
                    if d == 0 and vw in st.module_unread:
                        delta -= 25.0
                    elif d > 0 and vw in kept[d - 1].unread_writes:
                        delta -= 35.0
                    elif d > 0 and kept[d - 1].kind in ("if", "try", "case"):
                        if vw in st.module_unread or any(vw in kept[anc].unread_writes for anc in range(d - 1)):
                            delta += 22.0

                if pl.kind == "primary" and pl.subkind in ("for", "async_for") and pl.frame_bound:
                    for kf in kept:
                        if kf.kind in ("for", "async_for") and (pl.frame_bound & kf.bound_vars):
                            delta -= 45.0

                for m, kf in enumerate(kept):
                    # Class name does not exist directly inside its own class body!
                    if (
                        kf.kind == "class"
                        and kf.name
                        and kf.name in pl.reads
                        and not pl.is_ann_assign
                        and all(inner.kind not in ("def", "async_def") for inner in kept[m + 1 :])
                    ):
                        delta -= 120.0

                    if kf.kind in ("def", "async_def", "class") and kf.name:
                        if kf.name in pl.calls:
                            if all(inner.kind not in ("if", "try", "case") for inner in kept[m + 1 :]):
                                delta -= 120.0
                            elif kf.params and not (pl.reads & (kf.params | kf.local_vars)):
                                delta -= 60.0
                    if kf.kind in ("def", "async_def") and kf.params and kf.stmt_count >= 1:
                        if (pl.writes & kf.params) and not (pl.reads & (kf.params | kf.local_vars)):
                            delta -= 45.0

                outer_at_d = _outer_defined_vars(mod_at_d, kept, d)
                is_def_or_class_or_dec = pl.kind == "decorator" or (
                    pl.kind == "primary" and pl.subkind in ("def", "async_def", "class")
                )

                for pf in popped:
                    if pf.only_docstring and pf.kind in ("def", "async_def", "class") and not is_def_or_class_or_dec:
                        delta -= 55.0
                    if pf.kind == "while" and (pl.writes & pf.cond_vars):
                        delta -= 18.0
                    is_parallel_isinst = (
                        pl.kind == "primary"
                        and pl.subkind == "if"
                        and pl.cond_sig is not None
                        and pl.cond_sig[0] == "isinstance"
                        and pl.cond_sig == pf.cond_sig
                    )
                    for v in pl.reads:
                        if v in pf.bound_vars and v not in outer_at_d:
                            delta -= 90.0 if pf.kind in ("def", "async_def") else 45.0
                        elif (v in pf.local_vars or v in pf.shared_vars) and v not in outer_at_d:
                            delta -= 80.0 if pf.kind in ("def", "async_def") else 35.0
                        elif v in pf.isinstance_vars and not is_parallel_isinst:
                            delta -= 30.0
                    for pair in pl.attr_reads:
                        if pair in pf.hasattr_pairs:
                            delta -= 30.0

                next_pl = parsed[code_indices[step_idx + 1]] if step_idx + 1 < len(code_indices) else None
                active_frame_indices: set[int] = set()
                for m in range(d - 1, -1, -1):
                    if kept[m].kind == "while":
                        if pl.writes & kept[m].cond_vars:
                            active_frame_indices.add(m)
                            delta += 24.0
                            break
                        if (
                            pl.kind == "primary"
                            and pl.subkind == "if"
                            and next_pl is not None
                            and (next_pl.writes & kept[m].cond_vars)
                        ):
                            active_frame_indices.add(m)
                            delta += 28.0
                            break

                for v in pl.reads:
                    crossed_func = False
                    for m in range(d - 1, -1, -1):
                        kf = kept[m]
                        if crossed_func:
                            if kf.kind in ("def", "async_def") and v in kf.params:
                                active_frame_indices.add(m)
                                delta += 18.0
                                break
                            continue
                        if v in kf.bound_vars:
                            active_frame_indices.add(m)
                            delta += 26.0
                            break
                        if v in kf.local_vars or v in kf.shared_vars:
                            # `try: import x except: import x` exports `x` on pop too
                            if kf.kind == "try" and kf.clause != "body" and v in kf.shared_vars and v in kf.local_vars:
                                break
                            active_frame_indices.add(m)
                            delta += 26.0
                            break
                        if kf.kind in ("def", "async_def"):
                            crossed_func = True
                    for m in range(d - 1, -1, -1):
                        kf = kept[m]
                        is_parallel = (
                            pl.kind == "primary"
                            and pl.subkind == "if"
                            and pl.cond_sig is not None
                            and pl.cond_sig == kf.cond_sig
                        )
                        if v in kf.isinstance_vars and not is_parallel:
                            active_frame_indices.add(m)
                            delta += 22.0
                            break

                for pair in pl.attr_reads:
                    for m in range(d - 1, -1, -1):
                        if pair in kept[m].hasattr_pairs:
                            active_frame_indices.add(m)
                            delta += 22.0
                            break

                req_depth = (max(active_frame_indices) + 1) if active_frame_indices else 0

                for m in range(max(req_depth, min_d), d):
                    kf = kept[m]
                    if kf.kind in ("for", "async_for", "while"):
                        delta -= 15.0
                    elif kf.kind == "if":
                        if pl.kind == "primary" and pl.subkind in (
                            "if",
                            "for",
                            "async_for",
                            "while",
                            "def",
                            "class",
                        ):
                            delta -= 18.0
                        else:
                            delta -= 10.0
                    elif kf.kind in ("with", "async_with"):
                        delta -= 8.0
                    elif kf.kind == "try":
                        if pl.kind == "primary" and pl.subkind in (
                            "def",
                            "async_def",
                            "class",
                        ):
                            delta -= 22.0
                        elif kf.clause == "body":
                            delta -= 6.0
                        else:
                            delta -= 12.0
                    elif kf.kind == "case":
                        delta -= 10.0
                    elif kf.kind == "class":
                        if pl.kind == "primary" and pl.subkind == "class":
                            delta -= 18.0
                        elif kf.has_methods and not pl.writes and not pl.is_docstring:
                            delta -= 20.0
                        elif (
                            not pl.writes
                            and not pl.is_docstring
                            and not (
                                pl.kind == "decorator" or (pl.kind == "primary" and pl.subkind in ("def", "async_def"))
                            )
                        ):
                            delta -= 10.0
                    elif kf.kind in ("def", "async_def"):
                        if pl.is_global_stmt or pl.needs_func or pl.needs_nonlocal:
                            delta += 10.0
                        elif is_def_or_class_or_dec:
                            delta -= 12.0
                        elif kf.params and not (pl.reads & (kf.params | kf.local_vars)):
                            delta -= 12.0
                        else:
                            delta += 4.0

                cleaned_kept = _clear_read_vars(kept, pl.reads)
                new_mod_unread = st.module_unread - pl.reads

                if pl.kind == "decorator":
                    register(
                        SearchState(
                            stack=tuple(cleaned_kept),
                            module_vars=mod_at_d,
                            module_unread=new_mod_unread,
                            pending_dec=d,
                            pending_method_dec=pl.is_method_decorator,
                            score=st.score + delta,
                            indents=st.indents + (d,),
                        )
                    )
                elif pl.kind == "primary":
                    new_mod = mod_at_d | (pl.writes if d == 0 else frozenset())
                    if d == 0 and not pl.is_import:
                        new_mod_unread = new_mod_unread | (pl.writes - pl.reads)
                    if d > 0:
                        pf = cleaned_kept[d - 1]
                        outer_p = _same_lexical_scope_vars(new_mod, kept, d - 1)
                        added_local = pl.writes - outer_p - pf.global_vars
                        cleaned_kept[d - 1] = replace(
                            pf,
                            local_vars=pf.local_vars | added_local,
                            stmt_count=pf.stmt_count + 1,
                            only_docstring=False,
                            has_methods=(pf.has_methods or pl.subkind in ("def", "async_def")),
                            terminal=False,
                        )
                    new_frame = BlockFrame(
                        kind=pl.subkind,
                        name=pl.defined_name,
                        params=(pl.frame_bound if pl.subkind in ("def", "async_def") else frozenset()),
                        bound_vars=pl.frame_bound,
                        cond_vars=pl.cond_vars,
                        isinstance_vars=pl.isinstance_vars,
                        hasattr_pairs=pl.hasattr_pairs,
                        cond_sig=pl.cond_sig,
                    )
                    cleaned_kept.append(new_frame)
                    register(
                        SearchState(
                            stack=tuple(cleaned_kept),
                            module_vars=new_mod,
                            module_unread=new_mod_unread,
                            pending_dec=None,
                            pending_method_dec=False,
                            score=st.score + delta,
                            indents=st.indents + (d,),
                        )
                    )
                else:
                    new_mod = mod_at_d | (pl.writes if d == 0 else frozenset())
                    if d == 0 and not pl.is_import:
                        new_mod_unread = new_mod_unread | pure_writes
                    if d > 0:
                        pf = cleaned_kept[d - 1]
                        new_globals = pf.global_vars | pl.global_decl
                        if pl.global_decl:
                            new_mod = new_mod | pl.global_decl
                        outer_p = _same_lexical_scope_vars(new_mod, kept, d - 1)
                        added_local = pl.writes - outer_p - new_globals
                        added_unread = (pure_writes - new_globals) if not pl.is_import else frozenset()
                        cleaned_kept[d - 1] = replace(
                            pf,
                            local_vars=pf.local_vars | added_local,
                            unread_writes=pf.unread_writes | added_unread,
                            global_vars=new_globals,
                            stmt_count=pf.stmt_count + 1,
                            only_docstring=(pf.stmt_count == 0 and pl.is_docstring),
                            terminal=pl.is_terminal,
                        )
                    register(
                        SearchState(
                            stack=tuple(cleaned_kept),
                            module_vars=new_mod,
                            module_unread=new_mod_unread,
                            pending_dec=None,
                            pending_method_dec=False,
                            score=st.score + delta,
                            indents=st.indents + (d,),
                        )
                    )

        if not next_states:
            return None

        ranked = sorted(next_states.values(), key=lambda s: s.score, reverse=True)
        if len(ranked) <= beam_width:
            beam = ranked
        else:
            # Stratified beam: keep top global states + top representative per depth bucket
            reserve = min(16, beam_width // 4)
            core_limit = beam_width - reserve
            selected: list[SearchState] = ranked[:core_limit]
            seen_ids = {id(s) for s in selected}
            seen_buckets: set[tuple[int, int]] = {
                (
                    len(s.stack),
                    sum(1 for f in s.stack if f.kind in ("def", "async_def")),
                )
                for s in selected
            }
            for cand in ranked[core_limit:]:
                bkt = (
                    len(cand.stack),
                    sum(1 for f in cand.stack if f.kind in ("def", "async_def")),
                )
                if bkt not in seen_buckets:
                    seen_buckets.add(bkt)
                    seen_ids.add(id(cand))
                    selected.append(cand)
                    if len(selected) >= beam_width:
                        break
            if len(selected) < beam_width:
                for cand in ranked[core_limit:]:
                    if id(cand) not in seen_ids:
                        selected.append(cand)
                        if len(selected) >= beam_width:
                            break
            beam = selected

    valid_final = [st for st in beam if st.pending_dec is None and all(_can_pop_frame(f) for f in st.stack)]
    if not valid_final:
        return None

    for candidate in valid_final:
        full_indents = [0] * len(chunks)
        for idx_c, ind_val in zip(code_indices, candidate.indents):
            full_indents[idx_c] = ind_val
        next_ind = 0
        for idx_all in range(len(chunks) - 1, -1, -1):
            if parsed[idx_all].kind != "comment":
                next_ind = full_indents[idx_all]
            else:
                full_indents[idx_all] = next_ind

        rendered = render_chunks(chunks, full_indents)
        if is_compilable_python(rendered, filename=filename):
            return full_indents

    return None


# ---------------------------------------------------------------------------
# Public Source & File Reformatting API
# ---------------------------------------------------------------------------


def reformat_source(
    source: str,
    *,
    filename: str = "<source>",
    force: bool = False,
    beam_width: int = 64,
) -> ReformatResult:
    """Reconstruct newlines and 4-space indentations for a Python source string.

    Args:
        source: Raw (potentially collapsed or unindented) Python source text.
        filename: Virtual filename used in compiler diagnostics.
        force: Re-run segmentation and indentation inference even if ``source``
            already compiles cleanly.
        beam_width: Beam width for the indentation search.

    Returns:
        A :class:`ReformatResult` containing the repaired source and metrics.

    Raises:
        ReformatterError: If ``source`` cannot be reconstructed into valid Python.
    """
    normalized_source = source.replace("\r\n", "\n").replace("\r", "\n")
    orig_top_lines = [ln for ln in split_top_level_physical_lines(normalized_source) if ln.strip()]
    was_valid = is_compilable_python(normalized_source, filename=filename)

    # Guard against single-line files like `import a # comment code...` where `#`
    # accidentally swallowed the rest of the collapsed program into a comment.
    swallowed_code_in_comment = False
    if was_valid and not force and len(orig_top_lines) == 1 and bool(_find_hashes_outside_strings(orig_top_lines[0])):
        probe_extract = extract_logical_chunks(orig_top_lines[0], split_inline_comments=True)
        if probe_extract is not None and probe_extract[1] > 0:
            swallowed_code_in_comment = True

    if was_valid and not force and not swallowed_code_in_comment:
        tree = ast.parse(normalized_source, filename=filename)
        counts = collect_structural_counts(tree)
        comments = 0
        max_indent = 0
        for line in normalized_source.splitlines():
            stripped = line.lstrip(" ")
            if stripped:
                leading = len(line) - len(stripped)
                max_indent = max(max_indent, leading // 4)
            if stripped.startswith("#"):
                comments += 1
        return ReformatResult(
            source=source,
            was_valid_before=True,
            changed=False,
            logical_statements=counts.statements,
            comments_preserved=comments,
            inline_comments_split=0,
            shell_bridges_commented=0,
            max_indent_level=max_indent,
            counts=counts,
        )

    preprocessed, bridges = isolate_shell_heredoc_bridges(normalized_source)

    # Pass 1: if original input already had multiple physical lines, try without
    # splitting `#` comments first; if single-line collapsed, recover `#` comments first.
    attempt_modes: list[bool] = [False, True] if len(orig_top_lines) > 1 else [True, False]

    chosen_chunks: list[LogicalChunk] | None = None
    chosen_indents: list[int] | None = None
    chosen_split_comments = 0

    for split_comments in attempt_modes:
        extracted = extract_logical_chunks(preprocessed, split_inline_comments=split_comments)
        if extracted is None:
            continue
        chunks, split_cnt = extracted
        indents = infer_indents(chunks, beam_width=beam_width, filename=filename)
        if indents is not None:
            chosen_chunks = chunks
            chosen_indents = indents
            chosen_split_comments = split_cnt
            break

    if chosen_chunks is None or chosen_indents is None:
        raise ReformatterError(f"Unable to reconstruct valid Python syntax for {filename}.")

    repaired = render_chunks(chosen_chunks, chosen_indents)
    tree = ast.parse(repaired, filename=filename)
    compile(tree, filename=filename, mode="exec")
    counts = collect_structural_counts(tree)

    comments_preserved = sum(1 for ch in chosen_chunks if ch.code.startswith("#") or bool(ch.inline_comment))
    logical_code_lines = sum(1 for ch in chosen_chunks if not ch.code.startswith("#"))
    max_ind = max(chosen_indents, default=0)

    return ReformatResult(
        source=repaired,
        was_valid_before=(was_valid and not swallowed_code_in_comment),
        changed=(repaired != source),
        logical_statements=counts.statements,
        comments_preserved=comments_preserved,
        inline_comments_split=chosen_split_comments,
        shell_bridges_commented=bridges,
        max_indent_level=max_ind,
        counts=counts,
    )


def reformat_file(
    path: pathlib.Path,
    *,
    in_place: bool = True,
    force: bool = False,
    beam_width: int = 64,
) -> FileStats:
    """Repair a single Python file (in-place by default) and return :class:`FileStats`."""
    t0 = time.perf_counter()
    raw = path.read_text(encoding="utf-8")
    orig_lines = count_physical_lines(raw)
    orig_bytes = len(raw.encode("utf-8"))

    try:
        res = reformat_source(
            raw,
            filename=str(path),
            force=force,
            beam_width=beam_width,
        )
    except ReformatterError as exc:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return FileStats(
            path=str(path),
            status="failed",
            changed=False,
            was_valid_before=False,
            is_valid_after=False,
            original_lines=orig_lines,
            fixed_lines=orig_lines,
            original_bytes=orig_bytes,
            fixed_bytes=orig_bytes,
            logical_statements=0,
            comments_preserved=0,
            inline_comments_split=0,
            shell_bridges_commented=0,
            max_indent_level=0,
            functions=0,
            classes=0,
            loops=0,
            conditionals=0,
            try_blocks=0,
            ast_nodes=0,
            elapsed_ms=elapsed,
            error=str(exc),
        )

    if in_place and res.changed:
        path.write_text(res.source, encoding="utf-8")

    elapsed = (time.perf_counter() - t0) * 1000.0
    fixed_lines = count_physical_lines(res.source)
    fixed_bytes = len(res.source.encode("utf-8"))
    status = "fixed" if res.changed else "unchanged"

    return FileStats(
        path=str(path),
        status=status,
        changed=res.changed,
        was_valid_before=res.was_valid_before,
        is_valid_after=True,
        original_lines=orig_lines,
        fixed_lines=fixed_lines,
        original_bytes=orig_bytes,
        fixed_bytes=fixed_bytes,
        logical_statements=res.logical_statements,
        comments_preserved=res.comments_preserved,
        inline_comments_split=res.inline_comments_split,
        shell_bridges_commented=res.shell_bridges_commented,
        max_indent_level=res.max_indent_level,
        functions=res.counts.functions,
        classes=res.counts.classes,
        loops=res.counts.loops,
        conditionals=res.counts.conditionals,
        try_blocks=res.counts.try_blocks,
        ast_nodes=res.counts.ast_nodes,
        elapsed_ms=elapsed,
    )


def _natural_sort_key(path: pathlib.Path) -> tuple[tuple[int, int | str], ...]:
    """Natural sort key so ``broken2.py`` precedes ``broken10.py``."""
    parts = re.split(r"(\d+)", str(path))
    key: list[tuple[int, int | str]] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def discover_python_files(targets: Sequence[pathlib.Path]) -> list[pathlib.Path]:
    """Expand file and directory targets into a naturally sorted list of ``.py`` files."""
    found: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()

    for target in targets:
        if target.is_file():
            resolved = target.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(target)
        elif target.is_dir():
            for child in sorted(target.rglob("*.py"), key=_natural_sort_key):
                rel_parts = child.relative_to(target).parts
                if any(part in EXCLUDED_SCAN_DIRS for part in rel_parts[:-1]):
                    continue
                resolved = child.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    found.append(child)

    found.sort(key=_natural_sort_key)
    return found


def reformat_paths(
    targets: Sequence[pathlib.Path],
    *,
    in_place: bool = True,
    force: bool = False,
    beam_width: int = 64,
) -> BatchStats:
    """Process multiple files or directories and return aggregate :class:`BatchStats`."""
    files = discover_python_files(targets)
    file_stats: list[FileStats] = [
        reformat_file(f, in_place=in_place, force=force, beam_width=beam_width) for f in files
    ]

    fixed_files = sum(1 for s in file_stats if s.status == "fixed")
    unchanged_files = sum(1 for s in file_stats if s.status == "unchanged")
    failed_files = sum(1 for s in file_stats if s.status == "failed")

    return BatchStats(
        total_files=len(file_stats),
        fixed_files=fixed_files,
        unchanged_files=unchanged_files,
        failed_files=failed_files,
        original_total_lines=sum(s.original_lines for s in file_stats),
        fixed_total_lines=sum(s.fixed_lines for s in file_stats),
        original_total_bytes=sum(s.original_bytes for s in file_stats),
        fixed_total_bytes=sum(s.fixed_bytes for s in file_stats),
        total_logical_statements=sum(s.logical_statements for s in file_stats),
        total_comments_preserved=sum(s.comments_preserved for s in file_stats),
        total_inline_comments_split=sum(s.inline_comments_split for s in file_stats),
        total_shell_bridges_commented=sum(s.shell_bridges_commented for s in file_stats),
        max_indent_observed=max((s.max_indent_level for s in file_stats), default=0),
        total_functions=sum(s.functions for s in file_stats),
        total_classes=sum(s.classes for s in file_stats),
        total_loops=sum(s.loops for s in file_stats),
        total_conditionals=sum(s.conditionals for s in file_stats),
        total_try_blocks=sum(s.try_blocks for s in file_stats),
        total_ast_nodes=sum(s.ast_nodes for s in file_stats),
        total_elapsed_ms=sum(s.elapsed_ms for s in file_stats),
        files=tuple(file_stats),
    )


def format_stats_report(stats: BatchStats, *, dry_run: bool = False) -> str:
    """Format a human-readable summary table and aggregate statistics report."""
    mode_label = "DRY-RUN (no files written)" if dry_run else "IN-PLACE"
    header = (
        f"Python Indentation Autofixer Report [{mode_label}]\n"
        + "=" * 92
        + "\n"
        + f"{'File':<18} {'Status':<10} {'Lines':>11} {'Bytes':>15} "
        + f"{'Stmts':>6} {'Cmts':>5} {'MaxInd':>6} {'AST':>6} {'Time(ms)':>9}\n"
        + "-" * 92
    )
    rows: list[str] = [header]

    for fs in stats.files:
        name = pathlib.Path(fs.path).name
        if len(name) > 17:
            name = name[:14] + "..."
        lines_col = f"{fs.original_lines}->{fs.fixed_lines}"
        bytes_col = f"{fs.original_bytes}->{fs.fixed_bytes}"
        rows.append(
            f"{name:<18} {fs.status:<10} {lines_col:>11} {bytes_col:>15} "
            f"{fs.logical_statements:>6} {fs.comments_preserved:>5} "
            f"{fs.max_indent_level:>6} {fs.ast_nodes:>6} {fs.elapsed_ms:>9.2f}"
        )
        if fs.error:
            rows.append(f"  ERROR: {fs.error}")

    rows.append("-" * 92)
    tot_lines = f"{stats.original_total_lines}->{stats.fixed_total_lines}"
    tot_bytes = f"{stats.original_total_bytes}->{stats.fixed_total_bytes}"
    rows.append(
        f"{'TOTAL':<18} {f'{stats.fixed_files}/{stats.total_files} fixed':<10} "
        f"{tot_lines:>11} {tot_bytes:>15} "
        f"{stats.total_logical_statements:>6} {stats.total_comments_preserved:>5} "
        f"{stats.max_indent_observed:>6} {stats.total_ast_nodes:>6} "
        f"{stats.total_elapsed_ms:>9.2f}"
    )
    rows.append("=" * 92)
    rows.append(
        "Summary: "
        f"files={stats.total_files} (fixed={stats.fixed_files}, "
        f"unchanged={stats.unchanged_files}, failed={stats.failed_files}), "
        f"inline_comments_recovered={stats.total_inline_comments_split}, "
        f"shell_bridges_commented={stats.total_shell_bridges_commented}, "
        f"functions={stats.total_functions}, classes={stats.total_classes}, "
        f"loops={stats.total_loops}, conditionals={stats.total_conditionals}, "
        f"try_blocks={stats.total_try_blocks}"
    )
    return "\n".join(rows)


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="reformatter",
        description=(
            "Automatically reconstruct missing newlines and 4-space indentations in broken Python files in-place."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "Python files or directories to fix in-place. Defaults to broken*.py in the current directory if omitted."
        ),
    )
    parser.add_argument(
        "--dry-run",
        "--check",
        dest="dry_run",
        action="store_true",
        help="Analyze and report fixes without modifying files on disk.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-infer indentation even on files that already compile cleanly.",
    )
    parser.add_argument(
        "--beam-width",
        type=int,
        default=64,
        help="Beam width for dataflow-guided indentation search (default: 64).",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output batch statistics as JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for the in-place Python indentation autofixer."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    target_paths: list[pathlib.Path]
    if args.paths:
        target_paths = [pathlib.Path(p) for p in args.paths]
        missing = [p for p in target_paths if not p.exists()]
        if missing:
            for mp in missing:
                print(f"error: path does not exist: {mp}", file=sys.stderr)
            return 2
    else:
        target_paths = sorted(pathlib.Path(".").glob("broken*.py"), key=_natural_sort_key)
        if not target_paths:
            print(
                "No paths specified and no broken*.py files found in current directory.",
                file=sys.stderr,
            )
            return 2

    stats = reformat_paths(
        target_paths,
        in_place=not args.dry_run,
        force=args.force,
        beam_width=args.beam_width,
    )

    if args.json_output:
        print(json.dumps(asdict(stats), indent=2))
    else:
        print(format_stats_report(stats, dry_run=args.dry_run))

    return 1 if stats.failed_files > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
