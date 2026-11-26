"""Worker registration for the guild supervisor."""

from __future__ import annotations

from dataclasses import dataclass


_BANNED_TOOL_TOKENS = ("broker", "trading", "exchange", "alpaca", "binance", "ccxt", "oanda")


@dataclass(frozen=True)
class WorkerSpec:
    role_id: str
    task_type: str
    tools: tuple[str, ...]
    prompt_ref: str


WEBSITE_WORKER = WorkerSpec(
    role_id="website",
    task_type="website_design",
    tools=(),
    prompt_ref="website",
)


def registry() -> dict[str, WorkerSpec]:
    return {WEBSITE_WORKER.role_id: WEBSITE_WORKER}


def validate_tools(tools: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    names = tuple(tools)
    for name in names:
        lowered = name.lower()
        if any(token in lowered for token in _BANNED_TOOL_TOKENS):
            raise ValueError(f"{name} cannot be registered: guild workers have no broker or trading tool")
    return names
