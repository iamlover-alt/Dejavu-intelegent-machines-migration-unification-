"""Quality scores for an episode.

`stub_quality_score` is the first-slice stub (0.5 or 0.0). `HeuristicQualityScorer`
is flywheel v0. Both implement `QualityScorer` so the writer can be swapped.

1.0 stays reserved for a later judge rubric. The heuristic caps at 0.99.
RECONCILE with episode_schema.sql if the column type, scale, or score_version differs.
"""

from __future__ import annotations

from typing import Mapping, Protocol

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


class QualityScorer(Protocol):
    version: str

    def score(self, state: Mapping) -> float:
        """Return a deterministic score for this run state."""


class StubQualityScorer:
    """Adapter so the first-slice stub can be injected as a scorer."""

    version = "stub-v0"

    def score(self, state: Mapping) -> float:
        return stub_quality_score(state)


class HeuristicQualityScorer:
    """Four checks, summed, and capped below 1.0.

    * 0.40 the run completed: outcome success, no error, and the four slice nodes are in the trace
    * 0.30 the review produced at least one finding
    * 0.15 latency_ms is within max_latency_ms
    * 0.15 cost.total_usd is within max_cost_usd
    """

    version = "heuristic-v0"

    def __init__(self, max_latency_ms: int = 30_000, max_cost_usd: float = 1.0) -> None:
        self.max_latency_ms = max_latency_ms
        self.max_cost_usd = max_cost_usd

    def score(self, state: Mapping) -> float:
        points = 0.0
        traced = {event.get("node") for event in (state.get("trace") or []) if isinstance(event, dict)}
        completed = (
            state.get("outcome") == "success"
            and state.get("error") is None
            and HAPPY_PATH_NODES <= traced
        )
        if completed:
            points += 0.40
        output = state.get("output") or {}
        findings = output.get("findings") if isinstance(output, dict) else None
        if isinstance(findings, list) and any(isinstance(item, dict) and item.get("detail") for item in findings):
            points += 0.30
        latency = state.get("latency_ms")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool) and latency <= self.max_latency_ms:
            points += 0.15
        cost = state.get("cost") or {}
        total = cost.get("total_usd") if isinstance(cost, dict) else None
        if isinstance(total, (int, float)) and not isinstance(total, bool) and total <= self.max_cost_usd:
            points += 0.15
        return round(min(points, 0.99), 2)
