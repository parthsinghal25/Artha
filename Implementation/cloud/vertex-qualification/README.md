# Bounded Vertex AI job

This folder contains the existing job implementation, input preparer, dependency lock, image builder and attempted deployment scripts. See [cloud deployment](../../../docs/DEPLOYMENT.md) for execution assumptions and current status.

The entrypoint calls the existing framework's case-memory phase and optionally a small issue subset. It does not automatically publish to the dashboard or schedule the full corpus. Inputs are hash-validated, attempts are bounded and durable receipts are separate from the serving store.

The prior execution failed during container image import before inference. Scripts contain prior project identities, expiring grants and a single-use namespace. Review and adapt them before any new execution. Never commit credentials or generated input/output packets.
