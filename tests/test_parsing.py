"""تحليل الترويسات: فك الترميز، استخراج المرسل، وروابط إلغاء الاشتراك."""

from __future__ import annotations

import email

import pytest
from conftest import load_eml

from email_cleaner.parsing import (
    decode_header_value,
    extract_sender,
    extract_unsubscribe,
    parse_mailto,
    safe_int,
)


class TestDecodeHeader:
    def test_plain_ascii(self):
        assert decode_header_value("Hello") == "Hello"

    def test_base64_arabic(self):
        assert decode_header_value("=?UTF-8?B?2LnYsdi2INiu2KfYtQ==?=") == "عرض خاص"

    def test_quoted_printable(self):
        assert decode_header_value("=?UTF-8?Q?Caf=C3=A9?=") == "Café"

    def test_empty_and_none(self):
        assert decode_header_value("") == ""
        assert decode_header_value(None) == ""

    def test_unknown_charset_does_not_raise(self):
        # الترميز غير المعروف كان يمرّ عبر ``except:`` عارٍ يبتلع كل شيء.
        result = decode_header_value("=?INVALID-CHARSET?B?2YXYqtis2LE=?=")
        assert isinstance(result, str)

    def test_malformed_fixture_survives(self):
        msg = load_eml("malformed_header.eml")
        assert isinstance(decode_header_value(msg.get("Subject")), str)


class TestExtractSender:
    @pytest.mark.parametrize(
        ("header", "expected_address"),
        [
            ("Ali <ali@example.com>", "ali@example.com"),
            ('"Ali, Sales" <ali@example.com>', "ali@example.com"),
            ("ali@example.com", "ali@example.com"),
            ("  ALI@Example.COM  ", "ali@example.com"),
            ("Weird <name> <ali@example.com>", "ali@example.com"),
        ],
    )
    def test_address_extraction(self, header, expected_address):
        _, address = extract_sender(header)
        assert address == expected_address

    def test_encoded_display_name(self):
        name, address = extract_sender("=?UTF-8?B?2LnYqNiv2KfZhNmD2LHZitmF?= <a@b.com>")
        assert name == "عبدالكريم"
        assert address == "a@b.com"

    def test_missing_header(self):
        assert extract_sender(None) == ("", "")
        assert extract_sender("") == ("", "")

    def test_name_falls_back_to_local_part(self):
        name, _ = extract_sender("ali@example.com")
        assert name == "ali"


class TestExtractUnsubscribe:
    def test_prefers_https_and_detects_mailto(self):
        msg = load_eml("newsletter.eml")
        link, mailto, one_click = extract_unsubscribe(msg)
        assert link == "https://noon.example/u?id=abc"
        assert mailto == "mailto:unsub@noon.example?subject=stop"
        assert one_click is True

    def test_one_click_requires_the_post_header(self):
        # وجود رابط وحده لا يعني دعم النقرة الواحدة (RFC 8058).
        msg = load_eml("github_notification.eml")
        _, _, one_click = extract_unsubscribe(msg)
        assert one_click is False

    def test_missing_header(self):
        msg = load_eml("personal.eml")
        assert extract_unsubscribe(msg) == (None, None, False)

    def test_garbage_header_yields_nothing(self):
        msg = load_eml("malformed_header.eml")
        link, mailto, one_click = extract_unsubscribe(msg)
        assert (link, mailto, one_click) == (None, None, False)

    def test_mailto_only(self):
        msg = email.message_from_string("List-Unsubscribe: <mailto:x@y.com>\n\nbody")
        link, mailto, one_click = extract_unsubscribe(msg)
        assert link is None
        assert mailto == "mailto:x@y.com"
        assert one_click is False

    def test_one_click_without_link_is_false(self):
        msg = email.message_from_string(
            "List-Unsubscribe: <mailto:x@y.com>\n"
            "List-Unsubscribe-Post: List-Unsubscribe=One-Click\n\nbody"
        )
        assert extract_unsubscribe(msg)[2] is False


class TestParseMailto:
    def test_full_uri(self):
        assert parse_mailto("mailto:u@x.com?subject=stop&body=please") == (
            "u@x.com",
            "stop",
            "please",
        )

    def test_defaults_when_params_absent(self):
        assert parse_mailto("mailto:u@x.com") == ("u@x.com", "unsubscribe", "unsubscribe")

    def test_percent_encoding(self):
        recipient, _, _ = parse_mailto("mailto:a%2Bb@x.com")
        assert recipient == "a+b@x.com"


class TestSafeInt:
    def test_valid(self):
        assert safe_int("30", minimum=1, maximum=365, label="الأيام") == 30

    def test_arabic_indic_digits(self):
        assert safe_int("٣٠", minimum=1, maximum=365, label="الأيام") == 30

    @pytest.mark.parametrize("bad", ["", "   ", "abc", "3.5", "30d"])
    def test_non_numeric_raises_arabic_message(self, bad):
        # كان ``int(spinbox.get())`` يرفع ValueError غير ملتقط داخل معالج الزر.
        with pytest.raises(ValueError, match="الأيام"):
            safe_int(bad, minimum=1, maximum=365, label="الأيام")

    @pytest.mark.parametrize("out_of_range", ["0", "366", "-5"])
    def test_range_is_enforced(self, out_of_range):
        with pytest.raises(ValueError, match="بين"):
            safe_int(out_of_range, minimum=1, maximum=365, label="الأيام")
