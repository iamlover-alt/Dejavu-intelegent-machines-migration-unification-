from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from midplane.gateway.app import create_app, load_roster
from midplane.gateway.ddl import POSTGRES_GATEWAY_DDL
from midplane.gateway.keys import hash_token, mint_key
from midplane.gateway.store import GatewayStore
from tests.conftest import SAMPLE_DIFF

PEPPER = "test-pepper"


@pytest.fixture
def gateway_app(tmp_path):
    database = f"sqlite:///{tmp_path / 'gateway.sqlite'}"
    app = create_app(database_url=database, rate_limit_per_minute=1000, pepper=PEPPER)
    yield app
    app.state.gateway.close()
    app.state.episodes.close()
    app.state.retrieval.close()


@pytest.fixture
def client(gateway_app):
    return TestClient(gateway_app)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mint(gateway_app, client_id: str = "ios-dev") -> str:
    token, _record = mint_key(gateway_app.state.gateway, client_id, PEPPER)
    return token


def test_healthz_has_no_auth_and_sets_a_request_id(client: TestClient):
    response = client.get("/healthz", headers={"X-Request-ID": "req_fixed"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "req_fixed"


def test_runs_require_a_bearer_key(client: TestClient):
    response = client.post("/v1/runs", json={"task_type": "code_review", "input": {"diff": SAMPLE_DIFF}})
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["request_id"]
    assert response.headers["X-Request-ID"] == body["error"]["request_id"]


def test_revoked_key_is_rejected(gateway_app, client: TestClient):
    token, record = mint_key(gateway_app.state.gateway, "ios-dev", PEPPER)
    assert gateway_app.state.gateway.revoke_key(record.key_id)
    response = client.get("/v1/agents", headers=_auth(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_code_review_run_persists_an_episode_and_streams_nodes(client: TestClient, gateway_app):
    token = _mint(gateway_app)
    created = client.post(
        "/v1/runs",
        headers=_auth(token),
        json={
            "task_type": "code_review",
            "client_ref": "draft-1",
            "input": {"diff": SAMPLE_DIFF, "repo": "demo", "instructions": "Look for bugs."},
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "queued"
    run_id = body["run_id"]

    with client.stream("GET", f"/v1/runs/{run_id}/events", headers=_auth(token)) as stream:
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        payload = "".join(stream.iter_text())
    frames = _frames(payload)
    assert [frame["id"] for frame in frames] == list(range(1, len(frames) + 1))
    assert frames[0]["event"] == "run.started"
    completed = [frame["data"]["node"] for frame in frames if frame["event"] == "node.completed"]
    assert completed == ["supervisor", "router", "code_review", "episode_writer"]
    assert frames[-1]["event"] == "run.completed"
    episode_id = frames[-1]["data"]["episode_id"]

    run = client.get(f"/v1/runs/{run_id}", headers=_auth(token))
    assert run.status_code == 200
    run_body = run.json()
    assert run_body["status"] == "succeeded"
    assert run_body["episode_id"] == episode_id
    assert run_body["client_ref"] == "draft-1"
    assert run_body["result"]["findings"]
    assert run_body["error"] is None

    episode = client.get(f"/v1/episodes/{episode_id}", headers=_auth(token))
    assert episode.status_code == 200
    episode_body = episode.json()
    assert episode_body["episode_id"] == episode_id
    assert episode_body["source"] == "app"
    assert episode_body["quality_score"] == 0.99
    assert episode_body["score_version"] == "heuristic-v0"
    assert episode_body["created_at"]
    assert {event["node"] for event in episode_body["trace"]} >= {
        "supervisor",
        "router",
        "code_review",
        "episode_writer",
    }
    assert episode_body["inputs"]["repo"] == "demo"


def _frames(payload: str) -> list[dict]:
    frames = []
    for block in payload.strip().split("\n\n"):
        if not block.strip():
            continue
        event_id = None
        event_name = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("id: "):
                event_id = int(line.removeprefix("id: "))
            elif line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        frames.append({"id": event_id, "event": event_name, "data": json.loads(data)})
    return frames


def test_event_stream_resumes_after_last_event_id(client: TestClient, gateway_app):
    token = _mint(gateway_app)
    created = client.post(
        "/v1/runs",
        headers=_auth(token),
        json={"task_type": "code_review", "input": {"diff": SAMPLE_DIFF}},
    )
    run_id = created.json()["run_id"]
    with client.stream("GET", f"/v1/runs/{run_id}/events", headers=_auth(token)) as stream:
        full = _frames("".join(stream.iter_text()))
    assert full[-1]["event"] == "run.completed"
    midpoint = full[2]["id"]
    with client.stream(
        "GET",
        f"/v1/runs/{run_id}/events",
        headers={**_auth(token), "Last-Event-ID": str(midpoint)},
    ) as stream:
        resumed = _frames("".join(stream.iter_text()))
    assert [frame["id"] for frame in resumed] == [frame["id"] for frame in full if frame["id"] > midpoint]
    with client.stream(
        "GET",
        f"/v1/runs/{run_id}/events",
        headers={**_auth(token), "Last-Event-ID": "99999"},
    ) as stream:
        replayed = _frames("".join(stream.iter_text()))
    assert [frame["id"] for frame in replayed] == [frame["id"] for frame in full]
    with client.stream(
        "GET",
        f"/v1/runs/{run_id}/events",
        headers={**_auth(token), "Last-Event-ID": "not-an-id"},
    ) as stream:
        again = _frames("".join(stream.iter_text()))
    assert [frame["id"] for frame in again] == [frame["id"] for frame in full]


def test_nullable_contract_uses_type_arrays(gateway_app):
    schema = gateway_app.openapi()
    events = schema["paths"]["/v1/runs/{run_id}/events"]["get"]["responses"]["200"]
    assert "text/event-stream" in events["content"]
    assert "application/json" not in events["content"]
    names = schema["components"]["schemas"]
    for name in (
        "RunStartedData",
        "NodeStartedData",
        "NodeCompletedData",
        "RunCompletedData",
        "RunFailedData",
        "RunEvent",
    ):
        assert name in names
    status = names["RunStatusResponse"]
    for field in ("result", "episode_id", "client_ref", "error"):
        assert "null" in status["properties"][field]["type"]
        assert "anyOf" not in status["properties"][field]
        assert field not in status["required"]
    for schema_name in ("AgentPage", "EpisodePage"):
        page = names[schema_name]
        assert "null" in page["properties"]["next_cursor"]["type"]
        assert "next_cursor" not in page["required"]
    created = names["CreateRunRequest"]
    assert "null" in created["properties"]["client_ref"]["type"]
    assert "client_ref" not in created.get("required", [])
    for path in ("/v1/agents", "/v1/episodes"):
        parameters = schema["paths"][path]["get"]["parameters"]
        cursor_schema = next(item["schema"] for item in parameters if item["name"] == "cursor")
        assert "null" in cursor_schema["type"]
        assert "anyOf" not in cursor_schema
    dumped = json.dumps(schema)
    assert '{"type": "null"}' not in dumped
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "ValidationError" not in schema["components"]["schemas"]
    error_ref = {"$ref": "#/components/schemas/Error"}
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            names = [item.get("name") for item in operation.get("parameters", [])]
            assert "X-Request-ID" in names
            for status, response in operation["responses"].items():
                header_names = set(response.get("headers", {}))
                assert "X-Request-ID" in header_names
                if status.startswith(("4", "5")):
                    assert response["content"]["application/json"]["schema"] == error_ref
                if operation.get("security"):
                    assert {"RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"} <= header_names
            if operation.get("security"):
                denied = operation["responses"]["429"]
                assert "Retry-After" in denied["headers"]
                assert denied["content"]["application/json"]["schema"] == error_ref


def test_episode_list_is_filtered_and_scoped(client: TestClient, gateway_app):
    token = _mint(gateway_app)
    other = _mint(gateway_app, "ios-other")
    created = client.post(
        "/v1/runs",
        headers=_auth(token),
        json={"task_type": "code_review", "input": {"diff": SAMPLE_DIFF}},
    )
    run_id = created.json()["run_id"]
    with client.stream("GET", f"/v1/runs/{run_id}/events", headers=_auth(token)) as stream:
        "".join(stream.iter_text())
    listed = client.get("/v1/episodes", headers=_auth(token), params={"source": "app", "min_score": 0.5})
    assert listed.status_code == 200
    body = listed.json()
    assert body["limit"] == 50
    assert [item["episode_id"] for item in body["data"]] == [run_id]
    assert body["data"][0]["score_version"] == "heuristic-v0"
    assert body["next_cursor"] is None
    hidden = client.get("/v1/episodes", headers=_auth(other), params={"source": "app"})
    assert hidden.json()["data"] == []
    bad = client.get("/v1/episodes", headers=_auth(token), params={"cursor": "%%%"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_cursor"


def test_other_clients_cannot_read_the_run(client: TestClient, gateway_app):
    owner = _mint(gateway_app, "ios-dev")
    other = _mint(gateway_app, "ios-other")
    created = client.post(
        "/v1/runs",
        headers=_auth(owner),
        json={"task_type": "code_review", "input": {"diff": SAMPLE_DIFF}},
    )
    run_id = created.json()["run_id"]
    with client.stream("GET", f"/v1/runs/{run_id}/events", headers=_auth(owner)) as stream:
        "".join(stream.iter_text())
    hidden = client.get(f"/v1/runs/{run_id}", headers=_auth(other))
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "not_found"
    episode_id = client.get(f"/v1/runs/{run_id}", headers=_auth(owner)).json()["episode_id"]
    assert client.get(f"/v1/episodes/{episode_id}", headers=_auth(other)).status_code == 404


def test_unknown_task_type_uses_the_error_shape(client: TestClient, gateway_app):
    token = _mint(gateway_app)
    response = client.post(
        "/v1/runs",
        headers=_auth(token),
        json={"task_type": "trade", "input": {"diff": SAMPLE_DIFF}},
    )
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["request_id"]
    assert response.headers["x-request-id"] == body["error"]["request_id"]
    details = body["error"]["details"]
    assert any(item["field"] == "task_type" and item["issue"] for item in details)


def test_agents_roster_paginates_in_order(client: TestClient, gateway_app):
    token = _mint(gateway_app)
    first = client.get("/v1/agents", headers=_auth(token), params={"limit": 4})
    assert first.status_code == 200
    page = first.json()
    assert page["limit"] == 4
    titles = [item["title"] for item in page["data"]]
    assert titles == ["Bot Builder", "Storefront", "Front Desk", "Grok Bot"]
    assert page["next_cursor"]
    second = client.get("/v1/agents", headers=_auth(token), params={"limit": 4, "cursor": page["next_cursor"]})
    more = [item["title"] for item in second.json()["data"]]
    assert more[0] == "Money Desk"
    whole = client.get("/v1/agents", headers=_auth(token), params={"limit": 100})
    assert [item["title"] for item in whole.json()["data"]] == [item["title"] for item in load_roster()]
    assert whole.json()["next_cursor"] is None
    bad = client.get("/v1/agents", headers=_auth(token), params={"cursor": "not-a-cursor"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_cursor"


def test_rate_limit(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'limited.sqlite'}",
        rate_limit_per_minute=2,
        pepper=PEPPER,
    )
    token, _record = mint_key(app.state.gateway, "ios-dev", PEPPER)
    client = TestClient(app)
    headers = _auth(token)
    assert client.get("/v1/agents", headers=headers).status_code == 200
    assert client.get("/v1/agents", headers=headers).status_code == 200
    blocked = client.get("/v1/agents", headers=headers)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert "details" not in blocked.json()["error"]
    assert blocked.headers["retry-after"] == blocked.headers["ratelimit-reset"]
    assert int(blocked.headers["ratelimit-reset"]) >= 1
    assert blocked.headers["ratelimit-limit"] == "2"
    assert blocked.headers["ratelimit-remaining"] == "0"
    allowed = client.get("/healthz")
    assert "ratelimit-limit" not in {name.lower() for name in allowed.headers}
    app.state.gateway.close()
    app.state.episodes.close()
    app.state.retrieval.close()


def test_trading_paths_are_absent(client: TestClient, gateway_app):
    response = client.get("/v1/trading")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["request_id"]


def test_openapi_has_no_trading_paths(gateway_app):
    paths = gateway_app.openapi()["paths"]
    assert set(paths) == {
        "/healthz",
        "/v1/runs",
        "/v1/runs/{run_id}",
        "/v1/runs/{run_id}/events",
        "/v1/episodes",
        "/v1/episodes/{episode_id}",
        "/v1/agents",
        "/v1/retrieve",
    }
    assert all("broker" not in path and "trading" not in path for path in paths)


def test_openapi_file_matches_the_served_schema(gateway_app):
    document = yaml.safe_load(Path("openapi/midplane-v0.yaml").read_text())
    assert document == gateway_app.openapi()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == "0.1.0"


def test_gateway_migration_matches_the_packaged_ddl():
    text = Path("migrations/002_gateway.sql").read_text()
    assert "RECONCILE with integration_guide.md" in text
    assert POSTGRES_GATEWAY_DDL.strip() in text
    assert "api_keys" in text
    assert "runs" in text


def test_hash_changes_with_the_pepper():
    assert hash_token("mp_example", "one") != hash_token("mp_example", "two")


def test_cli_mints_and_revokes(tmp_path, capsys, monkeypatch):
    from midplane.gateway.keys import main

    monkeypatch.delenv("MIDPLANE_KEY_PEPPER", raising=False)
    database = f"sqlite:///{tmp_path / 'keys.sqlite'}"
    assert main(["--database", database, "mint", "--client", "ios-dev"]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    token = printed[0]
    key_id = printed[1].split("=", 1)[1]
    store = GatewayStore(database)
    assert store.find_active_key(hash_token(token)) is not None
    store.close()
    assert main(["--database", database, "revoke", "--key-id", key_id]) == 0
    store = GatewayStore(database)
    assert store.find_active_key(hash_token(token)) is None
    store.close()
