"""Thumbnails and downloads through the assembled server on Paperless 3.4.2, with real uploads."""

import base64
import uuid
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings
from mcp.shared.exceptions import MCPError
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent

from e2e.helpers import consume, pdf
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]

NON_ASCII = "Rechnung für Müller 日本語 €"


class Upload(Protocol):
    def __call__(self, title: str, filename: str, content: bytes, mime_type: str) -> int: ...


def server(stack: Stack, *, max_download_bytes: int | None = None) -> FastMCP:
    settings = paperless_settings(stack.base_url, stack.admin_token)
    return create_server(settings, server_settings(max_download_bytes=max_download_bytes))


@pytest.fixture
def upload(stack: Stack) -> Iterator[Upload]:
    """Uploads documents and waits for their consumption; deletes them when the test ends."""
    created: list[int] = []

    def upload_one(title: str, filename: str, content: bytes, mime_type: str) -> int:
        _, document = consume(stack.admin, title, content, filename=filename, mime_type=mime_type)
        assert document is not None, f"Consuming {title!r} failed"
        created.append(document)
        return document

    try:
        yield upload_one
    finally:
        for document in created:
            _ = stack.admin.delete(f"/api/documents/{document}/")


def blob(content: object) -> BlobResourceContents:
    assert isinstance(content, EmbeddedResource)
    assert isinstance(content.resource, BlobResourceContents)
    return content.resource


def named(file: BlobResourceContents) -> str:
    """The file name the server put in the content's metadata."""
    return cast("str", (file.meta or {})["filename"])


async def test_the_seeded_thumbnail_is_a_webp_image(stack: Stack) -> None:
    async with Client(server(stack)) as client:
        result = await client.call_tool(
            "get_document_thumbnail", {"document_id": stack.seeded.public_document}
        )

    image = result.content[1]
    assert isinstance(image, ImageContent)
    assert image.mime_type == "image/webp"
    assert base64.b64decode(image.data)[8:12] == b"WEBP"
    assert result.structured_content is not None
    assert result.structured_content["delivered"] is True


async def test_the_seeded_original_and_archive_are_pdfs(stack: Stack) -> None:
    number = stack.seeded.public_document
    async with Client(server(stack)) as client:
        original = await client.call_tool("download_document", {"document_id": number})
        archive = await client.call_tool(
            "download_document", {"document_id": number, "kind": "archive"}
        )

    for result, kind in ((original, "original"), (archive, "archive")):
        file = blob(result.content[1])
        assert (file.mime_type, file.uri) == (
            "application/pdf",
            f"paperless://documents/{number}/{kind}",
        )
        assert base64.b64decode(file.blob).startswith(b"%PDF")
        assert result.structured_content is not None
        assert named(file).endswith("e2e-public-invoice.pdf")
        assert named(file) == result.structured_content["filename"]
    assert blob(original.content[1]).blob != blob(archive.content[1]).blob


async def test_a_non_ascii_upload_keeps_its_name_in_tools_and_resources(
    stack: Stack, upload: Upload
) -> None:
    title = f"{NON_ASCII} {uuid.uuid4().hex[:12]}"
    number = upload(title, f"{NON_ASCII}.pdf", pdf(uuid.uuid4().hex), "application/pdf")

    async with Client(server(stack)) as client:
        downloaded = await client.call_tool("download_document", {"document_id": number})
        [original] = await client.read_resource(f"paperless://documents/{number}/original")
        [archive] = await client.read_resource(f"paperless://documents/{number}/archive")

    assert downloaded.structured_content is not None
    file = blob(downloaded.content[1])
    assert named(file).endswith(f" {title}.pdf")
    assert named(file) == downloaded.structured_content["filename"]
    for resource in (original, archive):
        assert isinstance(resource, BlobResourceContents)
        assert resource.mime_type == "application/pdf"
        assert named(resource) == downloaded.structured_content["filename"]


async def test_a_document_without_archive_version_downloads_only_its_original(
    stack: Stack, upload: Upload
) -> None:
    title = f"kein-archiv-ä {uuid.uuid4().hex[:12]}"
    number = upload(title, "notiz-ä.txt", title.encode(), "text/plain")

    async with Client(server(stack)) as client:
        original = await client.call_tool("download_document", {"document_id": number})
        failed = await client.call_tool(
            "download_document", {"document_id": number, "kind": "archive"}, raise_on_error=False
        )
        with pytest.raises(MCPError, match=f"Document {number} has no archive version"):
            _ = await client.read_resource(f"paperless://documents/{number}/archive")

    file = blob(original.content[1])
    assert file.mime_type == "text/plain; charset=utf-8"
    assert base64.b64decode(file.blob).decode() == title
    assert failed.is_error
    assert f"Document {number} has no archive version. Hint: Ask for the original" in str(
        failed.content
    )


async def test_a_file_over_the_limit_is_reported_without_content(stack: Stack) -> None:
    number = stack.seeded.public_document
    async with Client(server(stack, max_download_bytes=100)) as client:
        result = await client.call_tool(
            "download_document", {"document_id": number, "kind": "archive"}
        )
        with pytest.raises(MCPError, match="over the download limit of 100 bytes"):
            _ = await client.read_resource(f"paperless://documents/{number}/archive")

    assert len(result.content) == 1
    report = cast("Json", result.structured_content)
    assert (report["delivered"], report["max_bytes"]) == (False, 100)
    assert cast("int", report["size_bytes"]) > 100
    assert str(report["web_url"]).endswith(f"/documents/{number}/details")


async def test_a_missing_document_is_a_tool_error(stack: Stack) -> None:
    async with Client(server(stack)) as client:
        result = await client.call_tool(
            "get_document_thumbnail", {"document_id": 999_999}, raise_on_error=False
        )

    assert result.is_error
    assert "No document 999999 exists that this user may see" in str(result.content)
