from __future__ import annotations

import unicodedata


CONTROL_CHARS_TO_KEEP = {"\n", "\r", "\t"}

LIGATURE_EXPANSIONS = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
}


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


def normalize_reading_text(text: str) -> str:
    """Expand only explicit Unicode ligatures in the reading view."""
    normalized = normalize_text(text)
    return "".join(LIGATURE_EXPANSIONS.get(char, char) for char in normalized)
