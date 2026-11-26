"""Deterministic fake model provider.

The website agent's structured result comes from its rules. The provider hook
is optional and, in tests, this fake. It does not open a network connection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Protocol


class ModelError(RuntimeError):
    """A model call failed before it returned text."""


@dataclass(frozen=True)
class ModelResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model_id: str


class ModelProvider(Protocol):
    def complete(self, *, model_id: str, prompt: str) -> ModelResponse:
        """Return one completion. Raise ModelError or TimeoutError on failure."""


USD_PER_1K_TOKENS = {
    "fake-small": 0.001,
    "fake-large": 0.01,
}
DEFAULT_USD_PER_1K_TOKENS = 0.005
RESULT_MARKER = "RESULT_JSON\n"


def estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    rate = USD_PER_1K_TOKENS.get(model_id, DEFAULT_USD_PER_1K_TOKENS)
    return round((input_tokens + output_tokens) / 1000 * rate, 8)


def deterministic_completion(prompt: str) -> str:
    """Return the rule result embedded in the prompt. No randomness, no network."""

    if RESULT_MARKER in prompt:
        return prompt.split(RESULT_MARKER, 1)[1].strip()
    return '{"accepted": true}'


class FakeModelProvider:
    """Scripted provider for tests and the default guild run.

    `fail_models` raises ModelError before a response.
    `responses` maps a model id to a raw string the agent must not trust blindly.
    """

    def __init__(
        self,
        fail_models: set[str] | None = None,
        responses: dict[str, str] | None = None,
    ) -> None:
        self.fail_models = set(fail_models or ())
        self.responses = dict(responses or {})
        self.calls: list[str] = []

    def complete(self, *, model_id: str, prompt: str) -> ModelResponse:
        self.calls.append(model_id)
        if model_id in self.fail_models:
            raise ModelError(f"{model_id} failed")
        text = self.responses.get(model_id, deterministic_completion(prompt))
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(text) // 4)
        return ModelResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost_usd(model_id, input_tokens, output_tokens),
            model_id=model_id,
        )


def provider_from_env(env: Mapping[str, str] | None = None) -> ModelProvider:
    source = os.environ if env is None else env
    kind = source.get("MIDPLANE_MODEL_PROVIDER", "fake").strip().lower()
    if kind in {"", "fake"}:
        return FakeModelProvider()
    raise RuntimeError(
        "MIDPLANE_MODEL_PROVIDER must be fake for the website agent. "
        "This worker does not call OpenAI, Anthropic, or any other network provider."
    )
