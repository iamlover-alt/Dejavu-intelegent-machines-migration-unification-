"""Stub quality score for the first slice.

1.0 is reserved for the real rubric. This function returns 0.5 or 0.0.
RECONCILE with episode_schema.sql if the column type or scale differs.
"""

from __future__ import annotations

from typing import Mapping

REQUIRED_FIELDS = (
    "run_id",
    "source",
    "task",
    "plan",
    "model_choices",
    "tool_calls",
    "cost",
    "latency_ms",
    "outcome",
    "trace",
)

HAPPY_PATH_NODES = {"supervisor", "router", "code_review", "episode_writer"}


def stub_quality_score(state: Mapping) -> float:
    complete = all(state.get(key) is not None for key in REQUIRED_FIELDS)
    traced = {event.get("node") for event in (state.get("trace") or [])} >= HAPPY_PATH_NODES
    if (
        state.get("outcome") == "success"
        and state.get("error") is None
        and complete
        and traced
        and state.get("output")
    ):
        return 0.5
    return 0.0
