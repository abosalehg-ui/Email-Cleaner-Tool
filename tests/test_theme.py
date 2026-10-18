"""رموز التصميم: كل زوج نص/خلفية معتمد يجتاز WCAG AA."""

from __future__ import annotations

import re

import pytest

theme = pytest.importorskip("email_cleaner.theme")


@pytest.mark.parametrize(("fg", "bg"), theme.CONTRAST_PAIRS)
def test_contrast_meets_wcag_aa(fg, bg):
    ratio = theme.contrast_ratio(theme.COLORS[fg], theme.COLORS[bg])
    assert ratio >= 4.5, f"{fg} على {bg}: {ratio:.2f}:1"


def test_contrast_formula_known_values():
    assert theme.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert theme.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)


def test_every_color_is_a_hex_token():
    for name, value in theme.COLORS.items():
        assert re.fullmatch(r"#[0-9a-f]{6}", value), name


def test_spacing_is_on_a_4px_grid():
    assert all(value % 4 == 0 for value in theme.SPACE.values())
