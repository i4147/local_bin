"""Check recursive discovery on the supported Python runtime."""
import importlib.util
from pathlib import Path
import sys

SCRIPT = Path(__file__).resolve().parents[2] / 'py_strip.py'


def test_walk_directory_and_exclusions(tmp_path):
    spec = importlib.util.spec_from_file_location('py_strip_walk_test', SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        (tmp_path / 'sample.py').write_text('x = 1\n')
        (tmp_path / 'ignored').mkdir()
        (tmp_path / 'ignored' / 'bad.py').write_text('x = 2\n')
        (tmp_path / 'nested').mkdir()
        (tmp_path / 'nested' / 'run').write_text('#!/usr/bin/env python3\n')
        assert set(module.iter_python_files([tmp_path], frozenset({'ignored'}))) == {
            tmp_path / 'sample.py', tmp_path / 'nested' / 'run',
        }
    finally:
        sys.modules.pop(spec.name, None)
