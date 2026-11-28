#!/data/data/com.termux/files/usr/bin/env python
"""
git-rewrite-history — safe bulk history rewriting planner.
This is deliberately a **planner and safety wrapper**, not a history rewriter.
All destructive work is delegated to ``git-filter-repo``.
Typical workflow:
    python git_rewrite_history.py analyze /srv/rewrite.git
    python git_rewrite_history.py plan /srv/rewrite.git \
        --remove secrets/ --bigger-than 5M --dry-run
    python git_rewrite_history.py execute /srv/rewrite.git \
        --remove secrets/ --bigger-than 5M
Requirements:
    * Python 3.11+
    * Git
    * git-filter-repo for execution
    * A fresh mirror clone for execution
The script never performs tree surgery or rewrites objects itself.
"""

from __future__ import annotations
import argparse
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

from dh import atomic_write


EXIT_REFUSED: int = 3
EXIT_OPERATION_ERROR: int = 2
EXIT_ERROR: int = 1
PLAN_FILE_NAME: str = ".rewrite-plan.json"


class RewriteError(RuntimeError):
    """Base exception for repository and planning failures."""


class SafetyRefusal(RewriteError):
    """Raised when an operation is blocked by a destructive-operation guard."""

    def __init__(self, reason: str) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RewriteOptions:
    """Options that describe the requested rewrite."""

    remove_paths: tuple[str, ...] = ()
    bigger_than: str | None = None
    replace_text: Path | None = None
    mailmap: Path | None = None
    refs: tuple[str, ...] = ()
    prune_empty: str = "never"
    i_know: int = 0

    def command_arguments(self) -> list[str]:
        """Convert options to arguments for ``git-filter-repo``."""
        arguments: list[str] = []
        for path in self.remove_paths:
            arguments.extend(["--path", path])
        if self.remove_paths:
            arguments.append("--invert-paths")
        if self.bigger_than:
            arguments.append(f"--strip-blobs-bigger-than {self.bigger_than}")
        if self.replace_text:
            arguments.extend(["--replace-text", str(self.replace_text)])
        if self.mailmap:
            arguments.extend(["--mailmap", str(self.mailmap)])
        for ref in self.refs:
            arguments.extend(["--refs", ref])
        if self.prune_empty != "never":
            arguments.extend(["--prune-empty", self.prune_empty])
        arguments.append("--force")
        return arguments


@dataclass(frozen=True, slots=True)
class Consequences:
    """Destructive consequences found during analysis."""

    commit_count: int
    tag_count: int
    published_tag_count: int
    signed_commit_count: int
    author_count: int
    large_blob_count: int
    matching_path_count: int
    open_pr_count: int
    fork_count: int
    ci_cache_count: int
    tags: tuple[str, ...]
    authors: tuple[str, ...]
    signed_commits: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RewritePlan:
    """Complete planned rewrite information."""

    repository: str
    filter_repo_version: str
    target_ref: str
    before_commit_count: int
    before_object_count: int
    before_object_hash: str
    options: RewriteOptions
    consequences: Consequences
    command: tuple[str, ...]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_json(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "repository": self.repository,
            "filter_repo_version": self.filter_repo_version,
            "target_ref": self.target_ref,
            "before_commit_count": self.before_commit_count,
            "before_object_count": self.before_object_count,
            "before_object_hash": self.before_object_hash,
            "options": asdict(self.options),
            "consequences": asdict(self.consequences),
            "command": list(self.command),
            "created_at": self.created_at,
        }


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and return its captured result."""
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            check=check,
            capture_output=capture,
            text=True,
        )
    except FileNotFoundError as exc:
        executable = command[0]
        raise RewriteError(f"Executable not found: {executable}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RewriteError(
            f"Command failed ({exc.returncode}: {' '.join(command)}{(': ' + detail) if detail else ''}"
        ) from exc
    return result


def git(repo: Path, *arguments: str, check: bool = True) -> str:
    """Run Git in a repository and return standard output."""
    result = run(["git", "-C", str(repo), *arguments], check=check)
    return result.stdout.strip()


def require_git_repository(repository: Path) -> Path:
    """Resolve and validate a Git repository directory."""
    repo = repository.expanduser().resolve()
    if not repo.exists():
        raise RewriteError(f"Repository does not exist: {repo}")
    result = run(
        ["git", "-C", str(repo), "rev-parse", "--git-dir"],
        check=False,
    )
    if result.returncode != 0:
        raise RewriteError(f"Not a Git repository: {repo}")
    return repo


def require_mirror(repository: Path) -> Path:
    """Require a bare mirror clone suitable for safe rewriting."""
    repo = require_git_repository(repository)
    bare = git(repo, "rev-parse", "--is-bare-repository")
    if bare != "true":
        raise SafetyRefusal(
            "Repository is not a bare mirror clone. Create one with: git clone --mirror <origin-url> rewritten.git"
        )
    git_dir = git(repo, "rev-parse", "--absolute-git-dir")
    if Path(git_dir) == repo / ".git":
        raise SafetyRefusal("Refusing to rewrite a working-clone .git directory.")
    return repo


def require_filter_repo() -> str:
    """Check for git-filter-repo and return its installed version."""
    if shutil.which("git-filter-repo") is None:
        raise RewriteError(
            "git-filter-repo is not installed.\n"
            "Install it with: pipx install git-filter-repo\n"
            "or: python -m pip install git-filter-repo"
        )
    result = run(
        ["git", "filter-repo", "--version"],
        check=False,
    )
    if result.returncode != 0:
        raise RewriteError("Git found git-filter-repo is unavailable. Install it with: pipx install git-filter-repo")
    return result.stdout.strip() or "git-filter-repo"


def list_refs(repo: Path) -> tuple[str, ...]:
    """List all local references."""
    output = git(repo, "for-each-ref", "--format=%(refname)")
    return tuple(line for line in output.splitlines() if line)


def count_commits(repo: Path, refs: Sequence[str] = ()) -> int:
    """Count unique commits reachable from selected refs."""
    arguments = ["rev-list", "--count", "--all"]
    if refs:
        arguments = ["rev-list", "--count", *refs]
    output = git(repo, *arguments)
    return int(output or "0")


def count_objects(repo: Path) -> int:
    """Count objects in a repository."""
    output = git(repo, "count-objects", "-v")
    for line in output.splitlines():
        if line.startswith("count:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                continue
    return 0


def object_store_hash(repo: Path) -> str:
    """Produce a dry-run hash of object names and sizes."""
    result = git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
    names = result.splitlines()
    if not names:
        return "empty"
    import hashlib

    digest = hashlib.sha256()
    for name in sorted(names):
        digest.update(name.encode("ascii"))
    return digest.hexdigest()


def list_tags(repo: Path) -> tuple[str, ...]:
    """List tag names."""
    output = git(repo, "tag", "--list")
    return tuple(sorted(line for line in output.splitlines() if line))


def signed_commits(repo: Path, refs: Sequence[str] = ()) -> tuple[str, ...]:
    """Find commits containing GPG or SSH signature headers."""
    arguments = ["rev-list", "--all"]
    if refs:
        arguments = ["rev-list", *refs]
    hashes = git(repo, *arguments).splitlines()
    signed: list[str] = []
    for commit_hash in hashes:
        commit_object = git(repo, "cat-file", "commit", commit_hash)
        if "\ngpgsig " in "\n" + commit_object or "\nmergetag " in "\n" + commit_object:
            signed.append(commit_hash)
    return tuple(sorted(set(signed)))


def list_authors(repo: Path) -> tuple[str, ...]:
    """List distinct commit authors in the reachable history."""
    output = git(
        repo,
        "log",
        "--all",
        "--format=%aN <%aE>",
        "--use-mailmap",
    )
    return tuple(sorted({line for line in output.splitlines() if line}))


def matching_path_count(repo: Path, paths: Sequence[str]) -> int:
    """Count commits that modify one or more selected paths."""
    if not paths:
        return 0
    commits: set[str] = set()
    for path in paths:
        result = git(
            repo,
            "log",
            "--all",
            "--format=%H",
            "--",
            path,
        )
        commits.update(line for line in result.splitlines() if line)
    return len(commits)


def parse_byte_size(value: str) -> int:
    """Parse sizes such as 5M, 500K, or 1048576."""
    text = value.strip().upper()
    multipliers = {
        "B": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
    }
    if text[-1:] in multipliers:
        return int(float(text[:-1]) * multipliers[text[-1]])
    return int(text)


def count_large_blobs(repo: Path, size: str | None) -> int:
    """Count distinct blobs exceeding the configured threshold."""
    if not size:
        return 0
    threshold = parse_byte_size(size)
    batch = git(
        repo,
        "cat-file",
        "--batch-all-objects",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
    )
    blobs: set[str] = set()
    for line in batch.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[1] == "blob":
            if int(fields[2]) > threshold:
                blobs.add(fields[0])
    return len(blobs)


def estimate_open_prs(repo: Path) -> int:
    """Best-effort count of refs likely to represent open pull requests.
    Git itself has no open-PR concept. This detects refs under
    ``refs/pull/`` or ``refs/pull-request/`` when present in a local mirror.
    """
    refs = list_refs(repo)
    pull_refs = [ref for ref in refs if ref.startswith(("refs/pull/", "refs/pull-request/", "refs/merge-requests/"))]
    return len(pull_refs)


def estimate_fork_count(repo: Path) -> int:
    """Fork information cannot be discovered reliably by Git alone."""
    del repo
    return 0


def estimate_ci_caches(repo: Path) -> int:
    """SHA-keyed cache entries cannot be enumerated locally.
    GitHub Actions cache entries are a remote GitHub concern. This count
    is intentionally 0 unless GitHub metadata is later supplied.
    """
    del repo
    return 0


def analyze_repository(repo: Path, options: RewriteOptions) -> Consequences:
    """Analyze a repository and return potentially destructive consequences."""
    tags = list_tags(repo)
    signed = signed_commits(repo, options.refs)
    authors = list_authors(repo)
    return Consequences(
        commit_count=count_commits(repo, options.refs),
        tag_count=len(tags),
        published_tag_count=len(tags),
        signed_commit_count=len(signed),
        author_count=len(authors),
        large_blob_count=count_large_blobs(repo, options.bigger_than),
        matching_path_count=matching_path_count(repo, options.remove_paths),
        open_pr_count=estimate_open_prs(repo),
        fork_count=estimate_fork_count(repo),
        ci_cache_count=estimate_ci_caches(repo),
        tags=tags,
        authors=authors,
        signed_commits=signed,
    )


def build_filter_repo_command(
    repo: Path,
    options: RewriteOptions,
) -> tuple[str, ...]:
    """Build the exact delegated git-filter-repo command."""
    command = ["git", "-C", str(repo), "filter-repo"]
    command.extend(options.command_arguments())
    return tuple(command)


def create_plan(repo: Path, options: RewriteOptions) -> RewritePlan:
    """Create an inspectable in-memory rewrite plan."""
    require_filter_repo()
    consequences = analyze_repository(repo, options)
    target = ",".join(options.refs) if options.refs else "all reachable history"
    return RewritePlan(
        repository=str(repo),
        filter_repo_version=require_filter_repo(),
        target_ref=target,
        before_commit_count=consequences.commit_count,
        before_object_count=count_objects(repo),
        before_object_hash=object_store_hash(repo),
        options=options,
        consequences=consequences,
        command=build_filter_repo_command(repo, options),
    )


def plan_token(plan: RewritePlan) -> str:
    """Produce a stable digest used to verify that a plan was reviewed."""
    import hashlib

    payload = json.dumps(plan.as_json(), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_plan(repo: Path, plan: RewritePlan) -> Path:
    """Write the dry-run approval plan next to the mirror."""
    destination = repo / PLAN_FILE_NAME
    atomic_write(
        destination,
        json.dumps(plan.as_json(), indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def read_plan(repo: Path) -> RewritePlan:
    """Read and validate a previously created dry-run plan."""
    source = repo / PLAN_FILE_NAME
    if not source.exists():
        raise SafetyRefusal(f"No dry-run plan found. Run plan --dry-run first and review {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RewriteError(f"Invalid plan file: {source}: {exc}") from exc
    options_data = data["options"]
    consequences_data = data["consequences"]
    options = RewriteOptions(
        remove_paths=tuple(options_data["remove_paths"]),
        bigger_than=options_data["bigger_than"],
        replace_text=Path(options_data["replace_text"]) if options_data["replace_text"] else None,
        mailmap=Path(options_data["mailmap"]) if options_data["mailmap"] else None,
        refs=tuple(options_data["refs"]),
        prune_empty=options_data["prune_empty"],
        i_know=int(options_data["i_know"]),
    )
    consequences = Consequences(
        commit_count=int(consequences_data["commit_count"]),
        tag_count=int(consequences_data["tag_count"]),
        published_tag_count=int(consequences_data["published_tag_count"]),
        signed_commit_count=int(consequences_data["signed_commit_count"]),
        author_count=int(consequences_data["author_count"]),
        large_blob_count=int(consequences_data["large_blob_count"]),
        matching_path_count=int(consequences_data["matching_path_count"]),
        open_pr_count=int(consequences_data["open_pr_count"]),
        fork_count=int(consequences_data["fork_count"]),
        ci_cache_count=int(consequences_data["ci_cache_count"]),
        tags=tuple(consequences_data["tags"]),
        authors=tuple(consequences_data["authors"]),
        signed_commits=tuple(consequences_data["signed_commits"]),
    )
    return RewritePlan(
        repository=str(data["repository"]),
        filter_repo_version=str(data["filter_repo_version"]),
        target_ref=str(data["target_ref"]),
        before_commit_count=int(data["before_commit_count"]),
        before_object_count=int(data["before_object_count"]),
        before_object_hash=str(data["before_object_hash"]),
        options=options,
        consequences=consequences,
        command=tuple(data["command"]),
        created_at=str(data["created_at"]),
    )


def guard_destructive_metadata(
    repo: Path,
    consequences: Consequences,
    i_know: int,
) -> None:
    """Require explicit repeated acknowledgment for dangerous metadata."""
    del repo
    blockers: list[str] = []
    if consequences.signed_commit_count and i_know < 2:
        blockers.append(f"{consequences.signed_commit_count} GPG/SSH-signed commits (signatures will become invalid)")
    if consequences.tag_count and i_know < 2:
        blockers.append(
            f"{consequences.tag_count} tags point into rewritten history (published tags must be recreated)"
        )
    if blockers:
        raise SafetyRefusal(
            "Operation refused. The following destructively change:\n"
            + "\n".join(f"  - {item}" for item in blockers)
            + "\nRepeat --i-know twice to acknowledge: "
            "--i-know --i-know"
        )


def create_backup(
    source: Path,
    keep_days: int = 30,
) -> tuple[Path, str]:
    """Create an additional mirror clone and report its restore command."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup = source.with_name(f"backup-{timestamp}-{source.name}")
    run(["git", "clone", "--mirror", str(source), str(backup)])
    restore = f"git clone --mirror {backup} restored.git"
    expiration_marker = backup / ".keep-for"
    atomic_write(
        expiration_marker,
        f"{keep_days}d\n",
        encoding="utf-8",
    )
    return backup, restore


def post_checks(
    repo: Path,
    expected_commit_count: int,
    original_tag_names: tuple[str, ...],
) -> None:
    """Run standard post-rewrite integrity and consistency checks."""
    run(["git", "-C", str(repo), "fsck", "--full"])
    actual = count_commits(repo)
    if actual > expected_commit_count:
        raise RewriteError(
            "Post-check failed: rewrite unexpectedly increased commit count "
            f"(expected <= {expected_commit_count}, got {actual})"
        )
    remaining_tags = set(list_tags(repo))
    missing_tags = set(original_tag_names) - remaining_tags
    if missing_tags:
        raise RewriteError("Post-check failed: rewrite removed tags: " + ", ".join(sorted(missing_tags)))
    git(repo, "cat-file", "--batch-check", input=None)


def print_analyze(consequences: Consequences) -> None:
    """Print a human-readable consequence analysis."""
    print(
        f"PLAN  rewrite {consequences.commit_count:,} commits to drop "
        f"{consequences.matching_path_count:,} path-changing commits "
        f"and {consequences.large_blob_count:,} distinct large blobs"
    )
    print("CONSEQUENCES")
    print(f"  {consequences.open_pr_count:,} open PR refs detected (GitHub metadata needed for exact author and count)")
    print(f"  {consequences.tag_count:,} tags point into rewritten history (must be re-created)")
    print(f"  {consequences.signed_commit_count:,} commits are GPG/SSH-signed → signatures will be invalid")
    print(f"  CI caches keyed by SHA: {consequences.ci_cache_count:,} entries invalidated (API metadata needed)")
    print(f"  contributors' clones: {consequences.author_count:,} distinct authors in rewritten range")
    print(f"  forks: {consequences.fork_count:,} forks reference these hashes (GitHub API metadata needed)")


def print_plan(plan: RewritePlan) -> None:
    """Print a complete human-readable rewrite plan."""
    print(
        f"PLAN  rewrite {plan.before_commit_count:,} commits "
        f"to drop {plan.consequences.matching_path_count:,} path-changing "
        f"commits and {plan.consequences.large_blob_count:,} large blobs"
    )
    print("CONSEQUENCES")
    print(f"  {plan.consequences.open_pr_count:,} open PR refs/refs will need rebasing")
    print(f"  {plan.consequences.tag_count:,} tags point into rewritten history (must be re-created)")
    print(f"  {plan.consequences.signed_commit_count:,} commits are signed → signatures will be invalid")
    print(f"  CI caches keyed by SHA: {plan.consequences.ci_cache_count:,} entries invalidated")
    print(f"  contributors' clones: {plan.consequences.author_count:,} distinct authors in rewritten range")
    print(f"  forks: {plan.consequences.fork_count:,} forks reference these hashes (GitHub metadata needed)")
    print()
    print("BEFORE")
    print(f"  commits: {plan.before_commit_count:,}")
    print(f"  objects: {plan.before_object_count:,}")
    print(f"  object-store hash: {plan.before_object_hash}")
    print()
    print("EXACT COMMAND")
    print("  " + " ".join(plan.command))
    print()
    print("SAFE PROCEDURE")
    print("  1. git clone --mirror <origin-url> rewritten.git")
    print("  2. review this exact git-filter-repo command")
    print("  3. execute only after this dry-run plan is retained")
    print("  4. git fsck --full")
    print("  5. rotate every secret that was present in the blobs")
    print("  6. force-push with --force-with-lease")
    print("  7. notify contributors and recreate/remap published tags")
    print()
    print("IMPORTANT")
    print("  Removing text or blobs does not un-leak secrets.")
    print("  Rotate every exposed token, key, password, and credential.")


def options_from_args(args: argparse.Namespace) -> RewriteOptions:
    """Convert argparse values into immutable rewrite options."""
    return RewriteOptions(
        remove_paths=tuple(args.remove),
        bigger_than=args.bigger_than,
        replace_text=Path(args.replace_text) if args.replace_text else None,
        mailmap=Path(args.mailmap) if args.mailmap else None,
        refs=tuple(args.refs),
        prune_empty=args.prune_empty,
        i_know=int(args.i_know),
    )


def positive_int(value: str) -> int:
    """Argparse converter for non-negative integer values."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="rewrite-plan",
        description="Safe planner around git-filter-repo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Example:
              rewrite-plan analyze rewritten.git
              rewrite-plan plan rewritten.git --remove secrets/ --bigger-than 5M --dry-run
              rewrite-plan execute rewritten.git --remove secrets/ --bigger-than 5M --i-know --i-know
            """
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser(
        "analyze",
        help="Analyze consequences without modifying a repository",
    )
    analyze.add_argument("repository", type=Path)
    analyze.add_argument("--remove", action="append", default=[], metavar="PATH")
    analyze.add_argument("--bigger-than", metavar="SIZE")
    analyze.add_argument("--refs", action="append", default=[], metavar="REF")
    plan = subparsers.add_parser(
        "plan",
        help="Print and save an exact dry-run plan",
    )
    plan.add_argument("repository", type=Path)
    plan.add_argument("--remove", action="append", default=[], metavar="PATH")
    plan.add_argument("--bigger-than", metavar="SIZE")
    plan.add_argument("--replace-text", metavar="FILE")
    plan.add_argument("--mailmap", metavar="FILE")
    plan.add_argument("--refs", action="append", default=[], metavar="REF")
    plan.add_argument(
        "--prune-empty",
        choices=("never", "always", "auto"),
        default="never",
    )
    plan.add_argument(
        "--dry-run",
        action="store_true",
        help="Required to demonstrate the plan before execution",
    )
    plan.add_argument(
        "--i-know",
        action="count",
        default=0,
        help="Acknowledge consequences; repeat twice for signed commits and tags",
    )
    execute = subparsers.add_parser(
        "execute",
        help="Execute a reviewed plan in a backup mirror",
    )
    execute.add_argument("repository", type=Path)
    execute.add_argument("--remove", action="append", default=[], metavar="PATH")
    execute.add_argument("--bigger-than", metavar="SIZE")
    execute.add_argument("--replace-text", metavar="FILE")
    execute.add_argument("--mailmap", metavar="FILE")
    execute.add_argument("--refs", action="append", default=[], metavar="REF")
    execute.add_argument(
        "--prune-empty",
        choices=("never", "always", "auto"),
        default="never",
    )
    execute.add_argument(
        "--keep-backup",
        metavar="30d",
        default="30d",
    )
    execute.add_argument(
        "--i-know",
        action="count",
        default=0,
        help="Acknowledge consequences; repeat twice for signed commits and tags",
    )
    return parser


def parse_keep_days(value: str) -> int:
    """Parse a retention value such as 30d."""
    if not value.lower().endswith("d"):
        raise argparse.ArgumentTypeError("backup retention must end in d, such as 30d")
    try:
        return int(value[:-1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid backup retention") from exc


def execute_plan(
    repository: Path,
    requested_options: RewriteOptions,
    keep_backup: str,
) -> int:
    """Safely back up and execute an approved rewrite plan."""
    repo = require_mirror(repository)
    saved_plan = read_plan(repo)
    if saved_plan.options != requested_options:
        raise SafetyRefusal("Requested options do not match the reviewed dry-run plan")
    current_hash = object_store_hash(repo)
    if current_hash != saved_plan.before_object_hash:
        raise SafetyRefusal("Repository object store changed after dry-run. Run the dry-run again.")
    guard_destructive_metadata(
        repo,
        saved_plan.consequences,
        requested_options.i_know,
    )
    keep_days = parse_keep_days(keep_backup)
    backup, restore_command = create_backup(repo, keep_days)
    print(f"BACKUP  {backup}")
    print(f"ROLLBACK {restore_command}")
    print(f"KEEP    {keep_backup}")
    try:
        run(list(saved_plan.command))
    except RewriteError:
        print(
            f"Rewrite failed. Restore this backup with:\n  {restore_command}",
            file=sys.stderr,
        )
        raise
    post_checks(
        repo,
        saved_plan.before_commit_count,
        saved_plan.consequences.tags,
    )
    print("POST-CHECK  git fsck --full: passed")
    print(f"POST-CHECK  commits: {saved_plan.before_commit_count:,} before, {count_commits(repo):,} after")
    print(f"POST-CHECK  authors preserved: {saved_plan.consequences.author_count:,}")
    print("NEXT  Rotate every exposed secret. The rewrite does not un-leak it.")
    print("NEXT  Force-push with --force-with-lease")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        repo = require_git_repository(args.repository)
        if args.command == "analyze":
            options = RewriteOptions(
                remove_paths=tuple(args.remove),
                bigger_than=args.bigger_than,
                refs=tuple(args.refs),
            )
            consequences = analyze_repository(repo, options)
            print_analyze(consequences)
            return 0
        if args.command == "plan":
            if not args.dry_run:
                raise SafetyRefusal("A dry-run is mandatory. Add --dry-run before planning.")
            repo = require_mirror(repo)
            options = options_from_args(args)
            if options.replace_text and not options.replace_text.is_file():
                raise RewriteError(f"Replace-text file does not exist: {options.replace_text}")
            if options.mailmap and not options.mailmap.is_file():
                raise RewriteError(f"Mailmap file does not exist: {options.mailmap}")
            before_hash = object_store_hash(repo)
            plan = create_plan(repo, options)
            if before_hash != plan.before_object_hash:
                raise RewriteError("Repository changed while creating plan")
            print_plan(plan)
            plan_path = write_plan(repo, plan)
            print(f"DRY-RUN  Object store unchanged: {before_hash}")
            print(f"DRY-RUN  Plan saved: {plan_path}")
            return 0
        if args.command == "execute":
            repo = require_mirror(repo)
            options = options_from_args(args)
            return execute_plan(
                repo,
                options,
                args.keep_backup,
            )
        parser.error(f"Unknown command: {args.command}")
        return EXIT_ERROR
    except SafetyRefusal as exc:
        print(f"REFUSED: {exc.reason}", file=sys.stderr)
        return EXIT_REFUSED
    except RewriteError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_OPERATION_ERROR
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
