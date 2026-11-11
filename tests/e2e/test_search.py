"""search_documents through the assembled server against the seeded Paperless 3.4.2 stack."""

from datetime import date, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings

from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


async def search(stack: Stack, token: str, **arguments: object) -> list[Json]:
    server = create_server(paperless_settings(stack.base_url, token), server_settings())
    async with Client(server) as client:
        arguments = {"title_contains": "e2e-p", **arguments}
        result = await client.call_tool("search_documents", arguments)
    assert result.structured_content is not None
    return cast("list[Json]", result.structured_content["documents"])


async def titles(stack: Stack, **arguments: object) -> set[str]:
    return {str(row["title"]) for row in await search(stack, stack.admin_token, **arguments)}


async def test_name_filters_resolve_on_live_paperless(stack: Stack) -> None:
    assert await titles(stack, correspondent="E2E-ACME", tags_all=["e2e-finance"]) == {
        "e2e-public-invoice"
    }
    assert await titles(stack, owner="admin", tags_none=["e2e-inbox"]) == {"e2e-private-note"}


async def test_date_ranges_on_live_paperless(stack: Stack) -> None:
    [row] = await search(stack, stack.admin_token, title_contains="e2e-public-invoice")
    created = date.fromisoformat(str(row["created"]))
    day = timedelta(days=1)

    assert "e2e-public-invoice" in await titles(stack, created_from=str(created))
    assert "e2e-public-invoice" not in await titles(stack, created_to=str(created - day))


async def test_full_text_rows_are_readable_and_hold_no_content(stack: Stack) -> None:
    [row] = await search(stack, stack.reader_token, query="acme")

    assert row["title"] == "e2e-public-invoice"
    assert "<b>acme</b>" in str(row["highlights"]).lower()
    assert "content" not in row
    assert row["correspondent"] == {"id": stack.seeded.correspondent, "name": "e2e-acme"}
    values = {field["name"]: field["value"] for field in cast("list[Json]", row["custom_fields"])}
    assert (values["e2e-select"], values["e2e-monetary"]) == ("alpha", "EUR 12.50")
    assert str(row["web_url"]).endswith(f"/documents/{row['id']}/details")
