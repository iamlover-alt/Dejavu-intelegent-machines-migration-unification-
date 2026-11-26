"""Model routing for guild workers.

The policy ranks eligible models by cost, then latency, and records task type,
cost, latency, and confidence on every choice. The same contract is used by
the first-slice router. Threshold numbers are configuration.
"""

from __future__ import annotations

import os
import re
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
            "task_types": ["website_design"],
            "cost_rank": 0,
            "latency_rank": 0,
        },
        {
            "id": "fake-large",
            "task_types": ["website_design"],
            "cost_rank": 1,
            "latency_rank": 1,
        },
    ]
}

_WEBSITE_ROLES = {"website", "website_design", "squarespace", "website_agent"}
_WEBSITE_TEXT = re.compile(
    r"squarespace|website|web\s*site|landing\s+page|fluid\s+engine|"
    r"home\s*page|\bseo\b|alt\s+text|site\s+styles|"
    r"\b(?:home|about|platform|contact)\s+page\b|\bweb\s+page\b",
    re.IGNORECASE,
)
_DESIGN = re.compile(r"\b(?:re)?design\b", re.IGNORECASE)
_DESIGN_TARGET = re.compile(
    r"\bhero\b|\blayout\b|\bpalette\b|\btypograph|\bfonts?\b|"
    r"\bsquarespace\b|\bwebsite\b|\blanding\s+page\b|\bmobile\s+layout\b",
    re.IGNORECASE,
)


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

    A custom policy is invoked only when it is not this function's own default
    class, so the default policy can call `choose_model` without recursing.
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
    """The fake provider uses the in-repo table unless model ids are set."""

    source = os.environ if env is None else env
    provider = source.get("MIDPLANE_MODEL_PROVIDER", "fake").strip().lower()
    primary = source.get("MIDPLANE_PRIMARY_MODEL", "").strip()
    fallback = source.get("MIDPLANE_FALLBACK_MODEL", "").strip()
    if provider in {"", "fake"} and not primary:
        return DEFAULT_ROUTE_TABLE
    if not primary:
        raise RuntimeError("MIDPLANE_PRIMARY_MODEL is required when a non-fake provider is configured")
    models = [
        {
            "id": primary,
            "task_types": ["website_design"],
            "cost_rank": 0,
            "latency_rank": 0,
        }
    ]
    if fallback:
        models.append(
            {
                "id": fallback,
                "task_types": ["website_design"],
                "cost_rank": 1,
                "latency_rank": 1,
            }
        )
    return {"models": models}


def is_website_request(task: Mapping) -> bool:
    """True when the supervisor should hand the task to the website agent."""

    role = str(task.get("role") or "").strip().lower()
    if role in _WEBSITE_ROLES:
        return True
    text = " ".join(str(task.get(key) or "") for key in ("text", "instructions"))
    if _WEBSITE_TEXT.search(text):
        return True
    if _DESIGN.search(text) and _DESIGN_TARGET.search(text):
        return True
    return False
