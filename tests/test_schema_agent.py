"""Schema registry, connector selection, utilization plans, and the schema agent."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

import pytest
import urllib.request
import yaml
from fastapi.testclient import TestClient

from midplane.agents.plan import plan_utilization
from midplane.agents.schema_agent import AGENT_NAME, ROSTER_NAME, run_schema_agent
from midplane.connectors import (
    ConnectorConstraints,
    list_connectors,
    load_connector_registry,
    select_connectors,
    without_real_money,
)
from midplane.gateway.app import create_app, load_roster
from midplane.gateway.keys import mint_key
from midplane.graph import resolve_worker, run_review
from midplane.models import FakeModelProvider
from midplane.schema_registry import lookup_schema, search_schemas
from midplane.schema_registry.build import load_schema_registry
from midplane.state import NODE_CONTRACTS, MidPlaneState
from midplane.store import EPISODE_COLUMNS, Episode, SqliteEpisodeStore
from tests.conftest import SAMPLE_DIFF

PEPPER = "test-pepper"
_METHODS = {"get", "post", "put", "patch", "delete"}


def _end_nodes(trace: list[dict]) -> list[str]:
    return [event["node"] for event in trace if event.get("event") == "end"]


def _openapi_type(prop: dict) -> tuple[str, bool]:
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1], False
    typ = prop.get("type", "object")
    nullable = False
    if isinstance(typ, list):
        nullable = "null" in typ
        rest = [item for item in typ if item != "null"]
        base = str(rest[0]) if rest else "object"
    else:
        base = str(typ)
    if "const" in prop and base == "string":
        return f"enum[{prop['const']}]", nullable
    if "enum" in prop and base == "string":
        return "enum[" + ",".join(str(item) for item in prop["enum"]) + "]", nullable
    return base, nullable


def _sql_columns() -> dict[str, bool]:
    """column -> nullable, read straight from the episode migrations."""

    found: dict[str, bool] = {}
    create = Path("migrations/001_episodes.sql").read_text()
    body = create.split("(", 1)[1].rsplit(")", 1)[0]
    depth = 0
    quote = None
    buf: list[str] = []
    parts: list[str] = []
    for char in body:
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            buf.append(char)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    if buf:
        parts.append("".join(buf))
    for part in parts:
        text = " ".join(part.split())
        name = text.split(" ", 1)[0]
        if name.lower() in {"check", "primary", "unique", "constraint", "foreign"}:
            continue
        lowered = text.lower()
        found[name] = "not null" not in lowered and "primary key" not in lowered
    alters = Path("migrations/003_episode_indexes.sql").read_text()
    for match in re.finditer(
        r"add\s+column\s+(?:if\s+not\s+exists\s+)?(\w+)\s+([^;]+);",
        alters,
        flags=re.IGNORECASE,
    ):
        clause = match.group(2).lower()
        found[match.group(1)] = "not null" not in clause and "primary key" not in clause
    return found


def test_lookup_and_search_find_real_schemas():
    episode = lookup_schema("episode")
    assert episode is not None
    assert episode.domain == "episode"
    assert episode.version == "v0"
    assert episode.source_file == "migrations/001_episodes.sql"
    assert "episode_schema.sql" in episode.reconcile
    assert "episode_example.json" in episode.reconcile
    assert {field.name for field in episode.fields} == set(EPISODE_COLUMNS)
    assert all(field.nullable is False for field in episode.fields)

    exported = lookup_schema("export.jsonl")
    assert exported is not None
    assert "dev_guild_prompt_templates.md" in exported.reconcile
    assert "episode_schema.sql" in exported.reconcile
    model_choice = next(field for field in exported.fields if field.name == "model_choice")
    assert model_choice.nullable is True
    assert model_choice.type == "object"

    trading = lookup_schema("trading.control.result")
    assert trading is not None
    assert {field.name for field in trading.fields} == {
        "allowed",
        "mode",
        "executed",
        "broker_called",
        "reason",
    }
    assert "trading_control_service_spec.md" in trading.reconcile
    assert "trading_risk_mitigation.md" in trading.reconcile
    assert lookup_schema("does.not.exist") is None

    found = search_schemas("episode export quality_score")
    assert found
    assert found[0].id == "episode"
    assert any(entry.id == "export.jsonl" for entry in found)


def test_registry_matches_migrations_and_the_episode_model():
    entry = lookup_schema("episode")
    assert entry is not None
    sql_columns = _sql_columns()
    assert {field.name for field in entry.fields} == set(sql_columns)
    assert {field.name for field in fields(Episode)} == set(sql_columns)
    for field in entry.fields:
        assert field.nullable is sql_columns[field.name]
        assert field.source_file.startswith("migrations/")


def test_registry_matches_the_openapi_document():
    document = yaml.safe_load(Path("openapi/midplane-v0.yaml").read_text())
    version = document["info"]["version"]
    components = document["components"]["schemas"]
    for name, schema in components.items():
        if not isinstance(schema, dict) or schema.get("type") != "object":
            continue
        entry = lookup_schema(f"gateway.component.{name}")
        assert entry is not None, name
        assert entry.version == version
        assert entry.source_file == "openapi/midplane-v0.yaml"
        required = set(schema.get("required") or [])
        got = {field.name: field for field in entry.fields}
        assert set(got) == set(schema.get("properties") or {})
        for field_name, prop in (schema.get("properties") or {}).items():
            expected_type, expected_null = _openapi_type(prop)
            field = got[field_name]
            assert field.type == expected_type
            assert field.nullable is expected_null
            assert field.required is (field_name in required)

    error = lookup_schema("gateway.error")
    assert error is not None
    inner = components["Error"]["properties"]["error"]
    inner_required = set(inner.get("required") or [])
    assert {field.name for field in error.fields} == set(inner["properties"])
    for field in error.fields:
        expected_type, expected_null = _openapi_type(inner["properties"][field.name])
        assert field.type == expected_type
        assert field.nullable is expected_null
        assert field.required is (field.name in inner_required)
    assert {field.name for field in error.fields} >= {"code", "message", "request_id"}

    event_enum = components["RunEvent"]["properties"]["event"]["enum"]
    for event_name in event_enum:
        sse = lookup_schema(f"gateway.sse.{event_name}")
        assert sse is not None
        assert sse.fields

    for route, path_item in document["paths"].items():
        for method, operation in path_item.items():
            if method not in _METHODS:
                continue
            entry = lookup_schema(f"gateway.endpoint.{method.upper()} {route}")
            assert entry is not None
            names = {field.name for field in entry.fields}
            for param in operation.get("parameters") or []:
                assert param["name"] in names


def test_state_and_node_contracts_match_the_graph(store: SqliteEpisodeStore):
    from midplane.graph import GraphDeps, build_graph
    from midplane.models import FakeModelProvider
    from midplane.routing import DEFAULT_BUDGET, DEFAULT_ROUTE_TABLE, CostLatencyRoutingPolicy
    from typing import get_type_hints

    state = lookup_schema("midplane.state")
    assert state is not None
    assert {field.name for field in state.fields} == set(get_type_hints(MidPlaneState))
    outcome = next(field for field in state.fields if field.name == "outcome")
    assert outcome.nullable is True
    assert "langgraph_state_management.md" in state.reconcile

    graph = build_graph(
        GraphDeps(
            provider=FakeModelProvider(),
            store=store,
            policy=CostLatencyRoutingPolicy(),
            route_table=DEFAULT_ROUTE_TABLE,
            budget=DEFAULT_BUDGET,
        )
    )
    node_names = set(graph.get_graph().nodes)
    for name in NODE_CONTRACTS:
        assert name in node_names
        for direction in ("reads", "writes"):
            entry = lookup_schema(f"midplane.node.{name}.{direction}")
            assert entry is not None
            assert entry.fields


def test_connector_selection_filters_writes_and_refuses_real_money():
    quiet = select_connectors("read the episode schema")
    selected = {item.name: item for item in quiet.selected}
    assert "episode_store" in selected
    assert "upsert" not in {cap.id for cap in selected["episode_store"].allowed_capabilities}
    assert "upsert" in {cap.id for cap in selected["episode_store"].excluded_capabilities}
    assert "gmail" in selected
    assert "send" not in {cap.id for cap in selected["gmail"].allowed_capabilities}
    assert "trading_control" not in selected
    assert any(item.name == "trading_control" for item in quiet.excluded)
    assert all(item.real_money is False for item in quiet.selected)
    assert all(item.reasons for item in quiet.selected)
    assert all(cap.effect == "read" for item in quiet.selected for cap in item.allowed_capabilities)

    writes = select_connectors("persist an episode", ConnectorConstraints(allow_writes=True))
    store = next(item for item in writes.selected if item.name == "episode_store")
    assert "upsert" in {cap.id for cap in store.allowed_capabilities}
    assert "trading_control" not in {item.name for item in writes.selected}

    paper = select_connectors(
        "design a paper trading research loop",
        ConnectorConstraints(allow_side_effects=True),
    )
    trading = next(item for item in paper.selected if item.name == "trading_control")
    assert trading.real_money is False
    assert any("paper-only" in reason for reason in trading.reasons)
    assert any("hard external controls" in reason for reason in trading.reasons)

    live = select_connectors(
        "place a live real-money trade",
        ConnectorConstraints(allow_side_effects=True),
    )
    assert all(item.name != "trading_control" for item in live.selected)
    refused = next(item for item in live.excluded if item.name == "trading_control")
    assert any("never selected" in reason for reason in refused.reasons)

    seeded = next(item for item in load_connector_registry() if item.name == "trading_control")
    assert seeded.status == "planned"
    assert seeded.real_money is False
    evil = select_connectors(
        "use the trading control",
        ConnectorConstraints(allow_side_effects=True),
        connectors=(without_real_money(seeded),),
    )
    assert evil.selected == ()
    assert "real_money" in evil.excluded[0].reasons[0]


def test_utilization_plans_are_diverse_and_safe():
    plans = plan_utilization("how do episode schemas connect to the gateway and its connectors")
    assert 2 <= len(plans) <= 4
    assert len({plan.id for plan in plans}) == len(plans)
    assert len({plan.primary_connector for plan in plans}) == len(plans)
    known_schemas = {entry.id for entry in load_schema_registry().entries}
    known_connectors = {item.name for item in list_connectors()}
    ids = {plan.id for plan in plans}
    assert "read-episode-flywheel" in ids
    assert "gateway-contract-trace" in ids
    for plan in plans:
        assert plan.connectors
        assert set(plan.connectors) <= known_connectors
        assert set(plan.schemas) <= known_schemas
        assert len(plan.steps) >= 2
        assert plan.data_flow
        assert plan.risks
        assert plan.episodes
        assert "trading_control" not in plan.connectors
        for episode in plan.episodes:
            assert episode.source == "dev_guild"
            assert episode.agent == AGENT_NAME
        for flow in plan.data_flow:
            source = lookup_schema(flow.from_schema)
            assert source is not None
            assert set(flow.fields) <= {field.name for field in source.fields}
            assert lookup_schema(flow.to_schema) is not None
        allowed = select_connectors(plan.summary)
        chosen = {item.name: item for item in allowed.selected}
        for step in plan.steps:
            assert step.schema_id in known_schemas
            caps = {cap.id: cap.effect for cap in chosen[step.connector].allowed_capabilities}
            assert caps[step.capability] == "read"

    live = plan_utilization(
        "place a live real-money trade",
        ConnectorConstraints(allow_side_effects=True),
    )
    assert any(plan.id == "refuse-real-money-trading" for plan in live)
    assert all("trading_control" not in plan.connectors for plan in live)

    paper = plan_utilization(
        "design a paper trading research loop",
        ConnectorConstraints(allow_side_effects=True),
    )
    boundary = next(plan for plan in paper if plan.id == "paper-control-boundary")
    assert boundary.connectors == ("trading_control",)
    text = " ".join(boundary.risks).lower()
    assert "real_money" in text
    assert "hard external controls" in text
    assert all(step.capability == "submit_paper" for step in boundary.steps)


def test_questions_route_to_the_schema_agent(store: SqliteEpisodeStore, monkeypatch):
    def forbid_network(*_args, **_kwargs):
        raise AssertionError("the fake provider must not open a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", forbid_network)
    provider = FakeModelProvider()
    result = run_schema_agent(
        "Which connectors can read the episode schema?",
        store=store,
        provider=provider,
        run_id="run-schema",
    )
    assert provider.calls == ["fake-small"]
    assert _end_nodes(result.episode.trace) == [
        "supervisor",
        "router",
        "schema_agent",
        "episode_writer",
    ]
    assert result.episode.source == "dev_guild"
    assert result.episode.outcome == "success"
    assert result.episode.inputs["agent"] == AGENT_NAME
    assert result.episode.plan["agent"] == AGENT_NAME
    assert result.episode.outputs["agent"] == AGENT_NAME
    assert result.episode.outputs["prompt_version"] == "v0"
    assert result.episode.quality_score == 0.99
    assert store.count("run-schema") == 1
    tools = [call["tool"] for call in result.episode.tool_calls]
    assert tools[:1] == ["search_schemas"]
    assert "list_connectors" in tools
    assert "select_connectors" in tools
    assert "plan_utilization" in tools
    assert 2 <= len(result.episode.outputs["strategies"]) <= 4

    routed = run_review(
        "",
        role="auto",
        instructions="Explain the schema connector integration",
        store=store,
        provider=FakeModelProvider(),
        run_id="run-question",
    )
    assert routed.episode.plan["steps"] == ["schema_agent"]
    assert routed.episode.outputs["agent"] == AGENT_NAME
    assert resolve_worker({"role": "code_review", "instructions": "schema"}) == "code_review"


def test_unparseable_model_text_still_uses_the_tools(store: SqliteEpisodeStore):
    provider = FakeModelProvider(responses={"fake-small": "not json"})
    result = run_schema_agent("List gateway SSE events", store=store, provider=provider, run_id="run-text")
    assert provider.calls == ["fake-small"]
    assert result.episode.outcome == "success"
    assert result.episode.outputs["strategies"]


def test_schema_agent_falls_back_when_the_primary_model_fails(store: SqliteEpisodeStore):
    provider = FakeModelProvider(fail_models={"fake-small"})
    result = run_schema_agent("Explain the export schema", store=store, provider=provider, run_id="run-fb")
    assert provider.calls == ["fake-small", "fake-large"]
    assert result.episode.outcome == "success"
    roles = [item.get("role") for item in result.episode.model_choices if item.get("kind") == "attempt"]
    assert roles == ["primary", "fallback"]


def test_code_review_diff_still_stays_on_code_review(store: SqliteEpisodeStore):
    result = run_review(SAMPLE_DIFF, store=store, run_id="run-still-review")
    assert result.episode.plan["steps"] == ["code_review"]
    assert "schema_agent" not in _end_nodes(result.episode.trace)


@pytest.fixture
def gateway_app(tmp_path):
    database = f"sqlite:///{tmp_path / 'gateway.sqlite'}"
    app = create_app(database_url=database, rate_limit_per_minute=1000, pepper=PEPPER)
    yield app
    app.state.gateway.close()
    app.state.episodes.close()


def test_gateway_lists_and_runs_the_schema_agent(gateway_app):
    client = TestClient(gateway_app)
    token, _record = mint_key(gateway_app.state.gateway, "ios-dev", PEPPER)
    headers = {"Authorization": f"Bearer {token}"}
    roster = client.get("/v1/agents", headers=headers, params={"limit": 100})
    assert roster.status_code == 200
    names = [item["name"] for item in roster.json()["data"]]
    assert ROSTER_NAME in names
    assert names == [item["name"] for item in load_roster()]

    created = client.post(
        "/v1/runs",
        headers=headers,
        json={"task_type": "schema_agent", "input": {"goal": "How is the error envelope shaped?"}},
    )
    assert created.status_code == 201
    run_id = created.json()["run_id"]
    with client.stream("GET", f"/v1/runs/{run_id}/events", headers=headers) as stream:
        payload = "".join(stream.iter_text())
    assert "schema_agent" in payload
    assert "run.completed" in payload
    body = client.get(f"/v1/runs/{run_id}", headers=headers).json()
    assert body["status"] == "succeeded"
    assert body["task_type"] == "schema_agent"
    assert body["result"]["agent"] == AGENT_NAME
    episode = client.get(f"/v1/episodes/{body['episode_id']}", headers=headers).json()
    assert episode["source"] == "dev_guild"
    assert episode["inputs"]["agent"] == AGENT_NAME

    by_agent = client.post(
        "/v1/runs",
        headers=headers,
        json={"agent": "schema-agent", "input": {"goal": "Which connectors are only planned?"}},
    )
    assert by_agent.status_code == 201
    other_id = by_agent.json()["run_id"]
    with client.stream("GET", f"/v1/runs/{other_id}/events", headers=headers) as stream:
        "".join(stream.iter_text())
    other = client.get(f"/v1/runs/{other_id}", headers=headers).json()
    assert other["status"] == "succeeded"
    assert other["task_type"] == "schema_agent"

    conflict = client.post(
        "/v1/runs",
        headers=headers,
        json={
            "task_type": "code_review",
            "agent": "schema-agent",
            "input": {"diff": SAMPLE_DIFF, "goal": "schemas"},
        },
    )
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "validation_error"

    missing = client.post("/v1/runs", headers=headers, json={"input": {"goal": "schemas"}})
    assert missing.status_code == 422

    served = gateway_app.openapi()
    task = served["components"]["schemas"]["CreateRunRequest"]["properties"]["task_type"]
    assert "null" in task["type"]
    assert "enum" not in task
    assert "schema_agent" in task["description"]
    assert "agent" in served["components"]["schemas"]["CreateRunRequest"]["properties"]
    assert "goal" in served["components"]["schemas"]["RunInput"]["properties"]
