"""Identity service. Exposes /health, /ready, /metrics and the auth API."""

from __future__ import annotations

from fastapi import FastAPI

from agentarena_common.app import create_service_app
from agentarena_common.auth import LocalKeys
from agentarena_common.ratelimit import TokenBucketLimiter
from identity_service.api import router
from identity_service.config import Settings
from identity_service.security import load_or_create_signing_key

settings = Settings()


async def startup(app: FastAPI) -> None:
    key = await load_or_create_signing_key(app.state.database, settings)
    app.state.signing_key = key
    app.state.jwks = LocalKeys({key.kid: key.private_key.public_key()})


app = create_service_app(
    settings,
    title="AgentArena Identity Service",
    description="Accounts, organizations, memberships and access tokens.",
    on_startup=startup,
)
app.state.login_limiter = TokenBucketLimiter(settings.login_rate_per_second, settings.login_rate_burst)
app.state.register_limiter = TokenBucketLimiter(settings.register_rate_per_second, settings.register_rate_burst)
app.include_router(router)
