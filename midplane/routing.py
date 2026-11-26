"""Pluggable model routing.

The default policy ranks eligible models by cost, then latency, and records
task type, cost, latency, and confidence on every choice. Threshold numbers
are configuration. The model catalog is still an open question; see
docs/architecture/mid-plane.md.
"""

from __future__ import annotations

import os
from typing import Mapping, Protocol


DEFAULT_BUDGET = {
    "max_cost": 1.0,
    "max_latency_ms": 30_000,
    "min_confidence": 0.5,
}

DEFAULT_ROUTE_TABLE = {
    "models": [
        {
            "id": "fake-small",
            "task_types": ["code_review", "schema_agent"],
            "cost_rank": 0,
            "latency_rank": 0,
        },
        {
            "id": "fake-large",
            "task_types": ["code_review", "schema_agent"],
            "cost_rank": 1,
            "latency_rank": 1,
        },
    ]
}


class RoutingPolicy(Protocol):
    def choose(self, task_type: str, budget: Mapping, table: Mapping) -> dict:
        """Return primary, fallback, and the four routing criteria."""


class CostLatencyRoutingPolicy:
    """Prefer the cheaper model, then the lower-latency model."""

    def choose(self, task_type: str, budget: Mapping, table: Mapping) -> dict:
        return choose_model(task_type, budget, table, policy=self)


def choose_model(
    task_type: str,
    budget: Mapping,
    table: Mapping,
    policy: RoutingPolicy | None = None,
) -> dict:
    """Select a primary model and one fallback.

    `policy` is accepted so callers can inject another policy. The default
    implementation is the cost-then-latency ranking. A custom policy is invoked
    only when it is not this function's own default class, so the default
    policy can call `choose_model` without recursing.
    """

    if policy is not None and not isinstance(policy, CostLatencyRoutingPolicy):
        return policy.choose(task_type, budget, table)

    eligible = [
        model
        for model in table.get("models", [])
        if task_type in model.get("task_types", [])
    ]
    ranked = sorted(eligible, key=lambda model: (model["cost_rank"], model["latency_rank"]))
    criteria = {
        "task_type": task_type,
        "cost": budget["max_cost"],
        "latency": budget["max_latency_ms"],
        "confidence": budget["min_confidence"],
    }
    if not ranked:
        return {
            "kind": "route",
            "task_type": task_type,
            "primary": None,
            "fallback": None,
            "criteria": criteria,
            "error": "no_eligible_model",
        }
    fallback = ranked[1]["id"] if len(ranked) > 1 else None
    return {
        "kind": "route",
        "task_type": task_type,
        "primary": ranked[0]["id"],
        "fallback": fallback,
        "criteria": criteria,
    }


def budget_from_env(env: Mapping[str, str] | None = None) -> dict:
    source = os.environ if env is None else env
    return {
        "max_cost": float(source.get("MIDPLANE_MAX_COST", DEFAULT_BUDGET["max_cost"])),
        "max_latency_ms": int(source.get("MIDPLANE_MAX_LATENCY_MS", DEFAULT_BUDGET["max_latency_ms"])),
        "min_confidence": float(source.get("MIDPLANE_MIN_CONFIDENCE", DEFAULT_BUDGET["min_confidence"])),
    }


def route_table_from_env(env: Mapping[str, str] | None = None) -> dict:
    """Fake provider uses the in-repo fixture unless model ids are set.

    Real providers require MIDPLANE_PRIMARY_MODEL. MIDPLANE_FALLBACK_MODEL
    is optional.
    """

    source = os.environ if env is None else env
    provider = source.get("MIDPLANE_MODEL_PROVIDER", "fake").strip().lower()
    primary = source.get("MIDPLANE_PRIMARY_MODEL", "").strip()
    fallback = source.get("MIDPLANE_FALLBACK_MODEL", "").strip()
    if provider == "fake" and not primary:
        return DEFAULT_ROUTE_TABLE
    if not primary:
        raise RuntimeError("MIDPLANE_PRIMARY_MODEL is required when MIDPLANE_MODEL_PROVIDER is not fake")
    models = [
        {
            "id": primary,
            "task_types": ["code_review", "schema_agent"],
            "cost_rank": 0,
            "latency_rank": 0,
        }
    ]
    if fallback:
        models.append(
            {
                "id": fallback,
                "task_types": ["code_review", "schema_agent"],
                "cost_rank": 1,
                "latency_rank": 1,
            }
        )
    return {"models": models}
