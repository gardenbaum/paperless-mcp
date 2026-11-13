"""Toolset selection, read-only mode and capabilities on the assembled server.

The synthetic catalogue adds an ai tool of its own and a scratch toolset the server does not ship,
so that every rule is proven for toolsets of any size. The shipped ai, bulk and pdf toolsets are
covered in test_ai.py, test_bulk.py and test_pdf.py.
"""

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from fakes.paperless import FakePaperless, FakeUser, seeded
from fastmcp import Client
from fastmcp.server.providers import LocalProvider
from harness import admin, paperless_settings, refused_url, serve, server_settings
from mcp.types import ToolAnnotations

from paperless_mcp.domain.errors import InvalidConfiguration
from paperless_mcp.server import CATALOGUE, create_server
from paperless_mcp.tools._shared import ANNOTATIONS, TOOL_TOOLSETS, boundary

if TYPE_CHECKING:
    from fastmcp import FastMCP

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-4")]

READ = ToolAnnotations(title="R", read_only_hint=True)
WRITE = ToolAnnotations(title="W", destructive_hint=False)

extra = LocalProvider()


@extra.tool(tags={"ai"}, annotations=READ)
@boundary
async def ask_something() -> str:
    """Ask the AI."""
    return "answer"


@extra.tool(tags={"scratch"}, annotations=WRITE)
@boundary
async def scratch_change() -> str:
    """Change a PDF."""
    return "changed"


@extra.tool(tags={"scratch"}, annotations=READ)
@boundary
async def scratch_preview() -> str:
    """Preview a PDF change."""
    return "preview"


SYNTHETIC = replace(
    CATALOGUE,
    providers=(*CATALOGUE.providers, extra),
    annotations={
        **ANNOTATIONS,
        "ask_something": READ,
        "scratch_change": WRITE,
        "scratch_preview": READ,
    },
    tool_toolsets={
        **TOOL_TOOLSETS,
        "ask_something": "ai",
        "scratch_change": "scratch",
        "scratch_preview": "scratch",
    },
    toolsets=CATALOGUE.toolsets | {"ai", "scratch"},
    requires={"ai": "ai"},
)
CORE = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "core"}
SHIPPED_AI = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "ai"}
SHIPPED_VERSIONS = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "versions"}
SHIPPED_BULK = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "bulk"}
VIEWS = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "views"}
SHIPPED_PDF = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "pdf"}
READ_VIEWS = {"list_saved_views", "get_saved_view", "run_saved_view"}
SHIPPED_TRASH = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "trash"}


async def names(server: FastMCP) -> set[str]:
    async with Client(server) as client:
        return {tool.name for tool in await client.list_tools()}


def with_ai(*, enabled: bool) -> FakePaperless:
    return admin(ai_enabled=enabled)


async def test_the_default_profile_offers_only_the_core_toolset() -> None:
    assert await names(serve(admin(), catalogue=SYNTHETIC)) == CORE


async def test_named_toolsets_are_offered_and_others_are_not() -> None:
    server = serve(admin(), catalogue=SYNTHETIC, toolsets=["scratch"])

    assert await names(server) == {"scratch_change", "scratch_preview"}


async def test_several_toolsets_combine() -> None:
    server = serve(admin(), catalogue=SYNTHETIC, toolsets=["core", "scratch"])

    assert await names(server) == CORE | {"scratch_change", "scratch_preview"}


async def test_all_offers_every_toolset_the_server_may_offer() -> None:
    server = serve(with_ai(enabled=True), catalogue=SYNTHETIC, toolsets=["all"])

    shipped = CORE | SHIPPED_AI | SHIPPED_BULK | SHIPPED_VERSIONS | VIEWS | SHIPPED_TRASH
    assert await names(server) == shipped | SHIPPED_PDF | {
        "ask_something",
        "scratch_change",
        "scratch_preview",
    }


async def test_a_toolset_not_selected_takes_its_resources_with_it() -> None:
    async with Client(serve(admin(), catalogue=SYNTHETIC, toolsets=["scratch"])) as client:
        templates = await client.list_resource_templates()

    assert templates == []


async def test_read_only_mode_offers_only_tools_with_the_read_only_hint() -> None:
    server = serve(with_ai(enabled=True), catalogue=SYNTHETIC, toolsets=["all"], read_only=True)

    # The tools of core that change data are hidden in read-only mode.
    writing = {
        "update_document",
        "add_note",
        "delete_note",
        "delete_document",
        "upload_document",
    } | {
        "create_taxonomy_item",
        "update_taxonomy_item",
        "delete_taxonomy_item",
    }
    expected = (
        (CORE - writing)
        | SHIPPED_AI
        | READ_VIEWS
        | {"list_document_versions", "list_trash"}
        | {"ask_something", "scratch_preview"}
    )
    assert await names(server) == expected


async def test_disabled_tools_are_not_offered() -> None:
    server = serve(admin(), catalogue=SYNTHETIC, disabled_tools=["get_task", "list_taxonomy"])

    assert await names(server) == CORE - {"get_task", "list_taxonomy"}


async def test_an_unknown_toolset_stops_the_server_from_being_built() -> None:
    with pytest.raises(InvalidConfiguration, match="scratchh"):
        _ = serve(admin(), toolsets=["core", "scratchh"])


async def test_an_unknown_disabled_tool_stops_the_server_from_being_built() -> None:
    with pytest.raises(InvalidConfiguration, match="get_taks"):
        _ = serve(admin(), disabled_tools=["get_taks"])


async def test_disabling_every_tool_of_the_toolsets_stops_the_server_from_being_built() -> None:
    with pytest.raises(InvalidConfiguration) as caught:
        _ = serve(
            admin(),
            catalogue=SYNTHETIC,
            toolsets=["scratch"],
            disabled_tools=["scratch_change", "scratch_preview"],
        )

    assert "No tools are left" in caught.value.message
    assert "PAPERLESS_MCP_TOOLSETS" in caught.value.message


async def test_read_only_mode_over_writing_tools_only_stops_the_server_from_being_built() -> None:
    writing_only = replace(
        SYNTHETIC, annotations={**SYNTHETIC.annotations, "scratch_preview": WRITE}
    )

    with pytest.raises(InvalidConfiguration, match="No tools are left"):
        _ = serve(admin(), catalogue=writing_only, toolsets=["scratch"], read_only=True)


async def test_the_zero_tool_check_does_not_wait_for_paperless() -> None:
    with pytest.raises(InvalidConfiguration, match="No tools are left"):
        _ = create_server(
            paperless_settings(refused_url()),
            server_settings(disabled_tools=list(ANNOTATIONS)),
        )


async def test_a_toolset_whose_capability_is_missing_is_hidden() -> None:
    server = serve(with_ai(enabled=False), catalogue=SYNTHETIC, toolsets=["core", "ai"])

    assert await names(server) == CORE


async def test_a_toolset_whose_capability_is_present_is_offered() -> None:
    server = serve(with_ai(enabled=True), catalogue=SYNTHETIC, toolsets=["core", "ai"])

    assert await names(server) == CORE | SHIPPED_AI | {"ask_something"}


async def test_an_unknown_capability_leaves_the_toolset_visible() -> None:
    state = seeded()
    state.ai_enabled = False
    state.users.append(FakeUser(id=9, username="no-settings", permissions=frozenset()))
    blind = FakePaperless(state, user="no-settings")  # ai_enabled reads as null

    server = serve(blind, catalogue=SYNTHETIC, toolsets=["core", "ai"])

    assert "ask_something" in await names(server)


async def test_an_unreachable_paperless_at_startup_leaves_the_toolset_visible() -> None:
    server = create_server(
        paperless_settings(refused_url()),
        server_settings(toolsets=["core", "ai"]),
        catalogue=SYNTHETIC,
    )

    assert "ask_something" in await names(server)


async def test_a_missing_capability_can_leave_nothing_and_stops_the_start() -> None:
    server = serve(with_ai(enabled=False), catalogue=SYNTHETIC, toolsets=["ai"])

    with pytest.raises(RuntimeError) as caught:
        async with Client(server):
            pass

    assert isinstance(caught.value.__cause__, InvalidConfiguration)


async def test_one_server_follows_the_capability_of_each_start() -> None:
    state = seeded()
    state.ai_enabled = False
    fake = FakePaperless(state, user="admin")
    server = serve(fake, catalogue=SYNTHETIC, toolsets=["core", "ai"])

    first = await names(server)
    state.ai_enabled = True
    second = await names(server)
    state.ai_enabled = False
    third = await names(server)

    assert ("ask_something" in first, "ask_something" in second, "ask_something" in third) == (
        False,
        True,
        False,
    )
