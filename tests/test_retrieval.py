"""Retrieval accuracy, ranking, chaining, traces, and backend parity."""

from __future__ import annotations

import json
import math
import os
import urllib.request
from pathlib import Path

import pytest

from midplane.graph import build_graph, retrieval_enabled_from_env, run_review
from midplane.models import FakeModelProvider
from midplane.retrieval.chain import ChainStep, RetrievalChain
from midplane.retrieval.ddl import POSTGRES_RETRIEVAL_DDL
from midplane.retrieval.embedder import (
    EMBEDDING_DIM,
    HashedNgramEmbedder,
    OpenAICompatibleEmbedder,
    SentenceTransformerEmbedder,
)
from midplane.retrieval.episodes import EpisodeMemoryStore
from midplane.retrieval.index import InMemoryVectorIndex, PgVectorIndex, is_postgres_url
from midplane.retrieval.rerank import CrossEncoderReranker, LexicalReranker
from midplane.retrieval.schema_store import SchemaConnectorStore
from midplane.retrieval.traces import DecisionTraceStore
from midplane.retrieval.weights import PriorResult
from midplane.routing import DEFAULT_BUDGET, DEFAULT_ROUTE_TABLE, CostLatencyRoutingPolicy
from midplane.store import Episode, SqliteEpisodeStore
from tests.conftest import SAMPLE_DIFF

NOW = "2026-11-26T00:00:00.000000Z"
RECALL_AT_3 = 1.0
MRR_THRESHOLD = 1.0


def _episode(
    run_id: str,
    *,
    source: str = "dev_guild",
    score: float = 0.8,
    created_at: str = "2026-11-20T00:00:00.000000Z",
    detail: str = "No blocking issues in the added lines.",
    outcome: str = "success",
) -> Episode:
    return Episode(
        run_id=run_id,
        source=source,
        inputs={"role": "code_review", "repo": "demo", "diff": "+print('ok')\n", "instructions": detail},
        plan={"steps": ["code_review"]},
        tool_calls=[],
        model_choices=[{"kind": "route", "primary": "fake-small", "fallback": "fake-large", "task_type": "code_review"}],
        outputs={"summary": detail, "findings": [{"severity": "info", "detail": detail, "ref": "diff"}]},
        cost={"input_tokens": 10, "output_tokens": 5, "total_usd": 0.01, "by_model": {}},
        latency_ms=12,
        outcome=outcome,
        quality_score=score,
        trace=[{"node": "episode_writer", "event": "end"}],
        score_version="heuristic-v0",
        created_at=created_at,
    )


def _runs(hits) -> list[str]:
    ordered: list[str] = []
    for hit in hits:
        run_id = str(hit.metadata.get("run_id") or "")
        if run_id and run_id not in ordered:
            ordered.append(run_id)
    return ordered


def _recall(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def _mrr(ranked: list[str], relevant: set[str]) -> float:
    for index, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def test_hashed_embedder_is_deterministic_and_normalized():
    embedder = HashedNgramEmbedder()
    embedder.fit(["billing retry idempotency key", "stylesheet button color"])
    first = embedder.embed_query("billing retry idempotency key")
    second = embedder.embed_query("billing retry idempotency key")
    assert embedder.dimension == EMBEDDING_DIM == 256
    assert first == second
    assert len(first) == EMBEDDING_DIM
    norm = math.sqrt(sum(value * value for value in first))
    assert norm == pytest.approx(1.0, abs=1e-6)
    assert first != embedder.embed_query("stylesheet button color")


def test_optional_embedders_are_not_required(monkeypatch):
    def forbid(*_args, **_kwargs):
        raise AssertionError("constructing an adapter must not open a socket")

    monkeypatch.setattr(urllib.request, "urlopen", forbid)
    adapter = OpenAICompatibleEmbedder(api_key="test-key", model="text-embedding-3-small")
    assert adapter.dimension == 0

    class _Body:
        def read(self):
            return json.dumps({"data": [{"index": 0, "embedding": [3.0, 4.0]}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout=30):
        del timeout
        assert request.get_header("Authorization") == "Bearer test-key"
        assert json.loads(request.data.decode())["input"] == ["hello"]
        return _Body()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    vector = adapter.embed(["hello"])[0]
    assert vector == pytest.approx([0.6, 0.8])
    assert adapter.dimension == 2

    spec = __import__("importlib").util.find_spec("sentence_transformers")
    if spec is None:
        with pytest.raises(RuntimeError, match="embeddings"):
            SentenceTransformerEmbedder()
        with pytest.raises(RuntimeError, match="embeddings"):
            CrossEncoderReranker(lambda _document: PriorResult(1.0, {}, None, {}))


def test_episode_recall_and_mrr(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    memory = EpisodeMemoryStore(now=NOW)
    labelled = {
        "billing": "billing retry idempotency key missing on the charge endpoint",
        "trading": "paper trading control service kill switch refused a real money order",
        "ios": "ios swift client paginates the episode list with an opaque cursor",
        "css": "stylesheet button color changed from blue to green",
    }
    for run_id, detail in labelled.items():
        memory.index_episode(_episode(run_id, detail=detail))
    cases = {
        "billing retry idempotency key": "billing",
        "kill switch paper trading control": "trading",
        "opaque cursor ios swift episode list": "ios",
    }
    recalls = []
    reciprocal = []
    for query, run_id in cases.items():
        ranked = _runs(memory.retrieve(query, top_k=5))
        recalls.append(_recall(ranked, {run_id}, 3))
        reciprocal.append(_mrr(ranked, {run_id}))
        assert ranked, query
    assert sum(recalls) / len(recalls) >= RECALL_AT_3
    assert sum(reciprocal) / len(reciprocal) >= MRR_THRESHOLD
    memory.close()


def test_schema_recall_and_mrr(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    schema = SchemaConnectorStore()
    cases = {
        "GET /v1/episodes": "openapi:GET:/v1/episodes",
        "table decision_traces": "table:decision_traces",
        "gmail connector": "connector:gmail",
        "trading-control paper-only real_money": "connector:trading-control",
    }
    recalls = []
    reciprocal = []
    for query, doc_id in cases.items():
        ranked = [hit.doc_id for hit in schema.retrieve(query, top_k=5)]
        recalls.append(_recall(ranked, {doc_id}, 3))
        reciprocal.append(_mrr(ranked, {doc_id}))
    assert min(recalls) >= RECALL_AT_3
    assert min(reciprocal) >= MRR_THRESHOLD
    trading = schema.retrieve("trading-control", top_k=1)[0]
    assert trading.metadata["paper_only"] is True
    assert trading.metadata["real_money"] is False
    gmail = schema.retrieve("gmail", top_k=1)[0]
    assert gmail.metadata["status"] == "planned"
    assert gmail.breakdown.warning == "planned"
    schema.close()


def test_trace_recall_and_failure_warning(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    traces = DecisionTraceStore("sqlite:///:memory:")
    success = traces.record(
        agent="router",
        run_id="run-ok",
        decision_point="model_routing",
        options=["fake-small", "fake-large"],
        choice="fake-small",
        rationale="model routing code review chose fake-small and the review succeeded",
        outcome="success",
        outcome_score=0.9,
    )
    failure = traces.record(
        agent="router",
        run_id="run-bad",
        decision_point="model_routing",
        options=["fake-small", "fake-large"],
        choice="fake-large",
        rationale="model routing code review chose fake-large and the review failed",
        outcome="fail",
        outcome_score=0.1,
    )
    ranked = traces.retrieve("model routing code review", top_k=2)
    assert [hit.doc_id for hit in ranked][0] == f"trace:{success.trace_id}"
    warnings = {hit.doc_id: hit.breakdown.warning for hit in ranked}
    assert warnings[f"trace:{failure.trace_id}"] == "failure"
    assert warnings[f"trace:{success.trace_id}"] is None
    assert ranked[0].score > ranked[1].score
    only_failure = traces.retrieve("fake-large review failed", top_k=2)
    assert any(hit.breakdown.warning == "failure" for hit in only_failure)
    assert _mrr([hit.doc_id for hit in ranked], {f"trace:{success.trace_id}"}) >= MRR_THRESHOLD
    traces.close()


def test_quality_weight_beats_raw_similarity():
    memory = EpisodeMemoryStore(now=NOW)
    query = "billing retry idempotency key"
    memory.index_episode(
        _episode(
            "low",
            score=0.05,
            created_at="2026-11-25T00:00:00.000000Z",
            detail=query,
        )
    )
    memory.index_episode(
        _episode(
            "high",
            score=0.99,
            created_at="2026-01-01T00:00:00.000000Z",
            detail=query + " was checked in the charge path and left in place",
        )
    )
    hits = memory.retrieve(query, top_k=10)
    by_run: dict = {}
    best_knn: dict = {}
    best_lexical: dict = {}
    for hit in hits:
        by_run.setdefault(hit.metadata["run_id"], hit)
        run_id = hit.metadata["run_id"]
        best_knn[run_id] = max(best_knn.get(run_id, 0.0), hit.breakdown.knn)
        best_lexical[run_id] = max(best_lexical.get(run_id, 0.0), hit.breakdown.lexical)
    assert _runs(hits)[0] == "high", [(hit.metadata["run_id"], hit.score, hit.breakdown.knn, hit.breakdown.lexical) for hit in hits]
    assert best_lexical["low"] >= best_lexical["high"]
    assert best_knn["low"] + 1e-9 >= best_knn["high"]
    assert by_run["high"].breakdown.terms["quality"] > by_run["low"].breakdown.terms["quality"]
    memory.close()


def test_recency_then_source_break_ties():
    memory = EpisodeMemoryStore(now=NOW)
    detail = "shared review note about the cache stampede"
    memory.index_episode(_episode("old", detail=detail, created_at="2025-01-01T00:00:00.000000Z", score=0.8))
    memory.index_episode(_episode("new", detail=detail, created_at="2026-11-25T00:00:00.000000Z", score=0.8))
    ranked = _runs(memory.retrieve("cache stampede", top_k=4))
    assert ranked[0] == "new"
    memory.index_episode(_episode("guild", detail=detail, source="dev_guild", created_at=NOW, score=0.8))
    memory.index_episode(_episode("paper", detail=detail, source="trading", created_at=NOW, score=0.8))
    source_ranked = _runs(memory.retrieve("cache stampede", top_k=20))
    assert source_ranked.index("guild") < source_ranked.index("paper")
    memory.close()


def test_filters_source_score_and_time():
    memory = EpisodeMemoryStore(now=NOW)
    memory.index_episode(_episode("guild", source="dev_guild", score=0.9, created_at="2026-11-20T00:00:00.000000Z"))
    memory.index_episode(_episode("old", source="dev_guild", score=0.9, created_at="2026-01-01T00:00:00.000000Z"))
    memory.index_episode(_episode("weak", source="dev_guild", score=0.1, created_at="2026-11-20T00:00:00.000000Z"))
    memory.index_episode(_episode("trade", source="trading", score=0.9, created_at="2026-11-20T00:00:00.000000Z", detail="trading note"))
    hits = memory.retrieve(
        "No blocking issues",
        top_k=10,
        filters={"source": "dev_guild", "min_score": 0.5, "since": "2026-11-01T00:00:00.000000Z"},
    )
    assert _runs(hits) == ["guild"]
    with pytest.raises(ValueError, match="Unknown filter"):
        memory.retrieve("No blocking issues", filters={"broker": "alpaca"})
    memory.close()


def test_incremental_index_replaces_chunks():
    memory = EpisodeMemoryStore(now=NOW)
    memory.index_episode(_episode("run-1", detail="first finding about idempotency"))
    assert _runs(memory.retrieve("idempotency", top_k=2)) == ["run-1"]
    memory.index_episode(_episode("run-1", detail="second finding about pagination cursors"))
    assert memory.retrieve("idempotency", top_k=2) == []
    assert _runs(memory.retrieve("pagination cursors", top_k=2)) == ["run-1"]
    memory.close()


def test_reranker_reorders_away_from_knn():
    class Scripted:
        dimension = 4

        def fit(self, _texts):
            return None

        def embed(self, texts):
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        def embed_query(self, _text):
            return [1.0, 0.0, 0.0, 0.0]

    def flat(_document):
        return PriorResult(factor=1.0, terms={}, warning=None, weights={"lexical": 0.7, "field_match": 0.3})

    index = InMemoryVectorIndex(4)
    index.replace_all(
        [
            (
                "close",
                [1.0, 0.0, 0.0, 0.0],
                {"_text": "zzzz unrelated", "_fields": {}, "store": "episodes"},
            ),
            (
                "far",
                [0.0, 1.0, 0.0, 0.0],
                {"_text": "billing retry idempotency key", "_fields": {}, "store": "episodes"},
            ),
        ]
    )
    from midplane.retrieval.retriever import Retriever

    retriever = Retriever(name="episodes", embedder=Scripted(), index=index, reranker=LexicalReranker(flat))
    query = "billing retry idempotency key"
    knn = [hit.doc_id for hit in index.search(Scripted().embed_query(query), k=2)]
    assert knn[0] == "close"
    ranked = [hit.doc_id for hit in retriever.retrieve(query, top_k=2)]
    assert ranked[0] == "far"
    assert ranked[0] != knn[0]


def test_chain_needs_two_stores_and_limits_hops(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    schema = SchemaConnectorStore()
    memory = EpisodeMemoryStore(now=NOW)
    memory.index_episode(
        _episode(
            "boundary",
            detail="review outcome for trading-control broker_called false and the order was not executed",
        )
    )
    memory.index_episode(_episode("css", detail="stylesheet button color changed from blue to green"))

    def follow_connector(original, hits):
        del original
        names = [hit.metadata.get("name") for hit in hits if hit.metadata.get("kind") == "connector"]
        return f"review outcome {names[0]}"

    chain = RetrievalChain(stores={"episodes": memory, "schema": schema}, max_hops=3)
    result = chain.run(
        "What was the review outcome for the trading control service?",
        [
            ChainStep(store="schema", top_k=3),
            ChainStep(store="episodes", top_k=3, query_from=follow_connector),
        ],
    )
    assert result.hops[0].store == "schema"
    assert "connector:trading-control" in result.hops[0].doc_ids
    assert "trading-control" in result.hops[1].query
    assert "paper-only" not in result.hops[1].query
    assert "boundary:finding-0" in result.hops[1].doc_ids or "boundary" in _runs(
        [hit for hit in result.hits if hit.store == "episodes"]
    )
    fused_ids = [hit.doc_id for hit in result.hits]
    assert "connector:trading-control" in fused_ids
    assert any(doc_id.startswith("boundary:") for doc_id in fused_ids)
    assert len(fused_ids) == len(set(fused_ids))
    assert result.fused is True

    limited = RetrievalChain(stores={"schema": schema}, max_hops=2)
    stopped = limited.run(
        "gmail connector",
        [ChainStep(store="schema", query="gmail", top_k=1) for _ in range(3)],
    )
    assert stopped.truncated is True
    assert len(stopped.hops) == 2

    duplicated = limited.run(
        "gmail connector",
        [
            ChainStep(store="schema", query="gmail connector", top_k=2),
            ChainStep(store="schema", query="gmail connector", top_k=2),
        ],
    )
    assert duplicated.hops[1].dropped_duplicates
    assert len([hit.doc_id for hit in duplicated.hits]) == len({hit.doc_id for hit in duplicated.hits})
    schema.close()
    memory.close()


def test_chain_rewrite_uses_the_fake_provider(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    memory = EpisodeMemoryStore(now=NOW)
    memory.index_episode(_episode("boundary", detail="review outcome trading-control broker_called false"))
    provider = FakeModelProvider(responses={"fake-small": "review outcome trading-control broker_called false"})
    chain = RetrievalChain(
        stores={"episodes": memory},
        max_hops=2,
        provider=provider,
        model_id="fake-small",
    )
    result = chain.run(
        "paper-only connector",
        [
            ChainStep(store="episodes", query="stylesheet", top_k=1),
            ChainStep(store="episodes", rewrite_with_model=True, top_k=2),
        ],
    )
    assert provider.calls == ["fake-small"]
    assert result.hops[1].model_rewritten is True
    assert result.hops[1].query == "review outcome trading-control broker_called false"
    assert "boundary:finding-0" in result.hops[1].doc_ids
    memory.close()


def test_outcome_stats_groups_what_worked():
    traces = DecisionTraceStore("sqlite:///:memory:")
    traces.record(
        agent="router",
        run_id="a",
        decision_point="model_routing",
        options=["fake-small"],
        choice="fake-small",
        rationale="picked the cheap model",
        outcome="success",
        outcome_score=0.9,
    )
    traces.record(
        agent="router",
        run_id="b",
        decision_point="model_routing",
        options=["fake-small"],
        choice="fake-small",
        rationale="picked the cheap model again",
        outcome="success",
        outcome_score=0.8,
    )
    traces.record(
        agent="router",
        run_id="c",
        decision_point="model_routing",
        options=["fake-large"],
        choice="fake-large",
        rationale="picked the expensive model",
        outcome="fail",
        outcome_score=0.2,
    )
    stats = traces.outcome_stats("model_routing")
    assert len(stats) == 1
    assert stats[0]["counts"] == {"success": 2, "fail": 1, "unknown": 0}
    assert stats[0]["worked"][0]["choice"] == "fake-small"
    assert stats[0]["failed"][0]["choice"] == "fake-large"
    assert stats[0]["mean_outcome_score"] == pytest.approx((0.9 + 0.8 + 0.2) / 3)
    traces.close()


def test_graph_flag_off_keeps_the_slice_edges(store: SqliteEpisodeStore):
    assert retrieval_enabled_from_env({}) is False
    graph = build_graph(
        _deps(store),
    )
    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    assert all("retrieve_context" not in pair for pair in edges)


def test_graph_retrieves_before_code_review_and_records_traces(store: SqliteEpisodeStore, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    memory = EpisodeMemoryStore(now=NOW)
    memory.index_episode(
        _episode("prior", detail="billing retry idempotency key was missing on a previous review")
    )
    traces = DecisionTraceStore("sqlite:///:memory:")
    chain = RetrievalChain(stores={"episodes": memory}, max_hops=1)
    provider = FakeModelProvider()
    result = run_review(
        SAMPLE_DIFF,
        store=store,
        provider=provider,
        instructions="Review the billing retry idempotency key.",
        episode_memory=memory,
        decision_traces=traces,
        retrieval_chain=chain,
        retrieval_steps=(ChainStep(store="episodes", top_k=3),),
        retrieval_enabled=True,
        run_id="run-retrieve",
    )
    nodes = [event["node"] for event in result.state["trace"] if event.get("event") == "end"]
    assert nodes == ["supervisor", "router", "retrieve_context", "code_review", "episode_writer"]
    assert provider.calls == ["fake-small"]
    assert result.state["quality_score"] == 0.99
    assert result.state["retrieval"]["hits"]
    assert any(hit["doc_id"].startswith("prior:") for hit in result.state["retrieval"]["hits"])
    routing = traces.outcome_stats("model_routing")[0]
    assert routing["counts"]["success"] == 1
    assert routing["worked"][0]["choice"] == "fake-small"
    completion = traces.outcome_stats("episode_completion")[0]
    assert completion["counts"]["success"] == 1
    assert _runs(memory.retrieve("billing retry idempotency key", top_k=4))[0] in {"prior", "run-retrieve"}
    traces.close()
    memory.close()


def test_enabled_flag_retrieves_episodes_already_in_the_store(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)
    store = SqliteEpisodeStore(str(tmp_path / "episodes.sqlite"))
    run_review(
        SAMPLE_DIFF,
        store=store,
        instructions="Review the billing retry idempotency key.",
        run_id="first",
    )
    result = run_review(
        SAMPLE_DIFF,
        store=store,
        instructions="Review the billing retry idempotency key.",
        retrieval_enabled=True,
        run_id="second",
    )
    nodes = [event["node"] for event in result.state["trace"] if event.get("event") == "end"]
    assert nodes == ["supervisor", "router", "retrieve_context", "code_review", "episode_writer"]
    assert any(hit["doc_id"].startswith("first:") for hit in result.state["retrieval"]["hits"])
    assert result.episode.quality_score == 0.99
    store.close()


def test_migration_declares_hnsw_and_mentions_ivfflat():
    text = Path("migrations/004_retrieval.sql").read_text()
    assert "RECONCILE" in text
    assert POSTGRES_RETRIEVAL_DDL.strip() in text
    assert f"vector({EMBEDDING_DIM})" in text
    assert "using hnsw" in text
    assert "ivfflat" in text
    assert "decision_traces" in text
    assert "real_money" not in text


def test_backend_parity_in_memory_and_pgvector():
    url = _pgvector_url()
    if url is None:
        pytest.skip("Postgres with pgvector is not configured")
    embedder = HashedNgramEmbedder()
    documents = [
        ("billing", "billing retry idempotency key missing on the charge endpoint"),
        ("trading", "paper trading kill switch refused a real money order"),
        ("ios", "ios swift client paginates episodes with an opaque cursor"),
        ("css", "stylesheet button color changed from blue to green"),
    ]
    embedder.fit([text for _doc_id, text in documents])
    vectors = embedder.embed([text for _doc_id, text in documents])
    rows = [
        (doc_id, vector, {"_text": text, "_fields": {}, "store": "parity", "name": doc_id})
        for (doc_id, text), vector in zip(documents, vectors)
    ]
    memory = InMemoryVectorIndex(EMBEDDING_DIM)
    memory.replace_all(rows)
    postgres = PgVectorIndex(url, store_name="parity", dimension=EMBEDDING_DIM)
    try:
        postgres.replace_all(rows)
        query = embedder.embed_query("billing retry idempotency key")
        memory_ids = [hit.doc_id for hit in memory.search(query, k=4)]
        postgres_ids = [hit.doc_id for hit in postgres.search(query, k=4)]
        assert memory_ids[0] == postgres_ids[0] == "billing"
        assert set(memory_ids) == set(postgres_ids)
    finally:
        postgres.close()


def test_retrieval_package_has_no_broker_client():
    root = Path("midplane/retrieval")
    banned = ("alpaca", "binance", "ccxt", "oanda")
    for path in root.rglob("*"):
        if path.suffix not in {".py", ".yaml", ".yml"}:
            continue
        text = path.read_text().lower()
        for token in banned:
            assert token not in text


def _deps(store: SqliteEpisodeStore):
    from midplane.graph import GraphDeps

    return GraphDeps(
        provider=FakeModelProvider(),
        store=store,
        policy=CostLatencyRoutingPolicy(),
        route_table=DEFAULT_ROUTE_TABLE,
        budget=DEFAULT_BUDGET,
    )


def _forbid_network(*_args, **_kwargs):
    raise AssertionError("retrieval tests must not open a network connection")


def _pgvector_url() -> str | None:
    for key in ("MIDPLANE_TEST_POSTGRES_URL", "DATABASE_URL"):
        candidate = os.environ.get(key, "")
        if not is_postgres_url(candidate):
            continue
        try:
            import psycopg
        except ImportError:
            return None
        try:
            connection = psycopg.connect(candidate)
        except Exception:
            continue
        try:
            connection.execute("create extension if not exists vector")
            connection.commit()
        except Exception:
            connection.close()
            continue
        connection.close()
        return candidate
    return None
