"""create, update and delete_taxonomy_item through the in-memory client on the seeded fake."""

from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-14"),
]

type Json = dict[str, object]


def writer(state: FakeState | None = None, user: str = "admin") -> FakePaperless:
    return FakePaperless(state or seeded(), user=user, allow_writes=True)


async def call(fake: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(serve(fake)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def failure(fake: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(serve(fake)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def options(output: Json) -> list[tuple[str, str]]:
    found = cast("list[dict[str, str]]", output["select_options"])
    return [(str(o["id"]), str(o["label"])) for o in found]


async def test_a_created_tag_comes_back_with_its_settings_and_is_listed_at_once() -> None:
    fake = writer()
    async with Client(serve(fake)) as client:
        before = await client.call_tool("list_taxonomy", {"kind": "tag", "name_contains": "rec"})
        created = await client.call_tool(
            "create_taxonomy_item",
            {
                "kind": "tag",
                "name": "Receipts",
                "colour": "#112233",
                "parent": "e2e-finance",
                "matching_algorithm": "any",
                "match": "receipt",
            },
        )
        after = await client.call_tool("list_taxonomy", {"kind": "tag", "name_contains": "rec"})

    assert created.structured_content is not None
    output = created.structured_content
    assert (output["name"], output["colour"], output["parent"], output["document_count"]) == (
        "Receipts",
        "#112233",
        1,
        0,
    )
    assert (output["matching_algorithm"], output["match"]) == ("any", "receipt")
    assert cast("Json", before.structured_content)["count"] == 0
    assert cast("Json", after.structured_content)["count"] == 1


async def test_a_new_object_matches_nothing_by_itself_unless_asked() -> None:
    output = await call(writer(), "create_taxonomy_item", kind="correspondent", name="Acme Corp")

    assert (output["matching_algorithm"], output["match"]) == ("none", "")


async def test_a_storage_path_and_a_select_field_are_created_with_their_specifics() -> None:
    fake = writer()

    path = await call(
        fake, "create_taxonomy_item", kind="storage_path", name="By year", path="{{ created_year }}"
    )
    field = await call(
        fake,
        "create_taxonomy_item",
        kind="custom_field",
        name="Rating",
        data_type="select",
        select_options=["good", "bad"],
    )

    assert path["path"] == "{{ created_year }}"
    assert field["data_type"] == "select"
    assert [label for _, label in options(field)] == ["good", "bad"]


async def test_a_refused_create_is_a_tool_error_with_message_and_hint() -> None:
    text = await failure(writer(), "create_taxonomy_item", kind="storage_path", name="x", path=" ")

    assert "needs a path template" in text
    assert "Hint:" in text
    assert "created_year" in text


async def test_a_setting_of_another_kind_is_a_tool_error() -> None:
    text = await failure(
        writer(), "create_taxonomy_item", kind="correspondent", name="x", colour="#112233"
    )

    assert "colour is not a setting of a correspondent" in text


async def test_a_kind_that_cannot_be_written_is_refused_by_the_schema() -> None:
    text = await failure(writer(), "create_taxonomy_item", kind="saved_view", name="x")

    assert "kind" in text


async def test_an_update_finds_the_object_by_name_and_changes_only_what_is_named() -> None:
    fake = writer()

    output = await call(
        fake, "update_taxonomy_item", kind="tag", item="E2E-INBOX", colour="#445566"
    )

    assert (output["id"], output["name"], output["colour"]) == (4, "e2e-inbox", "#445566")
    assert output["is_inbox_tag"] is True
    assert output["document_count"] == 1


async def test_a_parent_is_set_by_name_and_cleared_with_clear_parent() -> None:
    fake = writer()

    moved = await call(fake, "update_taxonomy_item", kind="tag", item=4, parent="e2e-finance")
    freed = await call(fake, "update_taxonomy_item", kind="tag", item=4, clear_parent=True)

    assert (moved["parent"], freed["parent"]) == (1, None)


async def test_select_options_are_added_and_renamed_with_their_ids_kept() -> None:
    fake = writer()
    [(alpha, _), (beta, _)] = options(
        await call(
            fake, "update_taxonomy_item", kind="custom_field", item="e2e-select", name="e2e-select"
        )
    )

    output = await call(
        fake,
        "update_taxonomy_item",
        kind="custom_field",
        item="e2e-select",
        select_options_add=["gamma"],
        select_options_rename={"alpha": "ALPHA"},
    )

    [first, second, third] = options(output)
    assert (first, second) == ((alpha, "ALPHA"), (beta, "beta"))
    assert third[1] == "gamma"


async def test_removing_an_option_needs_confirm() -> None:
    fake = writer()

    text = await failure(
        fake,
        "update_taxonomy_item",
        kind="custom_field",
        item="e2e-select",
        select_options_remove=["beta"],
    )
    output = await call(
        fake,
        "update_taxonomy_item",
        kind="custom_field",
        item="e2e-select",
        select_options_remove=["beta"],
        confirm=True,
    )

    assert "confirm=true" in text
    assert [label for _, label in options(output)] == ["alpha"]


async def test_nothing_to_change_is_a_tool_error() -> None:
    text = await failure(writer(), "update_taxonomy_item", kind="tag", item=1)

    assert "Nothing to change" in text


async def test_a_delete_without_confirm_is_an_error_that_says_the_effect() -> None:
    state = seeded()
    reader = FakePaperless(state, user="admin")  # created without allow_writes: a write would fail
    async with Client(serve(reader)) as client:
        result = await client.call_tool(
            "delete_taxonomy_item", {"kind": "tag", "item": "e2e-finance"}, raise_on_error=False
        )
        listed = await client.call_tool(
            "list_taxonomy", {"kind": "tag", "name_contains": "e2e-fin"}
        )

    assert result.is_error
    text = str(result.content)
    assert "Nothing was changed. Confirming would delete tag 'e2e-finance' (id 1) for good" in text
    assert "'e2e-invoices (1 document)' are deleted with it" in text
    assert "confirm" in text
    assert cast("Json", listed.structured_content)["count"] == 1


async def test_a_confirmed_delete_deletes_and_the_list_shows_it() -> None:
    fake = writer()
    async with Client(serve(fake)) as client:
        _ = await client.call_tool("list_taxonomy", {"kind": "correspondent"})
        result = await client.call_tool(
            "delete_taxonomy_item", {"kind": "correspondent", "item": "e2e-acme", "confirm": True}
        )
        listed = await client.call_tool("list_taxonomy", {"kind": "correspondent"})

    output = result.structured_content
    assert output is not None
    assert (output["name"], output["document_count"]) == ("e2e-acme", 1)
    assert cast("Json", listed.structured_content)["count"] == 0
    assert fake.state.documents[0].correspondent is None


async def test_the_write_tools_are_refused_for_a_user_without_the_permission() -> None:
    reader = writer(user="e2e-reader")

    created = await failure(reader, "create_taxonomy_item", kind="tag", name="x")
    updated = await failure(reader, "update_taxonomy_item", kind="tag", item=1, name="x")
    deleted = await failure(reader, "delete_taxonomy_item", kind="tag", item=1, confirm=True)

    for text in (created, updated, deleted):
        assert "may not perform" in text


@pytest.mark.parametrize("tool", ["update_taxonomy_item", "delete_taxonomy_item"])
async def test_confirm_is_the_shared_parameter_that_defaults_to_false(tool: str) -> None:
    async with Client(serve(writer())) as client:
        [found] = [t for t in await client.list_tools() if t.name == tool]

    confirm = cast("Json", cast("Json", found.input_schema["properties"])["confirm"])
    assert confirm["default"] is False
    assert str(confirm["description"]).startswith("Must be true to go ahead.")
