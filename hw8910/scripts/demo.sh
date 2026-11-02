#!/bin/sh
# Commands for a live 5–8 minute recording. Read docs/video.md before starting.
set -eu
cd "$(dirname "$0")/.."
python_cmd="${PYTHON:-.venv/bin/python}"
"$python_cmd" scripts/verify_evidence.py
make check PYTHON="$python_cmd"
"$python_cmd" scripts/check_flow.py
"$python_cmd" scripts/measure_api.py --workspace .real --smoke-only
