from __future__ import annotations

from midplane.agents.website.knowledge import PAID_PLAN_IDS, load_knowledge, load_site_context
from midplane.agents.website.schemas import PAGE_NAMES, SECTION_TYPES


def test_paid_features_are_never_marked_free():
    knowledge = load_knowledge()
    assert knowledge["billing"]["permanent_free_website_plan"] is False
    assert "no permanent free website plan" in knowledge["billing"]["statement"].lower()
    assert knowledge["billing"]["trial"]["permanent"] is False
    assert knowledge["billing"]["trial_days"] == 14
    assert knowledge["billing"]["mobile_start"]["paid"] is True
    for feature in knowledge["features"]:
        assert feature["free"] is False
        plans = set(feature["plans"])
        assert plans <= set(PAID_PLAN_IDS)
        if plans:
            assert feature["requires_website_plan"] is True
        if feature["available_during_trial"]:
            assert feature["free"] is False


def test_domain_dns_is_free_with_the_domain_and_is_not_a_free_website():
    knowledge = load_knowledge()
    dns = next(feature for feature in knowledge["features"] if feature["id"] == "domain_dns")
    billing = knowledge["billing"]["domain_dns"]
    assert dns["free_with_domain"] is True
    assert dns["free"] is False
    assert dns["plans"] == []
    assert dns["requires_website_plan"] is False
    assert billing["free_website"] is False
    assert "free with the domain" in billing["statement"]
    assert "not a free website" in billing["statement"]


def test_core_only_code_features_are_not_on_basic_and_not_free():
    knowledge = load_knowledge()
    by_id = {feature["id"]: feature for feature in knowledge["features"]}
    for feature_id in ("code_injection", "javascript_iframe_block", "announcement_bar"):
        feature = by_id[feature_id]
        assert feature["plans"] == ["core", "plus", "advanced"]
        assert "basic" not in feature["plans"]
        assert feature["free"] is False
        assert feature["requires_website_plan"] is True
    assert "basic" in by_id["custom_css"]["plans"]
    assert by_id["custom_css"]["free"] is False


def test_section_types_layout_seo_and_pages_are_covered():
    knowledge = load_knowledge()
    ids = {feature["id"] for feature in knowledge["features"]}
    for name in SECTION_TYPES:
        feature = next(item for item in knowledge["features"] if item["id"] == f"section_{name}")
        assert feature["plans"] == list(PAID_PLAN_IDS)
        assert feature["free"] is False
    for feature_id in (
        "fluid_engine_grid",
        "section_height",
        "section_spacing",
        "site_styles",
        "color_palette",
        "mobile_layout",
        "seo_title",
        "seo_description",
        "seo_slug",
        "seo_alt_text",
        "seo_social_image",
        "multi_page",
    ):
        assert feature_id in ids
        assert next(item for item in knowledge["features"] if item["id"] == feature_id)["free"] is False
    assert knowledge["layout"]["desktop_columns"] == 24
    assert knowledge["layout"]["mobile_columns"] == 8
    assert [page["id"] for page in knowledge["recommended_pages"]] == list(PAGE_NAMES)
    assert set(knowledge["copy_limits"]) == set(SECTION_TYPES)


def test_site_context_matches_the_current_company_site():
    site = load_site_context()
    assert site["domain"] == "déjàvuintelligentmachines.com"
    assert site["punycode"] == "xn--djvuintelligentmachines-54b7t.com"
    assert site["registration"]["via"] == "Google Workspace"
    assert site["registration"]["squarespace_domains_account_activated"] is False
    assert site["hosting"] == {
        "kind": "github_pages",
        "repo": "iamlover-alt/dejavu-website",
        "status": "in_progress",
        "cost": "free",
    }
    assert site["dns_plan"]["a_records"] == [
        "185.199.108.153",
        "185.199.109.153",
        "185.199.110.153",
        "185.199.111.153",
    ]
    assert site["dns_plan"]["www_cname"] == "iamlover-alt.github.io"
    assert site["dns_plan"]["applied"] is False
    assert site["brand"]["logo"] == "DV single-line monogram with circuit nodes"
    assert site["brand"]["colors"] == ["cyan", "violet", "navy"]
    assert site["brand"]["wordmark"] == "DEJAVÚ / INTELLIGENT MACHINES"
    topics = " ".join(site["topics"]).lower()
    for phrase in (
        "unified ai platform",
        "langgraph",
        "dynamic routing",
        "episode data flywheel",
        "multi-agent",
        "mcp",
        "paper-only",
        "observability",
        "vision",
    ):
        assert phrase in topics
    assert "real money" not in topics
    assert "live trading" not in topics
