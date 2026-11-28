#!/data/data/com.termux/files/usr/bin/env python
"""git-trim - bulk-safe branch cleanup for git repositories.

``git branch --merged main`` misses everything merged through GitHub's
*squash* or *rebase* buttons, so developers pile up dead branches, stop
trusting the tool and start deleting by hand.  ``git-trim`` finds those
branches, refuses every deletion that could lose work, and can undo itself.

How a branch is judged
----------------------
1. **Ancestor** - the branch tip is reachable from the mainline (a plain merge
   or fast-forward).  This is what ``git branch --merged`` sees.
2. **Patch-id** - every non-merge commit of the branch has an equivalent
   commit on the mainline (same ``git patch-id --stable``).  This catches
   rebase-merges, cherry-picks and squashes of single-commit branches.
3. **Squash** - the *combined* diff of the branch (merge-base to tip) has the
   same patch-id as one mainline commit.  This catches multi-commit squash
   merges, which confuse commit-by-commit tools such as ``git cherry``.

The mainline is walked **once per invocation**, from the oldest fork point of
all examined branches, and its patch-ids are cached in memory.  A mainline
commit only counts as a match when the branch commit was not written on top
of it, so re-applying a change that the mainline reverted is not mistaken for
a merge.  Merge commits are skipped like ``git cherry`` does, but a branch
whose merge commit carries changes of its own (an "evil merge") is only
accepted through the combined diff.  Merging the mainline back into a branch
- before or after it was squashed - does not hide the merge.

When a branch is safe to delete
-------------------------------
A branch is **never** deleted when it

* is the current branch, is checked out in any worktree, or is in the middle
  of a rebase or bisect there,
* matches ``--keep`` (names or globs, case-insensitive; the mainline itself
  is always kept),
* has an unknown merge status (unrelated history, only merge commits or only
  empty commits ahead of the mainline; in a shallow clone, every branch whose
  tip is not reachable from the mainline, because patch-ids need the missing
  history),
* is the only thing keeping a commit alive that a superproject pins (when
  the repository is a submodule),
* is unmerged **and** has no remote upstream, a deleted upstream, or commits
  that were never pushed to its upstream,
* is unmerged **and** has a commit newer than ``--stale`` days.

Every deleted tip is appended to ``.git/trim-log.jsonl`` *before* the branch
is removed, so ``git-trim undo`` can recreate it with
``git branch <name> <sha>`` (and restore its upstream).

Usage
-----
Also works as a git subcommand (``git trim ...``) when installed on ``PATH``
as ``git-trim``::

    git-trim scan                     # table: name, last commit, merged?, method, safe?
    git-trim scan --json              # machine-readable, for CI
    git-trim delete --dry-run         # the default: print the plan, delete nothing
    git-trim delete --yes --pattern 'feat/*' --keep 'main,develop,release/*'
    git-trim prune-remote --yes       # explicit `git fetch --prune`
    git-trim undo                     # recreate the branches of the last delete
    git-trim undo --all               # recreate everything in the undo log

Exit codes: ``0`` nothing to do / success, ``10`` candidates found (for CI
and hooks), ``1`` error.

Requires Python 3.11+, git 2.15+, ``typer``, ``rich`` and ``loguru``.  Branch
names are always passed to git as argv entries - never through a shell.
"""

from __future__ import annotations
import argparse
from collections.abc import Generator, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Annotated, Any

from dh import atomic_write
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.text import Text


__all__: list[str] = [
    "Branch",
    "BranchReport",
    "Classification",
    "DeleteOutcome",
    "GitError",
    "Mainline",
    "Method",
    "PruneOutcome",
    "ScanResult",
    "UndoOutcome",
    "Verdict",
    "app",
    "classify_branches",
    "delete_branches",
    "list_branches",
    "main",
    "prune_remote",
    "scan_branches",
    "undo_deletions",
]

__version__: str = "1.0.0"

APP_NAME: str = "git-trim"

EXIT_OK: int = 0
"""Exit code: nothing to do, or the requested action succeeded."""
EXIT_ERROR: int = 1
"""Exit code: something went wrong."""
EXIT_CANDIDATES: int = 10
"""Exit code: branches that are safe to delete were found (and still exist)."""

DEFAULT_KEEP: tuple[str, ...] = ("main", "master", "develop")
"""Branches protected when ``--keep`` is not given."""
DEFAULT_STALE_DAYS: int = 30
"""Unmerged branches with a commit newer than this are never deleted."""
DEFAULT_KEEP_OPTION = ",".join(DEFAULT_KEEP)
"""The default keep list as the ``--keep`` option spells it."""
TRIM_LOG_NAME: str = "trim-log.jsonl"
"""Name of the undo log inside the repository's common git directory."""

# Options that make diff output independent of the user's configuration, so
# patch-ids computed for the branch and for the mainline are comparable and no
# change is hidden from them (ignored submodules, external diff drivers, ...).
_DIFF_OPTS: tuple[str, ...] = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--full-index",
    "--submodule=short",
    "--ignore-submodules=none",
)
# Configuration forced on every git call: with ``diff.relative`` a diff made
# in a subdirectory would silently leave out changes elsewhere in the tree.
_GIT_CONFIG: tuple[str, ...] = ("-c", "diff.relative=false")

# Quiet when imported as a library; the CLI callback re-enables logging.
logger.disable(__name__)


# ---------------------------------------------------------------------------
# Git plumbing (thin subprocess wrapper - argv lists only, never a shell)
# ---------------------------------------------------------------------------


class GitError(RuntimeError):
    """Raised when a git command fails or the repository is not usable."""


def _git_env() -> dict[str, str]:
    """Return the environment used for every git subprocess.

    Returns:
        A copy of the current environment with the locale forced to ``C`` so
        git's messages are stable and untranslated.
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANGUAGE"] = "C"
    return env


def run_git(
    repo: Path,
    args: Sequence[str],
    *,
    check: bool = True,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` inside *repo*.

    Args:
        repo: Directory to run in (anywhere inside the repository).
        args: Git subcommand and arguments, one argv entry each.
        check: Raise :class:`GitError` when git exits non-zero.
        stdin: Text to feed to git's standard input, if any.

    Returns:
        The completed process; ``stdout``/``stderr`` are decoded as UTF-8
        (undecodable bytes are replaced, never fatal).

    Raises:
        GitError: If git cannot be started, or fails while *check* is true.
    """
    logger.debug("$ git {}", " ".join(args))
    try:
        proc = subprocess.run(
            ["git", *_GIT_CONFIG, *args],
            cwd=repo,
            input=stdin,
            stdin=subprocess.DEVNULL if stdin is None else None,
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
        raise GitError(f"git {' '.join(args)} failed (exit {proc.returncode}): {message}")
    return proc


def _lines(text: str) -> list[str]:
    """Split *text* into its non-empty lines.

    Args:
        text: Raw command output.

    Returns:
        The non-blank lines, stripped of surrounding whitespace.
    """
    return [line.strip() for line in text.splitlines() if line.strip()]


def ensure_repository(repo: Path) -> None:
    """Check that *repo* is inside a git repository.

    Args:
        repo: Directory to check.

    Raises:
        GitError: If *repo* does not exist or is not inside a repository.
    """
    if not repo.is_dir():
        raise GitError(f"{repo} is not a directory")
    if run_git(repo, ["rev-parse", "--git-dir"], check=False).returncode != 0:
        raise GitError(f"{repo} is not inside a git repository")


def git_common_dir(repo: Path) -> Path:
    """Return the repository's common git directory as an absolute path.

    The common directory is shared by every worktree, which is why the undo
    log lives there rather than in a per-worktree git directory.

    Args:
        repo: Directory inside the repository.

    Returns:
        Absolute path of the common git directory (usually ``<root>/.git``).

    Raises:
        GitError: If *repo* is not inside a git repository.
    """
    raw = run_git(repo, ["rev-parse", "--git-common-dir"]).stdout.strip()
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def is_shallow(repo: Path) -> bool:
    """Tell whether *repo* is a shallow clone.

    Args:
        repo: Directory inside the repository.

    Returns:
        True when history is truncated, in which case patch-ids cannot be
        trusted and non-ancestor branches are reported as ``unknown``.
    """
    out = run_git(repo, ["rev-parse", "--is-shallow-repository"]).stdout.strip()
    return out == "true"


def _patch_id_pipeline(
    repo: Path,
    producer_args: Sequence[str],
    stdin_lines: Iterable[str] | None = None,
) -> list[tuple[str, str]]:
    """Run ``git <producer_args> | git patch-id --stable``.

    The diff is piped between the two git processes at OS level; it never
    passes through Python, so huge or non-UTF-8 diffs are not a problem.

    Args:
        repo: Directory inside the repository.
        producer_args: Arguments of the git command that prints the patches.
        stdin_lines: Lines for the producer's standard input (``--stdin``).

    Returns:
        ``(patch_id, commit)`` pairs in output order.  Patches that are not
        attached to a commit carry an all-zero commit id.

    Raises:
        GitError: If either process cannot be started or fails.
    """
    logger.debug("$ git {} | git patch-id --stable", " ".join(producer_args))
    env = _git_env()
    with (
        tempfile.TemporaryFile() as stdin_file,
        tempfile.TemporaryFile() as producer_err,
    ):
        if stdin_lines is not None:
            stdin_file.write("".join(f"{line}\n" for line in stdin_lines).encode("utf-8"))
            stdin_file.seek(0)
        try:
            producer = subprocess.Popen(
                ["git", *_GIT_CONFIG, *producer_args],
                cwd=repo,
                stdin=stdin_file,
                stdout=subprocess.PIPE,
                stderr=producer_err,
                env=env,
            )
        except OSError as exc:
            raise GitError(f"cannot run git in {repo}: {exc}") from exc
        assert producer.stdout is not None  # stdout=PIPE above
        try:
            consumer = subprocess.Popen(
                ["git", "patch-id", "--stable"],
                cwd=repo,
                stdin=producer.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            producer.kill()
            producer.wait()
            raise GitError(f"cannot run git patch-id in {repo}: {exc}") from exc
        finally:
            # The consumer holds its own copy; closing ours lets the producer
            # receive SIGPIPE if the consumer exits early.
            producer.stdout.close()
        out, err = consumer.communicate()
        producer_code = producer.wait()
        producer_err.seek(0)
        producer_message = producer_err.read().decode("utf-8", "replace").strip()
    if producer_code != 0:
        raise GitError(f"git {' '.join(producer_args)} failed (exit {producer_code}): {producer_message}")
    if consumer.returncode != 0:
        message = err.decode("utf-8", "replace").strip()
        raise GitError(f"git patch-id failed (exit {consumer.returncode}): {message}")
    pairs: list[tuple[str, str]] = []
    for line in out.decode("ascii", "replace").splitlines():
        parts = line.split()
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


# ---------------------------------------------------------------------------
# Refs
# ---------------------------------------------------------------------------

_FOR_EACH_REF_FORMAT = "%(refname)%00%(objectname)%00%(committerdate:iso-strict)%00%(upstream)%00%(upstream:track)"
_AHEAD_RE = re.compile(r"ahead (\d+)")
_BEHIND_RE = re.compile(r"behind (\d+)")


@dataclass(frozen=True)
class Branch:
    """A local branch and the metadata needed to judge it.

    Attributes:
        name: Branch name without the ``refs/heads/`` prefix.
        sha: Full commit id of the branch tip.
        last_commit_at: Committer date of the tip (timezone-aware).
        upstream: Full refname of the configured upstream, or ``""``.
        upstream_gone: True when the upstream is configured but its ref no
            longer exists (deleted on the remote and pruned locally).
        ahead: Number of commits on the branch that are not on its upstream.
        behind: Number of upstream commits that are not on the branch.
    """

    name: str
    sha: str
    last_commit_at: datetime
    upstream: str = ""
    upstream_gone: bool = False
    ahead: int = 0
    behind: int = 0

    @property
    def ref(self) -> str:
        """Full refname of the branch (``refs/heads/<name>``)."""
        return f"refs/heads/{self.name}"

    @property
    def upstream_short(self) -> str:
        """Upstream as people write it (``origin/feat/x``), or ``""``."""
        for prefix in ("refs/remotes/", "refs/heads/"):
            if self.upstream.startswith(prefix):
                return self.upstream.removeprefix(prefix)
        return self.upstream


def list_branches(repo: Path) -> list[Branch]:
    """List every local branch of *repo*.

    Args:
        repo: Directory inside the repository.

    Returns:
        One :class:`Branch` per local branch, sorted by name.

    Raises:
        GitError: If the refs cannot be listed.
    """
    out = run_git(repo, ["for-each-ref", f"--format={_FOR_EACH_REF_FORMAT}", "refs/heads/"]).stdout
    branches: list[Branch] = []
    for line in out.splitlines():
        parts = line.split("\x00")
        if len(parts) != 5:
            continue
        refname, sha, date, upstream, track = parts
        ahead = _AHEAD_RE.search(track)
        behind = _BEHIND_RE.search(track)
        branches.append(
            Branch(
                name=refname.removeprefix("refs/heads/"),
                sha=sha,
                last_commit_at=datetime.fromisoformat(date),
                upstream=upstream,
                upstream_gone="gone" in track,
                ahead=int(ahead.group(1)) if ahead else 0,
                behind=int(behind.group(1)) if behind else 0,
            )
        )
    return sorted(branches, key=lambda branch: branch.name)


def current_branch(repo: Path) -> str | None:
    """Return the branch checked out in *repo*.

    Args:
        repo: Directory inside the repository (or one of its worktrees).

    Returns:
        The branch name, or ``None`` when HEAD is detached.
    """
    proc = run_git(repo, ["symbolic-ref", "--quiet", "HEAD"], check=False)
    ref = proc.stdout.strip()
    if proc.returncode != 0 or not ref.startswith("refs/heads/"):
        return None
    return ref.removeprefix("refs/heads/")


# Files in a worktree's git directory that name the branch an unfinished
# operation will move or return to, although HEAD is detached meanwhile.
_OPERATION_FILES: tuple[tuple[str, str], ...] = (
    ("rebase-merge/head-name", "being rebased"),
    ("rebase-apply/head-name", "being rebased"),
    ("BISECT_START", "being bisected"),
)


def _operation_branches(worktree: Path) -> list[tuple[str, str]]:
    """Find the branches an unfinished rebase or bisect in *worktree* is using.

    Args:
        worktree: Root directory of a worktree.

    Returns:
        ``(branch name, state)`` pairs, e.g. ``("feat/x", "being rebased")``.
        Empty when nothing is in progress or the worktree is unreadable.
    """
    if not worktree.is_dir():
        return []
    proc = run_git(worktree, ["rev-parse", "--absolute-git-dir"], check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    git_dir = Path(proc.stdout.strip())
    found: list[tuple[str, str]] = []
    for relative, state in _OPERATION_FILES:
        try:
            content = (git_dir / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name = content.strip().removeprefix("refs/heads/")
        if name:
            found.append((name, state))
    return found


def worktree_branches(repo: Path) -> dict[str, str]:
    """Map every branch that a worktree is using to a description of the use.

    Besides plain checkouts this finds branches ``git branch`` does not show
    as checked out: during a rebase or a bisect HEAD is detached, but the
    worktree moves the branch (or returns to it) when the operation ends.

    Args:
        repo: Directory inside the repository.

    Returns:
        ``{branch name: "checked out in worktree <path>"}`` (or ``"being
        rebased in ..."``, ``"being bisected in ..."``) for all worktrees,
        including the main one.  Deleting any of these branches would break
        a checkout.
    """
    out = run_git(repo, ["worktree", "list", "--porcelain"]).stdout
    held: dict[str, str] = {}
    paths: list[str] = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            paths.append(line.removeprefix("worktree "))
        elif line.startswith("branch refs/heads/") and paths:
            held[line.removeprefix("branch refs/heads/")] = f"checked out in worktree {paths[-1]}"
    for path in paths:
        for name, state in _operation_branches(Path(path)):
            held.setdefault(name, f"{state} in worktree {path}")
    return held


def superproject_pins(repo: Path) -> tuple[str, list[str]] | None:
    """Find the commits a superproject pins when *repo* is a submodule.

    A superproject records one commit of each submodule.  Nothing in the
    submodule's own refs shows that, so a branch could look disposable while
    it is the only thing keeping the pinned commit alive.

    Args:
        repo: Directory inside the repository.

    Returns:
        ``(superproject path, pinned commit ids)`` - the commits recorded in
        the superproject's ``HEAD`` and index - or ``None`` when *repo* is
        not a submodule.
    """
    proc = run_git(repo, ["rev-parse", "--show-superproject-working-tree"], check=False)
    superproject = proc.stdout.strip()
    if proc.returncode != 0 or not superproject:
        return None
    top = run_git(repo, ["rev-parse", "--show-toplevel"], check=False).stdout.strip()
    if not top:
        return None
    top_parts = Path(top).resolve().parts
    sup_parts = Path(superproject).resolve().parts
    common = 0
    for a, b in zip(top_parts, sup_parts):
        if a != b:
            break
        common += 1
    rel_parts = ("..",) * (len(sup_parts) - common) + top_parts[common:]
    relative = Path(*rel_parts).as_posix() if rel_parts else "."
    pins: list[str] = []
    for spec in (f"HEAD:{relative}", f":{relative}"):
        found = run_git(Path(superproject), ["rev-parse", "--verify", "--quiet", spec], check=False)
        sha = found.stdout.strip()
        if found.returncode == 0 and sha and sha not in pins:
            pins.append(sha)
    return superproject, pins


def _pinned_branches(repo: Path, mainline: Mainline) -> dict[str, str]:
    """Find branches that keep a superproject-pinned commit reachable.

    Args:
        repo: Directory inside the repository.
        mainline: The mainline being compared against.

    Returns:
        ``{branch name: reason}`` for every local branch containing a pinned
        commit that is not on the mainline.  Empty outside submodules.
    """
    pinned: dict[str, str] = {}
    found = superproject_pins(repo)
    if found is None:
        return pinned
    superproject, pins = found
    for sha in pins:
        on_mainline = run_git(repo, ["merge-base", "--is-ancestor", sha, mainline.sha], check=False)
        if on_mainline.returncode == 0:
            continue
        refs = run_git(
            repo,
            ["for-each-ref", f"--contains={sha}", "--format=%(refname)", "refs/heads/"],
            check=False,
        )
        for ref in _lines(refs.stdout):
            pinned.setdefault(
                ref.removeprefix("refs/heads/"),
                f"superproject {superproject} pins {sha[:8]}, which is not on {mainline.label}",
            )
    return pinned


@dataclass(frozen=True)
class Mainline:
    """The line of development that branches are compared against.

    Attributes:
        label: Name to show to the user (``main``, ``origin/main``, ...).
        sha: Commit id the mainline points at.
        branch: Local branch name when the mainline is a local branch (that
            branch is never reported or deleted), otherwise ``None``.
    """

    label: str
    sha: str
    branch: str | None


def resolve_mainline(repo: Path, requested: str | None, branches: Sequence[Branch]) -> Mainline:
    """Work out which mainline to compare against.

    Args:
        repo: Directory inside the repository.
        requested: Value of ``--main``: a local branch or any commit-ish such
            as ``origin/main``.  ``None`` means auto-detect: the branch that
            ``origin/HEAD`` points at, then ``main``, then ``master``.
        branches: All local branches.

    Returns:
        The resolved :class:`Mainline`.

    Raises:
        GitError: If *requested* does not exist, or nothing can be detected.
    """
    by_name = {branch.name: branch for branch in branches}
    if requested:
        if requested in by_name:
            return Mainline(requested, by_name[requested].sha, requested)
        proc = run_git(
            repo,
            ["rev-parse", "--verify", "--quiet", f"{requested}^{{commit}}"],
            check=False,
        )
        sha = proc.stdout.strip()
        if proc.returncode == 0 and sha:
            return Mainline(requested, sha, None)
        raise GitError(f"mainline {requested!r} is neither a local branch nor a known commit")

    candidates: list[str] = []
    proc = run_git(repo, ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], check=False)
    target = proc.stdout.strip()
    if proc.returncode == 0 and target.startswith("refs/remotes/origin/"):
        candidates.append(target.removeprefix("refs/remotes/origin/"))
    for fallback in ("main", "master"):
        if fallback not in candidates:
            candidates.append(fallback)
    for name in candidates:
        if name in by_name:
            logger.debug("mainline auto-detected: {}", name)
            return Mainline(name, by_name[name].sha, name)
    raise GitError(f"cannot find a mainline branch (tried {', '.join(candidates)}); pass --main <branch>")


# ---------------------------------------------------------------------------
# Merge detection
# ---------------------------------------------------------------------------


class Classification(StrEnum):
    """How a branch relates to the mainline."""

    MERGED = "merged"
    """All work of the branch is on the mainline."""
    UNMERGED = "unmerged"
    """Some work of the branch was not found on the mainline."""
    UNKNOWN = "unknown"
    """Cannot tell - never treated as safe."""


class Method(StrEnum):
    """How a merged branch was recognised."""

    ANCESTOR = "ancestor"
    """The tip is reachable from the mainline (plain merge / fast-forward)."""
    PATCH_ID = "patch-id"
    """Every commit has a patch-equivalent commit on the mainline."""
    SQUASH = "squash"
    """The combined diff of the branch equals one mainline commit."""


@dataclass(frozen=True)
class Verdict:
    """Result of classifying one branch.

    Attributes:
        classification: Merged, unmerged or unknown.
        method: How the merge was detected (``None`` unless merged).
        detail: One-line human-readable explanation.
    """

    classification: Classification
    method: Method | None = None
    detail: str = ""


@dataclass
class _BranchSide:
    """The part of a branch's history that is not on the mainline.

    Attributes:
        commits: Non-merge commits ahead of the mainline.
        merges: Merge commits ahead of the mainline.
        boundaries: Mainline commits the branch grew from: its fork point and
            every mainline commit that was merged into it.  Empty for
            branches with unrelated history.
    """

    commits: list[str] = field(default_factory=list)
    merges: list[str] = field(default_factory=list)
    boundaries: set[str] = field(default_factory=set)


def _branch_side_graph(repo: Path, tips: Sequence[str], mainline_sha: str) -> dict[str, tuple[str, ...]]:
    """Load the commit graph reachable from *tips* but not from the mainline.

    Args:
        repo: Directory inside the repository.
        tips: Branch tip commit ids.
        mainline_sha: Commit id of the mainline.

    Returns:
        ``{commit: parents}`` for every commit that is not on the mainline.
        Parents missing from the mapping are mainline commits.
    """
    stdin = "".join(f"{tip}\n" for tip in tips) + f"^{mainline_sha}\n"
    out = run_git(repo, ["rev-list", "--parents", "--stdin"], stdin=stdin).stdout
    graph: dict[str, tuple[str, ...]] = {}
    for line in out.splitlines():
        sha, *parents = line.split()
        graph[sha] = tuple(parents)
    return graph


def _walk_branch_side(graph: dict[str, tuple[str, ...]], tip: str) -> _BranchSide:
    """Collect the commits of one branch that are not on the mainline.

    Args:
        graph: Graph from :func:`_branch_side_graph`.
        tip: Tip of the branch (must be a key of *graph*).

    Returns:
        The branch's own commits, split into merges and non-merges.
    """
    side = _BranchSide()
    seen = {tip}
    stack = [tip]
    while stack:
        commit = stack.pop()
        parents = graph[commit]
        (side.merges if len(parents) > 1 else side.commits).append(commit)
        for parent in parents:
            if parent not in graph:
                side.boundaries.add(parent)
            elif parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return side


def _reachable_within(graph: dict[str, tuple[str, ...]], start: str) -> set[str]:
    """Collect the commits of *graph* that are reachable from *start*.

    Args:
        graph: Graph from :func:`_branch_side_graph`.
        start: Commit to start from (must be a key of *graph*).

    Returns:
        *start* and all of its ancestors that are not on the mainline.
    """
    seen = {start}
    stack = [start]
    while stack:
        for parent in graph[stack.pop()]:
            if parent in graph and parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return seen


def _squash_heads(graph: dict[str, tuple[str, ...]], tip: str, uncovered: set[str]) -> list[str]:
    """List the commits whose combined diff may have been squashed.

    The obvious candidate is the tip.  The second one covers a branch that
    was squash-merged and *then* had the mainline merged back into it: walk
    the first parents from the tip, skipping commits that need no
    explanation (clean merges, empty commits, commits found on the mainline)
    until the first commit that does.  That commit only qualifies if every
    unexplained commit is part of its history.

    Args:
        graph: Graph from :func:`_branch_side_graph`.
        tip: Tip of the branch.
        uncovered: Commits whose changes were not found on the mainline.

    Returns:
        Candidate heads, the tip first.
    """
    heads = [tip]
    commit: str | None = tip
    while commit is not None and commit in graph and commit not in uncovered:
        parents = graph[commit]
        commit = parents[0] if parents else None
    if commit is not None and commit in graph and commit != tip and uncovered <= _reachable_within(graph, commit):
        heads.append(commit)
    return heads


def _commit_patch_ids(repo: Path, commits: Sequence[str]) -> dict[str, str]:
    """Compute the patch-id of each commit in *commits*.

    Args:
        repo: Directory inside the repository.
        commits: Non-merge commit ids.

    Returns:
        ``{commit: patch_id}``.  Commits without file changes (empty commits)
        have no patch-id and are absent from the mapping.
    """
    if not commits:
        return {}
    pairs = _patch_id_pipeline(
        repo,
        [
            "log",
            "--no-walk",
            "--no-merges",
            "-p",
            "--format=commit %H",
            *_DIFF_OPTS,
            "--stdin",
        ],
        stdin_lines=commits,
    )
    return {commit: patch_id for patch_id, commit in pairs}


def _combined_patch_id(repo: Path, mainline_sha: str, head: str) -> str | None:
    """Compute the patch-id of a branch's whole diff (merge-base to *head*).

    Args:
        repo: Directory inside the repository.
        mainline_sha: Commit id of the mainline.
        head: Commit on the branch, usually its tip.

    Returns:
        The patch-id, or ``None`` when there are no net changes.
    """
    pairs = _patch_id_pipeline(repo, ["diff", *_DIFF_OPTS, f"{mainline_sha}...{head}"])
    return pairs[0][0] if pairs else None


def _merges_with_own_changes(repo: Path, merges: Sequence[str]) -> set[str]:
    """Find merge commits that introduce changes of their own.

    A clean merge only combines its parents, so its dense combined diff
    (``--cc``) is empty.  Anything else is work that exists only in the merge
    commit - which commit-by-commit patch-id comparison cannot see.

    Args:
        repo: Directory inside the repository.
        merges: Merge commit ids to inspect.

    Returns:
        The subset of *merges* whose combined diff is not empty.
    """
    if not merges:
        return set()
    out = run_git(
        repo,
        [
            "log",
            "--no-walk",
            "--cc",
            "-p",
            "--format=commit %H",
            *_DIFF_OPTS,
            "--stdin",
        ],
        stdin="".join(f"{merge}\n" for merge in merges),
    ).stdout
    dirty: set[str] = set()
    current = ""
    for line in out.splitlines():
        if line.startswith("commit "):
            current = line.removeprefix("commit ").strip()
        elif line.startswith(("diff --cc ", "diff --combined ")) and current:
            dirty.add(current)
    return dirty


class _MainlineIndex:
    """Patch-ids of the mainline, computed once per invocation.

    The mainline is walked from the oldest fork point of all the branches
    being compared, so 200 branches cost one history walk instead of 200.
    """

    def __init__(self, repo: Path, mainline_sha: str, boundaries: Sequence[str]) -> None:
        """Walk the mainline and index its commits by patch-id.

        Args:
            repo: Directory inside the repository.
            mainline_sha: Commit id of the mainline.
            boundaries: Mainline commits the compared branches grew from
                (see :class:`_BranchSide`); history older than their common
                ancestor cannot hold a match and is not walked.
        """
        self._repo = repo
        self._by_patch_id: dict[str, list[str]] = {}
        self._reachable: dict[tuple[str, str], bool] = {}
        proc = run_git(repo, ["merge-base", "--octopus", mainline_sha, *boundaries], check=False)
        roots = _lines(proc.stdout) if proc.returncode == 0 else []
        if not roots:
            logger.debug("no common fork point; walking the whole mainline history")
        pairs = _patch_id_pipeline(
            repo,
            [
                "log",
                "--no-merges",
                "-p",
                "--format=commit %H",
                *_DIFF_OPTS,
                mainline_sha,
                *[f"^{root}" for root in roots],
            ],
        )
        for patch_id, commit in pairs:
            self._by_patch_id.setdefault(patch_id, []).append(commit)
        logger.debug("indexed {} mainline commit(s) by patch-id", len(pairs))

    def find(self, patch_id: str, source: str) -> str | None:
        """Find a mainline commit equivalent to *patch_id*.

        A mainline commit that is an ancestor of *source* does not count:
        the patch was written on top of it, so it re-applies something the
        mainline must have undone in between (a revert) and is new work.

        Args:
            patch_id: Patch-id to look for.
            source: The branch commit the patch comes from.

        Returns:
            A mainline commit with that patch-id that is not part of the
            history of *source*, or ``None``.
        """
        for commit in self._by_patch_id.get(patch_id, ()):
            if not self._is_reachable(commit, source):
                return commit
        return None

    def _is_reachable(self, commit: str, source: str) -> bool:
        """Tell whether *commit* is an ancestor of *source* (cached).

        Args:
            commit: Candidate ancestor.
            source: Commit whose history is searched.

        Returns:
            True when *commit* is reachable from *source*.

        Raises:
            GitError: If git cannot answer.
        """
        key = (commit, source)
        if key not in self._reachable:
            proc = run_git(self._repo, ["merge-base", "--is-ancestor", commit, source], check=False)
            if proc.returncode not in (0, 1):
                raise GitError(f"git merge-base --is-ancestor failed: {proc.stderr.strip()}")
            self._reachable[key] = proc.returncode == 0
        return self._reachable[key]


def _plural(count: int, noun: str) -> str:
    """Format *count* with *noun*, adding ``s`` or ``es`` when needed.

    Args:
        count: The quantity.
        noun: Singular noun.

    Returns:
        E.g. ``"1 commit"``, ``"3 commits"`` or ``"2 branches"``.
    """
    if count == 1:
        return f"{count} {noun}"
    return f"{count} {noun}{'es' if noun.endswith(('s', 'x', 'ch', 'sh')) else 's'}"


def _judge(
    repo: Path,
    branch: Branch,
    side: _BranchSide,
    graph: dict[str, tuple[str, ...]],
    branch_ids: dict[str, str],
    dirty_merges: set[str],
    index: _MainlineIndex,
    mainline: Mainline,
) -> Verdict:
    """Decide whether the work of *branch* landed on the mainline.

    Args:
        repo: Directory inside the repository.
        branch: Branch being judged.
        side: Its commits that are not on the mainline.
        graph: Graph from :func:`_branch_side_graph`.
        branch_ids: Patch-ids of branch-side commits.
        dirty_merges: Merge commits that carry changes of their own.
        index: Mainline patch-id index.
        mainline: The mainline being compared against.

    Returns:
        The verdict for the branch.
    """
    label = mainline.label
    with_changes = [commit for commit in side.commits if commit in branch_ids]
    if not with_changes:
        return Verdict(Classification.UNKNOWN, None, f"commits ahead of {label} change no files")

    # 1. Commit by commit: rebase-merges, cherry-picks, one-commit squashes.
    missing = [c for c in with_changes if index.find(branch_ids[c], c) is None]
    evil = [merge for merge in side.merges if merge in dirty_merges]
    if not missing and not evil:
        return Verdict(
            Classification.MERGED,
            Method.PATCH_ID,
            f"{_plural(len(with_changes), 'commit')} found on {label} by patch-id",
        )

    # 2. The concatenated diff: multi-commit squashes.  A lone commit without
    #    merges has the same diff as the whole branch, so skip the repeat.
    if len(side.commits) > 1 or side.merges:
        for head in _squash_heads(graph, branch.sha, {*missing, *evil}):
            combined = _combined_patch_id(repo, mainline.sha, head)
            match = index.find(combined, head) if combined else None
            if match:
                return Verdict(
                    Classification.MERGED,
                    Method.SQUASH,
                    f"combined diff equals {match[:8]} on {label}",
                )

    if missing:
        detail = f"{len(missing)} of {_plural(len(with_changes), 'commit')} not on {label}"
    else:
        detail = f"a merge commit carries changes that are not on {label}"
    return Verdict(Classification.UNMERGED, None, detail)


def classify_branches(
    repo: Path,
    branches: Sequence[Branch],
    mainline: Mainline,
    *,
    shallow: bool = False,
) -> dict[str, Verdict]:
    """Classify *branches* against *mainline*.

    Args:
        repo: Directory inside the repository.
        branches: Branches to classify.
        mainline: The mainline to compare against.
        shallow: True for shallow clones; only the ancestor check is trusted
            there and every other branch becomes ``unknown``.

    Returns:
        ``{branch name: verdict}`` for every branch in *branches*.

    Raises:
        GitError: If a git command fails.
    """
    verdicts: dict[str, Verdict] = {}
    if not branches:
        return verdicts
    label = mainline.label

    # 1. Plain merges: one git call for all branches.
    merged_refs = set(
        _lines(
            run_git(
                repo,
                [
                    "for-each-ref",
                    f"--merged={mainline.sha}",
                    "--format=%(refname)",
                    "refs/heads/",
                ],
            ).stdout
        )
    )
    pending: list[Branch] = []
    for branch in branches:
        if branch.ref in merged_refs:
            verdicts[branch.name] = Verdict(Classification.MERGED, Method.ANCESTOR, f"tip is reachable from {label}")
        elif shallow:
            verdicts[branch.name] = Verdict(Classification.UNKNOWN, None, "shallow clone: history is incomplete")
        else:
            pending.append(branch)
    if not pending:
        return verdicts

    # 2. Which commits does each remaining branch have that the mainline lacks?
    graph = _branch_side_graph(repo, [branch.sha for branch in pending], mainline.sha)
    sides = {branch.name: _walk_branch_side(graph, branch.sha) for branch in pending}
    comparable: list[Branch] = []
    for branch in pending:
        side = sides[branch.name]
        if not side.boundaries:
            verdicts[branch.name] = Verdict(Classification.UNKNOWN, None, f"no common history with {label}")
        elif not side.commits:
            verdicts[branch.name] = Verdict(Classification.UNKNOWN, None, f"only merge commits ahead of {label}")
        else:
            comparable.append(branch)
    if not comparable:
        return verdicts

    # 3. Patch-ids: one walk for all branch commits, one for the mainline.
    commits = sorted({commit for branch in comparable for commit in sides[branch.name].commits})
    merges = sorted({merge for branch in comparable for merge in sides[branch.name].merges})
    branch_ids = _commit_patch_ids(repo, commits)
    dirty_merges = _merges_with_own_changes(repo, merges)
    boundaries = sorted({b for branch in comparable for b in sides[branch.name].boundaries})
    index = _MainlineIndex(repo, mainline.sha, boundaries)

    for branch in comparable:
        verdict = _judge(
            repo,
            branch,
            sides[branch.name],
            graph,
            branch_ids,
            dirty_merges,
            index,
            mainline,
        )
        logger.debug("{}: {} ({})", branch.name, verdict.classification.value, verdict.detail)
        verdicts[branch.name] = verdict
    return verdicts


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


@dataclass
class BranchReport:
    """Everything ``git-trim`` knows about one branch.

    Attributes:
        branch: The branch that was examined.
        verdict: Its merge classification.
        age_days: Whole days since the tip was committed.
        safe: True when the branch may be deleted.
        reasons: Why deletion is refused (empty when safe).
        warnings: Things worth knowing that do not block deletion.
    """

    branch: Branch
    verdict: Verdict
    age_days: int
    safe: bool = False
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def classification(self) -> Classification:
        """Shortcut for ``verdict.classification``."""
        return self.verdict.classification

    @property
    def method(self) -> Method | None:
        """Shortcut for ``verdict.method``."""
        return self.verdict.method

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable description of this report.

        Returns:
            A dict with the branch, its merge status and the safety verdict.
        """
        merged = {
            Classification.MERGED: True,
            Classification.UNMERGED: False,
            Classification.UNKNOWN: None,
        }[self.classification]
        return {
            "branch": self.branch.name,
            "sha": self.branch.sha,
            "last_commit": self.branch.last_commit_at.isoformat(),
            "age_days": self.age_days,
            "upstream": self.branch.upstream_short or None,
            "upstream_gone": self.branch.upstream_gone,
            "unpushed_commits": self.branch.ahead,
            "classification": self.classification.value,
            "merged": merged,
            "method": self.method.value if self.method else None,
            "detail": self.verdict.detail,
            "safe": self.safe,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }


def parse_keep(value: str) -> list[str]:
    """Split a comma-separated ``--keep`` value.

    Args:
        value: Raw option value, e.g. ``"main,develop,release/*"``.

    Returns:
        The non-empty entries with surrounding whitespace removed.
    """
    return [entry.strip() for entry in value.split(",") if entry.strip()]


def matches_keep(name: str, keep: Iterable[str]) -> bool:
    """Tell whether *name* is protected by a keep list.

    Entries are exact names or ``fnmatch`` globs such as ``release/*``.
    Matching ignores case, so ``Main`` protects ``main`` on case-insensitive
    filesystems.

    Args:
        name: Branch name.
        keep: Keep entries.

    Returns:
        True when the branch must be kept.
    """
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, entry.strip().lower()) for entry in keep if entry.strip())


def assess_safety(
    report: BranchReport,
    *,
    current: str | None,
    in_use: dict[str, str],
    keep: Iterable[str],
    stale_days: int,
    dependents: Sequence[str] = (),
    pinned: str | None = None,
) -> None:
    """Fill in ``safe``, ``reasons`` and ``warnings`` of *report* in place.

    Args:
        report: Report to complete.
        current: Branch checked out where git-trim runs, if any.
        in_use: Branches used by any worktree, from :func:`worktree_branches`.
        keep: Protected names/globs.
        stale_days: Unmerged branches younger than this are never safe.
        dependents: Local branches whose upstream is this branch.
        pinned: Why a superproject needs this branch, if it does.
    """
    branch = report.branch
    reasons: list[str] = []
    warnings: list[str] = []

    if branch.name == current:
        reasons.append("current branch")
    elif branch.name in in_use:
        reasons.append(in_use[branch.name])
    if matches_keep(branch.name, keep):
        reasons.append("protected by --keep")
    if pinned:
        reasons.append(pinned)

    if report.classification is Classification.UNKNOWN:
        reasons.append(f"merge status unknown ({report.verdict.detail})")
    elif report.classification is Classification.UNMERGED:
        if not branch.upstream.startswith("refs/remotes/"):
            reasons.append("unmerged and has no remote upstream")
        elif branch.upstream_gone:
            reasons.append(f"unmerged and its upstream {branch.upstream_short} is gone")
        elif branch.ahead:
            reasons.append(f"unmerged with {_plural(branch.ahead, 'commit')} not pushed to {branch.upstream_short}")
        if report.age_days < stale_days:
            reasons.append(f"unmerged and active (last commit {report.age_days}d ago, --stale {stale_days})")
        if not reasons:
            warnings.append(
                f"not merged: once deleted, the work exists only on {branch.upstream_short} "
                f"(prune-remote checks that it is still there)"
            )

    if dependents:
        warnings.append(f"deleting it leaves {', '.join(dependents)} without an upstream")

    report.reasons = reasons
    report.warnings = warnings
    report.safe = not reasons


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


@dataclass
class ScanResult:
    """Outcome of scanning a repository.

    Attributes:
        mainline: Label of the mainline that was compared against.
        shallow: True when the repository is a shallow clone.
        stale_days: The ``--stale`` threshold that was applied.
        reports: One report per examined branch, sorted by name.
    """

    mainline: str
    shallow: bool
    stale_days: int
    reports: list[BranchReport] = field(default_factory=list)

    @property
    def candidates(self) -> list[BranchReport]:
        """Reports of the branches that are safe to delete."""
        return [report for report in self.reports if report.safe]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable description of the scan.

        Returns:
            A dict with scan metadata and one entry per branch.
        """
        return {
            "mainline": self.mainline,
            "shallow": self.shallow,
            "stale_days": self.stale_days,
            "candidates": len(self.candidates),
            "branches": [report.to_dict() for report in self.reports],
        }


def scan_branches(
    repo: Path,
    *,
    mainline: str | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
    keep: Iterable[str] = DEFAULT_KEEP,
    pattern: str | None = None,
    now: datetime | None = None,
) -> ScanResult:
    """Examine every local branch of *repo* except the mainline.

    This function only reads from the repository.

    Args:
        repo: Directory inside the repository.
        mainline: Mainline branch or commit-ish; ``None`` auto-detects.
        stale_days: Unmerged branches with a commit newer than this many days
            are never safe to delete.
        keep: Protected branch names or globs (case-insensitive).
        pattern: Only examine branches matching this ``fnmatch`` glob.
        now: Reference time for age computation (defaults to the present).

    Returns:
        The scan result, with one report per examined branch.

    Raises:
        GitError: If *repo* is not a repository or the mainline is unknown.
        ValueError: If *stale_days* is negative.
    """
    if stale_days < 0:
        raise ValueError("stale_days must not be negative")
    ensure_repository(repo)
    keep = list(keep)
    branches = list_branches(repo)
    main = resolve_mainline(repo, mainline, branches)
    current = current_branch(repo)
    in_use = worktree_branches(repo)
    pinned = _pinned_branches(repo, main)
    shallow = is_shallow(repo)
    moment = now or datetime.now(UTC)

    # Local branches that track another local branch lose their upstream when
    # that branch is deleted.
    dependents: dict[str, list[str]] = {}
    for branch in branches:
        if branch.upstream.startswith("refs/heads/"):
            dependents.setdefault(branch.upstream.removeprefix("refs/heads/"), []).append(branch.name)

    examined = [
        branch
        for branch in branches
        if branch.name != main.branch and (not pattern or fnmatch.fnmatchcase(branch.name, pattern))
    ]
    verdicts = classify_branches(repo, examined, main, shallow=shallow)

    result = ScanResult(mainline=main.label, shallow=shallow, stale_days=stale_days)
    for branch in examined:
        report = BranchReport(
            branch=branch,
            verdict=verdicts[branch.name],
            age_days=max((moment - branch.last_commit_at).days, 0),
        )
        assess_safety(
            report,
            current=current,
            in_use=in_use,
            keep=keep,
            stale_days=stale_days,
            dependents=dependents.get(branch.name, ()),
            pinned=pinned.get(branch.name),
        )
        result.reports.append(report)
    return result


# ---------------------------------------------------------------------------
# Delete / undo
# ---------------------------------------------------------------------------


def trim_log_path(repo: Path) -> Path:
    """Return the path of the undo log (``.git/trim-log.jsonl``).

    Args:
        repo: Directory inside the repository.

    Returns:
        Absolute path of the log, shared by all worktrees.
    """
    return git_common_dir(repo) / TRIM_LOG_NAME


def _read_trim_log(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the undo log.

    Args:
        path: Location of the log.

    Returns:
        ``(entries, unreadable)``: the valid entries in file order and the
        raw lines that could not be understood (kept verbatim on rewrite).
    """
    entries: list[dict[str, Any]] = []
    unreadable: list[str] = []
    if not path.exists():
        return entries, unreadable
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            entry = None
        if isinstance(entry, dict) and isinstance(entry.get("branch"), str) and isinstance(entry.get("sha"), str):
            entries.append(entry)
        else:
            logger.warning("ignoring unreadable line in {}: {!r}", path, line)
            unreadable.append(line)
    return entries, unreadable


def _write_trim_log(path: Path, entries: Sequence[dict[str, Any]], unreadable: Sequence[str]) -> None:
    """Rewrite the undo log, or remove it when nothing is left.

    Args:
        path: Location of the log.
        entries: Entries to keep.
        unreadable: Raw lines to preserve verbatim.
    """
    if not entries and not unreadable:
        path.unlink(missing_ok=True)
        return
    lines = [*unreadable, *(json.dumps(entry, sort_keys=True) for entry in entries)]
    atomic_write(path, "".join(f"{line}\n" for line in lines), encoding="utf-8")


@dataclass
class DeleteOutcome:
    """Result of :func:`delete_branches`.

    Attributes:
        deleted: Reports of the branches that were deleted.
        failed: ``(report, error message)`` for branches git refused to
            delete or that moved since they were scanned.
        log_path: The undo log that was written to, if anything was deleted.
    """

    deleted: list[BranchReport] = field(default_factory=list)
    failed: list[tuple[BranchReport, str]] = field(default_factory=list)
    log_path: Path | None = None


def delete_branches(repo: Path, reports: Iterable[BranchReport]) -> DeleteOutcome:
    """Delete every *safe* branch in *reports*, logging each tip for undo.

    Reports that are not safe are ignored.  The undo-log entry is written
    before the branch is removed, so a crash can never lose a commit id.
    A branch that moved since it was scanned is left alone.

    Args:
        repo: Directory inside the repository.
        reports: Reports from :func:`scan_branches`.

    Returns:
        What was deleted and what failed.

    Raises:
        GitError: If *repo* is not a repository.
    """
    outcome = DeleteOutcome()
    targets = [report for report in reports if report.safe]
    if not targets:
        return outcome
    log_path = trim_log_path(repo)
    run_id = datetime.now(UTC).isoformat(timespec="microseconds")
    logged_failures: set[str] = set()

    for report in targets:
        branch = report.branch
        proc = run_git(repo, ["rev-parse", "--verify", "--quiet", branch.ref], check=False)
        tip = proc.stdout.strip()
        if proc.returncode != 0 or tip != branch.sha:
            outcome.failed.append((report, "branch changed since it was scanned"))
            continue
        entry = {
            "run": run_id,
            "branch": branch.name,
            "sha": branch.sha,
            "upstream": branch.upstream_short if not branch.upstream_gone else "",
            "classification": report.classification.value,
            "method": report.method.value if report.method else None,
            "deleted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        # -D, because git's own -d refuses squash-merged branches.
        proc = run_git(repo, ["branch", "-D", "--", branch.name], check=False)
        if proc.returncode == 0:
            outcome.deleted.append(report)
            outcome.log_path = log_path
            logger.info("deleted {} (was {})", branch.name, branch.sha[:8])
        else:
            logged_failures.add(branch.name)
            outcome.failed.append((report, proc.stderr.strip() or "git branch -D failed"))

    if logged_failures:
        # Drop the log entries of branches that were not deleted after all.
        entries, unreadable = _read_trim_log(log_path)
        kept = [entry for entry in entries if not (entry.get("run") == run_id and entry["branch"] in logged_failures)]
        _write_trim_log(log_path, kept, unreadable)
    return outcome


@dataclass
class UndoOutcome:
    """Result of :func:`undo_deletions`.

    Attributes:
        restored: ``(branch, sha)`` pairs that were recreated.
        skipped: Branches that already exist at the logged commit.
        failed: ``(branch, reason)`` pairs that could not be recreated; their
            log entries are kept so nothing is forgotten.
    """

    restored: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """True when there was nothing to undo."""
        return not (self.restored or self.skipped or self.failed)


def undo_deletions(repo: Path, *, all_runs: bool = False) -> UndoOutcome:
    """Recreate deleted branches from the undo log.

    By default only the branches of the most recent ``delete`` run are
    restored; calling again restores the run before it.  Restored entries
    are removed from the log.

    Args:
        repo: Directory inside the repository.
        all_runs: Restore every entry in the log, not just the last run.

    Returns:
        What was restored, skipped and what failed.

    Raises:
        GitError: If *repo* is not a repository.
    """
    ensure_repository(repo)
    outcome = UndoOutcome()
    log_path = trim_log_path(repo)
    entries, unreadable = _read_trim_log(log_path)
    if not entries:
        return outcome

    last_run = entries[-1].get("run")
    existing = {branch.name: branch.sha for branch in list_branches(repo)}
    remaining: list[dict[str, Any]] = []
    for entry in entries:
        if not all_runs and entry.get("run") != last_run:
            remaining.append(entry)
            continue
        name, sha = entry["branch"], entry["sha"]
        if name in existing:
            if existing[name] == sha:
                outcome.skipped.append(name)
            else:
                outcome.failed.append((name, f"exists at {existing[name][:8]}; the deleted tip was {sha}"))
                remaining.append(entry)
            continue
        proc = run_git(repo, ["branch", "--", name, sha], check=False)
        if proc.returncode != 0:
            outcome.failed.append((name, proc.stderr.strip() or "git branch failed"))
            remaining.append(entry)
            continue
        existing[name] = sha
        outcome.restored.append((name, sha))
        logger.info("restored {} at {}", name, sha[:8])
        upstream = entry.get("upstream")
        if isinstance(upstream, str) and upstream:
            tracked = run_git(
                repo,
                ["branch", f"--set-upstream-to={upstream}", "--", name],
                check=False,
            )
            if tracked.returncode != 0:
                logger.info("{}: upstream {} could not be restored", name, upstream)
    _write_trim_log(log_path, remaining, unreadable)
    return outcome


# ---------------------------------------------------------------------------
# prune-remote
# ---------------------------------------------------------------------------


@dataclass
class PruneOutcome:
    """Result of :func:`prune_remote`.

    Attributes:
        command: The git command that was (or would be) run.
        executed: False for a dry run.
        pruned: Remote-tracking branches that were (or would be) removed.
        orphaned: Local branches whose upstream is among *pruned*.
    """

    command: str
    executed: bool
    pruned: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)


def _tracking_refs(repo: Path, remote: str) -> set[str]:
    """List the remote-tracking branches of *remote*.

    Args:
        repo: Directory inside the repository.
        remote: Remote name.

    Returns:
        Branch names on the remote as known locally (without ``HEAD``).
    """
    prefix = f"refs/remotes/{remote}/"
    out = run_git(repo, ["for-each-ref", "--format=%(refname)", prefix]).stdout
    return {ref.removeprefix(prefix) for ref in _lines(out)} - {"HEAD"}


def prune_remote(repo: Path, remote: str = "origin", *, execute: bool = False) -> PruneOutcome:
    """Run ``git fetch --prune <remote>``, or preview what it would prune.

    git-trim never contacts a remote on its own; this makes the refresh an
    explicit step.  The dry run asks the remote for its branch list
    (``git ls-remote``) but changes nothing locally.

    Args:
        repo: Directory inside the repository.
        remote: Remote to refresh.
        execute: Actually fetch and prune; otherwise only report.

    Returns:
        The command and the remote-tracking branches affected.

    Raises:
        GitError: If the remote is unknown or cannot be reached.
    """
    ensure_repository(repo)
    if remote not in _lines(run_git(repo, ["remote"]).stdout):
        raise GitError(f"no remote named {remote!r}")
    outcome = PruneOutcome(command=f"git fetch --prune {remote}", executed=execute)
    before = _tracking_refs(repo, remote)
    if execute:
        run_git(repo, ["fetch", "--prune", "--", remote])
        pruned = before - _tracking_refs(repo, remote)
    else:
        out = run_git(repo, ["ls-remote", "--heads", "--", remote]).stdout
        on_remote = {line.split("\t", 1)[1].removeprefix("refs/heads/") for line in out.splitlines() if "\t" in line}
        pruned = before - on_remote
    outcome.pruned = sorted(pruned)
    stale_refs = {f"refs/remotes/{remote}/{name}" for name in pruned}
    outcome.orphaned = [b.name for b in list_branches(repo) if b.upstream in stale_refs]
    return outcome


# ---------------------------------------------------------------------------
# Rendering (rich)
# ---------------------------------------------------------------------------

_MERGED_CELL: dict[Classification, tuple[str, str]] = {
    Classification.MERGED: ("yes", "green"),
    Classification.UNMERGED: ("no", "yellow"),
    Classification.UNKNOWN: ("?", "magenta"),
}


def _say(console: Console, message: str, style: str = "") -> None:
    """Print one line of plain text without markup parsing or hard wrapping.

    Args:
        console: Console to print to.
        message: Text to print (branch names may contain ``[``).
        style: Optional rich style.
    """
    console.print(Text(message, style=style), soft_wrap=True)


def _notes(report: BranchReport) -> str:
    """Build the "Notes" cell for *report*.

    Args:
        report: The branch report.

    Returns:
        The refusal reasons when unsafe, otherwise how the verdict was
        reached, followed by any warnings.
    """
    parts = list(report.reasons) if report.reasons else [report.verdict.detail]
    parts.extend(f"warning: {warning}" for warning in report.warnings)
    return "; ".join(part for part in parts if part) or "-"


def render_scan_table(result: ScanResult, console: Console) -> None:
    """Print the scan result as a table.

    Args:
        result: Result of :func:`scan_branches`.
        console: Console to print to.
    """
    if not result.reports:
        _say(console, f"No branches to examine (mainline: {result.mainline}).")
        return
    table = Table(
        title=Text(f"git-trim scan - mainline: {result.mainline}"),
        title_justify="left",
        header_style="bold",
    )
    table.add_column("Branch", overflow="fold")
    table.add_column("Last commit", no_wrap=True)
    table.add_column("Merged?", no_wrap=True)
    table.add_column("Method", no_wrap=True)
    table.add_column("Safe?", no_wrap=True)
    table.add_column("Notes", overflow="fold")
    for report in result.reports:
        merged_text, merged_style = _MERGED_CELL[report.classification]
        table.add_row(
            Text(report.branch.name, style="bold" if report.safe else ""),
            Text(f"{report.branch.last_commit_at:%Y-%m-%d} ({report.age_days}d)"),
            Text(merged_text, style=merged_style),
            Text(report.method.value if report.method else "-"),
            Text("yes", style="bold green") if report.safe else Text("NO", style="bold red"),
            Text(_notes(report)),
        )
    console.print(table)
    if result.shallow:
        _say(console, "Shallow clone: only plain merges can be detected here.", "yellow")
    count = len(result.candidates)
    if count:
        _say(
            console,
            f"{count} of {_plural(len(result.reports), 'branch')} can be deleted safely "
            f"(`{APP_NAME} delete` previews, `{APP_NAME} delete --yes` deletes).",
        )
    else:
        _say(
            console,
            f"Nothing to trim ({_plural(len(result.reports), 'branch')} examined).",
        )


def render_delete_plan(candidates: Sequence[BranchReport], kept: int, console: Console) -> None:
    """Print what ``delete --yes`` would do.

    Args:
        candidates: Reports of the branches that would be deleted.
        kept: Number of examined branches that would be kept.
        console: Console to print to.
    """
    _say(
        console,
        f"Dry run - nothing was deleted. Would delete {_plural(len(candidates), 'branch')}:",
    )
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("Branch", overflow="fold")
    table.add_column("Tip", no_wrap=True)
    table.add_column("Why", overflow="fold")
    for report in candidates:
        table.add_row(
            Text(f"  {report.branch.name}", style="bold"),
            Text(report.branch.sha[:8], style="dim"),
            Text(_notes(report)),
        )
    console.print(table)
    _say(
        console,
        f"Re-run with --yes to delete ({_plural(kept, 'branch')} kept; `{APP_NAME} scan` explains why).",
    )


# ---------------------------------------------------------------------------
# CLI (argparse)
# ---------------------------------------------------------------------------


def _non_negative_int(value: str) -> int:
    num = int(value)
    if num < 0:
        msg = "must be >= 0"
        raise argparse.ArgumentTypeError(msg)
    return num


def _add_repo_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-C",
        "--repo",
        type=Path,
        default=Path(),
        help="Repository to work on (any directory inside it). (default: current directory)",
    )


def _add_scan_options(parser: argparse.ArgumentParser) -> None:
    _add_repo_option(parser)
    parser.add_argument(
        "--main",
        dest="mainline",
        default=None,
        help="Mainline branch or ref to compare against. (default: origin/HEAD's branch, else main, else master)",
    )
    parser.add_argument(
        "--stale",
        type=_non_negative_int,
        default=DEFAULT_STALE_DAYS,
        help="Unmerged branches with a commit in the last N days are never deleted.",
    )
    parser.add_argument(
        "--keep",
        default=DEFAULT_KEEP_OPTION,
        help="Comma-separated branch names or globs that are never deleted (case-insensitive).",
    )
    parser.add_argument(
        "--pattern",
        default=None,
        help="Only consider branches matching this glob, e.g. 'feat/*'.",
    )


def _add_act_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Really do it. Without this flag nothing is changed.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Only show what would happen (the default; wins over --yes).",
    )


def _stderr_sink(message: str) -> None:
    """Write a formatted log record to whatever ``sys.stderr`` is right now.

    Looking the stream up on every call keeps logging working when stderr is
    replaced after configuration (test runners, output capture).

    Args:
        message: The formatted record, newline included.
    """
    sys.stderr.write(message)


def configure_logging(verbosity: int) -> None:
    """Send loguru output to stderr at a level chosen by ``-v`` flags.

    Args:
        verbosity: 0 shows warnings, 1 adds info, 2 or more adds debug
            (every git command that is run).
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


@contextmanager
def _cli_errors() -> Generator[None]:
    """Turn :class:`GitError` into an error message and exit code 1.

    Yields:
        Nothing; wrap the body of a command with it.

    Raises:
        SystemExit: With :data:`EXIT_ERROR` when a :class:`GitError` occurs.
    """
    try:
        yield
    except GitError as exc:
        _say(Console(stderr=True), f"error: {exc}", "bold red")
        raise SystemExit(EXIT_ERROR) from exc


def cmd_scan(
    repo: Path = Path(),
    mainline: str | None = None,
    stale: int = DEFAULT_STALE_DAYS,
    keep: str = DEFAULT_KEEP_OPTION,
    pattern: str | None = None,
    json_output: bool = False,
) -> None:
    """Show every branch with its merge status and whether it is safe to delete.

    Exits with 10 when at least one branch can be deleted, 0 otherwise.
    """
    with _cli_errors():
        result = scan_branches(
            repo,
            mainline=mainline,
            stale_days=stale,
            keep=parse_keep(keep),
            pattern=pattern,
        )
    if json_output:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        render_scan_table(result, Console())
    raise SystemExit(EXIT_CANDIDATES if result.candidates else EXIT_OK)


def cmd_delete(
    repo: Path = Path(),
    mainline: str | None = None,
    stale: int = DEFAULT_STALE_DAYS,
    keep: str = DEFAULT_KEEP_OPTION,
    pattern: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
    json_output: bool = False,
) -> None:
    """Delete the branches that are safe to delete (dry run unless --yes).

    Every deleted tip is recorded in .git/trim-log.jsonl; `git-trim undo`
    brings the branches back.
    """
    console = Console()
    act = yes and not dry_run
    with _cli_errors():
        result = scan_branches(
            repo,
            mainline=mainline,
            stale_days=stale,
            keep=parse_keep(keep),
            pattern=pattern,
        )
        candidates = result.candidates
        kept = len(result.reports) - len(candidates)
        outcome = delete_branches(repo, candidates) if act else DeleteOutcome()

    if json_output:
        payload: dict[str, Any] = {
            "dry_run": not act,
            "mainline": result.mainline,
            "kept": kept,
            "would_delete": [] if act else [report.to_dict() for report in candidates],
            "deleted": [report.to_dict() for report in outcome.deleted],
            "failed": [{"branch": report.branch.name, "error": error} for report, error in outcome.failed],
            "undo_log": str(outcome.log_path) if outcome.log_path else None,
        }
        print(json.dumps(payload, indent=2))
    elif not candidates:
        _say(console, "Nothing safe to delete.")
    elif not act:
        render_delete_plan(candidates, kept, console)
    else:
        for report in outcome.deleted:
            _say(
                console,
                f"deleted {report.branch.name} (was {report.branch.sha[:8]})",
                "green",
            )
        for report, error in outcome.failed:
            _say(console, f"FAILED  {report.branch.name}: {error}", "bold red")
        _say(
            console,
            f"Deleted {_plural(len(outcome.deleted), 'branch')}. Undo with `{APP_NAME} undo`.",
        )

    if not candidates:
        raise SystemExit(EXIT_OK)
    if not act:
        raise SystemExit(EXIT_CANDIDATES)
    raise SystemExit(EXIT_ERROR if outcome.failed else EXIT_OK)


def cmd_undo(
    repo: Path = Path(),
    all_runs: bool = False,
) -> None:
    """Recreate deleted branches from .git/trim-log.jsonl (last delete by default)."""
    console = Console()
    with _cli_errors():
        outcome = undo_deletions(repo, all_runs=all_runs)
    if outcome.empty:
        _say(console, "Nothing to undo.")
        raise SystemExit(EXIT_OK)
    for name, sha in outcome.restored:
        _say(console, f"restored {name} at {sha[:8]}", "green")
    for name in outcome.skipped:
        _say(console, f"skipped  {name}: already exists at the logged commit", "dim")
    for name, reason in outcome.failed:
        _say(console, f"FAILED   {name}: {reason}", "bold red")
    raise SystemExit(EXIT_ERROR if outcome.failed else EXIT_OK)


def cmd_prune_remote(
    repo: Path = Path(),
    remote: str = "origin",
    yes: bool = False,
    dry_run: bool = False,
) -> None:
    """Refresh remote-tracking branches with `git fetch --prune` (dry run unless --yes).

    git-trim never talks to a remote by itself; run this first so that
    upstream information can be trusted.
    """
    console = Console()
    act = yes and not dry_run
    with _cli_errors():
        outcome = prune_remote(repo, remote, execute=act)
    verb = "pruned" if act else "would prune"
    if act:
        _say(console, f"Ran: {outcome.command}")
    else:
        _say(console, f"Dry run - would run: {outcome.command} (re-run with --yes)")
    for name in outcome.pruned:
        _say(console, f"{verb} {remote}/{name}", "yellow")
    if not outcome.pruned:
        _say(console, "No stale remote-tracking branches.")
    if outcome.orphaned:
        intro = "Local branches left without an upstream" if act else "Would lose their upstream"
        _say(console, f"{intro}: {', '.join(outcome.orphaned)}")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=(
            "Bulk-safe branch cleanup: finds squash/rebase merges, refuses unsafe deletions. "
            "Exit codes: 0 nothing to do / success, 10 candidates found, 1 error."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="More logging on stderr (-v info, -vv debug).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {__version__}",
        help="Show the version and exit.",
    )
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser(
        "scan",
        help="Show every branch with its merge status and whether it is safe to delete.",
    )
    _add_scan_options(p_scan)
    p_scan.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )

    p_del = sub.add_parser(
        "delete",
        help="Delete the branches that are safe to delete (dry run unless --yes).",
    )
    _add_scan_options(p_del)
    _add_act_options(p_del)
    p_del.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )

    p_undo = sub.add_parser(
        "undo",
        help="Recreate deleted branches from .git/trim-log.jsonl (last delete by default).",
    )
    _add_repo_option(p_undo)
    p_undo.add_argument(
        "--all",
        dest="all_runs",
        action="store_true",
        help="Restore every logged branch, not just the last delete.",
    )

    p_prune = sub.add_parser(
        "prune-remote",
        help="Refresh remote-tracking branches with `git fetch --prune` (dry run unless --yes).",
    )
    _add_repo_option(p_prune)
    p_prune.add_argument("--remote", default="origin", help="Remote to refresh.")
    _add_act_options(p_prune)

    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point of the ``git-trim`` command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    if args.command == "scan":
        cmd_scan(
            repo=args.repo,
            mainline=args.mainline,
            stale=args.stale,
            keep=args.keep,
            pattern=args.pattern,
            json_output=args.json_output,
        )
    elif args.command == "delete":
        cmd_delete(
            repo=args.repo,
            mainline=args.mainline,
            stale=args.stale,
            keep=args.keep,
            pattern=args.pattern,
            yes=args.yes,
            dry_run=args.dry_run,
            json_output=args.json_output,
        )
    elif args.command == "undo":
        cmd_undo(repo=args.repo, all_runs=args.all_runs)
    elif args.command == "prune-remote":
        cmd_prune_remote(
            repo=args.repo,
            remote=args.remote,
            yes=args.yes,
            dry_run=args.dry_run,
        )
    else:
        parser.print_help()
        raise SystemExit(0)


if __name__ == "__main__":
    main()
