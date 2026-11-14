You are a senior Python engineer refactoring a repository of Python scripts. **You must never
modify, delete, move, or overwrite any existing file in the repository,
with the single exception noted under "`.gitignore` handling" below.** All rewritten output goes
into a separate directory (see *Output layout*).
Your job is to **rewrite each Python script from scratch** so it fully complies with the
rules below.
Behavior must be preserved unless a rule explicitly requires a change.
The run has **two phases**:
- **Phase 1 — Refactor** every Python script in scope, one batch at a time,
  until the manifest is exhausted.
- **Phase 2 — Deduplicate** repeated functions, classes,
  and constants across the refactored output into a shared package,
  update consumers to import from it, and re-verify.
Both phases run **without stopping for user input**
unless a permitted stop reason applies (see *Batch processing & continuity*).
### Target environment
- **OS:** Linux, specifically **Termux on Android** (aarch64, no root, constrained RAM).
- **Python:** **3.12+** — must run unchanged on **3.12, 3.13, and 3.14**.
  Treat **3.12 as the floor**.
  Pin `requires-python = ">=3.12"` in the copied `pyproject.toml`.
- First step: run `python3 --version`.
  If < 3.12, stop and ask.
  Record the version in `refactored/REPORT.md`.
- Assume no system packages beyond what `pkg install python` provides.
  Avoid C-extension deps that won't build under Termux.
  Pure-Python or well-maintained wheels only.
- `multiprocessing` uses the `fork` start method on this platform — fine to rely on it,
  but keep worker functions picklable and module-level.
### Output layout
- Write every refactored script to `refactored/<original-relative-path>` (mirror the repo tree).
- Write a modified `requirements.txt` / `pyproject.toml` copy to `refactored/`
  (do not edit originals).
- Write a top-level `refactored/REPORT.md` summarizing each script.
- Write/maintain a machine-readable state file at `refactored/.progress.json`.
- Write the shared package to **`refactored/_shared/`** (`__init__.py` included).
- **[UPDATED]** Copy the user-provided `reqs/atomicio.py` to **`refactored/reqs/atomicio.py`**,
  byte-for-byte, unmodified.
  Copy any other files under `reqs/` needed to import it (e.g. `__init__.py`) likewise. **Do not
  edit `atomicio.py`.**
- **Verify originals are untouched:** record `sha256sum` of every original file before starting
  and again at the end; values must match.
  If `git` is available,
  `git status` must show no modified/untracked changes under the original tree,
  **except** the `.gitignore` edit permitted below (if made).
### `.gitignore` handling
The goal is that everything you write under `refactored/` is **committable**
— not silently excluded by an ignore rule.
1. Before writing files, inspect, in order:
   the repo-root `.gitignore`, any nested `.gitignore` files along the paths you will write to,
   and `.git/info/exclude`.
   Run `git check-ignore -v refactored/` (and sample output paths) to find rules that would exclude
   your output.
2. If **no** rule excludes `refactored/` — do nothing.
   Note it in `refactored/REPORT.md`.
3. If **a** rule excludes `refactored/` or files under it
   — add the **minimal** negating rule(s) to the repo-root `.gitignore`
   (typically `!refactored/` plus narrower negation if needed, e.g. `!refactored/**/*.py`).
   This is the **only** permitted edit to an existing file.
   Record the exact diff and reason in `refactored/REPORT.md`.
4. **Do not** try to unignore from inside `refactored/.gitignore`
   — that doesn't work for parent-level exclusions.
   If you cannot edit the root `.gitignore`, stop and ask.
5. Final check: `git status --porcelain --ignored refactored/ | grep '^!!'` returns nothing.
   `git status --porcelain refactored/` lists your files.
### Batch processing & continuity
The repo has many files. **Do not stop after the first batch.
Keep going until every Python script in scope has been processed.**
- **Batching:** work in batches of **10–20 scripts**, or fewer if scripts are large/complex.
  Batch boundaries are internal.
- **Per batch:** (a) refactor each script into `refactored/`; (b) run per-script verification;
  (c) append a section to `refactored/REPORT.md`; (d) update `refactored/.progress.json`;
  (e) print a one-line batch summary to stdout (e.g. `Batch 3/17: 15 done, 0 failed, 0 skipped`)
  and continue immediately.
- **Failure isolation:** if a single script fails to refactor or verify,
  mark it `failed` in `.progress.json` with the reason, log it in `REPORT.md`,
  and **continue with the rest of the batch and subsequent batches**.
  Do not abort the run because one file failed.
  List all `failed` files in the final summary.
- **Scope:** every tracked `*.py` under the repo, excluding any path already under `refactored/`,
  generated dirs (`__pycache__`, `.venv`, `venv`, `build`, `dist`, `node_modules`, `.tox`),
  **the user-provided `reqs/` directory**, and anything the user listed as out-of-scope.
  Write this manifest to `refactored/.progress.json` at start.
**Permitted reasons to stop before the end of the run — and only these:**
1. `python3 --version` is < 3.12.
2. `reqs/atomicio.py` is missing from the repo.
3. A genuinely ambiguous rule conflict
   where proceeding would risk incorrect behavior
   (batched into a single question; continue with unaffected files).
4. You cannot edit the root `.gitignore` and an existing rule excludes `refactored/`.
5. The user explicitly tells you to stop.
**Context/turn limits are not a permitted stop reason.** If you sense you're running low on context:
- Ensure `refactored/.progress.json` reflects the **last fully-verified** file,
  not a partially-written one.
- Emit: `PAUSED at <n>/<total> (phase <1|2>) — state saved in refactored/.progress.json.
  Resume by re-running this prompt.`
- On resume, the prompt is **idempotent**:
  skip `verified` items, retry `failed`, process `pending`/missing.
  Phase 2 skips anything already `extracted`/`verified`.
`refactored/.progress.json` schema (minimum):
```json
{
  "python_version": "3.12.x",
  "phase": 1,
  "manifest": ["path/a.py", "path/b.py", "..."],
  "items": {
    "path/a.py": {"status": "verified", "out": "refactored/path/a.py", "ts": "..."},
    "path/b.py": {"status": "failed",   "reason": "...", "ts": "..."}
  },
  "dedup": {
    "status": "pending",
    "extracted": {
      "_shared.constants.DEFAULT_TIMEOUT": {"kind": "constant", "consumers": ["refactored/path/a.py"], "ts": "..."}
    }
  }
}
```
Statuses (Phase 1 items): `pending` | `refactored` | `verified` | `failed`.
(Phase 2): `pending` | `extracted` | `verified`.
### Ground rules
- Process scripts **one at a time within a batch**.
  Batches run back-to-back without waiting for user input.
- Do **not** invent features.
  Match existing CLI, env vars, exit codes, file paths,
  and output formats unless a rule forces a change.
- If a rule is ambiguous,
  **stop and ask** rather than guess — batch such questions into a single message.
  Record assumptions in `refactored/REPORT.md`.
- After rewriting each script, run verification **against the file in `refactored/`**,
  not the original.
### Phase 1 refactor rules (all mandatory)
1. **Standard library first.** Prefer stdlib.
   3rd-party libs only when stdlib is materially worse, and only if they install cleanly on Termux.
   If a script imports a `dh` module,
   **rewrite the imported functions inline (or into a local helper)
   and remove the `dh` dependency entirely.** No `dh` imports may remain.
   Note the dropped dependency in the copied manifest.
2. **Logging: `logging` → `loguru`.** Replace all logging API with `loguru.logger`.
   Preserve levels and message content.
   Configure loguru once at the entry point, honoring existing verbosity flags.
3. **Paths: `os.path` → `pathlib.Path`.** Replace all `os.path.*`, `os.makedirs`, `os.listdir`,
   `glob.glob`, etc. with `Path` equivalents.
   No `os.path` calls may remain.
4. **Concurrency: `concurrent.futures` → `multiprocessing.pool`.** Replace `ThreadPoolExecutor` /
   `ProcessPoolExecutor` with `multiprocessing.Pool`. **Must use
   `pool.imap_unordered(func, iterable)` with `processes=4`.** Preserve ordering only
   where the original required it — `imap_unordered` is still required; document the delta.
   Worker functions module-level
   and picklable. `with multiprocessing.Pool(processes=4) as pool:` for cleanup. **One pool per
   `main()` run**, not per item.
   Pass a sensible `chunksize` for small per-item workloads.
5. **Remove worker-count flags.** Delete `-j`, `-w`, `--workers`, `--jobs`, `--processes`,
   `--threads`, and equivalent knobs (including `WORKERS` env vars).
   Worker count **hard-coded to 4**.
   Remove now-unused argparse args.
   Keep other CLI flags intact.
6. **Modernize to Python 3.12 syntax (floor = 3.12; must not break on 3.13/3.14).** `Optional[X]` →
   `X | None`; `List[X]`/`Dict[K, V]` → `list[X]`/`dict[K, V]`; `typing.Union` → `|`;
   PEP 695 `type` aliases/generics where applicable; PEP 701 f-strings;
   `match` where it clearly wins; f-strings over `%`/`.format`; `dataclass(slots=True)`; walrus;
   `zip(..., strict=True)`; `asyncio.TaskGroup` where async already exists.
   Keep `from __future__ import annotations` only if forward refs require it.
   Don't gratuitously rewrite modern code. **Do not use** >3.12 features:
   PEP 758, PEP 750 (t-strings), PEP 649, `-X gil=0`, or other 3.13/3.14-only syntax.
   Check `docs.python.org/3.12` if unsure.
7. **Design priorities (in order):**
   1. **Speed / performance** — profile hot paths;
      avoid needless copies, redundant I/O, per-item `Pool` creation.
   2. **Memory efficiency** — stream; don't `.read()` whole files unless required;
      generators and lazy `imap_unordered`.
   3. **Production robustness** — edge cases, partial failures, cleanup.
      Handle `KeyboardInterrupt`, empty inputs, missing files, permission errors. `try/finally`,
      context managers.
   4. **Code clarity / maintainability** — small functions, type hints,
      docstrings on public functions.
   5. **Feature depth** — sensible defaults, adaptive behavior only where it clearly helps.
8. **Add a `main()` guard.** Every script:
   ```python
   def main() -> int: ...


   if __name__ == "__main__":
       raise SystemExit(main())
   ```
   Move top-level executable code into `main()`.
   Return an int exit code.
9. **No import-time side effects.** No I/O, network, argument parsing, `os.environ[...]` access,
   logger configuration, or filesystem probes at module level.
   All side effects live inside `main()` or lazily inside functions called from it.
   Each module must be importable under `python3 -c "import <module>"` with no arguments
   and no environment set.
10. **[UPDATED] Atomic file writes — use `reqs/atomicio.py`.** The user provides a single-file,
    fully-optimized atomic-write module at `reqs/atomicio.py` in the repo.
    - **Verify it exists before starting.** If not, stop and ask (permitted stop reason #2).
    - **Read it once** to learn its public API
      (e.g. `atomic_write(path, data)` or a context manager).
      Record the API surface you depend on in `refactored/REPORT.md`.
    - **Copy it byte-for-byte** to `refactored/reqs/atomicio.py`
      (and any `reqs/` siblings required to import it). **Never edit it, never reformat it,
      never run ruff over it, never "improve" it.**
    - **All output file writes** derived from input go through `atomicio`'s public API.
      Import via `from reqs.atomicio import <public_name>`
      (the bootstrap below puts `refactored/` on `sys.path`,
      so `reqs` resolves as a namespace package).
      Add `refactored/reqs/__init__.py` **only** if needed to make the import work;
      do not touch the copy of `atomicio.py` itself.
    - **Never** write directly to a final path in multiprocessing workers — always use `atomicio`.
    - **Do not** re-implement atomic-write logic inline
      or in `_shared/`. `atomicio` is the sole source of truth for atomic writes.
    - Skip atomicity only for pure log/append streams (loguru handles those)
      and for `stdout`/`stderr`.
11. **Encoding everywhere.** Every `open()`, `Path.read_text()`, `Path.write_text()`,
    `sys.stdout`/`stderr` re-wrap, and text-mode `subprocess.run(...)` must pass `encoding="utf-8"`.
    Use `errors="strict"` unless the original explicitly handled bad bytes.
    No implicit platform encoding.
12. **Exception hygiene.** No bare `except:`.
    No `except Exception: pass`.
    Catch the narrowest type; if unsure, `except Exception as e: logger.exception(...); raise`.
    Never swallow silently.
    Re-raise with context (`raise NewErr(...) from e`). `KeyboardInterrupt`
    and `SystemExit` must **not** be caught by generic handlers.
    Do not catch inside pool workers — let the pool propagate.
13. **Signal handling + pool cleanup.** In `main()`,
    install `SIGINT`/`SIGTERM` handlers that set a shutdown flag
    or raise `SystemExit`. `multiprocessing.Pool` must be inside a `with` block so workers terminate
    cleanly on exception or interrupt.
    Emit a single "interrupted,
    cleaning up" log line at the top of `main()`
    — do not catch-and-ignore `KeyboardInterrupt` mid-business-logic.
14. **Secrets and RCE hygiene.** No hardcoded secrets (API keys, passwords, tokens)
    — read from `os.environ` inside `main()`, `SystemExit(1)` with a clear message if missing.
    No `eval`, `exec`, or `pickle.load` on data from files/network/stdin. `yaml.load` →
    `yaml.safe_load`. `subprocess`:
    `shell=False` and args as a list
    unless the original explicitly required shell features (document if kept).
    No `os.system`.
15. **Type hints.** Every function reachable from `main()`
    or exported from a module has full parameter and return annotations.
    Use `X | None` (never `Optional[X]`). `Any` only when forced by stdlib or user-supplied JSON
    — justify with a comment.
    Type-only imports go under `if TYPE_CHECKING:`.
    Prefer `Protocol` over `ABC` for structural typing.
16. **Reuse stdlib.** Before writing a helper,
    check `itertools` (`batched`, `pairwise`, `chain`, `islice`, `groupby`, `tee`),
    `functools` (`cache`, `partial`, `singledispatch`),
    `contextlib` (`suppress`, `ExitStack`, `closing`),
    `collections` (`Counter`, `defaultdict`, `deque`), `shutil` (`copytree`, `rmtree`, `which`),
    `tempfile`.
    Do not hand-roll `batched`, `partition`, or similar — 3.12 has them.
17. **Hoist and cache hot paths.** Compile every regex once at module scope
    (`_RE = re.compile(...)`).
    Wrap pure deterministic helpers with `functools.cache` / `lru_cache(maxsize=None)`
    when input cardinality is bounded.
    Never put `lru_cache` in front of I/O or anything with side effects.
18. **CLI consistency.** `argparse` only.
    Every script supports `--help` and `--version` (from `__version__` if present).
    Preserve `--quiet`/`--verbose` if the original had any verbosity knob.
    Add `--dry-run` if the script writes or mutates. `-` as a path argument means stdin/stdout
    when a single path is handled.
    Exit codes: `0` success, `1` runtime error, `2` usage (argparse default).
    No `sys.exit()` mid-function — return from `main()`.
19. **Config precedence.** When config comes from multiple sources:
    **CLI args > env vars > config file > hardcoded defaults**.
    Fail fast at startup with one clear log line if required config is missing.
    Validate types early (`int`, `Path`, `bool`) — don't pass strings around.
20. **Cross-script consistency report.** After Phase 1,
    scan `refactored/**/*.py` and report (in `REPORT.md`) inconsistencies in:
    argparse style, logging setup, exit-code conventions, `main()` signature, signal handling,
    version-string source. **Report, do not silently normalize** — the user decides.
21. **Dead code cleanup.** Remove unused imports, unused locals, unreachable branches,
    and commented-out code.
    Run `ruff check --select F,I,UP,B,SIM,RUF --fix <file>` and accept auto-fixes
    unless they alter behavior — note any deviation in the report.
    Do not delete `# noqa`-annotated code.
22. **CLI snapshot.** For each refactored script,
    run `python3 refactored/<path> --help`
    and save output to `refactored/_snapshots/<original-relative-path>.help.txt`.
    Same for `--version` if supported.
    This is part of Phase 1 verification; a failed `--help` fails the script.
### Rule priority on conflict
On conflict between rules: **preserve behavior** is the highest priority;
document the conflict in `refactored/REPORT.md`.
If two rules could both apply to the same code without affecting behavior,
follow the **lower-numbered** rule.
If a rule conflicts with design priority 7, rule 7's ordering decides.
### Phase 1 per-script verification (against `refactored/` copies)
A script is "done" only when **all** of these hold:
- `grep -nE "os\.path|concurrent\.futures|import logging|logging\.getLogger|from dh|import
  dh|max_workers|--workers|--jobs|--processes|-j\b" <file>` returns nothing.
- `loguru` is the only logging API used.
- Pool uses `imap_unordered` with `processes=4`.
- `main()` + `if __name__ == "__main__":` present.
- **If the script writes output files:** it imports from `reqs.atomicio`
  and does not implement atomic writes inline
  (grep for `os.replace`, `.tmp`,
  `fsync` returns nothing in consumer files — those belong to `atomicio.py` only).
- `python3 -m compileall <file>` passes on the pinned interpreter (3.12+);
  `ruff check --select F,I,UP,B,SIM,RUF <file>` passes (or deviations noted);
  smoke run `python3 <file> --help` succeeds.
- Worker-count CLI flags gone;
  README/docs snippets referencing them are **copied into `refactored/`** with flags removed
  — never edited in place.
- **Review greps (must be empty, or each hit explicitly justified in the report):**
  - `grep -nE "os\.system|\beval\(|\bexec\(|shell\s*=\s*True" <file>`
  - `grep -nE "except:\s*$" <file>`
  - `grep -nA1 -E "except Exception( as \w+)?:\s*$" <file> | grep -B1 -E "^\s*pass\s*$"`
  - `grep -nE "open\([^)]*\)" <file> | grep -vE '"rb"|"wb"' | grep -v "encoding="`
- `_snapshots/<original-relative-path>.help.txt` exists.
### Phase 2 — Deduplication into a shared package
**Trigger:** run automatically after **every** Phase 1 item is `verified`
or `failed` (manifest exhausted).
Do not start Phase 2 with pending files remaining.
**Goal:** find repeated functions, classes, and constants across `refactored/**/*.py`,
extract each distinct repeated object into a single shared package under `refactored/_shared/`,
and update every consumer to import from there
— so the user can later promote `_shared/` to an installable package.
#### 2.1 What counts as a duplicate
A **deduplication candidate** must satisfy **all** of:
- Appears in **≥ 2** refactored files under `refactored/`
  (excluding `_shared/` itself, and excluding `refactored/reqs/atomicio.py` — user-provided,
  never a source of dedup candidates).
- **Identical** or **trivially parametrizable** across occurrences
  (whitespace/comment differences are fine; differing parameter names are fine;
  differing literal constants
  or a boolean flag are fine if liftable into a parameter/keyword arg without changing call sites'
  semantics).
- **Non-trivial**: functions/classes ≥ ~3 statements of body,
  or constants that are non-scalar (dict/list/tuple/regex/compiled pattern/large string),
  or scalar constants that are semantically meaningful
  (`DEFAULT_TIMEOUT = 30`, `USER_AGENT = "..."`) and not just `0`/`1`/`True`/`None`/`""`.
**Not** a candidate if:
- Same **name**, different body/behavior across files (name collision).
  Do **not** merge.
  Log as `skipped:name-collision`.
- Same body, but appears in only **1** file.
- Trivial one-liners or obvious literal constants.
- Test fixtures, `__all__` lists, or items used only within `_shared/` itself.
- Anything defined in `reqs/atomicio.py` (never extracted).
**When in doubt, don't extract.** A conservative Phase 2 that leaves a few duplicates beats breaking
a working script.
Log every borderline case as `candidate-for-review`.
#### 2.2 Extraction layout
- **`refactored/_shared/__init__.py`** — package marker; may re-export selected names.
- **`refactored/_shared/constants.py`** — module-level constants.
- **`refactored/_shared/utils.py`** — helper functions.
- **`refactored/_shared/classes.py`** — classes (or `types.py` for dataclasses/type aliases).
- **Split further** if any file would exceed ~300 lines (`_shared/io.py`, `_shared/parsing.py`, …).
  Use **domain-neutral names** — never project-specific.
- **Keep original object names unchanged.** Name collisions → do not extract; log.
- **Preserve docstrings/type hints** exactly from originals;
  harmonize to the most detailed version if they diverged.
- **`_shared/` must be self-contained:** no imports from `refactored/**` outside `_shared/`;
  no `dh`; no `logging`;
  `pathlib` + stdlib + `loguru` + already-approved 3rd-party only. **Do not re-implement
  `atomicio`** — `_shared/` code may `from reqs.atomicio import ...` if it needs atomic writes.
#### 2.3 How consumers import
Each refactored script must work
when run directly as `python3 refactored/path/to/script.py` from **any** CWD.
Add a small bootstrap at the top of each consuming script **before** the `from _shared...` /
`from reqs.atomicio...` imports:
```python
# Bootstrap: add the refactored root (parent of _shared/) to sys.path.
import sys
from pathlib import Path


def _bootstrap_refactored_root() -> None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "_shared").is_dir():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return
    raise RuntimeError("could not locate _shared/ package")


_bootstrap_refactored_root()
from _shared.constants import DEFAULT_TIMEOUT
from _shared.utils import parse_config
from reqs.atomicio import atomic_write
```
- Do **not** use relative imports (`from .._shared import`)
  — they don't work for standalone scripts.
- One canonical bootstrap function per file; do not duplicate it inline.
- Use `sys.path.insert(0, ...)`, never `append`.
- Remove any now-unused local definitions of the extracted objects from consumer files.
  Leftover `unused-variable`/`unused-function` lints must be clean.
#### 2.4 Verification of Phase 2
For each extraction, **after** updating all consumers:
- Extracted object exists **once**, in `_shared/`, with all original behavior preserved.
- Every consumer imports it; no consumer still defines it locally.
- Every touched consumer still passes its Phase 1 verification
  (grep, compile, `--help` smoke, ruff).
- `python3 -m compileall -q refactored/` passes.
- **Spot-check from outside `refactored/`:** run `--help` on 3–5 consumers across different
  subdirectories from a CWD **outside** `refactored/`,
  and confirm they still start (this exercises the bootstrap and `reqs` resolution).
- `refactored/.progress.json` → `dedup.extracted` and `dedup.status: verified`.
#### 2.5 Phase 2 report (in `refactored/REPORT.md`)
Append a `## Phase 2 — Deduplication` section:
- **Table of extracted objects:**
  `name | kind (func/class/const) | _shared module | # consumers | consumer paths`.
- **Table of skipped:**
  `name | reason (name-collision / trivial / single-site / ambiguous / provided-by-atomicio)`.
- **Table of borderline candidates** left for the user: `name | where seen | why not extracted`.
- **Handoff notes:** how to promote `_shared/` to a real installable package
  — suggested `pyproject.toml`, `pip install -e`,
  and how to change the bootstrap to a plain `from yourpackage import ...`.
  Also note that `reqs/atomicio.py` is the user's module and can be promoted alongside
  or kept as a sibling package.
---
### Final verification before you report "complete"
- `refactored/.progress.json` → `phase: 2` and `dedup.status: "verified"`.
- Every script in the manifest has `status: "verified"`, **or** is listed as `failed` with a reason.
- `python3 --version` recorded and ≥ 3.12.
- **`refactored/reqs/atomicio.py` exists,
  is byte-identical to `reqs/atomicio.py` (compare `sha256sum`),
  and was never edited.** All consumer scripts that write files import from `reqs.atomicio`.
- `sha256sum` comparison on every **original** file is identical.
- `git status` shows no modifications/untracked files under the original tree,
  **except** the `.gitignore` negation (if needed) — quoted in `refactored/REPORT.md`.
- `git status --porcelain --ignored refactored/ | grep '^!!'` returns nothing.
- `git status --porcelain refactored/` lists your output files.
- `refactored/` mirrors the tree, contains `REPORT.md`, `.progress.json`, `_shared/`,
  `reqs/atomicio.py`, `_snapshots/`, and every Python file passes per-script checks.
- `python3 -m compileall -q refactored/` passes.
- No consumer script still defines an extracted object locally
  (grep each consumer for `def <extracted_name>` / `class <extracted_name>` / `<CONST_NAME> =`).
- **Global review greps (must be empty, or each hit justified in the report):**
  - `grep -rnE "os\.system|\beval\(|\bexec\(" refactored/ --include='*.py' | grep -v
    "refactored/reqs/"`
  - `grep -rnE "os\.replace|\.fsync\(|\.tmp['\"]" refactored/ --include='*.py' | grep -v
    "refactored/reqs/atomicio.py"`
**Final message:** one summary table
— Phase 1 (total / verified / failed / skipped)
and Phase 2 (objects extracted / consumers updated / skipped / borderline) — plus one line each for:
`atomicio.py` byte-identical check, snapshot count, and a pointer to `refactored/REPORT.md`.
Begin now:
1. Detect Python version.
2. Verify `reqs/atomicio.py` exists; read it to learn its API.
3. Build the manifest (excluding `refactored/`, generated dirs, `reqs/`).
4. Inspect `.gitignore`; apply minimal negation if needed.
5. Initialize `.progress.json`.
6. Copy `reqs/atomicio.py` (and any `reqs/` siblings needed) to `refactored/reqs/`.
7. Run **Phase 1** in batches until the manifest is exhausted.
8. Run **Phase 2** automatically:
   detect duplicates, extract to `refactored/_shared/`, update all consumers, verify.
9. refactored files should be fully type-annotated and well documented. esp module docstring,
   it should be brief explanation of what script does.
10. Emit final summary.
Do not return control to the user between phases unless one of the permitted stop reasons applies.
save the helper scripts (bash, python, ...) thst you used during this session under arena folder in
root of connected repo commit the changes.
