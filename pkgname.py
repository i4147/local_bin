#!/data/data/com.termux/files/usr/bin/env python
"""pkgname: check whether a PyPI package name is available.

Usage::

    pkgname.py check NAME [--source auto|hybrid|pypi|local] [--json] ...
    pkgname.py index [--index PATH] [--rebuild] [--json]

Exit codes:
    0  AVAILABLE
    1  TAKEN
    2  REJECTED_SIMILAR
    3  Invalid input (bad characters, too long, ...)
    4  Network / PyPI error (or, in --source local, index missing/corrupt)
    5  Internal error (bug; surfaces a GitHub issue link)

Dependencies: httpx, rapidfuzz, tenacity (msgpack optional, speeds up cache).

Local index caveat: the index is a ``pip.json`` snapshot from
https://piwheels.org/packages.json. piwheels only lists packages it has
wheels for, so it is NOT the complete PyPI namespace. ``name in index`` means
almost certainly taken; ``name not in index`` means *unknown* and is confirmed
against the live PyPI API unless ``--source local`` was chosen.
"""

from __future__ import annotations
import argparse
import asyncio
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import keyword
import logging
import os
from pathlib import Path
import re
import sys
import time
from types import TracebackType
from typing import Any, Self

import httpx
from rapidfuzz import process
from rapidfuzz.distance import Levenshtein
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


_msgpack: Any
try:
    import msgpack as _msgpack
except ImportError:  # pragma: no cover - optional dependency
    _msgpack = None

__version__: str = "0.1.0"

logger = logging.getLogger("pkgname")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

SCHEMA_VERSION: int = 2
MAX_NAME_LENGTH: int = 214

DEFAULT_DISTANCE: int = 2
DEFAULT_LIMIT: int = 10
DEFAULT_TIMEOUT: float = 30.0
DEFAULT_CONCURRENCY: int = 8

DEFAULT_INDEX_PATH: str = "/sdcard/data/pip.json"
INDEX_ENV_VAR: str = "PKGNAME_INDEX"
CACHE_ENV_VAR: str = "PKGNAME_CACHE_DIR"

PYPI_BASE_URL: str = "https://pypi.org/pypi"
USER_AGENT: str = f"pkgname/{__version__}"
ISSUE_LINK: str = "https://github.com/example/pkgname/issues"

EXIT_INVALID: int = 3
EXIT_NETWORK: int = 4
EXIT_INTERNAL: int = 5

CONFUSABLE_AFFIXES: tuple[str, ...] = (
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

RECOMMEND_PREFIXES: tuple[str, ...] = ("py-", "python-", "get-")
RECOMMEND_SUFFIXES: tuple[str, ...] = (
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
    "-py",
    "2",
    "3",
)

SYNONYMS: dict[str, tuple[str, ...]] = {
    "fs": ("filesystem",),
    "walk": ("traverse", "scan", "iter"),
    "dir": ("directory",),
    "pkg": ("package",),
    "cfg": ("config",),
    "util": ("utils",),
    "db": ("database",),
}

KNOWN_BRANDS: frozenset[str] = frozenset({
    "requests",
    "numpy",
    "pandas",
    "django",
    "flask",
    "fastapi",
    "sqlalchemy",
    "pillow",
    "pytest",
    "click",
    "typer",
    "httpx",
    "pydantic",
    "matplotlib",
    "scipy",
    "tensorflow",
    "torch",
    "google",
    "microsoft",
    "apple",
    "amazon",
    "facebook",
    "twitter",
})

STRATEGY_RANK: dict[str, int] = {
    "separator": 0,
    "suffix": 1,
    "prefix": 2,
    "synonym": 3,
    "reversal": 3,
    "abbreviation": 4,
}
MAX_CANDIDATES: int = 2000

# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class Status(str, Enum):
    """Enumerate the supported Status values."""

    AVAILABLE = "AVAILABLE"
    TAKEN = "TAKEN"
    REJECTED_SIMILAR = "REJECTED_SIMILAR"


EXIT_CODES: dict[Status, int] = {
    Status.AVAILABLE: 0,
    Status.TAKEN: 1,
    Status.REJECTED_SIMILAR: 2,
}


class SourceMode(str, Enum):
    """Enumerate the supported SourceMode values."""

    auto = "auto"
    hybrid = "hybrid"
    pypi = "pypi"
    local = "local"


@dataclass
class Match:
    """Store name, normalized, reason, distance, summary and related state."""

    name: str
    normalized: str
    reason: str
    distance: int
    summary: str | None = None
    version: str | None = None
    origin: str = "local index (piwheels)"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict."""
        return {
            "name": self.name,
            "normalized": self.normalized,
            "reason": self.reason,
            "distance": self.distance,
            "summary": self.summary,
            "version": self.version,
            "origin": self.origin,
        }


@dataclass
class Recommendation:
    """Store name, normalized, strategy, score, verified_offline."""

    name: str
    normalized: str
    strategy: str
    score: float
    verified_offline: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict."""
        return {
            "name": self.name,
            "normalized": self.normalized,
            "strategy": self.strategy,
            "score": self.score,
            "verified_offline": self.verified_offline,
        }


@dataclass
class CheckResult:
    """Store query, normalized, status, source, used_local_index and related state."""

    query: str
    normalized: str
    status: Status
    source: str
    used_local_index: bool
    warnings: list[str] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict."""
        return {
            "schema_version": SCHEMA_VERSION,
            "query": self.query,
            "normalized": self.normalized,
            "status": self.status.value,
            "source": self.source,
            "used_local_index": self.used_local_index,
            "warnings": list(self.warnings),
            "matches": [m.to_dict() for m in self.matches],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class ProjectInfo:
    """Store name, normalized, summary, version."""

    name: str
    normalized: str
    summary: str | None = None
    version: str | None = None


@dataclass
class IndexStats:
    """Store source_path, file_size, sha256, entry_count, cache_path and related state."""

    source_path: str
    file_size: int
    sha256: str
    entry_count: int
    cache_path: str | None
    load_time_ms: float
    available: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict."""
        return {
            "source_path": self.source_path,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "entry_count": self.entry_count,
            "cache_path": self.cache_path,
            "load_time_ms": round(self.load_time_ms, 2),
            "available": self.available,
            "error": self.error,
        }


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class InvalidNameError(ValueError):
    """The supplied package name is not a valid PEP 508 name."""


class IndexUnavailableError(Exception):
    """The local index is missing, unreadable, or corrupt."""


class PyPIClientError(Exception):
    """A network or PyPI-side failure."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


# --------------------------------------------------------------------------
# Normalization & validation (PEP 503 / PEP 508)
# --------------------------------------------------------------------------

_NAME_RE = re.compile(r"[a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]")
_SEPARATOR_RE = re.compile(r"[-_.]+")


def normalize(name: str) -> str:
    """Normalize input."""
    return _SEPARATOR_RE.sub("-", name).lower()


def validate(name: str) -> None:
    if not name:
        raise InvalidNameError("package name must not be empty")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidNameError(f"package name is {len(name)} characters; PyPI allows at most {MAX_NAME_LENGTH}")
    if _NAME_RE.fullmatch(name) is None:
        raise InvalidNameError(
            f"invalid package name {name!r}: names must start and end with a letter "
            "or digit and may only contain letters, digits, '-', '_' and '.'"
        )


def warnings_for(normalized_name: str) -> list[str]:
    warnings: list[str] = []
    module_name = normalized_name.replace("-", "_")
    if module_name in sys.stdlib_module_names:
        warnings.append(f"{normalized_name!r} is a Python standard library module name")
    if normalized_name in keyword.kwlist:
        warnings.append(f"{normalized_name!r} is a Python keyword")
    if normalized_name in KNOWN_BRANDS:
        warnings.append(f"{normalized_name!r} is a well-known brand/project name")
    return warnings


# --------------------------------------------------------------------------
# Similarity heuristics
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SimilarityConfig:
    """Store distance, affixes."""

    distance: int = DEFAULT_DISTANCE
    affixes: tuple[str, ...] = CONFUSABLE_AFFIXES


def reason_for(query: str, candidate: str, distance: int, config: SimilarityConfig) -> str:
    if distance == 0:
        return "exact match"
    if query.replace("-", "") == candidate.replace("-", ""):
        return "separator variant"
    for affix in config.affixes:
        if candidate == query + affix or query == candidate + affix:
            return f"suffix variant `{affix}`"
        if candidate == affix + query or query == affix + candidate:
            return f"prefix variant `{affix}`"
    return f"edit distance {distance}"


def _length_filtered(
    query: str,
    names: Sequence[str],
    distance: int,
    buckets: dict[int, list[str]] | None = None,
) -> list[str]:
    if buckets is not None:
        pool: list[str] = []
        for length in range(max(0, len(query) - distance), len(query) + distance + 1):
            pool.extend(buckets.get(length, ()))
        return pool
    return [n for n in names if abs(len(n) - len(query)) <= distance]


def find_similar(
    query: str,
    names_list: Sequence[str],
    names_set: set[str],
    config: SimilarityConfig,
    *,
    origin: str = "local index (piwheels)",
    limit: int = 50,
    length_buckets: dict[int, list[str]] | None = None,
) -> list[Match]:
    """Find similar."""
    found: dict[str, Match] = {}

    def add(candidate: str) -> None:
        if candidate in found:
            return
        d = Levenshtein.distance(query, candidate)
        found[candidate] = Match(
            name=candidate,
            normalized=candidate,
            reason=reason_for(query, candidate, d, config),
            distance=d,
            origin=origin,
        )

    pool = _length_filtered(query, names_list, config.distance, length_buckets)
    if pool:
        hits = process.extract(
            query,
            pool,
            scorer=Levenshtein.distance,
            limit=limit,
            score_cutoff=config.distance,
        )
        for candidate, _score, _idx in hits:
            add(candidate)

    for affix in config.affixes:
        for variant in (query + affix, affix + query):
            if variant in names_set:
                add(variant)
        if query.startswith(affix) and len(query) > len(affix):
            stripped = query[len(affix) :]
            if stripped in names_set:
                add(stripped)
        if query.endswith(affix) and len(query) > len(affix):
            stripped = query[: -len(affix)]
            if stripped in names_set:
                add(stripped)

    return sorted(found.values(), key=lambda m: (m.distance, m.normalized))[:limit]


def is_too_similar(
    candidate: str,
    names_list: Sequence[str],
    names_set: set[str],
    config: SimilarityConfig,
    *,
    length_buckets: dict[int, list[str]] | None = None,
) -> bool:
    """Return whether too similar."""
    if candidate in names_set:
        return True
    for affix in config.affixes:
        if candidate + affix in names_set or affix + candidate in names_set:
            return True
        if candidate.startswith(affix) and candidate[len(affix) :] in names_set:
            return True
        if candidate.endswith(affix) and candidate[: -len(affix)] in names_set:
            return True
    pool = _length_filtered(candidate, names_list, config.distance, length_buckets)
    if pool:
        hit = process.extractOne(candidate, pool, scorer=Levenshtein.distance, score_cutoff=config.distance)
        if hit is not None:
            return True
    return False


def segment_with_keys(name: str, keys: Sequence[str]) -> list[str] | None:
    """Split ``name`` into a concatenation of ``keys`` (longest-first), or None."""
    ordered = sorted(keys, key=len, reverse=True)
    n = len(name)
    dp: list[list[str] | None] = [None] * (n + 1)
    dp[n] = []
    for i in range(n - 1, -1, -1):
        for key in ordered:
            if name.startswith(key, i):
                rest = dp[i + len(key)]
                if rest is not None:
                    dp[i] = [key, *rest]
                    break
    return dp[0]


def generate_variants(name: str, config: SimilarityConfig) -> list[tuple[str, str]]:
    """Generate variants."""
    variants: dict[str, str] = {}

    def add(variant: str, hint: str) -> None:
        if variant and variant != name:
            variants.setdefault(variant, hint)

    segments = name.split("-")
    if len(segments) == 1:
        split = segment_with_keys(name, list(SYNONYMS))
        if split and len(split) > 1:
            add("-".join(split), "separator variant")
    else:
        add("".join(segments), "separator variant")

    for affix in config.affixes:
        add(name + affix, f"suffix variant `{affix}`")
        add(affix + name, f"prefix variant `{affix}`")
        if name.startswith(affix) and len(name) > len(affix):
            add(name[len(affix) :], f"prefix variant `{affix}`")
        if name.endswith(affix) and len(name) > len(affix):
            add(name[: -len(affix)], f"suffix variant `{affix}`")

    for i in range(len(name)):
        add(name[:i] + name[i + 1 :], "edit distance 1")

    return sorted(variants.items())


def classify(*, exact: bool, matches: Sequence[Match]) -> Status:
    if exact:
        return Status.TAKEN
    if matches:
        return Status.REJECTED_SIMILAR
    return Status.AVAILABLE


# --------------------------------------------------------------------------
# PyPI client
# --------------------------------------------------------------------------


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, PyPIClientError) and exc.retryable


class PyPIClient:
    """Async PyPI JSON API client with retries, LRU cache, bounded concurrency."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        concurrency: int = 10,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = PYPI_BASE_URL,
        max_retries: int = 3,
        backoff_multiplier: float = 1.0,
        cache_size: int = 4096,
    ) -> None:
        """Initialize the instance state from the supplied arguments."""
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
            base_url=base_url,
            follow_redirects=True,
        )
        self._semaphore = asyncio.Semaphore(concurrency)
        self._max_retries = max_retries
        self._backoff_multiplier = backoff_multiplier
        self._cache_size = cache_size
        self._cache: OrderedDict[str, ProjectInfo | None] = OrderedDict()

    async def __aenter__(self) -> Self:
        """Enter the async managed context and return its resource."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Release resources acquired by the async managed context."""
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def get_project(self, normalized_name: str) -> ProjectInfo | None:
        """Return project."""
        if normalized_name in self._cache:
            self._cache.move_to_end(normalized_name)
            return self._cache[normalized_name]
        info = await self._get_with_retry(normalized_name)
        self._cache[normalized_name] = info
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return info

    async def probe_variants(self, name: str, config: SimilarityConfig) -> list[Match]:
        variants = generate_variants(name, config)

        async def probe(variant: str) -> Match | None:
            info = await self.get_project(variant)
            if info is None:
                return None
            distance = Levenshtein.distance(name, variant)
            return Match(
                name=info.name,
                normalized=variant,
                reason=reason_for(name, variant, distance, config),
                distance=distance,
                summary=info.summary,
                version=info.version,
                origin="pypi",
            )

        results = await asyncio.gather(*(probe(v) for v, _hint in variants))
        return sorted(
            (r for r in results if r is not None),
            key=lambda m: (m.distance, m.normalized),
        )

    async def _get_with_retry(self, normalized_name: str) -> ProjectInfo | None:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(
                multiplier=self._backoff_multiplier,
                min=self._backoff_multiplier,
                max=8 * self._backoff_multiplier,
            ),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                return await self._get_once(normalized_name)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _get_once(self, normalized_name: str) -> ProjectInfo | None:
        async with self._semaphore:
            try:
                response = await self._client.get(f"/{normalized_name}/json")
            except httpx.TimeoutException as exc:
                raise PyPIClientError(f"request to PyPI timed out: {exc}", retryable=True) from exc
            except httpx.TransportError as exc:
                raise PyPIClientError(f"network error contacting PyPI: {exc}", retryable=True) from exc
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError as exc:
                raise PyPIClientError(f"invalid JSON from PyPI for {normalized_name!r}", retryable=False) from exc
            info = payload.get("info") or {}
            return ProjectInfo(
                name=str(info.get("name") or normalized_name),
                normalized=normalized_name,
                summary=info.get("summary") or None,
                version=info.get("version") or None,
            )
        if response.status_code == 404:
            return None
        if response.status_code == 429 or response.status_code >= 500:
            raise PyPIClientError(
                f"PyPI returned {response.status_code} for {normalized_name!r} (rate limited or server error)",
                retryable=True,
                status_code=response.status_code,
            )
        raise PyPIClientError(
            f"PyPI returned {response.status_code} for {normalized_name!r}",
            retryable=False,
            status_code=response.status_code,
        )


# --------------------------------------------------------------------------
# Local piwheels index
# --------------------------------------------------------------------------

# Entries look like ["name", 0, 26]; none contain nested arrays, so a regex
# over a rolling buffer is both streaming-friendly and much faster than a
# per-character tokenizer.
_ENTRY_RE = re.compile(r'\[\s*"((?:[^"\\]|\\.)*)"[^\[\]]*\]')
_CARRY_LIMIT: int = 1 << 16


def resolve_index_path(path: Path | str | None = None) -> Path:
    """Resolve index path."""
    if path is not None:
        return Path(path)
    env = os.environ.get(INDEX_ENV_VAR)
    if env:
        return Path(env)
    return Path(DEFAULT_INDEX_PATH)


def default_cache_dir() -> Path:
    override = os.environ.get(CACHE_ENV_VAR)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "pkgname"


class LocalIndex:
    """Streaming loader + persistent cache for a piwheels ``pip.json``."""

    def __init__(
        self,
        path: Path | str,
        *,
        cache_dir: Path | str | None = None,
        use_cache: bool = True,
    ) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.path = Path(path)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self.use_cache = use_cache
        self._names_set: set[str] | None = None
        self._names_list: list[str] = []
        self._length_buckets: dict[int, list[str]] | None = None
        self.available = False
        self.error: str | None = None
        self._sha256 = ""
        self._file_size = 0
        self._load_time_ms = 0.0
        self._cache_path: Path | None = None
        self._loaded = False

    def load(self) -> LocalIndex:
        if self._loaded:
            return self
        self._loaded = True
        start = time.perf_counter()

        def elapsed() -> float:
            return (time.perf_counter() - start) * 1000

        if not self.path.is_file():
            self.error = f"index file not found: {self.path}"
            logger.warning(self.error)
            self._load_time_ms = elapsed()
            return self

        try:
            self._file_size = self.path.stat().st_size
            self._sha256 = self._hash_source()
        except OSError as exc:
            self.error = f"index file unreadable: {exc}"
            logger.warning(self.error)
            self._load_time_ms = elapsed()
            return self

        self._cache_path = self._cache_file_for(self._sha256)
        if self.use_cache and self._try_load_cache():
            self.available = True
            self._load_time_ms = elapsed()
            logger.debug("loaded %d names from cache %s", len(self._names_list), self._cache_path)
            return self

        names = self._parse_source()
        if not names:
            self.error = "index file appears corrupt or empty (no package entries parsed)"
            logger.warning("%s: %s", self.error, self.path)
            self._load_time_ms = elapsed()
            return self

        self._names_list = names
        self.available = True
        self._write_cache()
        self._load_time_ms = elapsed()
        logger.debug(
            "parsed %d names from %s in %.1f ms",
            len(names),
            self.path,
            self._load_time_ms,
        )
        return self

    def __contains__(self, normalized_name: str) -> bool:
        """Return whether the supplied item is present."""
        return normalized_name in self.names_set

    @property
    def names_set(self) -> set[str]:
        """Return the names set."""
        if self._names_set is None:
            self._names_set = set(self._names_list)
        return self._names_set

    @property
    def names_list(self) -> list[str]:
        """Return the names list."""
        return self._names_list

    @property
    def length_buckets(self) -> dict[int, list[str]]:
        """Return the length buckets."""
        if self._length_buckets is None:
            buckets: dict[int, list[str]] = {}
            for name in self._names_list:
                buckets.setdefault(len(name), []).append(name)
            self._length_buckets = buckets
        return self._length_buckets

    def stats(self) -> IndexStats:
        if not self._loaded:
            self.load()
        return IndexStats(
            source_path=str(self.path),
            file_size=self._file_size,
            sha256=self._sha256,
            entry_count=len(self._names_list),
            cache_path=str(self._cache_path) if self._cache_path else None,
            load_time_ms=self._load_time_ms,
            available=self.available,
            error=self.error,
        )

    # -- internals ---------------------------------------------------------

    def _hash_source(self) -> str:
        digest = hashlib.sha256()
        with self.path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _cache_file_for(self, sha256: str) -> Path:
        suffix = "msgpack" if _msgpack is not None else "txt"
        return self.cache_dir / f"index-{sha256}.{suffix}"

    def _try_load_cache(self) -> bool:
        cache_path = self._cache_path
        if cache_path is None or not cache_path.is_file():
            return False
        try:
            names: list[str]
            if _msgpack is not None and cache_path.suffix == ".msgpack":
                data = _msgpack.unpackb(cache_path.read_bytes(), raw=False)
                meta: dict[str, Any] = {k: data[k] for k in ("sha256", "size")}
                names = list(data["names"])
            else:
                lines = cache_path.read_text(encoding="utf-8").splitlines()
                meta = json.loads(lines[0])
                names = lines[1:]
            if meta.get("sha256") != self._sha256 or meta.get("size") != self._file_size:
                return False
            if not names:
                return False
            self._names_list = names
            return True
        except Exception as exc:  # corrupt cache is never fatal
            logger.debug("ignoring unreadable cache %s: %s", cache_path, exc)
            return False

    def _write_cache(self) -> None:
        cache_path = self._cache_path
        if cache_path is None:
            return
        meta = {"v": 1, "sha256": self._sha256, "size": self._file_size}
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_name(cache_path.name + ".tmp")
            if _msgpack is not None:
                tmp.write_bytes(_msgpack.packb({**meta, "names": self._names_list}))
            else:
                tmp.write_text(
                    json.dumps(meta) + "\n" + "\n".join(self._names_list) + "\n",
                    encoding="utf-8",
                )
            tmp.replace(cache_path)
        except OSError as exc:
            logger.warning("could not write index cache %s: %s", cache_path, exc)

    def _parse_source(self) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for raw in self._iter_raw_names(fh):
                    name = normalize(raw)
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("error reading index %s: %s", self.path, exc)
            return []
        return names

    @staticmethod
    def _iter_raw_names(fh: Any) -> Iterator[str]:
        carry = ""
        for chunk in iter(lambda: fh.read(1 << 20), ""):
            buf = carry + chunk
            last_end = 0
            for match in _ENTRY_RE.finditer(buf):
                last_end = match.end()
                raw = match.group(1)
                if "\\" in raw:
                    try:
                        raw = json.loads(f'"{raw}"')
                    except json.JSONDecodeError:
                        continue
                yield raw
            carry = buf[last_end:][-_CARRY_LIMIT:]


_instances: dict[str, LocalIndex] = {}


def get_index(path: Path | str | None = None) -> LocalIndex:
    """Return index."""
    resolved = resolve_index_path(path)
    key = str(resolved)
    if key not in _instances:
        _instances[key] = LocalIndex(resolved).load()
    return _instances[key]


# --------------------------------------------------------------------------
# Recommendations
# --------------------------------------------------------------------------


def _segments(name: str) -> list[str]:
    parts = name.split("-")
    if len(parts) == 1:
        split = segment_with_keys(name, list(SYNONYMS))
        if split and len(split) > 1:
            return split
    return parts


def _drop_trailing_vowels(segment: str) -> str:
    result = segment
    while len(result) > 1 and result[-1] in "aeiou":
        result = result[:-1]
    return result


def _drop_vowels(segment: str) -> str:
    return "".join(ch for ch in segment if ch not in "aeiou")


def generate_candidates(name: str) -> list[tuple[str, str]]:
    """Generate candidates."""
    candidates: dict[str, str] = {}

    def add(candidate: str, strategy: str) -> None:
        normalized = normalize(candidate).strip("-")
        if normalized and normalized != name and len(normalized) <= MAX_NAME_LENGTH and normalized not in candidates:
            candidates[normalized] = strategy

    segments = _segments(name)

    if len(segments) > 1:
        add("".join(segments), "separator")
        add("-".join(segments), "separator")

    for suffix in RECOMMEND_SUFFIXES:
        add(name + suffix, "suffix")
    for prefix in RECOMMEND_PREFIXES:
        add(prefix + name, "prefix")

    for i, segment in enumerate(segments):
        for synonym in SYNONYMS.get(segment, ()):
            replaced = segments.copy()
            replaced[i] = synonym
            add("-".join(replaced), "synonym")

    if len(segments) > 1:
        add("-".join(reversed(segments)), "reversal")

    add("-".join(_drop_trailing_vowels(s) for s in segments), "abbreviation")
    vowelless = [_drop_vowels(s) for s in segments]
    if all(len(s) >= 2 for s in vowelless):
        add("-".join(vowelless), "abbreviation")

    ordered = sorted(candidates.items(), key=lambda kv: (STRATEGY_RANK.get(kv[1], 5), kv[0]))
    return ordered[:MAX_CANDIDATES]


def score_candidate(original: str, candidate: str, strategy: str) -> float:
    """Score candidate."""
    distance = Levenshtein.distance(original, candidate)
    rank = STRATEGY_RANK.get(strategy, 5)
    score = 1.0 - 0.08 * distance - 0.04 * rank - 0.005 * len(candidate)
    return round(max(0.0, score), 4)


async def recommend(
    name: str,
    *,
    index: LocalIndex | None,
    client: PyPIClient | None,
    sim_config: SimilarityConfig,
    limit: int,
) -> list[Recommendation]:
    """Return up to ``limit`` alternatives that look available.

    Phase A filters against the local index; Phase B confirms against PyPI
    (404 + variant probe) whenever a client is supplied. Without a client the
    result is only verified against the (incomplete) local snapshot.
    """
    survivors: list[tuple[str, str]] = []
    for candidate, strategy in generate_candidates(name):
        if (
            index is not None
            and index.available
            and is_too_similar(
                candidate,
                index.names_list,
                index.names_set,
                sim_config,
                length_buckets=index.length_buckets,
            )
        ):
            continue
        survivors.append((candidate, strategy))

    survivors.sort(
        key=lambda cs: (
            -score_candidate(name, cs[0], cs[1]),
            Levenshtein.distance(name, cs[0]),
            STRATEGY_RANK.get(cs[1], 5),
            len(cs[0]),
            cs[0],
        )
    )

    results: list[Recommendation] = []
    for candidate, strategy in survivors:
        if len(results) >= limit:
            break
        if client is not None:
            if await client.get_project(candidate) is not None:
                continue
            if await client.probe_variants(candidate, sim_config):
                continue
        results.append(
            Recommendation(
                name=candidate,
                normalized=candidate,
                strategy=strategy,
                score=score_candidate(name, candidate, strategy),
                verified_offline=client is None,
            )
        )
    return results


# --------------------------------------------------------------------------
# Check workflow
# --------------------------------------------------------------------------


def _wants_recommendations(flag: bool | None, status: Status) -> bool:
    return flag if flag is not None else status != Status.AVAILABLE


def _local_match(normalized: str) -> Match:
    return Match(name=normalized, normalized=normalized, reason="exact match", distance=0)


async def _pypi_exact(normalized: str, client: PyPIClient) -> Match | None:
    info = await client.get_project(normalized)
    if info is None:
        return None
    return Match(
        name=info.name,
        normalized=normalized,
        reason="exact match",
        distance=0,
        summary=info.summary,
        version=info.version,
        origin="pypi",
    )


async def _exact_check(
    source: str,
    normalized: str,
    index: LocalIndex | None,
    client: PyPIClient | None,
) -> Match | None:
    in_index = index is not None and index.available and normalized in index

    if source == "local":
        return _local_match(normalized) if in_index else None

    if source == "auto":
        if in_index:
            return _local_match(normalized)
        return await _pypi_exact(normalized, client) if client is not None else None

    if source == "hybrid":
        if client is not None:
            match = await _pypi_exact(normalized, client)
            if match is not None:
                return match
        return _local_match(normalized) if in_index else None

    # source == "pypi"
    return await _pypi_exact(normalized, client) if client is not None else None


async def run_check(
    name: str,
    *,
    source: str,
    index_path: Path | None,
    recommend_flag: bool | None,
    limit: int,
    distance: int,
    timeout: float,
    concurrency: int,
) -> CheckResult:
    """Run check."""
    validate(name)
    normalized = normalize(name)
    sim_config = SimilarityConfig(distance=distance)
    result = CheckResult(
        query=name,
        normalized=normalized,
        status=Status.AVAILABLE,
        source=source,
        used_local_index=False,
        warnings=warnings_for(normalized),
    )

    index: LocalIndex | None = None
    if source in ("auto", "hybrid", "local"):
        index = get_index(index_path)
        if index.available:
            result.used_local_index = True
        elif source == "local":
            raise IndexUnavailableError(f"local index unavailable: {index.error}")
        else:
            result.warnings.append(f"local index unavailable ({index.error}); falling back to PyPI API")
    live_index = index if index is not None and index.available else None

    client: PyPIClient | None = None
    try:
        if source != "local":
            client = PyPIClient(timeout=timeout, concurrency=concurrency)

        exact = await _exact_check(source, normalized, index, client)

        matches: list[Match] = []
        if exact is not None:
            matches = [exact]
        elif live_index is not None:
            matches = find_similar(
                normalized,
                live_index.names_list,
                live_index.names_set,
                sim_config,
                length_buckets=live_index.length_buckets,
            )
        elif client is not None:
            matches = await client.probe_variants(normalized, sim_config)

        result.matches = matches
        result.status = classify(exact=exact is not None, matches=matches)

        if source == "local" and result.status == Status.AVAILABLE:
            result.warnings.append("offline check: not present in local piwheels snapshot; may exist on PyPI")

        if _wants_recommendations(recommend_flag, result.status):
            try:
                result.recommendations = await recommend(
                    normalized,
                    index=live_index,
                    client=client,
                    sim_config=sim_config,
                    limit=limit,
                )
            except PyPIClientError as exc:
                # Keep the verdict; never show unverified alternatives.
                result.warnings.append(f"could not verify alternatives on PyPI: {exc}")
    finally:
        if client is not None:
            await client.close()
    return result


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def _source_line(result: CheckResult) -> str:
    if result.source == "local":
        return "offline only (local piwheels snapshot)"
    if result.source == "pypi":
        return "PyPI API only"
    if result.used_local_index:
        return "local index (piwheels) + PyPI API"
    return "PyPI API (local index unavailable)"


def _render(result: CheckResult, as_json: bool, *, recommendations_requested: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return

    print(f"{result.status.value}: {result.query}")
    print(f"source: {_source_line(result)}")
    for warning in result.warnings:
        print(f"warning: {warning}")
    if result.matches:
        print("conflicts:")
        for match in result.matches:
            print(f"  {match.name} ({match.reason})")
    if result.recommendations:
        print("alternatives: " + ", ".join(r.name for r in result.recommendations))
    elif recommendations_requested and result.status != Status.AVAILABLE:
        print("alternatives: none found (try a longer name or --distance 1)")


def _emit_error(query: str, message: str, as_json: bool) -> None:
    if as_json:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "query": query,
            "normalized": None,
            "status": "ERROR",
            "source": None,
            "used_local_index": False,
            "warnings": [],
            "matches": [],
            "recommendations": [],
            "errors": [message],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"error: {message}", file=sys.stderr)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    """Run the check subcommand."""
    name: str = args.name
    as_json: bool = args.json
    try:
        result = asyncio.run(
            run_check(
                name,
                source=args.source,
                index_path=args.index,
                recommend_flag=args.recommend,
                limit=args.limit,
                distance=args.distance,
                timeout=args.timeout,
                concurrency=args.concurrency,
            )
        )
    except InvalidNameError as exc:
        _emit_error(name, str(exc), as_json)
        return EXIT_INVALID
    except (PyPIClientError, IndexUnavailableError) as exc:
        _emit_error(name, str(exc), as_json)
        return EXIT_NETWORK
    except Exception as exc:
        if args.verbose:
            raise
        _emit_error(
            name,
            f"internal error: {exc} (please report a bug: {ISSUE_LINK})",
            as_json,
        )
        return EXIT_INTERNAL

    _render(
        result,
        as_json,
        recommendations_requested=_wants_recommendations(args.recommend, result.status),
    )
    return EXIT_CODES[result.status]


def cmd_index(args: argparse.Namespace) -> int:
    """Run the index subcommand."""
    try:
        idx = LocalIndex(resolve_index_path(args.index), use_cache=not args.rebuild).load()
        stats = idx.stats()
    except Exception as exc:
        if args.verbose:
            raise
        _emit_error("", f"internal error: {exc} (please report a bug: {ISSUE_LINK})", args.json)
        return EXIT_INTERNAL

    if args.json:
        print(json.dumps(stats.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"source path: {stats.source_path}")
        print(f"file size:   {stats.file_size:,} bytes")
        print(f"sha256:      {stats.sha256 or '-'}")
        print(f"entries:     {stats.entry_count:,}")
        print(f"cache path:  {stats.cache_path or '-'}")
        print(f"load time:   {stats.load_time_ms:.1f} ms")
        print(f"available:   {stats.available}")
        if stats.error:
            print(f"error:       {stats.error}")
    return 0 if idx.available else EXIT_NETWORK


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------


def _int_at_least(minimum: int) -> Any:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from exc
        if number < minimum:
            raise argparse.ArgumentTypeError(f"must be >= {minimum}")
        return number

    return parse


def _float_at_least(minimum: float) -> Any:
    def parse(value: str) -> float:
        try:
            number = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid number: {value!r}") from exc
        if number < minimum:
            raise argparse.ArgumentTypeError(f"must be >= {minimum}")
        return number

    return parse


def build_parser() -> argparse.ArgumentParser:
    """Build parser."""
    parser = argparse.ArgumentParser(
        prog="pkgname",
        description="Check whether a PyPI package name is available.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", metavar="{check,index}")

    check = sub.add_parser("check", help="Check a package name.")
    check.add_argument("name", help="Package name to check.")
    check.add_argument(
        "--source",
        choices=[m.value for m in SourceMode],
        default=SourceMode.auto.value,
        help="Data-source mode (default: %(default)s).",
    )
    check.add_argument("--index", type=Path, default=None, help="Path to pip.json.")
    check.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")
    check.add_argument(
        "--recommend",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force or disable recommendations (default: only when not AVAILABLE).",
    )
    check.add_argument(
        "--limit",
        type=_int_at_least(1),
        default=DEFAULT_LIMIT,
        help="Max recommendations (default: %(default)s).",
    )
    check.add_argument(
        "--distance",
        type=_int_at_least(0),
        default=DEFAULT_DISTANCE,
        help="Levenshtein threshold (default: %(default)s).",
    )
    check.add_argument(
        "--timeout",
        type=_float_at_least(0.1),
        default=DEFAULT_TIMEOUT,
        help="Per-request timeout in seconds (default: %(default)s).",
    )
    check.add_argument(
        "--concurrency",
        type=_int_at_least(1),
        default=DEFAULT_CONCURRENCY,
        help="Max in-flight requests (default: %(default)s).",
    )
    check.add_argument("-v", "--verbose", action="store_true", help="Verbose logging to stderr.")
    check.set_defaults(func=cmd_check)

    index = sub.add_parser("index", help="Show local index statistics.")
    index.add_argument("--index", type=Path, default=None, help="Path to pip.json.")
    index.add_argument("--rebuild", action="store_true", help="Ignore cache and rebuild.")
    index.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    index.add_argument("-v", "--verbose", action="store_true", help="Verbose logging to stderr.")
    index.set_defaults(func=cmd_index)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 0
    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    func: Any = args.func
    code: int = func(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
