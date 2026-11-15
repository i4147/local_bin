# Task: Extend `pyrg` with the full ripgrep-class feature set

## Context

`pyrg` is a single-file Python CLI at `pyrg.py` that performs ripgrep-like
recursive regex/fixed-string search over files. It is already migrated to:

- **Typer** for the CLI (single command, no subcommands; entry via
  `typer.run(_pyrg_command)` and a `_run(argv) -> int` helper that wraps
  `typer.testing.CliRunner`)
- **pathlib** for all path handling (`os.scandir` is allowed inside `_walk_dir`
  for speed; `os.environ` allowed for `NO_COLOR`; `os.fdopen` for atomic writes)
- **loguru** for diagnostics (`_setup_logging()` configures a file logger with
  stderr fallback)
- **Full PEP 484 / PEP 604 type annotations** everywhere
- **Highlight colours**: file paths in `\x1b[30;43m` (black on yellow),
  matches in `\x1b[30;46m` (black on cyan), line numbers in green `\x1b[32m`

Existing modules/imports that MUST be preserved: `dh` (`MAX_WORKERS`,
`SKIP_DIRS`), `isbinary` (`is_binary`).

Read the current file, keep all public behaviour that is not explicitly
changed below, and produce the updated full source.

---

## Phase 1 — Fix known bugs first (mandatory, do these before adding features)

1. **Regex recompiled per file.** `worker()` calls
   `re.compile(regex_pattern, flags)` on every file. Move compilation out of
   `worker` into the dispatcher; pass the compiled `re.Pattern` in the job
   dict. Under the default `fork` start method this is picklable; if
   `spawn`/`forkserver` is possible, add a module-level
   `@functools.lru_cache(maxsize=64) def _compile(pat, flags)` helper as fallback.
2. **`is_binary()` may read the whole file.** Short-circuit: `get_files` already
   stats `st_size`; pass that down so `worker` skips `is_binary` for files
   already known to be non-text, and `is_binary` itself should only read the
   first 8192 bytes (`path.read_bytes()[:8192]` via `path.open("rb")`).
3. **UTF-8 BOM not stripped.** Files starting with `\xef\xbb\xbf` break `^`
   anchors. Detect the BOM; strip it before matching; re-emit it on write
   (only relevant once `--in-place` exists). Use `utf-8-sig` on read when
   detected.
4. **`--multiline` + `-v` silently falls back to line mode.** Emit
   `logger.warning("--multiline ignored with --invert-match")` and continue in
   line mode.
5. **`-n / --line-number` no-op.** In argparse both `-n` and
   `--no-line-number` targeted the same bool. In the Typer version only
   `--no-line-number` has effect (default `True`). Keep both flags but make
   passing `-n` and `--no-line-number` together an error (`exit 2`).
6. **`-l / --files-with-matches` + `--files-without-match`** are not
   mutually exclusive — add check, `exit 2`.
7. **Mutable list defaults** — change every `typer.Option([], …)` for
   `list[str]` to `typer.Option(None, …)` with `Optional[list[str]]` type
   annotation, and normalise to `list(x or [])` inside the command body.
   Applies to `extensions`, `types`, `glob_patterns`, `exclude`,
   `exclude_dirs`, and any new list-typed flag added below.
8. **`_run()` recreates a `Typer()` app per call.** Hoist to module level:
   ```python
   _app = typer.Typer(add_completion=False, no_args_is_help=False,
                      pretty_exceptions_enable=False)
   _app.command()(_pyrg_command)
   def _run(argv=None) -> int:
       return CliRunner().invoke(_app, list(argv) if argv is not None else sys.argv[1:]).exit_code
   ```
9. **Log file grows unbounded.** Add `rotation="10 MB", retention=5` to the
   `logger.add(_LOG_PATH, …)` call.
10. **`--out-file` opened before pattern validation** — reorder so
    pattern validation and file discovery happen **before** `_open_output`.
    If validation fails, exit `2` without creating a file.
11. **`except Exception: pass` around `is_binary`** in `worker` — change to
    `except OSError: pass`.
12. **`--max-count` semantics** — clarify in help text: `-m/--max-count`
    limits the number of **matching lines** per file (not total matches).
    Document in help and README; do not change behaviour.
13. **`_search_quiet` cancellation** — already cancels futures; add a
    `threading.Event` set on first match and check it between files inside
    the worker loop when reading line-by-line (relevant only after
    streaming mode is added).

---

## Phase 2 — Resolve flag conflicts (breaking change, do this before adding features)

The current short flags disagree with `rg`/`grep` conventions. Change:

| Old | New | Rationale |
|---|---|---|
| `-e / --extension` | `--ext` (long only); keep `-e` as `--regexp` alias for `-r` | `-e` = pattern in grep/rg |
| `-x / --exclude` | `-g '!pattern'` for excludes (rg idiom); `-x / --line-regexp` | `-x` = `--line-regexp` in grep/rg |
| `-L / --follow` | `--follow` (long only); `-L` = `--files-without-match` | rg/grep convention |
| `-M / --max-count` | keep `-M`, add `-m` alias | rg uses `-m` |
| `-t / --type` | keep (unchanged) | matches rg |
| `-i / --ignore-case` | keep | matches rg |
| `-F / --fixed-strings` | keep | matches rg |
| `-v / --invert-match` | keep | matches rg |
| `-o / --only-matching` | keep | matches rg |
| `-c / --count` | keep | matches rg |
| `-l / --files-with-matches` | keep | matches rg |
| `-n / --line-number` | keep, default on; `--no-line-number` to disable | matches rg |

**Update all help strings, epilog examples, and the `--help` output** to
reflect the new meanings. Add a `--legacy-flags` boolean (default `False`)
that, when passed, restores the old short meanings (`-e`=ext, `-x`=exclude,
`-L`=follow). When `--legacy-flags` is active, `-e/-x/-L` revert to old
behaviour and the new flags become long-only. Internally this means two
`typer.Option` declarations with the same short flag are not allowed — use
a single option per letter and branch on `--legacy-flags` in the command body.

---

## Phase 3 — Matching & pattern flags

Add all of the following. Where a short flag is not available, use the long
form only. Do **not** conflict with the reassigned flags above.

### `-w / --word-regexp`
Wrap the pattern in `\b…\b` before compiling. Works with both `--regexp` and
`--fixed-strings` (escape before wrapping in the latter case).

### `-U / --multiline`
Short alias for the existing `--multiline`. Semantics unchanged.

### `--no-unicode`
Compile with `re.ASCII`. Ignored in `--fixed-strings` mode (case-folding
respects ASCII in that path too).

### `--crlf`
Strip `\r` at end of line before matching (so `$` matches before `\r\n`), and
re-append `\r` on output when the original line ended with `\r\n`. Track
per-line endings during `_process_lines`.

### `-P / --pcre2`
Optional; only enabled if `import regex` succeeds. When `--pcre2` is passed
and the `regex` module is missing, print a clear error and exit `3`.
Uses `regex.compile(...)` instead of `re.compile(...)` for matching.
Falls back to `re` for everything if not requested.

### `--engine re|regex`
Explicit engine selection. Default `re`. `--engine regex` requires the
`regex` module. `--pcre2` is an alias for `--engine regex`.

### `-S / --smart-case`
Case-insensitive unless the pattern contains at least one uppercase letter.
Mutually exclusive with `-i / --ignore-case` and `--case-sensitive` (add
`-s / --case-sensitive` as an explicit disable of case-insensitivity).

### `--literal-replace`
When `--replace` is used, do **not** expand backreferences (`\1`, `\g<n>`,
`${name}`). Only matters when `--replace` is set.

### `--replace-from PATH`
Read the replacement template from `PATH` instead of the `--replace` value.
Mutually exclusive with `--replace`. `-` reads from stdin.

### `--context-separator SEP`
Custom separator printed between context groups. Default `--`.
Mutually exclusive with `--json` (JSON always uses `null` separators).

### `--field-match-separator SEP` / `--field-context-separator SEP`
Customize the character between `path`, `line`, and `text` for matching and
context lines respectively. Defaults `:` and `-`.

### `-f PATH / --pattern-file PATH`
Read patterns from `PATH` (one per line, blank lines and `#`-comments
ignored). Each pattern is OR-combined (matches if any pattern matches).
Mutually exclusive with `-r / --regexp` and with `--fixed-strings` combined
with `--replace` (backreferences ambiguous). `-` reads from stdin.

---

## Phase 4 — File selection & walking

### `--files-from PATH`
Newline-separated file list. `-` reads from stdin. When set, ignore the
`paths` positional. Each listed file is still filtered by extension/glob/
max-filesize/binary rules.

### `--ignore-file PATH` (repeatable)
Additional `.ignore`-style files to honour, in addition to per-directory
`.gitignore` / `.ignore`. Same rules engine as `IgnoreMatcher`.

### `--iglob GLOB` (repeatable)
Case-insensitive include glob. Combined with `-g`.

### `--glob-case-insensitive`
Make `-g`, `--iglob`, `--exclude` (via `-g '!…'`), and `--exclude-dir` all
case-insensitive.

### `--type-add NAME:EXT[,EXT...]` (repeatable)
Register a custom preset at runtime. E.g. `--type-add web:html,css,js`.

### `--type-clear NAME` (repeatable)
Remove a preset (built-in or user-added).

### `-T NAME / --type-not NAME` (repeatable)
Exclude files of the given type preset (inverse of `-t`).

### `--type-list`
Print the known presets and their extensions to stdout, exit `0`. Format:
```
py: .py .pyi .pyx .pyw
md: .md .markdown .mdx
```
Early-exit mode like `--files`.

### `-u / --unrestricted` (countable)
- `-u` (once): ignore `.gitignore`/`.ignore` (implies `--no-ignore`).
- `-uu`: also search hidden files/dirs (implies `--hidden`).
- `-uuu`: also search binary files (implies `-a / --text`).

### `-a / --text`
Treat binary files as text.

### `--binary`
Search binary files and escape NULs (`\x00`) in output.

### `-z / --search-zip`
Search inside `.gz`, `.bz2`, `.xz`, `.zip` (stdlib `gzip`, `bz2`, `lzma`,
`zipfile`). For zip files, treat each member as a separate result with the
path `<archive>/<member>`. Requires `-a / --text` unless the member's bytes
are already decodable as text.

### `--require-git`
Only search files tracked by git. Implement via
`subprocess.run(["git", "ls-files", "-z"], …)` and set membership. If
`git` is not installed or the directory is not a repo, exit `3` with a clear
error.

### `--sort path|modified|accessed|created|none` (+ `--sortr` for reverse)
Extend `--sort` choices. `none` keeps current fast-walk order. Requires
per-file `os.stat` for date-based modes; warn if `--max-depth` is combined
with a date-based sort (it still works, just slower).

### `--pre CMD` / `--pre-glob GLOB`
Pipe each matching file through `CMD` and search its stdout. `CMD` receives
the path as `$1` (i.e. `CMD` is invoked as `sh -c 'CMD "$1"' _ path`).
`--pre-glob` restricts which files go through the preprocessor; others are
searched normally.

---

## Phase 5 — Output & reporting

### `-H / --with-filename` / `--no-filename`
Force / suppress the filename in single-file output. Default: show for
multiple files, suppress for one file (current behaviour already does this
via heading logic — make it explicit).

### `--column`
Print the 1-based character column of the first match on the line
(`line:col:text`).

### `-b / --byte-offset`
Print the byte offset of the match (requires reading the raw bytes; do not
decode before computing offsets). Format `path:line:offset:text` or combine
with `--column`.

### `-0 / --null`
Use NUL (`\x00`) instead of `\n` after each path in `-l`, `-L`, `-c`,
`--files`, `--files-with-matches`, `--files-without-match`.

### `--passthru`
Print non-matching lines unchanged alongside matches. Mutually exclusive
with `-l`, `-L`, `-c`, `-q`, `--json`.

### `-p / --pretty`
Shorthand for `--color always --heading --line-number`.

### `--max-columns N`
Truncate printed lines to `N` characters. Applies to matched context lines
and non-matched context lines. Match spans that straddle the truncation
point are still shown (with the tail cut off).

### `--max-columns-preview`
When `--max-columns` truncates a line, show the first `N` chars followed
by `…` and a `(…N more chars…)` suffix on the same line.

### `--stats-only`
Print only the `--stats` summary (implies `-q`).

### `--json` (extend existing)
Emit one JSON object per **match** (not per line). Shape:
```json
{
  "path": "rel/path",
  "line": 42,
  "column": 7,
  "byte_offset": 1234,
  "match": "matched text",
  "replacement": "replaced text or null",
  "line_text": "full line content",
  "context_before": ["..."],
  "context_after": ["..."],
  "is_context": false
}
```

### `--json-seq`
One JSON **array** per match, useful for `jq`-friendly streaming. Same
fields as `--json`.

### `--version`
Print `pyrg <version>` where `<version>` is a module-level `__version__`
constant (start at `"0.2.0"`), exit `0`.

### `--generate bash|zsh|fish|powershell`
Emit shell completions to stdout and exit `0`. Use Typer's built-in
`typer.main.get_command(_app)` + `click.shell_completion.add_completion_class`
or `typer.rich_utils` helpers. Document `eval "$(pyrg --generate bash)"` in
the README.

### `--context-separator SEP` (see Phase 3)

### `--check`
Exit `0` if any match, `1` if none, `2` on error. Suppresses stdout unless
`--stats` or `--json` is also set.

### `--exit-code`
Exit `1` if no matches (mirror `rg --exit-code`). Overrides the default
exit-0-on-success.

### Document exit codes in `--help`
`0` match, `1` no match (with `--check` / `--exit-code`), `2` usage,
`3` IO error / missing dependency, `130` SIGINT.

---

## Phase 6 — Performance

### `-j N / --threads N`
Expose the worker count. Default `MAX_WORKERS` from `dh`. `--no-parallel`
alias for `-j 1`.

### `--no-parallel`
Force single-threaded execution.

### `--mmap` / `--no-mmap`
Use `mmap.mmap` for reading files above a size threshold (default 1 MiB).
Skip when `--in-place` is active. Default: `--mmap` on.

### `--buffer-size BYTES`
Buffered reader size for line-by-line mode. Default 65_536.

### `--process-lines`
Stream lines instead of reading the full file. Required for files larger
than `--max-filesize` when explicitly enabled; also used automatically when
`--multiline` is absent and `--process-lines` is passed. Non-matching
files are detected without reading the whole body. When active, the worker
reads via `path.open(...)` line iterator instead of `fh.read()`.

### `--skip-comments` / `--skip-strings`
Optional; only valid with `-t` in `py, js, ts, c, cpp, java` (skip inside
`# ...`, `// ...`, `/* ... */`, and string literals). Deferred — mark as a
follow-up and do **not** implement in this phase unless time permits.

### Early-exit
For `-l`, `-L`, `-c` (count saturates at `max_count`), and `-q`,
stop scanning each file after `max_count` matching lines.

### `--regex-size-limit BYTES` / `--dfa-size-limit BYTES`
Guard against pathological patterns. Only enforced when `--engine regex`
(the stdlib `re` doesn't expose these limits; log a warning if `--engine re`
is active and either limit is non-default).

---

## Phase 7 — In-place replacement (the headline feature)

Add a full in-place edit mode that turns `pyrg` into an `sd`/`ruplacer`-class
replacer. This reuses all existing `--replace`, `--only-matching`,
`--context`, and `--multiline` machinery.

### `-W / --in-place` (long form required since `-i` is `--ignore-case`)
Actually modify files. Requires `--replace` (or `--replace-from`). Mutually
exclusive with `-l`, `-L`, `-c`, `-q`, `--json`, `--json-seq`, `--passthru`,
`--files`, `--files-from`.

### `--dry-run`
Preview changes without writing. Implies `--diff` if `--diff` not set.

### `--diff`
Print a unified diff of proposed changes. Implies `--dry-run`.

### `--backup [SUFFIX]`
Before writing, save `<path><SUFFIX>` (default `.bak`). If `SUFFIX` is empty
(`--backup ''`), only enable undo-journal mode.

### `--backup-dir DIR`
Mirror the backup tree into `DIR` (implies `--backup .bak` if no suffix
given).

### `--undo-journal PATH`
JSON journal + adjacent blob directory `<PATH>.d/`. Each write appends
`{path, blob, old_hash, new_hash, ts}`. Verify `old_hash` before replacing
to avoid clobbering concurrent edits.

### `--undo`
Replay `--undo-journal` in reverse. Verify each file's `new_hash` matches
before restoring. Prints `Restored N file(s)`.

### `-p / --prompt` (interactive)
For each match, show a mini-diff (`path:line` + ± context) and ask
`[y]es / [n]o / [a]ll / [q]uit`. Mutually exclusive with `-j > 1`.

### `--confirm`
One global `[y/N]` prompt after printing the change summary (before writing).

### `--require-clean-git`
Refuse to run if `git status --porcelain` is non-empty. Exit `3`.

### `--atomic-write`
Default on when `--in-place`. Write `.tmp` in the same directory, then
`os.replace`. `--no-atomic-write` disables (useful for FUSE filesystems
where atomic rename is expensive).

### `--preserve-case` / `--upper` / `--lower` / `--capitalize`
Replacement case transforms, mirroring the `replacer` script:
- `--preserve-case`: `Foo→Bar`, `foo→bar`, `FOO→BAR` derived from `m.group(0)`
- `--upper`, `--lower`, `--capitalize`: apply to the whole replacement

### `--encoding` (already exists)
Used on both read and write; re-encode with the same encoding used to read
the file. Preserve a UTF-8 BOM if the original had one.

### Atomic-write helper
```python
def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".pyrg_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            shutil.copystat(path, tmp)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

---

## Phase 8 — Configuration, env vars, polish

### `--config PATH`
Load defaults from TOML or JSON. If not passed, auto-discover
`.pyrgrc` (TOML preferred, then JSON) by walking up from CWD.

### `--no-config`
Disable auto-discovery.

### Env-var defaults
`PYRG_JOBS`, `PYRG_COLOR`, `PYRG_CONFIG`, `PYRG_LOG_FILE`. Env vars take
precedence over the config file, CLI flags take precedence over env vars.

### `--debug` / `--trace`
Set loguru level to `DEBUG` / `TRACE` for the current run only. Do not
persist to the log file at those levels unless `--log-file` is set.

### `--log-file PATH`
Override the default `~/tmp/apps/pyrg.log`. Empty string disables file
logging entirely.

### Log rotation (see Phase 1, bug 9)

### `--no-messages` (exists) — extend
Also suppress the "no files matched the search filters" message.

### `--stats` (exists) — extend
Include a per-directory breakdown when `--stats` is passed twice (`-vv`-
style). Optional; gate on `stats >= 2`.

---

## Non-goals

- Do **not** add subcommands (`pyrg search`, `pyrg replace`). Keep a single
  command with `--in-place` as the only mode switch.
- Do **not** add a TUI or pager.
- Do **not** change the atomic-write semantics for `--dry-run` (never write).
- Do **not** add ripgrep's `-t all` / `-t none` magic.
- Do **not** implement `--skip-comments` / `--skip-strings` unless time
  permits — flag it as a follow-up.

---

## Acceptance criteria

After implementation, the following must pass (add these as tests in
`tests/test_pyrg.py` using `typer.testing.CliRunner` against `_run`):

1. `pyrg '</meta>' mmdc.html` matches the literal string `</meta>` in
   `mmdc.html` only (positional pattern, positional path).
2. `pyrg -e py 'TODO'` fails with a clear message pointing to `--ext`
   (after the flag rename) — or, if `--legacy-flags` is passed, succeeds
   with `-e` meaning extension.
3. `pyrg -i -S 'foo'` errors (mutually exclusive).
4. `pyrg -l foo --files-without-match` errors (mutually exclusive).
5. `pyrg -w 'foo'` does not match `foobar`.
6. `pyrg -m 2 foo file.txt` stops after 2 matching lines.
7. `pyrg -U 'a\nb' file.txt` matches across lines; `pyrg -U -v 'a\nb'`
   prints a warning and falls back to line mode.
8. `pyrg --files-from list.txt 'x'` searches only listed files.
9. `pyrg --type-add web:html,css 'x' -t web` searches only `.html`/`.css`.
10. `pyrg --type-list` prints presets and exits `0` without searching.
11. `pyrg -uu 'x'` searches hidden + ignored files.
12. `pyrg -u 'x'` searches ignored files but not hidden.
13. `pyrg -z 'x' archive.zip` searches inside the archive.
14. `pyrg --require-git 'x'` in a git repo lists only tracked files;
    outside a repo exits `3`.
15. `pyrg 'x' --column` prints `path:line:col:text`.
16. `pyrg 'x' -b` prints byte offsets.
17. `pyrg -0 -l 'x' | xargs -0` works.
18. `pyrg --json 'x' | jq .path` yields one JSON object per match with the
    documented fields.
19. `pyrg -W 'old' 'new' file.txt` rewrites `file.txt` (with `--backup .bak`,
    the original is preserved at `file.txt.bak`).
20. `pyrg -W 'old' 'new' file.txt --diff` prints a unified diff and does
    not modify the file.
21. `pyrg -W 'old' 'new' file.txt --undo-journal j.json` then
    `pyrg --undo --undo-journal j.json` restores the original.
22. `pyrg -W 'old' 'new' file.txt --preserve-case` renames `Old`→`New`,
    `OLD`→`NEW`, `old`→`new`.
23. `pyrg -W 'old' 'new' file.txt --dry-run` does not write.
24. `pyrg -p 'x'` is equivalent to `--color always --heading --line-number`.
25. `pyrg --generate bash` prints valid completions that `eval`-able.
26. `pyrg --version` prints `pyrg <version>` and exits `0`.
27. Filenames are printed in black-on-yellow (`\x1b[30;43m`), matches in
    black-on-cyan (`\x1b[30;46m`).
28. `pyrg -j 1 --no-parallel 'x'` and `pyrg -j 8 'x'` produce the same
    ordered output (with `--sort path`).
29. `pyrg 'x' --process-lines huge.log` streams without loading the whole
    file (assert peak RSS via `resource.getrusage` in the test).
30. `pyrg 'x' --mmap huge.log` and `--no-mmap` produce identical output.

---

## Deliverable

Return **one fenced markdown block** containing the complete updated
`pyrg.py`, with:

- `from __future__ import annotations` at the top
- Full PEP 484 / PEP 604 type annotations on every function, method, and
  module-level variable where meaningful
- Module docstring, per-function docstrings, and per-dataclass docstrings
  documenting parameters and return values
- All existing public behaviour preserved (Typer CLI, `_run(argv) -> int`,
  `--files` mode, `--quiet` fast path, exit codes, colour scheme)
- All new flags implemented per the specs above
- Bugs from Phase 1 fixed
- Flag conflicts from Phase 2 resolved (with `--legacy-flags` opt-in)
- No new external dependencies beyond what is already imported (only
  optional imports for `regex`, `tomllib`/`tomli`, `charset_normalizer` —
  degrade gracefully when absent)
- One `# ---` section separator between logical groups, no other inline
  comments (the user prefers no comments, only docstrings)

Do not truncate. Do not summarise. The output must be the complete,
runnable file.

