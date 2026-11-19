from __future__ import annotations

from agentarena_common.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "scenario-service"
    max_variations_per_request: int = 100
    max_dataset_rows: int = 5000
