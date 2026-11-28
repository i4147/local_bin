#!/usr/bin/env bash
# Create an isolated virtualenv with Astral's `ty`, `dh-slim`, third-party
# dependencies imported by root scripts, and `types-*` stub packages.
set -euo pipefail

VENV_DIR="${1:-/tmp/ty-venv}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    python3 -m venv "${VENV_DIR}"
fi

PIP="${VENV_DIR}/bin/pip"
"${PIP}" install --quiet --upgrade pip

# Core checker + i4147/dh runtime package (includes py.typed)
"${PIP}" install --quiet ty dh-slim

# Third-party runtime dependencies imported across root scripts
PACKAGES=(
    Pillow
    argcomplete
    astor
    autoflake
    beautifulsoup4
    black
    brotli
    brotlicffi
    build
    cairosvg
    chardet
    charset-normalizer
    click
    cssbeautifier
    diskcache
    extruct
    google-api-python-client
    googlesearch-python
    guessit
    httpx
    idna
    isort
    jinja2
    joblib
    jsbeautifier
    jsonpath-ng
    just
    justext
    langdetect
    langid
    libcst
    lingua-language-detector
    loguru
    lxml
    lxml-html-clean
    lz4
    markdown-it-py
    markdownify
    metadate
    msgpack
    numpy
    packaging
    pathspec
    pdfkit
    pikepdf
    py3langid
    py7zr
    pygments
    pymupdf
    pypdf
    pyperclip
    pyppmd
    python-dotenv
    python-magic
    python-snappy
    pyxdameraulevenshtein
    pyyaml
    rapidfuzz
    rarfile
    rcssmin
    regex
    requests
    requests-viewer
    rich
    rjsmin
    send2trash
    tenacity
    tinycss2
    tldextract
    tomli
    tomlkit
    tqdm
    tree-sitter
    tree-sitter-c
    tree-sitter-cpp
    tree-sitter-python
    typer
    ujson
    validate-pyproject
    watchdog
    weasyprint
    xhtml2pdf
    xxhash
    zstandard
)

# Type stubs for packages without inline types
STUBS=(
    types-PyYAML
    types-Pygments
    types-beautifulsoup4
    types-lxml
    types-regex
    types-requests
    types-tqdm
    types-ujson
)

# Install in one batch if possible; fall back to skipping any wheel that
# cannot build in the current environment (e.g. missing C headers).
if ! "${PIP}" install --quiet --prefer-binary "${PACKAGES[@]}" "${STUBS[@]}"; then
    echo "Batch install hit a package error; installing individually..." >&2
    for pkg in "${PACKAGES[@]}" "${STUBS[@]}"; do
        "${PIP}" install --quiet --prefer-binary "${pkg}" || echo "  [skip] ${pkg}" >&2
    done
fi

echo "Ready: $("${VENV_DIR}/bin/ty" --version) in ${VENV_DIR}"
