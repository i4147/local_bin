# Documented Python copies

This directory contains documentation-enriched copies of the repository's 72 root-level Python scripts. The original scripts remain unchanged.

The pass added concise module/class/function docstrings where the declaration or identifier supported a clear summary, plus parameter/return annotations only when a type was evident from a literal default or unambiguous return expression. It deliberately did not guess unclear behavior or types.

## Reports

- `AUDIT.md` — pre-edit per-file declaration inventory and docstring/comment status.
- `BATCH_REPORT.md` — changes and validation results for the 15 batches (14 groups of five and a final group of two, because there are 72 scripts).
- `NEEDS_REVIEW.txt` — public symbols whose role was not clear enough for a safe summary.
- `ANNOTATIONS_REVIEW.md` — remaining untyped parameters and return values, with source line references.

## Regeneration and validation

From the repository root:

```sh
python3 arena/documentation_inventory.py
python3 arena/run_document_batches.py
python3 arena/audit_annotations.py
python3 arena/verify_documented.py
```

`verify_documented.py` parses all originals and copies and checks that their ASTs are identical after removing docstrings and annotations. This checks the documented copies for executable-structure changes; it does not replace runtime tests for every CLI.
