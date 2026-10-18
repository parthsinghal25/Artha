# Google Cloud deployment

## Hosted read path

Firebase Hosting serves dashboard assets. Its same-origin API routes reach a Cloud Run service containing the UI gateway and backend sidecar. The backend restores a verified analytical SQLite publication from private Cloud Storage. Firestore holds request state and the active publication pointer. Cloud Build and Artifact Registry build and store container images.

Source builders:

```bash
python3 Implementation/cloud/build_backend_context.py
python3 Implementation/ui/build.py --hosting YOUR_CLOUD_RUN_SERVICE --project YOUR_PROJECT --region us-central1
python3 Implementation/cloud/render_service.py --help
```

Builders create ignored `dist/` folders. They do not upload or deploy. The service renderer still contains the existing demo's concrete resource defaults; review them before deploying to another project. Generate the descriptor against the intended export manifest, build and push both images, activate the verified export, deploy Cloud Run, check its read journey, then deploy Firebase Hosting. A new publication requires a matching service revision in the current implementation. Do not treat pointer changes as hot reloads.

The 500-member snapshot required more resources than the pilot. Preserve the running deployment's sizing when rendering a successor; do not assume renderer defaults are sufficient. Issue-specific impact queries can be slow and use a disclosed candidate subset.

## Cloud AI processing

The complete reasoning framework remains in `Implementation/ai-backend/sdbackend/`. `models/vertex.py` is the Vertex provider adapter. `cloud/vertex_job.py` is the isolated job entrypoint. Deployment source and pinned Linux dependencies are in `Implementation/cloud/vertex-qualification/`.

Expected credential flow:

1. Enable the required Google APIs in the target billed/trial project.
2. Attach a narrowly scoped service account to the private Cloud Run Job.
3. Grant publisher-model prediction and required input/output object access.
4. Configure project, location and an available model using `SDB_MODEL_PROVIDER=vertex`, `SDB_VERTEX_PROJECT`, `SDB_VERTEX_LOCATION` and `SDB_VERTEX_MODEL`.
5. Provide the validated hash-bound input packet and isolated output location.
6. Build the worker image, run the bounded job and inspect durable receipts and output before any publication promotion.

Vertex uses Application Default Credentials from the attached identity, not a browser subscription or embedded API key. The Gemini Developer API adapter is also present and uses server-side `GEMINI_API_KEY` with its existing route guards. An API key alone does not fix a container import failure, missing IAM permission, unavailable model or incompatible input.

The existing qualification entrypoint is deliberately scoped to one named development case and an optional small frozen issue subset. It is not a generic automatic 500-case cloud scheduler. The complete portfolio pipeline is available through the backend CLI; broadening the cloud wrapper requires explicit engineering and validation.

The qualification scripts preserve the exact attempted deployment, resource names, expiring permissions and single-use namespace. They are reference implementation, not safe copy-and-run setup for a fresh project. Do not replay the previous launch or resume scripts blindly.

## Execution status

The hosted analytical dashboard was deployed and checked. The separate Vertex qualification failed with `ContainerImageImportFailed` before application startup, producing no inference output. The source is included as implemented; successful cloud inference is not asserted. Diagnostic/output evaluation is handled separately.

Existing cloud identifiers containing the earlier internal name are retained for continuity. Renaming a local checkout does not rename a running Cloud Run service, bucket, service account or registry. No remote resource is modified by this cleanup.
