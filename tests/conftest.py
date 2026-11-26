from __future__ import annotations

import pytest

from midplane.agents.website.schemas import (
    ColorPair,
    ImageSnapshot,
    PageSnapshot,
    SeoSnapshot,
    SectionSnapshot,
    SiteSnapshot,
    StyleSnapshot,
)


@pytest.fixture
def clean_snapshot() -> SiteSnapshot:
    """A snapshot that satisfies every audit rule."""

    def page(page_id: str, slug: str, title: str, sections: list[SectionSnapshot]) -> PageSnapshot:
        return PageSnapshot(id=page_id, slug=slug, sections=sections)

    pages = [
        page(
            "home",
            "/",
            "Dejavú Intelligent Machines",
            [
                SectionSnapshot(id="home-hero", type="hero", has_cta=True, mobile_font_px=32),
                SectionSnapshot(id="home-text", type="text"),
            ],
        ),
        page(
            "about",
            "/about",
            "About Dejavú Intelligent Machines",
            [SectionSnapshot(id="about-text", type="text")],
        ),
        page(
            "platform",
            "/platform",
            "Dejavú Intelligent Machines Platform",
            [SectionSnapshot(id="platform-text", type="text")],
        ),
        page(
            "contact",
            "/contact",
            "Contact Dejavú Intelligent Machines",
            [SectionSnapshot(id="contact-form", type="contact", has_cta=True)],
        ),
    ]
    seo = [
        SeoSnapshot(
            page=item.id,
            title=item.id and {
                "home": "Dejavú Intelligent Machines",
                "about": "About Dejavú Intelligent Machines",
                "platform": "Dejavú Intelligent Machines Platform",
                "contact": "Contact Dejavú Intelligent Machines",
            }[item.id],
            description="The unified AI platform from Dejavú Intelligent Machines.",
            slug=item.slug,
            social_image="social.png",
        )
        for item in pages
    ]
    images = [
        ImageSnapshot(id=f"{item.id}-logo", page=item.id, section=item.sections[0].id, alt="DV monogram logo")
        for item in pages
    ]
    styles = StyleSnapshot(
        fonts=["Neue Haas Grotesk", "Source Serif 4"],
        color_pairs=[
            ColorPair(
                foreground="#000000",
                background="#FFFFFF",
                large_text=False,
                page="home",
                section="home-hero",
            )
        ],
    )
    return SiteSnapshot(pages=pages, seo=seo, images=images, styles=styles)
