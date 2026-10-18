#!/bin/sh
# Run any backend command against the named demo store (separate data dir, policy file and corpus binding).
#   scripts/demo.sh <command...>       e.g. scripts/demo.sh intel intake | intel status | serve
# Environment is derived from config/demo/$SDB_DEMO_NAME.corpus.json (default p4-pilot-v1). The legacy flags that
# would bypass review are always unset.
set -eu
cd "$(dirname "$0")/.."
NAME=${SDB_DEMO_NAME:-p4-pilot-v1}
CORPUS="config/demo/$NAME.corpus.json"
[ -f "$CORPUS" ] || { echo "no corpus file $CORPUS"; exit 2; }
PY=${SDB_PYTHON:-$PWD/.venv-intel/bin/python}
export SDB_DATA_DIR="$PWD/var/demo/$NAME"
export SDB_POLICY_PATH="$PWD/config/demo/policy.json"
export SDB_PUBLICATION_POLICY=synthetic-demo-v1
export SDB_DEMO_CORPUS="$PWD/$CORPUS"
export SDB_PORT=${SDB_PORT:-8091}
export SDB_UI_ORIGIN=${SDB_UI_ORIGIN:-http://127.0.0.1:15174}
export SDB_REPORT_DIR="$PWD/reports/intel/$NAME/runs"
# Antigravity CLI invocation traces (no prompts/credentials; ids, status, usage, tool gate) live with the store
export SDB_AGY_TRACE_DIR=${SDB_AGY_TRACE_DIR:-$SDB_DATA_DIR/agy-traces}
exec env -u SDB_ALLOW_UNREVIEWED_SOURCES -u SDB_ALLOW_SPECIMEN_ACCEPTANCE -u SDB_REVIEWER_REGISTRY "$PY" -m sdbackend "$@"
