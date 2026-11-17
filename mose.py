#!/data/data/com.termux/files/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mose.py — a dependency-free, MOSS-style source-code similarity detector.

A single-file, pure-stdlib (Python 3.8+) re-implementation of the core ideas
behind Stanford MOSS (Measure Of Software Similarity, http://theory.stanford.edu/~aiken/moss/):

  1. Tokenize each source file with a language-aware lexer (comments,
     whitespace and formatting are discarded; identifiers, numbers and
     string/char literals are normalized by default, so renaming variables
     or reformatting does not hide a copy).
  2. Hash every k-gram of tokens with a rolling (Rabin-Karp style) hash.
  3. "Winnow" the hash stream: keep the minimum hash of every window of w
     consecutive hashes (Schleimer, Wilkerson & Aiken, "Winnowing: Local
     Algorithms for Document Fingerprinting", SIGMOD 2003).  Any token match
     of length >= k + w - 1 is guaranteed to be detected.
  4. Compare the fingerprint sets of every file pair, chain neighboring
     matches into runs, and report per-file similarity percentages and the
     matching line regions.

Usage:
    python3 mose.py [options] PATH_A PATH_B
    python3 mose.py --self PATH          # compare files inside one corpus

Each PATH is one of:
    * a local directory (scanned recursively for source files)
    * a single source file
    * a git repository URL:
        https://github.com/OWNER/REPO[.git][/tree/REF[/SUBDIR]][#REF]
        https://gitlab.com/GROUP/REPO[-/tree/REF[/SUBDIR]][#REF]
        https://bitbucket.org/OWNER/REPO[/src/REF[/SUBDIR]][#REF]
      (no git binary needed — the tool downloads the archive tarball/zip)
    * a direct URL to a .zip / .tar / .tar.gz / .tar.bz2 / .tar.xz archive

Examples:
    python3 mose.py studentA/ studentB/ --html report.html
    python3 mose.py --self submissions/ --json self.json
    python3 mose.py https://github.com/octocat/RepoA local-copy/
    python3 mose.py projA/ projB/ --base starter-code/ -k 6

Key options:
    -k N          tokens per k-gram (default 5)
    -w N          winnowing window size (default 4)
    -l LANG       force a language for all files (python, c, java, ...)
    --exact-idents  keep identifier names (default: normalize to ID)
    --base PATH   base files (starter/skeleton code) excluded from matches
    --threshold P keep pairs whose max similarity >= P percent (default 0)
    --min-matched T  min matched tokens per pair (default 2*k)
    --top N       results shown on console/HTML (default 50; JSON gets all)
    --json FILE   write a machine-readable report
    --html FILE   write a self-contained HTML report with match regions
    --exclude G   skip files/dirs matching a glob (repeatable)
    --include G   also scan files matching a glob (e.g. "*.txt")
    -q / --quiet  suppress progress output

Exit status: 0 = comparison done, 1 = error (bad path, download failed, ...).

Notes:
  * Deterministic: hashes use fixed constants, so runs are reproducible.
  * Similarity of file X is (matched tokens) / (total tokens of X); the two
    directions differ when file sizes differ.
  * ``--base`` subtracts every k-gram that occurs in the base corpus, the
    standard trick for shared assignment skeletons (MOSS's -b option).
  * Small files that merely share language idioms (``for x in range(...)``,
    ``if __name__ == "__main__":`` ...) can show 20-40% similarity; that is
    genuine token overlap, not a bug.  Raise ``--threshold``, ``--min-matched``
    or ``-k`` to separate copies from idiom noise, and compare prose with
    ``--exact-idents`` so words are not all normalized away.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque
from dataclasses import dataclass, field, replace as _dc_replace
from html import escape as _hesc

# --------------------------------------------------------------------------
# constants / defaults
# --------------------------------------------------------------------------

VERSION = "1.0.0"
TOOL = "mose"

DEFAULT_K = 5                  # tokens per gram
DEFAULT_W = 4                  # winnowing window  (detect matches >= k+w-1)
DEFAULT_TOP = 50
MAX_FILE_BYTES = 1 << 20       # skip source files larger than 1 MB
SNIFF_BYTES = 8192             # binary sniff window
MAX_ARCHIVE_BYTES = 400 << 20  # download cap
DOWNLOAD_CHUNK = 1 << 16
HASH_PAIR_CAP = 65536          # skip grams occurring (|A|*|B|) more than this
MERSENNE = (1 << 61) - 1       # hash modulus (prime)
HASH_BASE = 1_000_003          # polynomial hash base

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".settings",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "env", "node_modules", "bower_components",
    "target", "build", "dist", "out", "vendor", ".gradle", ".cache",
    ".eggs", "site-packages", "Pods", "DerivedData",
}
SKIP_FILE_NAMES = {".ds_store", "thumbs.db", ".gitignore", ".gitattributes"}

# extension -> language id ("" entry handles extension-less names)
EXT_LANG = {
    ".py": "python", ".pyw": "python", ".pyi": "python",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".hh": "cpp", ".hxx": "cpp", ".inl": "cpp",
    ".m": "objc", ".mm": "objc",
    ".java": "java",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".cs": "csharp", ".swift": "swift",
    ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala", ".sc": "scala",
    ".php": "php", ".dart": "dart",
    ".rb": "ruby", ".rbw": "ruby",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ksh": "shell",
    ".sql": "sql",
}


class MoseError(Exception):
    """User-facing error (bad input, download failure, ...)."""


# --------------------------------------------------------------------------
# language tables
# --------------------------------------------------------------------------

# One generous keyword union for all C-like languages.  Over-inclusion is
# harmless here: a word kept verbatim in one language where it is merely an
# identifier name still tokenizes consistently in both compared files.
C_KEYWORDS = frozenset("""
    abstract alias alignas alignof and as asm assert async auto await base
    block bool boolean break byte case catch chan char char16_t char32_t
    class classpascal columnstore concept const consteval constexpr constinit
    const_cast continue crate debugger decltype declare deducing default
    defer defined delegate delete deleteJava dialect discard do done double
    dyn dynamic_cast echo elif else elseif endif enddeclare endfor endforeach
    endif endswitch endwhile enum error event exception exit explicit export
    extends extern extension fallthrough false final finally float fn for
    foreach forSome friend func function funcGlobal get goto guard if
    implements implicit import impure in include include_once init inline
    inout instanceof int interface internal is isset lateinit let lock long
    loop macro map match mixin mod module move mut mutable namespace native
    new nil noexcept not null nullptr object operator or override package
    params partial pass print private property protected ptr public pure
    raise readonly record ref register reinterpret_cast repeat require
    require_once restricted return sealed select set signed sizeof static
    static_assert static_cast storage strictfp struct super switch
    synchronized template this thread_local throw throws trait transient
    true try typedef typeid typename typeof typescript union unless unsafe
    unsigned use using var vararg virtual void volatile when where while
    with yield
""".split())

PY_KEYWORDS = frozenset("""
    False None True and as assert async await break class continue def del
    elif else except finally for from global if import in is lambda nonlocal
    not or pass raise return try while with yield
""".split())

RUBY_KEYWORDS = frozenset("""
    BEGIN END alias and begin break case class def defined? do else elsif
    end ensure extend false for if in include module module_function next
    nil not or private protected public raise redo require require_relative
    rescue retry return self super then true undef unless until when while
    yield attr_accessor attr_reader attr_writer attr_writer lambda loop
    catch throw
""".split())

SHELL_KEYWORDS = frozenset("""
    break case continue do done elif else esac exit export fi for function
    if in local readonly return select set shift source then time trap
    typeset unset until while coproc
""".split())

SQL_KEYWORDS = frozenset("""
    add all alter and any as asc autoincrement begin between by case cast
    check column commit constraint create cross current_date current_time
    database default delete desc distinct drop else end escape except
    exists foreign from full group having if in index inner insert
    intersect into is join key left like limit natural not null offset on
    or order outer primary procedure references rollback right rollback
    select set table then to transaction trigger union unique update
    values view when where with
""".split())


@dataclass(frozen=True)
class LangConf:
    """Lexer configuration for the C-like scanner family."""
    keywords: frozenset
    line_comments: tuple = ("//",)
    block_comment: bool = True
    nested_block: bool = False       # Rust block comments nest
    backtick_string: bool = False    # JS templates / Go raw strings / shell `…`
    js_regex: bool = False           # JS,TS: /regex/ literals
    ident_extra: str = ""            # extra identifier chars ($ for shell/php)
    ident_suffix: str = ""           # trailing ?/! (Ruby predicates)
    heredoc: bool = False            # <<TERM heredocs (ruby, shell)
    heredoc_lower: bool = False      # shell: <<eof (any case) is a heredoc
    ruby_symbols: bool = False       # :name
    ruby_beginend: bool = False      # =begin/=end block comments


def _mk(**kw):
    return LangConf(keywords=kw.pop("keywords"), **kw)


LANGS = {
    "c":          _mk(keywords=C_KEYWORDS),
    "cpp":        _mk(keywords=C_KEYWORDS),
    "objc":       _mk(keywords=C_KEYWORDS),
    "java":       _mk(keywords=C_KEYWORDS),
    "go":         _mk(keywords=C_KEYWORDS, backtick_string=True),
    "rust":       _mk(keywords=C_KEYWORDS, nested_block=True),
    "csharp":     _mk(keywords=C_KEYWORDS),
    "swift":      _mk(keywords=C_KEYWORDS),
    "kotlin":     _mk(keywords=C_KEYWORDS),
    "scala":      _mk(keywords=C_KEYWORDS),
    "dart":       _mk(keywords=C_KEYWORDS),
    "javascript": _mk(keywords=C_KEYWORDS, backtick_string=True, js_regex=True,
                      line_comments=("//",), ident_extra="$"),
    "typescript": _mk(keywords=C_KEYWORDS, backtick_string=True, js_regex=True,
                      ident_extra="$"),
    "php":        _mk(keywords=C_KEYWORDS, line_comments=("//", "#"),
                      ident_extra="$"),
    "sql":        _mk(keywords=SQL_KEYWORDS, line_comments=("--",)),
    "ruby":       _mk(keywords=RUBY_KEYWORDS, line_comments=("#",),
                      block_comment=False, ident_suffix="?!", heredoc=True,
                      ruby_symbols=True, ruby_beginend=True),
    "shell":      _mk(keywords=SHELL_KEYWORDS, line_comments=("#",),
                      block_comment=False, ident_extra="$", heredoc=True,
                      heredoc_lower=True, backtick_string=True),
    "python":     None,   # dedicated lexer
    "generic":    None,   # dedicated fallback lexer
}

# Multi-char operators (union over all supported languages; the tokenizer is
# shared, so tokenization is consistent for every compared file).
_OPS = sorted(
    """
    >>>= <<= >>= **= //= &&= ||= ??= ... ..= ..< === !== >>> ** :: -> => <-
    := ++ -- << >> <= >= == != && || ?? ?. ?: &^= &^ ;;
    """.split(),
    key=len, reverse=True,
)
OPS_RE = re.compile("|".join(re.escape(o) for o in _OPS) + "|.")

PY_OPS = sorted(
    """
    **= //= >>= <<= += -= *= /= %= &= |= ^= @= ... := -> ** // << >> <= >=
    == != @ + - * / % < > = & | ^ ~ ( ) [ ] { } , : ; . !
    """.split(),
    key=len, reverse=True,
)
PY_OPS_RE = re.compile("|".join(re.escape(o) for o in PY_OPS) + "|[^\n]")

PY_NUM_RE = re.compile(r"\d[\w.]*|\.\d[\w.]*")
PY_IDENT_RE = re.compile(r"[^\W\d]\w*", re.UNICODE)

NUM_RE = re.compile(r"\d[\w.]*")
CHAR_RE = re.compile(r"'([^'\\\n]|\\.)'")
HEREDOC_RE = re.compile(r"<<(-|\~)?(['\"]?)([A-Za-z_]\w*)\2")
# chars that may precede a JS regex literal (heuristic)
_REGEX_OK_OPS = {")", "]", "}", "++", "--"}


def _ident_re(conf):
    extra = re.escape(conf.ident_extra) if conf.ident_extra else ""
    suffix = ("[" + conf.ident_suffix + "]?") if conf.ident_suffix else ""
    start = r"[^\W\d]" if not extra else r"(?:[^\W\d]|[%s])" % extra
    return re.compile(start + r"[\w%s]*%s" % (extra, suffix), re.UNICODE)


_IDENT_CACHE = {}


def ident_re(conf):
    rx = _IDENT_CACHE.get(conf)
    if rx is None:
        rx = _ident_re(conf)
        _IDENT_CACHE[conf] = rx
    return rx


# --------------------------------------------------------------------------
# token table
# --------------------------------------------------------------------------

class TokenTable:
    """Interns token strings -> small ints so grams hash over dense ints."""

    __slots__ = ("_ids",)

    def __init__(self):
        self._ids = {}

    def get(self, text):
        i = self._ids.get(text)
        if i is None:
            i = len(self._ids)
            self._ids[text] = i
        return i

    def __len__(self):
        return len(self._ids)


# --------------------------------------------------------------------------
# lexers — every lexer returns a list of (token_text, line_number) tuples.
# token_text is the *normalized* token ("ID", "NUM", "STR", keyword, operator)
# unless ``exact`` is set, in which case identifiers/numbers stay verbatim.
# --------------------------------------------------------------------------

def _skip_heredoc_bodies(src, i, line, patterns):
    """Advance past the body of every pending heredoc (called at EOL)."""
    n = len(src)
    for pat in patterns:
        mm = pat.search(src, i)
        if mm is None:                        # unterminated: rest is body
            return n, line + src.count("\n", i, n)
        line += src.count("\n", i, mm.end())
        i = mm.end()
    patterns.clear()
    return i, line


def lex_c_like(src, conf, exact):
    """Shared scanner for the C-like language family (incl. ruby/shell)."""
    toks = []
    n = len(src)
    i = 0
    line = 1
    bol = True
    last_kind = ""                       # "", "id", "kw", "num", "str", "op"
    last_raw = ""
    heredocs = []                        # pending heredoc terminators
    ire = ident_re(conf)
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            bol = True
            if heredocs:
                i, line = _skip_heredoc_bodies(src, i, line, heredocs)
            continue
        if c in " \t\r\f\v":
            i += 1
            continue
        # Ruby =begin ... =end block comment
        if conf.ruby_beginend and bol and src.startswith("=begin", i):
            j = i
            consumed = False
            while j < n:
                j2 = src.find("\n", j)
                if j2 == -1:
                    j2 = n
                if src[j:j2].startswith("=end"):
                    line += src.count("\n", i, min(n, j2 + 1))
                    i = min(n, j2 + 1)
                    consumed = True
                    break
                j = j2 + 1
            if not consumed:
                i = n
            continue
        # line comments
        hit = False
        for lc in conf.line_comments:
            if src.startswith(lc, i):
                j = src.find("\n", i)
                i = n if j == -1 else j
                hit = True
                break
        if hit:
            continue
        # block comments /* ... */ (Rust's nest)
        if conf.block_comment and src.startswith("/*", i):
            depth = 1
            j = i + 2
            while j < n and depth:
                if conf.nested_block and src.startswith("/*", j):
                    depth += 1
                    j += 2
                elif src.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    if src[j] == "\n":
                        line += 1
                    j += 1
            i = j
            continue
        # heredocs  <<TERM / <<-TERM / <<~TERM / <<'TERM' / <<"TERM"
        # The marker itself becomes a STR token; the body is skipped when
        # the rest of the current line has been lexed (see _skip_heredoc_bodies).
        if (conf.heredoc and c == "<" and src.startswith("<<", i)
                and not src.startswith("<<=", i)):
            m = HEREDOC_RE.match(src, i)
            if m:
                dash = bool(m.group(1))
                quoted = bool(m.group(2))
                term = m.group(3)
                looks_heredoc = (dash or quoted or conf.heredoc_lower
                                 or term[0].isupper())
                if looks_heredoc:
                    if dash or quoted:
                        pat = re.compile(r"^[ \t]*" + re.escape(term)
                                         + r"[ \t\r]*$", re.M)
                    else:
                        pat = re.compile(r"^" + re.escape(term)
                                         + r"[ \t\r]*$", re.M)
                    heredocs.append(pat)
                    toks.append(("STR", line))
                    i = m.end()
                    last_kind = "str"
                    bol = False
                    continue
                # plain shift like  x << y  or  x<<y: lex "<<" as operator
        # double-quoted strings (may not span a raw newline)
        if c == '"':
            start_line = line
            j = i + 1
            while j < n:
                ch = src[j]
                if ch == "\\" and j + 1 < n:
                    if src[j + 1] == "\n":
                        line += 1
                    j += 2
                    continue
                if ch == '"':
                    j += 1
                    break
                if ch == "\n":
                    break                          # unterminated: stop at EOL
                j += 1
            toks.append(("STR", start_line))
            i = j
            last_kind = "str"
            bol = False
            continue
        # single quotes: char literal 'x' / '\n', else a lone quote
        # (Rust lifetimes 'a lex as the "'" operator followed by an ident)
        if c == "'":
            m = CHAR_RE.match(src, i)
            if m:
                toks.append(("CHR", line))
                i = m.end()
                last_kind = "str"
                bol = False
                continue
            toks.append(("'", line))
            i += 1
            last_kind = "op"
            last_raw = "'"
            bol = False
            continue
        # backtick strings: JS templates, Go raw strings, shell `cmd`
        if c == "`" and conf.backtick_string:
            start_line = line
            j = src.find("`", i + 1)
            end = n if j == -1 else j + 1
            line += src.count("\n", i, end)
            toks.append(("STR", start_line))
            i = end
            last_kind = "str"
            bol = False
            continue
        # Ruby :symbols
        if conf.ruby_symbols and c == ":":
            m = ire.match(src, i + 1)
            if m:
                toks.append(("SYM", line))
                i = m.end()
                last_kind = "str"
                bol = False
                continue
        # JS/TS regex literals:  /.../flags   (heuristic on previous token)
        if c == "/" and conf.js_regex:
            ok = (last_kind in ("", "kw")
                  or (last_kind == "op" and last_raw not in _REGEX_OK_OPS))
            if ok:
                j = i + 1
                in_class = False
                closed = False
                while j < n:
                    ch = src[j]
                    if ch == "\\" and j + 1 < n:
                        j += 2
                        continue
                    if ch == "\n":
                        break
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        j += 1
                        closed = True
                        break
                    j += 1
                if closed:
                    m2 = re.match(r"[a-z]*", src[j:])
                    j += m2.end() if m2 else 0
                    toks.append(("STR", line))
                    i = j
                    last_kind = "str"
                    bol = False
                    continue
                # not a regex after all: fall through to operator handling
        # numbers (0x1F, 1_000, 1.5e3, 10UL, ...)
        if c.isdigit():
            m = NUM_RE.match(src, i)
            toks.append((m.group(0) if exact else "NUM", line))
            i = m.end()
            last_kind = "num"
            bol = False
            continue
        # identifiers / keywords
        if c.isalpha() or c == "_" or (conf.ident_extra and c in conf.ident_extra):
            m = ire.match(src, i)
            text = m.group(0)
            kw = text in conf.keywords
            toks.append((text if (kw or exact) else "ID", line))
            i = m.end()
            last_kind = "kw" if kw else "id"
            bol = False
            continue
        # operators: longest match, single punctuation char as fallback
        m = OPS_RE.match(src, i)
        text = m.group(0)
        toks.append((text, line))
        i = m.end()
        last_kind = "op"
        last_raw = text
        bol = False
        continue
    return toks


def _scan_py_string(src, i):
    """Scan a Python string starting at the quote (prefix already consumed).

    Returns (end_index, newline_count).  Handles triple quotes, escape
    sequences and line continuations inside strings.
    """
    n = len(src)
    q = src[i]
    triple = src[i:i + 3] == q * 3
    j = i + (3 if triple else 1)
    nl = 0
    while j < n:
        ch = src[j]
        if ch == "\\" and j + 1 < n:
            if src[j + 1] == "\n":
                nl += 1
            j += 2
            continue
        if ch == "\n":
            if not triple:
                break                          # unterminated single-line
            nl += 1
            j += 1
            continue
        if triple:
            if src.startswith(q * 3, j):
                return j + 3, nl
        elif ch == q:
            return j + 1, nl
        j += 1
    return j, nl


_PY_PREFIXES = {"r", "b", "u", "f", "rb", "br", "fr", "rf"}


def lex_python(src, exact):
    """Python lexer with INDENT/DEDENT and NEWLINE tokens (PEP 8-ish)."""
    toks = []
    n = len(src)
    i = 0
    line = 1
    bol = True          # at the start of a logical line (indent pending)
    pending = False     # current logical line emitted at least one token
    depth = 0           # open ( [ { — newlines inside brackets are plain ws
    indents = [0]

    while i < n:
        c = src[i]
        if bol and depth == 0:
            j = i
            col = 0
            while j < n and src[j] in " \t":
                if src[j] == "\t":
                    col = (col // 8 + 1) * 8
                else:
                    col += 1
                j += 1
            if j >= n:
                break
            if src[j] == "\n":
                line += 1
                i = j + 1
                continue
            if src[j] == "#":                 # comment-only line
                i = j
                bol = False                    # let the main loop eat it
                continue
            if col > indents[-1]:
                indents.append(col)
                toks.append(("INDENT", line))
            else:
                while col < indents[-1]:
                    indents.pop()
                    toks.append(("DEDENT", line))
            bol = False
            i = j
            continue
        if c == "\n":
            line += 1
            i += 1
            if depth == 0:
                if pending:
                    toks.append(("NEWLINE", line - 1))
                    pending = False
                bol = True
            continue
        if c in " \t\r\f\v":
            i += 1
            continue
        if c == "\\" and i + 1 < n and src[i + 1] == "\n":
            line += 1
            i += 2
            continue
        if c == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == '"' or c == "'":
            end, nl = _scan_py_string(src, i)
            toks.append(("STR", line))
            line += nl
            i = end
            pending = True
            continue
        m = PY_IDENT_RE.match(src, i)
        if m:
            nxt = m.end()
            # string prefix: r"..." f'...' rb"""...""" ...
            if nxt < n and src[nxt] in "\"'" and m.group(0).lower() in _PY_PREFIXES:
                end, nl = _scan_py_string(src, nxt)
                toks.append(("STR", line))
                line += nl
                i = end
                pending = True
                continue
            text = m.group(0)
            toks.append((text if (exact or text in PY_KEYWORDS) else "ID", line))
            i = nxt
            pending = True
            continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            m = PY_NUM_RE.match(src, i)
            toks.append((m.group(0) if exact else "NUM", line))
            i = m.end()
            pending = True
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}" and depth > 0:
            depth -= 1
        m = PY_OPS_RE.match(src, i)
        toks.append((m.group(0), line))
        i = m.end()
        pending = True
        continue

    if pending:
        toks.append(("NEWLINE", line))
    while len(indents) > 1:
        indents.pop()
        toks.append(("DEDENT", line))
    return toks


_GENERIC_TOKEN_RE = re.compile(r"[^\W\d]\w*|\d[\w.]*|[^\s]", re.UNICODE)


def lex_generic(src, exact):
    """Fallback lexer: words, numbers and single punctuation characters."""
    out = []
    line = 1
    last = 0
    for m in _GENERIC_TOKEN_RE.finditer(src):
        line += src.count("\n", last, m.start())
        last = m.start()
        t = m.group(0)
        if t[0].isdigit():
            out.append((t if exact else "NUM", line))
        elif t[0].isalpha() or t[0] == "_":
            out.append((t if exact else "ID", line))
        else:
            out.append((t, line))
    return out


def lex_source(src, lang, exact):
    if lang == "python":
        return lex_python(src, exact)
    if lang == "generic":
        return lex_generic(src, exact)
    conf = LANGS.get(lang)
    if conf is None:
        raise MoseError("unknown language: %r" % lang)
    return lex_c_like(src, conf, exact)


# --------------------------------------------------------------------------
# fingerprinting: rolling k-gram hashes + winnowing
# --------------------------------------------------------------------------

def gram_hashes(ids, k):
    """Rabin-Karp rolling polynomial hash of every k-gram (mod 2^61-1)."""
    if len(ids) < k:
        return []
    P = MERSENNE
    B = HASH_BASE
    Bk = pow(B, k - 1, P)
    h = 0
    for x in ids[:k]:
        h = (h * B + x) % P
    out = [h]
    for j in range(k, len(ids)):
        h = ((h - ids[j - k] * Bk) * B + ids[j]) % P
        out.append(h)
    return out


def winnow(hashes, w):
    """Winnowing (Schleimer et al. 2003).

    Keeps the rightmost minimum of every window of ``w`` consecutive hashes;
    returns {gram_position: hash}.  Guarantees that any match of at least
    ``k + w - 1`` tokens leaves a shared fingerprint.
    """
    n = len(hashes)
    sel = {}
    if n == 0:
        return sel
    if n < w:
        best = min(hashes)
        sel[n - 1 - hashes[::-1].index(best)] = best
        return sel
    dq = deque()                       # (hash, position), increasing hashes
    for i, h in enumerate(hashes):
        while dq and dq[-1][0] >= h:   # ties: keep the rightmost
            dq.pop()
        dq.append((h, i))
        while dq[0][1] <= i - w:       # leftmost candidate left the window
            dq.popleft()
        if i >= w - 1:
            p0 = dq[0][1]
            if p0 not in sel:
                sel[p0] = dq[0][0]
    # practical extension: always keep the final gram so that byte-identical
    # files (which share every gram) compare at exactly 100%
    if n - 1 not in sel:
        sel[n - 1] = hashes[n - 1]
    return sel


def fingerprint_file(f, k, w):
    """Fill f.fps; returns the full hash list (needed for --base)."""
    if f.ntok < k:
        f.fps = {}
        return []
    hashes = gram_hashes(f.ids, k)
    f.fps = winnow(hashes, w)
    return hashes


# --------------------------------------------------------------------------
# corpus model
# --------------------------------------------------------------------------

@dataclass
class SourceFile:
    path: str                     # display path, relative to the corpus root
    lang: str
    lines: list                   # file content, for reports
    ids: list                     # token ids (ints from the shared TokenTable)
    tok_lines: list               # source line number of every token
    fps: dict = field(default_factory=dict)   # gram position -> hash

    @property
    def ntok(self):
        return len(self.ids)


@dataclass
class Corpus:
    label: str
    kind: str = "dir"             # dir / file / git / archive
    files: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # (path, reason), capped

    def total_tokens(self):
        return sum(f.ntok for f in self.files)


def _note_skip(corpus, rel, reason):
    if len(corpus.skipped) < 500:
        corpus.skipped.append((rel, reason))


def _looks_binary(data):
    head = data[:SNIFF_BYTES]
    if b"\x00" in head:
        return True
    if head:
        ctrl = sum(1 for b in head if b < 32 and b not in (9, 10, 12, 13, 27))
        return ctrl * 20 > len(head)          # > 5% control characters
    return False


def file_language(name, opts):
    """Language for a file name, or None if the file should not be scanned."""
    if opts.language:
        return opts.language
    ext = os.path.splitext(name)[1].lower()
    lang = EXT_LANG.get(ext)
    if lang:
        return lang
    for pat in opts.include or ():
        if fnmatch.fnmatch(name, pat):
            return "generic"
    return None


def _is_excluded(rel, opts):
    rel = rel.replace(os.sep, "/")
    parts = rel.split("/")
    for pat in opts.exclude or ():
        if (fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(parts[-1], pat)
                or any(fnmatch.fnmatch(p, pat) for p in parts)):
            return True
    return False


def _add_text_file(corpus, rel, text, lang, opts, table):
    """Lex text and append a SourceFile; returns False if skipped."""
    try:
        toks = lex_source(text, lang, opts.exact_idents)
    except MoseError:
        raise
    except Exception as e:                     # lexer must never kill a run
        _note_skip(corpus, rel, "lex error: %s" % e)
        return False
    if len(toks) < opts.k:
        _note_skip(corpus, rel, "too few tokens")
        return False
    corpus.files.append(SourceFile(
        path=rel.replace(os.sep, "/"),
        lang=lang,
        lines=text.splitlines(),
        ids=[table.get(t) for t, _ in toks],
        tok_lines=[ln for _, ln in toks],
    ))
    return True


def _add_disk_file(corpus, full, rel, lang, opts, table):
    try:
        if os.path.getsize(full) > opts.max_file_kb * 1024:
            _note_skip(corpus, rel, "too large")
            return
        with open(full, "rb") as fh:
            data = fh.read()
    except OSError:
        _note_skip(corpus, rel, "unreadable")
        return
    if _looks_binary(data):
        _note_skip(corpus, rel, "binary")
        return
    _add_text_file(corpus, rel, data.decode("utf-8-sig", "replace"),
                   lang, opts, table)


def scan_dir(corpus, root, subdir, opts, table):
    """Append every source file under root/subdir to the corpus."""
    base = os.path.join(root, subdir) if subdir else root
    base = os.path.normpath(base)
    if not os.path.isdir(base):
        raise MoseError('directory not found: "%s"' % base)
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
            and not _is_excluded(os.path.relpath(os.path.join(dirpath, d), base), opts)
        )
        for fn in sorted(filenames):
            if fn.lower() in SKIP_FILE_NAMES:
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), base)
            if _is_excluded(rel, opts):
                continue
            lang = file_language(fn, opts)
            if lang is None:
                _note_skip(corpus, rel, "not a source file")
                continue
            _add_disk_file(corpus, os.path.join(dirpath, fn), rel,
                           lang, opts, table)


# --------------------------------------------------------------------------
# remote repositories (no git binary — plain HTTPS archive downloads)
# --------------------------------------------------------------------------

UA = TOOL + "/" + VERSION


@dataclass
class RemoteSpec:
    provider: str          # github / gitlab / bitbucket / archive
    project: str           # "owner/repo", "group/sub/repo" or a plain URL
    ref: str               # branch / tag / commit / "HEAD" / ""
    subdir: str            # path inside the repo, "" for the root
    single_file: str       # non-empty for raw single-file URLs
    label: str


def looks_like_url(spec):
    return bool(re.match(r"^(?:https?://|git\+?ssh://|git://|ssh://git@|git@)",
                         spec, re.I))


def _normalize_git_url(spec):
    m = re.match(r"^(?:git@|ssh://git@)([^:/]+)[:/](.+?)(?:\.git)?$", spec)
    if m:
        return "https://%s/%s" % (m.group(1), m.group(2))
    m = re.match(r"^git://([^/]+)/(.+)$", spec)
    if m:
        return "https://%s/%s" % (m.group(1), m.group(2))
    return spec


def parse_remote(url):
    url = _normalize_git_url(url)
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise MoseError("unsupported URL: %s" % url)
    host = u.netloc.split("@")[-1].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    path = u.path.strip("/")
    frag = urllib.parse.unquote(u.fragment.strip()) if u.fragment else ""

    if host in ("github.com", "codeload.github.com", "raw.githubusercontent.com"):
        m = re.match(
            r"^([^/]+)/([^/]+?)(?:\.git)?"
            r"(?:/(tree|blob|commits?)/([^/]+)((?:/.*)?))?$", path)
        if not m:
            raise MoseError("cannot parse GitHub URL: %s" % url)
        owner, repo, kind, ref, rest = m.groups()
        subdir = rest.lstrip("/") if kind == "tree" and rest else ""
        single = rest.lstrip("/") if kind == "blob" and rest else ""
        ref = frag or ref or ""
        project = owner + "/" + repo
        label = "github.com/%s%s" % (project, "@" + ref if ref else "")
        return RemoteSpec("github", project, ref, subdir, single, label)

    if host == "gitlab.com":
        m = re.match(r"^(.+)/-/((?:tree|blob)/)([^/]+)((?:/.*)?)$", path)
        if m:
            project, kind, ref, rest = m.groups()
            subdir = rest.lstrip("/") if kind == "tree/" and rest else ""
            single = rest.lstrip("/") if kind == "blob/" and rest else ""
        else:
            project, ref, subdir, single = path, "", "", ""
        ref = frag or ref
        label = "gitlab.com/%s%s" % (project, "@" + ref if ref else "")
        return RemoteSpec("gitlab", project.strip("/"), ref, subdir, single,
                          label)

    if host == "bitbucket.org":
        m = re.match(r"^([^/]+)/([^/]+?)(?:\.git)?"
                     r"(?:/src/([^/]+)((?:/.*)?))?$", path)
        if not m:
            raise MoseError("cannot parse Bitbucket URL: %s" % url)
        owner, repo, ref, rest = m.groups()
        subdir = rest.lstrip("/") if rest else ""
        ref = frag or ref or ""
        project = owner + "/" + repo
        label = "bitbucket.org/%s%s" % (project, "@" + ref if ref else "")
        return RemoteSpec("bitbucket", project, ref, subdir, "", label)

    if re.search(r"\.(zip|tar|tgz)(?:[?#].*)?$", path) \
            or re.search(r"\.tar\.(?:gz|bz2|xz)$", path):
        return RemoteSpec("archive", url, "", "", "",
                          os.path.basename(path) or host)

    raise MoseError(
        "unsupported repository host %r; supported: github.com, gitlab.com, "
        "bitbucket.org, or a direct .zip/.tar archive URL" % host)


def _archive_url(spec):
    if spec.provider == "github":
        return "https://codeload.github.com/%s/zip/%s" % (
            spec.project, spec.ref or "HEAD")
    if spec.provider == "gitlab":
        name = spec.project.rsplit("/", 1)[-1]
        return "https://gitlab.com/%s/-/archive/%s/%s-%s.zip" % (
            spec.project, spec.ref, name, spec.ref)
    if spec.provider == "bitbucket":
        return "https://bitbucket.org/%s/get/%s.zip" % (spec.project, spec.ref)
    return spec.project                      # direct archive URL


def _raw_file_url(spec):
    p, r, f = spec.project, spec.ref or "HEAD", spec.single_file
    if spec.provider == "github":
        return "https://raw.githubusercontent.com/%s/%s/%s" % (p, r, f)
    if spec.provider == "gitlab":
        return "https://gitlab.com/%s/-/raw/%s/%s" % (p, r, f)
    raise MoseError("raw file not supported for %s" % spec.provider)


def _fetch_json(url, timeout=15):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _resolve_default_ref(spec):
    """Default branch for gitlab/bitbucket via their public APIs."""
    try:
        if spec.provider == "gitlab":
            enc = urllib.parse.quote(spec.project, safe="")
            info = _fetch_json("https://gitlab.com/api/v4/projects/%s" % enc)
            return info.get("default_branch") or None
        if spec.provider == "bitbucket":
            info = _fetch_json(
                "https://api.bitbucket.org/2.0/repositories/%s" % spec.project)
            return (info.get("mainbranch") or {}).get("name")
    except Exception:
        return None
    return None


def _download(url, dest_path, opts):
    """Stream ``url`` to ``dest_path``; raises MoseError on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            got = 0
            with open(dest_path, "wb") as out:
                while True:
                    chunk = resp.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > MAX_ARCHIVE_BYTES:
                        raise MoseError(
                            "download larger than %d MB: %s"
                            % (MAX_ARCHIVE_BYTES >> 20, url))
                    out.write(chunk)
    except urllib.error.HTTPError as e:
        raise MoseError("HTTP %s while fetching %s" % (e.code, url))
    except urllib.error.URLError as e:
        raise MoseError("cannot fetch %s (%s)" % (url, e.reason))
    except (OSError, TimeoutError) as e:
        raise MoseError("cannot fetch %s (%s)" % (url, e))
    return got


def _download_bytes(url, opts, cap=MAX_FILE_BYTES * 8):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read(cap + 1)
    except urllib.error.HTTPError as e:
        raise MoseError("HTTP %s while fetching %s" % (e.code, url))
    except urllib.error.URLError as e:
        raise MoseError("cannot fetch %s (%s)" % (url, e.reason))
    if len(data) > cap:
        raise MoseError("remote file too large: %s" % url)
    return data


def _safe_extract_zip(zpath, dest):
    total = 0
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            base = os.path.basename(name)
            if name.startswith("__MACOSX/") or base.startswith("._") \
                    or base == ".DS_Store":
                continue
            parts = name.split("/")
            if not name or name.startswith("/") or ".." in parts:
                continue                          # path traversal attempt
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                continue                          # symlink
            total += info.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise MoseError("archive too large when decompressed")
            target = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _safe_extract_tar(tpath, dest):
    total = 0
    with tarfile.open(tpath, "r:*") as tf:
        for m in tf:
            if not m.isreg():
                continue                          # skip links, dirs, devices
            name = m.name.replace("\\", "/").lstrip("/")
            base = os.path.basename(name)
            if base.startswith("._") or base == ".DS_Store" \
                    or "__MACOSX" in name:
                continue
            parts = name.split("/")
            if not name or ".." in parts:
                continue
            total += m.size
            if total > MAX_ARCHIVE_BYTES:
                raise MoseError("archive too large when decompressed")
            fsrc = tf.extractfile(m)
            if fsrc is None:
                continue
            target = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with fsrc, open(target, "wb") as out:
                shutil.copyfileobj(fsrc, out)


def _strip_root(dest):
    entries = [e for e in os.listdir(dest) if not e.startswith("__MACOSX")]
    if len(entries) == 1:
        p = os.path.join(dest, entries[0])
        if os.path.isdir(p):
            return p
    return dest


def _fmt_bytes(n):
    if n >= (1 << 20):
        return "%.1f MB" % (n / (1 << 20))
    if n >= (1 << 10):
        return "%.1f KB" % (n / (1 << 10))
    return "%d B" % n


def _remote_label(spec):
    extra = ("/" + spec.single_file) if spec.single_file \
        else (("/" + spec.subdir) if spec.subdir else "")
    return spec.label + extra


def load_remote(spec_str, opts, table, tmpdirs):
    rs = parse_remote(_normalize_git_url(spec_str))

    # -- single raw file (github/gitlab blob URLs) --
    if rs.single_file and rs.provider in ("github", "gitlab"):
        data = _download_bytes(_raw_file_url(rs), opts)
        text = data.decode("utf-8-sig", "replace")
        name = rs.single_file.rsplit("/", 1)[-1]
        lang = opts.language or EXT_LANG.get(
            os.path.splitext(name)[1].lower()) or "generic"
        corpus = Corpus(label=_remote_label(rs), kind="git")
        if not _add_text_file(corpus, name, text, lang, opts, table):
            raise MoseError("remote file %s has too few tokens to compare"
                            % rs.label)
        return corpus

    # -- whole-repo archive --
    refs = [rs.ref] if rs.ref else []
    if not refs:
        if rs.provider == "github":
            refs = ["HEAD"]
        else:
            resolved = _resolve_default_ref(rs)
            refs = [resolved] if resolved else ["main", "master"]
    archive_url = None
    zpath = None
    workdir = tempfile.mkdtemp(prefix="mose-")
    tmpdirs.append(workdir)
    last_err = None
    for ref in refs:
        spec = rs if ref == rs.ref else _dc_replace(rs, ref=ref)
        try:
            if not opts.quiet:
                print("downloading %s ..." % _archive_url(spec),
                      file=sys.stderr)
            zpath = os.path.join(workdir, "archive")
            _download(_archive_url(spec), zpath, opts)
            archive_url = _archive_url(spec)
            rs = spec
            break
        except MoseError as e:
            last_err = e
            zpath = None
    if zpath is None:
        raise MoseError("could not download %s (%s)"
                        % (rs.label, last_err or "no ref"))
    if not opts.quiet:
        print("ok, got %s" % _fmt_bytes(os.path.getsize(zpath)),
              file=sys.stderr)

    extract_dir = os.path.join(workdir, "x")
    os.makedirs(extract_dir, exist_ok=True)
    clean = archive_url.lower().split("?")[0].split("#")[0]
    tarball = clean.endswith((".tar", ".tgz", ".gz", ".bz2", ".xz")) \
        and ".zip" not in clean
    try:
        if tarball:
            _safe_extract_tar(zpath, extract_dir)
        else:
            _safe_extract_zip(zpath, extract_dir)
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as e:
        raise MoseError("cannot extract archive from %s (%s)"
                        % (archive_url, e))

    base = _strip_root(extract_dir)
    if rs.subdir:
        cand = os.path.normpath(os.path.join(base, rs.subdir))
        if os.path.isfile(cand):                  # src URL pointed at a file
            corpus = Corpus(label=_remote_label(rs), kind="git")
            name = os.path.basename(cand)
            lang = file_language(name, opts) or "generic"
            _add_disk_file(corpus, cand, name, lang, opts, table)
            if not corpus.files:
                raise MoseError("remote file %s/%s has too few tokens"
                                % (rs.label, rs.subdir))
            return corpus
        base = cand
        if not os.path.isdir(base):
            raise MoseError('subdirectory "%s" not found in %s'
                            % (rs.subdir, rs.label))
    corpus = Corpus(label=_remote_label(rs), kind="git")
    scan_dir(corpus, base, None, opts, table)
    if not corpus.files:
        raise _no_files_error(rs)
    return corpus


def _no_files_error(rs):
    return MoseError(
        "no source files found in %s (try --language or --include)" % rs.label)


# --------------------------------------------------------------------------
# corpus loading entry point
# --------------------------------------------------------------------------

def load_corpus(spec, opts, table, tmpdirs):
    if looks_like_url(spec):
        return load_remote(spec, opts, table, tmpdirs)
    if os.path.isdir(spec):
        corpus = Corpus(label=spec.rstrip("/\\") or spec, kind="dir")
        scan_dir(corpus, spec, None, opts, table)
        if not corpus.files:
            raise MoseError(
                'no source files found under "%s" (try --language or '
                '--include, or check --exclude patterns)' % spec)
        return corpus
    if os.path.isfile(spec):
        lang = file_language(os.path.basename(spec), opts)
        if lang is None:
            raise MoseError(
                'unrecognized source file "%s"; use -l LANG or --include'
                % spec)
        corpus = Corpus(label=os.path.basename(spec), kind="file")
        _add_disk_file(corpus, os.path.abspath(spec), os.path.basename(spec),
                       lang, opts, table)
        if not corpus.files:
            raise MoseError('file "%s" has too few tokens to compare' % spec)
        return corpus
    raise MoseError('path not found: "%s"' % spec)


# --------------------------------------------------------------------------
# comparison engine
# --------------------------------------------------------------------------

@dataclass
class PairMatch:
    file_a: SourceFile
    file_b: SourceFile
    runs: list                  # [(a0, a1, b0, b1)] token spans (a1 exclusive)
    matched_a: int              # union of matched token intervals, side A
    matched_b: int
    sim_a: float                # percent of file A matched by B
    sim_b: float
    total_grams: int            # paired fingerprints


def build_inverted(corpus):
    """hash -> [(file_index, gram_position), ...] (both ascending)."""
    inv = {}
    for fi, f in enumerate(corpus.files):
        for pos, h in f.fps.items():
            lst = inv.get(h)
            if lst is None:
                inv[h] = lst = []
            lst.append((fi, pos))
    return inv


def chain_runs(pairs, k, gap):
    """Chain gram-start pairs into runs [(a0, a1, b0, b1)].

    Two matches join when both sides advance by at most ``gap`` tokens, so
    small edits inside a copied block still produce one long region.
    """
    runs = []
    last_a = last_b = 0
    for a, b in sorted(pairs):
        if runs and a > last_a and b > last_b \
                and a - last_a <= gap and b - last_b <= gap:
            r = runs[-1]
            runs[-1] = (r[0], a + k, r[2], b + k)
        else:
            runs.append((a, a + k, b, b + k))
        last_a, last_b = a, b
    return runs


def _union_len(intervals, ntok):
    """Total number of tokens covered by possibly overlapping intervals."""
    total = 0
    cur0 = cur1 = None
    for a, b in sorted(intervals):
        a = max(0, a)
        b = min(ntok, b)
        if b <= a:
            continue
        if cur1 is None or a > cur1:
            if cur1 is not None:
                total += cur1 - cur0
            cur0, cur1 = a, b
        elif b > cur1:
            cur1 = b
    if cur1 is not None:
        total += cur1 - cur0
    return total


def compare_corpora(corpA, corpB, identical, base_hashes, opts):
    """All PairMatches between corpus A and corpus B, filtered and sorted."""
    invA = build_inverted(corpA)
    invB = invA if identical else build_inverted(corpB)
    k = opts.k
    pairhits = {}
    small = invA if len(invA) <= len(invB) else invB
    for h in small:
        la = invA.get(h)
        lb = invB.get(h)
        if not la or not lb:
            continue
        if base_hashes is not None and h in base_hashes:
            continue
        if len(la) * len(lb) > opts.max_hash_pairs:
            continue                        # gram occurs far too often: noise
        ga = {}
        for fi, pos in la:
            ga.setdefault(fi, []).append(pos)
        gb = {}
        for fi, pos in lb:
            gb.setdefault(fi, []).append(pos)
        for fa, pas in ga.items():
            for fb, pbs in gb.items():
                if identical and fa >= fb:
                    continue                # each unordered pair once, no self
                hits = pairhits.get((fa, fb))
                if hits is None:
                    pairhits[(fa, fb)] = hits = []
                hits.extend(zip(pas, pbs))

    matches = []
    for (fa, fb), hits in pairhits.items():
        runs = chain_runs(hits, k, opts.merge_gap)
        if not runs:
            continue
        faf, fbf = corpA.files[fa], corpB.files[fb]
        ma = _union_len([(r[0], r[1]) for r in runs], faf.ntok)
        mb = _union_len([(r[2], r[3]) for r in runs], fbf.ntok)
        matches.append(PairMatch(
            file_a=faf, file_b=fbf, runs=runs,
            matched_a=ma, matched_b=mb,
            sim_a=(100.0 * ma / faf.ntok) if faf.ntok else 0.0,
            sim_b=(100.0 * mb / fbf.ntok) if fbf.ntok else 0.0,
            total_grams=len(hits),
        ))

    out = []
    for pm in matches:
        if max(pm.matched_a, pm.matched_b) < opts.min_matched_tokens:
            continue
        if max(pm.sim_a, pm.sim_b) < opts.threshold:
            continue
        out.append(pm)
    out.sort(key=lambda m: (-max(m.matched_a, m.matched_b),
                            -max(m.sim_a, m.sim_b),
                            m.file_a.path, m.file_b.path))
    return out


# --------------------------------------------------------------------------
# reports: console + JSON
# --------------------------------------------------------------------------

def _run_lines(pm):
    """[(a0line, a1line, b0line, b1line)] for each run."""
    res = []
    for a0, a1, b0, b1 in pm.runs:
        la = pm.file_a.tok_lines
        lb = pm.file_b.tok_lines
        res.append((la[a0], la[a1 - 1], lb[b0], lb[b1 - 1]))
    return res


def _fmt_pct(x):
    return ("%5.1f" % x) if x >= 99.95 else ("%5.1f" % round(x, 1))


def print_report(matches, corpA, corpB, base_corpus, opts, pairs_compared):
    out = sys.stdout
    ident = "identifiers kept" if opts.exact_idents else "identifiers normalized"
    print("%s v%s | winnowing k=%d w=%d | %s" %
          (TOOL, VERSION, opts.k, opts.w, ident), file=out)

    def corpus_line(tag, c):
        print("%-4s %s | %d file(s) | %d token(s)"
              % (tag, c.label, len(c.files), c.total_tokens()), file=out)

    corpus_line("A:", corpA)
    if corpB is not corpA:
        corpus_line("B:", corpB)
    if base_corpus is not None:
        print("base %s | %d file(s) | %d token(s) (excluded from matches)"
              % (base_corpus.label, len(base_corpus.files),
                 base_corpus.total_tokens()), file=out)
    if corpA.skipped or (corpB is not corpA and corpB.skipped):
        n_sk = len(corpA.skipped) + (len(corpB.skipped)
                                     if corpB is not corpA else 0)
        print("(%d file(s) skipped: not-source/binary/too-large/too-small)"
              % n_sk, file=out)
    print("", file=out)
    print("%d pair(s) compared | %d pair(s) matched"
          % (pairs_compared, len(matches)), file=out)
    if not matches:
        print("no matching file pairs above the current thresholds.",
              file=out)
        return
    print("", file=out)
    print("  #     A%     B%   tokA  tokB  files", file=out)
    print("-" * 72, file=out)
    shown = matches[:opts.top]
    for rank, pm in enumerate(shown, 1):
        print("%3d %s %s %5d %5d  %s -> %s"
              % (rank, _fmt_pct(pm.sim_a), _fmt_pct(pm.sim_b),
                 pm.matched_a, pm.matched_b,
                 pm.file_a.path, pm.file_b.path), file=out)
        for la0, la1, lb0, lb1 in _run_lines(pm)[:3]:
            print("%41sA %d-%d  |  B %d-%d" % ("", la0, la1, lb0, lb1),
                  file=out)
        if len(pm.runs) > 3:
            print("%41s(+%d more region(s))" % ("", len(pm.runs) - 3),
                  file=out)
    if len(matches) > len(shown):
        print("... %d more matched pair(s) (raise --top to show them)"
              % (len(matches) - len(shown)), file=out)
    print("", file=out)
    print("similarity A% = matched tokens / tokens of the A file (same for B).",
          file=out)


def match_to_dict(pm):
    runs = []
    for (a0, a1, b0, b1), (la0, la1, lb0, lb1) in zip(pm.runs, _run_lines(pm)):
        runs.append({
            "a_lines": [la0, la1], "b_lines": [lb0, lb1],
            "a_tokens": [a0, a1], "b_tokens": [b0, b1],
            "tokens": a1 - a0,
        })
    return {
        "file_a": pm.file_a.path,
        "file_b": pm.file_b.path,
        "language_a": pm.file_a.lang,
        "language_b": pm.file_b.lang,
        "sim_a": round(pm.sim_a, 2),
        "sim_b": round(pm.sim_b, 2),
        "matched_tokens_a": pm.matched_a,
        "matched_tokens_b": pm.matched_b,
        "matched_fingerprints": pm.total_grams,
        "regions": runs,
    }


def _corpus_to_dict(c):
    return {
        "label": c.label, "kind": c.kind,
        "files": [{"path": f.path, "language": f.lang, "tokens": f.ntok}
                  for f in c.files],
        "skipped": [{"path": p, "reason": r} for p, r in c.skipped],
    }


def write_json(path, matches, corpA, corpB, base_corpus, opts, pairs_compared):
    data = {
        "tool": TOOL,
        "version": VERSION,
        "params": {
            "k": opts.k, "w": opts.w,
            "exact_idents": bool(opts.exact_idents),
            "merge_gap": opts.merge_gap,
            "min_matched_tokens": opts.min_matched_tokens,
            "threshold_percent": opts.threshold,
            "base": base_corpus.label if base_corpus is not None else None,
        },
        "corpus_a": _corpus_to_dict(corpA),
        "corpus_b": _corpus_to_dict(corpB),
        "summary": {"pairs_compared": pairs_compared,
                    "pairs_matched": len(matches)},
        "matches": [match_to_dict(m) for m in matches],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


# --------------------------------------------------------------------------
# reports: self-contained HTML
# --------------------------------------------------------------------------

_HTML_CSS = """
body { margin: 0; color: #1c2733; background: #f4f6f8;
       font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI",
             Roboto, "Helvetica Neue", Arial, sans-serif; }
header { padding: 18px 26px; background: #223649; color: #fff; }
header h1 { font-size: 19px; margin: 0 0 6px 0; }
header .meta { color: #b9c6d2; font-size: 12.5px; }
main { padding: 20px 26px; }
h2 { font-size: 16px; margin: 26px 0 10px 0; }
table { border-collapse: collapse; width: 100%; background: #fff;
        border: 1px solid #e2e7ec; font-size: 13px; }
th, td { padding: 6px 10px; border-bottom: 1px solid #eef1f4;
         text-align: left; vertical-align: top; }
th { background: #f0f3f6; white-space: nowrap; }
td.num { text-align: right; font-variant-numeric: tabular-nums;
         white-space: nowrap; }
.bar { height: 9px; background: #e5e9ee; border-radius: 5px;
       overflow: hidden; min-width: 90px; }
.bar span { display: block; height: 100%; }
a { color: #0b62c4; text-decoration: none; }
a:hover { text-decoration: underline; }
code { font-family: ui-monospace, Menlo, Consolas, monospace;
       font-size: 12px; }
.pair { margin: 26px 0; background: #fff; border: 1px solid #e2e7ec;
        border-radius: 8px; overflow: hidden; }
.pair > h2 { margin: 0; padding: 10px 16px; background: #f0f3f6;
             font-size: 14.5px; word-break: break-all; }
.pair .stats { padding: 7px 16px; color: #5a6b7b; font-size: 12.5px;
               border-bottom: 1px solid #eef1f4; }
.run { display: flex; flex-wrap: wrap; border-top: 1px solid #eef1f4; }
.pane { flex: 1 1 400px; min-width: 300px; overflow-x: auto; }
.pane .ph { padding: 4px 14px; font-size: 12px;
            font-family: ui-monospace, Menlo, Consolas, monospace;
            color: #5a6b7b; background: #fafbfc;
            border-bottom: 1px solid #eef1f4; word-break: break-all; }
.pane pre { margin: 0; padding: 6px 0 10px 0;
            font: 12.5px/1.5 ui-monospace, Menlo, Consolas, monospace; }
.ln { white-space: pre; display: flex; }
.ln .no { flex: 0 0 4em; text-align: right; padding-right: 10px;
          color: #9aa7b3; user-select: none; }
.ln.ctx .src { color: #8794a1; }
.ln.hit .src { color: #101d29; font-weight: 500; }
.trunc { padding: 5px 14px; color: #98a2ad; font-size: 12px;
         font-style: italic; }
@media print { .pair { break-inside: avoid; } }
"""

_REGION_CTX = 3          # context lines around a run in the HTML report
_REGION_MAX_LINES = 400  # per pane
_HTML_LINE_BUDGET = 100000


def _hbar(pct, hue):
    width = max(0.0, min(100.0, pct))
    return ('<div class="bar"><span style="width:%.1f%%;'
            'background:hsl(%d,55%%,45%%)"></span></div>'
            % (width, hue))


def _render_pane(lines, path, lo, hi, hit_lo, hit_hi, hue, budget):
    """One side of a run: file lines lo..hi (1-based), hit lines highlighted."""
    parts = ['<div class="pane"><div class="ph">%s:%d&ndash;%d</div><pre>'
             % (_hesc(path), lo, hi)]
    shown = 0
    for ln in range(lo, hi + 1):
        if ln > len(lines) or shown >= _REGION_MAX_LINES or budget[0] <= 0:
            parts.append('<div class="trunc">&hellip; (%d more line(s))</div>'
                         % max(0, hi - ln + 1))
            break
        text = lines[ln - 1] if ln <= len(lines) else ""
        if text == "":
            text = "\u00a0"
        hit = hit_lo <= ln <= hit_hi
        style = (' style="background:hsl(%d,80%%,88%%)"' % hue) if hit else ""
        parts.append('<div class="ln %s"%s><span class="no">%d</span>'
                     '<span class="src">%s</span></div>'
                     % ("hit" if hit else "ctx", style, ln, _hesc(text)))
        shown += 1
        budget[0] -= 1
    parts.append("</pre></div>")
    return "".join(parts)


def write_html(path, matches, corpA, corpB, base_corpus, opts, pairs_compared):
    shown = matches[:opts.top]
    budget = [_HTML_LINE_BUDGET]
    parts = []
    parts.append("<!doctype html>\n<html lang=\"en\">\n<head>\n"
                 "<meta charset=\"utf-8\">\n"
                 "<title>mose similarity report</title>\n"
                 "<style>%s</style>\n</head>\n<body>\n" % _HTML_CSS)
    parts.append("<header><h1>mose &mdash; code similarity report</h1>"
                 "<div class=\"meta\">generated %s &middot; winnowing "
                 "k=%d w=%d &middot; %s &middot; %d pair(s) compared, "
                 "%d matched</div></header>\n<main>\n"
                 % (time.strftime("%Y-%m-%d %H:%M:%S"), opts.k, opts.w,
                    "identifiers kept" if opts.exact_idents
                    else "identifiers normalized",
                    pairs_compared, len(matches)))
    parts.append("<table><tr><th>#</th><th>similarity A</th>"
                 "<th>similarity B</th><th>tokA</th><th>tokB</th>"
                 "<th>files</th></tr>\n")
    for i, pm in enumerate(shown, 1):
        hue = (i * 47) % 360
        parts.append(
            '<tr><td class="num">%d</td>'
            '<td><div class="bar"><span style="width:%.1f%%;'
            'background:hsl(%d,55%%,45%%)"></span></div><small>%.1f%%</small></td>'
            '<td><div class="bar"><span style="width:%.1f%%;'
            'background:hsl(%d,55%%,45%%)"></span></div><small>%.1f%%</small></td>'
            '<td class="num">%d</td><td class="num">%d</td>'
            '<td><a href="#pair%d" title="%s">%s</a><br>'
            '<a href="#pair%d" title="%s">%s</a></td></tr>\n'
            % (i, pm.sim_a, hue, pm.sim_a, pm.sim_b, hue, pm.sim_b,
               pm.matched_a, pm.matched_b, i,
               _hesc(pm.file_b.path), _hesc(pm.file_a.path),
               i, _hesc(pm.file_a.path), _hesc(pm.file_b.path)))
    parts.append("</table>\n")

    for i, pm in enumerate(shown, 1):
        hue = (i * 47) % 360
        parts.append('<div class="pair" id="pair%d"><h2>%d. %s &harr; %s'
                     ' &nbsp; <small>%.1f%% / %.1f%%</small></h2>'
                     % (i, i, _hesc(pm.file_a.path), _hesc(pm.file_b.path),
                        pm.sim_a, pm.sim_b))
        parts.append('<div class="stats">%d matched token(s) in A '
                     '(%d/%d), %d in B (%d/%d) &middot; %d region(s) &middot; '
                     'languages: %s / %s</div>'
                     % (pm.matched_a, pm.matched_a, pm.file_a.ntok,
                        pm.matched_b, pm.matched_b, pm.file_b.ntok,
                        len(pm.runs), pm.file_a.lang, pm.file_b.lang))
        for (a0, a1, b0, b1), (la0, la1, lb0, lb1) in zip(pm.runs,
                                                          _run_lines(pm)):
            if budget[0] <= 0:
                parts.append('<div class="trunc">report truncated here to '
                             'keep the file small</div>')
                break
            alo = max(1, la0 - _REGION_CTX)
            ahi = min(len(pm.file_a.lines), la1 + _REGION_CTX)
            blo = max(1, lb0 - _REGION_CTX)
            bhi = min(len(pm.file_b.lines), lb1 + _REGION_CTX)
            parts.append('<div class="run">')
            parts.append(_render_pane(pm.file_a.lines, pm.file_a.path,
                                      alo, ahi, la0, la1, hue, budget))
            parts.append(_render_pane(pm.file_b.lines, pm.file_b.path,
                                      blo, bhi, lb0, lb1, hue, budget))
            parts.append("</div>")
        parts.append("</div>\n")
    if len(matches) > len(shown):
        parts.append('<p class="trunc">%d more matched pair(s) not shown '
                     '(raise --top).</p>\n' % (len(matches) - len(shown)))
    parts.append("</main>\n</body>\n</html>\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(parts))


# --------------------------------------------------------------------------
# command line interface
# --------------------------------------------------------------------------

_EPILOG = """examples:
  %(prog)s studentA/ studentB/                       # two local folders
  %(prog)s studentA/ studentB/ --html report.html --json out.json
  %(prog)s --self submissions/                       # plagiarism inside one set
  %(prog)s a/binary_tree.c b/btree.py                # two single files
  %(prog)s https://github.com/torvalds/linux/lib local-lib/ -k 7
  %(prog)s projA/ projB/ --base starter-code/        # subtract skeleton code

paths may be local files/folders or git URLs:
  https://github.com/OWNER/REPO[/tree/BRANCH[/DIR]][#BRANCH]
  https://gitlab.com/GROUP/REPO[/-/tree/BRANCH[/DIR]]
  https://bitbucket.org/OWNER/REPO[/src/BRANCH[/DIR]]
  or any direct .zip / .tar / .tar.gz / .tar.bz2 / .tar.xz archive URL

languages: """ + ", ".join(sorted(LANGS)) + """

similarity: simA = (tokens of file A found in B) / (tokens of A); a pair is
listed when max(simA, simB) >= --threshold and matched tokens >= --min-matched.
"""


def build_argparser():
    p = argparse.ArgumentParser(
        prog="mose.py",
        description="MOSS-style source-code similarity between two folders, "
                    "two files or two git repositories. "
                    "Single file, no dependencies (Python 3.8+ stdlib only).",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path_a", metavar="PATH_A",
                   help="folder, file or repository URL (with --self: the "
                        "single corpus to compare with itself)")
    p.add_argument("path_b", metavar="PATH_B", nargs="?",
                   help="second folder, file or repository URL")
    p.add_argument("--self", dest="self_mode", action="store_true",
                   help="compare every file pair inside PATH_A "
                        "(plagiarism-inside-one-corpus mode)")
    p.add_argument("-l", "--language", metavar="LANG",
                   help="force this language for every scanned file")
    p.add_argument("-k", type=int, default=DEFAULT_K, metavar="N",
                   help="tokens per k-gram (default %(default)s)")
    p.add_argument("-w", type=int, default=DEFAULT_W, metavar="N",
                   help="winnowing window size; matches of at least k+w-1 "
                        "tokens are guaranteed to be found (default "
                        "%(default)s)")
    p.add_argument("--exact-idents", action="store_true",
                   help="keep identifier/number names (default: normalize "
                        "them, so renaming does not hide copies)")
    p.add_argument("--merge-gap", type=int, default=None, metavar="N",
                   help="max token gap used to chain neighboring matches "
                        "into one region (default: k)")
    p.add_argument("--threshold", type=float, default=0.0, metavar="PCT",
                   help="only keep pairs with max(simA, simB) >= PCT "
                        "percent (default %(default)s)")
    p.add_argument("--min-matched", type=int, default=None, metavar="N",
                   help="min matched tokens per pair (default 2*k)")
    p.add_argument("--top", type=int, default=DEFAULT_TOP, metavar="N",
                   help="pairs shown on the console and in HTML "
                        "(default %(default)s; JSON always contains all)")
    p.add_argument("--base", metavar="PATH",
                   help="base corpus (file/folder/URL of shared starter "
                        "code); its k-grams are excluded from matches")
    p.add_argument("--json", metavar="FILE", help="write a JSON report")
    p.add_argument("--html", metavar="FILE",
                   help="write a self-contained HTML report with side-by-side "
                        "match regions")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                   help="skip files/dirs matching a glob, e.g. 'tests/*' "
                        "(repeatable)")
    p.add_argument("--include", action="append", default=[], metavar="GLOB",
                   help="also scan files matching a glob, e.g. '*.txt' "
                        "(repeatable)")
    p.add_argument("--max-file-kb", type=int, default=MAX_FILE_BYTES >> 10,
                   metavar="KB", help="skip source files larger than this "
                   "(default %(default)s KB)")
    p.add_argument("--max-hash-pairs", type=int, default=HASH_PAIR_CAP,
                   metavar="N", help=argparse.SUPPRESS)
    p.add_argument("-q", "--quiet", action="store_true",
                   help="suppress console output (the table is still printed "
                        "unless --json/--html is given)")
    p.add_argument("--version", action="version",
                   version="%(prog)s " + VERSION)
    return p


def _validate(ap, opts):
    if opts.self_mode and opts.path_b is not None:
        ap.error("--self takes exactly one path")
    if not opts.self_mode and opts.path_b is None:
        ap.error("expected two paths (PATH_A PATH_B), or --self PATH")
    if opts.k < 1 or opts.w < 1:
        ap.error("k and w must be >= 1")
    if opts.threshold < 0 or opts.threshold > 100:
        ap.error("--threshold must be between 0 and 100")
    if opts.language is not None and opts.language not in LANGS:
        ap.error("unknown language %r (choose from: %s)"
                 % (opts.language, ", ".join(sorted(LANGS))))
    opts.merge_gap = opts.k if opts.merge_gap is None else opts.merge_gap
    if opts.min_matched is None:
        opts.min_matched = 2 * opts.k
    opts.min_matched_tokens = opts.min_matched   # internal name
    if opts.top < 1:
        opts.top = 1


def _same_corpus(path_a, path_b):
    """True when both arguments denote the very same corpus."""
    if path_a == path_b:
        return True
    try:
        if os.path.isdir(path_a) and os.path.isdir(path_b):
            return os.path.samefile(path_a, path_b)
    except OSError:
        pass
    return False


def main(argv=None):
    ap = build_argparser()
    opts = ap.parse_args(argv)
    _validate(ap, opts)

    tmpdirs = []
    try:
        table = TokenTable()

        # optional base code (starter/skeleton) whose grams are excluded
        base_corpus = None
        base_hashes = None
        if opts.base:
            base_corpus = load_corpus(opts.base, opts, table, tmpdirs)
            base_hashes = set()
            for f in base_corpus.files:
                base_hashes.update(fingerprint_file(f, opts.k, opts.w))

        corpA = load_corpus(opts.path_a, opts, table, tmpdirs)
        self_mode = opts.self_mode
        if not self_mode and _same_corpus(opts.path_a, opts.path_b):
            self_mode = True
            if not opts.quiet:
                print("note: both paths are the same corpus; "
                      "using self-comparison", file=sys.stderr)
        corpB = corpA if self_mode else \
            load_corpus(opts.path_b, opts, table, tmpdirs)

        for f in corpA.files:
            fingerprint_file(f, opts.k, opts.w)
        if corpB is not corpA:
            for f in corpB.files:
                fingerprint_file(f, opts.k, opts.w)

        matches = compare_corpora(corpA, corpB, corpB is corpA,
                                  base_hashes, opts)
        if corpB is corpA:
            n = len(corpA.files)
            pairs_compared = n * (n - 1) // 2
        else:
            pairs_compared = len(corpA.files) * len(corpB.files)

        if not opts.quiet or not (opts.json or opts.html):
            print_report(matches, corpA, corpB, base_corpus, opts,
                         pairs_compared)
        if opts.json:
            write_json(opts.json, matches, corpA, corpB, base_corpus, opts,
                       pairs_compared)
        if opts.html:
            write_html(opts.html, matches, corpA, corpB, base_corpus, opts,
                       pairs_compared)
        return 0

    except MoseError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    finally:
        for d in tmpdirs:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())





