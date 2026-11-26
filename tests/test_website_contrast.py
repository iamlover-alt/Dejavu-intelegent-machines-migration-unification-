from __future__ import annotations

import pytest

from midplane.agents.website.contrast import contrast_ratio, relative_luminance


def test_black_on_white_is_twenty_one():
    assert contrast_ratio("#000000", "#FFFFFF") == 21
    assert contrast_ratio("#FFFFFF", "#000000") == 21


def test_same_color_is_one():
    assert contrast_ratio("#FFFFFF", "#FFFFFF") == 1
    assert contrast_ratio("#000000", "#000000") == 1


def test_published_gray_on_white():
    # #767676 on white is the commonly cited ~4.54:1 WCAG boundary sample.
    assert round(contrast_ratio("#767676", "#FFFFFF"), 2) == 4.54


def test_relative_luminance_of_black_and_white():
    assert relative_luminance("#000000") == 0
    assert relative_luminance("#FFFFFF") == 1


def test_mid_gray_sits_between_large_and_normal_thresholds():
    ratio = contrast_ratio("#888888", "#FFFFFF")
    assert ratio == pytest.approx(3.54, abs=0.01)
    assert 3.0 <= ratio < 4.5


def test_low_gray_fails_normal_text():
    ratio = contrast_ratio("#777777", "#FFFFFF")
    assert ratio < 4.5
    assert round(ratio, 2) == 4.48
