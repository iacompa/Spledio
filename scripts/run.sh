#!/usr/bin/env bash
# Launch Spledio from the project virtual environment. Optional argument: audio file or project.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m spledio "$@"
