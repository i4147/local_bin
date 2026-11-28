"""Regression tests for scope-aware name rewriting in the consolidators."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import libcst as cst
from libcst.metadata import MetadataWrapper
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('script', ['consolidate2', 'consolidaze'])
def test_rename_global_preserves_local_shadow(script):
    spec = spec_from_file_location(script, ROOT / f'{script}.py')
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[script] = module
    try:
        spec.loader.exec_module(module)
        source = 'value = 10\ndef read():\n    return value\ndef shadow(value):\n    return value\n'
        tree = cst.parse_module(source)
        info = module.ModuleInfo('sample', Path('sample.py'), 'sample.py', tree=tree, wrapper=MetadataWrapper(tree))
        module.rename_in_module(info, {'value': 'renamed'}, set())
        namespace = {}
        exec(info.tree.code, namespace)
        assert namespace['read']() == 10
        assert namespace['shadow'](42) == 42
        assert namespace['renamed'] == 10
    finally:
        sys.modules.pop(script, None)
