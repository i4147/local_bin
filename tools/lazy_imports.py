"""Conservatively move eligible standalone-script imports into one function.

Only an explicit allowlist of standard-library modules is considered; everything
else is reported for manual review. Run with --apply to write changes.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import subprocess
import sys
from collections import Counter
from pathlib import Path

import libcst as cst
from libcst.metadata import (
    MetadataWrapper,
    ParentNodeProvider,
    PositionProvider,
    ScopeProvider,
)
from libcst.metadata.scope_provider import FunctionScope, GlobalScope, ImportAssignment
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
# Known standard library imports, excluding registration-oriented and third-party modules.
SAFE_MODULES = {'math', 'itertools', 'functools', 'operator', 'bisect', 'heapq', 'textwrap', 'difflib', 'fnmatch', 'shlex', 'statistics'}


class StringCollector(cst.CSTVisitor):
    def __init__(self):
        self.values = []
        self.dynamic = False
        self.declared_global = set()
    def visit_Global(self, node):
        self.declared_global.update(item.name.value for item in node.names)
    def visit_Nonlocal(self, node):
        self.declared_global.update(item.name.value for item in node.names)
    def visit_SimpleString(self, node):
        if isinstance(node.evaluated_value, str):
            self.values.append(node.evaluated_value)
    def visit_Call(self, node):
        if isinstance(node.func, cst.Name) and node.func.value in {'globals', 'locals', 'eval', 'exec'}:
            self.dynamic = True


class Move(cst.CSTTransformer):
    def __init__(self, removals, insertions):
        self.removals = removals
        self.insertions = insertions
    def leave_SimpleStatementLine(self, original_node, updated_node):
        if original_node in self.removals:
            return cst.RemoveFromParent()
        return updated_node
    def leave_FunctionDef(self, original_node, updated_node):
        if original_node not in self.insertions:
            return updated_node
        body = list(updated_node.body.body)
        # Preserve function docstrings, which must remain the first statement.
        index = 0
        if body and isinstance(body[0], cst.SimpleStatementLine) and len(body[0].body) == 1:
            first = body[0].body[0]
            if isinstance(first, cst.Expr) and isinstance(first.value, (cst.SimpleString, cst.ConcatenatedString)):
                index = 1
        body[index:index] = self.insertions[original_node]
        return updated_node.with_changes(body=updated_node.body.with_changes(body=body))


def analyze(source):
    wrapper = MetadataWrapper(cst.parse_module(source))
    module = wrapper.module
    scopes = wrapper.resolve(ScopeProvider)
    parents = wrapper.resolve(ParentNodeProvider)
    positions = wrapper.resolve(PositionProvider)
    strings = StringCollector()
    module.visit(strings)
    render = cst.Module([])
    removals, insertions, skipped, moved = set(), {}, [], []
    imports = 0
    for line in module.body:
        if not isinstance(line, cst.SimpleStatementLine):
            continue
        for imp in line.body:
            if not isinstance(imp, (cst.Import, cst.ImportFrom)):
                continue
            imports += 1
            reason = None
            aliases = imp.names
            module_name = (render.code_for_node(imp.module) if isinstance(imp, cst.ImportFrom) and imp.module else '')
            if isinstance(imp, cst.Import) and len(imp.names) == 1:
                module_name = render.code_for_node(imp.names[0].name)
            if isinstance(imp, cst.ImportFrom) and imp.relative:
                reason = 'relative import'
            elif module_name == '__future__':
                reason = 'future import'
            elif module_name not in SAFE_MODULES:
                reason = 'module side effects or safety not established'
            elif len(line.body) != 1 or isinstance(aliases, cst.ImportStar) or len(aliases) != 1:
                reason = 'multiple imports or star import'
            elif line.leading_lines or line.trailing_whitespace.comment:
                reason = 'attached comments'
            elif strings.dynamic:
                reason = 'dynamic namespace inspection in module'
            else:
                scope = scopes.get(imp)
                assignments = [a for a in scope.assignments if isinstance(a, ImportAssignment) and a.node is imp] if isinstance(scope, GlobalScope) else []
                if not isinstance(scope, GlobalScope) or len(assignments) != 1:
                    reason = 'ambiguous import binding'
                else:
                    assignment = assignments[0]
                    name = assignment.name
                    refs = list(assignment.references)
                    if name in strings.declared_global:
                        reason = 'global or nonlocal declaration'
                    elif any(a is not assignment and a.name == name for a in scope.assignments):
                        reason = 'binding reassigned at module level'
                    elif any(name in value for value in strings.values):
                        reason = 'name appears in a string (exports, patching or forward reference possible)'
                    elif not refs:
                        reason = 'unused import; side effects unknown'
                    elif any(not isinstance(ref.scope, FunctionScope) for ref in refs):
                        reason = 'module/class/import-time usage'
                    elif len({ref.scope for ref in refs}) != 1:
                        reason = 'used in multiple functions'
                    else:
                        target_scope = refs[0].scope
                        assert isinstance(target_scope, FunctionScope)
                        target = target_scope.node
                        if not isinstance(target, cst.FunctionDef) or not isinstance(target.body, cst.IndentedBlock):
                            reason = 'unsupported function body'
                        elif any(a.name == name for a in target_scope.assignments):
                            reason = 'binding also assigned inside function'
                        else:
                            for ref in refs:
                                node = ref.node
                                while node is not target and node in parents:
                                    if isinstance(node, cst.Annotation):
                                        reason = 'annotation usage'
                                        break
                                    node = parents[node]
                                if reason:
                                    break
                            if not reason:
                                removals.add(line)
                                insertions.setdefault(target, []).append(line)
                                moved.append({'line': positions[imp].start.line, 'import': render.code_for_node(imp), 'function': target.name.value})
            if reason:
                skipped.append({'line': positions[imp].start.line, 'import': render.code_for_node(imp), 'reason': reason})
    return module.visit(Move(removals, insertions)).code, imports, moved, skipped


def analyze_path(path):
    try:
        raw = path.read_bytes()
        if raw.startswith(b'\xef\xbb\xbf'):
            return path, None, None, 0, [], [], 'UTF-8 BOM requires manual review'
        source = raw.decode('utf-8')
        transformed, imports, moved, skipped = analyze(source)
        return path, source, transformed, imports, moved, skipped, None
    except Exception as exc:
        return path, None, None, 0, [], [], f'{type(exc).__name__}: {exc}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    logger.remove()
    if args.verbose:
        logger.add(sys.stderr)
    files = sorted(ROOT.glob('*.py'))
    results, failures, changed, before, count = {}, {}, [], 0, 0
    fallback = False
    try:
        with multiprocessing.Pool(4) as pool:
            analyses = list(pool.imap_unordered(analyze_path, files))
    except OSError as exc:
        if exc.errno != 38:
            raise
        fallback = True
        logger.warning('multiprocessing unavailable (errno 38); using sequential analysis')
        analyses = [analyze_path(path) for path in files]
    for path, source, transformed, imports, moved, skipped, error in sorted(analyses):
        try:
            if error:
                failures[path.name] = error
                continue
            before += imports
            count += len(moved)
            results[path.name] = {'imports_before': imports, 'moved': moved, 'skipped': skipped}
            if moved:
                if args.apply:
                    path.write_text(transformed, encoding='utf-8', newline='')
                    result = subprocess.run([sys.executable, '-m', 'compileall', '-q', str(path)], capture_output=True, text=True)
                    if result.returncode:
                        path.write_text(source, encoding='utf-8', newline='')
                        failures[path.name] = 'compileall failed, restored: ' + result.stderr
                        count -= len(moved)
                        results[path.name]['moved'] = []
                        continue
                changed.append(path.name)
        except Exception as exc:
            failures[path.name] = f'{type(exc).__name__}: {exc}'
    reasons = Counter(item['reason'] for result in results.values() for item in result['skipped'])
    stats = {'files_scanned': len(files), 'top_level_imports_before': before,
             'top_level_imports_after': before - count, 'imports_moved': count,
             'imports_kept_by_reason': dict(sorted(reasons.items())), 'files_changed': changed,
             'failures': failures, 'files': results, 'applied': args.apply, 'sequential_fallback': fallback,
             'notes': ['Root scripts treated as standalone as directed by user.', 'Only explicitly allowlisted modules and single-function uses are moved.', 'TYPE_CHECKING moves and ambiguous cases require manual review.']}
    if args.apply:
        output = ROOT / 'tools' / 'reports' / 'phase1_stats.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(stats, indent=2) + '\n', encoding='utf-8')
    logger.info('Files: {} | Imports: {} -> {} | Moved: {} | Files changed: {} | Failures: {}', len(files), before, before-count, count, len(changed), len(failures))
    logger.info('Kept by reason: {}', dict(reasons))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
