from __future__ import annotations

import unicodedata


CONTROL_CHARS_TO_KEEP = {"\n", "\r", "\t"}


def normalize_text(text: str) -> str:
    """Conservative text normalization for reading views.

    This keeps diacritics, numbers and punctuation intact. It only normalizes
    Unicode composition, line endings and control characters with no textual role.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    return "".join(
        char
        for char in text
        if char in CONTROL_CHARS_TO_KEEP or not unicodedata.category(char).startswith("C")
    )
