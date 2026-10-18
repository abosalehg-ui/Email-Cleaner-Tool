"""الواجهة: منطق الحالة والتحديد والبحث بلا خادم IMAP.

تحتاج شاشة (في CI عبر ``xvfb-run``)، وتُتخطى تلقائيًا بدونها.
"""

from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from email_cleaner import gui  # noqa: E402
from email_cleaner.models import DeleteMode, EmailMessage, ScanReport  # noqa: E402

MESSAGES = [
    EmailMessage(
        "1",
        "تخفيضات نهاية الموسم",
        "متجر",
        "deals@shop.com",
        "Mon, 13 Oct 2026 10:00:00 +0000",
        6,
        ("ترويسة إلغاء اشتراك",),
        "https://shop.com/u",
        None,
        True,
    ),
    EmailMessage(
        "2", "Weekly digest", "News", "news@site.com", "Sun, 12 Oct 2026 10:00:00 +0000", 5
    ),
    EmailMessage(
        "3", "Your newsletter", "Medium", "noreply@medium.com", "Wed, 01 Oct 2025 10:00:00 +0000", 3
    ),
]


@pytest.fixture
def app():
    try:
        instance = gui.EmailCleanerGUI()
    except tk.TclError as exc:
        pytest.skip(f"لا توجد شاشة: {exc}")
    instance.root.withdraw()
    instance.core.disconnect = lambda: setattr(instance.core, "connection", None)
    yield instance
    instance.root.destroy()


@pytest.fixture
def scanned(app):
    app.core.connection = object()
    app.core.account = "me@gmail.com"
    app._connected((True, "ok"))
    app.core.messages = list(MESSAGES)
    app._scan_finished(ScanReport(messages=list(MESSAGES), examined=40))
    return app


class TestLogin:
    def test_starts_on_login_with_scan_disabled(self, app):
        assert not app.core.is_connected
        assert app.connect_btn.enabled
        assert not app.scan_btn.enabled

    def test_invalid_email_shows_inline_error_without_connecting(self, app, monkeypatch):
        monkeypatch.setattr(app.core, "connect", lambda *a: pytest.fail("لا يجوز الاتصال"))
        app.email_var.set("not-an-email")
        app.pass_var.set("x")
        app._connect()
        assert "صيغة البريد" in app.login_error.cget("text") or app.login_error._raw

    def test_failed_login_keeps_the_password_and_shows_the_reason(self, app):
        app.pass_var.set("secret")
        app._connected((False, "البريد أو كلمة المرور غير صحيحة."))
        assert app.pass_var.get() == "secret"
        assert "غير صحيحة" in app.login_error._raw

    def test_successful_login_clears_the_password(self, app):
        app.core.connection = object()
        app.core.account = "me@gmail.com"
        app.pass_var.set("secret")
        app._connected((True, "ok"))
        assert app.pass_var.get() == ""
        assert app.scan_btn.enabled

    def test_server_hint_follows_the_address(self, app):
        app.email_var.set("someone@gmail.com")
        assert "imap.gmail.com" in app.server_hint.cget("text")
        app.email_var.set("broken")
        assert app.server_hint.cget("text") == ""

    def test_password_reveal_toggles(self, app):
        app._toggle_password()
        assert app.pass_entry.cget("show") == ""
        app._toggle_password()
        assert app.pass_entry.cget("show") == "•"


class TestScanResults:
    def test_high_scores_are_auto_checked(self, scanned):
        assert scanned.checked == {"1", "2"}

    def test_stats_reflect_the_scan(self, scanned):
        assert scanned.stats["examined"]._value.cget("text") == "40"
        assert scanned.stats["suspected"]._value.cget("text") == "3"
        assert scanned.stats["checked"]._value.cget("text") == "2"

    def test_process_button_counts_and_changes_with_mode(self, scanned):
        assert scanned.process_btn.enabled
        assert "2" in scanned.process_btn.text
        assert scanned.process_btn._variant == "primary"
        scanned.mode_var.set(DeleteMode.PERMANENT.value)
        assert scanned.process_btn._variant == "danger"
        assert "نهائي" in scanned.process_btn.text

    def test_unsubscribe_counts_only_messages_with_a_method(self, scanned):
        assert scanned.unsub_btn.enabled
        assert "(1)" in scanned.unsub_btn.text

    def test_toggle_updates_row_and_counts(self, scanned):
        scanned._toggle("1")
        assert "1" not in scanned.checked
        assert scanned.tree.set("1", "check") == gui.UNCHECKED
        assert "unchecked" in scanned.tree.item("1", "tags")
        assert "(1)" not in scanned.unsub_btn.text

    def test_nothing_checked_disables_processing(self, scanned):
        scanned._set_all(False)
        assert not scanned.process_btn.enabled
        assert not scanned.unsub_btn.enabled

    def test_search_filters_rows_and_select_all_applies_to_visible_only(self, scanned):
        scanned._set_all(False)
        scanned.search_var.set("medium")
        assert scanned.tree.get_children() == ("3",)
        scanned._set_all(True)
        assert scanned.checked == {"3"}

    def test_search_without_results_shows_empty_state(self, scanned):
        scanned.search_var.set("zzz")
        assert scanned.tree.get_children() == ()
        assert scanned.empty_state.winfo_manager() == "place"

    def test_sort_by_date_toggles_direction(self, scanned):
        scanned._sort_by("date")
        assert scanned.tree.get_children() == ("1", "2", "3")
        scanned._sort_by("date")
        assert scanned.tree.get_children() == ("3", "2", "1")

    def test_details_follow_the_selection(self, scanned):
        scanned.tree.selection_set("2")
        scanned._show_details()
        assert scanned.detail_subject._raw == "Weekly digest"
        assert scanned.detail_toggle.winfo_manager() == "pack"

    def test_processed_messages_leave_the_list(self, scanned):
        scanned.core.messages = [m for m in MESSAGES if m.uid == "3"]
        scanned._process_finished((2, [], "تم نقل 2 رسالة"))
        assert set(scanned.rows) == {"3"}
        assert scanned.checked == set()

    def test_disconnect_returns_to_login_and_clears_results(self, scanned):
        scanned._disconnect()
        assert scanned.rows == {}
        assert not scanned.core.is_connected
        assert scanned.connect_btn.enabled


def test_cancel_only_applies_while_scanning(app):
    app._cancel_scan()
    assert not app.cancel_event.is_set()
    app.scanning = True
    app._cancel_scan()
    assert app.cancel_event.is_set()
