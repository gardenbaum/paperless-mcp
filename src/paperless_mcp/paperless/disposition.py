"""The file name in a ``Content-Disposition`` header (RFC 6266).

Paperless sends both forms: ``filename`` folded to ASCII (the characters it cannot represent are
dropped) and ``filename*`` (RFC 5987) with the real name percent-encoded in UTF-8. The extended
form wins when it decodes. The header is read left to right as a list of parameters, so text
inside a quoted value (a title can hold ``filename*=...``) is never taken for a parameter.
"""

import re
import unicodedata
from urllib.parse import unquote_to_bytes

# `; name=value` with a quoted or a bare value; a quoted string is consumed whole.
PARAMETER = re.compile(r';\s*([^\s=;]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]*)')
# The only charsets RFC 8187 requires (and Paperless uses), by their registered names.
CHARSETS = {"utf-8": "utf-8", "iso-8859-1": "iso-8859-1"}
# Right-to-left overrides and isolates can make a name read as another extension.
BIDI = frozenset(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))


def _parameters(disposition: str) -> dict[str, str]:
    """The first value of each parameter, names in lower case, raw."""
    found: dict[str, str] = {}
    for match in PARAMETER.finditer(disposition):
        _ = found.setdefault(match[1].lower(), match[2])
    return found


def _extended(value: str) -> str | None:
    """Decode ``charset'language'percent-encoded``; None if it is malformed or undecodable."""
    charset, separator, rest = value.strip().partition("'")
    _language, separator_2, encoded = rest.partition("'")
    codec = CHARSETS.get(charset.lower())
    if not (separator and separator_2 and codec):
        return None
    try:
        return unquote_to_bytes(encoded).decode(codec)
    except UnicodeDecodeError:
        return None


def _plain(value: str) -> str:
    value = value.strip()
    if len(value) > 1 and value.startswith('"') and value.endswith('"'):
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value.removeprefix('"')


def _safe(name: str) -> str | None:
    """The last path segment without control characters or bidi marks; None if no name is left."""
    last = re.split(r"[/\\]", name)[-1]
    cleaned = "".join(c for c in last if unicodedata.category(c) != "Cc" and c not in BIDI).strip()
    return None if cleaned in {"", ".", ".."} else cleaned


def filename(disposition: str | None) -> str | None:
    """The proposed file name, safe to show: no directory part, no control or bidi characters.

    None if there is none.
    """
    if not disposition:
        return None
    parameters = _parameters(disposition)
    extended = parameters.get("filename*")
    name = (extended and _extended(extended)) or _plain(parameters.get("filename", ""))
    return _safe(name)
