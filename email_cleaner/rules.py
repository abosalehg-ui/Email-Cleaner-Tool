"""قواعد التصنيف وخوادم البريد - بيانات قابلة للتخصيص، لا كود.

القواعد الافتراضية مرفقة مع الحزمة في ``rules.json``. أي ملف يضعه المستخدم في
``~/.email_cleaner/rules.json`` يُدمج فوقها، فيبقى تخصيصه سليمًا بعد تحديث الأداة.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_RULES_FILE = Path(__file__).with_name("rules.json")
USER_RULES_FILE = Path.home() / ".email_cleaner" / "rules.json"

_ADDRESS_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


class InvalidAddressError(ValueError):
    """عنوان بريد لا يمكن تفسيره - يُرفع بدل انهيار ``split('@')[1]``."""


def normalize_digits(value: str) -> str:
    """يحوّل الأرقام الهندية (٣٠) إلى عربية غربية (30) قبل ``int()``."""
    return value.translate(_ARABIC_INDIC)


def split_address(address: str) -> tuple[str, str]:
    """يفصل البريد إلى (الجزء المحلي، النطاق) أو يرفع ``InvalidAddressError``."""
    address = (address or "").strip()
    if not _ADDRESS_RE.match(address):
        raise InvalidAddressError(f"صيغة البريد غير صحيحة: {address!r}")
    local, _, domain = address.rpartition("@")
    return local.lower(), domain.lower()


def _deep_merge(base: dict, override: dict) -> dict:
    """دمج غير مدمّر: القوائم تُستبدل، والقواميس تُدمج مفتاحًا مفتاحًا."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@dataclass
class Rules:
    """قواعد التصنيف المُحمّلة، مع تعابير منتظمة مُجمّعة مسبقًا."""

    data: dict
    _keyword_patterns: list[tuple[str, re.Pattern[str]]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        # حدود الكلمات (\b) تمنع مطابقة "sale" داخل "sales@" أو "wholesale".
        self._keyword_patterns = [
            (kw, re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)", re.IGNORECASE | re.UNICODE))
            for kw in self.data.get("promotional_keywords", [])
        ]

    # ------------------------------------------------------------------ تحميل

    @classmethod
    def load(cls, user_file: Path | None = USER_RULES_FILE) -> Rules:
        """يحمّل الافتراضيات ثم يدمج فوقها ملف المستخدم إن وُجد وكان سليمًا."""
        data = json.loads(DEFAULT_RULES_FILE.read_text(encoding="utf-8"))
        if user_file and user_file.is_file():
            # ملف مستخدم تالف يجب ألا يمنع تشغيل الأداة؛ نكتفي بالافتراضيات.
            with contextlib.suppress(json.JSONDecodeError, OSError):
                data = _deep_merge(data, json.loads(user_file.read_text(encoding="utf-8")))
        return cls(data)

    # ------------------------------------------------------------------ خوادم

    def imap_server(self, address: str) -> tuple[str, int]:
        """خادم IMAP للنطاق، أو تخمين ``imap.<domain>`` للنطاقات غير المعروفة."""
        _, domain = split_address(address)
        host, port = self.data["imap_servers"].get(domain, [f"imap.{domain}", 993])
        return host, int(port)

    def smtp_server(self, address: str) -> tuple[str, int]:
        """خادم SMTP للنطاق - يُستعمل فقط لإلغاء الاشتراك عبر ``mailto:``."""
        _, domain = split_address(address)
        host, port = self.data["smtp_servers"].get(domain, [f"smtp.{domain}", 587])
        return host, int(port)

    # ------------------------------------------------------------------ تصنيف

    def is_trusted(self, sender_email: str) -> bool:
        """إعفاء كامل - مبني على مطابقة النطاق بدقة، لا على احتواء نص.

        الفحص القديم كان ``'support@' in sender_email`` فكان
        ``support@spam-sender.com`` يمرّ كمرسل موثوق.

        الجزء المحلي وحده لا يمنح ثقة: أي مرسل يقدر أن يسمي نفسه
        ``security@`` أو ``noreply@``. يُعالَج بخفض الدرجة لا بالإعفاء،
        عبر :meth:`transactional_penalty`.
        """
        try:
            _, domain = split_address(sender_email)
        except InvalidAddressError:
            return False
        return domain in set(self.data.get("trusted_domains", []))

    def transactional_penalty(self, sender_email: str) -> int:
        """خفض للدرجة حين يشير الجزء المحلي إلى بريد معاملات لا دعاية.

        قيمة سالبة (أو صفر إن لم ينطبق). تكفي لإخراج إشعار أمني أو فاتورة
        من القائمة، ولا تكفي لتبييض رسالة دعائية صريحة من نفس العنوان.
        """
        try:
            local, _ = split_address(sender_email)
        except InvalidAddressError:
            return 0
        if local in set(self.data.get("trusted_local_parts", [])):
            return int(self.scoring.get("transactional_local_part", 0))
        return 0

    def keyword_hits(self, subject: str) -> list[str]:
        """الكلمات الدعائية الموجودة في الموضوع.

        الموضوع فقط - دمج الموضوع مع عنوان المرسل كان يجعل أي بريد من
        ``sales@`` يُصنَّف دعائيًا.
        """
        if not subject:
            return []
        return [kw for kw, pattern in self._keyword_patterns if pattern.search(subject)]

    # ------------------------------------------------------------------ عتبات

    @property
    def scoring(self) -> dict[str, int]:
        return self.data["scoring"]

    @property
    def include_threshold(self) -> int:
        """أدنى درجة تجعل الرسالة تظهر في القائمة."""
        return int(self.data["thresholds"]["include"])

    @property
    def auto_select_threshold(self) -> int:
        """أدنى درجة تجعل الرسالة مُحدَّدة تلقائيًا للحذف."""
        return int(self.data["thresholds"]["auto_select"])
