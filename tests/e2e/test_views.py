"""The views toolset through the assembled server against the seeded Paperless 3.4.2 stack."""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import results
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]
type Connected = Client[FastMCPTransport]

PUBLIC, PRIVATE = "e2e-public-invoice", "e2e-private-note"
# Views this module creates; the contract suite's cleanup removes leftovers of a crashed run.
SCRATCH = "contract-view-e2e-"


@pytest.fixture
async def client(stack: Stack) -> AsyncIterator[Connected]:
    server = create_server(
        paperless_settings(stack.base_url, stack.admin_token),
        server_settings(toolsets=["core", "views"]),
    )
    async with Client(server) as connected:
        yield connected
    # Whatever a test left behind, also when it failed.
    for view in results(stack.admin.get("/api/saved_views/", params={"page_size": 100})):
        if str(view["name"]).startswith(SCRATCH):
            _ = stack.admin.delete(f"/api/saved_views/{view['id']}/")


async def call(client: Connected, tool: str, **arguments: object) -> Json:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


def titles(output: Json) -> set[str]:
    return {str(row["title"]) for row in cast("list[Json]", output["documents"])}


async def test_the_seeded_view_runs_like_the_search_it_stands_for(
    client: Connected,
) -> None:
    listing = await call(client, "list_saved_views")
    viewed = await call(client, "run_saved_view", view="e2e-view", page_size=1)
    searched = await call(client, "search_documents", tags_all=["e2e-finance"], page_size=1)

    names = {str(v["name"]) for v in cast("list[Json]", listing["views"])}
    assert "e2e-view" in names
    assert viewed == searched
    assert titles(viewed) == {PUBLIC}


async def test_a_view_is_created_run_changed_and_deleted_by_name(
    client: Connected,
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    created = await call(
        client,
        "create_saved_view",
        name=name,
        rules=[
            {"rule": "has any tag", "value": True},
            {"rule": "correspondent is", "value": None},
        ],
        sort_field="title",
        sort_reverse=True,
    )
    assert [(r["rule_type"], r["value"]) for r in cast("list[Json]", created["rules"])] == [
        (7, "true"),
        (3, None),
    ]

    assert titles(await call(client, "run_saved_view", view=name)) >= {PRIVATE}
    assert PUBLIC not in titles(await call(client, "run_saved_view", view=name))

    _ = await call(
        client, "update_saved_view", view=name, rules=[{"rule": "is in inbox", "value": True}]
    )
    assert PUBLIC in titles(await call(client, "run_saved_view", view=name.upper()))

    refused = await client.call_tool("delete_saved_view", {"view": name}, raise_on_error=False)
    assert refused.is_error
    assert "Nothing was changed" in str(refused.content)
    gone = await call(client, "delete_saved_view", view=name, confirm=True)
    assert gone["name"] == name
    still = {
        str(v["name"])
        for v in cast("list[Json]", (await call(client, "list_saved_views"))["views"])
    }
    assert name not in still


async def test_a_stored_rule_that_cannot_be_run_is_an_error_naming_its_type(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    rules = [{"rule_type": 2, "value": "not a number"}]
    _ = stack.admin.post(
        "/api/saved_views/", json={"name": name, "filter_rules": rules}
    ).raise_for_status()

    result = await client.call_tool("run_saved_view", {"view": name}, raise_on_error=False)

    assert result.is_error
    assert "Filter rule type 2 (ASN is)" in str(result.content)


async def test_a_rule_with_comma_joined_ids_as_the_web_ui_writes_it_runs(
    client: Connected, stack: Stack
) -> None:
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    users = {str(row["username"]): row["id"] for row in results(stack.admin.get("/api/users/"))}
    ids = f"{users['admin']},{users['e2e-reader']}"
    rules = [{"rule_type": 35, "value": ids}, {"rule_type": 33, "value": ""}]
    _ = stack.admin.post(
        "/api/saved_views/", json={"name": name, "filter_rules": rules}
    ).raise_for_status()

    found = titles(await call(client, "run_saved_view", view=name))

    assert PUBLIC in found
    assert PRIVATE not in found
