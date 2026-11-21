"""LangGraph path: supervisor -> router -> code_review -> episode_writer.

RECONCILE with langgraph_nodes_pseudocode.py if node behavior differs.
Checkpoints use LangGraph's in-memory saver so this slice runs without a
database server. RECONCILE with langgraph_state_management.md if that
document names a Postgres checkpointer instead.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from midplane.models import ModelError, ModelProvider, provider_from_env
from midplane.prompts import CODE_REVIEW_PROMPT_REF, render_code_review_prompt
from midplane.routing import (
    CostLatencyRoutingPolicy,
    RoutingPolicy,
    budget_from_env,
    choose_model,
    route_table_from_env,
)
from midplane.scoring import stub_quality_score
from midplane.state import MidPlaneState, initial_state
from midplane.store import Episode, EpisodeStore, open_store

_BANNED_TOOL_TOKENS = ("broker", "trading", "exchange", "alpaca", "binance", "ccxt", "oanda")
_SEVERITIES = {"info", "warn", "block"}


def tool_allowlist() -> tuple[str, ...]:
    """Tools registered on this slice. The list is empty on purpose."""

    return ()


def validate_tool_allowlist(tools: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    names = tuple(tools)
    for name in names:
        lowered = name.lower()
        if any(token in lowered for token in _BANNED_TOOL_TOKENS):
            raise ValueError(f"{name} cannot be registered: this slice has no broker or trading tool")
    if names:
        raise ValueError("this slice's tool allowlist is empty")
    return ()


def parse_review(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    summary = data.get("summary")
    findings = data.get("findings")
    if not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(findings, list) or not findings:
        return None
    cleaned: list[dict] = []
    for item in findings:
        if not isinstance(item, dict):
            return None
        severity = item.get("severity")
        detail = item.get("detail")
        ref = item.get("ref", "")
        if severity not in _SEVERITIES or not isinstance(detail, str) or not detail.strip():
            return None
        cleaned.append({"severity": severity, "detail": detail, "ref": str(ref)})
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    return {"summary": summary.strip(), "findings": cleaned, "confidence": confidence}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timing(node: str, started: float, state: Mapping, **event_fields: object) -> dict:
    elapsed = int((time.perf_counter() - started) * 1000)
    event = {
        "node": node,
        "event": "end",
        "latency_ms": elapsed,
        "at": _now(),
        **event_fields,
    }
    return {
        "trace": [event],
        "latency_ms": int(state.get("latency_ms") or 0) + elapsed,
    }


def _empty_cost() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_usd": 0.0,
        "by_model": {},
    }


def _add_usage(cost: dict, model_id: str, input_tokens: int, output_tokens: int, cost_usd: float) -> dict:
    by_model = dict(cost.get("by_model") or {})
    by_model[model_id] = round(float(by_model.get(model_id, 0.0)) + cost_usd, 8)
    return {
        "input_tokens": int(cost.get("input_tokens", 0)) + input_tokens,
        "output_tokens": int(cost.get("output_tokens", 0)) + output_tokens,
        "total_usd": round(float(cost.get("total_usd", 0.0)) + cost_usd, 8),
        "by_model": by_model,
    }


def route_or_write(state: MidPlaneState) -> str:
    if state.get("error"):
        return "episode_writer"
    return "router"


def review_or_write(state: MidPlaneState) -> str:
    if state.get("error"):
        return "episode_writer"
    return "code_review"


def write_episode(state: MidPlaneState) -> str:
    return "episode_writer"


@dataclass
class GraphDeps:
    provider: ModelProvider
    store: EpisodeStore
    policy: RoutingPolicy
    route_table: dict
    budget: dict
    tools: tuple[str, ...] = ()


@dataclass
class RunResult:
    run_id: str
    thread_id: str
    state: dict
    episode: Episode
    graph: object


def _call_model(provider: ModelProvider, model_id: str, prompt: str, role: str) -> tuple[dict, dict | None]:
    prompt_tokens = max(1, len(prompt) // 4)
    try:
        response = provider.complete(model_id=model_id, prompt=prompt)
    except (ModelError, TimeoutError) as exc:
        return (
            {
                "kind": "attempt",
                "model_id": model_id,
                "role": role,
                "status": "error",
                "error": str(exc),
                "input_tokens": prompt_tokens,
                "output_tokens": 0,
                "cost_usd": 0.0,
            },
            None,
        )
    parsed = parse_review(response.text)
    return (
        {
            "kind": "attempt",
            "model_id": model_id,
            "role": role,
            "status": "success" if parsed is not None else "error",
            "error": None if parsed is not None else "unparseable",
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
        },
        parsed,
    )


def build_graph(deps: GraphDeps, checkpointer=None):
    validate_tool_allowlist(deps.tools)

    def supervisor(state: MidPlaneState) -> dict:
        started = time.perf_counter()
        task = state.get("task") or {}
        role = task.get("role")
        source = state.get("source")
        update: dict = {}
        if source not in {"dev_guild", "trading", "app"}:
            update["error"] = f"unsupported source: {source}"
            update["outcome"] = "error"
        elif role != "code_review":
            update["error"] = f"unsupported role: {role}"
            update["outcome"] = "error"
        else:
            update["plan"] = {"steps": ["code_review"], "prompt_ref": CODE_REVIEW_PROMPT_REF}
        update.update(_timing("supervisor", started, state))
        return update

    def router(state: MidPlaneState) -> dict:
        started = time.perf_counter()
        steps = (state.get("plan") or {}).get("steps") or []
        task_type = steps[0] if steps else ""
        choice = choose_model(task_type, deps.budget, deps.route_table, policy=deps.policy)
        update: dict = {
            "route": {
                "primary": choice.get("primary"),
                "fallback": choice.get("fallback"),
                "criteria": choice.get("criteria"),
            },
            "model_choices": [choice],
        }
        if choice.get("error") or not choice.get("primary"):
            update["error"] = str(choice.get("error") or "no_eligible_model")
            update["outcome"] = "error"
        update.update(_timing("router", started, state))
        return update

    def code_review(state: MidPlaneState) -> dict:
        started = time.perf_counter()
        task = state.get("task") or {}
        route = state.get("route") or {}
        prompt = render_code_review_prompt(
            repo=str(task.get("repo", "")),
            instructions=str(task.get("instructions", "")),
            diff=str(task.get("diff", "")),
        )
        attempts: list[dict] = []
        cost = _empty_cost()
        parsed: dict | None = None
        primary = route.get("primary")
        fallback = route.get("fallback")
        if not primary:
            update = {
                "error": "router did not select a model",
                "outcome": "error",
                "output": {},
                "cost": cost,
                "tool_calls": [],
            }
            update.update(_timing("code_review", started, state, model_attempts=attempts, cost=cost))
            return update

        attempt, parsed = _call_model(deps.provider, primary, prompt, "primary")
        attempts.append(attempt)
        cost = _add_usage(
            cost,
            primary,
            attempt["input_tokens"],
            attempt["output_tokens"],
            attempt["cost_usd"],
        )
        if parsed is None and fallback and fallback != primary:
            attempt, parsed = _call_model(deps.provider, fallback, prompt, "fallback")
            attempts.append(attempt)
            cost = _add_usage(
                cost,
                fallback,
                attempt["input_tokens"],
                attempt["output_tokens"],
                attempt["cost_usd"],
            )

        update = {
            "model_choices": attempts,
            "tool_calls": [],
            "cost": cost,
        }
        if parsed is None:
            update["error"] = "code_review failed on the routed models"
            update["outcome"] = "error"
            update["output"] = {}
        else:
            update["outcome"] = "success"
            update["output"] = parsed
        update.update(
            _timing(
                "code_review",
                started,
                state,
                model_attempts=attempts,
                tokens={
                    "input_tokens": cost["input_tokens"],
                    "output_tokens": cost["output_tokens"],
                },
                cost=cost,
            )
        )
        return update

    def episode_writer(state: MidPlaneState) -> dict:
        started = time.perf_counter()
        error = state.get("error")
        outcome = state.get("outcome")
        if outcome not in {"success", "error"}:
            outcome = "error" if error else "success"
        output = state.get("output") or {}
        tool_calls = list(state.get("tool_calls") or [])
        model_choices = list(state.get("model_choices") or [])
        cost = state.get("cost") or _empty_cost()
        plan = state.get("plan") or {}
        task = state.get("task") or {}
        elapsed = int((time.perf_counter() - started) * 1000)
        latency_ms = int(state.get("latency_ms") or 0) + elapsed
        prior = [event for event in (state.get("trace") or []) if isinstance(event, dict)]
        event = {
            "node": "episode_writer",
            "event": "end",
            "latency_ms": elapsed,
            "at": _now(),
            "node_sequence": [item.get("node") for item in prior if item.get("event") == "end"]
            + ["episode_writer"],
            "model_choices": model_choices,
            "tokens": {
                "input_tokens": int(cost.get("input_tokens", 0) or 0),
                "output_tokens": int(cost.get("output_tokens", 0) or 0),
            },
            "cost": cost,
        }
        full_trace = prior + [event]
        score_state = {
            "run_id": state.get("run_id"),
            "source": state.get("source"),
            "task": task,
            "plan": plan,
            "model_choices": model_choices,
            "tool_calls": tool_calls,
            "output": output,
            "cost": cost,
            "latency_ms": latency_ms,
            "outcome": outcome,
            "trace": full_trace,
            "error": error,
        }
        quality_score = stub_quality_score(score_state)
        episode = Episode(
            run_id=str(state["run_id"]),
            source=str(state["source"]),
            inputs=dict(task),
            plan=dict(plan),
            tool_calls=tool_calls,
            model_choices=model_choices,
            outputs=dict(output) if isinstance(output, dict) else {"value": output},
            cost=dict(cost),
            latency_ms=latency_ms,
            outcome=str(outcome),
            quality_score=quality_score,
            trace=full_trace,
        )
        deps.store.upsert(episode)
        return {
            "trace": [event],
            "latency_ms": latency_ms,
            "outcome": outcome,
            "output": output if isinstance(output, dict) else {},
            "cost": cost,
            "quality_score": quality_score,
        }

    graph = StateGraph(MidPlaneState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("router", router)
    graph.add_node("code_review", code_review)
    graph.add_node("episode_writer", episode_writer)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", route_or_write, ["router", "episode_writer"])
    graph.add_conditional_edges("router", review_or_write, ["code_review", "episode_writer"])
    graph.add_conditional_edges("code_review", write_episode, ["episode_writer"])
    graph.add_edge("episode_writer", END)
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else MemorySaver())


def _start_run(
    diff: str,
    *,
    repo: str,
    instructions: str,
    run_id: str | None,
    thread_id: str | None,
    source: str,
    role: str,
    task: dict | None,
    store: EpisodeStore | None,
    provider: ModelProvider | None,
    policy: RoutingPolicy | None,
    route_table: dict | None,
    budget: dict | None,
    checkpointer,
):
    resolved_run_id = run_id or str(uuid.uuid4())
    resolved_thread_id = thread_id or resolved_run_id
    deps = GraphDeps(
        provider=provider if provider is not None else provider_from_env(),
        store=store if store is not None else open_store(),
        policy=policy if policy is not None else CostLatencyRoutingPolicy(),
        route_table=route_table if route_table is not None else route_table_from_env(),
        budget=budget if budget is not None else budget_from_env(),
        tools=tool_allowlist(),
    )
    graph = build_graph(deps, checkpointer=checkpointer)
    incoming = initial_state(
        run_id=resolved_run_id,
        thread_id=resolved_thread_id,
        diff=diff,
        repo=repo,
        instructions=instructions,
        source=source,  # type: ignore[arg-type]
        role=role,
    )
    if task is not None:
        incoming["task"] = task
    config = {"configurable": {"thread_id": resolved_thread_id}}
    return deps, graph, incoming, config, resolved_run_id, resolved_thread_id


def _result_from_state(deps: GraphDeps, graph, final: dict, run_id: str, thread_id: str) -> RunResult:
    episode = deps.store.get(run_id)
    if episode is None:
        raise RuntimeError("episode_writer did not persist a row")
    return RunResult(
        run_id=run_id,
        thread_id=thread_id,
        state=final,
        episode=episode,
        graph=graph,
    )


def run_review(
    diff: str,
    *,
    repo: str = "local",
    instructions: str = "Review this diff.",
    run_id: str | None = None,
    thread_id: str | None = None,
    source: str = "dev_guild",
    role: str = "code_review",
    task: dict | None = None,
    store: EpisodeStore | None = None,
    provider: ModelProvider | None = None,
    policy: RoutingPolicy | None = None,
    route_table: dict | None = None,
    budget: dict | None = None,
    checkpointer=None,
) -> RunResult:
    deps, graph, incoming, config, resolved_run_id, resolved_thread_id = _start_run(
        diff,
        repo=repo,
        instructions=instructions,
        run_id=run_id,
        thread_id=thread_id,
        source=source,
        role=role,
        task=task,
        store=store,
        provider=provider,
        policy=policy,
        route_table=route_table,
        budget=budget,
        checkpointer=checkpointer,
    )
    final = graph.invoke(incoming, config=config)
    return _result_from_state(deps, graph, final, resolved_run_id, resolved_thread_id)


def stream_review(
    diff: str,
    *,
    repo: str = "local",
    instructions: str = "Review this diff.",
    run_id: str | None = None,
    thread_id: str | None = None,
    source: str = "dev_guild",
    role: str = "code_review",
    task: dict | None = None,
    store: EpisodeStore | None = None,
    provider: ModelProvider | None = None,
    policy: RoutingPolicy | None = None,
    route_table: dict | None = None,
    budget: dict | None = None,
    checkpointer=None,
):
    """Yield one event per finished node, then a terminal event with the episode.

    Node events are ``{"type": "node", "node": "<name>", "status": "completed"}``.
    The last event is ``{"type": "done", "episode_id", "outcome", "output"}``.
    """

    deps, graph, incoming, config, resolved_run_id, _thread_id = _start_run(
        diff,
        repo=repo,
        instructions=instructions,
        run_id=run_id,
        thread_id=thread_id,
        source=source,
        role=role,
        task=task,
        store=store,
        provider=provider,
        policy=policy,
        route_table=route_table,
        budget=budget,
        checkpointer=checkpointer,
    )
    for update in graph.stream(incoming, config=config, stream_mode="updates"):
        if not isinstance(update, dict):
            continue
        for node_name in update:
            yield {"type": "node", "node": str(node_name), "status": "completed"}
    episode = deps.store.get(resolved_run_id)
    if episode is None:
        raise RuntimeError("episode_writer did not persist a row")
    yield {
        "type": "done",
        "episode_id": episode.run_id,
        "outcome": episode.outcome,
        "output": episode.outputs,
        "quality_score": episode.quality_score,
    }
