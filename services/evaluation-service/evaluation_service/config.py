from __future__ import annotations

from agentarena_common.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "evaluation-service"

    consumer_prefetch: int = 16
    consumer_ops_port: int = 8000

    # A run passes when at least this share of its scenarios passes.
    benchmark_pass_rate: float = 0.8

    # The judge is optional and advisory. It runs only with an API key, and only
    # for runs that ask for it. Its scores never reach the benchmark.
    anthropic_api_key: str = ""
    judge_model: str = "claude-opus-5-5"
    judge_timeout_seconds: float = 60.0
    judge_max_retries: int = 2
