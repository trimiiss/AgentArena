"""Agent Registry service. Exposes /health, /ready, /metrics and the agents API."""

from __future__ import annotations

from agent_registry.api import router
from agent_registry.config import Settings
from agent_registry.crypto import CredentialCipher
from agentarena_common.app import create_service_app

settings = Settings()

app = create_service_app(
    settings,
    title="AgentArena Agent Registry",
    description="Registers AI agents, their immutable versions, endpoints and encrypted credentials.",
)
app.state.cipher = CredentialCipher(settings.credentials_encryption_key, settings.credentials_key_id)
app.include_router(router)
