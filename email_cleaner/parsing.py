"""تحليل ترويسات الرسائل وتسجيل درجة الاشتباه.

كل ما في هذه الوحدة نقي (pure): يأخذ كائن ``email.message.Message`` ويرجّع
قيمًا، بلا شبكة ولا واجهة. لذلك يمكن اختباره كاملًا بملفات ``.eml`` ثابتة.
"""

from __future__ import annotations

import email.errors
from collections import Counter
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


MAILTO_BODY = "unsubscribe"
MAILTO_SUBJECT_MAX = 100


def parse_mailto(uri: str) -> tuple[str, str, str]:
    """يفكّك ``mailto:`` إلى (المستقبِل، الموضوع، النص) بعد تقييده.

    الترويسة يكتبها المرسل كما يشاء، والرسالة تخرج من حساب المستخدم. فبلا قيود
    يقدر المرسل أن يجعل حسابك يرسل نصًا يختاره إلى عناوين يختارها. لذلك:
    مستقبِل واحد صالح فقط (وإلا يُرجَع فارغًا)، والنص ثابت يتجاهل ``body``،
    والموضوع بلا أسطر جديدة ومقصوص - بعض القوائم تحتاجه لتمييز الاشتراك.
    """
    parsed = urlparse(uri)
    raw = unquote(parsed.path or "")
    recipient = raw.strip()
    if any(ch in raw for ch in ",;\r\n") or not _is_address(recipient):
        recipient = ""
    params = parse_qs(parsed.query or "")
    subject = (params.get("subject", [""])[0] or "unsubscribe").strip()
    subject = " ".join(subject.split())[:MAILTO_SUBJECT_MAX] or "unsubscribe"
    return recipient, subject, MAILTO_BODY


def _is_address(value: str) -> bool:
    try:
        split_address(value)
    except InvalidAddressError:
        return False
    return True


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
    """يضيف درجة للمرسلين المتكررين.

    يُطبَّق على *كل* الرسائل المفحوصة قبل عتبة الإدراج، وإلا لما رفعت هذه
    الإشارة أي رسالة إلى القائمة. المرسلون الموثوقون مستثنون من الزيادة.
    """
    weights = rules.scoring
    threshold = weights["frequent_sender_threshold"]
    counts = Counter(message.sender_email for message in messages)

    for message in messages:
        if counts[message.sender_email] >= threshold and not rules.is_trusted(message.sender_email):
            message.score += weights["frequent_sender"]
            message.reasons = (
                *message.reasons,
                f"مرسل متكرر ({counts[message.sender_email]} رسالة)",
            )


__all__ = [
    "InvalidAddressError",
    "apply_sender_frequency",
    "decode_header_value",
    "extract_sender",
    "extract_unsubscribe",
    "parse_mailto",
    "score_message",
    "split_address",
]
