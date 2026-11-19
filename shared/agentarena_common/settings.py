"""Environment-driven settings shared by every process."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    """Base settings. Each service extends this with its own fields."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    service_name: str = "agentarena"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = ""
    db_pool_size: int = 10
    db_max_overflow: int = 10

    rabbitmq_url: str = ""

    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4317"
    otel_sample_ratio: float = 1.0

    # Token verification. Public keys come from the identity service's JWKS endpoint.
    jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"
    jwt_issuer: str = "agentarena-identity"
    jwt_audience: str = "agentarena-api"

    # Shared secret for /internal/* routes, which the ingress never exposes.
    internal_service_token: str = ""

    # Per-organization limit on write routes, enforced per replica.
    rate_limit_per_second: float = 50.0
    rate_limit_burst: int = 100

    # Policy for calls to other services.
    http_timeout_seconds: float = 3.0
    http_retry_attempts: int = 3
    http_retry_base_seconds: float = 0.1
    http_retry_max_seconds: float = 2.0
    breaker_failure_threshold: int = 5
    breaker_recovery_seconds: float = 15.0
