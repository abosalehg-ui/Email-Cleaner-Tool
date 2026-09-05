"""التصنيف بالدرجات - أهم اختبار في المشروع.

كل إيجابية كاذبة هنا كانت تعني رسالة مهمة تُحذف نهائيًا في النسخة السابقة.
"""

from __future__ import annotations

import pytest
from conftest import load_eml

from email_cleaner.models import EmailMessage, UnsubscribeMethod
from email_cleaner.parsing import (
    apply_sender_frequency,
    decode_header_value,
    extract_sender,
    score_message,
)


def score_fixture(name: str, rules) -> int:
    msg = load_eml(name)
    subject = decode_header_value(msg.get("Subject"))
    _, sender = extract_sender(msg.get("From"))
    return score_message(msg, subject, sender, rules)[0]


class TestPromotionalIsCaught:
    @pytest.mark.parametrize("fixture", ["newsletter.eml", "arabic_promo.eml"])
    def test_reaches_auto_select_threshold(self, fixture, rules):
        assert score_fixture(fixture, rules) >= rules.auto_select_threshold


class TestLegitimateMailIsSpared:
    @pytest.mark.parametrize(
        "fixture",
        [
            "github_notification.eml",  # فيها List-Unsubscribe لكنها ليست دعاية
            "bank_statement.eml",  # كشف حساب من statements@
            "job_offer.eml",  # كلمة "offer" في الموضوع
            "sales_colleague.eml",  # مرسل sales@
            "personal.eml",  # رسالة شخصية عادية
        ],
    )
    def test_stays_below_inclusion_threshold(self, fixture, rules):
        # القاعدة القديمة ``if msg.get('List-Unsubscribe'): return True`` كانت
        # تصنّف الثلاث الأولى كدعاية جاهزة للحذف.
        assert score_fixture(fixture, rules) < rules.include_threshold

    def test_list_unsubscribe_alone_is_not_enough(self, rules):
        assert score_fixture("github_notification.eml", rules) == 2


class TestScoringDetails:
    def test_reasons_are_human_readable(self, rules):
        msg = load_eml("newsletter.eml")
        subject = decode_header_value(msg.get("Subject"))
        _, sender = extract_sender(msg.get("From"))
        score, reasons = score_message(msg, subject, sender, rules)
        assert score >= 4
        assert any("إلغاء اشتراك" in reason for reason in reasons)
        assert any("Precedence" in reason for reason in reasons)

    def test_trusted_domain_short_circuits(self, rules):
        msg = load_eml("newsletter.eml")
        score, reasons = score_message(msg, "Sale! discount!", "x@google.com", rules)
        assert score == 0
        assert reasons == ("مرسل موثوق",)

    def test_score_never_goes_negative(self, rules):
        msg = load_eml("personal.eml")
        assert score_message(msg, "hello", "billing@shop.example", rules)[0] == 0


class TestSenderFrequency:
    def _messages(self, count: int, sender: str) -> list[EmailMessage]:
        return [
            EmailMessage(
                uid=str(index),
                subject="s",
                sender="n",
                sender_email=sender,
                date="d",
                score=3,
            )
            for index in range(count)
        ]

    def test_frequent_sender_gains_a_point(self, rules):
        messages = self._messages(6, "spam@shop.example")
        apply_sender_frequency(messages, rules)
        assert all(message.score == 4 for message in messages)
        assert all("مرسل متكرر" in " ".join(m.reasons) for m in messages)

    def test_rare_sender_is_unchanged(self, rules):
        messages = self._messages(2, "rare@shop.example")
        apply_sender_frequency(messages, rules)
        assert all(message.score == 3 for message in messages)


class TestUnsubscribeMethodSelection:
    def test_one_click_wins(self):
        message = EmailMessage(
            uid="1",
            subject="s",
            sender="n",
            sender_email="a@b.com",
            date="d",
            unsubscribe_link="https://x.com/u",
            unsubscribe_mailto="mailto:u@x.com",
            one_click=True,
        )
        assert message.method is UnsubscribeMethod.ONE_CLICK

    def test_mailto_preferred_over_bare_link(self):
        # mailto لا يكشف عنوان IP ولا يزور موقعًا يتحكم به المرسل.
        message = EmailMessage(
            uid="1",
            subject="s",
            sender="n",
            sender_email="a@b.com",
            date="d",
            unsubscribe_link="https://x.com/u",
            unsubscribe_mailto="mailto:u@x.com",
        )
        assert message.method is UnsubscribeMethod.MAILTO

    def test_bare_link_is_manual(self):
        message = EmailMessage(
            uid="1",
            subject="s",
            sender="n",
            sender_email="a@b.com",
            date="d",
            unsubscribe_link="https://x.com/u",
        )
        assert message.method is UnsubscribeMethod.MANUAL

    def test_nothing_available(self):
        message = EmailMessage(uid="1", subject="s", sender="n", sender_email="a@b.com", date="d")
        assert message.method is UnsubscribeMethod.NONE
