#!/usr/bin/env bash
# Run `ty check` and `py_compile` on specified Python files or all root `*.py`.
#
# Usage:
#   tools/run_ty_check.sh [--out REPORT.txt] [--py-version 3.14] [FILE ...]
set -euo pipefail

VENV_DIR="${TY_VENV:-/tmp/ty-venv}"
PY_VERSION="3.14"
OUT_FILE=""
FILES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --venv)
            VENV_DIR="$2"
            shift 2
            ;;
        --py-version)
            PY_VERSION="$2"
            shift 2
            ;;
        --out)
            OUT_FILE="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '2,6p' "$0"
            exit 0
            ;;
        *)
            FILES+=("$1")
            shift
            ;;
    esac
done

if [[ ! -x "${VENV_DIR}/bin/ty" ]]; then
    echo "ty not found in ${VENV_DIR}; run tools/setup_ty_venv.sh first." >&2
    exit 2
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    shopt -s nullglob
    FILES=( *.py )
    shopt -u nullglob
fi

python3 -m py_compile "${FILES[@]}"

TY_CMD=(
    "${VENV_DIR}/bin/ty" check
    --python "${VENV_DIR}/bin/python"
    --python-version "${PY_VERSION}"
    --output-format concise
    "${FILES[@]}"
)

if [[ -n "${OUT_FILE}" ]]; then
    set +e
    "${TY_CMD[@]}" > "${OUT_FILE}" 2>&1
    rc=$?
    set -e
    tail -n 1 "${OUT_FILE}"
    exit "${rc}"
else
    exec "${TY_CMD[@]}"
fi
