# Artha

**Evidence-linked complaint intelligence for Australian superannuation leadership.**

Artha converts complaint histories, correspondence, call summaries and operational records into case memories, recurring issue assessments and interactive business intelligence. Leaders can inspect issue prevalence over time, recorded financial burden and staff hours, then follow the evidence back to an original document.

Built by **Team Perception**, led by **Sakshi Tyagi**, for the Google AI Builder Cup 2026 BFSI theme.

[Open the hosted demonstration](https://project-c2a6c144-4e0d-440b-918.web.app)

## What is implemented

- Multiformat evidence intake, extraction, provenance and source citations.
- Persistent case memories with multiple factors and explicit uncertainty.
- Cross-case issue discovery and assessment against the eligible case population.
- Deterministic counts, shares, time buckets, recorded cash and staff hours.
- Dashboard drill-through from issue to case, citation and original file.
- Verified publication export/restore, Cloud Run read serving, Firebase Hosting, private Cloud Storage and shared Firestore request state.
- Gemini provider integrations, including a Vertex AI adapter and a separate bounded Cloud Run Job implementation.

The demonstration corpus contains **500 synthetic members and 650 cases**. The completed analytical publication assesses **519 development cases** against **88 provisional issue definitions**. Unassessed cases remain visible in coverage. These are population counts, not accuracy claims.

## Repository

```text
Artha/
  Implementation/
    ai-backend/       Complete reasoning framework, providers, API, storage and tests
    ui/               Dashboard, same-origin gateway, build tool and tests
    synthetic-data/   Authoring validation, rendering and packaging tools
    cloud/            Image builders, service renderer and bounded Vertex job
  Shared/contracts/v1/  Immutable shared contracts and manifest
  Data/contracts/       Immutable runtime and measurement contracts
  docs/                 Maintained architecture, setup and deployment documentation
  scripts/              Repository verification
```

The component layout preserves relative-path compatibility. This repository excludes local environments, keys, model weights, runtime databases, bulk case artifacts, private evaluation labels and build outputs.

## Start here

1. [Local setup and data requirements](docs/LOCAL-DEVELOPMENT.md)
2. [Architecture and incremental processing](docs/ARCHITECTURE.md)
3. [Cloud deployment and AI execution](docs/DEPLOYMENT.md)
4. [Data tooling](docs/DATA.md)
5. [Verification and known boundaries](docs/STATUS.md)

The UI can run without a data store and will show unavailable states. The full analytical journey requires a verified publication export or approved source corpus. A clone does not silently download or manufacture the 500-member database.

## Source and cloud status

The hosted dashboard serves a completed publication. Its completed corpus inference used Gemini through the Antigravity CLI adapter. The Vertex adapter and Cloud Run Job are included as implemented, with their credential and input requirements documented. The attempted cloud qualification failed before application startup; a successful fresh Vertex inference is **not** claimed. Output-quality evaluation belongs to the separate evaluation workstream.

## Verification

```bash
python3 scripts/verify_repository.py
python3 Implementation/ui/build.py
python3 Implementation/cloud/build_backend_context.py
```

See [STATUS](docs/STATUS.md) for the exact checks run on this clean copy and the distinction between source checks, fixture tests and live cloud acceptance.
