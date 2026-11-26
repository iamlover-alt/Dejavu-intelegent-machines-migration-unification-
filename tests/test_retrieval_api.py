"""POST /v1/retrieve: auth, client scope, chain spec, error envelope."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from midplane.gateway.app import create_app
from midplane.gateway.keys import mint_key
from midplane.store import Episode
from tests.test_retrieval import NOW, _episode

PEPPER = "test-pepper"


@pytest.fixture
def gateway_app(tmp_path):
    database = f"sqlite:///{tmp_path / 'retrieve.sqlite'}"
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


def _own(gateway_app, client_id: str, episode: Episode) -> None:
    gateway_app.state.episodes.upsert(episode)
    gateway_app.state.gateway.insert_run(
        run_id=episode.run_id,
        client_id=client_id,
        client_ref=None,
        task_type="code_review",
        payload={"diff": "+x"},
    )
    gateway_app.state.gateway.finish_run(
        episode.run_id,
        status="succeeded",
        result={"summary": "ok"},
        episode_id=episode.run_id,
        error_message=None,
    )


def test_retrieve_requires_a_bearer_key(client: TestClient):
    response = client.post("/v1/retrieve", json={"store": "schema", "query": "episodes"})
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["request_id"]
    assert response.headers["X-Request-ID"] == body["error"]["request_id"]


def test_retrieve_scopes_episodes_to_the_client(client: TestClient, gateway_app):
    owner = _mint(gateway_app, "ios-dev")
    other = _mint(gateway_app, "ios-other")
    episode = _episode(
        "ep-billing",
        detail="billing retry idempotency key missing on the charge endpoint",
        created_at=NOW,
    )
    _own(gateway_app, "ios-dev", episode)
    response = client.post(
        "/v1/retrieve",
        headers=_auth(owner),
        json={"store": "episodes", "query": "billing retry idempotency key", "top_k": 5},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fused"] is False
    assert body["truncated"] is False
    assert body["data"]
    assert body["data"][0]["metadata"]["run_id"] == "ep-billing"
    assert body["data"][0]["breakdown"]["final"] == body["data"][0]["score"]
    assert "knn" in body["data"][0]["breakdown"]
    assert body["data"][0]["fusion_score"] is None
    assert body["hops"][0]["store"] == "episodes"
    hidden = client.post(
        "/v1/retrieve",
        headers=_auth(other),
        json={"store": "episodes", "query": "billing retry idempotency key", "top_k": 5},
    )
    assert hidden.status_code == 200
    assert hidden.json()["data"] == []


def test_retrieve_schema_reports_paper_only_trading(client: TestClient, gateway_app):
    token = _mint(gateway_app)
    response = client.post(
        "/v1/retrieve",
        headers=_auth(token),
        json={"store": "schema", "query": "trading-control paper-only real_money", "top_k": 3},
    )
    assert response.status_code == 200, response.text
    hit = response.json()["data"][0]
    assert hit["doc_id"] == "connector:trading-control"
    assert hit["metadata"]["paper_only"] is True
    assert hit["metadata"]["real_money"] is False


def test_retrieve_chain_hops_and_rejects_a_bad_filter(client: TestClient, gateway_app):
    token = _mint(gateway_app)
    episode = _episode(
        "ep-boundary",
        detail="review outcome for trading-control broker_called false",
        created_at=NOW,
    )
    _own(gateway_app, "ios-dev", episode)
    response = client.post(
        "/v1/retrieve",
        headers=_auth(token),
        json={
            "store": "chain",
            "query": "trading control service paper-only",
            "top_k": 3,
            "chain": [
                {"store": "schema", "top_k": 2},
                {"store": "episodes", "use_previous": True, "top_k": 2},
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fused"] is True
    assert [hop["store"] for hop in body["hops"]] == ["schema", "episodes"]
    assert "trading-control" in body["hops"][1]["query"]
    assert any(item["doc_id"] == "connector:trading-control" for item in body["data"])
    assert any(item["metadata"].get("run_id") == "ep-boundary" for item in body["data"])
    invalid = client.post(
        "/v1/retrieve",
        headers=_auth(token),
        json={"store": "episodes", "query": "billing", "filters": {"broker": "alpaca"}},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert invalid.json()["error"]["request_id"]
    missing = client.post(
        "/v1/retrieve",
        headers=_auth(token),
        json={"store": "chain", "query": "episodes"},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "validation_error"


def test_retrieve_trace_is_client_scoped(client: TestClient, gateway_app):
    owner = _mint(gateway_app, "ios-dev")
    other = _mint(gateway_app, "ios-other")
    episode = _episode("ep-trace", detail="model routing note", created_at=NOW)
    _own(gateway_app, "ios-dev", episode)
    gateway_app.state.retrieval.traces.record(
        agent="router",
        run_id="ep-trace",
        decision_point="model_routing",
        options=["fake-small"],
        choice="fake-small",
        rationale="model routing chose fake-small for the code review",
        outcome="success",
        outcome_score=0.9,
    )
    response = client.post(
        "/v1/retrieve",
        headers=_auth(owner),
        json={"store": "traces", "query": "model routing fake-small", "top_k": 3},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]
    assert response.json()["data"][0]["metadata"]["run_id"] == "ep-trace"
    hidden = client.post(
        "/v1/retrieve",
        headers=_auth(other),
        json={"store": "traces", "query": "model routing fake-small", "top_k": 3},
    )
    assert hidden.json()["data"] == []
