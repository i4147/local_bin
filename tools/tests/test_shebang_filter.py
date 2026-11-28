"""Regression coverage for the shebang clean/smudge filter."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "shebang-filter.py"


def run_filter(mode: str, data: bytes, prefix: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PREFIX"] = str(prefix)
    return subprocess.run(
        [sys.executable, str(SCRIPT), mode], input=data, capture_output=True, env=env
    )


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"ordinary text\n", b"ordinary text\n"),
        (b"#!/usr/bin/python3\nbody\n", b"#!/usr/bin/env python3\nbody\n"),
        (b"#!/usr/bin/python3 -u\nbody\n", b"#!/usr/bin/env -S python3 -u\nbody\n"),
        (b"#!/usr/bin/env python3\nbody\n", b"#!/usr/bin/env python3\nbody\n"),
        (b"#!/usr/bin/python3", b"#!/usr/bin/python3"),
    ],
)
def test_clean(data: bytes, expected: bytes, tmp_path: Path) -> None:
    result = run_filter("clean", data, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


def test_smudge_installed_and_missing_interpreters(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").touch()
    data = b"#!/usr/bin/env -S python3 -u\nbody\n"
    result = run_filter("smudge", data, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"#!{bin_dir / 'python3'} -u\nbody\n".encode()
    missing = run_filter("smudge", b"#!/usr/bin/env missing\n", tmp_path)
    assert missing.returncode == 0, missing.stderr
    assert missing.stdout == b"#!/usr/bin/env missing\n"


def test_invalid_mode(tmp_path: Path) -> None:
    result = run_filter("invalid", b"", tmp_path)
    assert result.returncode == 2
    assert b"usage:" in result.stderr
