from __future__ import annotations

from pathlib import Path

from midplane.ddl import POSTGRES_EPISODES_DDL, SQLITE_EPISODES_DDL
from midplane.graph import run_review
from midplane.store import EPISODE_COLUMNS, SqliteEpisodeStore
from tests.conftest import SAMPLE_DIFF

REQUIRED_PRESENT = (
    "run_id",
    "source",
    "inputs",
    "plan",
    "tool_calls",
    "model_choices",
    "outputs",
    "cost",
    "latency_ms",
    "outcome",
    "quality_score",
    "trace",
)


def test_episode_row_has_required_fields(store: SqliteEpisodeStore):
    result = run_review(SAMPLE_DIFF, store=store, run_id="run-fields")
    episode = result.episode
    for field in REQUIRED_PRESENT:
        assert getattr(episode, field) is not None
    assert episode.inputs["diff"] == SAMPLE_DIFF
    assert episode.plan["steps"] == ["code_review"]
    assert isinstance(episode.tool_calls, list)
    assert isinstance(episode.model_choices, list) and episode.model_choices
    assert isinstance(episode.outputs, dict) and episode.outputs["summary"]
    assert isinstance(episode.cost, dict)
    assert isinstance(episode.trace, list) and episode.trace
    writer = episode.trace[-1]
    assert writer["node"] == "episode_writer"
    assert "node_sequence" in writer
    assert "model_choices" in writer
    assert "cost" in writer
    assert "tokens" in writer
    code_review = next(event for event in episode.trace if event["node"] == "code_review")
    assert "latency_ms" in code_review
    assert "tokens" in code_review


def test_second_upsert_replaces_the_same_run_id(store: SqliteEpisodeStore):
    result = run_review(SAMPLE_DIFF, store=store, run_id="run-upsert", thread_id="t1")
    store.upsert(result.episode)
    assert store.count("run-upsert") == 1
    assert store.get("run-upsert").quality_score == result.episode.quality_score


def test_postgres_migration_matches_packaged_ddl():
    text = Path("migrations/001_episodes.sql").read_text()
    assert "RECONCILE with episode_schema.sql" in text
    assert POSTGRES_EPISODES_DDL.strip() in text
    for column in EPISODE_COLUMNS:
        assert column in text
        assert column in SQLITE_EPISODES_DDL
    assert "jsonb" in POSTGRES_EPISODES_DDL
    assert "broker" not in text.lower()
