"""رموز التصميم (design tokens) وأنماط ttk - المصدر الوحيد للألوان والخطوط والمسافات.

لا يكتب أي ملف آخر لونًا أو مقاسًا بالأرقام مباشرةً؛ كل شيء يُقرأ من هنا،
فتغيير الهوية البصرية تعديلٌ في مكان واحد. أزواج التباين المعتمدة مفحوصة آليًا
في ``tests/test_theme.py`` مقابل WCAG AA (4.5:1 للنص العادي).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

COLORS = {
    # الأسطح - من الأعمق إلى الأعلى
    "bg": "#0e1117",
    "surface": "#161b22",
    "raised": "#1c2330",
    "border": "#2b3340",
    "selection": "#25304a",
    # النصوص
    "text": "#e6e9ef",
    "muted": "#9aa4b2",
    "subtle": "#7d8796",
    "on_accent": "#ffffff",
    # الإجراء الأساسي
    "accent": "#5865f2",
    "accent_hover": "#4752e0",
    "accent_pressed": "#3c46c8",
    "accent_text": "#8e98ff",
    # الإجراء الهدّام
    "danger": "#d63a52",
    "danger_hover": "#c22f47",
    "danger_pressed": "#a8273c",
    "danger_text": "#ff7a8c",
    # الحالات
    "success": "#3fb97a",
    "warning": "#e9b949",
}

# أزواج (نص، خلفية) يجب أن تتجاوز 4.5:1 - يتحقق منها الاختبار.
CONTRAST_PAIRS = (
    ("text", "bg"),
    ("text", "surface"),
    ("text", "raised"),
    ("text", "selection"),
    ("muted", "surface"),
    ("muted", "raised"),
    ("subtle", "surface"),
    ("on_accent", "accent"),
    ("on_accent", "accent_hover"),
    ("on_accent", "danger"),
    ("on_accent", "danger_hover"),
    ("accent_text", "surface"),
    ("danger_text", "surface"),
    ("success", "surface"),
    ("warning", "surface"),
)

# شبكة 4px: كل المسافات من هذه القيم فقط.
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = 8

# خطوط فيها محارف عربية كاملة، بترتيب الأفضلية لكل نظام.
# (Segoe UI Variable أحدث شكلًا لكنه بلا محارف عربية، فيُخلط مع خط احتياطي.)
UI_FONT_CANDIDATES = (
    "Segoe UI",
    "IBM Plex Sans Arabic",
    "SF Arabic",
    "Geeza Pro",
    "Noto Sans Arabic UI",
    "Noto Sans Arabic",
    "Tahoma",
    "DejaVu Sans",
)
# Consolas بلا محارف عربية إطلاقًا، فلا يصلح لسجل يعرض نصًا عربيًا.
MONO_FONT_CANDIDATES = (
    "Cascadia Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Menlo",
    "Courier New",
)

# (الحجم، الوزن) لكل دور طباعي.
TYPE_SCALE = {
    "display": (20, "bold"),
    "title": (14, "bold"),
    "heading": (11, "bold"),
    "body": (10, "normal"),
    "body_strong": (10, "bold"),
    "caption": (9, "normal"),
    "stat": (18, "bold"),
}


def contrast_ratio(foreground: str, background: str) -> float:
    """نسبة التباين وفق WCAG 2.x بين لونين بصيغة ``#rrggbb``."""

    def luminance(color: str) -> float:
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def pick_font(root: tk.Misc, candidates: tuple[str, ...], fallback: str) -> str:
    """أول خط متوفر فعليًا على الجهاز.

    بلا بديل تسقط الواجهة خارج ويندوز على خط عشوائي، وتظهر العربية بمحارف مربّعة.
    """
    available = {name.lower() for name in tkfont.families(root)}
    for name in candidates:
        if name.lower() in available:
            return name
    return fallback


class Theme:
    """يجمع الخطوط المختارة لهذا الجهاز ويطبّق أنماط ttk عليها."""

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.family = pick_font(root, UI_FONT_CANDIDATES, "TkDefaultFont")
        self.mono = pick_font(root, MONO_FONT_CANDIDATES, "TkFixedFont")

    def font(self, role: str) -> tuple[str, int, str]:
        size, weight = TYPE_SCALE[role]
        return (self.family, size, weight)

    def apply(self) -> None:
        """أنماط ttk: سطح مسطّح بلا حواف ثلاثية الأبعاد، وحدود بلون واحد."""
        c = COLORS
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(
            ".",
            background=c["bg"],
            foreground=c["text"],
            font=self.font("body"),
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            troughcolor=c["raised"],
            focuscolor=c["accent"],
            selectbackground=c["selection"],
            selectforeground=c["text"],
            insertcolor=c["text"],
        )

        # أسطح
        style.configure("TFrame", background=c["bg"])
        style.configure("Surface.TFrame", background=c["surface"])
        style.configure("Raised.TFrame", background=c["raised"])

        # نصوص
        for name, bg in (("", "bg"), ("Surface.", "surface"), ("Raised.", "raised")):
            style.configure(f"{name}TLabel", background=c[bg], foreground=c["text"])
            style.configure(
                f"{name}Muted.TLabel",
                background=c[bg],
                foreground=c["muted"],
                font=self.font("caption"),
            )
            style.configure(
                f"{name}Heading.TLabel",
                background=c[bg],
                foreground=c["text"],
                font=self.font("heading"),
            )
        style.configure("Display.TLabel", font=self.font("display"))
        style.configure("Surface.Title.TLabel", background=c["surface"], font=self.font("title"))
        style.configure(
            "Surface.Error.TLabel",
            background=c["surface"],
            foreground=c["danger_text"],
            font=self.font("caption"),
        )
        style.configure(
            "Surface.Link.TLabel",
            background=c["surface"],
            foreground=c["accent_text"],
            font=self.font("caption"),
        )

        # حقول الإدخال
        field = {
            "fieldbackground": c["raised"],
            "foreground": c["text"],
            "bordercolor": c["border"],
            "lightcolor": c["raised"],
            "darkcolor": c["raised"],
            "insertcolor": c["text"],
            "padding": (SPACE["sm"], 6),
        }
        style.configure("TEntry", **field)
        style.map(
            "TEntry",
            bordercolor=[("focus", c["accent"])],
            lightcolor=[("focus", c["accent"])],
            fieldbackground=[("disabled", c["surface"])],
            foreground=[("disabled", c["subtle"])],
        )
        style.configure("TSpinbox", arrowcolor=c["muted"], arrowsize=12, **field)
        style.map(
            "TSpinbox",
            bordercolor=[("focus", c["accent"])],
            lightcolor=[("focus", c["accent"])],
            background=[("active", c["raised"])],
        )

        # خيارات
        for widget in ("TCheckbutton", "TRadiobutton"):
            style.configure(
                f"Surface.{widget}",
                background=c["surface"],
                foreground=c["text"],
                indicatorbackground=c["raised"],
                indicatorforeground=c["accent"],
                upperbordercolor=c["border"],
                lowerbordercolor=c["border"],
            )
            style.map(
                f"Surface.{widget}",
                background=[("active", c["surface"])],
                indicatorbackground=[("selected", c["accent"]), ("active", c["selection"])],
            )

        # أشرطة التمرير
        style.configure(
            "Vertical.TScrollbar",
            background=c["raised"],
            troughcolor=c["surface"],
            bordercolor=c["surface"],
            arrowcolor=c["muted"],
            gripcount=0,
        )
        style.map("Vertical.TScrollbar", background=[("active", c["border"])])

        # الجدول
        style.configure(
            "Treeview",
            background=c["surface"],
            fieldbackground=c["surface"],
            foreground=c["text"],
            bordercolor=c["surface"],
            rowheight=34,
            font=self.font("body"),
        )
        style.map(
            "Treeview",
            background=[("selected", c["selection"])],
            foreground=[("selected", c["text"])],
        )
        style.configure(
            "Treeview.Heading",
            background=c["surface"],
            foreground=c["muted"],
            bordercolor=c["border"],
            lightcolor=c["surface"],
            darkcolor=c["border"],
            relief="flat",
            padding=(SPACE["sm"], SPACE["sm"]),
            font=self.font("caption"),
        )
        style.map(
            "Treeview.Heading",
            background=[("active", c["raised"])],
            foreground=[("active", c["text"])],
        )
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
