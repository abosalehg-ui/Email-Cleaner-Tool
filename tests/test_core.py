"""المحرك: أوامر IMAP المستخدمة، والنقل مقابل الحذف، والتصدير.

بلا شبكة - اتصال IMAP مزيّف يسجّل كل أمر يُرسَل إليه.
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest
from conftest import FIXTURES

from email_cleaner.core import EmailCleanerCore
from email_cleaner.models import DeleteMode, EmailMessage, UnsubscribeMethod
from email_cleaner.rules import Rules


class FakeIMAP:
    """اتصال IMAP مزيّف يسجّل الأوامر ويرجّع ترويسات ملفات fixtures."""

    def __init__(self, uids, fixture_map, fail_uids=()):
        self.uids = uids
        self.fixture_map = fixture_map
        self.fail_uids = set(fail_uids)
        self.commands = []
        self.expunged = False
        self.created = []
        self.selected = []

    def select(self, mailbox="INBOX"):
        self.selected.append(mailbox)
        if mailbox != "INBOX" and mailbox not in self.created:
            return "NO", [b"folder does not exist"]
        return "OK", [b"1"]

    def create(self, mailbox):
        self.created.append(mailbox)
        return "OK", [b"created"]

    def subscribe(self, mailbox):
        return "OK", [b"done"]

    def list(self, directory='""', pattern="*"):
        return "OK", [rb'(\Noselect) "/" ""']

    def expunge(self):
        self.expunged = True
        return "OK", [b"done"]

    def logout(self):
        return "BYE", [b"bye"]

    def uid(self, command, *args):
        self.commands.append((command, args))
        if command == "SEARCH":
            return "OK", [b" ".join(self.uids)]
        if command == "FETCH":
            uid = args[0]
            if uid in self.fail_uids:
                raise OSError("connection reset")
            raw = (FIXTURES / self.fixture_map[uid]).read_bytes()
            return "OK", [(b"%s (UID %s BODY[HEADER])" % (uid, uid), raw), b")"]
        if command in ("STORE", "COPY"):
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
        assert commands.count("FETCH") == 4

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
    def test_invalid_address_is_rejected_before_any_socket(self, core):
        ok, message = core.connect("ahmed", "secret")
        assert ok is False
        assert "صيغة البريد" in message


def test_core_loads_default_rules_without_user_file():
    assert isinstance(EmailCleanerCore(rules=Rules.load(None)).rules, Rules)
