"""الواجهة الرسومية (Tkinter). لا تحوي منطق بريد - كله في :mod:`core`.

شاشتان: تسجيل دخول في بطاقة وسطية، ثم مساحة عمل فيها شريط أدوات وملخص
بالأرقام وقائمة مراجعة مع لوحة تفاصيل وشريط إجراءات ثابت أسفل النافذة.
الألوان والخطوط من :mod:`theme`، والعناصر المرسومة من :mod:`widgets`.
"""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, ttk
from tkinter import font as tkfont

from . import __copyright__, __github__, __version__, textdir
from .core import REQUESTS_AVAILABLE, REVIEW_FOLDER_PARTS, EmailCleanerCore
from .display import (
    METHOD_LABELS,
    format_date,
    format_full_date,
    matches,
    score_level,
    sort_messages,
)
from .models import DeleteMode, EmailMessage, ScanReport, UnsubscribeMethod
from .netsafe import describe_targets
from .parsing import parse_mailto, safe_int
from .rules import InvalidAddressError, split_address
from .textdir import RLM, visual
from .theme import COLORS, SPACE, Theme
from .widgets import (
    Button,
    Card,
    Dialog,
    ProgressBar,
    SegmentedControl,
    StatTile,
    rounded_rect,
)

APP_NAME = "منظّف البريد"
APP_PASSWORD_HELP = "https://myaccount.google.com/apppasswords"

CHECKED, UNCHECKED = "☑", "☐"
# Treeview لا يعرف RTL، فتُرتَّب الأعمدة من اليسار ليقرأها المستخدم من اليمين:
# ☑ | المرسل | الموضوع | التاريخ | الدرجة
COLUMNS = ("score", "date", "subject", "sender", "check")
HEADINGS = {
    "check": ("", 44, "center", False),
    "sender": ("المرسل", 170, "e", False),
    "subject": ("الموضوع", 240, "e", True),
    "date": ("التاريخ", 100, "e", False),
    "score": ("الدرجة", 70, "center", False),
}
SORTABLE = ("sender", "subject", "date", "score")
DETAILS_WIDTH = 300
COMPACT_HEIGHT = 720  # تحت هذا الارتفاع تُخفى بطاقات الأرقام


class WrapLabel(tk.Label):
    """نص متعدد الأسطر يُلَف يدويًا قبل تحويله لترتيب العرض.

    لف Tk الآلي يعمل على النص بعد التحويل، فيضع آخر الجملة في السطر الأول
    على لينكس. هنا نلف بالكلمات المنطقية ثم نحوّل كل سطر على حدة.
    """

    def __init__(self, parent: tk.Misc, width: int, **kwargs) -> None:
        super().__init__(parent, justify=tk.RIGHT, anchor="ne", **kwargs)
        self._width = width
        self._raw = ""

    def set(self, text: str) -> None:
        self._raw = text
        font = tkfont.Font(font=self.cget("font"))
        lines: list[str] = []
        for paragraph in text.split("\n"):
            line = ""
            for word in paragraph.split(" "):
                candidate = f"{line} {word}".strip()
                if line and font.measure(visual(candidate)) > self._width:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
            lines.append(line)
        self.configure(text="\n".join(visual(line) for line in lines))


class EmailCleanerGUI:
    """نافذة التطبيق."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} — Email Cleaner {__version__}")
        self.root.geometry("1180x780")
        self.root.minsize(980, 640)
        self.root.configure(bg=COLORS["bg"])

        self.shaping_ok = textdir.configure(self.root.tk.call("tk", "windowingsystem"))
        self.theme = Theme(self.root)
        self.theme.apply()

        self.core = EmailCleanerCore()
        self.busy = False
        self.scanning = False
        self.cancel_event = threading.Event()
        self.report = ScanReport()
        self.checked: set[str] = set()
        self.rows: dict[str, EmailMessage] = {}
        self.sort_column = "score"
        self.sort_descending = True
        self.row_font = tkfont.Font(font=self.theme.font("body"))

        self._build_login_view()
        self._build_main_view()
        self._bind_keys()
        self._show_login()

        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.bind("<Configure>", self._on_window_resize, add="+")

    # ================================================================ أدوات

    def _label(self, parent: tk.Misc, text: str, style: str = "TLabel", **kwargs) -> ttk.Label:
        return ttk.Label(parent, text=visual(text), style=style, **kwargs)

    def _plain(
        self, parent: tk.Misc, text: str, *, bg: str, fg: str = "text", role: str = "body", **kw
    ) -> tk.Label:
        return tk.Label(
            parent,
            text=visual(text),
            bg=COLORS[bg],
            fg=COLORS[fg],
            font=self.theme.font(role),
            **kw,
        )

    def _logo(self, parent: tk.Misc, bg: str, size: int = 36) -> tk.Canvas:
        """علامة التطبيق: مربع مستدير بلون الإجراء وظرف مبسّط."""
        canvas = tk.Canvas(parent, width=size, height=size, bg=COLORS[bg], highlightthickness=0)
        rounded_rect(canvas, 0, 0, size, size, size // 4, fill=COLORS["accent"], outline="")
        m = size * 0.24
        top, bottom = size * 0.32, size * 0.68
        canvas.create_rectangle(m, top, size - m, bottom, outline=COLORS["on_accent"], width=2)
        canvas.create_line(
            m, top, size / 2, size / 2, size - m, top, fill=COLORS["on_accent"], width=2
        )
        return canvas

    # ================================================================ شاشة الدخول

    def _build_login_view(self) -> None:
        self.login_view = tk.Frame(self.root, bg=COLORS["bg"])
        holder = tk.Frame(self.login_view, bg=COLORS["bg"])
        holder.place(relx=0.5, rely=0.46, anchor="center")

        card = Card(holder, padding=SPACE["xxl"])
        card.pack()
        inner_width = 380

        brand = tk.Frame(card, bg=COLORS["surface"])
        brand.pack(fill=tk.X)
        self._logo(brand, "surface", 44).pack(side=tk.RIGHT)
        titles = tk.Frame(brand, bg=COLORS["surface"])
        titles.pack(side=tk.RIGHT, padx=(0, SPACE["md"]))
        self._plain(titles, APP_NAME, bg="surface", role="display", anchor="e").pack(fill=tk.X)
        self._plain(
            titles,
            "راجع رسائلك الدعائية ونظّفها — بقرارك أنت",
            bg="surface",
            fg="muted",
            role="caption",
            anchor="e",
        ).pack(fill=tk.X)

        form = tk.Frame(card, bg=COLORS["surface"], width=inner_width)
        form.pack(fill=tk.X, pady=(SPACE["xl"], 0))

        self._plain(form, "البريد الإلكتروني", bg="surface", role="body_strong", anchor="e").pack(
            fill=tk.X
        )
        self.email_var = tk.StringVar()
        self.email_entry = ttk.Entry(
            form, textvariable=self.email_var, justify="left", font=self.theme.font("body")
        )
        self.email_entry.pack(fill=tk.X, pady=(SPACE["xs"], 0), ipady=2)
        self.server_hint = self._plain(form, "", bg="surface", fg="subtle", role="caption")
        self.server_hint.configure(anchor="e")
        self.server_hint.pack(fill=tk.X, pady=(SPACE["xs"], SPACE["md"]))
        self.email_var.trace_add("write", lambda *_: self._update_server_hint())

        label_row = tk.Frame(form, bg=COLORS["surface"])
        label_row.pack(fill=tk.X)
        self._plain(label_row, "كلمة مرور التطبيق", bg="surface", role="body_strong").pack(
            side=tk.RIGHT
        )
        self.reveal_btn = Button(
            label_row,
            self.theme,
            "إظهار",
            self._toggle_password,
            variant="ghost",
            size="sm",
            surface="surface",
        )
        self.reveal_btn.pack(side=tk.LEFT)
        self.pass_var = tk.StringVar()
        self.pass_entry = ttk.Entry(
            form, textvariable=self.pass_var, show="•", justify="left", font=self.theme.font("body")
        )
        self.pass_entry.pack(fill=tk.X, pady=(SPACE["xs"], 0), ipady=2)

        self.login_error = WrapLabel(
            form,
            inner_width,
            bg=COLORS["surface"],
            fg=COLORS["danger_text"],
            font=self.theme.font("caption"),
        )
        self.login_error.pack(fill=tk.X, pady=(SPACE["sm"], 0))

        self.connect_btn = Button(
            form,
            self.theme,
            "اتصال",
            self._connect,
            size="lg",
            surface="surface",
        )
        self.connect_btn.pack(fill=tk.X, pady=(SPACE["md"], 0))

        help_link = self._plain(
            form,
            "Gmail وYahoo يتطلبان «كلمة مرور تطبيق» — كيف أنشئها؟",
            bg="surface",
            fg="accent_text",
            role="caption",
            cursor="hand2",
        )
        help_link.pack(pady=(SPACE["md"], 0))
        help_link.bind("<Button-1>", lambda _e: webbrowser.open(APP_PASSWORD_HELP))

        self._plain(
            holder,
            "لا تُحفظ كلمة المرور ولا تُكتب على القرص • الاتصال مشفّر",
            bg="bg",
            fg="subtle",
            role="caption",
        ).pack(pady=(SPACE["lg"], 0))
        footer = self._plain(
            holder,
            f"{__copyright__} • v{__version__} • GitHub",
            bg="bg",
            fg="subtle",
            role="caption",
            cursor="hand2",
        )
        footer.pack(pady=(SPACE["xs"], 0))
        footer.bind("<Button-1>", lambda _e: webbrowser.open(__github__))

    def _update_server_hint(self) -> None:
        address = self.email_var.get().strip()
        try:
            host, port = self.core.rules.imap_server(address)
        except InvalidAddressError:
            text = ""
        else:
            text = f"الخادم: {host}" if port == 993 else f"الخادم: {host} (منفذ {port})"
        self.server_hint.configure(text=visual(text))

    def _toggle_password(self) -> None:
        hidden = self.pass_entry.cget("show") != ""
        self.pass_entry.configure(show="" if hidden else "•")
        self.reveal_btn.set_text("إخفاء" if hidden else "إظهار")

    # ================================================================ مساحة العمل

    def _build_main_view(self) -> None:
        self.main_view = tk.Frame(self.root, bg=COLORS["bg"])
        self._build_header(self.main_view)
        self._build_action_bar(self.main_view)  # قبل المحتوى ليبقى ثابتًا أسفل النافذة

        body = tk.Frame(self.main_view, bg=COLORS["bg"], padx=SPACE["xl"], pady=SPACE["lg"])
        body.pack(fill=tk.BOTH, expand=True)
        self._build_toolbar(body)
        self._build_stats(body)
        self._build_log(body)
        self._build_content(body)

    def _build_header(self, parent: tk.Misc) -> None:
        bar = tk.Frame(
            parent,
            bg=COLORS["surface"],
            padx=SPACE["xl"],
            pady=SPACE["md"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        bar.pack(fill=tk.X)
        self._logo(bar, "surface", 30).pack(side=tk.RIGHT)
        self._plain(bar, APP_NAME, bg="surface", role="title").pack(
            side=tk.RIGHT, padx=(0, SPACE["sm"])
        )

        self.disconnect_btn = Button(
            bar,
            self.theme,
            "قطع الاتصال",
            self._disconnect,
            variant="secondary",
            size="sm",
            surface="surface",
        )
        self.disconnect_btn.pack(side=tk.LEFT)
        self.log_btn = Button(
            bar,
            self.theme,
            "السجل",
            self._toggle_log,
            variant="ghost",
            size="sm",
            surface="surface",
        )
        self.log_btn.pack(side=tk.LEFT, padx=(SPACE["sm"], 0))

        chip = tk.Frame(bar, bg=COLORS["raised"], padx=SPACE["md"], pady=SPACE["xs"])
        chip.pack(side=tk.LEFT, padx=(SPACE["lg"], 0))
        tk.Label(
            chip,
            text="●",
            bg=COLORS["raised"],
            fg=COLORS["success"],
            font=self.theme.font("caption"),
        ).pack(side=tk.RIGHT)
        self.account_label = tk.Label(
            chip, text="", bg=COLORS["raised"], fg=COLORS["text"], font=self.theme.font("caption")
        )
        self.account_label.pack(side=tk.RIGHT, padx=(0, SPACE["xs"]))

    def _build_toolbar(self, parent: tk.Misc) -> None:
        card = Card(parent, padding=SPACE["md"])
        self.toolbar_card = card
        card.pack(fill=tk.X)
        row = tk.Frame(card, bg=COLORS["surface"])
        row.pack(fill=tk.X)

        def spin(var: tk.StringVar, low: int, high: int, step: int) -> ttk.Spinbox:
            return ttk.Spinbox(
                row,
                from_=low,
                to=high,
                increment=step,
                textvariable=var,
                width=6,
                justify="center",
                font=self.theme.font("body"),
            )

        self.days_var = tk.StringVar(value="30")
        self.limit_var = tk.StringVar(value="500")
        self._plain(row, "افحص رسائل آخر", bg="surface").pack(side=tk.RIGHT)
        spin(self.days_var, 1, 365, 1).pack(side=tk.RIGHT, padx=SPACE["sm"])
        self._plain(row, "يومًا، بحد أقصى", bg="surface").pack(side=tk.RIGHT)
        spin(self.limit_var, 50, 2000, 50).pack(side=tk.RIGHT, padx=SPACE["sm"])
        self._plain(row, "رسالة", bg="surface").pack(side=tk.RIGHT)

        self.scan_btn = Button(row, self.theme, "فحص الصندوق  F5", self._scan, surface="surface")
        self.scan_btn.pack(side=tk.LEFT)
        self.cancel_btn = Button(
            row,
            self.theme,
            "إيقاف  Esc",
            self._cancel_scan,
            variant="secondary",
            surface="surface",
        )

        # الحالة في المساحة الفارغة بين الحقول والزر، والتقدم خط رفيع أسفل البطاقة.
        self.status_label = self._plain(row, "", bg="surface", fg="muted", role="caption")
        self.status_label.configure(anchor="e")
        self.status_label.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=SPACE["lg"])
        self.progress_var = tk.IntVar(value=0)
        ProgressBar(card, self.progress_var).pack(fill=tk.X, pady=(SPACE["md"], 0))

    def _build_stats(self, parent: tk.Misc) -> None:
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(fill=tk.X, pady=(SPACE["lg"], 0))
        self.stats_row = row
        specs = (
            ("examined", "رسالة مفحوصة", "text"),
            ("suspected", "مشتبهة", "warning"),
            ("checked", "محدَّدة للمعالجة", "accent_text"),
            ("senders", "مرسل مختلف", "text"),
        )
        self.stats: dict[str, StatTile] = {}
        for index, (key, label, tone) in enumerate(reversed(specs)):
            tile = StatTile(row, self.theme, label, tone)
            tile.grid(
                row=0, column=index, sticky="nsew", padx=(0 if index == 0 else SPACE["md"], 0)
            )
            row.columnconfigure(index, weight=1, uniform="stat")
            self.stats[key] = tile

    def _build_content(self, parent: tk.Misc) -> None:
        content = tk.Frame(parent, bg=COLORS["bg"])
        content.pack(fill=tk.BOTH, expand=True, pady=(SPACE["lg"], 0))
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        # ---- القائمة (يمين)
        list_card = Card(content, padding=SPACE["md"])
        list_card.grid(row=0, column=1, sticky="nsew")

        head = tk.Frame(list_card, bg=COLORS["surface"])
        head.pack(fill=tk.X, pady=(0, SPACE["sm"]))
        self._plain(head, "الرسائل المشتبهة", bg="surface", role="heading").pack(side=tk.RIGHT)
        self.search_var = tk.StringVar()
        search = ttk.Entry(head, textvariable=self.search_var, width=28, justify="right")
        search.pack(side=tk.RIGHT, padx=(SPACE["lg"], 0))
        self.search_entry = search
        self._search_placeholder(search)
        self.search_var.trace_add("write", lambda *_: self._render_rows())

        self.select_none_btn = Button(
            head,
            self.theme,
            "إلغاء التحديد",
            lambda: self._set_all(False),
            variant="ghost",
            size="sm",
            surface="surface",
        )
        self.select_none_btn.pack(side=tk.LEFT)
        self.select_all_btn = Button(
            head,
            self.theme,
            "تحديد الكل",
            lambda: self._set_all(True),
            variant="ghost",
            size="sm",
            surface="surface",
        )
        self.select_all_btn.pack(side=tk.LEFT, padx=(SPACE["xs"], 0))

        table = tk.Frame(list_card, bg=COLORS["surface"])
        table.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(table, columns=COLUMNS, show="headings", selectmode="extended")
        for column in COLUMNS:
            title, width, anchor, stretch = HEADINGS[column]
            command = (lambda c=column: self._sort_by(c)) if column in SORTABLE else None
            self.tree.heading(column, text=visual(title), anchor=anchor, command=command or "")
            self.tree.column(column, width=width, minwidth=40, anchor=anchor, stretch=stretch)
        self.tree.tag_configure("unchecked", foreground=COLORS["muted"])
        scrollbar = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.empty_state = self._plain(
            table,
            "لم يُجرَ فحص بعد\nاختر المدة واضغط «فحص الصندوق» أو F5",
            bg="surface",
            fg="muted",
            justify=tk.CENTER,
        )

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Configure>", lambda e: self._fit_columns(e.width))
        # سحب الفاصل بين رأسي عمودين يغيّر العرض؛ نعيد قص النصوص بعده.
        self.tree.bind(
            "<ButtonRelease-1>",
            lambda e: self.tree.identify_region(e.x, e.y) == "separator"
            and self._refresh_row_text(),
            add="+",
        )
        self.tree.bind("<space>", self._on_tree_space)
        self.tree.bind("<Control-a>", lambda _e: (self._set_all(True), "break")[1])
        self.tree.bind("<Delete>", lambda _e: self._process())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_details())

        # ---- لوحة التفاصيل (يسار)
        details = Card(content, padding=SPACE["lg"])
        details.grid(row=0, column=0, sticky="nsew", padx=(0, SPACE["lg"]))
        details.configure(width=DETAILS_WIDTH)
        details.grid_propagate(False)
        details.pack_propagate(False)
        self.details = details
        self._plain(details, "تفاصيل الرسالة", bg="surface", fg="muted", role="caption").pack(
            anchor="e"
        )
        wrap = DETAILS_WIDTH - 2 * SPACE["lg"] - 4
        self.detail_subject = WrapLabel(
            details, wrap, bg=COLORS["surface"], fg=COLORS["text"], font=self.theme.font("heading")
        )
        self.detail_subject.pack(fill=tk.X, pady=(SPACE["sm"], 0))
        self.detail_sender = WrapLabel(
            details, wrap, bg=COLORS["surface"], fg=COLORS["muted"], font=self.theme.font("caption")
        )
        self.detail_sender.pack(fill=tk.X, pady=(SPACE["xs"], 0))

        self.detail_badge = tk.Label(
            details,
            text="",
            bg=COLORS["raised"],
            fg=COLORS["warning"],
            font=self.theme.font("body_strong"),
            padx=SPACE["md"],
            pady=SPACE["xs"],
        )
        self.detail_reasons_title = self._plain(
            details, "لماذا رُشِّحت؟", bg="surface", fg="muted", role="caption"
        )
        self.detail_reasons = WrapLabel(
            details, wrap, bg=COLORS["surface"], fg=COLORS["text"], font=self.theme.font("body")
        )
        self.detail_method_title = self._plain(
            details, "إلغاء الاشتراك", bg="surface", fg="muted", role="caption"
        )
        self.detail_method = WrapLabel(
            details, wrap, bg=COLORS["surface"], fg=COLORS["text"], font=self.theme.font("body")
        )
        self.detail_toggle = Button(
            details,
            self.theme,
            "",
            self._toggle_current,
            variant="secondary",
            surface="surface",
        )
        self.detail_placeholder = WrapLabel(
            details, wrap, bg=COLORS["surface"], fg=COLORS["subtle"], font=self.theme.font("body")
        )
        self.detail_placeholder.set("اختر رسالة من القائمة لترى سبب ترشيحها ووسيلة إلغاء الاشتراك.")
        self.detail_placeholder.pack(fill=tk.X, pady=(SPACE["md"], 0))

    def _build_log(self, parent: tk.Misc) -> None:
        self.log_card = Card(parent, padding=SPACE["sm"])
        self.log_text = tk.Text(
            self.log_card,
            height=7,
            wrap=tk.WORD,
            font=(self.theme.mono, 9),
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            insertbackground=COLORS["text"],
            relief=tk.FLAT,
            highlightthickness=0,
            padx=SPACE["sm"],
            pady=SPACE["xs"],
        )
        self.log_text.tag_configure("rtl", justify=tk.RIGHT)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_visible = False

    def _build_action_bar(self, parent: tk.Misc) -> None:
        bar = tk.Frame(
            parent,
            bg=COLORS["surface"],
            padx=SPACE["xl"],
            pady=SPACE["md"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.mode_var = tk.StringVar(value=DeleteMode.MOVE.value)
        SegmentedControl(
            bar,
            self.theme,
            [(DeleteMode.MOVE.value, "نقل للمراجعة"), (DeleteMode.PERMANENT.value, "حذف نهائي")],
            self.mode_var,
        ).pack(side=tk.RIGHT)
        self.mode_var.trace_add("write", lambda *_: self._refresh_state())

        self.process_btn = Button(bar, self.theme, "نقل المحدد", self._process, surface="surface")
        self.process_btn.pack(side=tk.RIGHT, padx=(0, SPACE["md"]))
        self.unsub_btn = Button(
            bar,
            self.theme,
            "إلغاء الاشتراك",
            self._unsubscribe,
            variant="secondary",
            surface="surface",
        )
        self.unsub_btn.pack(side=tk.RIGHT, padx=(0, SPACE["sm"]))

        self.export_btn = Button(
            bar,
            self.theme,
            "تصدير JSON",
            self._export,
            variant="ghost",
            surface="surface",
        )
        self.export_btn.pack(side=tk.LEFT)
        # ttk يضع المربع يسار النص دائمًا؛ في RTL مكانه يمين النص، فنفصلهما.
        self.include_links_var = tk.BooleanVar(value=False)
        links = tk.Frame(bar, bg=COLORS["surface"])
        links.pack(side=tk.LEFT, padx=(SPACE["md"], 0))
        ttk.Checkbutton(links, variable=self.include_links_var, style="Surface.TCheckbutton").pack(
            side=tk.RIGHT
        )
        caption = self._plain(links, "مع روابط الإلغاء", bg="surface", fg="muted", role="caption")
        caption.pack(side=tk.RIGHT)
        caption.bind(
            "<Button-1>", lambda _e: self.include_links_var.set(not self.include_links_var.get())
        )

    def _search_placeholder(self, entry: ttk.Entry) -> None:
        """نص إرشادي داخل حقل البحث يختفي عند التركيز."""
        hint = tk.Label(
            entry,
            text=visual("بحث بالمرسل أو الموضوع  (Ctrl+F)"),
            bg=COLORS["raised"],
            fg=COLORS["subtle"],
            font=self.theme.font("caption"),
            cursor="xterm",
        )

        def sync(*_args) -> None:
            if self.search_var.get() or self.root.focus_get() is entry:
                hint.place_forget()
            else:
                hint.place(relx=1.0, rely=0.5, x=-SPACE["sm"], anchor="e")

        hint.bind("<Button-1>", lambda _e: entry.focus_set())
        entry.bind("<FocusIn>", sync, add="+")
        entry.bind("<FocusOut>", sync, add="+")
        self.search_var.trace_add("write", sync)
        self.root.after_idle(sync)

    def _bind_keys(self) -> None:
        self.email_entry.bind("<Return>", lambda _e: self.pass_entry.focus_set())
        self.pass_entry.bind("<Return>", lambda _e: self._connect())
        self.root.bind("<F5>", lambda _e: self._scan())
        self.root.bind("<Escape>", lambda _e: self._cancel_scan())
        self.root.bind("<Control-s>", lambda _e: self._export())
        self.root.bind("<Control-f>", lambda _e: self._focus_search())
        self.root.bind("<Control-l>", lambda _e: self._toggle_log())

    # ================================================================ التنقل بين الشاشتين

    def _show_login(self) -> None:
        self.main_view.pack_forget()
        self.login_view.pack(fill=tk.BOTH, expand=True)
        self._refresh_state()
        self.email_entry.focus_set()

    def _show_main(self) -> None:
        self.login_view.pack_forget()
        self.main_view.pack(fill=tk.BOTH, expand=True)
        self.account_label.configure(text=self.core.account)
        self._refresh_state()
        self.scan_btn.focus_set()

    # ================================================================ مساعدات

    def _log(self, message: str, clear: bool = False) -> None:
        if clear:
            self.log_text.delete(1.0, tk.END)
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"{RLM}{message}" if textdir.has_arabic(message) else message
        self.log_text.insert(tk.END, visual(f"{line}  [{stamp}]") + "\n", "rtl")
        self.log_text.see(tk.END)

    def _status(self, message: str, percent: int | None = None, tone: str = "muted") -> None:
        if percent is not None:
            self.progress_var.set(percent)
        self.status_label.configure(text=visual(message), fg=COLORS[tone])

    def _from_worker(self, message: str, percent: int) -> None:
        """جسر آمن من خيط العامل إلى حلقة أحداث Tk."""
        self.root.after(0, lambda: self._status(message, percent))

    def _alert(self, title: str, body: str) -> None:
        Dialog(self.root, self.theme, title, body, confirm="حسنًا", cancel=None).show()

    def _run_async(self, work, on_done) -> None:
        """يشغّل عملًا في خيط ويعيد الواجهة لحالتها مهما كانت النتيجة."""
        self.busy = True
        self._refresh_state()

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
            self.scanning = False
            self._refresh_state()

    def _worker_failed(self, error: Exception) -> None:
        self.busy = False
        self.scanning = False
        self._refresh_state()
        self._log(f"خطأ غير متوقع: {error}")
        self._status(f"حدث خطأ غير متوقع: {error}", tone="danger_text")

    def _refresh_state(self) -> None:
        """يزامن كل الأزرار والعدادات مع الحالة - المصدر الوحيد لتفعيلها."""
        connected = self.core.is_connected
        idle = not self.busy
        selected = self._checked_messages()
        unsubscribable = [m for m in selected if m.method is not UnsubscribeMethod.NONE]
        permanent = self.mode_var.get() == DeleteMode.PERMANENT.value

        self.connect_btn.set_enabled(idle and not connected)
        self.connect_btn.set_text("جارٍ الاتصال…" if self.busy and not connected else "اتصال")
        self.disconnect_btn.set_enabled(idle and connected)
        self.scan_btn.set_enabled(idle and connected)
        self.scan_btn.set_text("جارٍ الفحص…" if self.scanning else "فحص الصندوق  F5")
        if self.scanning:
            self.cancel_btn.pack(side=tk.LEFT, padx=(0, SPACE["sm"]))
        else:
            self.cancel_btn.pack_forget()

        count = len(selected)
        if permanent:
            self.process_btn.set_variant("danger")
            self.process_btn.set_text(f"حذف {count} نهائيًا" if count else "حذف نهائي")
        else:
            self.process_btn.set_variant("primary")
            self.process_btn.set_text(f"نقل {count} للمراجعة" if count else "نقل المحدد")
        self.process_btn.set_enabled(idle and connected and count > 0)
        self.unsub_btn.set_text(
            f"إلغاء الاشتراك ({len(unsubscribable)})" if unsubscribable else "إلغاء الاشتراك"
        )
        self.unsub_btn.set_enabled(idle and connected and bool(unsubscribable))
        self.export_btn.set_enabled(idle and bool(self.rows))
        self.select_all_btn.set_enabled(bool(self.rows))
        self.select_none_btn.set_enabled(bool(self.checked))

        entry_state = tk.DISABLED if (connected or self.busy) else tk.NORMAL
        self.email_entry.configure(state=entry_state)
        self.pass_entry.configure(state=entry_state)

        senders = {m.sender_email for m in self.rows.values()}
        self.stats["examined"].set(
            self.report.examined if self.rows or self.report.examined else "—"
        )
        self.stats["suspected"].set(len(self.rows) if self.report.examined else "—")
        self.stats["checked"].set(count if self.rows else "—")
        self.stats["senders"].set(len(senders) if self.rows else "—")

    # ================================================================ الاتصال

    def _connect(self) -> None:
        if self.busy or self.core.is_connected:
            return
        address = self.email_var.get().strip()
        password = self.pass_var.get()

        try:
            split_address(address)
        except InvalidAddressError:
            self.login_error.set("صيغة البريد غير صحيحة. مثال: name@example.com")
            self.email_entry.focus_set()
            return
        if not password:
            self.login_error.set("أدخل كلمة مرور التطبيق.")
            self.pass_entry.focus_set()
            return

        self.login_error.set("")
        self._run_async(lambda: self.core.connect(address, password), self._connected)

    def _connected(self, result: tuple[bool, str]) -> None:
        success, message = result
        self._log(message)
        if not success:
            self.login_error.set(message)
            self.pass_entry.focus_set()
            return
        # كلمة المرور لم تعد مطلوبة بعد تسجيل الدخول؛ نفرّغ الحقل.
        self.pass_var.set("")
        self._show_main()
        self._status("متصل. اختر المدة ثم اضغط «فحص الصندوق».", 0)
        if not self.shaping_ok:
            hint = "لعرض العربية سليمة على لينكس ثبّت: pip install arabic-reshaper python-bidi"
            self._log(hint)
            self._status(hint, tone="warning")

    def _disconnect(self) -> None:
        if self.busy:
            return
        self.core.disconnect()
        self.pass_var.set("")
        self.report = ScanReport()
        self._populate([])
        self._log("تم قطع الاتصال")
        self._show_login()

    # ================================================================ الفحص

    def _scan(self) -> None:
        if self.busy or not self.core.is_connected:
            return
        try:
            days = safe_int(self.days_var.get(), minimum=1, maximum=365, label="الأيام")
            limit = safe_int(self.limit_var.get(), minimum=50, maximum=2000, label="الحد الأقصى")
        except ValueError as exc:
            self._status(str(exc), tone="danger_text")
            return

        self.cancel_event.clear()
        self.scanning = True
        self._log(f"فحص آخر {days} يومًا (حتى {limit} رسالة)...", clear=True)
        self._status("جارٍ البحث في صندوق الوارد…", 0)
        self._run_async(
            lambda: self.core.scan_inbox(
                days, limit, self._from_worker, should_cancel=self.cancel_event.is_set
            ),
            self._scan_finished,
        )

    def _cancel_scan(self) -> None:
        if self.scanning:
            self.cancel_event.set()
            self._status("جارٍ الإيقاف بعد الرسالة الحالية…")

    def _scan_finished(self, report: ScanReport) -> None:
        self.report = report
        self._populate(report.messages)
        for error in report.errors:
            self._log(error)
        if report.skipped:
            self._log(f"تعذّر تحليل {report.skipped} رسالة من {report.examined}")

        if report.errors and not report.examined:
            self._status(report.errors[0], tone="danger_text")
            return
        if not report.messages:
            self._status(f"لا توجد رسائل تتجاوز عتبة الاشتباه بين {report.examined} رسالة.", 100)
            return

        auto = self.core.rules.auto_select_threshold
        prefix = "أُوقف الفحص" if report.cancelled else "اكتمل الفحص"
        self._status(
            f"{prefix}: {len(report.messages)} رسالة مشتبهة من {report.examined}. "
            f"حُدِّد تلقائيًا ما درجته {auto} فأعلى — راجع الباقي بنفسك.",
            100,
            tone="success",
        )
        self._log("أكثر المرسلين:")
        for index, (sender, count) in enumerate(list(self.core.senders_summary().items())[:10], 1):
            self._log(f"{index}. {sender}: {count}")
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])

    # ================================================================ القائمة

    def _populate(self, messages: list[EmailMessage]) -> None:
        self.rows = {m.uid: m for m in messages}
        auto = self.core.rules.auto_select_threshold
        self.checked = {m.uid for m in messages if m.score >= auto}
        self._render_rows()

    def _visible_messages(self) -> list[EmailMessage]:
        query = self.search_var.get()
        filtered = [m for m in self.rows.values() if matches(m, query)]
        return sort_messages(filtered, self.sort_column, self.sort_descending)

    def _render_rows(self) -> None:
        selection = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        visible = self._visible_messages()
        for message in visible:
            self.tree.insert(
                "",
                tk.END,
                iid=message.uid,
                tags=() if message.uid in self.checked else ("unchecked",),
                values=self._row_values(message),
            )
        keep = [uid for uid in selection if self.tree.exists(uid)]
        if keep:
            self.tree.selection_set(keep)

        for column in SORTABLE:
            title = HEADINGS[column][0]
            if column == self.sort_column:
                title = f"{title} {'▼' if self.sort_descending else '▲'}"
            self.tree.heading(column, text=visual(title))

        if visible:
            self.empty_state.place_forget()
        else:
            if self.rows:
                text = "لا نتائج تطابق البحث"
            elif self.report.examined:
                text = "لا توجد رسائل مشتبهة\nصندوقك نظيف ضمن المدة المحددة"
            else:
                text = "لم يُجرَ فحص بعد\nاختر المدة واضغط «فحص الصندوق» أو F5"
            self.empty_state.configure(text=visual(text))
            self.empty_state.place(relx=0.5, rely=0.45, anchor="center")
        self._show_details()
        self._refresh_state()

    def _row_values(self, message: EmailMessage) -> tuple:
        return (
            message.score,
            visual(format_date(message.date)),
            self._fit(message.subject, "subject"),
            self._fit(message.sender or message.sender_email, "sender"),
            CHECKED if message.uid in self.checked else UNCHECKED,
        )

    def _fit(self, text: str, column: str) -> str:
        """يقص النص من نهايته المنطقية ويضيف «…» ليتسع للعمود.

        Tk يقص الخلية الطويلة من اليمين مهما كانت المحاذاة، فيضيع أول العنوان
        العربي (بعد تحويله لترتيب العرض) ويبقى آخره. القص المسبق يحفظ البداية.
        """
        room = int(self.tree.column(column, "width")) - 2 * SPACE["sm"]
        measure = self.row_font.measure
        shown = visual(text)
        if measure(shown) <= room:
            return shown
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if measure(visual(text[:mid].rstrip() + "…")) <= room:
                low = mid
            else:
                high = mid - 1
        return visual(text[:low].rstrip() + "…")

    def _refresh_row_text(self) -> None:
        for uid in self.tree.get_children():
            message = self.rows.get(uid)
            if message is not None:
                self.tree.item(uid, values=self._row_values(message))

    def _fit_columns(self, width: int) -> None:
        """عمود الموضوع يأخذ ما يتبقى، فلا يُدفع عمود ☑ خارج الجدول عند تضييق النافذة."""
        fixed = sum(int(self.tree.column(c, "width")) for c in COLUMNS if c != "subject")
        subject = max(120, width - fixed - 2)
        if subject != int(self.tree.column("subject", "width")):
            self.tree.column("subject", width=subject)
            self._refresh_row_text()

    def _on_window_resize(self, event: tk.Event) -> None:
        """على الشاشات القصيرة تُخفى بطاقات الأرقام لصالح القائمة والتفاصيل."""
        if event.widget is not self.root:
            return
        compact = event.height < COMPACT_HEIGHT
        if compact and self.stats_row.winfo_manager():
            self.stats_row.pack_forget()
        elif not compact and not self.stats_row.winfo_manager():
            self.stats_row.pack(fill=tk.X, pady=(SPACE["lg"], 0), after=self.toolbar_card)

    def _sort_by(self, column: str) -> None:
        if column == self.sort_column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column = column
            self.sort_descending = column in ("score", "date")
        self._render_rows()

    def _focus_search(self) -> None:
        if self.core.is_connected:
            self.search_entry.focus_set()

    # ================================================================ التحديد

    def _toggle(self, uid: str) -> None:
        if uid not in self.rows:
            return
        if uid in self.checked:
            self.checked.discard(uid)
        else:
            self.checked.add(uid)
        if self.tree.exists(uid):
            self.tree.set(uid, "check", CHECKED if uid in self.checked else UNCHECKED)
            self.tree.item(uid, tags=() if uid in self.checked else ("unchecked",))
        self._show_details()
        self._refresh_state()

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

    def _toggle_current(self) -> None:
        selection = self.tree.selection()
        if selection:
            self._toggle(selection[0])

    def _set_all(self, value: bool) -> None:
        """يطبَّق على الرسائل الظاهرة فقط، فيمكن تحديد نتائج بحث بعينها."""
        visible = {m.uid for m in self._visible_messages()}
        self.checked = self.checked | visible if value else self.checked - visible
        for uid in visible:
            self.tree.set(uid, "check", CHECKED if value else UNCHECKED)
            self.tree.item(uid, tags=() if value else ("unchecked",))
        self._show_details()
        self._refresh_state()

    def _checked_messages(self) -> list[EmailMessage]:
        return [m for uid, m in self.rows.items() if uid in self.checked]

    def _show_details(self) -> None:
        selection = self.tree.selection()
        message = self.rows.get(selection[0]) if selection else None
        detail_widgets = (
            self.detail_subject,
            self.detail_sender,
            self.detail_badge,
            self.detail_reasons_title,
            self.detail_reasons,
            self.detail_method_title,
            self.detail_method,
            self.detail_toggle,
        )
        if message is None:
            for widget in detail_widgets:
                widget.pack_forget()
            self.detail_placeholder.pack(fill=tk.X, pady=(SPACE["md"], 0))
            return

        self.detail_placeholder.pack_forget()
        for widget in detail_widgets:
            widget.pack_forget()
        auto = self.core.rules.auto_select_threshold
        self.detail_subject.set(message.subject)
        self.detail_sender.set(
            f"{message.sender} • {message.sender_email}\n{format_full_date(message.date)}"
        )
        self.detail_badge.configure(
            text=visual(f"الدرجة {message.score} • {score_level(message.score, auto)}"),
            fg=COLORS["warning"] if message.score >= auto else COLORS["accent_text"],
        )
        self.detail_reasons.set("\n".join(f"{RLM}• {reason}" for reason in message.reasons))
        self.detail_method.set(METHOD_LABELS[message.method])
        checked = message.uid in self.checked
        self.detail_toggle.set_text("استبعاد من المعالجة" if checked else "إضافة للمعالجة")

        # الزر أولًا: عند ضيق الارتفاع يقتطع pack من آخر ما حُزم، والزر أهم من النص.
        self.detail_toggle.pack(side=tk.BOTTOM, fill=tk.X, pady=(SPACE["sm"], 0))
        self.detail_subject.pack(fill=tk.X, pady=(SPACE["sm"], 0))
        self.detail_sender.pack(fill=tk.X, pady=(SPACE["xs"], 0))
        self.detail_badge.pack(anchor="e", pady=(SPACE["md"], 0))
        self.detail_reasons_title.pack(anchor="e", pady=(SPACE["md"], 0))
        self.detail_reasons.pack(fill=tk.X, pady=(SPACE["xs"], 0))
        self.detail_method_title.pack(anchor="e", pady=(SPACE["md"], 0))
        self.detail_method.pack(fill=tk.X, pady=(SPACE["xs"], 0))

    # ================================================================ النقل والحذف

    def _process(self) -> None:
        if self.busy or not self.core.is_connected:
            return
        selected = self._checked_messages()
        if not selected:
            return

        mode = DeleteMode(self.mode_var.get())
        top = sorted({m.sender_email for m in selected})[:6]
        details = [f"• {sender}" for sender in top]
        if len({m.sender_email for m in selected}) > len(top):
            details.append("• …")
        if mode is DeleteMode.PERMANENT:
            confirmed = Dialog(
                self.root,
                self.theme,
                f"حذف {len(selected)} رسالة نهائيًا؟",
                "ستُمحى الرسائل من الخادم ولا يمكن استرجاعها.\n"
                "إن لم تكن متأكدًا، اختر «نقل للمراجعة» بدلًا من ذلك.",
                confirm="حذف نهائي",
                danger=True,
                details=details,
            ).show()
        else:
            folder = "/".join(REVIEW_FOLDER_PARTS)
            confirmed = Dialog(
                self.root,
                self.theme,
                f"نقل {len(selected)} رسالة للمراجعة؟",
                f"ستُنقل من صندوق الوارد إلى مجلد «{folder}».\nتبقى موجودة ويمكنك إرجاعها متى شئت.",
                confirm="نقل",
                details=details,
            ).show()
        if not confirmed:
            return

        self._log(f"معالجة {len(selected)} رسالة...")
        self._status("جارٍ المعالجة…", 0)
        self._run_async(
            lambda: self.core.process_messages(selected, mode, self._from_worker),
            self._process_finished,
        )

    def _process_finished(self, result) -> None:
        _processed, errors, summary = result
        self._log(summary)
        for error in errors[:10]:
            self._log(f"   {error}")
        if len(errors) > 10:
            self._log(f"   و{len(errors) - 10} خطأ آخر")
        handled = set(self.rows) - {m.uid for m in self.core.messages}
        for uid in handled:
            self.rows.pop(uid, None)
            self.checked.discard(uid)
        self._render_rows()
        self._status(summary, 100, tone="danger_text" if errors else "success")

    # ================================================================ إلغاء الاشتراك

    def _unsubscribe(self) -> None:
        if self.busy:
            return
        selected = [m for m in self._checked_messages() if m.method is not UnsubscribeMethod.NONE]
        if not selected:
            return

        by_sender: dict[str, EmailMessage] = {}
        for message in selected:
            by_sender.setdefault(message.sender_email, message)
        unique = list(by_sender.values())
        one_click = [m for m in unique if m.method is UnsubscribeMethod.ONE_CLICK]
        mailto = [m for m in unique if m.method is UnsubscribeMethod.MAILTO]
        manual = [m for m in unique if m.method is UnsubscribeMethod.MANUAL]

        # المستخدم يرى إلى أين ستذهب الطلبات قبل أن يُرسَل أي شيء.
        details: list[str] = []
        if one_click:
            domains = describe_targets([m.unsubscribe_link or "" for m in one_click])
            details.append(f"نقرة واحدة ({len(one_click)}): {'، '.join(domains[:4])}")
        if mailto:
            recipients = [parse_mailto(m.unsubscribe_mailto or "")[0] for m in mailto]
            details.append(f"رسالة بريدية ({len(mailto)}) إلى: {'، '.join(recipients[:3])}")
        if manual:
            details.append(f"يدوي ({len(manual)}): تُعرض روابطها في السجل ولا تُزار")

        if not Dialog(
            self.root,
            self.theme,
            f"إلغاء الاشتراك من {len(unique)} مرسل؟",
            "تُستخدم أأمن وسيلة متاحة لكل مرسل، ولا يُزار أي رابط لا يجتاز فحص السلامة.",
            confirm="متابعة",
            details=details,
        ).show():
            return

        password = None
        if mailto:
            answer = Dialog(
                self.root,
                self.theme,
                "كلمة مرور الإرسال",
                f"لإرسال {len(mailto)} رسالة إلغاء اشتراك عبر SMTP أدخل كلمة مرور التطبيق.\n"
                "لن تُحفظ.",
                confirm="إرسال",
                prompt_secret=True,
            ).show()
            password = answer if isinstance(answer, str) else None
            if not password:
                self._log("تم تخطي رسائل mailto (لم تُدخل كلمة المرور)")

        if one_click and not REQUESTS_AVAILABLE:
            self._log("مكتبة requests غير مثبتة: pip install requests")

        self._log(f"إلغاء الاشتراك من {len(unique)} مرسل...")
        self._status("جارٍ إلغاء الاشتراك…", 0)
        self._run_async(
            lambda: self.core.unsubscribe(
                selected, smtp_password=password, callback=self._from_worker
            ),
            self._unsubscribe_finished,
        )

    def _unsubscribe_finished(self, outcomes) -> None:
        succeeded = sum(1 for outcome in outcomes if outcome.ok)
        manual = [outcome for outcome in outcomes if outcome.method is UnsubscribeMethod.MANUAL]

        self._log("نتائج إلغاء الاشتراك:")
        for outcome in outcomes:
            mark = "✓" if outcome.ok else "!"
            self._log(f"{mark} {outcome.sender_email[:40]}: {outcome.detail}")
        if manual:
            self._log("روابط تحتاج فتحًا يدويًا:")
            for outcome in manual:
                self._log(f"{outcome.sender_email}: {outcome.link or '—'}")
            if not self.log_visible:
                self._toggle_log()

        pending = len(outcomes) - succeeded
        tone = "success" if pending == 0 else "warning"
        self._status(f"نجح مؤكدًا: {succeeded} • يحتاج متابعة يدوية: {pending}", 100, tone=tone)

    # ================================================================ التصدير والسجل

    def _export(self) -> None:
        if self.busy or not self.rows:
            return
        include_links = self.include_links_var.get()
        if (
            include_links
            and not Dialog(
                self.root,
                self.theme,
                "تضمين روابط الإلغاء؟",
                "هذه الروابط قد تعمل كرموز وصول لحسابك لدى المرسلين.\n"
                "لا تضع الملف في مجلد مزامَن ولا ترفعه.",
                confirm="تضمين الروابط",
                danger=True,
            ).show()
        ):
            return

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
            self._log(f"فشل التصدير: {exc}")
            self._alert("تعذّر حفظ الملف", str(exc))
            return
        self._log(f"تم التصدير إلى: {filepath}")
        self._status(f"تم التصدير ({count} عنصر) إلى {filepath}", tone="success")

    def _toggle_log(self) -> None:
        if not self.core.is_connected:
            return
        if self.log_visible:
            self.log_card.pack_forget()
        else:
            anchor = self.stats_row if self.stats_row.winfo_manager() else self.toolbar_card
            self.log_card.pack(fill=tk.X, pady=(SPACE["lg"], 0), after=anchor)
        self.log_visible = not self.log_visible
        self.log_btn.set_text("إخفاء السجل" if self.log_visible else "السجل")

    # ================================================================ الإغلاق

    def _on_closing(self) -> None:
        self.cancel_event.set()
        self.core.disconnect()
        self.pass_var.set("")
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    EmailCleanerGUI().run()
