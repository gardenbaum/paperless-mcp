"""Notes and deletion through the assembled server on Paperless 3.4.2, on documents of its own.

Every document is deleted again at the end and its trash entry emptied, so nothing is left.
"""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash, live_in_trash
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume, obj, parse, pdf, seq
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


@pytest.fixture
def document(stack: Stack) -> Iterator[int]:
    """A new document of the admin; deleted and removed from the trash again, also on failure."""
    title = f"plmcp17-{uuid.uuid4().hex}"
    _, created = consume(stack.admin, title, pdf(title))
    assert created is not None
    yield created
    delete_and_empty_trash(stack, [created])


def server(stack: Stack, token: str, *, read_only: bool = False) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, token), server_settings(read_only=read_only)
    )


async def run(stack: Stack, token: str, tool: str, **arguments: object) -> Json:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(stack: Stack, token: str, tool: str, **arguments: object) -> str:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def raw_notes(stack: Stack, document: int) -> list[Json]:
    return [obj(n) for n in seq(parse(stack.admin.get(f"/api/documents/{document}/notes/")))]


async def test_a_note_is_added_listed_and_deleted_only_with_confirm(
    stack: Stack, document: int
) -> None:
    kept = await run(stack, stack.admin_token, "add_note", document_id=document, note="keep me")
    dropped = await run(stack, stack.admin_token, "add_note", document_id=document, note="drop me")
    drop_id = cast("Json", dropped["note"])["id"]
    assert cast("Json", kept["note"])["user"] == "admin"

    unconfirmed = await refused(
        stack, stack.admin_token, "delete_note", document_id=document, note_id=drop_id
    )
    assert "Nothing was changed" in unconfirmed
    assert "confirm=true" in unconfirmed
    assert [n["note"] for n in raw_notes(stack, document)] == ["keep me", "drop me"]

    done = await run(
        stack,
        stack.admin_token,
        "delete_note",
        document_id=document,
        note_id=drop_id,
        confirm=True,
    )
    detail = await run(
        stack, stack.admin_token, "get_document", document_id=document, include=["notes"]
    )

    assert done["note_id"] == drop_id
    assert [n["note"] for n in raw_notes(stack, document)] == ["keep me"]
    assert [cast("Json", n)["note"] for n in cast("list[object]", detail["notes"])] == ["keep me"]
    assert detail["notes_count"] == 1


async def test_an_empty_note_and_an_unknown_note_are_refused_without_a_change(
    stack: Stack, document: int
) -> None:
    empty = await refused(stack, stack.admin_token, "add_note", document_id=document, note=" ")
    unknown = await refused(
        stack, stack.admin_token, "delete_note", document_id=document, note_id=999_999, confirm=True
    )

    assert "cannot be empty" in empty
    assert "no note 999999" in unknown
    assert raw_notes(stack, document) == []


async def test_a_document_goes_to_the_trash_only_with_confirm_and_can_be_restored(
    stack: Stack, document: int
) -> None:
    unconfirmed = await refused(stack, stack.admin_token, "delete_document", document_id=document)
    assert "Nothing was changed" in unconfirmed
    assert stack.admin.get(f"/api/documents/{document}/").status_code == 200
    assert not live_in_trash(stack, document)

    done = await run(
        stack, stack.admin_token, "delete_document", document_id=document, confirm=True
    )

    assert done["in_trash"] is True
    assert "trash" in str(done["message"])
    assert stack.admin.get(f"/api/documents/{document}/").status_code == 404
    assert live_in_trash(stack, document)
    restored = stack.admin.post("/api/trash/", json={"action": "restore", "documents": [document]})
    assert restored.status_code == 200
    assert stack.admin.get(f"/api/documents/{document}/").status_code == 200


async def test_a_restricted_user_is_refused_and_nothing_changes(stack: Stack) -> None:
    public = stack.seeded.public_document
    before = raw_notes(stack, public)

    note = await refused(stack, stack.reader_token, "add_note", document_id=public, note="x")
    confirmed = await refused(
        stack, stack.reader_token, "delete_document", document_id=public, confirm=True
    )

    assert "may not perform this action" in note
    assert "may not perform this action" in confirmed
    assert stack.admin.get(f"/api/documents/{public}/").status_code == 200
    assert raw_notes(stack, public) == before


async def test_a_read_only_server_offers_none_of_the_three_tools(stack: Stack) -> None:
    async with Client(server(stack, stack.admin_token, read_only=True)) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names.isdisjoint({"add_note", "delete_note", "delete_document"})
    assert "get_document" in names
