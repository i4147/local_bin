#!/data/data/com.termux/files/usr/bin/env python
"""Render reports/python_similarity.{md,svg,mmd} from arena/similarity_data.json.

All scores come from the measured data file; group descriptions are curated
from module docstrings / CLI help text inspected during the run.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape
import os
import sys
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


ROOT: Path = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "arena" / "similarity_data.json").read_text())
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

# Curated families: (title, members, kind, evidence note)
FAMILIES: list[tuple[Any, ...]] = [
    (
        "Python comment/docstring strippers",
        [
            "rmcfull.py",
            "strip_python.py",
            "striper_termux.py",
            "tmc.py",
            "rmc.py",
            "trmc.py",
            "safe_rmc.py",
            "py_strip.py",
            "clean-py.py",
        ],
        "duplication",
        "Measured: J(rmcfull, strip_python)=0.551, C=0.830; 20+ exact/normalized function clones.",
    ),
    (
        "Tree-sitter / Termux cleaners",
        [
            "ts_cleaner.py",
            "tscleaner.py",
            "tcleaner.py",
            "termux_cleaner.py",
            "pycleaner.py",
        ],
        "duplication",
        "Measured: J(ts_cleaner, tscleaner)=0.153, C=0.431; J(tcleaner, termux_cleaner)=0.080, C=0.303.",
    ),
    (
        "tools/ batch drivers (gitignored, first-party)",
        [
            "tools/migrate_logging_mp.py",
            "tools/migrate_os_path_mp.py",
            "tools/migrate_logging.py",
            "tools/migrate_os_path.py",
            "tools/document_sources.py",
            "tools/document_sources_mp.py",
            "tools/run_typer_batches_mp.py",
            "tools/run_document_batches_mp.py",
            "tools/documentation_inventory_mp.py",
        ],
        "duplication",
        "Measured: J(migrate_logging_mp, migrate_os_path_mp)=0.924, C=0.970; colorize_diff cloned in 6 files.",
    ),
    (
        "Annotation engine",
        ["annot.py", "tools/infer_annotations_mp.py"],
        "duplication",
        "Measured: J=0.210, C=0.376, 6314 shared shingles; 5 exact function clones.",
    ),
    (
        "Git helpers",
        [
            "git-changelog.py",
            "git-trim.py",
            "git-prune-merged.py",
            "git-rewrite-history.py",
            "git_rewrite_history.py",
        ],
        "mixed",
        "Measured: _git_env + configure_logging exact clones (changelog/trim). Purpose overlap: trim/prune-merged, rewrite-history pair.",
    ),
    (
        "Package consolidators",
        ["consolidate.py", "consolidator.py"],
        "overlap",
        "Same stated purpose (package -> single module); no measured token duplication above thresholds.",
    ),
    (
        "Duplicate-file finders",
        ["dupf.py", "dupfx.py", "pydup.py", "dedupe-py.py"],
        "overlap",
        "dupf/dupfx: duplicates by content; pydup/dedupe-py: duplicate Python definitions. No measured duplication.",
    ),
    (
        "Cron explainers",
        ["cron_explain.py", "cronwhy.py"],
        "overlap",
        "Both explain cron expressions; independent implementations (no measured duplication).",
    ),
    (
        "Compression toolkits",
        ["xztool.py", "xztools.py"],
        "overlap",
        "Both unified compression/decompression CLIs; no measured duplication.",
    ),
    (
        "PyPI name checkers",
        ["check_pypi_name.py", "pkgname.py"],
        "overlap",
        "Both check PyPI name availability/similarity; no measured duplication.",
    ),
    (
        "MHTML converters",
        ["mhtml.py", "mhtml_to_html.py"],
        "overlap",
        "Both convert MHTML to HTML; no measured duplication.",
    ),
    (
        "Python re-indenters",
        ["reformat_python.py", "reformatter.py"],
        "overlap",
        "Both restore indentation/newlines to flattened Python; no measured duplication.",
    ),
    (
        "HTML minifiers",
        ["html-minify.py", "html_minifier_terser.py"],
        "overlap",
        "Both minify HTML; no measured duplication (shared CLI scaffolding only).",
    ),
    (
        "Midnight Commander clones",
        ["gmc.py", "dmc.py"],
        "overlap",
        "gmc: GNU mc clone (3.14+ target); dmc: minimal mc for Termux. No measured duplication.",
    ),
    (
        "Identifier shorteners",
        ["shorten_names.py", "shortener.py"],
        "overlap",
        "shortener.py wraps codeutils.shorten_names over a file tree; shorten_names.py implements the operation.",
    ),
]

TOP_PAIRS_FOR_GRAPH: list[tuple[Any, ...]] = [
    ("tools/migrate_logging_mp.py", "tools/migrate_os_path_mp.py"),
    ("tools/migrate_logging.py", "tools/migrate_os_path.py"),
    ("rmcfull.py", "strip_python.py"),
    ("tools/document_sources.py", "tools/document_sources_mp.py"),
    ("strip_python.py", "tmc.py"),
    ("rmcfull.py", "striper_termux.py"),
    ("strip_python.py", "striper_termux.py"),
    ("annot.py", "tools/infer_annotations_mp.py"),
    ("rmcfull.py", "tmc.py"),
    ("ts_cleaner.py", "tscleaner.py"),
    ("rmc.py", "tmc.py"),
    ("tcleaner.py", "termux_cleaner.py"),
]


def pair(a: str, b: str) -> dict:
    for p in DATA["pairs"]:
        if {p["a"], p["b"]} == {a, b}:
            return p
    return {"a": a, "b": b, "jaccard": 0.0, "containment": 0.0, "shared": 0}


def md() -> str:
    L: list[str] = []
    add = L.append
    add("# Python similarity analysis — local_bin")
    add("")
    add("Phase 2 deliverable · 2026-11-25 · companion files: `python_similarity.svg`, `python_similarity.mmd`")
    add("")
    add("## Executive summary")
    add("")
    add(
        f"- **{DATA['n_files']} Python files** analyzed ({len([p for p in DATA['sizes'] if '/' not in p])} root scripts + "
        f"{len([p for p in DATA['sizes'] if p.startswith('tools/')])} `tools/` helpers); "
        f"{DATA['n_functions_hashed']} substantial functions fingerprinted."
    )
    add("- **Real, heavy code duplication exists**, concentrated in three places:")
    add(
        "  1. `rmcfull.py` ↔ `strip_python.py` — Jaccard **0.551**, containment **0.830** (4 646 shared 8-token shingles). "
        "Same module docstring, same CLI, 12+ identical functions. `striper_termux.py`, `tmc.py`, `rmc.py` share the same core (J 0.14–0.27)."
    )
    add(
        "  2. `tools/migrate_logging_mp.py` ↔ `tools/migrate_os_path_mp.py` — Jaccard **0.924**, containment **0.970**: "
        "near-identical batch drivers differing only in the migration they run."
    )
    add(
        "  3. `annot.py` ↔ `tools/infer_annotations_mp.py` — containment **0.376** (6 314 shared shingles): "
        "the annotation-inference engine was copied from `annot.py` into the tools driver."
    )
    add(
        f"- **{len(DATA['exact_cross'])} exact cross-file function-clone clusters** and "
        f"{len(DATA['norm_cross'])} normalized (renamed-locals) clusters were measured; the largest spans 6 files (`colorize_diff`)."
    )
    add(
        "- **Functional overlap without code duplication**: 10+ pairs implement the same tool idea independently "
        "(consolidate/consolidator, dupf/dupfx, cron_explain/cronwhy, xztool/xztools, check_pypi_name/pkgname, "
        "mhtml/mhtml_to_html, reformat_python/reformatter, html-minify/html_minifier_terser, gmc/dmc, "
        "git-rewrite-history/git_rewrite_history). These are consolidation candidates, not copy-paste."
    )
    add(
        "- A shared **CLI scaffolding template** (`_STATE` dict + `_Args(SimpleNamespace)` + Typer app wiring) appears in "
        "**34 root scripts**; it inflates containment for otherwise unrelated tools and is *not* counted as functional overlap."
    )
    add(
        "- Name trap: `pylocate.py` (PyPI substring search) and `pylocate_1.py` (file-name database builder) are **unrelated** despite the names."
    )
    add("")
    add("## Scope and exclusions")
    add("")
    add(
        "- Included: all 98 root `*.py` + 24 `tools/*.py`. `tools/` is listed in `.gitignore` but contains first-party "
        "helper source (not vendored/generated code), so it was included and its findings are labelled; this is the one "
        "documented deviation from strict ignore-file respect."
    )
    add(
        "- Excluded: `.git/`, `arena/` (helpers created by this run), caches/venvs/build dirs (none exist), "
        "`documented/` (does not exist at Phase 2 time). Extensionless symlinks (`ax -> ax.py`, …) are not counted twice."
    )
    add(
        "- Vendored inline sections inside `git_rewrite_history.py` / `mhtml_to_html.py` are part of those files and were "
        "left in; neither file produced an above-threshold pair, so vendoring does not distort the findings."
    )
    add("")
    add("## Method (measured scores, not estimates)")
    add("")
    add(
        "**Metric 1 — file-level token shingles (code duplication).** Each file is tokenized with `tokenize`; comments, "
        "string/number *contents*, indentation and blank lines are normalized away; identifiers, keywords and operators "
        "are kept verbatim. The stream is cut into overlapping 8-token windows, each BLAKE2b-hashed into a set `S(file)`."
    )
    add("")
    add("```")
    add("Jaccard     J(A,B) = |S(A) ∩ S(B)| / |S(A) ∪ S(B)|      # overall duplication, 0..1")
    add("Containment C(A,B) = |S(A) ∩ S(B)| / min(|S(A)|,|S(B)|)  # 'A embedded in B', 0..1")
    add("```")
    add("")
    add(
        "Reported pairs: shared shingles ≥ 40 and (J ≥ 0.10 or C ≥ 0.25). J is size-sensitive (a small file inside a big "
        "one scores low J but high C); both are given so neither effect hides the other. Reproduce with "
        "`python3 arena/similarity_analysis.py`."
    )
    add("")
    add(
        "**Metric 2 — function-level structural clones (functional-overlap evidence).** Functions with ≥ 4 body "
        "statements and ≥ 40 AST nodes are hashed two ways after removing docstrings and positions: *exact* `ast.dump` "
        "(Type-1) and *normalized* with local identifiers (parameters, assigned names, function names, except/global "
        "names) replaced by `_` while called names, attributes, constants and control flow are kept (Type-2). Equal "
        "hashes across files form clone clusters with exact line ranges. This metric is identifier-renaming-proof but "
        "conservative: restructured logic will not match."
    )
    add("")
    add(
        "Confidence levels: **High** = measured clone/duplication verified by reading the code; **Medium** = measured "
        "score or identical stated purpose + interface, not line-verified; **Low** = name/purpose adjacency only."
    )
    add("")
    add("## Detailed findings — code duplication")
    add("")
    add("### F1. The comment/docstring stripper family — High confidence")
    add("")
    add("| Pair | J | C | shared shingles |")
    add("|---|---|---|---|")
    for a, b in [
        ("rmcfull.py", "strip_python.py"),
        ("strip_python.py", "tmc.py"),
        ("rmcfull.py", "striper_termux.py"),
        ("strip_python.py", "striper_termux.py"),
        ("rmcfull.py", "tmc.py"),
        ("rmc.py", "tmc.py"),
        ("striper_termux.py", "tmc.py"),
    ]:
        p = pair(a, b)
        add(f"| `{a}` ↔ `{b}` | {p['jaccard']:.3f} | {p['containment']:.3f} | {p['shared']} |")
    add("")
    add(
        "`rmcfull.py` and `strip_python.py` even carry the **same module docstring** "
        '("Strip Python comments/docstrings recursively, updating files atomically."). Verified identical functions '
        "(line ranges from the clone index):"
    )
    add("")
    add(
        "- `make_diff` — rmcfull.py:258-267 ↔ strip_python.py:274-286 ↔ striper_termux.py:272-284 ↔ tmc.py:347-359 (verified by reading)"
    )
    add("- `create_backup` — rmcfull.py:270-283 ↔ strip_python.py:289-302 ↔ striper_termux.py:287-300 ↔ tmc.py:362-374")
    add("- `_collect_comments` — rmcfull.py:95-118 ↔ strip_python.py:104-127 ↔ striper_termux.py:93-116")
    add("- `should_skip` — rmcfull.py:554-571 ↔ strip_python.py:657-674")
    add("- `_is_docstring` / `_strip_suite` / `leave_Module` — rmcfull.py:384-453 ↔ strip_python.py:424-493")
    add("- `colorize_diff` — rmcfull.py:746-765 ↔ strip_python.py:980-997 ↔ striper_termux.py:359-376")
    add(
        "- `_collect_docstrings` / `strip` / `_apply` / `remove_blank_lines` — strip_python.py:129-327 ↔ striper_termux.py:118-325"
    )
    add("- `on_leave` / `_is_docstring` / `_rewrite` — rmc.py:172-347 ↔ tmc.py:124-259")
    add("")
    add(
        "Explanation: these are generations of the same tool (libcst-based strippers), forked and re-wrapped rather than "
        "shared via import. `trmc.py`, `safe_rmc.py`, `py_strip.py`, `clean-py.py` state the same purpose but fall below "
        "the token thresholds (independent or heavily reworked implementations — functional overlap only)."
    )
    add("")
    add("### F2. tools/ migration drivers — High confidence")
    add("")
    p = pair("tools/migrate_logging_mp.py", "tools/migrate_os_path_mp.py")
    add(
        f"- `tools/migrate_logging_mp.py` ↔ `tools/migrate_os_path_mp.py`: J={p['jaccard']:.3f}, C={p['containment']:.3f} "
        f"({p['shared']} shared shingles). Same batch driver, different migration payload."
    )
    p = pair("tools/migrate_logging.py", "tools/migrate_os_path.py")
    add(f"- Non-mp twins: J={p['jaccard']:.3f}, C={p['containment']:.3f}.")
    add(
        "- `colorize_diff` is byte-identical in 6 drivers: tools/document_sources_mp.py:141-155, "
        "tools/documentation_inventory_mp.py:40-54, tools/migrate_logging_mp.py:36-50, tools/migrate_os_path_mp.py:36-50, "
        "tools/run_document_batches_mp.py:46-60, tools/run_typer_batches_mp.py:112-126."
    )
    add(
        "- `*_mp.py` vs non-mp versions are parallel implementations of the same driver "
        "(document_sources J=0.437; documentation_inventory J=0.301; verify_documented J=0.251; run_document_batches J=0.185; "
        "verify_py311 J=0.112; audit_migrations J=0.101)."
    )
    add("")
    add("### F3. annot.py ↔ tools/infer_annotations_mp.py — High confidence")
    add("")
    p = pair("annot.py", "tools/infer_annotations_mp.py")
    add(
        f"J={p['jaccard']:.3f}, C={p['containment']:.3f}, {p['shared']} shared shingles. Exact clones: `atom_re` "
        "(annot.py:1137-1143 ↔ tools:835-841), `_union_none` (1547-1557 ↔ 1098-1108), `encoding_problem` "
        "(3945-3957 ↔ 3062-3074), `merge_edits` (3982-3990 ↔ 2973-2981), `diff_stats` (4063-4067 ↔ 3115-3119). "
        "The inference engine inside `annot.py` was copied into the driver."
    )
    add("")
    add("### F4. Tree-sitter / Termux cleaners — Medium confidence")
    add("")
    p = pair("ts_cleaner.py", "tscleaner.py")
    add(
        f"- `ts_cleaner.py` ↔ `tscleaner.py`: J={p['jaccard']:.3f}, C={p['containment']:.3f}; `_regressed` is a renamed-locals "
        "clone (ts_cleaner.py:202-217 ↔ tscleaner.py:943-950). Both strip comments via tree-sitter."
    )
    p = pair("tcleaner.py", "termux_cleaner.py")
    add(
        f"- `tcleaner.py` ↔ `termux_cleaner.py`: J={p['jaccard']:.3f}, C={p['containment']:.3f} — shared helpers, "
        "same cleaning purpose."
    )
    add("")
    add("### F5. Git helpers — High confidence (clones), Medium (purpose overlap)")
    add("")
    add(
        "- `git-changelog.py` ↔ `git-trim.py`: exact clones `_git_env` (311-320 ↔ 165-175) and `configure_logging` "
        "(3536-3549 ↔ 1840-1855)."
    )
    add(
        "- `git-trim.py` ↔ `git-prune-merged.py`: both bulk-delete merged branches (stated purpose); no measured token "
        "duplication — functional overlap only."
    )
    add(
        "- `git-rewrite-history.py` ↔ `git_rewrite_history.py`: same stated tool (safe bulk history rewrite planner); "
        "independent implementations, no measured duplication — functional overlap only."
    )
    add("")
    add("### F6. Within-file clones — High confidence")
    add("")
    add(
        "- `pyocr.py`: `TesseractError.__init__` (87-92) ≡ `CuneiformError.__init__` (96-101); "
        "`write_file` (582-590) ≡ `write_file` (638-646) in two backend classes. Small, deliberate parallel classes; "
        "low-value dedup target."
    )
    add("")
    add("### F7. Shared CLI scaffolding — boilerplate, NOT functional overlap")
    add("")
    add(
        "34 root scripts share a `_STATE: dict[str, object]` + `class _Args(SimpleNamespace)` + Typer wiring template. "
        "Measured example: 170-token identical block `html-minify.py:23-51` ↔ `json_merger.py:31-59`; also "
        "`html-minify.py:190-205` ↔ `json_merger.py:319-334` (54 tokens). This explains high-containment/low-Jaccard "
        "pairs like html-minify↔sortplan (C=0.250), html-minify↔sonic (C=0.255), git-prune-merged↔html-minify (C=0.254). "
        "Per the analysis rules, shared boilerplate alone is not evidence of functional overlap."
    )
    add("")
    add("## Detailed findings — functional overlap without measured duplication")
    add("")
    add(
        "All pairs below state the same purpose in their docstrings/CLI help and expose equivalent interfaces, but share "
        "no above-threshold token overlap or function clones. Confidence: Medium (purpose + interface inspected, "
        "implementations independent). Consolidation would be a design decision, not a dedup."
    )
    add("")
    add("| Pair / group | Shared purpose | Evidence |")
    add("|---|---|---|")
    add(
        "| `consolidate.py` / `consolidator.py` | Flatten a Python package into one module | identical docstring intent |"
    )
    add("| `dupf.py` / `dupfx.py` | Find (and remove) duplicate files by content | CLI help text |")
    add(
        "| `pydup.py` / `dedupe-py.py` | Find duplicate Python definitions (AST-level) | docstrings; adjacent to dupf/dupfx |"
    )
    add("| `cron_explain.py` / `cronwhy.py` | Explain cron expressions, next runs | docstrings |")
    add("| `xztool.py` / `xztools.py` | Unified compression/decompression CLI | docstrings |")
    add("| `check_pypi_name.py` / `pkgname.py` | PyPI name availability + similarity | docstrings |")
    add("| `mhtml.py` / `mhtml_to_html.py` | MHTML → HTML conversion | docstrings |")
    add("| `reformat_python.py` / `reformatter.py` | Restore indentation to flattened Python | docstrings |")
    add("| `html-minify.py` / `html_minifier_terser.py` | HTML minification | docstrings |")
    add("| `gmc.py` / `dmc.py` | Midnight Commander clone for terminals | CLI description strings |")
    add(
        "| `shorten_names.py` / `shortener.py` | Shorten Python identifier names | shortener imports `codeutils.shorten_names` and maps it over files |"
    )
    add(
        "| `similarity_check.py` / `fuzzycmp.py` | Text/file similarity scoring | adjacent scope (repo grouping vs pairwise fuzzy compare); Low confidence |"
    )
    add("")
    add(
        "**Not overlap:** `pylocate.py` (PyPI search) vs `pylocate_1.py` (local file-name index) — similar names, different tools."
    )
    add("")
    add("## All above-threshold file pairs (measured)")
    add("")
    add("| A | B | Jaccard | Containment | shared |")
    add("|---|---|---|---|---|")
    for p in DATA["pairs"]:
        add(f"| `{p['a']}` | `{p['b']}` | {p['jaccard']:.3f} | {p['containment']:.3f} | {p['shared']} |")
    add("")
    add("## Clone cluster index")
    add("")
    add(f"{len(DATA['exact_cross'])} exact cross-file clusters (Type-1, docstrings excluded):")
    add("")
    for r in DATA["exact_cross"]:
        ms = "; ".join(f"`{m['file']}:{m['lines'][0]}-{m['lines'][1]}` `{m['name']}`" for m in r["members"])
        add(f"- [{r['n_funcs']} funcs / {r['n_files']} files] {ms}")
    add("")
    add(
        f"{len(DATA['norm_cross'])} normalized cross-file clusters (Type-2, renamed locals; superset of the above plus):"
    )
    add("")
    exact_keys = {tuple((m["file"], m["name"]) for m in r["members"]) for r in DATA["exact_cross"]}
    for r in DATA["norm_cross"]:
        key = tuple((m["file"], m["name"]) for m in r["members"])
        if key in exact_keys:
            continue
        ms = "; ".join(f"`{m['file']}:{m['lines'][0]}-{m['lines'][1]}` `{m['name']}`" for m in r["members"])
        add(f"- [{r['n_funcs']} funcs / {r['n_files']} files] {ms}")
    add("")
    add(
        "Same-file exact clusters: "
        + "; ".join(
            f"`{r['members'][0]['file']}` "
            + " ≡ ".join(f"`{m['name']}` ({m['lines'][0]}-{m['lines'][1]})" for m in r["members"])
            for r in DATA["exact_same_file"]
        )
        + "."
    )
    add("")
    add("## Recommendations (for human decision, not applied)")
    add("")
    add("1. Pick one canonical stripper (rmcfull/strip_python are the same tool) and retire or symlink the forks.")
    add(
        "2. Extract `colorize_diff`, `make_diff`, `create_backup`, `_git_env`, `configure_logging` into a shared helper "
        "module if the collection's single-file philosophy allows it."
    )
    add("3. Merge the two migrate_*_mp drivers into one parameterized driver.")
    add("4. Treat the functional-overlap pairs as a product decision (which tool to keep), not a refactor.")
    add("")
    return "\n".join(L)


def svg() -> str:
    """Two-panel SVG: top-pair Jaccard bars + family map. Hand-laid-out, valid XML."""
    pairs = [pair(a, b) for a, b in TOP_PAIRS_FOR_GRAPH]
    W = 1180
    bar_h = 26
    top_h = 60 + len(pairs) * bar_h + 30
    fam_y = top_h + 40
    col_w = 380
    fam_heights = []
    for _, members, _, _ in FAMILIES:
        fam_heights.append(46 + len(members) * 18 + 26)
    rows = [FAMILIES[i : i + 3] for i in range(0, len(FAMILIES), 3)]
    row_heights = [max(fam_heights[i * 3 + j] for j in range(len(row))) for i, row in enumerate(rows)]
    H = fam_y + sum(row_heights) + 40
    S: list[str] = []
    S.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="monospace">'
    )
    S.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    S.append(
        f'<text x="20" y="30" font-size="18" font-weight="bold">{escape("local_bin — Python similarity (measured 8-token shingle Jaccard)")}</text>'
    )
    y = 60
    max_j = max(p["jaccard"] for p in pairs)
    for p in pairs:
        label = f"{p['a']} ↔ {p['b']}"
        bw = int(560 * p["jaccard"] / max_j)
        S.append(f'<text x="20" y="{y + 14}" font-size="11">{escape(label)}</text>')
        S.append(f'<rect x="640" y="{y}" width="{bw}" height="18" fill="#4477aa"/>')
        S.append(
            f'<text x="{640 + bw + 6}" y="{y + 14}" font-size="11">J={p["jaccard"]:.3f} C={p["containment"]:.3f}</text>'
        )
        y += bar_h
    S.append(
        f'<text x="20" y="{top_h + 16}" font-size="14" font-weight="bold">{escape("Families (blue border = measured duplication; grey = functional overlap only)")}</text>'
    )
    cy = fam_y
    for row, rh in zip(rows, row_heights):
        for ci, (title, members, kind, note) in enumerate(row):
            x = 20 + ci * (col_w + 10)
            stroke = "#4477aa" if kind in ("duplication", "mixed") else "#999999"
            h = 46 + len(members) * 18 + 8
            S.append(
                f'<rect x="{x}" y="{cy}" width="{col_w}" height="{h}" fill="#f7f9fc" stroke="{stroke}" stroke-width="1.5" rx="6"/>'
            )
            S.append(f'<text x="{x + 10}" y="{cy + 18}" font-size="12" font-weight="bold">{escape(title)}</text>')
            for mi, m in enumerate(members):
                S.append(f'<text x="{x + 14}" y="{cy + 36 + mi * 18}" font-size="11">{escape(m)}</text>')
            short_note = note if len(note) <= 58 else note[:55] + "..."
            S.append(f'<text x="{x + 10}" y="{cy + h - 8}" font-size="9" fill="#555555">{escape(short_note)}</text>')
        cy += rh
    S.append("</svg>")
    return "\n".join(S) + "\n"


def mmd() -> str:
    def nid(path: str) -> str:
        return path.replace(".py", "").replace("-", "_").replace("/", "_").replace(".", "_")

    L = ["graph LR"]
    for i, (title, members, kind, _) in enumerate(FAMILIES):
        L.append(f'    subgraph F{i}["{title}"]')
        for m in members:
            L.append(f'        {nid(m)}["{m}"]')
        L.append("    end")
    seen = set()
    for a, b in TOP_PAIRS_FOR_GRAPH:
        p = pair(a, b)
        if p["shared"] == 0:
            continue
        key = (a, b)
        if key in seen:
            continue
        seen.add(key)
        L.append(f'    {nid(a)} ---|"J={p["jaccard"]:.2f}"| {nid(b)}')
    overlaps = [
        ("consolidate.py", "consolidator.py"),
        ("dupf.py", "dupfx.py"),
        ("cron_explain.py", "cronwhy.py"),
        ("xztool.py", "xztools.py"),
        ("check_pypi_name.py", "pkgname.py"),
        ("mhtml.py", "mhtml_to_html.py"),
        ("reformat_python.py", "reformatter.py"),
        ("html-minify.py", "html_minifier_terser.py"),
        ("gmc.py", "dmc.py"),
        ("git-rewrite-history.py", "git_rewrite_history.py"),
        ("git-trim.py", "git-prune-merged.py"),
    ]
    for a, b in overlaps:
        L.append(f'    {nid(a)} -.-|"overlap"| {nid(b)}')
    L.append('    git_changelog ---|"clone: _git_env"| git_trim')
    return "\n".join(L) + "\n"


def main() -> None:
    (REPORTS / "python_similarity.md").write_text(md(), encoding="utf-8")
    (REPORTS / "python_similarity.svg").write_text(svg(), encoding="utf-8")
    (REPORTS / "python_similarity.mmd").write_text(mmd(), encoding="utf-8")
    import xml.etree.ElementTree as ET

    ET.parse(REPORTS / "python_similarity.svg")
    print("md/svg/mmd written; SVG parses as valid XML")


if __name__ == "__main__":
    main()
