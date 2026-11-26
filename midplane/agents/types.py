"""Structured result of one schema-agent run.

The registry reads this TypedDict. The agent result uses the same keys.
"""

from __future__ import annotations

from typing import TypedDict


class SchemaAgentResultFields(TypedDict):
    agent: str
    agent_version: str
    prompt_ref: str
    prompt_version: str
    goal: str
    summary: str
    schemas: list
    connectors: list
    excluded_connectors: list
    strategies: list
