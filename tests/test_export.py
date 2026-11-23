from __future__ import annotations

import json

import pytest

from midplane.export import export_episodes, main, split_episodes
from midplane.store import Episode, SqliteEpisodeStore


def make_episode(index: int, *, outcome: str = "success", quality: float = 0.5) -> Episode:
    return Episode(
        run_id=f"run-{index:04d}",
        source="dev_guild",
        inputs={},
        plan={},
        tool_calls=[],
        model_choices=[],
        outputs={},
        cost={},
        latency_ms=1,
        outcome=outcome,
        quality_score=quality,
        trace=[],
    )


def test_split_is_disjoint_complete_and_deterministic():
    episodes = [make_episode(i) for i in range(500)]
    train, evaluation = split_episodes(episodes, eval_fraction=0.2, seed="a")
    train_ids = {e.run_id for e in train}
    eval_ids = {e.run_id for e in evaluation}
    assert train_ids.isdisjoint(eval_ids)
    assert train_ids | eval_ids == {e.run_id for e in episodes}
    assert 50 < len(evaluation) < 150
    again_train, again_eval = split_episodes(list(reversed(episodes)), eval_fraction=0.2, seed="a")
    assert {e.run_id for e in again_eval} == eval_ids
    assert {e.run_id for e in again_train} == train_ids


def test_seed_changes_the_split():
    episodes = [make_episode(i) for i in range(200)]
    _, first = split_episodes(episodes, seed="a")
    _, second = split_episodes(episodes, seed="b")
    assert {e.run_id for e in first} != {e.run_id for e in second}


def test_filters_and_fraction_bounds():
    episodes = [make_episode(1, quality=0.0), make_episode(2, outcome="error"), make_episode(3)]
    train, evaluation = split_episodes(episodes, eval_fraction=0.0, min_quality=0.5, outcomes={"success"})
    assert [e.run_id for e in train] == ["run-0003"] and evaluation == []
    with pytest.raises(ValueError):
        split_episodes(episodes, eval_fraction=1.5)


def test_export_writes_jsonl_keyed_by_run_id(tmp_path):
    store = SqliteEpisodeStore(":memory:")
    for i in range(50):
        store.upsert(make_episode(i))
    train_count, eval_count = export_episodes(store, tmp_path / "out", eval_fraction=0.3)
    assert train_count + eval_count == 50
    rows = {
        name: [json.loads(line) for line in (tmp_path / "out" / f"{name}.jsonl").read_text().splitlines()]
        for name in ("train", "eval")
    }
    assert len(rows["train"]) == train_count and len(rows["eval"]) == eval_count
    assert {r["run_id"] for r in rows["train"]}.isdisjoint({r["run_id"] for r in rows["eval"]})


def test_cli_prints_counts_and_rejects_bad_fraction(tmp_path, capsys):
    database = tmp_path / "episodes.sqlite"
    store = SqliteEpisodeStore(str(database))
    store.upsert(make_episode(1))
    store.close()
    url = f"sqlite:///{database}"
    assert main(["--out-dir", str(tmp_path / "o"), "--database", url]) == 0
    assert capsys.readouterr().out.startswith("train=")
    assert main(["--out-dir", str(tmp_path / "o"), "--database", url, "--eval-fraction", "2"]) == 2
