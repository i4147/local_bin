"""Generate a source similarity report without changing application scripts."""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import re
import statistics
import sys
from typing import TypedDict
from collections import Counter
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from xml.sax.saxutils import escape

import libcst as cst
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = {'.git', '.venv', 'venv', 'build', 'dist', '__pycache__'}
WORD = re.compile(r'[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[A-Z]+|\d+')
FLAG = re.compile(r'(?<![\w-])--[\w][\w-]*')


def tokens(value: str) -> list[str]:
    return [m.group().lower() for m in WORD.finditer(value.replace('_', ' '))]


class Features(cst.CSTVisitor):
    def __init__(self) -> None:
        self.funcs: set[str] = set()
        self.mods: set[str] = set()
        self.flags: set[str] = set()
        self.words: Counter[str] = Counter()
        self.render = cst.Module([])

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self.funcs.add(node.name.value)

    def visit_Name(self, node: cst.Name) -> None:
        self.words.update(tokens(node.value))

    def visit_SimpleString(self, node: cst.SimpleString) -> None:
        value = node.evaluated_value
        if isinstance(value, str):
            self.words.update(tokens(value))
            self.flags.update(FLAG.findall(value))

    def visit_FormattedStringText(self, node: cst.FormattedStringText) -> None:
        self.words.update(tokens(node.value))
        self.flags.update(FLAG.findall(node.value))

    def visit_Import(self, node: cst.Import) -> None:
        for alias in node.names:
            self.mods.add(self.render.code_for_node(alias.name))

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        prefix = '.' * len(node.relative)
        if node.module:
            prefix += self.render.code_for_node(node.module)
        if prefix:
            self.mods.add(prefix)


def extract(path: Path) -> tuple[str, Features | None, str | None]:
    relative = path.relative_to(ROOT).as_posix()
    try:
        source = path.read_bytes().decode('utf-8-sig')
        visitor = Features()
        cst.parse_module(source).visit(visitor)
        return relative, visitor, None
    except Exception as exc:
        return relative, None, f'{type(exc).__name__}: {exc}'


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 1.0


def cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return sum(n * b[k] for k, n in a.items()) / math.sqrt(sum(n*n for n in a.values()) * sum(n*n for n in b.values()))


def compare(a: Features, b: Features) -> dict[str, float]:
    return {'funcs': jaccard(a.funcs, b.funcs), 'mods': jaccard(a.mods, b.mods),
            'flags': jaccard(a.flags, b.flags), 'words': cosine(a.words, b.words)}


def heatmap(paths: list[str], scores: dict[tuple[str, str], float]) -> str:
    cell = 12
    margin = 350
    size = margin + cell * len(paths) + 20
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="10" y="20" font-size="14">Similarity: white = 0, blue = 1</text>']
    for i, name in enumerate(paths):
        label = escape(name)
        pos = margin + i * cell + 9
        parts.append(f'<text x="{margin-5}" y="{pos}" text-anchor="end" font-size="8">{label}</text>')
        parts.append(f'<text transform="translate({pos},{margin-5}) rotate(-90)" font-size="8">{label}</text>')
        for j, other in enumerate(paths):
            score = 1.0 if i == j else scores[(min(name, other), max(name, other))]
            shade = round(255 * (1-score))
            parts.append(f'<rect x="{margin+j*cell}" y="{margin+i*cell}" width="{cell}" height="{cell}" fill="rgb({shade},{shade},255)"/>')
    parts.append('</svg>')
    return '\n'.join(parts) + '\n'
class PairRecord(TypedDict):
    a: str
    b: str
    score: float
    components: dict[str, float]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--threshold', type=float, default=0.0)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1:
        parser.error('threshold must be between 0 and 1')
    logger.remove()
    if args.verbose:
        logger.add(sys.stderr)
    paths = sorted(p for p in ROOT.rglob('*.py') if not (set(p.relative_to(ROOT).parts) & PROTECTED))
    fallback = False
    try:
        with multiprocessing.Pool(4) as pool:
            results = list(pool.imap_unordered(extract, paths))
    except OSError as exc:
        if exc.errno != 38:
            raise
        fallback = True
        logger.warning('multiprocessing unavailable (errno 38); using sequential extraction')
        results = [extract(p) for p in paths]
    failures = {name: err for name, _, err in results if err}
    features = {name: f for name, f, err in results if not err and f is not None}
    names = sorted(features)
    records: list[PairRecord] = []
    matrix = {}
    all_scores = []
    for a, b in combinations(names, 2):
        components = compare(features[a], features[b])
        score = sum(components.values()) / 4
        all_scores.append(score)
        matrix[(a, b)] = score
        if score >= args.threshold:
            records.append({'a': a, 'b': b, 'score': round(score, 3),
                            'components': {k: round(v, 3) for k, v in components.items()}})
    records.sort(key=lambda item: (-item['score'], item['a'], item['b']))
    output = ROOT / 'tools' / 'reports'
    output.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'generated_at': datetime.now(UTC).isoformat(),
              'repo': ROOT.name, 'files_compared': len(names),
              'weights': {'funcs': .25, 'mods': .25, 'flags': .25, 'words': .25},
              'threshold': args.threshold, 'pair_count': len(all_scores), 'pairs': records}
    (output / 'similarity.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    (output / 'similarity.svg').write_text(heatmap(names, matrix), encoding='utf-8')
    stats = {'files_discovered': len(paths), 'files_compared': len(names), 'pair_count': len(all_scores),
             'mean': round(statistics.mean(all_scores), 3) if all_scores else None,
             'median': round(statistics.median(all_scores), 3) if all_scores else None,
             'maximum': round(max(all_scores), 3) if all_scores else None,
             'top_5': sorted(records, key=lambda r: (-r['score'], r['a'], r['b']))[:5],
             'failures': failures, 'sequential_fallback': fallback,
             'note': 'Generated before Phase 1 was completed.'}
    (output / 'phase2_stats.json').write_text(json.dumps(stats, indent=2) + '\n', encoding='utf-8')
    logger.info('Files: {} | Pairs: {} | Mean: {} | Median: {} | Max: {} | Failures: {}', len(names), len(all_scores), stats['mean'], stats['median'], stats['maximum'], len(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
