"""فحص سلامة روابط إلغاء الاشتراك قبل زيارتها."""

from __future__ import annotations

import pytest

from email_cleaner.netsafe import (
    UnsafeUrlError,
    check_unsubscribe_url,
    describe_targets,
    is_safe_unsubscribe_url,
)


class TestRejectedUrls:
    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/unsub",  # غير مشفّر - الرابط يحمل معرّف المستلم
            "ftp://example.com/unsub",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "",
            "https://",
        ],
    )
    def test_scheme_and_shape(self, url):
        assert not is_safe_unsubscribe_url(url, resolve=False)

    @pytest.mark.parametrize(
        "url",
        [
            "https://127.0.0.1/admin",
            "https://localhost/x",
            "https://192.168.1.1/setup.cgi?action=reboot",  # واجهة الراوتر المنزلي
            "https://10.0.0.5/internal",
            "https://172.16.0.1/x",
            "https://169.254.169.254/latest/meta-data/",  # بيانات وصفية سحابية
            "https://0.0.0.0/x",
            "https://[::1]/x",
        ],
    )
    def test_internal_addresses_are_blocked(self, url):
        # بلا هذا الفحص يصير جهاز المستخدم هو من ينفّذ الطلب داخل شبكته.
        assert not is_safe_unsubscribe_url(url, resolve=False)

    def test_error_message_explains_why(self):
        with pytest.raises(UnsafeUrlError, match="HTTPS"):
            check_unsubscribe_url("http://example.com/u", resolve=False)


class TestAcceptedUrls:
    @pytest.mark.parametrize(
        "url",
        [
            "https://mailchimp.example/unsub?id=1",
            "https://sub.domain.example:8443/u",
            "https://8.8.8.8/u",
        ],
    )
    def test_public_https_passes(self, url):
        assert is_safe_unsubscribe_url(url, resolve=False)


class TestDescribeTargets:
    def test_lists_unique_hosts_in_order(self):
        urls = [
            "https://a.example/u1",
            "https://a.example/u2",
            "https://b.example/u",
        ]
        assert describe_targets(urls) == ["a.example", "b.example"]

    def test_handles_garbage(self):
        assert describe_targets(["not a url"]) == ["؟"]
