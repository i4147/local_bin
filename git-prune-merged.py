#!/data/data/com.termux/files/usr/bin/env python
"List and bulk-delete branches that are already merged into the base branch."

from __future__ import annotations
from dataclasses import asdict, dataclass
from enum import StrEnum
import fnmatch
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Literal

import click
import typer


if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence


class Scope2(StrEnum):
    "Enumerate the supported Scope2 values."

    local = "local"
    remote = "remote"
    both = "both"


_STATE: dict[str, object] = {}


class _Args(SimpleNamespace):
    """argparse.Namespace look-alike: unknown attributes read as None."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


class _ParserShim:
    """Stand-in for the removed argparse parser; hands back what Typer parsed."""

    def parse_args(self, args=None, namespace=None):
        "Parse args."
        return _Args(**_STATE)

    def error(self, message):
        raise typer.BadParameter(message)

    def print_help(self, file=None):
        "Print help."
        typer.echo(click.get_current_context().get_help(), file=file)

    def print_usage(self, file=None):
        "Print usage."
        typer.echo(click.get_current_context().get_usage(), file=file)


Scope = Literal["local", "remote"]
Kind = Literal["merged", "squash"]

DEFAULT_PROTECTED: tuple[str, ...] = ("main", "master", "develop", "dev", "trunk", "release/*")
SEP = "\x1f"
FIELDS = "%(refname)%1f%(objectname)%1f%(committerdate:unix)%1f%(authorname)%1f%(upstream:track)%1f%(subject)"


class GitError(Exception):
    "Signal an error raised by this module."

    pass


@dataclass(slots=True)
class Branch:
    "Store scope, name, display, refname, sha and related state."

    scope: Scope
    name: str
    display: str
    refname: str
    sha: str
    committed: int
    author: str
    subject: str
    gone: bool
    kind: Kind | None = None


@dataclass(frozen=True, slots=True)
class Policy:
    "Store protected, include, exclude, pattern, older_than and related state."

    protected: tuple[str, ...]
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    pattern: re.Pattern[str] | None
    older_than: int | None
    author: str | None


@dataclass(frozen=True, slots=True)
class Options:
    "Store repo, remote, scopes, base, policy and related state."

    repo: Path
    remote: str
    scopes: tuple[Scope, ...]
    base: str | None
    policy: Policy
    squash: bool
    fetch: bool
    delete: bool
    yes: bool
    dry_run: bool
    show_skipped: bool
    as_json: bool
    undo_file: Path | None
    color: bool


@dataclass(slots=True)
class Scan:
    "Store candidates, skipped, unmerged, filtered."

    candidates: list[Branch]
    skipped: list[tuple[Branch, str]]
    unmerged: int = 0
    filtered: int = 0


def paint(text: str, code: str, enabled: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if enabled and code else text


@dataclass(frozen=True, slots=True)
class Console:
    "Store color."

    color: bool

    def say(self, text: str = "") -> None:
        print(text)

    def warn(self, text: str) -> None:
        print(paint(f"warning: {text}", "33", self.color), file=sys.stderr)

    def fail(self, text: str) -> None:
        print(paint(f"error: {text}", "31", self.color), file=sys.stderr)


class Git:
    def __init__(self, cwd: Path) -> None:
        "Initialize the instance state from the supplied arguments."
        self.cwd = cwd
        self.env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def exec(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=self.cwd,
                env=self.env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            msg = "git executable not found"
            raise GitError(msg) from exc

    def run(self, *args: str) -> str:
        proc = self.exec(*args)
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
        return proc.stdout

    def ok(self, *args: str) -> bool:
        return self.exec(*args).returncode == 0

    def text(self, *args: str) -> str:
        proc = self.exec(*args)
        return proc.stdout.strip() if proc.returncode == 0 else ""


def ref_exists(git: Git, ref: str) -> bool:
    return git.ok("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")


def detect_base(git: Git, remote: str, explicit: str | None) -> tuple[str, str]:
    "Detect base."
    if explicit:
        if not ref_exists(git, explicit):
            msg = f"base ref not found: {explicit}"
            raise GitError(msg)
        return explicit, explicit.removeprefix(f"{remote}/")
    head = git.text("symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD")
    if head and ref_exists(git, head):
        return head, head.removeprefix(f"{remote}/")
    for name in ("main", "master", "trunk"):
        for ref in (f"{remote}/{name}", name):
            if ref_exists(git, ref):
                return ref, name
    current = git.text("symbolic-ref", "--quiet", "--short", "HEAD")
    if current:
        return current, current
    msg = "cannot determine the base branch; pass --base"
    raise GitError(msg)


def worktree_branches(git: Git) -> set[str]:
    found: set[str] = set()
    for line in git.text("worktree", "list", "--porcelain").splitlines():
        if line.startswith("branch "):
            found.add(line.removeprefix("branch ").removeprefix("refs/heads/"))
    return found


def list_branches(git: Git, scope: Scope, remote: str) -> list[Branch]:
    prefix = "refs/heads" if scope == "local" else f"refs/remotes/{remote}"
    branches: list[Branch] = []
    for line in git.run("for-each-ref", f"--format={FIELDS}", prefix).splitlines():
        parts = line.split(SEP)
        if len(parts) != 6:
            continue
        refname, sha, stamp, author, track, subject = parts
        name = refname.removeprefix(f"{prefix}/")
        if scope == "remote" and name == "HEAD":
            continue
        display = name if scope == "local" else f"{remote}/{name}"
        branches.append(Branch(scope, name, display, refname, sha, int(stamp or 0), author, subject, track == "[gone]"))
    return branches


def merged_refs(git: Git, base: str, scope: Scope, remote: str) -> set[str]:
    prefix = "refs/heads" if scope == "local" else f"refs/remotes/{remote}"
    return set(git.run("for-each-ref", f"--merged={base}", "--format=%(refname)", prefix).split())


def matches(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def protect_reason(branch: Branch, locked: dict[str, str], policy: Policy) -> str | None:
    if branch.name in locked:
        return locked[branch.name]
    if matches(branch.name, policy.protected):
        return "protected pattern"
    return None


def passes(branch: Branch, policy: Policy, now: float) -> bool:
    if policy.include and not matches(branch.name, policy.include):
        return False
    if policy.exclude and matches(branch.name, policy.exclude):
        return False
    if policy.pattern is not None and not policy.pattern.search(branch.name):
        return False
    if policy.older_than is not None and now - branch.committed < policy.older_than * 86400:
        return False
    return not (policy.author and policy.author.lower() not in branch.author.lower())


def squash_merged(git: Git, base: str, base_tree: str, branch: Branch) -> bool:
    proc = git.exec("merge-tree", "--write-tree", base, branch.refname)
    if proc.returncode != 0 or not proc.stdout.strip():
        return False
    return proc.stdout.split()[0] == base_tree


def scan(git: Git, opts: Options, con: Console, base: str, locked: dict[Scope, dict[str, str]]) -> Scan:
    result = Scan([], [])
    base_tree = git.text("rev-parse", f"{base}^{{tree}}")
    use_squash = opts.squash and bool(base_tree) and git.ok("merge-tree", "--write-tree", base, base)
    if opts.squash and not use_squash:
        con.warn("squash detection needs git 2.38+; continuing without it")
    now = time.time()
    for scope in opts.scopes:
        merged = merged_refs(git, base, scope, opts.remote)
        for branch in list_branches(git, scope, opts.remote):
            reason = protect_reason(branch, locked[scope], opts.policy)
            if reason:
                result.skipped.append((branch, reason))
                continue
            if not passes(branch, opts.policy, now):
                result.filtered += 1
                continue
            if branch.refname in merged:
                branch.kind = "merged"
            elif use_squash and squash_merged(git, base, base_tree, branch):
                branch.kind = "squash"
            else:
                result.unmerged += 1
                continue
            result.candidates.append(branch)
    result.candidates.sort(key=lambda item: (item.committed, item.display))
    return result


def age(stamp: int, now: float) -> str:
    days = max(0, int((now - stamp) // 86400))
    if days < 1:
        return "today"
    if days < 60:
        return f"{days}d"
    if days < 730:
        return f"{days // 30}mo"
    return f"{days // 365}y"


def status(branch: Branch) -> str:
    label = "squash-merged" if branch.kind == "squash" else "merged"
    return label + (", upstream gone" if branch.gone else "")


def clip(text: str, size: int) -> str:
    return text if len(text) <= size else text[: max(1, size - 1)] + "~"


def render(branches: Sequence[Branch], now: float, color: bool, width: int) -> list[str]:
    headers = ("BRANCH", "AGE", "AUTHOR", "STATUS", "SUBJECT")
    caps = (50, 8, 22, 28)
    cells = [(b.display, age(b.committed, now), b.author, status(b), b.subject) for b in branches]
    widths = [min(cap, max(len(headers[i]), *(len(row[i]) for row in cells))) for i, cap in enumerate(caps)]
    room = max(12, width - sum(widths) - 2 * len(widths))

    def line(row: Sequence[str], codes: Sequence[str]) -> str:
        parts = [paint(clip(row[i], widths[i]).ljust(widths[i]), codes[i], color) for i in range(4)]
        parts.append(paint(clip(row[4], room), codes[4], color))
        return "  ".join(parts).rstrip()

    lines = [line(headers, ("1",) * 5)]
    for branch, row in zip(branches, cells):
        lines.append(line(row, ("", "2", "", "32" if branch.kind == "merged" else "33", "2")))
    return lines


def command(branch: Branch, remote: str) -> str:
    if branch.scope == "local":
        return f"git branch -D {shlex.quote(branch.name)}"
    return f"git push {shlex.quote(remote)} {shlex.quote(':refs/heads/' + branch.name)}"


def restore_command(branch: Branch, remote: str) -> str:
    "Restore command."
    if branch.scope == "local":
        return f"git branch {shlex.quote(branch.name)} {branch.sha}"
    target = shlex.quote("refs/heads/" + branch.name)
    return f"git push {shlex.quote(remote)} {branch.sha}:{target}"


def remove(git: Git, remote: str, branch: Branch) -> None:
    if branch.scope == "local":
        current = git.text("rev-parse", "--verify", "--quiet", branch.refname)
        if current != branch.sha:
            msg = "branch moved since the scan; left untouched"
            raise GitError(msg)
        git.run("branch", "-D", branch.name)
    else:
        git.run("push", remote, f":refs/heads/{branch.name}")


def pick(candidates: Sequence[Branch]) -> list[Branch]:
    chosen: list[Branch] = []
    for index, branch in enumerate(candidates):
        while True:
            answer = input(f"  delete {branch.display} ({branch.sha[:8]})? [y/n/a/q] ").strip().lower()
            if answer in {"y", "yes"}:
                chosen.append(branch)
                break
            if answer in {"n", "no", ""}:
                break
            if answer in {"a", "all"}:
                chosen.extend(candidates[index:])
                return chosen
            if answer in {"q", "quit"}:
                return chosen
    return chosen


def choose(candidates: Sequence[Branch], assume_yes: bool) -> list[Branch]:
    if assume_yes:
        return list(candidates)
    if not sys.stdin.isatty():
        msg = "refusing to prompt without a terminal; pass --yes"
        raise GitError(msg)
    answer = input(f"Delete {len(candidates)} branch(es)? [y/N/i=pick each] ").strip().lower()
    if answer in {"y", "yes"}:
        return list(candidates)
    if answer in {"i", "interactive"}:
        return pick(candidates)
    return []


def execute(git: Git, opts: Options, con: Console, chosen: Sequence[Branch]) -> int:
    done: list[Branch] = []
    failed = 0
    for branch in chosen:
        if opts.dry_run:
            con.say(f"would run: {command(branch, opts.remote)}")
            continue
        try:
            remove(git, opts.remote, branch)
        except GitError as exc:
            failed += 1
            con.fail(f"{branch.display}: {exc}")
            continue
        done.append(branch)
        con.say(f"deleted  {branch.display} ({branch.sha[:8]})")
    if opts.dry_run:
        return 0
    con.say(f"\n{len(done)} deleted, {failed} failed.")
    if done:
        restore = [restore_command(branch, opts.remote) for branch in done]
        con.say("restore with:")
        for item in restore:
            con.say(f"  {item}")
        if opts.undo_file is not None:
            script = f"#!/bin/sh\ncd {shlex.quote(str(opts.repo.resolve()))}\n" + "\n".join(restore) + "\n"
            try:
                opts.undo_file.write_text(script, encoding="utf-8")
                con.say(f"undo script written to {opts.undo_file}")
            except OSError as exc:
                con.warn(f"could not write undo file: {exc}")
    return 1 if failed else 0


def run(opts: Options) -> int:
    con = Console(opts.color)
    if not opts.repo.is_dir():
        msg = f"not a directory: {opts.repo}"
        raise GitError(msg)
    git = Git(opts.repo)
    if not git.ok("rev-parse", "--git-dir"):
        msg = f"not a git repository: {opts.repo}"
        raise GitError(msg)
    if ("remote" in opts.scopes or opts.fetch) and opts.remote not in git.text("remote").split():
        msg = f"remote not found: {opts.remote}"
        raise GitError(msg)
    if opts.fetch:
        git.run("fetch", "--prune", opts.remote)
    base, short = detect_base(git, opts.remote, opts.base)
    local_locked = {short: "base branch"}
    for name in worktree_branches(git):
        local_locked.setdefault(name, "checked out in a worktree")
    current = git.text("symbolic-ref", "--quiet", "--short", "HEAD")
    if current:
        local_locked[current] = "current branch"
    locked: dict[Scope, dict[str, str]] = {"local": local_locked, "remote": {short: "base branch"}}
    result = scan(git, opts, con, base, locked)
    candidates = result.candidates
    if opts.as_json:
        payload = {
            "base": base,
            "candidates": [asdict(item) for item in candidates],
            "skipped": [{**asdict(item), "reason": reason} for item, reason in result.skipped],
            "unmerged": result.unmerged,
            "filtered": result.filtered,
        }
        print(json.dumps(payload, indent=2))
        return 0
    now = time.time()
    con.say(f"base: {base}   scope: {' + '.join(opts.scopes)}\n")
    if candidates:
        width = shutil.get_terminal_size((100, 24)).columns
        for line in render(candidates, now, opts.color, width):
            con.say(line)
        con.say()
    squash_count = sum(1 for item in candidates if item.kind == "squash")
    con.say(
        f"{len(candidates)} deletable ({len(candidates) - squash_count} merged, {squash_count} squash-merged); "
        f"kept {len(result.skipped)} protected, {result.unmerged} unmerged, {result.filtered} filtered out"
    )
    if opts.show_skipped:
        for item, reason in result.skipped:
            con.say(f"  keep  {item.display}  ({reason})")
    if squash_count:
        con.say("note: squash-merged means the changes already exist in base (heuristic); double-check them.")
    if not opts.delete:
        if candidates:
            con.say("listing only; re-run with --delete to remove these branches.")
        return 0
    if not candidates:
        return 0
    chosen = choose(candidates, opts.yes or opts.dry_run)
    if not chosen:
        con.say("nothing deleted.")
        return 0
    return execute(git, opts, con, chosen)


def build_parser() -> argparse.ArgumentParser:
    "Build parser."
    parser = _ParserShim()
    return parser


def build_options(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Options:
    "Build options."
    if args.as_json and args.delete:
        parser.error("--json cannot be combined with --delete")
    if args.older_than is not None and args.older_than < 0:
        parser.error("--older-than must be zero or greater")
    try:
        pattern = re.compile(args.regex) if args.regex else None
    except re.error as exc:
        parser.error(f"invalid --regex: {exc}")
    scopes: dict[str, tuple[Scope, ...]] = {
        "local": ("local",),
        "remote": ("remote",),
        "both": ("local", "remote"),
    }
    protected = tuple(args.protect) + (() if args.no_default_protect else DEFAULT_PROTECTED)
    policy = Policy(
        protected=protected,
        include=tuple(args.patterns),
        exclude=tuple(args.exclude),
        pattern=pattern,
        older_than=args.older_than,
        author=args.author,
    )
    color = not args.no_color and sys.stdout.isatty() and "NO_COLOR" not in os.environ
    return Options(
        repo=args.repo,
        remote=args.remote,
        scopes=scopes[args.scope],
        base=args.base,
        policy=policy,
        squash=args.squash,
        fetch=args.fetch,
        delete=args.delete,
        yes=args.yes,
        dry_run=args.dry_run,
        show_skipped=args.show_skipped,
        as_json=args.as_json,
        undo_file=args.undo_file,
        color=color,
    )


def main(argv: Sequence[str] | None = None) -> int:
    "Run the command-line interface."
    parser = build_parser()
    opts = build_options(parser, parser.parse_args(argv))
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")
    try:
        return run(opts)
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\naborted", file=sys.stderr)
        return 130


def _run() -> None:
    raise SystemExit(main())


app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
)


@app.command(help="List and bulk-delete branches that are already merged into the base branch.")
def cli(
    patterns: Annotated[
        list[str] | None, typer.Argument(help="only consider branches matching these globs", metavar="PATTERN")
    ] = None,
    repo: Annotated[Path, typer.Option("-C", "--repo", help="repository path (default: current directory)")] = Path(),
    base: Annotated[
        str | None,
        typer.Option("-b", "--base", help="base ref to compare against (default: remote HEAD, main or master)"),
    ] = None,
    scope: Annotated[Scope2, typer.Option("-s", "--scope", help="which branches to consider")] = Scope2.local,
    remote: Annotated[str, typer.Option("--remote", help="remote name (default: origin)")] = "origin",
    exclude: Annotated[
        list[str], typer.Option("-x", "--exclude", help="skip branches matching this glob (repeatable)", metavar="GLOB")
    ] = [],
    regex: Annotated[
        str | None, typer.Option("-e", "--regex", help="only consider branch names matching this regular expression")
    ] = None,
    older_than: Annotated[
        int | None,
        typer.Option("--older-than", help="only branches whose last commit is older than DAYS", metavar="DAYS"),
    ] = None,
    author: Annotated[
        str | None, typer.Option("--author", help="only branches whose last commit author contains this text")
    ] = None,
    protect: Annotated[
        list[str], typer.Option("-p", "--protect", help="extra protected branch globs (repeatable)", metavar="GLOB")
    ] = [],
    no_default_protect: Annotated[
        bool, typer.Option("--no-default-protect", help=f"do not protect {', '.join(DEFAULT_PROTECTED)}")
    ] = False,
    squash: Annotated[
        bool, typer.Option("--squash", help="also detect squash/rebase-merged branches (git 2.38+)")
    ] = False,
    fetch: Annotated[bool, typer.Option("-F", "--fetch", help="run 'git fetch --prune' on the remote first")] = False,
    delete: Annotated[
        bool, typer.Option("-d", "--delete", help="delete the listed branches after confirmation")
    ] = False,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="skip the confirmation prompt")] = False,
    dry_run: Annotated[
        bool, typer.Option("-n", "--dry-run", help="with --delete, print the commands without running them")
    ] = False,
    show_skipped: Annotated[
        bool, typer.Option("--show-skipped", help="list protected and checked-out branches that were kept")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="print machine-readable output (no deletion)")] = False,
    undo_file: Annotated[
        Path | None, typer.Option("--undo-file", help="write a shell script that restores the deleted branches")
    ] = None,
    no_color: Annotated[bool, typer.Option("--no-color", help="disable colored output")] = False,
) -> None:
    patterns = patterns or []
    _STATE.update(
        patterns=patterns,
        repo=repo,
        base=base,
        scope=scope,
        remote=remote,
        exclude=exclude,
        regex=regex,
        older_than=older_than,
        author=author,
        protect=protect,
        no_default_protect=no_default_protect,
        squash=squash,
        fetch=fetch,
        delete=delete,
        yes=yes,
        dry_run=dry_run,
        show_skipped=show_skipped,
        as_json=as_json,
        undo_file=undo_file,
        no_color=no_color,
    )
    _run()


if __name__ == "__main__":
    app()
