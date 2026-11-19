from __future__ import annotations

from agentarena_common.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "run-orchestrator"

    agent_registry_url: str = "http://agent-registry:8000"
    scenario_service_url: str = "http://scenario-service:8000"

    max_executions_per_run: int = 2000

    # Workers
    worker_concurrency: int = 8
    worker_ops_port: int = 8000
    shutdown_grace_seconds: float = 25.0
    # Short leases plus heartbeats find a dead worker fast, whatever the run timeout is.
    lease_seconds: float = 15.0
    config_cache_ttl_seconds: float = 60.0
    max_agent_response_bytes: int = 1_000_000
    max_step_content_chars: int = 16_000
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 30.0

    # Same policy as the agent registry: which agent endpoints may be called.
    agent_endpoint_allowlist: str = ""
    allow_http_endpoints: bool = False

    # Scheduler
    scheduler_ops_port: int = 8000
    tick_seconds: float = 1.0
    sweep_every_ticks: int = 5
    dispatch_timeout_seconds: float = 300.0
    evaluation_timeout_seconds: float = 300.0
