#!/data/data/com.termux/files/usr/bin/env python
from __future__ import annotations

import importlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

import typer

RemoveFn = Callable[[Path, Path, int, int], None]
Backend = tuple[str, RemoveFn]


def _validate(total: int, start: int, end: int) -> None:
    if start < 0 or end < 0:
        raise ValueError("page indices must be >= 0")
    if start > end:
        raise ValueError("start must be <= end")
    if end >= total:
        raise ValueError(f"end page index {end} >= total pages {total}")
    if end - start + 1 >= total:
        raise ValueError("cannot remove all pages from document")


def _try_pypdf() -> Backend | None:
    mod = None
    name = ""
    for candidate in ("pypdf", "PyPDF2"):
        try:
            mod = importlib.import_module(candidate)
            name = candidate
            break
        except ImportError:
            continue
    if mod is None:
        return None

    PdfReader = mod.PdfReader
    PdfWriter = mod.PdfWriter

    def remove(src: Path, dst: Path, start: int, end: int) -> None:
        reader = PdfReader(str(src))
        total = len(reader.pages)
        _validate(total, start, end)
        writer = PdfWriter()
        for i in range(total):
            if start <= i <= end:
                continue
            writer.add_page(reader.pages[i])
        with open(dst, "wb") as f:
            writer.write(f)

    return name, remove


def _try_pikepdf() -> Backend | None:
    try:
        import pikepdf
    except ImportError:
        return None

    def remove(src: Path, dst: Path, start: int, end: int) -> None:
        with pikepdf.open(str(src)) as pdf:
            total = len(pdf.pages)
            _validate(total, start, end)
            del pdf.pages[start : end + 1]
            pdf.save(str(dst))

    return "pikepdf", remove


def _try_pymupdf() -> Backend | None:
    try:
        import fitz
    except ImportError:
        return None

    def remove(src: Path, dst: Path, start: int, end: int) -> None:
        doc = fitz.open(str(src))
        try:
            _validate(doc.page_count, start, end)
            doc.delete_pages(list(range(start, end + 1)))
            doc.save(str(dst))
        finally:
            doc.close()

    return "pymupdf", remove


def _pick_backend() -> Backend:
    for factory in (_try_pypdf, _try_pikepdf, _try_pymupdf):
        b = factory()
        if b is not None:
            return b
    raise RuntimeError("No PDF backend available; install one of: pypdf, PyPDF2, pikepdf, pymupdf")


app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@app.command()
def main(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, resolve_path=True),
    start: int = typer.Argument(..., min=1),
    end: int = typer.Argument(..., min=1),
) -> None:
    if start > end:
        raise typer.BadParameter("start must be <= end")

    name, remove = _pick_backend()

    fd, tmp_str = tempfile.mkstemp(suffix=".pdf", dir=str(pdf.parent))
    os.close(fd)
    tmp_path = Path(tmp_str)

    try:
        remove(pdf, tmp_path, start - 1, end - 1)
        shutil.move(str(tmp_path), str(pdf))
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    typer.echo(f"[{name}] removed pages {start}-{end} from {pdf}")


if __name__ == "__main__":
    app()
