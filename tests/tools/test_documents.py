"""search_documents through the in-memory client on the seeded fake."""

import json
import logging
from typing import TYPE_CHECKING, cast, override

import pytest
from fakes.paperless import FakeDocument, FakePaperless, seeded
from fastmcp import Client
from harness import admin, serve
from mcp.shared.exceptions import MCPError
from mcp.types import TextResourceContents

if TYPE_CHECKING:
    from paperless_mcp.domain.documents import DocumentRecord

pytestmark = pytest.mark.anyio

type Json = dict[str, object]


async def call(arguments: Json, fake: FakePaperless | None = None) -> Json:
    async with Client(serve(fake or admin())) as client:
        result = await client.call_tool("search_documents", arguments)
    assert result.structured_content is not None
    return result.structured_content


async def test_names_and_full_text_find_the_document_as_a_compact_row() -> None:
    output = await call({"query": "acme", "correspondent": "E2E-ACME", "tags_all": ["e2e-inbox"]})

    assert (output["count"], output["page"], output["next_page"]) == (1, 1, None)
    [row] = cast("list[Json]", output["documents"])
    assert row["title"] == "e2e-public-invoice"
    assert row["correspondent"] == {"id": 1, "name": "e2e-acme"}
    assert row["highlights"] == "E2E public invoice <b>ACME</b> 2026"
    assert row["web_url"] == "http://paperless.test/documents/1/details"
    assert "content" not in row


async def test_the_default_page_holds_25_documents() -> None:
    state = seeded()
    state.documents.extend(FakeDocument(id=n, title=f"bulk-{n}") for n in range(10, 40))

    output = await call({"title_contains": "bulk"}, FakePaperless(state, user="admin"))

    assert (output["count"], output["next_page"]) == (30, 2)
    assert len(cast("list[Json]", output["documents"])) == 25


async def test_a_custom_field_query_passes_through() -> None:
    output = await call({"custom_field_query": ["e2e-date", "range", ["2026-01-01", "today"]]})

    assert output["count"] == 1


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"tags_any": ["e2e-financ"]}, "Did you mean 'e2e-finance'"),
        ({"query": "x", "more_like_id": 1}, "Pass either query or more_like_id"),
        ({"custom_field_query": ["e2e-nope", "exact", 1]}, "'e2e-nope' is not a valid"),
        ({"page_size": 101}, "less than or equal to 100"),
        ({"page": 0}, "greater than or equal to 1"),
    ],
)
async def test_bad_filters_are_tool_errors_with_a_hint(arguments: Json, message: str) -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool("search_documents", arguments, raise_on_error=False)

    assert result.is_error
    assert message in str(result.content)


async def get(name: str, arguments: Json, fake: FakePaperless | None = None) -> Json:
    async with Client(serve(fake or admin())) as client:
        result = await client.call_tool(name, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def test_get_document_returns_names_readable_fields_and_a_link() -> None:
    output = await get("get_document", {"document_id": 1})

    assert output["title"] == "e2e-public-invoice"
    assert output["correspondent"] == {"id": 1, "name": "e2e-acme"}
    assert output["notes_count"] == 1
    assert output["web_url"] == "http://paperless.test/documents/1/details"
    values = {f["name"]: f["value"] for f in cast("list[Json]", output["custom_fields"])}
    assert (values["e2e-select"], values["e2e-monetary"]) == ("alpha", "EUR 12.50")
    for absent in ("content", "notes", "history", "file_metadata", "versions", "suggestions"):
        assert absent not in output or output[absent] is None
    assert output["unavailable"] == []


async def test_get_document_include_adds_the_asked_sections() -> None:
    output = await get(
        "get_document", {"document_id": 1, "include": ["notes", "history", "metadata"]}
    )

    assert [n["note"] for n in cast("list[Json]", output["notes"])] == ["e2e remark zebra"]
    history = cast("Json", output["history"])
    assert history["total"] == 2
    assert "ACME" not in str(history)
    assert cast("Json", output["file_metadata"])["original_filename"] == "e2e-public-invoice.pdf"
    assert output["versions"] is None


async def test_a_section_the_user_may_not_read_is_listed_as_unavailable() -> None:
    fake = FakePaperless(seeded(), user="e2e-reader")

    output = await get("get_document", {"document_id": 1, "include": ["notes", "metadata"]}, fake)

    assert output["unavailable"] == ["notes"]
    assert output["notes"] is None


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"document_id": 999}, "No document 999 exists that this user may see"),
        ({"document_id": 1, "include": ["content"]}, "Input should be"),
    ],
)
async def test_get_document_errors_are_tool_errors(arguments: Json, message: str) -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool("get_document", arguments, raise_on_error=False)

    assert result.is_error
    assert message in str(result.content)


async def test_a_missing_document_error_names_the_next_tool() -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool("get_document", {"document_id": 999}, raise_on_error=False)

    assert "Hint: Take the id from a search_documents row." in str(result.content)


async def test_get_document_content_returns_a_slice_with_the_next_offset() -> None:
    state = seeded()
    state.documents[0].content = "0123456789"

    output = await get(
        "get_document_content",
        {"document_id": 1, "offset": 2, "max_chars": 5},
        FakePaperless(state, user="admin"),
    )

    assert output == {
        "document_id": 1,
        "offset": 2,
        "content": "23456",
        "total_chars": 10,
        "next_offset": 7,
        "web_url": "http://paperless.test/documents/1/details",
    }


async def test_get_document_content_defaults_to_20000_characters_from_the_start() -> None:
    state = seeded()
    state.documents[0].content = "x" * 50_000

    output = await get(
        "get_document_content", {"document_id": 1}, FakePaperless(state, user="admin")
    )

    assert (output["offset"], len(cast("str", output["content"]))) == (0, 20_000)
    assert (output["total_chars"], output["next_offset"]) == (50_000, 20_000)


async def test_get_document_content_past_the_end_is_empty_with_the_total() -> None:
    output = await get("get_document_content", {"document_id": 1, "offset": 9999})

    assert (output["content"], output["total_chars"], output["next_offset"]) == ("", 28, None)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"document_id": 999}, "No document 999"),
        ({"document_id": 1, "offset": -1}, "greater than or equal to 0"),
        ({"document_id": 1, "max_chars": 0}, "greater than or equal to 1"),
        ({"document_id": 1, "max_chars": 20_001}, "less than or equal to 20000"),
    ],
)
async def test_get_document_content_errors_are_tool_errors(arguments: Json, message: str) -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool("get_document_content", arguments, raise_on_error=False)

    assert result.is_error
    assert message in str(result.content)


async def read_json(uri: str, fake: FakePaperless | None = None) -> Json:
    async with Client(serve(fake or admin())) as client:
        [content] = await client.read_resource(uri)
    assert isinstance(content, TextResourceContents)
    assert content.mime_type == "application/json"
    return cast("Json", json.loads(content.text))


async def test_the_document_resource_is_the_detail_as_json() -> None:
    assert await read_json("paperless://documents/1") == await get(
        "get_document", {"document_id": 1}
    )


async def test_the_content_resource_is_a_slice_as_json() -> None:
    state = seeded()
    state.documents[0].content = "0123456789"
    fake = FakePaperless(state, user="admin")

    first = await read_json("paperless://documents/1/content", fake)
    later = await read_json("paperless://documents/1/content?offset=4&max_chars=3", fake)

    assert (first["content"], first["next_offset"], first["total_chars"]) == (
        "0123456789",
        None,
        10,
    )
    assert (later["content"], later["offset"], later["next_offset"]) == ("456", 4, 7)


@pytest.mark.parametrize("uri", ["paperless://documents/999", "paperless://documents/999/content"])
async def test_a_resource_of_a_missing_document_is_an_error_with_the_hint(uri: str) -> None:
    async with Client(serve(admin())) as client:
        with pytest.raises(
            MCPError, match="No document 999 exists that this user may see"
        ) as raised:
            _ = await client.read_resource(uri)
    assert "Hint: Take the id from a search_documents row." in str(raised.value)


@pytest.mark.parametrize("query", ["offset=-1", "max_chars=0", "max_chars=20001"])
async def test_the_content_resource_rejects_a_slice_out_of_range(query: str) -> None:
    async with Client(serve(admin())) as client:
        with pytest.raises(MCPError, match="offset must be at least 0 and max_chars between 1"):
            _ = await client.read_resource(f"paperless://documents/1/content?{query}")


class BrokenDocuments(FakePaperless):
    """Fails like a bug in the adapter: the exception message holds response content."""

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        message = f"secret-detail of {document_id}"
        raise RuntimeError(message)


async def test_a_resource_bug_is_masked_and_logged_by_type_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = serve(admin(), documents=BrokenDocuments(seeded(), user="admin"))
    caplog.set_level(logging.DEBUG)

    async with Client(server) as client:
        with pytest.raises(MCPError) as raised:
            _ = await client.read_resource("paperless://documents/1")

    assert "Internal error in document_detail; the server log has details." in str(raised.value)
    assert "secret-detail" not in str(raised.value)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert "document_detail failed: RuntimeError" in errors
    assert not [line for line in errors if "secret-detail" in line]


async def test_a_resource_domain_error_is_logged_at_info_with_its_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    async with Client(serve(admin())) as client:
        with pytest.raises(MCPError):
            _ = await client.read_resource("paperless://documents/999")

    logged = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "document_detail: No document 999 exists" in line and "Hint:" in line for line in logged
    )
