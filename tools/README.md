# Python documentation and annotation helpers

These scripts support the repository's documented-copy workflow. They inspect Python files in the repository root and write generated copies/reports under `documented/`; they do **not** edit original scripts.

## Add documentation and safe, obvious annotations to a new script

After adding a root-level script, run:

```sh
python3 tools/document_sources.py your_script.py
```

This creates `documented/your_script.py`. It adds summaries for declarations whose names have a clear conventional meaning, and annotations only where a literal default or simple, consistent return expression makes the type apparent. Review the generated copy before using it; uncertain symbols/types are reported in `documented/NEEDS_REVIEW.txt` and `documented/ANNOTATIONS_REVIEW.md`. It intentionally does not guess types from names or use `Any` to make an audit appear complete.

`document_sources.py` and the batch runner require `libcst` in the active Python environment. The audit and verification helpers otherwise use only the Python standard library. Annotation inference is intentionally conservative; review types that remain listed rather than filling them with guesses.

## Audit or regenerate all documented copies

```sh
python3 tools/documentation_inventory.py
python3 tools/run_document_batches.py
python3 tools/audit_annotations.py
python3 tools/verify_documented.py
```

- `documentation_inventory.py` creates the pre-edit symbol inventory at `documented/AUDIT.md`.
- `run_document_batches.py` processes all root-level `.py` files in batches of five and writes `documented/BATCH_REPORT.md` plus the unclear-symbol list.
- `audit_annotations.py` lists parameters and return types that remain unannotated in documented copies.
- `verify_documented.py` parses the originals and copies, then checks their ASTs match after docstrings and annotations are removed.

To check the original root scripts for Python 3.11 syntax/minimum-version compatibility, run:

```sh
python3 tools/verify_py311.py
```

It optionally invokes `vermin`; pydup.py's guarded `InterpreterPoolExecutor` report is expected. No helper here edits root source files.
