"""Shared graph state for one mid-plane run.

Field names match docs/architecture/first-slice.md.
RECONCILE with langgraph_state_management.md before treating them as frozen.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict


class MidPlaneState(TypedDict):
    run_id: str
    thread_id: str
    source: Literal["dev_guild", "trading", "app"]
    task: dict
    plan: dict
    route: dict
    model_choices: Annotated[list[dict], operator.add]
    tool_calls: Annotated[list[dict], operator.add]
    output: dict
    cost: dict
    latency_ms: int
    outcome: Literal["success", "error"] | None
    quality_score: float | None
    score_version: str | None
    created_at: str | None
    trace: Annotated[list[dict], operator.add]
    error: str | None


def initial_state(
    *,
    run_id: str,
    thread_id: str,
    diff: str,
    repo: str = "local",
    instructions: str = "Review this diff.",
    source: Literal["dev_guild", "trading", "app"] = "dev_guild",
    role: str = "code_review",
) -> MidPlaneState:
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "source": source,
        "task": {
            "role": role,
            "repo": repo,
            "diff": diff,
            "instructions": instructions,
        },
        "plan": {},
        "route": {},
        "model_choices": [],
        "tool_calls": [],
        "output": {},
        "cost": {},
        "latency_ms": 0,
        "outcome": None,
        "quality_score": None,
        "score_version": None,
        "created_at": None,
        "trace": [],
        "error": None,
    }
