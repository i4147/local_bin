#!/data/data/com.termux/files/usr/bin/env python
"""Apply patch files to the corresponding source files and write the results
to an output directory.

The examples in this repository are ordinary git-style unified diffs: each
``*.patch`` contains many ``diff --git`` sections, and a section can add a new
file, delete a file, or change an existing file.  This script parses those
patches itself (no ``git`` repository, ``git apply``, or ``patch`` binary is
needed) and applies them to the plain files that live next to the patches.

Default assumptions, as described in ``prompt.md``:

* patch files and the ``.py`` source files are in the current working directory;
* results go to ``./output`` (mirroring the paths used inside the patches);
* patch sections whose source file does not exist are skipped, not created.

Typical use::

    python apply_patches.py
    python apply_patches.py *.patch --verbose
    python apply_patches.py --new-files create --layout by-patch

Exit status is 0 for a completed run (missing source files and hunks that
cannot be matched are reported but are not fatal), 1 with ``--strict`` when a
section could not be applied, and 2 for usage/IO errors.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from dh import atomic_write
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


@dataclass
class Line:
    """One physical line of a file, with its own line ending preserved."""

    text: str
    eol: str = "\n"


@dataclass
class HunkLine:
    """One line of a hunk body: ' ' context, '-' removal, '+' addition."""

    tag: str
    text: str
    no_newline: bool = False


@dataclass
class Hunk:
    """Store old_start, old_count, new_start, new_count, lines and related state."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[HunkLine] = field(default_factory=list)
    heading: str = ""

    @property
    def anchor(self) -> int:
        """0-based index at which this hunk is expected in the old file.

        A hunk with no old lines is a pure insertion and is placed *after* the
        line named by ``old_start`` (git emits ``@@ -41,0 +42,3 @@``).
        """
        return self.old_start - 1 if self.old_count else self.old_start

    @property
    def old_lines(self) -> list[HunkLine]:
        """Return the old lines."""
        return [line for line in self.lines if line.tag in " -"]


@dataclass
class FileDiff:
    """One ``diff --git`` (or plain unified diff) section."""

    old_path: str | None = None
    new_path: str | None = None
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False
    header: list[str] = field(default_factory=list)
    paths_set: bool = False

    @property
    def is_new(self) -> bool:
        """Return whether new."""
        return self.old_path is None and self.new_path is not None

    @property
    def is_deleted(self) -> bool:
        """Return whether deleted."""
        return self.new_path is None and self.old_path is not None

    @property
    def target(self) -> str | None:
        """Return the target."""
        return self.new_path if self.new_path is not None else self.old_path


class PatchError(Exception):
    """A hunk did not match the source file."""


ENCODING: str = "utf-8"
ERRORS: str = "surrogateescape"


def read_text(path: Path) -> str:
    """Read text."""
    return path.read_bytes().decode(ENCODING, ERRORS)


def write_text(path: Path, text: str) -> None:
    """Write text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, text.encode(ENCODING, ERRORS))


def split_lines(text: str) -> list[Line]:
    """Split file text into lines, keeping each line's own EOL marker."""
    if not text:
        return []
    parts = text.split("\n")
    eols = ["\n"] * (len(parts) - 1) + [""]
    if parts[-1] == "":
        parts.pop()
        eols.pop()
    result: list[Line] = []
    for content, eol in zip(parts, eols):
        if eol == "\n" and content.endswith("\r"):
            content, eol = content[:-1], "\r\n"
        result.append(Line(content, eol))
    return result


def render(lines: Iterable[Line]) -> str:
    return "".join(line.text + line.eol for line in lines)


def dominant_eol(lines: Sequence[Line], fallback: str = "\n") -> str:
    counts: dict[str, int] = {}
    for line in lines:
        if line.eol:
            counts[line.eol] = counts.get(line.eol, 0) + 1
    if not counts:
        return fallback
    return max(counts, key=lambda eol: counts[eol])


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
DIFF_GIT_RE = re.compile(r'^diff --git (?P<old>"[^"]*"|\S+) (?P<new>"[^"]*"|\S+)\s*$')
HEADER_MARKERS: tuple[str, ...] = (
    "index ",
    "old mode ",
    "new mode ",
    "new file mode ",
    "deleted file mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "Binary files ",
    "GIT binary patch",
    "literal ",
    "delta ",
)
_ESCAPES: dict[str, str] = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "a": "\a",
    "\\": "\\",
    '"': '"',
}


def unquote_path(value: str) -> str:
    """Unquote a git C-style quoted path (``"a/my\\ file.py"``)."""
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        return value
    body = value[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in _ESCAPES:
                out.extend(_ESCAPES[nxt].encode(ENCODING))
                i += 2
            elif nxt in "01234567":
                j = i + 1
                digits = ""
                while j < len(body) and len(digits) < 3 and body[j] in "01234567":
                    digits += body[j]
                    j += 1
                out.append(int(digits, 8) & 0xFF)
                i = j
            else:
                out.extend(nxt.encode(ENCODING, ERRORS))
                i += 2
        else:
            out.extend(char.encode(ENCODING, ERRORS))
            i += 1
    return out.decode(ENCODING, ERRORS)


def clean_path(path: str) -> str | None:
    """Turn an (already unquoted) ``---``/``+++`` operand into a path."""
    if path in ("/dev/null", "dev/null", ""):
        return None
    return path


def normalise_paths(old: str, new: str) -> tuple[str, str]:
    """Strip the conventional ``a/`` and ``b/`` prefixes of git diffs."""
    old = unquote_path(old.split("\t", 1)[0].strip())
    new = unquote_path(new.split("\t", 1)[0].strip())
    if old.startswith("a/") and new.startswith("b/"):
        return old[2:], new[2:]
    if old.startswith("a/") and new == "/dev/null":
        return old[2:], new
    if old == "/dev/null" and new.startswith("b/"):
        return old, new[2:]
    return old, new


def safe_relative(path: str) -> str | None:
    """Reject absolute paths and paths that would escape the output dir."""
    if not path or path.startswith(("/", "\\")):
        return None
    normalised = path.replace("\\", "/")
    parts = PurePosixPath(normalised).parts
    if ".." in parts or re.match(r"^[A-Za-z]:", parts[0] if parts else ""):
        return None
    return normalised


def split_patch_lines(text: str) -> tuple[list[str], str]:
    """Split patch text into logical lines; return them plus the patch's EOL."""
    raw = text.split("\n")
    if raw and raw[-1] == "":
        raw.pop()
    crlf = bool(raw) and sum(1 for line in raw if line.endswith("\r")) > len(raw) // 2
    if crlf:
        raw = [line.removesuffix("\r") for line in raw]
        eol = "\r\n"
    else:
        eol = "\n"
    return raw, eol


def read_hunk(lines: list[str], index: int) -> tuple[Hunk, int]:
    """Parse a hunk, consuming exactly as many body lines as its header says."""
    match = HUNK_RE.match(lines[index])
    if match is None:
        raise PatchError(f"unsupported hunk header: {lines[index]!r}")
    hunk = Hunk(
        old_start=int(match.group(1)),
        old_count=int(match.group(2) or 1),
        new_start=int(match.group(3)),
        new_count=int(match.group(4) or 1),
        heading=match.group(5).strip(),
    )
    i = index + 1
    old_seen = new_seen = 0
    while i < len(lines) and (old_seen < hunk.old_count or new_seen < hunk.new_count):
        raw = lines[i]
        if raw.startswith("\\"):
            if hunk.lines:
                hunk.lines[-1].no_newline = True
            i += 1
            continue
        if raw.startswith("diff --git "):
            break
        if raw == "":
            tag, content = " ", ""
        else:
            tag, content = raw[0], raw[1:]
            if tag not in " +-":
                tag, content = " ", raw
        if tag in " -":
            old_seen += 1
        if tag in " +":
            new_seen += 1
        if old_seen > hunk.old_count or new_seen > hunk.new_count:
            break
        hunk.lines.append(HunkLine(tag, content))
        i += 1
    while i < len(lines) and lines[i].startswith("\\"):
        if hunk.lines:
            hunk.lines[-1].no_newline = True
        i += 1
    return hunk, i


def parse_patch(text: str) -> tuple[list[FileDiff], str]:
    """Parse every file section of one patch file.

    Supports both ``diff --git`` style patches and plain multi-file unified
    diffs, mail headers, quoted paths, binary markers and "/dev/null" sections.
    """
    lines, patch_eol = split_patch_lines(text)
    diffs: list[FileDiff] = []
    current: FileDiff | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            current = FileDiff(header=[line])
            diffs.append(current)
            header_match = DIFF_GIT_RE.match(line)
            if header_match:
                old_raw, new_raw = normalise_paths(header_match.group("old"), header_match.group("new"))
                current.old_path = clean_path(old_raw)
                current.new_path = clean_path(new_raw)
            i += 1
            continue
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old_raw, new_raw = normalise_paths(line[4:].strip(), lines[i + 1][4:].strip())
            if current is None or current.paths_set or current.hunks:
                current = FileDiff()
                diffs.append(current)
            current.old_path = clean_path(old_raw)
            current.new_path = clean_path(new_raw)
            current.paths_set = True
            i += 2
            continue
        if line.startswith(("@@ ", "@@@ ")):
            if current is None:
                current = FileDiff()
                diffs.append(current)
            hunk, i = read_hunk(lines, i)
            current.hunks.append(hunk)
            continue
        if line.startswith(HEADER_MARKERS):
            if current is None:
                current = FileDiff()
                diffs.append(current)
            current.header.append(line)
            if line.startswith(("Binary files ", "GIT binary patch")):
                current.binary = True
            i += 1
            continue
        i += 1
    return [d for d in diffs if d.target is not None], patch_eol


def comparison_text(text: str, ignore_whitespace: bool) -> str:
    text = text.removesuffix("\r")
    return text.strip() if ignore_whitespace else text


def find_match(
    source: Sequence[Line],
    hunk: Hunk,
    hint: int,
    min_pos: int,
    ignore_whitespace: bool,
    max_offset: int,
) -> int | None:
    """Find where a hunk applies, allowing line-number offsets like git does."""
    old_lines = hunk.old_lines
    if not old_lines:
        return min(max(hint, min_pos), len(source))
    limit = len(source) - len(old_lines)
    if limit < min_pos:
        return None

    def matches(pos: int) -> bool:
        if pos < min_pos or pos > limit:
            return False
        for offset, patch_line in enumerate(old_lines):
            expected = comparison_text(patch_line.text, ignore_whitespace)
            actual = comparison_text(source[pos + offset].text, ignore_whitespace)
            if expected != actual:
                return False
        return True

    hint = min(max(hint, min_pos), limit)
    if matches(hint):
        return hint
    bound = max_offset if max_offset > 0 else len(source)
    for distance in range(1, bound + 1):
        low, high = hint - distance, hint + distance
        if low < min_pos and high > limit:
            break
        if low >= min_pos and matches(low):
            return low
        if high <= limit and matches(high):
            return high
    return None


def apply_hunks(
    source: Sequence[Line],
    hunks: Sequence[Hunk],
    *,
    default_eol: str = "\n",
    ignore_whitespace: bool = False,
    max_offset: int = 0,
) -> list[Line]:
    """Apply all hunks of one file diff to ``source`` and return the new lines."""
    result: list[Line] = []
    pos = 0
    offset = 0
    for number, hunk in enumerate(hunks, 1):
        hint = hunk.anchor + offset
        match = find_match(source, hunk, hint, pos, ignore_whitespace, max_offset)
        if match is None:
            raise PatchError(
                f"hunk #{number} (@@ -{hunk.old_start},{hunk.old_count} "
                f"+{hunk.new_start},{hunk.new_count} @@) does not match the source file"
            )
        result.extend(source[pos:match])
        pos = match
        for patch_line in hunk.lines:
            if patch_line.tag == " ":
                if pos >= len(source):
                    raise PatchError(f"hunk #{number} runs past the end of the source file")
                result.append(source[pos])
                pos += 1
            elif patch_line.tag == "-":
                if pos >= len(source):
                    raise PatchError(f"hunk #{number} runs past the end of the source file")
                pos += 1
            elif patch_line.tag == "+":
                text, eol = patch_line.text, default_eol
                if text.endswith("\r"):
                    text, eol = text[:-1], "\r\n"
                if patch_line.no_newline:
                    eol = ""
                result.append(Line(text, eol))
        offset = match - hunk.anchor
    result.extend(source[pos:])
    return result


@dataclass
class Stats:
    """Store patches, applied, missing, skipped, failed and related state."""

    patches: int = 0
    applied: int = 0
    missing: int = 0
    skipped: int = 0
    failed: int = 0
    written: int = 0
    per_patch_copies: int = 0


def discover_patches(patch_dir: Path, patterns: Sequence[str]) -> list[Path]:
    """Discover patches."""
    found: set[Path] = set()
    for pattern in patterns:
        for path in patch_dir.glob(pattern):
            if path.is_file():
                found.add(path)
    return sorted(found, key=lambda p: p.name)


def output_paths(output_dir: Path, patch: Path, target: str, layout: str, collision: bool) -> list[Path]:
    """Where to write one result, given the chosen output layout."""
    paths: list[Path] = []
    if layout != "by-patch":
        paths.append(output_dir / target)
    if layout == "by-patch" or (layout == "auto" and collision):
        paths.append(output_dir / patch.stem / target)
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def run(args: argparse.Namespace) -> int:
    patch_dir = Path(args.patch_dir).resolve()
    source_dir = Path(args.source_dir).resolve()
    output_dir = Path(args.output_dir)
    if not patch_dir.is_dir():
        print(f"error: patch directory does not exist: {patch_dir}", file=sys.stderr)
        return 2
    if not source_dir.is_dir():
        print(f"error: source directory does not exist: {source_dir}", file=sys.stderr)
        return 2
    explicit = [
        (path if path.is_absolute() else (path if path.is_file() else patch_dir / path)).resolve()
        for path in (Path(name) for name in getattr(args, "explicit_patches", []))
    ]
    patches = (
        sorted({p for p in explicit if p.is_file()}, key=lambda p: p.name)
        if explicit
        else discover_patches(patch_dir, args.patches)
    )
    if not patches:
        if explicit:
            print(
                f"error: patch file(s) not found: {', '.join(map(str, explicit))}",
                file=sys.stderr,
            )
        else:
            print(
                f"error: no patch files matching {', '.join(args.patches)} in {patch_dir}",
                file=sys.stderr,
            )
        return 2
    parsed: list[tuple[Path, list[FileDiff], str]] = []
    for patch in patches:
        try:
            diffs, patch_eol = parse_patch(read_text(patch))
        except (OSError, PatchError) as exc:
            print(f"error: cannot parse {patch.name}: {exc}", file=sys.stderr)
            return 2
        parsed.append((patch, diffs, patch_eol))
    counts: dict[str, set[str]] = {}
    for patch, diffs, _ in parsed:
        for diff in diffs:
            if diff.target and not diff.is_deleted:
                counts.setdefault(diff.target, set()).add(patch.name)
    collisions = {name for name, owners in counts.items() if len(owners) > 1}
    if args.clean and output_dir.exists() and not args.dry_run:
        if output_dir.is_dir():
            shutil.rmtree(output_dir)
        else:
            output_dir.unlink()
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    stats = Stats(patches=len(parsed))
    for patch, diffs, patch_eol in parsed:
        if args.verbose:
            print(f"[{patch.name}] {len(diffs)} file section(s)")
        for diff in diffs:
            target = diff.target
            if target is None or safe_relative(target) is None:
                stats.skipped += 1
                if args.verbose:
                    print(f"  skip  {target!r}: unsafe or missing path")
                continue
            if diff.is_deleted:
                stats.skipped += 1
                if not args.dry_run:
                    for out in output_paths(output_dir, patch, target, args.layout, target in collisions):
                        out.unlink(missing_ok=True)
                if args.verbose:
                    print(f"  drop  {target}: deleted by patch")
                continue
            if diff.binary:
                stats.skipped += 1
                if args.verbose:
                    print(f"  skip  {target}: binary patches are not supported")
                continue
            source_path = None
            mode = 0o644
            if diff.is_new:
                if args.new_files == "skip":
                    stats.missing += 1
                    if args.verbose:
                        print(f"  skip  {target}: source file does not exist (new file)")
                    continue
                source_lines: list[Line] = []
            else:
                source_path = source_dir / (diff.old_path or target)
                if not source_path.is_file():
                    stats.missing += 1
                    if args.verbose:
                        print(f"  skip  {target}: source file does not exist")
                    continue
                try:
                    source_lines = split_lines(read_text(source_path))
                    mode = stat.S_IMODE(source_path.stat().st_mode)
                except OSError as exc:
                    stats.failed += 1
                    print(f"  FAIL  {target}: cannot read source ({exc})", file=sys.stderr)
                    continue
            if not diff.hunks:
                result = list(source_lines)
            else:
                try:
                    result = apply_hunks(
                        source_lines,
                        diff.hunks,
                        default_eol=dominant_eol(source_lines, patch_eol),
                        ignore_whitespace=args.ignore_whitespace,
                        max_offset=args.max_offset,
                    )
                except PatchError as exc:
                    stats.failed += 1
                    print(f"  FAIL  [{patch.name}] {target}: {exc}", file=sys.stderr)
                    continue
            stats.applied += 1
            if args.verbose:
                print(f"  ok    {target} ({len(diff.hunks)} hunk(s))")
            if args.dry_run:
                continue
            collision = target in collisions
            per_patch = args.layout == "by-patch" or (args.layout == "auto" and collision)
            wrote_here = False
            for out in output_paths(output_dir, patch, target, args.layout, collision):
                try:
                    write_text(out, render(result))
                    os.chmod(out, mode)
                    stats.written += 1
                    wrote_here = True
                except OSError as exc:
                    stats.failed += 1
                    print(f"  FAIL  {out}: cannot write ({exc})", file=sys.stderr)
            if per_patch and wrote_here:
                stats.per_patch_copies += 1
    if not args.quiet:
        print(
            f"Patches: {stats.patches} | applied: {stats.applied} file(s) "
            f"({stats.written} written to {output_dir}) | "
            f"skipped missing: {stats.missing} | skipped other: {stats.skipped} | "
            f"failed: {stats.failed}"
        )
        if stats.per_patch_copies:
            print(
                f"Note: {stats.per_patch_copies} result(s) also saved per patch under "
                f"{output_dir}/<patch-name>/ (several patches touch the same path); "
                f"the flat copy holds the last successfully applied patch."
            )
    if stats.failed and args.strict:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build parser."""
    parser = argparse.ArgumentParser(
        description="Apply .patch files to the corresponding source files, "
        "writing the patched results into an output directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="By default: patches and sources are in the current directory, "
        "results go to ./output, and sections for missing files are skipped.",
    )
    parser.add_argument(
        "patch_files",
        nargs="*",
        metavar="PATCH",
        help="optional explicit patch file(s); by default all *.patch/*.diff in the patch directory are used",
    )
    parser.add_argument(
        "--patches",
        action="append",
        metavar="GLOB",
        help="glob(s) for patch files (default: *.patch and *.diff); repeatable",
    )
    parser.add_argument(
        "--patch-dir",
        default=".",
        metavar="DIR",
        help="directory holding patch files (default: .)",
    )
    parser.add_argument(
        "--source-dir",
        default=".",
        metavar="DIR",
        help="directory holding the source files (default: .)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        metavar="DIR",
        help="directory for the results (default: output)",
    )
    parser.add_argument(
        "--layout",
        choices=("auto", "flat", "by-patch"),
        default="auto",
        help="flat: output/<path>; by-patch: output/<patch>/<path>; "
        "auto: flat, plus per-patch copies for paths appearing "
        "in more than one patch (default)",
    )
    parser.add_argument(
        "--new-files",
        choices=("skip", "create"),
        default="skip",
        help="what to do with '--- /dev/null' sections: skip them (default) or create the file from the patch",
    )
    parser.add_argument(
        "--max-offset",
        type=int,
        default=0,
        metavar="N",
        help="max line offset when locating a hunk (0 = search whole file)",
    )
    parser.add_argument(
        "--ignore-whitespace",
        action="store_true",
        help="match hunk context ignoring surrounding whitespace",
    )
    parser.add_argument("--clean", action="store_true", help="empty the output directory before writing")
    parser.add_argument("--dry-run", action="store_true", help="check patches without writing anything")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit with status 1 if any section could not be applied",
    )
    parser.add_argument("--verbose", action="store_true", help="print every file section")
    parser.add_argument("--quiet", action="store_true", help="suppress the summary")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)
    args.explicit_patches = [name for name in args.patch_files if Path(name).suffix.lower() in {".patch", ".diff"}]
    if not args.patches:
        args.patches = ["*.patch", "*.diff"]
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
