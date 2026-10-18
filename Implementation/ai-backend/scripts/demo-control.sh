#!/bin/sh
# Exact-PID control of the demo API server / background intel worker (see scripts/demo_control.py).
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
exec "$ROOT/.venv-intel/bin/python" "$ROOT/scripts/demo_control.py" "$@"
