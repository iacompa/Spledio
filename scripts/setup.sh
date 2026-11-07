#!/usr/bin/env bash
# Create a virtual environment with the verified, pinned dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required (verified on 3.14.7)"'
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e . --no-deps
echo "Setup complete. Start the app with: ./scripts/run.sh"
