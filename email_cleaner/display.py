"""تنسيق البيانات للعرض: تواريخ مقروءة، بحث، ترتيب. بلا Tk، فتُختبر في أي بيئة."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .models import EmailMessage, UnsubscribeMethod

ARABIC_MONTHS = (
    "يناير",
    "فبراير",
    "مارس",
    "أبريل",
    "مايو",
    "يونيو",
    "يوليو",
    "أغسطس",
    "سبتمبر",
    "أكتوبر",
    "نوفمبر",
    "ديسمبر",
)

METHOD_LABELS = {
    UnsubscribeMethod.ONE_CLICK: "نقرة واحدة (RFC 8058)",
    UnsubscribeMethod.MAILTO: "رسالة بريدية",
    UnsubscribeMethod.MANUAL: "رابط يدوي — لن يُزار آليًا",
    UnsubscribeMethod.NONE: "لا توجد وسيلة إلغاء اشتراك",
}


def parse_date(raw: str) -> datetime | None:
    """يفسّر ترويسة ``Date`` بصيغة RFC 2822، أو ``None`` إن كانت تالفة."""
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    # التواريخ بلا منطقة زمنية تُعامل كتوقيت محلي كي تصح المقارنة.
    return parsed if parsed.tzinfo else parsed.astimezone()


def format_date(raw: str, now: datetime | None = None) -> str:
    """«اليوم 10:22» أو «13 أكتوبر» أو «13 أكتوبر 2025» حسب القرب."""
    parsed = parse_date(raw)
    if parsed is None:
        return (raw or "—")[:16]
    local = parsed.astimezone()
    now = now or datetime.now().astimezone()
    if local.date() == now.date():
        return f"اليوم {local:%H:%M}"
    label = f"{local.day} {ARABIC_MONTHS[local.month - 1]}"
    return label if local.year == now.year else f"{label} {local.year}"


def format_full_date(raw: str) -> str:
    """«13 أكتوبر 2026، 10:22» بالتوقيت المحلي - للوحة التفاصيل."""
    parsed = parse_date(raw)
    if parsed is None:
        return raw or "—"
    local = parsed.astimezone()
    return f"{local.day} {ARABIC_MONTHS[local.month - 1]} {local.year}، {local:%H:%M}"


def score_level(score: int, auto_select: int) -> str:
    if score >= auto_select + 2:
        return "عالية جدًا"
    if score >= auto_select:
        return "عالية"
    return "متوسطة"


def matches(message: EmailMessage, query: str) -> bool:
    """بحث غير حساس لحالة الأحرف في المرسل وعنوانه والموضوع."""
    query = query.strip().lower()
    if not query:
        return True
    haystack = f"{message.sender} {message.sender_email} {message.subject}".lower()
    return all(part in haystack for part in query.split())


_OLDEST = datetime.min.replace(tzinfo=timezone.utc)

SORT_KEYS = {
    "score": lambda m: m.score,
    "date": lambda m: parse_date(m.date) or _OLDEST,
    "subject": lambda m: m.subject.lower(),
    "sender": lambda m: m.sender_email,
}


def sort_messages(
    messages: Iterable[EmailMessage], column: str, descending: bool
) -> list[EmailMessage]:
    """ترتيب مستقر؛ عند التساوي تبقى الرسائل الأعلى درجة أولًا."""
    ordered = sorted(messages, key=lambda m: -m.score)
    return sorted(ordered, key=SORT_KEYS[column], reverse=descending)
