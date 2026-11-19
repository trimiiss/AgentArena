from __future__ import annotations

from agentarena_common.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "agent-registry"

    # AES-256-GCM key for agent credentials. 32 bytes, base64-encoded.
    credentials_encryption_key: str = ""
    credentials_key_id: str = "k1"

    # Hosts allowed to resolve to private addresses, such as the bundled sample agent.
    agent_endpoint_allowlist: str = ""
    allow_http_endpoints: bool = False

    probe_timeout_seconds: float = 5.0
