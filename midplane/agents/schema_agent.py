"""Schema agent: registries, connector selection, and utilization plans.

The node calls the typed tools itself. The routed model is recorded on the
episode so the run stays on the same router as code_review. The fake provider
makes the model call deterministic and keeps tests off the network.

RECONCILE with dev_guild_prompt_templates.md if the guild catalog renames this role.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from midplane.agents.plan import UtilizationStrategy, plan_utilization
from midplane.agents.types import SchemaAgentResultFields
from midplane.connectors import (
    ConnectorConstraints,
    list_connectors,
    select_connectors,
)
from midplane.prompts import SCHEMA_AGENT_PROMPT_REF, schema_prompt_version
from midplane.schema_registry import lookup_schema, search_schemas

AGENT_NAME = "schema_agent"
ROSTER_NAME = "schema-agent"
AGENT_VERSION = "v0"


@dataclass(frozen=True)
class SchemaAgentResult:
    agent: str
    agent_version: str
    prompt_ref: str
    prompt_version: str
    goal: str
    summary: str
    schemas: tuple[dict, ...]
    connectors: tuple[dict, ...]
    excluded_connectors: tuple[dict, ...]
    strategies: tuple[dict, ...]

    def to_dict(self) -> dict:
        payload = {
            "agent": self.agent,
            "agent_version": self.agent_version,
            "prompt_ref": self.prompt_ref,
            "prompt_version": self.prompt_version,
            "goal": self.goal,
            "summary": self.summary,
            "schemas": list(self.schemas),
            "connectors": list(self.connectors),
            "excluded_connectors": list(self.excluded_connectors),
            "strategies": list(self.strategies),
        }
        expected = set(SchemaAgentResultFields.__annotations__)
        if set(payload) != expected:
            raise RuntimeError("schema agent result drifted from SchemaAgentResultFields")
        return payload


def _summary(goal: str, schemas: list[dict], connectors: list[dict], plans: tuple[UtilizationStrategy, ...]) -> str:
    schema_ids = ", ".join(item["id"] for item in schemas[:3]) or "none"
    names = ", ".join(item["name"] for item in connectors[:4]) or "none"
    strategy_ids = ", ".join(plan.id for plan in plans)
    return (
        f"Schema agent planned {len(plans)} strategies for {goal!r}. "
        f"Matched schemas: {schema_ids}. "
        f"Leading connectors: {names}. "
        f"Strategies: {strategy_ids}."
    )


def build_schema_result(
    goal: str,
    constraints: ConnectorConstraints | Mapping | None = None,
) -> tuple[SchemaAgentResult, list[dict]]:
    """Run the five tools and return a typed result plus the tool trace."""

    if isinstance(constraints, ConnectorConstraints):
        resolved = constraints
    else:
        resolved = ConnectorConstraints.from_mapping(constraints)
    tool_calls: list[dict] = []
    found = search_schemas(goal, limit=5)
    tool_calls.append(
        {
            "tool": "search_schemas",
            "args": {"query": goal, "limit": 5},
            "schema_ids": [entry.id for entry in found],
        }
    )
    detailed = []
    for entry in found[:3]:
        got = lookup_schema(entry.id)
        detailed.append(got)
        tool_calls.append(
            {
                "tool": "lookup_schema",
                "args": {"schema_id": entry.id},
                "found": got is not None,
            }
        )
    listed = list_connectors()
    tool_calls.append(
        {
            "tool": "list_connectors",
            "args": {},
            "connectors": [item.name for item in listed],
        }
    )
    selection = select_connectors(goal, resolved)
    tool_calls.append(
        {
            "tool": "select_connectors",
            "args": {"goal": goal, "constraints": resolved.to_dict()},
            "selected": [item.name for item in selection.selected],
            "excluded": [item.name for item in selection.excluded],
        }
    )
    plans = plan_utilization(goal, resolved)
    tool_calls.append(
        {
            "tool": "plan_utilization",
            "args": {"goal": goal, "constraints": resolved.to_dict()},
            "strategy_ids": [plan.id for plan in plans],
        }
    )
    schema_payload = [entry.to_dict() for entry in detailed if entry is not None]
    connector_payload = [item.to_dict() for item in selection.selected]
    excluded_payload = [item.to_dict() for item in selection.excluded]
    strategy_payload = [plan.to_dict() for plan in plans]
    version = schema_prompt_version()
    result = SchemaAgentResult(
        agent=AGENT_NAME,
        agent_version=AGENT_VERSION,
        prompt_ref=SCHEMA_AGENT_PROMPT_REF,
        prompt_version=version,
        goal=goal,
        summary=_summary(goal, schema_payload, connector_payload, plans),
        schemas=tuple(schema_payload),
        connectors=tuple(connector_payload),
        excluded_connectors=tuple(excluded_payload),
        strategies=tuple(strategy_payload),
    )
    return result, tool_calls


def schema_task(
    goal: str,
    *,
    repo: str = "local",
    diff: str = "",
    constraints: ConnectorConstraints | Mapping | None = None,
) -> dict:
    if isinstance(constraints, ConnectorConstraints):
        resolved = constraints
    else:
        resolved = ConnectorConstraints.from_mapping(constraints)
    return {
        "role": AGENT_NAME,
        "agent": AGENT_NAME,
        "goal": goal,
        "instructions": goal,
        "diff": diff,
        "repo": repo,
        "constraints": resolved.to_dict(),
    }


def run_schema_agent(
    goal: str,
    *,
    constraints: ConnectorConstraints | Mapping | None = None,
    repo: str = "local",
    diff: str = "",
    run_id: str | None = None,
    thread_id: str | None = None,
    source: str = "dev_guild",
    store=None,
    provider=None,
    policy=None,
    route_table=None,
    budget=None,
    checkpointer=None,
    scorer=None,
):
    """Run the schema agent on the mid-plane graph and write one episode."""

    from midplane.graph import run_review

    task = schema_task(goal, repo=repo, diff=diff, constraints=constraints)
    return run_review(
        diff,
        repo=repo,
        instructions=goal,
        run_id=run_id,
        thread_id=thread_id,
        source=source,  # type: ignore[arg-type]
        role=AGENT_NAME,
        task=task,
        store=store,
        provider=provider,
        policy=policy,
        route_table=route_table,
        budget=budget,
        checkpointer=checkpointer,
        scorer=scorer,
    )
