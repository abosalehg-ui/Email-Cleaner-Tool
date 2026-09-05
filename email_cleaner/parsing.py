"""تحليل ترويسات الرسائل وتسجيل درجة الاشتباه.

كل ما في هذه الوحدة نقي (pure): يأخذ كائن ``email.message.Message`` ويرجّع
قيمًا، بلا شبكة ولا واجهة. لذلك يمكن اختباره كاملًا بملفات ``.eml`` ثابتة.
"""

from __future__ import annotations

import email.errors
from email.header import decode_header
from email.message import Message
from urllib.parse import parse_qs, unquote, urlparse

from .rules import InvalidAddressError, Rules, split_address

_DECODE_ERRORS = (
    UnicodeDecodeError,
    LookupError,
    ValueError,
    TypeError,
    email.errors.HeaderParseError,
)


def decode_header_value(value: str | None) -> str:
    """يفكّ ترميز ترويسة مثل ``=?UTF-8?B?...?=`` ويعيدها نصًا مقروءًا."""
    if not value:
        return ""
    try:
        parts = decode_header(value)
    except _DECODE_ERRORS:
        return str(value)

    chunks: list[str] = []
    for part, encoding in parts:
        if isinstance(part, bytes):
            try:
                chunks.append(part.decode(encoding or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                # ترميز غير معروف للنظام - نعود إلى utf-8 مع استبدال التالف.
                chunks.append(part.decode("utf-8", errors="replace"))
        else:
            chunks.append(part)
    return " ".join(chunk.strip() for chunk in chunks if chunk).strip()


def extract_sender(from_header: str | None) -> tuple[str, str]:
    """يرجّع (الاسم المعروض، عنوان البريد بحروف صغيرة)."""
    raw = (from_header or "").strip()
    if not raw:
        return "", ""

    start, end = raw.rfind("<"), raw.rfind(">")
    if start != -1 and end > start:
        address = raw[start + 1 : end].strip()
        name = raw[:start].strip().strip("\"'").strip()
    else:
        address = raw
        name = ""

    address = address.lower()
    if not name:
        name = address.split("@")[0] if "@" in address else address
    return decode_header_value(name), address


def extract_unsubscribe(msg: Message) -> tuple[str | None, str | None, bool]:
    """يستخرج (رابط https، عنوان mailto، هل يدعم النقرة الواحدة).

    ``mailto`` مفضّل على ``https`` لأنه لا يكشف عنوان IP للمستخدم ولا يزور
    خادمًا يتحكم به المرسل. ودعم النقرة الواحدة يُعلن عنه الخادم صراحةً عبر
    ترويسة ``List-Unsubscribe-Post`` وفق RFC 8058.
    """
    header = msg.get("List-Unsubscribe", "") or ""

    link: str | None = None
    mailto: str | None = None
    for candidate in header.split(","):
        candidate = candidate.strip().strip("<>").strip()
        lowered = candidate.lower()
        if lowered.startswith("https://") and link is None:
            link = candidate
        elif lowered.startswith("http://") and link is None:
            # نحتفظ به ليراه المستخدم، لكن netsafe سيمنع زيارته آليًا.
            link = candidate
        elif lowered.startswith("mailto:") and mailto is None:
            mailto = candidate

    post_header = (msg.get("List-Unsubscribe-Post", "") or "").strip().lower()
    one_click = post_header.replace(" ", "") == "list-unsubscribe=one-click"
    return link, mailto, bool(one_click and link)


def parse_mailto(uri: str) -> tuple[str, str, str]:
    """يفكّك ``mailto:`` إلى (المستقبِل، الموضوع، النص) وفق RFC 2368."""
    parsed = urlparse(uri)
    recipient = unquote(parsed.path or "").strip()
    params = parse_qs(parsed.query or "")
    subject = (params.get("subject", ["unsubscribe"])[0] or "unsubscribe").strip()
    body = (params.get("body", ["unsubscribe"])[0] or "unsubscribe").strip()
    return recipient, subject, body


def score_message(
    msg: Message, subject: str, sender_email: str, rules: Rules
) -> tuple[int, tuple[str, ...]]:
    """درجة اشتباه من 0 فما فوق، مع أسباب مقروءة تُعرض للمستخدم.

    القرار الثنائي القديم كان يعتبر كل رسالة تحمل ``List-Unsubscribe``
    دعائيةً - وهي ترويسة تضعها إشعارات GitHub وكشوف البنوك وقوائم العمل.
    الدرجة تفصل بين «فيها وسيلة إلغاء اشتراك» و«هذه رسالة دعائية».
    """
    if rules.is_trusted(sender_email):
        return 0, ("مرسل موثوق",)

    weights = rules.scoring
    score = 0
    reasons: list[str] = []

    if msg.get("List-Unsubscribe"):
        score += weights["list_unsubscribe"]
        reasons.append("ترويسة إلغاء اشتراك")

    precedence = (msg.get("Precedence", "") or "").strip().lower()
    if precedence in ("bulk", "list", "junk"):
        score += weights["bulk_precedence"]
        reasons.append(f"Precedence: {precedence}")

    hits = rules.keyword_hits(subject)
    if hits:
        score += weights["keyword"]
        reasons.append("كلمات: " + "، ".join(hits[:3]))

    penalty = rules.transactional_penalty(sender_email)
    if penalty:
        score += penalty
        reasons.append("يشبه بريد معاملات")

    return max(score, 0), tuple(reasons)


def apply_sender_frequency(messages, rules: Rules) -> None:
    """يضيف درجة للمرسلين المتكررين - يُطبَّق بعد اكتمال الفحص."""
    weights = rules.scoring
    threshold = weights["frequent_sender_threshold"]
    counts: dict[str, int] = {}
    for message in messages:
        counts[message.sender_email] = counts.get(message.sender_email, 0) + 1

    for message in messages:
        if counts[message.sender_email] >= threshold:
            message.score += weights["frequent_sender"]
            message.reasons = (
                *message.reasons,
                f"مرسل متكرر ({counts[message.sender_email]} رسالة)",
            )


def safe_int(value: str, *, minimum: int, maximum: int, label: str) -> int:
    """يحوّل مدخل المستخدم إلى عدد ضمن المدى، أو يرفع ``ValueError`` بالعربية."""
    from .rules import normalize_digits

    try:
        number = int(normalize_digits((value or "").strip()))
    except ValueError:
        raise ValueError(f"{label}: أدخل رقمًا صحيحًا") from None
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}: يجب أن يكون بين {minimum} و{maximum}")
    return number


__all__ = [
    "InvalidAddressError",
    "apply_sender_frequency",
    "decode_header_value",
    "extract_sender",
    "extract_unsubscribe",
    "parse_mailto",
    "safe_int",
    "score_message",
    "split_address",
]
