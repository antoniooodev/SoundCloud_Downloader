#!/usr/bin/env bash
set -euo pipefail

PYTHON="${PYTHON:-python3}"

"${PYTHON}" -m compileall src
"${PYTHON}" -m pytest
"${PYTHON}" -m ruff check .
"${PYTHON}" -m mypy src
