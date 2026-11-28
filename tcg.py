#!/data/data/com.termux/files/usr/bin/env python
"""Create a script from clipboard content, with Termux shebang injection."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import libcst as cst
from dh import atomic_write
from loguru import logger
from pygments.lexers import guess_lexer
from pygments.util import ClassNotFound

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


TERMUX_SHEBANGS: dict[str, str] = {
    "python": "#!/data/data/com.termux/files/usr/bin/env python",
    "bash": "#!/data/data/com.termux/files/usr/bin/bash",
    "sh": "#!/data/data/com.termux/files/usr/bin/sh",
    "zsh": "#!/data/data/com.termux/files/usr/bin/zsh",
    "rust": "#!/data/data/com.termux/files/home/.cargo/bin/rust-script",
}
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".sh": "bash",
    ".zsh": "zsh",
    ".bash": "bash",
    ".rs": "rust",
}
LANG_TO_EXT: dict[str, str] = {
    "python": ".py",
    "bash": ".sh",
    "sh": ".sh",
    "zsh": ".zsh",
    "rust": ".rs",
}
PYGMENTS_MAP: dict[str, str] = {
    "python": "python",
    "python 3": "python",
    "python 2": "python",
    "bash": "bash",
    "shell": "sh",
    "sh": "sh",
    "zsh": "zsh",
    "rust": "rust",
}
SCRIPT_EXTENSIONS: set[str] = {".py", ".sh", ".rs", ".zsh"}
SCRIPT_DIRS: set[Path] = {
    (Path.home() / "bin").resolve(),
    (Path.home() / "bashbin").resolve(),
    (Path.home() / ".local" / "bin").resolve(),
    (Path.home() / ".cargo" / "bin").resolve(),
}
ARCHIVE_DIR: Path = Path.home() / "isaac" / "may" / "scripts"

# Markers that must appear before a successful libcst parse is trusted as
# Python. Prevents a bare ``true`` / ``false`` shell one-liner (also valid
# Python name expressions) from being classified as Python.
PY_INDICATORS: re.Pattern[str] = re.compile(
    r"^(?:"
    r"\s*import\s+[\w.]+"
    r"|\s*from\s+[\w.]+\s+import\s+"
    r"|\s*def\s+\w+\s*\("
    r"|\s*async\s+def\s+\w+\s*\("
    r"|\s*class\s+\w+\s*[:(]"
    r"|\s*if\s+__name__\s*=="
    r"|\s*@\w+"
    r")",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class Options:
    filename: str
    archive: bool = False
    symlink: bool = False
    force: bool = False
    lang: str | None = None


def get_clipboard_content() -> str:
    """Return clipboard text or terminate with a clear error."""
    try:
        result = subprocess.run(
            ["termux-clipboard-get"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as exc:
        print(f"Failed to read clipboard: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except FileNotFoundError as exc:
        print("Error: termux-clipboard-get not found", file=sys.stderr)
        raise SystemExit(1) from exc


def get_language_from_extension(filename: str) -> str:
    """Map a filename's suffix to a language, defaulting to ``bash``."""
    return EXTENSION_MAP.get(Path(filename).suffix.lower(), "bash")


def _parses_as_python(content: str) -> bool:
    """Return True iff ``content`` looks like and parses as Python.

    The indicator gate keeps single-token shell scripts (``true``, ``ls``)
    from being misclassified as Python just because libcst accepts them.
    """
    if not content.strip():
        return False
    if PY_INDICATORS.search(content) is None:
        return False
    try:
        cst.parse_module(content)
    except (cst.ParserSyntaxError, RecursionError):
        return False
    return True


def detect_language(content: str, filename: str) -> str:
    """Detect language from shebang, Python parse, Pygments, regex, or suffix."""
    lines = content.splitlines()
    first_line = lines[0].strip() if lines else ""

    # 1. Explicit shebang
    if first_line.startswith("#!"):
        shebang_lower = first_line.lower()
        if "python" in shebang_lower:
            return "python"
        if "zsh" in shebang_lower:
            return "zsh"
        if "bash" in shebang_lower:
            return "bash"
        if "rust" in shebang_lower or "rustc" in shebang_lower or "cargo" in shebang_lower:
            return "rust"
        if re.search(r"(?:^|[\s/])sh$", shebang_lower):
            return "sh"

    stripped = content.lstrip()
    if stripped:
        # 2. Strongest non-shebang signal: content actually parses as Python.
        #    Tried before Pygments because libcst is exact for valid Python,
        #    whereas Pygments frequently confuses Python snippets.
        if _parses_as_python(content):
            return "python"

        # 3. Pygments lexer detection (filtered to supported Termux languages)
        try:
            lexer = guess_lexer(stripped)
            detected = PYGMENTS_MAP.get(lexer.name.lower())
            if detected:
                return detected
        except ClassNotFound:
            pass

        # 4. Regex fallback for snippets Pygments misclassifies
        if re.search(
            r"^\s*(?:fn\s+\w+|use\s+std::|pub\s+(?:fn|struct|enum|mod)\b|#\[(?:tokio::main|test)\])",
            content,
            re.MULTILINE,
        ):
            return "rust"
        if re.search(
            r"^\s*(?:def\s+\w+|async\s+def\s+\w+|class\s+\w+|from\s+\S+\s+import\b|import\s+\S+)",
            content,
            re.MULTILINE,
        ):
            return "python"
        if re.search(
            r"^\s*(?:autoload\b|zparseopts\b|compdef\b|emulate\s+-L\s+zsh\b|typeset\s+-[A-Za-z]*[A-Z])",
            content,
            re.MULTILINE,
        ):
            return "zsh"
        if re.search(
            r"^\s*(?:\[\[|\bdeclare\s+-[a-zA-Z]+|\breadarray\b|\bmapfile\b|\bshopt\b|function\s+\w+\s*\(\))",
            content,
            re.MULTILINE,
        ):
            return "bash"

    # 5. File extension fallback
    return get_language_from_extension(filename)


def confirm_extension_change(output_path: Path, detected_lang: str) -> tuple[Path, str]:
    """Prompt on language/extension mismatch; leave suffix-less targets alone.

    A suffix-less target means the caller explicitly wanted no extension —
    we adopt the detected language and keep the path unchanged.
    """
    expected_ext = LANG_TO_EXT.get(detected_lang)
    current_ext = output_path.suffix.lower()

    if not current_ext:
        return output_path, detected_lang

    if not expected_ext or current_ext == expected_ext:
        return output_path, detected_lang
    if current_ext in {".sh", ".bash"} and detected_lang in {"bash", "sh"}:
        return output_path, detected_lang

    new_path = output_path.with_suffix(expected_ext)
    try:
        answer = (
            input(
                f"Detected {detected_lang} code, but extension is '{current_ext}'. Rename to '{new_path.name}'? [Y/n]: "
            )
            .strip()
            .lower()
        )
    except (EOFError, KeyboardInterrupt):
        print()
        return output_path, get_language_from_extension(output_path.name)

    if answer in {"", "y", "yes"}:
        return new_path, detected_lang

    return output_path, get_language_from_extension(output_path.name)


def replace_shebang(content: str, lang: str) -> str:
    """Replace or prepend the shebang line."""
    lines = content.splitlines()
    if lines and lines[0].startswith("#!"):
        lines.pop(0)
    lines.insert(0, TERMUX_SHEBANGS[lang])
    result = "\n".join(lines)
    return result if result.endswith("\n") else result + "\n"


def archive_existing_file(path: Path) -> None:
    """Move an existing file into the archive directory, de-duplicating names."""
    if not path.exists():
        return
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / path.name
    counter = 1
    while archive_path.exists():
        archive_path = ARCHIVE_DIR / f"{path.stem}_{counter}{path.suffix}"
        counter += 1
    try:
        shutil.move(str(path), str(archive_path))
        print(f"📦 Archived to: {archive_path}")
    except OSError as exc:
        print(f"❌ Failed to archive: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def create_symlink(script_path: Path) -> None:
    """Create the extensionless symlink for a suffix-bearing script."""
    suffix = script_path.suffix.lower()
    if suffix in {"", ".rs"}:
        # Nothing to strip for suffix-less targets or rust-script launchers.
        return
    symlink_path = script_path.parent / script_path.stem
    if symlink_path == script_path:
        return
    if symlink_path.is_symlink():
        symlink_path.unlink()
    if not symlink_path.exists():
        try:
            symlink_path.symlink_to(script_path)
            print(f"  → Symlink: {symlink_path.name}")
        except OSError as exc:
            print(f"  ⚠️  Symlink failed: {exc}", file=sys.stderr)


def is_target_script(output_path: Path) -> bool:
    """True if ``output_path`` is a script candidate inside a bin directory.

    Extensionless targets inside a bin directory are also candidates — this
    fixes the reported case where pasting Python into ``~/bin/mytool`` used
    to skip shebang handling because the suffix was empty.
    """
    target_dir = output_path.resolve().parent
    in_script_dir = target_dir in SCRIPT_DIRS or target_dir.name == "bin"
    if not in_script_dir:
        return False
    suffix = output_path.suffix.lower()
    return suffix == "" or suffix in SCRIPT_EXTENSIONS


def parse_args(argv: list[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(
        description="Create a script from clipboard content.",
    )
    parser.add_argument(
        "-a",
        "--archive",
        action="store_true",
        help="Archive existing file before writing",
    )
    parser.add_argument(
        "-s",
        "--symlink",
        action="store_true",
        help="Create an extensionless symlink",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Treat target as a script even outside known bin directories",
    )
    parser.add_argument(
        "--lang",
        choices=sorted(TERMUX_SHEBANGS),
        default=None,
        help="Override language detection (skips auto-detection)",
    )
    parser.add_argument("filename", help="Target script filename or path")
    ns = parser.parse_args(argv)
    lang_val = ns.lang
    return Options(
        filename=str(ns.filename),
        archive=bool(ns.archive),
        symlink=bool(ns.symlink),
        force=bool(ns.force),
        lang=lang_val if isinstance(lang_val, str) else None,
    )


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    output_path = Path(opts.filename)
    script_mode = opts.force or is_target_script(output_path)

    content = get_clipboard_content()
    if not content.strip():
        lang = opts.lang or get_language_from_extension(opts.filename)
        content = (TERMUX_SHEBANGS[lang] + "\n\n") if script_mode else "\n"
    elif script_mode:
        lang = opts.lang or detect_language(content, opts.filename)
        output_path, lang = confirm_extension_change(output_path, lang)
        content = replace_shebang(content, lang)

    if opts.archive and output_path.exists():
        archive_existing_file(output_path)

    try:
        atomic_write(output_path, content)
        print(f"✅ Saved: {output_path}")
    except OSError as exc:
        print(f"Error writing file: {exc}", file=sys.stderr)
        return 1

    if script_mode:
        output_path.chmod(0o755)
        if opts.symlink:
            create_symlink(output_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
