"""The file name in a ``Content-Disposition`` header (RFC 6266).

Paperless sends both forms: ``filename`` folded to ASCII (the characters it cannot represent are
dropped) and ``filename*`` (RFC 5987) with the real name percent-encoded in UTF-8. The extended
form wins when it decodes.
"""

import re
from urllib.parse import unquote_to_bytes

EXTENDED = re.compile(r"filename\*\s*=\s*([^;]+)", re.IGNORECASE)
PLAIN = re.compile(r'(?<![\w*])filename\s*=\s*("(?:[^"\\]|\\.)*"|[^;]+)', re.IGNORECASE)


def _extended(value: str) -> str | None:
    """Decode ``charset'language'percent-encoded``; None if it is malformed or undecodable."""
    charset, separator, rest = value.strip().partition("'")
    _language, separator_2, encoded = rest.partition("'")
    if not (separator and separator_2):
        return None
    try:
        return unquote_to_bytes(encoded).decode(charset)
    except LookupError, UnicodeDecodeError:
        return None


def _plain(value: str) -> str:
    value = value.strip()
    if value.startswith('"'):
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def filename(disposition: str | None) -> str | None:
    """The proposed file name, without any directory part; None if there is none."""
    if not disposition:
        return None
    extended = EXTENDED.search(disposition)
    plain = PLAIN.search(disposition)
    name = (extended and _extended(extended[1])) or (plain and _plain(plain[1])) or ""
    return re.split(r"[/\\]", name)[-1].strip() or None
