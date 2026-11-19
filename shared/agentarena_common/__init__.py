"""Cross-cutting infrastructure shared by AgentArena services.

This package deliberately holds no domain logic: logging, telemetry, token
verification, messaging, the outbox relay and resilience primitives only.
Domain models and business rules stay inside each service.
"""

__version__ = "1.0.0"
