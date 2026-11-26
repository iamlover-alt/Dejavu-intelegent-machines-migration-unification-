"""Load the Squarespace knowledge base and the company site fixture."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from midplane.agents.website.schemas import PAGE_NAMES, SECTION_TYPES

DATA_DIR = Path(__file__).resolve().parent / "data"
PAID_PLAN_IDS = ("basic", "core", "plus", "advanced")


def _read_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a mapping")
    return data


def validate_knowledge(data: dict) -> dict:
    """Reject a knowledge base that calls a paid website feature free."""

    billing = data["billing"]
    if billing["permanent_free_website_plan"] is not False:
        raise ValueError("Squarespace has no permanent free website plan")
    statement = billing["statement"]
    if "no permanent free website plan" not in statement.lower():
        raise ValueError("billing statement must say there is no permanent free website plan")
    if billing["trial_days"] != 14 or billing["trial"]["permanent"] is not False:
        raise ValueError("the website trial must stay a 14-day, non-permanent trial")
    dns = billing["domain_dns"]
    if dns["free_with_domain"] is not True or dns["requires_website_plan"] is not False:
        raise ValueError("domain DNS must stay free with the domain and not require a website plan")
    if dns["free_website"] is not False:
        raise ValueError("domain DNS is not a free website")
    if "free with the domain" not in dns["statement"].lower():
        raise ValueError("domain DNS statement must say free with the domain")

    pages = [item["id"] for item in data["recommended_pages"]]
    if pages != list(PAGE_NAMES):
        raise ValueError("recommended pages must be home, about, platform, contact")

    feature_ids = set()
    for feature in data["features"]:
        feature_ids.add(feature["id"])
        plans = set(feature["plans"])
        if not plans.issubset(set(PAID_PLAN_IDS)):
            raise ValueError(f"{feature['id']} names an unknown plan")
        if feature["free"] is not False:
            raise ValueError(f"{feature['id']} is marked free")
        if plans and feature["requires_website_plan"] is not True:
            raise ValueError(f"{feature['id']} is on a paid plan and must require a website plan")
        if feature["requires_website_plan"] and feature["free"] is not False:
            raise ValueError(f"{feature['id']} requires a website plan and cannot be free")
        if feature.get("free_with_domain") and feature["requires_website_plan"]:
            raise ValueError(f"{feature['id']} cannot be both a paid website feature and free with the domain")

    section_ids = {f"section_{name}" for name in SECTION_TYPES}
    if not section_ids.issubset(feature_ids):
        raise ValueError("section types are missing from the knowledge base")
    if "domain_dns" not in feature_ids:
        raise ValueError("domain DNS feature is missing")
    dns_feature = next(item for item in data["features"] if item["id"] == "domain_dns")
    if dns_feature["plans"] or dns_feature["free"] or not dns_feature["free_with_domain"]:
        raise ValueError("domain DNS feature must be free with the domain and not a paid plan feature")

    for name in SECTION_TYPES:
        limits = data["copy_limits"][name]
        for key in ("heading", "body", "cta"):
            if int(limits[key]) <= 0:
                raise ValueError(f"copy limit for {name}.{key} must be positive")
    if "paper-only" not in data["copy"]["required_trading_phrase"]:
        raise ValueError("trading copy must require the paper-only phrase")
    return data


@lru_cache(maxsize=1)
def load_knowledge() -> dict:
    return validate_knowledge(_read_yaml(DATA_DIR / "squarespace.yaml"))


def validate_site_context(data: dict) -> dict:
    if data["domain"] != "déjàvuintelligentmachines.com":
        raise ValueError("unexpected domain")
    if data["punycode"] != "xn--djvuintelligentmachines-54b7t.com":
        raise ValueError("unexpected punycode")
    if data["registration"]["squarespace_domains_account_activated"] is not False:
        raise ValueError("Squarespace Domains account is not activated")
    records = data["dns_plan"]["a_records"]
    if records != [
        "185.199.108.153",
        "185.199.109.153",
        "185.199.110.153",
        "185.199.111.153",
    ]:
        raise ValueError("unexpected GitHub Pages A records")
    if data["dns_plan"]["www_cname"] != "iamlover-alt.github.io":
        raise ValueError("unexpected www CNAME")
    if data["dns_plan"]["applied"] is not False:
        raise ValueError("the DNS plan is not applied")
    topics = " ".join(data["topics"]).lower()
    if "paper-only" not in topics:
        raise ValueError("site topics must describe trading as paper-only")
    for banned in ("real money", "live trading"):
        if banned in topics:
            raise ValueError("site topics must not suggest real-money trading")
    return data


@lru_cache(maxsize=1)
def load_site_context() -> dict:
    return validate_site_context(_read_yaml(DATA_DIR / "site_context.yaml"))
