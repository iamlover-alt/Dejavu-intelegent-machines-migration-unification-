from __future__ import annotations

import json

import pytest

from midplane.agents.website.audit import audit
from midplane.agents.website.schemas import SiteSnapshot
from midplane.guild.graph import run_task
from midplane.guild.provider import FakeModelProvider, provider_from_env
from midplane.guild.registry import registry, validate_tools
from midplane.guild.routing import DEFAULT_BUDGET, DEFAULT_ROUTE_TABLE, choose_model, is_website_request


def _guide_task(text: str, action: str = "implementation_guide", target: str = "build", **extra) -> dict:
    task = {"text": text, "action": action, "target": target}
    task.update(extra)
    return task


def test_choose_model_records_criteria_and_fallback():
    choice = choose_model("website_design", DEFAULT_BUDGET, DEFAULT_ROUTE_TABLE)
    assert choice["primary"] == "fake-small"
    assert choice["fallback"] == "fake-large"
    assert set(choice["criteria"]) == {"task_type", "cost", "latency", "confidence"}
    assert choice["criteria"]["task_type"] == "website_design"


def test_choose_model_errors_when_nothing_is_eligible():
    choice = choose_model("website_design", DEFAULT_BUDGET, {"models": []})
    assert choice["primary"] is None
    assert choice["error"] == "no_eligible_model"


def test_website_worker_is_registered_without_tools():
    spec = registry()["website"]
    assert spec.role_id == "website"
    assert spec.task_type == "website_design"
    assert spec.prompt_ref == "website"
    assert spec.tools == ()
    with pytest.raises(ValueError):
        validate_tools(["alpaca_order"])


@pytest.mark.parametrize(
    "text",
    [
        "Audit the Squarespace website",
        "Redesign the hero layout",
        "Write the about page",
        "Check the landing page SEO and alt text",
    ],
)
def test_classifier_accepts_website_and_design_requests(text):
    assert is_website_request({"text": text}) is True


@pytest.mark.parametrize(
    "text",
    [
        "Design a schema for episodes",
        "Review this pull request diff",
        "Fix the iOS navigation stack",
        "Route the trading order",
    ],
)
def test_classifier_rejects_unrelated_requests(text):
    assert is_website_request({"text": text}) is False


def test_explicit_role_routes_to_the_website_agent():
    provider = FakeModelProvider()
    result = run_task(
        {"role": "website", "text": "hello", "action": "implementation_guide", "target": "build"},
        provider=provider,
        run_id="role-run",
        thread_id="role-thread",
    )
    assert result.state["plan"]["steps"] == ["website"]
    assert result.state["route"]["worker"] == "website"
    assert result.state["output"]["worker"] == "website"
    assert provider.calls == ["fake-small"]
    assert [event["node"] for event in result.state["trace"]] == ["supervisor", "router", "website"]


def test_squarespace_audit_routes_to_the_website_agent(clean_snapshot):
    provider = FakeModelProvider()
    result = run_task(
        {
            "text": "Audit the Squarespace website design",
            "action": "audit",
            "snapshot": clean_snapshot.model_dump(),
        },
        provider=provider,
    )
    assert result.state["route"]["worker"] == "website"
    assert set(result.state["route"]["criteria"]) == {"task_type", "cost", "latency", "confidence"}
    assert result.state["output"]["action"] == "audit"
    assert result.state["output"]["result"] == audit(clean_snapshot).model_dump(mode="json")
    assert result.state["output"]["provider"]["accepted"] is True
    assert provider.calls == ["fake-small"]


def test_design_request_routes_to_the_website_agent():
    result = run_task(_guide_task("Redesign the hero layout", target="refine"))
    assert result.state["route"]["worker"] == "website"
    assert result.state["plan"]["action"] == "implementation_guide"
    steps = result.state["output"]["result"]["steps"]
    assert any(step["requires_paid_plan"] for step in steps)
    assert any(step["changes_live_dns"] for step in steps)
    assert result.state["output"]["result"]["executed"] is False


def test_schema_request_is_not_routed_to_the_website_agent():
    result = run_task({"text": "Design a schema for episodes", "action": "audit"})
    assert result.state["error"]
    assert "not selected" in result.state["error"]
    assert result.state["route"] == {}
    assert result.state["output"] == {}
    assert [event["node"] for event in result.state["trace"]] == ["supervisor"]


def test_router_stops_when_no_model_is_eligible():
    result = run_task(_guide_task("Squarespace implementation guide"), route_table={"models": []})
    assert result.state["error"] == "no_eligible_model"
    assert [event["node"] for event in result.state["trace"]] == ["supervisor", "router"]


def test_custom_policy_selects_the_model(clean_snapshot):
    class PinLarge:
        def choose(self, task_type, budget, table):
            return {
                "kind": "route",
                "task_type": task_type,
                "primary": "fake-large",
                "fallback": None,
                "criteria": {
                    "task_type": task_type,
                    "cost": budget["max_cost"],
                    "latency": budget["max_latency_ms"],
                    "confidence": budget["min_confidence"],
                },
            }

    provider = FakeModelProvider()
    result = run_task(
        {
            "text": "Audit the Squarespace website",
            "action": "audit",
            "snapshot": clean_snapshot.model_dump(),
        },
        provider=provider,
        policy=PinLarge(),
    )
    assert provider.calls == ["fake-large"]
    assert result.state["route"]["primary"] == "fake-large"


def test_checkpoint_is_stored_for_the_thread():
    result = run_task(_guide_task("Build the Squarespace site"), run_id="run-checkpoint", thread_id="thread-checkpoint")
    snapshot = result.graph.get_state({"configurable": {"thread_id": "thread-checkpoint"}})
    assert snapshot.values["run_id"] == "run-checkpoint"
    assert snapshot.values["route"]["worker"] == "website"


def test_fake_provider_output_is_deterministic(clean_snapshot):
    task = {
        "text": "Audit the Squarespace website",
        "action": "audit",
        "snapshot": json.loads(SiteSnapshot.model_validate(clean_snapshot.model_dump()).model_dump_json()),
    }
    provider = FakeModelProvider()
    first = run_task(task, provider=provider, run_id="det-1", thread_id="det-1")
    second = run_task(task, provider=provider, run_id="det-2", thread_id="det-2")
    assert first.state["output"] == second.state["output"]
    assert provider.calls == ["fake-small", "fake-small"]
    assert first.state["output"]["provider"]["accepted"] is True


def test_fallback_is_used_when_the_primary_fails(clean_snapshot):
    provider = FakeModelProvider(fail_models={"fake-small"})
    result = run_task(
        {
            "text": "Audit the Squarespace site",
            "action": "audit",
            "snapshot": clean_snapshot.model_dump(),
        },
        provider=provider,
    )
    assert provider.calls == ["fake-small", "fake-large"]
    assert result.state["output"]["provider"]["accepted"] is True
    assert result.state["output"]["result"] == audit(clean_snapshot).model_dump(mode="json")


def test_disagreeing_provider_cannot_replace_the_rule_result(clean_snapshot):
    provider = FakeModelProvider(
        responses={"fake-small": "This revolutionary system serves 99 customers.", "fake-large": "groundbreaking"}
    )
    result = run_task(
        {
            "role": "website",
            "action": "audit",
            "snapshot": clean_snapshot.model_dump(),
        },
        provider=provider,
    )
    encoded = json.dumps(result.state["output"]["result"]).lower()
    assert result.state["output"]["provider"]["accepted"] is False
    assert "revolutionary" not in encoded
    assert "99" not in encoded
    assert result.state["output"]["result"] == audit(clean_snapshot).model_dump(mode="json")
    assert result.state["outcome"] == "success"


def test_provider_from_env_is_fake_and_refuses_network_providers(monkeypatch):
    monkeypatch.delenv("MIDPLANE_MODEL_PROVIDER", raising=False)
    assert isinstance(provider_from_env(), FakeModelProvider)
    monkeypatch.setenv("MIDPLANE_MODEL_PROVIDER", "openai")
    with pytest.raises(RuntimeError):
        provider_from_env()


def test_run_does_not_open_a_socket(monkeypatch):
    import socket

    def explode(*args, **kwargs):
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "socket", explode)
    result = run_task(_guide_task("Squarespace website guide"))
    assert result.state["outcome"] == "success"
    assert result.state["output"]["result"]["executed"] is False
