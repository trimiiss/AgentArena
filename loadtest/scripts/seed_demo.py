#!/usr/bin/env python3
"""Creates a demo workspace by using the product through its public API.

Nothing is written to a database directly and no results are invented: this
registers a user, installs the starter pack, registers a flawed and a fixed
version of the sample agent, and runs real benchmarks against both.

Uses only the standard library, so it runs with any Python 3.10 or later:

    python scripts/seed_demo.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def read_env_value(name: str) -> str:
    if name in os.environ:
        return os.environ[name]
    for filename in (".env", ".env.example"):
        path = ROOT / filename
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip()
    sys.exit(f"{name} is not set. Copy .env.example to .env first.")


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = ""

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if raw else None

    def must(self, method: str, path: str, body: Any = None) -> Any:
        status, payload = self.call(method, path, body)
        if status >= 400:
            sys.exit(f"{method} {path} failed with {status}: {payload}")
        return payload


def sign_in(client: Client, email: str, password: str) -> None:
    status, session = client.call("POST", "/v1/auth/login", {"email": email, "password": password})
    if status != 200:
        session = client.must(
            "POST",
            "/v1/auth/register",
            {
                "email": email,
                "password": password,
                "display_name": "Demo User",
                "organization_name": "Demo Organization",
            },
        )
    client.token = session["access_token"]


def ensure_agent(client: Client) -> dict[str, Any]:
    prices = {"input_cost_per_1k": "0.003", "output_cost_per_1k": "0.015", "model_name": "sample-rules"}
    agents = client.must("GET", "/v1/agents?q=ACME%20Support%20Bot")["items"]
    if agents:
        return agents[0]
    agent = client.must(
        "POST",
        "/v1/agents",
        {
            "name": "ACME Support Bot",
            "description": "Sample customer-support agent. Version 1 has realistic flaws, and version 2 fixes them.",
            "agent_type": "customer_support",
            "endpoint_url": "http://sample-agent:8000/agents/v1",
            "notes": "First release.",
            **prices,
        },
    )
    client.must(
        "POST",
        f"/v1/agents/{agent['id']}/versions",
        {
            "endpoint_url": "http://sample-agent:8000/agents/v2",
            "notes": "Adds policy checks, asks for missing details, handles tool failures.",
            **prices,
        },
    )
    client.must("POST", f"/v1/agents/{agent['id']}/validate")
    return agent


def run_benchmark(client: Client, agent_id: str, suite_id: str, version: int, repetitions: int) -> dict[str, Any]:
    run = client.must(
        "POST",
        "/v1/runs",
        {
            "agent_id": agent_id,
            "agent_version": version,
            "suite_id": suite_id,
            "repetitions": repetitions,
            "concurrency": 8,
            "seed": 42,
            "name": f"Support benchmark v{version}",
        },
    )
    print(f"  started {run['name']} ({run['total_executions']} executions)")
    while True:
        current = client.must("GET", f"/v1/runs/{run['id']}")
        if current["status"] in ("completed", "failed", "cancelled"):
            return current
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("AGENTARENA_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--repetitions", type=int, default=8, help="Repetitions per scenario. 12 scenarios each.")
    arguments = parser.parse_args()

    client = Client(arguments.base_url)
    email = read_env_value("DEMO_USER_EMAIL")
    sign_in(client, email, read_env_value("DEMO_USER_PASSWORD"))
    pack = client.must("POST", "/v1/scenarios/starter-pack")
    agent = ensure_agent(client)

    for version in (1, 2):
        run = run_benchmark(client, agent["id"], pack["suite_id"], version, arguments.repetitions)
        progress = run["progress"]
        print(f"  v{version}: {progress['passed']} of {run['total_executions']} scenarios passed")
    print(f"\nOpen {arguments.base_url} and sign in as {email}.")
    print("The password is DEMO_USER_PASSWORD in your .env file.")


if __name__ == "__main__":
    main()
