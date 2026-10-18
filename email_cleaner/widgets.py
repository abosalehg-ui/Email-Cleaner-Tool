"""عناصر واجهة مرسومة يدويًا: Tk لا يوفّر أزرارًا بحواف مستديرة ولا حوارات مُنسَّقة.

كل نص يمرّ عبر :func:`textdir.visual` قبل رسمه، وكل لون يُقرأ من :mod:`theme`.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont
from tkinter import ttk

from .textdir import visual
from .theme import COLORS, RADIUS, SPACE, Theme

# variant -> (خلفية، تمرير، ضغط، نص، حدّ)
_VARIANTS = {
    "primary": ("accent", "accent_hover", "accent_pressed", "on_accent", None),
    "danger": ("danger", "danger_hover", "danger_pressed", "on_accent", None),
    "secondary": ("raised", "selection", "border", "text", "border"),
    "ghost": (None, "raised", "selection", "muted", None),
    "selected": ("selection", "selection", "selection", "text", "border"),
}
_SIZES = {"sm": (28, SPACE["md"]), "md": (36, SPACE["lg"]), "lg": (44, SPACE["xl"])}


def rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs) -> int:
    """مستطيل بحواف مستديرة عبر مضلّع ناعم - أخف من رسم أربعة أقواس."""
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    points = (
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    )  # fmt: skip
    return canvas.create_polygon(points, smooth=True, **kwargs)


class Button(tk.Canvas):
    """زر مسطّح بحواف مستديرة، يدعم التمرير والضغط والتعطيل وحلقة التركيز.

    يُفعَّل بالنقر أو بـ Enter/مسافة عند التركيز عليه بـ Tab.
    """

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        text: str,
        command: Callable[[], None] | None = None,
        *,
        variant: str = "primary",
        size: str = "md",
        surface: str = "bg",
    ) -> None:
        height, self._padx = _SIZES[size]
        super().__init__(
            parent,
            height=height,
            bg=COLORS[surface],
            highlightthickness=0,
            bd=0,
            takefocus=1,
        )
        self._theme = theme
        self._font = theme.font("body_strong" if size != "sm" else "caption")
        self._command = command
        self._variant = variant
        self._surface = surface
        self._text = text
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._focused = False
        self._resize()

        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda _e: self._set(focused=True))
        self.bind("<FocusOut>", lambda _e: self._set(focused=False))
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.bind(key, lambda _e: self.invoke())

    # ------------------------------------------------------------- واجهة عامة

    def invoke(self) -> None:
        if self._enabled and self._command:
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self._enabled:
            self._enabled = enabled
            self.configure(cursor="hand2" if enabled else "", takefocus=1 if enabled else 0)
            self._draw()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._resize()

    def set_variant(self, variant: str) -> None:
        if variant != self._variant:
            self._variant = variant
            self._draw()

    # ------------------------------------------------------------- رسم

    def _resize(self) -> None:
        # الزر الممتد (fill) يأخذ عرض حاويته؛ هذا العرض حده الأدنى فقط.
        width = tkfont.Font(font=self._font).measure(visual(self._text)) + 2 * self._padx
        self.configure(width=width, cursor="hand2" if self._enabled else "")
        self._draw()

    def _set(self, **state: bool) -> None:
        for key, value in state.items():
            setattr(self, f"_{key}", value)
        self._draw()

    def _on_release(self, event: tk.Event) -> None:
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        was_pressed = self._pressed
        self._set(pressed=False)
        if inside and was_pressed:
            self.invoke()

    def _colors(self) -> tuple[str, str, str | None]:
        base, hover, pressed, text, border = _VARIANTS[self._variant]
        if not self._enabled:
            fill = COLORS["raised"] if base else COLORS[self._surface]
            return fill, COLORS["subtle"], None
        key = pressed if self._pressed else hover if self._hover else base
        fill = COLORS[key] if key else COLORS[self._surface]
        return fill, COLORS[text], COLORS[border] if border else None

    def _draw(self) -> None:
        self.delete("all")
        # قبل أول عرض يكون winfo_width() = 1، فنرسم بالعرض المطلوب.
        width = self.winfo_width() if self.winfo_width() > 1 else int(self.cget("width"))
        height = int(self.cget("height"))
        fill, text_color, border = self._colors()
        rounded_rect(self, 1, 1, width - 1, height - 1, RADIUS, fill=fill, outline=border or fill)
        if self._focused and self._enabled:
            rounded_rect(
                self, 1, 1, width - 1, height - 1, RADIUS, fill="", outline=COLORS["accent_text"],
                width=2,
            )  # fmt: skip
        self.create_text(
            width / 2, height / 2, text=visual(self._text), fill=text_color, font=self._font
        )


class Card(tk.Frame):
    """سطح مرفوع بحدّ رفيع - الحاوية الأساسية لكل قسم."""

    def __init__(self, parent: tk.Misc, *, padding: int = SPACE["lg"], tone: str = "surface"):
        super().__init__(
            parent,
            bg=COLORS[tone],
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["border"],
            highlightthickness=1,
            bd=0,
            padx=padding,
            pady=padding,
        )


class StatTile(tk.Frame):
    """رقم كبير وتسميته بجانبه في سطر واحد - لملخص الفحص بلا هدر للارتفاع."""

    def __init__(self, parent: tk.Misc, theme: Theme, label: str, tone: str = "text") -> None:
        super().__init__(
            parent,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=SPACE["lg"],
            pady=SPACE["sm"],
        )
        self._value = tk.Label(
            self, text="—", bg=COLORS["surface"], fg=COLORS[tone], font=theme.font("stat")
        )
        self._value.pack(side=tk.RIGHT)
        tk.Label(
            self,
            text=visual(label),
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=theme.font("caption"),
        ).pack(side=tk.RIGHT, padx=(0, SPACE["sm"]), pady=(SPACE["xs"], 0))

    def set(self, value: int | str) -> None:
        self._value.configure(text=str(value))


class ProgressBar(tk.Canvas):
    """شريط تقدم رفيع بحواف مستديرة يمتلئ من اليمين - ttk لا يسمح بتنحيفه في سمة clam."""

    def __init__(self, parent: tk.Misc, variable: tk.IntVar, *, surface: str = "surface"):
        super().__init__(parent, height=4, bg=COLORS[surface], highlightthickness=0, bd=0)
        self._variable = variable
        variable.trace_add("write", lambda *_: self._draw())
        self.bind("<Configure>", lambda _e: self._draw())

    def _draw(self) -> None:
        self.delete("all")
        width, height = self.winfo_width(), int(self.cget("height"))
        rounded_rect(self, 0, 0, width, height, 2, fill=COLORS["raised"], outline="")
        percent = max(0, min(100, self._variable.get()))
        if percent:
            filled = max(int(width * percent / 100), height)
            rounded_rect(
                self, width - filled, 0, width, height, 2, fill=COLORS["accent"], outline=""
            )


class SegmentedControl(tk.Frame):
    """خياران أو أكثر متجاوران، واحد منهم فقط نشط - بديل عصري لأزرار الراديو."""

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        options: list[tuple[str, str]],
        variable: tk.StringVar,
    ) -> None:
        super().__init__(parent, bg=COLORS["raised"], padx=3, pady=3)
        self._variable = variable
        self._buttons: dict[str, Button] = {}
        # أول خيار في القائمة يقع أقصى اليمين.
        for value, label in options:
            button = Button(
                self,
                theme,
                label,
                lambda v=value: variable.set(v),
                variant="ghost",
                size="sm",
                surface="raised",
            )
            button.pack(side=tk.RIGHT, padx=1)
            self._buttons[value] = button
        variable.trace_add("write", lambda *_: self._sync())
        self._sync()

    def _sync(self) -> None:
        current = self._variable.get()
        for value, button in self._buttons.items():
            button.set_variant("selected" if value == current else "ghost")


class Dialog(tk.Toplevel):
    """حوار مشروط بنفس هوية التطبيق، بديلًا عن ``messagebox`` الذي لا يتبع السمة.

    ``Esc`` يلغي دائمًا. في الحوار الهدّام يكون التركيز الافتراضي على «إلغاء»
    كي لا يؤكد Enter عرضيٌّ عملية لا رجعة فيها.
    """

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        title: str,
        body: str,
        *,
        confirm: str | None = "متابعة",
        cancel: str | None = "إلغاء",
        danger: bool = False,
        details: list[str] | None = None,
        prompt_secret: bool = False,
    ) -> None:
        super().__init__(
            parent,
            bg=COLORS["surface"],
            padx=SPACE["xl"],
            pady=SPACE["xl"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        self.withdraw()
        self.title(title)  # شريط العنوان يرسمه مدير النوافذ، وهو يعرف RTL
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.result: bool | str | None = None
        self._secret = tk.StringVar()

        ttk.Label(self, text=visual(title), style="Surface.Title.TLabel", anchor="e").pack(
            fill=tk.X
        )
        tk.Label(
            self,
            text=visual(body),
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=theme.font("body"),
            justify=tk.RIGHT,
            anchor="e",
        ).pack(fill=tk.X, pady=(SPACE["sm"], 0))

        if details:
            box = tk.Frame(self, bg=COLORS["raised"], padx=SPACE["md"], pady=SPACE["sm"])
            box.pack(fill=tk.X, pady=(SPACE["md"], 0))
            for line in details:
                tk.Label(
                    box,
                    text=visual(line),
                    bg=COLORS["raised"],
                    fg=COLORS["text"],
                    font=theme.font("caption"),
                    anchor="e",
                ).pack(fill=tk.X)

        entry = None
        if prompt_secret:
            entry = ttk.Entry(self, textvariable=self._secret, show="•", justify="left", width=36)
            entry.pack(fill=tk.X, pady=(SPACE["md"], 0))

        # ترتيب RTL: الإجراء الأساسي أولًا من اليمين ثم «إلغاء» - كما في شريط الإجراءات.
        actions = tk.Frame(self, bg=COLORS["surface"])
        actions.pack(fill=tk.X, pady=(SPACE["xl"], 0))
        confirm_btn = cancel_btn = None
        if confirm:
            confirm_btn = Button(
                actions,
                theme,
                confirm,
                self._accept,
                variant="danger" if danger else "primary",
                surface="surface",
            )
            confirm_btn.pack(side=tk.RIGHT)
        if cancel:
            cancel_btn = Button(
                actions, theme, cancel, self._dismiss, variant="secondary", surface="surface"
            )
            cancel_btn.pack(side=tk.RIGHT, padx=(0, SPACE["sm"]))

        self.bind("<Escape>", lambda _e: self._dismiss())
        self.protocol("WM_DELETE_WINDOW", self._dismiss)
        if entry is not None:
            entry.bind("<Return>", lambda _e: self._accept())

        self.update_idletasks()
        top = parent.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_reqwidth()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        # على X11 يفشل grab_set إن سبق ظهور النافذة فعليًا.
        self.wait_visibility()
        self.grab_set()
        focus = entry or (cancel_btn if danger and cancel_btn else confirm_btn or cancel_btn)
        if focus is not None:
            focus.focus_set()

    def _accept(self) -> None:
        self.result = self._secret.get() if self._secret.get() else True
        self.destroy()

    def _dismiss(self) -> None:
        self.result = None
        self.destroy()

    def show(self) -> bool | str | None:
        self.wait_window()
        return self.result
