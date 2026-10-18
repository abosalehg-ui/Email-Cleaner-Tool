"""محرك IMAP: اتصال، فحص، نقل/حذف، إلغاء اشتراك، تصدير.

المحرك لا يعرف شيئًا عن Tkinter؛ يتواصل مع أي واجهة عبر ``callback(نص، نسبة)``.
"""

from __future__ import annotations

import contextlib
import email
import imaplib
import json
import re
import smtplib
import ssl
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage as StdEmailMessage
from pathlib import Path

from .models import (
    DeleteMode,
    EmailMessage,
    ScanReport,
    UnsubscribeMethod,
    UnsubscribeOutcome,
)
from .netsafe import MAX_REDIRECTS, UnsafeUrlError, check_unsubscribe_url
from .parsing import (
    apply_sender_frequency,
    decode_header_value,
    extract_sender,
    extract_unsubscribe,
    parse_mailto,
    score_message,
)
from .rules import InvalidAddressError, Rules

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:  # pragma: no cover - يعتمد على بيئة التثبيت
    requests = None
    REQUESTS_AVAILABLE = False

ProgressCallback = Callable[[str, int], None]

# الترويسات الوحيدة التي يحتاجها التصنيف. جلبها بدل الرسالة كاملة يقلّص النقل
# بأكثر من 95%، و«PEEK» تمنع تعليم الرسائل كمقروءة أثناء الفحص.
HEADER_FIELDS = "SUBJECT FROM DATE LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST PRECEDENCE"
FETCH_SPEC = f"(UID BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])"
# رسالة لكل طلب تعني رحلة ذهاب وإياب لكل رسالة؛ الدفعات تختصر 500 رحلة إلى 5.
FETCH_CHUNK = 100
_UID_RE = re.compile(rb"UID (\d+)")

REVIEW_FOLDER_PARTS = ("Email Cleaner", "Review")
CONNECT_TIMEOUT = 15
HTTP_TIMEOUT = 10
SMTP_TIMEOUT = 20
UID_CHUNK = 100
UNSUB_DELAY = 0.5

USER_AGENT = "EmailCleaner/2.0 (+https://github.com/abosalehg-ui/Email-Cleaner-Tool)"


def _translate_login_error(error: Exception) -> str:
    """يحوّل رسائل imaplib الخام إلى عربية مفهومة."""
    text = str(error).lower()
    if "authenticationfailed" in text or "invalid credentials" in text:
        return (
            "البريد أو كلمة المرور غير صحيحة.\n"
            "لـ Gmail وYahoo استخدم «كلمة مرور التطبيق» لا كلمة مرور الحساب."
        )
    if "imap" in text and ("disabled" in text or "not enabled" in text):
        return "خدمة IMAP غير مفعّلة في إعدادات بريدك. فعّلها ثم أعد المحاولة."
    if "too many" in text or "rate" in text:
        return "الخادم رفض المحاولة مؤقتًا لكثرة الطلبات. انتظر قليلًا ثم أعد المحاولة."
    return f"تعذّر تسجيل الدخول: {error}"


def _close_quietly(connection: imaplib.IMAP4 | None) -> None:
    """يغلق مقبس اتصال فاشل بلا LOGOUT - الجلسة لم تكتمل أصلًا."""
    if connection is not None:
        with contextlib.suppress(OSError, imaplib.IMAP4.error):
            connection.shutdown()


def _chunks(items: Sequence[bytes], size: int) -> list[list[bytes]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _quote_mailbox(name: str) -> str:
    """يقتبس اسم المجلد ليصلح كوسيط IMAP."""
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_fetch(payload: list) -> dict[bytes, bytes]:
    """يربط كل جسم ترويسات بـ UID الخاص به في استجابة FETCH متعددة.

    أغلب الخوادم تضع ``UID n`` قبل الجسم، وبعضها بعده في الجزء الختامي
    (``b' UID 101)'``)، فنبحث في الموضعين.
    """
    result: dict[bytes, bytes] = {}
    for index, item in enumerate(payload):
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        match = _UID_RE.search(item[0])
        if match is None and index + 1 < len(payload) and isinstance(payload[index + 1], bytes):
            match = _UID_RE.search(payload[index + 1])
        if match is not None:
            result[match.group(1)] = item[1]
    return result


@dataclass(frozen=True)
class _Strategy:
    """كيف تُنفَّذ المعالجة على هذا الخادم - تُحسب مرة لكل عملية."""

    mode: DeleteMode
    folder: str
    move: bool
    uidplus: bool


class EmailCleanerCore:
    """كل منطق البريد. آمن للاستدعاء من خيوط متعددة عبر قفل داخلي."""

    def __init__(self, rules: Rules | None = None) -> None:
        self.rules = rules or Rules.load()
        self.connection: imaplib.IMAP4_SSL | None = None
        self.account: str = ""
        self.messages: list[EmailMessage] = []
        self.last_report = ScanReport()
        self.unsubscribe_results: list[UnsubscribeOutcome] = []
        self._lock = threading.RLock()
        self._delimiter: str | None = None
        self._caps: set[str] | None = None

    # ------------------------------------------------------------- الاتصال

    @property
    def is_connected(self) -> bool:
        return self.connection is not None

    def connect(self, address: str, password: str) -> tuple[bool, str]:
        """يتصل بخادم IMAP. كلمة المرور تُستعمل ولا تُخزَّن في أي مكان."""
        try:
            host, port = self.rules.imap_server(address)
        except InvalidAddressError as exc:
            return False, str(exc)

        connection: imaplib.IMAP4_SSL | None = None
        try:
            with self._lock:
                # بلا ssl_context صريح يستخدم imaplib سياقًا لا يتحقق من الشهادة ولا
                # من اسم المضيف، فيقبل أي شهادة مزيّفة وتُسلَّم كلمة المرور لمن انتحلها.
                # وبلا مهلة كان الاتصال بنطاق مجهول يعلّق دقيقتين بلا أي مؤشر.
                connection = imaplib.IMAP4_SSL(
                    host, port, ssl_context=ssl.create_default_context(), timeout=CONNECT_TIMEOUT
                )
                connection.login(address, password)
                self.connection = connection
                self.account = address
                self._delimiter = None
                self._caps = None
            return True, f"تم الاتصال بـ {host} ✅"
        except imaplib.IMAP4.error as exc:
            message = _translate_login_error(exc)
        except UnicodeEncodeError:
            # imaplib يرسل الأوامر بترميز ASCII، فأي حرف عربي في كلمة المرور يكسرها.
            message = (
                "كلمة المرور تحوي أحرفًا غير لاتينية، وبروتوكول IMAP لا يقبلها.\n"
                "كلمات مرور التطبيقات أحرف إنجليزية فقط — تأكد من لغة لوحة المفاتيح."
            )
        except ssl.SSLCertVerificationError as exc:
            message = (
                f"شهادة الخادم {host} غير موثوقة ({exc.verify_message}).\n"
                "قد تكون الشبكة تعترض الاتصال؛ جرّب شبكة أخرى ولا تُدخل كلمة المرور هنا."
            )
        except TimeoutError:
            message = f"انتهت مهلة الاتصال بـ {host} بعد {CONNECT_TIMEOUT} ثانية."
        except OSError as exc:
            message = f"تعذّر الوصول إلى {host}: {exc}"
        _close_quietly(connection)
        return False, message

    def disconnect(self) -> None:
        with self._lock:
            connection, self.connection = self.connection, None
            self.account = ""
        if connection is None:
            return
        # الجلسة قد تكون سقطت أصلًا من طرف الخادم؛ لا شيء يُنقذ هنا.
        with contextlib.suppress(imaplib.IMAP4.error, OSError):
            connection.logout()

    # ------------------------------------------------------------- الفحص

    def scan_inbox(
        self,
        days_back: int = 30,
        limit: int = 500,
        callback: ProgressCallback | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ScanReport:
        """يفحص INBOX ويرجّع تقريرًا يفصل ما نجح عمّا فشل.

        ``should_cancel`` يُستشار قبل كل رسالة؛ الإلغاء يُبقي ما فُحص حتى لحظته.
        """
        report = ScanReport()
        self.messages = []
        self.last_report = report

        connection = self.connection
        if connection is None:
            report.errors.append("غير متصل")
            return report

        def notify(text: str, percent: int) -> None:
            if callback:
                callback(text, percent)

        try:
            with self._lock:
                connection.select("INBOX")
                since = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
                typ, data = connection.uid("SEARCH", None, f'(SINCE "{since}")')
            if typ != "OK":
                report.errors.append(f"فشل البحث في الصندوق: {typ}")
                return report
        except (imaplib.IMAP4.error, OSError) as exc:
            report.errors.append(f"تعذّر فتح صندوق الوارد: {exc}")
            return report

        uids = data[0].split() if data and data[0] else []
        uids = uids[-limit:]
        total = len(uids)
        report.examined = total

        if total == 0:
            notify("لا توجد رسائل في المدة المحددة", 100)
            return report

        notify(f"جاري فحص {total} رسالة...", 0)

        # تُصنَّف كل الرسائل أولًا ثم يُطبَّق عدّ المرسلين ثم العتبة. الفلترة قبل
        # العدّ كانت تُسقط رسائل المرسل المتكرر قبل أن تُحسب له، فلا ترفعه أبدًا.
        candidates: list[EmailMessage] = []
        done = 0
        for chunk in _chunks(uids, FETCH_CHUNK):
            if should_cancel and should_cancel():
                report.cancelled = True
                break
            for uid, outcome in self._fetch_chunk(chunk):
                if isinstance(outcome, EmailMessage):
                    candidates.append(outcome)
                elif outcome is not None:
                    report.skipped += 1
                    if len(report.errors) < 5:
                        report.errors.append(f"UID {uid.decode(errors='replace')}: {outcome}")
            done += len(chunk)
            suspected = sum(1 for m in candidates if m.score >= self.rules.include_threshold)
            notify(f"تم فحص {done}/{total} رسالة ({suspected} مشتبهة)", int(done / total * 100))

        report.examined = done
        apply_sender_frequency(candidates, self.rules)
        self.messages = [m for m in candidates if m.score >= self.rules.include_threshold]
        self.messages.sort(key=lambda m: (-m.score, m.sender_email))
        report.messages = self.messages

        verb = "أُلغي الفحص" if report.cancelled else "اكتمل الفحص"
        summary = f"{verb}: {len(self.messages)} رسالة مشتبهة من {report.examined}"
        if report.skipped:
            summary += f" (تعذّر تحليل {report.skipped})"
        notify(summary, 100)
        return report

    def _fetch_chunk(self, uids: list[bytes]) -> list[tuple[bytes, EmailMessage | str | None]]:
        """يجلب ترويسات دفعة في طلب واحد؛ إن فشل الطلب كله يعيد المحاولة فرادى.

        لكل UID: رسالة مصنَّفة، أو نص خطأ، أو ``None`` إن لم يرجع الخادم شيئًا
        (رسالة حُذفت بين البحث والجلب).
        """
        try:
            headers = self._fetch_headers(uids)
        except (imaplib.IMAP4.error, OSError) as exc:
            if len(uids) == 1:
                return [(uids[0], str(exc))]
            # الدفعة فشلت ككل - نعيد المحاولة فرادى لنعرف أي رسالة تحديدًا.
            return [result for uid in uids for result in self._fetch_chunk([uid])]
        return [
            (uid, self._classify(uid, headers[uid]) if uid in headers else None) for uid in uids
        ]

    def _fetch_headers(self, uids: list[bytes]) -> dict[bytes, bytes]:
        with self._lock:
            connection = self.connection
            if connection is None:
                raise imaplib.IMAP4.error("انقطع الاتصال أثناء الفحص")
            typ, payload = connection.uid("FETCH", b",".join(uids), FETCH_SPEC)
        if typ != "OK":
            raise imaplib.IMAP4.error(f"رفض الخادم الجلب ({typ})")
        return _parse_fetch(payload or [])

    def _classify(self, uid: bytes, raw: bytes) -> EmailMessage:
        """يصنّف ترويسات رسالة واحدة. لا يطبّق العتبة - ذلك بعد عدّ المرسلين."""
        msg = email.message_from_bytes(raw)
        subject = decode_header_value(msg.get("Subject"))
        sender_name, sender_email = extract_sender(msg.get("From"))
        score, reasons = score_message(msg, subject, sender_email, self.rules)
        link, mailto, one_click = extract_unsubscribe(msg)
        return EmailMessage(
            uid=uid.decode(),
            subject=subject[:120] if subject else "(بدون عنوان)",
            sender=sender_name,
            sender_email=sender_email,
            date=decode_header_value(msg.get("Date")),
            score=score,
            reasons=reasons,
            unsubscribe_link=link,
            unsubscribe_mailto=mailto,
            one_click=one_click,
        )

    # ------------------------------------------------------- النقل والحذف

    def _hierarchy_delimiter(self) -> str:
        """فاصل المجلدات لدى الخادم ('/' أو '.')، يُطلب مرة واحدة لكل اتصال."""
        if self._delimiter is not None:
            return self._delimiter
        delimiter = "/"
        try:
            with self._lock:
                typ, data = self.connection.list('""', '""')
            if typ == "OK" and data and data[0]:
                match = re.search(rb"\(.*?\)\s+\"([^\"]*)\"", data[0])
                if match and match.group(1):
                    delimiter = match.group(1).decode(errors="replace")
        except (imaplib.IMAP4.error, OSError, AttributeError):
            return delimiter  # لا نحفظ الافتراضي إن فشل الطلب؛ نعيد المحاولة لاحقًا
        self._delimiter = delimiter
        return delimiter

    def review_folder_name(self) -> str:
        return self._hierarchy_delimiter().join(REVIEW_FOLDER_PARTS)

    def review_folder_label(self) -> str:
        """اسم المجلد للعرض فقط - بلا أي طلب شبكة، فيصلح لخيط الواجهة."""
        return (self._delimiter or "/").join(REVIEW_FOLDER_PARTS)

    def _capabilities(self) -> set[str]:
        """قدرات الخادم *بعد* الدخول، تُطلب مرة لكل اتصال.

        ``imaplib`` يحفظ قدرات ما قبل الدخول فقط، وGmail مثلًا لا يعلن MOVE
        وUIDPLUS إلا بعد المصادقة - فالاعتماد عليها يُسقطنا إلى EXPUNGE العام.
        """
        if self._caps is None:
            caps: set[str] = set()
            try:
                with self._lock:
                    typ, data = self.connection.capability()
                if typ == "OK" and data and data[0]:
                    caps = {cap.upper() for cap in data[0].decode(errors="replace").split()}
            except (imaplib.IMAP4.error, OSError, AttributeError):
                pass
            if not caps:
                with contextlib.suppress(AttributeError, TypeError):
                    caps = {str(cap).upper() for cap in self.connection.capabilities}
            self._caps = caps
        return self._caps

    def process_messages(
        self,
        messages: Sequence[EmailMessage],
        mode: DeleteMode = DeleteMode.MOVE,
        callback: ProgressCallback | None = None,
    ) -> tuple[int, list[str], str]:
        """ينقل الرسائل المحددة إلى مجلد مراجعة، أو يحذفها نهائيًا.

        النقل هو الافتراضي لأن ``\\Deleted`` + ``EXPUNGE`` محو لا رجعة فيه على
        أغلب المزوّدين، وتصنيف الرسالة يبقى استدلاليًا مهما دقّ.

        ``EXPUNGE`` العادي يمحو *كل* رسالة معلّمة بـ ``\\Deleted`` في الصندوق، ومنها
        رسائل علّمها عميل آخر (Thunderbird مثلًا) ولم يخترها المستخدم هنا. لذلك:
        ``UID MOVE`` (RFC 6851) إن توفّر، وإلا ``UID EXPUNGE`` (RFC 4315) على
        رسائلنا فقط، وإلا لا محو عام إلا إذا لم يكن في الصندوق غير رسائلنا.
        يرجّع (عدد الناجح، قائمة الأخطاء، رسالة الحصيلة).
        """
        if self.connection is None:
            return 0, ["غير متصل"], "غير متصل"
        if not messages:
            return 0, [], "لم تُحدَّد أي رسالة"

        uids = [m.uid.encode() for m in messages]
        errors: list[str] = []
        done: list[bytes] = []
        folder = self.review_folder_name() if mode is DeleteMode.MOVE else ""
        caps = self._capabilities()
        strategy = _Strategy(
            mode=mode,
            folder=folder,
            move="MOVE" in caps and mode is DeleteMode.MOVE,
            uidplus="UIDPLUS" in caps,
        )

        if mode is DeleteMode.MOVE:
            ok, error = self._ensure_folder(folder)
            if not ok:
                return 0, [error], f"تعذّر تجهيز مجلد المراجعة: {error}"

        try:
            with self._lock:
                self.connection.select("INBOX")
        except (imaplib.IMAP4.error, OSError) as exc:
            return 0, [str(exc)], f"تعذّر فتح صندوق الوارد: {exc}"

        chunks = _chunks(uids, UID_CHUNK)
        for position, chunk in enumerate(chunks, start=1):
            ok, error = self._process_chunk(chunk, strategy)
            if ok:
                done.extend(chunk)
            else:
                # الدفعة فشلت ككل - نعيد المحاولة فرادى لنعرف أي رسالة تحديدًا.
                for uid in chunk:
                    single_ok, single_error = self._process_chunk([uid], strategy)
                    if single_ok:
                        done.append(uid)
                    else:
                        errors.append(f"UID {uid.decode(errors='replace')}: {single_error}")
            if callback:
                callback(
                    f"تمت معالجة {len(done)}/{len(uids)}",
                    int(position / len(chunks) * 100),
                )

        note = ""
        if done and not strategy.move and not strategy.uidplus:
            note = self._expunge_if_only_ours(done, errors)

        processed = len(done)
        if mode is DeleteMode.MOVE:
            summary = f"تم نقل {processed} رسالة إلى «{folder}»"
        else:
            summary = f"تم حذف {processed} رسالة نهائيًا"
        if errors:
            summary += f" — وفشل {len(errors)}"
        if note:
            summary += f"\n{note}"

        handled = {uid.decode() for uid in done}
        self.messages = [m for m in self.messages if m.uid not in handled]
        return processed, errors, summary

    def _expunge_if_only_ours(self, done: list[bytes], errors: list[str]) -> str:
        """محو عام آمن: فقط إن لم تكن في الصندوق رسائل محذوفة غير التي علّمناها."""
        try:
            with self._lock:
                typ, data = self.connection.uid("SEARCH", None, "DELETED")
                flagged = set(data[0].split()) if typ == "OK" and data and data[0] else set()
                others = flagged - set(done)
                if typ != "OK" or others:
                    count = len(others) if typ == "OK" else "؟"
                    return (
                        f"عُلّمت الرسائل للحذف ولم تُمحَ: في الصندوق {count} رسالة أخرى معلّمة "
                        "للحذف من عميل آخر، والخادم لا يدعم المحو الانتقائي (UIDPLUS)."
                    )
                self.connection.expunge()
        except (imaplib.IMAP4.error, OSError) as exc:
            errors.append(f"تعذّر إنهاء العملية على الخادم: {exc}")
        return ""

    def _ensure_folder(self, folder: str) -> tuple[bool, str]:
        """ينشئ مجلد المراجعة إن لم يكن موجودًا."""
        try:
            with self._lock:
                typ, _ = self.connection.select(_quote_mailbox(folder))
                if typ == "OK":
                    return True, ""
                typ, data = self.connection.create(_quote_mailbox(folder))
                if typ != "OK":
                    reason = data[0].decode(errors="replace") if data and data[0] else typ
                    return False, f"رفض الخادم إنشاء «{folder}»: {reason}"
                self.connection.subscribe(_quote_mailbox(folder))
            return True, ""
        except (imaplib.IMAP4.error, OSError) as exc:
            return False, str(exc)

    def _process_chunk(self, uids: list[bytes], strategy: _Strategy) -> tuple[bool, str]:
        joined = b",".join(uids)
        try:
            with self._lock:
                if strategy.move:
                    # نسخ ثم محو في أمر واحد ذري - لا تكرار في المجلد إن فشل نصف الطريق.
                    typ, _ = self.connection.uid("MOVE", joined, _quote_mailbox(strategy.folder))
                    return (True, "") if typ == "OK" else (False, f"فشل النقل ({typ})")
                if strategy.mode is DeleteMode.MOVE:
                    typ, _ = self.connection.uid("COPY", joined, _quote_mailbox(strategy.folder))
                    if typ != "OK":
                        return False, f"فشل النسخ إلى المجلد ({typ})"
                typ, _ = self.connection.uid("STORE", joined, "+FLAGS", "(\\Deleted)")
                if typ != "OK":
                    return False, f"فشل تعليم الرسالة ({typ})"
                if strategy.uidplus:
                    typ, _ = self.connection.uid("EXPUNGE", joined)
                    if typ != "OK":
                        return False, f"فشل المحو ({typ})"
            return True, ""
        except (imaplib.IMAP4.error, OSError) as exc:
            return False, str(exc)

    # -------------------------------------------------- إلغاء الاشتراك

    def unsubscribe(
        self,
        messages: Sequence[EmailMessage],
        *,
        allow_http: bool = True,
        smtp_password: str | None = None,
        callback: ProgressCallback | None = None,
    ) -> list[UnsubscribeOutcome]:
        """يلغي الاشتراك بأأمن وسيلة متاحة لكل مرسل.

        الترتيب: نقرة واحدة (RFC 8058) ← رسالة ``mailto`` ← يدوي.
        الروابط التي لا تجتاز فحص السلامة لا تُزار إطلاقًا، وتُعاد للمستخدم
        ليقرر بنفسه.
        """
        targets: dict[str, EmailMessage] = {}
        for message in messages:
            if message.method is not UnsubscribeMethod.NONE:
                targets.setdefault(message.sender_email, message)

        outcomes: list[UnsubscribeOutcome] = []
        total = len(targets)
        if total == 0:
            self.unsubscribe_results = []
            return outcomes

        for index, (sender, message) in enumerate(targets.items(), start=1):
            method = message.method
            if method is UnsubscribeMethod.ONE_CLICK and allow_http:
                outcomes.append(self._unsubscribe_one_click(message))
            elif method is UnsubscribeMethod.MAILTO and smtp_password:
                outcomes.append(self._unsubscribe_mailto(message, smtp_password))
            else:
                outcomes.append(
                    UnsubscribeOutcome(
                        sender_email=sender,
                        method=UnsubscribeMethod.MANUAL,
                        ok=False,
                        detail="يحتاج تأكيدًا يدويًا — افتح الرابط بنفسك",
                        link=message.unsubscribe_link or message.unsubscribe_mailto,
                    )
                )
            if callback:
                callback(f"تمت معالجة {index}/{total}", int(index / total * 100))
            time.sleep(UNSUB_DELAY)

        self.unsubscribe_results = outcomes
        return outcomes

    def _unsubscribe_one_click(self, message: EmailMessage) -> UnsubscribeOutcome:
        """RFC 8058: طلب POST بجسم ``List-Unsubscribe=One-Click``.

        الطلب القديم كان GET، وكثير من المزوّدين يتجاهلونه عمدًا لأن عملاء
        البريد يجلبون الروابط مسبقًا — فكانت الأداة تعلن نجاحًا لم يحصل.
        """
        link = message.unsubscribe_link or ""
        base = UnsubscribeOutcome(
            sender_email=message.sender_email,
            method=UnsubscribeMethod.ONE_CLICK,
            ok=False,
            detail="",
            link=link,
        )

        if not REQUESTS_AVAILABLE:
            base.detail = "مكتبة requests غير مثبتة: pip install requests"
            return base

        try:
            check_unsubscribe_url(link)
        except UnsafeUrlError as exc:
            base.method = UnsubscribeMethod.MANUAL
            base.detail = f"رابط مرفوض — {exc}"
            return base

        try:
            response = requests.post(
                link,
                data={"List-Unsubscribe": "One-Click"},
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=HTTP_TIMEOUT,
                # كل إعادة توجيه تُفحص يدويًا؛ الاتباع التلقائي يلتف على الفحص.
                allow_redirects=False,
            )
            hops = 0
            while response.is_redirect and hops < MAX_REDIRECTS:
                target = response.headers.get("Location", "")
                try:
                    check_unsubscribe_url(target)
                except UnsafeUrlError as exc:
                    base.method = UnsubscribeMethod.MANUAL
                    base.detail = f"إعادة توجيه مرفوضة — {exc}"
                    return base
                response = requests.post(
                    target,
                    data={"List-Unsubscribe": "One-Click"},
                    headers={"User-Agent": USER_AGENT},
                    timeout=HTTP_TIMEOUT,
                    allow_redirects=False,
                )
                hops += 1
        except Exception as exc:  # requests يرفع عائلة استثناءات واسعة
            base.detail = f"فشل الطلب: {str(exc)[:80]}"
            return base

        if 200 <= response.status_code < 300:
            base.ok = True
            base.detail = f"تم إلغاء الاشتراك (HTTP {response.status_code})"
        else:
            base.detail = f"رفض الخادم الطلب (HTTP {response.status_code})"
        return base

    def _unsubscribe_mailto(self, message: EmailMessage, password: str) -> UnsubscribeOutcome:
        """يرسل رسالة إلغاء اشتراك — لا تكشف IP ولا تزور موقع المرسل."""
        recipient, subject, body = parse_mailto(message.unsubscribe_mailto or "")
        outcome = UnsubscribeOutcome(
            sender_email=message.sender_email,
            method=UnsubscribeMethod.MAILTO,
            ok=False,
            detail="",
            link=message.unsubscribe_mailto,
        )
        if not recipient:
            outcome.detail = "عنوان mailto غير صالح أو يحوي أكثر من مستقبِل — لم يُرسَل شيء"
            return outcome

        try:
            host, port = self.rules.smtp_server(self.account)
            note = StdEmailMessage()
            note["From"] = self.account
            note["To"] = recipient
            note["Subject"] = subject
            note.set_content(body)

            context = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as server:
                server.starttls(context=context)
                server.login(self.account, password)
                server.send_message(note)
            outcome.ok = True
            outcome.detail = f"أُرسلت رسالة إلغاء اشتراك إلى {recipient}"
        except (smtplib.SMTPException, OSError, ValueError) as exc:
            # ValueError يشمل InvalidAddressError وترويسة فيها سطر جديد؛ فشل
            # مرسل واحد يجب ألا يوقف بقية الدفعة.
            outcome.detail = f"تعذّر الإرسال: {str(exc)[:80]}"
        return outcome

    # ------------------------------------------------------------- تقارير

    def senders_summary(self) -> dict[str, int]:
        return dict(Counter(message.sender_email for message in self.messages).most_common())

    def export_results(self, filepath: str | Path, include_links: bool = False) -> int:
        """يصدّر تقرير JSON.

        ``include_links`` مُطفأ افتراضيًا: روابط إلغاء الاشتراك تحمل غالبًا
        معرّفًا موقّعًا يخص المستلم، وبعض المزوّدين يجعلها تفتح صفحة تفضيلات
        الحساب بلا مصادقة — أي أنها أقرب إلى رموز وصول منها إلى بيانات تقرير.
        """
        payload: dict = {
            "scan_date": datetime.now().isoformat(timespec="seconds"),
            "account": self.account,
            "examined": self.last_report.examined,
            "suspected": len(self.messages),
            "skipped": self.last_report.skipped,
            "senders_summary": self.senders_summary(),
            "messages": [
                {
                    "sender": message.sender,
                    "email": message.sender_email,
                    "subject": message.subject,
                    "date": message.date,
                    "score": message.score,
                    "reasons": list(message.reasons),
                    "unsubscribe_method": message.method.value,
                }
                for message in self.messages
            ],
            "unsubscribe_results": [
                {
                    "sender": outcome.sender_email,
                    "method": outcome.method.value,
                    "ok": outcome.ok,
                    "detail": outcome.detail,
                }
                for outcome in self.unsubscribe_results
            ],
        }

        if include_links:
            payload["_warning"] = (
                "هذا الملف يحوي روابط إلغاء اشتراك قد تعمل كرموز وصول لحسابك "
                "لدى المرسلين. لا ترفعه ولا تضعه في مجلد مزامَن."
            )
            payload["links"] = [
                {
                    "email": message.sender_email,
                    "link": message.unsubscribe_link,
                    "mailto": message.unsubscribe_mailto,
                }
                for message in self.messages
                if message.unsubscribe_link or message.unsubscribe_mailto
            ]

        path = Path(filepath)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(payload.get("links", payload["messages"]))
