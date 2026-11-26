"""Page copy from a brief, with a validator that blocks invented claims."""

from __future__ import annotations

import re

from midplane.agents.website.knowledge import load_knowledge, load_site_context
from midplane.agents.website.schemas import (
    PAGE_NAMES,
    SECTION_TYPES,
    CopyBrief,
    CopyValidation,
    PageCopy,
    PageName,
    SectionCopy,
    SectionType,
)


_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_TRADING = re.compile(r"\btrad(?:e|es|ing)\b", re.IGNORECASE)

_HEADINGS: dict[str, str] = {
    "hero": "Dejavú Intelligent Machines",
    "text": "Details",
    "image": "Image",
    "gallery": "Gallery",
    "portfolio": "Portfolio",
    "team": "Team",
    "faq": "Questions",
    "pricing": "Pricing",
    "contact": "Contact",
}
_FALLBACK = "No further facts were supplied for this section."
_PAPER_ONLY = "Trading controls are paper-only."


class CopyRejected(ValueError):
    """The rule-based copy failed its own validator."""


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def contains_phrase(text: str, phrase: str) -> bool:
    return _phrase_pattern(phrase).search(text) is not None


def _numbers(text: str) -> set[str]:
    flattened = re.sub(r"(?<=\d),(?=\d)", "", text)
    return set(_NUMBER.findall(flattened))


def _brief_text(brief: CopyBrief) -> str:
    parts = list(brief.facts)
    if brief.audience:
        parts.append(brief.audience)
    return "\n".join(parts)


def mentions_trading(text: str) -> bool:
    return _TRADING.search(text) is not None


def validate_copy(page: PageCopy, brief: CopyBrief) -> CopyValidation:
    """Reject numbers, claim words, and hype that the brief does not support."""

    knowledge = load_knowledge()["copy"]
    limits = load_knowledge()["copy_limits"]
    brief_text = _brief_text(brief)
    violations: list[str] = []
    rendered: list[str] = []

    for section in page.sections:
        cap = limits[section.section_type]
        rendered.extend([section.heading, section.body])
        if section.cta:
            rendered.append(section.cta)
        if len(section.heading) > int(cap["heading"]):
            violations.append(
                f"{section.section_type} heading is {len(section.heading)} characters; "
                f"limit is {cap['heading']}"
            )
        if len(section.body) > int(cap["body"]):
            violations.append(
                f"{section.section_type} body is {len(section.body)} characters; "
                f"limit is {cap['body']}"
            )
        if section.cta is not None and len(section.cta) > int(cap["cta"]):
            violations.append(
                f"{section.section_type} cta is {len(section.cta)} characters; "
                f"limit is {cap['cta']}"
            )

    copy_text = "\n".join(rendered)
    invented = sorted(_numbers(copy_text) - _numbers(brief_text))
    for number in invented:
        violations.append(f"invented number: {number}")

    for phrase in knowledge["banned_hype"]:
        if contains_phrase(copy_text, phrase):
            violations.append(f"hype word: {phrase}")

    brief_lower = brief_text.lower()
    for word in knowledge["claim_words"]:
        if contains_phrase(copy_text, word) and not contains_phrase(brief_lower, word):
            violations.append(f"ungrounded claim: {word}")

    for phrase in knowledge["banned_trading"]:
        if contains_phrase(copy_text, phrase):
            violations.append(f"real-money trading language: {phrase}")

    if mentions_trading(brief_text) or mentions_trading(copy_text):
        required = knowledge["required_trading_phrase"]
        if not contains_phrase(copy_text, required):
            violations.append(f"trading copy must contain {required}")

    return CopyValidation(accepted=not violations, violations=violations)


def _usable_facts(facts: list[str]) -> list[str]:
    knowledge = load_knowledge()["copy"]
    banned = list(knowledge["banned_hype"]) + list(knowledge["banned_trading"])
    usable: list[str] = []
    for fact in facts:
        text = fact.strip()
        if not text:
            continue
        if any(contains_phrase(text, phrase) for phrase in banned):
            continue
        usable.append(text)
    return usable


def _sentence(fact: str) -> str:
    text = " ".join(fact.split())
    if text[-1] not in ".!?":
        text += "."
    return text


def _fit(parts: list[str], limit: int) -> str:
    chosen: list[str] = []
    for part in parts:
        candidate = " ".join(chosen + [part])
        if len(candidate) <= limit:
            chosen.append(part)
    if chosen:
        return " ".join(chosen)
    if parts and len(parts[0]) <= limit:
        return parts[0]
    fallback = _FALLBACK if len(_FALLBACK) <= limit else _FALLBACK[:limit]
    return fallback


def _sections_for(page: str, brief: CopyBrief) -> list[str]:
    if brief.sections:
        return list(brief.sections)
    defaults = {
        "home": ["hero", "text"],
        "about": ["text"],
        "platform": ["hero", "text"],
        "contact": ["contact"],
    }
    return defaults[page]


def write_copy(page: PageName | str, brief: CopyBrief) -> PageCopy:
    """Write section copy using only facts from the brief."""

    if page not in PAGE_NAMES:
        raise ValueError(f"unsupported page: {page}")
    knowledge = load_knowledge()
    limits = knowledge["copy_limits"]
    facts = [_sentence(fact) for fact in _usable_facts(brief.facts)]
    trading = mentions_trading(_brief_text(brief)) or any(mentions_trading(fact) for fact in brief.facts)
    if trading and not any(contains_phrase(fact, "paper-only") for fact in facts):
        facts.append(_PAPER_ONLY)
    if trading:
        facts = [fact for fact in facts if contains_phrase(fact, "paper-only")] + [
            fact for fact in facts if not contains_phrase(fact, "paper-only")
        ]

    sections: list[SectionCopy] = []
    for section_type in _sections_for(page, brief):
        if section_type not in SECTION_TYPES:
            raise ValueError(f"unsupported section: {section_type}")
        cap = limits[section_type]
        heading = _HEADINGS[section_type]
        if len(heading) > int(cap["heading"]):
            heading = heading[: int(cap["heading"])]
        body = _fit(facts, int(cap["body"]))
        cta = None if section_type == "contact" else "Contact"
        if cta is not None and len(cta) > int(cap["cta"]):
            cta = None
        sections.append(
            SectionCopy(
                section_type=section_type,  # type: ignore[arg-type]
                page=page,
                heading=heading,
                body=body,
                cta=cta,
            )
        )

    result = PageCopy(page=page, sections=sections, paper_only=trading)  # type: ignore[arg-type]
    validation = validate_copy(result, brief)
    if not validation.accepted:
        raise CopyRejected("; ".join(validation.violations))
    return result


def brief_from_site_context(page: PageName | str, sections: list[SectionType] | None = None) -> CopyBrief:
    context = load_site_context()
    return CopyBrief(facts=list(context["topics"]), sections=list(sections or []))
