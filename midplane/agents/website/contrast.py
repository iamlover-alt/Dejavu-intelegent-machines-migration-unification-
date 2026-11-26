"""WCAG 2.x contrast ratios for sRGB colors."""

from __future__ import annotations

import re


_HEX = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def normalize_hex(value: str) -> str:
    raw = value.strip()
    if not _HEX.match(raw):
        raise ValueError(f"expected a 6-digit hex color, got {value!r}")
    return "#" + raw.lstrip("#").upper()


def _channel(value: int) -> float:
    scaled = value / 255
    if scaled <= 0.04045:
        return scaled / 12.92
    return ((scaled + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    hex_color = normalize_hex(color).lstrip("#")
    red = int(hex_color[0:2], 16)
    green = int(hex_color[2:4], 16)
    blue = int(hex_color[4:6], 16)
    return (0.2126 * _channel(red)) + (0.7152 * _channel(green)) + (0.0722 * _channel(blue))


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG contrast ratio, from 1 to 21."""

    lighter = max(relative_luminance(foreground), relative_luminance(background))
    darker = min(relative_luminance(foreground), relative_luminance(background))
    return (lighter + 0.05) / (darker + 0.05)
