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

import argparse
import importlib
import os
import sys
import sysconfig
from datetime import datetime

import pydoc


# Modules whose import has side effects or is likely to fail / hang in a
# headless/automated context. Skip them.
BLACKLIST = {
    "antigravity",  # opens a web browser
    "this",  # prints Zen of Python
    "__hello__",
    "__phello__",
    "idlelib",
    "turtle",
    "turtledemo",
    "tkinter",  # GUI / display
    "test",
    "test.support",
    "unittest.test",  # CPython test suite
    "ensurepip",
    "venv",  # spawns subprocesses, slow
    "lib2to3",  # deprecated
}


def get_module_names():
    """Return sorted, public stdlib top-level module names."""
    if hasattr(sys, "stdlib_module_names"):
        names = list(sys.stdlib_module_names)
    else:
        # Fallback for Python < 3.10
        names = []
        stdlib = sysconfig.get_paths()["stdlib"]
        for entry in os.listdir(stdlib):
            full = os.path.join(stdlib, entry)
            if entry.endswith(".py"):
                names.append(entry[:-3])
            elif os.path.isdir(full) and os.path.exists(os.path.join(full, "__init__.py")):
                names.append(entry)

    return sorted({n for n in names if not n.startswith("_") and n not in BLACKLIST})


def render_module(name):
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


def build_html(sections):
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


def html_to_pdf(html_path, pdf_path):
    """Try several backends; return the backend name on success, else None."""
    try:
        from weasyprint import HTML  # type: ignore

        HTML(filename=html_path).write_pdf(pdf_path)
        return "weasyprint"
    except ImportError:
        pass

    try:
        from xhtml2pdf import pisa  # type: ignore

        with open(html_path, "rb") as src, open(pdf_path, "wb") as dst:
            result = pisa.CreatePDF(src, dest=dst)
        if result.err:
            return None
        return "xhtml2pdf"
    except ImportError:
        pass

    try:
        import pdfkit  # type: ignore

        pdfkit.from_file(html_path, pdf_path)
        return "pdfkit"
    except (ImportError, OSError):
        pass

    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", "--pdf", action="store_true", help="Also generate a PDF version.")
    parser.add_argument("-o", "--output", default="stdlib.html", help="Output HTML path (default: stdlib.html).")
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

    out_path = os.path.abspath(args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out_path} ({os.path.getsize(out_path) / 1024:.1f} KB)")

    if args.pdf:
        pdf_path = os.path.splitext(out_path)[0] + ".pdf"
        print("Generating PDF...", file=sys.stderr)
        backend = html_to_pdf(out_path, pdf_path)
        if backend:
            print(f"Wrote {pdf_path} ({os.path.getsize(pdf_path) / 1024:.1f} KB) via {backend}")
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
