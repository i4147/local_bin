#!/data/data/com.termux/files/usr/bin/env python

import argparse
import ast
import csv
import difflib
import fnmatch
import functools
import hashlib
import importlib.util
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import tokenize
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeAlias

from dh import MAX_WORKERS
from loguru import logger

try:
    from xxhash import xxh64_hexdigest as _xxh64_hexdigest
except ImportError:

    def _xxh64_hexdigest(data: bytes) -> str:
        return hashlib.blake2b(data, digest_size=8).hexdigest()


try:
    import pathspec as _pathspec
except ImportError:
    _pathspec = None
try:
    import ssdeep as _ssdeep
except ImportError:
    _ssdeep = None
try:
    import tlsh as _tlsh
except ImportError:
    _tlsh = None
try:
    from concurrent.futures import InterpreterPoolExecutor as _InterpreterPoolExecutor
except ImportError:
    _InterpreterPoolExecutor = None
try:
    from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
except ImportError:
    _ThreadPoolExecutor = None
try:
    import tomllib as _tomllib
except ImportError:
    try:
        import tomli as _tomllib
    except ImportError:
        _tomllib = None
__version__ = "1.0.0"
LOGGER = logger
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".venv",
    ".git",
    "__pycache__",
    "node_modules",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
})
PYTHON_SUFFIXES: frozenset[str] = frozenset({".py", ".pyi"})
NOTEBOOK_SUFFIX: str = ".ipynb"
CACHE_FILENAME: str = ".pydup-cache.json"
SIMILAR_FILENAME: str = "simz.json"
SIMILARITY_THRESHOLD: int = 70
MINHASH_SIG_BITS: int = 128
MINHASH_BANDS: int = 32
MINHASH_ROWS: int = MINHASH_SIG_BITS // MINHASH_BANDS
MASK64: int = (1 << 64) - 1
_MINHASH_SEED = random.Random(0x5EED)
_MINHASH_A: tuple[int, ...] = tuple(_MINHASH_SEED.getrandbits(64) | 1 for _ in range(MINHASH_SIG_BITS))
_MINHASH_B: tuple[int, ...] = tuple(_MINHASH_SEED.getrandbits(64) for _ in range(MINHASH_SIG_BITS))
CACHE_VERSION: int = 1
NGRAM_SIZE: int = 3
SKIP_TOKEN_TYPES: frozenset[int] = frozenset({
    tokenize.ENCODING,
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.COMMENT,
    tokenize.INDENT,
    tokenize.DEDENT,
})
ScanIssue: TypeAlias = tuple[str, str]
Failure: TypeAlias = tuple[str, str]


def _digest(data: bytes) -> str:
    return _xxh64_hexdigest(data)


@dataclass(slots=True)
class FileEntry:
    path: Path
    size: int
    mtime_ns: int
    ino: int
    dev: int


@dataclass(slots=True, frozen=True)
class DefinitionRef:
    digest: str
    path: str
    lineno: int
    name: str
    kind: str

    def __getstate__(self) -> dict[str, object]:
        return {
            "digest": self.digest,
            "path": self.path,
            "lineno": self.lineno,
            "name": self.name,
            "kind": self.kind,
        }

    def __setstate__(self, state: dict[str, object]) -> None:
        for name, value in state.items():
            object.__setattr__(self, name, value)


@dataclass(slots=True)
class WorkerResult:
    path: str
    digest: str | None = None
    error: str | None = None
    skipped: str | None = None
    ast_hash: str | None = None
    token_hash: str | None = None
    ngrams: list[int] | None = None
    definitions: list[DefinitionRef] | None = None
    fuzzy: str | None = None
    tlsh_hash: str | None = None
    minhash_sig: list[int] | None = None
    simhash_hash: int | None = None
    from_cache: bool = False

    def __getstate__(self) -> dict[str, object]:
        return {
            "path": self.path,
            "digest": self.digest,
            "error": self.error,
            "skipped": self.skipped,
            "ast_hash": self.ast_hash,
            "token_hash": self.token_hash,
            "ngrams": self.ngrams,
            "definitions": self.definitions,
            "fuzzy": self.fuzzy,
            "tlsh_hash": self.tlsh_hash,
            "minhash_sig": self.minhash_sig,
            "simhash_hash": self.simhash_hash,
            "from_cache": self.from_cache,
        }

    def __setstate__(self, state: dict[str, object]) -> None:
        for name, value in state.items():
            object.__setattr__(self, name, value)


@dataclass(slots=True)
class DuplicateGroup:
    digest: str
    paths: list[Path]
    sizes: dict[str, int]
    keeper: Path | None = None


@dataclass(slots=True)
class NearPair:
    paths: tuple[str, str]
    similarity: float


@dataclass(slots=True)
class DefinitionGroup:
    digest: str
    members: list[DefinitionRef]


@dataclass(slots=True)
class SimilarPair:
    paths: tuple[str, str]
    score: int
    method: str


@dataclass(slots=True)
class RunStats:
    scanned: int = 0
    parsed: int = 0
    cache_hits: int = 0
    skipped: int = 0
    failures: int = 0
    duplicate_groups: int = 0
    duplicate_files: int = 0
    wasted_bytes: int = 0
    elapsed_seconds: float = 0.0


@dataclass(slots=True)
class ScanOutcome:
    entries: dict[str, FileEntry]
    groups: list[DuplicateGroup]
    near_pairs: list[NearPair]
    definition_groups: list[DefinitionGroup]
    similar_pairs: list[SimilarPair]
    failures: list[Failure]
    stats: RunStats


@dataclass(slots=True)
class CacheEntry:
    mtime_ns: int
    size: int
    digest: str
    ast_hash: str | None
    token_hash: str | None
    fuzzy: str | None = None
    tlsh_hash: str | None = None
    minhash_sig: list[int] | None = None
    simhash_hash: int | None = None


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    mode: str
    ignore_imports: bool
    skip_empty: bool
    min_lines: int
    need_ngrams: bool
    need_definitions: bool
    need_ssdeep: bool = False
    need_tlsh: bool = False
    need_minhash: bool = False
    need_simhash: bool = False

    def __getstate__(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "ignore_imports": self.ignore_imports,
            "skip_empty": self.skip_empty,
            "min_lines": self.min_lines,
            "need_ngrams": self.need_ngrams,
            "need_definitions": self.need_definitions,
            "need_ssdeep": self.need_ssdeep,
            "need_tlsh": self.need_tlsh,
            "need_minhash": self.need_minhash,
            "need_simhash": self.need_simhash,
        }

    def __setstate__(self, state: dict[str, object]) -> None:
        for name, value in state.items():
            object.__setattr__(self, name, value)


@dataclass(slots=True)
class ScanConfig:
    follow_symlinks: bool
    include_notebooks: bool
    min_bytes: int
    exclude_patterns: list[str]


class SourceNormalizer(ast.NodeTransformer):
    def __init__(self, strip_annotations: bool, strip_type_nodes: bool) -> None:
        self.strip_annotations = strip_annotations
        self.strip_type_nodes = strip_type_nodes

    @staticmethod
    def _without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            return body[1:]
        return body

    def _clear_type_params(self, node: ast.AST) -> None:
        if self.strip_type_nodes and hasattr(node, "type_params"):
            setattr(node, "type_params", [])

    def visit_Module(self, node: ast.Module) -> ast.Module:
        self.generic_visit(node)
        node.body = self._without_docstring(node.body)
        if self.strip_type_nodes:
            node.type_ignores.clear()
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        if self.strip_type_nodes:
            node.type_comment = None
        if self.strip_annotations:
            node.returns = None
        self._clear_type_params(node)
        self.generic_visit(node)
        node.body = self._without_docstring(node.body)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        if self.strip_type_nodes:
            node.type_comment = None
        if self.strip_annotations:
            node.returns = None
        self._clear_type_params(node)
        self.generic_visit(node)
        node.body = self._without_docstring(node.body)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        self._clear_type_params(node)
        self.generic_visit(node)
        node.body = self._without_docstring(node.body)
        return node

    def visit_arg(self, node: ast.arg) -> ast.arg:
        if self.strip_annotations:
            node.annotation = None
        if self.strip_type_nodes and hasattr(node, "type_comment"):
            node.type_comment = None
        return node

    def visit_Assign(self, node: ast.Assign) -> ast.Assign:
        if self.strip_type_nodes:
            node.type_comment = None
        self.generic_visit(node)
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST | None:
        if self.strip_annotations:
            if node.value is None:
                return None
            target = self.visit(node.target)
            value = self.visit(node.value)
            if target is None or value is None:
                return None
            replacement = ast.Assign(targets=[target], value=value, type_comment=None)
            ast.copy_location(replacement, node)
            return replacement
        self.generic_visit(node)
        return node

    def visit_For(self, node: ast.For) -> ast.For:
        if self.strip_type_nodes:
            node.type_comment = None
        self.generic_visit(node)
        return node

    def visit_AsyncFor(self, node: ast.AsyncFor) -> ast.AsyncFor:
        if self.strip_type_nodes:
            node.type_comment = None
        self.generic_visit(node)
        return node

    def visit_With(self, node: ast.With) -> ast.With:
        if self.strip_type_nodes:
            node.type_comment = None
        self.generic_visit(node)
        return node

    def visit_AsyncWith(self, node: ast.AsyncWith) -> ast.AsyncWith:
        if self.strip_type_nodes:
            node.type_comment = None
        self.generic_visit(node)
        return node

    def visit_TypeAlias(self, node: ast.AST) -> ast.AST | None:
        if self.strip_type_nodes:
            return None
        return node


class _SimpleGitignore:
    def __init__(self, patterns: Sequence[str]) -> None:
        self._rules: list[tuple[bool, bool, re.Pattern[str]]] = []
        for raw in patterns:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = False
            if line.startswith("!"):
                negated = True
                line = line[1:]
            dir_only = line.endswith("/")
            if dir_only:
                line = line[:-1]
            anchored = "/" in line.rstrip("/")
            if line.startswith("/"):
                line = line[1:]
                anchored = True
            if not line:
                continue
            regex = self._compile(line, anchored)
            self._rules.append((negated, dir_only, regex))

    @staticmethod
    def _compile(pattern: str, anchored: bool) -> re.Pattern[str]:
        out: list[str] = []
        index = 0
        while index < len(pattern):
            char = pattern[index]
            if char == "*":
                if index + 1 < len(pattern) and pattern[index + 1] == "*":
                    out.append(".*")
                    index += 2
                    continue
                out.append("[^/]*")
            elif char == "?":
                out.append("[^/]")
            else:
                out.append(re.escape(char))
            index += 1
        body = "".join(out)
        if anchored:
            expr = rf"^{body}(/.*)?$"
        else:
            expr = rf"^(?:.*/)?{body}(/.*)?$"
        return re.compile(expr)

    def matches(self, rel: str, is_dir: bool) -> bool:
        result = False
        for negated, dir_only, regex in self._rules:
            if dir_only and not is_dir:
                continue
            if regex.match(rel):
                result = not negated
        return result


class _PathMatcher:
    def __init__(self, patterns: Sequence[str], gitignore_style: bool) -> None:
        cleaned = [p for p in patterns if p.strip() and not p.strip().startswith("#")]
        self._simple: _SimpleGitignore | None = None
        self._spec: Any = None
        if not cleaned:
            return
        if _pathspec is None:
            self._simple = _SimpleGitignore(cleaned)
            return
        if gitignore_style:
            from pathspec.gitignore import GitIgnoreSpec

            self._spec = GitIgnoreSpec.from_lines(cleaned)
        else:
            self._spec = _pathspec.PathSpec.from_lines("gitignore", cleaned)

    def matches(self, rel: str, is_dir: bool) -> bool:
        if self._simple is not None:
            return self._simple.matches(rel, is_dir)
        if self._spec is None:
            return False
        probes = [rel]
        if is_dir:
            probes.append(rel + "/")
        return any(self._spec.match_file(probe) for probe in probes)


def _strip_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _load_ignore_patterns(root: Path) -> list[str]:
    patterns: list[str] = []
    ignore_file = root / ".pydupignore"
    if ignore_file.is_file():
        try:
            patterns.extend(_strip_lines(ignore_file.read_text(encoding="utf-8")))
        except OSError as error:
            LOGGER.warning("cannot read {}: {}", ignore_file, error)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and _tomllib is not None:
        try:
            data = _tomllib.loads(pyproject.read_text(encoding="utf-8"))
            configured = data.get("tool", {}).get("pydup", {}).get("ignore", [])
            if isinstance(configured, list):
                patterns.extend(str(item) for item in configured)
        except Exception as error:
            LOGGER.warning("cannot parse {}: {}", pyproject, error)
    elif pyproject.is_file() and _tomllib is None:
        LOGGER.warning("tomllib/tomli unavailable; ignoring [tool.pydup] in {}", pyproject)
    return patterns


def _load_gitignore_patterns(root: Path) -> list[str]:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []
    try:
        return _strip_lines(gitignore.read_text(encoding="utf-8"))
    except OSError as error:
        LOGGER.warning("cannot read {}: {}", gitignore, error)
        return []


@dataclass(slots=True)
class _RootFilter:
    exclude: _PathMatcher
    vcs: _PathMatcher
    local: _PathMatcher

    def ignored(self, rel: str, is_dir: bool) -> bool:
        return self.exclude.matches(rel, is_dir) or self.vcs.matches(rel, is_dir) or self.local.matches(rel, is_dir)


def _build_root_filter(root: Path, cfg: ScanConfig) -> _RootFilter:
    return _RootFilter(
        exclude=_PathMatcher(cfg.exclude_patterns, gitignore_style=False),
        vcs=_PathMatcher(_load_gitignore_patterns(root), gitignore_style=True),
        local=_PathMatcher(_load_ignore_patterns(root), gitignore_style=True),
    )


def _is_candidate_file(name: str, include_notebooks: bool) -> bool:
    lowered = name.lower()
    if lowered.endswith((".py", ".pyi")):
        return True
    return include_notebooks and lowered.endswith(NOTEBOOK_SUFFIX)


def get_pyfiles(root: Path, cfg: ScanConfig) -> tuple[list[Path], list[ScanIssue]]:
    found: list[Path] = []
    issues: list[ScanIssue] = []
    root_filter = _build_root_filter(root, cfg)
    stack: list[Path] = [root]
    seen_dirs: set[tuple[int, int]] = set()
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as scanner:
                dir_entries = list(scanner)
        except OSError as error:
            issues.append((str(current), f"{type(error).__name__}: {error}"))
            continue
        for dir_entry in dir_entries:
            path = Path(current, dir_entry.name)
            rel = path.relative_to(root).as_posix() if path != root else dir_entry.name
            try:
                is_link = dir_entry.is_symlink()
                if dir_entry.is_dir(follow_symlinks=True):
                    if dir_entry.name in DEFAULT_EXCLUDE_DIRS:
                        continue
                    if is_link and not cfg.follow_symlinks:
                        LOGGER.debug("skipping symlinked directory {}", path)
                        continue
                    if root_filter.ignored(rel, True):
                        continue
                    if cfg.follow_symlinks:
                        try:
                            stat_result = dir_entry.stat(follow_symlinks=True)
                        except OSError as error:
                            issues.append((str(path), f"{type(error).__name__}: {error}"))
                            continue
                        dir_key = (stat_result.st_dev, stat_result.st_ino)
                        if dir_key in seen_dirs:
                            continue
                        seen_dirs.add(dir_key)
                    stack.append(path)
                    continue
                if is_link and not cfg.follow_symlinks:
                    if not _is_candidate_file(dir_entry.name, cfg.include_notebooks):
                        continue
                    if not Path(path).exists():
                        issues.append((str(path), "broken symlink"))
                    else:
                        LOGGER.debug("skipping symlinked file {}", path)
                    continue
                if not _is_candidate_file(dir_entry.name, cfg.include_notebooks):
                    continue
                if is_link and not Path(path).exists():
                    issues.append((str(path), "broken symlink"))
                    continue
                if root_filter.ignored(rel, False):
                    continue
                found.append(path)
            except OSError as error:
                issues.append((str(path), f"{type(error).__name__}: {error}"))
    return found, issues


def _decode_source(path: Path, raw: bytes) -> tuple[str | None, str | None]:
    if path.suffix.lower() == NOTEBOOK_SUFFIX:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as error:
            return None, f"{type(error).__name__}: {error}"
        lines: list[str] = []
        for cell in data.get("cells", []):
            if not isinstance(cell, dict) or cell.get("cell_type") != "code":
                continue
            source = cell.get("source", "")
            text = "".join(source) if isinstance(source, list) else str(source)
            for line in text.splitlines():
                if line.lstrip().startswith(("%", "!")):
                    continue
                lines.append(line)
            lines.append("")
        return "\n".join(lines), None
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        return raw.decode(encoding), None
    except Exception as error:
        return None, f"{type(error).__name__}: {error}"


def _parse_source(source: str, path: Path) -> tuple[ast.Module | None, str | None]:
    try:
        return (
            ast.parse(source, filename=str(path), type_comments=True),
            None,
        )
    except SyntaxError:
        try:
            return (
                ast.parse(source, filename=str(path), type_comments=False),
                None,
            )
        except SyntaxError as error:
            return None, f"SyntaxError: {error}"
    except Exception as error:
        return None, f"{type(error).__name__}: {error}"


def _filter_imports(tree: ast.Module) -> ast.Module:
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
    exported: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            for element in node.value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    exported.add(element.value)
    kept_imports: list[ast.stmt] = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        bindings: list[str] = []
        star = False
        if isinstance(node, ast.Import):
            for alias in node.names:
                bindings.append(alias.asname or alias.name.split(".")[0])
        else:
            for alias in node.names:
                if alias.name == "*":
                    star = True
                else:
                    bindings.append(alias.asname or alias.name)
        if star or any(name in used or name in exported for name in bindings):
            kept_imports.append(node)
    rest = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    kept_imports.sort(key=lambda stmt: ast.dump(stmt, include_attributes=False))
    tree.body = kept_imports + rest
    return tree


def _normalize_tree(tree: ast.Module, mode: str, ignore_imports: bool) -> ast.Module:
    strip_annotations = mode in ("loose", "structural")
    strip_type_nodes = mode in ("default", "loose", "structural")
    result = SourceNormalizer(strip_annotations, strip_type_nodes).visit(tree)
    if not isinstance(result, ast.Module):
        raise TypeError("normalization produced a non-module AST")
    if ignore_imports:
        result = _filter_imports(result)
    return result


def significant_tokens(source: str) -> list[str]:
    try:
        return [
            token.string
            for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type not in SKIP_TOKEN_TYPES
        ]
    except (tokenize.TokenError, SyntaxError, IndentationError, ValueError):
        return []


def _ngram_hashes(tokens: Sequence[str]) -> list[int]:
    if len(tokens) < NGRAM_SIZE:
        return []
    collected: set[int] = set()
    for index in range(len(tokens) - NGRAM_SIZE + 1):
        key = "\x1f".join(tokens[index : index + NGRAM_SIZE]).encode("utf-8", "replace")
        collected.add(int(_digest(key), 16))
    return sorted(collected)


def _tree_hash(tree: ast.Module) -> str:
    dumped = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return _digest(dumped.encode("utf-8"))


def _module_is_empty(tree: ast.Module) -> bool:
    if not tree.body:
        return True
    return len(tree.body) == 1 and isinstance(tree.body[0], ast.Pass)


def _definition_refs(tree: ast.Module, mode: str, path_text: str) -> list[DefinitionRef]:
    strip_annotations = mode in ("loose", "structural")
    strip_type_nodes = mode in ("default", "loose", "structural")
    normalizer = SourceNormalizer(strip_annotations, strip_type_nodes)
    refs: list[DefinitionRef] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body_stmts = list(node.body)
        if (
            body_stmts
            and isinstance(body_stmts[0], ast.Expr)
            and isinstance(body_stmts[0].value, ast.Constant)
            and isinstance(body_stmts[0].value.value, str)
        ):
            body_stmts = body_stmts[1:]
        if not body_stmts:
            continue
        if len(body_stmts) == 1 and isinstance(body_stmts[0], ast.Pass):
            continue
        subtree = deepcopy(node)
        normalized = normalizer.visit(subtree)
        if normalized is None:
            continue
        dumped = ast.dump(normalized, annotate_fields=True, include_attributes=False)
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        refs.append(
            DefinitionRef(
                digest=_digest(dumped.encode("utf-8")),
                path=path_text,
                lineno=node.lineno,
                name=node.name,
                kind=kind,
            )
        )
    return refs


def _hash_fuzzy(raw: bytes) -> str | None:
    if _ssdeep is None:
        return None
    try:
        return str(_ssdeep.hash(raw))
    except Exception:
        return None


def _hash_tlsh(raw: bytes) -> str | None:
    if _tlsh is None:
        return None
    try:
        value = str(_tlsh.hash(raw))
    except Exception:
        return None
    if not value or value.upper().endswith("NULL"):
        return None
    return value


def _shingle_values(tokens: Sequence[str]) -> set[int]:
    shingle_strings: list[str] = []
    if len(tokens) >= MINHASH_ROWS:
        for index in range(len(tokens) - MINHASH_ROWS + 1):
            shingle_strings.append("\x1f".join(tokens[index : index + MINHASH_ROWS]))
    elif len(tokens) >= 2:
        for index in range(len(tokens) - 1):
            shingle_strings.append("\x1f".join(tokens[index : index + 2]))
    else:
        shingle_strings.extend(tokens)
    values = {
        int.from_bytes(
            hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=16).digest(),
            "big",
        )
        for text in shingle_strings
    }
    return values


def _minhash_signature(shingles: set[int]) -> list[int] | None:
    if not shingles:
        return None
    return [
        min((coeff_a * value + coeff_b) & MASK64 for value in shingles)
        for coeff_a, coeff_b in zip(_MINHASH_A, _MINHASH_B)
    ]


def _simhash64(shingles: set[int]) -> int | None:
    if not shingles:
        return None
    counts = [0] * 64
    for value in shingles:
        feature = (value ^ (value >> 64)) & MASK64
        for bit in range(64):
            counts[bit] += 1 if (feature >> bit) & 1 else -1
    result = 0
    for bit, count in enumerate(counts):
        if count > 0:
            result |= 1 << bit
    return result


def process_file(path_text: str, cfg: WorkerConfig) -> WorkerResult:
    path = Path(path_text)
    try:
        raw = path.read_bytes()
    except Exception as error:
        return WorkerResult(path=path_text, error=f"{type(error).__name__}: {error}")
    result = _process_source(path_text, raw, cfg)
    if cfg.need_ssdeep:
        result.fuzzy = _hash_fuzzy(raw)
    if cfg.need_tlsh:
        result.tlsh_hash = _hash_tlsh(raw)
    if cfg.need_minhash or cfg.need_simhash:
        source, decode_error = _decode_source(path, raw)
        if source is not None:
            shingles = _shingle_values(significant_tokens(source))
            if cfg.need_minhash:
                result.minhash_sig = _minhash_signature(shingles)
            if cfg.need_simhash:
                result.simhash_hash = _simhash64(shingles)
    return result


def _process_source(path_text: str, raw: bytes, cfg: WorkerConfig) -> WorkerResult:
    path = Path(path_text)
    source, decode_error = _decode_source(path, raw)
    if source is None:
        return WorkerResult(path=path_text, error=decode_error or "decode failed")
    if cfg.min_lines > 0:
        line_count = sum(1 for line in source.splitlines() if line.strip())
        if line_count < cfg.min_lines:
            return WorkerResult(path=path_text, skipped="min-lines")
    if cfg.skip_empty and cfg.mode == "strict" and not raw.strip():
        return WorkerResult(path=path_text, skipped="empty")
    tokens: list[str] | None = None
    if cfg.need_ngrams or cfg.mode == "structural":
        tokens = significant_tokens(source)
    ngrams = _ngram_hashes(tokens) if cfg.need_ngrams and tokens is not None else None
    tree: ast.Module | None = None
    if cfg.mode != "strict" or cfg.need_definitions:
        tree, parse_error = _parse_source(source, path)
        if tree is None:
            if cfg.mode == "strict":
                LOGGER.debug("definitions unavailable for {}: {}", path_text, parse_error)
            else:
                return WorkerResult(path=path_text, error=parse_error, ngrams=ngrams)
    ast_hash: str | None = None
    if tree is not None and cfg.mode != "strict":
        normalized = _normalize_tree(tree, cfg.mode, cfg.ignore_imports)
        if cfg.skip_empty and _module_is_empty(normalized):
            return WorkerResult(path=path_text, skipped="empty")
        ast_hash = _tree_hash(normalized)
    token_hash: str | None = None
    if tokens is not None and cfg.mode == "structural":
        token_hash = _digest("\x1f".join(tokens).encode("utf-8", "replace"))
    definitions: list[DefinitionRef] | None = None
    if cfg.need_definitions and tree is not None:
        definitions = _definition_refs(tree, cfg.mode, path_text)
    if cfg.mode == "strict":
        digest = _digest(raw)
    else:
        digest = ast_hash
    return WorkerResult(
        path=path_text,
        digest=digest,
        ast_hash=ast_hash,
        token_hash=token_hash,
        ngrams=ngrams,
        definitions=definitions,
    )


def _print_progress_line(message: str, previous_width: int) -> int:
    sys.stderr.write("\r" + message.ljust(previous_width))
    sys.stderr.flush()
    return len(message)


def _notify(on_complete: Callable[[], None] | None, count: int = 1) -> None:
    if on_complete is None:
        return
    for _ in range(count):
        on_complete()


def _run_serial(
    paths: Sequence[str],
    cfg: WorkerConfig,
    on_complete: Callable[[], None] | None,
) -> list[WorkerResult]:
    results: list[WorkerResult] = []
    for path_text in paths:
        results.append(process_file(path_text, cfg))
        _notify(on_complete)
    return results


def _executor_class(method: str) -> type:
    if method == "cf.ThreadPoolExecutor":
        if _ThreadPoolExecutor is None:
            raise RuntimeError("ThreadPoolExecutor is unavailable")
        return _ThreadPoolExecutor
    if method == "cf.InterpreterPoolExecutor":
        if _InterpreterPoolExecutor is None:
            raise RuntimeError("--mp-method cf.InterpreterPoolExecutor requires Python 3.14+")
        return _InterpreterPoolExecutor
    return ProcessPoolExecutor


def _run_executor(
    executor_cls: type,
    paths: Sequence[str],
    cfg: WorkerConfig,
    workers: int,
    on_complete: Callable[[], None] | None,
) -> list[WorkerResult]:
    results: list[WorkerResult] = []
    executor = executor_cls(max_workers=workers)
    try:
        futures = [executor.submit(process_file, path_text, cfg) for path_text in paths]
        for future in as_completed(futures):
            results.append(future.result())
            _notify(on_complete)
    except BaseException:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    return results


def _run_mp_pool(
    paths: Sequence[str],
    cfg: WorkerConfig,
    workers: int,
    operation: str,
    on_complete: Callable[[], None] | None,
) -> list[WorkerResult]:
    import multiprocessing

    results: list[WorkerResult] = []
    pool = multiprocessing.Pool(processes=workers)
    worker = functools.partial(process_file, cfg=cfg)
    try:
        if operation == "map":
            results = list(pool.map(worker, paths))
            _notify(on_complete, len(results))
        elif operation == "imap":
            for item in pool.imap(worker, paths):
                results.append(item)
                _notify(on_complete)
        elif operation == "imap_unordered":
            for item in pool.imap_unordered(worker, paths):
                results.append(item)
                _notify(on_complete)
        elif operation == "starmap":
            results = list(pool.starmap(process_file, [(path, cfg) for path in paths]))
            _notify(on_complete, len(results))
        elif operation == "starmap_async":
            results = list(pool.starmap_async(process_file, [(path, cfg) for path in paths]).get())
            _notify(on_complete, len(results))
        elif operation == "map_async":
            results = list(pool.map_async(worker, paths).get())
            _notify(on_complete, len(results))
        elif operation == "apply":
            for path_text in paths:
                results.append(pool.apply(process_file, (path_text, cfg)))
                _notify(on_complete)
        elif operation == "apply_async":
            pending = [pool.apply_async(process_file, (path_text, cfg)) for path_text in paths]
            for async_result in pending:
                results.append(async_result.get())
                _notify(on_complete)
        else:
            raise ValueError(f"unknown mp.pool operation: {operation}")
    except BaseException:
        pool.terminate()
        raise
    else:
        pool.close()
    finally:
        pool.join()
    return results


def _run_joblib(
    paths: Sequence[str],
    cfg: WorkerConfig,
    workers: int,
    on_complete: Callable[[], None] | None,
) -> list[WorkerResult]:
    try:
        from joblib import Parallel, delayed
    except ImportError as error:
        raise RuntimeError("--mp-method joblib requires the joblib package (pip install joblib)") from error
    absolute_paths = [Path(path_text).absolute() for path_text in paths]
    results = list(
        Parallel(n_jobs=workers, prefer="processes")(
            delayed(process_file)(abs_path, cfg) for abs_path in absolute_paths
        )
    )
    for original_path, result in zip(paths, results):
        result.path = original_path
    _notify(on_complete, len(results))
    return results


def _run_jobs(
    paths: Sequence[str],
    cfg: WorkerConfig,
    workers: int,
    method: str,
    on_complete: Callable[[], None] | None = None,
) -> list[WorkerResult]:
    if workers <= 1 and method == "cf.ProcessPoolExecutor":
        return _run_serial(paths, cfg, on_complete)
    if method.startswith("mp.pool."):
        return _run_mp_pool(paths, cfg, workers, method[len("mp.pool.") :], on_complete)
    if method == "joblib":
        return _run_joblib(paths, cfg, workers, on_complete)
    return _run_executor(_executor_class(method), paths, cfg, workers, on_complete)


def process_files(
    entries: Sequence[FileEntry],
    cfg: WorkerConfig,
    cache: dict[str, CacheEntry],
    use_cache: bool,
    workers: int,
    show_progress: bool,
    method: str = "cf.ProcessPoolExecutor",
) -> tuple[list[WorkerResult], int]:
    pending: list[FileEntry] = []
    results: list[WorkerResult] = []
    cache_hits = 0
    for entry in entries:
        key = Path(str(entry.path)).absolute()
        cached = cache.get(key) if use_cache else None
        if (
            cached is not None
            and cached.mtime_ns == entry.mtime_ns
            and cached.size == entry.size
            and cached.digest
            and (not cfg.need_ssdeep or cached.fuzzy is not None)
            and (not cfg.need_tlsh or cached.tlsh_hash is not None)
            and (not cfg.need_minhash or cached.minhash_sig is not None)
            and (not cfg.need_simhash or cached.simhash_hash is not None)
        ):
            results.append(
                WorkerResult(
                    path=str(entry.path),
                    digest=cached.digest,
                    ast_hash=cached.ast_hash,
                    token_hash=cached.token_hash,
                    fuzzy=cached.fuzzy,
                    tlsh_hash=cached.tlsh_hash,
                    minhash_sig=cached.minhash_sig,
                    simhash_hash=cached.simhash_hash,
                    from_cache=True,
                )
            )
            cache_hits += 1
        else:
            pending.append(entry)
    if not pending:
        return results, cache_hits
    total = len(pending)
    available = os.cpu_count() or 1
    worker_count = max(1, min(total, workers if workers > 0 else available))
    path_texts = [str(entry.path) for entry in pending]
    if not show_progress:
        results.extend(_run_jobs(path_texts, cfg, worker_count, method))
        return results, cache_hits
    try:
        from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
    except ImportError:
        LOGGER.warning("rich is unavailable; falling back to basic progress output")
        if not sys.stderr.isatty():
            results.extend(_run_jobs(path_texts, cfg, worker_count, method))
            return results, cache_hits
        completed = 0
        width = 0

        def update_progress() -> None:
            nonlocal completed, width
            completed += 1
            width = _print_progress_line(f"Processing Python files: {completed}/{total}", width)

        try:
            results.extend(_run_jobs(path_texts, cfg, worker_count, method, update_progress))
        finally:
            sys.stderr.write("\n")
            sys.stderr.flush()
        return results, cache_hits
    from rich.console import Console as RichConsole

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=RichConsole(stderr=True),
    ) as progress:
        task_id = progress.add_task("Processing Python files", total=total)
        results.extend(
            _run_jobs(
                path_texts,
                cfg,
                worker_count,
                method,
                lambda: progress.update(task_id, advance=1),
            )
        )
    return results, cache_hits


def _fingerprint_key(result: WorkerResult, mode: str) -> str | None:
    if mode == "structural":
        if result.ast_hash is None or result.token_hash is None:
            return None
        return f"{result.ast_hash}:{result.token_hash}"
    return result.digest


def _union_find(size: int) -> list[int]:
    return list(range(size))


def _find(parent: list[int], node: int) -> int:
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node


def _union(parent: list[int], left: int, right: int) -> None:
    root_left = _find(parent, left)
    root_right = _find(parent, right)
    if root_left != root_right:
        parent[root_right] = root_left


def _verify_candidates(
    candidate_paths: set[str],
    results_by_path: dict[str, WorkerResult],
    entries: dict[str, FileEntry],
    cfg: WorkerConfig,
) -> tuple[dict[str, WorkerResult], list[Failure]]:
    verified: dict[str, WorkerResult] = {}
    failures: list[Failure] = []
    for path_text in sorted(candidate_paths):
        entry = entries.get(path_text)
        original = results_by_path[path_text]
        if entry is None:
            verified[path_text] = original
            continue
        try:
            stat_result = os.stat(path_text)
        except OSError as error:
            failures.append((path_text, f"changed during run: {type(error).__name__}: {error}"))
            continue
        if (
            stat_result.st_size != entry.size
            or stat_result.st_mtime_ns != entry.mtime_ns
            or stat_result.st_ino != entry.ino
        ):
            failures.append((path_text, "changed during run: file modified after scan"))
            continue
        fresh = process_file(path_text, cfg)
        if fresh.error is not None:
            failures.append((path_text, fresh.error))
            continue
        if fresh.skipped is not None:
            failures.append((path_text, f"changed during run: now skipped ({fresh.skipped})"))
            continue
        if _fingerprint_key(fresh, cfg.mode) != _fingerprint_key(original, cfg.mode):
            failures.append((path_text, "changed during run: fingerprint mismatch"))
            continue
        verified[path_text] = fresh
    return verified, failures


def _build_groups(
    results: list[WorkerResult],
    entries: dict[str, FileEntry],
    mode: str,
    cfg: WorkerConfig,
) -> tuple[list[DuplicateGroup], list[Failure]]:
    usable = [
        result
        for result in results
        if result.error is None and result.skipped is None and _fingerprint_key(result, mode) is not None
    ]
    results_by_path = {result.path: result for result in usable}
    buckets: dict[str, list[str]] = defaultdict(list)
    for result in usable:
        key = _fingerprint_key(result, mode)
        if key is not None:
            buckets[key].append(result.path)
    candidate_paths: set[str] = set()
    if mode == "structural":
        ast_buckets: dict[str, list[str]] = defaultdict(list)
        token_buckets: dict[str, list[str]] = defaultdict(list)
        for result in usable:
            if result.ast_hash is not None:
                ast_buckets[result.ast_hash].append(result.path)
            if result.token_hash is not None:
                token_buckets[result.token_hash].append(result.path)
        for bucket_map in (ast_buckets, token_buckets):
            for members in bucket_map.values():
                if len(members) > 1:
                    candidate_paths.update(members)
    else:
        for members in buckets.values():
            if len(members) > 1:
                candidate_paths.update(members)
    verified: dict[str, WorkerResult] = dict(results_by_path)
    failures: list[Failure] = []
    if candidate_paths:
        verified, failures = _verify_candidates(candidate_paths, results_by_path, entries, cfg)
    kept_results = [
        verified.get(result.path, result)
        for result in usable
        if result.path in verified or result.path not in candidate_paths
    ]
    groups: list[DuplicateGroup] = []
    if mode == "structural":
        index_by_path = {result.path: index for index, result in enumerate(kept_results)}
        parent = _union_find(len(kept_results))
        ast_buckets = defaultdict(list)
        token_buckets = defaultdict(list)
        for result in kept_results:
            if result.ast_hash is not None:
                ast_buckets[result.ast_hash].append(result.path)
            if result.token_hash is not None:
                token_buckets[result.token_hash].append(result.path)
        for bucket_map in (ast_buckets, token_buckets):
            for members in bucket_map.values():
                if len(members) < 2:
                    continue
                anchor = index_by_path[members[0]]
                for member in members[1:]:
                    _union(parent, anchor, index_by_path[member])
        components: dict[int, list[str]] = defaultdict(list)
        for result in kept_results:
            components[_find(parent, index_by_path[result.path])].append(result.path)
        for members in components.values():
            if len(members) < 2:
                continue
            sorted_paths = sorted(members)
            group_digest = _digest(
                "|".join(
                    sorted(f"{verified[path].ast_hash}:{verified[path].token_hash}" for path in sorted_paths)
                ).encode("utf-8")
            )
            groups.append(_make_group(group_digest, sorted_paths, entries))
    else:
        fresh_buckets: dict[str, list[str]] = defaultdict(list)
        for result in kept_results:
            key = _fingerprint_key(result, mode)
            if key is not None:
                fresh_buckets[key].append(result.path)
        for key, members in fresh_buckets.items():
            if len(members) > 1:
                groups.append(_make_group(key, sorted(members), entries))
    groups.sort(key=lambda group: str(group.paths[0]))
    return groups, failures


def _make_group(digest: str, members: Sequence[str], entries: dict[str, FileEntry]) -> DuplicateGroup:
    paths = [Path(member) for member in members]
    sizes: dict[str, int] = {}
    for member in members:
        entry = entries.get(member)
        sizes[member] = entry.size if entry is not None else 0
    return DuplicateGroup(digest=digest, paths=paths, sizes=sizes)


def _build_definition_groups(results: Sequence[WorkerResult]) -> list[DefinitionGroup]:
    buckets: dict[str, list[DefinitionRef]] = defaultdict(list)
    for result in results:
        for ref in result.definitions or []:
            buckets[ref.digest].append(ref)
    groups: list[DefinitionGroup] = []
    for digest, members in buckets.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda ref: (ref.path, ref.lineno))
        groups.append(DefinitionGroup(digest=digest, members=members))
    groups.sort(key=lambda group: (group.members[0].path, group.members[0].lineno))
    return groups


def _build_near_pairs(
    results: Sequence[WorkerResult],
    threshold: float,
    exact_pairs: set[frozenset[str]],
) -> list[NearPair]:
    items: list[tuple[str, set[int]]] = []
    for result in results:
        if result.ngrams is not None and len(result.ngrams) >= NGRAM_SIZE:
            items.append((result.path, set(result.ngrams)))
    items.sort(key=lambda item: len(item[1]))
    pairs: list[NearPair] = []
    for left_index in range(len(items)):
        left_path, left_set = items[left_index]
        for right_index in range(left_index + 1, len(items)):
            right_path, right_set = items[right_index]
            if len(left_set) / len(right_set) < threshold:
                break
            pair_key = frozenset((left_path, right_path))
            if pair_key in exact_pairs:
                continue
            union = len(left_set | right_set)
            if union == 0:
                continue
            similarity = len(left_set & right_set) / union
            if similarity >= threshold:
                pairs.append(
                    NearPair(
                        paths=(left_path, right_path),
                        similarity=round(similarity, 4),
                    )
                )
    pairs.sort(key=lambda pair: (-pair.similarity, pair.paths))
    return pairs


def _sized_results(
    results: Sequence[WorkerResult],
    entries: dict[str, FileEntry],
) -> list[tuple[str, WorkerResult, int]]:
    items: list[tuple[str, WorkerResult, int]] = []
    for result in results:
        entry = entries.get(result.path)
        if entry is not None:
            items.append((result.path, result, entry.size))
    items.sort(key=lambda item: item[2])
    return items


def _ssdeep_pairs(
    results: Sequence[WorkerResult],
    entries: dict[str, FileEntry],
    threshold: int,
) -> list[SimilarPair]:
    if _ssdeep is None:
        return []
    buckets: dict[str, list[WorkerResult]] = defaultdict(list)
    for result in results:
        if result.fuzzy:
            buckets[result.fuzzy.split(":", 1)[0]].append(result)
    pairs: list[SimilarPair] = []
    for members in buckets.values():
        for left_index in range(len(members)):
            for right_index in range(left_index + 1, len(members)):
                left = members[left_index]
                right = members[right_index]
                left_entry = entries.get(left.path)
                right_entry = entries.get(right.path)
                if left_entry is not None and right_entry is not None:
                    low_size = min(left_entry.size, right_entry.size)
                    high_size = max(left_entry.size, right_entry.size)
                    if high_size > 0 and low_size / high_size < 0.5:
                        continue
                try:
                    score = int(_ssdeep.compare(left.fuzzy, right.fuzzy))
                except Exception:
                    continue
                if score > threshold:
                    pairs.append(
                        SimilarPair(
                            paths=tuple(sorted((left.path, right.path))),
                            score=score,
                            method="ssdeep",
                        )
                    )
    return pairs


def _sweep_pairs(
    results: Sequence[WorkerResult],
    entries: dict[str, FileEntry],
    max_ratio: float,
) -> list[tuple[WorkerResult, WorkerResult]]:
    items = _sized_results(results, entries)
    pairs: list[tuple[WorkerResult, WorkerResult]] = []
    for left_index in range(len(items)):
        for right_index in range(left_index + 1, len(items)):
            left_size = items[left_index][2]
            right_size = items[right_index][2]
            if left_size <= 0 or right_size / left_size > max_ratio:
                break
            pairs.append((items[left_index][1], items[right_index][1]))
    return pairs


def _tlsh_pairs(
    results: Sequence[WorkerResult],
    entries: dict[str, FileEntry],
    threshold: int,
) -> list[SimilarPair]:
    if _tlsh is None:
        return []
    pairs: list[SimilarPair] = []
    for left, right in _sweep_pairs(results, entries, 2.0):
        if not left.tlsh_hash or not right.tlsh_hash:
            continue
        try:
            diff = int(_tlsh.diff(left.tlsh_hash, right.tlsh_hash))
        except Exception:
            continue
        score = 100 - diff
        if score > threshold:
            pairs.append(
                SimilarPair(
                    paths=tuple(sorted((left.path, right.path))),
                    score=score,
                    method="tlsh",
                )
            )
    return pairs


def _simhash_pairs(
    results: Sequence[WorkerResult],
    entries: dict[str, FileEntry],
    threshold: int,
) -> list[SimilarPair]:
    pairs: list[SimilarPair] = []
    for left, right in _sweep_pairs(results, entries, 4.0):
        if left.simhash_hash is None or right.simhash_hash is None:
            continue
        distance = (left.simhash_hash ^ right.simhash_hash).bit_count()
        score = round(100 * (64 - distance) / 64)
        if score > threshold:
            pairs.append(
                SimilarPair(
                    paths=tuple(sorted((left.path, right.path))),
                    score=score,
                    method="simhash",
                )
            )
    return pairs


def _minhash_pairs(
    results: Sequence[WorkerResult],
    threshold: int,
) -> list[SimilarPair]:
    signatures = {result.path: result.minhash_sig for result in results if result.minhash_sig is not None}
    band_buckets: dict[tuple[int, ...], list[str]] = defaultdict(list)
    for path, signature in signatures.items():
        for band_index in range(MINHASH_BANDS):
            start = band_index * MINHASH_ROWS
            key = tuple(signature[start : start + MINHASH_ROWS])
            band_buckets[(band_index, *key)].append(path)
    candidates: set[tuple[str, str]] = set()
    for members in band_buckets.values():
        if len(members) < 2:
            continue
        ordered = sorted(set(members))
        for left_index in range(len(ordered)):
            for right_index in range(left_index + 1, len(ordered)):
                candidates.add((ordered[left_index], ordered[right_index]))
    pairs: list[SimilarPair] = []
    for left_path, right_path in sorted(candidates):
        left_sig = signatures[left_path]
        right_sig = signatures[right_path]
        if len(left_sig) != len(right_sig):
            continue
        matching = sum(1 for a, b in zip(left_sig, right_sig) if a == b)
        score = round(100 * matching / len(left_sig))
        if score > threshold:
            pairs.append(SimilarPair(paths=(left_path, right_path), score=score, method="minhash"))
    return pairs


def _build_similar_pairs(
    results: Sequence[WorkerResult],
    entries: dict[str, FileEntry],
    methods: Sequence[str],
    threshold: int,
) -> list[SimilarPair]:
    pairs: list[SimilarPair] = []
    if "ssdeep" in methods:
        pairs.extend(_ssdeep_pairs(results, entries, threshold))
    if "tlsh" in methods:
        pairs.extend(_tlsh_pairs(results, entries, threshold))
    if "minhash" in methods:
        pairs.extend(_minhash_pairs(results, threshold))
    if "simhash" in methods:
        pairs.extend(_simhash_pairs(results, entries, threshold))
    pairs.sort(key=lambda pair: (-pair.score, pair.method, pair.paths))
    return pairs


def _write_simz(
    path: Path,
    pairs: Sequence[SimilarPair],
    methods: Sequence[str],
) -> None:
    payload = {
        "version": 2,
        "threshold": SIMILARITY_THRESHOLD,
        "generated": datetime.now(timezone.utc).isoformat(),
        "methods": list(methods),
        "pairs": [{"method": pair.method, "paths": list(pair.paths), "score": pair.score} for pair in pairs],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_cache(cache_path: Path, mode_key: str) -> dict[str, CacheEntry]:
    if not cache_path.is_file():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as error:
        LOGGER.warning("ignoring unreadable cache {}: {}", cache_path, error)
        return {}
    if data.get("version") != CACHE_VERSION or data.get("mode") != mode_key:
        LOGGER.debug("cache {} is stale; rebuilding", cache_path)
        return {}
    entries: dict[str, CacheEntry] = {}
    raw_entries = data.get("entries", {})
    if isinstance(raw_entries, dict):
        for key, value in raw_entries.items():
            try:
                entries[str(key)] = CacheEntry(
                    mtime_ns=int(value["mtime_ns"]),
                    size=int(value["size"]),
                    digest=str(value["digest"]),
                    ast_hash=value.get("ast_hash"),
                    token_hash=value.get("token_hash"),
                    fuzzy=value.get("fuzzy"),
                    tlsh_hash=value.get("tlsh_hash"),
                    minhash_sig=value.get("minhash_sig"),
                    simhash_hash=value.get("simhash_hash"),
                )
            except (KeyError, TypeError, ValueError):
                continue
    return entries


def _save_cache(
    cache_path: Path,
    mode_key: str,
    cache: dict[str, CacheEntry],
    entries: dict[str, FileEntry],
    results: Sequence[WorkerResult],
) -> None:
    for result in results:
        if result.digest is None or result.from_cache:
            continue
        entry = entries.get(result.path)
        if entry is None:
            continue
        cache[Path(result.path).absolute()] = CacheEntry(
            mtime_ns=entry.mtime_ns,
            size=entry.size,
            digest=result.digest,
            ast_hash=result.ast_hash,
            token_hash=result.token_hash,
            fuzzy=result.fuzzy,
            tlsh_hash=result.tlsh_hash,
            minhash_sig=result.minhash_sig,
            simhash_hash=result.simhash_hash,
        )
    for key in [key for key in cache if not Path(key).exists()]:
        del cache[key]
    payload = {
        "version": CACHE_VERSION,
        "mode": mode_key,
        "entries": {
            key: {
                "mtime_ns": value.mtime_ns,
                "size": value.size,
                "digest": value.digest,
                "ast_hash": value.ast_hash,
                "token_hash": value.token_hash,
                "fuzzy": value.fuzzy,
                "tlsh_hash": value.tlsh_hash,
                "minhash_sig": value.minhash_sig,
                "simhash_hash": value.simhash_hash,
            }
            for key, value in cache.items()
        },
    }
    try:
        temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temp_path, cache_path)
    except OSError as error:
        LOGGER.warning("cannot write cache {}: {}", cache_path, error)


def _nonneg_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from error
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


MP_METHOD_CANONICAL: tuple[str, ...] = (
    "cf.ProcessPoolExecutor",
    "cf.ThreadPoolExecutor",
    "cf.InterpreterPoolExecutor",
    "mp.pool.map",
    "mp.pool.imap",
    "mp.pool.imap_unordered",
    "mp.pool.apply",
    "mp.pool.apply_async",
    "mp.pool.map_async",
    "mp.pool.starmap",
    "mp.pool.starmap_async",
    "joblib",
)
_MP_METHOD_ALIASES: dict[str, str] = {canonical.lower(): canonical for canonical in MP_METHOD_CANONICAL}
for _mp_name in MP_METHOD_CANONICAL:
    if _mp_name.startswith("mp.pool."):
        _MP_METHOD_ALIASES["mp." + _mp_name.split("mp.pool.")[1]] = _mp_name
_MP_METHOD_ALIASES.update({
    "cf.process": "cf.ProcessPoolExecutor",
    "cf.thread": "cf.ThreadPoolExecutor",
    "cf.interpreter": "cf.InterpreterPoolExecutor",
    "cf.proceszpoolexecutor": "cf.ProcessPoolExecutor",
    "processpoolexecutor": "cf.ProcessPoolExecutor",
    "threadpoolexecutor": "cf.ThreadPoolExecutor",
    "interpreterpoolexecutor": "cf.InterpreterPoolExecutor",
    "mp.pool.starmao_async": "mp.pool.starmap_async",
    "mp.pool.imap_async": "mp.pool.imap_unordered",
    "joblib.parallel": "joblib",
})


def _mp_method_value(value: str) -> str:
    canonical = _MP_METHOD_ALIASES.get(value.strip().lower())
    if canonical is None:
        raise argparse.ArgumentTypeError(f"unknown mp method {value!r}; available: " + ", ".join(MP_METHOD_CANONICAL))
    return canonical


def _similarity_value(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a float in (0, 1]") from error
    if not 0.0 < number <= 1.0:
        raise argparse.ArgumentTypeError("must be a float in (0, 1]")
    return number


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pydup",
        description="Find duplicate Python files, definitions, and near-duplicates using normalized AST fingerprints.",
        epilog="Exit codes: 0 success, 1 with --fail-on-dupes/--fail-on-error conditions, 2 fatal errors, 130 interrupted.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="files or directories to scan; '-' reads newline-separated paths from stdin",
    )
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        default=None,
        metavar="DIR",
        help="directory root to scan (repeatable, enables cross-project comparison)",
    )
    parser.add_argument(
        "-r",
        "--remove",
        action="store_true",
        help="remove duplicate files (requires confirmation unless -y)",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="skip interactive confirmation prompts")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print a verbose deletion plan (keepers, removals, sizes, totals) without removing anything",
    )
    parser.add_argument(
        "-s",
        "--save-similar",
        action="store_true",
        dest="save_similar",
        help="compare ssdeep fuzzy hashes of all scanned files and save pairs scoring above 70 to simz.json",
    )
    parser.add_argument(
        "--tlsh",
        action="store_true",
        help="also compare TLSH fuzzy hashes and include pairs scoring above 70 in simz.json",
    )
    parser.add_argument(
        "--minhash",
        action="store_true",
        help="also compare 128-bit MinHash signatures (LSH-banded token shingles) and include pairs scoring above 70 in simz.json",
    )
    parser.add_argument(
        "--simhash",
        action="store_true",
        help="also compare 64-bit SimHash fingerprints (token shingles) and include pairs scoring above 70 in simz.json",
    )
    parser.add_argument(
        "--mp-method",
        type=_mp_method_value,
        default="cf.ProcessPoolExecutor",
        metavar="NAME",
        help=(
            "parallel engine: cf.{Process,Thread,Interpreter}PoolExecutor (default), "
            "mp.pool.{map,imap,imap_unordered,apply,apply_async,map_async,starmap,starmap_async}, joblib"
        ),
    )
    trash_group = parser.add_mutually_exclusive_group()
    trash_group.add_argument(
        "--to-trash",
        action="store_true",
        help="send duplicates to the system trash via send2trash instead of unlinking",
    )
    trash_group.add_argument(
        "--quarantine",
        type=Path,
        metavar="DIR",
        help="move duplicates into DIR instead of unlinking them",
    )
    parser.add_argument(
        "--keep",
        choices=("first", "oldest", "newest", "shortest", "longest", "pattern", "prefer"),
        default="first",
        help="keeper selection policy for each duplicate group (default: first)",
    )
    parser.add_argument("--pattern", metavar="GLOB", help="keeper glob for --keep pattern")
    parser.add_argument("--prefer", metavar="PREFIX", help="keeper path prefix for --keep prefer")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="exclude paths matching GLOB (repeatable)",
    )
    parser.add_argument(
        "--follow-symlinks",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="follow symlinks while scanning (default: --no-follow-symlinks)",
    )
    parser.add_argument(
        "--mode",
        choices=("strict", "default", "loose", "structural"),
        default="loose",
        help="normalization level: strict (raw bytes), default (docstrings stripped), loose (also annotations/types), structural (AST + token stream)",
    )
    parser.add_argument(
        "--similarity",
        type=_similarity_value,
        default=None,
        metavar="FLOAT",
        help="report near-duplicate pairs with token n-gram Jaccard >= FLOAT, e.g. 0.9",
    )
    parser.add_argument(
        "--functions",
        action="store_true",
        help="report duplicate functions and classes within and across files",
    )
    parser.add_argument(
        "--ignore-imports",
        action="store_true",
        help="treat files differing only by import order or unused imports as duplicates",
    )
    parser.add_argument(
        "--changed-since",
        metavar="REF",
        help="only scan files changed since the given git ref",
    )
    parser.add_argument(
        "--notebooks",
        action="store_true",
        help="include .ipynb notebooks (code cells are extracted)",
    )
    parser.add_argument(
        "--min-lines", type=_nonneg_int, default=0, metavar="N", help="skip files with fewer than N non-blank lines"
    )
    parser.add_argument("--min-bytes", type=_nonneg_int, default=0, metavar="N", help="skip files smaller than N bytes")
    parser.add_argument("--skip-empty", action="store_true", help="skip files that normalize to an empty module")
    parser.add_argument(
        "--fail-on-dupes",
        "--exit-code",
        action="store_true",
        dest="fail_on_dupes",
        help="exit with code 1 when duplicate groups are found",
    )
    parser.add_argument("--fail-on-error", action="store_true", help="exit with code 1 when files fail to parse")
    parser.add_argument(
        "--format",
        choices=("text", "json", "ndjson", "csv"),
        default="text",
        dest="output_format",
        help="report format (default: text)",
    )
    parser.add_argument("--rich", action="store_true", help="show rich progress while processing")
    parser.add_argument("--no-cache", action="store_true", help="disable the .pydup-cache.json fingerprint cache")
    parser.add_argument(
        "--baseline", type=Path, metavar="FILE", help="only report duplicate groups not present in FILE"
    )
    parser.add_argument("--print-keep", action="store_true", help="print only keeper paths, one per line")
    parser.add_argument(
        "--manifest", type=Path, metavar="FILE", help="write a machine-readable deletion manifest to FILE"
    )
    parser.add_argument(
        "--diff", nargs=2, type=Path, metavar=("A", "B"), help="print a unified diff of two normalized files and exit"
    )
    parser.add_argument("--stats", action="store_true", help="print a run summary at the end")
    parser.add_argument("--watch", action="store_true", help="watch for changes and rescan continuously (report-only)")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="count", default=0, help="increase log verbosity (repeatable)")
    verbosity.add_argument("-q", "--quiet", action="store_true", help="only log errors")
    parser.add_argument("--log-format", choices=("text", "json"), default="text", help="log line format on stderr")
    parser.add_argument("--version", action="version", version=f"pydup {__version__}")
    return parser.parse_args(argv)


def _configure_logging(verbose: int, quiet: bool, log_format: str) -> None:
    if quiet:
        level = "ERROR"
    elif verbose >= 2:
        level = "DEBUG"
    elif verbose == 1:
        level = "INFO"
    else:
        level = "WARNING"
    logger.remove()
    logger.add(sys.stderr, level=level, serialize=log_format == "json")


def _human_bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _group_wasted(group: DuplicateGroup) -> int:
    if group.keeper is None:
        return sum(group.sizes.values())
    keeper_size = group.sizes.get(str(group.keeper), 0)
    return max(0, sum(group.sizes.values()) - keeper_size)


def _choose_keeper(
    group: DuplicateGroup,
    entries: dict[str, FileEntry],
    policy: str,
    pattern: str | None,
    prefer: str | None,
) -> Path:
    paths = list(group.paths)

    def path_key(path: Path) -> tuple[int, str]:
        entry = entries.get(str(path))
        return (entry.mtime_ns if entry is not None else 0, str(path))

    def variants(path: Path) -> list[str]:
        text = str(path)
        parts = Path(text).parts
        suffixes = ["/".join(parts[index:]) for index in range(1, len(parts))]
        return [text, path.name, *suffixes]

    if policy == "oldest":
        return min(paths, key=path_key)
    if policy == "newest":
        return max(paths, key=path_key)
    if policy == "shortest":
        return min(paths, key=lambda path: (len(str(path)), str(path)))
    if policy == "longest":
        return max(paths, key=lambda path: (len(str(path)), str(path)))
    if policy == "pattern":
        if pattern is None:
            return min(paths, key=str)
        matches = [path for path in paths if any(fnmatch.fnmatch(candidate, pattern) for candidate in variants(path))]
        if matches:
            return min(matches, key=str)
        LOGGER.warning("--pattern {} matched no member of group {}; keeping first path", pattern, group.digest[:12])
        return min(paths, key=str)
    if policy == "prefer":
        if prefer is None:
            return min(paths, key=str)
        matches = [path for path in paths if any(candidate.startswith(prefer) for candidate in variants(path))]
        if matches:
            return min(matches, key=str)
        LOGGER.warning("--prefer {} matched no member of group {}; keeping first path", prefer, group.digest[:12])
        return min(paths, key=str)
    return min(paths, key=str)


def _assign_keepers(
    groups: Sequence[DuplicateGroup],
    entries: dict[str, FileEntry],
    policy: str,
    pattern: str | None,
    prefer: str | None,
) -> None:
    for group in groups:
        group.keeper = _choose_keeper(group, entries, policy, pattern, prefer)
        group.paths.sort(key=lambda path: (str(path) != str(group.keeper), str(path)))


def _load_baseline(path: Path) -> set[tuple[str, ...]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"cannot read baseline {path}: {error}") from error
    groups = data.get("groups", [])
    known: set[tuple[str, ...]] = set()
    for group in groups:
        paths = group.get("paths") if isinstance(group, dict) else None
        if isinstance(paths, list):
            known.add(tuple(sorted(str(item) for item in paths)))
    return known


def _filter_baseline(
    groups: list[DuplicateGroup],
    baseline: set[tuple[str, ...]],
) -> tuple[list[DuplicateGroup], int]:
    fresh: list[DuplicateGroup] = []
    suppressed = 0
    for group in groups:
        key = tuple(sorted(str(path) for path in group.paths))
        if key in baseline:
            suppressed += 1
        else:
            fresh.append(group)
    return fresh, suppressed


def _changed_since_paths(roots: Sequence[Path], ref: str) -> set[str] | None:
    changed: set[str] = set()
    repositories = 0
    commands = (
        ["git", "diff", "--name-only", "--diff-filter=d", "-z", ref, "--", "."],
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", "."],
    )
    for root in roots:
        if not root.is_dir():
            continue
        for command in commands:
            try:
                proc = subprocess.run(
                    command,
                    cwd=str(root),
                    capture_output=True,
                    check=False,
                )
            except OSError as error:
                LOGGER.error("git unavailable: {}", error)
                return None
            if proc.returncode != 0:
                stderr_text = proc.stderr.decode("utf-8", errors="replace").strip()
                LOGGER.error("git command failed in {}: {}", root, stderr_text or f"exit {proc.returncode}")
                return None
            repositories += 1
            for raw_path in proc.stdout.split(b"\0"):
                if not raw_path:
                    continue
                rel = raw_path.decode("utf-8", errors="replace")
                changed.add(Path(Path(str(root)).joinpath(rel)).absolute())
    if repositories == 0:
        LOGGER.error("--changed-since requires at least one directory root inside a git repository")
        return None
    return changed


def _stat_entry(path: Path) -> tuple[FileEntry | None, str | None]:
    try:
        stat_result = os.stat(path)
    except FileNotFoundError:
        if path.is_symlink():
            return None, "broken symlink"
        return None, "missing file"
    except OSError as error:
        return None, f"{type(error).__name__}: {error}"
    return (
        FileEntry(
            path=path,
            size=stat_result.st_size,
            mtime_ns=stat_result.st_mtime_ns,
            ino=stat_result.st_ino,
            dev=stat_result.st_dev,
        ),
        None,
    )


def _finalize_entries(
    candidates: Iterable[Path],
    cfg: ScanConfig,
    seen_inodes: dict[tuple[int, int], str],
    issues: list[ScanIssue],
    enforce_suffix: bool,
) -> tuple[list[FileEntry], int]:
    entries: list[FileEntry] = []
    skipped = 0
    for path in candidates:
        if enforce_suffix and not _is_candidate_file(path.name, cfg.include_notebooks):
            continue
        entry, error = _stat_entry(path)
        if entry is None:
            issues.append((str(path), error or "unreadable"))
            continue
        if cfg.min_bytes and entry.size < cfg.min_bytes:
            skipped += 1
            continue
        inode_key = (entry.dev, entry.ino)
        if inode_key in seen_inodes:
            LOGGER.debug("skipping {}: same inode as {}", path, seen_inodes[inode_key])
            continue
        seen_inodes[inode_key] = str(path)
        entries.append(entry)
    return entries, skipped


def _split_paths(
    path_args: Sequence[str],
    root_args: Sequence[Path] | None,
    issues: list[ScanIssue],
) -> tuple[list[Path], list[Path]]:
    roots: list[Path] = list(root_args or [])
    files: list[Path] = []
    stdin_paths: list[str] = []
    for raw in path_args:
        if raw == "-":
            stdin_paths.extend(line.strip() for line in sys.stdin if line.strip())
        else:
            path_arg = Path(raw)
            if path_arg.is_dir():
                roots.append(path_arg)
            else:
                files.append(path_arg)
    for raw in stdin_paths:
        path_arg = Path(raw)
        if path_arg.is_dir():
            roots.append(path_arg)
        else:
            files.append(path_arg)
    if not roots and not files:
        roots.append(Path.cwd())
    unique_roots: list[Path] = []
    seen_roots: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        if not root.is_dir():
            if root.exists():
                files.append(root)
            else:
                issues.append((str(root), "missing path"))
            continue
        unique_roots.append(root)
    return unique_roots, files


def _collect_entries(
    args: argparse.Namespace,
    cfg: ScanConfig,
    issues: list[ScanIssue],
) -> tuple[list[FileEntry], int]:
    roots, files = _split_paths(args.paths, args.root, issues)
    candidates: list[Path] = []
    enforce_suffix = False
    if args.changed_since:
        changed = _changed_since_paths(roots, args.changed_since)
        if changed is None:
            raise RuntimeError("cannot resolve --changed-since file list")
        candidates.extend(Path(path_text) for path_text in sorted(changed))
        enforce_suffix = True
    else:
        for root in roots:
            found, walk_issues = get_pyfiles(root, cfg)
            issues.extend(walk_issues)
            candidates.extend(found)
    seen_inodes: dict[tuple[int, int], str] = {}
    entries, skipped = _finalize_entries(candidates, cfg, seen_inodes, issues, enforce_suffix)
    explicit_entries, explicit_skipped = _finalize_entries(files, cfg, seen_inodes, issues, False)
    entries.extend(explicit_entries)
    return entries, skipped + explicit_skipped


def _print_groups_plain(groups: Sequence[DuplicateGroup]) -> None:
    for index, group in enumerate(groups, start=1):
        wasted = _group_wasted(group)
        print(
            f"Duplicate group {index} (normalized hash: {group.digest[:16]}, "
            f"{len(group.paths)} files, {_human_bytes(wasted)} wasted):"
        )
        for path in group.paths:
            marker = "keeper" if path == group.keeper else "dup   "
            size = group.sizes.get(str(path), 0)
            print(f"  [{marker}] {path} ({_human_bytes(size)})")


def _print_groups_rich(groups: Sequence[DuplicateGroup]) -> bool:
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        return False
    table = Table(title="Duplicate groups")
    table.add_column("#", justify="right")
    table.add_column("Hash")
    table.add_column("Files", justify="right")
    table.add_column("Wasted", justify="right")
    table.add_column("Paths")
    for index, group in enumerate(groups, start=1):
        lines = []
        for path in group.paths:
            marker = "* " if path == group.keeper else "  "
            size = group.sizes.get(str(path), 0)
            lines.append(f"{marker}{path} ({_human_bytes(size)})")
        table.add_row(
            str(index),
            group.digest[:12],
            str(len(group.paths)),
            _human_bytes(_group_wasted(group)),
            "\n".join(lines),
        )
    Console().print(table)
    return True


def _iter_group_pairs(groups: Sequence[DuplicateGroup]) -> set[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    for group in groups:
        paths = [str(path) for path in group.paths]
        for left_index in range(len(paths)):
            for right_index in range(left_index + 1, len(paths)):
                pairs.add(frozenset((paths[left_index], paths[right_index])))
    return pairs


def _group_payload(group: DuplicateGroup) -> dict[str, Any]:
    return {
        "digest": group.digest,
        "paths": [str(path) for path in group.paths],
        "keeper": str(group.keeper) if group.keeper is not None else None,
        "bytes_wasted": _group_wasted(group),
    }


def _report_text(outcome: ScanOutcome, args: argparse.Namespace) -> None:
    groups = outcome.groups
    for path_text, error in outcome.failures:
        print(f"Skipped {path_text}: {error}", file=sys.stderr)
    if args.print_keep:
        for group in groups:
            if group.keeper is not None:
                print(group.keeper)
        return
    if not groups:
        print("No duplicate files found.")
    elif not _print_groups_rich(groups):
        _print_groups_plain(groups)
    if outcome.definition_groups:
        print(f"\nDuplicate definitions ({len(outcome.definition_groups)} groups):")
        for group in outcome.definition_groups:
            print(f"  hash {group.digest[:12]}:")
            for member in group.members:
                print(f"    {member.path}:{member.lineno} ({member.kind} {member.name})")
    if outcome.near_pairs:
        print(f"\nNear duplicates (similarity >= {args.similarity}):")
        for pair in outcome.near_pairs:
            print(f"  {pair.similarity:.4f}  {pair.paths[0]} ~ {pair.paths[1]}")
    fuzzy_enabled = args.save_similar or args.tlsh or args.minhash or args.simhash
    if fuzzy_enabled:
        enabled_methods = ", ".join(getattr(args, "fuzzy_methods", []) or ["ssdeep"])
        print(
            f"\nMost similar (score > {SIMILARITY_THRESHOLD}, via {enabled_methods}): "
            f"{len(outcome.similar_pairs)} pair(s) -> {SIMILAR_FILENAME}"
        )
        for pair in outcome.similar_pairs:
            print(f"  {pair.score:>3}  {pair.method:<8} {pair.paths[0]} ~ {pair.paths[1]}")
    total_files = sum(len(group.paths) for group in groups)
    total_wasted = sum(_group_wasted(group) for group in groups)
    if not args.quiet:
        print(f"\n{len(groups)} groups, {total_files} files, {_human_bytes(total_wasted)} recoverable")
    if args.dry_run:
        _print_verbose_dry_run(groups, args)
    elif not args.remove and groups:
        print("No files removed. Re-run with -r to remove duplicates.")
    if args.stats:
        stats = outcome.stats
        print("\nStats:")
        print(f"  files scanned:   {stats.scanned}")
        print(f"  files parsed:    {stats.parsed}")
        print(f"  cache hits:      {stats.cache_hits}")
        print(f"  skipped:         {stats.skipped}")
        print(f"  failures:        {stats.failures}")
        print(f"  duplicate groups: {stats.duplicate_groups}")
        print(f"  wasted bytes:    {stats.wasted_bytes} ({_human_bytes(stats.wasted_bytes)})")
        print(f"  elapsed:         {stats.elapsed_seconds:.2f}s")


def _print_verbose_dry_run(groups: Sequence[DuplicateGroup], args: argparse.Namespace) -> None:
    if args.quarantine is not None:
        action = f"move to {args.quarantine.expanduser()}"
    elif args.to_trash:
        action = "send to system trash (send2trash)"
    else:
        action = "unlink (delete)"
    policy = args.keep
    if args.keep == "pattern" and args.pattern:
        policy = f"pattern {args.pattern}"
    elif args.keep == "prefer" and args.prefer:
        policy = f"prefer {args.prefer}"
    if not args.remove:
        confirmation = "--remove not set: nothing would be deleted (add -r)"
    elif args.yes:
        confirmation = "auto-confirmed (-y)"
    elif _interactive():
        confirmation = "interactive prompt [y/N/a/q] per group"
    else:
        confirmation = "non-TTY stdin: deletions would be skipped (pass -y)"
    print()
    print("DRY RUN: verbose deletion plan (no files will be removed)")
    print(f"  action:       {action}")
    print(f"  keep policy:  {policy}")
    print(f"  confirmation: {confirmation}")
    total_removed = 0
    total_reclaimed = 0
    for index, group in enumerate(groups, start=1):
        wasted = _group_wasted(group)
        keeper_size = group.sizes.get(str(group.keeper), 0) if group.keeper else 0
        print(f"  [{index}] {len(group.paths)} file(s), {_human_bytes(wasted)} recoverable, digest {group.digest[:16]}")
        print(f"      keep   {group.keeper} ({_human_bytes(keeper_size)})")
        removed_here = 0
        for path in group.paths:
            if path == group.keeper:
                continue
            size = group.sizes.get(str(path), 0)
            suffix = ""
            if args.quarantine is not None:
                suffix = f" -> {args.quarantine.expanduser() / path.name}"
            elif args.to_trash:
                suffix = " -> trash"
            print(f"      remove {path} ({_human_bytes(size)}){suffix}")
            removed_here += 1
        total_removed += removed_here
        total_reclaimed += wasted
    print(
        f"  totals: {len(groups)} group(s), {total_removed} file(s) would be "
        f"removed, {_human_bytes(total_reclaimed)} reclaimed"
    )
    if args.remove:
        print("  re-run without --dry-run to perform deletion")
    else:
        print("  re-run with -r (and -y to skip prompts) to delete")


def _report_json(outcome: ScanOutcome, args: argparse.Namespace) -> None:
    payload: dict[str, Any] = {
        "version": 1,
        "groups": [_group_payload(group) for group in outcome.groups],
        "failures": [{"path": path, "error": error} for path, error in outcome.failures],
    }
    if args.similarity is not None:
        payload["near_duplicates"] = [
            {"paths": list(pair.paths), "similarity": pair.similarity} for pair in outcome.near_pairs
        ]
    if args.functions:
        payload["definitions"] = [
            {
                "digest": group.digest,
                "members": [
                    {
                        "path": member.path,
                        "lineno": member.lineno,
                        "name": member.name,
                        "kind": member.kind,
                    }
                    for member in group.members
                ],
            }
            for group in outcome.definition_groups
        ]
    if args.stats:
        payload["stats"] = _stats_payload(outcome.stats)
    json.dump(payload, sys.stdout, indent=2)
    print()


def _report_ndjson(outcome: ScanOutcome, args: argparse.Namespace) -> None:
    def emit(payload: dict[str, Any]) -> None:
        print(json.dumps(payload, ensure_ascii=False))

    emit({"type": "meta", "version": 1, "generated": datetime.now(timezone.utc).isoformat()})
    for group in outcome.groups:
        emit({"type": "group", **_group_payload(group)})
    for path, error in outcome.failures:
        emit({"type": "failure", "path": path, "error": error})
    if args.similarity is not None:
        for pair in outcome.near_pairs:
            emit({"type": "near_duplicate", "paths": list(pair.paths), "similarity": pair.similarity})
    if args.functions:
        for group in outcome.definition_groups:
            emit({
                "type": "definition_group",
                "digest": group.digest,
                "members": [
                    {
                        "path": member.path,
                        "lineno": member.lineno,
                        "name": member.name,
                        "kind": member.kind,
                    }
                    for member in group.members
                ],
            })
    if args.stats:
        emit({"type": "stats", **_stats_payload(outcome.stats)})


def _report_csv(outcome: ScanOutcome, args: argparse.Namespace) -> None:
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(["group", "digest", "path", "is_keeper", "size_bytes", "bytes_wasted"])
    for index, group in enumerate(outcome.groups, start=1):
        wasted = _group_wasted(group)
        for path in group.paths:
            writer.writerow([
                index,
                group.digest,
                str(path),
                1 if path == group.keeper else 0,
                group.sizes.get(str(path), 0),
                wasted,
            ])


def _stats_payload(stats: RunStats) -> dict[str, Any]:
    return {
        "files_scanned": stats.scanned,
        "files_parsed": stats.parsed,
        "cache_hits": stats.cache_hits,
        "skipped": stats.skipped,
        "failures": stats.failures,
        "duplicate_groups": stats.duplicate_groups,
        "duplicate_files": stats.duplicate_files,
        "wasted_bytes": stats.wasted_bytes,
        "elapsed_seconds": round(stats.elapsed_seconds, 3),
    }


def _report(outcome: ScanOutcome, args: argparse.Namespace) -> None:
    if args.output_format == "json":
        _report_json(outcome, args)
    elif args.output_format == "ndjson":
        _report_ndjson(outcome, args)
    elif args.output_format == "csv":
        _report_csv(outcome, args)
    else:
        _report_text(outcome, args)


def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except ValueError:
        return False


def _prompt(message: str) -> str:
    sys.stderr.write(message)
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        return "q"
    return line.strip().lower()


def _guard_unchanged(path: Path, entry: FileEntry | None) -> str | None:
    if entry is None:
        return "unknown file"
    try:
        stat_result = os.stat(path)
    except OSError as error:
        return f"{type(error).__name__}: {error}"
    if (
        stat_result.st_size != entry.size
        or stat_result.st_mtime_ns != entry.mtime_ns
        or stat_result.st_ino != entry.ino
    ):
        return "file changed since scan"
    return None


def _quarantine_target(directory: Path, path: Path) -> Path:
    candidate = directory / path.name
    counter = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = directory / f"{path.stem}_{counter}{path.suffix}"
        counter += 1
    return candidate


def _delete_one(
    path: Path,
    entry: FileEntry | None,
    args: argparse.Namespace,
    record: dict[str, Any],
) -> str | None:
    guard_error = _guard_unchanged(path, entry)
    if guard_error is not None:
        record["error"] = guard_error
        return guard_error
    try:
        if args.quarantine is not None:
            directory = args.quarantine.expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            target = _quarantine_target(directory, path)
            shutil.move(str(path), str(target))
            record["target"] = str(target)
            record["action"] = "quarantine"
        elif args.to_trash:
            from send2trash import send2trash

            send2trash(str(path))
            record["action"] = "trash"
        else:
            path.unlink()
            record["action"] = "unlink"
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        record["error"] = message
        return message
    record["removed"] = True
    return None


def _write_manifest(path: Path, records: Sequence[dict[str, Any]]) -> None:
    payload = {
        "version": 1,
        "generated": datetime.now(timezone.utc).isoformat(),
        "entries": list(records),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _manifest_record(group: DuplicateGroup, path: Path, args: argparse.Namespace) -> dict[str, Any]:
    if args.quarantine is not None:
        action = "quarantine"
    elif args.to_trash:
        action = "trash"
    else:
        action = "unlink"
    return {
        "path": str(path),
        "digest": group.digest,
        "keeper": str(group.keeper),
        "size": group.sizes.get(str(path), 0),
        "action": action,
        "removed": False,
        "error": None,
        "target": None,
    }


def _apply_removals(
    outcome: ScanOutcome,
    args: argparse.Namespace,
) -> int:
    group_records: list[list[dict[str, Any]]] = []
    records: list[dict[str, Any]] = []
    for group in outcome.groups:
        per_group: list[dict[str, Any]] = []
        for path in group.paths:
            if group.keeper is not None and path == group.keeper:
                continue
            record = _manifest_record(group, path, args)
            per_group.append(record)
            records.append(record)
        group_records.append(per_group)
    if args.manifest is not None:
        try:
            _write_manifest(args.manifest, records)
        except OSError as error:
            LOGGER.error("cannot write manifest {}: {}", args.manifest, error)
            return 2
    if not args.remove or args.dry_run:
        if args.manifest is not None and not args.quiet:
            print(f"Manifest written to {args.manifest}", file=sys.stderr)
        return 0
    if args.to_trash and importlib.util.find_spec("send2trash") is None:
        LOGGER.error("--to-trash requires the send2trash package")
        return 2
    removed = 0
    failed = 0
    all_mode = False
    stopped = False
    non_interactive_warned = False
    for index, (group, per_group) in enumerate(zip(outcome.groups, group_records), start=1):
        if stopped:
            for record in per_group:
                record["error"] = "skipped: quit at earlier prompt"
            continue
        if not args.yes and not all_mode:
            if not _interactive():
                if not non_interactive_warned:
                    LOGGER.warning("stdin is not a TTY; pass -y to confirm deletions. Skipping all removals.")
                    non_interactive_warned = True
                for record in per_group:
                    record["error"] = "skipped: confirmation required"
                continue
            others = [path for path in group.paths if path != group.keeper]
            answer = _prompt(f"Group {index}: keep {group.keeper}, delete {len(others)} other(s)? [y/N/a/q] ")
            if answer == "a":
                all_mode = True
            elif answer == "q":
                stopped = True
                for record in per_group:
                    record["error"] = "skipped: quit at prompt"
                continue
            elif answer != "y":
                for record in per_group:
                    record["error"] = "skipped: declined"
                if not args.quiet:
                    print(f"Skipped group {index}.", file=sys.stderr)
                continue
        for record in per_group:
            path = Path(record["path"])
            entry = outcome.entries.get(record["path"])
            error = _delete_one(path, entry, args, record)
            if error is not None:
                failed += 1
                LOGGER.error("failed to remove {}: {}", path, error)
                continue
            removed += 1
            if not args.quiet:
                if record["action"] == "unlink":
                    print(f"{path} removed; kept {group.keeper}", file=sys.stderr)
                else:
                    print(
                        f"{path} moved via {record['action']}; kept {group.keeper}",
                        file=sys.stderr,
                    )
    if args.manifest is not None:
        try:
            _write_manifest(args.manifest, records)
        except OSError as error:
            LOGGER.error("cannot update manifest {}: {}", args.manifest, error)
            return 2
    if not args.quiet:
        if removed:
            print(f"{removed} duplicate files removed.", file=sys.stderr)
        elif records:
            print("No duplicate files were removed.", file=sys.stderr)
    if failed:
        return 2
    return 0


def _run_diff(args: argparse.Namespace) -> int:
    left_path, right_path = args.diff
    texts: list[str] = []
    for path in (left_path, right_path):
        try:
            raw = path.read_bytes()
        except OSError as error:
            LOGGER.error("cannot read {}: {}", path, error)
            return 2
        source, error = _decode_source(path, raw)
        if source is None:
            LOGGER.error("cannot decode {}: {}", path, error)
            return 2
        if args.mode == "strict":
            texts.append(source)
            continue
        tree, parse_error = _parse_source(source, path)
        if tree is None:
            LOGGER.error("cannot parse {}: {}", path, parse_error)
            return 2
        normalized = _normalize_tree(tree, args.mode, args.ignore_imports)
        try:
            texts.append(ast.unparse(normalized) + "\n")
        except Exception as error:
            LOGGER.error("cannot unparse {}: {}", path, error)
            return 2
    diff = difflib.unified_diff(
        texts[0].splitlines(keepends=True),
        texts[1].splitlines(keepends=True),
        fromfile=str(left_path),
        tofile=str(right_path),
    )
    written = False
    for line in diff:
        sys.stdout.write(line)
        written = True
    if not written:
        print(f"{left_path} and {right_path} are identical under mode {args.mode}")
        return 0
    return 1


def _resolve_fuzzy_methods(args: argparse.Namespace) -> tuple[list[str], str | None]:
    methods: list[str] = []
    if args.save_similar:
        methods.append("ssdeep")
    if args.tlsh:
        methods.append("tlsh")
    if args.minhash:
        methods.append("minhash")
    if args.simhash:
        methods.append("simhash")
    if not methods:
        return [], None
    if "ssdeep" in methods and _ssdeep is None:
        if len(methods) == 1:
            return [], "-s/--save-similar requires the ssdeep package (pip install ssdeep)"
        LOGGER.warning("ssdeep is unavailable; skipping ssdeep similarity pairs")
        methods.remove("ssdeep")
    if "tlsh" in methods and _tlsh is None:
        return [], "--tlsh requires the tlsh package (pip install py-tlsh)"
    return methods, None


def _scan(args: argparse.Namespace) -> ScanOutcome | None:
    started = time.monotonic()
    issues: list[ScanIssue] = []
    stats = RunStats()
    cfg = ScanConfig(
        follow_symlinks=args.follow_symlinks,
        include_notebooks=args.notebooks,
        min_bytes=args.min_bytes,
        exclude_patterns=list(args.exclude),
    )
    fuzzy_methods = getattr(args, "fuzzy_methods", None)
    if fuzzy_methods is None:
        fuzzy_methods, _resolve_error = _resolve_fuzzy_methods(args)
    worker_cfg = WorkerConfig(
        mode=args.mode,
        ignore_imports=args.ignore_imports,
        skip_empty=args.skip_empty,
        min_lines=args.min_lines,
        need_ngrams=args.similarity is not None,
        need_definitions=args.functions,
        need_ssdeep="ssdeep" in fuzzy_methods,
        need_tlsh="tlsh" in fuzzy_methods,
        need_minhash="minhash" in fuzzy_methods,
        need_simhash="simhash" in fuzzy_methods,
    )
    try:
        entries, scan_skipped = _collect_entries(args, cfg, issues)
    except RuntimeError as error:
        LOGGER.error("{}", error)
        return None
    stats.scanned = len(entries)
    stats.skipped += scan_skipped
    entries.sort(key=lambda entry: str(entry.path))
    LOGGER.info("scanned {} candidate files", len(entries))
    mode_key = json.dumps(
        {
            "mode": args.mode,
            "ignore_imports": args.ignore_imports,
            "skip_empty": args.skip_empty,
            "min_lines": args.min_lines,
            "version": CACHE_VERSION,
        },
        sort_keys=True,
    )
    cache_path = Path.cwd() / CACHE_FILENAME
    cache: dict[str, CacheEntry] = {}
    use_cache = not args.no_cache and args.similarity is None and not args.functions
    if not args.no_cache:
        cache = _load_cache(cache_path, mode_key)
    try:
        results, cache_hits = process_files(
            entries,
            worker_cfg,
            cache,
            use_cache,
            MAX_WORKERS,
            args.rich,
            args.mp_method,
        )
    except Exception as error:
        LOGGER.error("processing failed: {}", error)
        return None
    stats.cache_hits = cache_hits
    failures: list[Failure] = list(issues)
    for result in results:
        if result.error is not None:
            failures.append((result.path, result.error))
        elif result.skipped is not None:
            stats.skipped += 1
            LOGGER.debug("skipped {} ({})", result.path, result.skipped)
        elif result.digest is not None:
            if not result.from_cache:
                stats.parsed += 1
    if not args.no_cache and entries:
        _save_cache(cache_path, mode_key, cache, {str(e.path): e for e in entries}, results)
    entries_map = {str(entry.path): entry for entry in entries}
    groups, verify_failures = _build_groups(results, entries_map, args.mode, worker_cfg)
    failures.extend(verify_failures)
    definition_groups: list[DefinitionGroup] = []
    if args.functions:
        definition_groups = _build_definition_groups(results)
    near_pairs: list[NearPair] = []
    if args.similarity is not None:
        exact_pairs = _iter_group_pairs(groups)
        near_pairs = _build_near_pairs(results, args.similarity, exact_pairs)
    similar_pairs: list[SimilarPair] = []
    if fuzzy_methods:
        similar_pairs = _build_similar_pairs(results, entries_map, fuzzy_methods, SIMILARITY_THRESHOLD)
        try:
            _write_simz(Path.cwd() / SIMILAR_FILENAME, similar_pairs, fuzzy_methods)
        except OSError as error:
            LOGGER.error("cannot write {}: {}", SIMILAR_FILENAME, error)
    failures.sort()
    stats.failures = len(failures)
    stats.duplicate_groups = len(groups)
    stats.duplicate_files = sum(len(group.paths) for group in groups)
    stats.wasted_bytes = sum(_group_wasted(group) for group in groups)
    stats.elapsed_seconds = time.monotonic() - started
    for path_text, error in failures:
        LOGGER.debug("failure for {}: {}", path_text, error)
    return ScanOutcome(
        entries=entries_map,
        groups=groups,
        near_pairs=near_pairs,
        definition_groups=definition_groups,
        similar_pairs=similar_pairs,
        failures=failures,
        stats=stats,
    )


def _validate_args(args: argparse.Namespace) -> str | None:
    methods, methods_error = _resolve_fuzzy_methods(args)
    if methods_error is not None:
        return methods_error
    args.fuzzy_methods = methods
    if args.mp_method == "cf.InterpreterPoolExecutor" and _InterpreterPoolExecutor is None:
        return "--mp-method cf.InterpreterPoolExecutor requires Python 3.14+"
    if args.mp_method == "joblib" and importlib.util.find_spec("joblib") is None:
        return "--mp-method joblib requires the joblib package (pip install joblib)"
    if args.keep == "pattern" and not args.pattern:
        return "--keep pattern requires --pattern GLOB"
    if args.keep == "prefer" and not args.prefer:
        return "--keep prefer requires --prefer PREFIX"
    if args.watch and args.remove:
        LOGGER.warning("--remove is ignored in --watch mode (report-only)")
        args.remove = False
    if args.print_keep and args.output_format != "text":
        LOGGER.warning("--print-keep only applies to --format text; ignoring")
        args.print_keep = False
    return None


def _exit_code(args: argparse.Namespace, outcome: ScanOutcome, deletion_code: int) -> int:
    if deletion_code:
        return deletion_code
    code = 0
    if args.fail_on_dupes and outcome.groups:
        code = 1
    if args.fail_on_error and outcome.failures:
        code = 1
    return code


def _emit_outcome(outcome: ScanOutcome, args: argparse.Namespace) -> None:
    if args.baseline is not None:
        try:
            baseline = _load_baseline(args.baseline)
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        outcome.groups, suppressed = _filter_baseline(outcome.groups, baseline)
        if suppressed and not args.quiet:
            LOGGER.info("suppressed {} baseline duplicate groups", suppressed)
        outcome.stats.duplicate_groups = len(outcome.groups)
        outcome.stats.duplicate_files = sum(len(group.paths) for group in outcome.groups)
        outcome.stats.wasted_bytes = sum(_group_wasted(group) for group in outcome.groups)
    _report(outcome, args)


def _watch(args: argparse.Namespace) -> int:
    try:
        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        LOGGER.error("--watch requires the watchdog package")
        return 2
    timer_lock = threading.Lock()
    pending: threading.Timer | None = None

    def run_scan() -> None:
        try:
            outcome = _scan(args)
            if outcome is None:
                return
            _assign_keepers(
                outcome.groups,
                outcome.entries,
                args.keep,
                args.pattern,
                args.prefer,
            )
            _emit_outcome(outcome, args)
        except Exception as error:
            LOGGER.error("scan failed: {}", error)

    def schedule(_event: "FileSystemEvent") -> None:
        nonlocal pending
        with timer_lock:
            if pending is not None:
                pending.cancel()
            pending = threading.Timer(0.5, run_scan)
            pending.start()

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event: "FileSystemEvent") -> None:
            if event.is_directory:
                return
            if str(event.src_path).lower().endswith((".py", ".pyi", NOTEBOOK_SUFFIX)):
                schedule(event)

    observer = Observer()
    roots: list[Path] = []
    root_args = args.root or []
    for raw in args.paths:
        if raw == "-":
            continue
        path = Path(raw)
        if path.is_dir():
            root_args = [*root_args, path]
    for root in root_args or [Path.cwd()]:
        if root.is_dir():
            observer.schedule(_Handler(), str(root), recursive=True)
            roots.append(root)
    if not roots:
        LOGGER.error("--watch needs at least one directory to watch")
        return 2
    LOGGER.info("watching {}", ", ".join(str(root) for root in roots))
    run_scan()
    observer.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        raise
    finally:
        observer.stop()
        observer.join()
    return 130


def _run(args: argparse.Namespace) -> int:
    validation_error = _validate_args(args)
    if validation_error is not None:
        LOGGER.error("{}", validation_error)
        return 2
    if args.diff is not None:
        return _run_diff(args)
    if args.watch:
        return _watch(args)
    outcome = _scan(args)
    if outcome is None:
        return 2
    _assign_keepers(outcome.groups, outcome.entries, args.keep, args.pattern, args.prefer)
    try:
        _emit_outcome(outcome, args)
    except RuntimeError as error:
        LOGGER.error("{}", error)
        return 2
    deletion_code = _apply_removals(outcome, args)
    return _exit_code(args, outcome, deletion_code)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose, args.quiet, args.log_format)
    try:
        return _run(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
