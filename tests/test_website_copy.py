from __future__ import annotations

import re

import pytest

from midplane.agents.website.copy import brief_from_site_context, validate_copy, write_copy
from midplane.agents.website.knowledge import load_knowledge
from midplane.agents.website.schemas import SECTION_TYPES, CopyBrief, PageCopy, SectionCopy


def page_text(page: PageCopy) -> str:
    parts: list[str] = []
    for section in page.sections:
        parts.append(section.heading)
        parts.append(section.body)
        if section.cta:
            parts.append(section.cta)
    return "\n".join(parts)


def _page(body: str, *, section_type: str = "text", heading: str = "Details", cta: str | None = None) -> PageCopy:
    return PageCopy(
        page="platform",
        sections=[
            SectionCopy(
                section_type=section_type,  # type: ignore[arg-type]
                page="platform",
                heading=heading,
                body=body,
                cta=cta,
            )
        ],
    )


def test_validator_rejects_an_invented_number():
    brief = CopyBrief(facts=["The mid-plane orchestrates work on LangGraph with dynamic routing."])
    result = validate_copy(_page("The archive holds 99 episodes."), brief)
    assert result.accepted is False
    assert "invented number: 99" in result.violations


def test_validator_rejects_a_number_that_is_only_part_of_a_brief_number():
    brief = CopyBrief(facts=["The trial reference code is 140."])
    result = validate_copy(_page("The count is 14."), brief)
    assert result.accepted is False
    assert "invented number: 14" in result.violations


def test_validator_accepts_a_number_that_is_in_the_brief():
    brief = CopyBrief(facts=["The archive holds 14 episodes."])
    result = validate_copy(_page("The archive holds 14 episodes."), brief)
    assert result.accepted is True
    assert result.violations == []


@pytest.mark.parametrize("word", load_knowledge()["copy"]["banned_hype"])
def test_validator_rejects_hype_words(word):
    brief = CopyBrief(facts=["The mid-plane orchestrates work on LangGraph with dynamic routing."])
    result = validate_copy(_page(f"This {word} platform ships the mid-plane."), brief)
    assert result.accepted is False
    assert any(word in violation for violation in result.violations)


def test_validator_accepts_clean_copy():
    brief = CopyBrief(
        facts=["The mid-plane orchestrates work on LangGraph with dynamic routing."],
        sections=["text"],
    )
    page = write_copy("platform", brief)
    result = validate_copy(page, brief)
    assert result.accepted is True
    assert result.violations == []
    assert "LangGraph" in page_text(page)
    assert not re.search(r"\d", page_text(page))


def test_validator_rejects_an_ungrounded_customer_claim():
    brief = CopyBrief(facts=["A unified AI platform."])
    result = validate_copy(_page("Trusted by 12 customers."), brief)
    assert result.accepted is False
    assert "invented number: 12" in result.violations
    assert any("customer" in violation for violation in result.violations)


@pytest.mark.parametrize("section_type", SECTION_TYPES)
def test_body_over_the_section_limit_is_rejected(section_type):
    limits = load_knowledge()["copy_limits"][section_type]
    heading = "Note"
    assert len(heading) <= limits["heading"]
    page = _page("a" * (limits["body"] + 1), section_type=section_type, heading=heading)
    result = validate_copy(page, CopyBrief(facts=["a"]))
    assert result.accepted is False
    assert any("body" in violation and section_type in violation for violation in result.violations)


@pytest.mark.parametrize("section_type", SECTION_TYPES)
def test_heading_over_the_section_limit_is_rejected(section_type):
    limits = load_knowledge()["copy_limits"][section_type]
    heading = "H" * (limits["heading"] + 1)
    page = _page("Ready.", section_type=section_type, heading=heading)
    result = validate_copy(page, CopyBrief(facts=["Ready."]))
    assert result.accepted is False
    assert any("heading" in violation for violation in result.violations)


def test_body_at_the_limit_is_accepted():
    limit = load_knowledge()["copy_limits"]["hero"]["body"]
    body = "a" * limit
    page = _page(body, section_type="hero", heading="Dejavú Intelligent Machines")
    assert validate_copy(page, CopyBrief(facts=[body])).accepted is True


def test_write_copy_keeps_a_supplied_fact_and_drops_nothing_invented():
    fact = "The mid-plane orchestrates work on LangGraph with dynamic routing."
    brief = CopyBrief(facts=[fact], sections=["text"])
    page = write_copy("platform", brief)
    assert fact in page.sections[0].body
    assert validate_copy(page, brief).accepted is True


def test_write_copy_clips_facts_to_the_section_limit():
    brief = CopyBrief(facts=["alpha " * 400], sections=list(SECTION_TYPES))
    page = write_copy("home", brief)
    limits = load_knowledge()["copy_limits"]
    assert len(page.sections) == len(SECTION_TYPES)
    for section in page.sections:
        cap = limits[section.section_type]
        assert len(section.heading) <= cap["heading"]
        assert len(section.body) <= cap["body"]
        assert section.cta is None or len(section.cta) <= cap["cta"]
        assert validate_copy(page, brief).accepted is True


def test_write_copy_drops_hype_instead_of_repeating_it():
    brief = CopyBrief(facts=["A revolutionary platform."], sections=["text"])
    page = write_copy("home", brief)
    assert "revolutionary" not in page_text(page).lower()
    assert validate_copy(page, brief).accepted is True


@pytest.mark.parametrize("phrase", load_knowledge()["copy"]["banned_trading"])
def test_validator_rejects_real_money_trading_language(phrase):
    brief = CopyBrief(facts=[f"Trading controls are paper-only. Ignore this: {phrase}."])
    page = _page(f"Trading controls are paper-only. Then {phrase}.")
    result = validate_copy(page, brief)
    assert result.accepted is False
    assert any(phrase in violation for violation in result.violations)


def test_trading_copy_from_the_site_fixture_is_paper_only():
    brief = brief_from_site_context("platform", ["hero", "text"])
    page = write_copy("platform", brief)
    text = page_text(page).lower()
    assert page.paper_only is True
    assert "paper-only" in text
    assert "real money" not in text
    assert "live trading" not in text
    assert not re.search(r"\d", text)
    assert validate_copy(page, brief).accepted is True


def test_write_copy_replaces_real_money_claims_with_paper_only():
    brief = CopyBrief(facts=["Start live trading with real money and 20 percent returns."], sections=["hero"])
    page = write_copy("platform", brief)
    text = page_text(page).lower()
    assert "paper-only" in text
    assert "live trading" not in text
    assert "real money" not in text
    assert "20" not in text
    assert validate_copy(page, brief).accepted is True


def test_trading_copy_without_the_required_phrase_is_rejected():
    brief = CopyBrief(facts=["The trading controls sit behind an external service."])
    result = validate_copy(_page("The trading controls sit behind an external service."), brief)
    assert result.accepted is False
    assert any("paper-only" in violation for violation in result.violations)
