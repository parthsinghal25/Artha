"""Runtime configuration from environment variables (never secrets in source; see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ARTHA_ROOT = BACKEND_ROOT.parents[1]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); existing environment values win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _flag(name: str, default: bool = False) -> bool:
    value = _env(name)
    return default if value is None else value.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    shared_contract_dir: Path
    data_contract_dir: Path
    host: str
    port: int
    # Model routing
    model_provider: str                 # "gemini" | "gemma" | "auto"
    gemini_model: str
    gemini_api_key_present: bool
    gemini_free_tier_attested: bool     # operator attests the key's project is Free tier with billing disabled
    gemini_rpm: int
    gemini_rpd: int
    gemini_tpm: int
    gemma_model_path: Path
    gemma_model_id: str
    model_concurrency: int
    fallback_consecutive_quota_failures: int
    fallback_max_eta_seconds: int
    # Serving
    allow_unreviewed_sources: bool
    session_ttl_seconds: int
    context_ttl_seconds: int
    asset_token_ttl_seconds: int
    cookie_secure: bool
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    ui_origin: str = "http://127.0.0.1:5173"
    model_call_timeout_s: float = 1200.0       # per call wall clock (measured local max ≈ 290 s)
    # Explicit publication policy. `independent-review-v1` (default, legacy accepted path) or `synthetic-demo-v1`
    # (a named demo corpus file; serves only `release:demo`; never a global unreviewed-source bypass).
    publication_policy: str = "independent-review-v1"
    demo_corpus_path: Path | None = None
    vertex_project: str = ""
    vertex_location: str = "global"
    vertex_model: str = "gemini-3.8-flash"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "control.sqlite3"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def package_dir(self) -> Path:
        return self.data_dir / "packages"


def load_settings() -> Settings:
    if not _flag("SDB_NO_DOTENV"):            # the hosted serving entrypoint never reads a local .env
        _load_dotenv(BACKEND_ROOT / ".env")
    data_dir = Path(_env("SDB_DATA_DIR", str(BACKEND_ROOT / "var" / "data"))).resolve()
    return Settings(
        data_dir=data_dir,
        shared_contract_dir=Path(_env("SDB_SHARED_CONTRACT_DIR", str(ARTHA_ROOT / "Shared" / "contracts" / "v1"))),
        data_contract_dir=Path(_env("SDB_DATA_CONTRACT_DIR", str(ARTHA_ROOT / "Data" / "contracts"))),
        host=_env("SDB_HOST", "127.0.0.1"),
        port=int(_env("SDB_PORT", "8080")),
        model_provider=_env("SDB_MODEL_PROVIDER", "auto"),
        gemini_model=_env("SDB_GEMINI_MODEL", "gemini-3.8-flash"),
        gemini_api_key_present=bool(_env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")),
        gemini_free_tier_attested=_flag("SDB_GEMINI_FREE_TIER_ATTESTED"),
        gemini_rpm=int(_env("SDB_GEMINI_RPM", "5")),
        gemini_rpd=int(_env("SDB_GEMINI_RPD", "100")),
        gemini_tpm=int(_env("SDB_GEMINI_TPM", "200000")),
        gemma_model_path=Path(_env("SDB_GEMMA_MODEL_PATH", str(BACKEND_ROOT / "var" / "models" / "gemma-4-e4b-it-4bit"))),
        gemma_model_id=_env("SDB_GEMMA_MODEL_ID", "mlx-community/gemma-4-e4b-it-4bit"),
        model_concurrency=int(_env("SDB_MODEL_CONCURRENCY", "1")),
        fallback_consecutive_quota_failures=int(_env("SDB_FALLBACK_QUOTA_FAILURES", "3")),
        fallback_max_eta_seconds=int(_env("SDB_FALLBACK_MAX_ETA_SECONDS", str(6 * 3600))),
        allow_unreviewed_sources=_flag("SDB_ALLOW_UNREVIEWED_SOURCES"),
        session_ttl_seconds=int(_env("SDB_SESSION_TTL_SECONDS", str(8 * 3600))),
        context_ttl_seconds=min(300, int(_env("SDB_CONTEXT_TTL_SECONDS", "300"))),
        asset_token_ttl_seconds=min(300, int(_env("SDB_ASSET_TOKEN_TTL_SECONDS", "300"))),
        cookie_secure=_flag("SDB_COOKIE_SECURE"),
        allowed_origins=tuple(o for o in (_env("SDB_ALLOWED_ORIGINS", "") or "").split(",") if o),
        ui_origin=_env("SDB_UI_ORIGIN", "http://127.0.0.1:5173"),
        model_call_timeout_s=float(_env("SDB_MODEL_CALL_TIMEOUT_S", "1200")),
        publication_policy=_env("SDB_PUBLICATION_POLICY", "independent-review-v1"),
        demo_corpus_path=Path(_env("SDB_DEMO_CORPUS")) if _env("SDB_DEMO_CORPUS") else None,
        vertex_project=_env("SDB_VERTEX_PROJECT", ""),
        vertex_location=_env("SDB_VERTEX_LOCATION", "global"),
        vertex_model=_env("SDB_VERTEX_MODEL", "gemini-3.8-flash"),
    )
