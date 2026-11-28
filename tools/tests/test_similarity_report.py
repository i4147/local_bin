"""Tests for source feature extraction and similarity calculation."""
import importlib.util
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import libcst as cst

SCRIPT = Path(__file__).resolve().parents[1] / 'similarity_report.py'
spec = importlib.util.spec_from_file_location('similarity_report', SCRIPT)
assert spec and spec.loader
similarity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(similarity)


def test_features_capture_lazy_imports_names_and_flags():
    visitor = similarity.Features()
    cst.parse_module('import os.path\nfrom .foo import thing\ndef my_func():\n    import json\n    return "--dry-run camelCase snake_case"\n').visit(visitor)
    assert visitor.funcs == {'my_func'}
    assert visitor.mods == {'os.path', '.foo', 'json'}
    assert visitor.flags == {'--dry-run'}
    assert {'camel', 'case', 'snake'} <= set(visitor.words)


def test_scores_and_empty_sets():
    assert similarity.jaccard(set(), set()) == 1
    assert similarity.jaccard({'a'}, {'b'}) == 0
    assert similarity.cosine(Counter(), Counter()) == 1
    assert similarity.cosine(Counter(a=2), Counter(a=3)) == 1
    assert similarity.cosine(Counter(a=1), Counter(b=1)) == 0


def test_svg_escapes_labels():
    svg = similarity.heatmap(['a&b.py', 'x<y.py'], {('a&b.py', 'x<y.py'): 0.5})
    root = ET.fromstring(svg)
    assert root.tag.endswith('svg')
    assert 'a&amp;b.py' in svg
    assert 'x&lt;y.py' in svg
