#!/data/data/com.termux/files/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import multiprocessing as mp
import os
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal, NamedTuple, Protocol, cast

from dh import SKIP_DIRS, atomic_write
from loguru import logger
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text
from tree_sitter import Language, Node, Parser, Tree

Status = Literal["changed", "unchanged", "skipped", "unsupported", "invalid", "error"]
VetoFn = Callable[[Node, bytes], bool]


class _DigestLike(Protocol):
    def update(self, data: bytes, /) -> None: ...


class _TsPackModule(Protocol):
    def languages(self) -> tuple[str, ...]: ...
    def get_parser(self, name: str) -> Parser: ...


DEFAULT_WORKERS: Final[int] = 4
DEFAULT_MAX_SIZE: Final[int] = 5 * 1024 * 1024
LIVE_REFRESH_HZ: Final[int] = 12
LIVE_MIN_INTERVAL: Final[float] = 1.0 / LIVE_REFRESH_HZ
_DEFAULT_COMMENT_TYPES: Final[frozenset[str]] = frozenset({
    "comment",
    "line_comment",
    "block_comment",
    "comment_line",
    "comment_block",
    "doc_comment",
    "documentation_comment",
    "multiline_comment",
    "js_comment",
})
_XML_COMMENT_TYPES: Final[frozenset[str]] = _DEFAULT_COMMENT_TYPES | frozenset({"Comment"})
_LATEX_COMMENT_TYPES: Final[frozenset[str]] = _DEFAULT_COMMENT_TYPES | frozenset({"comment_environment"})
_ALL_COMMENT_TYPES: Final[frozenset[str]] = _XML_COMMENT_TYPES | _LATEX_COMMENT_TYPES
_TEXT_NODE_TYPES: Final[frozenset[str]] = frozenset({"text", "CharData"})
_DOC_PREFIXES: Final[tuple[bytes, bytes, bytes]] = (b"/**", b"///", b"//!")
_PROTECTED_NODE_TYPES: Final[frozenset[str]] = frozenset({
    "template_string",
    "heredoc_body",
    "here_document",
    "heredoc",
    "chomped_heredoc",
    "raw_string_literal",
    "raw_string",
    "string_literal",
    "interpreted_string_literal",
    "multiline_string_literal",
    "triple_quoted_string",
    "concatenated_string",
    "verbatim_string_literal",
    "verbatim_string",
    "string",
    "block_string",
    "long_string",
    "text_block",
    "block_scalar",
    "preproc_arg",
})
_HTML_PREFORMATTED_TAGS: Final[frozenset[bytes]] = frozenset({b"pre", b"textarea"})
_PROSE_LANGUAGES: Final[frozenset[str]] = frozenset({"markdown", "rst", "org"})
_SCRIPT_TYPE_LANGUAGES: Final[dict[str, str]] = {
    "": "javascript",
    "module": "javascript",
    "text/javascript": "javascript",
    "application/javascript": "javascript",
    "text/ecmascript": "javascript",
    "application/ecmascript": "javascript",
    "application/x-javascript": "javascript",
    "text/x-javascript": "javascript",
    "text/jscript": "javascript",
    "text/livescript": "javascript",
    "text/babel": "javascript",
    "text/jsx": "javascript",
    "text/typescript": "typescript",
    "application/typescript": "typescript",
    "text/tsx": "tsx",
    "text/ts": "typescript",
}
_STYLE_TYPE_LANGUAGES: Final[dict[str, str]] = {
    "": "css",
    "text/css": "css",
}
_COMMON_VITAL: Final[str] = (
    r"copyright|\(c\)\s*\d|\u00a9|spdx-|spdx\b|licen[cs]e|all rights reserved"
    r"|permission is hereby granted|licensed (?:under|to)|@license|@preserve"
    r"|@generated|code generated|auto-?generated|do not (?:remove|delete|edit|modify)"
    r"|noqa|nosec|nolint|nosonar|\bvim?:|-\*-|coding[:=]\s*[-\w.]+"
    r"|prettier-ignore|fmt:\s*(?:on|off|skip)|pragma|[#/]\s*(?:end)?region\b"
    r"|sourceMappingURL|sourceURL"
)
_C_VITAL: Final[str] = r"\A/\*!|clang-format|clang-tidy|coverity|fallthrough|-Wno-|cppcheck-suppress"
_JS_VITAL: Final[str] = (
    r"\A/\*!|\A///\s*<|@cc_on|eslint|@ts-|tslint|jshint|biome-ignore|oxlint"
    r"|istanbul|c8 ignore|v8 ignore|webpack(?:Chunk|Mode|Prefetch|Preload|Ignore|Include|Exclude|Exports)"
    r"|@jsx|@flow|@vite-ignore|[@#]__(?:PURE|NO_SIDE_EFFECTS)__"
)
_SHELL_VITAL: Final[str] = r"\A#!|shellcheck|shfmt"
_JVM_VITAL: Final[str] = (
    r"noinspection|checkstyle|spotbugs|nopmd|@formatter:|formatter:\s*(?:on|off)"
    r"|ktlint|detekt|scalafmt|wartremover"
)
_CSS_VITAL: Final[str] = r"\A/\*!|@charset|stylelint|postcss|autoprefixer|rtl:"
_HTML_VITAL: Final[str] = (
    r"\A<!--\s*\[if|\[endif\]|\A<!--!|svelte-ignore|@vite-ignore|eslint-"
    r"|\A<!--\s*#(?:include|exec|echo|config|set|if|else|endif)"
)


def _is_cgo_preamble(node: Node, src: bytes) -> bool:
    sibling: Node | None = node.next_sibling
    while sibling is not None and sibling.type in _ALL_COMMENT_TYPES:
        sibling = sibling.next_sibling
    return (
        sibling is not None
        and sibling.type == "import_declaration"
        and b'"C"' in src[sibling.start_byte : sibling.end_byte]
    )


@dataclass(frozen=True, slots=True)
class LangSpec:
    factories: tuple[tuple[str, str], ...]
    suffixes: frozenset[str]
    vital_re: re.Pattern[str]
    comment_types: frozenset[str] = _DEFAULT_COMMENT_TYPES
    filenames: frozenset[str] = frozenset()
    veto: VetoFn | None = None


def _spec(
    factories: tuple[tuple[str, str], ...],
    suffixes: str,
    extra_vital: str = "",
    *,
    comment_types: frozenset[str] = _DEFAULT_COMMENT_TYPES,
    filenames: str = "",
    veto: VetoFn | None = None,
) -> LangSpec:
    pattern: str = "|".join(part for part in (_COMMON_VITAL, extra_vital) if part)
    return LangSpec(
        factories=factories,
        suffixes=frozenset(suffixes.split()),
        vital_re=re.compile(pattern, re.IGNORECASE | re.DOTALL),
        comment_types=comment_types,
        filenames=frozenset(filenames.split()),
        veto=veto,
    )


LANGUAGES: Final[dict[str, LangSpec]] = {
    "bash": _spec((("tree_sitter_bash", "language"),), ".sh .bash .ksh", _SHELL_VITAL),
    "zsh": _spec(
        (("tree_sitter_zsh", "language"), ("tree_sitter_bash", "language")),
        ".zsh",
        _SHELL_VITAL,
    ),
    "fish": _spec((("tree_sitter_fish", "language"),), ".fish", _SHELL_VITAL),
    "powershell": _spec(
        (("tree_sitter_powershell", "language"),),
        ".ps1 .psm1 .psd1",
        r"\A#!|requires\s+-|psscriptanalyzer|<#\s*\.",
    ),
    "c": _spec((("tree_sitter_c", "language"),), ".c .h", _C_VITAL),
    "cpp": _spec(
        (("tree_sitter_cpp", "language"),),
        ".cc .cpp .cxx .c++ .hh .hpp .hxx .h++",
        _C_VITAL,
    ),
    "c_sharp": _spec(
        (("tree_sitter_c_sharp", "language"),),
        ".cs",
        r"\A/\*!|resharper|<auto-generated",
    ),
    "css": _spec((("tree_sitter_css", "language"),), ".css", _CSS_VITAL),
    "scss": _spec((("tree_sitter_scss", "language"),), ".scss", _CSS_VITAL),
    "sass": _spec((("tree_sitter_sass", "language"),), ".sass", _CSS_VITAL),
    "less": _spec((("tree_sitter_less", "language"),), ".less", _CSS_VITAL),
    "go": _spec(
        (("tree_sitter_go", "language"),),
        ".go",
        r"\A//go:|\A//\s*\+build|\A//line |\A//export |#cgo|lint:ignore|\A//\s*Code generated",
        veto=_is_cgo_preamble,
    ),
    "html": _spec((("tree_sitter_html", "language"),), ".html .htm .xhtml", _HTML_VITAL),
    "vue": _spec((("tree_sitter_vue", "language"),), ".vue", _HTML_VITAL),
    "svelte": _spec((("tree_sitter_svelte", "language"),), ".svelte", _HTML_VITAL),
    "java": _spec((("tree_sitter_java", "language"),), ".java", _JVM_VITAL),
    "kotlin": _spec((("tree_sitter_kotlin", "language"),), ".kt .kts", _JVM_VITAL),
    "scala": _spec((("tree_sitter_scala", "language"),), ".scala .sc", _JVM_VITAL),
    "javascript": _spec((("tree_sitter_javascript", "language"),), ".js .mjs .cjs .jsx", _JS_VITAL),
    "typescript": _spec(
        (
            ("tree_sitter_typescript", "language_typescript"),
            ("tree_sitter_typescript", "language"),
        ),
        ".ts .mts .cts .d.ts",
        _JS_VITAL,
    ),
    "tsx": _spec(
        (("tree_sitter_typescript", "language_tsx"), ("tree_sitter_tsx", "language")),
        ".tsx",
        _JS_VITAL,
    ),
    "json": _spec((("tree_sitter_json", "language"),), ".json .jsonc"),
    "latex": _spec(
        (("tree_sitter_latex", "language"),),
        ".tex .sty .cls",
        r"\A%!|\A%\s*!(?:TeX|BIB|Mode)",
        comment_types=_LATEX_COMMENT_TYPES,
    ),
    "lua": _spec(
        (("tree_sitter_lua", "language"),),
        ".lua",
        r"\A#!|\A---\s*@|luacheck|stylua|selene|luacov|@diagnostic|@class|@param|@return|@type",
    ),
    "perl": _spec(
        (("tree_sitter_perl", "language"),),
        ".pl .pm",
        r"\A#!|##\s*(?:no|use)\s+critic|perlcritic",
    ),
    "php": _spec(
        (("tree_sitter_php", "language_php"), ("tree_sitter_php", "language")),
        ".php .phtml",
        r"\A#!|phpcs|phpstan|psalm|php-cs-fixer|noinspection|@var|@codeCoverageIgnore|@psalm-|@phpstan-",
    ),
    "query": _spec((("tree_sitter_query", "language"),), ".scm"),
    "rst": _spec((("tree_sitter_rst", "language"),), ".rst"),
    "markdown": _spec((("tree_sitter_markdown", "language"),), ".md .markdown", r"markdownlint|vale"),
    "org": _spec((("tree_sitter_org", "language"),), ".org", r"\A#\+"),
    "ruby": _spec(
        (("tree_sitter_ruby", "language"),),
        ".rb .rake .gemspec",
        r"\A#!|frozen_string_literal|rubocop|:nodoc:|:nocov:|typed:|sorbet|steep:"
        r"|shareable_constant_value|warn_indent|encoding:",
        filenames="gemfile rakefile",
    ),
    "rust": _spec((("tree_sitter_rust", "language"),), ".rs", r"rustfmt|clippy|SAFETY:|cbindgen"),
    "swift": _spec((("tree_sitter_swift", "language"),), ".swift", r"swiftlint|swiftformat|\bMARK:"),
    "dart": _spec(
        (("tree_sitter_dart", "language"),),
        ".dart",
        r"ignore:|ignore_for_file:|dartfmt",
    ),
    "r": _spec((("tree_sitter_r", "language"),), ".r", r"\A#!|\A#'"),
    "julia": _spec((("tree_sitter_julia", "language"),), ".jl", r"\A#!|format:\s*(?:on|off)"),
    "elixir": _spec((("tree_sitter_elixir", "language"),), ".ex .exs", r"\A#!|credo:"),
    "erlang": _spec((("tree_sitter_erlang", "language"),), ".erl .hrl", r"dialyzer|elvis|eqwalizer"),
    "haskell": _spec((("tree_sitter_haskell", "language"),), ".hs", r"\A\{-#|hlint"),
    "elm": _spec((("tree_sitter_elm", "language"),), ".elm", r"elm-review"),
    "clojure": _spec(
        (("tree_sitter_clojure", "language"),),
        ".clj .cljs .cljc",
        r"clj-kondo|kibit|eastwood",
    ),
    "fsharp": _spec(
        (("tree_sitter_fsharp", "language_fsharp"), ("tree_sitter_fsharp", "language")),
        ".fs .fsx .fsi",
        r"#nowarn|fsharplint|fantomas",
    ),
    "nim": _spec((("tree_sitter_nim", "language"),), ".nim", r"nimfmt"),
    "zig": _spec((("tree_sitter_zig", "language"),), ".zig", r"zig fmt:"),
    "v": _spec((("tree_sitter_v", "language"),), ".v", r"vfmt"),
    "solidity": _spec(
        (("tree_sitter_solidity", "language"),),
        ".sol",
        r"solhint|slither-disable|@custom:",
    ),
    "sql": _spec((("tree_sitter_sql", "language"),), ".sql", r"\A/\*\+|\A/\*!"),
    "graphql": _spec((("tree_sitter_graphql", "language"),), ".graphql .gql"),
    "toml": _spec((("tree_sitter_toml", "language"),), ".toml", r"taplo|\A#:schema|:schema"),
    "yaml": _spec(
        (("tree_sitter_yaml", "language"),),
        ".yaml .yml",
        r"yamllint|yaml-language-server:|checkov:|hadolint",
    ),
    "ini": _spec((("tree_sitter_ini", "language"),), ".ini .cfg .conf"),
    "vim": _spec((("tree_sitter_vim", "language"),), ".vim", r"vint:"),
    "xml": _spec(
        (("tree_sitter_xml", "language_xml"), ("tree_sitter_xml", "language")),
        ".xml .svg .xsl .xslt .xsd",
        r"\A<!--!|\A<!--\s*\[if|\[endif\]|noinspection|xmllint",
        comment_types=_XML_COMMENT_TYPES,
    ),
    "proto": _spec((("tree_sitter_proto", "language"),), ".proto", r"buf:lint|clang-format"),
    "cmake": _spec(
        (("tree_sitter_cmake", "language"),),
        ".cmake",
        r"cmake-format|cmakelint",
        filenames="cmakelists.txt",
    ),
    "dockerfile": _spec(
        (("tree_sitter_dockerfile", "language"),),
        ".dockerfile",
        r"\A#\s*(?:syntax|escape|check)\s*=|hadolint",
        filenames="dockerfile",
    ),
    "make": _spec(
        (("tree_sitter_make", "language"),),
        ".make .mk",
        _SHELL_VITAL,
        filenames="makefile gnumakefile",
    ),
    "nix": _spec((("tree_sitter_nix", "language"),), ".nix", r"nixfmt|statix"),
    "pug": _spec((("tree_sitter_pug", "language"),), ".pug"),
    "wasm": _spec((("tree_sitter_wasm", "language"),), ".wasm .wat"),
    "wgsl": _spec((("tree_sitter_wgsl", "language"),), ".wgsl"),
}
_INTERPRETERS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"zsh", re.IGNORECASE), "zsh"),
    (re.compile(r"(?:ba|da|k|mk|pdk|a)?sh", re.IGNORECASE), "bash"),
    (re.compile(r"fish", re.IGNORECASE), "fish"),
    (re.compile(r"pwsh|powershell", re.IGNORECASE), "powershell"),
    (re.compile(r"node(?:js)?|deno|bun", re.IGNORECASE), "javascript"),
    (re.compile(r"perl[\d.]*", re.IGNORECASE), "perl"),
    (re.compile(r"php[\d.]*", re.IGNORECASE), "php"),
    (re.compile(r"ruby[\d.]*", re.IGNORECASE), "ruby"),
    (re.compile(r"lua[\d.]*", re.IGNORECASE), "lua"),
    (re.compile(r"rscript", re.IGNORECASE), "r"),
    (re.compile(r"julia", re.IGNORECASE), "julia"),
    (re.compile(r"elixir", re.IGNORECASE), "elixir"),
)


def _load_tspack() -> tuple[_TsPackModule | None, frozenset[str]]:
    try:
        mod: object = importlib.import_module("tspack")
    except (ImportError, OSError):
        return None, frozenset()
    langs_fn: object = getattr(mod, "languages", None)
    parser_fn: object = getattr(mod, "get_parser", None)
    if not callable(langs_fn) or not callable(parser_fn):
        return None, frozenset()
    try:
        raw_langs: object = langs_fn()
    except Exception:
        return None, frozenset()
    if not isinstance(raw_langs, tuple):
        return None, frozenset()
    compiled: frozenset[str] = frozenset(str(name) for name in raw_langs if isinstance(name, str) and name in LANGUAGES)
    return cast(_TsPackModule, mod), compiled


def _probe_fallback_factories(skip_langs: frozenset[str]) -> dict[str, tuple[str, str]]:
    available: dict[str, tuple[str, str]] = {}
    spec_cache: dict[str, bool] = {}
    for lang, spec in LANGUAGES.items():
        if lang in skip_langs:
            continue
        for module_name, factory_name in spec.factories:
            installed: bool | None = spec_cache.get(module_name)
            if installed is None:
                try:
                    installed = importlib.util.find_spec(module_name) is not None
                except (ImportError, ValueError):
                    installed = False
                spec_cache[module_name] = installed
            if installed:
                available[lang] = (module_name, factory_name)
                break
    return available


_TSPACK_MOD, _TSPACK_LANGS = _load_tspack()
_FALLBACK_FACTORIES: Final[dict[str, tuple[str, str]]] = _probe_fallback_factories(_TSPACK_LANGS)
_TSPACK_ZSH_FALLBACK: Final[bool] = (
    "zsh" not in _TSPACK_LANGS and "zsh" not in _FALLBACK_FACTORIES and "bash" in _TSPACK_LANGS
)
AVAILABLE_LANGS: Final[frozenset[str]] = (
    _TSPACK_LANGS | frozenset(_FALLBACK_FACTORIES) | (frozenset({"zsh"}) if _TSPACK_ZSH_FALLBACK else frozenset())
)
_MULTI_SUFFIX_TO_LANG: Final[tuple[tuple[str, str], ...]] = tuple(
    sorted(
        (
            (suffix, lang)
            for lang, spec in LANGUAGES.items()
            if lang in AVAILABLE_LANGS
            for suffix in spec.suffixes
            if suffix.count(".") > 1
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
_SUFFIX_TO_LANG: Final[dict[str, str]] = {
    suffix: lang
    for lang, spec in LANGUAGES.items()
    if lang in AVAILABLE_LANGS
    for suffix in spec.suffixes
    if suffix.count(".") == 1
}
_FILENAME_TO_LANG: Final[dict[str, str]] = {
    filename: lang for lang, spec in LANGUAGES.items() if lang in AVAILABLE_LANGS for filename in spec.filenames
}
_PARSERS: dict[str, Parser] = {}
_UNAVAILABLE_PARSERS: set[str] = set()


class Task(NamedTuple):
    path: Path
    check_only: bool
    backup: bool
    keep_doc: bool
    max_size: int


@dataclass(frozen=True, slots=True)
class ProcessResult:
    path: Path
    original_bytes: int
    reduced_bytes: int
    status: Status
    detail: str = ""


@dataclass(frozen=True, slots=True)
class _EmbeddedBlock:
    language: str | None
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _EmbeddedValidation:
    block_index: int
    language: str
    original_root: Node
    original_bytes: bytes
    removed_spans: frozenset[tuple[int, int]]


def _load_fallback_parser(lang: str) -> Parser | None:
    factory_info: tuple[str, str] | None = _FALLBACK_FACTORIES.get(lang)
    candidates: tuple[tuple[str, str], ...] = (factory_info,) if factory_info is not None else LANGUAGES[lang].factories
    for module_name, factory_name in candidates:
        try:
            module = importlib.import_module(module_name)
            factory: Callable[[], object] = getattr(module, factory_name)
            language = Language(factory())
            try:
                return Parser(language)
            except TypeError:
                parser = Parser()
                parser.language = language
                return parser
        except (ImportError, AttributeError, OSError, TypeError, ValueError):
            continue
    return None


def _get_parser(lang: str) -> Parser | None:
    if lang in _UNAVAILABLE_PARSERS:
        return None
    cached: Parser | None = _PARSERS.get(lang)
    if cached is not None:
        return cached
    if _TSPACK_MOD is not None:
        tspack_key: str | None = (
            lang if lang in _TSPACK_LANGS else ("bash" if lang == "zsh" and _TSPACK_ZSH_FALLBACK else None)
        )
        if tspack_key is not None:
            try:
                parser: Parser = _TSPACK_MOD.get_parser(tspack_key)
                _PARSERS[lang] = parser
                return parser
            except (
                ImportError,
                LookupError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                pass
    fallback_parser: Parser | None = _load_fallback_parser(lang)
    if fallback_parser is not None:
        _PARSERS[lang] = fallback_parser
        return fallback_parser
    _UNAVAILABLE_PARSERS.add(lang)
    return None


def _shebang_language(first_line: bytes) -> str | None:
    import shlex
    if not first_line.startswith(b"#!"):
        return None
    line: str = first_line[2:].decode("utf-8", "replace").strip()
    if not line:
        return None
    try:
        parts: list[str] = shlex.split(line)
    except ValueError:
        parts = line.split()
    if not parts:
        return None
    interpreter: str = PurePosixPath(parts[0]).name
    if interpreter == "env":
        rest: list[str] = [part for part in parts[1:] if not part.startswith("-") and "=" not in part]
        if not rest:
            return None
        interpreter = PurePosixPath(rest[0]).name
    for pattern, lang in _INTERPRETERS:
        if pattern.fullmatch(interpreter):
            if lang in AVAILABLE_LANGS:
                return lang
            if lang == "zsh" and "bash" in AVAILABLE_LANGS:
                return "bash"
            return None
    return None


def _detect_language_by_name(name: str) -> str | None:
    lower_name: str = name.lower()
    by_filename: str | None = _FILENAME_TO_LANG.get(lower_name)
    if by_filename is not None:
        return by_filename
    for suffix, lang in _MULTI_SUFFIX_TO_LANG:
        if lower_name.endswith(suffix):
            return lang
    dot_idx: int = lower_name.rfind(".")
    if dot_idx > 0:
        return _SUFFIX_TO_LANG.get(lower_name[dot_idx:])
    return None


def detect_language(path: Path, source: bytes | None = None) -> str | None:
    by_name: str | None = _detect_language_by_name(path.name)
    head: bytes = b""
    if source is not None:
        nl: int = source.find(b"\n", 0, 512)
        head = source[:512] if nl == -1 else source[:nl]
    elif by_name is None:
        try:
            with path.open("rb") as fh:
                head = fh.readline(512)
        except OSError:
            return None
    if head.startswith(b"#!"):
        shebang_lang: str | None = _shebang_language(head.rstrip(b"\r\n"))
        if shebang_lang is not None:
            return shebang_lang
    return by_name


def _scan_dir(root: Path, excludes: frozenset[str]) -> Iterator[Path]:
    stack: list[Path] = [root]
    while stack:
        current: Path = stack.pop()
        try:
            with os.scandir(current) as entries:
                subdirs: list[Path] = []
                files: list[Path] = []
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in excludes:
                                subdirs.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            lang: str | None = _detect_language_by_name(entry.name)
                            if lang is not None:
                                files.append(Path(entry.path))
                            elif "." not in entry.name:
                                candidate = Path(entry.path)
                                if detect_language(candidate) is not None:
                                    files.append(candidate)
                    except OSError as exc:
                        logger.error("{} | cannot inspect entry: {}", entry.path, exc)
                subdirs.sort(reverse=True)
                stack.extend(subdirs)
                files.sort()
                yield from files
        except OSError as exc:
            logger.error("{} | cannot scan directory: {}", current, exc)


def collect_supported_files(inputs: Sequence[Path], excludes: frozenset[str]) -> list[Path]:
    targets: Sequence[Path] = inputs if inputs else (Path.cwd(),)
    seen: set[Path] = set()
    unique_files: list[Path] = []
    for input_path in targets:
        if input_path.is_symlink():
            logger.warning("{} | symlink skipped", input_path)
            continue
        if not input_path.exists():
            logger.error("{} | path does not exist", input_path)
            continue
        if input_path.is_file():
            if detect_language(input_path) is not None:
                resolved: Path = input_path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    unique_files.append(input_path)
            else:
                logger.debug("{} | unsupported or grammar not installed, skipping", input_path)
        elif input_path.is_dir():
            for candidate in _scan_dir(input_path, excludes):
                resolved_candidate: Path = candidate.resolve()
                if resolved_candidate not in seen:
                    seen.add(resolved_candidate)
                    unique_files.append(candidate)
        else:
            logger.warning("{} | not a regular file or directory, skipping", input_path)
    unique_files.sort(key=lambda item: str(item).lower())
    return unique_files


def _is_vital_comment(src: bytes, start: int, end: int, spec: LangSpec, keep_doc: bool) -> bool:
    raw: bytes = src[start:end]
    if start == 0 and raw.startswith(b"#!"):
        return True
    if keep_doc and raw.startswith(_DOC_PREFIXES) and raw != b"/**/":
        return True
    return spec.vital_re.search(raw.decode("utf-8", "replace")) is not None


def _collect_removable_comments(
    root: Node,
    src: bytes,
    spec: LangSpec,
    keep_doc: bool,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    comment_types: frozenset[str] = spec.comment_types
    veto: VetoFn | None = spec.veto
    stack: list[Node] = [root]
    while stack:
        node: Node = stack.pop()
        if node.is_missing:
            continue
        if node.type in comment_types:
            start: int = node.start_byte
            end: int = node.end_byte
            if _is_vital_comment(src, start, end, spec, keep_doc):
                continue
            if veto is not None and veto(node, src):
                continue
            ranges.append((start, end))
            continue
        children: list[Node] = node.children
        if children:
            stack.extend(reversed(children))
    return ranges


def _attribute_text(node: Node) -> bytes:
    for child in node.children:
        if child.type == "quoted_attribute_value":
            inner: Node | None = next(
                (item for item in child.children if item.type == "attribute_value"),
                None,
            )
            return inner.text or b"" if inner is not None else b""
        if child.type == "attribute_value":
            return child.text or b""
    return b""


def _embedded_language(start_tag: Node | None, kind: str) -> str | None:
    if start_tag is None:
        return None
    table: dict[str, str] = _SCRIPT_TYPE_LANGUAGES if kind == "script" else _STYLE_TYPE_LANGUAGES
    value: bytes = b""
    for attribute in start_tag.children:
        if attribute.type != "attribute":
            continue
        name: Node | None = next(
            (child for child in attribute.children if child.type == "attribute_name"),
            None,
        )
        if name is None or (name.text or b"").lower() != b"type":
            continue
        value = _attribute_text(attribute)
        break
    mime: str = value.split(b";")[0].strip().lower().decode("utf-8", "replace")
    return table.get(mime)


def _html_raw_text_blocks(root: Node) -> list[_EmbeddedBlock]:
    blocks: list[_EmbeddedBlock] = []
    stack: list[Node] = [root]
    while stack:
        node: Node = stack.pop()
        if node.type in {"script_element", "style_element"}:
            kind: str = "script" if node.type == "script_element" else "style"
            start_tag: Node | None = next((child for child in node.children if child.type == "start_tag"), None)
            raw_text: Node | None = next((child for child in node.children if child.type == "raw_text"), None)
            if raw_text is not None:
                start, end = raw_text.start_byte, raw_text.end_byte
            else:
                start = start_tag.end_byte if start_tag is not None else node.start_byte
                end = start
            blocks.append(_EmbeddedBlock(_embedded_language(start_tag, kind), start, end))
            continue
        if node.type == "ERROR":
            for index, child in enumerate(node.children):
                if child.type != "start_tag":
                    continue
                tag_name: Node | None = next((item for item in child.children if item.type == "tag_name"), None)
                tag_kind: str = (
                    (tag_name.text or b"").lower().decode("utf-8", "replace") if tag_name is not None else ""
                )
                if tag_kind not in {"script", "style"}:
                    continue
                following: Node | None = node.children[index + 1] if index + 1 < len(node.children) else None
                if following is None or following.type != "raw_text":
                    continue
                blocks.append(
                    _EmbeddedBlock(
                        _embedded_language(child, tag_kind),
                        following.start_byte,
                        following.end_byte,
                    )
                )
            stack.extend(reversed(node.children))
            continue
        if node.children:
            stack.extend(reversed(node.children))
    return blocks


def _legacy_html_comment_ranges(embedded: bytes, root: Node, spec: LangSpec, keep_doc: bool) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    stack: list[Node] = [root]
    while stack:
        node: Node = stack.pop()
        if node.type == "html_comment":
            if _is_vital_comment(embedded, node.start_byte, node.end_byte, spec, keep_doc):
                continue
            line_start: int = embedded.rfind(b"\n", 0, node.start_byte) + 1
            line_end: int = embedded.find(b"\n", node.end_byte)
            if line_end == -1:
                line_end = len(embedded)
            if not embedded[line_start : node.start_byte].strip() and not embedded[node.end_byte : line_end].strip():
                ranges.append((line_start, node.end_byte))
            continue
        if node.children:
            stack.extend(reversed(node.children))
    return ranges


def _html_embedded_comments(
    blocks: Sequence[_EmbeddedBlock],
    source: bytes,
    keep_doc: bool,
) -> tuple[list[tuple[int, int]], list[_EmbeddedValidation]]:
    ranges: list[tuple[int, int]] = []
    validations: list[_EmbeddedValidation] = []
    for block_index, block in enumerate(blocks):
        if block.language is None or block.end <= block.start:
            continue
        embedded_parser: Parser | None = _get_parser(block.language)
        if embedded_parser is None:
            continue
        embedded_spec: LangSpec = LANGUAGES[block.language]
        embedded: bytes = source[block.start : block.end]
        embedded_tree: Tree = embedded_parser.parse(embedded)
        embedded_ranges: list[tuple[int, int]] = _collect_removable_comments(
            embedded_tree.root_node,
            embedded,
            embedded_spec,
            keep_doc,
        )
        if block.language in {"javascript", "typescript", "tsx"}:
            embedded_ranges.extend(
                _legacy_html_comment_ranges(embedded, embedded_tree.root_node, embedded_spec, keep_doc)
            )
        if embedded_ranges:
            ranges.extend((block.start + left, block.start + right) for left, right in embedded_ranges)
        validations.append(
            _EmbeddedValidation(
                block_index=block_index,
                language=block.language,
                original_root=embedded_tree.root_node,
                original_bytes=embedded,
                removed_spans=frozenset(embedded_ranges),
            )
        )
    return ranges, validations


def _scan_json_comment_ranges(
    source: bytes,
    spec: LangSpec,
    keep_doc: bool,
    *,
    preserve_vital: bool = True,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    length: int = len(source)
    i: int = 0
    in_string: bool = False
    while i < length:
        ch: int = source[i]
        if in_string:
            if ch == 0x5C:
                i += 2
                continue
            if ch == 0x22:
                in_string = False
            i += 1
            continue
        if ch == 0x22:
            in_string = True
            i += 1
            continue
        if ch == 0x2F and i + 1 < length:
            nxt: int = source[i + 1]
            if nxt == 0x2F:
                start: int = i
                end: int = source.find(b"\n", i + 2)
                if end == -1:
                    end = length
                elif end > start and source[end - 1] == 0x0D:
                    end -= 1
                if not preserve_vital or not _is_vital_comment(source, start, end, spec, keep_doc):
                    ranges.append((start, end))
                i = end
                continue
            if nxt == 0x2A:
                start = i
                close_pos: int = source.find(b"*/", i + 2)
                if close_pos == -1:
                    break
                end = close_pos + 2
                if not preserve_vital or not _is_vital_comment(source, start, end, spec, keep_doc):
                    ranges.append((start, end))
                i = end
                continue
        i += 1
    return ranges


def _remove_comment_ranges(source: bytes, ranges: list[tuple[int, int]]) -> bytes:
    if not ranges:
        return source
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    view: memoryview = memoryview(source)
    output: bytearray = bytearray()
    cursor: int = 0
    src_len: int = len(source)
    for start, end in merged:
        output.extend(view[cursor:start])
        comment: bytes = source[start:end]
        line_breaks: bytes = bytes(byte for byte in comment if byte in (0x0A, 0x0D))
        if not line_breaks and start > 0 and end < src_len:
            before: int = source[start - 1]
            after: int = source[end]
            if before not in b" \t\r\n" and after not in b" \t\r\n":
                output.append(0x20)
        output.extend(line_breaks)
        cursor = end
    output.extend(view[cursor:])
    return bytes(output)


def _element_name(node: Node) -> bytes:
    start_tag: Node | None = next((child for child in node.children if child.type == "start_tag"), None)
    if start_tag is None:
        return b""
    name: Node | None = next((child for child in start_tag.children if child.type == "tag_name"), None)
    return (name.text or b"").lower() if name is not None else b""


def _protected_spans(root: Node, source: bytes, lang: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    stack: list[Node] = [root]
    while stack:
        node: Node = stack.pop()
        if node.type in _PROTECTED_NODE_TYPES:
            if b"\n" in source[node.start_byte : node.end_byte]:
                spans.append((node.start_byte, node.end_byte))
            continue
        if lang == "html" and node.type == "element" and _element_name(node) in _HTML_PREFORMATTED_TAGS:
            start_tag: Node | None = next((child for child in node.children if child.type == "start_tag"), None)
            end_tag: Node | None = next((child for child in node.children if child.type == "end_tag"), None)
            open_end: int = start_tag.end_byte if start_tag is not None else node.start_byte
            close_start: int = end_tag.start_byte if end_tag is not None else node.end_byte
            if close_start > open_end:
                spans.append((open_end, close_start))
            continue
        if node.children:
            stack.extend(node.children)
    return spans


def _protected_regions(root: Node, source: bytes, lang: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = _protected_spans(root, source, lang)
    if lang != "html":
        return spans
    for block in _html_raw_text_blocks(root):
        if block.end <= block.start:
            continue
        if block.language is None:
            spans.append((block.start, block.end))
            continue
        embedded_parser: Parser | None = _get_parser(block.language)
        if embedded_parser is None:
            spans.append((block.start, block.end))
            continue
        embedded: bytes = source[block.start : block.end]
        embedded_root: Node = embedded_parser.parse(embedded).root_node
        spans.extend(
            (block.start + left, block.start + right)
            for left, right in _protected_spans(embedded_root, embedded, block.language)
        )
    return spans


def _remove_blank_lines(source: bytes, protected: list[tuple[int, int]]) -> bytes:
    spans: list[tuple[int, int]] = sorted(protected)
    retained: list[bytes] = []
    offset: int = 0
    span_index: int = 0
    span_count: int = len(spans)
    for line in source.splitlines(keepends=True):
        start: int = offset
        end: int = offset + len(line)
        offset = end
        while span_index < span_count and spans[span_index][1] <= start:
            span_index += 1
        if span_index < span_count and spans[span_index][0] < end:
            retained.append(line)
            continue
        if line.endswith(b"\r\n"):
            body, ending = line[:-2], b"\r\n"
        elif line.endswith((b"\n", b"\r")):
            body, ending = line[:-1], line[-1:]
        else:
            body, ending = line, b""
        body = body.rstrip(b" \t")
        if body.strip():
            retained.append(body + ending)
    return b"".join(retained)


def _significant_bytes(data: bytes) -> bytes:
    return b"".join(data.split())


def _defect_counts(node: Node) -> tuple[int, int]:
    errors: int = 0
    missing: int = 0
    stack: list[Node] = [node]
    while stack:
        current: Node = stack.pop()
        if current.type == "ERROR":
            errors += 1
        elif current.is_missing:
            missing += 1
        if current.children:
            stack.extend(current.children)
    return errors, missing


def _regressed(before: Node, after: Node) -> bool:
    if not after.has_error:
        return False
    if not before.has_error:
        return True
    before_errors, before_missing = _defect_counts(before)
    after_errors, after_missing = _defect_counts(after)
    return after_errors > before_errors or after_missing > before_missing


def _flush_text(digest: _DigestLike, pending: list[bytes]) -> None:
    if pending:
        normalized: bytes = b" ".join(b"".join(pending).split())
        if normalized:
            digest.update(b"T\0" + normalized + b"\0")
        pending.clear()


def _fingerprint(
    root: Node,
    src: bytes,
    comment_types: frozenset[str],
    removed_spans: frozenset[tuple[int, int]],
    embedded_spans: frozenset[tuple[int, int]] = frozenset(),
) -> bytes:
    digest = hashlib.blake2b(digest_size=16)
    pending: list[bytes] = []
    stack: list[Node] = [root]
    while stack:
        node: Node = stack.pop()
        if node.is_missing:
            continue
        key: tuple[int, int] = (node.start_byte, node.end_byte)
        if node.type in comment_types or node.type == "html_comment":
            if key in removed_spans:
                continue
            _flush_text(digest, pending)
            chunk: bytes = src[node.start_byte : node.end_byte].rstrip()
            if chunk:
                digest.update(node.type.encode("utf-8") + b"\0" + chunk + b"\0")
            continue
        if node.type == "raw_text" and key in embedded_spans:
            _flush_text(digest, pending)
            digest.update(b"raw_text\0<embedded>\0")
            continue
        if node.child_count == 0:
            leaf: bytes = src[node.start_byte : node.end_byte]
            if node.type in _TEXT_NODE_TYPES:
                pending.append(leaf)
                continue
            if not leaf.strip():
                continue
            _flush_text(digest, pending)
            digest.update(node.type.encode("utf-8") + b"\0" + leaf + b"\0")
            continue
        stack.extend(reversed(node.children))
    _flush_text(digest, pending)
    return digest.digest()


def _validate_tree(
    original_root: Node,
    original_src: bytes,
    cleaned_root: Node,
    cleaned_src: bytes,
    comment_types: frozenset[str],
    removed_spans: frozenset[tuple[int, int]],
    orig_embedded_spans: frozenset[tuple[int, int]] = frozenset(),
    clean_embedded_spans: frozenset[tuple[int, int]] = frozenset(),
) -> str | None:
    if _regressed(original_root, cleaned_root):
        return "cleaned output introduces tree-sitter parse errors"
    before_fp: bytes = _fingerprint(
        original_root,
        original_src,
        comment_types,
        removed_spans,
        orig_embedded_spans,
    )
    after_fp: bytes = _fingerprint(
        cleaned_root,
        cleaned_src,
        comment_types,
        frozenset(),
        clean_embedded_spans,
    )
    if before_fp != after_fp:
        return "token stream changed beyond intended comment removal"
    return None


def _process_json(path: Path, source: bytes, task: Task, ref: os.stat_result) -> ProcessResult:
    parser: Parser | None = _get_parser("json")
    if parser is None:
        return ProcessResult(path, len(source), 0, "skipped", "grammar not installed: json")
    spec: LangSpec = LANGUAGES["json"]
    comment_ranges: list[tuple[int, int]] = _scan_json_comment_ranges(source, spec, task.keep_doc)
    comment_free: bytes = _remove_comment_ranges(source, comment_ranges)
    cleaned: bytes = _remove_blank_lines(comment_free, [])
    if _significant_bytes(cleaned) != _significant_bytes(comment_free):
        return ProcessResult(path, len(source), 0, "invalid", "cleanup would drop non-whitespace bytes")
    if cleaned == source:
        return ProcessResult(path, len(source), 0, "unchanged")
    all_orig_comments: list[tuple[int, int]] = _scan_json_comment_ranges(
        source, spec, task.keep_doc, preserve_vital=False
    )
    all_clean_comments: list[tuple[int, int]] = _scan_json_comment_ranges(
        cleaned, spec, task.keep_doc, preserve_vital=False
    )
    strict_orig: bytes = _remove_comment_ranges(source, all_orig_comments)
    strict_clean: bytes = _remove_comment_ranges(cleaned, all_clean_comments)
    orig_tree: Tree = parser.parse(strict_orig)
    clean_tree: Tree = parser.parse(strict_clean)
    problem: str | None = _validate_tree(
        orig_tree.root_node,
        strict_orig,
        clean_tree.root_node,
        strict_clean,
        spec.comment_types,
        frozenset(),
    )
    if problem is not None:
        return ProcessResult(path, len(source), 0, "invalid", f"JSON validation failed: {problem}")
    return _commit_result(path, source, cleaned, task, ref)


def _commit_result(
    path: Path,
    source: bytes,
    cleaned: bytes,
    task: Task,
    ref: os.stat_result,
) -> ProcessResult:
    reduced: int = max(0, len(source) - len(cleaned))
    if cleaned == source:
        return ProcessResult(path, len(source), 0, "unchanged")
    if not task.check_only:
        current: os.stat_result = path.stat()
        if (current.st_mtime_ns, current.st_size) != (ref.st_mtime_ns, ref.st_size):
            return ProcessResult(path, len(source), 0, "skipped", "file modified concurrently")
        if task.backup:
            shutil.copy2(path, path.with_name(f"{path.name}.bak"))
        atomic_write(path, cleaned)
    return ProcessResult(path, len(source), reduced, "changed")


def process_file(task: Task) -> ProcessResult:
    path: Path = task.path
    try:
        if path.is_symlink() or not path.is_file():
            return ProcessResult(path, 0, 0, "skipped", "not a regular file")
        ref: os.stat_result = path.stat()
        if ref.st_size == 0:
            return ProcessResult(path, 0, 0, "unchanged")
        if ref.st_size > task.max_size:
            return ProcessResult(
                path,
                ref.st_size,
                0,
                "skipped",
                f"exceeds max size ({task.max_size} bytes)",
            )
        source: bytes = path.read_bytes()
        if b"\x00" in source[:4096]:
            return ProcessResult(path, len(source), 0, "skipped", "binary file")
        lang: str | None = detect_language(path, source)
        if lang is None:
            return ProcessResult(path, len(source), 0, "unsupported", "no supported extension or grammar")
        if lang == "json":
            return _process_json(path, source, task, ref)
        parser: Parser | None = _get_parser(lang)
        if parser is None:
            return ProcessResult(path, len(source), 0, "skipped", f"grammar not installed: {lang}")
        spec: LangSpec = LANGUAGES[lang]
        original_tree: Tree = parser.parse(source)
        comment_ranges: list[tuple[int, int]] = _collect_removable_comments(
            original_tree.root_node,
            source,
            spec,
            task.keep_doc,
        )
        embedded_validations: list[_EmbeddedValidation] = []
        orig_html_blocks: list[_EmbeddedBlock] = []
        if lang == "html":
            orig_html_blocks = _html_raw_text_blocks(original_tree.root_node)
            embedded_ranges, embedded_validations = _html_embedded_comments(
                orig_html_blocks,
                source,
                task.keep_doc,
            )
            comment_ranges.extend(embedded_ranges)
        comment_free: bytes = _remove_comment_ranges(source, comment_ranges)
        if lang in _PROSE_LANGUAGES:
            cleaned: bytes = comment_free
        else:
            stripped_tree: Tree = parser.parse(comment_free)
            protected: list[tuple[int, int]] = _protected_regions(stripped_tree.root_node, comment_free, lang)
            cleaned = _remove_blank_lines(comment_free, protected)
        if _significant_bytes(cleaned) != _significant_bytes(comment_free):
            return ProcessResult(
                path,
                len(source),
                0,
                "invalid",
                "cleanup would drop non-whitespace bytes",
            )
        if cleaned == source:
            return ProcessResult(path, len(source), 0, "unchanged")
        cleaned_tree: Tree = parser.parse(cleaned)
        orig_embedded_spans: frozenset[tuple[int, int]] = frozenset()
        clean_embedded_spans: frozenset[tuple[int, int]] = frozenset()
        if embedded_validations:
            cleaned_html_blocks: list[_EmbeddedBlock] = _html_raw_text_blocks(cleaned_tree.root_node)
            if len(cleaned_html_blocks) != len(orig_html_blocks):
                return ProcessResult(path, len(source), 0, "invalid", "HTML embedded block count changed")
            validated_indices: set[int] = {item.block_index for item in embedded_validations}
            orig_embedded_spans = frozenset(
                (orig_html_blocks[idx].start, orig_html_blocks[idx].end) for idx in validated_indices
            )
            clean_embedded_spans = frozenset(
                (cleaned_html_blocks[idx].start, cleaned_html_blocks[idx].end) for idx in validated_indices
            )
            for item in embedded_validations:
                if item.block_index >= len(cleaned_html_blocks):
                    return ProcessResult(path, len(source), 0, "invalid", "HTML embedded block missing")
                clean_block: _EmbeddedBlock = cleaned_html_blocks[item.block_index]
                if clean_block.language != item.language:
                    return ProcessResult(
                        path,
                        len(source),
                        0,
                        "invalid",
                        "HTML embedded block language changed",
                    )
                emb_parser: Parser | None = _get_parser(item.language)
                if emb_parser is None:
                    return ProcessResult(
                        path,
                        len(source),
                        0,
                        "skipped",
                        f"grammar not installed: {item.language}",
                    )
                clean_emb_bytes: bytes = cleaned[clean_block.start : clean_block.end]
                clean_emb_root: Node = emb_parser.parse(clean_emb_bytes).root_node
                emb_problem: str | None = _validate_tree(
                    item.original_root,
                    item.original_bytes,
                    clean_emb_root,
                    clean_emb_bytes,
                    LANGUAGES[item.language].comment_types,
                    item.removed_spans,
                )
                if emb_problem is not None:
                    return ProcessResult(
                        path,
                        len(source),
                        0,
                        "invalid",
                        f"embedded {item.language}: {emb_problem}",
                    )
        outer_removed: frozenset[tuple[int, int]] = frozenset(
            span
            for span in comment_ranges
            if not any(left <= span[0] and span[1] <= right for left, right in orig_embedded_spans)
        )
        problem: str | None = _validate_tree(
            original_tree.root_node,
            source,
            cleaned_tree.root_node,
            cleaned,
            spec.comment_types,
            outer_removed,
            orig_embedded_spans,
            clean_embedded_spans,
        )
        if problem is not None:
            return ProcessResult(path, len(source), 0, "invalid", problem)
        return _commit_result(path, source, cleaned, task, ref)
    except Exception as exc:
        return ProcessResult(path, 0, 0, "error", f"{type(exc).__name__}: {exc}")


def _build_table(results: Sequence[ProcessResult], total: int, width: int) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 1), expand=True, width=width)
    index_width: int = len(f"[{max(total, 1)}/{max(total, 1)}]")
    table.add_column("index", justify="right", no_wrap=True, width=index_width)
    table.add_column("filename", overflow="ellipsis", no_wrap=True, ratio=1)
    table.add_column("bytes", justify="right", no_wrap=True, width=10)
    for index, result in enumerate(results, start=1):
        style: str = "green" if result.reduced_bytes > 0 else "dim"
        table.add_row(
            f"[{index}/{total}]",
            str(result.path),
            Text(str(result.reduced_bytes), style=style),
        )
    return table


def _configure_logging(verbose: bool, log_file: Path | None) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "ERROR",
        format="{time:HH:mm:ss} | {level: <8} | {message}",
    )
    if log_file is not None:
        logger.add(log_file, level="DEBUG", rotation="10 MB", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class CliArgs:
    paths: tuple[Path, ...]
    check: bool
    backup: bool
    keep_doc: bool
    workers: int
    max_size: int
    exclude: tuple[str, ...]
    log_file: Path | None
    verbose: bool


def _positive_int(raw: str) -> int:
    try:
        value: int = int(raw)
    except ValueError as exc:
        msg: str = f"invalid integer value: {raw!r}"
        raise argparse.ArgumentTypeError(msg) from exc
    if value < 1:
        msg = "must be >= 1"
        raise argparse.ArgumentTypeError(msg)
    return value


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tsclean",
        description="Strip non-vital comments and blank lines using tree-sitter, safely and in parallel.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        metavar="PATH",
        help="files/directories (default: current directory)",
    )
    parser.add_argument(
        "-c",
        "--check",
        action="store_true",
        help="report potential savings without writing",
    )
    parser.add_argument(
        "-b",
        "--backup",
        action="store_true",
        help="save .bak copy before modifying files",
    )
    parser.add_argument(
        "--keep-doc",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="preserve doc comments (/**, ///, //!)",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=_positive_int,
        default=DEFAULT_WORKERS,
        help=f"number of parallel worker processes (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--max-size",
        type=_positive_int,
        default=DEFAULT_MAX_SIZE,
        help=f"maximum file size in bytes to process (default: {DEFAULT_MAX_SIZE})",
    )
    parser.add_argument(
        "-x",
        "--exclude",
        action="append",
        default=[],
        metavar="NAME",
        help="additional directory names to skip",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="optional path to write loguru logs",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable verbose loguru output",
    )
    return parser


def _parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    ns: argparse.Namespace = _build_arg_parser().parse_args(argv)
    raw_paths: list[Path] = list(ns.paths)
    raw_excludes: list[str] = list(ns.exclude)
    return CliArgs(
        paths=tuple(raw_paths),
        check=bool(ns.check),
        backup=bool(ns.backup),
        keep_doc=bool(ns.keep_doc),
        workers=int(ns.workers),
        max_size=int(ns.max_size),
        exclude=tuple(raw_excludes),
        log_file=ns.log_file if isinstance(ns.log_file, Path) else None,
        verbose=bool(ns.verbose),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args: CliArgs = _parse_args(argv)
    _configure_logging(args.verbose, args.log_file)
    console = Console()
    if not AVAILABLE_LANGS:
        logger.error("No tree-sitter grammar libraries are installed.")
        console.print("[yellow]No supported tree-sitter grammars installed.[/]")
        return 0
    logger.debug(
        "grammars loaded: tspack={} fallback={}",
        len(_TSPACK_LANGS),
        len(_FALLBACK_FACTORIES),
    )
    excludes: frozenset[str] = frozenset(SKIP_DIRS) | frozenset(args.exclude)
    target_paths: Sequence[Path] = args.paths if args.paths else (Path.cwd(),)
    files: list[Path] = collect_supported_files(target_paths, excludes)
    if not files:
        console.print("[yellow]No supported files found.[/]")
        return 0
    total: int = len(files)
    tasks: list[Task] = [Task(path, args.check, args.backup, args.keep_doc, args.max_size) for path in files]
    chunksize: int = max(1, min(8, total // (args.workers * 4) or 1))
    results: list[ProcessResult] = []
    total_freed: int = 0
    with mp.Pool(processes=args.workers) as pool:
        last_refresh: float = 0.0
        with Live(
            _build_table(results, total, console.width),
            console=console,
            refresh_per_second=LIVE_REFRESH_HZ,
        ) as live:
            for result in pool.imap_unordered(process_file, tasks, chunksize=chunksize):
                results.append(result)
                total_freed += result.reduced_bytes
                now: float = time.monotonic()
                if now - last_refresh >= LIVE_MIN_INTERVAL or len(results) == total:
                    live.update(_build_table(results, total, console.width))
                    last_refresh = now
    total_label: str = "Potential space freed" if args.check else "Total space freed"
    console.print(f"\n[bold]{total_label}:[/] [green]{total_freed}[/] bytes")
    errors: list[ProcessResult] = [res for res in results if res.status in {"invalid", "error"}]
    for res in errors:
        logger.error("{} | {} ({})", res.path, res.detail, res.status)
    return 1 if errors else 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
