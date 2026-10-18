#!/bin/sh
# Create ONE fresh environment (.venv-intel) and verify it with the same interpreter that later runs tests/service.
#   scripts/setup-intel-env.sh            base only (all platforms)
#   scripts/setup-intel-env.sh --mac      base + Apple-silicon MLX/Vision extras (no model weights downloaded)
set -eu
cd "$(dirname "$0")/.."
PY=${SDB_SETUP_PYTHON:-python3.12}
VENV=${SDB_VENV:-.venv-intel}
[ -e "$VENV" ] && { echo "refusing to reuse existing $VENV (delete it for a fresh install)"; exit 2; }
"$PY" -m venv "$VENV"
. "$VENV/bin/activate"
python --version
python -m pip install --disable-pip-version-check -q -r requirements/base.in -c requirements.lock
python -m pip check
python -c "import fastapi, jsonschema, pdfplumber, pypdf, openpyxl, docx, PIL; print('base imports ok')"
python -m pip freeze > requirements/base.lock
if [ "${1:-}" = "--mac" ]; then
  python -m pip install --disable-pip-version-check -q -r requirements/mac-local.in -c requirements.lock
  python -m pip check
  python -c "import mlx.core, mlx_vlm, Vision, Quartz; print('mac-local imports ok')"
  python -m pip freeze > requirements/mac-local.lock
fi
echo "environment ready: $(command -v python)"
