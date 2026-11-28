#!/data/data/com.termux/files/usr/bin/env python
"""
Generate single-file HTML documentation for the Python standard library
of the current interpreter.

Usage:
    python gen_stdlib_docs.py           # -> ./stdlib.html
    python gen_stdlib_docs.py -p        # -> ./stdlib.html + ./stdlib.pdf
    python gen_stdlib_docs.py -o doc.html -p

PDF backend (checked in order): weasyprint, xhtml2pdf, pdfkit(wkhtmltopdf).
Install any one, e.g. `pip install weasyprint`.
"""

from __future__ import annotations
import argparse
from collections.abc import Iterable, Sequence
from datetime import datetime
import importlib
import os
from pathlib import Path
import pydoc
import sys
import sysconfig
from typing import Any
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


BLACKLIST: set[str] = {
    "antigravity",
    "this",
    "__hello__",
    "__phello__",
    "idlelib",
    "turtle",
    "turtledemo",
    "tkinter",
    "test",
    "test.support",
    "unittest.test",
    "ensurepip",
    "venv",
    "lib2to3",
}


def get_module_names():
    """Return sorted, public stdlib top-level module names."""
    if hasattr(sys, "stdlib_module_names"):
        names = list(sys.stdlib_module_names)
    else:
        names = []
        stdlib = Path(sysconfig.get_paths()["stdlib"])
        for full in stdlib.iterdir():
            if full.suffix == ".py":
                names.append(full.stem)
            elif full.is_dir() and (full / "__init__.py").exists():
                names.append(full.name)
    return sorted({n for n in names if not n.startswith("_") and n not in BLACKLIST})


def render_module(name: str) -> str | None:
    """Import a module and return its pydoc HTML body, or None on failure."""
    try:
        module = importlib.import_module(name)
    except BaseException:
        return None
    try:
        body = pydoc.html.docmodule(module)
    except BaseException:
        return None
    return body or None


def build_html(sections: Sequence[tuple[str, str]]) -> str:
    """Build html."""
    version = sys.version.split()[0]
    generated = datetime.now().isoformat(timespec="seconds")
    out = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="en"><head><meta charset="utf-8">')
    out.append(f"<title>Python {version} Standard Library</title>")
    out.append(
        "<style>"
        "body{font-family:system-ui,sans-serif;max-width:1000px;"
        "margin:2em auto;padding:0 1em;line-height:1.4;}"
        "h1{border-bottom:2px solid #333;}"
        ".module{margin-top:2.5em;border-top:1px solid #ccc;padding-top:1em;}"
        "pre{background:#f4f4f4;padding:.6em;overflow-x:auto;}"
        ".toc{columns:3;column-gap:2em;}"
        ".toc ul{padding-left:1.2em;margin:.3em 0;}"
        ".toc a{text-decoration:none;color:#0b5fbf;}"
        "a{color:#0b5fbf;}"
        "dt{font-family:monospace;}"
        "</style>"
    )
    out.append("</head><body>")
    out.append(f"<h1>Python {version} Standard Library</h1>")
    out.append(f"<p>Generated {generated} on {sys.platform} &mdash; {len(sections)} modules.</p>")
    out.append('<h2 id="toc">Index</h2><div class="toc"><ul>')
    for name, _ in sections:
        out.append(f'<li><a href="#mod-{name}">{name}</a></li>')
    out.append("</ul></div>")
    for name, body in sections:
        out.append(f'<div class="module" id="mod-{name}">{body}</div>')
    out.append("</body></html>")
    return "\n".join(out)


def html_to_pdf(html_path: Path, pdf_path: Path) -> str | None:
    """Try several backends; return the backend name on success, else None."""
    try:
        from weasyprint import HTML

        HTML(filename=html_path).write_pdf(pdf_path)
        return "weasyprint"
    except ImportError:
        pass
    try:
        from xhtml2pdf import pisa

        with open(html_path, "rb") as src, open(pdf_path, "wb") as dst:
            result = pisa.CreatePDF(src, dest=dst)
        if result.err:
            return None
        return "xhtml2pdf"
    except ImportError:
        pass
    try:
        import pdfkit

        pdfkit.from_file(html_path, pdf_path)
        return "pdfkit"
    except (ImportError, OSError):
        pass
    return None


def main() -> None:
    """Run the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", "--pdf", action="store_true", help="Also generate a PDF version.")
    parser.add_argument(
        "-o",
        "--output",
        default="stdlib.html",
        help="Output HTML path (default: stdlib.html).",
    )
    args = parser.parse_args()
    version = sys.version.split()[0]
    names = get_module_names()
    print(f"Python {version}: {len(names)} stdlib modules to render.", file=sys.stderr)
    sections = []
    for i, name in enumerate(names, 1):
        if i % 25 == 0 or i == len(names):
            print(f"  [{i}/{len(names)}] {name}", file=sys.stderr)
        body = render_module(name)
        if body:
            sections.append((name, body))
    print(f"Rendered {len(sections)} modules. Writing HTML...", file=sys.stderr)
    html = build_html(sections)
    out_path = Path(args.output).resolve()
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
    if args.pdf:
        pdf_path = out_path.with_suffix(".pdf")
        print("Generating PDF...", file=sys.stderr)
        backend = html_to_pdf(out_path, pdf_path)
        if backend:
            print(f"Wrote {pdf_path} ({pdf_path.stat().st_size / 1024:.1f} KB) via {backend}")
        else:
            print(
                "PDF generation requires one of: weasyprint, xhtml2pdf, or "
                "pdfkit(+wkhtmltopdf).\n"
                "Install with e.g.:  pip install weasyprint",
                file=sys.stderr,
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
