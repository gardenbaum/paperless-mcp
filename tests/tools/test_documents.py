"""search_documents through the in-memory client on the seeded fake."""

from typing import cast

import pytest
from fakes.paperless import FakeDocument, FakePaperless, seeded
from fastmcp import Client
from harness import admin, serve

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
