#!/bin/sh
# Existing local entry point; detached process, exact recorded ownership and HTTP health.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/local_backend.py" "${1:-status}"
