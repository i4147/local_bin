#!/data/data/com.termux/files/usr/bin/env python

import shutil
import subprocess
import sys
from pathlib import Path

TERMUX_SHEBANGS = {
    "python": "#!/data/data/com.termux/files/usr/bin/env python",
    "bash": "#!/data/data/com.termux/files/usr/bin/bash",
    "sh": "#!/data/data/com.termux/files/usr/bin/sh",
    "zsh": "#!/data/data/com.termux/files/usr/bin/zsh",
    "rust": "#!/data/data/com.termux/files/home/.cargo/bin/rust-script",
}
EXTENSION_MAP = {
    ".py": "python",
    ".sh": "bash",
    ".zsh": "zsh",
    ".bash": "bash",
    ".rs": "rust",
}
SCRIPT_EXTENSIONS = {".py", ".sh", ".rs", ".zsh"}
SCRIPT_DIRS = {
    Path.home() / "bin",
    Path.home() / "bashbin",
    Path.home() / ".cargo" / "bin",
}
ARCHIVE_DIR = Path.home() / "isaac" / "may" / "scripts"


def get_clipboard_content() -> str:
    try:
        result = subprocess.run(["termux-clipboard-get"], capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Failed to read clipboard: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: termux-clipboard-get not found", file=sys.stderr)
        sys.exit(1)


def get_language_from_extension(filename: str) -> str:
    return EXTENSION_MAP.get(Path(filename).suffix.lower(), "bash")


def replace_shebang(content: str, lang: str) -> str:
    lines = content.splitlines()
    if lines and lines[0].startswith("#!"):
        lines.pop(0)
    lines.insert(0, TERMUX_SHEBANGS[lang])
    result = "\n".join(lines)
    return result if result.endswith("\n") else result + "\n"


def archive_existing_file(path: Path) -> None:
    if not path.exists():
        return
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / path.name
    counter = 1
    while archive_path.exists():
        archive_name = f"{path.stem}_{counter}{path.suffix}"
        archive_path = ARCHIVE_DIR / archive_name
        counter += 1
    try:
        shutil.move(str(path), str(archive_path))
        print(f"📦 Archived to: {archive_path}")
    except OSError as e:
        print(f"❌ Failed to archive: {e}", file=sys.stderr)
        sys.exit(1)


def create_symlink(script_path: Path) -> None:
    if script_path.suffix.lower() == ".rs":
        return
    symlink_path = script_path.parent / script_path.stem
    if symlink_path.exists() and symlink_path.is_symlink():
        symlink_path.unlink()
    if not symlink_path.exists():
        try:
            symlink_path.symlink_to(script_path)
            print(f"  → Symlink: {symlink_path.name}")
        except OSError as e:
            print(f"  ⚠️  Symlink failed: {e}", file=sys.stderr)


def main() -> None:
    archive = "-a" in sys.argv
    args = [arg for arg in sys.argv[1:] if arg != "-a"]
    if len(args) != 1:
        print(f"Usage: {sys.argv[0]} [-a] <filename>", file=sys.stderr)
        sys.exit(1)
    filename = args[0]
    if "." not in filename.strip():
        print("you didnt provide an extension,continue?")
        ans = input("y/n")
        if not ans == "y":
            sys.exit(0)
    output_path = Path(filename)
    is_script_dir = (
        Path.cwd() in SCRIPT_DIRS or Path.cwd().name == "bin"
    ) and output_path.suffix.lower() in SCRIPT_EXTENSIONS
    if archive and output_path.exists():
        archive_existing_file(output_path)
    content = get_clipboard_content()
    if not content.strip():
        content = (TERMUX_SHEBANGS[get_language_from_extension(filename)] + "\n\n") if is_script_dir else "\n"
    elif is_script_dir:
        lang = get_language_from_extension(filename)
        content = replace_shebang(content, lang)
    try:
        output_path.write_text(content)
    except OSError as e:
        print(f"Error writing file: {e}", file=sys.stderr)
        sys.exit(1)
    if is_script_dir:
        output_path.chmod(0o755)
        create_symlink(output_path)


if __name__ == "__main__":
    raise SystemExit(main())
