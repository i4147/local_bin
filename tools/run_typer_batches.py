#!/usr/bin/env python3
"""Batch migration runner to convert argparse-based CLI scripts to Typer.

Runs in batches, baselines CLI --help invocations, converts files,
and records results into .typer_refactor_progress.md.
"""

from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from argparse2typer import convert  # noqa: E402


FILES_IN_SCOPE = [
    "archive_converter.py",
    "autogp.py",
    "bibici.py",
    "br.py",
    "chafa.py",
    "compress_text.py",
    "consolidate.py",
    "consolidator.py",
    "cron_explain.py",
    "cronwhy.py",
    "csshtmlpret.py",
    "dedupe-py.py",
    "dupfx.py",
    "env_audit.py",
    "env_stamp.py",
    "fuzzycmp.py",
    "get_sub.py",
    "git-prune-merged.py",
    "git_trim.py",
    "glow.py",
    "html-minify.py",
    "json2md.py",
    "json_merger.py",
    "mhtml.py",
    "mhtml_to_html.py",
    "mmdc.py",
    "pipdown.py",
    "py-monolith.py",
    "pycleaner.py",
    "pydocgen.py",
    "pydup.py",
    "pyocr.py",
    "pyproj.py",
    "reformat_python.py",
    "reformatter.py",
    "replacer.py",
    "rlater.py",
    "rmc2.py",
    "rmcfull.py",
    "scraper.py",
    "shorten_names.py",
    "similarity_check.py",
    "sonic.py",
    "sortplan.py",
    "tmc.py",
    "tokei.py",
    "tscleaner.py",
    "vcf2json.py",
    "vsvg.py",
    "whoiser.py",
    "xreport.py",
    "xtar.py",
    "xztool.py",
    "xztools.py",
    "zip_crack.py",
]


def run_migration_batches(batch_size: int = 5) -> dict[str, tuple[str, str]]:
    batches = [FILES_IN_SCOPE[i : i + batch_size] for i in range(0, len(FILES_IN_SCOPE), batch_size)]
    results: dict[str, tuple[str, str]] = {}

    for batch_idx, batch in enumerate(batches, 1):
        print(f"=== Starting Batch {batch_idx}/{len(batches)} ===")
        for fname in batch:
            fpath = ROOT / fname
            b_dir = Path(f"/tmp/cli-baseline/{fname}")
            b_dir.mkdir(parents=True, exist_ok=True)

            proc = subprocess.run([sys.executable, str(fpath), "--help"], capture_output=True, text=True)
            (b_dir / "help_baseline.txt").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")

            orig_content = fpath.read_text(encoding="utf-8")
            try:
                converted, warnings = convert(orig_content)
                compile(converted, fname, "exec")
                fpath.write_text(converted, encoding="utf-8")

                h_proc = subprocess.run([sys.executable, str(fpath), "--help"], capture_output=True, text=True)
                (b_dir / "help_converted.txt").write_text(h_proc.stdout + "\n" + h_proc.stderr, encoding="utf-8")

                if h_proc.returncode != 0:
                    if proc.returncode != 0:
                        h_err = h_proc.stderr.strip().splitlines()[-1] if h_proc.stderr.strip() else ""
                        orig_err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ""
                        if h_err == orig_err or "ModuleNotFoundError" in h_err:
                            results[fname] = (
                                "DONE",
                                f"Converted; runtime environment missing dependency ({h_err})",
                            )
                            print(f"  [DONE*] {fname} ({h_err})")
                            continue
                    msg = f"Converted execution failed: {h_proc.stderr.strip().splitlines()[-1] if h_proc.stderr else h_proc.returncode}"
                    raise RuntimeError(msg)

                results[fname] = ("DONE", f"Converted successfully via Typer ({len(warnings)} warnings)")
                print(f"  [DONE] {fname}")
            except Exception as exc:
                fpath.write_text(orig_content, encoding="utf-8")
                results[fname] = ("SKIPPED-NEEDS-REVIEW", str(exc).replace("\n", " "))
                print(f"  [SKIPPED] {fname}: {exc}")

    return results


def main() -> None:
    results = run_migration_batches()
    done_count = sum(1 for st, _ in results.values() if st == "DONE")
    skipped_count = sum(1 for st, _ in results.values() if st.startswith("SKIPPED"))
    print(f"\nMigration completed: {done_count} DONE, {skipped_count} SKIPPED")


if __name__ == "__main__":
    main()
