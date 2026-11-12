"""get_document_thumbnail, download_document and the file resources through the in-memory client."""

import base64
import json
import logging
from typing import TYPE_CHECKING, cast, override

import pytest
from fakes.accounts import READER
from fakes.paperless import THUMBNAIL, FakeDocument, FakePaperless, seeded
from fastmcp import Client
from harness import admin, serve
from mcp.shared.exceptions import MCPError
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent, TextContent

if TYPE_CHECKING:
    from mcp.types import ContentBlock

    from paperless_mcp.domain.documents import DocumentFile, FileVersion

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

LIMIT = 64


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode()


def fake_with_files(
    *,
    original: bytes = b"%PDF-1.4 fake original",
    archive: bytes | None = b"%PDF-1.4 fake archive",
    mime_type: str = "application/pdf",
    file_name: str | None = None,
) -> FakePaperless:
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
    return FakePaperless(state, user="admin")


async def call(
    name: str, arguments: Json, fake: FakePaperless | None = None, limit: int = LIMIT
) -> tuple[list[ContentBlock], Json]:
    async with Client(serve(fake or admin(), max_download_bytes=limit)) as client:
        result = await client.call_tool(name, arguments)
    assert result.structured_content is None  # decision-11: content blocks only
    content = list(result.content)
    assert isinstance(content[0], TextContent)
    return content, cast("Json", json.loads(content[0].text))


async def test_a_thumbnail_is_image_content_with_a_report() -> None:
    content, report = await call("get_document_thumbnail", {"document_id": 1})

    [_, image] = content
    assert isinstance(image, ImageContent)
    assert (image.mime_type, image.data) == ("image/webp", encoded(THUMBNAIL))
    assert report == {
        "document_id": 1,
        "kind": "thumbnail",
        "filename": "0000001.webp",
        "mime_type": "image/webp",
        "size_bytes": len(THUMBNAIL),
        "delivered": True,
        "max_bytes": None,
        "web_url": "http://paperless.test/documents/1/details",
        "note": None,
    }


async def test_a_download_is_an_embedded_resource_with_its_type_and_name() -> None:
    fake = fake_with_files(original=b"%PDF original", file_name="Käse.pdf")

    content, report = await call("download_document", {"document_id": 7}, fake)

    [_, embedded] = content
    assert isinstance(embedded, EmbeddedResource)
    blob = embedded.resource
    assert isinstance(blob, BlobResourceContents)
    assert blob.blob == encoded(b"%PDF original")
    assert (blob.mime_type, blob.uri) == ("application/pdf", "paperless://documents/7/original")
    assert (blob.meta or {}).get("filename") == "2026-11-11 Müller €.pdf"
    assert report["filename"] == "2026-11-11 Müller €.pdf"
    assert (report["kind"], report["delivered"], report["max_bytes"]) == ("original", True, LIMIT)


async def test_the_archive_version_is_asked_for_with_kind() -> None:
    fake = fake_with_files(original=b"x", archive=b"%PDF archive")

    content, report = await call("download_document", {"document_id": 7, "kind": "archive"}, fake)

    embedded = content[1]
    assert isinstance(embedded, EmbeddedResource)
    assert isinstance(embedded.resource, BlobResourceContents)
    assert embedded.resource.blob == encoded(b"%PDF archive")
    assert embedded.resource.uri == "paperless://documents/7/archive"
    assert report["kind"] == "archive"


async def test_a_text_original_keeps_its_charset_and_is_still_a_blob() -> None:
    fake = fake_with_files(original="grüße".encode(), mime_type="text/plain", file_name="a.txt")

    content, report = await call("download_document", {"document_id": 7}, fake)

    embedded = content[1]
    assert isinstance(embedded, EmbeddedResource)
    assert isinstance(embedded.resource, BlobResourceContents)
    assert embedded.resource.mime_type == "text/plain; charset=utf-8"
    assert base64.b64decode(embedded.resource.blob).decode() == "grüße"
    assert report["mime_type"] == "text/plain; charset=utf-8"


async def test_a_download_over_the_limit_is_metadata_and_a_link_only() -> None:
    fake = fake_with_files(original=b"x" * (LIMIT + 1))

    content, report = await call("download_document", {"document_id": 7}, fake)

    assert len(content) == 1
    assert (report["delivered"], report["size_bytes"], report["max_bytes"]) == (
        False,
        LIMIT + 1,
        LIMIT,
    )
    assert str(report["note"]).startswith(f"The file is over the download limit of {LIMIT} bytes")
    assert report["web_url"] == "http://paperless.test/documents/7/details"


async def test_a_download_exactly_at_the_limit_is_attached() -> None:
    fake = fake_with_files(original=b"x" * LIMIT)

    content, report = await call("download_document", {"document_id": 7}, fake)

    assert len(content) == 2
    assert report["delivered"] is True


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("get_document_thumbnail", {"document_id": 999}, "No document 999 exists"),
        ("download_document", {"document_id": 999}, "No document 999 exists"),
        ("download_document", {"document_id": 1, "kind": "preview"}, "kind"),
    ],
)
async def test_bad_requests_are_tool_errors(name: str, arguments: Json, message: str) -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool(name, arguments, raise_on_error=False)

    assert result.is_error
    assert message in str(result.content)


async def test_a_document_without_archive_version_is_an_error_with_a_hint() -> None:
    fake = fake_with_files(archive=None, mime_type="text/plain")

    async with Client(serve(fake)) as client:
        result = await client.call_tool(
            "download_document", {"document_id": 7, "kind": "archive"}, raise_on_error=False
        )

    assert result.is_error
    assert "Document 7 has no archive version. Hint: Call download_document with kind=" in str(
        result.content
    )


async def test_a_hidden_document_is_a_permission_error() -> None:
    reader = FakePaperless(seeded(), user=READER[0])

    async with Client(serve(reader)) as client:
        result = await client.call_tool(
            "download_document", {"document_id": 2}, raise_on_error=False
        )

    assert result.is_error
    assert "may not perform this action" in str(result.content)


async def read(
    uri: str, fake: FakePaperless | None = None, limit: int = LIMIT
) -> BlobResourceContents:
    async with Client(serve(fake or admin(), max_download_bytes=limit)) as client:
        [contents] = await client.read_resource(uri)
    assert isinstance(contents, BlobResourceContents)
    return contents


async def test_the_thumbnail_resource_is_the_image() -> None:
    contents = await read("paperless://documents/1/thumbnail")

    assert (contents.mime_type, contents.blob) == ("image/webp", encoded(THUMBNAIL))
    assert (contents.meta or {}).get("filename") == "0000001.webp"


async def test_the_original_and_archive_resources_carry_their_own_type_and_name() -> None:
    fake = fake_with_files(
        original=b"plain", archive=b"%PDF archive", mime_type="text/plain", file_name="a.txt"
    )

    original = await read("paperless://documents/7/original", fake)
    archive = await read("paperless://documents/7/archive", fake)

    assert (original.mime_type, original.blob) == ("text/plain; charset=utf-8", encoded(b"plain"))
    assert (original.meta or {}).get("filename") == "2026-11-11 Müller €.txt"
    assert (archive.mime_type, archive.blob) == ("application/pdf", encoded(b"%PDF archive"))
    assert (archive.meta or {}).get("filename") == "2026-11-11 Müller €.pdf"


@pytest.mark.parametrize("kind", ["original", "archive"])
async def test_a_file_resource_over_the_limit_is_an_error_naming_the_link(kind: str) -> None:
    fake = fake_with_files(original=b"x" * (LIMIT + 1), archive=b"x" * (LIMIT + 1))

    async with Client(serve(fake, max_download_bytes=LIMIT)) as client:
        with pytest.raises(MCPError, match="over the download limit") as raised:
            _ = await client.read_resource(f"paperless://documents/7/{kind}")

    assert "http://paperless.test/documents/7/details" in str(raised.value)


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        ("paperless://documents/999/thumbnail", "No document 999 exists"),
        ("paperless://documents/999/original", "No document 999 exists"),
        ("paperless://documents/999/archive", "No document 999 exists"),
    ],
)
async def test_a_file_resource_of_a_missing_document_is_an_error(uri: str, message: str) -> None:
    async with Client(serve(admin())) as client:
        with pytest.raises(MCPError, match=message):
            _ = await client.read_resource(uri)


async def test_a_file_resource_without_archive_version_is_an_error() -> None:
    fake = fake_with_files(archive=None, mime_type="text/plain")

    async with Client(serve(fake)) as client:
        with pytest.raises(MCPError, match="Document 7 has no archive version"):
            _ = await client.read_resource("paperless://documents/7/archive")


async def test_a_file_bug_is_masked_and_logged_by_type_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken(FakePaperless):
        @override
        async def get_document_thumbnail(self, document_id: int) -> DocumentFile:
            message = f"secret-detail of {document_id}"
            raise RuntimeError(message)

    caplog.set_level(logging.DEBUG)
    async with Client(serve(admin(), documents=Broken(seeded(), user="admin"))) as client:
        result = await client.call_tool(
            "get_document_thumbnail", {"document_id": 1}, raise_on_error=False
        )

    assert result.is_error
    assert "secret-detail" not in str(result.content)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert "get_document_thumbnail failed: RuntimeError" in errors
    assert not [line for line in errors if "secret-detail" in line]


class Nameless(FakePaperless):
    """Paperless proposes no file name."""

    @override
    async def get_document_file(
        self, document_id: int, version: FileVersion, *, max_bytes: int
    ) -> DocumentFile:
        found = await super().get_document_file(document_id, version, max_bytes=max_bytes)
        return found.model_copy(update={"filename": None})


async def test_a_file_without_a_name_has_no_filename_in_its_metadata() -> None:
    state = seeded()
    fake = Nameless(state, user="admin")

    content, report = await call("download_document", {"document_id": 1}, fake)
    resource = await read("paperless://documents/1/original", fake)

    embedded = blob_of(content[1])
    assert report["filename"] is None
    assert embedded.meta is None
    assert resource.meta is None


async def test_an_empty_thumbnail_is_an_error_not_an_empty_image() -> None:
    class Empty(FakePaperless):
        @override
        async def get_document_thumbnail(self, document_id: int) -> DocumentFile:
            found = await super().get_document_thumbnail(document_id)
            return found.model_copy(update={"content": b""})

    async with Client(serve(admin(), documents=Empty(seeded(), user="admin"))) as client:
        result = await client.call_tool(
            "get_document_thumbnail", {"document_id": 1}, raise_on_error=False
        )

    assert result.is_error
    assert "empty thumbnail" in str(result.content)


def blob_of(content: object) -> BlobResourceContents:
    assert isinstance(content, EmbeddedResource)
    assert isinstance(content.resource, BlobResourceContents)
    return content.resource
