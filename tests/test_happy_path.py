from __future__ import annotations

import urllib.request

from langgraph.checkpoint.memory import MemorySaver

from midplane.graph import build_graph, run_review, tool_allowlist
from midplane.models import FakeModelProvider
from midplane.routing import DEFAULT_BUDGET, DEFAULT_ROUTE_TABLE, CostLatencyRoutingPolicy
from midplane.scoring import HeuristicQualityScorer, StubQualityScorer
from midplane.store import SqliteEpisodeStore
from tests.conftest import SAMPLE_DIFF


def _end_nodes(trace: list[dict]) -> list[str]:
    return [event["node"] for event in trace if event.get("event") == "end"]


def test_happy_path_writes_one_episode_and_a_checkpoint(store: SqliteEpisodeStore, monkeypatch):
    def forbid_network(*_args, **_kwargs):
        raise AssertionError("the fake provider must not open a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", forbid_network)
    provider = FakeModelProvider()
    checkpointer = MemorySaver()
    result = run_review(
        SAMPLE_DIFF,
        repo="demo",
        store=store,
        provider=provider,
        checkpointer=checkpointer,
        run_id="run-happy",
        thread_id="thread-happy",
    )

    assert provider.calls == ["fake-small"]
    assert _end_nodes(result.state["trace"]) == [
        "supervisor",
        "router",
        "code_review",
        "episode_writer",
    ]
    assert result.episode.source == "dev_guild"
    assert result.episode.outcome == "success"
    assert result.episode.score_version == "heuristic-v0"
    assert result.episode.quality_score == 0.99
    assert result.episode.quality_score == HeuristicQualityScorer().score(result.state)
    assert store.count("run-happy") == 1
    assert result.episode.trace == result.state["trace"]
    assert result.episode.outputs["findings"]
    assert result.episode.outputs["findings"][0]["severity"] in {"info", "warn", "block"}
    assert result.episode.cost["input_tokens"] > 0
    assert "total_usd" in result.episode.cost
    assert isinstance(result.episode.latency_ms, int)

    snapshot = result.graph.get_state({"configurable": {"thread_id": "thread-happy"}})
    assert snapshot.values["run_id"] == "run-happy"
    assert snapshot.values["quality_score"] == 0.99


def test_compiled_edges_can_exit_to_episode_writer(store: SqliteEpisodeStore):
    graph = build_graph(
        _deps(store),
        checkpointer=MemorySaver(),
    )
    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    assert ("__start__", "supervisor") in edges
    assert ("supervisor", "router") in edges
    assert ("supervisor", "episode_writer") in edges
    assert ("router", "code_review") in edges
    assert ("router", "episode_writer") in edges
    assert ("code_review", "episode_writer") in edges
    assert ("episode_writer", "__end__") in edges
    assert tool_allowlist() == ()


def test_fallback_then_success(store: SqliteEpisodeStore):
    provider = FakeModelProvider(fail_models={"fake-small"})
    result = run_review(SAMPLE_DIFF, store=store, provider=provider, run_id="run-fallback")
    assert provider.calls == ["fake-small", "fake-large"]
    assert result.episode.outcome == "success"
    roles = [item.get("role") for item in result.episode.model_choices if item.get("kind") == "attempt"]
    assert roles == ["primary", "fallback"]
    assert result.state["quality_score"] == 0.99


def test_unparseable_primary_uses_the_fallback(store: SqliteEpisodeStore):
    provider = FakeModelProvider(responses={"fake-small": "not json"})
    result = run_review(SAMPLE_DIFF, store=store, provider=provider, run_id="run-parse")
    assert provider.calls == ["fake-small", "fake-large"]
    assert result.episode.outcome == "success"
    statuses = [item.get("status") for item in result.episode.model_choices if item.get("kind") == "attempt"]
    assert statuses == ["error", "success"]


def test_both_models_fail_still_persists(store: SqliteEpisodeStore):
    provider = FakeModelProvider(fail_models={"fake-small", "fake-large"})
    result = run_review(SAMPLE_DIFF, store=store, provider=provider, run_id="run-fail")
    assert result.episode.outcome == "error"
    assert result.episode.quality_score == 0.30
    assert result.episode.quality_score == HeuristicQualityScorer().score(result.state)
    assert store.count("run-fail") == 1
    assert "code_review" in _end_nodes(result.episode.trace)


def test_same_run_id_upserts_one_row(store: SqliteEpisodeStore):
    first = run_review(SAMPLE_DIFF, store=store, run_id="run-once", thread_id="thread-a")
    second = run_review(SAMPLE_DIFF, store=store, run_id="run-once", thread_id="thread-b")
    assert store.count("run-once") == 1
    assert first.episode.run_id == second.episode.run_id == "run-once"


def test_unknown_role_skips_the_worker(store: SqliteEpisodeStore):
    provider = FakeModelProvider()
    result = run_review(
        SAMPLE_DIFF,
        store=store,
        provider=provider,
        role="ship_it",
        run_id="run-bad-role",
    )
    assert provider.calls == []
    assert result.episode.outcome == "error"
    assert result.episode.quality_score == 0.30
    assert _end_nodes(result.episode.trace) == ["supervisor", "episode_writer"]


def test_stub_scorer_can_replace_the_heuristic(store: SqliteEpisodeStore):
    result = run_review(
        SAMPLE_DIFF,
        store=store,
        scorer=StubQualityScorer(),
        run_id="run-stub-scorer",
    )
    assert result.episode.score_version == "stub-v0"
    assert result.episode.quality_score == 0.5


def _deps(store: SqliteEpisodeStore):
    from midplane.graph import GraphDeps

    return GraphDeps(
        provider=FakeModelProvider(),
        store=store,
        policy=CostLatencyRoutingPolicy(),
        route_table=DEFAULT_ROUTE_TABLE,
        budget=DEFAULT_BUDGET,
    )
