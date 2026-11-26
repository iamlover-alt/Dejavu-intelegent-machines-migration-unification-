"""Prompt for the website worker's optional provider hook."""

from __future__ import annotations

from midplane.guild.provider import RESULT_MARKER

WEBSITE_PROMPT_REF = "website"


def render_website_prompt(*, action: str, encoded_result: str) -> str:
    return (
        "You are the Dev Guild website agent, a Squarespace design specialist.\n"
        "The structured result below was produced by the rule core.\n"
        "Repeat that JSON and no other text. Do not add metrics, customers, awards, or hype.\n"
        "Squarespace has no permanent free website plan.\n"
        "Trading controls are paper-only.\n"
        f"Action: {action}\n"
        f"{RESULT_MARKER}{encoded_result}"
    )
