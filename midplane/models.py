"""Model providers.

The fake provider is deterministic and does not open a network connection.
OpenAI and Anthropic providers are selected with MIDPLANE_MODEL_PROVIDER and
read their keys from the environment. They are not used by the tests.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Protocol


class ModelError(RuntimeError):
    """A model call failed, timed out, or returned a body we will not parse."""


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


# Estimates, not invoices. Real provider responses do not include a dollar price.
USD_PER_1K_TOKENS = {
    "fake-small": 0.001,
    "fake-large": 0.01,
}
DEFAULT_USD_PER_1K_TOKENS = 0.005


def estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    rate = USD_PER_1K_TOKENS.get(model_id, DEFAULT_USD_PER_1K_TOKENS)
    return round((input_tokens + output_tokens) / 1000 * rate, 8)


def deterministic_review(prompt: str) -> str:
    """Stable JSON review derived from the diff text. No randomness, no network."""

    diff = prompt.split("\nDiff:\n", 1)[-1]
    findings: list[dict] = []
    for lineno, line in enumerate(diff.splitlines(), start=1):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        lowered = line.lower()
        if any(token in lowered for token in ("password", "secret", "api_key", "apikey", "eval(")):
            findings.append(
                {
                    "severity": "block",
                    "detail": "Sensitive or unsafe pattern in an added line.",
                    "ref": f"L{lineno}",
                }
            )
        elif "todo" in lowered or "fixme" in lowered:
            findings.append(
                {
                    "severity": "warn",
                    "detail": "Unresolved TODO or FIXME in an added line.",
                    "ref": f"L{lineno}",
                }
            )
    if not findings:
        findings.append(
            {
                "severity": "info",
                "detail": "No blocking issues in the added lines.",
                "ref": "diff",
            }
        )
    return json.dumps(
        {
            "summary": f"Reviewed diff ({len(diff.splitlines())} lines).",
            "findings": findings,
            "confidence": 0.8,
        }
    )


class FakeModelProvider:
    """Scripted provider for tests and the default CLI.

    `fail_models` raises ModelError before a response.
    `responses` maps a model id to a raw string, which may be invalid JSON.
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
        text = self.responses.get(model_id, deterministic_review(prompt))
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(text) // 4)
        return ModelResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost_usd(model_id, input_tokens, output_tokens),
            model_id=model_id,
        )


def _post_json(url: str, payload: dict, headers: dict, timeout: float = 30.0) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise ModelError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ModelError(str(exc.reason)) from exc
    except TimeoutError as exc:
        raise ModelError("provider timed out") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ModelError("provider returned non-JSON") from exc
    if not isinstance(parsed, dict):
        raise ModelError("provider returned a non-object JSON body")
    return parsed


class OpenAIChatProvider:
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1") -> None:
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required when MIDPLANE_MODEL_PROVIDER=openai")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def complete(self, *, model_id: str, prompt: str) -> ModelResponse:
        payload = _post_json(
            f"{self._base_url}/chat/completions",
            {
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
            },
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            text = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens") or max(1, len(prompt) // 4))
            output_tokens = int(usage.get("completion_tokens") or max(1, len(text) // 4))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError("openai response is missing completion text") from exc
        if not isinstance(text, str):
            raise ModelError("openai completion text is not a string")
        return ModelResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost_usd(model_id, input_tokens, output_tokens),
            model_id=model_id,
        )


class AnthropicMessagesProvider:
    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com") -> None:
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required when MIDPLANE_MODEL_PROVIDER=anthropic")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def complete(self, *, model_id: str, prompt: str) -> ModelResponse:
        payload = _post_json(
            f"{self._base_url}/v1/messages",
            {
                "model": model_id,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            {
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )
        try:
            blocks = payload["content"]
            text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
            usage = payload.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or max(1, len(prompt) // 4))
            output_tokens = int(usage.get("output_tokens") or max(1, len(text) // 4))
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelError("anthropic response is missing completion text") from exc
        if not text:
            raise ModelError("anthropic response contained no text")
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
    if kind == "fake" or kind == "":
        return FakeModelProvider()
    if kind == "openai":
        return OpenAIChatProvider(
            api_key=source.get("OPENAI_API_KEY", ""),
            base_url=source.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
    if kind == "anthropic":
        return AnthropicMessagesProvider(
            api_key=source.get("ANTHROPIC_API_KEY", ""),
            base_url=source.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        )
    raise RuntimeError(
        "MIDPLANE_MODEL_PROVIDER must be fake, openai, or anthropic"
    )
