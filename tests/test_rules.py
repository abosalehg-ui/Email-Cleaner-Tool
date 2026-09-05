"""قواعد التصنيف: مطابقة المرسلين الموثوقين، الكلمات، وتحليل العناوين."""

from __future__ import annotations

import json

import pytest

from email_cleaner.rules import (
    InvalidAddressError,
    Rules,
    normalize_digits,
    split_address,
)


class TestSplitAddress:
    def test_valid_address(self):
        assert split_address("User@Example.COM") == ("user", "example.com")

    @pytest.mark.parametrize("bad", ["ahmed", "", "  ", "a@b", "a@@b.com", "@x.com"])
    def test_invalid_raises_instead_of_crashing(self, bad):
        # كان ``split('@')[1]`` يرفع IndexError فيصل للمستخدم كـ
        # "خطأ في الاتصال: list index out of range".
        with pytest.raises(InvalidAddressError):
            split_address(bad)


class TestTrustedSenders:
    def test_trusted_domain(self, rules):
        assert rules.is_trusted("anything@google.com")

    @pytest.mark.parametrize(
        "spoofed",
        [
            "support@spam-sender.example",
            "security@evil.example.net",
            "noreply@google.com.attacker.example",
        ],
    )
    def test_substring_spoofing_is_rejected(self, rules, spoofed):
        # الفحص القديم ``'support@' in sender_email`` كان يمرّر كل هذه.
        assert not rules.is_trusted(spoofed)

    def test_invalid_address_is_not_trusted(self, rules):
        assert not rules.is_trusted("not-an-address")


class TestKeywordMatching:
    def test_matches_whole_word(self, rules):
        assert "newsletter" in rules.keyword_hits("Our Weekly Newsletter")

    @pytest.mark.parametrize(
        "subject",
        [
            "Job Offer from Acme",
            "Re: contract with sales@corp.example",
            "Let us finalize the deal today",
            "Wholesale pricing attached",
            "An exclusive interview",
        ],
    )
    def test_no_false_positive_on_business_mail(self, rules, subject):
        assert rules.keyword_hits(subject) == []

    def test_arabic_keyword(self, rules):
        assert rules.keyword_hits("عرض خاص لك اليوم")

    def test_empty_subject(self, rules):
        assert rules.keyword_hits("") == []


class TestServers:
    def test_known_domain(self, rules):
        assert rules.imap_server("a@gmail.com") == ("imap.gmail.com", 993)

    def test_unknown_domain_is_guessed(self, rules):
        assert rules.imap_server("a@custom.example") == ("imap.custom.example", 993)

    def test_smtp_lookup(self, rules):
        assert rules.smtp_server("a@gmail.com") == ("smtp.gmail.com", 587)


class TestUserOverrides:
    def test_user_file_merges_over_defaults(self, tmp_path):
        override = tmp_path / "rules.json"
        override.write_text(json.dumps({"thresholds": {"include": 1}}), encoding="utf-8")
        merged = Rules.load(user_file=override)
        assert merged.include_threshold == 1
        # المفاتيح غير المذكورة تبقى كما هي.
        assert merged.auto_select_threshold == 4

    def test_corrupt_user_file_falls_back_to_defaults(self, tmp_path):
        broken = tmp_path / "rules.json"
        broken.write_text("{not json", encoding="utf-8")
        assert Rules.load(user_file=broken).include_threshold == 3


def test_arabic_indic_digits_are_normalized():
    assert normalize_digits("٣٠") == "30"
    assert normalize_digits("۱۲۳") == "123"
    assert normalize_digits("30") == "30"


class TestTransactionalPenalty:
    def test_local_part_lowers_score_without_granting_trust(self, rules):
        assert not rules.is_trusted("security@mybank.example")
        assert rules.transactional_penalty("security@mybank.example") < 0

    def test_unknown_local_part_has_no_penalty(self, rules):
        assert rules.transactional_penalty("promo@shop.example") == 0

    def test_invalid_address_has_no_penalty(self, rules):
        assert rules.transactional_penalty("nonsense") == 0
