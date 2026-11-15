#!/data/data/com.termux/files/usr/bin/env python
"""Verify all root .py files parse and run on Python 3.11.

Checks:
  1. ast.parse every *.py in the repo root with the running interpreter
     (must be Python 3.11 to be meaningful).
  2. Runs vermin (if installed) targeting 3.11- and reports violations.

Usage:
    python3 arena/verify_py311.py
"""

import ast
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent

# pydup.py's concurrent.futures.InterpreterPoolExecutor (3.14) is guarded by
# try/except ImportError and only used when explicitly requested, so vermin's
# flag for it is expected and acceptable.
EXPECTED_VERMINT_HITS = ("pydup.py",)


def check_parse() -> list[str]:
    """Parse every root Python file and return syntax failures."""
    files = sorted(ROOT.glob("*.py"))
    failures = []
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            failures.append(f"{path.name}:{exc.lineno}: {exc.msg}")
    print(f"[parse] Python {sys.version.split()[0]}: {len(files) - len(failures)}/{len(files)} files OK")
    for failure in failures:
        print(f"  FAIL {failure}")
    return failures


def check_vermin() -> list[str]:
    """Run vermin for the Python 3.11 target and return unexpected violations."""
    try:
        result = subprocess.run(
            ["vermin", "-t=3.11-", "--violations", "--all", *[str(p) for p in sorted(ROOT.glob("*.py"))]],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        print("[vermin] not installed; skipping")
        return []
    # Violation lines start with ! or ~; detail lines are indented.
    lines = result.stdout.splitlines()
    hits = []
    for i, line in enumerate(lines):
        if line[:1] in ("!", "~"):
            detail = lines[i + 1].strip() if i + 1 < len(lines) else ""
            hits.append(f"{line.strip()} {detail}".strip())
    print(f"[vermin] {len(hits)} violation(s)")
    for hit in hits:
        print(f"  {hit}")
    return [h for h in hits if not any(name in h for name in EXPECTED_VERMINT_HITS)]


def main() -> int:
    """Run syntax and minimum-version checks and return a process status."""
    if sys.version_info[:2] != (3, 11):
        print(
            f"warning: running on Python {sys.version.split()[0]}, expected 3.11 for a meaningful check",
            file=sys.stderr,
        )
    failures = check_parse()
    unexpected = check_vermin()
    if not failures and not unexpected:
        print("OK: all files are Python 3.11 compatible")
        return 0
    print("FAILED: see above", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
