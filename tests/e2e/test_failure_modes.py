"""How the platform behaves when agents misbehave and clients retry."""

from __future__ import annotations

import concurrent.futures
import json
import time
import uuid

import httpx
import pytest
from conftest import SAMPLE_AGENT_API_KEY, Api, url_for


@pytest.fixture(scope="module")
def suite(shared_api: Api) -> str:
    return shared_api.starter_pack()["suite_id"]


def agent(api: Api, path: str) -> dict:
    return api.create_agent(f"{path} {uuid.uuid4().hex[:6]}", path)


def statuses(api: Api, run_id: str) -> list[dict]:
    return api.ok(api.get(f"/v1/runs/{run_id}/executions"))["items"]


class TestAgentFailures:
    def test_flaky_agent_recovers_through_retries(self, shared_api: Api, suite: str) -> None:
        run = shared_api.start_run(agent(shared_api, "flaky")["id"], suite, max_retries=2, repetitions=2)
        finished = shared_api.wait_for_run(run["id"])
        rows = statuses(shared_api, run["id"])
        assert finished["status"] == "completed"
        assert all(row["status"] == "completed" for row in rows)
        assert any(row["attempt"] > 1 for row in rows), "this seed set should trigger at least one retry"

    def test_retry_limit_is_honoured(self, shared_api: Api, suite: str) -> None:
        run = shared_api.start_run(agent(shared_api, "flaky")["id"], suite, max_retries=0, repetitions=2)
        shared_api.wait_for_run(run["id"])
        rows = statuses(shared_api, run["id"])
        assert all(row["attempt"] == 1 for row in rows)
        failed = [row for row in rows if row["status"] == "failed"]
        assert failed
        assert all(row["error_code"] == "agent_http_5xx" for row in failed)

    def test_hanging_agent_times_out(self, shared_api: Api, suite: str) -> None:
        run = shared_api.start_run(
            agent(shared_api, "hang")["id"], suite, timeout_seconds=1, max_retries=0, concurrency=12
        )
        finished = shared_api.wait_for_run(run["id"], timeout=60)
        rows = statuses(shared_api, run["id"])
        assert finished["status"] == "failed"
        assert all(row["status"] == "timed_out" for row in rows)
        summary = shared_api.wait_for_benchmark(run["id"])["summary"]
        assert summary["reliability"]["timeout_rate"] == 1
        assert summary["failure_categories"]["timeout"] == 12

    def test_protocol_violation_is_not_retried(self, shared_api: Api, suite: str) -> None:
        run = shared_api.start_run(agent(shared_api, "broken")["id"], suite, max_retries=3)
        shared_api.wait_for_run(run["id"])
        rows = statuses(shared_api, run["id"])
        assert all(row["error_code"] == "agent_protocol_error" for row in rows)
        assert all(row["attempt"] == 1 for row in rows)

    def test_missing_credential_fails_and_stored_credential_works(self, shared_api: Api, suite: str) -> None:
        secure = agent(shared_api, "secure")
        run = shared_api.start_run(secure["id"], suite, max_retries=0)
        shared_api.wait_for_run(run["id"])
        assert all(row["error_code"] == "agent_http_error" for row in statuses(shared_api, run["id"]))

        shared_api.ok(
            shared_api.request(
                "PUT",
                f"/v1/agents/{secure['id']}/credential",
                json={"auth_type": "bearer", "secret": SAMPLE_AGENT_API_KEY},
            )
        )
        # The worker caches agent config briefly, so a new version avoids the old entry.
        shared_api.ok(
            shared_api.post(
                f"/v1/agents/{secure['id']}/versions",
                json={"endpoint_url": "http://sample-agent:8000/agents/secure"},
            )
        )
        run = shared_api.start_run(secure["id"], suite)
        finished = shared_api.wait_for_run(run["id"])
        assert finished["status"] == "completed"
        assert finished["progress"]["completed"] == 12


class TestValidation:
    def test_probe_reports_an_unreachable_agent(self, api: Api) -> None:
        created = api.create_agent("Unknown Path", "does-not-exist")
        result = api.ok(api.post(f"/v1/agents/{created['id']}/validate"))
        assert result["valid"] is False
        assert result["status"] == "unreachable"

    def test_probe_detects_a_protocol_mismatch(self, api: Api) -> None:
        created = api.create_agent("Broken Path", "broken")
        result = api.ok(api.post(f"/v1/agents/{created['id']}/validate"))
        assert result["valid"] is False
        assert any(check["name"] == "protocol" and not check["passed"] for check in result["checks"])

    def test_run_needs_exactly_one_scenario_source(self, api: Api) -> None:
        body = {"agent_id": str(uuid.uuid4()), "suite_id": str(uuid.uuid4()), "scenario_ids": [str(uuid.uuid4())]}
        assert api.post("/v1/runs", json=body).status_code == 422
        assert api.post("/v1/runs", json={"agent_id": str(uuid.uuid4())}).status_code == 422

    def test_unknown_agent_is_not_found(self, api: Api) -> None:
        pack = api.starter_pack()
        response = api.post("/v1/runs", json={"agent_id": str(uuid.uuid4()), "suite_id": pack["suite_id"]})
        assert response.status_code == 404

    def test_oversized_run_is_refused(self, api: Api) -> None:
        pack = api.starter_pack()
        created = api.create_agent("Big Run")
        body = {"agent_id": created["id"], "suite_id": pack["suite_id"], "repetitions": 100, "concurrency": 2}
        assert api.post("/v1/runs", json=body).status_code in (202, 422)
        body["repetitions"] = 101
        assert api.post("/v1/runs", json=body).status_code == 422

    def test_disabled_agent_cannot_run(self, api: Api) -> None:
        pack = api.starter_pack()
        created = api.create_agent("Disabled Bot")
        api.ok(api.request("PATCH", f"/v1/agents/{created['id']}", json={"status": "disabled"}))
        response = api.post("/v1/runs", json={"agent_id": created["id"], "suite_id": pack["suite_id"]})
        assert response.status_code == 409


class TestIdempotency:
    def test_repeating_a_request_returns_the_same_run(self, api: Api) -> None:
        pack = api.starter_pack()
        created = api.create_agent("Idempotent Bot")
        body = {"agent_id": created["id"], "suite_id": pack["suite_id"]}
        key = {"Idempotency-Key": uuid.uuid4().hex}
        first = api.post("/v1/runs", json=body, headers=dict(key))
        second = api.post("/v1/runs", json=body, headers=dict(key))
        assert first.status_code == 202
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert api.ok(api.get("/v1/runs"))["total"] == 1

    def test_parallel_duplicates_still_create_one_run(self, api: Api) -> None:
        pack = api.starter_pack()
        created = api.create_agent("Racing Bot")
        body = {"agent_id": created["id"], "suite_id": pack["suite_id"]}
        key = uuid.uuid4().hex

        def submit(_: int) -> httpx.Response:
            return httpx.post(
                url_for("/v1/runs"),
                json=body,
                headers={"Authorization": f"Bearer {api.token}", "Idempotency-Key": key},
                timeout=15,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(pool.map(submit, range(10)))
        assert all(response.status_code in (200, 202) for response in responses)
        assert len({response.json()["id"] for response in responses}) == 1
        assert api.ok(api.get("/v1/runs"))["total"] == 1


class TestRunControl:
    def test_concurrency_limit_is_respected(self, shared_api: Api, suite: str) -> None:
        run = shared_api.start_run(agent(shared_api, "slow")["id"], suite, concurrency=2, repetitions=1)
        peak = 0
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            current = shared_api.ok(shared_api.get(f"/v1/runs/{run['id']}"))
            progress = current["progress"]
            peak = max(peak, progress["running"] + progress["dispatched"])
            assert peak <= 2
            if peak == 2:
                break
            time.sleep(0.1)
        assert peak == 2
        shared_api.ok(shared_api.post(f"/v1/runs/{run['id']}/cancel"))

    def test_cancel_stops_pending_work(self, shared_api: Api, suite: str) -> None:
        run = shared_api.start_run(agent(shared_api, "slow")["id"], suite, concurrency=1, repetitions=2)
        time.sleep(1)
        shared_api.ok(shared_api.post(f"/v1/runs/{run['id']}/cancel"))
        finished = shared_api.wait_for_run(run["id"], timeout=40)
        assert finished["status"] == "cancelled"
        assert finished["progress"]["cancelled"] > 0

    def test_finished_run_cannot_be_cancelled(self, shared_api: Api, suite: str) -> None:
        run = shared_api.start_run(agent(shared_api, "v2")["id"], suite)
        shared_api.wait_for_run(run["id"])
        assert shared_api.post(f"/v1/runs/{run['id']}/cancel").status_code == 409


def test_live_progress_streams_until_the_run_ends(shared_api: Api, suite: str) -> None:
    run = shared_api.start_run(agent(shared_api, "v2")["id"], suite)
    events: list[tuple[str, dict]] = []
    headers = {"Authorization": f"Bearer {shared_api.token}"}
    with httpx.stream("GET", url_for(f"/v1/runs/{run['id']}/stream"), headers=headers, timeout=60) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        name = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
    names = [name for name, _ in events]
    assert names[0] == "progress"
    assert "executions" in names
    assert names[-1] == "done"
    assert events[-1][1]["status"] == "completed"


class TestScenarioManagement:
    definition = {"turns": [{"role": "user", "content": "Hello"}]}

    def test_new_version_leaves_the_old_one_intact(self, api: Api) -> None:
        scenario = api.ok(
            api.post(
                "/v1/scenarios",
                json={"name": "Versioned", "scenario_type": "tool_use", "definition": self.definition},
            )
        )
        changed = {"turns": [{"role": "user", "content": "Changed"}]}
        version = api.ok(
            api.post(
                f"/v1/scenarios/{scenario['id']}/versions",
                json={"definition": changed, "change_note": "Clearer wording"},
            )
        )
        assert version["version"] == 2
        first = api.ok(api.get(f"/v1/scenarios/{scenario['id']}/versions/1"))
        assert first["definition"]["turns"][0]["content"] == "Hello"
        assert api.ok(api.get(f"/v1/scenarios/{scenario['id']}"))["latest_version"] == 2

    def test_dataset_fills_a_template_into_real_scenarios(self, api: Api) -> None:
        pack = api.starter_pack()
        template = api.ok(api.get("/v1/scenarios", params={"tag": "template"}))["items"][0]
        created = api.ok(
            api.post(
                f"/v1/scenarios/{template['id']}/variations",
                json={"count": 3, "dataset_id": pack["dataset_id"], "strategies": []},
            )
        )["created"]
        assert len(created) == 3
        child = api.ok(api.get(f"/v1/scenarios/{created[0]['id']}"))
        turn = child["current"]["definition"]["turns"][0]["content"]
        assert "{{" not in turn
        assert "Arta Krasniqi" in turn

    def test_generated_variations_are_runnable(self, api: Api) -> None:
        api.starter_pack()
        base = api.ok(api.get("/v1/scenarios", params={"q": "Order status lookup"}))["items"][0]
        created = api.ok(
            api.post(f"/v1/scenarios/{base['id']}/variations", json={"count": 4, "strategies": ["polite", "urgent"]})
        )["created"]
        bot = api.create_agent("Variation Bot")
        run = api.ok(
            api.post("/v1/runs", json={"agent_id": bot["id"], "scenario_ids": [item["id"] for item in created]})
        )
        finished = api.wait_for_run(run["id"])
        assert finished["progress"]["passed"] == 4

    def test_starter_pack_is_safe_to_install_twice(self, api: Api) -> None:
        first = api.starter_pack()
        second = api.starter_pack()
        assert second["scenarios_created"] == 0
        assert second["suite_id"] == first["suite_id"]
