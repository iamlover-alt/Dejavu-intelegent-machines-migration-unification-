"""Structured inputs and outputs for the website agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SectionType = Literal[
    "hero",
    "text",
    "image",
    "gallery",
    "portfolio",
    "team",
    "faq",
    "pricing",
    "contact",
]
PageName = Literal["home", "about", "platform", "contact"]
Severity = Literal["info", "warn", "block"]
GuideAction = Literal["build", "refine"]

SECTION_TYPES: tuple[str, ...] = (
    "hero",
    "text",
    "image",
    "gallery",
    "portfolio",
    "team",
    "faq",
    "pricing",
    "contact",
)
PAGE_NAMES: tuple[str, ...] = ("home", "about", "platform", "contact")


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SectionSnapshot(APIModel):
    id: str
    type: SectionType
    has_cta: bool = False
    mobile_font_px: int | None = None


class PageSnapshot(APIModel):
    id: str
    slug: str
    sections: list[SectionSnapshot] = Field(default_factory=list)


class SeoSnapshot(APIModel):
    page: str
    title: str | None = None
    description: str | None = None
    slug: str | None = None
    social_image: str | None = None


class ImageSnapshot(APIModel):
    id: str
    page: str
    section: str
    alt: str | None = None


class ColorPair(APIModel):
    foreground: str
    background: str
    large_text: bool = False
    page: str | None = None
    section: str | None = None


class StyleSnapshot(APIModel):
    fonts: list[str] = Field(default_factory=list)
    color_pairs: list[ColorPair] = Field(default_factory=list)


class SiteSnapshot(APIModel):
    pages: list[PageSnapshot]
    seo: list[SeoSnapshot]
    images: list[ImageSnapshot] = Field(default_factory=list)
    styles: StyleSnapshot = Field(default_factory=StyleSnapshot)


class AuditFinding(APIModel):
    rule: str
    severity: Severity
    recommendation: str
    page: str | None = None
    section: str | None = None
    detail: dict = Field(default_factory=dict)


class AuditReport(APIModel):
    findings: list[AuditFinding]


class CopyBrief(APIModel):
    facts: list[str] = Field(default_factory=list)
    sections: list[SectionType] = Field(default_factory=list)
    audience: str | None = None


class SectionCopy(APIModel):
    section_type: SectionType
    page: str
    heading: str
    body: str
    cta: str | None = None


class PageCopy(APIModel):
    page: PageName
    sections: list[SectionCopy]
    paper_only: bool = False


class CopyValidation(APIModel):
    accepted: bool
    violations: list[str]


class GuideTarget(APIModel):
    action: GuideAction = "build"


class GuideStep(APIModel):
    id: str
    order: int
    title: str
    detail: str
    requires_paid_plan: bool
    changes_live_dns: bool
    owner_approval_required: bool
    note: str


class ImplementationGuide(APIModel):
    target: GuideAction
    domain: str
    punycode: str
    statement: str
    executed: Literal[False] = False
    steps: list[GuideStep]
