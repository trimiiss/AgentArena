"""End-to-end tests. They talk to a running stack over HTTP and mock nothing.

Start it first with: docker compose up --build --wait
Point these at another deployment with AGENTARENA_BASE_URL, for example a kind
ingress. Without it, each service is reached on its published local port.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

BASE_URL = os.environ.get("AGENTARENA_BASE_URL", "")
SAMPLE_AGENT_URL = os.environ.get("SAMPLE_AGENT_URL", "http://sample-agent:8000")
SAMPLE_AGENT_API_KEY = os.environ.get("SAMPLE_AGENT_API_KEY", "local-dev-sample-agent")

LOCAL_PORTS = {
    "/v1/auth": 8001,
    "/v1/organizations": 8001,
    "/v1/agents": 8002,
    "/v1/scenarios": 8003,
    "/v1/suites": 8003,
    "/v1/datasets": 8003,
    "/v1/runs": 8004,
    "/v1/executions": 8004,
    "/v1/evaluations": 8005,
    "/v1/benchmarks": 8005,
    "/v1/comparisons": 8005,
    "/v1/analytics": 8005,
}


def url_for(path: str) -> str:
    if BASE_URL:
        return f"{BASE_URL}{path}"
    for prefix, port in LOCAL_PORTS.items():
        if path.startswith(prefix):
            return f"http://127.0.0.1:{port}{path}"
    raise ValueError(f"No service for {path}")


class Api:
    """A small client for one logged-in user."""

    def __init__(self) -> None:
        self.http = httpx.Client(timeout=15)
        self.token = ""
        self.org_id = ""
        self.email = ""

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        if self.token:
            headers.setdefault("Authorization", f"Bearer {self.token}")
        return self.http.request(method, url_for(path), headers=headers, **kwargs)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def ok(self, response: httpx.Response) -> Any:
        assert response.is_success, f"{response.request.method} {response.url}: {response.status_code} {response.text}"
        return response.json() if response.content else None

    def register(self, label: str = "e2e") -> Api:
        self.email = f"{label}-{uuid.uuid4().hex[:10]}@example.com"
        body = self.ok(
            self.post(
                "/v1/auth/register",
                json={
                    "email": self.email,
                    "password": "correct-horse-battery",
                    "display_name": "E2E User",
                    "organization_name": f"{label} org {uuid.uuid4().hex[:6]}",
                },
            )
        )
        self.token = body["access_token"]
        self.org_id = body["organization"]["id"]
        return self

    def create_agent(self, name: str, path: str = "v2", **extra: Any) -> dict[str, Any]:
        return self.ok(
            self.post(
                "/v1/agents",
                json={
                    "name": name,
                    "agent_type": "customer_support",
                    "endpoint_url": f"{SAMPLE_AGENT_URL}/agents/{path}",
                    "input_cost_per_1k": "0.003",
                    "output_cost_per_1k": "0.015",
                    **extra,
                },
            )
        )

    def starter_pack(self) -> dict[str, Any]:
        return self.ok(self.post("/v1/scenarios/starter-pack"))

    def start_run(self, agent_id: str, suite_id: str, **options: Any) -> dict[str, Any]:
        body = {"agent_id": agent_id, "suite_id": suite_id, "concurrency": 8, "timeout_seconds": 20, **options}
        return self.ok(self.post("/v1/runs", json=body))

    def wait_for_run(self, run_id: str, timeout: float = 180) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.ok(self.get(f"/v1/runs/{run_id}"))
            if run["status"] in ("completed", "failed", "cancelled"):
                return run
            time.sleep(0.5)
        raise AssertionError(f"Run {run_id} did not finish: {run}")

    def wait_for_benchmark(self, run_id: str, timeout: float = 30) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.get(f"/v1/benchmarks/runs/{run_id}")
            if response.status_code == 200 and response.json()["final"]:
                return response.json()
            time.sleep(0.5)
        raise AssertionError(f"No final benchmark for {run_id}")


@pytest.fixture(scope="session", autouse=True)
def stack_is_running() -> None:
    try:
        httpx.get(url_for("/v1/agents"), timeout=5)
    except httpx.HTTPError as exc:
        pytest.skip(f"Stack is not running: {exc}")


@pytest.fixture()
def api() -> Iterator[Api]:
    client = Api().register()
    yield client
    client.http.close()


@pytest.fixture(scope="module")
def shared_api() -> Iterator[Api]:
    client = Api().register("shared")
    yield client
    client.http.close()
