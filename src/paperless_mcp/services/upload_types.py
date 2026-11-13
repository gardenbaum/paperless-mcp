"""What an upload is, found from its bytes: the types Paperless consumes, and no others.

The file name is never trusted: a type comes from magic numbers (and, for zip-based documents,
from the names of the archive's members), so a PDF named ``x.exe`` is a PDF and an executable
named ``x.pdf`` is nothing. The one use of the extension is to call text with a ``.csv`` name
``text/csv``; both are plain text to Paperless.

Paperless 3.4.2 consumes PDF, the common raster images and plain text out of the box. Office
and OpenDocument files and RTF need its Tika and Gotenberg add-ons: when they are missing it
refuses them with a clear 400, so they are allowed here and Paperless has the last word. Not
recognised (so refused here): legacy binary Office files (OLE containers cannot be told apart
without parsing them), HTML, e-mail and markup in general.

A file can be several things at once (a PDF that is also a zip, or an image that is also a PDF).
The first signature wins, and Paperless decides again when it consumes the file; this module
keeps obvious non-documents out, it is no malware scanner.
"""

import io
import zipfile
import zlib
from typing import Final

PDF: Final = "application/pdf"
TEXT: Final = "text/plain"
CSV: Final = "text/csv"
RTF: Final = "text/rtf"
OOXML: Final = "application/vnd.openxmlformats-officedocument."
ODF: Final = "application/vnd.oasis.opendocument."

# The folder every part of a zip-based Office document lives in, and what it is.
OFFICE_FOLDERS: Final = {
    "word/": OOXML + "wordprocessingml.document",
    "xl/": OOXML + "spreadsheetml.sheet",
    "ppt/": OOXML + "presentationml.presentation",
}
OPENDOCUMENT: Final = frozenset({ODF + "text", ODF + "spreadsheet", ODF + "presentation"})
# BMP: the size of the info header that follows the 14-byte file header.
BMP_HEADERS: Final = frozenset({12, 40, 52, 56, 64, 108, 124})
BMP_INFO_AT: Final = 14

# The bytes text may hold: from the space up except DEL, tab, line feed, form feed, carriage return.
TEXT_BYTES: Final = bytes((*range(32, 127), *range(128, 256), 9, 10, 12, 13))
BYTE_ORDER_MARK: Final = "\ufeff"
# What reading a crafted archive can raise: encrypted or unsupported members, damaged data.
UNREADABLE: Final = (
    zipfile.BadZipFile,
    RuntimeError,
    NotImplementedError,
    zlib.error,
    EOFError,
    ValueError,
    OSError,
)

# Every type `sniff` can return.
CONSUMABLE: Final = frozenset(
    {
        PDF,
        TEXT,
        CSV,
        RTF,
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/tiff",
        "image/bmp",
        "image/webp",
        *OFFICE_FOLDERS.values(),
        *OPENDOCUMENT,
    }
)


def _zipped(data: bytes) -> str | None:
    """The type of a zip-based document, from the member names (nothing is unpacked)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            names = bundle.namelist()
            if "[Content_Types].xml" in names:
                return next(
                    (
                        kind
                        for folder, kind in OFFICE_FOLDERS.items()
                        if any(name.startswith(folder) for name in names)
                    ),
                    None,
                )
            if "mimetype" in names:
                with bundle.open("mimetype") as member:
                    declared = member.read(100).decode("ascii", errors="replace")
                return declared if declared in OPENDOCUMENT else None
    except UNREADABLE:
        return None
    return None


def _text(data: bytes, filename: str) -> str | None:
    """Plain text: UTF-8 without control characters (but tabs and line breaks), not markup."""
    if not data or data.translate(None, TEXT_BYTES):
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.removeprefix(BYTE_ORDER_MARK).lstrip().startswith("<"):
        return None
    return CSV if filename.lower().endswith(".csv") else TEXT


def sniff(data: bytes, filename: str) -> str | None:
    """The MIME type of ``data`` if Paperless can consume it, else None.

    ``filename`` only decides between plain text and CSV.
    """
    signatures = (
        (b"%PDF-", PDF),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"II*\x00", "image/tiff"),
        (b"MM\x00*", "image/tiff"),
        (b"{\\rtf", RTF),
    )
    for prefix, kind in signatures:
        if data.startswith(prefix):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if (
        data[:2] == b"BM"
        and int.from_bytes(data[BMP_INFO_AT : BMP_INFO_AT + 4], "little") in BMP_HEADERS
    ):
        return "image/bmp"
    if data.startswith(b"PK\x03\x04"):
        return _zipped(data)
    return _text(data, filename)
