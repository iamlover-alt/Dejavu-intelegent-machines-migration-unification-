"""Stand-in prompt for the code_review role.

RECONCILE with dev_guild_prompt_templates.md. That file owns the prompt text
and the canonical role list. This module only supplies a prompt the slice can run.
"""

from __future__ import annotations

CODE_REVIEW_PROMPT_REF = "code_review"


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
