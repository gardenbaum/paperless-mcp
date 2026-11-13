"""The views toolset through the in-memory client on the seeded fake."""

from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeSavedView, FakeState, seeded
from fastmcp import Client
from harness import serve

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-5"),
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-14"),
]

type Json = dict[str, object]

TOOLS = {
    "list_saved_views",
    "get_saved_view",
    "create_saved_view",
    "update_saved_view",
    "delete_saved_view",
    "run_saved_view",
}


def fake(
    state: FakeState | None = None, *, writes: bool = True, user: str = "admin"
) -> FakePaperless:
    return FakePaperless(state or seeded(), user=user, allow_writes=writes)


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(serve(source, toolsets=["core", "views"])) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(serve(source, toolsets=["core", "views"])) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def titles(output: Json) -> list[str]:
    return [str(d["title"]) for d in cast("list[Json]", output["documents"])]


async def test_the_views_are_not_in_the_default_profile() -> None:
    async with Client(serve(fake())) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names.isdisjoint(TOOLS)


async def test_the_views_toolset_offers_six_tools_and_read_only_mode_keeps_three() -> None:
    async with Client(serve(fake(), toolsets=["views"])) as client:
        names = {tool.name for tool in await client.list_tools()}
    async with Client(serve(fake(), toolsets=["views"], read_only=True)) as client:
        read_only = {tool.name for tool in await client.list_tools()}

    assert names == TOOLS
    assert read_only == {"list_saved_views", "get_saved_view", "run_saved_view"}


async def test_the_saved_views_are_listed_with_rule_labels() -> None:
    output = await call(fake(), "list_saved_views")

    assert output["count"] == 1
    [view] = cast("list[Json]", output["views"])
    assert view["name"] == "e2e-view"
    assert view["rules"] == [{"rule_type": 6, "name": "has tag", "value": "1"}]


async def test_a_view_is_read_by_name_or_id() -> None:
    by_name = await call(fake(), "get_saved_view", view="E2E-VIEW")
    by_id = await call(fake(), "get_saved_view", view=1)

    assert by_name == by_id
    assert by_name["can_change"] is True


async def test_a_view_is_created_with_rules_by_label_and_name_and_run() -> None:
    source = fake()
    created = await call(
        source,
        "create_saved_view",
        name="Acme invoices",
        rules=[
            {"rule": "correspondent is", "value": "e2e-acme"},
            {"rule": "has tag", "value": "e2e-invoices"},
            {"rule": "is in inbox", "value": True},
        ],
        sort_field="title",
    )

    assert [(r["rule_type"], r["value"]) for r in cast("list[Json]", created["rules"])] == [
        (3, "1"),
        (6, "2"),
        (5, "true"),
    ]
    assert titles(await call(source, "run_saved_view", view="Acme invoices")) == [
        "e2e-public-invoice"
    ]


async def test_a_view_is_updated_renamed_and_its_rules_replaced() -> None:
    source = fake()

    output = await call(
        source,
        "update_saved_view",
        view="e2e-view",
        name="renamed",
        rules=[{"rule": 6, "value": "e2e-private"}],
        clear_sort=True,
    )

    assert output["name"] == "renamed"
    assert [(r["rule_type"], r["value"]) for r in cast("list[Json]", output["rules"])] == [(6, "3")]


async def test_deleting_without_confirm_is_an_error_that_says_the_effect_and_changes_nothing() -> (
    None
):
    state = seeded()

    text = await refusal(fake(state, writes=False), "delete_saved_view", view="e2e-view")

    assert "Nothing was changed" in text
    assert "delete saved view 'e2e-view' (id 1) for good" in text
    assert "confirm=true" in text
    assert [v.name for v in state.saved_views] == ["e2e-view"]


async def test_deleting_with_confirm_removes_the_view() -> None:
    state = seeded()

    output = await call(fake(state), "delete_saved_view", view=1, confirm=True)

    assert (output["id"], output["name"]) == (1, "e2e-view")
    assert state.saved_views == []


async def test_running_a_view_returns_what_search_documents_returns_for_the_same_filters() -> None:
    state = seeded()
    state.saved_views.append(
        FakeSavedView(id=2, name="all by title", owner=2, sort_field="title", sort_reverse=True)
    )
    async with Client(serve(fake(state), toolsets=["core", "views"])) as client:
        viewed = await client.call_tool("run_saved_view", {"view": "all by title", "page_size": 1})
        searched = await client.call_tool(
            "search_documents", {"ordering": "-title", "page_size": 1}
        )
        second = await client.call_tool(
            "run_saved_view", {"view": "all by title", "page": 2, "page_size": 1}
        )

    assert viewed.structured_content == searched.structured_content
    assert viewed.structured_content is not None
    assert (viewed.structured_content["count"], viewed.structured_content["next_page"]) == (2, 2)
    assert second.structured_content is not None
    assert titles(second.structured_content) == ["e2e-private-note"]


async def test_a_view_that_cannot_be_run_is_an_error_naming_the_rule_type() -> None:
    state = seeded()
    text = await refusal(
        fake(state),
        "create_saved_view",
        name="broken",
        rules=[{"rule": "created after", "value": "soon"}],
    )

    assert "Filter rule type 9 (created after)" in text
    assert state.saved_views[-1].name == "e2e-view"


async def test_a_user_without_the_view_permission_gets_a_permission_error() -> None:
    text = await refusal(fake(user="e2e-reader"), "list_saved_views")

    assert "may not perform this action" in text


async def test_the_confirm_parameter_is_the_shared_one_and_off_by_default() -> None:
    async with Client(serve(fake(), toolsets=["views"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    properties = cast("dict[str, Json]", tools["delete_saved_view"].input_schema["properties"])
    assert properties["confirm"]["default"] is False
    assert "confirm" not in cast("list[str]", tools["delete_saved_view"].input_schema["required"])
