#!/bin/sh
# Resumable corpus processing through the Antigravity CLI provider (explicit `--provider agy`).
#   SDB_AGY_OVERAGES_NEVER_ATTESTED=1 scripts/agy-bulk.sh          (defaults: fixed500-agy-v1, view 2026-09-30, 6 workers)
# Safe to rerun at any time: sealed case memories and persisted assessment batches are reused under the same
# input/model/prompt/harness versions; re-queued (stopped, transient, quota-paused) cases are picked up again.
# 1. N case-memory workers on disjoint case shards, each its own resumable run (each memory sealed as it completes).
# 2. One single-worker memory pass picks up anything re-queued.
# 3. Collective discovery/assessment + publication run only when that pass reports `complete`; otherwise the
#    script stops and prints the state (e.g. provider_unavailable on quota pause) — rerun after quota refresh.
set -eu
cd "$(dirname "$0")/.."
NAME=${SDB_DEMO_NAME:-fixed500-agy-v1}
VIEW=${VIEW:-2026-09-30}
N=${WORKERS:-6}
: "${SDB_AGY_OVERAGES_NEVER_ATTESTED:?set to 1 only after confirming AI Credit Overages = Never}"
export SDB_DEMO_NAME="$NAME" SDB_AGY_OVERAGES_NEVER_ATTESTED
LOG="reports/intel/$NAME/bulk"
mkdir -p "$LOG"
STAMP=$(date +%Y%m%dT%H%M%S)
echo "$(date '+%F %T') start name=$NAME view=$VIEW workers=$N stamp=$STAMP" >> "$LOG/bulk.log"

# N workers on disjoint deterministic case shards (`--shard i/N`): no two workers can ever take the same case
i=0
while [ "$i" -lt "$N" ]; do
  scripts/demo.sh intel update --as-of "$VIEW" --provider agy --memory-only --shard "$i/$N" \
    > "$LOG/w$i-$STAMP.json" 2> "$LOG/w$i-$STAMP.log" &
  i=$((i + 1))
done
wait || true
echo "$(date '+%F %T') parallel memory workers ended" >> "$LOG/bulk.log"

scripts/demo.sh intel update --as-of "$VIEW" --provider agy --memory-only > "$LOG/sweep-$STAMP.json" 2> "$LOG/sweep-$STAMP.log" || true
STATE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('state'))" "$LOG/sweep-$STAMP.json" 2>/dev/null || echo unknown)
echo "$(date '+%F %T') sweep state=$STATE" >> "$LOG/bulk.log"
if [ "$STATE" != "complete" ]; then
  echo "memory phase not complete (state=$STATE); collective/publication NOT run. Rerun this script to resume." | tee -a "$LOG/bulk.log"
  exit 3
fi
scripts/demo.sh intel update --as-of "$VIEW" --provider agy > "$LOG/collective-$STAMP.json" 2> "$LOG/collective-$STAMP.log"
STATE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('state'))" "$LOG/collective-$STAMP.json" 2>/dev/null || echo unknown)
echo "$(date '+%F %T') collective+publication state=$STATE" | tee -a "$LOG/bulk.log"
[ "$STATE" = "complete" ]
