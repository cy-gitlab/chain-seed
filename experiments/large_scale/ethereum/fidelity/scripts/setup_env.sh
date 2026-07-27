#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIDELITY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-fidelity}"

if command -v conda >/dev/null 2>&1; then
    if ! conda run -n "${CONDA_ENV_NAME}" python --version >/dev/null 2>&1; then
        echo "Conda environment '${CONDA_ENV_NAME}' does not exist" >&2
        echo "Create it first or set CONDA_ENV_NAME to an existing environment" >&2
        exit 1
    fi

    conda run --no-capture-output -n "${CONDA_ENV_NAME}" \
        python -m pip install -r "${FIDELITY_DIR}/requirements.txt"
elif "${PYTHON_BIN}" -m venv "${FIDELITY_DIR}/.venv" 2>/dev/null; then
    "${FIDELITY_DIR}/.venv/bin/python" -m pip install --upgrade pip
    "${FIDELITY_DIR}/.venv/bin/python" -m pip install -r "${FIDELITY_DIR}/requirements.txt"
else
    echo "Conda was not found and Python venv creation failed" >&2
    exit 1
fi
