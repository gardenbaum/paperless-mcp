"""Finding the type of an upload from its bytes: magic numbers, never the file name."""

import io
import struct
import zipfile

import pytest
from e2e.helpers import pdf

from paperless_mcp.services.upload_types import CONSUMABLE, sniff

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
ODT = "application/vnd.oasis.opendocument.text"
ODS = "application/vnd.oasis.opendocument.spreadsheet"
ODP = "application/vnd.oasis.opendocument.presentation"


def archive(members: dict[str, bytes], *, first: tuple[str, bytes] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        if first:
            bundle.writestr(first[0], first[1], compress_type=zipfile.ZIP_STORED)
        for name, data in members.items():
            bundle.writestr(name, data)
    return out.getvalue()


def office(folder: str) -> bytes:
    return archive({"[Content_Types].xml": b"<Types/>", f"{folder}/part.xml": b"<x/>"})


def opendocument(kind: str) -> bytes:
    return archive({"content.xml": b"<c/>"}, first=("mimetype", kind.encode()))


def bitmap(header: int, tail: bytes | None = None) -> bytes:
    return b"BM" + struct.pack("<IHHII", 70, 0, 0, 54, header) + (tail or bytes(40))


def crafted(damage: str) -> bytes:
    """A zip whose `mimetype` member cannot be read the ordinary way (it fools no reader)."""
    out = io.BytesIO()
    info = zipfile.ZipInfo("mimetype")
    info.compress_type = zipfile.ZIP_DEFLATED if damage == "corrupt" else zipfile.ZIP_STORED
    with zipfile.ZipFile(out, "w") as bundle:
        bundle.writestr(info, ODT.encode() * 4)
    data = bytearray(out.getvalue())
    if damage == "encrypted":  # the encryption flag, in the local and the central header
        data[6] |= 1
        data[data.index(b"PK\x01\x02") + 8] |= 1
    if damage == "method":  # a compression method no reader supports
        data[8] = 99
        data[data.index(b"PK\x01\x02") + 10] = 99
    if damage == "text":  # a type name that is not even ASCII
        data[30 + len("mimetype") : 30 + len("mimetype") + 6] = b"\xff" * 6
    if damage == "corrupt":  # deflate data that is none
        start = 30 + len("mimetype")
        data[start : start + 6] = b"\xff" * 6
    return bytes(data)


BMP = bitmap(40)
WEBP = b"RIFF" + struct.pack("<I", 20) + b"WEBPVP8L" + bytes(12)

SAMPLES = [
    pytest.param(bitmap(40, b"\x01" * 40), "image/bmp", id="bmp whose data starts non-zero"),
    pytest.param(pdf("hello"), "application/pdf", id="pdf"),
    pytest.param(b"\x89PNG\r\n\x1a\n" + bytes(20), "image/png", id="png"),
    pytest.param(b"\xff\xd8\xff\xe0" + bytes(20), "image/jpeg", id="jpeg"),
    pytest.param(b"GIF87a" + bytes(20), "image/gif", id="gif87a"),
    pytest.param(b"GIF89a" + bytes(20), "image/gif", id="gif89a"),
    pytest.param(b"II*\x00" + bytes(20), "image/tiff", id="tiff little endian"),
    pytest.param(b"MM\x00*" + bytes(20), "image/tiff", id="tiff big endian"),
    *(
        pytest.param(bitmap(size), "image/bmp", id=f"bmp with a {size} byte header")
        for size in (12, 40, 52, 56, 64, 108, 124)
    ),
    pytest.param(WEBP, "image/webp", id="webp"),
    pytest.param(b"{\\rtf1\\ansi hello}", "text/rtf", id="rtf"),
    pytest.param(office("word"), DOCX, id="docx"),
    pytest.param(office("xl"), XLSX, id="xlsx"),
    pytest.param(office("ppt"), PPTX, id="pptx"),
    pytest.param(opendocument(ODT), ODT, id="odt"),
    pytest.param(opendocument(ODS), ODS, id="ods"),
    pytest.param(opendocument(ODP), ODP, id="odp"),
    pytest.param("Grüße, Käse\n".encode(), "text/plain", id="utf-8 text"),
    pytest.param(b"\xef\xbb\xbfwith a byte order mark", "text/plain", id="utf-8 text with BOM"),
]


@pytest.mark.parametrize(("data", "expected"), SAMPLES)
def test_the_type_comes_from_the_content(data: bytes, expected: str) -> None:
    assert sniff(data, "no-extension") == expected
    assert expected in CONSUMABLE


@pytest.mark.parametrize(("data", "expected"), SAMPLES)
def test_the_file_name_does_not_decide_the_type(data: bytes, expected: str) -> None:
    assert sniff(data, "liar.exe") == expected


@pytest.mark.parametrize("data", [b"BM", b"BMW invoice", b"%PDF", b"GIF", b"PK", b"{\\rt"])
def test_a_signature_that_is_cut_short_is_just_text(data: bytes) -> None:
    assert sniff(data, "note.pdf") == "text/plain"


def test_the_extension_only_tells_csv_from_other_text() -> None:
    table = b"a,b\n1,2\n"

    assert sniff(table, "table.csv") == "text/csv"
    assert sniff(table, "TABLE.CSV") == "text/csv"
    assert sniff(table, "table.txt") == "text/plain"
    assert sniff(table, "table") == "text/plain"
    assert sniff(pdf("not a table"), "table.csv") == "application/pdf"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"\x7fELF\x02\x01\x01" + bytes(64), id="executable"),
        pytest.param(b"MZ\x90\x00" + bytes(64), id="windows executable"),
        pytest.param(archive({"a.txt": b"x"}), id="plain zip"),
        pytest.param(
            archive({"[Content_Types].xml": b"<Types/>"}), id="zip with only a content list"
        ),
        pytest.param(opendocument("application/x-evil"), id="opendocument of an unknown kind"),
        pytest.param(b"PK\x03\x04 not really a zip", id="broken zip"),
        pytest.param(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(64), id="legacy office file"),
        pytest.param(b"hello\x00world", id="text with a NUL"),
        pytest.param(b"caf\xe9 in latin-1", id="text that is not UTF-8"),
        pytest.param(b"<html><body>hi</body></html>", id="html"),
        pytest.param(b"\xef\xbb\xbf<html></html>", id="html after a byte order mark"),
        pytest.param(b"\xef\xbb\xbf \n<?xml version='1.0'?>", id="xml after a BOM and blanks"),
        pytest.param(b"text with a delete\x7f character", id="text with DEL"),
        pytest.param(crafted("encrypted"), id="zip member that wants a password"),
        pytest.param(crafted("method"), id="zip member with an unknown compression"),
        pytest.param(crafted("corrupt"), id="zip member with broken data"),
        pytest.param(crafted("text"), id="zip member whose type is not ASCII"),
        *(
            pytest.param(bitmap(size), id=f"bm with a {size} byte header")
            for size in (0, 11, 13, 39, 41, 125)
        ),
        pytest.param(b"  \n<svg xmlns='http://www.w3.org/2000/svg'/>", id="svg"),
        pytest.param(b"RIFF" + bytes(4) + b"WAVEfmt " + bytes(12), id="riff that is not webp"),
        pytest.param(b"text with an escape \x1b[0m", id="text with a control character"),
    ],
)
def test_what_paperless_cannot_consume_has_no_type(data: bytes) -> None:
    assert sniff(data, "whatever.pdf") is None


def test_every_type_found_is_consumable() -> None:
    found = {kind for param in SAMPLES if (kind := param.values[1])}

    assert found <= CONSUMABLE
    assert CONSUMABLE - found == {"text/csv"}
