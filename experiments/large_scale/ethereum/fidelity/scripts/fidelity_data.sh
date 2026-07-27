#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIDELITY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

CONDA_ENV_NAME="${CONDA_ENV_NAME:-fidelity}"

# The local HTTP proxy can be orders of magnitude slower for the public Xatu
# object store. Bypass it only for this host, leaving all other proxy behavior
# unchanged. Set XATU_BYPASS_PROXY=0 when direct access is unavailable.
if [[ "${XATU_BYPASS_PROXY:-1}" != "0" ]]; then
    for proxy_bypass_var in no_proxy NO_PROXY; do
        proxy_bypass_value="${!proxy_bypass_var:-}"
        if [[ ",${proxy_bypass_value}," != *",data.ethpandaops.io,"* ]]; then
            printf -v "${proxy_bypass_var}" '%s%s' \
                "${proxy_bypass_value}" \
                "${proxy_bypass_value:+,}data.ethpandaops.io"
        fi
        export "${proxy_bypass_var}"
    done
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
    PYTHON_BIN="${PYTHON_BIN}"
elif command -v conda >/dev/null 2>&1 \
    && conda run -n "${CONDA_ENV_NAME}" python --version >/dev/null 2>&1; then
    if ! conda run -n "${CONDA_ENV_NAME}" \
        python -c 'import duckdb, matplotlib, yaml' >/dev/null 2>&1; then
        echo "Conda environment '${CONDA_ENV_NAME}' is missing fidelity dependencies" >&2
        echo "Run ${FIDELITY_DIR}/scripts/setup_env.sh first" >&2
        exit 1
    fi

    export PYTHONPATH="${FIDELITY_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
    export MPLCONFIGDIR="${MPLCONFIGDIR:-${FIDELITY_DIR}/.matplotlib-cache}"
    mkdir -p "${MPLCONFIGDIR}"
    exec conda run --no-capture-output -n "${CONDA_ENV_NAME}" python -m fidelity.cli "$@"
elif [[ -x "${FIDELITY_DIR}/.venv/bin/python" ]]; then
    PYTHON_BIN="${FIDELITY_DIR}/.venv/bin/python"
elif python3 -c 'import duckdb, matplotlib, yaml' >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    echo "No Python environment with the fidelity dependencies was found" >&2
    echo "Run ${FIDELITY_DIR}/scripts/setup_env.sh first" >&2
    exit 1
fi

export PYTHONPATH="${FIDELITY_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-${FIDELITY_DIR}/.matplotlib-cache}"
mkdir -p "${MPLCONFIGDIR}"

exec "${PYTHON_BIN}" -m fidelity.cli "$@"
