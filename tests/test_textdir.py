"""تحويل النص العربي لترتيب العرض على X11، والتجاوز على غيره."""

from __future__ import annotations

import pytest

from email_cleaner import textdir


@pytest.fixture(autouse=True)
def _reset():
    yield
    textdir.configure("win32")


def test_identity_on_windows_and_mac():
    for system in ("win32", "aqua"):
        assert textdir.configure(system) is True
        assert textdir.visual("فحص") == "فحص"


def test_empty_and_none_are_safe():
    textdir.configure("x11")
    assert textdir.visual("") == ""


def test_has_arabic():
    assert textdir.has_arabic("Gmail وYahoo")
    assert not textdir.has_arabic("Weekly digest")


class TestX11:
    @pytest.fixture(autouse=True)
    def _require_libs(self):
        pytest.importorskip("arabic_reshaper")
        pytest.importorskip("bidi")
        assert textdir.configure("x11") is True

    def test_arabic_is_reversed_into_visual_order(self):
        shown = textdir.visual("فحص")
        assert shown != "فحص"
        # الحروف تتحول لأشكالها المتصلة (Presentation Forms) ثم تُعكس.
        assert all("ﭐ" <= ch <= "﻿" for ch in shown)

    def test_latin_text_is_untouched(self):
        assert textdir.visual("deals@noon.com") == "deals@noon.com"

    def test_brackets_are_mirrored_inside_arabic(self):
        shown = textdir.visual("مرسل (deals@noon.com)")
        assert "(deals@noon.com)" in shown

    def test_arabic_line_starting_with_latin_is_still_rtl(self):
        # في ترتيب العرض لفقرة يمينية، أول كلمة منطقية تقع آخر السطر.
        assert textdir.visual("Gmail وYahoo يتطلبان").endswith("Gmail")

    def test_rlm_forces_rtl_and_is_removed(self):
        shown = textdir.visual(f"{textdir.RLM}• Precedence: bulk")
        assert textdir.RLM not in shown
        assert shown.endswith("•")

    def test_lines_are_converted_independently(self):
        first, second = textdir.visual("سطر أول\nسطر ثان").split("\n")
        assert first == textdir.visual("سطر أول")
        assert second == textdir.visual("سطر ثان")


def test_missing_libraries_report_false(monkeypatch):
    monkeypatch.setattr(textdir, "build_transform", lambda: None)
    assert textdir.configure("x11") is False
    assert textdir.visual("فحص") == "فحص"
