#!/data/data/com.termux/files/usr/bin/env python
"""git-rewrite-history — *plan* a safe bulk Git history rewrite, then execute it.
**You do not want a rewriter.  You want a safety layer around the one that
already exists.**
History rewriting (dropping a leaked ``secrets/`` directory, stripping a 5 GB
blob, renaming ``old/`` to ``new/`` across 2,400 commits) is *destructive,
irreversible without a backup, and contagious*: every clone, open pull
request, CI cache keyed by SHA, signed commit, release tag and fork keeps the
old hashes and silently diverges the moment ``main`` is force-pushed.
``git-filter-repo`` is excellent at the tree surgery, but it documents the
blast radius in a 400-line ``--help`` and still gets misfired.  Re-implementing
fast-export/import stream filtering here would be a multi-week project and a
data-loss incident waiting to happen — so this tool **delegates every
byte-level change to ``git-filter-repo``** and owns only the safety layer the
ecosystem is missing:
    rewrite-plan --analyze                # what will this rewrite break?
    rewrite-plan --remove secrets/ \
                 --bigger-than 5M \
                 --dry-run                # exact changes + exact command, zero side effects
    rewrite-plan --remove secrets/       # mirror backup -> rewrite -> fsck -> report
Design rules (each is enforced in code and covered by the test-suite)
----------------------------------------------------------------------
1. **Never operate in place.**  The target must be a *bare mirror clone*; the
   rewrite runs in a brand-new scratch clone made from it, and the working
   directory is only updated after post-rewrite checks pass.  A plain working
   tree is refused with exit code :data:`EXIT_REFUSED` (3).
2. **Signed commits and published tags are refused** unless ``--i-know`` is
   passed twice (and ``--i-know`` is refused non-interactively).  Signatures
   and the immutable checksums of published artifacts cannot be re-created.
3. **Dry-run first, always.**  ``--dry-run`` prints before/after commit and
   object counts, the exact consequences and the exact command line it would
   run, and writes nothing (verified by hashing the object store before/after).
4. **Delegate the rewriting** to ``git-filter-repo`` (subprocess; a missing
   binary fails with an install hint, never with a re-implementation).
5. **Auto-backup.**  A ``git clone --mirror`` snapshot is taken into the
   scratch area before anything is touched, a restore command is printed, and
   it is kept per ``--keep-backup`` (``30d`` default; never deletes without
   ``--prune-backups``).
6. **Post-checks.**  ``git fsck --full``, commit-count reconciliation against
   the pre-rewrite expectation, the tag map (old SHA -> new SHA) and the list
   of preserved authors are printed before the tool declares success.
7. **A rewrite does not un-leak.**  When removed content is classified as
   secret-like, the plan yells about rotation: removing a token from history
   does *not* revoke it.
What this tool deliberately does **not** do
-------------------------------------------
* It never contacts a forge (GitHub/GitLab) — no API tokens, no network.  The
  consequence list is derived from the local mirror (``refs/merge-requests/*``,
  ``refs/pull/*`` as fetched by ``git clone --mirror``) and, optionally,
  ``gh pr list`` (stubbed in tests, never a real remote).
* It never force-pushes and never deletes remote refs; the final push is a
  reviewed step in the generated procedure.
* It never re-signs commits (unless ``--resign-commits`` delegates that to
  ``git-filter-repo``, which then **must** have a GPG key configured).
CLI
---
::
    rewrite-plan TARGET  [--analyze] [--dry-run] [--backup-only]
                         --remove PATH ...      drop paths/dirs from history
                         --bigger-than SIZE     strip blobs larger than SIZE
                         --rename OLD:NEW       move/rename paths in history
                         --subdirectory-filter DIR
                         --replace-text FILE     (literal/regex replacement expressions)
                         --prune-empty {always,auto,never}
                         --refs RANGE            restrict to e.g. 'main~50..main'
                         --strip-signatures | --resign-commits
                         --i-know --i-know       acknowledge signed commits / tags
                         --junit-xml FILE        (alias: --report)
                         --fresh FORGE:OWNER/REPO   verify a fresh mirror clone
                         --strict                exit 3 on any blocking finding
                         --docs-only             print the safety model, exit 0
    Exit codes: 0 success / non-strict findings, 1 operational error,
                2 usage error, 3 refused (policy / unsafe target / --strict).
Target auto-detection (unless ``--bare`` is forced)
---------------------------------------------------
* ``<dir>.git`` that is a bare repository            -> use in place (a mirror clone)
* ``<dir>/.git`` is a repository                     -> *if* the repository has a
  ``forge`` remote configured (see :func:`set_forge_remote`) the URL is used and
  the fresh-mirror check runs if ``--fresh`` is given; otherwise the working
  tree is **refused** with instructions (``--bare <dir>.git`` and
  ``--backup-only <dir>.git`` are the escape hatches: ``--backup-only`` only
  *creates* a backup, it never rewrites).
Library use
-----------
>>> plan = build_plan("/path/to/repo.git", Policy(remove=["secrets/"]))  # doctest: +SKIP
>>> render_plan(plan)                       # doctest: +SKIP
>>> plan.consequences.has("rot-secret")     # doctest: +SKIP
Run ``python -m doctest git_rewrite_history.py`` for the embedded self-test.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import hashlib
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as _ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


__all__: list[str] = [
    "EXIT_ERROR",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_USAGE",
    "Consequence",
    "ExecutionReport",
    "GitError",
    "Plan",
    "Policy",
    "RemovedBlob",
    "RewriteRefused",
    "TagInfo",
    "Target",
    "blob_paths",
    "blob_sizes",
    "build_consequences",
    "build_plan",
    "check_authorization",
    "classify_secret",
    "collect_authors",
    "collect_forge_pull_refs",
    "collect_tags",
    "count_commits",
    "count_objects",
    "create_mirror_backup",
    "discover_forge_pull_requests",
    "execute_plan",
    "fetch_pull_refs",
    "first_touch_by_path",
    "forge_url_from_spec",
    "git_dir_digest",
    "is_secret_blob",
    "is_secret_path",
    "main",
    "parse_duration",
    "parse_i_know",
    "parse_rename_spec",
    "parse_signature_status",
    "parse_size",
    "prune_old_backups",
    "render_plan",
    "resolve_target",
    "run_git",
    "scan_removed_blobs",
    "set_forge_remote",
    "validate_ref",
    "verify_fresh_mirror",
    "write_junit_report",
]
__version__: str = "1.0.0"
EXIT_OK: int = 0
EXIT_ERROR: int = 1
EXIT_USAGE: int = 2
EXIT_REFUSED: int = 3
DEFAULT_KEEP_BACKUP: str = "30d"
_SECRET_PATH_RE: re.Pattern[str] = re.compile(
    r"(?:^|/)(?:\.env(?:rc)?|\.npmrc|\.pypirc|\.netrc|id_rsa|id_dsa|id_ecdsa|"
    r"id_ed25519|secrets?|credentials?|\.aws|\.ssh|private)(?:\.\w+)?(?:$|/)",
    re.IGNORECASE,
)
_SECRET_CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("aws-access-key", re.compile(rb"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("private-key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "github-token",
        re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ),
    ("slack-token", re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}")),
    (
        "generic-assignment",
        re.compile(
            rb"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|"
            rb"client[_-]?secret)\b\s*[:=]\s*['\"]?[^\s'\"#]{6,}"
        ),
    ),
)
_OBJECT_KINDS: tuple[str, ...] = ("commit", "tree", "blob", "tag")
_DURATION_UNITS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 7 * 86400,
}
_REF_FORBIDDEN: tuple[str, ...] = (
    "..",
    "@{",
    "\\",
    " ",
    "\t",
    "\n",
    "~",
    "^",
    ":",
    "?",
    "*",
    "[",
)
_REF_FORBIDDEN_EDGES: tuple[str, ...] = (".", "/", ".lock")
_SHA_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class GitError(RuntimeError):
    """A git sub-command failed (or git/python dependencies are unusable).
    Attributes:
        args: the argv run.
        returncode: process exit status (``None`` if it could not start).
        stderr: captured standard error.
    """

    def __init__(
        self,
        message: str,
        args: Sequence[str] | None = None,
        returncode: int | None = None,
        stderr: str = "",
    ) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__(message)
        self.args_run: tuple[str, ...] = tuple(args or ())
        self.returncode: int | None = returncode
        self.stderr: str = stderr


class RewriteRefused(Exception):
    """The plan is unsafe or the target is not eligible; carries ``reason``.
    The CLI maps this exception to :data:`EXIT_REFUSED` (3).
    """

    def __init__(self, reason: str, plan: Plan | None = None) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__(reason)
        self.reason: str = reason
        self.plan: Plan | None = plan


def _check_choice(value: str, allowed: Iterable[str], what: str) -> str:
    """Return ``value`` if it is one of ``allowed``; raise ``ValueError`` otherwise."""
    if value not in tuple(allowed):
        raise ValueError(f"invalid {what}: {value!r} (expected one of {sorted(allowed)})")
    return value


_REF_SIDE_RE: re.Pattern[str] = re.compile(r"[A-Za-z0-9_@+./-]+(?:[~^]\d*)*")
_REF_BAD_CHARS: tuple[str, ...] = (
    "@{",
    "\\",
    ":",
    "?",
    "*",
    "[",
    "]",
    "\x00",
    " ",
    "\t",
    "\n",
)


def validate_ref(ref: str) -> str:
    """Validate a user-supplied git revision / rev range.
    Accepted: single revisions (``main``, ``v1.2.3``, ``HEAD~50``) and
    two-sided ranges (``main~50..main``, ``..main``).  Rejected: anything that
    could be reinterpreted as an option (leading ``-``), whitespace, path
    traversal, ``@{`` reflog syntax, ``? * [ \\ :`` and empty sides other
    than the two openable ends of a range.
    >>> validate_ref("main~50..main")
    'main~50..main'
    >>> validate_ref("v1.2.3")
    'v1.2.3'
    >>> validate_ref("--force")
    Traceback (most recent call last):
    ...
    ValueError: invalid ref: '--force' (may not start with '-')
    >>> validate_ref("../../etc")
    Traceback (most recent call last):
    ...
    ValueError: invalid ref: '../../etc' (malformed range)
    """
    if not ref:
        raise ValueError("invalid ref: '' (empty)")
    if ref.startswith("-"):
        raise ValueError(f"invalid ref: {ref!r} (may not start with '-')")
    if ".." in ref.replace("...", "..") and "..." in ref:
        raise ValueError(f"invalid ref: {ref!r} (three-dot ranges not supported)")
    for bad in _REF_BAD_CHARS:
        if bad in ref:
            raise ValueError(f"invalid ref: {ref!r} (may not contain {bad!r})")
    parts = ref.split("..")
    if len(parts) == 1:
        if not parts[0]:
            raise ValueError("invalid ref: '' (empty)")
    elif len(parts) == 2:
        if not parts[0] and not parts[1]:
            raise ValueError(f"invalid ref: {ref!r} (empty range)")
    else:
        raise ValueError(f"invalid ref: {ref!r} (malformed range)")
    for side in parts:
        if not side:
            continue
        if "//" in side or side.endswith("/"):
            raise ValueError(f"invalid ref: {ref!r} (empty path component)")
        for component in side.split("/"):
            if component in ("", ".", ".."):
                raise ValueError(f"invalid ref: {ref!r} (path traversal)")
            if component.endswith(".lock"):
                raise ValueError(f"invalid ref: {ref!r} ('.lock' suffix)")
        if not _REF_SIDE_RE.fullmatch(side):
            raise ValueError(f"invalid ref: {ref!r} (unexpected characters)")
    return ref


_SIZE_RE: re.Pattern[str] = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b?)?$", re.IGNORECASE)


def parse_size(text: str) -> int:
    """Parse a human size (``1024``, ``5M``, ``1.5G``, ``10MiB``) into bytes.
    Binary multipliers (K = 1024, M = 1024^2, G = 1024^3) are used — the exact
    unit system ``git-filter-repo --strip-blobs-bigger-than`` applies, so a
    ``--bigger-than 5M`` here strips byte-for-byte what ``5M`` strips there.
    >>> parse_size("5M")
    5242880
    >>> parse_size("1.5G")
    1610612736
    >>> parse_size("512")
    512
    """
    match = _SIZE_RE.match(text.strip().replace(" ", ""))
    if not match:
        raise ValueError(f"invalid size: {text!r} (expected e.g. 1024, 5M, 1.5G)")
    number: float = float(match.group(1))
    exponent = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4}[match.group(2).lower()]
    return int(number * (1024**exponent))


_DURATION_RE: re.Pattern[str] = re.compile(r"^(\d+)([smhdw])$", re.IGNORECASE)


def parse_duration(text: str) -> _dt.timedelta:
    """Parse ``30d`` / ``48h`` / ``90m`` / ``2w`` into a :class:`datetime.timedelta`.
    >>> parse_duration("30d").days
    30
    >>> parse_duration("2w").days
    14
    """
    match = _DURATION_RE.match(text.strip())
    if not match:
        raise ValueError(f"invalid duration: {text!r} (expected e.g. 30d, 48h, 90m)")
    return _dt.timedelta(seconds=int(match.group(1)) * _DURATION_UNITS[match.group(2).lower()])


def parse_i_know(text: str) -> int:
    """Parse the ``--i-know`` argument; the CLI passes the *count* of occurrences.
    ``--i-know`` alone (count 1) prints what would break and refuses; the
    acknowledgement only counts when given **twice** (count >= 2).  In
    non-interactive runs the CLI refuses any ``--i-know`` regardless of count.
    >>> parse_i_know("1")
    1
    >>> parse_i_know("2")
    2
    """
    try:
        count = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid --i-know count: {text!r}") from exc
    if count < 0:
        raise ValueError("--i-know count may not be negative")
    return count


def parse_rename_spec(spec: str) -> tuple[str, str]:
    """Split a ``--rename OLD:NEW`` argument (``git-filter-repo --path-rename`` syntax).
    ``OLD`` may be empty for "move everything under NEW" only when written
    ``:new/``; a bare ``:`` is rejected.
    >>> parse_rename_spec("old/:new/")
    ('old/', 'new/')
    >>> parse_rename_spec(":")
    Traceback (most recent call last):
    ...
    ValueError: invalid rename spec ':' (need OLD:NEW)
    """
    if ":" not in spec:
        raise ValueError(f"invalid rename spec {spec!r} (need OLD:NEW)")
    old, new = spec.split(":", 1)
    if not old and not new:
        raise ValueError(f"invalid rename spec {spec!r} (need OLD:NEW)")
    for part in (old, new):
        if part.startswith("/") or ".." in part.split("/"):
            raise ValueError(f"invalid rename spec {spec!r} (absolute or '..' path)")
    return old, new


def is_secret_path(path: str) -> bool:
    """Heuristically flag a path whose *name* suggests it holds credentials.
    >>> is_secret_path("secrets/db.env")
    True
    >>> is_secret_path("src/app.py")
    False
    """
    return bool(_SECRET_PATH_RE.search(path))


def is_secret_blob(blob: bytes, *, sample: int = 256 * 1024) -> bool:
    """Return True when ``blob`` contains something that looks like a credential.
    Only the first ``sample`` bytes are inspected (a leaked token is not
    position-dependent and history blobs can be huge).  Binary blobs that are
    not private keys are ignored.
    """
    head = blob[:sample]
    return any(pattern.search(head) for _, pattern in _SECRET_CONTENT_PATTERNS)


def classify_secret(blob: bytes, *, sample: int = 256 * 1024) -> str | None:
    """Return the *kind* of the first credential-looking match, else ``None``.
    >>> classify_secret(b"aws = 'AKIA" + b"A" * 16 + b"'")
    'aws-access-key'
    >>> classify_secret(b"nothing to see") is None
    True
    """
    head = blob[:sample]
    for kind, pattern in _SECRET_CONTENT_PATTERNS:
        if pattern.search(head):
            return kind
    return None


def parse_signature_status(text: str) -> str:
    """Normalise ``git log --format=%G?`` output; anything but ``N`` is signed.
    ``git`` reports ``G`` good / ``B`` bad / ``U`` unknown validity / ``X``
    expired / ``Y`` expired key / ``R`` revoked key / **``E`` the key is
    unavailable (or gpg is not configured)**.  Every one of those means a
    ``gpgsig`` header *exists* and will be destroyed by a rewrite — and the
    ``E`` case is exactly what a planning box without the signers' keyrings
    sees, so it must count.  ``N`` (or empty) means no signature.
    >>> parse_signature_status("E")
    'E'
    >>> parse_signature_status("N")
    'N'
    """
    value = text.strip()
    if value in ("G", "B", "U", "X", "Y", "R", "E"):
        return value
    return "N"


def _human_bytes(count: int) -> str:
    """Render a byte count compactly (``1.5 MB``, ``820 KB``, ``17 B``)."""
    size = float(count)
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000.0 or suffix == "TB":
            return f"{size:.0f} {suffix}" if suffix == "B" else f"{size:.1f} {suffix}"
        size /= 1000.0
    return f"{count} B"  # pragma: no cover - unreachable


_GIT_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C",
    "LANG": "C",
}


def _git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the process environment forced into non-interactive, locale-stable mode."""
    env = dict(os.environ)
    env.update(_GIT_ENV)
    if extra:
        env.update(extra)
    return env


def run_git(
    args: Sequence[str],
    cwd: Path | None = None,
    *,
    check: bool = True,
    input_bytes: bytes | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 300.0,
) -> subprocess.CompletedProcess[bytes]:
    """Run a git command non-interactively and return the completed process.
    Raises:
        GitError: if git is missing, times out, or exits non-zero under ``check``.
    """
    argv = ["git", *args]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            input=input_bytes,
            capture_output=True,
            env=_git_env(env),
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - git is a hard dependency
        raise GitError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        raise GitError(f"git timed out after {timeout}s", args=argv) from exc
    if check and proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(
            f"git {' '.join(args)} failed ({proc.returncode}): {stderr[:2000]}",
            args=argv,
            returncode=proc.returncode,
            stderr=stderr,
        )
    return proc


def _git_text(args: Sequence[str], cwd: Path, *, check: bool = True) -> str:
    """Run git and return stripped stdout as text."""
    return run_git(args, cwd, check=check).stdout.decode("utf-8", "replace").strip()


def _rev_parse(repo: Path, rev: str) -> str | None:
    """Resolve ``rev`` to a full SHA in ``repo``; ``None`` when unknown."""
    proc = run_git(["rev-parse", "--verify", "--quiet", rev], repo, check=False)
    value = proc.stdout.decode("ascii", "replace").strip()
    return value if proc.returncode == 0 and _SHA_RE.match(value) else None


@dataclass(frozen=True)
class Target:
    """Where the rewrite will happen and what was auto-detected.
    Attributes:
        repo: repository to rewrite (a bare mirror, or a working tree when
            explicitly allowed).
        kind: ``"bare"`` or ``"worktree"``.
        mode: ``"in-place"`` (repo is itself the mirror clone) or ``"detached"``
            (a working tree that was explicitly allowed; the rewrite still
            happens out-of-tree).
        forge_url: remote URL of the forge to verify for freshness (if any).
        warnings: non-fatal observations to surface in the plan.
    """

    repo: Path
    kind: str
    mode: str
    forge_url: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def scratch_root(self) -> Path:
        """Directory that will hold backups and the scratch clone."""
        anchor = self.repo if self.kind == "bare" else (self.repo / ".git")
        return anchor.parent / (anchor.name + ".rewrite-plan")


def _is_git_repo(path: Path) -> bool:
    """True when ``path`` is the top of a git repository/work tree."""
    if not path.exists():
        return False
    proc = run_git(["rev-parse", "--git-dir"], path, check=False)
    return proc.returncode == 0


def _is_bare_repo(path: Path) -> bool:
    """True when ``path`` is a bare git repository."""
    if not path.is_dir():
        return False
    proc = run_git(["rev-parse", "--is-bare-repository"], path, check=False)
    return proc.returncode == 0 and proc.stdout.decode("ascii", "replace").strip() == "true"


def resolve_target(path: Path, *, allow_worktree: bool = False) -> Target:
    """Classify ``path`` as a rewrite target.
    * ``<path>`` or ``<path>.git`` that is a bare repository -> in-place mirror.
    * A working tree -> refused unless ``allow_worktree``.
    Note that a *prime directory* is only used when a worktree is explicitly
    allowed (``--backup-only``): a rewrite never happens inside it.
    Raises:
        RewriteRefused: when the target is a working tree without permission,
            or not a git repository at all.
    """
    if _is_bare_repo(path):
        warnings: list[str] = []
        shallow = _git_text(["rev-parse", "--is-shallow-repository"], path, check=False)
        if shallow == "true":
            warnings.append(
                "SHALLOW mirror: history before the shallow boundary is invisible; "
                "the rewrite may silently truncate history. Re-clone with --mirror."
            )
        remote = run_git(["config", "--get", "remote.forge.url"], path, check=False)
        forge_url = remote.stdout.decode("utf-8", "replace").strip() or None
        return Target(
            repo=path,
            kind="bare",
            mode="in-place",
            forge_url=forge_url,
            warnings=tuple(warnings),
        )
    if _is_git_repo(path):
        if not allow_worktree:
            raise RewriteRefused(
                f"refusing to rewrite a working tree: {path}\n"
                "  History rewriting is only safe inside a fresh bare mirror clone.\n"
                "  Do this instead:\n"
                f"    git clone --mirror <forge-url> {path.name}.git\n"
                f"    rewrite-plan {path.name}.git --analyze <options>\n"
                f"  (--backup-only {path.name}.git creates a backup but never rewrites; "
                "--bare overrides detection.)"
            )
        remote = run_git(["config", "--get", "remote.forge.url"], path, check=False)
        forge_url = remote.stdout.decode("utf-8", "replace").strip() or None
        return Target(repo=path, kind="worktree", mode="detached", forge_url=forge_url)
    raise RewriteRefused(f"not a git repository: {path}")


def set_forge_remote(repo: Path, url: str) -> None:
    """Configure ``remote.forge.url`` — the non-``origin`` remote the planner uses.
    Using a dedicated remote name keeps the freshness check honest: an
    ``origin`` of a mirror clone may be a local path or an already-rewritten
    intermediate, while ``forge`` is always the authoritative server.
    ``git clone --mirror`` keeps this config entry, so fresh mirror clones
    carry it automatically (including from a GitHub/GitLab source repo).
    """
    run_git(["config", "remote.forge.url", url], repo)


def _ls_remote(url: str) -> dict[str, str]:
    """Return ``{ref: sha}`` for every ref advertised by ``url`` (``git ls-remote``)."""
    proc = run_git(["ls-remote", url], check=False)
    refs: dict[str, str] = {}
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if "\t" in line:
            sha, name = line.split("\t", 1)
            refs[name.strip()] = sha.strip()
    return refs


def _local_refs(repo: Path) -> dict[str, str]:
    """Return ``{ref: sha}`` for every fully-peeled ref in ``repo`` (``--mirror`` clones)."""
    out = _git_text(["for-each-ref", "--format=%(objectname) %(refname)"], repo)
    refs: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, name = line.partition(" ")
        if name:
            refs[name.strip()] = sha.strip()
    return refs


def verify_fresh_mirror(mirror: Path, forge_url: str) -> tuple[bool, list[str]]:
    """Compare ``mirror``'s refs with the forge's advertised refs.
    Returns ``(fresh, problems)``.  ``fresh`` is False when any ref differs or
    is missing in either direction — i.e. the mirror is not a pristine copy of
    the server and a rewrite would drop or resurrect others' commits.
    """
    remote = _ls_remote(forge_url)
    local = _local_refs(mirror)
    problems: list[str] = []
    for name in sorted(set(remote) | set(local)):
        if name == "HEAD":
            continue
        if name not in local:
            problems.append(f"missing in mirror: {name} (remote {remote[name][:12]})")
        elif name not in remote:
            problems.append(f"not on remote: {name} (mirror {local[name][:12]})")
        elif local[name] != remote[name]:
            problems.append(f"stale ref {name}: mirror {local[name][:12]} != remote {remote[name][:12]}")
    return (not problems, problems)


_FORGE_PR_RE: re.Pattern[str] = re.compile(r"^refs/(?:pull/(\d+)/head|merge-requests/(\d+)/head)$")


def collect_forge_pull_refs(repo: Path, *, limit: int | None = 1000) -> list[dict[str, str]]:
    """Find PR/MR head refs present in a mirror clone.
    ``git clone --mirror`` of a GitHub/GitLab repository fetches
    ``refs/pull/<n>/head`` / ``refs/merge-requests/<n>/head``; without them the
    *only* honest answer is "unknown" (see :class:`Consequence` for the
    ``gh api`` fallback).  Author attribution is **not** guessed locally.
    """
    names = _git_text(
        ["for-each-ref", "--format=%(refname)", "refs/pull/", "refs/merge-requests/"],
        repo,
        check=False,
    ).splitlines()
    found: list[dict[str, str]] = []
    for name in names:
        match = _FORGE_PR_RE.match(name.strip())
        if match:
            found.append({
                "number": match.group(1) or match.group(2) or "?",
                "ref": name.strip(),
                "source": "local",
            })
    if limit is not None:
        return found[:limit]
    return found


def discover_forge_pull_requests(
    repo: Path,
    *,
    remote: str = "origin",
    runner: Callable[[Path], subprocess.CompletedProcess[bytes]] | None = None,
) -> tuple[list[dict[str, str]], str]:
    """Ask ``gh pr list`` for open PRs (best effort; never touches the network by itself).
    Returns ``(pull_requests, how)`` where ``how`` is ``"gh"`` on success or
    ``"unavailable: <reason>"``.  Tests inject a fake ``runner``; the real
    default shells out to the GitHub CLI, which may be absent or offline —
    that is reported honestly instead of guessed.
    """
    if runner is None:
        if shutil.which("gh") is None:
            return [], "unavailable: gh CLI not installed"
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state=open",
                "--json",
                "number,author,title",
                "--limit",
                "200",
            ],
            cwd=str(repo),
            capture_output=True,
            env=_git_env(),
            check=False,
        )
    else:
        proc = runner(repo)
    if proc.returncode != 0:
        return [], f"unavailable: gh exited {proc.returncode}"
    import json

    try:
        payload = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except ValueError:
        return [], "unavailable: gh returned invalid JSON"
    prs: list[dict[str, str]] = []
    for item in payload:
        author = item.get("author") or {}
        prs.append({
            "number": str(item.get("number", "?")),
            "author": str(author.get("login", "?")) if isinstance(author, dict) else str(author),
            "title": str(item.get("title", ""))[:120],
            "ref": f"refs/pull/{item.get('number', '?')}/head",
            "source": "gh",
        })
    return prs, "gh"


def fetch_pull_refs(repo: Path, remote: str = "origin") -> None:
    """Fetch ``refs/pull/*`` from ``remote`` into a mirror clone (best effort)."""
    run_git(["fetch", remote, "+refs/pull/*:refs/pull/*"], repo, check=False)


def count_objects(repo: Path) -> dict[str, int]:
    """Count reachable objects by type (``commit``/``tree``/``blob``/``tag``).
    Uses one ``git cat-file --batch-check`` pass over ``--batch-all-objects``
    (works on bare repos and pruned packs alike).
    """
    proc = run_git(
        ["cat-file", "--batch-all-objects", "--batch-check=%(objecttype)"],
        repo,
        check=False,
    )
    counts = {kind: 0 for kind in _OBJECT_KINDS}
    for line in proc.stdout.decode("ascii", "replace").splitlines():
        kind = line.strip()
        if kind in counts:
            counts[kind] += 1
    return counts


def count_commits(repo: Path, revs: Sequence[str] | None = None) -> int:
    """Count commits reachable from ``revs`` (default: ``--all``)."""
    args = ["rev-list", "--count"]
    args.extend(revs if revs else ["--all"])
    out = _git_text(args, repo)
    return int(out or "0")


def git_dir_digest(repo: Path) -> str:
    """SHA-256 digest of the *entire* git directory tree (content + paths).
    Any write — a ref update, a new object, a config tweak, a reflog line —
    changes the digest.  The test-suite and ``--dry-run`` use it to prove that
    "planning only" really wrote nothing.
    """
    if _is_bare_repo(repo):
        git_dir = repo
    else:
        git_dir = Path(_git_text(["rev-parse", "--absolute-git-dir"], repo))
    digest = hashlib.sha256()
    for root, _dirs, files in os.walk(git_dir):
        for name in sorted(files):
            full = Path(root) / name
            rel = full.relative_to(git_dir).as_posix()
            digest.update(rel.encode("utf-8", "replace") + b"\0")
            try:
                with open(full, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(chunk)
            except OSError:  # pragma: no cover - races with git's own temp files
                digest.update(b"<unreadable>")
            digest.update(b"\0")
    return digest.hexdigest()


def collect_authors(repo: Path, revs: Sequence[str] | None = None) -> list[tuple[str, str, int]]:
    """Return ``[(name, email, commit_count), ...]`` sorted by count, then name."""
    args = ["log", r"--format=%an <%ae>"]
    args.extend(revs if revs else ["--all"])
    out = _git_text(args, repo, check=False)
    counts: dict[str, int] = {}
    for line in out.splitlines():
        who = line.strip()
        if who:
            counts[who] = counts.get(who, 0) + 1
    rows: list[tuple[str, str, int]] = []
    for who, count in counts.items():
        name, _, email = who.partition(" <")
        rows.append((name.strip(), email.rstrip(">").strip(), count))
    rows.sort(key=lambda row: (-row[2], row[0].lower(), row[1].lower()))
    return rows


@dataclass(frozen=True)
class TagInfo:
    """A tag ref and the commit it ultimately points at."""

    name: str
    ref: str
    object_type: str
    commit: str

    @property
    def published(self) -> bool:
        """Convention: anything outside ``dev``/``tmp``/``test`` prefixes is release-like."""
        try:
            return not self.ref.startswith(("refs/tags/dev/", "refs/tags/tmp/", "refs/tags/test/"))
        except AttributeError:  # pragma: no cover - defensive for str subclasses
            return True


def collect_tags(repo: Path) -> list[TagInfo]:
    """Enumerate every tag with its object type and fully-peeled commit."""
    out = _git_text(
        [
            "for-each-ref",
            "--format=%(refname) %(objecttype) %(objectname) %(*objectname)",
            "refs/tags/",
        ],
        repo,
        check=False,
    )
    tags: list[TagInfo] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        ref, obj_type, obj_sha = parts[0], parts[1], parts[2]
        commit = parts[3] if len(parts) > 3 and parts[3] else obj_sha
        tags.append(TagInfo(ref.removeprefix("refs/tags/"), ref, obj_type, commit))
    return tags


@dataclass(frozen=True)
class RemovedBlob:
    """A blob the policy drops, with everything needed to judge its severity.
    Attributes:
        oid: blob object id.
        size: blob size in bytes.
        path: one representative path the blob was committed under.
        first_commit: earliest commit (by committer timestamp) introducing it.
        introducing_tags: tags on commits that introduced the blob.
        secret_kind: credential kind detected in content (:func:`classify_secret`),
            or ``None``.
        secret_path: the *path* looks secret-like even if content did not
            (an encrypted key file is still a leak).
        oversized: removed purely because of ``--bigger-than``.
        path_removed: removed because every path it ever had matches the policy.
    """

    oid: str
    size: int
    path: str
    first_commit: str | None
    introducing_tags: tuple[str, ...]
    secret_kind: str | None
    secret_path: bool
    oversized: bool
    path_removed: bool

    @property
    def severity(self) -> str:
        """``"SECRET"`` > ``"SECRET-PATH"`` > ``"LARGE"`` > ``"BLOB"``."""
        if self.secret_kind and self.secret_kind != "large-blob":
            return "SECRET"
        if self.secret_path:
            return "SECRET-PATH"
        if self.oversized:
            return "LARGE"
        return "BLOB"


def blob_sizes(repo: Path) -> dict[str, int]:
    """Return ``{blob_oid: size}`` for every blob object in the store.
    One ``git cat-file --batch-all-objects --batch-check`` process for the
    whole object database (includes unreferenced objects, but
    :func:`blob_paths` only keeps reachable ones, so the extra entries are
    harmless and never reported).
    """
    sizes: dict[str, int] = {}
    proc = run_git(
        [
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        ],
        repo,
        check=False,
    )
    for line in proc.stdout.decode("ascii", "replace").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "blob":
            try:
                sizes[parts[0]] = int(parts[2])
            except ValueError:  # pragma: no cover - defensive
                continue
    return sizes


def blob_paths(repo: Path, revs: Sequence[str]) -> dict[str, set[str]]:
    """Map ``blob_oid -> every path it was ever committed under``.
    ``git rev-list --objects`` lists each reachable object once per identity;
    filtering by the known blob ids (from :func:`blob_sizes`) is exact — no
    mtime, rename or diff-status heuristics are involved.
    Note: a blob whose path changed (rename without content change) yields
    every path, because each tree entry contributes a line.
    """
    known = set(blob_sizes(repo))
    mapping: dict[str, set[str]] = {}
    out = run_git(["rev-list", "--objects", *revs], repo, check=False).stdout.decode("utf-8", "replace")
    for line in out.splitlines():
        oid, _, path = line.partition(" ")
        if oid in known and path:
            mapping.setdefault(oid, set()).add(path)
    return mapping


def first_touch_by_path(repo: Path, paths: Iterable[str], revs: Sequence[str]) -> dict[str, tuple[int, str]]:
    """For each path, the earliest ``(timestamp, commit)`` touching it in ``revs``.
    ``git rev-list --reverse`` is not usable with ``-1`` (it keeps the newest),
    so the oldest touch is read as the *last* line of a newest-first
    ``git log -- <path>``.  Only policy candidates are queried — not every blob
    in history.
    """
    tracked = sorted(set(paths))
    earliest: dict[str, tuple[int, str]] = {}
    for path in tracked:
        out = run_git(["log", *revs, "--format=%H%x1f%ct", "--", path], repo, check=False).stdout.decode(
            "utf-8", "replace"
        )
        lines = [line for line in out.splitlines() if line.strip()]
        if not lines:
            continue
        sha, _, raw_ts = lines[-1].partition("\x1f")
        sha = sha.strip()
        if sha:
            try:
                ts = int(raw_ts.strip())
            except ValueError:  # pragma: no cover - defensive
                ts = 0
            earliest[path] = (ts, sha)
    return earliest


def _tag_names_by_commit(repo: Path) -> dict[str, list[str]]:
    """``{commit_sha: [tag names]}`` for every tag (peels annotated tags)."""
    mapping: dict[str, list[str]] = {}
    for tag in collect_tags(repo):
        mapping.setdefault(tag.commit, []).append(tag.name)
    return mapping


def scan_removed_blobs(
    repo: Path,
    policy: Policy,
    *,
    revs: Sequence[str] | None = None,
) -> list[RemovedBlob]:
    """Find every blob the policy drops, with severity markers.
    A blob is *removed* when **every** path it was ever committed under
    matches ``--remove`` / ``--subdirectory-filter``, or when it exceeds
    ``--bigger-than``.  Content is inspected (up to 4 MiB per blob) for
    credentials, because *a rewrite does not un-leak anything*: a dropped blob
    that contained a token is a rotation task, not merely a history edit.
    Complexity: four flat git passes (object list, sizes, object->path map,
    one log pass over the candidate paths) plus one ``cat-file`` per removed
    blob — independent of the number of commits.
    Returns:
        Removed blobs, secrets first, then by descending size.
    """
    rev_list = list(revs) if revs else ["--all"]
    sizes = blob_sizes(repo)
    paths = blob_paths(repo, rev_list)
    tag_map = _tag_names_by_commit(repo)
    size_limit = policy.bigger_than
    candidates: list[tuple[str, set[str], int]] = []
    for oid, blob_path_set in paths.items():
        size = sizes.get(oid, 0)
        path_removed = bool(blob_path_set) and all(policy.removes_path(p) for p in sorted(blob_path_set))
        oversized = size_limit is not None and size > size_limit
        if path_removed or oversized:
            candidates.append((oid, blob_path_set, size))
    earliest = first_touch_by_path(repo, (min(paths_) for _, paths_, _ in candidates), rev_list)
    removed: list[RemovedBlob] = []
    for oid, blob_path_set, size in candidates:
        ordered = sorted(blob_path_set)
        path_removed = bool(blob_path_set) and all(policy.removes_path(p) for p in ordered)
        oversized = size_limit is not None and size > size_limit
        secret_kind: str | None = None
        if 0 < size <= 4 * 1024 * 1024:
            blob_proc = run_git(["cat-file", "blob", oid], repo, check=False)
            if blob_proc.returncode == 0:
                secret_kind = classify_secret(blob_proc.stdout)
        _committed_at, commit = earliest.get(ordered[0], (0, ""))
        removed.append(
            RemovedBlob(
                oid=oid,
                size=size,
                path=ordered[0],
                first_commit=commit or None,
                introducing_tags=tuple(sorted(tag_map.get(commit, ()))) if commit else (),
                secret_kind=secret_kind,
                secret_path=any(is_secret_path(p) for p in ordered),
                oversized=oversized,
                path_removed=path_removed,
            )
        )
    severity_rank = {"SECRET": 0, "SECRET-PATH": 1, "LARGE": 2, "BLOB": 3}
    removed.sort(key=lambda rb: (severity_rank[rb.severity], -rb.size, rb.path))
    return removed


@dataclass(frozen=True)
class Policy:
    """A declarative description of *what* to remove/rewrite.
    Attributes:
        remove: history paths to drop (``--remove``); a path matches itself and
            everything beneath it.
        bigger_than: strip blobs strictly larger than this many bytes.
        rename: ``(old, new)`` pairs passed to ``git-filter-repo --path-rename``.
        subdirectory_filter: keep only this subdirectory (extract a repo).
        replace_text: file of replacement expressions (``--replace-text``).
        prune_empty: ``always`` / ``auto`` / ``never``.
        refs: optional rev range restricting the rewrite (e.g. ``main~50..main``).
    Note on signatures: ``git-filter-repo`` has no "keep signatures" mode — it
    rebuilds commits and signatures necessarily die.  There is also no
    ``--resign-commits`` there; re-signing would require a callback plus a
    secure key pipeline, which is deliberately *not* attempted.  The planner
    therefore reports signed commits as BLOCKING and offers
    ``--acknowledge-signatures`` only as an explicit acknowledgement.
    Everything here is *forwarded verbatim* to ``git-filter-repo``; this class
    never re-implements the actual tree surgery.
    """

    remove: tuple[str, ...] = ()
    bigger_than: int | None = None
    rename: tuple[tuple[str, str], ...] = ()
    subdirectory_filter: str | None = None
    replace_text: Path | None = None
    prune_empty: str = "never"
    refs: str | None = None
    acknowledge_signatures: bool = False

    def __post_init__(self) -> None:
        """Finish initialization after the dataclass fields are set."""
        _check_choice(self.prune_empty, ("always", "auto", "never"), "prune-empty mode")
        for path in self.remove:
            if not path or path.startswith("/") or ".." in path.split("/"):
                raise ValueError(f"invalid removal path: {path!r} (relative, no '..')")
        for old, new in self.rename:
            for part in (old, new):
                if part.startswith("/") or ".." in part.split("/"):
                    raise ValueError(f"invalid rename path: {part!r} (relative, no '..')")

    @property
    def is_empty(self) -> bool:
        """True when nothing would be rewritten (an analyze-only plan)."""
        return not (
            self.remove or self.bigger_than is not None or self.rename or self.subdirectory_filter or self.replace_text
        )

    def removes_path(self, path: str) -> bool:
        """True when ``--remove``/``--subdirectory-filter`` drops ``path``.
        >>> Policy(remove=["secrets/"]).removes_path("secrets/db.env")
        True
        >>> Policy(remove=["secrets/"]).removes_path("src/main.py")
        False
        >>> Policy(subdirectory_filter="pkg").removes_path("docs/x.md")
        True
        """
        if self.subdirectory_filter is not None:
            base = self.subdirectory_filter.strip("/")
            if not (path == base or path.startswith(base + "/")):
                return True
        for item in self.remove:
            target = item.strip("/")
            if path == target or path.startswith(target + "/"):
                return True
        return False

    def filter_repo_args(self, *, target_dir: Path) -> list[str]:
        """Translate the policy into a ``git-filter-repo`` argument list.
        The list is what ``--dry-run`` prints and what :func:`execute_plan`
        executes — the single source of truth for the rewrite command.
        ``--force`` is required because the mirror/scratch clone is not a
        "fresh clone" by filter-repo's heuristic; the tool re-creates that
        guarantee itself with the mirror backup and the freshness check.
        """
        args: list[str] = ["filter-repo"]
        if self.refs is not None:
            args.append(f"--refs={validate_ref(self.refs)}")
        for path in self.remove:
            args.extend(["--path", path, "--invert-paths"])
        if self.subdirectory_filter is not None:
            args.append(f"--subdirectory-filter={self.subdirectory_filter}")
        for old, new in self.rename:
            args.append(f"--path-rename={old}:{new}")
        if self.bigger_than is not None:
            args.append(f"--strip-blobs-bigger-than={self.bigger_than}")
        if self.replace_text is not None:
            args.append(f"--replace-text={self.replace_text}")
        args.append(f"--prune-empty={self.prune_empty}")
        args.extend(["--force", "--prune-degenerate=never"])
        return args

    def describe(self) -> str:
        """One-line human summary of the policy (used in PLAN headers)."""
        bits: list[str] = []
        if self.remove:
            bits.append(f"drop {len(self.remove)} path(s): {', '.join(self.remove)}")
        if self.bigger_than is not None:
            bits.append(f"strip blobs > {_human_bytes(self.bigger_than)}")
        if self.rename:
            bits.append("rename " + ", ".join(f"{o}->{n}" for o, n in self.rename))
        if self.subdirectory_filter is not None:
            bits.append(f"keep only {self.subdirectory_filter}/")
        if self.replace_text is not None:
            bits.append(f"replace text from {self.replace_text}")
        if self.refs is not None:
            bits.append(f"only refs {self.refs}")
        return "; ".join(bits) if bits else "analyze only (no rewrite requested)"


@dataclass(frozen=True)
class Consequence:
    """One thing the rewrite will break, plus how to survive it.
    Attributes:
        code: stable machine-readable key (``rot-secret``, ``signed-commits``,
            ``tags``, ``pull-requests``, ``storage``, ``contributors``,
            ``forks``, ``shas``, ``links``, ``freshness``, ``shallow``).
        severity: ``"blocking"`` (refused without ``--i-know --i-know``),
            ``"warning"`` (must be announced), or ``"info"``.
        title: short human headline.
        details: individual bullet facts, one per line.
        remedy: what the human must do about it.
    """

    code: str
    severity: str
    title: str
    details: tuple[str, ...] = ()
    remedy: str = ""

    def __post_init__(self) -> None:
        """Finish initialization after the dataclass fields are set."""
        _check_choice(self.severity, ("blocking", "warning", "info"), "severity")

    def render(self) -> list[str]:
        """Render as indented report lines."""
        tag = {"blocking": "BLOCKING", "warning": "WARNING", "info": "info"}[self.severity]
        lines = [f"  [{tag}] {self.title}"]
        lines.extend(f"      - {d}" for d in self.details)
        if self.remedy:
            lines.append(f"      -> {self.remedy}")
        return lines


def _consequences_pull_requests(
    local: Sequence[dict[str, str]],
    forge_prs: Sequence[dict[str, str]],
    gh_status: str,
) -> Consequence:
    """Open PR/MR heads that will need rebasing (local mirror refs + optional ``gh``)."""
    rows = list(local) or list(forge_prs)
    details = [
        f"#{pr['number']}"
        + (f" @{pr['author']}" if pr.get("author") else "")
        + (f" — {pr['title']}" if pr.get("title") else "")
        for pr in rows[:20]
    ]
    if len(rows) > 20:
        details.append(f"... and {len(rows) - 20} more")
    if rows:
        return Consequence(
            code="pull-requests",
            severity="warning",
            title=f"{len(rows)} open PR/MR head ref(s) will need rebasing onto the rewritten history",
            details=tuple(f"PR {d}" for d in details),
            remedy="comment on each PR and have the authors run `git rebase --onto <new-base> <old-base>`; "
            "otherwise the PR shows duplicated commits and phantom diffs",
        )
    if gh_status.startswith("unavailable"):
        return Consequence(
            code="pull-requests",
            severity="info",
            title="open PRs: unknown (no refs/pull/* in the mirror; gh unavailable)",
            details=(gh_status,),
            remedy="run `gh pr list --state=open` yourself before pushing — the count belongs in the announcement",
        )
    return Consequence(code="pull-requests", severity="info", title="no open PR/MR heads found")


def _consequences_tags(tags: Sequence[TagInfo], rewritten: set[str]) -> Consequence:
    """Tags whose target commit is inside the rewritten range."""
    affected = [t for t in tags if t.commit in rewritten]
    published = [t for t in affected if t.published]
    if not affected:
        return Consequence(code="tags", severity="info", title="no tags point into the rewritten range")
    details = [
        f"{t.name} ({'annotated' if t.object_type == 'tag' else 'lightweight'}) -> {t.commit[:12]}"
        for t in affected[:25]
    ]
    if len(affected) > 25:
        details.append(f"... and {len(affected) - 25} more")
    return Consequence(
        code="tags",
        severity="blocking" if published else "warning",
        title=f"{len(affected)} tag(s) point into rewritten history"
        + (f" ({len(published)} release-like: immutable artifacts)" if published else ""),
        details=tuple(details),
        remedy="re-create each tag at its *rewritten* commit and delete the old refs on the server; "
        "published artifacts keep old checksums forever — cut new versions instead of re-uploading",
    )


def _consequences_signatures(signed: int, total: int, policy: Policy) -> Consequence:
    """Signed commits in the rewritten range (blocking unless acknowledged twice)."""
    if signed == 0:
        return Consequence(
            code="signed-commits",
            severity="info",
            title="no signed commits in the rewritten range",
        )
    action = "signatures will become INVALID (git-filter-repo rebuilds commits; signatures cannot survive)"
    return Consequence(
        code="signed-commits",
        severity="blocking",
        title=f"{signed} of {total} commit(s) are signed — {action}",
        details=(
            "signing identity is per-commit; a rewrite can never preserve the originals",
            "some projects (kernel-style DCO/signature policies) treat this as a hard no",
        ),
        remedy="acknowledge explicitly with --i-know --i-know (twice, interactively): no supported tool "
        "re-signs third-party commits, so the honest options are accept, or do not rewrite",
    )


def _consequences_storage(tags: Sequence[TagInfo], *, commits_before: int) -> Consequence:
    """Force-push side effects: CI caches and immutable release archives."""
    pushed = [t for t in tags if t.published]
    details = [
        f"{commits_before} commit SHA(s) become unreachable; CI caches keyed by SHA are invalidated",
        "GitHub auto-generated release archives (zip/tar) are immutable and keep the old tree",
    ]
    if pushed:
        details.append(f"{len(pushed)} release-like tag(s) will keep serving old archive hashes")
    return Consequence(
        code="storage",
        severity="warning",
        title="force-push consequences for CI caches and release archives",
        details=tuple(details),
        remedy="after the push run `gh cache delete --all` (or your CI's cache purge) and note in the "
        "release announcement that archives are pinned to old hashes",
    )


def _consequences_links(policy: Policy, removed: Sequence[RemovedBlob]) -> Consequence:
    """Permalinks into rewritten history."""
    dropped_paths = len({r.path for r in removed if r.path_removed})
    details = [f"{dropped_paths} removed blob path(s) will stop resolving in permalinks"]
    if policy.rename:
        details.append(f"{len(policy.rename)} rename rule(s) will change file URLs in old links")
    return Consequence(
        code="links",
        severity="info",
        title="absolute commit permalinks on the forge will point at dropped paths or 404",
        details=tuple(details),
        remedy="prefer tag-based links in docs; grep issue templates and README for 40-hex SHAs",
    )


def _consequences_contributors(authors: Sequence[tuple[str, str, int]]) -> Consequence:
    """Distinct authors whose clones diverge — the notification list."""
    emails = sorted({email for _, email, _ in authors if email})
    if not authors:
        return Consequence(
            code="contributors",
            severity="info",
            title="no authors in the rewritten range",
        )
    details = [f"{name} <{email}> ({count} commits)" for name, email, count in authors[:15]]
    if len(authors) > 15:
        details.append(f"... and {len(authors) - 15} more identities")
    return Consequence(
        code="contributors",
        severity="warning",
        title=f"{len(authors)} author identit(y/ies) / {len(emails)} email(s) must be notified before the push",
        details=tuple(details),
        remedy="announce first: every existing clone diverges silently after the force-push; "
        "if identity history should be normalised, add a --mailmap rewrite to the same pass",
    )


def _consequences_secrets(removed: Sequence[RemovedBlob]) -> Consequence:
    """The line everyone misses: rewriting does NOT un-leak a secret."""
    secretish = [r for r in removed if r.severity in ("SECRET", "SECRET-PATH")]
    if not removed:
        return Consequence(
            code="rot-secret",
            severity="info",
            title="nothing is being removed — nothing to rotate",
        )
    if not secretish:
        return Consequence(
            code="rot-secret",
            severity="info",
            title="no credential-looking content detected in the removed blobs",
        )
    kinds: dict[str, int] = {}
    for item in secretish:
        key = item.secret_kind if item.secret_kind and item.secret_kind != "large-blob" else "secret-looking-path"
        kinds[key] = kinds.get(key, 0) + 1
    return Consequence(
        code="rot-secret",
        severity="blocking",
        title=f"{len(secretish)} removed blob(s) look like credentials — ROTATE THEM, a rewrite does not un-leak",
        details=tuple(f"{kind}: {count} blob(s)" for kind, count in sorted(kinds.items())),
        remedy="revoke/rotate every secret that ever lived in those blobs at the provider; "
        "assume every clone, fork and CI log already has a copy (GitHub keeps unreferenced objects "
        "reachable for a while after a force-push)",
    )


def _consequences_shallow(target: Target) -> Consequence:
    """A shallow mirror hides the history you are about to destroy."""
    if any("SHALLOW" in w for w in target.warnings):
        return Consequence(
            code="shallow",
            severity="blocking",
            title="target is a SHALLOW mirror — history before the boundary is invisible",
            remedy="re-clone with `git clone --mirror <forge-url> dir.git`",
        )
    return Consequence(code="shallow", severity="info", title="history is complete (not shallow)")


def _consequences_freshness(target: Target, fresh_result: tuple[bool, list[str]] | None) -> Consequence:
    """Stale mirrors resurrect deleted commits when force-pushed."""
    if fresh_result is None:
        return Consequence(
            code="freshness",
            severity="info",
            title="freshness NOT verified (no `forge` remote, or no --fresh) — run this before executing",
            remedy="run with `--fresh github:OWNER/REPO` (or set remote.forge.url) so the plan can compare "
            "every ref with the server; a stale mirror undoes others' deletions",
        )
    fresh, problems = fresh_result
    if fresh:
        return Consequence(code="freshness", severity="info", title="mirror refs match the forge")
    return Consequence(
        code="freshness",
        severity="blocking",
        title=f"mirror is STALE relative to {target.forge_url}",
        details=tuple(problems[:20]) + (("...",) if len(problems) > 20 else ()),
        remedy="re-clone the mirror from the forge; pushing a stale rewrite resurrects deleted commits",
    )


def _consequences_shas(*, commits_before: int) -> Consequence:
    """SHA churn affects even content-identical commits."""
    return Consequence(
        code="shas",
        severity="info",
        title=f"all {commits_before} commit hash(es) will change, including untouched commits",
        details=("any SHA pinned in issues, CI configs, submodules or vendored manifests goes dangling",),
        remedy="grep the tree for 40-hex SHAs and migrate pinned references in the same announcement",
    )


def _consequences_forks() -> Consequence:
    """Forks cannot be enumerated from a local mirror — say so instead of guessing."""
    return Consequence(
        code="forks",
        severity="info",
        title="forks: not derivable from a local mirror — they diverge silently",
        remedy="list forks on the forge (GitHub: /network/members or the API) and include them in the announcement",
    )


def build_consequences(
    target: Target,
    policy: Policy,
    *,
    commits_before: int,
    rewritten_commits: set[str],
    tags: Sequence[TagInfo],
    authors: Sequence[tuple[str, str, int]],
    removed: Sequence[RemovedBlob],
    signed_count: int,
    fresh_result: tuple[bool, list[str]] | None = None,
    pull_refs: Sequence[dict[str, str]] = (),
    gh_prs: Sequence[dict[str, str]] = (),
    gh_status: str = "not queried (local mirror has PR refs)",
) -> tuple[Consequence, ...]:
    """Assemble every consequence of the planned rewrite, in report order."""
    return (
        _consequences_secrets(removed),
        _consequences_signatures(signed_count, commits_before, policy),
        _consequences_tags(tags, rewritten_commits),
        _consequences_pull_requests(pull_refs, gh_prs, gh_status),
        _consequences_storage(tags, commits_before=commits_before),
        _consequences_contributors(authors),
        _consequences_forks(),
        _consequences_shas(commits_before=commits_before),
        _consequences_links(policy, removed),
        _consequences_freshness(target, fresh_result),
        _consequences_shallow(target),
    )


def _stamp(now: _dt.datetime | None = None) -> str:
    """Compact UTC timestamp for backup/scratch directory names (``20261119T160500Z``)."""
    moment = now or _dt.datetime.now(_dt.UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class Plan:
    """A fully computed, side-effect-free description of one rewrite.
    Produced by :func:`build_plan`; rendered by :func:`render_plan`; gated and
    executed by :func:`execute_plan`.  Building a plan only *reads* from the
    repository (proven by ``git_dir_digest`` in the tests).
    """

    target: Target
    policy: Policy
    commits_before: int
    objects_before: Mapping[str, int]
    tags: tuple[TagInfo, ...]
    rewritten_commits: frozenset[str]
    authors: tuple[tuple[str, str, int], ...]
    removed: tuple[RemovedBlob, ...]
    signed_count: int
    consequences: tuple[Consequence, ...]
    head_sha: str | None = None
    fresh_url: str | None = None
    stamp: str = field(default_factory=_stamp)
    created_at: str = field(default_factory=lambda: _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))

    @property
    def blocking(self) -> tuple[Consequence, ...]:
        """Blocking consequences (each must be acknowledged)."""
        return tuple(c for c in self.consequences if c.severity == "blocking")

    @property
    def warnings(self) -> tuple[Consequence, ...]:
        """Warning-level consequences."""
        return tuple(c for c in self.consequences if c.severity == "warning")

    @property
    def backup_path(self) -> Path:
        """Where :func:`execute_plan` will create the mirror backup."""
        return self.target.scratch_root / f"backup-{self.stamp}"

    @property
    def scratch_path(self) -> Path:
        """Where :func:`execute_plan` will run ``git-filter-repo``."""
        return self.target.scratch_root / f"scratch-{self.stamp}"

    @property
    def planned_args(self) -> list[str]:
        """The exact ``git filter-repo ...`` argv that execution would run."""
        return self.policy.filter_repo_args(target_dir=self.scratch_path)

    def consequence(self, code: str) -> Consequence | None:
        """Return the consequence with ``code`` (or ``None``)."""
        for item in self.consequences:
            if item.code == code:
                return item
        return None

    def has(self, code: str) -> bool:
        """True when a consequence with ``code`` exists and is not ``info``-only."""
        item = self.consequence(code)
        return item is not None and item.severity in ("blocking", "warning")


def forge_url_from_spec(spec: str, *, host_override: str | None = None) -> str:
    """Turn ``github:owner/repo`` / ``gitlab:owner/repo`` / a full URL into a clone URL.
    >>> forge_url_from_spec("github:acme/widgets")
    'https://github.com/acme/widgets.git'
    >>> forge_url_from_spec("https://example.com/acme/widgets")
    'https://example.com/acme/widgets'
    """
    if "://" in spec or spec.startswith("git@"):
        return spec
    host, _, path = spec.partition(":")
    if not path or host not in ("github", "gitlab", "bitbucket"):
        raise ValueError(f"invalid --fresh spec {spec!r}: use FORGE:OWNER/REPO (github|gitlab|bitbucket) or a full URL")
    base = {
        "github": "https://github.com",
        "gitlab": "https://gitlab.com",
        "bitbucket": "https://bitbucket.org",
    }[host if host_override is None else host_override]
    url = f"{base}/{path.strip('/')}"
    return url if url.endswith(".git") else url + ".git"


def _signed_count(repo: Path, revs: Sequence[str]) -> int:
    """Count commits that carry a ``gpgsig``/``gpgsig-sha256`` header.
    Deliberately **not** based on ``git log --format=%G?``: that field is the
    *verification* verdict, and a machine without the signers' keyrings (the
    normal case on a planning box) reports ``E``/``N`` even though the
    signature bytes are right there in the commit object.  Scanning the raw
    headers is deterministic: a ``gpgsig`` header means the rewrite will
    destroy a signature, full stop.
    One ``git rev-list | git cat-file --batch`` pipeline for the whole range.
    """
    shas = _git_text(["rev-list", *revs], repo, check=False).split()
    if not shas:
        return 0
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=str(repo),
        input=("\n".join(shas) + "\n").encode("ascii"),
        capture_output=True,
        env=_git_env(),
        check=False,
    )
    data = proc.stdout
    signed = 0
    pos = 0
    while pos < len(data):
        header_end = data.find(b"\n", pos)
        if header_end < 0:
            break
        header = data[pos:header_end].split()
        pos = header_end + 1
        if len(header) < 3 or header[1] != b"commit":
            continue
        size = int(header[2])
        body = data[pos : pos + size]
        pos += size + 1
        head = body.split(b"\n\n", 1)[0]
        if b"\ngpgsig " in b"\n" + head:
            signed += 1
    return signed


def _rel(path: Path) -> str:
    """Render ``path`` relative to CWD when possible (stable, copy-pasteable output)."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except (ValueError, OSError):
        return str(path)


def build_plan(
    target_path: Path,
    policy: Policy,
    *,
    allow_worktree: bool = False,
    fresh_check: bool = False,
    fresh_url: str | None = None,
    gh_runner: Callable[[Path], subprocess.CompletedProcess[bytes]] | None = None,
    gh_remote: str = "origin",
    verify_fresh: Callable[[Path, str], tuple[bool, list[str]]] | None = None,
) -> Plan:
    """Survey a mirror and compute the full plan — **read-only**.
    Args:
        target_path: mirror clone (or, with ``allow_worktree``, a working tree).
        policy: what to rewrite.
        allow_worktree: permit a working-tree target (``--backup-only`` only).
        fresh_check: compare refs against the forge.
        fresh_url: forge URL (overrides ``remote.forge.url``).
        gh_runner: injectable ``gh pr list`` runner (tests never hit a network).
        gh_remote: remote name to inspect.
        verify_fresh: injectable freshness checker (default :func:`verify_fresh_mirror`).
    Raises:
        RewriteRefused: the target is not eligible (working tree, non-repo).
    """
    target = resolve_target(target_path, allow_worktree=allow_worktree)
    checker = verify_fresh or verify_fresh_mirror
    repo = target.repo
    if fresh_check and not (fresh_url or target.forge_url):
        raise ValueError("freshness check requested but no forge URL is known (use --fresh FORGE:OWNER/REPO)")
    revs = [policy.refs] if policy.refs else ["--all"]
    if policy.refs:
        validate_ref(policy.refs)
    commits_before = count_commits(repo, revs)
    objects_before = count_objects(repo)
    tags = tuple(collect_tags(repo))
    authors = tuple(collect_authors(repo, revs))
    rewritten = frozenset(
        _git_text(["rev-list", *revs], repo, check=False).split()
        if policy.refs
        else (_git_text(["rev-list", "--all"], repo, check=False).split())
    )
    signed = _signed_count(repo, revs)
    removed = tuple(scan_removed_blobs(repo, policy, revs=None if policy.refs is None else [policy.refs]))
    pull_refs = tuple(collect_forge_pull_refs(repo)) if target.kind == "bare" else ()
    gh_prs: tuple[dict[str, str], ...] = ()
    if not pull_refs and target.kind == "bare":
        found, gh_status = discover_forge_pull_requests(repo, remote=gh_remote, runner=gh_runner)
        gh_prs = tuple(found)
    else:
        gh_status = "not queried (mirror already contains PR refs)"
    url = fresh_url or target.forge_url
    fresh_result: tuple[bool, list[str]] | None = None
    if fresh_check and url:
        fresh_result = checker(repo, url)
        target = dataclasses.replace(target, forge_url=url)
    effective_target = dataclasses.replace(
        target,
        warnings=target.warnings + (tuple(fresh_result[1]) if fresh_result and fresh_result[1] else ()),
    )
    consequences = build_consequences(
        effective_target,
        policy,
        commits_before=commits_before,
        rewritten_commits=set(rewritten),
        tags=tags,
        authors=authors,
        removed=removed,
        signed_count=signed,
        fresh_result=fresh_result,
        pull_refs=pull_refs,
        gh_prs=gh_prs,
        gh_status=gh_status,
    )
    return Plan(
        target=effective_target,
        policy=policy,
        commits_before=commits_before,
        objects_before=objects_before,
        tags=tags,
        rewritten_commits=rewritten,
        authors=authors,
        removed=removed,
        signed_count=signed,
        consequences=consequences,
        head_sha=_rev_parse(repo, "HEAD"),
        fresh_url=url,
    )


_BAR = "-" * 100


def _tag_map(plan: Plan) -> list[tuple[str, str, str | None]]:
    """Map every pre-rewrite tag to its post-rewrite target (``None`` = gone).
    Computed from the scratch clone by tracing *which old commit became which
    new commit* through ``git replace``-free means: the old tag's commit must
    still exist in the scratch clone under a new SHA, so we match on
    ``(author date, subject)`` — the only stable identity a rewrite preserves.
    """
    rows: list[tuple[str, str, str | None]] = []
    if not plan.scratch_path.exists():
        return [(t.name, t.commit, None) for t in plan.tags]
    scratch = plan.scratch_path
    index: dict[tuple[str, str], list[str]] = {}
    for line in _git_text(["log", "--all", "--format=%ct%x1f%s%x1f%H"], scratch, check=False).splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            index.setdefault((parts[0], parts[1]), []).append(parts[2])
    for tag in plan.tags:
        old = _git_text(
            ["log", "-1", "--format=%ct%x1f%s", tag.commit],
            plan.target.repo,
            check=False,
        ).split("\x1f")
        new_key = (old[0], old[1]) if len(old) == 2 else None
        candidates = index.get(new_key, []) if new_key else []
        rows.append((tag.name, tag.commit, candidates[0] if candidates else None))
    return rows


def _safe_procedure(plan: Plan, backup: Path) -> list[str]:
    """The reviewed, ordered procedure a human executes after reading the plan."""
    args = plan.planned_args[1:]
    command = "git filter-repo " + " ".join(_shell_quote(a) for a in args)
    steps = [
        f"git clone --mirror {_shell_quote(str(plan.target.repo))} {_shell_quote(_rel(backup))}"
        + (
            "   # or: rewrite-plan ran this for you (auto-backup)" if backup.exists() else "   # never rewrite in place"
        ),
        f"cp -r {_shell_quote(_rel(backup))} {_shell_quote(_rel(plan.scratch_path))}",
        f"(cd {_shell_quote(_rel(plan.scratch_path))} && {command})",
    ]
    if any(r.severity in ("SECRET", "SECRET-PATH") for r in plan.removed):
        kinds = sorted({
            r.secret_kind or "secret-path" for r in plan.removed if r.severity in ("SECRET", "SECRET-PATH")
        })
        steps.append(
            f"ROTATE NOW: revoke and re-issue every credential that lived in the removed blobs ({', '.join(kinds)}) "
            "— the rewrite does not un-leak them"
        )
    steps.extend([
        f"(cd {_shell_quote(_rel(plan.scratch_path))} && git fsck --full --no-dangling)   # expect no errors",
        (
            f"git -C {_shell_quote(_rel(plan.scratch_path))} log --oneline --all | wc -l   "
            f"# expect {plan.commits_before} or fewer (prune-empty={plan.policy.prune_empty})"
        ),
        "re-create tags at their new SHAs (see TAG MAP below); delete old tag refs on the server",
        "announce to the authors listed above **before** pushing",
        f"git -C {_shell_quote(_rel(plan.scratch_path))} push --mirror --force-with-lease <forge-url>",
        "invalidate CI caches keyed by old SHAs; close/rebase every open PR listed above",
    ])
    return steps


def _shell_quote(token: str) -> str:
    """Minimal POSIX quoting for display (never executed by this tool)."""
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./->~^]+", token):
        return token
    return "'" + token.replace("'", "'\\''") + "'"


def render_plan(plan: Plan, *, command: str = "rewrite-plan") -> str:
    """Render the plan as the human report: PLAN / CONSEQUENCES / SAFE PROCEDURE / ROLLBACK.
    The layout intentionally mirrors a review artifact: it is copied into the
    PR/issue that announces the rewrite.
    """
    backup = plan.backup_path
    lines: list[str] = []
    policy = plan.policy
    target = plan.target
    lines.append(f"PLAN  rewrite {target.repo} — {plan.commits_before} commit(s)")
    lines.append(f"      {policy.describe()}")
    lines.append(f"      objects before: {', '.join(f'{v} {k}' for k, v in plan.objects_before.items())}")
    if plan.removed:
        lines.append(
            f"      removed content: {len(plan.removed)} blob(s), "
            f"{_human_bytes(sum(r.size for r in plan.removed))} total"
        )
        for item in plan.removed[:12]:
            lines.append(
                f"        {item.severity:<11} {item.path}  ({_human_bytes(item.size)})"
                + (f"  tags: {', '.join(item.introducing_tags)}" if item.introducing_tags else "")
            )
        if len(plan.removed) > 12:
            lines.append(f"        ... and {len(plan.removed) - 12} more blob(s)")
    lines.append(f"      tags: {len(plan.tags)}   authors: {len(plan.authors)}   HEAD: {(plan.head_sha or '?')[:12]}")
    lines.append(_BAR)
    lines.append("CONSEQUENCES")
    if not plan.consequences:
        lines.append("  (none)")
    for consequence in plan.consequences:
        lines.extend(consequence.render())
    lines.append(_BAR)
    lines.append("SAFE PROCEDURE (generated, reviewed, then executed by YOU or by execute_plan)")
    for number, step in enumerate(_safe_procedure(plan, backup), start=1):
        lines.append(f"  {number}. {step}")
    lines.append(_BAR)
    lines.append("ROLLBACK")
    restore = (
        f"mv {_shell_quote(_rel(backup))} {_shell_quote(_rel(target.repo))}.restored   "
        f"# then re-point the forge at it, or: git -C {_shell_quote(_rel(backup))} push --mirror <forge-url>"
    )
    lines.append(f"  original mirror preserved at {_rel(backup)}")
    lines.append(f"  restore: {restore}")
    if not backup.exists():
        lines.append("  (backup does not exist yet — execute_plan creates it before touching anything)")
    if plan.blocking:
        lines.append(_BAR)
        lines.append(
            f"REFUSED: {len(plan.blocking)} blocking consequence(s) — acknowledge with "
            "--i-know --i-know (twice) to proceed"
        )
    return "\n".join(lines)


def write_junit_report(plan: Plan, path: Path) -> Path:
    """Write the plan and its consequences as JUnit XML for CI dashboards.
    Each consequence becomes a ``<testcase>``: ``info`` passes, ``warning`` is
    a ``<skipped>`` (visible, does not fail), ``blocking`` is a ``<failure>``
    — so a CI job running ``rewrite-plan --junit-xml plan.xml --strict`` fails
    exactly when a human must look.
    """
    suite = _ET.Element(
        "testsuite",
        {
            "name": "git-rewrite-history",
            "tests": str(len(plan.consequences)),
            "failures": str(len(plan.blocking)),
            "skipped": str(len(plan.warnings)),
            "timestamp": plan.created_at,
        },
    )
    for consequence in plan.consequences:
        case = _ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"consequences.{consequence.code}",
                "name": consequence.title,
            },
        )
        text = "\n".join(consequence.details + ((consequence.remedy,) if consequence.remedy else ()))
        if consequence.severity == "blocking":
            failure = _ET.SubElement(case, "failure", {"message": consequence.title})
            failure.text = text
        elif consequence.severity == "warning":
            skipped = _ET.SubElement(case, "skipped", {"message": consequence.title})
            skipped.text = text
    if plan.removed:
        case = _ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "plan.removed",
                "name": f"{len(plan.removed)} blob(s) will be removed",
            },
        )
    tree = _ET.ElementTree(suite)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ET.indent(tree, space="  ")
    tree.write(str(path), encoding="utf-8", xml_declaration=True)
    return path


FILTER_REPO_INSTALL_HINT: str = (
    "git-filter-repo is required to execute rewrites (the planning commands work without it).\n"
    "  install: python3 -m pip install git-filter-repo\n"
    "  or:      brew install git-filter-repo / apt install git-filter-repo\n"
    "This tool never re-implements history rewriting: writing a correct fast-export/import "
    "stream filter is a multi-week project, and a subtly wrong one is a data-loss incident."
)


def check_authorization(plan: Plan, *, acknowledged: int, interactive: bool) -> None:
    """Refuse unless every blocking consequence is acknowledged by a human.
    Rules (deliberately annoying):
    * no blocking consequence -> always authorised;
    * ``acknowledged == 0`` -> refuse, listing exactly what breaks;
    * ``--i-know`` given once -> refuse: the flag must be given **twice**;
    * non-interactive stdin (CI, cron) -> always refuse: nobody can read a
      list they never see, so ``--i-know`` cannot count.
    Raises:
        RewriteRefused: with the full list of blocking consequences.
    """
    blocking = plan.blocking
    if not blocking:
        return
    listing = "\n".join(line for c in blocking for line in c.render())
    if acknowledged <= 0:
        raise RewriteRefused(
            "refusing: the plan has blocking consequences that must be acknowledged:\n"
            f"{listing}\n"
            "Re-run with --i-know --i-know (twice) if you accept them.",
            plan=plan,
        )
    if not interactive:
        raise RewriteRefused(
            "refusing: --i-know cannot be given in a non-interactive session — "
            "a human must read and accept these consequences:\n"
            f"{listing}\nRun this command from an interactive terminal.",
            plan=plan,
        )
    if acknowledged < 2:
        raise RewriteRefused(
            f"refusing: --i-know given once; this acknowledgement requires it twice:\n{listing}",
            plan=plan,
        )


def create_mirror_backup(plan: Plan) -> Path:
    """Create and verify a ``git clone --mirror`` snapshot of the target.
    The clone happens **before** anything is rewritten, is verified by
    comparing every ref with the source, and is never overwritten.  A failed
    verification deletes the half-made backup and raises.
    Returns:
        Path of the verified backup repository.
    """
    plan.target.scratch_root.mkdir(parents=True, exist_ok=True)
    backup = plan.backup_path
    if backup.exists():
        raise GitError(f"backup path already exists, refusing to overwrite: {backup}")
    run_git(["clone", "--mirror", str(plan.target.repo), str(backup)], timeout=1800.0)
    source_refs = _local_refs(plan.target.repo)
    backup_refs = _local_refs(backup)
    if source_refs != backup_refs:
        shutil.rmtree(backup, ignore_errors=True)
        differing = sorted(
            name for name in set(source_refs) | set(backup_refs) if source_refs.get(name) != backup_refs.get(name)
        )
        raise GitError(
            "backup verification failed — refs differ between source and backup: " + ", ".join(differing[:10])
        )
    return backup


def prune_old_backups(scratch_root: Path, keep: _dt.timedelta, *, now: _dt.datetime | None = None) -> list[Path]:
    """Delete ``backup-<stamp>`` snapshots older than ``keep``; return what was pruned.
    Only directories matching the tool's own naming scheme are touched, and
    only when the caller opted in (``--prune-backups``) — the default keeps
    backups forever and prints the ``rm -rf`` line instead.
    """
    pruned: list[Path] = []
    if not scratch_root.is_dir():
        return pruned
    reference = now or _dt.datetime.now(_dt.UTC)
    for child in sorted(scratch_root.iterdir()):
        match = re.fullmatch(r"backup-(\d{8}T\d{6}Z)", child.name)
        if not match or not child.is_dir():
            continue
        try:
            made = _dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=_dt.UTC)
        except ValueError:  # pragma: no cover - defensive
            continue
        if reference - made > keep:
            shutil.rmtree(child, ignore_errors=True)
            pruned.append(child)
    return pruned


@dataclass(frozen=True)
class ExecutionReport:
    """Outcome of :func:`execute_plan` — the post-check evidence trail."""

    plan: Plan
    backup: Path
    scratch: Path
    command: tuple[str, ...]
    commits_before: int
    commits_after: int
    objects_before: Mapping[str, int]
    objects_after: Mapping[str, int]
    fsck_ok: bool
    fsck_output: str
    tag_map: tuple[tuple[str, str, str | None], ...]
    authors_after: tuple[tuple[str, str, int], ...]
    warnings: tuple[str, ...] = ()
    pruned_backups: tuple[Path, ...] = ()

    @property
    def authors_lost(self) -> int:
        """Distinct e-mails present before but not after (expected with aggressive pruning)."""
        before = {email for _, email, _ in self.plan.authors}
        after = {email for _, email, _ in self.authors_after}
        return len(before - after)

    def render(self) -> str:
        """Render the post-rewrite report."""
        lines: list[str] = [f"EXECUTED  rewrite in {_rel(self.scratch)}", ""]
        lines.append("COMMAND   " + self.command[0] + " " + " ".join(_shell_quote(t) for t in self.command[1:]))
        lines.append(
            f"BEFORE    {self.commits_before} commit(s)   "
            + ", ".join(f"{v} {k}" for k, v in self.objects_before.items())
        )
        lines.append(
            f"AFTER     {self.commits_after} commit(s)   "
            + ", ".join(f"{v} {k}" for k, v in self.objects_after.items())
        )
        lines.append("")
        lines.append("POST-CHECKS")
        lines.append(
            f"  [{'ok' if self.fsck_ok else 'FAIL'}] git fsck --full"
            + ("" if self.fsck_ok else ":\n" + self.fsck_output)
        )
        bounded = 0 <= self.commits_after <= self.commits_before
        lines.append(
            f"  [{'ok' if bounded else 'FAIL'}] commit count {self.commits_after} "
            f"{'within' if bounded else 'ABOVE'} the pre-rewrite count {self.commits_before}"
        )
        gone = [name for name, _old, new in self.tag_map if new is None]
        lines.append(
            f"  [{'ok' if not gone else 'warn'}] tags resolved: "
            f"{len(self.tag_map) - len(gone)}/{len(self.tag_map)}"
            + (f" (unresolved: {', '.join(gone)})" if gone else "")
        )
        lines.append(
            f"  [{'ok' if self.authors_lost == 0 else 'warn'}] authors preserved: "
            f"{len({e for _, e, _ in self.authors_after})} e-mail(s), {self.authors_lost} lost"
        )
        for warning in self.warnings:
            lines.append(f"  [warn] {warning}")
        if self.tag_map:
            lines.append("")
            lines.append("TAG MAP (old -> new; re-create these on the forge)")
            for name, old, new in self.tag_map:
                lines.append(f"  {name:<28} {old[:12]} -> {(new or '(gone)')[:12]}")
        if self.pruned_backups:
            lines.append("")
            lines.append("PRUNED BACKUPS  " + ", ".join(_rel(p) for p in self.pruned_backups))
        lines.append("")
        lines.append(
            f"BACKUP    {_rel(self.backup)}   (restore: git -C {_shell_quote(_rel(self.backup))} "
            f"push --mirror <forge-url>)"
        )
        lines.append(
            "NEXT      this tool never pushes. After review: announce to the authors, then "
            f"`git -C {_shell_quote(_rel(self.scratch))} push --mirror --force-with-lease <forge-url>`"
        )
        if any(r.severity in ("SECRET", "SECRET-PATH") for r in self.plan.removed):
            lines.append("          and ROTATE the leaked credentials — a rewrite does not un-leak them")
        return "\n".join(lines)


def execute_plan(
    plan: Plan,
    *,
    acknowledged: int = 0,
    interactive: bool | None = None,
    filter_repo: str = "git-filter-repo",
    keep_backup: _dt.timedelta | None = None,
    prune_backups: bool = False,
    timeout: float = 1800.0,
) -> ExecutionReport:
    """Authorise, back up, rewrite a scratch clone, verify, and report.
    Never touches the source mirror: the rewrite runs inside
    ``<target>.rewrite-plan/scratch-<stamp>``; the original repository is
    copied to ``<target>.rewrite-plan/backup-<stamp>`` and left byte-identical.
    Nothing is pushed.
    Raises:
        RewriteRefused: :func:`check_authorization` said no.
        GitError: filter-repo is missing, the backup could not be verified, or
            the rewrite itself failed (the backup is preserved in every case).
    """
    if interactive is None:
        interactive = sys.stdin.isatty()
    check_authorization(plan, acknowledged=acknowledged, interactive=interactive)
    executable = filter_repo if os.sep in filter_repo else (shutil.which(filter_repo) or "")
    if not executable:
        raise GitError(FILTER_REPO_INSTALL_HINT)
    backup = create_mirror_backup(plan)
    scratch = plan.scratch_path
    if scratch.exists():
        raise GitError(f"scratch path already exists: {scratch}")
    run_git(["clone", "--mirror", str(plan.target.repo), str(scratch)], timeout=timeout)
    args = plan.policy.filter_repo_args(target_dir=scratch)
    command = ("git filter-repo", *args[1:])
    try:
        proc = subprocess.run(
            [executable, *args[1:]],
            cwd=str(scratch),
            capture_output=True,
            env=_git_env(),
            timeout=timeout,
            check=False,
        )
    except OSError as exc:
        raise GitError(
            f"could not execute {executable!r}: {exc}\n{FILTER_REPO_INSTALL_HINT}",
            args=command,
        ) from exc
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        raise GitError(
            f"git-filter-repo timed out after {timeout}s; backup preserved at {backup}",
            args=command,
        ) from exc
    if proc.returncode != 0:
        raise GitError(
            f"git-filter-repo failed ({proc.returncode}); the source mirror is untouched, "
            f"backup at {backup}\n{proc.stderr.decode('utf-8', 'replace').strip()[:4000]}",
            args=command,
            returncode=proc.returncode,
            stderr=proc.stderr.decode("utf-8", "replace"),
        )
    fsck = run_git(["fsck", "--full", "--no-dangling"], scratch, check=False)
    fsck_output = (fsck.stdout + fsck.stderr).decode("utf-8", "replace").strip()
    warnings: list[str] = []
    if not plan.policy.is_empty and plan.commits_before and count_commits(scratch, ["--all"]) > plan.commits_before:
        warnings.append("commit count grew — this should be impossible; do NOT push this scratch clone")
    if plan.signed_count:
        warnings.append(
            f"{plan.signed_count} commit signature(s) were dropped — re-sign only if your policy requires it"
        )
    report = ExecutionReport(
        plan=plan,
        backup=backup,
        scratch=scratch,
        command=command,
        commits_before=plan.commits_before,
        commits_after=count_commits(scratch, ["--all"]),
        objects_before=plan.objects_before,
        objects_after=count_objects(scratch),
        fsck_ok=fsck.returncode == 0,
        fsck_output=fsck_output,
        tag_map=tuple(_tag_map(plan)),
        authors_after=tuple(collect_authors(scratch)),
        warnings=tuple(warnings),
    )
    if fsck.returncode != 0:
        raise GitError(
            f"post-rewrite `git fsck --full` failed; do not use this scratch clone "
            f"(backup preserved at {backup})\n{fsck_output[:4000]}"
        )
    pruned: tuple[Path, ...] = ()
    if keep_backup is not None:
        pruned = tuple(prune_old_backups(plan.target.scratch_root, keep_backup))
    return dataclasses.replace(report, pruned_backups=pruned)


DOCS: str = """\
git-rewrite-history — plan a safe bulk git history rewrite, then let you execute it.
  rewrite-plan REPO.git --analyze                       what breaks? (PRs, tags, signatures, secrets, forks)
  rewrite-plan REPO.git --remove secrets/ --dry-run     exact blob list + exact command, writes NOTHING
  rewrite-plan REPO.git --remove secrets/ --bigger-than 5M
                                                        back up -> rewrite a SCRATCH clone -> fsck -> report
  rewrite-plan REPO.git --backup-only                   only snapshot the mirror (never rewrites)
Safety model
  1. A working tree is refused (exit 3); only a `git clone --mirror` target is
     rewritten, and even then the rewrite happens in a scratch clone, never in place.
  2. Signed commits, release-like tags, stale mirrors, secret-looking blobs and
     shallow mirrors are BLOCKING; each needs --i-know --i-know (twice, on a TTY).
  3. --dry-run prints before/after counts, consequences and the exact command;
     it cannot write to the repository.
  4. Rewriting is delegated to git-filter-repo; this tool adds the guardrails,
     the backup, the post-checks and the loud "ROTATE your secrets" line.
  5. It never pushes and never contact a forge (except an optional `git ls-remote`
     freshness check and an optional best-effort `gh pr list`, both read-only).
Exit codes: 0 ok, 1 error, 2 usage error, 3 refused.
"""


def _build_parser() -> argparse.ArgumentParser:
    """Construct the ``rewrite-plan`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="rewrite-plan",
        description="Safe bulk Git history rewriting: plan, analyse, then execute via git-filter-repo.",
        epilog="Exit codes: 0 ok, 1 error, 2 usage error, 3 refused. See --docs-only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="bare mirror clone (dir.git) or working tree (refused unless --backup-only)",
    )
    parser.add_argument("--analyze", action="store_true", help="print the plan and consequences only")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; guarantees zero writes")
    parser.add_argument(
        "--backup-only",
        action="store_true",
        help="create the mirror backup and stop (never rewrites)",
    )
    parser.add_argument(
        "--remove",
        action="append",
        default=[],
        metavar="PATH",
        help="drop this path/prefix from all history (repeatable)",
    )
    parser.add_argument(
        "--bigger-than",
        metavar="SIZE",
        help="strip blobs larger than SIZE (e.g. 5M, 1.5G)",
    )
    parser.add_argument(
        "--rename",
        action="append",
        default=[],
        metavar="OLD:NEW",
        help="rename a path prefix in history (repeatable)",
    )
    parser.add_argument("--subdirectory-filter", metavar="DIR", help="keep only DIR/ in history")
    parser.add_argument(
        "--replace-text",
        metavar="FILE",
        help="git-filter-repo replacement expressions file",
    )
    parser.add_argument(
        "--prune-empty",
        default="never",
        choices=("always", "auto", "never"),
        help="empty-commit policy (default: never)",
    )
    parser.add_argument("--refs", metavar="RANGE", help="restrict to a rev range, e.g. 'main~50..main'")
    parser.add_argument(
        "--acknowledge-signatures",
        action="store_true",
        help="state that losing GPG signatures is accepted (still needs --i-know twice when they exist)",
    )
    parser.add_argument(
        "--i-know",
        action="count",
        default=0,
        help="acknowledge blocking consequences; must be given TWICE",
    )
    parser.add_argument(
        "--junit-xml",
        "--report",
        dest="junit_xml",
        metavar="FILE",
        help="write consequences as JUnit XML",
    )
    parser.add_argument(
        "--fresh",
        metavar="FORGE:OWNER/REPO",
        help="verify the mirror is a fresh copy (github:, gitlab:, or URL)",
    )
    parser.add_argument(
        "--keep-backup",
        default=DEFAULT_KEEP_BACKUP,
        metavar="DURATION",
        help="backup retention hint, e.g. 30d (default: %(default)s)",
    )
    parser.add_argument(
        "--prune-backups",
        action="store_true",
        help="actually delete expired backups (default: keep and print the rm line)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 3 when any blocking or warning consequence exists",
    )
    parser.add_argument(
        "--filter-repo",
        default="git-filter-repo",
        metavar="BIN",
        help="git-filter-repo executable (default: %(default)s)",
    )
    parser.add_argument(
        "--gh-remote",
        default="origin",
        metavar="NAME",
        help="remote for `gh pr list` (default: %(default)s)",
    )
    parser.add_argument("--bare", action="store_true", help="force bare-repo treatment of TARGET")
    parser.add_argument("--docs-only", action="store_true", help="print the safety model and exit 0")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _policy_from_args(args: argparse.Namespace) -> Policy:
    """Validate CLI arguments into a :class:`Policy` (raises ``ValueError``)."""
    bigger = parse_size(args.bigger_than) if args.bigger_than else None
    renames = tuple(parse_rename_spec(spec) for spec in args.rename)
    replace_text = Path(args.replace_text).expanduser() if args.replace_text else None
    if replace_text is not None and not replace_text.is_file():
        raise ValueError(f"--replace-text file not found: {replace_text}")
    refs = validate_ref(args.refs) if args.refs else None
    return Policy(
        remove=tuple(args.remove),
        bigger_than=bigger,
        rename=renames,
        subdirectory_filter=args.subdirectory_filter,
        replace_text=replace_text,
        prune_empty=args.prune_empty,
        refs=refs,
        acknowledge_signatures=args.acknowledge_signatures,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code (see module docstring)."""
    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exit_:
        return int(exit_.code or 0)
    if args.docs_only:
        sys.stdout.write(DOCS)
        return EXIT_OK
    if not args.target:
        parser.print_usage(sys.stderr)
        sys.stderr.write("rewrite-plan: error: a TARGET repository is required (see --docs-only)\n")
        return EXIT_USAGE
    target_path = Path(args.target).expanduser()
    try:
        policy = _policy_from_args(args)
        fresh_url = forge_url_from_spec(args.fresh) if args.fresh else None
        keep_backup = parse_duration(args.keep_backup)
    except ValueError as error:
        sys.stderr.write(f"rewrite-plan: error: {error}\n")
        return EXIT_USAGE
    analyze_only = args.analyze or args.dry_run
    if analyze_only and args.backup_only:
        sys.stderr.write("rewrite-plan: error: --analyze/--dry-run and --backup-only are mutually exclusive\n")
        return EXIT_USAGE
    if not analyze_only and not args.backup_only and policy.is_empty:
        sys.stderr.write(
            "rewrite-plan: error: nothing to do — pass --analyze, --dry-run or at least one of "
            "--remove/--bigger-than/--rename/--subdirectory-filter/--replace-text\n"
        )
        return EXIT_USAGE
    try:
        plan = build_plan(
            target_path,
            policy,
            allow_worktree=bool(args.backup_only or args.bare),
            fresh_check=args.fresh is not None,
            fresh_url=fresh_url,
        )
        if args.i_know > 0 and not sys.stdin.isatty():
            raise RewriteRefused(
                "--i-know refuses to run without a terminal: a human must see the consequences first.",
                plan=plan,
            )
        sys.stdout.write(render_plan(plan) + "\n")
        if args.junit_xml:
            path = write_junit_report(plan, Path(args.junit_xml).expanduser())
            sys.stdout.write(f"\nJUnit report written to {path}\n")
        if analyze_only:
            if args.strict and (plan.blocking or plan.warnings):
                sys.stdout.write(
                    f"\nSTRICT: {len(plan.blocking)} blocking / {len(plan.warnings)} warning consequence(s)\n"
                )
                return EXIT_REFUSED
            return EXIT_OK
        if args.backup_only:
            backup = create_mirror_backup(plan)
            sys.stdout.write(
                f"\nBACKUP CREATED  {_rel(backup)}\n"
                f"  restore: git -C {_shell_quote(_rel(backup))} push --mirror <forge-url>\n"
                f"  keep for: {args.keep_backup}"
                + ("" if args.prune_backups else " (pruning is opt-in: --prune-backups)")
                + "\n"
            )
            return EXIT_OK
        report = execute_plan(
            plan,
            acknowledged=args.i_know,
            interactive=sys.stdin.isatty(),
            filter_repo=args.filter_repo,
            keep_backup=keep_backup if args.prune_backups else None,
            prune_backups=args.prune_backups,
        )
        sys.stdout.write("\n" + report.render() + "\n")
        return EXIT_OK if report.fsck_ok else EXIT_ERROR
    except RewriteRefused as refusal:
        sys.stderr.write(str(refusal.reason) + "\n")
        return EXIT_REFUSED
    except (GitError, OSError) as error:
        sys.stderr.write(f"rewrite-plan: error: {error}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
