# Artha AI framework

Complete provider-neutral complaint intelligence implementation: intake, extraction, source citations, durable case memory, collective issue discovery, population assessment, deterministic measurement, publication and read APIs.

See [setup](../../docs/LOCAL-DEVELOPMENT.md), [architecture](../../docs/ARCHITECTURE.md) and [cloud execution](../../docs/DEPLOYMENT.md). Run CLI commands from this folder using a Python 3.12 environment. `python -m sdbackend --help` lists the available commands. `SDB_NO_DOTENV=1` disables reading a local environment file during isolated checks.

`sdbackend/intel/` contains the portfolio pipeline. `sdbackend/models/` contains the model providers. `sdbackend/cloud/` contains publication transfer and the bounded Vertex job. Model credentials and the full corpus are not included.
