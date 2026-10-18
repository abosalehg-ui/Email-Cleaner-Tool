"""المحرك: أوامر IMAP المستخدمة، والنقل مقابل الحذف، والتصدير.

بلا شبكة - اتصال IMAP مزيّف يسجّل كل أمر يُرسَل إليه.
"""

from __future__ import annotations

import imaplib
import json
import ssl
from typing import ClassVar

import pytest
from conftest import FIXTURES

from email_cleaner.core import EmailCleanerCore
from email_cleaner.models import DeleteMode, EmailMessage, UnsubscribeMethod
from email_cleaner.rules import Rules


class FakeIMAP:
    """اتصال IMAP مزيّف يسجّل الأوامر ويحاكي حالة الصندوق.

    ``capabilities`` يحدد ما يدعمه الخادم (MOVE / UIDPLUS)، و``flagged`` رسائل
    عليها ``\\Deleted`` مسبقًا من عميل آخر - لاختبار أن المحو لا يطالها.
    """

    def __init__(
        self,
        uids,
        fixture_map,
        fail_uids=(),
        capabilities=("IMAP4REV1",),
        flagged=(),
        uid_after_body=False,
        create_result="OK",
    ):
        self.uids = uids
        self.fixture_map = fixture_map
        self.fail_uids = set(fail_uids)
        self.capabilities = tuple(capabilities)
        self.flagged = set(flagged)
        self.uid_after_body = uid_after_body
        self.create_result = create_result
        self.commands = []
        self.expunged = False
        self.expunged_uids = set()
        self.created = []
        self.selected = []

    def select(self, mailbox="INBOX"):
        self.selected.append(mailbox)
        if mailbox != "INBOX" and mailbox not in self.created:
            return "NO", [b"folder does not exist"]
        return "OK", [b"1"]

    def create(self, mailbox):
        if self.create_result != "OK":
            return self.create_result, [b"[CANNOT] reserved name"]
        self.created.append(mailbox)
        return "OK", [b"created"]

    def subscribe(self, mailbox):
        return "OK", [b"done"]

    def list(self, directory='""', pattern="*"):
        return "OK", [rb'(\Noselect) "/" ""']

    def expunge(self):
        # EXPUNGE عام: يمحو كل ما عليه \Deleted - وهو ما يجب تجنّبه.
        self.expunged = True
        self.expunged_uids |= self.flagged
        self.flagged = set()
        return "OK", [b"done"]

    def logout(self):
        return "BYE", [b"bye"]

    def capability(self):
        # قدرات ما بعد الدخول؛ ``self.capabilities`` عند imaplib تبقى قدرات ما قبله.
        return "OK", [" ".join(self.capabilities).encode()]

    def shutdown(self):
        self.commands.append(("SHUTDOWN", ()))

    def uid(self, command, *args):
        self.commands.append((command, args))
        if command == "SEARCH":
            if args[-1] == "DELETED":
                return "OK", [b" ".join(sorted(self.flagged))]
            return "OK", [b" ".join(self.uids)]
        if command == "FETCH":
            wanted = args[0].split(b",")
            if self.fail_uids & set(wanted):
                raise OSError("connection reset")
            payload = []
            for seq, uid in enumerate(wanted, start=1):
                source = self.fixture_map[uid]
                raw = source if isinstance(source, bytes) else (FIXTURES / source).read_bytes()
                if self.uid_after_body:
                    payload += [
                        (b"%d (BODY[HEADER] {%d}" % (seq, len(raw)), raw),
                        b" UID %s)" % uid,
                    ]
                else:
                    payload += [(b"%d (UID %s BODY[HEADER] {%d}" % (seq, uid, len(raw)), raw), b")"]
            return "OK", payload
        if command == "STORE":
            self.flagged |= set(args[0].split(b","))
            return "OK", [b"done"]
        if command == "COPY":
            return "OK", [b"done"]
        if command == "MOVE" and "MOVE" in self.capabilities:
            self.expunged_uids |= set(args[0].split(b","))
            return "OK", [b"done"]
        if command == "EXPUNGE" and "UIDPLUS" in self.capabilities:
            targets = set(args[0].split(b",")) & self.flagged
            self.expunged_uids |= targets
            self.flagged -= targets
            return "OK", [b"done"]
        return "NO", [b"unsupported"]


@pytest.fixture
def core(rules):
    engine = EmailCleanerCore(rules=rules)
    return engine


@pytest.fixture
def wired_core(core):
    core.connection = FakeIMAP(
        uids=[b"101", b"102", b"103", b"104"],
        fixture_map={
            b"101": "newsletter.eml",
            b"102": "github_notification.eml",
            b"103": "arabic_promo.eml",
            b"104": "personal.eml",
        },
    )
    core.account = "user@example.com"
    return core


class TestScanUsesSafeImapCommands:
    def test_search_and_fetch_go_through_uid(self, wired_core):
        wired_core.scan_inbox(days_back=30, limit=500)
        commands = [name for name, _ in wired_core.connection.commands]
        # أرقام IMAP التسلسلية تنزاح كلما حُذفت رسالة، فالحذف بها قد يطال
        # رسائل لم تُفحص. UID معرّف ثابت.
        assert "SEARCH" in commands
        # الترويسات تُجلب على دفعات: 4 رسائل في طلب واحد لا أربعة طلبات.
        assert commands.count("FETCH") == 1

    def test_fetch_uses_peek_and_headers_only(self, wired_core):
        wired_core.scan_inbox()
        fetches = [args for name, args in wired_core.connection.commands if name == "FETCH"]
        spec = fetches[0][1]
        # RFC822 كان ينزّل الرسالة كاملة *ويعلّمها مقروءة*.
        assert "BODY.PEEK" in spec
        assert "RFC822" not in spec
        assert "HEADER.FIELDS" in spec

    def test_only_suspected_messages_are_returned(self, wired_core):
        report = wired_core.scan_inbox()
        senders = {message.sender_email for message in report.messages}
        assert senders == {"newsletter@noon.example", "promo@shop.example"}
        assert report.examined == 4

    def test_results_are_sorted_by_score(self, wired_core):
        report = wired_core.scan_inbox()
        scores = [message.score for message in report.messages]
        assert scores == sorted(scores, reverse=True)


class TestScanErrorReporting:
    def test_failed_fetches_are_counted_not_swallowed(self, core):
        core.connection = FakeIMAP(
            uids=[b"101", b"102"],
            fixture_map={b"101": "newsletter.eml", b"102": "personal.eml"},
            fail_uids=[b"102"],
        )
        report = core.scan_inbox()
        # ``except Exception: continue`` القديم كان يجعل صندوقًا معطوبًا
        # يبدو نظيفًا تمامًا.
        assert report.skipped == 1
        assert report.errors

    def test_not_connected_is_reported(self, core):
        report = core.scan_inbox()
        assert report.errors == ["غير متصل"]

    def test_cancel_stops_early_and_keeps_partial_results(self, wired_core, monkeypatch):
        monkeypatch.setattr("email_cleaner.core.FETCH_CHUNK", 2)
        calls = iter([False, True])
        report = wired_core.scan_inbox(should_cancel=lambda: next(calls, True))
        assert report.cancelled is True
        assert report.examined == 2
        fetched = [args[0] for name, args in wired_core.connection.commands if name == "FETCH"]
        assert fetched == [b"101,102"]

    def test_failed_batch_is_retried_one_by_one(self, core):
        core.connection = FakeIMAP(
            uids=[b"101", b"102", b"103"],
            fixture_map={b"101": "newsletter.eml", b"103": "arabic_promo.eml"},
            fail_uids=[b"102"],
        )
        report = core.scan_inbox()
        assert report.skipped == 1
        assert {m.sender_email for m in report.messages} == {
            "newsletter@noon.example",
            "promo@shop.example",
        }


class TestBatchFetchParsing:
    def test_uid_after_the_body_is_understood(self, core):
        # بعض الخوادم ترسل ``UID n`` بعد جسم الترويسات لا قبله.
        core.connection = FakeIMAP(
            uids=[b"101", b"103"],
            fixture_map={b"101": "newsletter.eml", b"103": "arabic_promo.eml"},
            uid_after_body=True,
        )
        report = core.scan_inbox()
        assert {m.uid for m in report.messages} == {"101", "103"}

    def test_frequency_is_counted_before_the_threshold(self, core):
        # رسالة فيها List-Unsubscribe فقط درجتها 2 (تحت العتبة 3). المرسل المتكرر
        # يجب أن يرفعها - الفلترة قبل العدّ كانت تُسقطها قبل أن تُحسب.
        uids = [str(n).encode() for n in range(201, 207)]
        core.connection = FakeIMAP(
            uids=uids, fixture_map=dict.fromkeys(uids, "github_notification.eml")
        )
        report = core.scan_inbox()
        assert len(report.messages) == 6
        assert all("مرسل متكرر" in " ".join(m.reasons) for m in report.messages)

    def test_a_lone_low_score_message_stays_out(self, core):
        core.connection = FakeIMAP(uids=[b"201"], fixture_map={b"201": "github_notification.eml"})
        assert core.scan_inbox().messages == []


class TestExpungeNeverTouchesOtherMessages:
    """EXPUNGE العام يمحو كل ما عليه \\Deleted، ومنه ما علّمه عميل آخر."""

    def _scan(self, core, **fake):
        core.connection = FakeIMAP(
            uids=[b"101", b"103"],
            fixture_map={b"101": "newsletter.eml", b"103": "arabic_promo.eml"},
            **fake,
        )
        core.scan_inbox()
        return list(core.messages)

    def test_uses_uid_move_when_supported(self, core):
        messages = self._scan(core, capabilities=("IMAP4REV1", "MOVE"), flagged={b"999"})
        processed, errors, _ = core.process_messages(messages, DeleteMode.MOVE)
        names = [name for name, _ in core.connection.commands]
        assert processed == 2 and errors == []
        assert "MOVE" in names
        assert "COPY" not in names and "STORE" not in names
        assert not core.connection.expunged
        assert core.connection.flagged == {b"999"}

    def test_uid_expunge_with_uidplus_removes_only_ours(self, core):
        messages = self._scan(core, capabilities=("IMAP4REV1", "UIDPLUS"), flagged={b"999"})
        core.process_messages(messages, DeleteMode.PERMANENT)
        assert core.connection.expunged_uids == {b"101", b"103"}
        assert core.connection.flagged == {b"999"}
        assert not core.connection.expunged

    def test_without_uidplus_other_flagged_mail_blocks_global_expunge(self, core):
        messages = self._scan(core, flagged={b"999"})
        processed, _, summary = core.process_messages(messages, DeleteMode.PERMANENT)
        assert processed == 2
        assert not core.connection.expunged
        assert b"999" not in core.connection.expunged_uids
        assert "لم تُمحَ" in summary

    def test_without_uidplus_global_expunge_runs_when_only_ours_are_flagged(self, core):
        messages = self._scan(core)
        core.process_messages(messages, DeleteMode.PERMANENT)
        assert core.connection.expunged

    def test_folder_creation_refusal_is_one_clear_error(self, core):
        messages = self._scan(core, create_result="NO")
        processed, errors, summary = core.process_messages(messages, DeleteMode.MOVE)
        assert processed == 0
        assert len(errors) == 1
        assert "رفض الخادم إنشاء" in summary


class TestProcessMessages:
    def test_move_copies_before_deleting(self, wired_core):
        wired_core.scan_inbox()
        messages = list(wired_core.messages)
        processed, errors, summary = wired_core.process_messages(messages, DeleteMode.MOVE)
        commands = [name for name, _ in wired_core.connection.commands]
        assert processed == len(messages)
        assert errors == []
        assert "COPY" in commands
        assert "STORE" in commands
        assert wired_core.connection.expunged
        assert "نقل" in summary

    def test_permanent_mode_skips_the_copy(self, wired_core):
        wired_core.scan_inbox()
        wired_core.process_messages(list(wired_core.messages), DeleteMode.PERMANENT)
        commands = [name for name, _ in wired_core.connection.commands]
        assert "COPY" not in commands
        assert "STORE" in commands

    def test_only_selected_messages_are_touched(self, wired_core):
        wired_core.scan_inbox()
        chosen = wired_core.messages[:1]
        wired_core.process_messages(chosen, DeleteMode.PERMANENT)
        stores = [args for name, args in wired_core.connection.commands if name == "STORE"]
        assert stores[0][0] == chosen[0].uid.encode()
        # الرسالة غير المحددة ما زالت في القائمة.
        assert len(wired_core.messages) == 1

    def test_empty_selection_is_a_no_op(self, wired_core):
        processed, _errors, summary = wired_core.process_messages([])
        assert processed == 0
        assert "لم تُحدَّد" in summary

    def test_not_connected(self, core):
        message = EmailMessage(uid="1", subject="s", sender="n", sender_email="a@b.com", date="d")
        processed, errors, _ = core.process_messages([message])
        assert processed == 0
        assert errors == ["غير متصل"]


class TestUnsubscribe:
    def test_bad_mailto_fails_alone_without_aborting_the_batch(self, core, monkeypatch):
        sent = []

        class FakeSMTP:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def starttls(self, context=None):
                pass

            def login(self, user, password):
                pass

            def send_message(self, note):
                sent.append(note["To"])

        monkeypatch.setattr("email_cleaner.core.smtplib.SMTP", FakeSMTP)
        monkeypatch.setattr("email_cleaner.core.UNSUB_DELAY", 0)
        core.account = "me@gmail.com"
        bad = EmailMessage(
            "1", "s", "n", "a@evil.com", "d", unsubscribe_mailto="mailto:x@y.com%0ABcc:%20v@z.com"
        )
        good = EmailMessage(
            "2", "s", "n", "b@shop.com", "d", unsubscribe_mailto="mailto:u@shop.com"
        )
        outcomes = core.unsubscribe([bad, good], smtp_password="pw")
        assert [o.ok for o in outcomes] == [False, True]
        assert sent == ["u@shop.com"]

    def test_unsafe_link_is_never_visited(self, core, monkeypatch):
        message = EmailMessage(
            uid="1",
            subject="s",
            sender="n",
            sender_email="a@b.com",
            date="d",
            unsubscribe_link="https://192.168.1.1/unsub",
            one_click=True,
        )

        def explode(*args, **kwargs):
            raise AssertionError("لا يجوز إرسال أي طلب لرابط داخلي")

        monkeypatch.setattr("email_cleaner.core.requests.post", explode)
        outcomes = core.unsubscribe([message])
        assert len(outcomes) == 1
        assert outcomes[0].ok is False
        assert outcomes[0].method is UnsubscribeMethod.MANUAL

    def test_manual_links_are_reported_not_opened(self, core, monkeypatch):
        message = EmailMessage(
            uid="1",
            subject="s",
            sender="n",
            sender_email="a@b.com",
            date="d",
            unsubscribe_link="https://shop.example/u",  # بلا one_click
        )

        def explode(*args, **kwargs):
            raise AssertionError("رابط بلا دعم النقرة الواحدة لا يُزار آليًا")

        monkeypatch.setattr("email_cleaner.core.requests.post", explode)
        outcomes = core.unsubscribe([message])
        assert outcomes[0].method is UnsubscribeMethod.MANUAL
        assert outcomes[0].link == "https://shop.example/u"

    def test_one_click_sends_post_with_rfc8058_body(self, core, monkeypatch):
        captured = {}

        class Response:
            status_code = 200
            is_redirect = False
            headers: ClassVar[dict] = {}

        def fake_post(url, data=None, headers=None, timeout=None, allow_redirects=None):
            captured.update(url=url, data=data, allow_redirects=allow_redirects)
            return Response()

        monkeypatch.setattr("email_cleaner.core.requests.post", fake_post)
        monkeypatch.setattr("email_cleaner.core.check_unsubscribe_url", lambda url: None)

        message = EmailMessage(
            uid="1",
            subject="s",
            sender="n",
            sender_email="a@b.com",
            date="d",
            unsubscribe_link="https://shop.example/u",
            one_click=True,
        )
        outcomes = core.unsubscribe([message])
        assert captured["data"] == {"List-Unsubscribe": "One-Click"}
        # الاتباع التلقائي لإعادة التوجيه يلتف حول فحص السلامة.
        assert captured["allow_redirects"] is False
        assert outcomes[0].ok is True

    def test_messages_without_any_method_are_skipped(self, core):
        message = EmailMessage(uid="1", subject="s", sender="n", sender_email="a@b.com", date="d")
        assert core.unsubscribe([message]) == []


class TestExport:
    def test_links_are_excluded_by_default(self, wired_core, tmp_path):
        wired_core.scan_inbox()
        target = tmp_path / "report.json"
        wired_core.export_results(target)
        payload = json.loads(target.read_text(encoding="utf-8"))
        # الروابط تحمل معرّفًا موقّعًا يخص المستلم؛ لا تُكتب إلا بطلب صريح.
        assert "links" not in payload
        assert payload["suspected"] == len(wired_core.messages)
        assert payload["messages"]

    def test_links_included_on_request_with_a_warning(self, wired_core, tmp_path):
        wired_core.scan_inbox()
        target = tmp_path / "report.json"
        wired_core.export_results(target, include_links=True)
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["links"]
        assert "_warning" in payload

    def test_export_is_valid_utf8_json(self, wired_core, tmp_path):
        wired_core.scan_inbox()
        target = tmp_path / "report.json"
        wired_core.export_results(target)
        assert "عرض" in target.read_text(encoding="utf-8")


class TestConnectValidation:
    def test_certificate_is_verified(self, core, monkeypatch):
        captured = {}

        class Recorder:
            def __init__(self, host, port, **kwargs):
                captured.update(kwargs)

            def login(self, user, password):
                return "OK", [b"ok"]

        monkeypatch.setattr("email_cleaner.core.imaplib.IMAP4_SSL", Recorder)
        ok, _ = core.connect("me@gmail.com", "secret")
        context = captured["ssl_context"]
        assert ok is True
        # بلا سياق صريح يستخدم imaplib سياقًا يقبل أي شهادة (CERT_NONE).
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (UnicodeEncodeError("ascii", "ك", 0, 1, "x"), "غير لاتينية"),
            (imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials"), "غير صحيحة"),
        ],
    )
    def test_failed_login_closes_the_socket(self, core, monkeypatch, error, expected):
        closed = []

        class Failing:
            def __init__(self, *args, **kwargs):
                pass

            def login(self, user, password):
                raise error

            def shutdown(self):
                closed.append(True)

        monkeypatch.setattr("email_cleaner.core.imaplib.IMAP4_SSL", Failing)
        ok, message = core.connect("me@gmail.com", "secret")
        assert ok is False
        assert expected in message
        assert closed == [True]
        assert core.connection is None

    def test_invalid_address_is_rejected_before_any_socket(self, core):
        ok, message = core.connect("ahmed", "secret")
        assert ok is False
        assert "صيغة البريد" in message


def test_core_loads_default_rules_without_user_file():
    assert isinstance(EmailCleanerCore(rules=Rules.load(None)).rules, Rules)
