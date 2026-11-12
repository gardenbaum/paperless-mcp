"""add_note, delete_note and delete_document through the in-memory client on the seeded fake."""

from typing import cast

import pytest
from fakes.accounts import PUBLIC_NOTE
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

type Json = dict[str, object]

TOOLS = ("add_note", "delete_note", "delete_document")


def fake(state: FakeState | None = None, *, writes: bool = True) -> FakePaperless:
    return FakePaperless(state or seeded(), user="admin", allow_writes=writes)


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(serve(source)) as client:
        result = await client.call_tool(tool, {"document_id": 1, **arguments})
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(serve(source)) as client:
        result = await client.call_tool(tool, {"document_id": 1, **arguments}, raise_on_error=False)
    assert result.is_error
    return str(result.content)


async def test_a_note_is_added_and_returned() -> None:
    state = seeded()

    output = await call(fake(state), "add_note", note="paid by card")

    assert output["document_id"] == 1
    assert output["web_url"] == "http://paperless.test/documents/1/details"
    added = cast("Json", output["note"])
    assert (added["note"], added["user"]) == ("paid by card", "admin")
    assert [n.note for n in state.documents[0].notes] == [PUBLIC_NOTE, "paid by card"]


async def test_an_empty_note_is_a_tool_error_that_says_what_to_pass() -> None:
    text = await refusal(fake(writes=False), "add_note", note="  ")

    assert "cannot be empty" in text
    assert "Pass the text" in text


async def test_deleting_a_note_without_confirm_changes_nothing_and_says_how_to_confirm() -> None:
    state = seeded()

    text = await refusal(fake(state, writes=False), "delete_note", note_id=1)

    assert "Nothing was changed" in text
    assert "note 1" in text
    assert "confirm=true" in text
    assert [n.id for n in state.documents[0].notes] == [1]


async def test_deleting_a_note_with_confirm_removes_it() -> None:
    state = seeded()

    output = await call(fake(state), "delete_note", note_id=1, confirm=True)

    assert (output["document_id"], output["note_id"]) == (1, 1)
    assert state.documents[0].notes == []


async def test_deleting_a_document_without_confirm_changes_nothing_and_says_how_to_confirm() -> (
    None
):
    state = seeded()

    text = await refusal(fake(state, writes=False), "delete_document")

    assert "Nothing was changed" in text
    assert "e2e-public-invoice" in text
    assert "trash" in text
    assert "confirm=true" in text
    assert len(state.documents) == 2
    assert state.trash == []


async def test_deleting_a_document_with_confirm_says_it_is_in_the_trash() -> None:
    state = seeded()

    output = await call(fake(state), "delete_document", confirm=True)

    assert output["in_trash"] is True
    assert "trash" in str(output["message"])
    assert [d.id for d in state.trash] == [1]


async def test_the_tools_are_offered_by_default_and_hidden_in_read_only_mode() -> None:
    async with Client(serve(fake())) as client:
        offered = {t.name for t in await client.list_tools()}
    async with Client(serve(fake(), read_only=True)) as client:
        read_only = {t.name for t in await client.list_tools()}

    assert set(TOOLS) <= offered
    assert not set(TOOLS) & read_only


async def test_the_annotations_tell_which_tool_destroys_and_which_can_repeat() -> None:
    async with Client(serve(fake())) as client:
        tools = {t.name: t for t in await client.list_tools() if t.name in TOOLS}

    hints = {name: tool.annotations for name, tool in tools.items()}
    assert all(h is not None and h.read_only_hint is False for h in hints.values())
    assert all(h is not None and h.open_world_hint is False for h in hints.values())
    assert [(n, h.destructive_hint) for n, h in hints.items() if h] == [
        ("add_note", False),
        ("delete_note", True),
        ("delete_document", True),
    ]
    assert hints["add_note"] is not None
    assert hints["add_note"].idempotent_hint is False


async def test_the_destructive_tools_take_a_described_confirm_that_defaults_to_false() -> None:
    async with Client(serve(fake())) as client:
        tools = {t.name: t for t in await client.list_tools() if t.name in TOOLS}

    for name in ("delete_note", "delete_document"):
        properties = cast("dict[str, Json]", tools[name].input_schema["properties"])
        assert properties["confirm"]["default"] is False
        assert "true" in str(properties["confirm"]["description"])
        assert "confirm" not in tools[name].input_schema.get("required", [])
    assert "confirm" not in cast("Json", tools["add_note"].input_schema["properties"])


async def test_the_descriptions_name_the_two_step_flow_and_the_trash() -> None:
    async with Client(serve(fake())) as client:
        tools = {t.name: t.description or "" for t in await client.list_tools()}

    assert "trash" in tools["delete_document"]
    assert "restore" in tools["delete_document"]
    assert "confirm" in tools["delete_document"]
    assert "for good" in tools["delete_note"]
    assert "confirm" in tools["delete_note"]
    assert "include" in tools["delete_note"]
