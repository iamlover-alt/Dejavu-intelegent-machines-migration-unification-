"""Multi-step utilization plans over the schema and connector catalogs.

Each plan uses only connectors and capabilities select_connectors already
allowed. Real-money trading never appears.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from midplane.connectors import ConnectorConstraints, ConnectorSelection, select_connectors
from midplane.schema_registry import SchemaEntry, lookup_schema

_PRIORITY = {
    "read-episode-flywheel": 0,
    "gateway-contract-trace": 1,
    "github-review-source": 2,
    "workspace-connector-fit": 3,
    "refuse-real-money-trading": 4,
    "paper-control-boundary": 5,
    "llm-routed-reading": 6,
}


@dataclass(frozen=True)
class PlanStep:
    order: int
    action: str
    connector: str
    capability: str
    schema_id: str

    def to_dict(self) -> dict:
        return {
            "order": self.order,
            "action": self.action,
            "connector": self.connector,
            "capability": self.capability,
            "schema_id": self.schema_id,
        }


@dataclass(frozen=True)
class DataFlow:
    from_schema: str
    to_schema: str
    fields: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "from_schema": self.from_schema,
            "to_schema": self.to_schema,
            "fields": list(self.fields),
        }


@dataclass(frozen=True)
class PlannedEpisode:
    source: str
    agent: str
    kind: str
    summary: str

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "agent": self.agent,
            "kind": self.kind,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class UtilizationStrategy:
    id: str
    title: str
    summary: str
    primary_connector: str
    steps: tuple[PlanStep, ...]
    data_flow: tuple[DataFlow, ...]
    risks: tuple[str, ...]
    episodes: tuple[PlannedEpisode, ...]
    connectors: tuple[str, ...]
    schemas: tuple[str, ...]
    keywords: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "primary_connector": self.primary_connector,
            "steps": [step.to_dict() for step in self.steps],
            "data_flow": [item.to_dict() for item in self.data_flow],
            "risks": list(self.risks),
            "episodes": [item.to_dict() for item in self.episodes],
            "connectors": list(self.connectors),
            "schemas": list(self.schemas),
        }


def _cap(selection: ConnectorSelection, name: str, effect: str) -> str | None:
    item = selection.get(name)
    if item is None:
        return None
    for capability in item.allowed_capabilities:
        if capability.effect == effect:
            return capability.id
    return None


def _entry(schema_id: str) -> SchemaEntry | None:
    return lookup_schema(schema_id)


def _flow(source: SchemaEntry, dest: SchemaEntry) -> DataFlow:
    dest_names = {field.name for field in dest.fields}
    shared = tuple(field.name for field in source.fields if field.name in dest_names)
    fields = shared[:4] or tuple(field.name for field in source.fields[:3])
    return DataFlow(from_schema=source.id, to_schema=dest.id, fields=fields)


def _risks(selection: ConnectorSelection, names: tuple[str, ...], schemas: tuple[str, ...]) -> tuple[str, ...]:
    risks: list[str] = []
    for name in names:
        item = selection.get(name)
        if item is None:
            continue
        if item.status == "planned":
            risks.append(f"{name} is planned and not wired into the running platform.")
        if item.kind == "trading":
            risks.append(
                "trading_control is paper-only (real_money false). "
                "Hard external controls are required before any live use. "
                "This plan does not place an order."
            )
    for schema_id in schemas:
        entry = _entry(schema_id)
        if entry is None:
            continue
        for flag in entry.reconcile:
            line = f"{schema_id} is marked RECONCILE with {flag}."
            if line not in risks:
                risks.append(line)
    if not risks:
        risks.append("Read-only plan. No write or side-effect capability is used.")
    return tuple(risks)


def _episode(kind: str, summary: str) -> PlannedEpisode:
    return PlannedEpisode(source="dev_guild", agent="schema_agent", kind=kind, summary=summary)


def _steps(pairs: list[tuple[str, str, str, str]]) -> tuple[PlanStep, ...] | None:
    steps: list[PlanStep] = []
    for index, (action, connector, capability, schema_id) in enumerate(pairs, start=1):
        if _entry(schema_id) is None:
            return None
        steps.append(
            PlanStep(
                order=index,
                action=action,
                connector=connector,
                capability=capability,
                schema_id=schema_id,
            )
        )
    if len(steps) < 2:
        return None
    return tuple(steps)


def _strategy(
    *,
    strategy_id: str,
    title: str,
    summary: str,
    primary: str,
    connectors: tuple[str, ...],
    schema_ids: tuple[str, ...],
    steps: tuple[PlanStep, ...] | None,
    selection: ConnectorSelection,
    keywords: tuple[str, ...],
    extra_risks: tuple[str, ...] = (),
) -> UtilizationStrategy | None:
    if steps is None:
        return None
    entries = []
    for schema_id in schema_ids:
        entry = _entry(schema_id)
        if entry is None:
            return None
        entries.append(entry)
    if len(entries) < 2:
        return None
    flow = _flow(entries[0], entries[1])
    risks = extra_risks + _risks(selection, connectors, schema_ids)
    return UtilizationStrategy(
        id=strategy_id,
        title=title,
        summary=summary,
        primary_connector=primary,
        steps=steps,
        data_flow=(flow,),
        risks=risks,
        episodes=(_episode(strategy_id, summary),),
        connectors=connectors,
        schemas=schema_ids,
        keywords=keywords,
    )


def _catalog(selection: ConnectorSelection) -> list[UtilizationStrategy]:
    plans: list[UtilizationStrategy] = []
    read_episode = _cap(selection, "episode_store", "read")
    list_episode = _cap(selection, "episode_store", "read")
    if read_episode and list_episode:
        second = "list" if any(
            item.id == "list" for item in (selection.get("episode_store").allowed_capabilities if selection.get("episode_store") else ())
        ) else read_episode
        plans.append(
            _strategy(
                strategy_id="read-episode-flywheel",
                title="Read episodes and project the training export",
                summary="Read the episode row, then project the JSONL record the flywheel would export.",
                primary="episode_store",
                connectors=("episode_store",),
                schema_ids=("episode", "export.jsonl"),
                steps=_steps(
                    [
                        ("Read the episode schema from the store.", "episode_store", read_episode, "episode"),
                        ("Project one JSONL training record from that row.", "episode_store", second, "export.jsonl"),
                    ]
                ),
                selection=selection,
                keywords=("episode", "export", "flywheel", "score", "training"),
            )
        )
    gateway_read = _cap(selection, "gateway", "read")
    ios_read = _cap(selection, "ios_swift_client", "read")
    if gateway_read:
        second_connector = "ios_swift_client" if ios_read else "gateway"
        second_cap = ios_read or gateway_read
        names = ("gateway", "ios_swift_client") if ios_read else ("gateway",)
        plans.append(
            _strategy(
                strategy_id="gateway-contract-trace",
                title="Trace the gateway contract through the Swift client",
                summary="Read the episode response schema, then the SSE completion event the client decodes.",
                primary="gateway",
                connectors=names,
                schema_ids=("gateway.component.EpisodeResponse", "gateway.sse.run.completed"),
                steps=_steps(
                    [
                        ("Read the episode response the gateway returns.", "gateway", gateway_read, "gateway.component.EpisodeResponse"),
                        ("Follow the run.completed event the client consumes.", second_connector, second_cap, "gateway.sse.run.completed"),
                    ]
                ),
                selection=selection,
                keywords=("gateway", "openapi", "endpoint", "sse", "ios", "swift", "client"),
            )
        )
    github_read = _cap(selection, "github", "read")
    if github_read:
        plans.append(
            _strategy(
                strategy_id="github-review-source",
                title="Feed a review from a source-control diff",
                summary="Read the code_review input contract, then the output contract that diff would fill.",
                primary="github",
                connectors=("github",),
                schema_ids=("midplane.node.code_review.reads", "midplane.node.code_review.writes"),
                steps=_steps(
                    [
                        ("Read the code_review input a diff would fill.", "github", github_read, "midplane.node.code_review.reads"),
                        ("Map that diff onto the code_review output contract.", "github", github_read, "midplane.node.code_review.writes"),
                    ]
                ),
                selection=selection,
                keywords=("github", "review", "diff", "code"),
            )
        )
    workspace = [
        name
        for name in ("gmail", "google_calendar", "google_drive", "x")
        if _cap(selection, name, "read")
    ]
    if workspace:
        first = workspace[0]
        second = workspace[1] if len(workspace) > 1 else workspace[0]
        plans.append(
            _strategy(
                strategy_id="workspace-connector-fit",
                title="Fit planned workspace connectors onto platform schemas",
                summary="Read two planned connectors and record which platform schemas an integration would touch.",
                primary=first,
                connectors=tuple(workspace),
                schema_ids=("episode", "gateway.component.CreateRunRequest"),
                steps=_steps(
                    [
                        (f"Read {first} without sending or writing.", first, _cap(selection, first, "read") or "", "episode"),
                        (
                            f"Read {second} and map it to the run request schema.",
                            second,
                            _cap(selection, second, "read") or "",
                            "gateway.component.CreateRunRequest",
                        ),
                    ]
                ),
                selection=selection,
                keywords=("gmail", "calendar", "drive", "email", "post", "workspace"),
            )
        )
    if gateway_read:
        plans.append(
            _strategy(
                strategy_id="refuse-real-money-trading",
                title="Refuse real-money trading",
                summary="Read the gateway error and run contracts and record that live trading is not selected.",
                primary="gateway",
                connectors=("gateway",),
                schema_ids=("gateway.error", "gateway.component.RunStatusResponse"),
                steps=_steps(
                    [
                        ("Read the error envelope the gateway already publishes.", "gateway", gateway_read, "gateway.error"),
                        ("Read the run status contract and stop before any trading call.", "gateway", gateway_read, "gateway.component.RunStatusResponse"),
                    ]
                ),
                selection=selection,
                keywords=("trading", "money", "live", "broker", "real"),
                extra_risks=(
                    "Real-money trading is never selected. The gateway has no trading endpoint.",
                ),
            )
        )
    paper = _cap(selection, "trading_control", "side_effect")
    if paper:
        plans.append(
            _strategy(
                strategy_id="paper-control-boundary",
                title="Stay inside the paper trading control boundary",
                summary="Read the control-service action schema, then the paper acknowledgement. No order is placed.",
                primary="trading_control",
                connectors=("trading_control",),
                schema_ids=("trading.control.action", "trading.control.result"),
                steps=_steps(
                    [
                        ("Read the paper-mode action schema.", "trading_control", paper, "trading.control.action"),
                        ("Record the acknowledgement schema. broker_called stays false.", "trading_control", paper, "trading.control.result"),
                    ]
                ),
                selection=selection,
                keywords=("trading", "paper", "control"),
            )
        )
    complete = _cap(selection, "llm_router", "side_effect")
    if complete:
        plans.append(
            _strategy(
                strategy_id="llm-routed-reading",
                title="Ask the routed model to read a schema result",
                summary="Use the router only as a side effect the caller allowed, and store a schema_agent episode.",
                primary="llm_router",
                connectors=("llm_router",),
                schema_ids=("midplane.node.router.writes", "schema_agent.result"),
                steps=_steps(
                    [
                        ("Read the route record the supervisor already stored.", "llm_router", complete, "midplane.node.router.writes"),
                        ("Produce the schema agent result on that model.", "llm_router", complete, "schema_agent.result"),
                    ]
                ),
                selection=selection,
                keywords=("model", "llm", "route", "provider"),
            )
        )
    return [plan for plan in plans if plan is not None]


def _goal_score(plan: UtilizationStrategy, goal: str) -> int:
    text = goal.lower()
    return sum(1 for token in plan.keywords if token in text)


def plan_utilization(
    goal: str,
    constraints: ConnectorConstraints | Mapping | None = None,
) -> tuple[UtilizationStrategy, ...]:
    """Return 2 to 4 strategies that do not share a primary connector."""

    if isinstance(constraints, ConnectorConstraints):
        resolved = constraints
    else:
        resolved = ConnectorConstraints.from_mapping(constraints)
    selection = select_connectors(goal, resolved)
    ranked = sorted(
        _catalog(selection),
        key=lambda plan: (-_goal_score(plan, goal), _PRIORITY.get(plan.id, 99), plan.id),
    )
    picked: list[UtilizationStrategy] = []
    used: set[str] = set()
    for plan in ranked:
        if plan.primary_connector in used:
            continue
        if len(plan.steps) < 2 or not plan.data_flow or not plan.episodes:
            continue
        picked.append(plan)
        used.add(plan.primary_connector)
        if len(picked) == 4:
            break
    if len(picked) < 2:
        raise RuntimeError("plan_utilization could not build two safe strategies")
    return tuple(picked)
