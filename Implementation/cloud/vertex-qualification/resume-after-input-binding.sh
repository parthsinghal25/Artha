#!/bin/bash
# Run only after explicit approval of the runtime identity and permissions in README.md.
set -euo pipefail
G=${GCLOUD_BIN:-gcloud}
P=project-c2a6c144-4e0d-440b-918
B=superdashboard-c2a6c144-artifacts
SA=artha-vertex-qual@$P.iam.gserviceaccount.com
QUAL_RUN=20261018-vertex-onecase-v1
PREFIX=qualifications/vertex-onecase/$QUAL_RUN
HERE=$(cd "$(dirname "$0")" && pwd)
INPUT="$HERE/../../ai-backend/var/qualification/vertex-onecase/input-v2"
IMAGE=us-central1-docker.pkg.dev/$P/superdashboard/artha-vertex-qual@sha256:4e3e2c51d70b94a430a654bb47c6823c5bdaec210212ecf60acfafd6ce21e349
MANIFEST_SHA=f85b4e7d0709ee2d6e9c15a1619960961d5ffac51625ced485dcd2559fa56f44
# Resume only after the already-approved input binding is verified. Does not recreate identities or roles.
"$G" storage buckets add-iam-policy-binding "gs://$B" --member="serviceAccount:$SA" --role=roles/storage.objectCreator --condition="expression=resource.name.startsWith('projects/_/buckets/$B/objects/$PREFIX/output/') && request.time < timestamp('2026-10-19T00:00:00Z'),title=artha-qual-output"
# Existing authorised operator deploys and acts as this identity; do not add broad operator roles.
# If actAs/run/create permission is missing, report it; no automatic IAM escalation.
# Copy each file to its exact object name, preserving originals/ without folder-name nesting.
while IFS= read -r name; do
  "$G" storage cp "$INPUT/$name" "gs://$B/$PREFIX/input/$name" --project="$P"
done < <(python3 - "$INPUT" <<'PYUPLOAD'
import json,sys
from pathlib import Path
root=Path(sys.argv[1])
for name in ['manifest.json',*json.loads((root/'manifest.json').read_text())['files']]: print(name)
PYUPLOAD
)
# Verify the uploaded manifest by exact URI before creating the job; do not list input as runtime SA.
# gcloud cp folder semantics must preserve manifest.json directly under input/. If not, fix staging BEFORE execution.
"$G" storage cat "gs://$B/$PREFIX/input/manifest.json" --project="$P" | shasum -a 256
# The hash above MUST equal MANIFEST_SHA; use the machine guard below as well.
READ_SHA=$("$G" storage cat "gs://$B/$PREFIX/input/manifest.json" --project="$P" | shasum -a 256 | cut -d' ' -f1)
test "$READ_SHA" = "$MANIFEST_SHA"
"$G" run jobs create artha-vertex-onecase --project="$P" --region=us-central1 --image="$IMAGE" --service-account="$SA" --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=900s --cpu=1 --memory=1Gi --set-env-vars="SDB_VERTEX_PROJECT=$P,SDB_VERTEX_LOCATION=global,SDB_VERTEX_MODEL=gemini-3.8-flash,QUAL_INPUT_URI=gs://$B/$PREFIX/input,QUAL_OUTPUT_URI=gs://$B/$PREFIX/output,QUAL_MANIFEST_SHA256=$MANIFEST_SHA"
"$G" run jobs execute artha-vertex-onecase --project="$P" --region=us-central1 --async --format=json
# Exactly ONE execution. Retrieve result and receipts with operator permissions after completion.
