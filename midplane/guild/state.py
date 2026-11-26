"""Shared state for one guild routing run."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict


class GuildState(TypedDict):
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
    trace: Annotated[list[dict], operator.add]
    error: str | None


def initial_state(
    *,
    run_id: str,
    thread_id: str,
    task: dict,
    source: Literal["dev_guild", "trading", "app"] = "dev_guild",
) -> GuildState:
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "source": source,
        "task": task,
        "plan": {},
        "route": {},
        "model_choices": [],
        "tool_calls": [],
        "output": {},
        "cost": {},
        "latency_ms": 0,
        "outcome": None,
        "trace": [],
        "error": None,
    }
