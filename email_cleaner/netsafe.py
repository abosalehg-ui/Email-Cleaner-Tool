"""فحص سلامة روابط إلغاء الاشتراك قبل زيارتها.

روابط ``List-Unsubscribe`` يكتبها المرسل كما يشاء. زيارتها بلا فحص تعني أن
جهاز المستخدم - من داخل شبكته وخلف جدارها الناري - ينفّذ طلبًا لعنوان يختاره
طرف غير موثوق. هذه الوحدة تمنع ذلك.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# مضيفات تُرفض بالاسم قبل أي استعلام DNS.
BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)

MAX_REDIRECTS = 2


class UnsafeUrlError(ValueError):
    """رابط لا يجوز زيارته - الرسالة تشرح السبب للمستخدم."""


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """يقبل العناوين العامة فقط (قائمة بيضاء).

    القائمة السلبية السابقة (is_private, is_loopback...) كانت تمرّر نطاق CGNAT
    ``100.64.0.0/10``، وهو داخلي لدى بعض المزوّدين وفي شبكات مثل Tailscale.
    ``is_global`` يستبعده، والبث المتعدد يُستبعد صراحةً لأن is_global لا يغطيه.
    """
    return ip.is_global and not ip.is_multicast


def check_unsubscribe_url(url: str, *, resolve: bool = True) -> None:
    """يرفع ``UnsafeUrlError`` إذا كان الرابط غير صالح للزيارة الآلية.

    ``resolve=False`` يتخطى استعلام DNS (يُستعمل في الاختبارات فقط).

    ملاحظة: الفحص يتم قبل الطلب، فتبقى ثغرة نظرية باسم DNS rebinding إذا
    غيّر الخادم إجابته بين الفحص والاتصال. تقليصها الكامل يتطلب ربط الطلب
    بعنوان IP مُتحقَّق منه، وهو خارج نطاق هذه الأداة.
    """
    parsed = urlparse((url or "").strip())

    if parsed.scheme != "https":
        raise UnsafeUrlError(f"مخطط غير مسموح ({parsed.scheme or 'بلا مخطط'}) - HTTPS فقط")

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeUrlError("الرابط بلا مضيف")
    if host in BLOCKED_HOSTNAMES:
        raise UnsafeUrlError(f"مضيف محظور: {host}")

    # مضيف مكتوب كعنوان IP مباشرةً - افحصه بلا DNS.
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(literal):
            raise UnsafeUrlError(f"عنوان داخلي: {host}")
        return

    if not resolve:
        return

    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"تعذّر تحليل اسم المضيف {host}: {exc}") from exc

    if not infos:
        raise UnsafeUrlError(f"لا توجد عناوين للمضيف {host}")

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError as exc:
            raise UnsafeUrlError(f"عنوان غير مفهوم للمضيف {host}") from exc
        if not _is_public_ip(ip):
            raise UnsafeUrlError(f"المضيف {host} يشير إلى عنوان داخلي ({ip})")


def is_safe_unsubscribe_url(url: str, *, resolve: bool = True) -> bool:
    """نسخة منطقية من :func:`check_unsubscribe_url`."""
    try:
        check_unsubscribe_url(url, resolve=resolve)
    except UnsafeUrlError:
        return False
    return True


def describe_targets(urls: list[str]) -> list[str]:
    """أسماء النطاقات فقط - تُعرض على المستخدم قبل تنفيذ أي طلب."""
    seen: list[str] = []
    for url in urls:
        host = (urlparse(url).hostname or "؟").lower()
        if host not in seen:
            seen.append(host)
    return seen
