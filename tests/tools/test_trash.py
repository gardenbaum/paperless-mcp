"""The trash toolset through the in-memory client on the seeded fake (PLMCP-27).

The toolset is off by default (the default profile stays within its tool budget). `empty_trash`
needs ids or all=true, and confirm in both cases (decision-6, decision-15).
"""

from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve

if TYPE_CHECKING:
    from fastmcp import FastMCP

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

type Json = dict[str, object]

TOOLS = frozenset({"list_trash", "restore_from_trash", "empty_trash"})
CONFIRM_DESCRIPTION = (
    "Must be true to go ahead. Without it nothing changes and the error says what would "
    "happen. Describe that to the user and pass true only once they agree, or if they "
    "already asked for exactly this."
)


def fake(state: FakeState | None = None, *, writes: bool = True) -> FakePaperless:
    return FakePaperless(state or seeded(), user="admin", allow_writes=writes)


def server(source: FakePaperless, **settings: object) -> FastMCP:
    return serve(source, toolsets=["trash"], **settings)  # pyright: ignore[reportArgumentType] -- settings


async def delete(source: FakePaperless, *documents: int) -> None:
    for document in documents:
        await source.delete_document(document)


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


# --- the toolset -------------------------------------------------------------------------------


async def test_the_trash_toolset_is_off_by_default_and_has_three_tools_when_selected() -> None:
    async with Client(serve(fake())) as client:
        default = {t.name for t in await client.list_tools()}
    async with Client(server(fake())) as client:
        selected = {t.name for t in await client.list_tools()}

    assert not TOOLS & default
    assert selected == TOOLS


async def test_read_only_mode_leaves_only_the_list() -> None:
    async with Client(serve(fake(), toolsets=["trash"], read_only=True)) as client:
        offered = {t.name for t in await client.list_tools()}

    assert offered == {"list_trash"}


async def test_the_annotations_tell_which_tool_destroys_and_which_can_repeat() -> None:
    async with Client(server(fake())) as client:
        tools = {t.name: t.annotations for t in await client.list_tools()}

    hints = {
        name: (a.read_only_hint, a.destructive_hint, a.idempotent_hint, a.open_world_hint)
        for name, a in tools.items()
        if a
    }
    assert hints == {
        "list_trash": (True, None, True, False),
        "restore_from_trash": (False, False, True, False),
        "empty_trash": (False, True, False, False),
    }


async def test_empty_trash_takes_a_described_confirm_that_defaults_to_false() -> None:
    async with Client(server(fake())) as client:
        tools = {t.name: t for t in await client.list_tools()}

    schema = tools["empty_trash"].input_schema
    properties = cast("dict[str, Json]", schema["properties"])
    assert properties["confirm"]["default"] is False
    assert properties["confirm"]["description"] == CONFIRM_DESCRIPTION
    assert schema.get("required", []) == []  # nothing is required, and nothing defaults to all
    assert properties["all"]["default"] is False
    assert properties["document_ids"]["default"] is None
    assert "never empties everything by default" in (tools["empty_trash"].description or "")


async def test_the_descriptions_say_where_ids_come_from_and_what_is_lost() -> None:
    async with Client(server(fake())) as client:
        tools = {t.name: t.description or "" for t in await client.list_tools()}

    assert "version" in tools["list_trash"]
    assert "list_trash" in tools["restore_from_trash"]
    assert "for good" in tools["empty_trash"]
    assert "confirm" in tools["empty_trash"]


# --- listing and restoring ---------------------------------------------------------------------


async def test_a_deleted_document_is_listed_and_then_restored() -> None:
    source = fake()
    await delete(source, 1)

    listed = await call(source, "list_trash")
    restored = await call(source, "restore_from_trash", document_ids=[1])

    assert (listed["count"], listed["page"], listed["next_page"]) == (1, 1, None)
    [entry] = cast("list[Json]", listed["entries"])
    assert (entry["id"], entry["title"], entry["version_of"]) == (1, "e2e-public-invoice", None)
    assert str(entry["deleted_at"]).startswith("20")
    assert [e["id"] for e in cast("list[Json]", restored["restored"])] == [1]
    assert restored["message"] == (
        "Restored 1 document from the trash: document 1 'e2e-public-invoice'."
    )
    assert (await call(source, "list_trash"))["count"] == 0


async def test_the_list_is_paged() -> None:
    source = fake()
    await delete(source, 1, 2)

    first = await call(source, "list_trash", page=1, page_size=1)

    assert (first["count"], first["next_page"], len(cast("list[Json]", first["entries"]))) == (
        2,
        2,
        1,
    )
    assert "Page 3 of the trash" in await refusal(source, "list_trash", page=3, page_size=1)


async def test_restoring_something_that_is_not_in_the_trash_is_a_tool_error_with_a_hint() -> None:
    text = await refusal(fake(), "restore_from_trash", document_ids=[1])

    assert "Not in the trash: 1." in text
    assert "Hint: List the trash with list_trash." in text


async def test_restoring_without_ids_or_with_too_many_is_refused_by_the_schema() -> None:
    async with Client(server(fake())) as client:
        for ids in ([], list(range(1, 102))):
            result = await client.call_tool(
                "restore_from_trash", {"document_ids": ids}, raise_on_error=False
            )
            assert result.is_error


# --- emptying ---------------------------------------------------------------------------------


async def test_emptying_with_nothing_named_is_a_tool_error_even_with_confirm() -> None:
    source = fake(writes=False)  # a write would raise

    nothing: list[Json] = [
        {},
        {"confirm": True},
        {"document_ids": []},
        {"all": False, "confirm": True},
    ]
    for arguments in nothing:
        text = await refusal(source, "empty_trash", **arguments)
        assert "Name what to empty: nothing was emptied." in text
        assert "Pass document_ids from list_trash, or all=true" in text


async def test_emptying_ids_without_confirm_names_what_would_be_lost_and_changes_nothing() -> None:
    source = fake()
    await delete(source, 1)

    text = await refusal(source, "empty_trash", document_ids=[1])

    assert "Nothing was changed. Confirming would permanently delete 1 entry" in text
    assert "document 1 'e2e-public-invoice'" in text
    assert "for good" in text
    assert "confirm=true" in text
    assert [d.id for d in source.state.trash] == [1]


async def test_emptying_with_confirm_deletes_the_named_entries_for_good() -> None:
    source = fake()
    await delete(source, 1, 2)

    output = await call(source, "empty_trash", document_ids=[1], confirm=True)

    assert (output["document_ids"], output["count"]) == ([1], 1)
    assert [d.id for d in source.state.trash] == [2]


async def test_all_needs_confirm_too_and_then_empties_every_entry() -> None:
    source = fake()
    await delete(source, 1, 2)

    text = await refusal(source, "empty_trash", all=True)
    assert "permanently delete ALL 2 entries of the Paperless trash" in text
    assert len(source.state.trash) == 2

    output = await call(source, "empty_trash", all=True, confirm=True)

    assert output["count"] == 2
    assert source.state.trash == []


async def test_ids_and_all_together_are_a_tool_error() -> None:
    source = fake()
    await delete(source, 1)

    text = await refusal(source, "empty_trash", document_ids=[1], all=True, confirm=True)

    assert "Pass either document_ids or all=true, not both." in text
    assert len(source.state.trash) == 1
