"""update_document through the in-memory client on the seeded fake."""

from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

type Json = dict[str, object]


def writer(state: FakeState | None = None) -> FakePaperless:
    return FakePaperless(state or seeded(), user="admin", allow_writes=True)


async def update(fake: FakePaperless, **arguments: object) -> Json:
    async with Client(serve(fake)) as client:
        result = await client.call_tool("update_document", {"document_id": 1, **arguments})
    assert result.structured_content is not None
    return result.structured_content


async def failure(fake: FakePaperless, **arguments: object) -> str:
    async with Client(serve(fake)) as client:
        result = await client.call_tool(
            "update_document", {"document_id": 1, **arguments}, raise_on_error=False
        )
    assert result.is_error
    return str(result.content)


async def test_one_call_changes_names_tags_and_one_custom_field_and_returns_the_detail() -> None:
    state = seeded()
    before = dict(state.documents[0].custom_fields)

    output = await update(
        writer(state),
        title="Renamed",
        correspondent="E2E-ACME",
        tags_add=["e2e-private"],
        tags_remove=["e2e-inbox"],
        custom_fields={"e2e-select": "beta", "e2e-monetary": "EUR 3.5"},
        created="2025-01-02",
        archive_serial_number=9,
    )

    assert output["title"] == "Renamed"
    assert output["created"] == "2025-01-02"
    assert output["archive_serial_number"] == 9
    assert output["correspondent"] == {"id": 1, "name": "e2e-acme"}
    assert {t["name"] for t in cast("list[Json]", output["tags"])} == {
        "e2e-finance",
        "e2e-invoices",
        "e2e-private",
    }
    values = {f["name"]: f["value"] for f in cast("list[Json]", output["custom_fields"])}
    assert values["e2e-select"] == "beta"
    assert values["e2e-monetary"] == "EUR 3.50"
    assert len(values) == len(before) == 10
    assert output["web_url"] == "http://paperless.test/documents/1/details"


async def test_clear_empties_a_reference_and_remove_custom_fields_takes_a_field_off() -> None:
    output = await update(
        writer(), clear=["correspondent", "storage_path"], remove_custom_fields=["e2e-string"]
    )

    assert output["correspondent"] is None
    assert output["storage_path"] is None
    names = {f["name"] for f in cast("list[Json]", output["custom_fields"])}
    assert "e2e-string" not in names
    assert len(names) == 9


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"tags_add": ["e2e-nope"]}, "create_missing_tags"),
        ({"custom_fields": {"e2e-integer": "many"}}, "whole number"),
        ({"custom_fields": {"e2e-select": "gamma"}, "title": "x"}, "Options"),
        ({"tags_set": ["a"], "tags_add": ["b"]}, "tags_set replaces all tags"),
        ({}, "Nothing to change"),
        ({"archive_serial_number": -1}, "greater than or equal to 0"),
        ({"clear": ["title"]}, "Input should be"),
        ({"created": "yesterday"}, "date"),
    ],
)
async def test_bad_input_is_a_tool_error_that_says_what_to_do(
    arguments: Json, message: str
) -> None:
    assert message in await failure(writer(), **arguments)


async def test_unknown_tags_are_created_when_asked() -> None:
    state = seeded()

    output = await update(writer(state), tags_add=["e2e-new"], create_missing_tags=True)

    assert "e2e-new" in {t["name"] for t in cast("list[Json]", output["tags"])}
    assert "e2e-new" in {t.name for t in state.tags}


async def test_a_user_without_the_change_permission_gets_a_permission_error() -> None:
    fake = FakePaperless(seeded(), user="e2e-reader", allow_writes=True)

    assert "may not perform this action" in await failure(fake, title="x")


async def test_the_tool_is_offered_by_default_and_hidden_in_read_only_mode() -> None:
    async with Client(serve(writer())) as client:
        offered = {t.name for t in await client.list_tools()}
    async with Client(serve(writer(), read_only=True)) as client:
        read_only = {t.name for t in await client.list_tools()}

    assert "update_document" in offered
    assert "update_document" not in read_only
    assert "get_document" in read_only


async def test_the_annotations_say_it_writes_without_destroying_and_can_repeat() -> None:
    async with Client(serve(writer())) as client:
        [tool] = [t for t in await client.list_tools() if t.name == "update_document"]

    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is False
