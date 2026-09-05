"""الواجهة الرسومية (Tkinter). لا تحوي منطق بريد - كله في :mod:`core`."""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from . import __author__, __copyright__, __email__, __github__, __title__, __version__
from .core import REQUESTS_AVAILABLE, EmailCleanerCore
from .models import DeleteMode, EmailMessage, UnsubscribeMethod
from .netsafe import describe_targets
from .parsing import safe_int
from .rules import InvalidAddressError, split_address

# ألوان مفحوصة مقابل WCAG AA (4.5:1 للنص العادي).
# اللوحة السابقة كانت تضع نصًا أبيض على أخضر فاتح بنسبة 2.0:1.
COLORS = {
    "bg": "#1a1a2e",
    "fg": "#eaeaea",
    "muted": "#9aa0b4",  # 6.6:1 على الخلفية
    "muted_dim": "#8b91a6",  # 5.5:1 على الخلفية
    "accent": "#0f3460",
    "entry_bg": "#16213e",
    "row_alt": "#202844",
    "danger": "#c1123a",  # مع أبيض: 6.2:1 (كان #e94560 بـ 3.8:1)
    "danger_fg": "#ffffff",
    "success": "#00d26a",
    "success_fg": "#0d2818",  # نص داكن: 7.8:1 (كان أبيض بـ 2.0:1)
    "info": "#17a2b8",
    "info_fg": "#04252b",  # نص داكن: 5.3:1 (كان أبيض بـ 3.0:1)
    "neutral": "#0f3460",
    "neutral_fg": "#ffffff",  # 12.5:1
}

UI_FONT_CANDIDATES = (
    "Segoe UI",
    "Noto Sans Arabic",
    "Tahoma",
    "Dubai",
    "Geeza Pro",
    "DejaVu Sans",
)
# Consolas بلا محارف عربية إطلاقًا، فلا يصلح لصندوق يعرض نصًا عربيًا.
MONO_FONT_CANDIDATES = (
    "Cascadia Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Menlo",
    "Courier New",
)

CHECKED, UNCHECKED = "☑", "☐"
COLUMNS = ("score", "date", "subject", "sender", "check")


def pick_font(root: tk.Misc, candidates: tuple[str, ...], fallback: str) -> str:
    """أول خط متوفر فعليًا على الجهاز.

    ``Segoe UI`` و``Consolas`` خطّان خاصان بويندوز؛ بلا بديل تسقط الواجهة على
    خط عشوائي خارجه، وتظهر العربية بمحارف مربّعة في صندوق النتائج.
    """
    from tkinter import font as tkfont

    available = {name.lower() for name in tkfont.families(root)}
    for name in candidates:
        if name.lower() in available:
            return name
    return fallback


class EmailCleanerGUI:
    """نافذة التطبيق."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"{__title__} v{__version__}")
        self.root.geometry("1000x760")
        self.root.minsize(880, 640)
        self.root.configure(bg=COLORS["bg"])

        self.ui_font = pick_font(self.root, UI_FONT_CANDIDATES, "TkDefaultFont")
        self.mono_font = pick_font(self.root, MONO_FONT_CANDIDATES, "TkFixedFont")

        self.core = EmailCleanerCore()
        self.busy = False
        self.checked: set[str] = set()
        self.rows: dict[str, EmailMessage] = {}

        self._setup_styles()
        self._build_widgets()
        self._bind_keys()
        self._refresh_buttons()

        self.email_entry.focus_set()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ------------------------------------------------------------- الأنماط

    def _setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        base = (self.ui_font, 10)

        style.configure("TFrame", background=COLORS["bg"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["fg"], font=base)
        style.configure(
            "Muted.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["muted"],
            font=(self.ui_font, 9),
        )
        style.configure(
            "Dim.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["muted_dim"],
            font=(self.ui_font, 9),
        )
        style.configure(
            "Title.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["fg"],
            font=(self.ui_font, 16, "bold"),
        )
        style.configure("TLabelframe", background=COLORS["bg"], bordercolor=COLORS["accent"])
        style.configure(
            "TLabelframe.Label",
            background=COLORS["bg"],
            foreground=COLORS["fg"],
            font=(self.ui_font, 10, "bold"),
        )
        style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["fg"], font=base)
        style.map("TCheckbutton", background=[("active", COLORS["bg"])])
        style.configure("TRadiobutton", background=COLORS["bg"], foreground=COLORS["fg"], font=base)
        style.map("TRadiobutton", background=[("active", COLORS["bg"])])
        style.configure(
            "TProgressbar",
            background=COLORS["success"],
            troughcolor=COLORS["accent"],
            bordercolor=COLORS["accent"],
        )
        style.configure("TNotebook", background=COLORS["bg"], bordercolor=COLORS["accent"])
        style.configure(
            "TNotebook.Tab",
            background=COLORS["accent"],
            foreground=COLORS["fg"],
            font=base,
            padding=(14, 6),
        )
        style.map("TNotebook.Tab", background=[("selected", COLORS["entry_bg"])])
        style.configure(
            "Treeview",
            background=COLORS["entry_bg"],
            fieldbackground=COLORS["entry_bg"],
            foreground=COLORS["fg"],
            rowheight=26,
            font=base,
        )
        style.configure(
            "Treeview.Heading",
            background=COLORS["accent"],
            foreground=COLORS["fg"],
            font=(self.ui_font, 10, "bold"),
        )
        style.map("Treeview", background=[("selected", COLORS["accent"])])

    def _action_button(
        self, parent: tk.Misc, text: str, command, tone: str, size: int = 11
    ) -> tk.Button:
        """مصنع أزرار - كان النمط نفسه مكررًا بثمانية معاملات في كل زر."""
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=COLORS[tone],
            fg=COLORS[f"{tone}_fg"],
            activebackground=COLORS[tone],
            activeforeground=COLORS[f"{tone}_fg"],
            font=(self.ui_font, size, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=7,
            cursor="hand2",
            disabledforeground=COLORS["muted_dim"],
        )

    # ------------------------------------------------------------- البناء

    def _build_widgets(self) -> None:
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text="🧹 أداة تنظيف البريد الإلكتروني", style="Title.TLabel").pack()
        ttk.Label(header, text="Email Cleaner Tool", style="Muted.TLabel").pack()

        self._build_login(main)
        self._build_settings(main)
        self._build_actions(main)
        self._build_progress(main)
        self._build_results(main)
        self._build_footer(main)

    def _build_login(self, parent: tk.Misc) -> None:
        frame = ttk.LabelFrame(parent, text=" 🔐 تسجيل الدخول ", padding=12)
        frame.pack(fill=tk.X, pady=(0, 10))

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text="📧 البريد:", width=14, anchor="e").pack(side=tk.RIGHT)
        self.email_var = tk.StringVar()
        self.email_entry = ttk.Entry(
            row, textvariable=self.email_var, font=(self.ui_font, 11), justify="left"
        )
        self.email_entry.pack(side=tk.RIGHT, padx=10, fill=tk.X, expand=True)

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text="🔑 كلمة المرور:", width=14, anchor="e").pack(side=tk.RIGHT)
        self.pass_var = tk.StringVar()
        self.pass_entry = ttk.Entry(
            row, textvariable=self.pass_var, show="●", font=(self.ui_font, 11), justify="left"
        )
        self.pass_entry.pack(side=tk.RIGHT, padx=10, fill=tk.X, expand=True)

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=(10, 0))
        # ترتيب RTL: أول إجراء يقع أقصى اليمين حيث تبدأ عين القارئ العربي.
        self.connect_btn = self._action_button(row, "🔗 اتصال", self._connect, "danger", size=10)
        self.connect_btn.pack(side=tk.RIGHT, padx=4)
        self.disconnect_btn = self._action_button(
            row, "🔌 قطع", self._disconnect, "neutral", size=10
        )
        self.disconnect_btn.pack(side=tk.RIGHT, padx=4)
        self.status_label = ttk.Label(row, text="⚪ غير متصل", style="Muted.TLabel")
        self.status_label.pack(side=tk.LEFT, padx=8)

    def _build_settings(self, parent: tk.Misc) -> None:
        frame = ttk.LabelFrame(parent, text=" ⚙️ الإعدادات ", padding=10)
        frame.pack(fill=tk.X, pady=(0, 10))

        row = ttk.Frame(frame)
        row.pack(fill=tk.X)
        ttk.Label(row, text="📅 الأيام:").pack(side=tk.RIGHT)
        self.days_var = tk.StringVar(value="30")
        ttk.Spinbox(
            row, from_=1, to=365, textvariable=self.days_var, width=6, font=(self.ui_font, 10)
        ).pack(side=tk.RIGHT, padx=(6, 20))
        ttk.Label(row, text="📊 الحد الأقصى:").pack(side=tk.RIGHT)
        self.limit_var = tk.StringVar(value="500")
        ttk.Spinbox(
            row,
            from_=50,
            to=2000,
            increment=50,
            textvariable=self.limit_var,
            width=6,
            font=(self.ui_font, 10),
        ).pack(side=tk.RIGHT, padx=6)

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(row, text="🗂️ الإجراء:").pack(side=tk.RIGHT)
        self.mode_var = tk.StringVar(value=DeleteMode.MOVE.value)
        ttk.Radiobutton(
            row,
            text="نقل إلى مجلد مراجعة (قابل للتراجع)",
            value=DeleteMode.MOVE.value,
            variable=self.mode_var,
        ).pack(side=tk.RIGHT, padx=(6, 16))
        ttk.Radiobutton(
            row, text="⚠️ حذف نهائي", value=DeleteMode.PERMANENT.value, variable=self.mode_var
        ).pack(side=tk.RIGHT)

    def _build_actions(self, parent: tk.Misc) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=8)
        self.scan_btn = self._action_button(row, "🔍 فحص", self._scan, "success")
        self.scan_btn.pack(side=tk.RIGHT, padx=3)
        self.process_btn = self._action_button(row, "🗂️ معالجة المحدد", self._process, "danger")
        self.process_btn.pack(side=tk.RIGHT, padx=3)
        self.unsub_btn = self._action_button(row, "🚫 إلغاء الاشتراك", self._unsubscribe, "info")
        self.unsub_btn.pack(side=tk.RIGHT, padx=3)
        self.export_btn = self._action_button(row, "📄 تصدير", self._export, "neutral")
        self.export_btn.pack(side=tk.RIGHT, padx=3)

        self.include_links_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="ضمّن الروابط في التصدير", variable=self.include_links_var).pack(
            side=tk.LEFT, padx=8
        )

    def _build_progress(self, parent: tk.Misc) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=4)
        self.progress_var = tk.IntVar(value=0)
        ttk.Progressbar(frame, variable=self.progress_var, maximum=100).pack(fill=tk.X)
        self.progress_label = ttk.Label(frame, text="", style="Muted.TLabel", anchor="e")
        self.progress_label.pack(fill=tk.X, pady=4)

    def _build_results(self, parent: tk.Misc) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        messages_tab = ttk.Frame(notebook, padding=8)
        notebook.add(messages_tab, text="  📋 الرسائل  ")

        toolbar = ttk.Frame(messages_tab)
        toolbar.pack(fill=tk.X, pady=(0, 6))
        self._action_button(
            toolbar, "تحديد الكل", lambda: self._set_all(True), "neutral", size=9
        ).pack(side=tk.RIGHT, padx=3)
        self._action_button(
            toolbar, "إلغاء التحديد", lambda: self._set_all(False), "neutral", size=9
        ).pack(side=tk.RIGHT, padx=3)
        self.selection_label = ttk.Label(toolbar, text="لم يُجرَ فحص بعد", style="Muted.TLabel")
        self.selection_label.pack(side=tk.LEFT, padx=8)

        # لا يوجد ترتيب أعمدة RTL أصيل في Treeview، فرتّبنا الأعمدة يدويًا
        # ليقرأها المستخدم من اليمين: ☑ | المرسل | الموضوع | التاريخ | الدرجة.
        self.tree = ttk.Treeview(
            messages_tab, columns=COLUMNS, show="headings", selectmode="extended"
        )
        headings = {
            "check": ("☑", 44, "center"),
            "sender": ("المرسل", 230, "e"),
            "subject": ("الموضوع", 340, "e"),
            "date": ("التاريخ", 150, "e"),
            "score": ("الدرجة", 70, "center"),
        }
        for column in COLUMNS:
            title, width, anchor = headings[column]
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor=anchor, stretch=(column == "subject"))
        self.tree.tag_configure("odd", background=COLORS["row_alt"])
        self.tree.tag_configure("high", foreground="#ffd479")

        scrollbar = ttk.Scrollbar(messages_tab, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_tree_space)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_reasons())

        log_tab = ttk.Frame(notebook, padding=8)
        notebook.add(log_tab, text="  📝 السجل  ")
        self.log_text = scrolledtext.ScrolledText(
            log_tab,
            wrap=tk.WORD,
            font=(self.mono_font, 10),
            bg=COLORS["entry_bg"],
            fg=COLORS["fg"],
            insertbackground=COLORS["fg"],
            height=10,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_footer(self, parent: tk.Misc) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X)
        self.detail_label = ttk.Label(frame, text="", style="Muted.TLabel", anchor="e")
        self.detail_label.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(frame, text=f"تطوير: {__author__} | {__email__}", style="Muted.TLabel").pack()
        ttk.Label(frame, text=__copyright__, style="Dim.TLabel").pack()

        links = ttk.Frame(frame)
        links.pack(pady=3)
        github = ttk.Label(links, text="GitHub", style="Muted.TLabel", cursor="hand2")
        github.pack(side=tk.RIGHT, padx=10)
        github.bind("<Button-1>", lambda _e: webbrowser.open(__github__))
        contact = ttk.Label(links, text="📧 تواصل", style="Muted.TLabel", cursor="hand2")
        contact.pack(side=tk.RIGHT, padx=10)
        contact.bind("<Button-1>", lambda _e: webbrowser.open(f"mailto:{__email__}"))

    def _bind_keys(self) -> None:
        self.pass_entry.bind("<Return>", lambda _e: self._connect())
        self.email_entry.bind("<Return>", lambda _e: self.pass_entry.focus_set())
        self.root.bind("<F5>", lambda _e: self._scan())
        self.root.bind("<Control-s>", lambda _e: self._export())
        self.root.bind("<Control-a>", lambda _e: self._set_all(True))

    # ------------------------------------------------------------- مساعدات

    def _log(self, message: str, clear: bool = False) -> None:
        if clear:
            self.log_text.delete(1.0, tk.END)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{stamp}] {message}\n")
        self.log_text.see(tk.END)

    def _progress(self, message: str, percent: int) -> None:
        self.progress_var.set(percent)
        self.progress_label.config(text=message)

    def _from_worker(self, message: str, percent: int) -> None:
        """جسر آمن من خيط العامل إلى حلقة أحداث Tk."""
        self.root.after(0, lambda: self._progress(message, percent))

    def _run_async(self, work, on_done) -> None:
        """يشغّل عملًا في خيط ويعيد الواجهة لحالتها مهما كانت النتيجة.

        النمط السابق كان يعطّل الزر ثم يعيد تفعيله في دالة النجاح فقط، فأي
        استثناء غير متوقع كان يترك الزر معطّلًا حتى إعادة تشغيل البرنامج.
        """
        self.busy = True
        self._refresh_buttons()

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._worker_failed(e))
                return
            self.root.after(0, lambda: self._worker_finished(result, on_done))

        threading.Thread(target=runner, daemon=True).start()

    def _worker_finished(self, result, on_done) -> None:
        try:
            on_done(result)
        finally:
            self.busy = False
            self._refresh_buttons()

    def _worker_failed(self, error: Exception) -> None:
        self.busy = False
        self._refresh_buttons()
        self._log(f"❌ خطأ غير متوقع: {error}")
        messagebox.showerror("خطأ", f"حدث خطأ غير متوقع:\n{error}")

    def _refresh_buttons(self) -> None:
        connected = self.core.is_connected
        idle = not self.busy
        has_checked = bool(self.checked)
        unsubscribable = any(
            self.rows[uid].method is not UnsubscribeMethod.NONE
            for uid in self.checked
            if uid in self.rows
        )

        def state(enabled: bool) -> str:
            return tk.NORMAL if enabled else tk.DISABLED

        self.connect_btn.config(state=state(idle and not connected))
        self.disconnect_btn.config(state=state(idle and connected))
        self.scan_btn.config(state=state(idle and connected))
        self.process_btn.config(state=state(idle and connected and has_checked))
        self.unsub_btn.config(state=state(idle and connected and unsubscribable))
        self.export_btn.config(state=state(idle and bool(self.rows)))
        entry_state = tk.DISABLED if connected else tk.NORMAL
        self.email_entry.config(state=entry_state)
        self.pass_entry.config(state=entry_state)

        if self.rows:
            self.selection_label.config(
                text=f"محدَّد {len(self.checked)} من {len(self.rows)} رسالة"
            )

    # ------------------------------------------------------------- الاتصال

    def _connect(self) -> None:
        if self.busy or self.core.is_connected:
            return
        address = self.email_var.get().strip()
        password = self.pass_var.get()

        try:
            split_address(address)
        except InvalidAddressError:
            messagebox.showwarning("تنبيه", "صيغة البريد غير صحيحة.\nمثال: name@example.com")
            return
        if not password:
            messagebox.showwarning("تنبيه", "أدخل كلمة المرور")
            return

        self._log("جاري الاتصال...", clear=True)
        self._progress("جاري الاتصال بالخادم...", 0)
        self._run_async(lambda: self.core.connect(address, password), self._connected)

    def _connected(self, result: tuple[bool, str]) -> None:
        success, message = result
        self._log(message)
        self._progress("" if success else "فشل الاتصال", 0)

        if success:
            # كلمة المرور لم تعد مطلوبة بعد تسجيل الدخول؛ نمسحها من الذاكرة.
            self.pass_var.set("")
            self.status_label.config(text="🟢 متصل", foreground=COLORS["success"])
            self._log("✅ اضغط «فحص» (أو F5) لبدء البحث")
        else:
            self.status_label.config(text="⚪ غير متصل", foreground=COLORS["muted"])
            self._log("💡 لـ Gmail أنشئ كلمة مرور تطبيق:")
            self._log("   https://myaccount.google.com/apppasswords")

    def _disconnect(self) -> None:
        if self.busy:
            return
        self.core.disconnect()
        self.pass_var.set("")
        self.status_label.config(text="⚪ غير متصل", foreground=COLORS["muted"])
        self._log("🔌 تم قطع الاتصال")
        self._refresh_buttons()

    # ------------------------------------------------------------- الفحص

    def _scan(self) -> None:
        if self.busy or not self.core.is_connected:
            return
        try:
            days = safe_int(self.days_var.get(), minimum=1, maximum=365, label="الأيام")
            limit = safe_int(self.limit_var.get(), minimum=50, maximum=2000, label="الحد الأقصى")
        except ValueError as exc:
            messagebox.showwarning("تنبيه", str(exc))
            return

        self._log(f"🔍 فحص آخر {days} يوم (حتى {limit} رسالة)...", clear=True)
        self._run_async(
            lambda: self.core.scan_inbox(days, limit, self._from_worker),
            self._scan_finished,
        )

    def _scan_finished(self, report) -> None:
        self._populate_tree(report.messages)
        for error in report.errors:
            self._log(f"⚠️ {error}")

        if report.skipped:
            self._log(f"⚠️ تعذّر تحليل {report.skipped} رسالة من {report.examined}")
        if not report.messages:
            self._log("📭 لا توجد رسائل تتجاوز عتبة الاشتباه")
            return

        auto = self.core.rules.auto_select_threshold
        self._log(f"📊 {len(report.messages)} رسالة مشتبهة من {report.examined} مفحوصة")
        self._log(f"✅ حُدِّدت تلقائيًا الرسائل بدرجة {auto} فأعلى — راجع الباقي بنفسك")
        self._log("\n📈 أكثر المرسلين:")
        for index, (sender, count) in enumerate(list(self.core.senders_summary().items())[:10], 1):
            self._log(f"   {index:2}. {sender}: {count}")

    def _populate_tree(self, messages: list[EmailMessage]) -> None:
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.checked.clear()

        auto = self.core.rules.auto_select_threshold
        for index, message in enumerate(messages):
            self.rows[message.uid] = message
            if message.score >= auto:
                self.checked.add(message.uid)
            tags = ["odd"] if index % 2 else []
            if message.score >= auto:
                tags.append("high")
            self.tree.insert(
                "",
                tk.END,
                iid=message.uid,
                tags=tuple(tags),
                values=(
                    message.score,
                    (message.date or "")[:31],
                    message.subject,
                    message.sender_email,
                    CHECKED if message.uid in self.checked else UNCHECKED,
                ),
            )
        self._refresh_buttons()

    # ------------------------------------------------------------- التحديد

    def _toggle(self, uid: str) -> None:
        if uid not in self.rows:
            return
        if uid in self.checked:
            self.checked.discard(uid)
        else:
            self.checked.add(uid)
        self.tree.set(uid, "check", CHECKED if uid in self.checked else UNCHECKED)
        self._refresh_buttons()

    def _on_tree_click(self, event: tk.Event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != f"#{COLUMNS.index('check') + 1}":
            return
        row = self.tree.identify_row(event.y)
        if row:
            self._toggle(row)

    def _on_tree_space(self, _event: tk.Event) -> str:
        for uid in self.tree.selection():
            self._toggle(uid)
        return "break"

    def _set_all(self, value: bool) -> None:
        self.checked = set(self.rows) if value else set()
        for uid in self.rows:
            self.tree.set(uid, "check", CHECKED if value else UNCHECKED)
        self._refresh_buttons()

    def _show_reasons(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        message = self.rows.get(selection[0])
        if message:
            reasons = "، ".join(message.reasons) or "بلا أسباب"
            self.detail_label.config(text=f"الدرجة {message.score} — {reasons}")

    def _checked_messages(self) -> list[EmailMessage]:
        return [self.rows[uid] for uid in self.rows if uid in self.checked]

    # ------------------------------------------------------- النقل والحذف

    def _process(self) -> None:
        if self.busy:
            return
        selected = self._checked_messages()
        if not selected:
            messagebox.showinfo("معلومة", "لم تحدد أي رسالة")
            return

        mode = DeleteMode(self.mode_var.get())
        if mode is DeleteMode.PERMANENT:
            question = (
                f"سيتم حذف {len(selected)} رسالة من INBOX نهائيًا.\n"
                "لا يمكن التراجع عن هذه العملية.\n\nهل تريد المتابعة؟"
            )
        else:
            question = (
                f"سيتم نقل {len(selected)} رسالة من INBOX إلى مجلد "
                f"«{self.core.review_folder_name()}».\n"
                "تبقى الرسائل موجودة ويمكنك إرجاعها.\n\nهل تريد المتابعة؟"
            )
        if not messagebox.askyesno("تأكيد", question, icon="warning"):
            return

        self._log(f"\n🗂️ جاري معالجة {len(selected)} رسالة...")
        self._run_async(
            lambda: self.core.process_messages(selected, mode, self._from_worker),
            self._process_finished,
        )

    def _process_finished(self, result) -> None:
        _processed, errors, summary = result
        self._log(f"✅ {summary}")
        for error in errors[:10]:
            self._log(f"   ⚠️ {error}")
        if len(errors) > 10:
            self._log(f"   ⚠️ و{len(errors) - 10} خطأ آخر")
        self._populate_tree(self.core.messages)
        messagebox.showinfo("اكتمل", summary)

    # -------------------------------------------------- إلغاء الاشتراك

    def _unsubscribe(self) -> None:
        if self.busy:
            return
        selected = [m for m in self._checked_messages() if m.method is not UnsubscribeMethod.NONE]
        if not selected:
            messagebox.showinfo("معلومة", "لا توجد رسائل محددة فيها وسيلة إلغاء اشتراك")
            return

        one_click = [m for m in selected if m.method is UnsubscribeMethod.ONE_CLICK]
        mailto = [m for m in selected if m.method is UnsubscribeMethod.MAILTO]
        manual = [m for m in selected if m.method is UnsubscribeMethod.MANUAL]

        # المستخدم يرى النطاقات التي ستُزار قبل أن يُرسَل أي طلب.
        domains = describe_targets([m.unsubscribe_link or "" for m in one_click])
        lines = [f"سيُنفَّذ إلغاء الاشتراك لـ {len(selected)} مرسل:", ""]
        if one_click:
            lines.append(f"• {len(one_click)} عبر نقرة واحدة (RFC 8058) — نطاقات:")
            lines.extend(f"    {domain}" for domain in domains[:10])
            if len(domains) > 10:
                lines.append(f"    و{len(domains) - 10} نطاقًا آخر")
        if mailto:
            lines.append(f"• {len(mailto)} عبر رسالة بريدية (لا تكشف عنوانك)")
        if manual:
            lines.append(f"• {len(manual)} تحتاج تأكيدًا يدويًا — لن نزور روابطها")
        lines.append("")
        lines.append("هل تريد المتابعة؟")

        if not messagebox.askyesno("تأكيد إلغاء الاشتراك", "\n".join(lines)):
            return

        password = None
        if mailto:
            password = simpledialog.askstring(
                "كلمة مرور الإرسال",
                f"لإرسال {len(mailto)} رسالة إلغاء اشتراك عبر SMTP،\n"
                "أدخل كلمة مرور التطبيق (لن تُحفظ):",
                show="●",
                parent=self.root,
            )
            if not password:
                self._log("↩️ تم تخطي رسائل mailto (لم تُدخل كلمة المرور)")

        if one_click and not REQUESTS_AVAILABLE:
            messagebox.showwarning("تنبيه", "مكتبة requests غير مثبتة:\npip install requests")

        self._log(f"\n🚫 إلغاء الاشتراك من {len(selected)} مرسل...")
        self._run_async(
            lambda: self.core.unsubscribe(
                selected, smtp_password=password, callback=self._from_worker
            ),
            self._unsubscribe_finished,
        )

    def _unsubscribe_finished(self, outcomes) -> None:
        succeeded = sum(1 for outcome in outcomes if outcome.ok)
        manual = [outcome for outcome in outcomes if outcome.method is UnsubscribeMethod.MANUAL]

        self._log(f"\n{'=' * 50}")
        self._log("📊 نتائج إلغاء الاشتراك:")
        for outcome in outcomes:
            mark = "✅" if outcome.ok else "⚠️"
            self._log(f"   {mark} {outcome.sender_email[:40]}: {outcome.detail}")

        if manual:
            self._log("\n🔗 روابط تحتاج فتحًا يدويًا:")
            for outcome in manual:
                self._log(f"   {outcome.sender_email}: {outcome.link or '—'}")

        self._log(f"\n✅ مؤكد: {succeeded} | ⚠️ يحتاج متابعة: {len(outcomes) - succeeded}")
        messagebox.showinfo(
            "اكتمل",
            f"✅ نجح مؤكدًا: {succeeded}\n"
            f"⚠️ يحتاج متابعة يدوية: {len(outcomes) - succeeded}\n\n"
            "راجع تبويب السجل للروابط اليدوية.",
        )

    # ------------------------------------------------------------- التصدير

    def _export(self) -> None:
        if self.busy or not self.rows:
            return
        include_links = self.include_links_var.get()
        if include_links and not messagebox.askyesno(
            "تحذير",
            "روابط إلغاء الاشتراك قد تعمل كرموز وصول لحسابك لدى المرسلين.\n"
            "لا تضع الملف في مجلد مزامَن ولا ترفعه.\n\nهل تريد تضمينها؟",
            icon="warning",
        ):
            return

        # المعامل الصحيح هو initialfile؛ استخدام initialfilename يرفع TclError.
        filepath = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"email_report_{datetime.now():%Y%m%d_%H%M}.json",
        )
        if not filepath:
            return

        try:
            count = self.core.export_results(filepath, include_links=include_links)
        except OSError as exc:
            self._log(f"❌ فشل التصدير: {exc}")
            messagebox.showerror("خطأ", f"تعذّر حفظ الملف:\n{exc}")
            return

        self._log(f"\n📄 تم التصدير إلى: {filepath}")
        messagebox.showinfo("نجاح", f"تم التصدير ({count} عنصر)")

    # ------------------------------------------------------------- الإغلاق

    def _on_closing(self) -> None:
        self.core.disconnect()
        self.pass_var.set("")
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    EmailCleanerGUI().run()
