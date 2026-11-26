from __future__ import annotations

import pytest

from midplane.agents.website.audit import audit
from midplane.agents.website.contrast import contrast_ratio
from midplane.agents.website.knowledge import load_knowledge
from midplane.agents.website.schemas import ColorPair, SectionSnapshot


def _rules(report, rule: str):
    return [finding for finding in report.findings if finding.rule == rule]


def test_clean_snapshot_has_no_findings(clean_snapshot):
    assert audit(clean_snapshot).findings == []


def test_missing_title_is_flagged(clean_snapshot):
    clean_snapshot.seo[0] = clean_snapshot.seo[0].model_copy(update={"title": None})
    found = _rules(audit(clean_snapshot), "missing_title")
    assert len(found) == 1
    assert found[0].severity == "block"
    assert found[0].page == "home"
    assert found[0].section is None
    assert "page title" in found[0].recommendation.lower()
    assert _rules(audit(clean_snapshot), "title_length") == []


def test_blank_title_is_missing_not_a_length_problem(clean_snapshot):
    clean_snapshot.seo[0] = clean_snapshot.seo[0].model_copy(update={"title": "   "})
    report = audit(clean_snapshot)
    assert _rules(report, "missing_title")
    assert _rules(report, "title_length") == []


def test_present_title_is_not_flagged(clean_snapshot):
    assert _rules(audit(clean_snapshot), "missing_title") == []


@pytest.mark.parametrize("length,flagged", [(19, True), (20, False), (60, False), (61, True)])
def test_title_length_bounds(clean_snapshot, length, flagged):
    clean_snapshot.seo[1] = clean_snapshot.seo[1].model_copy(update={"title": "A" * length})
    found = _rules(audit(clean_snapshot), "title_length")
    assert bool(found) is flagged
    if flagged:
        assert found[0].page == "about"
        assert found[0].detail["length"] == length
        assert str(length) in found[0].recommendation


def test_missing_meta_description_is_flagged(clean_snapshot):
    clean_snapshot.seo[2] = clean_snapshot.seo[2].model_copy(update={"description": "  "})
    found = _rules(audit(clean_snapshot), "missing_meta_description")
    assert len(found) == 1
    assert found[0].page == "platform"
    assert "meta description" in found[0].recommendation.lower()


def test_present_meta_description_is_not_flagged(clean_snapshot):
    assert _rules(audit(clean_snapshot), "missing_meta_description") == []


@pytest.mark.parametrize("alt", [None, "", "   "])
def test_missing_alt_text_is_flagged(clean_snapshot, alt):
    clean_snapshot.images[0] = clean_snapshot.images[0].model_copy(update={"alt": alt})
    found = _rules(audit(clean_snapshot), "missing_alt_text")
    assert len(found) == 1
    assert found[0].page == "home"
    assert found[0].section == "home-hero"
    assert "alt text" in found[0].recommendation.lower()
    assert all(item.detail["image_id"] != "about-logo" for item in found)


def test_present_alt_text_is_not_flagged(clean_snapshot):
    assert _rules(audit(clean_snapshot), "missing_alt_text") == []


def test_low_contrast_is_flagged_with_the_computed_ratio(clean_snapshot):
    pair = ColorPair(
        foreground="#777777",
        background="#FFFFFF",
        large_text=False,
        page="home",
        section="home-hero",
    )
    clean_snapshot.styles = clean_snapshot.styles.model_copy(update={"color_pairs": [pair]})
    found = _rules(audit(clean_snapshot), "low_contrast")
    ratio = contrast_ratio("#777777", "#FFFFFF")
    assert len(found) == 1
    assert found[0].severity == "block"
    assert found[0].page == "home"
    assert found[0].section == "home-hero"
    assert found[0].detail["ratio"] == round(ratio, 2)
    assert f"{ratio:.2f}:1" in found[0].recommendation
    assert "4.5:1" in found[0].recommendation


def test_sufficient_contrast_is_not_flagged(clean_snapshot):
    assert contrast_ratio("#000000", "#FFFFFF") >= 4.5
    assert _rules(audit(clean_snapshot), "low_contrast") == []


def test_large_text_uses_the_lower_threshold(clean_snapshot):
    ratio = contrast_ratio("#888888", "#FFFFFF")
    assert 3.0 <= ratio < 4.5
    failing = ColorPair(foreground="#888888", background="#FFFFFF", large_text=False, page="home", section="home-hero")
    passing = failing.model_copy(update={"large_text": True})
    clean_snapshot.styles = clean_snapshot.styles.model_copy(update={"color_pairs": [failing]})
    assert _rules(audit(clean_snapshot), "low_contrast")
    clean_snapshot.styles = clean_snapshot.styles.model_copy(update={"color_pairs": [passing]})
    assert _rules(audit(clean_snapshot), "low_contrast") == []


def test_too_many_fonts_is_flagged(clean_snapshot):
    cap = load_knowledge()["audit"]["max_font_families"]
    fonts = ["Neue Haas Grotesk", "Source Serif 4", "IBM Plex Mono"]
    assert len(fonts) == cap + 1
    clean_snapshot.styles = clean_snapshot.styles.model_copy(update={"fonts": fonts})
    found = _rules(audit(clean_snapshot), "too_many_fonts")
    assert len(found) == 1
    assert found[0].page is None
    assert "IBM Plex Mono" in found[0].recommendation
    assert str(cap) in found[0].recommendation


def test_font_count_within_the_cap_is_not_flagged(clean_snapshot):
    fonts = ["Inter", "inter", "Source Serif 4"]
    clean_snapshot.styles = clean_snapshot.styles.model_copy(update={"fonts": fonts})
    assert _rules(audit(clean_snapshot), "too_many_fonts") == []


def test_missing_contact_is_flagged_even_when_a_cta_exists(clean_snapshot):
    pages = [page for page in clean_snapshot.pages if page.id != "contact"]
    pages = [
        page.model_copy(
            update={
                "sections": [section for section in page.sections if section.type != "contact"],
            }
        )
        for page in pages
    ]
    clean_snapshot.pages = pages
    clean_snapshot.seo = [seo for seo in clean_snapshot.seo if seo.page != "contact"]
    found = _rules(audit(clean_snapshot), "missing_contact_or_cta")
    assert any(item.detail["missing"] == "contact" for item in found)
    assert found[0].page == "contact" or any(item.page == "contact" for item in found)
    assert all(item.detail["missing"] != "cta" for item in found)


def test_missing_cta_is_flagged_even_when_contact_exists(clean_snapshot):
    pages = []
    for page in clean_snapshot.pages:
        sections = [section.model_copy(update={"has_cta": False}) for section in page.sections]
        pages.append(page.model_copy(update={"sections": sections}))
    clean_snapshot.pages = pages
    found = _rules(audit(clean_snapshot), "missing_contact_or_cta")
    assert len(found) == 1
    assert found[0].detail["missing"] == "cta"
    assert found[0].page == "home"
    assert found[0].section == "home-hero"
    assert "button" in found[0].recommendation.lower()


def test_contact_and_cta_together_are_not_flagged(clean_snapshot):
    assert _rules(audit(clean_snapshot), "missing_contact_or_cta") == []


def test_oversized_mobile_hero_is_flagged(clean_snapshot):
    cap = load_knowledge()["audit"]["mobile_hero_max_px"]
    pages = []
    for page in clean_snapshot.pages:
        sections = []
        for section in page.sections:
            if section.id == "home-hero":
                sections.append(section.model_copy(update={"mobile_font_px": cap + 24}))
            elif section.id == "home-text":
                sections.append(section.model_copy(update={"mobile_font_px": cap + 40}))
            else:
                sections.append(section)
        pages.append(page.model_copy(update={"sections": sections}))
    clean_snapshot.pages = pages
    found = _rules(audit(clean_snapshot), "oversized_hero_text")
    assert len(found) == 1
    assert found[0].page == "home"
    assert found[0].section == "home-hero"
    assert str(cap) in found[0].recommendation
    assert "8-column" in found[0].recommendation


def test_modest_mobile_hero_is_not_flagged(clean_snapshot):
    assert _rules(audit(clean_snapshot), "oversized_hero_text") == []


def test_missing_recommended_pages_are_flagged(clean_snapshot):
    clean_snapshot.pages = [page for page in clean_snapshot.pages if page.id != "about"]
    found = _rules(audit(clean_snapshot), "missing_pages")
    assert [item.page for item in found] == ["about"]
    assert "about" in found[0].recommendation
    assert "home, about, platform, and contact" in found[0].recommendation


def test_index_alias_counts_as_home(clean_snapshot):
    pages = []
    for page in clean_snapshot.pages:
        if page.id == "home":
            pages.append(page.model_copy(update={"id": "index"}))
        else:
            pages.append(page)
    clean_snapshot.pages = pages
    assert _rules(audit(clean_snapshot), "missing_pages") == []


def test_recommended_pages_present_are_not_flagged(clean_snapshot):
    assert _rules(audit(clean_snapshot), "missing_pages") == []


def test_hero_at_the_cap_is_not_oversized(clean_snapshot):
    cap = load_knowledge()["audit"]["mobile_hero_max_px"]
    assert cap == 40
    pages = []
    for page in clean_snapshot.pages:
        sections = [
            section.model_copy(update={"mobile_font_px": cap}) if section.type == "hero" else section
            for section in page.sections
        ]
        pages.append(page.model_copy(update={"sections": sections}))
    clean_snapshot.pages = pages
    assert _rules(audit(clean_snapshot), "oversized_hero_text") == []


def test_section_snapshot_rejects_unknown_types():
    with pytest.raises(ValueError):
        SectionSnapshot(id="x", type="carousel")  # type: ignore[arg-type]
