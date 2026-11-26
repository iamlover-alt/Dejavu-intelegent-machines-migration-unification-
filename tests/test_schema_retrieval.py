"""SchemaDocumentLoader and schema-agent retrieval behind MIDPLANE_RETRIEVAL.

The embedder is the local hashed n-gram embedder. The model is the fake
provider. Nothing in this module opens a network connection.
"""

from __future__ import annotations

import urllib.request

from midplane.agents.schema_agent import AGENT_NAME, run_schema_agent
from midplane.connectors import load_connector_registry
from midplane.models import FakeModelProvider
from midplane.prompts import render_schema_agent_prompt
from midplane.retrieval.embedder import HashedNgramEmbedder
from midplane.retrieval.index import InMemoryVectorIndex
from midplane.retrieval.schema_store import (
    SCHEMA_AGENT_TOP_K,
    SchemaConnectorStore,
    SchemaDocumentLoader,
)
from midplane.retrieval.traces import DecisionTraceStore
from midplane.schema_registry import load_schema_registry


GOAL = "Which connectors can read the episode schema?"


class RecordingProvider:
    def __init__(self) -> None:
        self.inner = FakeModelProvider()
        self.prompts: list[str] = []
        self.calls = self.inner.calls

    def complete(self, *, model_id: str, prompt: str):
        self.prompts.append(prompt)
        return self.inner.complete(model_id=model_id, prompt=prompt)


class ExplodingStore:
    def retrieve(self, *_args, **_kwargs):
        raise AssertionError("schema_agent retrieved while the flag was off")


def _forbid_network(*_args, **_kwargs):
    raise AssertionError("schema retrieval tests must not open a network connection")


def _end_nodes(trace: list) -> list[str]:
    return [event["node"] for event in trace if isinstance(event, dict) and event.get("event") == "end"]


def _schema_event(trace: list) -> dict:
    events = [
        event
        for event in trace
        if isinstance(event, dict) and event.get("node") == "schema_agent" and event.get("event") == "end"
    ]
    assert len(events) == 1
    return events[0]


def _stable(result) -> dict:
    return {
        "tools": [call["tool"] for call in result.episode.tool_calls],
        "strategies": [item["id"] for item in result.episode.outputs["strategies"]],
        "connectors": [item["name"] for item in result.episode.outputs["connectors"]],
        "agent": result.episode.outputs["agent"],
        "prompt_version": result.episode.outputs["prompt_version"],
    }


def _registry_store() -> SchemaConnectorStore:
    embedder = HashedNgramEmbedder()
    return SchemaConnectorStore(
        loader=SchemaDocumentLoader(),
        embedder=embedder,
        index=InMemoryVectorIndex(embedder.dimension),
    )


def test_loader_covers_the_schema_and_connector_registries(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    documents = SchemaDocumentLoader().load_documents()
    schemas = [item for item in documents if item.metadata["kind"] == "schema"]
    connectors = [item for item in documents if item.metadata["kind"] == "connector"]
    catalog = load_schema_registry()
    registry = load_connector_registry()
    assert {item.metadata["name"] for item in schemas} == {entry.id for entry in catalog.entries}
    assert {item.metadata["name"] for item in connectors} == {item.name for item in registry}
    assert len(documents) == len(catalog.entries) + len(registry)
    assert len({item.doc_id for item in documents}) == len(documents)
    assert all(item.metadata["real_money"] is False for item in connectors)
    assert all(item.metadata["status"] in {"wired", "planned"} for item in connectors)
    trading = next(item for item in connectors if item.metadata["name"] == "trading_control")
    assert trading.metadata["paper_only"] is True
    assert trading.metadata["status"] == "planned"
    assert trading.doc_id == "connector:trading_control"
    assert "real_money false" in trading.text

    store = _registry_store()
    try:
        assert isinstance(store.embedder, HashedNgramEmbedder)
        assert store.reload() == len(documents)
        episode = [hit.doc_id for hit in store.retrieve("episode schema columns", top_k=3, filters={"kind": "schema"})]
        gmail = [hit.doc_id for hit in store.retrieve("gmail connector", top_k=3, filters={"kind": "connector"})]
        trading_hits = store.retrieve("trading_control paper-only real_money", top_k=3, filters={"kind": "connector"})
        assert "schema:episode" in episode
        assert "connector:gmail" in gmail
        assert trading_hits[0].doc_id == "connector:trading_control"
        assert trading_hits[0].metadata["real_money"] is False
        assert trading_hits[0].metadata["paper_only"] is True
    finally:
        store.close()


def test_schema_agent_flag_off_matches_a_run_without_retrieval(store, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    monkeypatch.delenv("MIDPLANE_RETRIEVAL", raising=False)
    provider_off = RecordingProvider()
    provider_default = RecordingProvider()
    traces = DecisionTraceStore("sqlite:///:memory:")
    off = run_schema_agent(
        GOAL,
        store=store,
        provider=provider_off,
        retrieval_enabled=False,
        schema_documents=ExplodingStore(),
        decision_traces=traces,
        run_id="run-off",
    )
    default = run_schema_agent(
        GOAL,
        store=store,
        provider=provider_default,
        schema_documents=ExplodingStore(),
        run_id="run-default",
    )
    recorded = traces.list_traces(run_id="run-off")
    traces.close()
    assert _stable(off) == _stable(default)
    assert _end_nodes(off.episode.trace) == ["supervisor", "router", "schema_agent", "episode_writer"]
    assert _end_nodes(default.episode.trace) == _end_nodes(off.episode.trace)
    assert "retrieved_doc_ids" not in _schema_event(off.episode.trace)
    assert "retrieved_doc_ids" not in _schema_event(default.episode.trace)
    assert off.state.get("retrieval") in ({}, None)
    expected = render_schema_agent_prompt(goal=GOAL, constraints=off.episode.inputs["constraints"])
    assert provider_off.prompts == [expected]
    assert provider_default.prompts == [expected]
    assert provider_off.calls == ["fake-small"]
    assert all(item.decision_point != "schema_retrieval" for item in recorded)
    assert off.episode.outputs["agent"] == AGENT_NAME
    assert off.episode.quality_score == 0.99


def test_schema_agent_retrieves_and_records_doc_ids_when_the_flag_is_on(store, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    provider = RecordingProvider()
    documents = _registry_store()
    traces = DecisionTraceStore("sqlite:///:memory:")
    result = run_schema_agent(
        GOAL,
        store=store,
        provider=provider,
        retrieval_enabled=True,
        schema_documents=documents,
        decision_traces=traces,
        schema_retrieval_top_k=SCHEMA_AGENT_TOP_K,
        run_id="run-on",
    )
    off = run_schema_agent(
        GOAL,
        store=store,
        provider=RecordingProvider(),
        retrieval_enabled=False,
        schema_documents=ExplodingStore(),
        run_id="run-on-parity",
    )
    recorded = [item for item in traces.list_traces(run_id="run-on") if item.decision_point == "schema_retrieval"]
    traces.close()
    documents.close()

    assert provider.calls == ["fake-small"]
    assert isinstance(documents.embedder, HashedNgramEmbedder)
    assert _end_nodes(result.episode.trace) == ["supervisor", "router", "schema_agent", "episode_writer"]
    assert "retrieve_context" not in _end_nodes(result.episode.trace)
    event = _schema_event(result.episode.trace)
    doc_ids = event["retrieved_doc_ids"]
    assert doc_ids
    assert len(doc_ids) <= SCHEMA_AGENT_TOP_K * 2
    assert any(doc_id.startswith("schema:") for doc_id in doc_ids)
    assert any(doc_id.startswith("connector:") for doc_id in doc_ids)
    assert "schema:episode" in doc_ids
    assert result.state["retrieval"]["doc_ids"] == doc_ids
    prompt = provider.prompts[0]
    assert prompt.startswith("Retrieved schema and connector documents:\n")
    goal_at = prompt.index("\nGoal: ")
    context = prompt[:goal_at]
    for doc_id in doc_ids:
        assert doc_id in context
    assert prompt[goal_at:].startswith("\nGoal: ")
    assert len(recorded) == 1
    assert recorded[0].agent == "schema_agent"
    assert recorded[0].options == doc_ids
    assert recorded[0].choice == doc_ids[0]
    assert recorded[0].episode_id == "run-on"
    assert _stable(result) == _stable(off)
    assert result.episode.outcome == "success"
    assert result.episode.quality_score == 0.99
