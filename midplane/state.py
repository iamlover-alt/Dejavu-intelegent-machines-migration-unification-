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
    retrieval: dict


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
        "retrieval": {},
    }


class SupervisorReads(TypedDict):
    source: str
    task: dict


class SupervisorWrites(TypedDict):
    plan: dict
    trace: list
    latency_ms: int
    error: str | None
    outcome: str | None


class RouterReads(TypedDict):
    plan: dict


class RouterWrites(TypedDict):
    route: dict
    model_choices: list
    trace: list
    latency_ms: int
    error: str | None
    outcome: str | None


class CodeReviewReads(TypedDict):
    task: dict
    route: dict


class CodeReviewWrites(TypedDict):
    output: dict
    model_choices: list
    tool_calls: list
    cost: dict
    latency_ms: int
    trace: list
    outcome: str | None
    error: str | None


class SchemaAgentReads(TypedDict):
    task: dict
    route: dict


class SchemaAgentWrites(TypedDict):
    output: dict
    model_choices: list
    tool_calls: list
    cost: dict
    latency_ms: int
    trace: list
    outcome: str | None
    error: str | None
    retrieval: dict


class EpisodeWriterReads(TypedDict):
    run_id: str
    source: str
    task: dict
    plan: dict
    model_choices: list
    tool_calls: list
    output: dict
    cost: dict
    latency_ms: int
    outcome: str | None
    trace: list
    error: str | None
    created_at: str | None


class EpisodeWriterWrites(TypedDict):
    trace: list
    latency_ms: int
    outcome: str | None
    output: dict
    cost: dict
    quality_score: float | None
    score_version: str | None
    created_at: str | None


NODE_CONTRACTS: dict[str, dict[str, type]] = {
    "supervisor": {"reads": SupervisorReads, "writes": SupervisorWrites},
    "router": {"reads": RouterReads, "writes": RouterWrites},
    "code_review": {"reads": CodeReviewReads, "writes": CodeReviewWrites},
    "schema_agent": {"reads": SchemaAgentReads, "writes": SchemaAgentWrites},
    "episode_writer": {"reads": EpisodeWriterReads, "writes": EpisodeWriterWrites},
}
