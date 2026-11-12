#!/data/data/com.termux/files/usr/bin/env python
"""tokei.py -- count code, quickly. A single-file, stdlib-only reimplementation of tokei.

tokei (https://github.com/XAMPPRocky/tokei) counts files, lines, code, comments
and blank lines, grouped by language. This Python 3.12+ port reproduces its
counting model and terminal table:

  * multi-line and nested block comments (Rust, Haskell, Scala, Kotlin, ...)
  * comment markers inside string literals are not miscounted
  * raw/verbatim string prefixes (Python r"", Rust r#"..."#, C# @"...")
  * column-anchored comments (Ruby =begin/=end, Perl POD)
  * lines containing both code and comments count as code (tokei's rule)
  * blank-looking lines inside block comments count as comments, while lines
    inside multi-line strings count as code -- so Python docstrings are code,
    exactly like tokei

It respects .gitignore/.ignore/.tokeignore files (including those in parent
directories up to the repository root), skips hidden files and .git by default,
detects languages by extension, filename or shebang line, and can count in
parallel worker processes.

Usage:
    python tokei.py [options] [PATH]...

Differences from Rust tokei: ~85 languages instead of 200+, no literate-code
child-language accounting (Markdown code blocks), and extra CSV/TOML outputs.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

if sys.version_info < (3, 12):
    sys.exit("tokei.py requires Python 3.12 or newer")

__version__ = "1.0.0"

type LanguageName = str
type SortColumn = Literal["files", "lines", "blanks", "code", "comments"]


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Stats:
    """Line counts for one file or one language (picklable for worker processes)."""

    files: int = 0
    lines: int = 0
    code: int = 0
    comments: int = 0
    blanks: int = 0

    def add(self, other: Stats) -> None:
        self.files += other.files
        self.lines += other.lines
        self.code += other.code
        self.comments += other.comments
        self.blanks += other.blanks

    def as_dict(self) -> dict[str, int]:
        return {
            "blanks": self.blanks,
            "code": self.code,
            "comments": self.comments,
            "files": self.files,
            "lines": self.lines,
        }


@dataclass(frozen=True, slots=True)
class Block:
    """A block (multi-line) comment pair."""

    start: str
    end: str
    at_line_start: bool = False  # start/end tokens are only valid in column 0


@dataclass(frozen=True, slots=True)
class Quote:
    """A string literal delimiter.

    raw      -- no backslash escape processing (raw strings)
    doubled  -- a doubled end delimiter is an escaped one (C# @"..."")
    multiline -- an unterminated string continues on the next line
    esc      -- the escape character (backslash, or backtick for PowerShell)
    """

    start: str
    end: str
    raw: bool = False
    doubled: bool = False
    multiline: bool = False
    esc: str = "\\"


@dataclass(frozen=True, slots=True)
class Syntax:
    """Everything needed to tokenise one language."""

    line_comment: tuple[str, ...] = ()
    block_comment: tuple[Block, ...] = ()
    nested: bool = False
    quotes: tuple[Quote, ...] = ()
    prefix_chars: str = ""  # letters allowed immediately before a quote
    raw_indicator: str = ""  # prefix letters that make the string raw


# --------------------------------------------------------------------------- #
# Counting engine
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _ParseState:
    block: Block | None = None
    depth: int = 0
    quote: Quote | None = None


def _split_lines(text: str) -> list[str]:
    """Split like Rust's str::lines(): on '\n', dropping one trailing empty."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


class Parser:
    """Counts code/comment/blank lines for one language's syntax.

    The scanner never walks character by character in Python: it uses a single
    compiled alternation of all comment/quote start tokens to jump between
    "interesting" positions (C speed), and `str.find`/regex to find string and
    block-comment ends.
    """

    __slots__ = ("syn", "_tok_re", "_tok_map", "_ends")

    def __init__(self, syn: Syntax) -> None:
        self.syn = syn
        toks: dict[str, tuple[str, object]] = {}
        for blk in syn.block_comment:
            toks.setdefault(blk.start, ("block", blk))
        for lc in syn.line_comment:
            toks.setdefault(lc, ("line", None))
        for q in syn.quotes:
            toks.setdefault(q.start, ("quote", q))
        self._tok_map = toks
        if toks:
            # Longest token first so that e.g. """ wins over " and #= over #.
            alternation = "|".join(re.escape(t) for t in sorted(toks, key=len, reverse=True))
            self._tok_re: re.Pattern[str] | None = re.compile(alternation)
        else:
            self._tok_re = None
        self._ends: dict[Quote, re.Pattern[str]] = {}

    # -- public API ---------------------------------------------------------- #

    def parse(self, text: str) -> Stats:
        code = comments = blanks = 0
        if self._tok_re is None:  # plain text: no comment syntax at all
            for ln in _split_lines(text):
                if ln.strip():
                    code += 1
                else:
                    blanks += 1
            return Stats(files=1, lines=code + comments + blanks, code=code, comments=comments, blanks=blanks)

        st = _ParseState()
        for ln in _split_lines(text):
            if ln.endswith("\r"):
                ln = ln[:-1]
            has_code, has_comment = self._scan_line(ln, st)
            if has_code:
                code += 1
            elif has_comment:
                comments += 1
            else:
                blanks += 1
        return Stats(files=1, lines=code + comments + blanks, code=code, comments=comments, blanks=blanks)

    # -- internals ------------------------------------------------------------ #

    def _scan_line(self, ln: str, st: _ParseState) -> tuple[bool, bool]:
        """Classify one line; returns (has_code, has_comment). Mutates st."""
        has_code = has_comment = False
        i = 0

        if st.quote is not None:  # continuing a multi-line string
            has_code = True  # string content (even a blank line) is code
            i = self._string_advance(ln, 0, st.quote, st)
            if st.quote is not None:
                return has_code, has_comment

        if st.block is not None:  # continuing a block comment
            has_comment = True  # even a blank-looking line counts as a comment
            i = self._block_advance(ln, 0, st)
            if st.block is not None:
                return has_code, has_comment

        assert self._tok_re is not None
        while i < len(ln):
            m = self._tok_re.search(ln, i)
            if m is None:
                if ln[i:].strip():
                    has_code = True
                break
            j = m.start()
            if ln[i:j].strip():
                has_code = True
            kind, obj = self._tok_map[m.group()]

            if kind == "line":
                has_comment = True
                break  # rest of the line is a comment

            if kind == "block":
                blk = obj
                assert isinstance(blk, Block)
                if blk.at_line_start and j != 0:
                    has_code = True  # e.g. "x =pod" is code, not a POD marker
                    i = m.end()
                    continue
                st.block = blk
                st.depth = 1
                has_comment = True
                i = self._block_advance(ln, m.end(), st)
                if st.block is not None:
                    break
                continue

            quote = obj
            assert isinstance(quote, Quote)
            if self.syn.prefix_chars:
                quote = self._with_prefix(ln, j, quote)
            has_code = True  # string content is code
            i = self._string_advance(ln, m.end(), quote, st)
            if st.quote is not None:
                break  # multi-line string still open at EOL

        return has_code, has_comment

    def _block_advance(self, ln: str, i: int, st: _ParseState) -> int:
        """Scan block-comment content; returns the resume index (EOL if open)."""
        blk = st.block
        assert blk is not None
        nested = self.syn.nested
        while True:
            if blk.at_line_start:
                # Ruby =end, Perl =cut: the end token only counts in column 0.
                if i == 0 and ln.startswith(blk.end):
                    st.depth = 0
                    st.block = None
                    return len(blk.end)
                return i
            e = ln.find(blk.end, i)
            if not nested:
                if e < 0:
                    return i
                st.depth = 0
                st.block = None
                return e + len(blk.end)
            s = ln.find(blk.start, i)
            if s >= 0 and (e < 0 or s < e):
                st.depth += 1
                i = s + len(blk.start)
                continue
            if e < 0:
                return i
            st.depth -= 1
            if st.depth <= 0:
                st.block = None
                return e + len(blk.end)
            i = e + len(blk.end)

    def _string_advance(self, ln: str, i: int, q: Quote, st: _ParseState) -> int:
        """Scan string content; returns the resume index (EOL if still open)."""
        end_re = self._end_re(q)
        while True:
            m = end_re.search(ln, i)
            if m is None:
                st.quote = q if q.multiline else None
                return len(ln)
            if m.group() == q.end:
                st.quote = None
                return m.end()
            i = m.end()  # skipped an escape pair or a doubled end delimiter

    def _with_prefix(self, ln: str, j: int, q: Quote) -> Quote:
        """Honour raw-string prefixes (Python r"", Rust br"", Dart r'')."""
        syn = self.syn
        k = j
        while k > 0 and ln[k - 1] in syn.prefix_chars:
            k -= 1
        word = ln[k:j]
        if word and any(ch in syn.raw_indicator for ch in word):
            return replace(q, raw=True)
        return q

    def _end_re(self, q: Quote) -> re.Pattern[str]:
        pat = self._ends.get(q)
        if pat is None:
            e = re.escape(q.end)
            if q.doubled:
                pat = re.compile(f"{e}{e}|{e}")
            elif q.raw:
                pat = re.compile(e)
            else:
                pat = re.compile(f"{re.escape(q.esc)}.|{e}")
            self._ends[q] = pat
        return pat


_PARSERS: dict[str, Parser] = {}


def _parser(lang: LanguageName) -> Parser:
    p = _PARSERS.get(lang)
    if p is None:
        p = _PARSERS[lang] = Parser(LANGUAGES[lang])
    return p


# --------------------------------------------------------------------------- #
# Language definitions
# --------------------------------------------------------------------------- #


def _b(start: str, end: str, *, at_line_start: bool = False) -> Block:
    return Block(start, end, at_line_start)


def _q(
    start: str,
    end: str | None = None,
    *,
    raw: bool = False,
    doubled: bool = False,
    multiline: bool = False,
    esc: str = "\\",
) -> Quote:
    return Quote(start, end if end is not None else start, raw, doubled, multiline, esc)


_C_LIKE = Syntax(line_comment=("//",), block_comment=(_b("/*", "*/"),), quotes=(_q('"'), _q("'")))
_PY_LIKE = Syntax(
    line_comment=("#",),
    quotes=(_q('"""', multiline=True), _q("'''", multiline=True), _q('"'), _q("'")),
    prefix_chars="rRbBuUfF",
    raw_indicator="rR",
)
_JS_LIKE = Syntax(
    line_comment=("//",), block_comment=(_b("/*", "*/"),), quotes=(_q("`", multiline=True), _q('"'), _q("'"))
)
_SHELL_LIKE = Syntax(line_comment=("#",), quotes=(_q("'", raw=True), _q('"')))
_LISP_LIKE = Syntax(line_comment=(";",))
_CSS_LIKE = Syntax(line_comment=("//",), block_comment=(_b("/*", "*/"),), quotes=(_q('"'), _q("'")))
_MARKUP = Syntax(block_comment=(_b("<!--", "-->"),))
_GNULIKE = Syntax(line_comment=("//",), block_comment=(_b("/*", "*/"),))
_GNULIKE_Q = Syntax(line_comment=("//",), block_comment=(_b("/*", "*/"),), quotes=(_q('"'), _q("'")))
_PERL_POD = tuple(
    _b(t, "=cut", at_line_start=True)
    for t in ("=pod", "=head1", "=head2", "=head3", "=head4", "=over", "=item", "=back", "=begin", "=for", "=encoding")
)

LANGUAGES: dict[LanguageName, Syntax] = {
    "Ada": Syntax(line_comment=("--",)),
    "Assembly": Syntax(line_comment=(";", "#")),
    "Awk": Syntax(line_comment=("#",), quotes=(_q('"'),)),
    "BASH": _SHELL_LIKE,
    "Bazel": _PY_LIKE,
    "Batch": Syntax(line_comment=("REM", "rem", "Rem", "::")),
    "C": _C_LIKE,
    "C Header": _C_LIKE,
    "C++": _C_LIKE,
    "C#": Syntax(
        line_comment=("//",),
        block_comment=(_b("/*", "*/"),),
        quotes=(_q('@"', '"', raw=True, doubled=True, multiline=True), _q('"'), _q("'")),
    ),
    "CMake": Syntax(line_comment=("#",), block_comment=(_b("#[[", "]]"),)),
    "Clojure": _LISP_LIKE,
    "CoffeeScript": Syntax(
        line_comment=("#",),
        block_comment=(_b("###", "###"),),
        quotes=(_q('"""', multiline=True), _q("'''", multiline=True), _q('"'), _q("'")),
    ),
    "Common Lisp": _LISP_LIKE,
    "Crystal": Syntax(
        line_comment=("#",),
        block_comment=(_b("=begin", "=end", at_line_start=True),),
        quotes=(_q('"', multiline=True), _q("'")),
    ),
    "CSS": Syntax(block_comment=(_b("/*", "*/"),), quotes=(_q('"'), _q("'"))),
    "D": Syntax(
        line_comment=("//",),
        block_comment=(_b("/*", "*/"), _b("/+", "+/")),
        nested=True,
        quotes=(_q("`", raw=True, multiline=True), _q('"', multiline=True), _q("'")),
    ),
    "Dart": Syntax(
        line_comment=("//",),
        block_comment=(_b("/*", "*/"),),
        nested=True,
        quotes=(
            _q("'''", multiline=True, raw=True),
            _q('"""', multiline=True, raw=True),
            _q("'", multiline=True),
            _q('"', multiline=True),
        ),
        prefix_chars="rR",
        raw_indicator="r",
    ),
    "Dockerfile": Syntax(line_comment=("#",)),
    "Elixir": Syntax(
        line_comment=("#",), quotes=(_q('"""', multiline=True), _q("'''", multiline=True), _q('"'), _q("'"))
    ),
    "Elm": Syntax(
        line_comment=("--",), block_comment=(_b("{-", "-}"),), nested=True, quotes=(_q('"', multiline=True),)
    ),
    "Emacs Lisp": _LISP_LIKE,
    "Erlang": Syntax(line_comment=("%",), quotes=(_q('"'),)),
    "F#": Syntax(
        line_comment=("//",),
        block_comment=(_b("(*", "*)"),),
        nested=True,
        quotes=(_q('"""', multiline=True, raw=True), _q('"')),
    ),
    "Fortran": Syntax(line_comment=("!",)),
    "GAS": Syntax(line_comment=("#", "//"), block_comment=(_b("/*", "*/"),)),
    "Go": Syntax(
        line_comment=("//",),
        block_comment=(_b("/*", "*/"),),
        quotes=(_q("`", raw=True, multiline=True), _q('"'), _q("'")),
    ),
    "GraphQL": Syntax(line_comment=("#",), quotes=(_q('"""', multiline=True), _q('"'))),
    "Groovy": Syntax(
        line_comment=("//",),
        block_comment=(_b("/*", "*/"),),
        nested=True,
        quotes=(_q("'''", multiline=True, raw=True), _q('"""', multiline=True, raw=True), _q("'"), _q('"')),
    ),
    "HCL": Syntax(line_comment=("#", "//"), block_comment=(_b("/*", "*/"),), quotes=(_q('"'),)),
    "HTML": _MARKUP,
    "Haskell": Syntax(line_comment=("--",), block_comment=(_b("{-", "-}"),), nested=True, quotes=(_q('"'), _q("'"))),
    "INI": Syntax(line_comment=(";", "#")),
    "Ignore": Syntax(line_comment=("#",)),
    "Java": Syntax(
        line_comment=("//",), block_comment=(_b("/*", "*/"),), quotes=(_q('"""', multiline=True), _q('"'), _q("'"))
    ),
    "JavaScript": _JS_LIKE,
    "JSON": Syntax(),
    "JSONC": Syntax(line_comment=("//",), block_comment=(_b("/*", "*/"),), quotes=(_q('"'),)),
    "JSX": _JS_LIKE,
    "Julia": Syntax(
        line_comment=("#",),
        block_comment=(_b("#=", "=#"),),
        nested=True,
        quotes=(_q('"""', multiline=True), _q('"'), _q("`"), _q("'")),
    ),
    "Kotlin": Syntax(
        line_comment=("//",),
        block_comment=(_b("/*", "*/"),),
        nested=True,
        quotes=(_q('"""', multiline=True, raw=True), _q('"'), _q("'")),
    ),
    "LESS": _CSS_LIKE,
    "Lua": Syntax(
        line_comment=("--",),
        block_comment=(_b("--[[", "]]"),),
        quotes=(_q("[[", "]]", raw=True, multiline=True), _q('"'), _q("'")),
    ),
    "Makefile": Syntax(line_comment=("#",)),
    "Markdown": _MARKUP,
    "Meson": _PY_LIKE,
    "Mojo": _PY_LIKE,
    "Nim": Syntax(
        line_comment=("#",),
        block_comment=(_b("#[", "]#"),),
        nested=True,
        quotes=(_q('"""', multiline=True, raw=True), _q('"')),
    ),
    "Nix": Syntax(
        line_comment=("#",),
        block_comment=(_b("/*", "*/"),),
        quotes=(_q("''", multiline=True, raw=True), _q('"', multiline=True)),
    ),
    "OCaml": Syntax(block_comment=(_b("(*", "*)"),), nested=True, quotes=(_q('"'),)),
    "Objective-C": _C_LIKE,
    "Objective-C++": _C_LIKE,
    "Pascal": Syntax(
        line_comment=("//",),
        block_comment=(
            _b("{", "}"),
            _b("(*", "*)"),
        ),
    ),
    "Perl": Syntax(line_comment=("#",), block_comment=_PERL_POD, quotes=(_q('"'), _q("'"))),
    "PHP": Syntax(line_comment=("//", "#"), block_comment=(_b("/*", "*/"),), quotes=(_q('"'), _q("'"))),
    "Plain Text": Syntax(),
    "PowerShell": Syntax(
        line_comment=("#",), block_comment=(_b("<#", "#>"),), quotes=(_q('"', esc="`"), _q("'", raw=True))
    ),
    "Protobuf": _GNULIKE_Q,
    "Python": _PY_LIKE,
    "R": Syntax(line_comment=("#",), quotes=(_q('"'), _q("'"))),
    "Racket": _LISP_LIKE,
    "ReStructuredText": Syntax(),
    "Ruby": Syntax(
        line_comment=("#",),
        block_comment=(_b("=begin", "=end", at_line_start=True),),
        quotes=(_q('"', multiline=True), _q("'")),
    ),
    "Rust": Syntax(
        line_comment=("//",),
        block_comment=(_b("/*", "*/"),),
        nested=True,
        quotes=(
            _q('r###"', '"###', raw=True),
            _q('r##"', '"##', raw=True),
            _q('r#"', '"#', raw=True),
            _q('"', multiline=True),
            _q("'"),
        ),
        prefix_chars="rb",
        raw_indicator="r",
    ),
    "SQL": Syntax(line_comment=("--",), block_comment=(_b("/*", "*/"),), quotes=(_q("'"), _q('"'))),
    "Sass": _CSS_LIKE,
    "SCSS": _CSS_LIKE,
    "Scheme": _LISP_LIKE,
    "Shell": _SHELL_LIKE,
    "Solidity": _GNULIKE_Q,
    "Svelte": Syntax(
        line_comment=("//",),
        block_comment=(
            _b("<!--", "-->"),
            _b("/*", "*/"),
        ),
        quotes=(_q('"'), _q("'")),
    ),
    "Swift": Syntax(
        line_comment=("//",),
        block_comment=(_b("/*", "*/"),),
        nested=True,
        quotes=(_q('"""', multiline=True), _q('"'), _q("'")),
    ),
    "SystemVerilog": _GNULIKE,
    "Tcl": Syntax(line_comment=("#",), quotes=(_q('"'),)),
    "Terraform": Syntax(line_comment=("#", "//"), block_comment=(_b("/*", "*/"),), quotes=(_q('"'),)),
    "TOML": Syntax(
        line_comment=("#",),
        quotes=(_q('"""', multiline=True), _q("'''", multiline=True, raw=True), _q('"'), _q("'", raw=True)),
    ),
    "TSX": _JS_LIKE,
    "TypeScript": _JS_LIKE,
    "Verilog": _GNULIKE,
    "VHDL": Syntax(line_comment=("--",)),
    "Vim Script": Syntax(line_comment=('"',)),
    "Vue": Syntax(
        line_comment=("//",),
        block_comment=(
            _b("<!--", "-->"),
            _b("/*", "*/"),
        ),
        quotes=(_q('"'), _q("'")),
    ),
    "XML": _MARKUP,
    "YAML": Syntax(line_comment=("#",), quotes=(_q("'", raw=True), _q('"'))),
    "Zig": Syntax(line_comment=("//",)),
}

EXTENSIONS: dict[str, LanguageName] = {}


def _ext(lang: LanguageName, *exts: str) -> None:
    for e in exts:
        EXTENSIONS[e] = lang


_ext("Ada", "adb", "ads", "ada")
_ext("Assembly", "asm")
_ext("Awk", "awk")
_ext("BASH", "bash")
_ext("Bazel", "bzl", "bazel")
_ext("Batch", "bat", "cmd")
_ext("C", "c")
_ext("C Header", "h")
_ext("C++", "cpp", "cc", "cxx", "c++", "hpp", "hh", "hxx", "ipp", "tpp", "inl", "cppm", "ixx")
_ext("C#", "cs", "csx")
_ext("CMake", "cmake")
_ext("Clojure", "clj", "cljs", "cljc", "edn")
_ext("CoffeeScript", "coffee")
_ext("Common Lisp", "lisp", "lsp")
_ext("Crystal", "cr")
_ext("CSS", "css")
_ext("D", "d", "di")
_ext("Dart", "dart")
_ext("Dockerfile", "dockerfile")
_ext("Elixir", "ex", "exs")
_ext("Elm", "elm")
_ext("Emacs Lisp", "el")
_ext("Erlang", "erl", "hrl")
_ext("F#", "fs", "fsi", "fsx", "fsscript")
_ext("Fortran", "f", "for", "f90", "f95", "f03", "f08", "f18")
_ext("GAS", "s")
_ext("Go", "go")
_ext("GraphQL", "graphql", "gql")
_ext("Groovy", "groovy", "gradle")
_ext("Haskell", "hs")
_ext("HCL", "hcl")
_ext("HTML", "html", "htm", "xhtml")
_ext("INI", "ini", "cfg", "conf", "properties")
_ext("Java", "java")
_ext("JavaScript", "js", "mjs", "cjs")
_ext("JSON", "json")
_ext("JSONC", "jsonc", "json5")
_ext("JSX", "jsx")
_ext("Julia", "jl")
_ext("Kotlin", "kt", "kts", "ktm")
_ext("LESS", "less")
_ext("Lua", "lua")
_ext("Makefile", "mk", "make")
_ext("Markdown", "md", "markdown", "mdx")
_ext("Mojo", "mojo")
_ext("Nim", "nim", "nimble")
_ext("Nix", "nix")
_ext("OCaml", "ml", "mli")
_ext("Objective-C", "m")
_ext("Objective-C++", "mm")
_ext("Pascal", "pas", "pp", "dpr", "lpr")
_ext("Perl", "pl", "pm", "pod")
_ext("PHP", "php", "phtml")
_ext("Plain Text", "txt", "text")
_ext("PowerShell", "ps1", "psm1", "psd1")
_ext("Protobuf", "proto")
_ext("Python", "py", "pyi", "pyw", "pyx")
_ext("R", "r")
_ext("Racket", "rkt")
_ext("ReStructuredText", "rst")
_ext("Ruby", "rb", "rake", "gemspec", "ru")
_ext("Rust", "rs")
_ext("SQL", "sql")
_ext("Sass", "sass")
_ext("SCSS", "scss")
_ext("Scheme", "scm", "ss")
_ext("Shell", "sh", "zsh", "ksh", "fish", "csh", "tcsh")
_ext("Solidity", "sol")
_ext("Svelte", "svelte")
_ext("Swift", "swift")
_ext("SystemVerilog", "sv", "svh")
_ext("Tcl", "tcl", "tk")
_ext("Terraform", "tf", "tfvars")
_ext("TOML", "toml")
_ext("TSX", "tsx")
_ext("TypeScript", "ts", "mts", "cts")
_ext("Verilog", "v")
_ext("VHDL", "vhd", "vhdl")
_ext("Vim Script", "vim")
_ext("Vue", "vue")
_ext("XML", "xml", "xsd", "xsl", "xslt", "svg", "plist")
_ext("YAML", "yml", "yaml")
_ext("Zig", "zig")

# Exact filenames (lowercased) -> language; checked before the extension so
# that e.g. CMakeLists.txt is CMake, not Plain Text.
FILENAMES: dict[str, LanguageName] = {
    "cmakelists.txt": "CMake",
    "dockerfile": "Dockerfile",
    "gnumakefile": "Makefile",
    "makefile": "Makefile",
    "meson.build": "Meson",
    "rakefile": "Ruby",
    "gemfile": "Ruby",
    "pkgbuild": "Shell",
    "jenkinsfile": "Groovy",
    "cargo.lock": "TOML",
    "poetry.lock": "TOML",
    "_vimrc": "Vim Script",
    ".bashrc": "BASH",
    ".bash_profile": "BASH",
    ".zshrc": "Shell",
    ".zshenv": "Shell",
    ".zprofile": "Shell",
    ".profile": "Shell",
    ".kshrc": "Shell",
    ".gitignore": "Ignore",
    ".gitattributes": "Ignore",
    ".gitmodules": "Ignore",
    ".clang-format": "YAML",
    ".babelrc": "JSONC",
    ".eslintrc": "JSONC",
    ".editorconfig": "INI",
}

# Filename prefixes (lowercased) -> language (Dockerfile.prod, Jenkinsfile.ci).
FILENAME_PREFIXES: tuple[tuple[str, LanguageName], ...] = (
    ("dockerfile", "Dockerfile"),
    ("jenkinsfile", "Groovy"),
)

# Shebang substrings for extensionless scripts; order matters ("pwsh" and
# "bash" must be tested before "sh", since they contain it).
_SHEBANG_INTERPRETERS: tuple[tuple[str, LanguageName], ...] = (
    ("python", "Python"),
    ("perl", "Perl"),
    ("ruby", "Ruby"),
    ("node", "JavaScript"),
    ("awk", "Awk"),
    ("lua", "Lua"),
    ("php", "PHP"),
    ("rscript", "R"),
    ("pwsh", "PowerShell"),
    ("powershell", "PowerShell"),
    ("fish", "Shell"),
    ("bash", "BASH"),
    ("zsh", "Shell"),
    ("ksh", "Shell"),
    ("sh", "Shell"),
)


# --------------------------------------------------------------------------- #
# File reading and language detection
# --------------------------------------------------------------------------- #


def count_file(path: Path, lang: LanguageName) -> Stats | None:
    """Count one file. Returns None for unreadable or binary files."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None  # binary
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return _parser(lang).parse(text)


def _shebang_language(path: Path) -> LanguageName | None:
    try:
        with path.open("rb") as fh:
            head = fh.read(256)
    except OSError:
        return None
    if not head.startswith(b"#!"):
        return None
    first = head.split(b"\n", 1)[0].decode("latin-1").lower()
    for key, lang in _SHEBANG_INTERPRETERS:
        if key in first:
            return lang
    return None


def detect_language(path: Path) -> LanguageName | None:
    lower = path.name.lower()
    lang = FILENAMES.get(lower)
    if lang is not None:
        return lang
    for prefix, plang in FILENAME_PREFIXES:
        if lower.startswith(prefix):
            return plang
    ext = path.suffix[1:].lower()
    if ext:
        lang = EXTENSIONS.get(ext)
        if lang is not None:
            return lang
    return _shebang_language(path)


def _count_one(task: tuple[str, str]) -> tuple[str, str, Stats | None]:
    """Worker entry point (module-level so it can be pickled)."""
    path_s, lang = task
    return path_s, lang, count_file(Path(path_s), lang)


# --------------------------------------------------------------------------- #
# .gitignore support
# --------------------------------------------------------------------------- #

IGNORE_FILE_NAMES = (".gitignore", ".ignore", ".tokeignore")


@dataclass(frozen=True, slots=True)
class _Rule:
    regex: re.Pattern[str]
    negated: bool
    dir_only: bool


def _translate_gitignore(pat: str) -> str:
    """Translate one gitignore pattern body (no leading '!') to a regex body."""
    out: list[str] = []
    i, n = 0, len(pat)
    while i < n:
        ch = pat[i]
        if ch == "*":
            if pat.startswith("**", i):
                if pat.startswith("**/", i) and (i == 0 or pat[i - 1] == "/"):
                    out.append("(?:[^/]+/)*")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            j = pat.find("]", i + 1)
            if j < 0:
                out.append(re.escape(ch))
                i += 1
            else:
                cls = pat[i + 1 : j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                out.append("[" + cls + "]")
                i = j + 1
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


def _parse_rules(lines: Iterable[str]) -> tuple[_Rule, ...]:
    rules: list[_Rule] = []
    for raw in lines:
        line = raw.rstrip("\r").rstrip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        if not line:
            continue
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = line.startswith("/") or "/" in line
        line = line.lstrip("/")
        if not line:
            continue
        body = _translate_gitignore(line)
        rx = body if anchored else "(?:.*/)?" + body
        rules.append(_Rule(re.compile(rx), negated, dir_only))
    return tuple(rules)


class IgnoreFile:
    """One .gitignore-style file, anchored at the directory containing it."""

    __slots__ = ("base", "rules")

    def __init__(self, base: Path, text: str) -> None:
        self.base = base
        self.rules = _parse_rules(text.splitlines())

    @classmethod
    def load(cls, directory: Path, name: str) -> IgnoreFile | None:
        f = directory / name
        if not f.is_file():
            return None
        try:
            return cls(directory, f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None

    def match(self, rel: str, is_dir: bool) -> bool | None:
        """True=ignored, False=kept, None=no opinion. Last matching rule wins."""
        result: bool | None = None
        for r in self.rules:
            if r.dir_only and not is_dir:
                continue
            if r.regex.fullmatch(rel):
                result = not r.negated
        return result


class ExcludePatterns:
    """-e/--exclude patterns, applied like gitignore rules (tokei semantics)."""

    __slots__ = ("rules",)

    def __init__(self, patterns: Sequence[str]) -> None:
        self.rules = _parse_rules(list(patterns))

    def excluded(self, p: Path, is_dir: bool, roots: Sequence[Path]) -> bool:
        candidates = [p.name, p.as_posix()]
        for root in roots:
            try:
                candidates.append(p.relative_to(root).as_posix())
            except ValueError:
                pass
        try:
            candidates.append(p.relative_to(Path.cwd()).as_posix())
        except ValueError:
            pass
        for rule in self.rules:
            if rule.dir_only and not is_dir:
                continue
            for cand in candidates:
                if rule.regex.fullmatch(cand):
                    return True
        return False


def _ancestor_ignores(root: Path, no_ignore: bool) -> list[IgnoreFile]:
    """Ignore files above the scan root, up to and including the repo root."""
    if no_ignore:
        return []
    found: list[IgnoreFile] = []
    d = root.parent
    hops = 0
    while True:
        for name in IGNORE_FILE_NAMES:
            ig = IgnoreFile.load(d, name)
            if ig is not None:
                found.append(ig)
        if (d / ".git").exists() or d == d.parent or hops >= 64:
            break
        d = d.parent
        hops += 1
    found.reverse()  # shallowest first; deeper files take precedence
    return found


# --------------------------------------------------------------------------- #
# Directory traversal
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Options:
    paths: list[Path]
    files: bool
    types: set[str]
    exclude: list[str]
    hidden: bool
    no_ignore: bool
    sort: SortColumn | str | None
    output: str
    columns: int | None
    jobs: int
    verbose: bool


def _walk_dir(root: Path, opts: Options, exclude: ExcludePatterns | None) -> Iterator[Path]:
    """Yield countable files under root, honouring ignore rules (Path.walk, 3.12)."""
    base_active = _ancestor_ignores(root, opts.no_ignore)
    active_by_dir: dict[Path, list[IgnoreFile]] = {}

    for dirpath, dirnames, filenames in root.walk(top_down=True):
        parent_active = active_by_dir.get(dirpath.parent)
        active = list(parent_active if parent_active is not None else base_active)
        if not opts.no_ignore:
            for name in IGNORE_FILE_NAMES:
                ig = IgnoreFile.load(dirpath, name)
                if ig is not None:
                    active.append(ig)
        active_by_dir[dirpath] = active

        def keep(name: str, is_dir: bool, *, _dir: Path = dirpath, _active: list[IgnoreFile] = active) -> bool:
            if name == ".git":
                return False
            if not opts.hidden and name.startswith("."):
                return False
            p = _dir / name
            if exclude is not None and exclude.excluded(p, is_dir, (root,)):
                return False
            for ig in reversed(_active):  # deepest ignore file decides
                try:
                    rel = p.relative_to(ig.base).as_posix()
                except ValueError:
                    continue
                verdict = ig.match(rel, is_dir)
                if verdict is not None:
                    return not verdict
            return True

        dirnames[:] = [d for d in dirnames if keep(d, True)]
        for fname in filenames:
            if keep(fname, False):
                yield dirpath / fname


def iter_input_files(opts: Options, exclude: ExcludePatterns | None) -> Iterator[Path]:
    for p in opts.paths:
        if p.is_dir():
            yield from _walk_dir(p, opts, exclude)
        else:
            yield p  # explicitly named files bypass ignore/hidden/exclude rules


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _run_jobs(tasks: list[tuple[str, str]], opts: Options) -> list[tuple[str, str, Stats | None]]:
    if opts.jobs > 1 and len(tasks) >= 32:
        try:
            with ProcessPoolExecutor(max_workers=opts.jobs) as pool:
                return list(pool.map(_count_one, tasks, chunksize=16))
        except OSError:
            pass  # e.g. no /dev/shm in restricted environments: go sequential
    return [_count_one(t) for t in tasks]


def gather(opts: Options) -> tuple[dict[str, Stats], dict[str, list[tuple[str, Stats]]], list[str]]:
    exclude = ExcludePatterns(opts.exclude) if opts.exclude else None
    tasks: list[tuple[str, str]] = []
    unknown: list[str] = []
    for p in iter_input_files(opts, exclude):
        lang = detect_language(p)
        if lang is None:
            unknown.append(str(p))
            continue
        if opts.types and lang not in opts.types:
            continue
        tasks.append((str(p), lang))

    results: dict[str, Stats] = {}
    reports: dict[str, list[tuple[str, Stats]]] = {}
    for path_s, lang, st in _run_jobs(tasks, opts):
        if st is None:
            continue
        results.setdefault(lang, Stats()).add(st)
        reports.setdefault(lang, []).append((path_s, st))
    return results, reports, unknown


# --------------------------------------------------------------------------- #
# Output rendering (terminal geometry matches tokei v12.1.2 exactly)
# --------------------------------------------------------------------------- #

_FALLBACK_COLUMNS = 79  # tokei's FALLBACK_ROW_LEN


def _terminal_columns(opts: Options) -> int:
    if opts.columns:
        return max(opts.columns, 68)
    if sys.stdout.isatty():
        try:
            return max(shutil.get_terminal_size((_FALLBACK_COLUMNS, 24)).columns, 68)
        except OSError:
            return _FALLBACK_COLUMNS
    return _FALLBACK_COLUMNS


def _sorted_items(results: dict[str, Stats], opts: Options) -> list[tuple[str, Stats]]:
    items = sorted(results.items())  # tokei's default: alphabetical
    if opts.sort:
        items.sort(key=lambda kv: -getattr(kv[1], opts.sort))
    return items


def _total(results: dict[str, Stats]) -> Stats:
    total = Stats()
    for st in results.values():
        total.add(st)
    return total


def render_terminal(results: dict[str, Stats], reports: dict[str, list[tuple[str, Stats]]], opts: Options) -> str:
    columns = _terminal_columns(opts)
    hdr_lang = columns - 67
    row_lang = columns - 61
    path_w = columns - 54
    bar = "=" * columns
    dash = "-" * columns
    out: list[str] = [
        bar,
        f" {'Language':<{hdr_lang}} {'Files':>12} {'Lines':>12} {'Code':>12} {'Comments':>12} {'Blanks':>12}",
        bar,
    ]

    def row(name: str, st: Stats) -> str:
        if len(name) > row_lang:  # truncate long names, tokei-style
            name = name[: row_lang - 1] + "|"
        return f" {name:<{row_lang}} {st.files:>6} {st.lines:>12} {st.code:>12} {st.comments:>12} {st.blanks:>12}"

    for idx, (name, st) in enumerate(_sorted_items(results, opts)):
        if idx and opts.files:
            out.append(dash)
        out.append(row(name, st))
        if opts.files:
            out.append(dash)
            for p, fst in sorted(reports.get(name, ())):
                disp = p if len(p) <= path_w else "|" + p[-(path_w - 1) :]
                out.append(f" {disp:<{path_w}} {fst.lines:>12} {fst.code:>12} {fst.comments:>12} {fst.blanks:>12}")

    out += [bar, row("Total", _total(results)), bar]
    return "\n".join(out)


def render_json(results: dict[str, Stats], reports: dict[str, list[tuple[str, Stats]]], opts: Options) -> str:
    obj: dict[str, object] = {}
    for name in sorted(results):
        entry: dict[str, object] = results[name].as_dict()
        if opts.files and name in reports:
            entry["reports"] = [{"location": p, "stats": fst.as_dict()} for p, fst in sorted(reports[name])]
        obj[name] = entry
    return json.dumps(obj, indent=2, ensure_ascii=False)


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(s: str) -> str:
    """A bare key when possible, else a double-quoted (YAML/TOML safe) key."""
    return s if _BARE_KEY.match(s) else json.dumps(s, ensure_ascii=False)


def render_yaml(results: dict[str, Stats], reports: dict[str, list[tuple[str, Stats]]], opts: Options) -> str:
    lines: list[str] = []
    for name in sorted(results):
        lines.append(f"{_key(name)}:")
        for k, v in results[name].as_dict().items():
            lines.append(f"  {k}: {v}")
        if opts.files and name in reports:
            lines.append("  reports:")
            for p, fst in sorted(reports[name]):
                lines.append(f"    - location: {_key(p)}")
                for k, v in fst.as_dict().items():
                    lines.append(f"      {k}: {v}")
    return "\n".join(lines)


def render_toml(results: dict[str, Stats], reports: dict[str, list[tuple[str, Stats]]], opts: Options) -> str:
    blocks = []
    for name in sorted(results):
        block = [f"[{_key(name)}]"]
        for k, v in results[name].as_dict().items():
            block.append(f"{k} = {v}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def render_csv(results: dict[str, Stats], reports: dict[str, list[tuple[str, Stats]]], opts: Options) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["language", "files", "lines", "code", "comments", "blanks"])
    for name, st in _sorted_items(results, opts):
        w.writerow([name, st.files, st.lines, st.code, st.comments, st.blanks])
    t = _total(results)
    w.writerow(["Total", t.files, t.lines, t.code, t.comments, t.blanks])
    return buf.getvalue().rstrip("\n")


# --------------------------------------------------------------------------- #
# Command line interface
# --------------------------------------------------------------------------- #

_CANONICAL = {n.lower(): n for n in LANGUAGES}

_EXAMPLES = """\
examples:
  %(prog)s                          count the current directory
  %(prog)s src tests -e '*.snapshot'
  %(prog)s -t Python,Rust -s code
  %(prog)s -o json --files > stats.json
"""


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="tokei",
        description="tokei.py -- a single-file Python reimplementation of tokei "
        "(https://github.com/XAMPPRocky/tokei). Counts files, lines, "
        "code, comments and blanks, grouped by language.",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "input", nargs="*", type=Path, metavar="PATH", help="file or directory to count (default: current directory)"
    )
    ap.add_argument("-f", "--files", action="store_true", help="print statistics for individual files")
    ap.add_argument(
        "-t",
        "--type",
        action="append",
        default=[],
        metavar="LANG",
        help="filter output by language type, comma separated (e.g. -t=Rust,Markdown)",
    )
    ap.add_argument(
        "-e",
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="ignore all files and directories matching PATTERN (gitignore semantics)",
    )
    ap.add_argument("--hidden", action="store_true", help="count hidden files")
    ap.add_argument(
        "--no-ignore", action="store_true", help="don't respect ignore files (.gitignore, .ignore, .tokeignore)"
    )
    ap.add_argument(
        "-s",
        "--sort",
        choices=("files", "lines", "blanks", "code", "comments"),
        default=None,
        help="sort languages by column (default: alphabetical)",
    )
    ap.add_argument(
        "-o",
        "--output",
        choices=("terminal", "json", "yaml", "toml", "csv"),
        default="terminal",
        help="output format (default: terminal)",
    )
    ap.add_argument(
        "-c", "--columns", type=int, default=None, metavar="N", help="strict column width for terminal output"
    )
    ap.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=None,
        metavar="N",
        help="worker processes for counting (default: auto on big inputs)",
    )
    ap.add_argument("-l", "--languages", action="store_true", help="print the supported languages and their extensions")
    ap.add_argument("-v", "--verbose", action="store_true", help="list files whose type could not be recognized")
    ap.add_argument("--version", action="version", version=f"tokei.py {__version__} (single-file Python tokei)")
    return ap


def _canonical_types(raw: list[str], ap: argparse.ArgumentParser) -> set[str]:
    out: set[str] = set()
    for chunk in raw:
        for t in chunk.split(","):
            t = t.strip()
            if not t:
                continue
            c = _CANONICAL.get(t.lower())
            if c is None:
                ap.error(f"unknown language type {t!r}; run with --languages for the list")
            out.add(c)
    return out


def _print_languages() -> None:
    by_lang: dict[str, set[str]] = {}
    for ext, name in EXTENSIONS.items():
        by_lang.setdefault(name, set()).add("." + ext)
    for name in sorted(LANGUAGES):
        parts = []
        exts = ", ".join(sorted(by_lang.get(name, ())))
        if exts:
            parts.append(exts)
        extras = [f for f, n in FILENAMES.items() if n == name]
        if extras:
            parts.append("also: " + ", ".join(sorted(extras)))
        print(name + (": " + "  ".join(parts) if parts else ""))


def main(argv: list[str] | None = None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    if args.languages:
        _print_languages()
        return 0

    paths = args.input or [Path(".")]
    for p in paths:
        if not p.exists():
            print(f"error: path does not exist: {p}", file=sys.stderr)
            return 1

    jobs = args.jobs if args.jobs is not None else min(32, os.cpu_count() or 1)
    opts = Options(
        paths=paths,
        files=args.files,
        types=_canonical_types(args.type, ap),
        exclude=list(args.exclude),
        hidden=args.hidden,
        no_ignore=args.no_ignore,
        sort=args.sort,
        output=args.output,
        columns=args.columns,
        jobs=max(jobs, 1),
        verbose=args.verbose,
    )

    results, reports, unknown = gather(opts)

    for u in unknown:
        if args.verbose:
            print(f"warn: unknown file type: {u}", file=sys.stderr)
    if unknown and not args.verbose:
        print(
            f"warn: {len(unknown)} file(s) with unrecognized types were skipped (use --verbose to list them)",
            file=sys.stderr,
        )

    match opts.output:
        case "json":
            out = render_json(results, reports, opts)
        case "yaml":
            out = render_yaml(results, reports, opts)
        case "toml":
            out = render_toml(results, reports, opts)
        case "csv":
            out = render_csv(results, reports, opts)
        case _:
            out = render_terminal(results, reports, opts)

    print(out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # e.g. piped into head(1)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
