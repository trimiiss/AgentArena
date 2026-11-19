from __future__ import annotations

from agentarena_common.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "identity-service"

    # Encrypts the private signing key at rest. 32 bytes, base64-encoded.
    identity_key_encryption_key: str = ""

    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 14 * 24 * 3600
    refresh_cookie_name: str = "aa_refresh"
    # Must be true anywhere the API is served over HTTPS.
    cookie_secure: bool = False

    login_rate_per_second: float = 1.0
    login_rate_burst: int = 10
    register_rate_per_second: float = 1.0
    register_rate_burst: int = 10
