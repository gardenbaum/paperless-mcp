"""ask_paperless_ai and get_ai_suggestions through the in-memory client on the seeded fake."""

from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeUser, seeded
from fastmcp import Client
from harness import admin, serve

from paperless_mcp.domain.ai import AiSuggestions

pytestmark = pytest.mark.anyio

type Json = dict[str, object]


async def run(
    name: str, arguments: Json, fake: FakePaperless | None = None, *, raises: bool = True
) -> Json:
    async with Client(serve(fake or admin(ai_enabled=True))) as client:
        result = await client.call_tool(name, arguments, raise_on_error=raises)
    if not raises:
        return {"is_error": result.is_error, "text": cast("Json", result.content[0].model_dump())}
    assert result.structured_content is not None
    return result.structured_content


async def error_text(name: str, arguments: Json, fake: FakePaperless) -> str:
    output = await run(name, arguments, fake, raises=False)
    assert output["is_error"] is True
    return str(cast("Json", output["text"])["text"])


async def names(fake: FakePaperless, **settings: object) -> set[str]:
    async with Client(serve(fake, **settings)) as client:  # pyright: ignore[reportArgumentType] -- harness keywords
        return {tool.name for tool in await client.list_tools()}


async def test_a_question_is_answered_with_its_sources_and_links() -> None:
    output = await run("ask_paperless_ai", {"question": "acme"})

    assert "e2e-public-invoice" in str(output["answer"])
    assert output["truncated"] is False
    assert output["sources"] == [
        {
            "id": 1,
            "title": "e2e-public-invoice",
            "web_url": "http://paperless.test/documents/1/details",
        }
    ]


async def test_a_question_about_one_document_stays_with_that_document() -> None:
    output = await run("ask_paperless_ai", {"question": "e2e", "document_id": 1})

    assert [source["id"] for source in cast("list[Json]", output["sources"])] == [1]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"question": "   "}, "The question is empty"),
        ({"question": "x" * 4001}, "at most 4000"),
        ({"question": "acme", "document_id": 999}, "No document 999"),
    ],
)
async def test_bad_questions_are_tool_errors(arguments: Json, message: str) -> None:
    assert message in await error_text("ask_paperless_ai", arguments, admin(ai_enabled=True))


async def test_the_tools_report_disabled_ai_when_the_capability_is_unknown() -> None:
    state = seeded()
    state.ai_enabled = False
    state.users.append(
        FakeUser(id=9, username="no-settings", permissions=frozenset({"view_document"}))
    )
    unknown = FakePaperless(state, user="no-settings")  # may not view UI settings: ai_enabled null

    assert await names(unknown) >= {"ask_paperless_ai", "get_ai_suggestions"}
    calls: list[tuple[str, Json]] = [
        ("ask_paperless_ai", {"question": "acme"}),
        ("get_ai_suggestions", {"document_id": 1}),
    ]
    for name, arguments in calls:
        assert "Paperless AI is not enabled" in await error_text(name, arguments, unknown)


async def test_suggestions_are_returned_with_names() -> None:
    fake = admin(ai_enabled=True)
    fake.state.documents[0].ai_suggestions = AiSuggestions(
        title="Invoice March",
        correspondents=(1,),
        new_correspondents=("Acme Ltd",),
        tags=(2,),
        new_tags=(),
        document_types=(),
        new_document_types=(),
        storage_paths=(),
        new_storage_paths=("Taxes",),
        dates=("2026-03-01",),
    )

    output = await run("get_ai_suggestions", {"document_id": 1}, fake)

    assert output["document_id"] == 1
    assert output["web_url"] == "http://paperless.test/documents/1/details"
    assert output["title"] == "Invoice March"
    assert output["correspondents"] == [{"id": 1, "name": "e2e-acme"}]
    assert output["tags"] == [{"id": 2, "name": "e2e-invoices"}]
    assert output["new_correspondents"] == ["Acme Ltd"]
    assert output["new_storage_paths"] == ["Taxes"]
    assert output["dates"] == ["2026-03-01"]


async def test_suggestions_of_a_missing_document_are_a_tool_error() -> None:
    message = await error_text("get_ai_suggestions", {"document_id": 999}, admin(ai_enabled=True))

    assert "No document 999" in message


# --- Visibility: the toolset is in the default profile and follows the capability ---------------


async def test_the_ai_toolset_is_offered_by_default_when_ai_is_enabled() -> None:
    assert {"ask_paperless_ai", "get_ai_suggestions"} <= await names(admin(ai_enabled=True))


async def test_the_ai_toolset_is_hidden_by_default_when_ai_is_disabled() -> None:
    offered = await names(admin(ai_enabled=False))

    assert offered
    assert not {"ask_paperless_ai", "get_ai_suggestions"} & offered


async def test_the_ai_tools_survive_read_only_mode_because_they_change_nothing() -> None:
    offered = await names(admin(ai_enabled=True), read_only=True)

    assert {"ask_paperless_ai", "get_ai_suggestions"} <= offered
