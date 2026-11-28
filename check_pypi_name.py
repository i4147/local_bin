#!/data/data/com.termux/files/usr/bin/env python
"""
Check PyPI package name availability, squatting similarity, and get safe alternatives.

argparse CLI version (no typer/rich dependency).
"""

from __future__ import annotations
import argparse
import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import keyword
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

import httpx
from loguru import logger
from rapidfuzz import process
from rapidfuzz.distance import Levenshtein
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


try:
    import msgpack  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    msgpack = None

__version__: str = "0.1.0"
SCHEMA_VERSION: int = 2
GITHUB_ISSUES_URL: str = "https://github.com/i4147/pkgname/issues"

logger.remove()
logger.add(sys.stderr, level="WARNING", format="{level}: {message}")

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

DEFAULT_INDEX_PATH: Path = Path("/sdcard/data/pip.json")
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "check_pypi_name"
MAX_PROJECT_NAME_LENGTH: int = 214

DEFAULT_SIMILARITY_AFFIXES: tuple[str, ...] = (
    "py-",
    "python-",
    "py",
    "-py",
    "-cli",
    "-tool",
    "-lib",
    "-kit",
    "-utils",
    "-dev",
    "-core",
    "get-",
    "-next",
    "-ng",
    "-alt",
)

DEFAULT_RECOMMEND_AFFIXES: tuple[str, ...] = (
    "py",
    "py-",
    "-py",
    "-cli",
    "-tool",
    "-lib",
    "-kit",
    "-utils",
    "-dev",
    "-core",
    "-next",
    "-ng",
    "-alt",
    "-lite",
    "-mini",
    "2",
    "3",
)

DEFAULT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "fs": ("filesystem",),
    "walk": ("traverse", "scan", "iter"),
    "dir": ("directory",),
    "pkg": ("package",),
    "cfg": ("config",),
    "util": ("utils",),
    "db": ("database",),
}

WELL_KNOWN_SQUATTED_NAMES: frozenset[str] = frozenset({
    "requests",
    "urllib3",
    "numpy",
    "pandas",
    "scipy",
    "django",
    "flask",
    "fastapi",
    "boto3",
    "botocore",
    "pip",
    "setuptools",
    "wheel",
    "pytest",
    "attrs",
    "pydantic",
    "sqlalchemy",
    "celery",
    "redis",
    "pillow",
})


@dataclass(slots=True)
class SimilarityConfig:
    """Store max_edit_distance, check_separators, check_affixes, affixes."""

    max_edit_distance: int = 2
    check_separators: bool = True
    check_affixes: bool = True
    affixes: tuple[str, ...] = field(default_factory=lambda: DEFAULT_SIMILARITY_AFFIXES)


# ---------------------------------------------------------------------------
# Models & Exceptions
# ---------------------------------------------------------------------------


class Status(str, Enum):
    """Enumerate the supported Status values."""

    AVAILABLE = "AVAILABLE"
    TAKEN = "TAKEN"
    REJECTED_SIMILAR = "REJECTED_SIMILAR"

    @property
    def exit_code(self) -> int:
        """Return the exit code."""
        if self is Status.AVAILABLE:
            return 0
        if self is Status.TAKEN:
            return 1
        return 2


class PkgNameError(Exception):
    """Base error for pkgname."""


class ValidationError(PkgNameError):
    """Raised when a package name fails PEP 508 / PyPI validation."""


class IndexLoadError(PkgNameError):
    """Raised when a required local index cannot be loaded."""


class PyPIClientError(PkgNameError):
    """Raised when querying PyPI fails due to network, timeout, or HTTP errors."""


@dataclass(slots=True)
class Match:
    """Store name, normalized, reason, distance, origin and related state."""

    name: str
    normalized: str
    reason: str
    distance: int
    origin: str
    summary: str | None = None
    version: str | None = None


@dataclass(slots=True)
class Recommendation:
    """Store name, normalized, strategy, score, verified_offline."""

    name: str
    normalized: str
    strategy: str
    score: float
    verified_offline: bool = False


@dataclass(slots=True)
class IndexStats:
    """Store source_path, exists, file_size_bytes, sha256, entry_count and related state."""

    source_path: str
    exists: bool
    file_size_bytes: int
    sha256: str
    entry_count: int
    cache_path: str
    cache_hit: bool
    load_time_ms: float


@dataclass(slots=True)
class CheckResult:
    """Store query, normalized, status, source, used_local_index and related state."""

    query: str
    normalized: str
    status: Status
    source: str
    used_local_index: bool
    schema_version: int = SCHEMA_VERSION
    warnings: list[str] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict."""
        return {
            "schema_version": self.schema_version,
            "query": self.query,
            "normalized": self.normalized,
            "status": self.status.value,
            "source": self.source,
            "used_local_index": self.used_local_index,
            "warnings": list(self.warnings),
            "matches": [asdict(m) for m in self.matches],
            "recommendations": [asdict(r) for r in self.recommendations],
            "errors": list(self.errors),
        }

    def to_json(self) -> str:
        """Convert to json."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


# ---------------------------------------------------------------------------
# Normalization & PEP 508 Validation
# ---------------------------------------------------------------------------

PEP508_NAME_RE = re.compile(r"^([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9])$")
PEP503_SEP_RE = re.compile(r"[-_.]+")


def normalize_name(name: str) -> str:
    """Normalize name."""
    return PEP503_SEP_RE.sub("-", name).lower()


def validate_name(name: str) -> list[str]:
    """Validate name."""
    if not name:
        raise ValidationError("Package name must not be empty.")
    if len(name) > MAX_PROJECT_NAME_LENGTH:
        raise ValidationError(
            f"Package name exceeds maximum length of {MAX_PROJECT_NAME_LENGTH} characters (got {len(name)})."
        )
    if not PEP508_NAME_RE.match(name):
        raise ValidationError(
            f"Invalid package name '{name}': must start and end with an ASCII letter or "
            "digit and contain only ASCII letters, digits, '.', '_', or '-'."
        )

    warnings: list[str] = []
    normalized = normalize_name(name)
    if normalized in WELL_KNOWN_SQUATTED_NAMES:
        warnings.append(f"'{name}' matches a well-known popular/squatted package name and is likely unavailable.")
    if keyword.iskeyword(normalized) or keyword.issoftkeyword(normalized):
        warnings.append(f"'{name}' is a Python keyword, which may cause import friction.")
    stdlib_names = getattr(sys, "stdlib_module_names", frozenset())
    if normalized in stdlib_names:
        warnings.append(f"'{name}' collides with a Python standard library module name.")
    return warnings


# ---------------------------------------------------------------------------
# Offline Index
# ---------------------------------------------------------------------------


def _compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_line_entry(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.endswith(","):
        stripped = stripped[:-1].strip()
    if stripped in ("[", "]"):
        return None
    row = json.loads(stripped)
    if isinstance(row, list) and row and isinstance(row[0], str):
        return normalize_name(row[0])
    return None


def _stream_parse_pip_json(path: Path) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                norm = _parse_line_entry(line)
                if norm and norm not in seen:
                    seen.add(norm)
                    names.append(norm)
    except json.JSONDecodeError:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise IndexLoadError(f"Expected JSON list in '{path}'.")
        names = []
        seen = set()
        for row in data:
            if isinstance(row, list) and row and isinstance(row[0], str):
                norm = normalize_name(row[0])
                if norm not in seen:
                    seen.add(norm)
                    names.append(norm)
    return names


@dataclass(slots=True)
class LocalIndex:
    """Store source_path, names_list, names_set, sha256, file_size_bytes and related state."""

    source_path: Path
    names_list: list[str]
    names_set: set[str]
    sha256: str
    file_size_bytes: int
    cache_path: Path
    cache_hit: bool
    load_time_ms: float

    def __contains__(self, name: str) -> bool:
        """Return whether the supplied item is present."""
        return normalize_name(name) in self.names_set

    def stats(self) -> IndexStats:
        return IndexStats(
            source_path=str(self.source_path),
            exists=True,
            file_size_bytes=self.file_size_bytes,
            sha256=self.sha256,
            entry_count=len(self.names_list),
            cache_path=str(self.cache_path),
            cache_hit=self.cache_hit,
            load_time_ms=round(self.load_time_ms, 2),
        )


def resolve_index_path(index_path: Path | str | None = None) -> Path:
    """Resolve index path."""
    if index_path is not None:
        return Path(index_path)
    env_path = os.environ.get("PKGNAME_INDEX")
    if env_path:
        return Path(env_path)
    return DEFAULT_INDEX_PATH


def _cache_file_path(
    source_path: Path,
    stat_result: os.stat_result,
    sha256: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Path:
    fingerprint = f"{source_path.resolve()}:{stat_result.st_mtime_ns}:{stat_result.st_size}:{sha256}"
    key = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
    ext = "msgpack" if msgpack is not None else "txt"
    return cache_dir / f"index-{key}.{ext}"


def _write_cache(cache_path: Path, names: list[str]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    if msgpack is not None:
        packed: Any = msgpack.packb({"names": names}, use_bin_type=True)
        tmp_path.write_bytes(packed)
    else:
        tmp_path.write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")
    tmp_path.replace(cache_path)


def _read_cache(cache_path: Path) -> list[str] | None:
    if not cache_path.exists():
        return None
    try:
        if msgpack is not None and cache_path.suffix == ".msgpack":
            payload = msgpack.unpackb(cache_path.read_bytes(), raw=False)
            if isinstance(payload, dict) and isinstance(payload.get("names"), list):
                return [str(x) for x in payload["names"]]
            return None
        lines = cache_path.read_text(encoding="utf-8").splitlines()
        return [ln.strip() for ln in lines if ln.strip()]
    except Exception as exc:
        logger.warning("Ignoring unreadable index cache '{}': {}", cache_path, exc)
        return None


def load_local_index(
    index_path: Path | str | None = None,
    *,
    force_rebuild: bool = False,
    strict: bool = False,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> LocalIndex | None:
    """Load local index."""
    start = time.perf_counter()
    resolved = resolve_index_path(index_path)

    if not resolved.exists() or not resolved.is_file():
        msg = f"Local index file not found at '{resolved}'."
        if strict:
            raise IndexLoadError(msg)
        logger.warning("{} Continuing without local index.", msg)
        return None

    try:
        stat_result = resolved.stat()
        sha256 = _compute_file_sha256(resolved)
        cache_path = _cache_file_path(resolved, stat_result, sha256, cache_dir=cache_dir)

        cached_names: list[str] | None = None
        if not force_rebuild:
            cached_names = _read_cache(cache_path)

        if cached_names is not None:
            names_list = cached_names
            cache_hit = True
        else:
            names_list = _stream_parse_pip_json(resolved)
            _write_cache(cache_path, names_list)
            cache_hit = False

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return LocalIndex(
            source_path=resolved,
            names_list=names_list,
            names_set=set(names_list),
            sha256=sha256,
            file_size_bytes=stat_result.st_size,
            cache_path=cache_path,
            cache_hit=cache_hit,
            load_time_ms=elapsed_ms,
        )
    except IndexLoadError:
        raise
    except Exception as exc:
        msg = f"Failed to load local index '{resolved}': {exc}"
        if strict:
            raise IndexLoadError(msg) from exc
        logger.warning("{} Continuing without local index.", msg)
        return None


_DEFAULT_INDEX: LocalIndex | None = None
_DEFAULT_INDEX_LOADED: bool = False


def get_index(
    index_path: Path | str | None = None,
    *,
    force_rebuild: bool = False,
    strict: bool = False,
) -> LocalIndex | None:
    """Return index."""
    global _DEFAULT_INDEX, _DEFAULT_INDEX_LOADED
    if index_path is None and not force_rebuild and not strict and _DEFAULT_INDEX_LOADED:
        return _DEFAULT_INDEX
    loaded = load_local_index(
        index_path=index_path,
        force_rebuild=force_rebuild,
        strict=strict,
    )
    if index_path is None and not strict:
        _DEFAULT_INDEX = loaded
        _DEFAULT_INDEX_LOADED = True
    return loaded


def reset_index_singleton() -> None:
    global _DEFAULT_INDEX, _DEFAULT_INDEX_LOADED
    _DEFAULT_INDEX = None
    _DEFAULT_INDEX_LOADED = False


# ---------------------------------------------------------------------------
# Similarity Detection
# ---------------------------------------------------------------------------


def _matches_affix_rule(norm_query: str, norm_candidate: str, affixes: tuple[str, ...]) -> str | None:
    for affix in affixes:
        if affix.endswith("-"):
            if norm_candidate == f"{affix}{norm_query}" or norm_query == f"{affix}{norm_candidate}":
                return f"prefix variant `{affix}`"
        elif affix.startswith("-"):
            if norm_candidate == f"{norm_query}{affix}" or norm_query == f"{norm_candidate}{affix}":
                return f"suffix variant `{affix}`"
        else:
            if norm_candidate == f"{affix}{norm_query}" or norm_query == f"{affix}{norm_candidate}":
                return f"prefix variant `{affix}`"
            if norm_candidate == f"{norm_query}{affix}" or norm_query == f"{norm_candidate}{affix}":
                return f"suffix variant `{affix}`"
    return None


def evaluate_candidate_similarity(
    query: str,
    candidate: str,
    config: SimilarityConfig,
    *,
    origin: str = "local_index",
) -> Match | None:
    norm_q = normalize_name(query)
    norm_c = normalize_name(candidate)
    if not norm_c or norm_c == norm_q:
        if norm_c == norm_q and query.lower() != candidate.lower():
            return Match(
                name=candidate,
                normalized=norm_c,
                reason="separator variant",
                distance=0,
                origin=origin,
            )
        return None

    dist = int(Levenshtein.distance(norm_q, norm_c))

    if config.check_separators and norm_q.replace("-", "") == norm_c.replace("-", ""):
        return Match(
            name=candidate,
            normalized=norm_c,
            reason="separator variant",
            distance=dist,
            origin=origin,
        )

    if config.check_affixes:
        affix_reason = _matches_affix_rule(norm_q, norm_c, config.affixes)
        if affix_reason is not None:
            return Match(
                name=candidate,
                normalized=norm_c,
                reason=affix_reason,
                distance=dist,
                origin=origin,
            )

    if dist <= config.max_edit_distance:
        reason = "edit distance 1" if dist == 1 else f"edit distance {dist}"
        return Match(
            name=candidate,
            normalized=norm_c,
            reason=reason,
            distance=dist,
            origin=origin,
        )

    return None


def scan_local_index_similarity(
    query: str,
    index: LocalIndex,
    config: SimilarityConfig,
    *,
    limit: int = 50,
) -> list[Match]:
    """Scan local index similarity."""
    norm_q = normalize_name(query)
    matches_by_norm: dict[str, Match] = {}

    if norm_q in index.names_set:
        matches_by_norm[norm_q] = Match(
            name=norm_q,
            normalized=norm_q,
            reason="exact match",
            distance=0,
            origin="local_index",
        )

    fuzzy_hits = process.extract(
        norm_q,
        index.names_list,
        scorer=Levenshtein.distance,
        limit=limit,
        score_cutoff=config.max_edit_distance,
    )
    for cand_name, _score, _idx in fuzzy_hits:
        if cand_name in matches_by_norm:
            continue
        match = evaluate_candidate_similarity(query, cand_name, config, origin="local_index")
        if match is not None:
            matches_by_norm[cand_name] = match

    compact_q = norm_q.replace("-", "")
    if config.check_separators or config.check_affixes:
        for cand_name in index.names_list:
            if cand_name in matches_by_norm:
                continue
            if config.check_separators and cand_name.replace("-", "") == compact_q:
                dist = int(Levenshtein.distance(norm_q, cand_name))
                matches_by_norm[cand_name] = Match(
                    name=cand_name,
                    normalized=cand_name,
                    reason="separator variant",
                    distance=dist,
                    origin="local_index",
                )
                continue
            if config.check_affixes:
                affix_reason = _matches_affix_rule(norm_q, cand_name, config.affixes)
                if affix_reason is not None:
                    dist = int(Levenshtein.distance(norm_q, cand_name))
                    matches_by_norm[cand_name] = Match(
                        name=cand_name,
                        normalized=cand_name,
                        reason=affix_reason,
                        distance=dist,
                        origin="local_index",
                    )

    return sorted(matches_by_norm.values(), key=lambda m: (m.distance, m.normalized))


def generate_probe_variants(query: str, config: SimilarityConfig) -> list[str]:
    """Generate probe variants."""
    norm_q = normalize_name(query)
    variants: list[str] = []
    seen: set[str] = {norm_q}

    def add(val: str) -> None:
        n = normalize_name(val)
        if n and len(n) >= 2 and n not in seen:
            seen.add(n)
            variants.append(n)

    if "-" in norm_q:
        add(norm_q.replace("-", ""))

    if config.check_affixes:
        for affix in config.affixes:
            if affix.endswith("-"):
                add(f"{affix}{norm_q}")
                if norm_q.startswith(affix):
                    add(norm_q.removeprefix(affix))
            elif affix.startswith("-"):
                add(f"{norm_q}{affix}")
                if norm_q.endswith(affix):
                    add(norm_q.removesuffix(affix))
            else:
                add(f"{affix}{norm_q}")
                add(f"{norm_q}{affix}")

    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    for i in range(len(norm_q)):
        add(norm_q[:i] + norm_q[i + 1 :])
        for ch in alphabet:
            add(norm_q[:i] + ch + norm_q[i + 1 :])
    for i in range(len(norm_q) + 1):
        for ch in alphabet:
            add(norm_q[:i] + ch + norm_q[i:])

    return variants


def classify_verdict(*, exact_exists_on_pypi: bool | None, matches: list[Match]) -> Status:
    """Classify verdict."""
    if exact_exists_on_pypi is True:
        return Status.TAKEN
    if any(m.reason == "exact match" for m in matches):
        return Status.TAKEN
    if matches:
        return Status.REJECTED_SIMILAR
    return Status.AVAILABLE


# ---------------------------------------------------------------------------
# Recommendation Engine
# ---------------------------------------------------------------------------

STRATEGY_PRIORITY: dict[str, int] = {
    "separator": 0,
    "suffix": 1,
    "prefix": 2,
    "synonym": 3,
    "abbreviation": 4,
}


@dataclass(slots=True)
class _Candidate:
    name: str
    normalized: str
    strategy: str


def _abbreviate_conservative(token: str) -> str | None:
    if len(token) < 5:
        return None
    vowels = set("aeiou")
    head = token[0]
    tail_stripped = head + "".join(ch for ch in token[1:] if ch not in vowels)
    if 3 <= len(tail_stripped) < len(token):
        return tail_stripped
    return None


def generate_candidates(
    query: str,
    *,
    affixes: tuple[str, ...] = DEFAULT_RECOMMEND_AFFIXES,
    synonyms: dict[str, tuple[str, ...]] = DEFAULT_SYNONYMS,
    max_candidates: int = 250,
) -> list[_Candidate]:
    """Generate candidates."""
    norm_q = normalize_name(query)
    candidates: list[_Candidate] = []
    seen: set[str] = {norm_q}

    def add(raw: str, strategy: str) -> None:
        if len(candidates) >= max_candidates:
            return
        cand_norm = normalize_name(raw)
        if not cand_norm or len(cand_norm) < 2 or cand_norm in seen:
            return
        seen.add(cand_norm)
        candidates.append(_Candidate(name=cand_norm, normalized=cand_norm, strategy=strategy))

    if "-" in norm_q:
        add(norm_q.replace("-", ""), "separator")
    elif len(norm_q) >= 4:
        mid = len(norm_q) // 2
        add(f"{norm_q[:mid]}-{norm_q[mid:]}", "separator")

    parts = norm_q.split("-")
    if len(parts) >= 2:
        add("-".join(reversed(parts)), "separator")

    for affix in affixes:
        if affix.endswith("-"):
            add(f"{affix}{norm_q}", "prefix")
        elif affix.startswith("-"):
            add(f"{norm_q}{affix}", "suffix")
        else:
            add(f"{norm_q}-{affix}", "suffix")
            add(f"{affix}-{norm_q}", "prefix")

    for idx, part in enumerate(parts):
        if part in synonyms:
            for syn in synonyms[part]:
                new_parts = list(parts)
                new_parts[idx] = syn
                add("-".join(new_parts), "synonym")
        for key, syns in synonyms.items():
            if key in part and part != key:
                for syn in syns:
                    add(part.replace(key, syn), "synonym")

    for idx, part in enumerate(parts):
        abbr = _abbreviate_conservative(part)
        if abbr:
            new_parts = list(parts)
            new_parts[idx] = abbr
            add("-".join(new_parts), "abbreviation")

    return candidates


def _fails_phase_a_local(
    cand_norm: str,
    query_norm: str,
    local_index: LocalIndex,
    config: SimilarityConfig,
) -> bool:
    if cand_norm in local_index.names_set:
        return True
    dist = int(Levenshtein.distance(query_norm, cand_norm))
    affix_hit = _matches_affix_rule(query_norm, cand_norm, config.affixes) is not None
    if dist <= config.max_edit_distance and affix_hit:
        return True
    return evaluate_candidate_similarity(query_norm, cand_norm, config, origin="local_index") is not None


def _score_recommendation(query_norm: str, cand: _Candidate) -> float:
    dist = int(Levenshtein.distance(query_norm, cand.normalized))
    prio = STRATEGY_PRIORITY.get(cand.strategy, 5)
    raw_score = 1.0 - min(dist * 0.12 + prio * 0.05 + len(cand.normalized) * 0.004, 0.85)
    return round(max(raw_score, 0.05), 4)


async def recommend_alternatives(
    query: str,
    *,
    limit: int = 10,
    config: SimilarityConfig,
    local_index: LocalIndex | None = None,
    offline_only: bool = False,
    pypi_exists_fn: Callable[[str], Awaitable[bool]] | None = None,
) -> list[Recommendation]:
    if limit <= 0:
        return []

    norm_q = normalize_name(query)
    candidates = generate_candidates(query)
    accepted: list[Recommendation] = []

    for cand in candidates:
        if len(accepted) >= limit:
            break
        if local_index is not None:
            if _fails_phase_a_local(cand.normalized, norm_q, local_index, config):
                continue
        if offline_only:
            accepted.append(
                Recommendation(
                    name=cand.name,
                    normalized=cand.normalized,
                    strategy=cand.strategy,
                    score=_score_recommendation(norm_q, cand),
                    verified_offline=True,
                )
            )
            continue
        if pypi_exists_fn is None:
            continue
        if await pypi_exists_fn(cand.normalized):
            continue
        accepted.append(
            Recommendation(
                name=cand.name,
                normalized=cand.normalized,
                strategy=cand.strategy,
                score=_score_recommendation(norm_q, cand),
                verified_offline=False,
            )
        )

    accepted.sort(
        key=lambda r: (
            -r.score,
            int(Levenshtein.distance(norm_q, r.normalized)),
            STRATEGY_PRIORITY.get(r.strategy, 5),
            len(r.normalized),
            r.normalized,
        )
    )
    return accepted[:limit]


# ---------------------------------------------------------------------------
# Async PyPI JSON API Client
# ---------------------------------------------------------------------------


class _RetryableStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Retryable HTTP status {status_code}")


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (_RetryableStatusError, httpx.TimeoutException, httpx.TransportError))


@dataclass(slots=True)
class PyPIProjectInfo:
    """Store exists, summary, version."""

    exists: bool
    summary: str | None = None
    version: str | None = None


class PyPIClient:
    def __init__(
        self,
        *,
        timeout: float = 10.0,
        concurrency: int = 10,
        max_attempts: int = 3,
        cache_size: int = 1024,
        base_url: str = "https://pypi.org/pypi",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.timeout = timeout
        self.concurrency = max(1, concurrency)
        self.max_attempts = max(1, max_attempts)
        self.cache_size = max(1, cache_size)
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._semaphore = asyncio.Semaphore(self.concurrency)
        self._cache: OrderedDict[str, PyPIProjectInfo] = OrderedDict()

    def _cache_get(self, key: str) -> PyPIProjectInfo | None:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def _cache_put(self, key: str, value: PyPIProjectInfo) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    async def _fetch_once(self, client: httpx.AsyncClient, norm_name: str) -> PyPIProjectInfo:
        url = f"{self.base_url}/{norm_name}/json"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=0.1, min=0.1, max=2.0),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                response = await client.get(url)
                if response.status_code == 200:
                    data: dict[str, Any] = response.json()
                    info = data.get("info") if isinstance(data, dict) else {}
                    summary = info.get("summary") if isinstance(info, dict) else None
                    version = info.get("version") if isinstance(info, dict) else None
                    return PyPIProjectInfo(
                        exists=True,
                        summary=str(summary) if summary else None,
                        version=str(version) if version else None,
                    )
                if response.status_code == 404:
                    return PyPIProjectInfo(exists=False)
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    raise _RetryableStatusError(response.status_code)
                raise PyPIClientError(f"Unexpected HTTP status {response.status_code} from PyPI for '{norm_name}'.")
        raise PyPIClientError(f"Failed to query PyPI for '{norm_name}'.")

    async def lookup(self, name: str, client: httpx.AsyncClient | None = None) -> PyPIProjectInfo:
        norm = normalize_name(name)
        cached = self._cache_get(norm)
        if cached is not None:
            return cached

        headers = {"User-Agent": f"pkgname/{__version__} (+https://pypi.org)"}
        async with self._semaphore:
            cached_after_wait = self._cache_get(norm)
            if cached_after_wait is not None:
                return cached_after_wait

            try:
                if client is not None:
                    result = await self._fetch_once(client, norm)
                else:
                    async with httpx.AsyncClient(
                        timeout=self.timeout,
                        headers=headers,
                        transport=self._transport,
                    ) as owned_client:
                        result = await self._fetch_once(owned_client, norm)
            except PyPIClientError:
                raise
            except _RetryableStatusError as exc:
                raise PyPIClientError(
                    f"PyPI rate limit or server error ({exc.status_code}) after retries for '{norm}'."
                ) from exc
            except httpx.TimeoutException as exc:
                raise PyPIClientError(f"Timed out querying PyPI for '{norm}'.") from exc
            except httpx.HTTPError as exc:
                raise PyPIClientError(f"Network error querying PyPI for '{norm}': {exc}") from exc

        self._cache_put(norm, result)
        return result

    async def exists(self, name: str, client: httpx.AsyncClient | None = None) -> bool:
        info = await self.lookup(name, client=client)
        return info.exists


# ---------------------------------------------------------------------------
# Core pipeline (source-agnostic)
# ---------------------------------------------------------------------------


class SourceMode(str, Enum):
    """Enumerate the supported SourceMode values."""

    AUTO = "auto"
    HYBRID = "hybrid"
    PYPI = "pypi"
    LOCAL = "local"


async def _run_check(
    name: str,
    *,
    source: SourceMode,
    index_path: Path | None,
    recommend: bool | None,
    limit: int,
    distance: int,
    timeout: float,
    concurrency: int,
) -> CheckResult:
    norm_q = normalize_name(name)
    warnings = validate_name(name)
    sim_cfg = SimilarityConfig(max_edit_distance=distance)

    local_idx = None
    used_local_index = False
    if source != SourceMode.PYPI:
        resolved_path = resolve_index_path(index_path)
        if source == SourceMode.LOCAL:
            local_idx = load_local_index(resolved_path, strict=True)
        else:
            local_idx = get_index(resolved_path, strict=False)
            if local_idx is None:
                warnings.append(f"Local index '{resolved_path}' unavailable; falling back to PyPI.")
        used_local_index = local_idx is not None

    exact_exists: bool | None = None
    exact_summary: str | None = None
    exact_version: str | None = None
    matches: list[Match] = []

    client = PyPIClient(timeout=timeout, concurrency=concurrency)
    headers = {"User-Agent": f"pkgname/{__version__}"}

    if source == SourceMode.LOCAL:
        assert local_idx is not None
        if norm_q in local_idx.names_set:
            exact_exists = True
        matches = scan_local_index_similarity(name, local_idx, sim_cfg)
        if exact_exists is None and not matches:
            warnings.append("offline check: not present in local piwheels snapshot; may exist on PyPI")
    elif source == SourceMode.AUTO:
        if local_idx is not None and norm_q in local_idx.names_set:
            exact_exists = True
            matches = scan_local_index_similarity(name, local_idx, sim_cfg)
        else:
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as http_client:
                info = await client.lookup(norm_q, client=http_client)
                exact_exists = info.exists
                exact_summary = info.summary
                exact_version = info.version
                if local_idx is not None:
                    matches = scan_local_index_similarity(name, local_idx, sim_cfg)
                else:
                    variants = generate_probe_variants(norm_q, sim_cfg)[:30]
                    tasks = [client.lookup(v, client=http_client) for v in variants]
                    results = await asyncio.gather(*tasks)
                    for var_name, var_info in zip(variants, results, strict=True):
                        if var_info.exists:
                            m = evaluate_candidate_similarity(name, var_name, sim_cfg, origin="pypi")
                            if m is not None:
                                m.summary = var_info.summary
                                m.version = var_info.version
                                matches.append(m)
    elif source == SourceMode.HYBRID:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as http_client:
            info = await client.lookup(norm_q, client=http_client)
            exact_exists = info.exists
            exact_summary = info.summary
            exact_version = info.version
        if local_idx is not None:
            matches = scan_local_index_similarity(name, local_idx, sim_cfg)
    else:  # SourceMode.PYPI
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as http_client:
            info = await client.lookup(norm_q, client=http_client)
            exact_exists = info.exists
            exact_summary = info.summary
            exact_version = info.version
            variants = generate_probe_variants(norm_q, sim_cfg)[:30]
            tasks = [client.lookup(v, client=http_client) for v in variants]
            results = await asyncio.gather(*tasks)
            for var_name, var_info in zip(variants, results, strict=True):
                if var_info.exists:
                    m = evaluate_candidate_similarity(name, var_name, sim_cfg, origin="pypi")
                    if m is not None:
                        m.summary = var_info.summary
                        m.version = var_info.version
                        matches.append(m)

    if exact_exists is True:
        existing_exact = next((m for m in matches if m.normalized == norm_q), None)
        if existing_exact is None:
            matches.insert(
                0,
                Match(
                    name=norm_q,
                    normalized=norm_q,
                    reason="exact match",
                    distance=0,
                    origin="local_index" if source == SourceMode.LOCAL else "pypi",
                    summary=exact_summary,
                    version=exact_version,
                ),
            )
        else:
            existing_exact.reason = "exact match"
            existing_exact.distance = 0
            if exact_summary:
                existing_exact.summary = exact_summary
            if exact_version:
                existing_exact.version = exact_version

    status = classify_verdict(exact_exists_on_pypi=exact_exists, matches=matches)

    should_recommend = recommend if recommend is not None else status in (Status.TAKEN, Status.REJECTED_SIMILAR)
    recommendations: list[Recommendation] = []
    if should_recommend:
        if source == SourceMode.LOCAL:
            recommendations = await recommend_alternatives(
                name,
                limit=limit,
                config=sim_cfg,
                local_index=local_idx,
                offline_only=True,
            )
        else:
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as http_client:

                async def _exists_check(cand: str) -> bool:
                    return await client.exists(cand, client=http_client)

                recommendations = await recommend_alternatives(
                    name,
                    limit=limit,
                    config=sim_cfg,
                    local_index=local_idx,
                    offline_only=False,
                    pypi_exists_fn=_exists_check,
                )

    return CheckResult(
        query=name,
        normalized=norm_q,
        status=status,
        source=source.value,
        used_local_index=used_local_index,
        warnings=warnings,
        matches=matches,
        recommendations=recommendations,
    )


# ---------------------------------------------------------------------------
# Plain-text rendering
# ---------------------------------------------------------------------------


def _render_human(result: CheckResult) -> None:
    print(f"STATUS: {result.status.value} ({result.normalized})")

    source_desc = {
        "auto": "local index (piwheels) + PyPI API" if result.used_local_index else "PyPI API only",
        "hybrid": "local index (piwheels) + PyPI API (hybrid)",
        "pypi": "PyPI API only (--source pypi)",
        "local": "offline only (--source local, piwheels snapshot)",
    }.get(result.source, result.source)
    print(f"source: {source_desc}")

    for w in result.warnings:
        print(f"Warning: {w}")

    if result.matches:
        print("\nConflicting / Similar Packages")
        header = f"{'Name':<32} {'Reason':<24} {'Dist':>4} {'Version':<12} Summary"
        print(header)
        print("-" * len(header))
        for m in result.matches:
            print(f"{m.name:<32} {m.reason:<24} {m.distance:>4} {(m.version or '-'):<12} {m.summary or '-'}")

    if result.recommendations:
        print("\nRecommended Alternatives")
        header = f"{'Name':<32} {'Strategy':<14} {'Score':>6} Verified"
        print(header)
        print("-" * len(header))
        for r in result.recommendations:
            verified_label = "offline-only" if r.verified_offline else "pypi-404"
            print(f"{r.name:<32} {r.strategy:<14} {r.score:>6.2f} {verified_label}")
    elif result.status in (Status.TAKEN, Status.REJECTED_SIMILAR):
        print("No alternative names found. Try re-running with --distance 1 or a more descriptive name.")


# ---------------------------------------------------------------------------
# argparse CLI
# ---------------------------------------------------------------------------


def _configure_logging(verbose: bool) -> None:
    level = "DEBUG" if verbose else "WARNING"
    logger.remove()
    logger.add(sys.stderr, level=level, format="{level}: {message}")


def _cmd_check(args: argparse.Namespace) -> int:
    _configure_logging(args.verbose)
    recommend = None
    if args.recommend is not None:
        recommend = args.recommend
    try:
        result = asyncio.run(
            _run_check(
                args.name,
                source=SourceMode(args.source),
                index_path=args.index,
                recommend=recommend,
                limit=args.limit,
                distance=args.distance,
                timeout=args.timeout,
                concurrency=args.concurrency,
            )
        )
        if args.json:
            print(result.to_json())
        else:
            _render_human(result)
        return result.status.exit_code
    except ValidationError as exc:
        if args.json:
            print(
                json.dumps(
                    {"schema_version": SCHEMA_VERSION, "error": str(exc)},
                    sort_keys=True,
                )
            )
        else:
            print(f"Invalid input: {exc}", file=sys.stderr)
        return 3
    except (PyPIClientError, IndexLoadError) as exc:
        if args.json:
            print(
                json.dumps(
                    {"schema_version": SCHEMA_VERSION, "error": str(exc)},
                    sort_keys=True,
                )
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 4
    except Exception as exc:
        if args.verbose:
            raise
        if args.json:
            print(
                json.dumps(
                    {"schema_version": SCHEMA_VERSION, "error": str(exc)},
                    sort_keys=True,
                )
            )
        else:
            print(
                f"Internal error: {exc} (report issues at {GITHUB_ISSUES_URL})",
                file=sys.stderr,
            )
        return 5


def _cmd_index(args: argparse.Namespace) -> int:
    _configure_logging(args.verbose)
    try:
        loaded = load_local_index(index_path=args.index, force_rebuild=args.rebuild, strict=True)
        assert loaded is not None
        st = loaded.stats()
        if args.json:
            print(
                json.dumps(
                    {
                        "source_path": st.source_path,
                        "file_size_bytes": st.file_size_bytes,
                        "sha256": st.sha256,
                        "entry_count": st.entry_count,
                        "cache_path": st.cache_path,
                        "cache_hit": st.cache_hit,
                        "load_time_ms": st.load_time_ms,
                    },
                    sort_keys=True,
                    indent=2,
                )
            )
        else:
            print(f"Source path:  {st.source_path}")
            print(f"File size:    {st.file_size_bytes} bytes")
            print(f"SHA-256:      {st.sha256}")
            print(f"Entry count:  {st.entry_count}")
            print(f"Cache path:   {st.cache_path}")
            print(f"Cache hit:    {st.cache_hit}")
            print(f"Load time:    {st.load_time_ms:.2f} ms")
        return 0
    except IndexLoadError as exc:
        print(f"Index error: {exc}", file=sys.stderr)
        return 4


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pkgname",
        description="Check PyPI package name availability, similarity, and alternative recommendations.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"pkgname {__version__}",
        help="Print version and exit.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- check ---------------------------------------------------------------
    p_check = sub.add_parser(
        "check",
        help="Check whether NAME is available on PyPI and suggest alternatives if not.",
    )
    p_check.add_argument("name", help="Candidate PyPI package name.")
    p_check.add_argument(
        "--source",
        choices=[m.value for m in SourceMode],
        default=SourceMode.AUTO.value,
        help="Data-source mode (default: auto).",
    )
    p_check.add_argument(
        "--index",
        type=Path,
        default=None,
        help="Path to pip.json (default: /sdcard/data/pip.json or $PKGNAME_INDEX).",
    )
    p_check.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON only.",
    )
    p_check.add_argument(
        "--recommend",
        dest="recommend",
        action="store_true",
        default=None,
        help="Force recommendations.",
    )
    p_check.add_argument(
        "--no-recommend",
        dest="recommend",
        action="store_false",
        help="Disable recommendations.",
    )
    p_check.add_argument(
        "--limit",
        type=int,
        default=10,
        choices=range(1, 101),
        metavar="N",
        help="Max recommendations (1-100, default: 10).",
    )
    p_check.add_argument(
        "--distance",
        type=int,
        default=2,
        choices=range(11),
        metavar="N",
        help="Levenshtein threshold (0-10, default: 2).",
    )
    p_check.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        metavar="SEC",
        help="Per-request timeout seconds (default: 10.0).",
    )
    p_check.add_argument(
        "--concurrency",
        type=int,
        default=10,
        choices=range(1, 101),
        metavar="N",
        help="Max in-flight requests (1-100, default: 10).",
    )
    p_check.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging to stderr.",
    )
    p_check.set_defaults(func=_cmd_check)

    # -- index ---------------------------------------------------------------
    p_index = sub.add_parser("index", help="Inspect or rebuild the local piwheels index cache.")
    p_index.add_argument(
        "--index",
        type=Path,
        default=None,
        help="Path to pip.json.",
    )
    p_index.add_argument(
        "--rebuild",
        action="store_true",
        help="Force cache rebuild (ignore mtime/sha).",
    )
    p_index.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable output.",
    )
    p_index.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging to stderr.",
    )
    p_index.set_defaults(func=_cmd_index)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
