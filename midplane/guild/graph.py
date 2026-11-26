"""LangGraph path: supervisor -> router -> website.

The website node is a compiled worker step. Its structured result comes from
the rule core. The fake provider is consulted and cannot replace that result
unless it echoes it. This graph does not write episodes and does not call
the network.
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
from pydantic import ValidationError

from midplane.agents.website.audit import audit
from midplane.agents.website.guide import implementation_guide
from midplane.agents.website.copy import write_copy
from midplane.agents.website.schemas import CopyBrief, GuideTarget, SiteSnapshot
from midplane.guild.prompts import WEBSITE_PROMPT_REF, render_website_prompt
from midplane.guild.provider import ModelError, ModelProvider, provider_from_env
from midplane.guild.registry import WEBSITE_WORKER, registry, validate_tools
from midplane.guild.routing import (
    CostLatencyRoutingPolicy,
    RoutingPolicy,
    budget_from_env,
    choose_model,
    is_website_request,
    route_table_from_env,
)
from midplane.guild.state import GuildState, initial_state


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timing(node: str, started: float, state: Mapping) -> dict:
    elapsed = int((time.perf_counter() - started) * 1000)
    return {
        "trace": [
            {
                "node": node,
                "event": "end",
                "latency_ms": elapsed,
                "at": _now(),
            }
        ],
        "latency_ms": int(state.get("latency_ms") or 0) + elapsed,
    }


def _empty_cost() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "total_usd": 0.0, "by_model": {}}


def _add_usage(cost: dict, model_id: str, input_tokens: int, output_tokens: int, cost_usd: float) -> dict:
    by_model = dict(cost.get("by_model") or {})
    by_model[model_id] = round(float(by_model.get(model_id, 0.0)) + cost_usd, 8)
    return {
        "input_tokens": int(cost.get("input_tokens", 0)) + input_tokens,
        "output_tokens": int(cost.get("output_tokens", 0)) + output_tokens,
        "total_usd": round(float(cost.get("total_usd", 0.0)) + cost_usd, 8),
        "by_model": by_model,
    }


def _encode(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _infer_action(task: Mapping) -> str | None:
    action = task.get("action")
    if isinstance(action, str) and action.strip():
        return action.strip()
    text = str(task.get("text") or task.get("instructions") or "").lower()
    if any(token in text for token in ("implementation guide", "squarespace guide", "build the site")):
        return "implementation_guide"
    if "copy" in text or text.startswith("write"):
        return "write_copy"
    if any(token in text for token in ("audit", "review the site", "check the site")):
        return "audit"
    if task.get("snapshot") is not None:
        return "audit"
    if task.get("brief") is not None:
        return "write_copy"
    if task.get("target") is not None:
        return "implementation_guide"
    return None


def _run_core(task: Mapping) -> tuple[str, dict]:
    action = _infer_action(task)
    if action == "audit":
        snapshot = SiteSnapshot.model_validate(task.get("snapshot"))
        return action, audit(snapshot).model_dump(mode="json")
    if action == "write_copy":
        page = task.get("page") or "home"
        brief = CopyBrief.model_validate(task.get("brief") or {"facts": [], "sections": []})
        return action, write_copy(page, brief).model_dump(mode="json")
    if action == "implementation_guide":
        raw_target = task.get("target") or "build"
        if isinstance(raw_target, str):
            target = GuideTarget(action=raw_target)  # type: ignore[arg-type]
        else:
            target = GuideTarget.model_validate(raw_target)
        return action, implementation_guide(target).model_dump(mode="json")
    raise ValueError("website request needs an action: audit, write_copy, or implementation_guide")


def route_or_end(state: GuildState) -> str:
    if state.get("error"):
        return "end"
    return "router"


def work_or_end(state: GuildState) -> str:
    if state.get("error"):
        return "end"
    return "website"


@dataclass
class GraphDeps:
    provider: ModelProvider
    policy: RoutingPolicy
    route_table: dict
    budget: dict
    tools: tuple[str, ...] = ()


@dataclass
class RunResult:
    run_id: str
    thread_id: str
    state: dict
    graph: object


def build_graph(deps: GraphDeps, checkpointer=None):
    validate_tools(deps.tools)
    if deps.tools:
        raise ValueError("the website worker's tool allowlist is empty")

    def supervisor(state: GuildState) -> dict:
        started = time.perf_counter()
        task = state.get("task") or {}
        source = state.get("source")
        update: dict = {}
        if source not in {"dev_guild", "trading", "app"}:
            update["error"] = f"unsupported source: {source}"
            update["outcome"] = "error"
        elif not is_website_request(task):
            update["error"] = "unsupported role: website agent is not selected"
            update["outcome"] = "error"
        elif registry().get(WEBSITE_WORKER.role_id) is None:
            update["error"] = "website worker is not registered"
            update["outcome"] = "error"
        else:
            action = _infer_action(task)
            if action not in {"audit", "write_copy", "implementation_guide"}:
                update["error"] = (
                    "website request needs an action: audit, write_copy, or implementation_guide"
                )
                update["outcome"] = "error"
            else:
                update["plan"] = {
                    "steps": [WEBSITE_WORKER.role_id],
                    "action": action,
                    "prompt_ref": WEBSITE_PROMPT_REF,
                    "worker": WEBSITE_WORKER.role_id,
                }
        update.update(_timing("supervisor", started, state))
        return update

    def router(state: GuildState) -> dict:
        started = time.perf_counter()
        spec = registry()[WEBSITE_WORKER.role_id]
        choice = choose_model(spec.task_type, deps.budget, deps.route_table, policy=deps.policy)
        choice = dict(choice)
        choice["worker"] = spec.role_id
        update: dict = {
            "route": {
                "worker": spec.role_id,
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

    def website(state: GuildState) -> dict:
        started = time.perf_counter()
        task = state.get("task") or {}
        route = state.get("route") or {}
        try:
            action, canonical = _run_core(task)
        except (ValidationError, ValueError) as exc:
            update = {
                "error": str(exc),
                "outcome": "error",
                "output": {},
                "cost": _empty_cost(),
                "tool_calls": [],
            }
            update.update(_timing("website", started, state))
            return update

        encoded = _encode(canonical)
        prompt = render_website_prompt(action=action, encoded_result=encoded)
        attempts: list[dict] = []
        cost = _empty_cost()
        accepted = False
        provider_error: str | None = None
        used_model: str | None = None
        models = [route.get("primary")]
        fallback = route.get("fallback")
        if fallback and fallback != route.get("primary"):
            models.append(fallback)

        for index, model_id in enumerate(models):
            if not model_id:
                provider_error = "router did not select a model"
                break
            role = "primary" if index == 0 else "fallback"
            try:
                response = deps.provider.complete(model_id=str(model_id), prompt=prompt)
            except (ModelError, TimeoutError) as exc:
                attempts.append(
                    {
                        "kind": "attempt",
                        "model_id": model_id,
                        "role": role,
                        "status": "error",
                        "error": str(exc),
                        "input_tokens": max(1, len(prompt) // 4),
                        "output_tokens": 0,
                        "cost_usd": 0.0,
                    }
                )
                provider_error = str(exc)
                continue
            cost = _add_usage(
                cost,
                str(model_id),
                response.input_tokens,
                response.output_tokens,
                response.cost_usd,
            )
            agrees = response.text.strip() == encoded
            attempts.append(
                {
                    "kind": "attempt",
                    "model_id": model_id,
                    "role": role,
                    "status": "success" if agrees else "error",
                    "error": None if agrees else "disagreed",
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cost_usd": response.cost_usd,
                }
            )
            used_model = str(model_id)
            if agrees:
                accepted = True
                provider_error = None
                break
            provider_error = "disagreed"

        update = {
            "model_choices": attempts,
            "tool_calls": [],
            "output": {
                "action": action,
                "worker": WEBSITE_WORKER.role_id,
                "result": canonical,
                "provider": {
                    "accepted": accepted,
                    "model_id": used_model,
                    "error": provider_error,
                },
            },
            "cost": cost,
            "outcome": "success",
            "error": None,
        }
        update.update(_timing("website", started, state))
        return update

    graph = StateGraph(GuildState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("router", router)
    graph.add_node("website", website)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", route_or_end, {"router": "router", "end": END})
    graph.add_conditional_edges("router", work_or_end, {"website": "website", "end": END})
    graph.add_edge("website", END)
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else MemorySaver())


def run_task(
    task: Mapping,
    *,
    provider: ModelProvider | None = None,
    policy: RoutingPolicy | None = None,
    route_table: dict | None = None,
    budget: dict | None = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    source: str = "dev_guild",
    checkpointer=None,
) -> RunResult:
    resolved_run_id = run_id or str(uuid.uuid4())
    resolved_thread_id = thread_id or resolved_run_id
    deps = GraphDeps(
        provider=provider if provider is not None else provider_from_env(),
        policy=policy if policy is not None else CostLatencyRoutingPolicy(),
        route_table=route_table if route_table is not None else route_table_from_env(),
        budget=budget if budget is not None else budget_from_env(),
        tools=registry()[WEBSITE_WORKER.role_id].tools,
    )
    graph = build_graph(deps, checkpointer=checkpointer)
    incoming = initial_state(
        run_id=resolved_run_id,
        thread_id=resolved_thread_id,
        task=dict(task),
        source=source,  # type: ignore[arg-type]
    )
    final = graph.invoke(incoming, config={"configurable": {"thread_id": resolved_thread_id}})
    return RunResult(
        run_id=resolved_run_id,
        thread_id=resolved_thread_id,
        state=final,
        graph=graph,
    )
