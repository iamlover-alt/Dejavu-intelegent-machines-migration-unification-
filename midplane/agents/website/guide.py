"""Ordered Squarespace implementation steps. This module never changes DNS."""

from __future__ import annotations

from midplane.agents.website.knowledge import load_knowledge, load_site_context
from midplane.agents.website.schemas import GuideStep, GuideTarget, ImplementationGuide

PAID_NOTE = "Ask the owner first. This agent does not buy a Squarespace plan."
DNS_NOTE = "Ask the owner first. This agent does not change DNS."
ACCOUNT_NOTE = "Ask the owner first. This agent does not sign in and does not change DNS."


def _step(
    order: int,
    step_id: str,
    title: str,
    detail: str,
    *,
    requires_paid_plan: bool,
    changes_live_dns: bool,
    note: str,
) -> GuideStep:
    return GuideStep(
        id=step_id,
        order=order,
        title=title,
        detail=detail,
        requires_paid_plan=requires_paid_plan,
        changes_live_dns=changes_live_dns,
        owner_approval_required=requires_paid_plan or changes_live_dns or "Ask the owner first." in note,
        note=note,
    )


def implementation_guide(target: GuideTarget | str) -> ImplementationGuide:
    """Return ordered steps for déjàvuintelligentmachines.com. Does not perform them."""

    if isinstance(target, str):
        target = GuideTarget(action=target)  # type: ignore[arg-type]
    action = target.action
    knowledge = load_knowledge()
    site = load_site_context()
    statement = knowledge["billing"]["statement"]
    dns_statement = knowledge["billing"]["domain_dns"]["statement"]
    records = ", ".join(site["dns_plan"]["a_records"])
    layout = knowledge["layout"]
    verb = "Create" if action == "build" else "Refine"
    design_title = (
        "Design the four pages during the trial"
        if action == "build"
        else "Refine the four pages during the trial"
    )
    design_detail = (
        f"{verb} home, about, platform, and contact in Fluid Engine on Squarespace 7.1. "
        f"The desktop grid is {layout['desktop_columns']} columns and the mobile grid is "
        f"{layout['mobile_columns']} columns below {layout['mobile_breakpoint_px'] + 1}px. "
        f"{layout['section_height']} {layout['spacing']} "
        "Use hero, text, image, gallery, portfolio, team, FAQ, pricing, and contact sections "
        "from blocks included on Basic and above. Do not add code injection, JavaScript, or "
        "an announcement bar; those require Core, Plus, or Advanced."
    )

    steps = [
        _step(
            1,
            "trial_notice",
            "State the trial limit before any design work",
            statement
            + " There is no permanent free Squarespace website. The trial is 14 days, and "
            "trial sites cannot accept payments.",
            requires_paid_plan=False,
            changes_live_dns=False,
            note="This step only records the plan fact. It does not start a trial or buy a plan.",
        ),
        _step(
            2,
            "design_pages",
            design_title,
            design_detail,
            requires_paid_plan=False,
            changes_live_dns=False,
            note="Editor work during the trial does not keep the site online after day 14.",
        ),
        _step(
            3,
            "brand_and_seo",
            "Apply the brand, type, contrast, and SEO fields",
            "Use the DV single-line monogram with circuit nodes, cyan and violet on navy, and "
            f"the wordmark {site['brand']['wordmark']}. In site styles, keep one heading font "
            "and one paragraph font. Check text against WCAG AA (4.5:1 for normal text, 3:1 "
            "for large text). In page settings, set the title, meta description, URL slug, and "
            "social sharing image. Add alt text on every image block. Keep mobile hero text at "
            "40px or smaller; that cap is the agent's readability rule.",
            requires_paid_plan=False,
            changes_live_dns=False,
            note="These settings are available in the trial and on every paid website plan.",
        ),
        _step(
            4,
            "trading_language",
            "Keep trading language paper-only",
            "Any copy about trading controls must say paper-only and must sit behind the hard "
            "external controls. Do not write real-money trading, live trading, or a funded account.",
            requires_paid_plan=False,
            changes_live_dns=False,
            note="This is a copy rule. It does not place an order.",
        ),
        _step(
            5,
            "publish_paid_plan",
            "Ask before paying to keep a Squarespace site online",
            "Publishing the Squarespace site past the 14-day trial requires a paid plan. Basic "
            "is the lowest plan that includes Fluid Engine, these sections, site styles, mobile "
            "layout, and built-in SEO. Core and above add code injection and JavaScript blocks, "
            "which this guide does not use. Do not describe Basic, Core, Plus, or Advanced as free.",
            requires_paid_plan=True,
            changes_live_dns=False,
            note=PAID_NOTE,
        ),
        _step(
            6,
            "activate_domains_account",
            "Ask the owner to activate Squarespace Domains",
            f"{site['domain']} (punycode {site['punycode']}) was registered via "
            f"{site['registration']['via']}. The Squarespace Domains account is not activated. "
            f"{dns_statement}",
            requires_paid_plan=False,
            changes_live_dns=False,
            note=ACCOUNT_NOTE,
        ),
        _step(
            7,
            "dns_github_pages",
            "Record the GitHub Pages DNS plan without applying it",
            "The in-progress free host is GitHub Pages at "
            f"{site['hosting']['repo']}. The planned records are four A records ({records}) "
            f"and a www CNAME to {site['dns_plan']['www_cname']}. "
            f"{dns_statement} Applying these records would point the live domain at GitHub Pages.",
            requires_paid_plan=False,
            changes_live_dns=True,
            note=DNS_NOTE,
        ),
        _step(
            8,
            "dns_squarespace_hosting",
            "Ask before pointing the domain at Squarespace hosting",
            "Connecting the domain to a Squarespace-hosted site replaces DNS and also requires "
            "a paid website plan to stay online after the trial. Leave the GitHub Pages records "
            "in place until the owner chooses.",
            requires_paid_plan=True,
            changes_live_dns=True,
            note=f"{PAID_NOTE} {DNS_NOTE}",
        ),
    ]
    return ImplementationGuide(
        target=action,
        domain=site["domain"],
        punycode=site["punycode"],
        statement=statement,
        executed=False,
        steps=steps,
    )
