from __future__ import annotations

import pytest

from midplane.graph import run_review
from midplane.models import FakeModelProvider
from midplane.routing import (
    DEFAULT_BUDGET,
    DEFAULT_ROUTE_TABLE,
    CostLatencyRoutingPolicy,
    choose_model,
)
from midplane.store import SqliteEpisodeStore


def test_choose_model_records_criteria_and_fallback():
    choice = choose_model("code_review", DEFAULT_BUDGET, DEFAULT_ROUTE_TABLE)
    assert choice["primary"] == "fake-small"
    assert choice["fallback"] == "fake-large"
    assert set(choice["criteria"]) == {"task_type", "cost", "latency", "confidence"}
    assert choice["criteria"]["task_type"] == "code_review"
    assert choice["criteria"]["cost"] == DEFAULT_BUDGET["max_cost"]
    assert choice["criteria"]["latency"] == DEFAULT_BUDGET["max_latency_ms"]
    assert choice["criteria"]["confidence"] == DEFAULT_BUDGET["min_confidence"]


def test_choose_model_errors_when_nothing_is_eligible():
    choice = choose_model("code_review", DEFAULT_BUDGET, {"models": []})
    assert choice["primary"] is None
    assert choice["error"] == "no_eligible_model"
    assert set(choice["criteria"]) == {"task_type", "cost", "latency", "confidence"}


def test_custom_policy_is_used():
    class PreferLarge:
        def choose(self, task_type, budget, table):
            return {
                "kind": "route",
                "task_type": task_type,
                "primary": "fake-large",
                "fallback": "fake-small",
                "criteria": {
                    "task_type": task_type,
                    "cost": budget["max_cost"],
                    "latency": budget["max_latency_ms"],
                    "confidence": budget["min_confidence"],
                },
            }

    choice = choose_model(
        "code_review",
        DEFAULT_BUDGET,
        DEFAULT_ROUTE_TABLE,
        policy=PreferLarge(),
    )
    assert choice["primary"] == "fake-large"
    assert not isinstance(PreferLarge(), CostLatencyRoutingPolicy)


def test_graph_records_the_route_and_honors_a_custom_policy(store: SqliteEpisodeStore):
    class PinLarge:
        def choose(self, task_type, budget, table):
            return {
                "kind": "route",
                "task_type": task_type,
                "primary": "fake-large",
                "fallback": None,
                "criteria": {
                    "task_type": task_type,
                    "cost": budget["max_cost"],
                    "latency": budget["max_latency_ms"],
                    "confidence": budget["min_confidence"],
                },
            }

    provider = FakeModelProvider()
    result = run_review(
        "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@\n+print('ok')\n",
        store=store,
        provider=provider,
        policy=PinLarge(),
        run_id="run-route",
    )
    route_records = [item for item in result.episode.model_choices if item.get("kind") == "route"]
    assert len(route_records) == 1
    assert set(route_records[0]["criteria"]) == {"task_type", "cost", "latency", "confidence"}
    assert provider.calls == ["fake-large"]
    assert result.state["route"]["primary"] == "fake-large"


def test_router_budget_changes_recorded_criteria(monkeypatch):
    monkeypatch.setenv("MIDPLANE_MAX_COST", "0.25")
    monkeypatch.setenv("MIDPLANE_MAX_LATENCY_MS", "1500")
    monkeypatch.setenv("MIDPLANE_MIN_CONFIDENCE", "0.9")
    from midplane.routing import budget_from_env

    budget = budget_from_env()
    choice = choose_model("code_review", budget, DEFAULT_ROUTE_TABLE)
    assert choice["criteria"]["cost"] == pytest.approx(0.25)
    assert choice["criteria"]["latency"] == 1500
    assert choice["criteria"]["confidence"] == pytest.approx(0.9)
