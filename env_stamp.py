#!/data/data/com.termux/files/usr/bin/env python
"""env-stamp: reproducible environment fingerprints -- capture, diff, bisect.

"It works on my machine" usually starts with "here is my ``pip freeze``" --
200 lines that nobody diffs.  The interesting facts (Python build, glibc,
locale, which env vars *differ*) are invisible, and when you do diff you get
60 changes and no idea which one matters.

``env-stamp`` turns an environment into a small, redacted, hash-stamped JSON
"fingerprint", ranks the differences between two of them, and -- going one
step beyond a diff -- *bisects* them: it rebuilds candidate environments in a
throwaway venv and searches for the smallest change set that flips a test
from pass to fail.

Prior art
---------
* ``pip freeze`` / ``uv pip list``: versions only; no OS, no env, no redaction.
* ``envstamp`` (PyPI, https://pypi.org/project/envstamp/): atomic SHA-256
  fingerprints of installed *packages*; does not capture env vars, OS/libc
  info, or offer a diff/bisect workflow.
* ``script-bisect`` (https://github.com/ianhi/script-bisect): bisects *git
  commits* of a dependency; not environment deltas.
* CPython's ``test.bisect``: finds failing tests in the CPython suite.

The capture -> ranked-diff -> environment-bisect pipeline implemented here
is, to the author's knowledge, not available elsewhere on PyPI.

Quick start (CLI)
-----------------
::

    env-stamp capture --out good.json          # on the machine where it works
    env-stamp capture --out broken.json        # on the machine where it fails
    env-stamp diff good.json broken.json       # ranked, grouped by relevance
    cat broken.json | env-stamp diff good.json --stdin

    env-stamp bisect --baseline good.json \\
        --test "pytest -x tests/test_x.py" --venv .venv

Quick start (library)
---------------------
>>> import env_stamp
>>> stamp = env_stamp.capture()                       # current environment
>>> other = env_stamp.Stamp.from_dict(json.loads(path.read_text()))
>>> for change in env_stamp.diff_stamps(other, stamp):
...     print(change.rank, change.detail)

Capture format (schema 1)
-------------------------
A JSON document shaped like::

    {"schema": 1, "captured_at": "2026-11-09T14:02:11Z", "host": "build-07",
     "python": {"version": "3.12.7", "implementation": "cpython",
                "abiflags": "", "executable_kind": "venv",
                "platform_tag": "manylinux_2_39_x86_64"},
     "os": {"name": "linux", "distro": "ubuntu 24.04", "kernel": "6.8.0-45",
            "libc": "glibc 2.39", "locale": "en_US.UTF-8",
            "fs_case_sensitive": true, "umask": "0o022"},
     "packages": [{"name": "numpy", "version": "2.1.3", "direct": true,
                   "installer": "uv", "wheel_tag": "...", "origin": ""}],
     "env_keys": ["DATABASE_URL", "TZ"],
     "env_redacted": {"TZ": "UTC"},
     "tools": {"pip": "24.2", "uv": "0.5.2", "gcc": "13.2.0"},
     "hash": "blake2b:9f2c..."}

``env_keys`` always records env-var *names* only.  Values are recorded in
``env_redacted`` only for explicitly allow-listed, non-sensitive keys (by
default ``TZ``, ``LANG``, ``PYTHONHASHSEED``).  ``hash`` is a BLAKE2b digest
over the semantic content (everything except ``captured_at``/``host``), so
two captures of the same environment hash identically.

Redaction policy (default on)
-----------------------------
* Any key whose *name* matches ``(?i)key|token|secret|pass|cred|auth|dsn|url``
  never has its value recorded -- regardless of allow-lists.
* Values that look like well-known token prefixes (``ghp_``, ``sk-``,
  ``AKIA...``) are dropped.
* High-entropy values (>= 4.5 bits/char over >= 16 chars) are dropped even
  under innocent names.
* Values that survive and contain ``scheme://user:pass@host`` have the
  password masked as ``***``.
* ``--no-redact`` records everything, but must be given explicitly and prints
  a loud warning.  ``--no-paths`` strips editable/``file://`` origins.

Diff ranking heuristic (rank 1 = most likely relevant)
------------------------------------------------------
1. Interpreter major/minor/build, ``sys.abiflags``, glibc/musl version.
2. Packages present/absent -- binary/C-extension packages (numpy,
   cryptography, pydantic-core, lxml, ...) first; same version with a
   different wheel tag counts here too.
3. Packages with a changed version (major changes first); distro/kernel.
4. Locale, ``LANG``, ``TZ``/``PYTHONHASHSEED`` values, filesystem
   case-sensitivity, umask, build-tool versions.
5. Env-var *names* only -- values are never recorded, so never diffed.

Bisect algorithm
----------------
``env-stamp bisect`` compares a *baseline* stamp (the known-good machine)
with the current environment (or ``--venv``), then:

1. Refuses outright when the difference is C-level/system-level (libc,
   interpreter major/minor, OS) -- it would rather say "cannot do this"
   than produce a wrong answer.
2. Rebuilds the baseline package set in a throwaway venv (never activating
   it; packages are read via ``importlib.metadata.distributions(path=...)``)
   and verifies the test passes there.
3. Probes one axis at a time -- packages, then interpreter patch version
   (only if a matching interpreter is on PATH), then env vars.
4. Delta-debugs (ddmin) the culprit axis down to the minimal change set
   that flips the test to FAIL, with results cached by candidate hash and
   a hard cap of ``--max-runs`` test executions.
5. If no single axis reproduces, falls back to a combined minimisation and
   reports an interaction.

``--parallel`` parallelises *only* the axis-level probes (each in its own
venv); within-axis minimisation always runs sequentially against a single
shared venv.  Nothing is ever uploaded: ``diff`` reads local files and
``bisect`` only talks to the local pip/uv.

Exit codes
----------
* ``capture``: 0 ok, 2 error.
* ``diff``: 0 identical, 1 differences found, 2 error.
* ``bisect``: 0 culprit found, 1 not found / not reproducible, 2 refused
  (system-level difference), 3 baseline fails, 4 run budget exhausted.

Limitations
-----------
* Bisect pins packages as ``name==version``; editable/``file://`` origins
  are noted but not reproduced.
* Env-var bisection can only *set* values that were recorded (the safe,
  allow-listed ones).  Unknown values are documented blind spots.
* The interpreter axis needs a matching interpreter binary on PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from importlib.metadata import Distribution, distributions
from pathlib import Path
from typing import Any, ClassVar, TypeVar
from venv import create as _venv_create

if sys.version_info < (3, 12):  # pragma: no cover - exercised only on old pythons
    raise SystemExit("env-stamp requires Python 3.12 or newer")

__version__ = "1.0.0"

__all__ = [
    "SCHEMA_VERSION",
    "SAFE_VALUE_KEYS",
    "SENSITIVE_NAME_RE",
    "URL_CREDENTIALS_RE",
    "TOKEN_PREFIX_RE",
    "BINARY_PACKAGES",
    "TOOL_PROBES",
    "ENTROPY_LIMIT",
    "ENTROPY_MIN_LENGTH",
    "DEFAULT_MAX_DIFF_LINES",
    "DEFAULT_MAX_RUNS",
    "Change",
    "PythonInfo",
    "OSInfo",
    "PackageInfo",
    "Stamp",
    "Pin",
    "EnvProfile",
    "Candidate",
    "BisectReport",
    "Bisector",
    "canonicalize_name",
    "shannon_entropy",
    "mask_url_credentials",
    "redact_env",
    "capture",
    "stamp_digest",
    "diff_stamps",
    "render_terminal",
    "render_markdown",
    "render_json",
    "build_parser",
    "main",
]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Schema version of the stamp JSON produced by :func:`capture`.
SCHEMA_VERSION: int = 1

#: Env-var names whose *values* are safe to record (meaningful, never secret).
SAFE_VALUE_KEYS: frozenset[str] = frozenset({"TZ", "LANG", "PYTHONHASHSEED"})

#: Deny-list: any env-var name matching this pattern never gets its value
#: recorded, even if the user allow-lists it explicitly.
SENSITIVE_NAME_RE = re.compile(r"(?i)key|token|secret|pass|cred|auth|dsn|url")

#: Credential-bearing URL pattern: ``scheme://user:password@host``.
URL_CREDENTIALS_RE = re.compile(r"(?i)([a-z][a-z0-9+.\-]*://)([^/\s:@'\"]+):([^/\s@'\"]+)@")

#: Well-known secret/token prefixes; values starting with these are dropped.
TOKEN_PREFIX_RE = re.compile(r"(?i)^(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|sk-|sk_|xox[bpas]-|AKIA|ASIA|AIza)")

#: Packages that ship binary/C-extension wheels; presence/absence changes are
#: ranked above pure-python ones because they interact with the interpreter
#: and system libraries.  Extend as needed for your domain.
BINARY_PACKAGES: frozenset[str] = frozenset({
    "numpy",
    "scipy",
    "pandas",
    "cryptography",
    "pydantic-core",
    "lxml",
    "pillow",
    "brotli",
    "zstandard",
    "cffi",
    "regex",
    "psycopg2",
    "pyyaml-lib",
    "orjson",
    "ujson",
    "markupsafe",
    "argon2-cffi",
})

#: Entropy threshold (bits per character) above which a value is presumed to
#: be a secret/token and dropped.
ENTROPY_LIMIT: float = 4.5

#: Minimum length before the entropy heuristic applies.
ENTROPY_MIN_LENGTH: int = 16

#: External tools probed (best effort) for the ``tools`` stamp section.
TOOL_PROBES: tuple[str, ...] = ("pip", "uv", "gcc", "clang", "git")

#: Default truncation for rendered diffs (definition of done: <= 10 lines).
DEFAULT_MAX_DIFF_LINES: int = 10

#: Default cap on test executions during ``bisect``.
DEFAULT_MAX_RUNS: int = 12

#: Default per-test timeout for ``bisect --test`` (seconds).
DEFAULT_TEST_TIMEOUT: float = 900.0

#: Env vars always kept when reconstructing the baseline env surface -- the
#: minimum needed to actually *run* a child process.
INFRA_ENV_KEYS: frozenset[str] = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TERM",
    "SHELL",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "SYSTEMDRIVE",
    "WINDIR",
    "NUMBER_OF_PROCESSORS",
})

_T = TypeVar("_T")


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------


def canonicalize_name(name: str) -> str:
    """Normalise a distribution name per PEP 503 (``Foo_Bar.baz`` -> ``foo-bar-baz``)."""
    return re.sub(r"[-_.]+", "-", name).lower().strip()


def _now_iso() -> str:
    """Current UTC time in ``2026-11-09T14:02:11Z`` form."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _version_tuple(version: str) -> tuple[int, ...]:
    """Numeric components of a version string (``3.12.7rc1`` -> ``(3, 12, 7, 1)``)."""
    return tuple(int(piece) for piece in re.findall(r"\d+", version)) or (0,)


def _version_change_scope(a: str, b: str) -> str:
    """Classify how two versions differ: ``major``, ``minor`` or ``patch``.

    Returns ``"same"`` when the numeric parts are equal (e.g. differing only
    in a local version suffix).
    """
    ta, tb = _version_tuple(a), _version_tuple(b)
    n = max(len(ta), len(tb))
    ta, tb = ta + (0,) * (n - len(ta)), tb + (0,) * (n - len(tb))
    scopes = ("major", "minor", "patch")
    for i, scope in enumerate(scopes[: min(n, len(scopes))]):
        if ta[i] != tb[i]:
            return scope
    return "same"


def _run_quiet(cmd: Sequence[str], timeout: float = 10.0) -> str | None:
    """Run ``cmd`` capturing combined output; return it, or ``None`` on failure."""
    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return (proc.stdout or "") + (proc.stderr or "")


def _partition(items: list[_T], n: int) -> list[list[_T]]:
    """Split ``items`` into at most ``n`` contiguous, near-equal chunks."""
    n = max(1, min(n, len(items)))
    size, rem = divmod(len(items), n)
    out: list[list[_T]] = []
    at = 0
    for i in range(n):
        end = at + size + (1 if i < rem else 0)
        out.append(items[at:end])
        at = end
    return out


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


def shannon_entropy(value: str) -> float:
    """Shannon entropy of ``value`` in bits per character (0.0 for empty)."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def mask_url_credentials(value: str) -> str:
    """Mask passwords inside ``scheme://user:pass@host`` URLs as ``***``.

    Example: ``postgres://ci:pencil@db/x`` -> ``postgres://ci:***@db/x``.
    """
    return URL_CREDENTIALS_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}:***@", value)


def redact_env(
    environ: Mapping[str, str],
    *,
    allow_values: Iterable[str] = SAFE_VALUE_KEYS,
    no_redact: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Split ``environ`` into ``(all names, safe values)``.

    The returned names list is always complete and sorted; it is safe to
    publish.  The values mapping only ever contains entries for keys that:

    * appear in ``allow_values`` (default :data:`SAFE_VALUE_KEYS`), AND
    * do **not** match :data:`SENSITIVE_NAME_RE`, AND
    * do not start with a well-known token prefix, AND
    * are not high-entropy blobs (>= :data:`ENTROPY_LIMIT` bits/char over
      >= :data:`ENTROPY_MIN_LENGTH` characters),

    and any value that does survive has embedded URL passwords masked by
    :func:`mask_url_credentials`.

    Args:
        environ: mapping to redact (e.g. ``os.environ``).
        allow_values: names whose values may be recorded once they pass the
            deny-list and entropy checks.  Replaces the default allow-list.
        no_redact: disable redaction entirely and record every value
            verbatim.  The CLI requires an explicit ``--no-redact`` flag and
            prints a warning; the library caller is trusted to know better.

    Returns:
        ``(sorted_names, safe_values)``.
    """
    names = sorted(environ)
    if no_redact:
        return names, {name: environ[name] for name in names}
    allowed = set(allow_values)
    safe: dict[str, str] = {}
    for name in names:
        value = environ[name]
        if name not in allowed:
            continue
        if SENSITIVE_NAME_RE.search(name):
            continue
        if TOKEN_PREFIX_RE.match(value):
            continue
        if len(value) >= ENTROPY_MIN_LENGTH and shannon_entropy(value) >= ENTROPY_LIMIT:
            continue
        safe[name] = mask_url_credentials(value)
    return names, safe


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PythonInfo:
    """Interpreter facts (rank-1 signals in :func:`diff_stamps`)."""

    version: str
    """``platform.python_version()`` e.g. ``"3.12.7"``."""

    implementation: str
    """``"cpython"``, ``"pypy"`` ... lower-cased."""

    abiflags: str
    """``sys.abiflags`` (``""``, ``"d"``, ``"t"`` ...), best effort."""

    executable_kind: str
    """``"venv"``, ``"conda"``, ``"system"`` or ``"unknown"``."""

    platform_tag: str
    """Best-effort wheel platform tag, e.g. ``manylinux_2_39_x86_64``."""


@dataclass(frozen=True)
class OSInfo:
    """Operating-system facts.  ``libc`` is a rank-1 diff signal."""

    name: str
    distro: str
    kernel: str
    libc: str
    locale: str = ""
    fs_case_sensitive: bool = True
    umask: str = "0o022"


@dataclass(frozen=True)
class PackageInfo:
    """One installed distribution."""

    name: str
    """PEP 503 canonicalised distribution name."""

    version: str

    direct: bool = False
    """True when no other installed distribution requires it (top-level)."""

    installer: str = ""
    """Contents of the dist-info ``INSTALLER`` file (``pip``, ``uv``, ...)."""

    wheel_tag: str = ""
    """Best-effort ``Tag:`` lines from the dist-info ``WHEEL`` file."""

    origin: str = ""
    """``"editable:<url>"`` / ``"direct:<url>"`` from ``direct_url.json``.

    Recorded by default (useful for reproducing), stripped by ``--no-paths``
    because local paths can leak usernames."""


@dataclass(frozen=True)
class Stamp:
    """A complete, redacted environment fingerprint (schema 1)."""

    schema: int
    captured_at: str
    host: str
    python: PythonInfo
    os: OSInfo
    packages: list[PackageInfo]
    env_keys: list[str]
    env_redacted: dict[str, str]
    tools: dict[str, str]
    hash: str

    # -- construction -----------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        python: PythonInfo,
        os: OSInfo,
        packages: Sequence[PackageInfo],
        env_keys: Sequence[str],
        env_redacted: Mapping[str, str],
        tools: Mapping[str, str],
        host: str | None = None,
        captured_at: str | None = None,
    ) -> Stamp:
        """Assemble a :class:`Stamp`, computing its semantic hash.

        Packages are sorted by name and env names sorted, so equal
        environments always produce equal hashes.
        """
        core = cls._core_dict(
            python=python, os=os, packages=packages, env_keys=env_keys, env_redacted=env_redacted, tools=tools
        )
        return cls(
            schema=SCHEMA_VERSION,
            captured_at=captured_at or _now_iso(),
            host=host if host is not None else (platform.node() or "unknown"),
            python=python,
            os=os,
            packages=sorted(packages, key=lambda p: p.name),
            env_keys=sorted(env_keys),
            env_redacted=dict(env_redacted),
            tools=dict(tools),
            hash=stamp_digest(core),
        )

    @staticmethod
    def _core_dict(
        *,
        python: PythonInfo,
        os: OSInfo,
        packages: Sequence[PackageInfo],
        env_keys: Sequence[str],
        env_redacted: Mapping[str, str],
        tools: Mapping[str, str],
    ) -> dict[str, Any]:
        """The hash-covered subset of a stamp (everything but time/host/hash)."""
        return {
            "schema": SCHEMA_VERSION,
            "python": asdict(python),
            "os": asdict(os),
            "packages": sorted((asdict(p) for p in packages), key=lambda d: d["name"]),
            "env_keys": sorted(env_keys),
            "env_redacted": dict(sorted(env_redacted.items())),
            "tools": dict(sorted(tools.items())),
        }

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Plain (JSON-ready) dict in the documented schema-1 key order."""
        return {
            "schema": self.schema,
            "captured_at": self.captured_at,
            "host": self.host,
            "python": asdict(self.python),
            "os": asdict(self.os),
            "packages": [asdict(p) for p in self.packages],
            "env_keys": list(self.env_keys),
            "env_redacted": dict(self.env_redacted),
            "tools": dict(self.tools),
            "hash": self.hash,
        }

    def to_json(self) -> str:
        """Pretty JSON text of :meth:`to_dict` (no trailing newline)."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, verify: bool = True) -> Stamp:
        """Rebuild a :class:`Stamp` from a decoded JSON mapping.

        Args:
            data: mapping as produced by :meth:`to_dict`.  Unknown extra keys
                are ignored for forward compatibility.
            verify: recompute the semantic hash and raise :class:`ValueError`
                on mismatch (tamper/corruption detection).

        Raises:
            ValueError: on missing keys, unsupported schema, or hash mismatch.
        """
        try:
            schema = int(data["schema"])  # type: ignore[arg-type]
            py_raw = data["python"]  # type: ignore[index]
            os_raw = data["os"]  # type: ignore[index]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"stamp is missing required key: {exc}") from None
        if schema != SCHEMA_VERSION:
            raise ValueError(f"unsupported stamp schema {schema} (expected {SCHEMA_VERSION})")
        try:
            python = PythonInfo(
                version=str(py_raw["version"]),
                implementation=str(py_raw["implementation"]),
                abiflags=str(py_raw["abiflags"]),
                executable_kind=str(py_raw["executable_kind"]),
                platform_tag=str(py_raw["platform_tag"]),
            )
            os_info = OSInfo(
                name=str(os_raw["name"]),
                distro=str(os_raw["distro"]),
                kernel=str(os_raw["kernel"]),
                libc=str(os_raw["libc"]),
                locale=str(os_raw.get("locale", "")),
                fs_case_sensitive=bool(os_raw.get("fs_case_sensitive", True)),
                umask=str(os_raw.get("umask", "0o022")),
            )
            packages = [
                PackageInfo(
                    name=str(p["name"]),
                    version=str(p["version"]),
                    direct=bool(p.get("direct", False)),
                    installer=str(p.get("installer", "")),
                    wheel_tag=str(p.get("wheel_tag", "")),
                    origin=str(p.get("origin", "")),
                )
                for p in data.get("packages", [])  # type: ignore[union-attr]
            ]
            stamp = cls(
                schema=schema,
                captured_at=str(data.get("captured_at", "")),
                host=str(data.get("host", "unknown")),
                python=python,
                os=os_info,
                packages=packages,
                env_keys=[str(k) for k in data.get("env_keys", [])],  # type: ignore[union-attr]
                env_redacted={str(k): str(v) for k, v in data.get("env_redacted", {}).items()},  # type: ignore[union-attr]
                tools={str(k): str(v) for k, v in data.get("tools", {}).items()},  # type: ignore[union-attr]
                hash=str(data.get("hash", "")),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed stamp section: {exc}") from None
        if verify and stamp_digest(data) != stamp.hash:
            raise ValueError("stamp hash mismatch -- the file is corrupted or was tampered with")
        return stamp


def stamp_digest(stamp: Mapping[str, Any]) -> str:
    """BLAKE2b digest over a stamp's *semantic* content.

    Everything except ``captured_at``, ``host`` and ``hash`` participates,
    so two captures of the same environment (possibly taken at different
    times or on different hosts) hash identically.  The canonical form is
    ``json.dumps(..., sort_keys=True, separators=(",", ":"))``.
    """
    payload = {k: v for k, v in stamp.items() if k not in ("hash", "captured_at", "host")}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "blake2b:" + hashlib.blake2b(blob.encode("utf-8"), digest_size=16).hexdigest()


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------

_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

_PY_PROBE = (
    "import json,platform,sys;"
    "print(json.dumps({'version':platform.python_version(),"
    "'implementation':platform.python_implementation().lower(),"
    "'abiflags':getattr(sys,'abiflags','')}))"
)


def _abiflags() -> str:
    """``sys.abiflags`` when defined, else derived from ``EXT_SUFFIX``."""
    abiflags = getattr(sys, "abiflags", None)
    if abiflags is not None:
        return abiflags
    ext = sysconfig.get_config_var("EXT_SUFFIX") or ""
    m = re.search(r"cpython-\d+([a-z]+)-", ext)
    return m.group(1) if m else ""


def _executable_kind() -> str:
    """Classify where the running interpreter lives."""
    prefix = Path(sys.prefix)
    if (prefix / "pyvenv.cfg").exists():
        return "venv"
    if os.environ.get("CONDA_PREFIX") or "conda" in prefix.as_posix().lower():
        return "conda"
    if sys.prefix != sys.base_prefix:
        return "other"
    return "system"


def _libc_string_cached() -> str:
    """``"glibc 2.39"`` / ``"musl 1.2.5"`` / ``"unknown"`` (best effort)."""
    try:
        lib, ver = platform.libc_ver()
        if lib:
            return f"{lib} {ver}".rstrip()
    except Exception:  # pragma: no cover - platform-specific
        pass
    out = _run_quiet(["ldd", "--version"], timeout=5.0)
    if out:
        m = re.search(r"(?i)musl[^0-9]*(\d+(?:\.\d+){1,2})", out)
        if m:
            return f"musl {m.group(1)}"
    return "unknown"


# Cache the libc probe: it may spawn `ldd`.
_libc_cache: list[str] = []


def _libc_string() -> str:
    """Memoised :func:`_libc_string_cached`."""
    if not _libc_cache:
        _libc_cache.append(_libc_string_cached())
    return _libc_cache[0]


def _platform_tag() -> str:
    """Best-effort wheel platform tag for the running interpreter."""
    machine = (platform.machine() or "unknown").lower()
    try:
        if sys.platform == "darwin":
            mac_ver = platform.mac_ver()[0] or "0.0"
            return f"macosx_{mac_ver.replace('.', '_')}_{machine}"
        libc = _libc_string()
        if libc.startswith("glibc"):
            ver = libc.split()[1] if " " in libc else ""
            if re.fullmatch(r"\d+\.\d+", ver):
                return f"manylinux_{ver.replace('.', '_')}_{machine}"
        if libc.startswith("musl"):
            ver = libc.split()[1] if " " in libc else ""
            if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", ver):
                return f"musllinux_{ver.replace('.', '_')}_{machine}"
        return sysconfig.get_platform().lower().replace("-", "_").replace(".", "_")
    except Exception:  # pragma: no cover - never let capture crash
        return "unknown"


def _python_info() -> PythonInfo:
    """Describe the *running* interpreter."""
    return PythonInfo(
        version=platform.python_version(),
        implementation=platform.python_implementation().lower(),
        abiflags=_abiflags(),
        executable_kind=_executable_kind(),
        platform_tag=_platform_tag(),
    )


def _python_info_for(venv_dir: Path | None) -> PythonInfo:
    """Python info for the running interpreter, or for an unactivated venv.

    When ``venv_dir`` is given, a tiny probe is executed with that venv's
    interpreter (the venv is *never* activated).
    """
    if venv_dir is None:
        return _python_info()
    py = _venv_bin(venv_dir, "python")
    kind = "venv" if (venv_dir / "pyvenv.cfg").exists() else "unknown"
    if not py.exists():
        return replace(_python_info(), executable_kind=kind)
    out = _run_quiet([str(py), "-c", _PY_PROBE], timeout=30.0)
    if out:
        try:
            data = json.loads(out.strip().splitlines()[-1])
            return PythonInfo(
                version=str(data["version"]),
                implementation=str(data["implementation"]),
                abiflags=str(data.get("abiflags", "")),
                executable_kind=kind,
                platform_tag=_platform_tag(),
            )
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
    return replace(_python_info(), executable_kind="unknown")


def _distro() -> str:
    """Human-readable distro string from ``/etc/os-release`` (best effort)."""
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        text = ""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip().strip('"')
    name = fields.get("NAME", "")
    version = fields.get("VERSION_ID", "")
    if name:
        return f"{name} {version}".strip()
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]}".strip()
    if sys.platform == "win32":
        return f"windows {platform.version()}".strip()
    return "unknown"


def _locale_string() -> str:
    """Preferred locale name without mutating process locale state."""
    for var in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = os.environ.get(var)
        if value:
            return value
    try:
        name = locale.getlocale()[0]
        if name:
            return str(name)
    except Exception:  # pragma: no cover - exotic locales
        pass
    return ""


def _fs_case_sensitive() -> bool:
    """Probe whether the temp filesystem is case-sensitive."""
    try:
        with tempfile.TemporaryDirectory(prefix="env-stamp-case-") as td:
            probe = Path(td) / "EnVsTaMpPrObE"
            probe.touch()
            return not (Path(td) / "envstampprobe").exists()
    except OSError:  # pragma: no cover - fall back to platform default
        return sys.platform != "darwin" and os.name != "nt"


def _current_umask() -> str:
    """Read the process umask (``"0o022"``) and restore it."""
    current = os.umask(0o022)
    os.umask(current)
    return f"0o{current:03o}"


def _os_info() -> OSInfo:
    name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    return OSInfo(
        name=name,
        distro=_distro(),
        kernel=platform.release() or "unknown",
        libc=_libc_string(),
        locale=_locale_string(),
        fs_case_sensitive=_fs_case_sensitive(),
        umask=_current_umask(),
    )


def _venv_bin(venv_dir: Path, name: str = "python") -> Path:
    """Path of ``name`` inside a venv (POSIX ``bin/`` or Windows ``Scripts/``)."""
    suffix = ".exe" if os.name == "nt" else ""
    for sub in ("bin", "Scripts"):
        cand = venv_dir / sub / f"{name}{suffix}"
        if cand.exists():
            return cand
    return venv_dir / "bin" / f"{name}{suffix}"


def _site_packages(venv_dir: Path) -> list[Path]:
    """Locate site-packages directories under ``venv_dir`` (no activation)."""
    if venv_dir.name == "site-packages" and venv_dir.is_dir():
        return [venv_dir]
    found: list[Path] = []
    for pattern in ("lib/python*/site-packages", "Lib/site-packages"):
        found.extend(sorted(p for p in venv_dir.glob(pattern) if p.is_dir()))
    return found


def _iter_dists(venv_dir: Path | None) -> Iterator[Distribution]:
    """Yield distributions from the running env, or from an arbitrary venv.

    Uses ``importlib.metadata.distributions(path=[...])`` so the target venv
    is inspected on disk without ever being activated.
    """
    if venv_dir is None:
        yield from distributions()
        return
    sp = _site_packages(venv_dir)
    if not sp:
        raise ValueError(f"no site-packages directory found under {venv_dir}")
    yield from distributions(path=[str(p) for p in sp])


def _wheel_tag(dist: Distribution) -> str:
    """Comma-joined ``Tag:`` lines from the dist-info ``WHEEL`` file."""
    wheel_meta = dist.read_text("WHEEL")
    if not wheel_meta:
        return ""
    tags = [line.split(":", 1)[1].strip() for line in wheel_meta.splitlines() if line.lower().startswith("tag:")]
    return ",".join(tags)


def _origin(dist: Distribution) -> str:
    """``"editable:<url>"`` / ``"direct:<url>"`` from ``direct_url.json``."""
    raw = dist.read_text("direct_url.json")
    if not raw:
        return ""
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    url = mask_url_credentials(str(info.get("url", "")))
    if not url:
        return ""
    editable = bool((info.get("dir_info") or {}).get("editable"))
    return f"{'editable' if editable else 'direct'}:{url}"


def _req_name(requirement: str) -> str:
    """Extract the canonicalised distribution name from a ``Requires-Dist``."""
    m = _REQ_NAME_RE.match(requirement)
    return canonicalize_name(m.group(1)) if m else ""


def _is_extra_only(requirement: str) -> bool:
    """True for ``Requires-Dist: foo ; extra == "bar"`` style requirements."""
    _, _, marker = requirement.partition(";")
    return bool(marker) and re.search(r"(?i)\bextra\b", marker) is not None


def _package_infos(dists: Iterable[Distribution], *, no_paths: bool) -> list[PackageInfo]:
    """Convert distributions into sorted :class:`PackageInfo` records.

    ``direct`` is computed locally: a package is *direct* when nothing else
    installed requires it (extra-only requirements are ignored).
    """
    dist_list = list(dists)
    required: set[str] = set()
    for dist in dist_list:
        for req in dist.requires or []:
            if _is_extra_only(req):
                continue
            name = _req_name(req)
            if name:
                required.add(name)
    infos: list[PackageInfo] = []
    for dist in dist_list:
        name = canonicalize_name(dist.metadata["Name"] or "")
        if not name:
            continue
        infos.append(
            PackageInfo(
                name=name,
                version=dist.version,
                direct=name not in required,
                installer=(dist.read_text("INSTALLER") or "").strip(),
                wheel_tag=_wheel_tag(dist),
                origin="" if no_paths else _origin(dist),
            )
        )
    return sorted(infos, key=lambda p: p.name)


def _tool_versions(venv_dir: Path | None = None) -> dict[str, str]:
    """Best-effort versions of :data:`TOOL_PROBES` found on PATH.

    When ``venv_dir`` is given, its own ``pip`` (if any) wins over the
    PATH one, so the stamp reflects the venv's toolchain.
    """
    probes: list[tuple[str, str]] = []
    if venv_dir is not None:
        venv_pip = _venv_bin(venv_dir, "pip")
        if venv_pip.exists():
            probes.append(("pip", str(venv_pip)))
    for name in TOOL_PROBES:
        if name == "pip" and any(n == "pip" for n, _ in probes):
            continue
        exe = shutil.which(name)
        if exe:
            probes.append((name, exe))
    tools: dict[str, str] = {}
    for name, exe in probes:
        out = _run_quiet([exe, "--version"], timeout=10.0)
        if not out:
            continue
        m = re.search(r"\d+(?:\.\d+)+", out)
        if m:
            tools[name] = m.group(0)
    return tools


def capture(
    *,
    venv: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    allow_values: Iterable[str] = SAFE_VALUE_KEYS,
    no_redact: bool = False,
    no_paths: bool = False,
    with_tools: bool = True,
) -> Stamp:
    """Snapshot an environment into a redacted :class:`Stamp`.

    Args:
        venv: optional path to a virtual environment whose *packages* should
            be fingerprinted instead of the running environment's.  It is
            inspected via ``importlib.metadata.distributions(path=[...])``
            and a tiny version probe -- never activated.
        environ: env mapping to fingerprint (defaults to ``os.environ``);
            injectable for testing.
        allow_values: env-var names whose values may be recorded once they
            pass the deny-list/entropy checks (default TZ/LANG/PYTHONHASHSEED).
        no_redact: record every env value verbatim (see :func:`redact_env`).
        no_paths: strip editable/``file://`` package origins (leak safety).
        with_tools: probe external tool versions (pip/uv/gcc/clang/git).

    Returns:
        The stamp; serialise with :meth:`Stamp.to_json`.

    Raises:
        ValueError: if ``venv`` is given but has no site-packages directory.
    """
    venv_dir = Path(venv).expanduser() if venv is not None else None
    env_source = dict(os.environ if environ is None else environ)
    names, safe = redact_env(env_source, allow_values=allow_values, no_redact=no_redact)
    packages = _package_infos(_iter_dists(venv_dir), no_paths=no_paths)
    return Stamp.build(
        python=_python_info_for(venv_dir),
        os=_os_info(),
        packages=packages,
        env_keys=names,
        env_redacted=safe,
        tools=_tool_versions(venv_dir) if with_tools else {},
    )


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One ranked difference between two stamps.

    Attributes:
        axis: ``"interpreter"``, ``"os"``, ``"packages"``, ``"env"`` or
            ``"tools"``.
        rank: 1 (most likely relevant) .. 5 (env-var names only).
        kind: ``"changed"``, ``"added"``, ``"removed"``, ``"major"``,
            ``"minor"``, ``"patch"``, ``"wheel-tag"``, ``"value"`` or
            ``"names"``.
        name: machine-readable key (package name, ``"python"``, ``"libc"``,
            ``"env:+"`` ...).
        detail: one-line human description, safe to paste into an issue.
        severity: secondary sort key inside a rank (higher sorts first).
    """

    axis: str
    rank: int
    kind: str
    name: str
    detail: str
    severity: int = 0


def _name_list(names: Sequence[str], what: str) -> str:
    shown = ", ".join(names[:8])
    more = f" (+{len(names) - 8} more)" if len(names) > 8 else ""
    return f"{len(names)} env-var name(s) {what}: {shown}{more}  [names only, values never recorded]"


def diff_stamps(a: Stamp, b: Stamp) -> list[Change]:
    """Rank the differences between baseline ``a`` and suspect ``b``.

    Ordering (rank, then -severity, then name):

    1. Interpreter version/implementation/abiflags/platform tag and libc.
    2. Packages added/removed (binary/C-extension first) and wheel-tag
       mismatches at equal versions.
    3. Package version changes (major first), distro and kernel changes.
    4. Locale, ``LANG``/``TZ``/``PYTHONHASHSEED`` safe values, filesystem
       case-sensitivity, umask, tool versions.
    5. Env-var *names* present in only one stamp (values are never
       recorded, hence never diffed).

    Returns:
        Sorted list of :class:`Change`; render with
        :func:`render_terminal` / :func:`render_markdown` / :func:`render_json`.
    """
    changes: list[Change] = []
    pa, pb = a.python, b.python

    # -- rank 1: interpreter + libc ---------------------------------------
    if pa.version != pb.version:
        scope = _version_change_scope(pa.version, pb.version)
        severity = {"major": 30, "minor": 20}.get(scope, 10)
        changes.append(
            Change(
                "interpreter",
                1,
                scope if scope in ("major", "minor", "patch") else "changed",
                "python",
                f"python {pa.version} -> {pb.version} ({scope})",
                severity,
            )
        )
    if pa.implementation != pb.implementation:
        changes.append(
            Change(
                "interpreter",
                1,
                "changed",
                "implementation",
                f"implementation {pa.implementation} -> {pb.implementation}",
                25,
            )
        )
    if pa.abiflags != pb.abiflags:
        changes.append(
            Change(
                "interpreter",
                1,
                "changed",
                "abiflags",
                f"abiflags {pa.abiflags!r} -> {pb.abiflags!r}",
                5,
            )
        )
    if pa.platform_tag != pb.platform_tag:
        changes.append(
            Change(
                "interpreter",
                1,
                "changed",
                "platform_tag",
                f"platform tag {pa.platform_tag} -> {pb.platform_tag}",
                8,
            )
        )
    if a.os.libc != b.os.libc and "unknown" not in (a.os.libc, b.os.libc):
        changes.append(
            Change(
                "os",
                1,
                "changed",
                "libc",
                f"libc {a.os.libc} -> {b.os.libc}",
                15,
            )
        )

    # -- rank 2: packages present/absent (binary first), wheel tags --------
    ma = {p.name: p for p in a.packages}
    mb = {p.name: p for p in b.packages}
    for name in sorted(ma.keys() - mb.keys()):
        pkg = ma[name]
        binary = name in BINARY_PACKAGES
        changes.append(
            Change(
                "packages",
                2,
                "removed",
                name,
                f"{name} {pkg.version} present only in baseline{' (binary/C-extension)' if binary else ''}",
                10 if binary else 0,
            )
        )
    for name in sorted(mb.keys() - ma.keys()):
        pkg = mb[name]
        binary = name in BINARY_PACKAGES
        changes.append(
            Change(
                "packages",
                2,
                "added",
                name,
                f"{name} {pkg.version} present only in suspect{' (binary/C-extension)' if binary else ''}",
                10 if binary else 0,
            )
        )
    for name in sorted(ma.keys() & mb.keys()):
        x, y = ma[name], mb[name]
        if x.version != y.version:
            scope = _version_change_scope(x.version, y.version)
            severity = {"major": 3, "minor": 2}.get(scope, 1)
            changes.append(
                Change(
                    "packages",
                    3,
                    scope if scope in ("major", "minor") else "changed",
                    name,
                    f"{name} {x.version} -> {y.version} ({scope})",
                    severity,
                )
            )
        elif x.wheel_tag != y.wheel_tag and (x.wheel_tag or y.wheel_tag):
            changes.append(
                Change(
                    "packages",
                    2,
                    "wheel-tag",
                    name,
                    f"{name} {y.version}: same version, different wheel "
                    f"({x.wheel_tag or 'n/a'} vs {y.wheel_tag or 'n/a'})",
                    5,
                )
            )

    # -- rank 3: distro / kernel -------------------------------------------
    if a.os.distro != b.os.distro and "unknown" not in (a.os.distro, b.os.distro):
        changes.append(
            Change(
                "os",
                3,
                "changed",
                "distro",
                f"distro {a.os.distro} -> {b.os.distro}",
                2,
            )
        )
    if a.os.kernel != b.os.kernel:
        changes.append(
            Change(
                "os",
                3,
                "changed",
                "kernel",
                f"kernel {a.os.kernel} -> {b.os.kernel}",
                1,
            )
        )

    # -- rank 4: locale / fs / umask / safe env values / tools --------------
    if a.os.locale != b.os.locale:
        changes.append(
            Change(
                "os",
                4,
                "changed",
                "locale",
                f"locale {a.os.locale or 'n/a'} -> {b.os.locale or 'n/a'}",
                3,
            )
        )
    if a.os.fs_case_sensitive != b.os.fs_case_sensitive:
        changes.append(
            Change(
                "os",
                4,
                "changed",
                "fs_case_sensitive",
                "filesystem case-sensitivity: "
                f"{'sensitive' if a.os.fs_case_sensitive else 'insensitive'} -> "
                f"{'sensitive' if b.os.fs_case_sensitive else 'insensitive'}",
                4,
            )
        )
    if a.os.umask != b.os.umask:
        changes.append(
            Change(
                "os",
                4,
                "changed",
                "umask",
                f"umask {a.os.umask} -> {b.os.umask}",
                3,
            )
        )
    for key in sorted(a.env_redacted.keys() & b.env_redacted.keys()):
        if a.env_redacted[key] != b.env_redacted[key]:
            changes.append(
                Change(
                    "env",
                    4,
                    "value",
                    key,
                    f"{key}: {a.env_redacted[key]} -> {b.env_redacted[key]}",
                    2,
                )
            )
    for tool in sorted(a.tools.keys() | b.tools.keys()):
        va, vb = a.tools.get(tool), b.tools.get(tool)
        if va != vb:
            kind = "changed" if (va and vb) else ("removed" if va else "added")
            changes.append(
                Change(
                    "tools",
                    4,
                    kind,
                    tool,
                    f"tool {tool}: {va or 'absent'} -> {vb or 'absent'}",
                    1,
                )
            )

    # -- rank 5: env-var names only -----------------------------------------
    env_added = sorted(set(b.env_keys) - set(a.env_keys))
    env_removed = sorted(set(a.env_keys) - set(b.env_keys))
    if env_added:
        changes.append(Change("env", 5, "names", "env:+", _name_list(env_added, "present only in suspect"), 0))
    if env_removed:
        changes.append(Change("env", 5, "names", "env:-", _name_list(env_removed, "present only in baseline"), 0))

    changes.sort(key=lambda c: (c.rank, -c.severity, c.name, c.detail))
    return changes


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------


def render_terminal(
    changes: Sequence[Change],
    *,
    max_lines: int | None = DEFAULT_MAX_DIFF_LINES,
    header: str | None = None,
) -> str:
    """Render ranked changes as plain terminal text (default: top 10 lines)."""
    lines: list[str] = []
    if header:
        lines.extend([header, ""])
    if not changes:
        lines.append("No differences: the two stamps describe equivalent environments.")
        return "\n".join(lines)
    shown = list(changes) if max_lines is None else list(changes[:max_lines])
    for c in shown:
        lines.append(f"[{c.rank}] {c.axis:<12} {c.name:<20} {c.detail}")
    hidden = len(changes) - len(shown)
    if hidden > 0:
        lines.append(f"... and {hidden} more (ranked lower; use --all to show)")
    return "\n".join(lines)


def render_markdown(
    changes: Sequence[Change],
    *,
    max_lines: int | None = DEFAULT_MAX_DIFF_LINES,
) -> str:
    """Render ranked changes as a Markdown bullet list for pasting into issues."""
    if not changes:
        return "_No differences between the two environment stamps._"
    shown = list(changes) if max_lines is None else list(changes[:max_lines])
    out = [f"**{len(changes)} environment difference(s)** (rank 1 = most likely relevant):", ""]
    for c in shown:
        out.append(f"- `[{c.rank}]` **{c.axis}** {c.name} -- {c.detail}")
    hidden = len(changes) - len(shown)
    if hidden > 0:
        out.append(f"- _... {hidden} more (lower relevance)_")
    return "\n".join(out)


def render_json(changes: Sequence[Change]) -> str:
    """Render changes as a JSON array of plain dicts."""
    return json.dumps([asdict(c) for c in changes], indent=2, ensure_ascii=True)


# --------------------------------------------------------------------------
# Bisect
# --------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Pin:
    """A reproducible package pin: canonical name + exact version."""

    name: str
    version: str

    def spec(self) -> str:
        """pip requirement string (``name==version``)."""
        return f"{self.name}=={self.version}"


@dataclass(frozen=True)
class EnvProfile:
    """How to build the child env for a candidate test run.

    ``mode="current"`` runs with the live environment (plus venv PATH).
    Any other mode starts from the *baseline* env surface (names recorded in
    the baseline stamp + infra keys + baseline safe values), then applies
    ``unset``/``overrides`` transitions that move it toward the suspect env.
    """

    mode: str = "baseline"
    unset: frozenset[str] = frozenset()
    overrides: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Candidate:
    """A fully specified environment to run the test in."""

    packages: frozenset[Pin]
    interpreter: str
    env: EnvProfile = EnvProfile("baseline")
    channel: str = "main"
    """Venv placement key: parallel axis probes get distinct channels."""


@dataclass(frozen=True)
class _Atomic:
    """One atomic baseline->suspect transition for delta-debugging."""

    axis: str  # "packages" | "env"
    kind: str  # add-pkg | remove-pkg | change-pkg | set-env | unset-env
    name: str
    payload: str = ""


class _BudgetExhausted(RuntimeError):
    """Raised when ``--max-runs`` test executions have been spent."""

    def __init__(self, message: str, partial: list[_Atomic] | None = None) -> None:
        super().__init__(message)
        self.partial = partial


def _pins_from_stamp(stamp: Stamp, notes: list[str]) -> frozenset[Pin]:
    """Extract installable pins from a stamp.

    Editable/``file://`` origins are not reproducible from a stamp; they are
    noted and pinned by ``name==version`` instead.
    """
    pins: set[Pin] = set()
    for pkg in stamp.packages:
        if pkg.origin:
            notes.append(
                f"{pkg.name}: installed from {pkg.origin}; bisect pins it as "
                f"{pkg.name}=={pkg.version} instead (origin not reproducible)"
            )
        pins.add(Pin(pkg.name, pkg.version))
    return frozenset(pins)


def _package_items(base_pins: frozenset[Pin], susp_pins: frozenset[Pin]) -> list[_Atomic]:
    """Atomic package transitions (baseline -> suspect)."""
    bm = {p.name: p for p in base_pins}
    sm = {p.name: p for p in susp_pins}
    items: list[_Atomic] = []
    for name in sorted(sm.keys() - bm.keys()):
        items.append(_Atomic("packages", "add-pkg", name, sm[name].version))
    for name in sorted(bm.keys() - sm.keys()):
        items.append(_Atomic("packages", "remove-pkg", name, bm[name].version))
    for name in sorted(bm.keys() & sm.keys()):
        if bm[name].version != sm[name].version:
            items.append(_Atomic("packages", "change-pkg", name, sm[name].version))
    return items


def _env_items(baseline: Stamp, suspect: Stamp) -> tuple[list[_Atomic], list[str]]:
    """Atomic env-var transitions (baseline -> suspect).

    Returns ``(items, blind_spots)``: keys that exist only on the suspect
    side but whose values were never captured (redacted) cannot be replayed;
    they are reported as blind spots instead of silently ignored.
    """
    items: list[_Atomic] = []
    blind: list[str] = []
    base_keys, susp_keys = set(baseline.env_keys), set(suspect.env_keys)
    for key in sorted(susp_keys - base_keys):
        if key in suspect.env_redacted:
            items.append(_Atomic("env", "set-env", key, suspect.env_redacted[key]))
        else:
            blind.append(key)
    for key in sorted(base_keys - susp_keys):
        if key in os.environ:  # only meaningful if currently set
            items.append(_Atomic("env", "unset-env", key))
    for key in sorted(baseline.env_redacted.keys() & suspect.env_redacted.keys()):
        if baseline.env_redacted[key] != suspect.env_redacted[key]:
            items.append(_Atomic("env", "set-env", key, suspect.env_redacted[key]))
    return items, blind


def _apply_package_items(base_pins: frozenset[Pin], items: Sequence[_Atomic]) -> frozenset[Pin]:
    """Baseline pins with the given package transitions applied."""
    pins = {p.name: p for p in base_pins}
    for item in items:
        if item.kind == "remove-pkg":
            pins.pop(item.name, None)
        else:  # add-pkg / change-pkg
            pins[item.name] = Pin(item.name, item.payload)
    return frozenset(pins.values())


def _apply_env_items(base_profile: EnvProfile, items: Sequence[_Atomic]) -> EnvProfile:
    """Baseline env profile with the given env transitions applied."""
    if not items:
        return base_profile  # identity: keeps cache keys stable
    unset = set(base_profile.unset)
    overrides = dict(base_profile.overrides)
    for item in items:
        if item.kind == "unset-env":
            unset.add(item.name)
            overrides.pop(item.name, None)
        else:  # set-env
            overrides[item.name] = item.payload
            unset.discard(item.name)
    return EnvProfile("derived", frozenset(unset), tuple(sorted(overrides.items())))


def _locate_interpreter(want_version: str) -> str | None:
    """Find an interpreter matching ``want_version`` (exact, then X.Y)."""
    if want_version == platform.python_version():
        return sys.executable
    mm = ".".join(_version_tuple(want_version)[:2])
    if not mm:
        return None
    for name in (f"python{mm}", f"python{mm.replace('.', '')}"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def _refusal_reasons(baseline: Stamp, suspect: Stamp) -> list[str]:
    """System-level differences env-stamp cannot and will not bisect across."""
    reasons: list[str] = []
    if baseline.python.implementation != suspect.python.implementation:
        reasons.append(
            f"interpreter implementation differs ({baseline.python.implementation} vs {suspect.python.implementation})"
        )
    if _version_tuple(baseline.python.version)[:2] != _version_tuple(suspect.python.version)[:2]:
        reasons.append(
            f"interpreter major.minor differs "
            f"({baseline.python.version} vs {suspect.python.version}); "
            "only patch-level differences can be bisected"
        )
    if baseline.os.libc != suspect.os.libc and "unknown" not in (baseline.os.libc, suspect.os.libc):
        reasons.append(
            f"C library differs ({baseline.os.libc} vs {suspect.os.libc}) -- system-level, outside env-stamp's control"
        )
    if baseline.os.name != suspect.os.name:
        reasons.append(f"operating system differs ({baseline.os.name} vs {suspect.os.name})")
    return reasons


def _changes_from_items(items: Sequence[_Atomic]) -> list[Change]:
    """Render atomic transitions as :class:`Change` records for the report."""
    out: list[Change] = []
    for item in items:
        if item.axis == "packages":
            if item.kind == "add-pkg":
                rank, kind, detail = 2, "added", f"{item.name} {item.payload} present only in suspect"
            elif item.kind == "remove-pkg":
                rank, kind, detail = 2, "removed", f"{item.name} {item.payload} present only in baseline"
            else:
                rank, kind, detail = 3, "changed", f"{item.name} -> {item.payload}"
        else:
            if item.kind == "set-env":
                rank, kind, detail = 4, "value", f"{item.name}={item.payload} (safe value from suspect)"
            else:
                rank, kind, detail = 5, "names", f"unsetting {item.name} (absent in suspect)"
        out.append(Change(item.axis, rank, kind, item.name, detail))
    return sorted(out, key=lambda c: (c.rank, c.name))


@dataclass(frozen=True)
class BisectReport:
    """Outcome of a :meth:`Bisector.run` invocation."""

    outcome: str
    """``found`` | ``interaction`` | ``not-found`` | ``refused`` |
    ``baseline-fails`` | ``budget-exhausted`` | ``error``."""

    culprits: list[Change]
    """Minimal change set that flips the test from pass to fail."""

    runs_used: int
    cache_hits: int
    max_runs: int
    notes: list[str]
    failing_output: str = ""

    _EXIT_CODES: ClassVar[dict[str, int]] = {
        "found": 0,
        "interaction": 0,
        "not-found": 1,
        "refused": 2,
        "baseline-fails": 3,
        "budget-exhausted": 4,
        "error": 5,
    }

    @property
    def exit_code(self) -> int:
        """Process exit code for this outcome (see module docstring)."""
        return self._EXIT_CODES.get(self.outcome, 5)

    def render(self, *, verbose: bool = False) -> str:
        """Human-readable multi-line report."""
        titles = {
            "found": "CULPRIT FOUND",
            "interaction": "INTERACTION FOUND",
            "not-found": "NO CULPRIT ISOLATED",
            "refused": "REFUSED (system-level difference)",
            "baseline-fails": "BASELINE FAILS",
            "budget-exhausted": "RUN BUDGET EXHAUSTED",
            "error": "ERROR",
        }
        lines = [f"env-stamp bisect: {titles.get(self.outcome, self.outcome.upper())}"]
        if self.outcome == "refused":
            lines.append("  env-stamp refuses to bisect across system-level differences:")
            lines.extend(f"  - {note}" for note in self.notes)
            lines.append(
                "  hint: compare the stamps with `env-stamp diff` instead; "
                "rebuilding OS images / system libraries is out of scope."
            )
        else:
            for c in self.culprits:
                lines.append(f"  culprit [{c.rank}] {c.axis}: {c.detail}")
            if not self.culprits and self.outcome in {"not-found", "budget-exhausted"}:
                lines.append("  no minimal change set could be isolated")
        lines.append(f"  test runs: {self.runs_used}/{self.max_runs}   cache hits: {self.cache_hits}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        if verbose and self.failing_output:
            lines.append("  last failing test output (tail):")
            lines.extend(f"    | {line}" for line in self.failing_output.splitlines()[-12:])
        return "\n".join(lines)


class Bisector:
    """Rebuild candidate environments and find what flips a test to FAIL.

    The bisector compares a *baseline* stamp (known-good machine) with a
    *suspect* stamp (the failing environment, usually captured locally),
    then probes one axis at a time -- packages, interpreter patch version,
    env vars -- and delta-debugs the guilty axis to the minimal change set.

    Example:
        >>> bisector = Bisector(test_cmd="pytest -x tests/test_x.py", baseline=good, suspect=bad)
        >>> report = bisector.run()
        >>> report.outcome, report.exit_code
        ('found', 0)

    Notes:
        * ``run()`` may be called exactly once per instance.
        * Test outcomes are cached by candidate hash, so repeated candidates
          are free; ``runs_used`` counts actual subprocess executions.
        * ``--parallel`` parallelises only the axis-level probes (one venv
          each); within-axis minimisation is always sequential on the shared
          venv for that axis.
    """

    def __init__(
        self,
        *,
        test_cmd: str,
        baseline: Stamp,
        suspect: Stamp,
        python_bin: str | None = None,
        find_links: Sequence[str | os.PathLike[str]] = (),
        max_runs: int = DEFAULT_MAX_RUNS,
        timeout: float = DEFAULT_TEST_TIMEOUT,
        workdir: str | os.PathLike[str] | None = None,
        keep: bool = False,
        prefer_uv: bool = True,
        parallel: bool = False,
        quiet: bool = False,
    ) -> None:
        """
        Args:
            test_cmd: shell command run in each candidate environment; exit
                code 0 means PASS, anything else (or timeout) means FAIL.
            baseline: stamp of the environment where the test passes.
            suspect: stamp of the environment where it fails.
            python_bin: interpreter used to build throwaway venvs (default:
                the interpreter running env-stamp).
            find_links: directories of local wheels; when given, installs go
                ``--no-index --find-links`` so everything works offline.
            max_runs: hard cap on test executions (cache hits are free).
            timeout: per-test timeout in seconds.
            workdir: where throwaway venvs live (default: a fresh temp dir).
            keep: keep the venvs after the run for inspection.
            prefer_uv: use ``uv pip`` when available (falls back to pip).
            parallel: run the axis-level probes concurrently.
            quiet: suppress progress logging to stderr.
        """
        if not test_cmd.strip():
            raise ValueError("test_cmd must be a non-empty shell command")
        if max_runs < 2:
            raise ValueError("max_runs must be >= 2 (baseline verify + at least one probe)")
        self.test_cmd = test_cmd
        self.baseline = baseline
        self.suspect = suspect
        self.python_bin = python_bin or sys.executable
        self.find_links = tuple(Path(p).expanduser() for p in find_links)
        self.max_runs = max_runs
        self.timeout = timeout
        self.keep = keep
        self.parallel = parallel
        self.quiet = quiet
        if workdir is None:
            self.workdir = Path(tempfile.mkdtemp(prefix="env-stamp-bisect-"))
        else:
            self.workdir = Path(workdir).expanduser()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._use_uv = prefer_uv and shutil.which("uv") is not None
        # Runtime state.
        self._runs = 0
        self._cache_hits = 0
        self._cache: dict[str, bool] = {}
        self._venvs: dict[str, Path] = {}
        self._installed: dict[Path, frozenset[Pin]] = {}
        self._lock = threading.Lock()
        self._notes: list[str] = []
        self._last_fail_output = ""
        self._ran = False

    # -- public API ---------------------------------------------------------

    @property
    def runs_used(self) -> int:
        """Number of actual test executions so far (cache hits excluded)."""
        return self._runs

    @property
    def cache_hits(self) -> int:
        """Number of candidate lookups served from the result cache."""
        return self._cache_hits

    def run(self) -> BisectReport:
        """Execute the bisection and return a :class:`BisectReport`.

        Cleans up throwaway venvs afterwards unless ``keep=True``.
        """
        if self._ran:
            raise RuntimeError("Bisector.run() may only be called once")
        self._ran = True
        try:
            return self._run_inner()
        finally:
            if not self.keep:
                for venv_dir in set(self._venvs.values()):
                    shutil.rmtree(venv_dir, ignore_errors=True)

    # -- orchestration -------------------------------------------------------

    def _run_inner(self) -> BisectReport:
        def report(outcome: str, culprits: list[Change] | None = None) -> BisectReport:
            return BisectReport(
                outcome=outcome,
                culprits=culprits or [],
                runs_used=self._runs,
                cache_hits=self._cache_hits,
                max_runs=self.max_runs,
                notes=list(self._notes),
                failing_output=self._last_fail_output,
            )

        reasons = _refusal_reasons(self.baseline, self.suspect)
        if reasons:
            # Refuse *before* creating any venvs: producing a wrong answer
            # here would be worse than refusing.
            return BisectReport("refused", [], 0, 0, self.max_runs, reasons)

        base_pins = _pins_from_stamp(self.baseline, self._notes)
        susp_pins = _pins_from_stamp(self.suspect, self._notes)
        base_profile = EnvProfile("baseline")

        # 1. Baseline must pass.
        try:
            if not self._run_test(Candidate(base_pins, self.python_bin, base_profile)):
                self._notes.append(
                    "the test FAILS on the reconstructed baseline environment; "
                    "there is nothing to bisect (check the test and the baseline stamp)"
                )
                return report("baseline-fails")
        except _BudgetExhausted:
            return report("budget-exhausted")

        # 2. Collect per-axis deltas.
        pkg_items = _package_items(base_pins, susp_pins)
        env_items, blind = _env_items(self.baseline, self.suspect)
        self._notes.extend(
            f"env var '{key}' exists only on the suspect side but its value was "
            "never captured (redacted) -- it is a bisect blind spot"
            for key in blind
        )

        def channel(axis: str) -> str:
            return f"axis-{axis}" if self.parallel else "main"

        probes: dict[str, Candidate] = {}
        if pkg_items:
            probes["packages"] = Candidate(susp_pins, self.python_bin, base_profile, channel("packages"))
        if self.suspect.env_keys != self.baseline.env_keys or self.suspect.env_redacted != self.baseline.env_redacted:
            probes["env"] = Candidate(base_pins, self.python_bin, EnvProfile("current"), channel("env"))
        if self.suspect.python.version != self.baseline.python.version:
            located = _locate_interpreter(self.suspect.python.version)
            if located is None:
                self._notes.append(
                    f"interpreter {self.baseline.python.version} -> "
                    f"{self.suspect.python.version}, but no matching interpreter "
                    "found on PATH; interpreter axis not testable"
                )
            else:
                probes["interpreter"] = Candidate(base_pins, located, base_profile, channel("interpreter"))

        culprits: list[Change] = []
        try:
            # 3. Probe axes (one at a time; concurrently only if --parallel).
            if probes and self.parallel and len(probes) > 1:
                with ThreadPoolExecutor(max_workers=len(probes)) as pool:
                    future_axis = {pool.submit(self._run_test, cand): ax for ax, cand in probes.items()}
                    results = {ax: fut.result() for fut, ax in future_axis.items()}
            else:
                results = {ax: self._run_test(cand) for ax, cand in probes.items()}

            failing_axes = [ax for ax, ok in results.items() if not ok]
            if failing_axes:
                for ax in failing_axes:
                    if ax == "interpreter":
                        culprits.append(
                            Change(
                                "interpreter",
                                1,
                                "changed",
                                "python",
                                f"switching the interpreter alone "
                                f"({self.baseline.python.version} -> {self.suspect.python.version}) "
                                "flips the test to FAIL",
                                10,
                            )
                        )
                        continue
                    items = pkg_items if ax == "packages" else env_items
                    minimal = self._ddmin(items, base_pins, base_profile)
                    if not minimal:
                        self._notes.append(
                            f"{ax} axis flips the test only in the live environment; "
                            "the exact change could not be reconstructed (uncaptured "
                            "values are blind spots)"
                        )
                        culprits.append(Change(ax, 5, "names", ax, f"{ax} axis difference flips the test"))
                    else:
                        culprits.extend(_changes_from_items(minimal))
                if len(failing_axes) > 1:
                    self._notes.append("multiple axes independently flip the test: " + ", ".join(sorted(failing_axes)))
                return report("found", culprits)

            # 4. No single axis flips: try the full suspect environment.
            full = Candidate(susp_pins, self.python_bin, EnvProfile("current"))
            if not self._run_test(full):
                minimal = self._ddmin(pkg_items + env_items, base_pins, base_profile)
                if minimal:
                    culprits = _changes_from_items(minimal)
                    self._notes.append(
                        "no single axis reproduces the failure; the minimal set below combines axes (interaction)"
                    )
                    return report("interaction", culprits)
                self._notes.append(
                    "the failure depends on env values that were never captured; it cannot be minimised, only observed"
                )
                return report("interaction")

            self._notes.append(
                "the test passes in every reconstructed candidate; the failure "
                "likely depends on untracked state (cwd, files on disk, system "
                "libraries, randomness) or is flaky"
            )
            return report("not-found")
        except _BudgetExhausted as exc:
            partial = exc.partial or []
            if partial:
                culprits = _changes_from_items(partial)
                self._notes.append(
                    "run budget exhausted mid-minimisation; the culprit set shown "
                    "is the best-known failing set and may not be minimal"
                )
                return report("budget-exhausted", culprits)
            self._notes.append(str(exc))
            return report("budget-exhausted")

    # -- candidate execution --------------------------------------------------

    def _run_test(self, cand: Candidate) -> bool:
        """Run the test in a candidate environment; True means PASS.

        Results are cached by candidate hash; timeouts count as FAIL.
        """
        key = self._cache_key(cand)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache_hits += 1
                self._log(f"[cache hit #{self._cache_hits}] pins={len(cand.packages)} env={cand.env.mode}")
                return cached
        if self._runs >= self.max_runs:
            raise _BudgetExhausted(f"run budget of {self.max_runs} test executions exhausted before a verdict")
        venv_dir = self._venv_for(cand.interpreter, cand.channel)
        self._sync(venv_dir, cand.packages)
        env = self._child_env(cand.env, venv_dir)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                self.test_cmd,
                shell=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            passed = proc.returncode == 0
            output = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            passed = False
            output = f"<test timed out after {self.timeout:g}s>"
            self._notes.append("at least one candidate run timed out (counted as FAIL)")
        elapsed = time.monotonic() - started
        with self._lock:
            self._runs += 1
            self._cache[key] = passed
        if not passed:
            self._last_fail_output = output[-2000:]
        self._log(
            f"[run {self._runs}/{self.max_runs}] channel={cand.channel} "
            f"pins={len(cand.packages)} env={cand.env.mode} -> "
            f"{'PASS' if passed else 'FAIL'} ({elapsed:.1f}s)"
        )
        return passed

    def _cache_key(self, cand: Candidate) -> str:
        """Hash of the candidate delta set + test command (channel-free)."""
        payload = json.dumps(
            {
                "test": self.test_cmd,
                "interpreter": cand.interpreter,
                "packages": sorted(p.spec() for p in cand.packages),
                "env_mode": cand.env.mode,
                "env_unset": sorted(cand.env.unset),
                "env_set": [list(kv) for kv in cand.env.overrides],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()

    def _venv_for(self, interpreter: str, channel: str) -> Path:
        """Create (once) and return the throwaway venv for a channel."""
        key = f"{channel}:{interpreter}"
        with self._lock:
            existing = self._venvs.get(key)
        if existing is not None:
            return existing
        slug = hashlib.blake2b(key.encode("utf-8"), digest_size=6).hexdigest()
        safe_channel = re.sub(r"[^A-Za-z0-9_.-]", "-", channel)
        venv_dir = self.workdir / f"venv-{safe_channel}-{slug}"
        self._log(f"creating venv for channel '{channel}' with {interpreter}")
        _venv_create(venv_dir, with_pip=not self._use_uv)
        with self._lock:
            self._venvs[key] = venv_dir
            self._installed.setdefault(venv_dir, frozenset())
        return venv_dir

    def _sync(self, venv_dir: Path, pins: frozenset[Pin]) -> None:
        """Install/uninstall so the venv contains exactly ``pins``."""
        with self._lock:
            current = self._installed.get(venv_dir, frozenset())
        if current == pins:
            return
        to_remove = current - pins
        to_add = pins - current
        if to_remove:
            names = [p.name for p in sorted(to_remove)]
            if self._use_uv:
                self._installer(venv_dir, ["uninstall", *names])
            else:
                self._installer(venv_dir, ["uninstall", "-y", *names])
        if to_add:
            args = ["install", "--no-deps", *self._index_args()]
            args.extend(p.spec() for p in sorted(to_add))
            self._installer(venv_dir, args)
        with self._lock:
            self._installed[venv_dir] = pins

    def _index_args(self) -> list[str]:
        """Offline index arguments when ``find_links`` directories are given."""
        if not self.find_links:
            return []
        args = ["--no-index"]
        for directory in self.find_links:
            args.extend(["--find-links", str(directory)])
        return args

    def _installer(self, venv_dir: Path, args: Sequence[str]) -> None:
        """Run pip (or uv) against a throwaway venv; raise on failure."""
        py = _venv_bin(venv_dir, "python")
        if self._use_uv:
            cmd = ["uv", "pip", *args, "--python", str(py)]
        else:
            cmd = [str(py), "-m", "pip", *args]
        self._log("$ " + " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"package manager failed to launch: {exc}") from None
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-1500:]
            raise RuntimeError(f"package operation failed ({' '.join(args[:2])} ...):\n{tail}")

    def _child_env(self, profile: EnvProfile, venv_dir: Path) -> dict[str, str]:
        """Build the environment for a candidate test run.

        ``mode="current"`` inherits the live environment; otherwise the
        baseline env surface is reconstructed (names from the baseline
        stamp + infra keys + safe recorded values), then transitions are
        applied.  PYTHONPATH/PYTHONHOME are always dropped so they cannot
        inject packages into the candidate venv.
        """
        if profile.mode == "current":
            env = dict(os.environ)
        else:
            allowed = set(self.baseline.env_keys) | INFRA_ENV_KEYS
            env = {k: v for k, v in os.environ.items() if k in allowed}
            env.update(self.baseline.env_redacted)
        for key in profile.unset:
            env.pop(key, None)
        for key, value in profile.overrides:
            env[key] = value
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env.pop("__PYVENV_LAUNCHER__", None)
        bin_dir = _venv_bin(venv_dir, "python").parent
        env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
        env["VIRTUAL_ENV"] = str(venv_dir)
        return env

    # -- delta debugging --------------------------------------------------

    def _ddmin(
        self,
        items: list[_Atomic],
        base_pins: frozenset[Pin],
        base_profile: EnvProfile,
    ) -> list[_Atomic]:
        """Minimise a failure-inducing transition set (ddmin).

        Precondition: the caller verified that applying *all* items fails.
        Returns the minimal subset that still fails (possibly empty when the
        full set unexpectedly passes -- a blind-spot situation).
        """
        if not items:
            return []
        if self._run_subset(items, base_pins, base_profile):
            return []  # contradiction: full set passes; blind spot
        work = list(items)
        granularity = 2
        while len(work) >= 2:
            try:
                chunks = _partition(work, granularity)
                # (a) a chunk that alone reproduces the failure
                shrink = next(
                    (c for c in chunks if c and not self._run_subset(c, base_pins, base_profile)),
                    None,
                )
                if shrink is not None:
                    work = shrink
                    granularity = 2
                    continue
                # (b) a chunk whose removal keeps the failure
                drop: list[_Atomic] | None = None
                for chunk in chunks:
                    if not chunk or len(chunk) == len(work):
                        continue
                    complement = [i for i in work if i not in chunk]
                    if not self._run_subset(complement, base_pins, base_profile):
                        drop = chunk
                        break
                if drop is not None:
                    work = [i for i in work if i not in drop]
                    granularity = max(2, granularity - 1)
                    continue
                if granularity >= len(work):
                    break
                granularity = min(len(work), granularity * 2)
            except _BudgetExhausted:
                raise _BudgetExhausted("run budget exhausted during minimisation", partial=work) from None
        return work

    def _run_subset(
        self,
        subset: Sequence[_Atomic],
        base_pins: frozenset[Pin],
        base_profile: EnvProfile,
    ) -> bool:
        """Apply ``subset`` to the baseline and run the test (True = PASS)."""
        pins = _apply_package_items(base_pins, [i for i in subset if i.axis == "packages"])
        profile = _apply_env_items(base_profile, [i for i in subset if i.axis == "env"])
        return self._run_test(Candidate(pins, self.python_bin, profile))

    def _log(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parse_allow_values(spec: str) -> set[str]:
    """Parse a comma-separated ``--allow-values`` argument."""
    return {part.strip() for part in spec.split(",") if part.strip()}


def _load_stamp(source: str | os.PathLike[str] | None, *, stdin: bool = False) -> Stamp:
    """Load and hash-verify a stamp from a file path (or stdin)."""
    if stdin:
        data = json.load(sys.stdin)
    else:
        if source is None:
            raise ValueError("missing stamp file argument")
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    return Stamp.from_dict(data, verify=True)


def _cmd_capture(args: argparse.Namespace) -> int:
    """Handle ``env-stamp capture``."""
    if args.no_redact:
        print(
            "WARNING: --no-redact disables redaction: ALL environment variable "
            "VALUES will be recorded.\n"
            "         Do not share the resulting file.",
            file=sys.stderr,
        )
    stamp = capture(
        venv=args.venv,
        allow_values=_parse_allow_values(args.allow_values),
        no_redact=args.no_redact,
        no_paths=args.no_paths,
        with_tools=not args.no_tools,
    )
    text = stamp.to_json()
    if args.out is None:
        print(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        if not args.quiet:
            print(
                f"wrote {args.out} ({len(stamp.packages)} packages, "
                f"{len(stamp.env_keys)} env names, {len(stamp.env_redacted)} safe "
                f"values, {stamp.hash[:26]}...)",
                file=sys.stderr,
            )
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    """Handle ``env-stamp diff``."""
    baseline = _load_stamp(args.baseline)
    if args.stdin:
        if args.suspect:
            print("env-stamp: error: --stdin cannot be combined with a second file", file=sys.stderr)
            return 2
        suspect = _load_stamp(None, stdin=True)
    elif args.suspect == "-":
        suspect = _load_stamp(None, stdin=True)
    else:
        if not args.suspect:
            print("env-stamp: error: expected two stamp files (or --stdin)", file=sys.stderr)
            return 2
        suspect = _load_stamp(args.suspect)
    changes = diff_stamps(baseline, suspect)
    max_lines = None if args.all else args.max_lines
    if args.format == "json":
        print(render_json(changes))
    elif args.format == "markdown":
        print(render_markdown(changes, max_lines=max_lines))
    else:
        header = f"env-stamp diff: {args.baseline} vs {args.suspect or '<stdin>'}"
        print(render_terminal(changes, max_lines=max_lines, header=header))
    return 1 if changes else 0


def _cmd_bisect(args: argparse.Namespace) -> int:
    """Handle ``env-stamp bisect``."""
    baseline = _load_stamp(args.baseline)
    if not args.quiet:
        target = f" (from {args.venv})" if args.venv else ""
        print(f"capturing suspect environment{target} ...", file=sys.stderr)
    suspect = capture(venv=args.venv)
    bisector = Bisector(
        test_cmd=args.test,
        baseline=baseline,
        suspect=suspect,
        python_bin=args.python,
        find_links=args.find_links,
        max_runs=args.max_runs,
        timeout=args.timeout,
        workdir=args.workdir,
        keep=args.keep,
        prefer_uv=not args.force_pip,
        parallel=args.parallel,
        quiet=args.quiet,
    )
    report = bisector.run()
    print(report.render(verbose=args.verbose))
    return report.exit_code


def build_parser() -> argparse.ArgumentParser:
    """Build the ``env-stamp`` argument parser (documented in --help)."""
    parser = argparse.ArgumentParser(
        prog="env-stamp",
        description="Reproducible environment fingerprints: capture, diff, bisect.",
        epilog=(
            "examples:\n"
            "  env-stamp capture --out good.json\n"
            "  env-stamp capture --out broken.json\n"
            "  env-stamp diff good.json broken.json\n"
            "  cat broken.json | env-stamp diff good.json --stdin\n"
            "  env-stamp bisect --baseline good.json "
            '--test "pytest -x tests/test_x.py" --venv .venv\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    cap = sub.add_parser("capture", help="fingerprint the current (or --venv) environment")
    cap.add_argument("--out", "-o", type=Path, default=None, help="write the stamp JSON to this file (default: stdout)")
    cap.add_argument(
        "--venv", type=Path, default=None, help="fingerprint the packages of this venv without activating it"
    )
    cap.add_argument(
        "--allow-values",
        default=",".join(sorted(SAFE_VALUE_KEYS)),
        help="comma-separated env names whose values may be recorded "
        "(default: TZ,LANG,PYTHONHASHSEED); replaces the default",
    )
    cap.add_argument(
        "--no-redact", action="store_true", help="record ALL env values (prints a warning; do not share the file)"
    )
    cap.add_argument("--no-paths", action="store_true", help="strip editable/file:// package origins (leak safety)")
    cap.add_argument("--no-tools", action="store_true", help="skip probing external tool versions (pip/uv/gcc/...)")
    cap.add_argument("--quiet", "-q", action="store_true", help="suppress summary output")
    cap.set_defaults(handler=_cmd_capture)

    dif = sub.add_parser("diff", help="rank the differences between two stamps")
    dif.add_argument("baseline", help="path to the baseline (good) stamp JSON")
    dif.add_argument(
        "suspect", nargs="?", default=None, help="path to the suspect (broken) stamp JSON, or '-' for stdin"
    )
    dif.add_argument("--stdin", action="store_true", help="read the suspect stamp from stdin")
    dif.add_argument(
        "--format",
        choices=("terminal", "markdown", "json"),
        default="terminal",
        help="output renderer (default: terminal)",
    )
    dif.add_argument("--all", action="store_true", help="show every change, not just the top 10")
    dif.add_argument(
        "--max-lines",
        type=int,
        default=DEFAULT_MAX_DIFF_LINES,
        help=f"truncate to N lines (default: {DEFAULT_MAX_DIFF_LINES})",
    )
    dif.set_defaults(handler=_cmd_diff)

    bis = sub.add_parser("bisect", help="find the smallest change set that flips a test to FAIL")
    bis.add_argument("--baseline", required=True, help="stamp of the environment where the test passes")
    bis.add_argument("--test", required=True, help="shell command; exit code 0 = PASS, anything else = FAIL")
    bis.add_argument(
        "--venv", type=Path, default=None, help="suspect venv to compare against (default: current environment)"
    )
    bis.add_argument(
        "--python", default=None, help="interpreter used to build candidate venvs (default: running python)"
    )
    bis.add_argument(
        "--find-links",
        action="append",
        default=[],
        metavar="DIR",
        help="local wheel directory; repeatable; implies --no-index (offline mode)",
    )
    bis.add_argument(
        "--max-runs", type=int, default=DEFAULT_MAX_RUNS, help=f"cap on test executions (default: {DEFAULT_MAX_RUNS})"
    )
    bis.add_argument(
        "--timeout", type=float, default=DEFAULT_TEST_TIMEOUT, help="per-test timeout in seconds (default: 900)"
    )
    bis.add_argument(
        "--workdir", type=Path, default=None, help="where throwaway venvs live (default: a fresh temp dir)"
    )
    bis.add_argument(
        "--parallel", action="store_true", help="run axis-level probes concurrently (each in its own venv)"
    )
    bis.add_argument("--force-pip", action="store_true", help="use pip even when uv is available")
    bis.add_argument("--keep", action="store_true", help="keep throwaway venvs for inspection")
    bis.add_argument("--verbose", action="store_true", help="show tail of the failing test output")
    bis.add_argument("--quiet", "-q", action="store_true", help="suppress progress logging")
    bis.set_defaults(handler=_cmd_bisect)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns a process exit code (see module docstring)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"env-stamp: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
