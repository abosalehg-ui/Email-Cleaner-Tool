"""أنواع البيانات المشتركة بين المحرك والواجهة."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class UnsubscribeMethod(str, Enum):
    """الطريقة التي ستُستخدم لإلغاء الاشتراك من مرسل معيّن."""

    ONE_CLICK = "one_click"  # RFC 8058 - طلب POST يعترف به الخادم رسميًا
    MAILTO = "mailto"  # رسالة بريدية - لا تكشف عنوان IP ولا تزور موقع المرسل
    MANUAL = "manual"  # يحتاج تدخل المستخدم؛ لا نزور الرابط نيابةً عنه
    NONE = "none"  # لا توجد وسيلة إلغاء اشتراك في الرسالة


class DeleteMode(str, Enum):
    """ماذا يحصل للرسائل المختارة."""

    MOVE = "move"  # نقل إلى مجلد مراجعة - قابل للتراجع (الافتراضي)
    PERMANENT = "permanent"  # حذف نهائي من الخادم - لا رجعة فيه


@dataclass
class EmailMessage:
    """رسالة مرشّحة لأنها دعائية.

    الحقل ``uid`` يحمل معرّف IMAP الثابت (UID) وليس الرقم التسلسلي.
    الفرق جوهري: الأرقام التسلسلية تنزاح كلما حُذفت رسالة من المجلد،
    فالاعتماد عليها بين الفحص والحذف قد يطال رسائل لم تُفحص أصلًا.
    """

    uid: str
    subject: str
    sender: str
    sender_email: str
    date: str
    score: int = 0
    reasons: tuple[str, ...] = ()
    unsubscribe_link: str | None = None
    unsubscribe_mailto: str | None = None
    one_click: bool = False

    @property
    def method(self) -> UnsubscribeMethod:
        """أفضل طريقة متاحة لإلغاء الاشتراك، مرتّبة من الأأمن إلى الأقل."""
        if self.one_click and self.unsubscribe_link:
            return UnsubscribeMethod.ONE_CLICK
        if self.unsubscribe_mailto:
            return UnsubscribeMethod.MAILTO
        if self.unsubscribe_link:
            return UnsubscribeMethod.MANUAL
        return UnsubscribeMethod.NONE


@dataclass
class UnsubscribeOutcome:
    """نتيجة محاولة إلغاء اشتراك واحدة - بلا ادعاء نجاح غير مثبت."""

    sender_email: str
    method: UnsubscribeMethod
    ok: bool
    detail: str
    link: str | None = None


@dataclass
class ScanReport:
    """حصيلة عملية فحص كاملة."""

    messages: list[EmailMessage] = field(default_factory=list)
    examined: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
