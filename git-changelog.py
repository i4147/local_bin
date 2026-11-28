#!/data/data/com.termux/files/usr/bin/env python
"""git-changelog - release notes written for the people who *use* the software.
Turning commits into Markdown is a solved problem (``git-cliff``,
``git-changelog``, ``towncrier``...).  What those tools produce is a list
written for the people who wrote the commits.  This tool attacks the
editorial gap instead:
1. **Breaking changes come with migration notes.**  ``feat!: drop Python 3.8``
   is a line; users need "Python 3.9+ required; replace ``foo()`` with
   ``foo(bar=...)``".  The note is looked for in the commit message (a
   ``Migration:`` paragraph or a ``BREAKING CHANGE:`` footer that tells the
   reader what to do), in a ``MIGRATION.md`` entry added by the commit, and
   in the pull-request body (``gh pr view``) - and only then is a visible
   placeholder emitted.
2. **Noise budget.**  Changes are ranked breaking > security > feature > fix
   > everything else; dependency bumps collapse into one line ("12 dependency
   updates, including 3 major"); internal work is counted, not listed.
3. **"How do I upgrade?"**  The public Python API of the two revisions is
   compared with ``ast`` on ``git archive`` trees: removed and renamed
   symbols and incompatible signature changes are listed explicitly.
4. **Honesty.**  No empty sections, no padding.  If nothing user-facing
   changed the notes say exactly that; truncated sections say how much was
   left out.
Usage
-----
::
    git-changelog --from v1.4.0 --to v1.5.0 --audience users --format md
    git-changelog --to v1.5.0                      # previous tag is found by version
    git-changelog --unreleased --audience maintainers
    git-changelog --unreleased --check             # CI: breaking change without a note -> exit 1
    git-changelog --api-diff v1.4.0..v1.5.0        # removed/renamed public symbols
    git-changelog --to v1.5.0 --write CHANGELOG.md # insert below <!-- changelog:next -->
Installed on ``PATH`` as ``git-changelog`` it also runs as ``git changelog``.
How things are recognised
-------------------------
* Commits follow Conventional Commits *loosely*: ``Fix: x``, ``feature(api): x``
  and friends are understood, and everything else lands in "Other changes"
  instead of being dropped.  Merge commits, release commits
  (``chore: release v1.5.0``) and changes reverted inside the range are left
  out.
* A migration note is "an instruction" when it tells the reader what to do,
  i.e. it contains a cue such as *use*, *replace*, *instead*, *rename*,
  *requires* (see ``INSTRUCTION_CUES``).  ``BREAKING CHANGE: the flag was
  removed`` describes the damage, not the way out, and fails ``--check``.
* Tags are ordered by *version*, never by date, and pre-releases are skipped
  when looking for the release that precedes a final one.
* Authors are read through ``.mailmap``; the maintainer view suggests entries
  for people who still appear under several names or addresses.
* ``--write`` never rewrites a changelog: the notes are inserted below the
  ``<!-- changelog:next -->`` marker and everything else stays byte-identical.
* Pull-request bodies are optional.  Without ``gh``, without a network or
  with ``--offline`` the tool works from the repository alone.
Exit codes: ``0`` success, ``1`` ``--check`` found breaking changes without
migration notes, ``2`` error.
Requires Python 3.11+, git 2.21+, ``typer``, ``rich`` and ``loguru``.
"""

from __future__ import annotations
import argparse
import ast
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
import dataclasses
from dataclasses import dataclass, field
import difflib
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Annotated, Any

from dh import atomic_write
from loguru import logger
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text


__all__: list[str] = [
    "ApiChange",
    "ApiSnapshot",
    "Audience",
    "Category",
    "Change",
    "Commit",
    "GitError",
    "PullRequest",
    "PullRequestFetcher",
    "ReleaseNotes",
    "RevRange",
    "Version",
    "app",
    "build_release_notes",
    "diff_api",
    "extract_api",
    "extract_migration_section",
    "insert_into_changelog",
    "main",
    "parse_commit",
    "parse_version",
    "render_json",
    "render_markdown",
    "resolve_range",
    "snapshot_api",
]
__version__: str = "1.0.0"
APP_NAME: str = "git-changelog"
EXIT_OK: int = 0
"""Exit code: success."""
EXIT_CHECK_FAILED: int = 1
"""Exit code: ``--check`` found a breaking change without a migration note."""
EXIT_ERROR: int = 2
"""Exit code: something went wrong (bad revision, not a repository, ...)."""
CHANGELOG_MARKER: str = "<!-- changelog:next -->"
"""Marker below which ``--write`` inserts new notes."""
UNRELEASED_TITLE: str = "Unreleased"
"""Title of notes for a range that does not end at a version tag."""
UNRELEASED_ID: str = "unreleased"
"""Identifier of the changelog entry for such a range (``--unreleased --write``)."""
DEFAULT_MIGRATION_FILES: tuple[str, ...] = ("MIGRATION.md", "UPGRADING.md")
"""Files whose additions count as the migration note of the commit adding them."""
DEFAULT_USER_BUDGET: int = 25
"""Maximum number of bullets in notes for users (breaking/security are never cut)."""
DEFAULT_PR_LOOKUPS: int = 30
"""Maximum number of pull requests fetched through ``gh`` per run."""
SECTION_FLOOR: int = 3
"""Bullets every non-empty section gets before the budget is spent by rank."""
API_SECTION_LIMIT: int = 25
"""Maximum number of API changes listed in notes for users."""
INSTRUCTION_CUES: tuple[str, ...] = (
    "use",
    "instead",
    "replace",
    "replaced",
    "rename",
    "renamed",
    "switch to",
    "migrate",
    "upgrade to",
    "update your",
    "moved to",
    "in favor of",
    "in favour of",
    "pass",
    "set",
    "run",
    "call",
    "install",
    "import",
    "convert",
    "must",
    "should",
    "need to",
    "needs to",
    "have to",
    "require",
    "requires",
    "required",
    "->",
    "→",
)
"""What makes a ``BREAKING CHANGE:`` footer an instruction rather than a description.
``BREAKING CHANGE: the --fast flag was removed`` names the damage;
``...; use --quick instead`` names the way out.  A footer without any of
these cues does not count as a migration note (an explicit ``Migration:``
paragraph always does).
"""
# Quiet when imported as a library; the CLI re-enables logging.
logger.disable(__name__)


class Audience(StrEnum):
    """Who the notes are written for."""

    USERS = "users"
    """People upgrading: what breaks, what is new, nothing internal."""
    MAINTAINERS = "maintainers"
    """People working on the project: everything, with commits and authors."""


class OutputFormat(StrEnum):
    """How the result is printed."""

    MD = "md"
    """Raw Markdown (the default; safe to redirect into a file)."""
    JSON = "json"
    """Machine-readable JSON."""
    TERM = "term"
    """Rendered for the terminal with rich."""


class Category(StrEnum):
    """Ranking bucket of a change, most important first."""

    BREAKING = "breaking"
    SECURITY = "security"
    FEATURE = "feature"
    FIX = "fix"
    PERFORMANCE = "performance"
    OTHER = "other"
    DEPENDENCY = "dependency"
    INTERNAL = "internal"


CATEGORY_RANK: dict[Category, int] = {category: rank for rank, category in enumerate(Category)}
"""Lower is more important; follows the declaration order of :class:`Category`."""
USER_SECTIONS: tuple[tuple[Category, str], ...] = (
    (Category.BREAKING, "Breaking changes"),
    (Category.SECURITY, "Security"),
    (Category.FEATURE, "New features"),
    (Category.FIX, "Bug fixes"),
    (Category.PERFORMANCE, "Performance"),
    (Category.OTHER, "Other changes"),
)
"""Sections shown to every audience, in order."""
INTERNAL_SECTIONS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("docs", "Documentation", frozenset({"docs"})),
    ("refactor", "Refactoring and style", frozenset({"refactor", "style"})),
    ("test", "Tests", frozenset({"test"})),
    ("build", "Build and CI", frozenset({"build", "ci"})),
    ("chore", "Chores", frozenset({"chore"})),
)
"""Maintainer-only sections: key, title and the commit types they collect."""
KNOWN_TYPES: frozenset[str] = frozenset({
    "feat",
    "fix",
    "perf",
    "security",
    "deps",
    "docs",
    "refactor",
    "style",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
})
"""Commit types the parser understands after alias resolution."""
TYPE_ALIASES: dict[str, str] = {
    "feature": "feat",
    "features": "feat",
    "bug": "fix",
    "bugfix": "fix",
    "hotfix": "fix",
    "fixes": "fix",
    "fixed": "fix",
    "performance": "perf",
    "sec": "security",
    "dep": "deps",
    "dependencies": "deps",
    "doc": "docs",
    "documentation": "docs",
    "refactoring": "refactor",
    "styles": "style",
    "tests": "test",
    "testing": "test",
    "chores": "chore",
    "reverts": "revert",
}
"""Spellings people actually use, mapped to the canonical type."""


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------
def _plural(count: int, noun: str) -> str:
    """Format *count* with *noun* in the right number.
    Args:
        count: The quantity.
        noun: Singular noun (its last word is inflected).
    Returns:
        E.g. ``"1 bug fix"``, ``"3 bug fixes"``, ``"2 dependency updates"``.
    """
    if count == 1:
        return f"{count} {noun}"
    if noun.endswith(("s", "x", "ch", "sh")):
        return f"{count} {noun}es"
    if noun.endswith("y") and noun[-2:-1] not in "aeiou":
        return f"{count} {noun[:-1]}ies"
    return f"{count} {noun}s"


def _join(parts: Sequence[str]) -> str:
    """Join *parts* the way a sentence lists things.
    Args:
        parts: Items to list.
    Returns:
        ``"a"``, ``"a and b"`` or ``"a, b and c"``.
    """
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


# ---------------------------------------------------------------------------
# Git plumbing (argv lists only, never a shell)
# ---------------------------------------------------------------------------
class GitError(RuntimeError):
    """Raised when git fails or the repository cannot be used."""


def _git_env() -> dict[str, str]:
    """Return the environment for git subprocesses.
    Returns:
        The current environment with the locale forced to ``C`` so messages
        are stable.
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANGUAGE"] = "C"
    return env


def run_git(repo: Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` inside *repo*.
    Args:
        repo: Directory inside the repository.
        args: Git subcommand and arguments, one argv entry each.
        check: Raise :class:`GitError` on a non-zero exit status.
    Returns:
        The completed process with text output (invalid UTF-8 is replaced).
    Raises:
        GitError: If git cannot be started, or fails while *check* is true.
    """
    logger.debug("$ git {}", " ".join(args))
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=_git_env(),
            check=False,
        )
    except OSError as exc:
        raise GitError(f"cannot run git in {repo}: {exc}") from exc
    if check and proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "no output"
        raise GitError(f"git {' '.join(args)} failed: {message}")
    return proc


def repository_root(repo: Path) -> Path:
    """Find the directory from which git is run for the repository of *repo*.
    Pathspecs and ``git archive`` are relative to the working directory, so
    everything is run from the top of the work tree - a tool started in
    ``src/pkg`` must still see the whole project.
    Args:
        repo: A directory inside the repository.
    Returns:
        The top level of the work tree, or the repository directory itself
        for a bare repository.
    Raises:
        GitError: If *repo* is not a directory or not inside a repository.
    """
    if not repo.is_dir():
        raise GitError(f"{repo} is not a directory")
    top = run_git(repo, ["rev-parse", "--show-toplevel"], check=False)
    if top.returncode == 0 and top.stdout.strip("\n"):
        return Path(top.stdout.strip("\n"))
    git_dir = run_git(repo, ["rev-parse", "--absolute-git-dir"], check=False)
    if git_dir.returncode != 0:
        raise GitError(f"{repo} is not inside a git repository")
    return Path(git_dir.stdout.strip("\n"))


def resolve_commit(repo: Path, ref: str) -> str:
    """Resolve *ref* to a commit id.
    Args:
        repo: Directory inside the repository.
        ref: Tag, branch or any other commit-ish.
    Returns:
        The full commit id.
    Raises:
        GitError: If *ref* does not name a commit.
    """
    if not ref or ref.startswith("-"):  # never let a revision be read as an option
        raise GitError(f"invalid revision {ref!r}")
    proc = run_git(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], check=False)
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not sha:
        raise GitError(f"unknown revision {ref!r}")
    return sha


def detect_web_url(repo: Path) -> str | None:
    """Derive the GitHub web address of the repository from its ``origin``.
    Credentials embedded in the remote URL are never part of the result.
    Args:
        repo: Directory inside the repository.
    Returns:
        E.g. ``https://github.com/owner/project``, or ``None`` when there is
        no ``origin`` or it is not hosted on a GitHub instance.
    """
    proc = run_git(repo, ["remote", "get-url", "origin"], check=False)
    match = re.match(
        r"^(?:https?://(?:[^@/]+@)?|ssh://(?:[^@/]+@)?|[^@/:]+@)"
        r"(?P<host>[^/:]+)(?::\d+)?[/:](?P<path>[^:]+?)(?:\.git)?/?$",
        proc.stdout.strip(),
    )
    if proc.returncode != 0 or not match or "github" not in match["host"].lower():
        return None
    return f"https://{match['host']}/{match['path']}"


# ---------------------------------------------------------------------------
# Versions, tags and revision ranges
# ---------------------------------------------------------------------------
_VERSION_RE = re.compile(
    r"^(?P<prefix>(?:@?[A-Za-z][\w./-]*?[-/@_])?v?)"
    r"(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-._]?(?P<label>dev|alpha|beta|preview|post|pre|rc|a|b|c)[-._]?(?P<number>\d*))?"
    r"(?:\+[\w.-]+)?$",
    re.IGNORECASE,
)
_FINAL_PHASE: int = 4
_PHASES: dict[str, int] = {
    "dev": 0,
    "a": 1,
    "alpha": 1,
    "b": 2,
    "beta": 2,
    "c": 3,
    "rc": 3,
    "pre": 3,
    "preview": 3,
    "post": 5,
}


@dataclass(frozen=True)
class Version:
    """A tag understood as a version number.
    Attributes:
        tag: The tag name as it is in the repository.
        prefix: What precedes the number (``v``, ``release-``, ``pkg@``).
        release: The numeric components without trailing zeros.
        phase: Position in the release cycle: 0 dev, 1 alpha, 2 beta,
            3 release candidate, 4 final, 5 post-release.
        number: The number of the pre- or post-release (``rc2`` -> 2).
        commit: The commit the tag points at, when known.
    """

    tag: str
    prefix: str
    release: tuple[int, ...]
    phase: int = _FINAL_PHASE
    number: int = 0
    commit: str = field(default="", compare=False)

    @property
    def is_final(self) -> bool:
        """True unless the tag is an alpha, beta, release candidate or dev build."""
        return self.phase >= _FINAL_PHASE

    @property
    def sort_key(self) -> tuple[tuple[int, ...], int, int]:
        """Key that orders versions; a pre-release sorts before its final release."""
        return (self.release, self.phase, self.number)


def parse_version(tag: str, commit: str = "") -> Version | None:
    """Interpret a tag name as a version.
    Only names that *are* a version are accepted - ``v1.5.0``, ``1.5``,
    ``v2.0.0-rc.1``, ``release-2024.10``, ``pkg@3.1.0`` - so a tag such as
    ``before-refactor-2024`` is never mistaken for a release.
    Args:
        tag: The tag name.
        commit: The commit the tag points at, if known.
    Returns:
        The parsed version, or ``None`` when the tag is not a version.
    """
    match = _VERSION_RE.match(tag)
    if not match:
        return None
    release = [int(part) for part in match["release"].split(".")]
    if len(release) == 1 and match["prefix"].lower() not in ("", "v"):
        return None  # "name-2024" is a label, not a version
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    phase = _PHASES[match["label"].lower()] if match["label"] else _FINAL_PHASE
    return Version(tag, match["prefix"], tuple(release), phase, int(match["number"] or 0), commit)


def list_versions(repo: Path, merged_into: str | None = None) -> list[Version]:
    """List the version tags of *repo*, newest version first.
    Tags are ordered by version, never by date: a maintenance release made
    last week must not hide the major release from last month.
    Args:
        repo: Directory inside the repository.
        merged_into: Only tags reachable from this revision.
    Returns:
        Parsed version tags, highest version first.
    """
    fields = "%(refname)%00%(objectname)%00%(*objectname)"
    args = ["for-each-ref", "--sort=-v:refname", f"--format={fields}"]
    if merged_into:
        args.append(f"--merged={resolve_commit(repo, merged_into)}")
    versions: list[Version] = []
    for line in run_git(repo, [*args, "refs/tags"]).stdout.splitlines():
        ref, _, objects = line.partition("\0")
        tagged, _, peeled = objects.partition("\0")
        version = parse_version(ref.removeprefix("refs/tags/"), peeled or tagged)
        if version is not None:
            versions.append(version)
    # git's own version sort puts v1.0-rc1 *after* v1.0; ours does not.
    return sorted(versions, key=lambda version: version.sort_key, reverse=True)


@dataclass(frozen=True)
class RevRange:
    """The revisions a set of release notes covers.
    Attributes:
        from_ref: Exclusive start, or ``None`` for "from the first commit".
        to_ref: Inclusive end.
        title: Heading of the notes: the tag name or ``Unreleased``.
        date: Date (``YYYY-MM-DD``) of the release, for tagged releases.
    """

    from_ref: str | None
    to_ref: str
    title: str
    date: str | None = None

    @property
    def is_release(self) -> bool:
        """True when the range ends at a version tag, false for unreleased work."""
        return self.title != UNRELEASED_TITLE

    @property
    def spec(self) -> str:
        """The range in git's ``A..B`` notation."""
        return f"{self.from_ref}..{self.to_ref}" if self.from_ref else self.to_ref

    @property
    def since(self) -> str:
        """The start of the range in words."""
        return f"since {self.from_ref}" if self.from_ref else "since the first commit"


def _is_tag(repo: Path, name: str) -> bool:
    """Tell whether *name* is the name of a tag.
    Args:
        repo: Directory inside the repository.
        name: Candidate tag name.
    Returns:
        True when ``refs/tags/<name>`` exists.
    """
    proc = run_git(repo, ["rev-parse", "--verify", "--quiet", f"refs/tags/{name}"], check=False)
    return proc.returncode == 0


def previous_version(repo: Path, to_ref: str) -> str | None:
    """Find the release that precedes *to_ref*.
    Args:
        repo: Directory inside the repository.
        to_ref: End of the range: a version tag, or any other revision.
    Returns:
        The highest version tag reachable from *to_ref* that is lower than
        it.  Pre-releases are skipped unless *to_ref* is a pre-release itself
        or nothing else exists; other tags on the very commit of a tagged
        *to_ref* (a moving ``v4`` next to ``v4.1.2``) are skipped as well.
        ``None`` when there is no earlier tag.
    """
    target = parse_version(to_ref) if _is_tag(repo, to_ref) else None
    candidates = list_versions(repo, merged_into=to_ref)
    if target is not None:
        end = resolve_commit(repo, to_ref)
        candidates = [
            version
            for version in candidates
            if version.prefix == target.prefix and version.sort_key < target.sort_key and version.commit != end
        ]
    if target is None or target.is_final:
        candidates = [v for v in candidates if v.is_final] or candidates
    return candidates[0].tag if candidates else None


def resolve_range(
    repo: Path,
    from_ref: str | None = None,
    to_ref: str | None = None,
    *,
    unreleased: bool = False,
) -> RevRange:
    """Work out which commits the notes cover.
    Args:
        repo: Directory inside the repository.
        from_ref: Exclusive start; found from the tags when omitted.
        to_ref: Inclusive end; ``HEAD`` when omitted.
        unreleased: Cover everything after the latest release up to ``HEAD``.
    Returns:
        The resolved range with its title.
    Raises:
        GitError: If a revision does not exist or the options contradict.
    """
    if unreleased and (from_ref or to_ref):
        raise GitError("--unreleased cannot be combined with --from or --to")
    end = to_ref or "HEAD"
    if run_git(repo, ["rev-parse", "--verify", "--quiet", "HEAD"], check=False).returncode != 0:
        raise GitError("the repository has no commits yet")
    resolve_commit(repo, end)
    start = from_ref or previous_version(repo, end)
    if start:
        resolve_commit(repo, start)
    if not unreleased and _is_tag(repo, end):
        created = run_git(repo, ["for-each-ref", "--format=%(creatordate:short)", f"refs/tags/{end}"]).stdout.strip()
        return RevRange(start, end, title=end, date=created or None)
    return RevRange(start, end, title=UNRELEASED_TITLE)


# ---------------------------------------------------------------------------
# Commits and the tolerant conventional-commit parser
# ---------------------------------------------------------------------------
_FIELD: str = "\x1f"
_RECORD: str = "\x1e"
_LOG_FORMAT: str = _FIELD.join(["%H", "%aI", "%aN", "%aE", "%s", "%b"]) + _RECORD
_HEADER_RE = re.compile(
    r"^(?P<type>[A-Za-z][A-Za-z0-9_-]*)(?:\((?P<scope>[^()\r\n]*)\))?(?P<bang>!)?\s*:\s*"
    r"(?P<desc>\S.*)$"
)
_PR_SUFFIX_RE = re.compile(r"\s*\(#(?P<number>\d+)\)\s*$")
_REF_RE = re.compile(r"(?<![\w&/])#(\d+)\b")
_GIT_REVERT_RE = re.compile(r'^Revert\s+"(?P<subject>.+)"')
_REVERTED_SHA_RE = re.compile(r"\bThis reverts commit ([0-9a-f]{7,40})\b")
_REVERTED_PR_RE = re.compile(r"^Reverts\s+(?:[\w.-]+/[\w.-]+)?#(\d+)\b", re.MULTILINE)
_AUTOSQUASH_RE = re.compile(r"^(?:fixup|squash|amend)! ")
_BREAKING_RE = re.compile(r"^(?i:breaking(?:[ -]changes?)?)\s*:\s*(?P<value>.*)$")
_CO_AUTHOR_RE = re.compile(r"^(?i:co-authored-by):\s*(?P<name>[^<]+?)\s*(?:<[^>]*>)?\s*$")
_TRAILER_RE = re.compile(
    r"^(?:(?i:breaking(?:[ -]changes?)?)\s*:"
    r"|[A-Za-z]+(?:-[A-Za-z]+)+:(?:\s|$)"
    r"|(?i:fix(?:e[sd])?|close[sd]?|resolve[sd]?|refs?|see(?:\s+also)?|related(?:\s+to)?|cc)"
    r"(?::(?:\s|$)|(?:\s*(?:,|and|&)?\s*(?:(?:[\w.-]+/[\w.-]+)?#\d+|https?://\S+|@[\w-]+))+"
    r"\s*\.?\s*$))"
)
"""A line that ends the prose of a message: ``Signed-off-by: ...``, ``Fixes #12``."""
_RELEASE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:chore|build|release)\((?:release|version)\)!?:", re.IGNORECASE),
    re.compile(r"^\w+\((?:release|version)\)!?:.*\d+\.\d+", re.IGNORECASE),
    re.compile(r"^release(?:\([^)]*\))?!?:\s*v?\d", re.IGNORECASE),
    re.compile(
        r"^(?:\w+(?:\([^)]*\))?!?:\s*)?(?:prepare\s+(?:for\s+)?)?"
        r"(?:release|bump\s+version|version\s+bump|cut\s+release)"
        r"(?:\s+(?:of|to|for|from|version))*\s*:?\s*v?\d+(?:\.\d+)+",
        re.IGNORECASE,
    ),
    re.compile(r"^v?\d+\.\d+(?:\.\d+)*(?:[-+.]?[\w.]+)?$"),
)
_DEPENDENCY_SCOPES = frozenset({
    "deps",
    "dep",
    "dependencies",
    "dependency",
    "deps-dev",
    "dev-deps",
    "requirements",
})
_BUMP_FROM_TO_RE = re.compile(
    r"\b(?:bump|update|upgrade)s?\s+(?:dependency\s+|the\s+)?(?P<name>[\w@/.\[\]-]+)\s+from\s+"
    r"v?(?P<old>\d[\w.+-]*)\s+to\s+v?(?P<new>\d[\w.+-]*)",
    re.IGNORECASE,
)
_BUMP_TO_RE = re.compile(
    r"\b(?:bump|update|upgrade)s?\s+(?:dependency\s+|the\s+)?(?P<name>[\w@/.\[\]-]+)\s+to\s+"
    r"v?(?P<new>\d[\w.+-]*)",
    re.IGNORECASE,
)
_ADVISORY_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b|\bGHSA(?:-[0-9a-z]{4}){3}\b", re.IGNORECASE)
_SECURITY_WORD_RE = re.compile(r"\bsecurity\b|\bvulnerabilit(?:y|ies)\b", re.IGNORECASE)
_INSTRUCTION_RE = re.compile(
    "|".join(
        rf"\b{re.escape(cue)}\b".replace(r"\ ", r"\s+") if cue[0].isalpha() else re.escape(cue)
        for cue in INSTRUCTION_CUES
    ),
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Commit:
    """A commit as read from ``git log``.
    Attributes:
        sha: Full commit id.
        date: Author date in ISO 8601.
        author: Author name after ``.mailmap``.
        email: Author e-mail after ``.mailmap``.
        subject: First line of the message.
        body: The rest of the message.
    """

    sha: str
    date: str
    author: str
    email: str
    subject: str
    body: str = ""

    @property
    def short(self) -> str:
        """Abbreviated commit id."""
        return self.sha[:7]


@dataclass(frozen=True)
class DependencyBump:
    """What a dependency update changed.
    Attributes:
        name: Package name, when the subject names one.
        old: Previous version, when stated.
        new: New version, when stated.
        major: True when the major version changed.
    """

    name: str | None = None
    old: str | None = None
    new: str | None = None
    major: bool = False

    @property
    def label(self) -> str:
        """Short description such as ``requests 2 → 3``."""
        name = self.name or "a dependency"
        if self.old and self.new:
            return f"{name} {_major_of(self.old)} → {_major_of(self.new)}"
        return f"{name} → {self.new}" if self.new else name


@dataclass
class Change:
    """A commit, interpreted.
    Attributes:
        commit: The underlying commit.
        type: Canonical conventional type, or ``None`` for free-form subjects.
        scope: Conventional scope, if any.
        description: The subject without type, scope and PR suffix.
        category: Ranking bucket.
        breaking: True for ``type!:`` subjects and ``BREAKING CHANGE:`` footers.
        breaking_notes: Texts of the ``BREAKING CHANGE:`` footers.
        pr: Pull-request number from a ``(#123)`` subject suffix.
        refs: Every ``#123`` mentioned in the message, PR first.
        dependency: Set when the change is a dependency update.
        reverts: Commit id (possibly abbreviated) this commit reverts.
        reverts_pr: Pull request this commit reverts (GitHub's ``Reverts #123``).
        reverted_by: Commit id of the commit in the range that reverts this one.
        cancelled: True when the change was reverted inside the range, or is
            a revert of something inside the range.
        migration: What users must do, if anybody wrote it down.
        migration_source: Where *migration* was found.
        detail: One-paragraph summary taken from the pull-request body.
        co_authors: Names from ``Co-authored-by:`` trailers.
    """

    commit: Commit
    type: str | None
    scope: str | None
    description: str
    category: Category = Category.OTHER
    breaking: bool = False
    breaking_notes: list[str] = field(default_factory=list)
    pr: int | None = None
    refs: list[int] = field(default_factory=list)
    dependency: DependencyBump | None = None
    reverts: str | None = None
    reverts_pr: int | None = None
    reverted_by: str | None = None
    cancelled: bool = False
    migration: str | None = None
    migration_source: str | None = None
    detail: str | None = None
    co_authors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable description of the change.
        Returns:
            A dict with the commit, its interpretation and its migration note.
        """
        return {
            "sha": self.commit.sha,
            "date": self.commit.date,
            "author": self.commit.author,
            "subject": self.commit.subject,
            "type": self.type,
            "scope": self.scope,
            "description": self.description,
            "category": self.category.value,
            "breaking": self.breaking,
            "breaking_notes": list(self.breaking_notes),
            "pr": self.pr,
            "refs": list(self.refs),
            "migration": self.migration,
            "migration_source": self.migration_source,
            "detail": self.detail,
        }


def _range_spec(repo: Path, rev_range: RevRange) -> str:
    """Turn a range into commit ids that git cannot misread.
    Args:
        repo: Directory inside the repository.
        rev_range: The revisions.
    Returns:
        ``<sha>..<sha>``, or a single commit id for a range without a start.
    Raises:
        GitError: If one end of the range does not name a commit.
    """
    end = resolve_commit(repo, rev_range.to_ref)
    return f"{resolve_commit(repo, rev_range.from_ref)}..{end}" if rev_range.from_ref else end


def read_commits(repo: Path, rev_range: RevRange) -> list[Commit]:
    """Read the non-merge commits of *rev_range*, newest first.
    Args:
        repo: Directory inside the repository.
        rev_range: The revisions to read.
    Returns:
        The commits, with author names and e-mails resolved through ``.mailmap``.
    Raises:
        GitError: If one end of the range does not name a commit.
    """
    spec = _range_spec(repo, rev_range)
    out = run_git(repo, ["log", "--no-merges", f"--format={_LOG_FORMAT}", spec, "--"])
    commits: list[Commit] = []
    for record in out.stdout.split(_RECORD):
        fields = record.lstrip("\n").split(_FIELD)
        if len(fields) != 6:
            continue
        sha, date, author, email, subject, body = fields
        commits.append(Commit(sha, date, author, email, subject.strip(), body.strip()))
    return commits


def is_release_commit(subject: str) -> bool:
    """Tell whether a subject is release bookkeeping.
    ``chore: release v1.5.0`` must not show up in the notes of v1.5.0.
    Args:
        subject: First line of a commit message.
    Returns:
        True for version bumps and release commits.
    """
    return any(pattern.search(subject.strip()) for pattern in _RELEASE_PATTERNS)


def _major_of(version: str) -> str:
    """Return the leading number of a version string.
    Args:
        version: E.g. ``2.31.0``.
    Returns:
        E.g. ``2`` (the input itself when it has no leading number).
    """
    match = re.match(r"\d+", version)
    return match.group() if match else version


def detect_dependency(type_: str | None, scope: str | None, text: str) -> DependencyBump | None:
    """Recognise dependency updates from Dependabot, Renovate and humans.
    Args:
        type_: Canonical commit type, if the subject was conventional.
        scope: Commit scope, if any.
        text: The description (or the whole subject for free-form commits).
    Returns:
        The update, or ``None`` when the commit is not a dependency update.
    """
    scoped = type_ == "deps" or (scope or "").lower() in _DEPENDENCY_SCOPES
    full = _BUMP_FROM_TO_RE.search(text)
    partial = None if full else _BUMP_TO_RE.search(text)
    if not scoped and not ((full or partial) and type_ in (None, "build", "chore", "ci")):
        return None
    if full:
        major = _major_of(full["old"]) != _major_of(full["new"])
        return DependencyBump(full["name"], full["old"], full["new"], major)
    flagged = bool(re.search(r"(?<!non-)\bmajor\b", text, re.IGNORECASE))
    if partial:
        # Renovate writes "to v3" for major updates and "to v3.1.2" otherwise.
        bare_major = re.fullmatch(r"\d+", partial["new"]) is not None
        return DependencyBump(partial["name"], None, partial["new"], bare_major or flagged)
    return DependencyBump(major=flagged)


def _is_security(type_: str | None, scope: str | None, subject: str, body: str) -> bool:
    """Tell whether a commit is a security fix.
    Args:
        type_: Canonical commit type, if any.
        scope: Commit scope, if any.
        subject: First line of the message.
        body: The rest of the message.
    Returns:
        True for ``security:`` commits, ``fix(security):`` scopes, advisory
        ids (CVE/GHSA) and fixes whose subject talks about security.
    """
    if type_ == "security" or (scope or "").lower() in {"security", "sec"}:
        return True
    if type_ in {"docs", "test", "ci", "style"}:
        return False
    if _ADVISORY_RE.search(subject) or _ADVISORY_RE.search(body):
        return True
    return type_ in (None, "fix") and bool(_SECURITY_WORD_RE.search(subject))


def _breaking_notes(body: str) -> list[str]:
    """Collect the texts of ``BREAKING CHANGE:`` footers.
    Args:
        body: Commit message body or pull-request description.
    Returns:
        One text per footer, continuation lines joined; empty footers dropped.
    """
    notes: list[list[str]] = []
    current: list[str] | None = None
    for line in body.splitlines():
        match = _BREAKING_RE.match(line.strip())
        if match:
            current = [match["value"].strip()]
            notes.append(current)
        elif current is not None:
            if not line.strip() or _TRAILER_RE.match(line) or _is_migration_marker(line):
                current = None
            else:
                current.append(line.strip())
    return [text for note in notes if (text := " ".join(part for part in note if part))]


def has_instruction(text: str) -> bool:
    """Tell whether *text* tells the reader what to do.
    Args:
        text: A breaking-change footer.
    Returns:
        True when the text contains one of :data:`INSTRUCTION_CUES`.
    """
    return bool(_INSTRUCTION_RE.search(text))


def _categorize(change: Change, *, security: bool) -> Category:
    """Pick the ranking bucket of *change*.
    Args:
        change: A parsed change (``breaking``, ``type`` and ``dependency`` set).
        security: Whether the commit is a security fix.
    Returns:
        The most important category that applies.
    """
    if change.breaking:
        return Category.BREAKING
    if security:
        return Category.SECURITY
    if change.dependency is not None:
        return Category.DEPENDENCY
    by_type = {
        "feat": Category.FEATURE,
        "fix": Category.FIX,
        "perf": Category.PERFORMANCE,
    }
    if change.type in by_type:
        return by_type[change.type]
    return Category.OTHER if change.type in (None, "revert") else Category.INTERNAL


def parse_commit(commit: Commit) -> Change:
    """Interpret a commit message.
    The parser is tolerant: unknown or missing types do not lose the commit,
    it simply becomes an "other change" with its full subject.
    Args:
        commit: The commit to interpret.
    Returns:
        The interpreted change, including any migration note found in the
        message itself.
    """
    subject = commit.subject.strip()
    pr: int | None = None
    suffix = _PR_SUFFIX_RE.search(subject)
    if suffix:
        pr = int(suffix["number"])
        subject = subject[: suffix.start()].rstrip()
    type_: str | None = None
    scope: str | None = None
    description = subject
    bang = False
    header = _HEADER_RE.match(subject)
    if header:
        candidate = TYPE_ALIASES.get(header["type"].lower(), header["type"].lower())
        if candidate in KNOWN_TYPES:
            type_ = candidate
            scope = (header["scope"] or "").strip() or None
            description = header["desc"].strip()
            bang = bool(header["bang"])
    reverts: str | None = None
    reverts_pr: int | None = None
    if type_ == "revert" or _GIT_REVERT_RE.match(subject):
        type_ = "revert"
        by_sha = _REVERTED_SHA_RE.search(commit.body)
        by_pr = _REVERTED_PR_RE.search(commit.body)
        reverts = by_sha.group(1) if by_sha else None
        reverts_pr = int(by_pr.group(1)) if by_pr else None
    elif _AUTOSQUASH_RE.match(subject):
        type_ = "chore"  # leftovers of an interactive rebase
    notes = _breaking_notes(commit.body)
    refs: list[int] = [pr] if pr is not None else []
    for number in _REF_RE.findall(f"{subject}\n{commit.body}"):
        if int(number) not in refs:
            refs.append(int(number))
    co_authors = [
        match["name"].strip() for line in commit.body.splitlines() if (match := _CO_AUTHOR_RE.match(line.strip()))
    ]
    change = Change(
        commit=commit,
        type=type_,
        scope=scope,
        description=description,
        breaking=bang or bool(notes),
        breaking_notes=notes,
        pr=pr,
        refs=refs,
        dependency=detect_dependency(type_, scope, description),
        reverts=reverts,
        reverts_pr=reverts_pr,
        co_authors=co_authors,
    )
    change.category = _categorize(change, security=_is_security(type_, scope, subject, commit.body))
    if change.breaking:
        _note_from_text(change, commit.body, "commit message")
    return change


def cancel_reverts(changes: Sequence[Change]) -> int:
    """Mark changes that were reverted inside the range.
    A feature that was added and reverted before the release never reached
    users, so neither commit belongs in the notes.  A revert of a revert
    brings the original change back.  The reverted commit is identified by
    ``This reverts commit <sha>`` (git), by ``Reverts #123`` (a squash-merged
    GitHub revert pull request) or, failing both, by the quoted subject.
    Args:
        changes: Changes of one range, newest first (as git prints them).
    Returns:
        The number of changes that were cancelled.
    """
    position = {change.commit.sha: index for index, change in enumerate(changes)}

    def find(change: Change) -> Change | None:
        if change.type != "revert":
            return None
        if change.reverts:
            return next((c for c in changes if c.commit.sha.startswith(change.reverts)), None)
        older = changes[position[change.commit.sha] + 1 :]
        if change.reverts_pr is not None:
            return next((c for c in older if c.pr == change.reverts_pr), None)
        quoted = _GIT_REVERT_RE.match(change.commit.subject)
        if quoted is None:
            return None
        subject = quoted["subject"]
        return next(
            (c for c in older if subject in (c.commit.subject, _PR_SUFFIX_RE.sub("", c.commit.subject))),
            None,
        )

    for change in reversed(changes):  # oldest first
        target = find(change)
        if target is None:
            continue
        change.cancelled = True
        target.reverted_by = change.commit.sha
        reverted = True
        while (inner := find(target)) is not None:
            target = inner
            reverted = not reverted
        target.cancelled = reverted
    return sum(1 for change in changes if change.cancelled)


# ---------------------------------------------------------------------------
# Migration notes
# ---------------------------------------------------------------------------
_MIGRATION_WORDS: str = (
    r"(?:migration|migrating|migrate|upgrade|upgrading|how\s+to\s+(?:upgrade|migrate))"
    r"(?:\s+(?:notes?|guide|instructions?|path|steps))?"
)
_MIGRATION_HEADING_RE = re.compile(
    rf"^\s{{0,3}}(?:#{{1,6}}\s+{_MIGRATION_WORDS}\s*:?"
    rf"|(?:\*\*|__){_MIGRATION_WORDS}\s*:?(?:\*\*|__)\s*:?)\s*$",
    re.IGNORECASE,
)
_MIGRATION_LABEL_RE = re.compile(
    rf"^\s{{0,3}}(?:\*\*|__)?{_MIGRATION_WORDS}\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(?P<inline>.*)$",
    re.IGNORECASE,
)
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_BOLD_HEADING_RE = re.compile(r"^\s*(?:\*\*|__)[^*_]+(?:\*\*|__)\s*:?\s*$")
_MD_BLOCK_RE = re.compile(r"^\s*(?:[-*+>|#]\s|\d+[.)]\s|```|~~~|\||<|!\[)")
_MD_RULE_RE = re.compile(r"^(?:[-*_=]\s*){3,}$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_NO_NOTE = frozenset({
    "",
    "none",
    "n/a",
    "na",
    "no",
    "nothing",
    "tbd",
    "todo",
    "no response",
    "not applicable",
})
_PR_MISSING_RE = re.compile(
    r"could not resolve to a pullrequest|no pull requests? (?:found|match)",
    re.IGNORECASE,
)
_SHA_TOKEN_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def _is_migration_marker(line: str) -> bool:
    """Tell whether *line* starts a migration note.
    Args:
        line: One line of a message.
    Returns:
        True for ``Migration: ...`` labels and ``## Migration`` headings.
    """
    return bool(_MIGRATION_LABEL_RE.match(line) or _MIGRATION_HEADING_RE.match(line))


def _fence_mask(lines: Sequence[str]) -> list[bool]:
    """Mark the lines that belong to fenced code blocks.
    Args:
        lines: The lines of a Markdown text.
    Returns:
        One flag per line; true for the fences and everything between them.
    """
    mask: list[bool] = []
    fence = ""
    for line in lines:
        stripped = line.lstrip()
        if fence:
            mask.append(True)
            if stripped.startswith(fence):
                fence = ""
        else:
            fence = stripped[:3] if stripped.startswith(("```", "~~~")) else ""
            mask.append(bool(fence))
    return mask


def _migration_blocks(lines: Sequence[str]) -> list[tuple[int, int, str]]:
    """Locate the migration notes in the lines of a message.
    Args:
        lines: The lines of a commit message or pull-request description.
    Returns:
        ``(first line, line after the last, note)`` for every ``Migration:``
        label or heading outside fenced code.  A label with text covers one
        paragraph; a heading covers everything up to the next heading or
        trailer.
    """
    code = _fence_mask(lines)
    blocks: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        label = None if code[index] else _MIGRATION_LABEL_RE.match(lines[index])
        if code[index] or not (label or _MIGRATION_HEADING_RE.match(lines[index])):
            index += 1
            continue
        inline = label["inline"].strip() if label else ""
        collected = [inline] if inline else []
        end = index + 1
        while end < len(lines):
            follower = lines[end]
            if not code[end] and (
                _MD_HEADING_RE.match(follower)
                or _BOLD_HEADING_RE.match(follower)
                or _TRAILER_RE.match(follower)
                or (inline and not follower.strip())
            ):
                break
            collected.append(follower.rstrip())
            end += 1
        blocks.append((index, end, "\n".join(collected).strip()))
        index = end
    return blocks


def extract_migration_section(text: str) -> str | None:
    """Find an explicit migration note in a commit message or PR description.
    Recognised forms are a label (``Migration: pass timeout= instead``,
    ``**Upgrade notes:** ...``), which covers one paragraph, and a heading
    (``## Migration``, ``**How to upgrade**``), which covers everything up to
    the next heading or trailer.  Fenced code is never mistaken for either.
    Args:
        text: Commit message body or pull-request description.
    Returns:
        The note, or ``None`` when there is none (or it only says "none").
    """
    lines = _HTML_COMMENT_RE.sub("", text).splitlines()
    for _, _, note in _migration_blocks(lines):
        if note.lower().strip(" \t_*-.") not in _NO_NOTE:
            return note
    return None


def _note_from_text(change: Change, text: str, source: str) -> bool:
    """Take the migration note of *change* from free text, if it has one.
    Args:
        change: A breaking change still lacking a migration note.
        text: Commit message body or pull-request description.
        source: Where *text* comes from, for attribution.
    Returns:
        True when a note was found and stored on *change*.
    """
    note = extract_migration_section(text)
    if note is None:
        note = next((n for n in _breaking_notes(text) if has_instruction(n)), None)
        source = f"BREAKING CHANGE footer ({source})" if note else source
    if note is None:
        return False
    change.migration = note
    change.migration_source = source
    return True


def _guide_pathspecs(names: Sequence[str]) -> list[str]:
    """Build pathspecs matching migration guides in any directory and case.
    Args:
        names: File names such as ``MIGRATION.md``.
    Returns:
        One pathspec per name.
    """
    return [f":(top,icase,glob)**/{name}" for name in names]


def migration_guide_commits(repo: Path, rev_range: RevRange, names: Sequence[str]) -> set[str]:
    """Find the commits of a range that change a migration guide.
    Args:
        repo: Directory inside the repository.
        rev_range: The revisions to search.
        names: File names that count as migration guides.
    Returns:
        The commit ids; empty when there is no guide.
    """
    if not names:
        return set()
    spec = _range_spec(repo, rev_range)
    proc = run_git(
        repo,
        [
            "log",
            "--no-merges",
            "--full-history",
            "--format=%H",
            spec,
            "--",
            *_guide_pathspecs(names),
        ],
        check=False,
    )
    return set(proc.stdout.split()) if proc.returncode == 0 else set()


def migration_file_entry(repo: Path, sha: str, names: Sequence[str]) -> str | None:
    """Return what a commit added to the project's migration guide.
    Args:
        repo: Directory inside the repository.
        sha: The commit to inspect.
        names: File names that count as migration guides, in any directory
            and any letter case.
    Returns:
        The added text without headings, or ``None`` when the commit added
        nothing to a guide.
    """
    if not names:
        return None
    proc = run_git(
        repo,
        [
            *(
                "show",
                "--no-color",
                "--no-ext-diff",
                "--format=",
                "--unified=0",
                sha,
                "--",
            ),
            *_guide_pathspecs(names),
        ],
        check=False,
    )
    if proc.returncode != 0:
        return None
    added = [
        line[1:].rstrip() for line in proc.stdout.splitlines() if line.startswith("+") and not line.startswith("+++")
    ]
    note = "\n".join(line for line in added if not _MD_HEADING_RE.match(line)).strip()
    return note or None


@dataclass(frozen=True)
class PullRequest:
    """The parts of a pull request the notes can use.
    Attributes:
        number: Pull-request number.
        title: Its title.
        body: Its description (Markdown).
        url: Its web address.
    """

    number: int
    title: str
    body: str
    url: str = ""


class PullRequestFetcher:
    """Reads pull-request descriptions through the GitHub CLI, if possible.
    Squash merges leave one commit whose subject is the PR title; the detail
    users need lives in the PR body.  Fetching is strictly optional: without
    ``gh``, without credentials or without a network every lookup returns
    nothing, and the first such failure switches further lookups off.  A
    number that simply is not a pull request is skipped without that effect.
    """

    def __init__(
        self,
        repo: Path,
        *,
        enabled: bool = True,
        limit: int = DEFAULT_PR_LOOKUPS,
        timeout: float = 15.0,
        workers: int = 4,
    ) -> None:
        """Prepare a fetcher.
        Args:
            repo: Directory inside the repository (``gh`` infers the project).
            enabled: False to never run ``gh`` (``--offline``).
            limit: Maximum number of pull requests to fetch.
            timeout: Seconds to wait for one ``gh`` call.
            workers: Number of ``gh`` calls running at the same time.
        """
        self._repo = repo
        self._limit = limit
        self._timeout = timeout
        self._workers = max(workers, 1)
        self._cache: dict[int, PullRequest | None] = {}
        self.lookups = 0
        self.status = ""
        if not enabled or limit <= 0:
            self.status = "disabled (--offline)"
        elif shutil.which("gh") is None:
            self.status = "unavailable (gh is not installed)"
        if self.status:
            logger.info("pull-request lookups {}", self.status)

    @property
    def available(self) -> bool:
        """True while lookups can still succeed."""
        return not self.status and self.lookups < self._limit

    def _view(self, number: int) -> tuple[PullRequest | None, str]:
        """Run ``gh pr view`` once.
        Args:
            number: Pull-request number.
        Returns:
            ``(pull request, error)``.  Both are empty when *number* is not
            a pull request; *error* is set when ``gh`` cannot work here.
        """
        command = ["gh", "pr", "view", str(number), "--json", "number,title,body,url"]
        logger.debug("$ {}", " ".join(command))
        try:
            proc = subprocess.run(
                command,
                cwd=self._repo,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"},
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, f"gh did not answer within {self._timeout:g}s"
        except OSError as exc:
            return None, str(exc)
        if proc.returncode != 0:
            if _PR_MISSING_RE.search(proc.stderr):
                return None, ""
            lines = proc.stderr.strip().splitlines()
            return None, lines[0] if lines else f"gh exited with status {proc.returncode}"
        try:
            data = json.loads(proc.stdout)
            pull = PullRequest(
                number=int(data.get("number", number)),
                title=str(data.get("title") or ""),
                body=str(data.get("body") or ""),
                url=str(data.get("url") or ""),
            )
        except (ValueError, AttributeError, TypeError) as exc:
            return None, f"unexpected output from gh: {exc}"
        return pull, ""

    def _store(self, number: int, pull: PullRequest | None, error: str) -> None:
        """Record the outcome of one lookup.
        Args:
            number: Pull-request number.
            pull: The pull request, if it was found.
            error: Why ``gh`` cannot work here, or ``""``.
        """
        self.lookups += 1
        self._cache[number] = pull
        if error and not self.status:
            # Offline-safe: gh is not usable here, so stop asking.
            self.status = f"unavailable ({error})"
            logger.warning("pull-request lookups {}; continuing without them", self.status)
        elif pull is None and not error:
            logger.debug("#{} is not a pull request", number)

    def fetch_many(self, numbers: Sequence[int]) -> dict[int, PullRequest]:
        """Fetch several pull requests.
        The first lookup runs alone; when it shows that ``gh`` works, the
        others run concurrently.
        Args:
            numbers: Pull-request numbers, most important first (the lookup
                limit cuts from the end).
        Returns:
            The pull requests that could be fetched, by number.
        """
        wanted = [number for number in dict.fromkeys(numbers) if number not in self._cache]
        wanted = wanted[: max(self._limit - self.lookups, 0)]
        if wanted and not self.status:
            self._store(wanted[0], *self._view(wanted[0]))
        if len(wanted) > 1 and not self.status:
            with ThreadPoolExecutor(max_workers=self._workers) as pool:
                for number, outcome in zip(wanted[1:], pool.map(self._view, wanted[1:]), strict=True):
                    self._store(number, *outcome)
        return {n: pull for n in numbers if (pull := self._cache.get(n)) is not None}

    def fetch(self, number: int) -> PullRequest | None:
        """Fetch one pull request.
        Args:
            number: Pull-request number.
        Returns:
            The pull request, or ``None`` when it cannot be fetched.
        """
        return self.fetch_many([number]).get(number)


def summarize_description(body: str, *, limit: int = 280) -> str | None:
    """Condense a pull-request description to its first paragraph of prose.
    Headings, lists (check lists included), tables, quotes, code, HTML,
    trailers such as ``Fixes #12`` and the migration section - which is shown
    on its own - are passed over.
    Args:
        body: The description in Markdown.
        limit: Maximum length of the summary.
    Returns:
        One line of text, or ``None`` when the description has no prose.
    """
    lines = _HTML_COMMENT_RE.sub("", body).splitlines()
    hidden = _fence_mask(lines)
    for start, end, _ in _migration_blocks(lines):
        hidden[start:end] = [True] * (end - start)
    paragraph: list[str] = []
    in_block = False  # inside a list item, quote or table, which runs to the next blank line
    for line, skip in zip(lines, hidden, strict=True):
        text = line.strip()
        heading = _MD_HEADING_RE.match(line) or _BOLD_HEADING_RE.match(line)
        structure = not heading and (_MD_BLOCK_RE.match(line) or _MD_RULE_RE.match(text))
        if skip or not text or heading or structure or _TRAILER_RE.match(text):
            if paragraph:
                break
            in_block = bool(structure) and not skip
        elif not in_block and (paragraph or not line.startswith(("    ", "\t"))):
            paragraph.append(text)
    if not paragraph:
        return None
    summary = " ".join(paragraph)
    return summary if len(summary) <= limit else summary[: limit - 1].rstrip() + "…"


def _documents(commit: Change, target: Change) -> bool:
    """Tell whether a commit says that it documents another change.
    Args:
        commit: A later commit that adds to the migration guide.
        target: The breaking change that needs a note.
    Returns:
        True when the message of *commit* mentions the pull request of
        *target* (``#123``) or its commit id.
    """
    if target.pr is not None and target.pr in commit.refs:
        return True
    message = f"{commit.commit.subject}\n{commit.commit.body}"
    return any(target.commit.sha.startswith(token) for token in _SHA_TOKEN_RE.findall(message))


def enrich_changes(
    repo: Path,
    rev_range: RevRange,
    changes: Sequence[Change],
    fetcher: PullRequestFetcher | None,
    migration_files: Sequence[str] = DEFAULT_MIGRATION_FILES,
) -> None:
    """Complete changes with migration guides and pull-request descriptions.
    Offline sources are used first: what the breaking commit itself adds to
    the migration guide, or what a later commit adds while naming it
    (``docs: migration note for #123``) - the way to document a change that
    is already merged.  Pull requests are consulted for the most important
    changes first, because lookups are limited; a description that declares
    a ``BREAKING CHANGE:`` promotes its commit to a breaking change.
    Args:
        repo: Directory inside the repository.
        rev_range: The revisions the changes come from.
        changes: Parsed changes; modified in place.
        fetcher: Source of pull-request descriptions, or ``None``.
        migration_files: File names that count as migration guides.
    """
    guides = migration_guide_commits(repo, rev_range, migration_files)
    entries: dict[str, str | None] = {}
    followups = [c for c in changes if c.commit.sha in guides and not c.cancelled and not c.breaking]

    def entry(sha: str) -> str | None:
        if sha not in entries:
            entries[sha] = migration_file_entry(repo, sha, migration_files)
        return entries[sha]

    def from_guide(change: Change) -> None:
        if not change.breaking or change.migration or change.cancelled:
            return
        if change.commit.sha in guides and (note := entry(change.commit.sha)):
            change.migration = note
            change.migration_source = "migration guide"
            return
        for other in followups:
            if _documents(other, change) and (note := entry(other.commit.sha)):
                change.migration = note
                change.migration_source = f"migration guide ({other.commit.short})"
                return

    for change in changes:
        from_guide(change)
    if fetcher is None or not fetcher.available:
        return
    background = (Category.DEPENDENCY, Category.INTERNAL)
    candidates = [
        change
        for change in sorted(changes, key=lambda c: CATEGORY_RANK[c.category])
        if change.pr is not None and not change.cancelled and change.category not in background
    ]
    pulls = fetcher.fetch_many([c.pr for c in candidates if c.pr is not None])
    for change in candidates:
        pull = pulls.get(change.pr) if change.pr is not None else None
        if pull is None:
            continue
        notes = _breaking_notes(pull.body)
        if notes and not change.breaking:
            logger.info("{} is breaking according to PR #{}", change.commit.short, pull.number)
            change.breaking = True
            change.category = Category.BREAKING
        change.breaking_notes.extend(note for note in notes if note not in change.breaking_notes)
        if change.breaking and not change.migration:
            _note_from_text(change, pull.body, f"PR #{pull.number}")
            from_guide(change)
        summary = summarize_description(pull.body)
        if summary and summary.lower().rstrip(".") != change.description.lower().rstrip("."):
            change.detail = summary


# ---------------------------------------------------------------------------
# Public API diff (ast on `git archive` trees)
# ---------------------------------------------------------------------------
_NON_API_TOP_DIRS = frozenset({
    "docs",
    "doc",
    "examples",
    "example",
    "scripts",
    "benchmarks",
    "tools",
    "build",
    "dist",
})
_NON_API_DIRS = frozenset({"tests", "test"})
_NON_API_MODULES = frozenset({"setup", "conftest", "noxfile", "fabfile"})
_MOVABLE_KINDS = frozenset({"function", "class", "constant"})
_OPAQUE_KINDS = frozenset({"import", "name"})


@dataclass(frozen=True)
class Param:
    """One parameter of a callable.
    Attributes:
        name: Parameter name.
        kind: ``positional-only``, ``positional``, ``var-positional``,
            ``keyword-only`` or ``var-keyword``.
        required: True when the parameter has no default.
        default: Source text of the default (display only, not compared).
    """

    name: str
    kind: str
    required: bool
    default: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Symbol:
    """A name of the Python API.
    Attributes:
        qualname: The dotted path users write, e.g. ``pkg.Client.get``.
        kind: ``function``, ``class``, ``method``, ``attribute``, ``constant``,
            ``import`` (a re-export of something outside the project) or
            ``name`` (listed in ``__all__`` but created dynamically).
        module: The module the path goes through.
        parent: Path of the owning class, for members.
        params: Parameters of callables (the constructor for classes), else
            ``None``.
        fingerprint: Hash of the implementation, used to recognise renames;
            empty for trivial bodies.
        origin: Qualified name of the definition the path leads to.  Paths
            with the same origin are aliases of one object, e.g.
            ``pkg.connect`` re-exporting ``pkg._client.connect``.
        target: For imports that are not resolved yet, the absolute dotted
            name that is imported.
    """

    qualname: str
    kind: str
    module: str
    parent: str | None = None
    params: tuple[Param, ...] | None = None
    fingerprint: str = ""
    origin: str = ""
    target: str | None = None

    @property
    def name(self) -> str:
        """The last component of the path."""
        return self.qualname.rsplit(".", 1)[-1]

    @property
    def definition(self) -> str:
        """Qualified name of the object behind the path."""
        return self.origin or self.qualname

    @property
    def signature(self) -> str:
        """The parameter list as source text, or ``""`` for non-callables."""
        return format_signature(self.params) if self.params is not None else ""


@dataclass
class ApiSnapshot:
    """The public API of one revision.
    Attributes:
        symbols: Public symbols by the path users write.
        modules: Dotted names of the public modules.
        skipped: Files that could not be parsed.
    """

    symbols: dict[str, Symbol] = field(default_factory=dict)
    modules: set[str] = field(default_factory=set)
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ApiChange:
    """One difference between two API snapshots.
    Attributes:
        change: ``removed``, ``renamed``, ``changed`` or ``added``.
        kind: Kind of the symbol, or ``module``.
        name: Path in the old revision (in the new one for ``added``).
        new_name: The replacement: the new path of a renamed symbol, or the
            path under which a removed symbol is still available.
        old_signature: Old parameter list, when callable.
        new_signature: New parameter list, when callable.
        detail: What exactly changed, in words.
        breaking: True when existing callers can break.
        aliases: Other paths of the same object that changed the same way.
    """

    change: str
    kind: str
    name: str
    new_name: str | None = None
    old_signature: str = ""
    new_signature: str = ""
    detail: str = ""
    breaking: bool = True
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable description of the difference.
        Returns:
            A dict with all attributes.
        """
        return {
            "change": self.change,
            "kind": self.kind,
            "name": self.name,
            "new_name": self.new_name,
            "old_signature": self.old_signature,
            "new_signature": self.new_signature,
            "detail": self.detail,
            "breaking": self.breaking,
            "aliases": list(self.aliases),
        }


@dataclass
class _ClassScan:
    """What a class definition declares itself.
    Attributes:
        members: Public methods and attributes by name.
        bases: Base classes as written (dotted names).
        init: Signature of the constructor, when the class defines one.
    """

    members: dict[str, Symbol] = field(default_factory=dict)
    bases: list[str] = field(default_factory=list)
    init: tuple[Param, ...] | None = None


@dataclass
class _ModuleScan:
    """Everything one module binds at top level, before imports are followed.
    Attributes:
        name: Dotted name of the module.
        is_package: True for ``__init__.py`` files.
        names: Every top-level binding by local name (definitions and imports).
        classes: Details of the classes defined here, by local name.
        exported: The literal ``__all__``, when there is one.
        stars: ``from X import *`` statements: the absolute module and whether
            the import re-exports by its position.
        reexports: Imported names that are public without ``__all__``.
    """

    name: str
    is_package: bool
    names: dict[str, Symbol] = field(default_factory=dict)
    classes: dict[str, _ClassScan] = field(default_factory=dict)
    exported: set[str] | None = None
    stars: list[tuple[str, bool]] = field(default_factory=list)
    reexports: set[str] = field(default_factory=set)


def format_signature(params: Sequence[Param]) -> str:
    """Render parameters the way they are written in a ``def``.
    Args:
        params: The parameters, in order.
    Returns:
        E.g. ``(host, port=5432, *, timeout=None)``.
    """
    parts: list[str] = []
    star_written = False
    for index, param in enumerate(params):
        text = param.name if param.default is None else f"{param.name}={param.default}"
        if param.kind == "var-positional":
            text = f"*{param.name}"
            star_written = True
        elif param.kind == "var-keyword":
            text = f"**{param.name}"
        elif param.kind == "keyword-only" and not star_written:
            parts.append("*")
            star_written = True
        parts.append(text)
        last_positional_only = param.kind == "positional-only" and (
            index + 1 == len(params) or params[index + 1].kind != "positional-only"
        )
        if last_positional_only:
            parts.append("/")
    return f"({', '.join(parts)})"


def _default_text(node: ast.expr | None) -> str | None:
    """Render a default value for display.
    Args:
        node: The default expression, or ``None`` when there is none.
    Returns:
        The source text, shortened to ``...`` when long; ``None`` without default.
    """
    if node is None:
        return None
    text = ast.unparse(node)
    return text if len(text) <= 24 else "..."


def _params(args: ast.arguments, *, drop_first: bool) -> tuple[Param, ...]:
    """Convert ``ast`` arguments to :class:`Param` objects.
    Args:
        args: Arguments of a function definition.
        drop_first: Leave out the first positional parameter (``self``/``cls``).
    Returns:
        The parameters in declaration order.
    """
    positional = [*args.posonlyargs, *args.args]
    first_default = len(positional) - len(args.defaults)
    params: list[Param] = []
    for index, arg in enumerate(positional):
        kind = "positional-only" if index < len(args.posonlyargs) else "positional"
        default = args.defaults[index - first_default] if index >= first_default else None
        params.append(Param(arg.arg, kind, default is None, _default_text(default)))
    if drop_first and params:
        params = params[1:]
    if args.vararg:
        params.append(Param(args.vararg.arg, "var-positional", required=False))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        params.append(Param(arg.arg, "keyword-only", default is None, _default_text(default)))
    if args.kwarg:
        params.append(Param(args.kwarg.arg, "var-keyword", required=False))
    return tuple(params)


def _without_docstring(body: Sequence[ast.stmt]) -> Sequence[ast.stmt]:
    """Drop a leading docstring from a body.
    Args:
        body: Statements of a function or class.
    Returns:
        The statements without the docstring expression.
    """
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        return body[1:] if isinstance(body[0].value.value, str) else body
    return body


def _fingerprint(nodes: Iterable[ast.AST]) -> str:
    """Hash an implementation so that a renamed copy can be recognised.
    Args:
        nodes: The nodes that make up the implementation.
    Returns:
        A hex digest, or ``""`` for bodies too small to be distinctive.
    """
    dump = "".join(ast.dump(node) for node in nodes)
    if len(dump) < 120:
        return ""
    return hashlib.sha1(dump.encode("utf-8"), usedforsecurity=False).hexdigest()


def _dotted_name(node: ast.expr) -> str:
    """Render a name expression as written.
    Args:
        node: A ``Name``, ``Attribute``, ``Subscript`` or ``Call`` expression.
    Returns:
        E.g. ``dataclasses.dataclass`` for ``@dataclasses.dataclass(frozen=True)``
        and ``Generic`` for ``Generic[T]``; ``""`` for anything else.
    """
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    if isinstance(node, ast.Subscript):
        return _dotted_name(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def _decorator_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> set[str]:
    """Return the trailing names of the decorators of *node*.
    Args:
        node: A function or class definition.
    Returns:
        E.g. ``{"property"}``, ``{"staticmethod"}`` or ``{"dataclass"}``.
    """
    names = {_dotted_name(decorator).rsplit(".", 1)[-1] for decorator in node.decorator_list}
    return names - {""}


def _statements(body: Sequence[ast.stmt]) -> Iterator[ast.stmt]:
    """Iterate module-level statements, looking inside ``if`` and ``try`` blocks.
    Args:
        body: Statements of a module.
    Yields:
        Every statement that defines names at module level.
    """
    for node in body:
        if isinstance(node, ast.If):
            yield from _statements([*node.body, *node.orelse])
        elif isinstance(node, ast.Try):
            handlers = [stmt for handler in node.handlers for stmt in handler.body]
            yield from _statements([*node.body, *handlers, *node.orelse, *node.finalbody])
        else:
            yield node


def _assigned_names(node: ast.stmt) -> list[str]:
    """Return the plain names an assignment statement binds.
    Args:
        node: Any statement.
    Returns:
        Target names of ``a = ...``, ``a, b = ...`` and ``a: T = ...``.
    """
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    names: list[str] = []
    for target in targets:
        elements = target.elts if isinstance(target, ast.Tuple | ast.List) else [target]
        names.extend(element.id for element in elements if isinstance(element, ast.Name))
    return names


def _declared_all(tree: ast.Module) -> set[str] | None:
    """Read a literal ``__all__`` from a module.
    Args:
        tree: The parsed module.
    Returns:
        The exported names, or ``None`` when ``__all__`` is absent or dynamic.
    """
    exported: set[str] = set()
    found = False
    for node in _statements(tree.body):
        value: ast.expr | None = None
        if isinstance(node, ast.Assign | ast.AnnAssign) and "__all__" in _assigned_names(node):
            value = node.value
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            value = node.value if node.target.id == "__all__" else None
        if value is None:
            continue
        found = True
        for sequence in ast.walk(value):
            if isinstance(sequence, ast.List | ast.Tuple | ast.Set):
                exported.update(
                    element.value
                    for element in sequence.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                )
    return exported if found and exported else None


def _keyword_flag(call: ast.expr | None, name: str) -> bool | None:
    """Read a literal boolean keyword argument of a call.
    Args:
        call: The expression to inspect (anything but a call yields ``None``).
        name: The keyword to look for.
    Returns:
        The literal value, or ``None`` when absent or not a literal boolean.
    """
    if not isinstance(call, ast.Call):
        return None
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value if isinstance(keyword.value.value, bool) else None
    return None


def _dataclass_params(node: ast.ClassDef) -> tuple[Param, ...] | None:
    """Derive the constructor that ``@dataclass`` generates.
    Args:
        node: A class definition.
    Returns:
        The parameters of the generated ``__init__``, or ``None`` when the
        class is not a dataclass or its fields cannot all be seen (it has
        base classes, or ``init=False``).
    """
    decorator = next(
        (d for d in node.decorator_list if _dotted_name(d).rsplit(".", 1)[-1] == "dataclass"),
        None,
    )
    if decorator is None or node.bases or _keyword_flag(decorator, "init") is False:
        return None
    keyword_only = _keyword_flag(decorator, "kw_only") is True
    params: list[Param] = []
    for item in node.body:
        if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
            continue
        annotation = ast.unparse(item.annotation)
        if "ClassVar" in annotation:
            continue
        if annotation.endswith("KW_ONLY"):
            keyword_only = True
            continue
        value = item.value
        required = value is None
        default = _default_text(value)
        field_keyword_only = keyword_only
        if isinstance(value, ast.Call) and _dotted_name(value).rsplit(".", 1)[-1] == "field":
            if _keyword_flag(value, "init") is False:
                continue
            options = {keyword.arg: keyword.value for keyword in value.keywords if keyword.arg}
            required = "default" not in options and "default_factory" not in options
            default = None if required else _default_text(options.get("default")) or "..."
            flag = _keyword_flag(value, "kw_only")
            field_keyword_only = keyword_only if flag is None else flag
        kind = "keyword-only" if field_keyword_only else "positional"
        params.append(Param(item.target.id, kind, required, default))
    # The generated __init__ takes keyword-only fields after all the others.
    params.sort(key=lambda param: param.kind == "keyword-only")
    return tuple(params)


def _scan_class(node: ast.ClassDef, qualname: str, module: str) -> _ClassScan:
    """Collect what a class definition declares itself.
    Args:
        node: The class definition.
        qualname: Qualified name of the class.
        module: Dotted name of the defining module.
    Returns:
        Public methods (with signatures), attributes, base classes and the
        constructor signature.
    """
    scan = _ClassScan(bases=[name for base in node.bases if (name := _dotted_name(base))])
    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            decorators = _decorator_names(item)
            if item.name == "__init__":
                scan.init = _params(item.args, drop_first=True)
            if item.name.startswith("_") or decorators & {
                "setter",
                "deleter",
                "getter",
            }:
                continue
            path = f"{qualname}.{item.name}"
            if decorators & {"property", "cached_property"}:
                scan.members[item.name] = Symbol(path, "attribute", module, qualname)
            else:
                scan.members[item.name] = Symbol(
                    path,
                    "method",
                    module,
                    qualname,
                    _params(item.args, drop_first="staticmethod" not in decorators),
                    _fingerprint([item.args, *_without_docstring(item.body)]),
                )
        else:
            for name in _assigned_names(item):
                if not name.startswith("_"):
                    scan.members[name] = Symbol(f"{qualname}.{name}", "attribute", module, qualname)
    if scan.init is None:
        scan.init = _dataclass_params(node)
    return scan


def _import_source(package: str, level: int, module: str | None) -> str | None:
    """Work out the absolute module a ``from ... import`` statement reads.
    Args:
        package: The package containing the importing module.
        level: Number of leading dots.
        module: The module written after the dots, if any.
    Returns:
        The absolute dotted name, or ``None`` when the dots lead above the
        top-level package.
    """
    if level == 0:
        return module
    parts = package.split(".") if package else []
    if level - 1 > len(parts):
        return None
    base = parts[: len(parts) - (level - 1)]
    return ".".join([*base, *([module] if module else [])]) or None


def _scan_module(module: str, source: str | bytes, *, is_package: bool) -> _ModuleScan:
    """Parse one module and record every name it binds at top level.
    Args:
        module: Dotted name of the module.
        source: Its source code; bytes are decoded the way Python does it
            (BOM, ``# -*- coding: ... -*-``).
        is_package: True for ``__init__.py`` files.
    Returns:
        The bindings; imports are recorded but not followed yet.
    Raises:
        SyntaxError: If *source* is not valid Python for this interpreter.
    """
    tree = ast.parse(source)
    scan = _ModuleScan(module, is_package, exported=_declared_all(tree))
    package = module if is_package else module.rpartition(".")[0]
    top_level = module.split(".")[0]
    for node in _statements(tree.body):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            scan.names[node.name] = Symbol(
                f"{module}.{node.name}",
                "function",
                module,
                params=_params(node.args, drop_first=False),
                fingerprint=_fingerprint([node.args, *_without_docstring(node.body)]),
            )
        elif isinstance(node, ast.ClassDef):
            qualname = f"{module}.{node.name}"
            details = _scan_class(node, qualname, module)
            scan.classes[node.name] = details
            scan.names[node.name] = Symbol(
                qualname,
                "class",
                module,
                params=details.init,
                fingerprint=_fingerprint(_without_docstring(node.body)),
            )
        elif isinstance(node, ast.ImportFrom):
            origin = _import_source(package, node.level, node.module)
            if origin is None:
                continue
            # In a package's __init__, importing from the project itself is
            # how names are published.
            publishes = is_package and (node.level > 0 or origin.split(".")[0] == top_level)
            for alias in node.names:
                if alias.name == "*":
                    scan.stars.append((origin, publishes))
                    continue
                local = alias.asname or alias.name
                scan.names[local] = Symbol(
                    f"{module}.{local}",
                    "import",
                    module,
                    target=f"{origin}.{alias.name}",
                )
                if publishes:
                    scan.reexports.add(local)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                target = alias.name if alias.asname else local
                scan.names[local] = Symbol(f"{module}.{local}", "import", module, target=target)
        else:
            for name in _assigned_names(node):
                if name != "__all__":
                    scan.names[name] = Symbol(f"{module}.{name}", "constant", module)
    return scan


def _public_names(scan: _ModuleScan) -> set[str]:
    """Decide which names of a module are public.
    Args:
        scan: The module.
    Returns:
        The names in a literal ``__all__`` when there is one; otherwise
        every name without a leading underscore that is defined in the
        module or re-exported by a package ``__init__``.
    """
    if scan.exported is not None:
        return set(scan.exported)
    return {
        name
        for name, symbol in scan.names.items()
        if not name.startswith("_") and (symbol.kind != "import" or name in scan.reexports)
    }


def _expand_star_imports(scans: dict[str, _ModuleScan]) -> None:
    """Turn ``from X import *`` into the individual imports it performs.
    Args:
        scans: All modules of the project by name; modified in place.
    """
    done: set[str] = set()

    def expand(name: str, stack: tuple[str, ...]) -> None:
        if name in done or name in stack:
            return
        scan = scans[name]
        for source, publishes in scan.stars:
            if source not in scans:
                continue
            expand(source, (*stack, name))
            for local in sorted(_public_names(scans[source])):
                if local in scan.names or local not in scans[source].names:
                    continue
                scan.names[local] = Symbol(f"{name}.{local}", "import", name, target=f"{source}.{local}")
                if publishes:
                    scan.reexports.add(local)
        done.add(name)

    for name in list(scans):
        expand(name, ())


def _resolve(scans: dict[str, _ModuleScan], symbol: Symbol) -> Symbol | None:
    """Follow imports to the definition a name stands for.
    Args:
        scans: All modules of the project by name.
        symbol: A binding of one of the modules.
    Returns:
        The definition; a symbol of kind ``module`` when the name is a
        module of the project; ``None`` when the trail leaves the project.
    """
    seen: set[str] = set()
    while symbol.kind == "import":
        target = symbol.target
        if not target or target in seen:
            return None
        seen.add(target)
        module, _, local = target.rpartition(".")
        scan = scans.get(module)
        if scan is not None and local in scan.names:
            symbol = scan.names[local]
        elif target in scans:
            return Symbol(target, "module", target)
        else:
            return None
    return symbol


def _lookup(scans: dict[str, _ModuleScan], module: str, dotted: str) -> Symbol | None:
    """Resolve a name the way it is written inside a module.
    Args:
        scans: All modules of the project by name.
        module: The module in which the name is written.
        dotted: The name, e.g. ``Base`` or ``base.Base``.
    Returns:
        The definition, or ``None`` when it is not part of the project.
    """
    first, _, rest = dotted.partition(".")
    scan = scans.get(module)
    bound = scan.names.get(first) if scan else None
    found = _resolve(scans, bound) if bound else None
    if found is None or not rest:
        return found
    return _lookup(scans, found.qualname, rest) if found.kind == "module" else None


def _class_api(
    scans: dict[str, _ModuleScan],
    module: str,
    name: str,
    seen: frozenset[str] = frozenset(),
) -> tuple[dict[str, Symbol], tuple[Param, ...] | None]:
    """Collect the members and constructor of a class, inherited ones included.
    Only base classes defined in the project can be followed; what a class
    inherits from elsewhere is not part of this project's API diff.
    Args:
        scans: All modules of the project by name.
        module: Module defining the class.
        name: Name of the class in that module.
        seen: Classes already visited (guards against cycles).
    Returns:
        ``(members by name, constructor parameters or None)``.
    """
    details = scans[module].classes.get(name)
    if details is None:
        return {}, None
    members = dict(details.members)
    init = details.init
    for base in details.bases:
        found = _lookup(scans, module, base)
        if found is None or found.kind != "class" or found.qualname in seen:
            continue
        inherited, inherited_init = _class_api(scans, found.module, found.name, seen | {f"{module}.{name}"})
        for member_name, member in inherited.items():
            members.setdefault(member_name, member)
        if init is None:
            init = inherited_init
    return members, init


def is_public_module(module: str) -> bool:
    """Tell whether a module is meant to be imported by users.
    Args:
        module: Dotted module name.
    Returns:
        False when any component starts with an underscore.
    """
    return not any(part.startswith("_") for part in module.split("."))


def _public_api(scans: dict[str, _ModuleScan]) -> ApiSnapshot:
    """Build the public API from scanned modules.
    Re-exports are followed to their definitions, so ``pkg.connect`` carries
    the signature of ``pkg._client.connect``; classes get the members they
    inherit from project classes.
    Args:
        scans: All modules of the project by name (private ones included).
    Returns:
        The snapshot of public modules and symbols.
    """
    snapshot = ApiSnapshot()
    for scan in scans.values():
        if not is_public_module(scan.name):
            continue
        snapshot.modules.add(scan.name)
        for local in _public_names(scan):
            path = f"{scan.name}.{local}"
            bound = scan.names.get(local)
            if bound is None:
                if path not in scans:  # submodules are covered by `modules`
                    snapshot.symbols[path] = Symbol(path, "name", scan.name)
                continue
            found = _resolve(scans, bound)
            if found is None:
                snapshot.symbols[path] = Symbol(path, "import", scan.name)
                continue
            if found.kind == "module":
                continue
            members: dict[str, Symbol] = {}
            params = found.params
            if found.kind == "class":
                members, params = _class_api(scans, found.module, found.name)
            snapshot.symbols[path] = dataclasses.replace(
                found,
                qualname=path,
                module=scan.name,
                params=params,
                origin=found.qualname,
            )
            for member_name, member in members.items():
                snapshot.symbols[f"{path}.{member_name}"] = dataclasses.replace(
                    member,
                    qualname=f"{path}.{member_name}",
                    module=scan.name,
                    parent=path,
                    origin=member.qualname,
                )
    return snapshot


def extract_api(module: str, source: str, *, is_package: bool = False) -> dict[str, Symbol]:
    """Extract the public API of one module, looked at in isolation.
    Public means: listed in a literal ``__all__`` when there is one,
    otherwise not starting with an underscore.  Imports count only as
    re-exports - project imports in a package's ``__init__`` or names listed
    in ``__all__`` - and stay opaque here because the other modules are not
    available (:func:`snapshot_api` follows them).
    Args:
        module: Dotted name of the module.
        source: Its source code.
        is_package: True for ``__init__.py`` files.
    Returns:
        Public symbols by qualified name (class members included).
    Raises:
        SyntaxError: If *source* is not valid Python for this interpreter.
    """
    scan = _scan_module(module, source, is_package=is_package)
    return _public_api({module: scan}).symbols


def module_name(path: str) -> str | None:
    """Map a file path to the dotted name of the module it defines.
    Args:
        path: POSIX path of a ``.py`` file, relative to the import root.
    Returns:
        The module name, or ``None`` for tests, documentation, scripts and
        names that cannot be imported.  Private modules are named too: they
        are not part of the API, but public names are often defined there.
    """
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    if parts[0] in _NON_API_TOP_DIRS or any(part in _NON_API_DIRS for part in parts):
        return None
    last = parts[-1]
    if last in _NON_API_MODULES or last.startswith("test_") or last.endswith("_test"):
        return None
    return ".".join(parts)


def read_python_sources(repo: Path, ref: str) -> Iterator[tuple[str, bytes]]:
    """Stream the Python files of a revision out of ``git archive``.
    Nothing is extracted to disk and files marked ``export-ignore`` are left
    out, exactly as they would be from a release tarball.
    Args:
        repo: Directory inside the repository.
        ref: The revision to read.
    Yields:
        ``(path, content)`` for every ``.py`` file.
    Raises:
        GitError: If the revision is unknown or ``git archive`` fails.
    """
    repo = repository_root(repo)  # from a subdirectory, git archive packs only that directory
    command = [
        "git",
        "archive",
        "--format=tar",
        resolve_commit(repo, ref),
        "--",
        "*.py",
    ]
    logger.debug("$ {}", " ".join(command))
    with tempfile.TemporaryFile() as errors:
        try:
            proc = subprocess.Popen(command, cwd=repo, stdout=subprocess.PIPE, stderr=errors, env=_git_env())
        except OSError as exc:
            raise GitError(f"cannot run git in {repo}: {exc}") from exc
        assert proc.stdout is not None  # stdout=PIPE above
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
                for member in archive:
                    if member.isfile() and member.name.endswith(".py"):
                        handle = archive.extractfile(member)
                        if handle is not None:
                            yield member.name, handle.read()
        except tarfile.TarError as exc:
            logger.debug("git archive {} produced no usable tar stream: {}", ref, exc)
        finally:
            proc.stdout.close()
            status = proc.wait()
        if status != 0:
            errors.seek(0)
            message = errors.read().decode("utf-8", "replace").strip()
            if "did not match any files" not in message:  # a revision without Python files
                raise GitError(f"git archive {ref} failed: {message}")


def _import_root(path: str, files: set[str]) -> str:
    """Find the directory from which the files below *path* are imported.
    Args:
        path: A repository path given with ``--api-path``.
        files: All Python files of the revision.
    Returns:
        *path* itself, or - when it is a package - the directory that
        contains the outermost package.
    """
    root = path
    while root and f"{root}/__init__.py" in files:
        root = root.rpartition("/")[0]
    return root


def snapshot_api(repo: Path, ref: str, paths: Sequence[str] = ()) -> ApiSnapshot:
    """Collect the public Python API of a revision.
    Args:
        repo: Directory inside the repository.
        ref: The revision to inspect.
        paths: Only consider files below these repository paths; module
            names are then relative to them (``--api-path packages/core/src``
            makes ``packages/core/src/core/io.py`` the module ``core.io``).
    Returns:
        The snapshot; files that do not parse are listed in ``skipped``.
    Raises:
        GitError: If the revision cannot be archived.
    """
    sources = dict(read_python_sources(repo, ref))
    files = set(sources)
    prefixes = [path.strip("/") for path in paths if path.strip("/")] or [""]
    roots = [(prefix, _import_root(prefix, files)) for prefix in prefixes]
    scans: dict[str, _ModuleScan] = {}
    skipped: list[str] = []
    for path, content in sources.items():
        root = next(
            (r for prefix, r in roots if not prefix or path.startswith(f"{prefix}/")),
            None,
        )
        if root is None:
            continue
        module = module_name(path[len(root) + 1 :] if root else path)
        if module is None:
            continue
        try:
            scans[module] = _scan_module(module, content, is_package=path.endswith("__init__.py"))
        except (SyntaxError, ValueError, RecursionError) as exc:
            logger.warning("{}:{} cannot be parsed and is ignored: {}", ref, path, exc)
            skipped.append(path)
    _expand_star_imports(scans)
    snapshot = _public_api(scans)
    snapshot.skipped = skipped
    return snapshot


def _listed(names: Sequence[str], noun: str) -> str:
    """Name parameters in a note.
    Args:
        names: Parameter names.
        noun: What they are, e.g. ``new optional parameter``.
    Returns:
        E.g. ``new optional parameters `a`, `b```.
    """
    quoted = ", ".join(f"`{name}`" for name in names)
    return f"{noun}{'s' if len(names) > 1 else ''} {quoted}"


def compare_params(old: Sequence[Param], new: Sequence[Param]) -> tuple[list[str], list[str]]:
    """Explain how a parameter list changed.
    Args:
        old: Parameters before.
        new: Parameters after.
    Returns:
        ``(breaking, compatible)``: reasons existing calls can fail, and
        changes that existing calls survive.
    """
    variadic = {"var-positional", "var-keyword"}
    positional = {"positional-only", "positional"}
    old_by = {p.name: p for p in old if p.kind not in variadic}
    new_by = {p.name: p for p in new if p.kind not in variadic}
    breaking: list[str] = []
    compatible: list[str] = []
    old_positional = [p.name for p in old if p.kind in positional]
    new_positional = [p.name for p in new if p.kind in positional]
    gone = [name for name in old_by if name not in new_by]
    fresh = [name for name in new_by if name not in old_by]
    renamed: dict[str, str] = {}
    if len(gone) == 1 and len(fresh) == 1 and gone[0] in old_positional:
        position = old_positional.index(gone[0])
        if position < len(new_positional) and new_positional[position] == fresh[0]:
            renamed[gone[0]] = fresh[0]
            breaking.append(f"parameter `{gone[0]}` renamed to `{fresh[0]}`")
    removed = [name for name in gone if name not in renamed]
    if removed:
        breaking.append(f"{_listed(removed, 'parameter')} removed")
    for name, before in old_by.items():
        after = new_by.get(name)
        if after is None:
            continue
        if after.required and not before.required:
            breaking.append(f"`{name}` is now required")
        if before.kind in positional and after.kind == "keyword-only":
            breaking.append(f"`{name}` is now keyword-only")
        if before.kind != "positional-only" and after.kind == "positional-only":
            breaking.append(f"`{name}` is now positional-only")
        defaults = (before.default, after.default)
        if None not in defaults and "..." not in defaults and before.default != after.default:
            compatible.append(f"default of `{name}` changed from `{before.default}` to `{after.default}`")
    additions = [name for name in fresh if name not in renamed.values()]
    required = [name for name in additions if new_by[name].required]
    optional = [name for name in additions if not new_by[name].required]
    if required:
        breaking.append(_listed(required, "new required parameter"))
    if optional:
        compatible.append(_listed(optional, "new optional parameter"))
    for kind, stars in (("var-positional", "*"), ("var-keyword", "**")):
        had = next((p.name for p in old if p.kind == kind), None)
        if had and not any(p.kind == kind for p in new):
            breaking.append(f"no longer accepts `{stars}{had}`")
    still_positional = [renamed.get(name, name) for name in old_positional]
    if set(still_positional) <= set(new_positional) and new_positional[: len(still_positional)] != still_positional:
        breaking.append("positional parameters changed order")
    return breaking, compatible


def _by_object(symbols: Iterable[Symbol]) -> dict[str, list[Symbol]]:
    """Group paths by the object they lead to.
    Args:
        symbols: Symbols of one snapshot.
    Returns:
        ``{definition: symbols}``, the shortest path first in every group.
    """
    groups: dict[str, list[Symbol]] = {}
    for symbol in sorted(symbols, key=lambda s: (s.qualname.count("."), s.qualname)):
        groups.setdefault(symbol.definition, []).append(symbol)
    return groups


def _match_renames(removed: dict[str, Symbol], added: dict[str, Symbol]) -> dict[str, str]:
    """Pair removed paths with the added paths that replace them.
    Objects are matched, not paths, so a function that is exported from two
    places is still one candidate.  Three passes, each accepting only
    unambiguous pairs: the same name in another module (moved), an identical
    implementation under a new name (renamed), and - for callables in the
    same place - identical parameters with a similar name.
    Args:
        removed: Symbols that exist only in the old snapshot.
        added: Symbols that exist only in the new snapshot.
    Returns:
        ``{old path: new path}``.
    """
    old_groups = _by_object(removed.values())
    new_groups = _by_object(added.values())
    old_heads = {origin: group[0] for origin, group in old_groups.items()}
    new_heads = {origin: group[0] for origin, group in new_groups.items()}
    pairs: dict[str, str] = {}
    taken: set[str] = set()

    def pair_unique(key_of: Callable[[Symbol], Hashable | None]) -> None:
        old_index: dict[Hashable, list[str]] = {}
        new_index: dict[Hashable, list[str]] = {}
        for origin, head in old_heads.items():
            if origin not in pairs and (key := key_of(head)) is not None:
                old_index.setdefault(key, []).append(origin)
        for origin, head in new_heads.items():
            if origin not in taken and (key := key_of(head)) is not None:
                new_index.setdefault(key, []).append(origin)
        for key, olds in old_index.items():
            news = new_index.get(key, [])
            if len(olds) == 1 and len(news) == 1:
                pairs[olds[0]] = news[0]
                taken.add(news[0])

    pair_unique(lambda s: (s.kind, s.name) if s.parent is None and s.kind in _MOVABLE_KINDS else None)
    pair_unique(lambda s: (s.kind, s.parent, s.fingerprint) if s.fingerprint else None)
    for origin, head in old_heads.items():
        if origin in pairs or not head.params:
            continue
        place = (head.kind, head.module, head.parent, head.params)
        scored = [
            (difflib.SequenceMatcher(None, head.name, other.name).ratio(), new_origin)
            for new_origin, other in new_heads.items()
            if new_origin not in taken and (other.kind, other.module, other.parent, other.params) == place
        ]
        best = max(scored, default=None)
        if best is not None and best[0] >= 0.6:
            pairs[origin] = best[1]
            taken.add(best[1])
    renames: dict[str, str] = {}
    for old_origin, new_origin in pairs.items():
        targets = new_groups[new_origin]
        for symbol in old_groups[old_origin]:
            same_place = [t for t in targets if (t.module, t.parent) == (symbol.module, symbol.parent)]
            renames[symbol.qualname] = (same_place or targets)[0].qualname
    return renames


def _names_preview(symbols: Sequence[Symbol], limit: int = 4) -> str:
    """List a few symbol names for a collapsed module entry.
    Args:
        symbols: The symbols to mention.
        limit: How many to name before summarising.
    Returns:
        E.g. ``3 public names: a, b, c`` or ``9 public names: a, b, c, d, ...``.
    """
    names = sorted(symbol.name for symbol in symbols)
    listed = ", ".join(names[:limit]) + (", ..." if len(names) > limit else "")
    return f"{_plural(len(names), 'public name')}: {listed}"


def _a(kind: str) -> str:
    """Put the right indefinite article in front of a symbol kind.
    Args:
        kind: E.g. ``attribute`` or ``method``.
    Returns:
        E.g. ``an attribute`` or ``a method``.
    """
    return f"{'an' if kind[:1] in 'aeiou' else 'a'} {kind}"


def diff_api(old: ApiSnapshot, new: ApiSnapshot) -> list[ApiChange]:
    """Compare two API snapshots.
    Whole modules and classes are reported once, not member by member, and
    an object that is exported from several places is reported once, with
    the other paths as aliases.
    Args:
        old: The API before.
        new: The API after.
    Returns:
        Removals, renames, signature changes and additions - breaking
        changes first, each group ordered by name.
    """
    removed = {path: s for path, s in old.symbols.items() if path not in new.symbols}
    added = {path: s for path, s in new.symbols.items() if path not in old.symbols}
    # A removed path whose object is still exported under another old path.
    still: dict[str, str] = {}
    for group in _by_object(s for s in old.symbols.values() if s.parent is None).values():
        alive = [s.qualname for s in group if s.qualname in new.symbols and new.symbols[s.qualname].kind == s.kind]
        if alive:
            still.update({s.qualname: alive[0] for s in group if s.qualname in removed})
    renames = _match_renames({p: s for p, s in removed.items() if p not in still}, added)
    renamed_to = set(renames.values())
    entries: list[tuple[tuple[str, ...], ApiChange]] = []
    collapsed: set[str] = set()
    for module in sorted(old.modules - new.modules):
        members = [s for s in removed.values() if s.module == module and s.parent is None]
        targets = [still.get(s.qualname) or renames.get(s.qualname) for s in members]
        destinations = {new.symbols[target].module for target in targets if target}
        count = _plural(len(members), "public name")
        if not members or (any(targets) and not (all(targets) and len(destinations) == 1)):
            continue
        if not any(targets):
            change = ApiChange("removed", "module", module, detail=_names_preview(members))
        elif all(s.qualname in still for s in members):
            destination = destinations.pop()
            verb = "is" if len(members) == 1 else "are"
            detail = f"its {count} {verb} still available from `{destination}`"
            change = ApiChange("removed", "module", module, destination, detail=detail)
        else:
            change = ApiChange("renamed", "module", module, destinations.pop(), detail=f"{count} moved")
        entries.append(((change.change, "module", module), change))
        collapsed.update(symbol.qualname for symbol in members)
    for path, symbol in sorted(removed.items()):
        if path in collapsed or (symbol.parent and symbol.parent in removed):
            continue
        if path in still:
            detail = f"still available as `{still[path]}`"
            change = ApiChange(
                "removed",
                symbol.kind,
                path,
                still[path],
                symbol.signature,
                detail=detail,
            )
        elif path in renames:
            successor = new.symbols[renames[path]]
            notes: list[str] = []
            if symbol.params is not None and successor.params is not None:
                breaking, compatible = compare_params(symbol.params, successor.params)
                notes = [*breaking, *compatible]
            change = ApiChange(
                "renamed",
                symbol.kind,
                path,
                successor.qualname,
                symbol.signature,
                successor.signature,
                "; ".join(notes),
            )
            entries.append((("renamed", symbol.definition, successor.definition), change))
            continue
        else:
            change = ApiChange("removed", symbol.kind, path, None, symbol.signature)
        entries.append((("removed", symbol.definition), change))
    for path in sorted(old.symbols.keys() & new.symbols.keys()):
        before, after = old.symbols[path], new.symbols[path]
        if before.kind in _OPAQUE_KINDS or after.kind in _OPAQUE_KINDS:
            continue
        if before.kind != after.kind:
            change = ApiChange(
                "changed",
                after.kind,
                path,
                detail=f"was {_a(before.kind)}, is now {_a(after.kind)}",
                breaking={before.kind, after.kind} == {"method", "attribute"},
            )
        elif before.params is not None and after.params is not None:
            breaking, compatible = compare_params(before.params, after.params)
            if not breaking and not compatible:
                continue
            change = ApiChange(
                "changed",
                after.kind,
                path,
                None,
                before.signature,
                after.signature,
                "; ".join([*breaking, *compatible]),
                breaking=bool(breaking),
            )
        else:
            continue
        entries.append((("changed", before.definition), change))
    new_modules = new.modules - old.modules
    for module in sorted(new_modules):
        members = [
            s for s in added.values() if s.module == module and s.parent is None and s.qualname not in renamed_to
        ]
        if members:
            change = ApiChange(
                "added",
                "module",
                module,
                detail=_names_preview(members),
                breaking=False,
            )
            entries.append((("added", "module", module), change))
    for path, symbol in sorted(added.items()):
        if path in renamed_to or symbol.module in new_modules:
            continue
        if symbol.parent and symbol.parent in added:
            continue
        change = ApiChange("added", symbol.kind, path, new_signature=symbol.signature, breaking=False)
        entries.append((("added", symbol.definition), change))
    # One object, one entry: the shortest path leads, the others are aliases.
    merged: dict[tuple[str, ...], list[ApiChange]] = {}
    for key, change in entries:
        same = (*key, change.old_signature, change.new_signature, str(change.breaking))
        merged.setdefault(same, []).append(change)
    changes: list[ApiChange] = []
    for duplicates in merged.values():
        duplicates.sort(key=lambda c: (c.name.count("."), c.name))
        aliases = tuple(change.name for change in duplicates[1:])
        changes.append(dataclasses.replace(duplicates[0], aliases=aliases))
    order = {"removed": 0, "renamed": 1, "changed": 2, "added": 3}
    return sorted(changes, key=lambda c: (not c.breaking, order[c.change], c.name))


def api_diff(repo: Path, old_ref: str, new_ref: str, paths: Sequence[str] = ()) -> tuple[list[ApiChange], list[str]]:
    """Diff the public Python API of two revisions.
    Args:
        repo: Directory inside the repository.
        old_ref: The earlier revision.
        new_ref: The later revision.
        paths: Only consider files below these repository paths.
    Returns:
        ``(changes, skipped files)``.
    Raises:
        GitError: If a revision cannot be read.
    """
    old = snapshot_api(repo, old_ref, paths)
    new = snapshot_api(repo, new_ref, paths)
    logger.info(
        "public API: {} symbols at {}, {} at {}",
        len(old.symbols),
        old_ref,
        len(new.symbols),
        new_ref,
    )
    return diff_api(old, new), sorted({*old.skipped, *new.skipped})


# ---------------------------------------------------------------------------
# Ranking, noise budget and the release-notes model
# ---------------------------------------------------------------------------
@dataclass
class Section:
    """A titled group of changes.
    Attributes:
        key: Category value or internal group key.
        title: Heading of the section.
        items: The changes that are shown.
        omitted: How many changes of this group the noise budget left out.
    """

    key: str
    title: str
    items: list[Change] = field(default_factory=list)
    omitted: int = 0


@dataclass
class DependencySummary:
    """All dependency updates of a release, collapsed.
    Attributes:
        changes: The individual updates.
    """

    changes: list[Change] = field(default_factory=list)

    @property
    def major(self) -> list[str]:
        """Labels of the major updates, e.g. ``requests 2 → 3``."""
        bumps = [change.dependency for change in self.changes if change.dependency]
        return [bump.label for bump in bumps if bump.major]

    @property
    def headline(self) -> str:
        """The one-line summary, e.g. ``12 dependency updates, including 3 major``."""
        text = _plural(len(self.changes), "dependency update")
        if self.major:
            text += f", including {len(self.major)} major ({', '.join(self.major)})"
        return text


@dataclass
class ReleaseNotes:
    """Everything the renderers need.
    Attributes:
        range: The revisions covered.
        audience: Who the notes are for.
        changes: Every interpreted commit of the range (release commits excluded).
        sections: The visible sections, most important first.
        dependencies: Collapsed dependency updates, if any.
        api_changes: Differences in the public Python API.
        api_skipped: Python files that could not be parsed for the API diff.
        release_commits: How many release-bookkeeping commits were left out.
        web_url: GitHub address of the project, for links.
        pr_status: Why pull-request descriptions were not used, or ``""``.
    """

    range: RevRange
    audience: Audience
    changes: list[Change] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    dependencies: DependencySummary | None = None
    api_changes: list[ApiChange] = field(default_factory=list)
    api_skipped: list[str] = field(default_factory=list)
    release_commits: int = 0
    web_url: str | None = None
    pr_status: str = ""

    @property
    def active(self) -> list[Change]:
        """Changes that are part of the release (not reverted before it)."""
        return [change for change in self.changes if not change.cancelled]

    @property
    def cancelled(self) -> list[Change]:
        """Changes that were reverted inside the range, and their reverts."""
        return [change for change in self.changes if change.cancelled]

    @property
    def internal(self) -> list[Change]:
        """Internal changes (docs, tests, CI, refactoring, chores)."""
        return [change for change in self.active if change.category is Category.INTERNAL]

    @property
    def missing_migrations(self) -> list[Change]:
        """Breaking changes nobody wrote a migration note for."""
        return [change for change in self.active if change.breaking and not change.migration]

    @property
    def breaking_api_changes(self) -> list[ApiChange]:
        """API changes that can break existing callers."""
        return [change for change in self.api_changes if change.breaking]

    @property
    def user_facing(self) -> bool:
        """True when users have something to read."""
        visible = {category for category, _ in USER_SECTIONS}
        return bool(self.breaking_api_changes) or any(change.category in visible for change in self.active)

    def counts(self) -> Counter[Category]:
        """Count the active changes per category.
        Returns:
            A counter keyed by :class:`Category`.
        """
        return Counter(change.category for change in self.active)

    def contributors(self) -> list[str]:
        """List the people behind the release, most commits first.
        Returns:
            Author and co-author names after ``.mailmap``, without bots
            (``dependabot[bot]``).
        """
        tally: Counter[str] = Counter()
        for change in self.changes:
            tally[change.commit.author] += 1
            tally.update(change.co_authors)
        people = [item for item in tally.items() if not item[0].endswith("[bot]")]
        return [name for name, _ in sorted(people, key=lambda item: (-item[1], item[0]))]


def allocate_budget(sizes: Sequence[int], budget: int, floor: int = SECTION_FLOOR) -> list[int]:
    """Split a bullet budget across sections given in rank order.
    Every section first gets up to *floor* bullets, so that 40 features
    cannot silence the bug fixes; what is left is spent by rank.
    Args:
        sizes: Number of changes per section, most important section first.
        budget: Total number of bullets available.
        floor: Bullets reserved per section.
    Returns:
        How many changes of each section are shown.
    """
    shown = [0] * len(sizes)
    remaining = max(budget, 0)
    for index, size in enumerate(sizes):
        shown[index] = min(size, floor, remaining)
        remaining -= shown[index]
    for index, size in enumerate(sizes):
        extra = min(size - shown[index], remaining)
        shown[index] += extra
        remaining -= extra
    return shown


def build_sections(changes: Sequence[Change], audience: Audience, max_items: int) -> list[Section]:
    """Group changes into sections and apply the noise budget.
    Breaking changes and security fixes are never cut.
    Args:
        changes: The active changes of the release, newest first.
        audience: Maintainers additionally get the internal sections.
        max_items: Bullet budget; ``0`` means unlimited.
    Returns:
        The non-empty sections in display order.
    """
    grouped = {category: [c for c in changes if c.category is category] for category in Category}
    sections = [
        Section(category.value, title, grouped[category]) for category, title in USER_SECTIONS if grouped[category]
    ]
    if max_items > 0:
        protected = {Category.BREAKING.value, Category.SECURITY.value}
        fixed = sum(len(s.items) for s in sections if s.key in protected)
        flexible = [section for section in sections if section.key not in protected]
        shown = allocate_budget([len(s.items) for s in flexible], max_items - fixed)
        for section, count in zip(flexible, shown, strict=True):
            section.omitted = len(section.items) - count
            section.items = section.items[:count]
    if audience is Audience.MAINTAINERS:
        internal = grouped[Category.INTERNAL]
        known = frozenset().union(*(types for _, _, types in INTERNAL_SECTIONS))
        for key, title, types in INTERNAL_SECTIONS:
            catch_all = key == "chore"
            items = [c for c in internal if c.type in types or (catch_all and c.type not in known)]
            if items:
                sections.append(Section(key, title, items))
    return [section for section in sections if section.items or section.omitted]


def build_release_notes(
    repo: Path,
    rev_range: RevRange,
    *,
    audience: Audience = Audience.USERS,
    max_items: int | None = None,
    fetcher: PullRequestFetcher | None = None,
    api: bool = True,
    api_paths: Sequence[str] = (),
    migration_files: Sequence[str] = DEFAULT_MIGRATION_FILES,
) -> ReleaseNotes:
    """Run the whole pipeline: read, parse, enrich, diff the API, rank.
    Args:
        repo: Directory inside the repository.
        rev_range: The revisions to cover.
        audience: Who the notes are for.
        max_items: Bullet budget; ``None`` picks the audience default
            (:data:`DEFAULT_USER_BUDGET` for users, unlimited for
            maintainers) and ``0`` means unlimited.
        fetcher: Source of pull-request descriptions; ``None`` to stay offline.
        api: Whether to diff the public Python API.
        api_paths: Only consider Python files below these paths for the API.
        migration_files: File names that count as migration guides.
    Returns:
        The release notes, ready for rendering.
    Raises:
        GitError: If the repository or the revisions cannot be read.
    """
    notes = ReleaseNotes(range=rev_range, audience=audience, web_url=detect_web_url(repo))
    for commit in read_commits(repo, rev_range):
        if is_release_commit(commit.subject):
            notes.release_commits += 1
        else:
            notes.changes.append(parse_commit(commit))
    cancel_reverts(notes.changes)
    enrich_changes(repo, rev_range, notes.changes, fetcher, migration_files)
    if fetcher is not None:
        notes.pr_status = fetcher.status
    if api and rev_range.from_ref:
        notes.api_changes, notes.api_skipped = api_diff(repo, rev_range.from_ref, rev_range.to_ref, api_paths)
    if max_items is None:
        max_items = DEFAULT_USER_BUDGET if audience is Audience.USERS else 0
    notes.sections = build_sections(notes.active, audience, max_items)
    dependencies = [c for c in notes.active if c.category is Category.DEPENDENCY]
    notes.dependencies = DependencySummary(dependencies) if dependencies else None
    logger.info(
        "{}: {} commits, {} shown in {} sections",
        rev_range.spec,
        len(notes.changes),
        sum(len(section.items) for section in notes.sections),
        len(notes.sections),
    )
    return notes


def mailmap_hints(changes: Sequence[Change]) -> list[str]:
    """Suggest ``.mailmap`` lines for people who appear under several identities.
    Two identities are taken for the same person when they share a name
    (ignoring case and spacing) or an e-mail address (ignoring case); chains
    are followed, so "Jane <a>", "Jane <b>" and "J. Doe <b>" are one person.
    The identity with the most commits - the most recent one on a tie -
    becomes the canonical one.
    Args:
        changes: The changes of the range, newest first (authors already mapped).
    Returns:
        Lines to add to ``.mailmap``, sorted; empty when everybody has one identity.
    """
    commits: Counter[tuple[str, str]] = Counter(
        (change.commit.author, change.commit.email.lower()) for change in changes
    )
    identities = list(commits)
    leader = list(range(len(identities)))

    def find(index: int) -> int:
        while leader[index] != index:
            leader[index] = leader[leader[index]]
            index = leader[index]
        return index

    first_seen: dict[tuple[str, str], int] = {}
    for index, (name, email) in enumerate(identities):
        for key in (("name", " ".join(name.lower().split())), ("email", email)):
            if key[1]:
                leader[find(index)] = find(first_seen.setdefault(key, index))
    people: dict[int, list[tuple[str, str]]] = {}
    for index, identity in enumerate(identities):
        people.setdefault(find(index), []).append(identity)
    hints: set[str] = set()
    for group in people.values():
        canonical = max(group, key=lambda identity: commits[identity])
        name, email = canonical
        for identity in group:
            if identity == canonical:
                continue
            if identity[1] == email:
                hints.add(f"{name} <{email}>")  # same address, another spelling of the name
            else:
                hints.add(f"{name} <{email}> <{identity[1]}>")
    return sorted(hints)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
_CATEGORY_NOUNS: tuple[tuple[Category, str], ...] = (
    (Category.BREAKING, "breaking change"),
    (Category.SECURITY, "security fix"),
    (Category.FEATURE, "new feature"),
    (Category.FIX, "bug fix"),
    (Category.PERFORMANCE, "performance improvement"),
    (Category.OTHER, "other change"),
)
_API_VERBS: dict[str, str] = {
    "removed": "Removed",
    "renamed": "Renamed",
    "changed": "Changed",
    "added": "Added",
}
_DETAILED_CATEGORIES: tuple[Any, ...] = (
    Category.BREAKING,
    Category.SECURITY,
    Category.FEATURE,
)
"""Categories whose bullets carry the summary of the pull-request description."""
_CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)")
_MD_SPECIAL_RE = re.compile(r"(?<!\\)(?:\*|(?<![^\W_])_|_(?![^\W_])|<(?=[A-Za-z/!?]))")
_PLAIN_WORD_RE = re.compile(r"[a-z]+(?![\w(.\-/])")


def _reference(change: Change, web_url: str | None) -> str:
    """Render the pull request or issue a change refers to.
    Args:
        change: The change.
        web_url: GitHub address of the project, for links.
    Returns:
        E.g. `` (#123)`` - linked when the project address is known - or ``""``.
    """
    number = change.pr if change.pr is not None else (change.refs[0] if change.refs else None)
    if number is None:
        return ""
    if web_url:
        kind = "pull" if change.pr is not None else "issues"
        return f" ([#{number}]({web_url}/{kind}/{number}))"
    return f" (#{number})"


def _escape_markdown(text: str) -> str:
    """Keep the text of a commit subject from being read as formatting.
    ``__init__`` must not turn bold, ``*args`` must not start an emphasis and
    ``<details>`` must not become HTML.  Code spans are left alone, and so
    are the underscores inside ``snake_case`` words, which are never emphasis.
    Args:
        text: Text written by a committer.
    Returns:
        The text with the troublesome characters backslash-escaped.
    """
    parts: list[str] = []
    position = 0
    for span in _CODE_SPAN_RE.finditer(text):
        parts.append(_MD_SPECIAL_RE.sub(r"\\\g<0>", text[position : span.start()]))
        parts.append(span.group())
        position = span.end()
    parts.append(_MD_SPECIAL_RE.sub(r"\\\g<0>", text[position:]))
    return "".join(parts)


def _headline(change: Change) -> str:
    """Render the description of a change as a bullet text.
    Args:
        change: The change.
    Returns:
        The description with the scope in bold in front (unless the scope
        only repeats the section, like ``deps``).  An ordinary first word is
        capitalised; ``iOS``, ``fetch()`` and ``snake_case`` are left as written.
    """
    text = change.description.strip().rstrip(".")
    if _PLAIN_WORD_RE.match(text):
        text = text[0].upper() + text[1:]
    text = _escape_markdown(text)
    scope = change.scope or ""
    if not scope or scope.lower() in _DEPENDENCY_SCOPES | {"security", "sec"}:
        return text
    return f"**{_escape_markdown(scope)}:** {text}"


def _indent(text: str, prefix: str) -> list[str]:
    """Put a possibly multi-line note under a bullet.
    Args:
        text: The note.
        prefix: The nested bullet with its label (e.g. ``"  - **Migration:** "``).
    Returns:
        Markdown lines: prose is joined onto the bullet's line, while lists,
        code blocks and tables keep their shape on indented lines below it.
    """
    lines = text.splitlines() or [""]
    if not any(_MD_BLOCK_RE.match(line) for line in lines):
        return [prefix + " ".join(line.strip() for line in lines if line.strip())]
    return [
        prefix.rstrip(),
        "",
        *(f"    {line}" if line.strip() else "" for line in lines),
    ]


def _bullet(change: Change, notes: ReleaseNotes) -> list[str]:
    """Render one change as Markdown.
    Args:
        change: The change.
        notes: The release notes it belongs to (audience, links).
    Returns:
        The bullet and its nested lines.
    """
    maintainers = notes.audience is Audience.MAINTAINERS
    line = f"- {_headline(change)}{_reference(change, notes.web_url)}"
    if maintainers:
        sha = change.commit.short
        if notes.web_url:
            sha = f"[{sha}]({notes.web_url}/commit/{change.commit.sha})"
        line += f" - {sha}, {change.commit.author}"
    lines = [line]
    if change.detail and change.category in _DETAILED_CATEGORIES:
        lines.append(f"  - {change.detail}")
    if not change.breaking:
        return lines
    for note in change.breaking_notes:
        if note != change.migration:
            lines.extend(_indent(note, "  - "))
    if change.migration:
        lines.extend(_indent(change.migration, "  - **Migration:** "))
    else:
        where = f"#{change.pr}" if change.pr is not None else f"commit {change.commit.short}"
        who = f"; ask {change.commit.author}" if maintainers else ""
        lines.append(f"  - **Migration:** _no migration note was provided (see {where}{who})._")
    return lines


def _callable_name(name: str, kind: str, signature: str = "") -> str:
    """Render a symbol name as inline code.
    Args:
        name: Qualified name.
        kind: Kind of the symbol.
        signature: Parameter list to show, if any.
    Returns:
        E.g. ```pkg.connect(host, port=5432)```, ```pkg.connect()``` or ```pkg.LIMIT```.
    """
    if kind in {"function", "method"}:
        return f"`{name}{signature or '()'}`"
    return f"`{name}{signature}`" if signature else f"`{name}`"


def describe_api_change(change: ApiChange) -> str:
    """Render one API change as a Markdown sentence.
    Args:
        change: The API change.
    Returns:
        E.g. ``**Renamed** `pkg.fetch()` → `pkg.fetch_all()```.
    """
    verb = _API_VERBS[change.change] + (" module" if change.kind == "module" else "")
    if change.change == "renamed":
        new_name = _callable_name(change.new_name or "", change.kind)
        text = f"**{verb}** {_callable_name(change.name, change.kind)} → {new_name}"
    elif change.change == "changed" and change.old_signature != change.new_signature:
        before = _callable_name(change.name, change.kind, change.old_signature)
        after = _callable_name(change.name, change.kind, change.new_signature)
        text = f"**{verb}** {before} → {after}"
    else:
        signature = change.new_signature if change.change == "added" else ""
        text = f"**{verb}** {_callable_name(change.name, change.kind, signature)}"
    if change.detail:
        text += f" - {change.detail}"
    if change.aliases:
        shown = ", ".join(f"`{alias}`" for alias in change.aliases[:3])
        more = f" and {len(change.aliases) - 3} more" if len(change.aliases) > 3 else ""
        text += f" (also {shown}{more})"
    return text


def _api_lines(notes: ReleaseNotes) -> list[str]:
    """Render the "How do I upgrade?" section.
    Args:
        notes: The release notes.
    Returns:
        Markdown lines, empty when the API diff has nothing for the audience.
    """
    everything = notes.audience is Audience.MAINTAINERS
    listed = notes.api_changes if everything else notes.breaking_api_changes
    if not listed:
        return []
    limit = len(listed) if everything else API_SECTION_LIMIT
    start = notes.range.from_ref or "the first commit"
    lines = [
        "### How do I upgrade?",
        "",
        f"Public Python API changes between {start} and {notes.range.to_ref}:",
        "",
        *(f"- {describe_api_change(change)}" for change in listed[:limit]),
    ]
    if len(listed) > limit:
        more = _plural(len(listed) - limit, "more API change")
        lines.append(f"- ...and {more} (`{APP_NAME} --api-diff {start}..{notes.range.to_ref}` lists all).")
    return [*lines, ""]


def _intro(notes: ReleaseNotes) -> str:
    """Write the sentence that opens the notes.
    Args:
        notes: The release notes.
    Returns:
        A summary of what changed - or the plain statement that nothing
        user-facing did.
    """
    since = notes.range.since
    counts = notes.counts()
    background = [
        _plural(counts[Category.INTERNAL], "internal change") if counts[Category.INTERNAL] else "",
        _plural(counts[Category.DEPENDENCY], "dependency update") if counts[Category.DEPENDENCY] else "",
    ]
    background = [part for part in background if part]
    if not notes.active:
        return f"No changes {since}."
    if not notes.user_facing:
        lead = "This release contains" if notes.range.is_release else "So far there are"
        rest = f" {lead} only {_join(background)}." if background else ""
        return f"No user-facing changes {since}.{rest}"
    parts = [_plural(counts[category], noun) for category, noun in _CATEGORY_NOUNS if counts[category]]
    if not parts:
        parts = [_plural(len(notes.breaking_api_changes), "breaking API change")]
    return f"{_join(parts)} {since}."


def render_markdown(notes: ReleaseNotes) -> str:
    """Render release notes as Markdown.
    Empty sections are never printed; what the noise budget leaves out is
    counted in the text.
    Args:
        notes: The release notes.
    Returns:
        The Markdown document, starting with a level-2 heading.
    """
    maintainers = notes.audience is Audience.MAINTAINERS
    title = notes.range.title + (f" ({notes.range.date})" if notes.range.date else "")
    lines = [f"## {title}", "", _intro(notes), ""]
    nouns: dict[str, str] = {category.value: noun for category, noun in _CATEGORY_NOUNS}
    unlisted: list[str] = []
    api_written = False
    for section in notes.sections:
        noun = nouns.get(section.key, "change")
        if not section.items:
            unlisted.append(_plural(section.omitted, noun))
            continue
        if section.key != Category.BREAKING.value and not api_written:
            lines.extend(_api_lines(notes))
            api_written = True
        lines.extend([f"### {section.title}", ""])
        for change in section.items:
            lines.extend(_bullet(change, notes))
        if section.omitted:
            lines.append(f"- ...and {_plural(section.omitted, f'more {noun}')} not listed here.")
        lines.append("")
    if not api_written:
        lines.extend(_api_lines(notes))
    if notes.dependencies:
        lines.extend(["### Dependencies", "", f"- {notes.dependencies.headline}."])
        if maintainers:
            for change in notes.dependencies.changes:
                lines.extend(f"  {line}" for line in _bullet(change, notes))
        lines.append("")
    internal = notes.internal
    if internal and not maintainers and notes.user_facing:
        kinds = sorted({change.type or "chore" for change in internal})
        unlisted.append(f"{_plural(len(internal), 'internal change')} ({', '.join(kinds)})")
    if unlisted:
        lines.extend([f"_Not listed: {_join(unlisted)}._", ""])
    if maintainers:
        lines.extend(_maintainer_footer(notes))
    return "\n".join(lines).rstrip() + "\n"


def _maintainer_footer(notes: ReleaseNotes) -> list[str]:
    """Render what only maintainers want to know.
    Args:
        notes: The release notes.
    Returns:
        Markdown lines about reverted work, contributors, ``.mailmap`` and
        anything the tool could not look at.
    """
    lines: list[str] = []
    reverted = [change for change in notes.cancelled if change.reverted_by]
    if reverted:
        lines.extend(["### Reverted before release", ""])
        lines.extend(
            f"- {_escape_markdown(change.commit.subject)} - {change.commit.short}, "
            f"reverted by {(change.reverted_by or '')[:7]}"
            for change in reverted
        )
        lines.append("")
    contributors = notes.contributors()
    if contributors:
        lines.extend(["### Contributors", "", ", ".join(contributors), ""])
    hints = mailmap_hints(notes.changes)
    if hints:
        lines.extend([
            "_Some people appear under several identities. Adding these lines to `.mailmap` merges them:_",
            "",
            "```",
            *hints,
            "```",
            "",
        ])
    remarks = []
    if notes.release_commits:
        remarks.append(f"{_plural(notes.release_commits, 'release commit')} left out")
    if notes.pr_status and any(change.pr is not None for change in notes.changes):
        remarks.append(f"pull-request descriptions were not used: {notes.pr_status}")
    if notes.api_skipped:
        remarks.append(f"not parsed for the API diff: {', '.join(notes.api_skipped)}")
    if remarks:
        text = "; ".join(remarks)
        lines.extend([f"_{text[:1].upper()}{text[1:]}._", ""])
    return lines


def render_json(notes: ReleaseNotes) -> str:
    """Render release notes as JSON.
    Args:
        notes: The release notes.
    Returns:
        A JSON document with the same content as the Markdown rendering,
        plus the list of missing migration notes.
    """
    counts = notes.counts()
    payload = {
        "title": notes.range.title,
        "from": notes.range.from_ref,
        "to": notes.range.to_ref,
        "date": notes.range.date,
        "audience": notes.audience.value,
        "summary": _intro(notes),
        "user_facing": notes.user_facing,
        "counts": {category.value: counts[category] for category in Category},
        "reverted_before_release": sum(1 for change in notes.cancelled if change.reverted_by),
        "sections": [
            {
                "key": section.key,
                "title": section.title,
                "omitted": section.omitted,
                "items": [change.to_dict() for change in section.items],
            }
            for section in notes.sections
        ],
        "dependencies": (
            {
                "total": len(notes.dependencies.changes),
                "major": notes.dependencies.major,
                "headline": notes.dependencies.headline,
            }
            if notes.dependencies
            else None
        ),
        "api_changes": [change.to_dict() for change in notes.api_changes],
        "missing_migration_notes": [change.to_dict() for change in notes.missing_migrations],
        "contributors": notes.contributors(),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render_api_markdown(changes: Sequence[ApiChange], old_ref: str, new_ref: str) -> str:
    """Render a stand-alone API diff as Markdown.
    Args:
        changes: The API changes.
        old_ref: The earlier revision.
        new_ref: The later revision.
    Returns:
        A Markdown document; says so plainly when nothing changed.
    """
    lines = [f"## Public API changes: {old_ref}..{new_ref}", ""]
    if not changes:
        lines.append("No public API changes detected.")
    lines.extend(f"- {describe_api_change(change)}" for change in changes)
    return "\n".join(lines) + "\n"


def api_table(changes: Sequence[ApiChange], old_ref: str, new_ref: str) -> Table:
    """Build a rich table of API changes.
    Args:
        changes: The API changes.
        old_ref: The earlier revision.
        new_ref: The later revision.
    Returns:
        The table, one row per change.
    """
    table = Table(
        title=Text(f"Public API changes: {old_ref}..{new_ref}"),
        title_justify="left",
        header_style="bold",
    )
    for column in ("Change", "Kind", "Symbol", "Now", "Details"):
        table.add_column(column, overflow="fold")
    styles = {
        "removed": "red",
        "renamed": "yellow",
        "changed": "yellow",
        "added": "green",
    }
    for change in changes:
        now = change.new_name or ""
        if change.new_signature:
            now = (change.new_name or change.name) + change.new_signature
        details = [change.detail.replace("`", "") or ("" if change.breaking else "compatible")]
        if change.aliases:
            details.append(f"also {', '.join(change.aliases)}")
        table.add_row(
            Text(
                change.change,
                style=styles[change.change] if change.breaking else "green",
            ),
            Text(change.kind),
            Text(change.name + change.old_signature),
            Text(now or "-"),
            Text("; ".join(detail for detail in details if detail)),
        )
    return table


def check_table(missing: Sequence[Change]) -> Table:
    """Build the table ``--check`` prints for breaking changes without notes.
    Args:
        missing: The offending changes.
    Returns:
        The table, one row per change.
    """
    table = Table(
        title=Text("Breaking changes without a migration note"),
        title_justify="left",
        header_style="bold",
    )
    for column in ("Commit", "Subject", "PR", "Author"):
        table.add_column(column, overflow="fold")
    for change in missing:
        table.add_row(
            Text(change.commit.short, style="red"),
            Text(change.commit.subject),
            Text(f"#{change.pr}" if change.pr is not None else "-"),
            Text(change.commit.author),
        )
    return table


CHECK_ADVICE: str = (
    "Say what users must do, in one of these places:\n"
    "  * a `BREAKING CHANGE:` footer that gives an instruction\n"
    '    (e.g. "BREAKING CHANGE: `--fast` is gone; use `--quick` instead"),\n'
    "  * a `Migration:` paragraph in the commit message or a `## Migration`\n"
    "    section in the pull-request description,\n"
    "  * an entry in MIGRATION.md in the same commit.\n"
    "Already merged? Edit the pull-request description, or add the MIGRATION.md\n"
    'entry in a new commit that names the change ("docs: migration note for #123").'
)
"""What ``--check`` tells people to do about a missing migration note."""


# ---------------------------------------------------------------------------
# CHANGELOG.md: insert, never rewrite
# ---------------------------------------------------------------------------
def _write_text(path: Path, text: str) -> None:
    """Write *text* to *path* exactly as given (no newline translation).
    Args:
        path: File to write.
        text: The complete new content.
    Raises:
        GitError: If the file cannot be written.
    """
    try:
        atomic_write(path, text.encode("utf-8"))
    except OSError as exc:
        raise GitError(f"cannot write {path}: {exc}") from exc


def _marker_line(marker: str) -> re.Pattern[str]:
    """Build a pattern that finds *marker* standing alone on a line.
    Args:
        marker: The HTML comment to look for.
    Returns:
        A pattern that ignores the marker where it is merely quoted in prose.
    """
    return re.compile(rf"^[ \t]*{re.escape(marker)}[ \t]*\r?$", re.MULTILINE)


def _after_line_break(text: str, position: int) -> int:
    """Skip the line break at *position*, if there is one.
    Args:
        text: The text.
        position: Offset of the end of a line.
    Returns:
        The offset of the next line.
    """
    for newline in ("\r\n", "\n"):
        if text.startswith(newline, position):
            return position + len(newline)
    return position


def insert_into_changelog(path: Path, block_id: str, markdown: str, *, replace: bool = False) -> str:
    """Insert notes into a changelog file without touching anything else.
    The notes are wrapped in ``<!-- changelog:begin ID -->`` /
    ``<!-- changelog:end ID -->`` and placed directly below the line that
    holds the :data:`CHANGELOG_MARKER`, so the newest release stays on top -
    below the entry for unreleased changes, if the file has one.  Text
    outside the inserted block is preserved byte for byte.
    Args:
        path: The changelog file; created with a marker when missing.
        block_id: Identifies the block (the version, or ``unreleased``).
        markdown: The notes to insert.
        replace: Overwrite an existing block with the same id.
    Returns:
        ``"created"``, ``"inserted"`` or ``"replaced"``.
    Raises:
        GitError: If the file has no marker line, or the block exists and
            *replace* is false; the file is left untouched in both cases.
    """
    begin = f"<!-- changelog:begin {block_id} -->"
    end = f"<!-- changelog:end {block_id} -->"
    # Notes that quote a marker must not be taken for one on the next run.
    body = markdown.strip().replace("<!-- changelog:", "&lt;!-- changelog:")
    block = f"{begin}\n{body}\n{end}\n"
    if not path.exists():
        _write_text(path, f"# Changelog\n\n{CHANGELOG_MARKER}\n\n{block}")
        return "created"
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GitError(f"cannot read {path} as UTF-8 text: {exc} (nothing was written)") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    block = block.replace("\n", newline)
    existing = _marker_line(begin).search(text)
    if existing:
        closing = _marker_line(end).search(text, existing.end())
        if closing is None:
            raise GitError(f"{path} has '{begin}' without a matching end marker")
        if not replace:
            raise GitError(f"{path} already has an entry for {block_id}; pass --replace to regenerate it")
        rest = text[_after_line_break(text, closing.end()) :]
        _write_text(path, text[: existing.start()] + block + rest)
        return "replaced"
    marker = _marker_line(CHANGELOG_MARKER).search(text)
    if marker is None:
        raise GitError(
            f"{path} has no '{CHANGELOG_MARKER}' line; add it where new releases "
            f"should be inserted (nothing was written)"
        )
    position = _after_line_break(text, marker.end())
    if block_id != UNRELEASED_ID:
        # A release goes below the entry for unreleased changes, which stays on top.
        pending = _marker_line(f"<!-- changelog:begin {UNRELEASED_ID} -->").search(text, position)
        closing = _marker_line(f"<!-- changelog:end {UNRELEASED_ID} -->").search(text, position)
        if pending and closing and pending.start() < closing.start() and not text[position : pending.start()].strip():
            position = _after_line_break(text, closing.end())
    before, after = text[:position], text[position:]
    if not before.endswith(newline):
        before += newline
    separator = "" if not after or after.startswith(newline) else newline
    _write_text(path, before + newline + block + separator + after)
    return "inserted"


# ---------------------------------------------------------------------------
# CLI (argparse)
# ---------------------------------------------------------------------------


def _stderr_sink(message: str) -> None:
    """Write a log record to whatever ``sys.stderr`` currently is.
    Args:
        message: The formatted record, newline included.
    """
    sys.stderr.write(message)


def configure_logging(verbosity: int) -> None:
    """Send loguru output to stderr at a level chosen by ``-v`` flags.
    Args:
        verbosity: 0 shows warnings, 1 adds info, 2 or more adds debug.
    """
    level = "WARNING" if verbosity <= 0 else "INFO" if verbosity == 1 else "DEBUG"
    logger.remove()
    logger.add(
        _stderr_sink,
        level=level,
        format="<level>{level: <7}</level> {message}",
        colorize=sys.stderr.isatty(),
    )
    logger.enable(__name__)


def _fail(message: str) -> SystemExit:
    """Print an error and build the exit to raise.
    Args:
        message: What went wrong.
    Returns:
        The exception to raise, carrying :data:`EXIT_ERROR`.
    """
    Console(stderr=True).print(Text(f"error: {message}", style="bold red"), soft_wrap=True)
    return SystemExit(EXIT_ERROR)


def _emit(text: str, output_format: OutputFormat) -> None:
    """Print a rendered document.
    Args:
        text: Markdown or JSON text.
        output_format: ``term`` renders Markdown with rich; anything else is
            written verbatim so it can be redirected.
    """
    if output_format is OutputFormat.TERM:
        Console().print(Markdown(text))
    else:
        print(text.rstrip("\n"))


def _run_api_diff(repo: Path, spec: str, paths: Sequence[str], output_format: OutputFormat) -> None:
    """Handle ``--api-diff A..B``.
    Args:
        repo: Directory inside the repository.
        spec: The revision range.
        paths: Only consider Python files below these paths.
        output_format: How to print the result.
    Raises:
        GitError: If the range is malformed or a revision is unknown.
    """
    old_ref, separator, new_ref = spec.partition("..")
    new_ref = new_ref.lstrip(".")
    if not separator or not old_ref or not new_ref:
        raise GitError(f"--api-diff expects OLD..NEW, got {spec!r}")
    resolve_commit(repo, old_ref)
    resolve_commit(repo, new_ref)
    changes, skipped = api_diff(repo, old_ref, new_ref, paths)
    if output_format is OutputFormat.JSON:
        payload = {
            "from": old_ref,
            "to": new_ref,
            "breaking": sum(1 for change in changes if change.breaking),
            "changes": [change.to_dict() for change in changes],
            "skipped": skipped,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif output_format is OutputFormat.TERM and changes:
        Console().print(api_table(changes, old_ref, new_ref))
    else:
        _emit(render_api_markdown(changes, old_ref, new_ref), output_format)


def _run_check(notes: ReleaseNotes, output_format: OutputFormat) -> int:
    """Handle ``--check``.
    Args:
        notes: The release notes of the checked range.
        output_format: ``json`` prints a machine-readable verdict.
    Returns:
        :data:`EXIT_CHECK_FAILED` when a breaking change lacks a migration
        note, otherwise :data:`EXIT_OK`.
    """
    missing = notes.missing_migrations
    breaking = [change for change in notes.active if change.breaking]
    undeclared = notes.breaking_api_changes if not breaking else []
    if output_format is OutputFormat.JSON:
        payload = {
            "ok": not missing,
            "range": notes.range.spec,
            "breaking_changes": len(breaking),
            "missing_migration_notes": [change.to_dict() for change in missing],
            "undeclared_api_breaks": [change.to_dict() for change in undeclared],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_CHECK_FAILED if missing else EXIT_OK
    console = Console()
    if undeclared:
        text = (
            f"warning: {_plural(len(undeclared), 'public API change')} can break callers, "
            f"but no commit in {notes.range.spec} is marked as breaking "
            f"(`{APP_NAME} --api-diff` shows them)."
        )
        console.print(Text(text, style="yellow"), soft_wrap=True)
    if not missing:
        if breaking:
            count = _plural(len(breaking), "breaking change")
            text = f"OK: {count} in {notes.range.spec}, all with migration notes."
        else:
            text = f"OK: no breaking changes in {notes.range.spec}."
        console.print(Text(text, style="green"), soft_wrap=True)
        return EXIT_OK
    console.print(check_table(missing))
    if notes.pr_status and any(change.pr is not None for change in missing):
        text = f"Pull-request descriptions were not consulted: {notes.pr_status}."
        console.print(Text(text, style="yellow"), soft_wrap=True)
    console.print(Text(CHECK_ADVICE), soft_wrap=True)
    return EXIT_CHECK_FAILED


def _non_negative_int(value: str) -> int:
    num = int(value)
    if num < 0:
        msg = "must be >= 0"
        raise argparse.ArgumentTypeError(msg)
    return num


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=(
            "Release notes for the people who use the software. "
            "Without --from/--to the notes cover everything since the latest version tag. "
            "Exit codes: 0 success, 1 --check failed, 2 error."
        ),
    )
    parser.add_argument(
        "-C",
        "--repo",
        type=Path,
        default=Path(),
        help="Repository to read. (default: current directory)",
    )
    parser.add_argument(
        "--from",
        dest="from_ref",
        default=None,
        help="Start of the range (exclusive). (default: previous version tag)",
    )
    parser.add_argument(
        "--to",
        dest="to_ref",
        default=None,
        help="End of the range (inclusive). (default: HEAD)",
    )
    parser.add_argument(
        "--unreleased",
        action="store_true",
        help="Everything after the latest release tag.",
    )
    parser.add_argument(
        "--audience",
        type=str.lower,
        choices=[a.value for a in Audience],
        default=Audience.USERS.value,
        help="Who the notes are for.",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        type=str.lower,
        choices=[o.value for o in OutputFormat],
        default=OutputFormat.MD.value,
        help="Output format.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 when a breaking change lacks a migration note (for CI).",
    )
    parser.add_argument(
        "--api-diff",
        dest="api_diff_range",
        default=None,
        metavar="OLD..NEW",
        help="Only diff the public Python API.",
    )
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        metavar="FILE",
        help=f"Insert the notes into FILE below '{CHANGELOG_MARKER}' instead of printing them.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="With --write: regenerate an entry that already exists.",
    )
    parser.add_argument(
        "--max-items",
        type=_non_negative_int,
        default=None,
        help=(
            f"Noise budget: maximum number of bullets (0 = unlimited). "
            f"(default: {DEFAULT_USER_BUDGET} for users, unlimited for maintainers)"
        ),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        default=_env_truthy("GIT_CHANGELOG_OFFLINE"),
        help="Never call `gh` for pull-request descriptions.",
    )
    parser.add_argument(
        "--pr-limit",
        type=_non_negative_int,
        default=DEFAULT_PR_LOOKUPS,
        help="Read at most this many pull-request descriptions, most important changes first.",
    )
    parser.add_argument(
        "--api",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include the public API diff in the notes.",
    )
    parser.add_argument(
        "--api-path",
        action="append",
        default=None,
        metavar="PATH",
        help="Limit the API diff to PATH, relative to the repository root (repeatable).",
    )
    parser.add_argument(
        "--migration-file",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "File whose additions count as migration notes (repeatable). "
            f"(default: {', '.join(DEFAULT_MIGRATION_FILES)})"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Log to stderr (-v info, -vv debug).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {__version__}",
        help="Show the version.",
    )
    return parser


def changelog(
    repo: Path = Path(),
    from_ref: str | None = None,
    to_ref: str | None = None,
    unreleased: bool = False,
    audience: Audience = Audience.USERS,
    output_format: OutputFormat = OutputFormat.MD,
    check: bool = False,
    api_diff_range: str | None = None,
    write: Path | None = None,
    replace: bool = False,
    max_items: int | None = None,
    offline: bool = False,
    pr_limit: int = DEFAULT_PR_LOOKUPS,
    api: bool = True,
    api_path: list[str] | None = None,
    migration_file: list[str] | None = None,
    verbose: int = 0,
) -> None:
    """Write release notes for a range of commits.
    Without --from/--to the notes cover everything since the latest version
    tag.  Exit codes: 0 success, 1 --check failed, 2 error.
    """
    configure_logging(verbose)
    try:
        repo = repository_root(repo)
        if api_diff_range:
            _run_api_diff(repo, api_diff_range, api_path or [], output_format)
            return
        rev_range = resolve_range(repo, from_ref, to_ref, unreleased=unreleased)
        notes = build_release_notes(
            repo,
            rev_range,
            audience=audience,
            max_items=max_items,
            fetcher=PullRequestFetcher(repo, enabled=not offline, limit=pr_limit),
            api=api,
            api_paths=api_path or [],
            migration_files=migration_file or DEFAULT_MIGRATION_FILES,
        )
        if check:
            raise SystemExit(_run_check(notes, output_format))
        if write is not None:
            block_id = rev_range.title if rev_range.is_release else UNRELEASED_ID
            action = insert_into_changelog(write, block_id, render_markdown(notes), replace=replace)
            Console().print(
                Text(f"{write}: {action} the entry for {rev_range.title}"),
                soft_wrap=True,
            )
            return
    except GitError as exc:
        raise _fail(str(exc)) from exc
    if output_format is OutputFormat.JSON:
        print(render_json(notes))
    else:
        _emit(render_markdown(notes), output_format)


def main(argv: list[str] | None = None) -> None:
    """Entry point of the ``git-changelog`` command."""
    args = build_parser().parse_args(argv)
    changelog(
        repo=args.repo,
        from_ref=args.from_ref,
        to_ref=args.to_ref,
        unreleased=args.unreleased,
        audience=Audience(args.audience),
        output_format=OutputFormat(args.output_format),
        check=args.check,
        api_diff_range=args.api_diff_range,
        write=args.write,
        replace=args.replace,
        max_items=args.max_items,
        offline=args.offline,
        pr_limit=args.pr_limit,
        api=args.api,
        api_path=args.api_path,
        migration_file=args.migration_file,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
