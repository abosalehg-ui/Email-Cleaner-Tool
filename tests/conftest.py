"""أدوات مشتركة للاختبارات - كلها بلا شبكة وبلا خادم IMAP."""

from __future__ import annotations

import email
import sys
from email.message import Message
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURES = Path(__file__).parent / "fixtures"


def load_eml(name: str) -> Message:
    """يقرأ رسالة ثابتة من مجلد fixtures."""
    return email.message_from_bytes((FIXTURES / name).read_bytes())


@pytest.fixture
def rules():
    """القواعد الافتراضية بلا أي ملف تخصيص من المستخدم."""
    from email_cleaner.rules import Rules

    return Rules.load(user_file=None)
