"""عرض النص العربي في Tk على كل الأنظمة.

Tk 8.6 على ويندوز وماك يمرّر النص لمحرك النظام فيُشكَّل ويُرتَّب من اليمين
تلقائيًا. أما على لينكس (X11) فيرسمه حرفًا حرفًا من اليسار: الحروف منفصلة
والكلمات مقلوبة. الحل الوحيد داخل Tk هو تحويل النص مسبقًا إلى «ترتيب العرض»
بمكتبتَي ``arabic-reshaper`` و``python-bidi`` (اختياريتان: ``pip install
"email-cleaner[arabic]"``). بدونهما، أو على غير X11، تُرجع :func:`visual` النص كما هو.

لا تستورد هذه الوحدة tkinter، فتُختبر في أي بيئة.
"""

from __future__ import annotations

from collections.abc import Callable

RLM = "‏"  # علامة اتجاه من اليمين - تثبّت سطرًا عربيًا يبدأ برقم أو عنوان لاتيني

_transform: Callable[[str], str] | None = None


def has_arabic(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in text)


def build_transform() -> Callable[[str], str] | None:
    """دالة التحويل إلى ترتيب العرض، أو ``None`` إن لم تُثبَّت المكتبتان."""
    try:
        import arabic_reshaper

        # الواجهة القديمة (bidi.algorithm) تعكس الأقواس داخل المقاطع العربية؛
        # الجديدة في 0.6+ لا تفعل، فتظهر «(نص)» بأقواس مقلوبة.
        from bidi.algorithm import get_display
    except ImportError:
        return None

    def transform(text: str) -> str:
        # أي سطر فيه عربية يُعامل كفقرة يمينية حتى لو بدأ بكلمة لاتينية («Gmail وYahoo»).
        # وكذلك سطر يبدأ بـ RLM صراحةً؛ والعلامة نفسها تُحذف لأن Tk على X11 يرسمها مربعًا.
        lines = []
        for line in text.split("\n"):
            rtl = has_arabic(line) or line.startswith(RLM)
            shaped = arabic_reshaper.reshape(line.replace(RLM, ""))
            lines.append(get_display(shaped, base_dir="R" if rtl else None))
        return "\n".join(lines)

    return transform


def configure(windowing_system: str) -> bool:
    """يقرر مرة واحدة إن كان النص يحتاج تحويلًا، حسب ``tk windowingsystem``.

    يرجّع ``False`` إذا كان التحويل لازمًا لكن المكتبتين غير مثبّتتين، ليعرض
    التطبيق تنبيهًا مرة واحدة.
    """
    global _transform
    _transform = build_transform() if windowing_system == "x11" else None
    return windowing_system != "x11" or _transform is not None


def visual(text: str) -> str:
    """النص كما يجب أن يُرسَم. آمن للاستدعاء بأي قيمة."""
    if not text or _transform is None:
        return text
    return _transform(text)
