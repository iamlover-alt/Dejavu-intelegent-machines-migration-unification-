from __future__ import annotations

import json
from pathlib import Path

from midplane.ddl import POSTGRES_EPISODE_INDEX_DDL
from midplane.export import export_episodes, main, training_record
from midplane.scoring import HeuristicQualityScorer, StubQualityScorer
from midplane.store import Episode, SqliteEpisodeStore, resolve_database_url


def _episode(
    run_id: str,
    *,
    source: str = "dev_guild",
    score: float = 0.8,
    created_at: str = "2026-11-21T00:00:00.000000Z",
    findings: bool = True,
    outcome: str = "success",
) -> Episode:
    return Episode(
        run_id=run_id,
        source=source,
        inputs={"role": "code_review", "repo": "demo", "diff": "+print('ok')\n", "instructions": "Review."},
        plan={"steps": ["code_review"]},
        tool_calls=[],
        model_choices=[{"kind": "route", "primary": "fake-small", "fallback": "fake-large", "criteria": {}}],
        outputs={"summary": "Reviewed.", "findings": [{"severity": "info", "detail": "ok", "ref": "diff"}]}
        if findings
        else {},
        cost={"input_tokens": 10, "output_tokens": 5, "total_usd": 0.01, "by_model": {}},
        latency_ms=12,
        outcome=outcome,
        quality_score=score,
        trace=[{"node": "episode_writer", "event": "end"}],
        score_version="heuristic-v0",
        created_at=created_at,
    )


def test_heuristic_scores_each_check():
    scorer = HeuristicQualityScorer(max_latency_ms=100, max_cost_usd=0.05)
    base = {
        "outcome": "success",
        "error": None,
        "trace": [
            {"node": "supervisor"},
            {"node": "router"},
            {"node": "code_review"},
            {"node": "episode_writer"},
        ],
        "output": {"findings": [{"detail": "a finding"}]},
        "latency_ms": 10,
        "cost": {"total_usd": 0.01},
    }
    assert scorer.score(base) == 0.99
    assert scorer.version == "heuristic-v0"
    slow = dict(base, latency_ms=1000)
    assert scorer.score(slow) == 0.85
    no_findings = dict(base, output={})
    assert scorer.score(no_findings) == 0.70
    failed = dict(base, outcome="error", error="boom")
    assert scorer.score(failed) == 0.60
    assert scorer.score(base) < 1.0
    assert StubQualityScorer().version == "stub-v0"


def test_list_filters_and_cursor(tmp_path):
    store = SqliteEpisodeStore(str(tmp_path / "episodes.sqlite"))
    store.upsert(_episode("a2", created_at="2026-11-21T00:00:04.000000Z", score=0.95, source="dev_guild"))
    store.upsert(_episode("a", created_at="2026-11-21T00:00:03.000000Z", score=0.9, source="dev_guild"))
    store.upsert(_episode("b", created_at="2026-11-21T00:00:02.000000Z", score=0.2, source="dev_guild"))
    store.upsert(_episode("c", created_at="2026-11-21T00:00:01.000000Z", score=0.7, source="app"))
    high, cursor = store.list_episodes(source="dev_guild", min_score=0.5, limit=1)
    assert [item.run_id for item in high] == ["a2"]
    assert cursor
    rest, done = store.list_episodes(source="dev_guild", min_score=0.5, limit=1, cursor=cursor)
    assert [item.run_id for item in rest] == ["a"]
    assert done is None
    since, _ = store.list_episodes(since="2026-11-21T00:00:02.000000Z", limit=10)
    assert [item.run_id for item in since] == ["a2", "a", "b"]
    owned, _ = store.list_episodes(run_ids=["c"], limit=10)
    assert [item.run_id for item in owned] == ["c"]
    assert store.list_episodes(run_ids=[], limit=10) == ([], None)
    store.close()


def test_export_jsonl_respects_filters(tmp_path):
    database = tmp_path / "episodes.sqlite"
    url = f"sqlite:///{database}"
    store = SqliteEpisodeStore(str(database))
    store.upsert(_episode("keep", score=0.8, created_at="2026-11-25T01:00:00.000000Z", source="app"))
    store.upsert(_episode("drop-score", score=0.1, created_at="2026-11-25T02:00:00.000000Z", source="app"))
    store.upsert(_episode("drop-source", score=0.9, created_at="2026-11-25T03:00:00.000000Z", source="trading"))
    store.upsert(_episode("drop-time", score=0.9, created_at="2026-11-01T00:00:00.000000Z", source="app"))
    store.close()
    destination = tmp_path / "train.jsonl"
    count = export_episodes(
        destination=destination,
        since="2026-11-20T00:00:00.000000Z",
        min_score=0.5,
        source="app",
        database_url=url,
    )
    assert count == 1
    record = json.loads(destination.read_text().splitlines()[0])
    assert record["episode_id"] == "keep"
    assert record["score"] == 0.8
    assert record["score_version"] == "heuristic-v0"
    assert record["model_choice"]["primary"] == "fake-small"
    assert record["messages"][0]["role"] == "user"
    assert "+print('ok')" in record["prompt"]
    assert record["messages"][1]["role"] == "assistant"
    assert record["prompt"] == record["messages"][0]["content"]
    assert training_record(store_get(url, "keep"))["episode_id"] == "keep"


def store_get(url: str, run_id: str) -> Episode:
    store = SqliteEpisodeStore(url.removeprefix("sqlite:///"))
    try:
        episode = store.get(run_id)
        assert episode is not None
        return episode
    finally:
        store.close()


def test_export_cli(tmp_path, capsys):
    database = tmp_path / "episodes.sqlite"
    store = SqliteEpisodeStore(str(database))
    store.upsert(_episode("one"))
    store.close()
    destination = tmp_path / "out.jsonl"
    code = main(["--database", f"sqlite:///{database}", "--min-score", "0.5", "--source", "dev_guild", "--out", str(destination)])
    assert code == 0
    assert capsys.readouterr().err.strip() == "1"
    assert destination.read_text().count("\n") == 1


def test_database_url_beats_the_midplane_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///from-database-url.sqlite")
    monkeypatch.setenv("MIDPLANE_DATABASE_URL", "sqlite:///from-midplane.sqlite")
    assert resolve_database_url() == "sqlite:///from-database-url.sqlite"
    assert resolve_database_url("sqlite:///explicit.sqlite") == "sqlite:///explicit.sqlite"


def test_index_migration_is_reconcile_marked():
    text = Path("migrations/003_episode_indexes.sql").read_text()
    assert "RECONCILE with episode_schema.sql" in text
    assert POSTGRES_EPISODE_INDEX_DDL.strip() in text
    for name in (
        "episodes_source_quality_score_idx",
        "episodes_source_created_at_idx",
        "episodes_created_at_run_id_idx",
        "score_version",
        "created_at",
    ):
        assert name in text
    assert "broker" not in text.lower()
