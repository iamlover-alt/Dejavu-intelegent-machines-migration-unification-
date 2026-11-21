from __future__ import annotations

from midplane.scoring import stub_quality_score


def _success_state() -> dict:
    trace = [
        {"node": "supervisor", "event": "end"},
        {"node": "router", "event": "end"},
        {"node": "code_review", "event": "end"},
        {"node": "episode_writer", "event": "end"},
    ]
    return {
        "run_id": "run",
        "source": "dev_guild",
        "task": {"role": "code_review", "diff": "+pass"},
        "plan": {"steps": ["code_review"]},
        "model_choices": [{"kind": "route"}],
        "tool_calls": [],
        "output": {"summary": "ok", "findings": [{"severity": "info", "detail": "d", "ref": "diff"}]},
        "cost": {"total_usd": 0.0},
        "latency_ms": 4,
        "outcome": "success",
        "trace": trace,
        "error": None,
    }


def test_stub_score_is_half_on_a_complete_success():
    assert stub_quality_score(_success_state()) == 0.5


def test_stub_score_is_zero_on_error_even_when_complete():
    state = _success_state()
    state["outcome"] = "error"
    state["error"] = "failed"
    assert stub_quality_score(state) == 0.0


def test_stub_score_is_zero_without_output_or_a_node():
    missing_output = _success_state()
    missing_output["output"] = {}
    assert stub_quality_score(missing_output) == 0.0

    missing_node = _success_state()
    missing_node["trace"] = [event for event in missing_node["trace"] if event["node"] != "router"]
    assert stub_quality_score(missing_node) == 0.0


def test_stub_never_returns_the_reserved_perfect_score():
    assert stub_quality_score(_success_state()) != 1.0
