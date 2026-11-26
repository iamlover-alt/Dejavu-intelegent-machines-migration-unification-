from __future__ import annotations

import pytest

from midplane.agents.website.guide import implementation_guide
from midplane.agents.website.knowledge import load_knowledge, load_site_context


@pytest.mark.parametrize("action", ["build", "refine"])
def test_guide_flags_paid_steps_and_asks_the_owner(action):
    guide = implementation_guide(action)
    paid = [step for step in guide.steps if step.requires_paid_plan]
    assert paid
    for step in paid:
        assert "Ask the owner first." in step.note
        assert step.owner_approval_required is True
        assert guide.executed is False


@pytest.mark.parametrize("action", ["build", "refine"])
def test_guide_flags_dns_steps_and_does_not_perform_them(action):
    guide = implementation_guide(action)
    site = load_site_context()
    dns_steps = [step for step in guide.steps if step.changes_live_dns]
    assert {step.id for step in dns_steps} == {"dns_github_pages", "dns_squarespace_hosting"}
    github = next(step for step in dns_steps if step.id == "dns_github_pages")
    for record in site["dns_plan"]["a_records"]:
        assert record in github.detail
    assert site["dns_plan"]["www_cname"] in github.detail
    assert github.requires_paid_plan is False
    assert "free with the domain" in github.detail
    for step in dns_steps:
        assert step.owner_approval_required is True
        assert "Ask the owner first." in step.note
        assert "does not change DNS" in step.note
    assert guide.executed is False
    assert guide.domain == site["domain"]
    assert guide.punycode == site["punycode"]


def test_squarespace_hosting_dns_requires_a_paid_plan_and_a_live_change():
    guide = implementation_guide("build")
    step = next(item for item in guide.steps if item.id == "dns_squarespace_hosting")
    assert step.requires_paid_plan is True
    assert step.changes_live_dns is True
    assert "Ask the owner first." in step.note


def test_trial_design_steps_are_not_marked_as_a_paid_plan():
    guide = implementation_guide("build")
    design = next(step for step in guide.steps if step.id == "design_pages")
    assert design.requires_paid_plan is False
    assert design.changes_live_dns is False
    assert "Fluid Engine" in design.detail
    assert "code injection" in design.detail.lower()
    assert "free" not in design.detail.lower()
    publish = next(step for step in guide.steps if step.id == "publish_paid_plan")
    assert publish.requires_paid_plan is True
    assert "Basic" in publish.detail
    assert "Do not describe" in publish.detail


def test_guide_states_there_is_no_permanent_free_plan():
    guide = implementation_guide("refine")
    statement = load_knowledge()["billing"]["statement"]
    assert "no permanent free website plan" in statement.lower()
    assert guide.statement == statement
    notice = next(step for step in guide.steps if step.id == "trial_notice")
    assert statement in notice.detail
    assert "14" in notice.detail


def test_guide_steps_are_ordered_and_trading_stays_paper_only():
    guide = implementation_guide("build")
    assert [step.order for step in guide.steps] == list(range(1, len(guide.steps) + 1))
    trading = next(step for step in guide.steps if step.id == "trading_language")
    assert "paper-only" in trading.detail
    assert "real-money" in trading.detail
    assert trading.requires_paid_plan is False
    assert trading.changes_live_dns is False


def test_unpaid_steps_do_not_call_a_website_plan_free():
    guide = implementation_guide("build")
    for step in guide.steps:
        if step.requires_paid_plan:
            continue
        text = f"{step.detail} {step.note}".lower()
        assert "website plan is free" not in text
        assert "squarespace is free" not in text
