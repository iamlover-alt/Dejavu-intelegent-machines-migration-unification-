"""Rule-based audit of a Squarespace site snapshot."""

from __future__ import annotations

from midplane.agents.website.contrast import contrast_ratio, normalize_hex
from midplane.agents.website.knowledge import load_knowledge
from midplane.agents.website.schemas import (
    PAGE_NAMES,
    AuditFinding,
    AuditReport,
    SiteSnapshot,
)


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _finding(
    rule: str,
    severity: str,
    recommendation: str,
    page: str | None = None,
    section: str | None = None,
    **detail: object,
) -> AuditFinding:
    return AuditFinding(
        rule=rule,
        severity=severity,  # type: ignore[arg-type]
        recommendation=recommendation,
        page=page,
        section=section,
        detail=dict(detail),
    )


def audit(snapshot: SiteSnapshot) -> AuditReport:
    knowledge = load_knowledge()
    rules = knowledge["audit"]
    findings: list[AuditFinding] = []
    findings.extend(_seo_findings(snapshot, rules))
    findings.extend(_alt_findings(snapshot))
    findings.extend(_contrast_findings(snapshot, rules))
    findings.extend(_font_findings(snapshot, rules))
    findings.extend(_contact_findings(snapshot))
    findings.extend(_hero_findings(snapshot, rules))
    findings.extend(_page_findings(snapshot))
    findings.sort(key=lambda item: (item.rule, item.page or "", item.section or ""))
    return AuditReport(findings=findings)


def _seo_findings(snapshot: SiteSnapshot, rules: dict) -> list[AuditFinding]:
    title_min = int(rules["title_min"])
    title_max = int(rules["title_max"])
    found: list[AuditFinding] = []
    for seo in snapshot.seo:
        if _blank(seo.title):
            found.append(
                _finding(
                    "missing_title",
                    "block",
                    "Add a page title in Squarespace page settings, under SEO. "
                    f"Use {title_min} to {title_max} characters.",
                    page=seo.page,
                )
            )
        else:
            length = len(seo.title.strip())
            if length < title_min or length > title_max:
                direction = "Lengthen" if length < title_min else "Shorten"
                found.append(
                    _finding(
                        "title_length",
                        "warn",
                        f"{direction} the title to between {title_min} and {title_max} characters. "
                        f"This title is {length} characters.",
                        page=seo.page,
                        length=length,
                        title_min=title_min,
                        title_max=title_max,
                    )
                )
        if _blank(seo.description):
            found.append(
                _finding(
                    "missing_meta_description",
                    "warn",
                    "Add a meta description in Squarespace page settings, under SEO.",
                    page=seo.page,
                )
            )
    return found


def _alt_findings(snapshot: SiteSnapshot) -> list[AuditFinding]:
    found: list[AuditFinding] = []
    for image in snapshot.images:
        if _blank(image.alt):
            found.append(
                _finding(
                    "missing_alt_text",
                    "warn",
                    "Add alt text that describes this image. On an image block, the alt text "
                    "field stays empty until it is filled in.",
                    page=image.page,
                    section=image.section,
                    image_id=image.id,
                )
            )
    return found


def _contrast_findings(snapshot: SiteSnapshot, rules: dict) -> list[AuditFinding]:
    normal = float(rules["contrast_normal"])
    large = float(rules["contrast_large"])
    found: list[AuditFinding] = []
    for pair in snapshot.styles.color_pairs:
        ratio = contrast_ratio(pair.foreground, pair.background)
        threshold = large if pair.large_text else normal
        if ratio < threshold:
            kind = "large text" if pair.large_text else "normal text"
            found.append(
                _finding(
                    "low_contrast",
                    "block",
                    f"Contrast ratio {ratio:.2f}:1 is below WCAG AA {threshold:.1f}:1 for {kind}. "
                    f"Change {normalize_hex(pair.foreground)} on {normalize_hex(pair.background)} "
                    "in site styles.",
                    page=pair.page,
                    section=pair.section,
                    ratio=round(ratio, 2),
                    threshold=threshold,
                    foreground=normalize_hex(pair.foreground),
                    background=normalize_hex(pair.background),
                )
            )
    return found


def _font_findings(snapshot: SiteSnapshot, rules: dict) -> list[AuditFinding]:
    cap = int(rules["max_font_families"])
    families: list[str] = []
    seen: set[str] = set()
    for font in snapshot.styles.fonts:
        key = font.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        families.append(font.strip())
    if len(families) <= cap:
        return []
    extra = ", ".join(families[cap:])
    return [
        _finding(
            "too_many_fonts",
            "warn",
            f"Use at most {cap} font families (one heading font and one paragraph font). "
            f"Remove {extra}.",
            count=len(families),
            fonts=families,
        )
    ]


def _contact_findings(snapshot: SiteSnapshot) -> list[AuditFinding]:
    has_contact = any(page.id.casefold() == "contact" for page in snapshot.pages) or any(
        section.type == "contact" for page in snapshot.pages for section in page.sections
    )
    has_cta = any(section.has_cta for page in snapshot.pages for section in page.sections)
    found: list[AuditFinding] = []
    if not has_contact:
        found.append(
            _finding(
                "missing_contact_or_cta",
                "warn",
                "Add a contact page with a form section so a visitor can reach the company.",
                page="contact",
                missing="contact",
            )
        )
    if not has_cta:
        hero = next(
            (
                (page.id, section.id)
                for page in snapshot.pages
                for section in page.sections
                if section.type == "hero"
            ),
            (None, None),
        )
        found.append(
            _finding(
                "missing_contact_or_cta",
                "warn",
                "Add a button that asks the visitor to take the next step, such as Contact.",
                page=hero[0],
                section=hero[1],
                missing="cta",
            )
        )
    return found


def _hero_findings(snapshot: SiteSnapshot, rules: dict) -> list[AuditFinding]:
    cap = int(rules["mobile_hero_max_px"])
    found: list[AuditFinding] = []
    for page in snapshot.pages:
        for section in page.sections:
            size = section.mobile_font_px
            if section.type != "hero" or size is None or size <= cap:
                continue
            found.append(
                _finding(
                    "oversized_hero_text",
                    "warn",
                    f"Lower the mobile hero text from {size}px to {cap}px or smaller. "
                    "This is the agent's readability cap. Edit the mobile layout on the "
                    "8-column Fluid Engine grid; it does not follow the desktop type size.",
                    page=page.id,
                    section=section.id,
                    mobile_font_px=size,
                    max_px=cap,
                )
            )
    return found


def _page_findings(snapshot: SiteSnapshot) -> list[AuditFinding]:
    present = {page.id.casefold() for page in snapshot.pages}
    aliases = {"index": "home", "homepage": "home"}
    present = {aliases.get(name, name) for name in present}
    found: list[AuditFinding] = []
    for name in PAGE_NAMES:
        if name in present:
            continue
        found.append(
            _finding(
                "missing_pages",
                "warn",
                f"Add the {name} page. The recommended structure is home, about, platform, and contact.",
                page=name,
                missing=name,
            )
        )
    return found
