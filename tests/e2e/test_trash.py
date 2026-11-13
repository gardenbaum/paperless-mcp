"""The trash toolset through the assembled server on Paperless 3.4.2, on documents of its own.

Titles start with `plmcp27-`; they are plain text, which skips OCR. The shared stack's trash holds
entries of other tests, so nothing here empties the whole trash: `all=true` is only called without
confirm (it must refuse and name what it would destroy), and what it does with confirm is tested
on the fake (tests/services/test_trash.py). Every document a test made is deleted again at its end
and its trash entry emptied, also when it fails.
"""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash, live_in_trash
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume_text, parse, seq
from paperless_mcp.server import create_server
from paperless_mcp.services.trash import MAX_ALL

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP
    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


class Scope:
    """The documents of one test: every id it made, deleted again at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.ids: list[int] = []

    def document(self, label: str) -> tuple[int, str]:
        title = f"plmcp27-{label}-{uuid.uuid4().hex[:8]}"
        created = consume_text(self.stack.admin, title)
        self.ids.append(created)
        return created, title


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    delete_and_empty_trash(stack, made.ids)


def server(stack: Stack, token: str, *, read_only: bool = False) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, token),
        server_settings(toolsets=["core", "trash"], read_only=read_only),
    )


async def run(client: Client[FastMCPTransport], tool: str, **arguments: object) -> Json:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(client: Client[FastMCPTransport], tool: str, **arguments: object) -> str:
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


async def listed(client: Client[FastMCPTransport], wanted: set[int]) -> dict[int, Json]:
    """The trash entries with these ids, page by page until all are found."""
    found: dict[int, Json] = {}
    page: object = 1
    while page is not None and not wanted <= found.keys():
        output = await run(client, "list_trash", page=page, page_size=100)
        found |= {
            int(cast("int", e["id"])): e
            for e in cast("list[Json]", output["entries"])
            if e["id"] in wanted
        }
        page = output["next_page"]
    return found


def note_count(stack: Stack, document: int) -> int:
    return len(seq(parse(stack.admin.get(f"/api/documents/{document}/notes/"))))


async def test_a_deleted_document_is_listed_restored_and_emptied_only_with_confirm(
    stack: Stack, scope: Scope
) -> None:
    kept, kept_title = scope.document("kept")
    gone, gone_title = scope.document("gone")
    other, other_title = scope.document("other")
    _ = stack.admin.post(f"/api/documents/{kept}/notes/", json={"note": "plmcp27 note"})
    async with Client(server(stack, stack.admin_token)) as admin:
        for document in (kept, gone, other):
            _ = await run(admin, "delete_document", document_id=document, confirm=True)

        entries = await listed(admin, {kept, gone, other})
        assert {i: e["title"] for i, e in entries.items()} == {
            kept: kept_title,
            gone: gone_title,
            other: other_title,
        }
        assert all(e["version_of"] is None for e in entries.values())

        restored = await run(admin, "restore_from_trash", document_ids=[kept])
        assert restored["message"] == (
            f"Restored 1 document from the trash: document {kept} {kept_title!r}."
        )
        [entry] = cast("list[Json]", restored["restored"])
        assert str(entry["web_url"]).endswith(f"/documents/{kept}/details")
        assert not live_in_trash(stack, kept)
        assert note_count(stack, kept) == 1
        assert "Not in the trash" in await refused(admin, "restore_from_trash", document_ids=[kept])

        # Emptying needs a selection and confirm, in both forms; nothing changed so far.
        assert "Name what to empty" in await refused(admin, "empty_trash", confirm=True)
        asked = await refused(admin, "empty_trash", document_ids=[gone, other])
        assert f"document {gone} {gone_title!r}" in asked
        assert f"document {other} {other_title!r}" in asked
        assert "for good" in asked
        # all=true is refused without confirm either way; which refusal depends on the size of
        # the shared stack's trash, which is read first.
        total = (await run(admin, "list_trash", page_size=1))["count"]
        everything = await refused(admin, "empty_trash", all=True)
        if cast("int", total) > MAX_ALL:
            assert f"all=true handles at most {MAX_ALL} at once" in everything
        else:
            assert "Nothing was changed. Confirming would permanently delete ALL " in everything
        assert live_in_trash(stack, gone)
        assert live_in_trash(stack, other)

        emptied = await run(admin, "empty_trash", document_ids=[gone], confirm=True)
        assert (emptied["document_ids"], emptied["count"]) == ([gone], 1)
    assert not live_in_trash(stack, gone)
    assert live_in_trash(stack, other)
    assert stack.admin.get(f"/api/documents/{gone}/").status_code == 404

    # Someone who cannot see an entry cannot empty it either.
    async with Client(server(stack, stack.reader_token)) as reader:
        hidden = await refused(reader, "empty_trash", document_ids=[other], confirm=True)
    assert f"Not in the trash: {other}" in hidden
    assert live_in_trash(stack, other)


async def test_read_only_mode_offers_only_the_list(stack: Stack) -> None:
    async with Client(server(stack, stack.admin_token, read_only=True)) as client:
        tools = {t.name for t in await client.list_tools()}
        result = await client.call_tool("list_trash", {"page_size": 1})

    assert {"list_trash"} <= tools
    assert not tools & {"restore_from_trash", "empty_trash"}
    assert result.structured_content is not None
    assert result.structured_content["page"] == 1
