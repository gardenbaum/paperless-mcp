"""FileService: thumbnails and downloads with a size limit, decided before the bytes are read."""

from typing import TYPE_CHECKING, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, seeded

from paperless_mcp.domain.errors import NotFound, PermissionDenied
from paperless_mcp.services.files import FileService

if TYPE_CHECKING:
    from paperless_mcp.domain.documents import DocumentFile, FileMetadata, FileVersion

pytestmark = pytest.mark.anyio

WEB = "https://paperless.example/"
LIMIT = 100


class Recording(FakePaperless):
    """The fake, counting the files it hands out."""

    served: int = 0

    @override
    async def get_document_file(self, document_id: int, version: FileVersion) -> DocumentFile:
        self.served += 1
        return await super().get_document_file(document_id, version)


class Understating(FakePaperless):
    """A Paperless whose metadata names a smaller size than the file really has."""

    @override
    async def get_document_metadata(self, document_id: int) -> FileMetadata:
        found = await super().get_document_metadata(document_id)
        return found.model_copy(update={"original_size": 1, "archive_size": 1})


def files(fake: FakePaperless, limit: int = LIMIT) -> FileService:
    return FileService(fake, web_url=WEB, max_bytes=limit)


def fake_with(
    *,
    original: bytes = b"%PDF-1.4 fake original",
    archive: bytes | None = b"%PDF-1.4 fake archive",
    mime_type: str = "application/pdf",
    file_name: str | None = None,
) -> Recording:
    state = seeded()
    state.documents.append(
        FakeDocument(
            id=7,
            title="Müller €",
            original=original,
            archive=archive,
            mime_type=mime_type,
            file_name=file_name,
        )
    )
    return Recording(state, user="admin")


async def test_a_thumbnail_comes_with_its_report() -> None:
    fake = Recording(seeded(), user=ADMIN[0])

    delivery = await files(fake).thumbnail(1)

    report = delivery.report
    assert delivery.data == (await fake.get_document_thumbnail(1)).content
    assert (report.document_id, report.kind) == (1, "thumbnail")
    assert (report.mime_type, report.filename) == ("image/webp", "0000001.webp")
    assert report.size_bytes == len(delivery.data or b"")
    assert (report.delivered, report.max_bytes, report.note) == (True, None, None)
    assert report.web_url == "https://paperless.example/documents/1/details"


async def test_an_original_within_the_limit_is_delivered_with_its_name_and_type() -> None:
    fake = fake_with(original=b"x" * LIMIT, file_name="Müller.txt", mime_type="text/plain")

    delivery = await files(fake).download(7, "original")

    report = delivery.report
    assert delivery.data == b"x" * LIMIT
    assert (report.document_id, report.kind) == (7, "original")
    assert (report.mime_type, report.filename) == (
        "text/plain; charset=utf-8",
        "2026-11-11 Müller €.txt",
    )
    assert (report.size_bytes, report.delivered, report.max_bytes) == (LIMIT, True, LIMIT)
    assert report.note is None
    assert report.web_url == "https://paperless.example/documents/7/details"


async def test_an_archive_within_the_limit_is_delivered_as_pdf() -> None:
    fake = fake_with(original=b"x" * 500, archive=b"%PDF-archive")

    delivery = await files(fake).download(7, "archive")

    assert delivery.data == b"%PDF-archive"
    assert (delivery.report.kind, delivery.report.mime_type) == ("archive", "application/pdf")
    assert delivery.report.filename == "2026-11-11 Müller €.pdf"
    assert delivery.report.size_bytes == len(b"%PDF-archive")


async def test_a_file_over_the_limit_is_reported_without_being_downloaded() -> None:
    fake = fake_with(original=b"x" * (LIMIT + 1), file_name="Rechnung Käse.pdf")

    delivery = await files(fake).download(7, "original")

    report = delivery.report
    assert delivery.data is None
    assert fake.served == 0
    assert (report.delivered, report.size_bytes, report.max_bytes) == (False, LIMIT + 1, LIMIT)
    assert (report.mime_type, report.filename) == ("application/pdf", "Rechnung Käse.pdf")
    assert report.note is not None
    assert f"{LIMIT} bytes" in report.note
    assert report.web_url in report.note
    assert report.web_url == "https://paperless.example/documents/7/details"


async def test_an_archive_over_the_limit_is_named_after_the_upload_as_pdf() -> None:
    fake = fake_with(original=b"x", archive=b"x" * (LIMIT + 1), file_name="scan.JPG")

    delivery = await files(fake).download(7, "archive")

    assert delivery.data is None
    assert (delivery.report.mime_type, delivery.report.filename) == ("application/pdf", "scan.pdf")
    assert delivery.report.size_bytes == LIMIT + 1


async def test_the_size_is_checked_again_on_the_bytes_that_arrive() -> None:
    state = seeded()
    state.documents.append(FakeDocument(id=7, title="big", original=b"x" * (LIMIT + 50)))
    fake = Understating(state, user=ADMIN[0])

    delivery = await files(fake).download(7, "original")

    assert delivery.data is None
    assert (delivery.report.delivered, delivery.report.size_bytes) == (False, LIMIT + 50)


async def test_a_document_without_archive_version_has_no_archive_to_download() -> None:
    fake = fake_with(archive=None, mime_type="text/plain")

    with pytest.raises(NotFound, match="no archive version") as raised:
        _ = await files(fake).download(7, "archive")

    assert "original" in (raised.value.hint or "")
    assert fake.served == 0
    assert (await files(fake).download(7, "original")).report.delivered


async def test_a_missing_or_hidden_document_is_an_error_before_anything_is_read() -> None:
    reader = Recording(seeded(), user=READER[0])

    with pytest.raises(NotFound):
        _ = await files(reader).download(999, "original")
    with pytest.raises(PermissionDenied):
        _ = await files(reader).download(2, "original")
    with pytest.raises(PermissionDenied):
        _ = await files(reader).thumbnail(2)
    assert reader.served == 0
