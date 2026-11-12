#!/data/data/com.termux/files/usr/bin/env python
"""git-trim - bulk-safe branch cleanup that understands squash and rebase merges.

WHY
    `git branch --merged main` only sees branches whose tip is an *ancestor* of
    main.  Anything merged through GitHub's "Squash and merge" or "Rebase and
    merge" buttons is invisible to it, so dead branches pile up and people start
    deleting by hand.  git-trim detects those merges via `git patch-id --stable`
    (the same content fingerprint `git cherry` uses), refuses every deletion it
    cannot prove safe, logs what it removes, and can undo.

HOW A BRANCH IS CLASSIFIED (against the base branch, auto-detected)
    1. ancestor   branch tip is reachable from the base          -> merged (plain merge / ff)
    2. patch-id   every branch commit's patch-id exists on the   -> merged (rebase merge or
                  mainline since the merge-base                    single-commit squash)
    3. squash     the patch-id of the branch's *combined* diff   -> merged (multi-commit squash)
                  since the merge-base matches a mainline commit
    Mainline patch-ids are cached per merge-base and branch patch-ids per tip,
    so a repo with hundreds of branches does not re-walk history.

SAFETY - a branch is only deletable when ALL of these hold
    * it is not the current branch and not checked out in any `git worktree`;
    * it is not the base branch and does not match --keep (case-insensitive);
    * its merge status is known: either merged, or unmerged with a *live*
      upstream and older than --stale days (i.e. recoverable from the remote).
    Never deleted: unmerged branches without a live upstream (a "gone" upstream
    counts as none), anything in a shallow clone (reported "unknown"), and
    worktree/current/protected branches.  Every deletion is appended to
    <gitdir>/trim-log.jsonl and `git-trim undo` recreates it at the exact SHA.

USAGE
    git-trim scan                      # table: name, last commit, merged?, method, safe?
    git-trim scan --json
    git-trim delete --dry-run          # default is dry-run; --yes is required to act
    git-trim delete --yes --pattern 'feat/*' --keep main,develop,release/*
    git-trim prune-remote --yes        # git fetch --prune, then delete gone-upstream locals
    git-trim undo                      # recreate from .git/trim-log.jsonl

    Global flags (-C, --no-color, --version) must precede the subcommand.
    This file is self-contained (stdlib only).  Run it directly:

        python3 git_trim.py scan

    Install as a git subcommand (so `git trim scan` works):

        cp git_trim.py /usr/local/bin/git-trim && chmod +x /usr/local/bin/git-trim

EXIT CODES
    0    success / nothing to do
    10   candidates found (scan, or delete / prune-remote dry-run) - for CI hooks
    1    runtime error (usage errors exit 2 via argparse)

TESTS & DEMO
    pytest git_trim.py -q              # embedded test suite (needs pytest + git)
    python3 git_trim.py demo           # builds a throwaway repo and walks through
                                       # scan -> delete -> prune-remote -> undo
    python3 git_trim.py demo --bench 100

LIMITATIONS
    * Multi-commit squash detection compares the branch's combined diff against
      the merge-base; it can miss squashes landed after the base advanced
      between branch point and squash (it then reports "unmerged" - a safe
      false negative).  Pull your base branch first: git-trim auto-upgrades the
      base to origin/<base> when the remote is strictly ahead of the local one.
    * Shallow clones: patch-ids of missing commits are unavailable, so
      non-ancestor branches are reported "unknown" and never deletable.
    * Deleting a local branch that another local branch tracks prints a warning
      (the tracker's upstream is orphaned).

Requires Python 3.12+ and a reasonably modern git (>= 2.28 recommended).
"""

import argparse
import contextlib
import fnmatch
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path

if sys.version_info < (3, 12):
    sys.exit("git-trim requires Python 3.12+")

__version__ = "1.0.0"

GIT_TIMEOUT = 60.0  # seconds per git invocation
FETCH_TIMEOUT = 300.0  # git fetch can legitimately be slow
TRIM_LOG_NAME = "trim-log.jsonl"
DEFAULT_KEEP = ("main", "master", "develop")
DEFAULT_STALE_DAYS = 14.0
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CANDIDATES = 10


# ============================================================================
# gitio - thin subprocess wrapper.  Never shell-interpolate branch names: every
# call passes an argv list straight to subprocess.
# ============================================================================


class GitError(RuntimeError):
    """A git invocation failed or is impossible."""


def run_git_ex(repo: Path, *args: str, timeout: float = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run git in `repo`, returning the CompletedProcess (stdout/stderr decoded)."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args[:3])} ... timed out after {timeout:.0f}s") from exc


def run_git(repo: Path, *args: str, check: bool = True, timeout: float = GIT_TIMEOUT) -> str:
    """Run git and return stdout; raise GitError on failure when check=True."""
    proc = run_git_ex(repo, *args, timeout=timeout)
    if check and proc.returncode != 0:
        detail = (proc.stderr.strip() or proc.stdout.strip() or "no output").splitlines()
        raise GitError(f"git {' '.join(args[:3])} failed (exit {proc.returncode}): {details_of(detail)}")
    return proc.stdout


def details_of(lines: list[str]) -> str:
    return lines[0][:200] if lines else "no output"


def _pipe(repo: Path, git_argv: list[str]) -> str:
    """Run `git_argv` and pipe its stdout through `git patch-id --stable`.

    Returns the raw patch-id stdout (lines of "<patch-id> <commit>").
    """
    producer = subprocess.Popen(
        ["git", *git_argv],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    try:
        consumer = subprocess.run(
            ["git", "patch-id", "--stable"],
            cwd=str(repo),
            stdin=producer.stdout,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
        )
        producer.stdout.close()
        producer.wait(timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        producer.kill()
        producer.wait()
        raise GitError(f"git {git_argv[1]} timed out while feeding git patch-id") from None
    if producer.returncode != 0:
        err = (producer.stderr.read() or "").strip().splitlines()
        raise GitError(f"git {git_argv[1]} failed (exit {producer.returncode}): {details_of(err)}")
    if consumer.returncode != 0:
        raise GitError(f"git patch-id failed (exit {consumer.returncode}): {consumer.stderr.strip()}")
    return consumer.stdout


def _parse_patch_ids(text: str) -> set[str]:
    """Parse `git patch-id` output lines ("<patch-id> <commit sha>") into a set."""
    return {line.split()[0] for line in text.splitlines() if line.strip()}


# ============================================================================
# refs - parsing `for-each-ref`, current branch, worktrees, git dir.
# ============================================================================

REF_FORMAT = (
    "%(refname:short)%00%(objectname)%00%(upstream:short)%00%(upstream:track,nobracket)%00%(committerdate:unix)"
)


class Method(StrEnum):
    ANCESTOR = "ancestor"  # tip reachable from base (plain merge / fast-forward)
    PATCH_ID = "patch-id"  # every branch commit's patch-id is on the mainline
    SQUASH = "squash"  # the branch's combined diff matches a mainline commit


@dataclass(slots=True)
class BranchRef:
    name: str
    sha: str
    upstream: str = ""  # short upstream name, "" when none
    track: str = ""  # upstream:track,nobracket -> "", "gone", "ahead 1", ...
    committed_ts: float = 0.0  # committerdate of the tip, unix epoch
    current: bool = False
    worktree: str | None = None
    merged: bool | None = None  # None = unknown (shallow clone, patch-id failure)
    method: Method | None = None
    safe: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def upstream_gone(self) -> bool:
        return self.track == "gone"

    @property
    def age_days(self) -> float:
        return max(0.0, (time.time() - self.committed_ts) / 86400.0)

    @property
    def last_commit_iso(self) -> str:
        if not self.committed_ts:
            return ""
        return datetime.fromtimestamp(self.committed_ts, tz=timezone.utc).isoformat(timespec="seconds")


def parse_refs(text: str) -> list[BranchRef]:
    """Parse `for-each-ref --format=REF_FORMAT refs/heads` output."""
    refs: list[BranchRef] = []
    for line in text.splitlines():
        if not line:
            continue
        parts = line.split("\x00")
        if len(parts) != 5:
            continue
        name, sha, upstream, track, ts = parts
        try:
            committed_ts = float(ts.split()[0]) if ts.split() else 0.0
        except ValueError:
            committed_ts = 0.0
        refs.append(BranchRef(name, sha, upstream, track, committed_ts))
    return refs


def list_branches(repo: Path) -> list[BranchRef]:
    return parse_refs(run_git(repo, "for-each-ref", f"--format={REF_FORMAT}", "refs/heads"))


def current_branch(repo: Path) -> str | None:
    out = run_git(repo, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    return out.strip() or None


def worktree_branches(repo: Path) -> dict[str, str]:
    """Map branch name -> worktree path for every branch checked out in a worktree."""
    result: dict[str, str] = {}
    path = ""
    for line in run_git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.startswith("branch ") and line[len("branch ") :].startswith("refs/heads/"):
            result[line[len("branch refs/heads/") :]] = path
    return result


def git_dir(repo: Path) -> Path:
    out = run_git(repo, "rev-parse", "--git-dir").strip()
    p = Path(out)
    if not p.is_absolute():
        p = repo / p
    return p.resolve()


def is_shallow(repo: Path) -> bool:
    return run_git(repo, "rev-parse", "--is-shallow-repository").strip().lower() == "true"


def ref_exists(repo: Path, ref: str) -> bool:
    return run_git_ex(repo, "rev-parse", "--verify", "-q", ref).returncode == 0


def rev_sha(repo: Path, ref: str) -> str:
    return run_git(repo, "rev-parse", ref).strip()


def merge_base(repo: Path, a: str, b: str) -> str | None:
    proc = run_git_ex(repo, "merge-base", a, b)
    if proc.returncode == 0:
        return proc.stdout.strip()
    if proc.returncode == 1 and not proc.stdout.strip():
        return None  # no common ancestor
    raise GitError(f"git merge-base {a} {b} failed: {(proc.stderr.strip() or 'unknown error')[:200]}")


def resolve_repo(path_str: str) -> Path:
    repo = Path(path_str or ".").expanduser().resolve()
    if not repo.is_dir():
        raise GitError(f"not a directory: {repo}")
    run_git(repo, "rev-parse", "--git-dir")  # raises GitError when not a repository
    return repo


def resolve_base(repo: Path, explicit: str | None) -> str:
    """Pick the base committish: explicit --base, else origin/HEAD's local
    counterpart, else main, else master.  When origin/<local> is strictly ahead
    of the local branch, prefer it (fresher squash-merge detection)."""
    if explicit:
        if not ref_exists(repo, explicit):
            raise GitError(f"--base '{explicit}' does not resolve to a commit in this repository")
        return explicit
    local: str | None = None
    origin_head = run_git(repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    if origin_head.startswith("origin/"):
        candidate = origin_head[len("origin/") :]
        if ref_exists(repo, f"refs/heads/{candidate}"):
            local = candidate
    if local is None:
        for candidate in ("main", "master"):
            if ref_exists(repo, f"refs/heads/{candidate}"):
                local = candidate
                break
    if local is None:
        raise GitError("could not auto-detect a base branch (no origin/HEAD, 'main' or 'master'); pass --base")
    remote = f"origin/{local}"
    if ref_exists(repo, f"refs/remotes/{remote}"):
        try:
            if merge_base(repo, remote, local) == rev_sha(repo, local) != rev_sha(repo, remote):
                return remote  # remote strictly ahead: compare against the fresher history
        except GitError:
            pass
    return local


# ============================================================================
# merges - classification: ancestor check, patch-id subset, combined squash id.
# ============================================================================


class PatchIdIndex:
    """Per-invocation cache of patch-id sets (keyed by merge-base / branch tip)."""

    def __init__(self, repo: Path, base: str) -> None:
        self.repo = repo
        self.base = base
        self._mainline: dict[str, set[str]] = {}
        self._branch: dict[tuple[str, str], set[str]] = {}
        self._combined: dict[tuple[str, str], str | None] = {}

    def mainline_ids(self, mergebase: str) -> set[str]:
        if mergebase not in self._mainline:
            self._mainline[mergebase] = _parse_patch_ids(
                _pipe(self.repo, ["log", "--no-merges", "-p", f"{mergebase}..{self.base}"])
            )
        return self._mainline[mergebase]

    def branch_ids(self, mergebase: str, tip: str) -> set[str]:
        key = (mergebase, tip)
        if key not in self._branch:
            self._branch[key] = _parse_patch_ids(_pipe(self.repo, ["log", "--no-merges", "-p", f"{mergebase}..{tip}"]))
        return self._branch[key]

    def combined_id(self, mergebase: str, tip: str) -> str | None:
        key = (mergebase, tip)
        if key not in self._combined:
            ids = _parse_patch_ids(_pipe(self.repo, ["diff", mergebase, tip]))
            self._combined[key] = next(iter(ids), None)
        return self._combined[key]


def classify_branch(repo: Path, base: str, br: BranchRef, index: PatchIdIndex, shallow: bool) -> None:
    """Set br.merged / br.method.  Never guesses: unknown on shallow or failure."""
    mb = merge_base(repo, base, f"refs/heads/{br.name}")
    if mb is None:
        br.merged = False
        br.reasons.append("no common ancestor with base")
        return
    if mb == br.sha:
        br.merged, br.method = True, Method.ANCESTOR
        return
    if shallow:
        br.merged, br.method = None, None  # downgrade to unknown, never "safe to delete"
        return
    try:
        mainline = index.mainline_ids(mb)
        branch_ids = index.branch_ids(mb, br.sha)
        if branch_ids and branch_ids <= mainline:
            br.merged, br.method = True, Method.PATCH_ID
            return
        combined = index.combined_id(mb, br.sha)
        if combined is not None and combined in mainline:
            br.merged, br.method = True, Method.SQUASH
            return
    except GitError:
        br.merged, br.method = None, None
        return
    br.merged, br.method = False, None


# ============================================================================
# safety - the policy and the refusal rules.
# ============================================================================


@dataclass(slots=True)
class Policy:
    base: str  # committish compared against (may be origin/...)
    protect: str  # local base branch name, always protected
    keep: tuple[str, ...]  # --keep globs
    stale_days: float  # unmerged branches need to be older than this


def _split_patterns(values: list[str] | None) -> tuple[str, ...]:
    """--keep/--pattern accept repeatable flags and comma-separated lists."""
    if not values:
        return ()
    out: list[str] = []
    for value in values:
        out.extend(p for p in (s.strip() for s in value.split(",")) if p)
    return tuple(out)


def _match_any(name: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    low = name.lower()
    return any(fnmatch.fnmatch(low, p.lower()) for p in patterns)


def is_protected(name: str, protect: str, keep: tuple[str, ...]) -> str | None:
    """Return the protection reason, or None.  Case-insensitive: 'Main' vs 'main'
    matters on case-insensitive filesystems, so we over-protect deliberately."""
    if name.lower() == protect.lower():
        return "protected: base branch"
    for pattern in keep:
        if fnmatch.fnmatch(name.lower(), pattern.lower()):
            return f"protected: matches --keep '{pattern}'"
    return None


def evaluate(br: BranchRef, policy: Policy, current: str | None, worktrees: dict[str, str]) -> None:
    """Decide whether the branch may be deleted; collect refusal reasons."""
    if current is not None and br.name == current:
        br.current = True
        br.reasons.append("current branch")
    if br.name in worktrees:
        br.worktree = worktrees[br.name]
        br.reasons.append("checked out in a worktree")
    protected = is_protected(br.name, policy.protect, policy.keep)
    if protected:
        br.reasons.append(protected)
    if br.merged is None:
        br.reasons.append("unknown merge status (shallow clone or patch-id unavailable)")
    elif br.merged is False:
        if not br.upstream:
            br.reasons.append("unmerged: no upstream")
        elif br.upstream_gone:
            br.reasons.append(f"unmerged: upstream '{br.upstream}' is gone")
        elif br.age_days < policy.stale_days:
            br.reasons.append(
                f"unmerged: last commit {humanize_age(br.age_days)} ago is younger"
                f" than the {policy.stale_days:g}-day stale window"
            )
    br.safe = not br.reasons


@dataclass(slots=True)
class Analysis:
    repo: Path
    base: str
    shallow: bool
    current: str | None
    worktrees: dict[str, str]
    branches: list[BranchRef]

    @property
    def candidates(self) -> list[BranchRef]:
        return [b for b in self.branches if b.safe]


def analyze(repo: Path, policy: Policy) -> Analysis:
    branches = list_branches(repo)
    current = current_branch(repo)
    worktrees = worktree_branches(repo)
    shallow = is_shallow(repo)
    index = PatchIdIndex(repo, policy.base)
    for br in branches:
        classify_branch(repo, policy.base, br, index, shallow)
        evaluate(br, policy, current, worktrees)
    branches.sort(key=lambda b: b.name)
    return Analysis(repo, policy.base, shallow, current, worktrees, branches)


# ============================================================================
# actions - trim log, deletion, undo, prune-remote.
# ============================================================================


def trim_log_path(repo: Path) -> Path:
    return git_dir(repo) / TRIM_LOG_NAME


def append_trim_log(repo: Path, branches: list[BranchRef], source: str) -> Path:
    """Record deletions BEFORE deleting, so undo works even if we crash midway."""
    path = trim_log_path(repo)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as fh:
        for br in branches:
            fh.write(
                json.dumps({
                    "ts": now,
                    "action": "delete",
                    "source": source,
                    "name": br.name,
                    "sha": br.sha,
                    "upstream": br.upstream or None,
                })
                + "\n"
            )
    return path


def read_trim_log(path: Path) -> list[dict]:
    entries: list[dict] = []
    if not path.exists():
        return entries
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"warning: skipping malformed trim-log line {lineno}", file=sys.stderr)
    return entries


def delete_branches(repo: Path, branches: list[BranchRef]) -> list[tuple[BranchRef, str]]:
    """Force-delete (we did the merged-ness analysis git cannot see).  Returns failures."""
    failures: list[tuple[BranchRef, str]] = []
    for br in branches:
        proc = run_git_ex(repo, "branch", "-D", "--", br.name)
        if proc.returncode != 0:
            failures.append((br, (proc.stderr.strip() or proc.stdout.strip() or "unknown error")))
    return failures


def warn_orphans(analysis: Analysis, deleted: list[BranchRef]) -> None:
    """Deleting a local branch that another local branch tracks orphans it."""
    deleted_names = {b.name for b in deleted}
    for br in analysis.branches:
        if br.upstream in deleted_names and br.name not in deleted_names:
            print(
                f"warning: '{br.name}' tracks local branch '{br.upstream}' which was just"
                f" deleted; its upstream is now orphaned",
                file=sys.stderr,
            )


# ============================================================================
# render - rich-free table writer, colors, JSON shapes.
# ============================================================================


def _color_enabled(args: argparse.Namespace) -> bool:
    return not getattr(args, "no_color", False) and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def humanize_age(age_days: float) -> str:
    age = max(0.0, float(age_days))
    if age < 1 / 24:
        return f"{int(age * 1440)}m"
    if age < 1:
        return f"{int(age * 24)}h"
    if age < 14:
        return f"{int(age)}d"
    if age < 60:
        return f"{int(age / 7)}w"
    if age < 365:
        return f"{int(age / 30)}mo"
    return f"{age / 365:.1f}y"


def why_text(br: BranchRef) -> str:
    if br.safe:
        if br.merged:
            return f"merged via {br.method.value}"
        return f"unmerged, recoverable from '{br.upstream}'"
    return "; ".join(br.reasons)


def _trunc(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(1, width - 3)] + "..."


def render_table(
    headers: list[str],
    rows: list[list[str]],
    caps: list[int],
    colors: list[list[str | None]] | None = None,
    use_color: bool = False,
) -> str:
    colors = colors or [[None] * len(headers) for _ in rows]
    grid = [[_trunc(row[i], caps[i]) for i in range(len(headers))] for row in rows]
    widths = [len(h) for h in headers]
    for row in grid:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cell: str, width: int, code: str | None) -> str:
        padded = cell.ljust(width)
        return f"\x1b[{code}m{padded}\x1b[0m" if (use_color and code) else padded

    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row, row_colors in zip(grid, colors):
        lines.append("  ".join(fmt(row[i], widths[i], row_colors[i]) for i in range(len(headers))).rstrip())
    return "\n".join(lines)


def render_scan_table(analysis: Analysis, use_color: bool = False) -> str:
    headers = ["BRANCH", "AGE", "MERGED", "METHOD", "SAFE", "WHY"]
    caps = [34, 4, 7, 8, 4, 58]
    rows: list[list[str]] = []
    colors: list[list[str | None]] = []
    for br in analysis.branches:
        merged = "yes" if br.merged is True else ("no" if br.merged is False else "unknown")
        method = br.method.value if br.method else "-"
        safe = "yes" if br.safe else "no"
        rows.append([br.name, humanize_age(br.age_days), merged, method, safe, why_text(br)])
        colors.append([
            None,
            None,
            "32" if br.merged is True else ("31" if br.merged is False else "33"),
            None,
            "32" if br.safe else "31",
            None,
        ])
    return render_table(headers, rows, caps, colors, use_color)


def summary_line(analysis: Analysis) -> str:
    branches = analysis.branches
    merged = sum(1 for b in branches if b.merged is True)
    unmerged = sum(1 for b in branches if b.merged is False)
    unknown = sum(1 for b in branches if b.merged is None)
    return (
        f"summary: {len(branches)} branches | {merged} merged | {unmerged} unmerged |"
        f" {unknown} unknown | {len(analysis.candidates)} safe to delete"
    )


def branch_json(br: BranchRef) -> dict:
    return {
        "name": br.name,
        "sha": br.sha,
        "current": br.current,
        "worktree": br.worktree,
        "upstream": br.upstream or None,
        "upstream_gone": br.upstream_gone,
        "last_commit_unix": int(br.committed_ts) if br.committed_ts else None,
        "last_commit_iso": br.last_commit_iso,
        "age_days": round(br.age_days, 2),
        "merged": br.merged,
        "method": br.method.value if br.method else None,
        "safe": br.safe,
        "reasons": list(br.reasons),
        "why": why_text(br),
    }


def scan_json(analysis: Analysis) -> dict:
    candidates = [b.name for b in analysis.candidates]
    return {
        "tool": "git-trim",
        "version": __version__,
        "repo": str(analysis.repo),
        "base": analysis.base,
        "shallow": analysis.shallow,
        "current": analysis.current,
        "branches": [branch_json(b) for b in analysis.branches],
        "candidates": candidates,
        "summary": {
            "total": len(analysis.branches),
            "merged": sum(1 for b in analysis.branches if b.merged is True),
            "unmerged": sum(1 for b in analysis.branches if b.merged is False),
            "unknown": sum(1 for b in analysis.branches if b.merged is None),
            "safe_to_delete": len(candidates),
        },
    }


# ============================================================================
# cli - argparse (stdlib is enough), subcommands, exit codes.
# ============================================================================

EPILOG = """examples:
  git-trim scan                          # table: name, last commit, merged?, method, safe?
  git-trim scan --json
  git-trim delete --dry-run              # default is dry-run; requires --yes to act
  git-trim delete --yes --pattern 'feat/*' --keep main,develop,release/*
  git-trim prune-remote --yes            # git fetch --prune, then clean gone upstreams
  git-trim undo                          # recreate from .git/trim-log.jsonl

exit codes: 0 nothing to do / success | 10 candidates found (CI hooks) | 1 error
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-trim",
        description="Bulk-safe branch cleanup that understands squash and rebase merges.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"git-trim {__version__}")
    parser.add_argument(
        "-C", "--repo", default=".", metavar="PATH", help="repository to operate on (default: current directory)"
    )
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def common(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--json", action="store_true", help="machine-readable JSON output")
        sp.add_argument(
            "--base",
            metavar="REF",
            default=None,
            help="base branch to compare against (default: auto-detect origin/HEAD,"
            " then main, then master; upgraded to origin/<base> when strictly ahead)",
        )
        sp.add_argument(
            "--keep",
            action="append",
            metavar="PATTERNS",
            default=None,
            help=f"comma-separated globs of branches to never delete"
            f" (default: {','.join(DEFAULT_KEEP)}; base branch always protected)",
        )
        sp.add_argument(
            "--stale",
            type=float,
            default=DEFAULT_STALE_DAYS,
            metavar="DAYS",
            help=f"unmerged branches with a live upstream are only deletable when"
            f" older than this many days (default: {DEFAULT_STALE_DAYS:g})",
        )
        return sp

    sp = common(sub.add_parser("scan", help="list branches, how they merged, and whether they are safe"))
    sp.set_defaults(func=cmd_scan)

    sp = common(sub.add_parser("delete", help="delete safe-to-delete branches (dry-run unless --yes)"))
    sp.add_argument("--dry-run", action="store_true", help="explicit dry-run (the default behaviour)")
    sp.add_argument("--yes", action="store_true", help="actually delete (required to act)")
    sp.add_argument(
        "--pattern",
        action="append",
        metavar="PATTERNS",
        default=None,
        help="only consider branches matching these globs, e.g. 'feat/*'",
    )
    sp.set_defaults(func=cmd_delete)

    sp = common(
        sub.add_parser("prune-remote", help="git fetch --prune, then delete local branches whose upstream is gone")
    )
    sp.add_argument("--dry-run", action="store_true", help="explicit dry-run (the default behaviour)")
    sp.add_argument("--yes", action="store_true", help="actually delete (required to act)")
    sp.add_argument(
        "--pattern",
        action="append",
        metavar="PATTERNS",
        default=None,
        help="only consider branches matching these globs, e.g. 'feat/*'",
    )
    sp.set_defaults(func=cmd_prune_remote)

    sp = sub.add_parser("undo", help="restore branches recorded in .git/trim-log.jsonl")
    sp.add_argument("--json", action="store_true", help="machine-readable JSON output")
    sp.set_defaults(func=cmd_undo)

    sp = sub.add_parser("demo", help="build a throwaway repo and walk through the whole workflow")
    sp.add_argument(
        "--dir", metavar="PATH", default=None, help="directory for the demo repo (default: a fresh temp dir)"
    )
    sp.add_argument(
        "--bench", type=int, default=0, metavar="N", help="also benchmark scan with N extra unmerged branches"
    )
    sp.set_defaults(func=cmd_demo)

    return parser


def _local_base_name(base: str) -> str:
    if base.startswith("origin/"):
        return base.split("/", 1)[1]
    return base


def _analyze(args: argparse.Namespace, repo: Path | None = None):
    """Common setup for scan/delete/prune-remote.  Returns (repo, analysis) or None."""
    if repo is None:
        repo = resolve_repo(args.repo)
    branches = list_branches(repo)
    if not branches:
        if args.json:
            payload = scan_json(Analysis(repo, "", is_shallow(repo), None, {}, []))
            payload["base"] = None
            print(json.dumps(payload, indent=2))
        else:
            print("no local branches - nothing to do")
        return None
    base = resolve_base(repo, args.base)
    keep = DEFAULT_KEEP if args.keep is None else _split_patterns(args.keep)
    policy = Policy(base=base, protect=_local_base_name(base), keep=keep, stale_days=args.stale)
    return repo, analyze(repo, policy)


def _reject_conflicting_flags(args: argparse.Namespace) -> None:
    if getattr(args, "yes", False) and getattr(args, "dry_run", False):
        raise GitError("--yes and --dry-run are mutually exclusive")


def cmd_scan(args: argparse.Namespace) -> int:
    loaded = _analyze(args)
    if loaded is None:
        return EXIT_OK
    _repo, analysis = loaded
    if args.json:
        print(json.dumps(scan_json(analysis), indent=2))
    else:
        print(f"local branches vs base '{analysis.base}':")
        print(render_scan_table(analysis, use_color=_color_enabled(args)))
        print(summary_line(analysis))
        if analysis.candidates:
            print("act with: git-trim delete --yes   (dry-run by default; undo available)")
    return EXIT_CANDIDATES if analysis.candidates else EXIT_OK


def _preview_or_delete(
    repo: Path,
    analysis: Analysis,
    eligible: list[BranchRef],
    selected: list[BranchRef],
    args: argparse.Namespace,
    source: str,
    qualifier: str = "",
) -> int:
    """Shared dry-run/act flow of `delete` and `prune-remote`.

    `eligible` is the deletable pool before --pattern filtering; `selected` is
    what remains after it.  The difference is reported as excluded-by-pattern.
    """
    selected_names = {b.name for b in selected}
    excluded = [b.name for b in eligible if b.name not in selected_names]
    if not args.yes:
        unsafe = [b for b in analysis.branches if not b.safe]
        if args.json:
            print(
                json.dumps(
                    {
                        "would_delete": [branch_json(b) for b in selected],
                        "excluded_by_pattern": excluded,
                        "skipped_unsafe": [branch_json(b) for b in unsafe],
                    },
                    indent=2,
                )
            )
        else:
            if selected:
                suffix = f" {qualifier}" if qualifier else ""
                print(f"dry-run: would delete {len(selected)} branch(es){suffix}:")
                for br in selected:
                    print(f"  {br.name:<34} {br.sha[:10]}  {why_text(br)}")
            else:
                print("dry-run: nothing to delete")
            if excluded:
                print(f"  ({len(excluded)} deletable branch(es) excluded by --pattern)")
            if unsafe:
                print(f"  {len(unsafe)} branch(es) kept for safety - inspect with: git-trim scan")
            if selected:
                print("  re-run with --yes to delete | deletions are logged | undo: git-trim undo")
        return EXIT_CANDIDATES if selected else EXIT_OK

    if not selected:
        if args.json:
            print(json.dumps({"deleted": [], "failed": [], "log": str(trim_log_path(repo))}, indent=2))
        else:
            print("nothing to delete")
        return EXIT_OK

    log_path = append_trim_log(repo, selected, source)
    failures = delete_branches(repo, selected)
    failed_names = {b.name for b, _ in failures}
    deleted = [b for b in selected if b.name not in failed_names]
    for br, err in failures:
        print(f"error: could not delete {br.name}: {err}", file=sys.stderr)
    warn_orphans(analysis, selected)
    if args.json:
        print(
            json.dumps(
                {
                    "deleted": [{"name": b.name, "sha": b.sha} for b in deleted],
                    "failed": [{"name": b.name, "error": e} for b, e in failures],
                    "log": str(log_path),
                },
                indent=2,
            )
        )
    else:
        for br in deleted:
            print(f"deleted {br.name} (was {br.sha[:10]})")
        if excluded:
            print(f"({len(excluded)} deletable branch(es) excluded by --pattern)")
        print(f"{len(deleted)} branch(es) deleted | log: {log_path} | undo: git-trim undo")
    return EXIT_ERROR if failures else EXIT_OK


def cmd_delete(args: argparse.Namespace) -> int:
    _reject_conflicting_flags(args)
    loaded = _analyze(args)
    if loaded is None:
        return EXIT_OK
    repo, analysis = loaded
    eligible = analysis.candidates
    patterns = _split_patterns(args.pattern)
    selected = [b for b in eligible if _match_any(b.name, patterns)]
    return _preview_or_delete(repo, analysis, eligible, selected, args, source="delete")


def cmd_prune_remote(args: argparse.Namespace) -> int:
    _reject_conflicting_flags(args)
    repo = resolve_repo(args.repo)
    if not run_git(repo, "remote").split():
        raise GitError("no git remote is configured; 'prune-remote' needs a remote to fetch --prune")
    proc = run_git_ex(repo, "fetch", "--prune", timeout=FETCH_TIMEOUT)
    if proc.returncode != 0:
        raise GitError(f"git fetch --prune failed: {(proc.stderr.strip() or proc.stdout.strip())[:200]}")
    if not args.json:
        print("ran: git fetch --prune")
        for line in (proc.stderr or "").splitlines():
            if line.strip():
                print(f"  {line.strip()}")
    loaded = _analyze(args, repo=repo)
    if loaded is None:
        return EXIT_OK
    repo, analysis = loaded
    patterns = _split_patterns(args.pattern)
    eligible = [b for b in analysis.candidates if b.upstream_gone]
    selected = [b for b in eligible if _match_any(b.name, patterns)]
    return _preview_or_delete(
        repo,
        analysis,
        eligible,
        selected,
        args,
        source="prune-remote",
        qualifier="whose upstream is gone",
    )


def cmd_undo(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.repo)
    path = trim_log_path(repo)
    entries = read_trim_log(path)
    if not entries:
        if args.json:
            print(json.dumps({"restored": [], "existing": [], "failed": [], "log": str(path)}, indent=2))
        else:
            print(f"nothing to undo - no delete records in {path}")
        return EXIT_OK
    latest: dict[str, str] = {}
    upstreams: dict[str, str] = {}
    for entry in entries:
        name, sha = entry.get("name"), entry.get("sha")
        if isinstance(name, str) and isinstance(sha, str) and name and sha:
            latest[name] = sha  # later entries win (branch may have been re-deleted)
            if isinstance(entry.get("upstream"), str) and entry["upstream"]:
                upstreams[name] = entry["upstream"]
    restored: list[tuple[str, str]] = []
    existing: list[str] = []
    failed: list[tuple[str, str]] = []
    for name in sorted(latest):
        if ref_exists(repo, f"refs/heads/{name}"):
            existing.append(name)
            continue
        proc = run_git_ex(repo, "branch", "--", name, latest[name])
        if proc.returncode == 0:
            restored.append((name, latest[name]))
            _restore_upstream(repo, name, upstreams.get(name))  # best effort
        else:
            failed.append((name, proc.stderr.strip() or proc.stdout.strip() or "unknown error"))
    if args.json:
        print(
            json.dumps(
                {
                    "restored": [{"name": n, "sha": s} for n, s in restored],
                    "existing": existing,
                    "failed": [{"name": n, "error": e} for n, e in failed],
                    "log": str(path),
                },
                indent=2,
            )
        )
    else:
        for name, sha in restored:
            print(f"restored {name} -> {sha}")
        for name, err in failed:
            print(f"error: could not restore {name}: {err}", file=sys.stderr)
        if restored:
            print(f"{len(restored)} branch(es) restored from {path}")
        elif existing and not failed:
            print(f"nothing to restore - {len(existing)} logged branch(es) already exist")
    return EXIT_ERROR if failed else EXIT_OK


def _restore_upstream(repo: Path, name: str, upstream: str | None) -> None:
    """git branch -D drops branch.<name>.* config; re-link tracking when possible."""
    if not upstream:
        return
    if not (ref_exists(repo, f"refs/remotes/{upstream}") or ref_exists(repo, f"refs/heads/{upstream}")):
        return  # e.g. the remote branch was deleted too; nothing to track
    run_git_ex(repo, "branch", f"--set-upstream-to={upstream}", name)  # best effort


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except GitError as exc:
        print(f"git-trim: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("git-trim: interrupted", file=sys.stderr)
        return 130


# ============================================================================
# scenario builders - shared by the embedded tests and the demo.  These build
# throwaway repos with subprocess git (never the module under test's wrappers).
# ============================================================================


def _run(
    repo: Path, *args: str, env_extra: dict[str, str] | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_extra} if env_extra else None
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, encoding="utf-8", errors="replace", env=env
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed (exit {proc.returncode}): {proc.stderr.strip()}")
    return proc


def _mkrepo(parent: Path, name: str = "repo", branch: str = "main") -> Path:
    repo = parent / name
    repo.mkdir()
    proc = subprocess.run(["git", "init", "-b", branch], cwd=str(repo), capture_output=True, encoding="utf-8")
    if proc.returncode != 0:  # git < 2.28 fallback
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", f"refs/heads/{branch}"], cwd=str(repo), capture_output=True)
    _run(repo, "config", "user.email", "git-trim@example.com")
    _run(repo, "config", "user.name", "git-trim test")
    _run(repo, "config", "commit.gpgsign", "false")
    _commit(repo, "README.md", "# demo\n", "initial commit")
    return repo


def _commit(repo: Path, fname: str, content: str, msg: str, date: str | None = None) -> str:
    """A commit that actually changes a file (empty commits have no patch-id)."""
    (repo / fname).write_text(content, encoding="utf-8")
    _run(repo, "add", "--", fname)
    env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else None
    _run(repo, "commit", "-q", "-m", msg, env_extra=env)
    return _run(repo, "rev-parse", "HEAD").stdout.strip()


def _commit_on(
    repo: Path, branch: str, changes: list[tuple[str, str]], date: str | None = None, start: str = "main"
) -> list[str]:
    _run(repo, "checkout", "-q", "-b", branch, start)
    return [_commit(repo, fname, content, f"{branch} #{i}", date=date) for i, (fname, content) in enumerate(changes, 1)]


def _merge(repo: Path, branch: str) -> None:
    _run(repo, "checkout", "-q", "main")
    _run(repo, "merge", "--no-ff", "-m", f"Merge branch '{branch}'", branch)


def _squash_merge(repo: Path, branch: str, msg: str) -> None:
    _run(repo, "checkout", "-q", "main")
    _run(repo, "merge", "--squash", branch)
    _run(repo, "commit", "-m", msg)


def _mkorigin(root: Path, head: str = "main") -> Path:
    origin = root / "origin.git"
    _run(root, "init", "--bare", str(origin))
    _run(origin, "symbolic-ref", "HEAD", f"refs/heads/{head}")
    return origin


# ============================================================================
# demo - `git-trim demo` builds a scenario repo and walks the whole workflow.
# ============================================================================


def _demo_cli(repo: Path, *argv: str) -> int:
    print(f"$ git-trim {' '.join(argv)}")
    rc = main(["-C", str(repo), *argv])
    note = "  (10 = safe candidates found)" if rc == EXIT_CANDIDATES else ""
    print(f"[exit {rc}]{note}")
    return rc


def cmd_demo(args: argparse.Namespace) -> int:
    root = Path(args.dir).expanduser().resolve() if args.dir else Path(tempfile.mkdtemp(prefix="git-trim-demo-"))
    if root.exists() and any(root.iterdir()):
        raise GitError(f"demo directory is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "project"

    def banner(title: str) -> None:
        print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)

    def note(text: str) -> None:
        print(f"  {text}")

    old_date = "@" + str(int(time.time() - 60 * 86400))  # 60 days ago, git env format

    banner(f"[1/8] building a demo repo with 8 branches in {root}")
    _mkrepo(root, name="project")
    origin = _mkorigin(root)
    _run(repo, "remote", "add", "origin", str(origin))

    _commit_on(repo, "bugfix/crash", [("crash.txt", "fix\n")])
    _merge(repo, "bugfix/crash")
    note("bugfix/crash  regular merge          -> 'ancestor' method")

    _commit_on(repo, "feat/login", [("login-1.txt", "1\n"), ("login-2.txt", "2\n")])
    _squash_merge(repo, "feat/login", "feat(login): add login (#11)")
    note("feat/login    squash of 2 commits    -> 'squash' method (combined patch-id)")

    _commit_on(repo, "feat/theme", [("theme.txt", "dark\n")])
    _squash_merge(repo, "feat/theme", "feat(theme): dark mode (#12)")
    note("feat/theme    squash of 1 commit     -> 'patch-id' method (subset match)")

    _commit_on(repo, "feat/done", [("done.txt", "done\n")])
    _squash_merge(repo, "feat/done", "chore: done (#13)")
    _run(repo, "push", "-u", "origin", "main")
    _run(repo, "push", "-u", "origin", "feat/done")
    _run(repo, "push", "origin", "--delete", "feat/done")
    _run(repo, "fetch", "--prune")
    note("feat/done     merged, remote deleted -> upstream 'gone' (prune-remote target)")

    _commit_on(repo, "feat/old", [("old.txt", "old\n")], date=old_date)
    _run(repo, "push", "-u", "origin", "feat/old")
    note("feat/old      unmerged, pushed, 60d  -> deletable (recoverable from origin)")

    _commit_on(repo, "feat/wip", [("wip.txt", "wip\n")])
    note("feat/wip      unmerged, no upstream  -> NEVER deleted")

    _commit_on(repo, "feat/wt", [("wt.txt", "wt\n")])
    _merge(repo, "feat/wt")
    _run(repo, "worktree", "add", str(root / "worktree"), "feat/wt")
    note("feat/wt       merged but checked out in ../worktree -> skipped")

    note("main          current + base         -> protected")

    banner("[2/8] git-trim scan  (table: name, last commit, merged?, method, safe?)")
    _demo_cli(repo, "scan")

    banner("[3/8] git-trim scan --json  (excerpt; one object per branch in full output)")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(["-C", str(repo), "scan", "--json"])
    data = json.loads(buf.getvalue())
    for br in data["branches"][:4]:
        print("  " + json.dumps(br))
    print(f"  ... {max(0, len(data['branches']) - 4)} more; candidates: {data['candidates']}")

    banner("[4/8] git-trim delete  (dry-run is the default - --yes is required to act)")
    _demo_cli(repo, "delete")

    banner("[5/8] git-trim prune-remote  (runs 'git fetch --prune' first, then looks for gone upstreams)")
    _demo_cli(repo, "prune-remote")

    banner("[6/8] git-trim prune-remote --yes")
    _demo_cli(repo, "prune-remote", "--yes")

    banner("[7/8] git-trim delete --yes")
    _demo_cli(repo, "delete", "--yes")

    banner("[8/8] git-trim undo  (recreates branches from .git/trim-log.jsonl at the exact SHA)")
    _demo_cli(repo, "undo")

    banner("final state: git-trim scan")
    _demo_cli(repo, "scan")

    if args.bench and args.bench > 0:
        banner(f"bonus: benchmark - adding {args.bench} unmerged branches and timing scan")
        for i in range(args.bench):
            _run(repo, "checkout", "-q", "-b", f"bench/{i:03d}", "main")
            _commit(repo, f"bench-{i:03d}.txt", f"{i}\n", f"bench {i}")
        _run(repo, "checkout", "-q", "main")
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(["-C", str(repo), "scan", "--json"])
        elapsed = time.perf_counter() - t0
        print(f"  scan over {args.bench} extra branches took {elapsed:.2f}s (exit {rc}; synthetic repo, cold cache)")

    print(f"\ndemo repo kept at: {root}")
    print(f"explore it:  cd {repo}  &&  git worktree list  &&  git-trim scan")
    return EXIT_OK


# ============================================================================
# tests - embedded pytest suite.  Run with:  pytest git_trim.py -q
# Integration tests build throwaway repos in tmp_path with subprocess git.
# NOTE: commits must change files - empty commits have empty diffs and no
# patch-id, which would make squash-detection tests meaningless.
# ============================================================================

try:
    import pytest
except ImportError:  # pragma: no cover - only hit when used as a plain script
    pytest = None  # type: ignore[assignment]


def _need_git() -> None:
    import shutil

    if pytest is not None and shutil.which("git") is None:
        pytest.skip("git not available")  # pragma: no cover


def _scan(repo: Path, capsys, *extra: str) -> tuple[int, dict]:
    rc = main(["-C", str(repo), "scan", "--json", *extra])
    return rc, json.loads(capsys.readouterr().out)


def _branch_map(data: dict) -> dict[str, dict]:
    return {b["name"]: b for b in data["branches"]}


def _exists(repo: Path, branch: str) -> bool:
    return _run(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).returncode == 0


# ------------------------------- unit tests ---------------------------------


def test_humanize_age() -> None:
    assert humanize_age(0) == "0m"
    assert humanize_age(30 / 1440) == "30m"
    assert humanize_age(5 / 24) == "5h"
    assert humanize_age(3.7) == "3d"
    assert humanize_age(20) == "2w"
    assert humanize_age(100) == "3mo"
    assert humanize_age(400) == "1.1y"


def test_parse_refs() -> None:
    raw = (
        "bugfix/x\x00abc123def456\x00origin/bugfix/x\x00gone\x001700000000 +0000\n"
        "main\x00def987abc000\x00\x00\x001700000001\n"
    )
    refs = parse_refs(raw)
    assert len(refs) == 2
    assert refs[0].name == "bugfix/x" and refs[0].sha == "abc123def456"
    assert refs[0].upstream == "origin/bugfix/x" and refs[0].upstream_gone
    assert refs[0].committed_ts == 1700000000.0
    assert refs[1].upstream == "" and not refs[1].upstream_gone
    assert refs[1].track == ""


def test_parse_patch_ids() -> None:
    assert _parse_patch_ids("id1 sha1\n\nid2 sha2\n") == {"id1", "id2"}
    assert _parse_patch_ids("") == set()
    assert _parse_patch_ids("ca29 0000...0\n") == {"ca29"}


def test_is_protected_case_insensitive() -> None:
    assert is_protected("Main", "main", ()) == "protected: base branch"
    assert is_protected("MAIN", "main", ()) == "protected: base branch"
    assert is_protected("feat/x", "main", ("release/*",)) is None
    assert is_protected("release/9.9", "main", ("release/*",)) is not None
    assert is_protected("RELEASE/9.9", "main", ("release/*",)) is not None


def test_split_patterns_and_match_any() -> None:
    assert _split_patterns(None) == ()
    assert _split_patterns([""]) == ()
    assert _split_patterns(["a, b", "c"]) == ("a", "b", "c")
    assert _match_any("FEAT/x", ("feat/*",))
    assert not _match_any("chore/x", ("feat/*",))
    assert _match_any("anything", ())


def test_render_table_truncates() -> None:
    headers = ["BRANCH", "WHY"]
    rows = [["feat/" + "x" * 80, "r" * 200]]
    out = render_table(headers, rows, caps=[34, 58])
    lines = out.splitlines()
    assert "..." in lines[2]
    assert all(len(line) <= 40 + 58 + 2 for line in lines)


# ---------------------------- integration tests -----------------------------


def test_plain_merge_classified_merged(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "bugfix/crash", [("crash.txt", "fix\n")])
    _merge(repo, "bugfix/crash")
    rc, data = _scan(repo, capsys)
    branch = _branch_map(data)["bugfix/crash"]
    assert branch["merged"] is True and branch["method"] == "ancestor"
    assert branch["safe"] is True and branch["reasons"] == []
    assert rc == EXIT_CANDIDATES


def test_single_commit_squash_detected(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "feat/theme", [("theme.txt", "dark\n")])
    _squash_merge(repo, "feat/theme", "feat(theme): dark mode (#12)")
    rc, data = _scan(repo, capsys)
    branch = _branch_map(data)["feat/theme"]
    assert branch["merged"] is True and branch["method"] == "patch-id"
    assert branch["safe"] is True
    assert rc == EXIT_CANDIDATES


def test_multicommit_squash_detected(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "feat/login", [("login-1.txt", "1\n"), ("login-2.txt", "2\n")])
    _squash_merge(repo, "feat/login", "feat(login): add login (#11)")
    rc, data = _scan(repo, capsys)
    branch = _branch_map(data)["feat/login"]
    assert branch["merged"] is True and branch["method"] == "squash"
    assert branch["safe"] is True


def test_rebase_merge_detected(tmp_path, capsys) -> None:
    """Rebase-merges replay each commit: the per-commit patch-id subset matches."""
    _need_git()
    repo = _mkrepo(tmp_path)
    shas = _commit_on(repo, "feat/rb", [("r1.txt", "1\n"), ("r2.txt", "2\n")])
    _run(repo, "checkout", "-q", "main")
    _commit(repo, "base-advance.txt", "b\n", "advance base")  # parents now differ, so the
    _run(repo, "cherry-pick", *shas)  # replayed commits are new objects (like a real rebase)
    rc, data = _scan(repo, capsys)
    branch = _branch_map(data)["feat/rb"]
    assert branch["merged"] is True and branch["method"] == "patch-id"
    assert branch["safe"] is True


def test_unmerged_branch_is_unsafe_and_survives_delete(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "feat/wip", [("wip.txt", "wip\n")])
    rc, data = _scan(repo, capsys)
    branch = _branch_map(data)["feat/wip"]
    assert branch["merged"] is False and branch["safe"] is False
    assert any(r == "unmerged: no upstream" for r in branch["reasons"])
    rc = main(["-C", str(repo), "delete", "--yes"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK and "nothing to delete" in captured.out
    assert _exists(repo, "feat/wip")


def test_branch_in_worktree_is_skipped(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "feat/wt", [("wt.txt", "wt\n")])
    _merge(repo, "feat/wt")
    _run(repo, "worktree", "add", str(tmp_path / "wt"), "feat/wt")
    rc, data = _scan(repo, capsys)
    branch = _branch_map(data)["feat/wt"]
    assert branch["merged"] is True and branch["safe"] is False
    assert branch["worktree"] == str(tmp_path / "wt")
    assert any("worktree" in r for r in branch["reasons"])
    main(["-C", str(repo), "delete", "--yes"])
    assert _exists(repo, "feat/wt")


def test_current_branch_is_never_a_candidate(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "bugfix/x", [("x.txt", "x\n")])
    _merge(repo, "bugfix/x")
    _run(repo, "checkout", "-q", "bugfix/x")  # merged, but checked out here
    rc, data = _scan(repo, capsys)
    branch = _branch_map(data)["bugfix/x"]
    assert branch["current"] is True and branch["safe"] is False
    assert "current branch" in branch["reasons"]
    assert rc == EXIT_OK


def test_keep_patterns_and_defaults(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    for name in ("develop", "release/9.9", "feat/x"):
        _commit_on(repo, name, [(f"{name.replace('/', '-')}.txt", "c\n")])
        _merge(repo, name)
    _rc, data = _scan(repo, capsys)  # defaults keep main,master,develop
    branches = _branch_map(data)
    assert branches["develop"]["safe"] is False
    assert "protected: matches --keep 'develop'" in branches["develop"]["reasons"]
    assert branches["feat/x"]["safe"] is True
    assert branches["release/9.9"]["safe"] is True
    _rc, data = _scan(repo, capsys, "--keep", "release/*")
    branches = _branch_map(data)
    assert branches["release/9.9"]["safe"] is False  # glob protected
    assert branches["develop"]["safe"] is True  # default keep was replaced
    assert branches["feat/x"]["safe"] is True


def test_dryrun_yes_undo_roundtrip(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    tip = _commit_on(repo, "feat/one", [("one.txt", "1\n")])[-1]
    _merge(repo, "feat/one")

    rc = main(["-C", str(repo), "delete"])  # dry-run is the default
    out = capsys.readouterr().out
    assert rc == EXIT_CANDIDATES and "dry-run" in out and "feat/one" in out
    assert _exists(repo, "feat/one")

    rc = main(["-C", str(repo), "delete", "--yes"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK and "deleted feat/one" in out
    assert not _exists(repo, "feat/one")

    log_file = git_dir(repo) / TRIM_LOG_NAME
    entries = [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]
    assert entries[-1]["name"] == "feat/one" and entries[-1]["sha"] == tip
    assert entries[-1]["source"] == "delete"

    rc = main(["-C", str(repo), "undo"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK and "restored feat/one" in out
    assert _run(repo, "rev-parse", "refs/heads/feat/one").stdout.strip() == tip  # exact SHA

    rc = main(["-C", str(repo), "undo"])  # idempotent
    out = capsys.readouterr().out
    assert rc == EXIT_OK and "already exist" in out


def test_undo_restores_upstream_tracking(tmp_path, capsys) -> None:
    """git branch -D drops branch.<name>.* config; undo re-links it when possible."""
    _need_git()
    repo = _mkrepo(tmp_path)
    origin = _mkorigin(tmp_path)
    _run(repo, "remote", "add", "origin", str(origin))
    _run(repo, "push", "-u", "origin", "main")
    old_date = "@" + str(int(time.time() - 60 * 86400))
    _commit_on(repo, "feat/old", [("old.txt", "old\n")], date=old_date)
    _run(repo, "push", "-u", "origin", "feat/old")
    _run(repo, "checkout", "-q", "main")

    rc, data = _scan(repo, capsys)
    assert _branch_map(data)["feat/old"]["safe"] is True  # unmerged but recoverable
    rc = main(["-C", str(repo), "delete", "--yes"])
    capsys.readouterr()
    assert rc == EXIT_OK and not _exists(repo, "feat/old")

    rc = main(["-C", str(repo), "undo"])
    capsys.readouterr()
    assert rc == EXIT_OK and _exists(repo, "feat/old")
    up = _run(repo, "rev-parse", "--abbrev-ref", "feat/old@{upstream}").stdout.strip()
    assert up == "origin/feat/old"

    _rc, data = _scan(repo, capsys)  # and it is safely deletable again
    assert _branch_map(data)["feat/old"]["safe"] is True


def test_delete_pattern_filters(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    for name in ("feat/a", "chore/b"):
        _commit_on(repo, name, [(f"{name.replace('/', '-')}.txt", "c\n")])
        _merge(repo, name)
    rc = main(["-C", str(repo), "delete", "--yes", "--pattern", "feat/*"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK and "deleted feat/a" in out
    assert not _exists(repo, "feat/a") and _exists(repo, "chore/b")
    assert "excluded by --pattern" in out


def test_scan_exit_codes(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    rc, _data = _scan(repo, capsys)  # only main -> nothing to do
    assert rc == EXIT_OK
    _commit_on(repo, "feat/one", [("one.txt", "1\n")])
    _merge(repo, "feat/one")
    rc, _data = _scan(repo, capsys)
    assert rc == EXIT_CANDIDATES
    rc = main(["-C", str(tmp_path / "does-not-exist"), "scan"])
    assert rc == EXIT_ERROR and "error" in capsys.readouterr().err
    rc = main(["-C", str(repo), "scan", "--base", "no-such-ref"])
    assert rc == EXIT_ERROR and "error" in capsys.readouterr().err


def test_scan_json_shape(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "feat/one", [("one.txt", "1\n")])
    _merge(repo, "feat/one")
    _rc, data = _scan(repo, capsys)
    assert data["tool"] == "git-trim" and data["base"] == "main"
    assert data["shallow"] is False and data["current"] == "main"
    branch = _branch_map(data)["feat/one"]
    assert {
        "name",
        "sha",
        "upstream",
        "upstream_gone",
        "merged",
        "method",
        "safe",
        "reasons",
        "age_days",
        "last_commit_iso",
        "current",
        "worktree",
        "why",
    } <= set(branch)
    assert data["candidates"] == [b["name"] for b in data["branches"] if b["safe"]]
    assert data["summary"]["total"] == len(data["branches"]) == 2
    assert data["summary"]["safe_to_delete"] == 1


def test_gone_upstream_counts_as_no_upstream(tmp_path, capsys) -> None:
    """Unmerged + upstream deleted on the remote must be refused."""
    _need_git()
    repo = _mkrepo(tmp_path)
    origin = _mkorigin(tmp_path)
    _run(repo, "remote", "add", "origin", str(origin))
    _run(repo, "push", "-u", "origin", "main")
    _commit_on(repo, "feat/wip", [("wip.txt", "wip\n")])
    _run(repo, "push", "-u", "origin", "feat/wip")
    _run(repo, "push", "origin", "--delete", "feat/wip")
    _run(repo, "fetch", "--prune")
    _rc, data = _scan(repo, capsys)
    branch = _branch_map(data)["feat/wip"]
    assert branch["upstream"] == "origin/feat/wip" and branch["upstream_gone"] is True
    assert branch["merged"] is False and branch["safe"] is False
    assert any("gone" in r for r in branch["reasons"])
    rc = main(["-C", str(repo), "prune-remote", "--yes"])
    capsys.readouterr()
    assert rc == EXIT_OK and _exists(repo, "feat/wip")


def test_prune_remote_deletes_merged_gone_and_undo_restores(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    origin = _mkorigin(tmp_path)
    _run(repo, "remote", "add", "origin", str(origin))
    _run(repo, "push", "-u", "origin", "main")
    _commit_on(repo, "feat/done", [("done.txt", "done\n")])
    _squash_merge(repo, "feat/done", "chore: done (#13)")
    _run(repo, "push", "-u", "origin", "feat/done")
    _run(repo, "push", "origin", "--delete", "feat/done")
    _run(repo, "fetch", "--prune")

    _rc, data = _scan(repo, capsys)
    branch = _branch_map(data)["feat/done"]
    assert branch["merged"] is True and branch["upstream_gone"] is True and branch["safe"] is True

    rc = main(["-C", str(repo), "prune-remote"])  # dry-run
    out = capsys.readouterr().out
    assert rc == EXIT_CANDIDATES and "feat/done" in out and "dry-run" in out
    assert _exists(repo, "feat/done")

    rc = main(["-C", str(repo), "prune-remote", "--yes"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK and "deleted feat/done" in out
    assert not _exists(repo, "feat/done")
    entries = [json.loads(l) for l in (git_dir(repo) / TRIM_LOG_NAME).read_text().splitlines() if l.strip()]
    assert entries[-1]["source"] == "prune-remote" and entries[-1]["name"] == "feat/done"

    rc = main(["-C", str(repo), "undo"])
    assert rc == EXIT_OK and _exists(repo, "feat/done")


def test_stale_window_rules(tmp_path, capsys) -> None:
    """Unmerged branches with a live upstream: old+pushed = deletable, fresh = refused."""
    _need_git()
    repo = _mkrepo(tmp_path)
    origin = _mkorigin(tmp_path)
    _run(repo, "remote", "add", "origin", str(origin))
    _run(repo, "push", "-u", "origin", "main")
    old_date = "@" + str(int(time.time() - 60 * 86400))
    _commit_on(repo, "feat/old", [("old.txt", "old\n")], date=old_date)
    _run(repo, "push", "-u", "origin", "feat/old")
    _commit_on(repo, "feat/fresh", [("fresh.txt", "f\n")])
    _run(repo, "push", "-u", "origin", "feat/fresh")

    _rc, data = _scan(repo, capsys)
    branches = _branch_map(data)
    assert branches["feat/old"]["merged"] is False and branches["feat/old"]["safe"] is True
    assert branches["feat/old"]["reasons"] == []
    fresh = branches["feat/fresh"]
    assert fresh["safe"] is False
    assert any(r.startswith("unmerged: last commit") for r in fresh["reasons"])

    _rc, data = _scan(repo, capsys, "--stale", "90")
    branches = _branch_map(data)
    assert branches["feat/old"]["safe"] is False  # 60d < 90d window


def test_shallow_clone_downgrades_to_unknown(tmp_path, capsys) -> None:
    """Shallow repositories cannot compute trustworthy patch-ids: never safe."""
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "feat/sq", [("sq.txt", "sq\n")])
    _squash_merge(repo, "feat/sq", "chore: sq (#21)")
    root = _run(repo, "rev-list", "--max-parents=0", "HEAD").stdout.split()[0]
    (repo / ".git" / "shallow").write_text(root + "\n")  # simulate a shallow clone
    rc, data = _scan(repo, capsys)
    assert data["shallow"] is True
    branch = _branch_map(data)["feat/sq"]
    assert branch["merged"] is None and branch["safe"] is False
    assert any("shallow" in r for r in branch["reasons"])
    assert rc == EXIT_OK  # nothing is safe to delete
    assert not _exists(repo, "nonexistent")  # sanity: repo still intact


def test_orphan_upstream_warning(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "lib", [("lib.txt", "l\n")])
    _merge(repo, "lib")
    _commit_on(repo, "tool", [("tool.txt", "t\n")])
    _run(repo, "branch", "--set-upstream-to=lib", "tool")  # tool tracks LOCAL branch lib
    rc = main(["-C", str(repo), "delete", "--yes"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK and "deleted lib" in captured.out
    assert "warning" in captured.err and "tool" in captured.err and "lib" in captured.err


def test_detached_head_scan_works(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "bugfix/x", [("x.txt", "x\n")])
    _merge(repo, "bugfix/x")
    _run(repo, "checkout", "-q", "--detach")
    rc, data = _scan(repo, capsys)
    assert rc == EXIT_CANDIDATES
    assert data["current"] is None
    assert "bugfix/x" in data["candidates"]


def test_base_autodetect_master_and_origin_head(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path, branch="master")
    _rc, data = _scan(repo, capsys)
    assert data["base"] == "master"

    repo2 = _mkrepo(tmp_path, name="repo2")
    _commit_on(repo2, "trunk", [("t.txt", "t\n")])
    origin = _mkorigin(tmp_path)
    _run(repo2, "remote", "add", "origin", str(origin))
    _run(repo2, "push", "-u", "origin", "main", "trunk")
    _run(repo2, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    _rc, data2 = _scan(repo2, capsys)
    assert data2["base"] == "trunk"  # origin/HEAD wins over 'main'


def test_base_prefers_remote_when_strictly_ahead(tmp_path, capsys) -> None:
    """The GitHub flow: squash merged on the remote, local main not yet pulled."""
    _need_git()
    repo = _mkrepo(tmp_path)
    origin = _mkorigin(tmp_path)
    _run(repo, "remote", "add", "origin", str(origin))
    _run(repo, "push", "-u", "origin", "main")
    _commit_on(repo, "feat/x", [("x.txt", "x\n")])
    _run(repo, "push", "origin", "feat/x")  # no -u: deliberately no upstream
    _run(repo, "checkout", "-q", "main")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], cwd=str(tmp_path), capture_output=True, text=True)
    _run(clone, "config", "user.email", "git-trim@example.com")
    _run(clone, "config", "user.name", "git-trim test")
    _run(clone, "checkout", "-q", "main")
    _run(clone, "merge", "--squash", "origin/feat/x")
    _run(clone, "commit", "-m", "feat(x): x (#31)")
    _run(clone, "push", "origin", "main")

    _run(repo, "fetch", "origin")
    rc, data = _scan(repo, capsys)
    assert data["base"] == "origin/main"  # remote strictly ahead -> used as base
    branch = _branch_map(data)["feat/x"]
    assert branch["merged"] is True and branch["safe"] is True
    assert rc == EXIT_CANDIDATES


def test_undo_without_log(tmp_path, capsys) -> None:
    _need_git()
    repo = _mkrepo(tmp_path)
    rc = main(["-C", str(repo), "undo"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK and "nothing to undo" in out


def test_extra_unmerged_commit_keeps_branch_unmerged(tmp_path, capsys) -> None:
    """Squash-merged, then one more commit afterwards -> NOT fully landed."""
    _need_git()
    repo = _mkrepo(tmp_path)
    _commit_on(repo, "feat/plus", [("p1.txt", "1\n")])
    _squash_merge(repo, "feat/plus", "feat(plus): p1 (#41)")
    _run(repo, "checkout", "-q", "feat/plus")
    _commit(repo, "p2.txt", "2\n", "feat/plus #2")  # extra, unmerged work
    _run(repo, "checkout", "-q", "main")
    rc, data = _scan(repo, capsys)
    branch = _branch_map(data)["feat/plus"]
    assert branch["merged"] is False and branch["safe"] is False
    assert rc == EXIT_OK


def test_demo_runs(tmp_path, capsys) -> None:
    _need_git()
    rc = main(["demo", "--dir", str(tmp_path / "demo")])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "squash" in out and "git-trim scan" in out
    assert "restored" in out and "prune-remote" in out and "undo" in out


if __name__ == "__main__":
    raise SystemExit(main())
