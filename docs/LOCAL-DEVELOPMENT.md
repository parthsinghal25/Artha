# Local development

Run commands from the repository root unless a component directory is shown. Python 3.12 is recommended for backend and data tooling. The UI uses Python standard library and browser JavaScript, with no Node build dependency.

## Backend environment

```bash
cd Implementation/ai-backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements/base.lock
cp .env.example .env
```

Keep credentials in the ignored `.env` or external environment. Cloud serving uses `requirements/cloud.lock` and does not require inference dependencies. A Gemini SDK is required for model calls; the cloud worker's exact Linux dependencies are in `../cloud/vertex-qualification/requirements.lock`. Local MLX inference has a separate macOS dependency set. Do not install macOS-only MLX or Apple Vision dependencies into Linux images.

## Dashboard layouts without a data store

```bash
python3 Implementation/ui/server/devserver.py
```

Open `http://127.0.0.1:5173`. Unconfigured backend states are explicit. This mode verifies layout availability, not analytical integration.

## Serve a verified export locally

Obtain a complete export directory produced by `sdbackend.cloud.publication.export_serving_publication`, including its manifest and archive. Supply your own absolute path. Neither the export nor its private original files are in Git.

```bash
cd Implementation/ai-backend
SDB_NO_DOTENV=1 SDB_HOSTED_DEMO=1 SDB_SERVING_STATE=sqlite ARTHA_PUBLICATION_SOURCE=local:/absolute/path/to/export ARTHA_RESTORE_ROOT=/tmp/artha-local SDB_HOST=127.0.0.1 SDB_PORT=8091 .venv/bin/python -m sdbackend.cloud.boot
```

Read-serving dependencies must include the packages from `requirements/cloud.lock`. The entrypoint verifies the export and restores to a fresh serving directory. Use the release ID from the export manifest in a second terminal:

```bash
SD_HOST=127.0.0.1 SD_PORT=15174 SD_API_UPSTREAM=http://127.0.0.1:8091 SD_RELEASE_ID=YOUR_RELEASE_ID SD_ASSET_REDEEM_PATH='/v1/assets/{token}' python3 Implementation/ui/server/devserver.py
```

Open `http://127.0.0.1:15174/overview`. Hosted demo mode uses a fixed read-only identity with no login screen. Processing controls are separate.

## Full processing

The existing `scripts/demo.sh` in the backend binds a named corpus and store. Corpus manifests under `config/demo/` describe the original delivery IDs, input digests and split boundaries. Their bulk deliveries and stores are deliberately absent. They cannot run until those inputs are supplied at the declared paths with their declared hashes.

With the required approved inputs available, run `python -m sdbackend --help` and `python -m sdbackend intel --help` from the backend environment. `intel plan` is the no-inference planning step; `intel update` performs bounded memory/collective/publication work. Provider access, model availability and quotas must be configured before update. Do not remove holdout exclusions to make a run succeed.
