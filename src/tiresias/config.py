from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TiresiasSettings(BaseSettings):
    """All configuration for the Tiresias container, loaded from environment variables."""

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Deployment mode
    # - saas: shared multi-tenant proxy, tenant resolved per-request from API key
    # - dedicated: single-tenant cloud pod, tenant_id from env (same as onprem code path)
    # - onprem: single-tenant, local keys, local dashboard, no cloud dependency
    mode: Literal["saas", "dedicated", "onprem"] = Field(
        default="onprem", alias="TIRESIAS_MODE"
    )

    # Authentication opt-out for single-tenant modes. OFF by default: dedicated
    # and onprem proxies require the tenant's first-boot API key on every
    # route except /health and the docs. Setting this true makes every route
    # answer anyone who can reach the port. Only for a single-user proxy bound
    # to 127.0.0.1; logged as a WARNING at every boot. Ignored in saas mode.
    allow_unauthenticated: bool = Field(default=False, alias="TIRESIAS_ALLOW_UNAUTHENTICATED")

    # Core identity
    tenant_id: str = Field(
        default_factory=lambda: str(uuid4()),
        alias="TIRESIAS_TENANT_ID",
    )
    kek_provider: Literal[
        "local", "aws-kms", "hashicorp-vault", "azure-kv", "gcp-sm"
    ] = Field(default="local", alias="TIRESIAS_KEK_PROVIDER")
    kek_value: str | None = Field(default=None, alias="TIRESIAS_KEK")

    # Retention
    retention_days: int = Field(default=30, alias="TIRESIAS_RETENTION_DAYS")
    usage_retention_days: int = Field(default=90, alias="TIRESIAS_USAGE_RETENTION_DAYS")

    # Network
    proxy_port: int = Field(default=8080, alias="PROXY_PORT")
    dashboard_port: int = Field(default=3000, alias="DASHBOARD_PORT")

    # Storage
    data_root: Path = Field(default=Path("/data"), alias="TIRESIAS_DATA_ROOT")
    database_url: str | None = Field(default=None, alias="TIRESIAS_DATABASE_URL")

    # Purge
    purge_dek: bool = Field(default=False, alias="TIRESIAS_PURGE_DEK")
    purge_interval_hours: int = Field(default=24, alias="TIRESIAS_PURGE_INTERVAL_HOURS")

    # Upstream provider URL (single-provider legacy mode and generic proxy)
    upstream_url: str = Field(default="https://api.openai.com", alias="TIRESIAS_UPSTREAM_URL")

    # Multi-provider routing: comma-separated list, e.g. "openai,anthropic,gemini"
    providers: str = Field(default="openai", alias="TIRESIAS_PROVIDERS")

    # Generic API proxy mode (Phase 5 — APIP-01)
    # When True, all requests to /api/{path} are forwarded to upstream_url/{path}
    # with per-endpoint telemetry recorded.
    generic_proxy_mode: bool = Field(default=False, alias="TIRESIAS_GENERIC_PROXY_MODE")

    # API service label for cost attribution (e.g. "stripe", "twilio")
    api_service: str | None = Field(default=None, alias="TIRESIAS_API_SERVICE")

    # AWS KMS
    aws_kms_key_id: str | None = Field(default=None, alias="TIRESIAS_AWS_KMS_KEY_ID")
    aws_kms_region: str | None = Field(default=None, alias="TIRESIAS_AWS_KMS_REGION")

    # HashiCorp Vault
    vault_url: str | None = Field(default=None, alias="TIRESIAS_VAULT_URL")
    vault_token: str | None = Field(default=None, alias="TIRESIAS_VAULT_TOKEN")
    vault_mount: str | None = Field(default=None, alias="TIRESIAS_VAULT_MOUNT")
    vault_path: str | None = Field(default=None, alias="TIRESIAS_VAULT_PATH")

    # Azure Key Vault
    azure_vault_url: str | None = Field(default=None, alias="TIRESIAS_AZURE_VAULT_URL")
    azure_key_name: str | None = Field(default=None, alias="TIRESIAS_AZURE_KEY_NAME")

    # GCP Secret Manager
    gcp_project_id: str | None = Field(default=None, alias="TIRESIAS_GCP_PROJECT_ID")
    gcp_secret_id: str | None = Field(default=None, alias="TIRESIAS_GCP_SECRET_ID")

    # Redis (used for distributed rate limiting in SaaS mode)
    redis_url: str | None = Field(default=None, alias="TIRESIAS_REDIS_URL")


def parse_providers(providers_str: str) -> list[str]:
    """Parse TIRESIAS_PROVIDERS env var into an ordered list of provider names."""
    return [p.strip().lower() for p in providers_str.split(",") if p.strip()]

