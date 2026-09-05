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
from collections.abc import Callable, Sequence
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
FETCH_SPEC = f"(BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])"

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


def _quote_mailbox(name: str) -> str:
    """يقتبس اسم المجلد ليصلح كوسيط IMAP."""
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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

        try:
            with self._lock:
                # بلا مهلة كان الاتصال بنطاق مجهول يعلّق دقيقتين بلا أي مؤشر.
                connection = imaplib.IMAP4_SSL(host, port, timeout=CONNECT_TIMEOUT)
                connection.login(address, password)
                self.connection = connection
                self.account = address
            return True, f"تم الاتصال بـ {host} ✅"
        except imaplib.IMAP4.error as exc:
            return False, _translate_login_error(exc)
        except TimeoutError:
            return False, f"انتهت مهلة الاتصال بـ {host} بعد {CONNECT_TIMEOUT} ثانية."
        except OSError as exc:
            return False, f"تعذّر الوصول إلى {host}: {exc}"

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
    ) -> ScanReport:
        """يفحص INBOX ويرجّع تقريرًا يفصل ما نجح عمّا فشل."""
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

        for index, uid in enumerate(uids, start=1):
            try:
                message = self._fetch_and_classify(uid)
            except (imaplib.IMAP4.error, OSError) as exc:
                report.skipped += 1
                if len(report.errors) < 5:
                    report.errors.append(f"UID {uid.decode(errors='replace')}: {exc}")
            else:
                if message is not None:
                    self.messages.append(message)

            if index % 20 == 0 or index == total:
                notify(
                    f"تم فحص {index}/{total} رسالة ({len(self.messages)} مشتبهة)",
                    int(index / total * 100),
                )

        apply_sender_frequency(self.messages, self.rules)
        self.messages.sort(key=lambda m: (-m.score, m.sender_email))
        report.messages = self.messages

        summary = f"اكتمل الفحص: {len(self.messages)} رسالة مشتبهة من {total}"
        if report.skipped:
            summary += f" (تعذّر تحليل {report.skipped})"
        notify(summary, 100)
        return report

    def _fetch_and_classify(self, uid: bytes) -> EmailMessage | None:
        """يجلب ترويسات رسالة واحدة ويرجّعها إن تجاوزت عتبة الاشتباه."""
        with self._lock:
            connection = self.connection
            if connection is None:
                raise imaplib.IMAP4.error("انقطع الاتصال أثناء الفحص")
            typ, payload = connection.uid("FETCH", uid, FETCH_SPEC)

        if typ != "OK" or not payload or not isinstance(payload[0], tuple):
            return None

        msg = email.message_from_bytes(payload[0][1])
        subject = decode_header_value(msg.get("Subject"))
        sender_name, sender_email = extract_sender(msg.get("From"))

        score, reasons = score_message(msg, subject, sender_email, self.rules)
        if score < self.rules.include_threshold:
            return None

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
        """فاصل المجلدات لدى الخادم ('/' أو '.')، مع افتراضي آمن."""
        try:
            with self._lock:
                typ, data = self.connection.list('""', '""')
            if typ == "OK" and data and data[0]:
                match = re.search(rb"\(.*?\)\s+\"([^\"]*)\"", data[0])
                if match and match.group(1):
                    return match.group(1).decode(errors="replace")
        except (imaplib.IMAP4.error, OSError, AttributeError):
            pass
        return "/"

    def review_folder_name(self) -> str:
        return self._hierarchy_delimiter().join(REVIEW_FOLDER_PARTS)

    def process_messages(
        self,
        messages: Sequence[EmailMessage],
        mode: DeleteMode = DeleteMode.MOVE,
        callback: ProgressCallback | None = None,
    ) -> tuple[int, list[str], str]:
        """ينقل الرسائل المحددة إلى مجلد مراجعة، أو يحذفها نهائيًا.

        النقل هو الافتراضي لأن ``\\Deleted`` + ``EXPUNGE`` محو لا رجعة فيه على
        أغلب المزوّدين، وتصنيف الرسالة يبقى استدلاليًا مهما دقّ.
        يرجّع (عدد الناجح، قائمة الأخطاء، رسالة الحصيلة).
        """
        if self.connection is None:
            return 0, ["غير متصل"], "غير متصل"
        if not messages:
            return 0, [], "لم تُحدَّد أي رسالة"

        uids = [m.uid.encode() for m in messages]
        errors: list[str] = []
        processed = 0
        folder = self.review_folder_name() if mode is DeleteMode.MOVE else ""

        if mode is DeleteMode.MOVE:
            ok, error = self._ensure_folder(folder)
            if not ok:
                return 0, [error], f"تعذّر تجهيز مجلد المراجعة: {error}"

        try:
            with self._lock:
                self.connection.select("INBOX")
        except (imaplib.IMAP4.error, OSError) as exc:
            return 0, [str(exc)], f"تعذّر فتح صندوق الوارد: {exc}"

        chunks = [uids[i : i + UID_CHUNK] for i in range(0, len(uids), UID_CHUNK)]
        for position, chunk in enumerate(chunks, start=1):
            ok, error = self._process_chunk(chunk, folder, mode)
            if ok:
                processed += len(chunk)
            else:
                # الدفعة فشلت ككل - نعيد المحاولة فرادى لنعرف أي رسالة تحديدًا.
                for uid in chunk:
                    single_ok, single_error = self._process_chunk([uid], folder, mode)
                    if single_ok:
                        processed += 1
                    else:
                        errors.append(f"UID {uid.decode(errors='replace')}: {single_error}")
            if callback:
                callback(
                    f"تمت معالجة {processed}/{len(uids)}",
                    int(position / len(chunks) * 100),
                )

        try:
            with self._lock:
                self.connection.expunge()
        except (imaplib.IMAP4.error, OSError) as exc:
            errors.append(f"تعذّر إنهاء العملية على الخادم: {exc}")

        if mode is DeleteMode.MOVE:
            summary = f"تم نقل {processed} رسالة إلى «{folder}»"
        else:
            summary = f"تم حذف {processed} رسالة نهائيًا"
        if errors:
            summary += f" — وفشل {len(errors)}"

        handled = {m.uid for m in messages}
        self.messages = [m for m in self.messages if m.uid not in handled]
        return processed, errors, summary

    def _ensure_folder(self, folder: str) -> tuple[bool, str]:
        """ينشئ مجلد المراجعة إن لم يكن موجودًا."""
        try:
            with self._lock:
                typ, _ = self.connection.select(_quote_mailbox(folder))
                if typ == "OK":
                    return True, ""
                self.connection.create(_quote_mailbox(folder))
                self.connection.subscribe(_quote_mailbox(folder))
            return True, ""
        except (imaplib.IMAP4.error, OSError) as exc:
            return False, str(exc)

    def _process_chunk(self, uids: list[bytes], folder: str, mode: DeleteMode) -> tuple[bool, str]:
        joined = b",".join(uids)
        try:
            with self._lock:
                if mode is DeleteMode.MOVE:
                    typ, _ = self.connection.uid("COPY", joined, _quote_mailbox(folder))
                    if typ != "OK":
                        return False, f"فشل النسخ إلى المجلد ({typ})"
                typ, _ = self.connection.uid("STORE", joined, "+FLAGS", "(\\Deleted)")
                if typ != "OK":
                    return False, f"فشل تعليم الرسالة ({typ})"
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
            outcome.detail = "عنوان mailto غير صالح"
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
        except (smtplib.SMTPException, OSError, InvalidAddressError) as exc:
            outcome.detail = f"تعذّر الإرسال: {str(exc)[:80]}"
        return outcome

    # ------------------------------------------------------------- تقارير

    def senders_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for message in self.messages:
            counts[message.sender_email] = counts.get(message.sender_email, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))

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
