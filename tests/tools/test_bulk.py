"""bulk_edit_documents and bulk_edit_taxonomy through the in-memory client on the seeded fake."""

from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import admin, serve

from paperless_mcp.domain.bulk import MAX_DOCUMENTS

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

type Json = dict[str, object]

CONFIRM_DESCRIPTION = (
    "Must be true to go ahead. Without it nothing changes and the error says what would "
    "happen. Describe that to the user and pass true only once they agree, or if they "
    "already asked for exactly this."
)
TOOLS = ("bulk_edit_documents", "bulk_edit_taxonomy")
INBOX = {"op": "add_tags", "tags": ["e2e-inbox"]}


def fake(state: FakeState | None = None, *, writes: bool = True) -> FakePaperless:
    state = state or seeded()
    state.documents.append(FakeDocument(id=5, title="Second invoice"))
    return FakePaperless(state, user="admin", allow_writes=writes)


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(serve(source, toolsets=["bulk"])) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(serve(source, toolsets=["bulk"])) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def consts(operation: Json) -> list[object]:
    """The `op` values of the variants of an operation parameter, in order."""
    variants = cast("list[Json]", operation["oneOf"])
    return [cast("Json", cast("Json", v["properties"])["op"])["const"] for v in variants]


async def test_the_bulk_tools_are_offered_only_when_the_toolset_is_selected() -> None:
    async def names(**settings: object) -> set[str]:
        async with Client(serve(admin(), **cast("Mapping[str, object]", settings))) as client:  # pyright: ignore[reportArgumentType] -- keyword passthrough
            return {tool.name for tool in await client.list_tools()}

    assert set(TOOLS).isdisjoint(await names())
    assert set(TOOLS) <= await names(toolsets=["bulk"])
    assert set(TOOLS) <= await names(toolsets=["all"])
    assert set(TOOLS).isdisjoint(await names(toolsets=["all"], read_only=True))


async def test_the_input_schema_types_the_operation_and_bounds_the_selection() -> None:
    async with Client(serve(admin(), toolsets=["bulk"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    documents = tools["bulk_edit_documents"].input_schema
    properties = cast("dict[str, Json]", documents["properties"])
    assert set(properties) == {"document_ids", "operation", "dry_run", "confirm"}
    assert documents["required"] == ["document_ids", "operation"]
    assert properties["document_ids"]["maxItems"] == MAX_DOCUMENTS
    assert properties["document_ids"]["minItems"] == 1
    assert properties["confirm"]["description"] == CONFIRM_DESCRIPTION
    assert consts(properties["operation"]) == [
        "add_tags",
        "remove_tags",
        "modify_tags",
        "set_correspondent",
        "set_document_type",
        "set_storage_path",
        "modify_custom_fields",
        "delete",
        "reprocess",
    ]

    taxonomy = tools["bulk_edit_taxonomy"].input_schema
    taxonomy_properties = cast("dict[str, Json]", taxonomy["properties"])
    assert set(taxonomy_properties) == {"kind", "items", "operation", "dry_run", "confirm"}
    assert taxonomy_properties["kind"]["enum"] == [
        "tag",
        "correspondent",
        "document_type",
        "storage_path",
    ]
    assert consts(taxonomy_properties["operation"]) == ["delete", "set_permissions"]


async def test_a_dry_run_returns_the_typed_preview_and_changes_nothing() -> None:
    state = seeded()

    output = await call(
        fake(state, writes=False),
        "bulk_edit_documents",
        document_ids=[1, 5],
        operation=INBOX,
        dry_run=True,
    )

    assert (output["dry_run"], output["applied"], output["requested"]) == (True, False, 2)
    assert (output["affected_count"], output["unchanged_count"]) == (1, 1)
    [sample] = cast("list[Json]", output["sample"])
    assert (sample["id"], sample["title"], sample["change"]) == (
        5,
        "Second invoice",
        "tags +e2e-inbox",
    )
    assert sample["web_url"] == "http://paperless.test/documents/5/details"
    assert state.documents[-1].tags == []


async def test_without_confirm_the_call_is_an_error_that_says_what_would_happen() -> None:
    state = seeded()

    text = await refusal(
        fake(state, writes=False), "bulk_edit_documents", document_ids=[5], operation=INBOX
    )

    assert "Nothing was changed" in text
    assert "Second invoice" in text
    assert "confirm=true" in text


async def test_with_confirm_the_documents_are_changed_and_the_report_says_so() -> None:
    state = seeded()
    source = fake(state)

    output = await call(
        source, "bulk_edit_documents", document_ids=[1, 5], operation=INBOX, confirm=True
    )

    assert (output["applied"], output["affected_count"]) == (True, 1)
    assert state.documents[-1].tags == [4]


async def test_a_reference_is_cleared_with_an_explicit_null() -> None:
    state = seeded()

    output = await call(
        fake(state),
        "bulk_edit_documents",
        document_ids=[1],
        operation={"op": "set_correspondent", "correspondent": None},
        confirm=True,
    )

    assert output["applied"] is True
    assert state.documents[0].correspondent is None


async def test_custom_field_values_go_in_a_values_object() -> None:
    state = seeded()

    output = await call(
        fake(state),
        "bulk_edit_documents",
        document_ids=[5],
        operation={
            "op": "modify_custom_fields",
            "values": {"e2e-string": "x"},
            "remove": ["e2e-integer"],
        },
        confirm=True,
    )

    assert output["applied"] is True
    assert state.documents[-1].custom_fields == {1: "x"}


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "explode"},
        {"op": "add_tags"},
        {"op": "add_tags", "tags": []},
        {"op": "add_tags", "tags": ["a"], "surprise": 1},
        {"op": "set_correspondent"},
    ],
    ids=["unknown-op", "missing-tags", "empty-tags", "extra-field", "missing-null"],
)
async def test_a_malformed_operation_is_rejected_before_anything_runs(
    operation: Json,
) -> None:
    text = await refusal(
        fake(writes=False), "bulk_edit_documents", document_ids=[5], operation=operation
    )

    assert "operation" in text


@pytest.mark.parametrize("ids", [[], list(range(1, MAX_DOCUMENTS + 2))])
async def test_the_selection_bounds_are_enforced_by_the_schema(ids: list[int]) -> None:
    text = await refusal(
        fake(writes=False), "bulk_edit_documents", document_ids=ids, operation=INBOX
    )

    assert "document_ids" in text


async def test_taxonomy_deletion_is_previewed_refused_then_done() -> None:
    state = seeded()
    source = fake(state)
    delete = {"op": "delete"}

    preview = await call(
        source,
        "bulk_edit_taxonomy",
        kind="tag",
        items=["e2e-finance"],
        operation=delete,
        dry_run=True,
    )
    text = await refusal(
        fake(seeded(), writes=False),
        "bulk_edit_taxonomy",
        kind="tag",
        items=["e2e-finance"],
        operation=delete,
    )
    done = await call(
        source,
        "bulk_edit_taxonomy",
        kind="tag",
        items=["e2e-finance"],
        operation=delete,
        confirm=True,
    )

    assert (preview["dry_run"], preview["applied"], preview["also_deleted"]) == (
        True,
        False,
        ["e2e-invoices"],
    )
    assert "Nothing was changed" in text
    assert "confirm=true" in text
    assert (done["applied"], done["affected_count"]) == (True, 1)
    assert {t.id for t in state.tags} == {3, 4}


async def test_permissions_are_set_by_name() -> None:
    state = seeded()

    output = await call(
        fake(state),
        "bulk_edit_taxonomy",
        kind="correspondent",
        items=["e2e-acme"],
        operation={
            "op": "set_permissions",
            "owner": "e2e-reader",
            "view_groups": ["e2e-group"],
        },
        confirm=True,
    )

    assert output["applied"] is True
    acme = state.correspondents[0]
    assert (acme.owner, acme.view_groups, acme.viewers) == (3, {1}, set())


async def test_a_taxonomy_selection_over_the_limit_is_rejected() -> None:
    text = await refusal(
        fake(writes=False),
        "bulk_edit_taxonomy",
        kind="tag",
        items=list(range(1, MAX_DOCUMENTS + 2)),
        operation={"op": "delete"},
    )

    assert "items" in text
