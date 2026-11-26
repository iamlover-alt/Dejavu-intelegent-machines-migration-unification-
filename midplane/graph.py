"""LangGraph path: supervisor -> router -> code_review | schema_agent -> episode_writer.

``MIDPLANE_RETRIEVAL=1`` inserts ``retrieve_context`` between the router and
code_review, and makes ``schema_agent`` pull schema and connector documents
before it answers. The default is off, so those edges and that prompt stay
as written here.

RECONCILE with langgraph_nodes_pseudocode.py if node behavior differs.
Checkpoints use LangGraph's in-memory saver so this slice runs without a
database server. RECONCILE with langgraph_state_management.md if that
document names a Postgres checkpointer instead.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from midplane.agents.schema_agent import AGENT_NAME, build_schema_result
from midplane.connectors import ConnectorConstraints
from midplane.models import ModelError, ModelProvider, provider_from_env
from midplane.prompts import (
    CODE_REVIEW_PROMPT_REF,
    render_code_review_prompt,
    render_schema_agent_prompt,
    schema_prompt_version,
)
from midplane.retrieval.chain import (
    DEFAULT_RETRIEVAL_STEPS,
    ChainStep,
    RetrievalChain,
    retrieval_payload,
    retrieval_prefix,
)
from midplane.retrieval.episodes import EpisodeMemoryStore
from midplane.retrieval.schema_store import (
    SCHEMA_AGENT_TOP_K,
    SchemaConnectorStore,
    SchemaDocumentLoader,
    registry_context_prefix,
    retrieve_schema_and_connector_docs,
)
from midplane.retrieval.traces import DecisionTraceStore
from midplane.routing import (
    CostLatencyRoutingPolicy,
    RoutingPolicy,
    budget_from_env,
    choose_model,
    route_table_from_env,
)
from midplane.scoring import HeuristicQualityScorer, QualityScorer
from midplane.state import MidPlaneState, initial_state
from midplane.store import Episode, EpisodeStore, episode_timestamp, open_store

_BANNED_TOOL_TOKENS = ("broker", "trading", "exchange", "alpaca", "binance", "ccxt", "oanda")
_SEVERITIES = {"info", "warn", "block"}
_SCHEMA_QUESTION = ("schema", "connector", "integration")


def resolve_worker(task: Mapping) -> str | None:
    """Pick code_review or schema_agent. Unknown roles stay unrouted.

    An explicit code_review role stays on code_review even when the diff
    mentions a schema. Questions with role auto, question, or empty route to
    the schema agent when they ask about schemas, connectors, or integrations.
    """

    role = str(task.get("role") or "").strip().lower().replace("-", "_")
    if role in {"code_review", "review"}:
        return "code_review"
    if role in {"schema_agent", "schema"}:
        return "schema_agent"
    if role not in {"", "auto", "question"}:
        return None
    text = " ".join(str(task.get(key) or "") for key in ("goal", "instructions", "question")).lower()
    if any(token in text for token in _SCHEMA_QUESTION):
        return "schema_agent"
    return None


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
    steps = (state.get("plan") or {}).get("steps") or []
    if steps and steps[0] == "schema_agent":
        return "schema_agent"
    return "code_review"


def write_episode(state: MidPlaneState) -> str:
    return "episode_writer"


def retrieval_enabled_from_env(env: Mapping[str, str] | None = None) -> bool:
    """Retrieval stays off unless this is on.

    When it is on, code review runs ``retrieve_context`` and the schema agent
    loads registry documents before it answers. The default is off.
    """

    source = os.environ if env is None else env
    return source.get("MIDPLANE_RETRIEVAL", "").strip().lower() in {"1", "true", "yes", "on"}


def _registry_schema_store(deps: GraphDeps) -> SchemaConnectorStore:
    """Schema and connector documents from the registries.

    The index stays in memory so it does not share the pgvector rows the
    built-in HTTP loader writes under the same store name.
    """

    if deps.schema_documents is None:
        from midplane.retrieval.embedder import HashedNgramEmbedder
        from midplane.retrieval.index import InMemoryVectorIndex

        embedder = HashedNgramEmbedder()
        deps.schema_documents = SchemaConnectorStore(
            loader=SchemaDocumentLoader(),
            embedder=embedder,
            index=InMemoryVectorIndex(embedder.dimension),
        )
    return deps.schema_documents


def _store_url(store: EpisodeStore) -> str | None:
    dsn = getattr(store, "dsn", None)
    if isinstance(dsn, str) and dsn:
        return dsn
    path = getattr(store, "path", None)
    if path == ":memory:":
        return "sqlite:///:memory:"
    if isinstance(path, str) and path:
        return "sqlite:///" + path
    return None


@dataclass
class GraphDeps:
    provider: ModelProvider
    store: EpisodeStore
    policy: RoutingPolicy
    route_table: dict
    budget: dict
    tools: tuple[str, ...] = ()
    scorer: QualityScorer | None = None
    retrieval_enabled: bool = False
    episode_memory: EpisodeMemoryStore | None = None
    decision_traces: DecisionTraceStore | None = None
    retrieval_chain: RetrievalChain | None = None
    retrieval_steps: tuple[ChainStep, ...] = ()
    schema_documents: SchemaConnectorStore | None = None
    schema_retrieval_top_k: int = SCHEMA_AGENT_TOP_K


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


def _complete_text(provider: ModelProvider, model_id: str, prompt: str, role: str) -> dict:
    """One completion. Any text is success. Provider failures are errors."""

    prompt_tokens = max(1, len(prompt) // 4)
    try:
        response = provider.complete(model_id=model_id, prompt=prompt)
    except (ModelError, TimeoutError) as exc:
        return {
            "kind": "attempt",
            "model_id": model_id,
            "role": role,
            "status": "error",
            "error": str(exc),
            "input_tokens": prompt_tokens,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "ok": False,
        }
    return {
        "kind": "attempt",
        "model_id": model_id,
        "role": role,
        "status": "success",
        "error": None,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": response.cost_usd,
        "ok": True,
    }


def build_graph(deps: GraphDeps, checkpointer=None):
    validate_tool_allowlist(deps.tools)

    def supervisor(state: MidPlaneState) -> dict:
        started = time.perf_counter()
        task = state.get("task") or {}
        role = task.get("role")
        source = state.get("source")
        worker = resolve_worker(task)
        update: dict = {}
        if source not in {"dev_guild", "trading", "app"}:
            update["error"] = f"unsupported source: {source}"
            update["outcome"] = "error"
        elif worker is None:
            update["error"] = f"unsupported role: {role}"
            update["outcome"] = "error"
        elif worker == "code_review":
            update["plan"] = {"steps": ["code_review"], "prompt_ref": CODE_REVIEW_PROMPT_REF}
        else:
            update["plan"] = {
                "steps": ["schema_agent"],
                "prompt_ref": "schema_agent",
                "prompt_version": schema_prompt_version(),
                "agent": AGENT_NAME,
            }
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
        if deps.decision_traces is not None:
            options = [
                str(model.get("id"))
                for model in deps.route_table.get("models", [])
                if isinstance(model, dict) and task_type in model.get("task_types", [])
            ]
            criteria = choice.get("criteria") or {}
            deps.decision_traces.record(
                agent="router",
                run_id=str(state.get("run_id") or ""),
                episode_id=str(state.get("run_id") or ""),
                decision_point="model_routing",
                options=options,
                choice=str(choice.get("primary") or ""),
                rationale=(
                    f"primary={choice.get('primary')} fallback={choice.get('fallback')} "
                    f"criteria={json.dumps(criteria, sort_keys=True)}"
                ),
                outcome="unknown",
            )
        update.update(_timing("router", started, state))
        return update

    def code_review(state: MidPlaneState) -> dict:
        started = time.perf_counter()
        task = state.get("task") or {}
        route = state.get("route") or {}
        prompt = retrieval_prefix(state.get("retrieval") or {}) + render_code_review_prompt(
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

    def schema_agent(state: MidPlaneState) -> dict:
        started = time.perf_counter()
        task = state.get("task") or {}
        route = state.get("route") or {}
        goal = str(task.get("goal") or task.get("instructions") or "").strip()
        constraints = ConnectorConstraints.from_mapping(task.get("constraints"))
        attempts: list[dict] = []
        cost = _empty_cost()
        retrieved_ids: list[str] | None = None
        retrieval_update: dict = {}
        if not goal:
            update = {
                "error": "schema_agent requires a goal",
                "outcome": "error",
                "output": {},
                "cost": cost,
                "tool_calls": [],
            }
            update.update(_timing("schema_agent", started, state, model_attempts=attempts, cost=cost))
            return update
        prompt = render_schema_agent_prompt(goal=goal, constraints=constraints.to_dict())
        if deps.retrieval_enabled:
            hits = retrieve_schema_and_connector_docs(
                _registry_schema_store(deps),
                goal,
                top_k=deps.schema_retrieval_top_k,
            )
            retrieved_ids = [hit.doc_id for hit in hits]
            retrieval_update = {
                "retrieval": {
                    "doc_ids": list(retrieved_ids),
                    "hits": [
                        {
                            "doc_id": hit.doc_id,
                            "store": hit.store,
                            "kind": hit.metadata.get("kind"),
                            "score": hit.score,
                            "text": hit.text[:300],
                        }
                        for hit in hits
                    ],
                }
            }
            prompt = registry_context_prefix(hits) + prompt
            if deps.decision_traces is not None:
                deps.decision_traces.record(
                    agent="schema_agent",
                    run_id=str(state.get("run_id") or ""),
                    episode_id=str(state.get("run_id") or ""),
                    decision_point="schema_retrieval",
                    options=retrieved_ids,
                    choice=retrieved_ids[0] if retrieved_ids else "",
                    rationale=(
                        "retrieved schema and connector documents before answering: "
                        + ", ".join(retrieved_ids)
                    ),
                    outcome="unknown",
                )
        primary = route.get("primary")
        fallback = route.get("fallback")

        def _finish(update: dict) -> dict:
            if retrieved_ids is not None:
                update.update(retrieval_update)
            timing_fields: dict = {"model_attempts": attempts, "cost": cost}
            if attempts:
                timing_fields["tokens"] = {
                    "input_tokens": cost["input_tokens"],
                    "output_tokens": cost["output_tokens"],
                }
            if retrieved_ids is not None:
                timing_fields["retrieved_doc_ids"] = list(retrieved_ids)
            update.update(_timing("schema_agent", started, state, **timing_fields))
            return update

        if not primary:
            return _finish(
                {
                    "error": "router did not select a model",
                    "outcome": "error",
                    "output": {},
                    "cost": cost,
                    "tool_calls": [],
                }
            )

        def _absorb(model_id: str, role: str) -> bool:
            nonlocal cost
            attempt = _complete_text(deps.provider, model_id, prompt, role)
            ok = bool(attempt.pop("ok"))
            attempts.append(attempt)
            cost = _add_usage(
                cost,
                model_id,
                attempt["input_tokens"],
                attempt["output_tokens"],
                attempt["cost_usd"],
            )
            return ok

        ok = _absorb(primary, "primary")
        if not ok and fallback and fallback != primary:
            ok = _absorb(fallback, "fallback")
        update = {"model_choices": attempts, "cost": cost}
        if not ok:
            update["error"] = "schema_agent failed on the routed models"
            update["outcome"] = "error"
            update["output"] = {}
            update["tool_calls"] = []
        else:
            result, tool_calls = build_schema_result(goal, constraints)
            update["outcome"] = "success"
            update["output"] = result.to_dict()
            update["tool_calls"] = tool_calls
        return _finish(update)

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
        scorer = deps.scorer if deps.scorer is not None else HeuristicQualityScorer()
        quality_score = scorer.score(score_state)
        created_at = state.get("created_at") or episode_timestamp()
        score_version = scorer.version
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
            score_version=score_version,
            created_at=str(created_at),
        )
        deps.store.upsert(episode)
        if deps.episode_memory is not None:
            deps.episode_memory.index_episode(episode)
        if deps.decision_traces is not None:
            primary = ""
            for item in model_choices:
                if isinstance(item, dict) and item.get("kind") == "route":
                    primary = str(item.get("primary") or "")
                    break
            deps.decision_traces.complete_run(
                run_id=str(state["run_id"]),
                episode_id=str(state["run_id"]),
                outcome="success" if outcome == "success" else "fail",
                outcome_score=quality_score,
                choice=primary or str(outcome),
                rationale=(
                    f"quality_score={quality_score} score_version={score_version} outcome={outcome}"
                ),
                agent="episode_writer",
            )
        return {
            "trace": [event],
            "latency_ms": latency_ms,
            "outcome": outcome,
            "output": output if isinstance(output, dict) else {},
            "cost": cost,
            "quality_score": quality_score,
            "score_version": score_version,
            "created_at": str(created_at),
        }

    def retrieve_context(state: MidPlaneState) -> dict:
        started = time.perf_counter()
        task = state.get("task") or {}
        query = " ".join(
            part
            for part in (
                str(task.get("instructions") or ""),
                str(task.get("repo") or ""),
                "code review",
            )
            if part
        )
        if deps.retrieval_chain is None:
            payload = {"hits": [], "hops": [], "truncated": False, "fused": False}
        else:
            result = deps.retrieval_chain.run(query, deps.retrieval_steps or DEFAULT_RETRIEVAL_STEPS)
            payload = retrieval_payload(result)
        update = {"retrieval": payload}
        update.update(_timing("retrieve_context", started, state, hits=len(payload["hits"])))
        return update

    def review_or_retrieve(state: MidPlaneState) -> str:
        if state.get("error"):
            return "episode_writer"
        steps = (state.get("plan") or {}).get("steps") or []
        if steps and steps[0] == "schema_agent":
            return "schema_agent"
        return "retrieve_context"

    graph = StateGraph(MidPlaneState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("router", router)
    graph.add_node("code_review", code_review)
    graph.add_node("schema_agent", schema_agent)
    graph.add_node("episode_writer", episode_writer)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", route_or_write, ["router", "episode_writer"])
    if deps.retrieval_enabled:
        graph.add_node("retrieve_context", retrieve_context)
        graph.add_conditional_edges(
            "router",
            review_or_retrieve,
            ["retrieve_context", "schema_agent", "episode_writer"],
        )
        graph.add_edge("retrieve_context", "code_review")
    else:
        graph.add_conditional_edges(
            "router",
            review_or_write,
            ["code_review", "schema_agent", "episode_writer"],
        )
    graph.add_conditional_edges("code_review", write_episode, ["episode_writer"])
    graph.add_conditional_edges("schema_agent", write_episode, ["episode_writer"])
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
    scorer: QualityScorer | None,
    retrieval_enabled: bool | None,
    episode_memory: EpisodeMemoryStore | None,
    decision_traces: DecisionTraceStore | None,
    retrieval_chain: RetrievalChain | None,
    retrieval_steps: tuple[ChainStep, ...] | None,
    schema_documents: SchemaConnectorStore | None,
    schema_retrieval_top_k: int | None,
):
    resolved_run_id = run_id or str(uuid.uuid4())
    resolved_thread_id = thread_id or resolved_run_id
    resolved_store = store if store is not None else open_store()
    resolved_provider = provider if provider is not None else provider_from_env()
    enabled = retrieval_enabled_from_env() if retrieval_enabled is None else retrieval_enabled
    memory = episode_memory
    traces = decision_traces
    chain = retrieval_chain
    steps = retrieval_steps if retrieval_steps is not None else ()
    if enabled and memory is None:
        memory = EpisodeMemoryStore(database_url=_store_url(resolved_store))
        memory.reindex(resolved_store)
    if enabled and traces is None:
        url = _store_url(resolved_store)
        trace_url = url if url and not url.endswith(":memory:") else "sqlite:///:memory:"
        traces = DecisionTraceStore(trace_url)
    if enabled and chain is None:
        schema = SchemaConnectorStore(database_url=_store_url(resolved_store))
        chain = RetrievalChain(
            stores={"episodes": memory, "schema": schema, "traces": traces},
            max_hops=2,
            provider=resolved_provider,
        )
        if not steps:
            steps = DEFAULT_RETRIEVAL_STEPS
    deps = GraphDeps(
        provider=resolved_provider,
        store=resolved_store,
        policy=policy if policy is not None else CostLatencyRoutingPolicy(),
        route_table=route_table if route_table is not None else route_table_from_env(),
        budget=budget if budget is not None else budget_from_env(),
        tools=tool_allowlist(),
        scorer=scorer if scorer is not None else HeuristicQualityScorer(),
        retrieval_enabled=enabled,
        episode_memory=memory,
        decision_traces=traces,
        retrieval_chain=chain,
        retrieval_steps=steps,
        schema_documents=schema_documents,
        schema_retrieval_top_k=SCHEMA_AGENT_TOP_K if schema_retrieval_top_k is None else schema_retrieval_top_k,
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
    scorer: QualityScorer | None = None,
    retrieval_enabled: bool | None = None,
    episode_memory: EpisodeMemoryStore | None = None,
    decision_traces: DecisionTraceStore | None = None,
    retrieval_chain: RetrievalChain | None = None,
    retrieval_steps: tuple[ChainStep, ...] | None = None,
    schema_documents: SchemaConnectorStore | None = None,
    schema_retrieval_top_k: int | None = None,
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
        scorer=scorer,
        retrieval_enabled=retrieval_enabled,
        episode_memory=episode_memory,
        decision_traces=decision_traces,
        retrieval_chain=retrieval_chain,
        retrieval_steps=retrieval_steps,
        schema_documents=schema_documents,
        schema_retrieval_top_k=schema_retrieval_top_k,
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
    scorer: QualityScorer | None = None,
    retrieval_enabled: bool | None = None,
    episode_memory: EpisodeMemoryStore | None = None,
    decision_traces: DecisionTraceStore | None = None,
    retrieval_chain: RetrievalChain | None = None,
    retrieval_steps: tuple[ChainStep, ...] | None = None,
    schema_documents: SchemaConnectorStore | None = None,
    schema_retrieval_top_k: int | None = None,
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
        scorer=scorer,
        retrieval_enabled=retrieval_enabled,
        episode_memory=episode_memory,
        decision_traces=decision_traces,
        retrieval_chain=retrieval_chain,
        retrieval_steps=retrieval_steps,
        schema_documents=schema_documents,
        schema_retrieval_top_k=schema_retrieval_top_k,
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
