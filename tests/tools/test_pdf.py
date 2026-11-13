"""The pdf tools through the in-memory client on the seeded fake (PLMCP-23)."""

from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import admin, serve

from paperless_mcp.services.pdf import MAX_DOCUMENTS, MAX_PAGES, MAX_PARTS

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

type Json = dict[str, object]

TOOLS = (
    "rotate_documents",
    "merge_documents",
    "split_document",
    "delete_pages",
    "edit_pdf",
    "remove_document_password",
    "reprocess_documents",
)
CONFIRM_DESCRIPTION = (
    "Must be true to go ahead. Without it nothing changes and the error says what would "
    "happen. Describe that to the user and pass true only once they agree, or if they "
    "already asked for exactly this."
)
FIRST, SECOND, LOCKED = 10, 11, 12


def fake(*, writes: bool = True) -> FakePaperless:
    state: FakeState = seeded()
    state.documents.extend(
        [
            FakeDocument(id=FIRST, title="Scan one", owner=2, page_count=4),
            FakeDocument(id=SECOND, title="Scan two", owner=2, page_count=2),
            FakeDocument(id=LOCKED, title="Locked", owner=2, page_count=None, password="s3cret"),
        ]
    )
    return FakePaperless(state, user="admin", allow_writes=writes)


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(serve(source, toolsets=["pdf"])) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(serve(source, toolsets=["pdf"])) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


async def test_the_pdf_tools_are_offered_only_when_the_toolset_is_selected() -> None:
    async def names(**settings: object) -> set[str]:
        async with Client(serve(admin(), **cast("Mapping[str, object]", settings))) as client:  # pyright: ignore[reportArgumentType] -- keyword passthrough
            return {tool.name for tool in await client.list_tools()}

    assert set(TOOLS).isdisjoint(await names())
    assert set(TOOLS) <= await names(toolsets=["pdf"])
    assert set(TOOLS) <= await names(toolsets=["all"])
    assert set(TOOLS).isdisjoint(await names(toolsets=["all"], read_only=True))


async def test_the_input_schemas_bound_the_selections() -> None:
    async with Client(serve(admin(), toolsets=["pdf"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    def properties(tool: str) -> dict[str, Json]:
        return cast("dict[str, Json]", tools[tool].input_schema["properties"])

    rotate = properties("rotate_documents")
    assert set(rotate) == {"document_ids", "degrees", "wait_seconds"}
    assert (rotate["document_ids"]["minItems"], rotate["document_ids"]["maxItems"]) == (
        1,
        MAX_DOCUMENTS,
    )
    assert rotate["degrees"]["enum"] == [90, 180, 270]
    merge = properties("merge_documents")
    assert set(merge) == {
        "document_ids",
        "metadata_document_id",
        "delete_originals",
        "confirm",
        "wait_seconds",
    }
    assert merge["document_ids"]["minItems"] == 2
    assert merge["confirm"]["description"] == CONFIRM_DESCRIPTION
    split = properties("split_document")
    assert set(split) == {"document_id", "parts", "output", "confirm", "wait_seconds"}
    assert (split["parts"]["minItems"], split["parts"]["maxItems"]) == (2, MAX_PARTS)
    assert split["output"]["enum"] == ["new_document", "replace_original"]
    pages = properties("delete_pages")
    assert set(pages) == {"document_id", "pages", "output", "confirm", "wait_seconds"}
    assert pages["output"]["enum"] == ["new_document", "new_version", "replace_original"]
    assert pages["output"]["default"] == "new_version"
    edit = properties("edit_pdf")
    assert set(edit) == {"document_id", "pages", "output", "confirm", "wait_seconds"}
    assert (edit["pages"]["minItems"], edit["pages"]["maxItems"]) == (1, MAX_PAGES)
    password = properties("remove_document_password")
    assert set(password) == {"document_id", "password", "output", "confirm", "wait_seconds"}
    assert password["password"]["minLength"] == 1
    reprocess = properties("reprocess_documents")
    assert set(reprocess) == {"document_ids", "remote_ocr", "confirm", "wait_seconds"}
    for tool in TOOLS:
        wait = properties(tool)["wait_seconds"]
        assert (wait["minimum"], wait["default"]) == (0, 0)


async def test_rotating_returns_the_result_of_the_operation() -> None:
    source = fake()

    result = await call(source, "rotate_documents", document_ids=[FIRST, SECOND], degrees=90)

    assert result["operation"] == "rotate"
    assert result["summary"] == "rotate 2 documents by 90 degrees clockwise"
    assert result["source_document_ids"] == [FIRST, SECOND]
    assert (result["state"], result["wait"]) == ("success", "not_requested")
    assert len(cast("list[Json]", result["tasks"])) == 2


async def test_a_wait_returns_the_finished_tasks_and_the_documents_they_made() -> None:
    source = fake()

    result = await call(source, "merge_documents", document_ids=[FIRST, SECOND], wait_seconds=5)

    assert (result["state"], result["wait"]) == ("success", "finished")
    [made] = cast("list[int]", result["produced_ids"])
    assert [d.page_count for d in source.state.documents if d.id == made] == [6]


async def test_deleting_the_originals_of_a_merge_is_refused_without_confirm() -> None:
    source = fake(writes=False)

    text = await refusal(
        source, "merge_documents", document_ids=[FIRST, SECOND], delete_originals=True
    )

    assert "Nothing was changed. Confirming would merge" in text
    assert "confirm=true" in text
    assert source.state.trash == []


async def test_a_confirmed_merge_trashes_the_originals() -> None:
    source = fake()

    _ = await call(
        source,
        "merge_documents",
        document_ids=[FIRST, SECOND],
        delete_originals=True,
        confirm=True,
    )

    assert {d.id for d in source.state.trash} == {FIRST, SECOND}


async def test_splitting_makes_a_document_per_part() -> None:
    source = fake()

    result = await call(
        source, "split_document", document_id=FIRST, parts=["1-2", "3-4"], wait_seconds=5
    )

    assert len(cast("list[int]", result["produced_ids"])) == 2
    assert (
        result["summary"] == f"split document {FIRST} into 2 new documents: pages 1-2 | pages 3-4"
    )


async def test_replacing_the_original_by_a_split_needs_confirm() -> None:
    text = await refusal(
        fake(writes=False),
        "split_document",
        document_id=FIRST,
        parts=["1-2", "3-4"],
        output="replace_original",
    )

    assert "move the original to the Paperless trash" in text


async def test_deleting_pages_needs_confirm_and_then_makes_a_shorter_version() -> None:
    text = await refusal(fake(writes=False), "delete_pages", document_id=FIRST, pages="2")
    assert "delete page 2 of document 10 'Scan one'" in text

    source = fake()
    result = await call(source, "delete_pages", document_id=FIRST, pages="2", confirm=True)

    assert result["summary"] == f"delete page 2 of document {FIRST} as a new version"
    assert [d.page_count for d in source.state.documents if d.id == FIRST] == [3]


async def test_a_page_beyond_the_end_is_refused_naming_the_page_count() -> None:
    text = await refusal(fake(writes=False), "delete_pages", document_id=SECOND, pages="3")

    assert "Page 3 does not exist: the document has 2 pages" in text


async def test_an_edit_takes_pages_with_rotation_and_output_file() -> None:
    source = fake()

    result = await call(
        source,
        "edit_pdf",
        document_id=FIRST,
        pages=[{"page": 4}, {"page": 1, "rotate": 90}, {"page": 2, "document": 1}],
        wait_seconds=5,
    )

    assert len(cast("list[int]", result["produced_ids"])) == 2


async def test_removing_a_password_keeps_the_password_out_of_the_result() -> None:
    source = fake()

    result = await call(
        source, "remove_document_password", document_id=LOCKED, password="s3cret", wait_seconds=5
    )

    assert result["state"] == "success"
    assert "s3cret" not in str(result)


async def test_a_wrong_password_is_an_error_that_does_not_repeat_it() -> None:
    text = await refusal(
        fake(), "remove_document_password", document_id=LOCKED, password="guess-1234"
    )

    assert "password" in text
    assert "guess-1234" not in text


async def test_reprocessing_needs_confirm_and_then_starts_the_tasks() -> None:
    text = await refusal(fake(writes=False), "reprocess_documents", document_ids=[FIRST])
    assert "reprocess document 10 'Scan one'" in text

    source = fake()
    result = await call(
        source, "reprocess_documents", document_ids=[FIRST], confirm=True, wait_seconds=5
    )

    assert (result["state"], result["produced_ids"]) == ("success", [])
    assert source.state.reprocessed == [FIRST]
