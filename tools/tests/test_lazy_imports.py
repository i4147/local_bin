"""Safety checks for the conservative standalone-script import mover."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'lazy_imports.py'
spec = importlib.util.spec_from_file_location('lazy_imports', SCRIPT)
assert spec and spec.loader
lazy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lazy)


def test_moves_single_function_use_after_docstring():
    source = 'import math as calc\n\ndef f(x):\n    """Keep me."""\n    return calc.sqrt(x)\n'
    result, count, moved, skipped = lazy.analyze(source)
    assert count == 1 and len(moved) == 1 and not skipped
    assert result == '\ndef f(x):\n    """Keep me."""\n    import math as calc\n    return calc.sqrt(x)\n'
    namespace = {}
    exec(result, namespace)
    assert namespace['f'](9) == 3
    assert namespace['f'].__doc__ == 'Keep me.'


def test_preserves_import_time_and_ambiguous_uses():
    sources = [
        'import math\ndef f(x=math.pi):\n    return math.sqrt(x)\n',
        'import math\ndef f(): return math.pi\ndef g(): return math.e\n',
        'import math\n__all__ = ["math"]\ndef f(): return math.pi\n',
        'import math\ndef f(x: math.pi):\n    return math.sqrt(x)\n',
        'import math\ndef f():\n    return globals()["math"]\n',
        'import custom_plugin\ndef f():\n    return custom_plugin.run()\n',
        'import math  # keep comment\ndef f():\n    return math.pi\n',
    ]
    for source in sources:
        result, _, moved, skipped = lazy.analyze(source)
        assert not moved and skipped
        assert result == source


def test_keeps_import_when_function_rebinds_name():
    source = 'import math\ndef f():\n    global math\n    return math.pi\n'
    result, _, moved, _ = lazy.analyze(source)
    assert not moved
    assert result == source
