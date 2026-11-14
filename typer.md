You are refactoring a Python repository to standardize CLI argument parsing on Typer.
This is a long, multi-batch job. You must complete the entire job in one run without
stopping for approval between batches. Do not ask me to confirm plans or per-file
changes — make the best decision, record it, and keep going.

## Goal
Refactor every Python file in the ROOT of this repository that currently parses CLI
arguments (using `argparse` or `click`) so it uses `typer` as the argument parser.
Files that do not parse CLI arguments are left untouched.

## Scope
- Only files directly in the repository root (not subdirectories, not tests, not
  setup.py / pyproject.toml).
- Do not rename, move, or delete files.
- Do not change non-CLI logic. CLI-layer refactor only.

## Hard requirements (behavior parity)
For every refactored file, identical before and after:
1. Accepted flags/options/positionals: names, short aliases, defaults, required-ness,
   choices/enums, types, help text.
2. `--help` output content (formatting may differ; every option + description must
   still be present and accurate).
3. Exit codes on success, invalid args, `--help`, `--version`.
4. Env-var handling (argparse env patterns, click `envvar=`).
5. Prompts/confirmations.
6. Still runnable as `python path/to/file.py ...`, via existing shebang, and via any
   existing console entry point.
7. `if __name__ == "__main__":` blocks still work.

## BATCH PROTOCOL — follow exactly

### Batching
- After the inventory, split root-level files-to-change into batches of **5 files**
  (or fewer for the last batch). If a natural grouping is obvious (e.g. files sharing
  a subcommand family), you may group by that instead, but never exceed 8 files per
  batch.
- Process batches **sequentially, without pausing between them**. When a batch
  finishes (edits + verification + ledger update), immediately start the next batch.
  Do not wait for my approval. Do not stop to summarize between batches beyond
  updating the ledger.

### Persistent ledger (write this to disk)
- Create `.typer_refactor_progress.md` in the repo root before editing anything.
- After **every file** you touch, update the ledger with one row:
  `file | source_lib (argparse/click/typer/none) | status (DONE / SKIPPED-NEEDS-REVIEW / UNCHANGED) | notes`
- Also record at the top: total files in scope, batches defined, current batch number,
  and a "batch completed" checklist.
- **Resumability:** if your context is running low, finish the current file, flush the
  ledger, and continue by re-reading the ledger in a fresh step. Treat the ledger as
  the source of truth for what's done. Never redo a file marked DONE unless you have
  a concrete reason (e.g. it fails verification).
- The ledger is a work artifact — leave it in place at the end as part of the deliverable.

### Per-batch loop
For each batch, without asking me anything:

1. **Baseline.** For each file in the batch, capture current `--help` output and
   current stdout/stderr/exit code for 2–3 representative invocations into
   `/tmp/cli-baseline/<file>/`.
2. **Refactor** each file in the batch, one file at a time, applying the mapping
   rules below. After each file, run `python <file> --help` and diff against
   baseline, run the representative invocations, and run the project linter/formatter
   (ruff/black if configured) on that file only.
3. **On failure:** fix and retry up to 3 times. If still failing, or if the file hits
   an edge case listed under "Blockers", revert that file to its original content
   (`git checkout -- <file>` if git is available, otherwise restore from your saved
   copy), mark it `SKIPPED-NEEDS-REVIEW` in the ledger with a one-line reason, and
   **continue with the next file**. Do not halt the batch.
4. **Batch close:** update the ledger with the batch's completion status, then
   immediately start the next batch.

### When you ARE allowed to stop and report
Stop the whole run and report immediately only if:
- The repo cannot be read, or git is in an unusable state.
- `typer` cannot be installed/imported and no fallback exists.
- More than 50% of the first batch comes back `SKIPPED-NEEDS-REVIEW` — that means the
  mapping assumptions are wrong and the rest of the run would be wasted.
- A file would require changing a public non-CLI function signature to make Typer
  work (scope violation) — mark and skip that file, but do not stop the run.

Everything else: record in the ledger and keep going.

## Mapping rules

### argparse → Typer
- `argparse.ArgumentParser(...)` + `add_argument(...)` → `@app.command()` function
  with typed params (or `typer.run(main)` for single-command scripts).
- Use `Annotated[T, typer.Option(...)]` / `Annotated[T, typer.Argument(...)]` when
  Python >= 3.9; otherwise default-value form `x: int = typer.Option(...)`.
- `--foo / -f` → `typer.Option(..., "--foo", "-f")`.
- Positionals → `typer.Argument(...)` in declaration order.
- `type=int/float/str/pathlib.Path` → annotate the parameter.
- `choices=[...]` → `Literal[...]` or `str, Enum` subclass.
- `nargs="+"` → `list[T]` (required); `nargs="*"` → `list[T]` (default `[]`).
- `action="store_true"` → `bool = typer.Option(False, ...)`.
- `action="store_false"` → `bool = typer.Option(True, ...)`.
- `action="count"` → `int = typer.Option(0, "--verbose", "-v", count=True)`.
- `action="append"` → `list[T] = typer.Option([], ...)`.
- `default=`, `required=`, `help=`, `metavar=` → corresponding Typer kwargs.
- `dest="x"` → rename the parameter to `x`.
- `parse_args()` at the end → `app()` or `typer.run(main)` in `__main__` guard.
- Subparsers → sub-`typer.Typer()` apps via `app.add_typer(...)`.
- `parser.error(...)` / `parser.exit(...)` → `typer.echo(..., err=True)` then
  `raise typer.Exit(code=...)`.
- `description` / `epilog` → `Typer(help=..., epilog=...)` or command docstring.

### Click → Typer
- `@click.command()` → `@app.command()` (or `typer.run(main)`).
- `@click.group()` → `app = typer.Typer()` + `@app.command()` per subcommand.
- `@click.option(...)` → move names/default/help/type into `typer.Option(...)` + a
  type annotation.
- `@click.argument(...)` → `typer.Argument(...)`.
- `click.echo/style/secho/confirm/prompt/progressbar` → the `typer.*` equivalents.
- `@click.pass_context` + `ctx` → add `ctx: typer.Context` as the first parameter.
- `@click.pass_obj` / `ctx.obj` → use `ctx: typer.Context` and `ctx.obj`; if it
  becomes invasive, mark `SKIPPED-NEEDS-REVIEW` and continue.
- `is_flag=True` → `bool = typer.Option(False, "--flag/--no-flag")`, preserving
  whether a `--no-` form existed.
- `multiple=True` → `list[T] = typer.Option([])`.
- `nargs=N` → `list[T]`; note any strict-N discrepancy in the ledger.
- `count=True` → `int = typer.Option(0, count=True)`.
- `click.Choice([...])` → `Literal[...]` or `Enum`.
- `click.Path(...)` → `pathlib.Path` with matching `typer.Option(...)` kwargs; if
  unsupported, fall back to `click_type=click.Path(...)` inside `typer.Option` and
  note it.

## Style requirements
- Prefer `Annotated` style on Python >= 3.9.
- Add `import typer` and `from typing import Annotated, Optional, Literal` only when
  actually used; remove now-unused `argparse` / `click` imports from refactored files.
- Keep module docstrings. If a command function has no docstring, add a one-line
  docstring equal to the old parser's description so `--help` text survives.
- Do not reformat unrelated code. Run the configured formatter on touched files only.

## Blockers (→ mark SKIPPED-NEEDS-REVIEW, continue)
Do not guess and do not halt for these — record and move on:
- `parse_known_args`, partial parsing, intermixed parsing.
- Custom `argparse.Action` or `click.ParamType` subclasses.
- `ctx.invoke`, `ctx.forward`, `ctx.call_on_close`, `result_callback`.
- Chained click groups (`chain=True`), `invoke_without_command=True`,
  `no_args_is_help` with no 1:1 mapping.
- Multiple independent parsers in one file that must remain separately invocable.
- A script imported as a module whose `parse_args` is called by tests with a
  specific `argv` and where Typer would change that call shape.
- Python < 3.9 constraints that block the `Annotated` form.
- Any change that would require touching a public non-CLI function signature.

## Do NOT
- Change public non-CLI signatures.
- Change runtime behavior of non-CLI code paths.
- Add dependencies beyond `typer` (and `click` only for the explicit fallback above).
- Touch files outside the repo root.
- Stop between batches to ask for permission, or to produce prose summaries.
  Summaries go in the ledger and in the final report only.

## Deliverables (at end of the full run)
1. `.typer_refactor_progress.md` — the completed ledger.
2. Final report:
   - Table: file → source lib → status (DONE / SKIPPED-NEEDS-REVIEW / UNCHANGED) →
     notes.
   - Any `--help` or behavior differences that remain, with explanation.
   - Which files were skipped and exactly why, so I can hand-review them.
   - The verification commands you ran (linter, tests, representative invocations).
   - Per-file new invocation example (the new "how to run").
3. Do not delete the ledger or the `/tmp/cli-baseline/` diffs until I confirm.

Begin now: produce the inventory, define the batches, write the ledger skeleton, then
start batch 1. Do not wait for approval.