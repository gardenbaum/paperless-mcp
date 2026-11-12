"""get_document and get_document_content through the assembled server on Paperless 3.4.2."""

import json
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from fakes.accounts import PUBLIC_NOTE
from fastmcp import Client
from harness import paperless_settings, server_settings
from mcp.shared.exceptions import MCPError
from mcp.types import TextResourceContents

from e2e.helpers import ensure_document, obj, parse
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]

MILLION = 1_000_000


def server(stack: Stack, token: str) -> FastMCP:
    return create_server(paperless_settings(stack.base_url, token), server_settings())


async def call(stack: Stack, token: str, tool: str, **arguments: object) -> Json:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def test_a_document_reads_as_names_and_readable_custom_fields_of_every_type(
    stack: Stack,
) -> None:
    detail = await call(
        stack, stack.admin_token, "get_document", document_id=stack.seeded.public_document
    )

    assert detail["title"] == "e2e-public-invoice"
    assert detail["correspondent"] == {"id": stack.seeded.correspondent, "name": "e2e-acme"}
    assert detail["document_type"] == {"id": stack.seeded.document_type, "name": "e2e-invoice"}
    assert detail["storage_path"] == {"id": stack.seeded.storage_path, "name": "e2e-archive"}
    assert detail["owner"] is None
    assert detail["notes_count"] == 1
    assert (detail["mime_type"], detail["page_count"]) == ("application/pdf", 1)
    assert str(detail["web_url"]).endswith(f"/documents/{stack.seeded.public_document}/details")
    values = {f["name"]: f["value"] for f in cast("list[Json]", detail["custom_fields"])}
    assert values == {
        "e2e-string": "hello",
        "e2e-url": "https://example.com/e2e",
        "e2e-date": "2026-01-15",
        "e2e-boolean": True,
        "e2e-integer": 42,
        "e2e-float": 3.25,
        "e2e-monetary": "EUR 12.50",
        "e2e-documentlink": [{"id": stack.seeded.private_document, "name": "e2e-private-note"}],
        "e2e-longtext": "line one\nline two",
        "e2e-select": "alpha",
    }


async def test_include_adds_every_section_for_an_admin(stack: Stack) -> None:
    detail = await call(
        stack,
        stack.admin_token,
        "get_document",
        document_id=stack.seeded.public_document,
        include=["notes", "history", "metadata", "versions", "suggestions"],
    )

    assert [n["note"] for n in cast("list[Json]", detail["notes"])] == [PUBLIC_NOTE]
    history = cast("Json", detail["history"])
    assert cast("int", history["total"]) >= 2
    assert "ACME 2026" not in json.dumps(history)
    assert cast("Json", detail["file_metadata"])["original_filename"] == "e2e-public-invoice.pdf"
    assert [v["is_root"] for v in cast("list[Json]", detail["versions"])] == [True]
    assert cast("Json", detail["suggestions"])["dates"] == []
    assert detail["unavailable"] == []


async def test_a_restricted_user_gets_what_it_may_read_and_the_rest_is_named(
    stack: Stack,
) -> None:
    detail = await call(
        stack,
        stack.reader_token,
        "get_document",
        document_id=stack.seeded.public_document,
        include=["notes", "history", "metadata"],
    )

    assert detail["unavailable"] == ["notes", "history"]
    assert cast("Json", detail["file_metadata"])["original_mime_type"] == "application/pdf"


async def test_missing_and_hidden_documents_are_not_found_with_a_hint(stack: Stack) -> None:
    for token, document_id in (
        (stack.admin_token, 999_999),
        (stack.reader_token, stack.seeded.private_document),
    ):
        async with Client(server(stack, token)) as client:
            for tool in ("get_document", "get_document_content"):
                result = await client.call_tool(
                    tool, {"document_id": document_id}, raise_on_error=False
                )
                assert result.is_error
                assert f"No document {document_id} exists that this user may see" in str(
                    result.content
                )
                assert "search_documents" in str(result.content)


async def test_a_user_without_the_document_permission_is_denied(stack: Stack) -> None:
    async with Client(server(stack, stack.blind_token)) as client:
        result = await client.call_tool(
            "get_document", {"document_id": stack.seeded.public_document}, raise_on_error=False
        )

    assert result.is_error
    assert "may not perform this action" in str(result.content)


async def test_content_comes_in_slices_and_an_offset_past_the_end_is_empty(stack: Stack) -> None:
    document_id = stack.seeded.public_document
    text = "E2E public invoice ACME 2026"

    first = await call(
        stack, stack.reader_token, "get_document_content", document_id=document_id, max_chars=10
    )
    last = await call(
        stack, stack.reader_token, "get_document_content", document_id=document_id, offset=10
    )
    beyond = await call(
        stack, stack.reader_token, "get_document_content", document_id=document_id, offset=5000
    )

    assert (first["content"], first["next_offset"], first["total_chars"]) == (
        text[:10],
        10,
        len(text),
    )
    assert (last["content"], last["next_offset"]) == (text[10:], None)
    assert (beyond["content"], beyond["total_chars"], beyond["next_offset"]) == (
        "",
        len(text),
        None,
    )


async def test_the_document_resources_answer_like_the_tools(stack: Stack) -> None:
    document_id = stack.seeded.public_document
    async with Client(server(stack, stack.reader_token)) as client:
        [detail] = await client.read_resource(f"paperless://documents/{document_id}")
        [content] = await client.read_resource(f"paperless://documents/{document_id}/content")
        with pytest.raises(MCPError, match="No document"):
            _ = await client.read_resource(f"paperless://documents/{stack.seeded.private_document}")

    assert isinstance(detail, TextResourceContents)
    assert isinstance(content, TextResourceContents)
    assert json.loads(detail.text)["title"] == "e2e-public-invoice"
    assert json.loads(content.text)["content"] == "E2E public invoice ACME 2026"


@pytest.fixture
def huge_document(stack: Stack) -> Iterator[int]:
    """A throwaway document whose text is a little over a million characters."""
    title = f"e2e-huge-{uuid.uuid4().hex[:8]}"
    document_id = ensure_document(stack.admin, title, "huge")
    try:
        _ = stack.admin.patch(
            f"/api/documents/{document_id}/",
            json={"content": "0123456789" * (MILLION // 10 + 5000)},
        ).raise_for_status()
        yield document_id
    finally:
        _ = stack.admin.delete(f"/api/documents/{document_id}/")
        _ = stack.admin.post("/api/trash/", json={"action": "empty", "documents": [document_id]})


async def test_a_document_of_over_a_million_characters_is_served_slice_by_slice(
    stack: Stack, huge_document: int
) -> None:
    total = MILLION + 50_000
    stored = obj(
        parse(stack.admin.get(f"/api/documents/{huge_document}/", params={"fields": "content"}))
    )
    text = cast("str", stored["content"])
    assert len(text) == total

    slice_size = 20_000
    last_start = total - total % slice_size  # 1_040_000: the final, shorter slice
    offset: int | None = 0
    async with Client(server(stack, stack.admin_token)) as client:

        async def read(start: int) -> Json:
            result = await client.call_tool(
                "get_document_content",
                {"document_id": huge_document, "offset": start, "max_chars": slice_size},
            )
            assert result.structured_content is not None
            assert result.structured_content["total_chars"] == total
            return result.structured_content

        for _ in range(3):  # chain the first slices through next_offset
            assert offset is not None
            piece = await read(offset)
            assert piece["content"] == text[offset : offset + slice_size]
            assert piece["next_offset"] == offset + slice_size
            offset = cast("int", piece["next_offset"])
        last = await read(last_start)

    assert last["content"] == text[last_start:]
    assert (len(cast("str", last["content"])), last["next_offset"]) == (10_000, None)
