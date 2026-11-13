"""The pdf toolset through the assembled server on Paperless 3.4.2, on documents of its own.

Titles start with `plmcp23-`. Every document and version a test made is deleted again at its end
and its trash entry emptied, so nothing is left on the shared stack, also when a test fails. The
PDFs have a text layer per page, so OCR has little to do.
"""

import asyncio
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash, live_in_trash
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import ENCRYPTED_PASSWORD, ENCRYPTED_PDF, consume, consume_text, num, pdf_pages
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


class Scope:
    """The documents of one test: every id it made, deleted again at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.ids: list[int] = []

    def pdf(self, pages: int) -> int:
        title = f"plmcp23-{uuid.uuid4().hex}"
        texts = [f"{title} page {n}" for n in range(1, pages + 1)]
        _, created = consume(self.stack.admin, title, pdf_pages(texts))
        assert created is not None
        self.ids.append(created)
        return created

    def locked(self) -> int:
        title = f"plmcp23-{uuid.uuid4().hex}"
        _, created = consume(self.stack.admin, title, ENCRYPTED_PDF)
        assert created is not None
        self.ids.append(created)
        return created

    def made(self, result: Json) -> list[int]:
        """The ids a result produced, tracked for the cleanup."""
        produced = [num(n) for n in cast("list[object]", result["produced_ids"])]
        self.ids.extend(produced)
        return produced


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    delete_and_empty_trash(stack, made.ids)


def server(stack: Stack, *, read_only: bool = False) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, stack.admin_token),
        server_settings(toolsets=["pdf"], read_only=read_only),
    )


async def run(stack: Stack, tool: str, **arguments: object) -> Json:
    async with Client(server(stack)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(stack: Stack, tool: str, **arguments: object) -> str:
    async with Client(server(stack)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def document(stack: Stack, number: int) -> Json:
    return cast("Json", stack.admin.get(f"/api/documents/{number}/").raise_for_status().json())


def versions(stack: Stack, number: int) -> list[int]:
    return [
        num(cast("Json", v)["id"])
        for v in cast("list[object]", document(stack, number)["versions"])
    ]


async def test_the_main_flow_rotates_cuts_splits_and_merges_a_document(
    stack: Stack, scope: Scope
) -> None:
    source = scope.pdf(3)

    rotated = await run(
        stack, "rotate_documents", document_ids=[source], degrees=90, wait_seconds=30
    )
    assert (rotated["state"], rotated["wait"]) == ("success", "finished")
    [version] = scope.made(rotated)
    assert versions(stack, source) == [version, source]

    unconfirmed = await refused(stack, "delete_pages", document_id=source, pages="2")
    assert "Nothing was changed" in unconfirmed
    assert f"delete page 2 of document {source}" in unconfirmed
    assert len(versions(stack, source)) == 2
    too_far = await refused(stack, "delete_pages", document_id=source, pages="4", confirm=True)
    assert "Page 4 does not exist: the document has 3 pages" in too_far
    cut = await run(
        stack, "delete_pages", document_id=source, pages="2", confirm=True, wait_seconds=30
    )
    assert cut["state"] == "success"
    [shorter] = scope.made(cut)
    assert document(stack, source)["page_count"] == 2
    assert versions(stack, source) == [shorter, version, source]

    split = await run(
        stack, "split_document", document_id=source, parts=["1", "2"], wait_seconds=30
    )
    assert split["state"] == "success"
    halves = scope.made(split)
    assert [document(stack, n)["page_count"] for n in halves] == [1, 1]

    merged = await run(
        stack,
        "merge_documents",
        document_ids=halves,
        metadata_document_id=halves[0],
        wait_seconds=30,
    )
    assert merged["state"] == "success"
    [together] = scope.made(merged)
    assert document(stack, together)["page_count"] == 2
    assert [live_in_trash(stack, n) for n in halves] == [False, False]


async def test_deleting_originals_needs_confirm_and_sends_them_to_the_trash(
    stack: Stack, scope: Scope
) -> None:
    first, second = scope.pdf(1), scope.pdf(1)

    unconfirmed = await refused(
        stack, "merge_documents", document_ids=[first, second], delete_originals=True
    )
    assert "Nothing was changed" in unconfirmed
    assert not live_in_trash(stack, first)

    merged = await run(
        stack,
        "merge_documents",
        document_ids=[first, second],
        delete_originals=True,
        confirm=True,
        wait_seconds=30,
    )

    _ = scope.made(merged)
    assert merged["state"] == "success"
    # Paperless deletes the originals once the merged document exists: give it a moment.
    for _ in range(40):
        if live_in_trash(stack, first) and live_in_trash(stack, second):
            break
        await asyncio.sleep(0.5)
    assert live_in_trash(stack, first)
    assert live_in_trash(stack, second)


async def test_edit_pdf_reorders_and_turns_pages_into_a_new_document(
    stack: Stack, scope: Scope
) -> None:
    source = scope.pdf(2)

    edited = await run(
        stack,
        "edit_pdf",
        document_id=source,
        pages=[{"page": 2}, {"page": 1, "rotate": 180}],
        wait_seconds=30,
    )

    [made] = scope.made(edited)
    assert document(stack, made)["page_count"] == 2
    assert document(stack, source)["page_count"] == 2


async def test_a_password_is_removed_and_the_unlocked_copy_is_read(
    stack: Stack, scope: Scope
) -> None:
    locked = scope.locked()
    wrong = await refused(stack, "remove_document_password", document_id=locked, password="nope")
    assert "password" in wrong
    assert "nope" not in wrong

    result = await run(
        stack,
        "remove_document_password",
        document_id=locked,
        password=ENCRYPTED_PASSWORD,
        output="new_version",
        wait_seconds=30,
    )

    assert result["state"] == "success"
    [version] = scope.made(result)
    assert versions(stack, locked) == [version, locked]
    assert "secret page one" in str(document(stack, locked)["content"])
    assert ENCRYPTED_PASSWORD not in str(result)


async def test_reprocessing_needs_confirm_and_starts_a_task(stack: Stack, scope: Scope) -> None:
    number = consume_text(stack.admin, f"plmcp23-{uuid.uuid4().hex}")
    scope.ids.append(number)

    unconfirmed = await refused(stack, "reprocess_documents", document_ids=[number])
    assert "Nothing was changed" in unconfirmed

    result = await run(
        stack, "reprocess_documents", document_ids=[number], confirm=True, wait_seconds=30
    )

    assert result["state"] == "success"
    [task] = cast("list[Json]", result["tasks"])
    assert task["document_ids"] == []
