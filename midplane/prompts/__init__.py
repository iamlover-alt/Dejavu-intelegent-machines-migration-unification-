"""Prompt text for mid-plane workers.

RECONCILE with dev_guild_prompt_templates.md. That file owns the prompt text
and the canonical role list. This package supplies prompts the slice can run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

CODE_REVIEW_PROMPT_REF = "code_review"
SCHEMA_AGENT_PROMPT_REF = "schema_agent"
_SCHEMA_PROMPT_PATH = Path(__file__).with_name("schema_agent.md")
_VERSION_RE = re.compile(r"(?m)^version:\s*(\S+)\s*$")


def render_code_review_prompt(*, repo: str, instructions: str, diff: str) -> str:
    return (
        "You are the Dev Guild code_review worker.\n"
        "Review the unified diff. Reply with one JSON object and no other text.\n"
        "Keys: summary (string), findings (non-empty list of objects with "
        "severity, detail, and ref), confidence (number from 0 to 1).\n"
        "severity is info, warn, or block.\n"
        f"Repo: {repo}\n"
        f"Instructions: {instructions}\n"
        "Diff:\n"
        f"{diff}"
    )


def schema_prompt_text() -> str:
    return _SCHEMA_PROMPT_PATH.read_text(encoding="utf-8")


def schema_prompt_version() -> str:
    match = _VERSION_RE.search(schema_prompt_text())
    if match is None:
        raise ValueError("midplane/prompts/schema_agent.md is missing a version")
    return match.group(1)


def render_schema_agent_prompt(*, goal: str, constraints: Mapping | None = None) -> str:
    body = schema_prompt_text().rstrip()
    encoded = json.dumps(dict(constraints or {}), sort_keys=True, separators=(",", ":"))
    return f"{body}\n\nGoal: {goal}\nConstraints: {encoded}\n"
