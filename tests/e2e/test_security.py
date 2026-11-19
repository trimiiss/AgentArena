"""Authentication, roles and organization isolation against the running services."""

from __future__ import annotations

import base64
import concurrent.futures
import json
import uuid

import httpx
import pytest
from conftest import BASE_URL, Api, url_for


@pytest.fixture(scope="module")
def tenants() -> dict:
    """Organization A owns real data. Organization B tries to reach it."""
    owner = Api().register("tenant-a")
    other = Api().register("tenant-b")
    pack = owner.starter_pack()
    agent = owner.create_agent("Isolated Bot")
    run = owner.start_run(agent["id"], pack["suite_id"])
    owner.wait_for_run(run["id"])
    owner.wait_for_benchmark(run["id"])
    executions = owner.ok(owner.get(f"/v1/runs/{run['id']}/executions"))["items"]
    scenarios = owner.ok(owner.get("/v1/scenarios"))["items"]
    return {
        "owner": owner,
        "other": other,
        "agent": agent,
        "pack": pack,
        "run": run,
        "execution": executions[0],
        "scenario": scenarios[0],
    }


def resource_paths(data: dict) -> list[str]:
    run_id = data["run"]["id"]
    agent_id = data["agent"]["id"]
    return [
        f"/v1/agents/{agent_id}",
        f"/v1/agents/{agent_id}/versions",
        f"/v1/agents/{agent_id}/versions/1",
        f"/v1/scenarios/{data['scenario']['id']}",
        f"/v1/scenarios/{data['scenario']['id']}/versions",
        f"/v1/suites/{data['pack']['suite_id']}",
        f"/v1/suites/{data['pack']['suite_id']}/resolve",
        f"/v1/datasets/{data['pack']['dataset_id']}",
        f"/v1/runs/{run_id}",
        f"/v1/runs/{run_id}/executions",
        f"/v1/runs/{run_id}/stream",
        f"/v1/executions/{data['execution']['id']}",
        f"/v1/evaluations/executions/{data['execution']['id']}",
        f"/v1/benchmarks/runs/{run_id}",
        f"/v1/benchmarks/runs/{run_id}/report",
    ]


def test_owner_can_read_everything(tenants: dict) -> None:
    for path in resource_paths(tenants):
        if path.endswith("/stream"):
            continue
        assert tenants["owner"].get(path).status_code == 200, path


def test_other_organization_gets_not_found_everywhere(tenants: dict) -> None:
    """404, not 403, so another tenant cannot even learn that an id exists."""
    for path in resource_paths(tenants):
        assert tenants["other"].get(path).status_code == 404, path


def test_lists_only_show_your_own_data(tenants: dict) -> None:
    other = tenants["other"]
    for path in ("/v1/agents", "/v1/scenarios", "/v1/suites", "/v1/datasets", "/v1/runs", "/v1/benchmarks"):
        assert other.ok(other.get(path))["total"] == 0, path
    assert other.ok(other.get("/v1/analytics/overview"))["executions"] == 0
    assert other.ok(other.get("/v1/analytics/agents")) == []


def test_cannot_change_another_organizations_data(tenants: dict) -> None:
    other = tenants["other"]
    agent_id = tenants["agent"]["id"]
    assert other.request("PATCH", f"/v1/agents/{agent_id}", json={"name": "taken"}).status_code == 404
    assert other.request("DELETE", f"/v1/agents/{agent_id}").status_code == 404
    assert other.post(f"/v1/runs/{tenants['run']['id']}/cancel").status_code == 404
    assert other.post(f"/v1/runs/{tenants['run']['id']}/replay").status_code == 404


def test_cannot_run_another_organizations_agent(tenants: dict) -> None:
    other = tenants["other"]
    own_pack = other.starter_pack()
    response = other.post("/v1/runs", json={"agent_id": tenants["agent"]["id"], "suite_id": own_pack["suite_id"]})
    assert response.status_code == 404


def test_cannot_use_another_organizations_suite(tenants: dict) -> None:
    other = tenants["other"]
    agent = other.create_agent("Own Bot")
    response = other.post("/v1/runs", json={"agent_id": agent["id"], "suite_id": tenants["pack"]["suite_id"]})
    assert response.status_code == 404


def test_cannot_compare_against_another_organizations_run(tenants: dict) -> None:
    response = tenants["other"].get(
        "/v1/comparisons/runs",
        params={"base_run_id": tenants["run"]["id"], "candidate_run_id": tenants["run"]["id"]},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/v1/agents", "/v1/scenarios", "/v1/runs", "/v1/benchmarks", "/v1/auth/me"])
def test_requests_without_a_token_are_rejected(path: str) -> None:
    assert httpx.get(url_for(path), timeout=5).status_code == 401


def _b64(value: dict | bytes) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_unsigned_token_is_rejected(api: Api) -> None:
    claims = {"sub": str(uuid.uuid4()), "org": api.org_id, "role": "owner", "email": "x@example.com"}
    forged = f"{_b64({'alg': 'none', 'kid': 'x'})}.{_b64(claims)}."
    response = httpx.get(url_for("/v1/agents"), headers={"Authorization": f"Bearer {forged}"}, timeout=5)
    assert response.status_code == 401


def test_tampered_token_is_rejected(api: Api) -> None:
    header, payload, signature = api.token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["org"] = str(uuid.uuid4())
    tampered = f"{header}.{_b64(claims)}.{signature}"
    response = httpx.get(url_for("/v1/agents"), headers={"Authorization": f"Bearer {tampered}"}, timeout=5)
    assert response.status_code == 401


def test_internal_routes_refuse_user_tokens(api: Api) -> None:
    agent = api.create_agent("Internal Bot")
    response = api.get(f"/v1/agents/{agent['id']}/versions/1")
    assert response.status_code == 200
    if not BASE_URL:  # The published service ports only exist on Compose.
        internal = api.http.get(
            f"http://127.0.0.1:8002/internal/v1/agents/{agent['id']}/versions/1",
            headers={"Authorization": f"Bearer {api.token}", "X-Org-Id": api.org_id},
        )
        assert internal.status_code == 401


class TestCredentials:
    secret = "agent-api-secret-value"

    def test_credentials_are_write_only(self, api: Api) -> None:
        agent = api.create_agent("Secret Bot", "secure")
        stored = api.ok(
            api.request(
                "PUT", f"/v1/agents/{agent['id']}/credential", json={"auth_type": "bearer", "secret": self.secret}
            )
        )
        assert "secret" not in stored
        for path in (f"/v1/agents/{agent['id']}", f"/v1/agents/{agent['id']}/versions", "/v1/agents"):
            response = api.get(path)
            assert response.status_code == 200
            assert self.secret not in response.text

    def test_secrets_cannot_go_in_plain_headers(self, api: Api) -> None:
        response = api.post(
            "/v1/agents",
            json={
                "name": "Bad Bot",
                "endpoint_url": "http://sample-agent:8000/agents/v2",
                "default_headers": {"Authorization": "Bearer oops"},
            },
        )
        assert response.status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:5432/",
        "http://localhost:8000/",
        "http://postgres:5432/",
        "http://rabbitmq:15672/",
        "file:///etc/passwd",
    ],
)
def test_internal_addresses_cannot_be_registered(api: Api, url: str) -> None:
    response = api.post("/v1/agents", json={"name": "SSRF Bot", "endpoint_url": url})
    assert response.status_code == 422


class TestRoles:
    def member_of(self, owner: Api, role: str) -> Api:
        user = Api().register(f"role-{role}")
        owner.ok(owner.post("/v1/organizations/current/members", json={"email": user.email, "role": role}))
        session = user.ok(user.post("/v1/auth/switch-organization", json={"organization_id": owner.org_id}))
        user.token = session["access_token"]
        return user

    def test_viewer_can_read_but_not_write(self, api: Api) -> None:
        pack = api.starter_pack()
        agent = api.create_agent("Role Bot")
        viewer = self.member_of(api, "viewer")
        assert viewer.get("/v1/agents").status_code == 200
        assert viewer.post("/v1/agents", json={"name": "No", "endpoint_url": "https://example.com"}).status_code == 403
        assert viewer.post("/v1/runs", json={"agent_id": agent["id"], "suite_id": pack["suite_id"]}).status_code == 403

    def test_member_can_write_but_not_administer(self, api: Api) -> None:
        member = self.member_of(api, "member")
        assert member.create_agent("Member Bot")["name"] == "Member Bot"
        assert member.request("PATCH", "/v1/organizations/current", json={"name": "Renamed"}).status_code == 403

    def test_admin_cannot_promote_to_owner(self, api: Api) -> None:
        admin = self.member_of(api, "admin")
        viewer = self.member_of(api, "viewer")
        members = api.ok(api.get("/v1/organizations/current/members"))
        user_id = next(m["user_id"] for m in members if m["email"] == viewer.email)
        response = admin.request("PATCH", f"/v1/organizations/current/members/{user_id}", json={"role": "owner"})
        assert response.status_code == 403

    def test_last_owner_cannot_leave(self, api: Api) -> None:
        members = api.ok(api.get("/v1/organizations/current/members"))
        response = api.request("DELETE", f"/v1/organizations/current/members/{members[0]['user_id']}")
        assert response.status_code == 409


class TestSessions:
    password = "correct-horse-battery"

    def login(self, email: str) -> httpx.Response:
        return httpx.post(url_for("/v1/auth/login"), json={"email": email, "password": self.password}, timeout=10)

    def test_login_returns_a_working_token(self, api: Api) -> None:
        response = self.login(api.email)
        assert response.status_code == 200
        me = httpx.get(
            url_for("/v1/auth/me"),
            headers={"Authorization": f"Bearer {response.json()['access_token']}"},
            timeout=5,
        )
        assert me.json()["user"]["email"] == api.email

    def test_wrong_password_and_unknown_user_look_identical(self, api: Api) -> None:
        wrong = httpx.post(url_for("/v1/auth/login"), json={"email": api.email, "password": "not-the-one"}, timeout=10)
        unknown = httpx.post(
            url_for("/v1/auth/login"),
            json={"email": f"{uuid.uuid4().hex}@example.com", "password": "not-the-one"},
            timeout=10,
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["detail"] == unknown.json()["detail"]

    def test_email_can_only_register_once(self, api: Api) -> None:
        response = httpx.post(
            url_for("/v1/auth/register"),
            json={
                "email": api.email,
                "password": self.password,
                "display_name": "Copy",
                "organization_name": "Copy org",
            },
            timeout=10,
        )
        assert response.status_code == 409

    def test_weak_password_is_rejected(self) -> None:
        response = httpx.post(
            url_for("/v1/auth/register"),
            json={
                "email": f"{uuid.uuid4().hex}@example.com",
                "password": "short",
                "display_name": "Weak",
                "organization_name": "Weak org",
            },
            timeout=10,
        )
        assert response.status_code == 422

    def test_refresh_cookie_is_protected(self, api: Api) -> None:
        cookie = self.login(api.email).headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=strict" in cookie

    def test_refresh_rotates_and_replay_ends_the_session(self, api: Api) -> None:
        first = self.login(api.email).cookies["aa_refresh"]
        refreshed = httpx.post(url_for("/v1/auth/refresh"), cookies={"aa_refresh": first}, timeout=5)
        assert refreshed.status_code == 200
        second = refreshed.cookies["aa_refresh"]
        assert second != first

        replayed = httpx.post(url_for("/v1/auth/refresh"), cookies={"aa_refresh": first}, timeout=5)
        assert replayed.status_code == 401
        after_replay = httpx.post(url_for("/v1/auth/refresh"), cookies={"aa_refresh": second}, timeout=5)
        assert after_replay.status_code == 401, "replaying a used token must revoke the family"

    def test_logout_revokes_the_refresh_token(self, api: Api) -> None:
        token = self.login(api.email).cookies["aa_refresh"]
        assert httpx.post(url_for("/v1/auth/logout"), cookies={"aa_refresh": token}, timeout=5).status_code == 204
        assert httpx.post(url_for("/v1/auth/refresh"), cookies={"aa_refresh": token}, timeout=5).status_code == 401

    def test_repeated_failed_logins_are_rate_limited(self, api: Api) -> None:
        def attempt(_: int) -> int:
            body = {"email": api.email, "password": "wrong-pass"}
            return httpx.post(url_for("/v1/auth/login"), json=body, timeout=20).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            codes = list(pool.map(attempt, range(40)))
        assert 429 in codes
        assert 401 in codes
