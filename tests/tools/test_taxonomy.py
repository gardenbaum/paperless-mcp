"""list_taxonomy and the paperless://taxonomy/{kind} resource, through the in-memory client."""

import json
from typing import cast

import pytest
from fakes.accounts import READER
from fakes.paperless import FakePaperless, FakeTag, seeded
from fastmcp import Client
from harness import admin, serve
from mcp.shared.exceptions import MCPError
from mcp.types import TextResourceContents

pytestmark = pytest.mark.anyio


async def test_list_taxonomy_returns_compact_rows() -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool("list_taxonomy", {"kind": "tag", "name_contains": "E2E-IN"})

    assert result.structured_content == {
        "kind": "tag",
        "count": 2,
        "truncated": False,
        "items": [
            {
                "id": 4,
                "name": "e2e-inbox",
                "document_count": 1,
                "colour": "#a6cee3",
                "parent": None,
                "data_type": None,
                "select_options": None,
            },
            {
                "id": 2,
                "name": "e2e-invoices",
                "document_count": 1,
                "colour": "#a6cee3",
                "parent": 1,
                "data_type": None,
                "select_options": None,
            },
        ],
    }


async def test_list_taxonomy_caps_the_rows_and_says_so() -> None:
    state = seeded()
    state.tags.extend(FakeTag(id=n, name=f"bulk-{n:03}") for n in range(10, 160))

    async with Client(serve(FakePaperless(state, user="admin"))) as client:
        result = await client.call_tool("list_taxonomy", {"kind": "tag"})

    output = result.structured_content
    assert output is not None
    items = cast("list[object]", output["items"])
    assert (output["count"], output["truncated"], len(items)) == (154, True, 100)


async def test_a_kind_the_user_may_not_view_is_a_tool_error() -> None:
    async with Client(serve(FakePaperless(seeded(), user=READER[0]))) as client:
        result = await client.call_tool("list_taxonomy", {"kind": "user"}, raise_on_error=False)

    assert result.is_error
    assert "may not perform this action" in str(result.content)


async def test_the_resource_is_the_full_table_as_json() -> None:
    async with Client(serve(admin())) as client:
        contents = await client.read_resource("paperless://taxonomy/custom_field")

    [content] = contents
    assert isinstance(content, TextResourceContents)
    assert content.mime_type == "application/json"
    table = cast("list[dict[str, object]]", json.loads(content.text))
    rows = {row["name"]: row for row in table}
    assert rows["e2e-select"]["select_options"] == [
        {"id": "opt-alpha", "label": "alpha"},
        {"id": "opt-beta", "label": "beta"},
    ]
    assert len(rows) == 10


async def test_a_resource_domain_error_reaches_the_client() -> None:
    async with Client(serve(FakePaperless(seeded(), user=READER[0]))) as client:
        with pytest.raises(MCPError, match="may not perform this action"):
            _ = await client.read_resource("paperless://taxonomy/group")
