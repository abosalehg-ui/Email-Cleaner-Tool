"""تنسيق العرض: التواريخ والبحث والترتيب - بلا Tk."""

from __future__ import annotations

from datetime import datetime, timezone

from email_cleaner.display import (
    format_date,
    format_full_date,
    matches,
    parse_date,
    score_level,
    sort_messages,
)
from email_cleaner.models import EmailMessage


def make(uid, subject="s", sender="a@x.com", date="", score=3, name="N"):
    return EmailMessage(uid, subject, name, sender, date, score)


class TestDates:
    NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

    def test_rfc2822_is_parsed(self):
        parsed = parse_date("Mon, 13 Oct 2026 10:22:01 +0300")
        assert parsed is not None
        assert parsed.utcoffset() is not None

    def test_garbage_is_none_not_a_crash(self):
        assert parse_date("not a date") is None
        assert parse_date("") is None

    def test_same_year_shows_day_and_arabic_month(self):
        text = format_date("Mon, 13 Oct 2026 10:22:01 +0000", now=self.NOW)
        assert "أكتوبر" in text
        assert "2026" not in text

    def test_older_year_includes_the_year(self):
        assert "2024" in format_date("Wed, 13 Mar 2024 10:22:01 +0000", now=self.NOW)

    def test_today_shows_time(self):
        today = self.NOW.astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
        assert format_date(today, now=self.NOW.astimezone()).startswith("اليوم")

    def test_unparseable_date_falls_back_to_raw_text(self):
        assert format_date("yesterday-ish") == "yesterday-ish"
        assert format_full_date("") == "—"


class TestSearch:
    def test_empty_query_matches_everything(self):
        assert matches(make("1"), "   ")

    def test_matches_sender_subject_and_name_case_insensitively(self):
        message = make("1", subject="Flash SALE today", sender="deals@noon.com", name="متجر نون")
        assert matches(message, "flash")
        assert matches(message, "NOON")
        assert matches(message, "متجر")

    def test_all_words_must_match(self):
        message = make("1", subject="Weekly digest", sender="news@x.com")
        assert matches(message, "weekly news")
        assert not matches(message, "weekly coupon")


class TestSorting:
    def test_score_descending(self):
        ordered = sort_messages([make("1", score=3), make("2", score=6)], "score", True)
        assert [m.uid for m in ordered] == ["2", "1"]

    def test_date_with_broken_values_sorts_last_when_descending(self):
        messages = [
            make("old", date="Mon, 01 Jan 2024 00:00:00 +0000"),
            make("broken", date="??"),
            make("new", date="Mon, 13 Oct 2026 00:00:00 +0000"),
        ]
        ordered = sort_messages(messages, "date", True)
        assert [m.uid for m in ordered] == ["new", "old", "broken"]

    def test_ties_keep_higher_scores_first(self):
        messages = [make("low", sender="a@x.com", score=3), make("high", sender="a@x.com", score=5)]
        assert [m.uid for m in sort_messages(messages, "sender", False)] == ["high", "low"]


def test_score_levels():
    assert score_level(3, 4) == "متوسطة"
    assert score_level(4, 4) == "عالية"
    assert score_level(6, 4) == "عالية جدًا"
