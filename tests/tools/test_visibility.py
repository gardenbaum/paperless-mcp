"""Toolset selection, read-only mode and capabilities on the assembled server.

The shipped catalogue has only the core toolset so far; the synthetic catalogue adds the toolsets
later tasks bring (ai, bulk) so that every rule is proven for them now.
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


@extra.tool(tags={"bulk"}, annotations=WRITE)
@boundary
async def bulk_change() -> str:
    """Change many things."""
    return "changed"


@extra.tool(tags={"bulk"}, annotations=READ)
@boundary
async def bulk_preview() -> str:
    """Preview a bulk change."""
    return "preview"


SYNTHETIC = replace(
    CATALOGUE,
    providers=(*CATALOGUE.providers, extra),
    annotations={
        **ANNOTATIONS,
        "ask_something": READ,
        "bulk_change": WRITE,
        "bulk_preview": READ,
    },
    tool_toolsets={
        **TOOL_TOOLSETS,
        "ask_something": "ai",
        "bulk_change": "bulk",
        "bulk_preview": "bulk",
    },
    toolsets=CATALOGUE.toolsets | {"ai", "bulk"},
    requires={"ai": "ai"},
)
CORE = set(ANNOTATIONS)


async def names(server: FastMCP) -> set[str]:
    async with Client(server) as client:
        return {tool.name for tool in await client.list_tools()}


def with_ai(*, enabled: bool) -> FakePaperless:
    return admin(ai_enabled=enabled)


async def test_the_default_profile_offers_only_the_core_toolset() -> None:
    assert await names(serve(admin(), catalogue=SYNTHETIC)) == CORE


async def test_named_toolsets_are_offered_and_others_are_not() -> None:
    server = serve(admin(), catalogue=SYNTHETIC, toolsets=["bulk"])

    assert await names(server) == {"bulk_change", "bulk_preview"}


async def test_several_toolsets_combine() -> None:
    server = serve(admin(), catalogue=SYNTHETIC, toolsets=["core", "bulk"])

    assert await names(server) == CORE | {"bulk_change", "bulk_preview"}


async def test_all_offers_every_toolset_the_server_may_offer() -> None:
    server = serve(with_ai(enabled=True), catalogue=SYNTHETIC, toolsets=["all"])

    assert await names(server) == CORE | {"ask_something", "bulk_change", "bulk_preview"}


async def test_a_toolset_not_selected_takes_its_resources_with_it() -> None:
    async with Client(serve(admin(), catalogue=SYNTHETIC, toolsets=["bulk"])) as client:
        templates = await client.list_resource_templates()

    assert templates == []


async def test_read_only_mode_offers_only_tools_with_the_read_only_hint() -> None:
    server = serve(with_ai(enabled=True), catalogue=SYNTHETIC, toolsets=["all"], read_only=True)

    # update_document is core's first tool that changes data, so read-only mode hides it.
    assert await names(server) == (CORE - {"update_document"}) | {"ask_something", "bulk_preview"}


async def test_disabled_tools_are_not_offered() -> None:
    server = serve(admin(), catalogue=SYNTHETIC, disabled_tools=["get_task", "list_taxonomy"])

    assert await names(server) == CORE - {"get_task", "list_taxonomy"}


async def test_an_unknown_toolset_stops_the_server_from_being_built() -> None:
    with pytest.raises(InvalidConfiguration, match="pdff"):
        _ = serve(admin(), toolsets=["core", "pdff"])


async def test_an_unknown_disabled_tool_stops_the_server_from_being_built() -> None:
    with pytest.raises(InvalidConfiguration, match="get_taks"):
        _ = serve(admin(), disabled_tools=["get_taks"])


async def test_disabling_every_tool_of_the_toolsets_stops_the_server_from_being_built() -> None:
    with pytest.raises(InvalidConfiguration) as caught:
        _ = serve(
            admin(),
            catalogue=SYNTHETIC,
            toolsets=["bulk"],
            disabled_tools=["bulk_change", "bulk_preview"],
        )

    assert "No tools are left" in caught.value.message
    assert "PAPERLESS_MCP_TOOLSETS" in caught.value.message


async def test_read_only_mode_over_writing_tools_only_stops_the_server_from_being_built() -> None:
    writing_only = replace(SYNTHETIC, annotations={**SYNTHETIC.annotations, "bulk_preview": WRITE})

    with pytest.raises(InvalidConfiguration, match="No tools are left"):
        _ = serve(admin(), catalogue=writing_only, toolsets=["bulk"], read_only=True)


async def test_the_zero_tool_check_does_not_wait_for_paperless() -> None:
    with pytest.raises(InvalidConfiguration, match="No tools are left"):
        _ = create_server(
            paperless_settings(refused_url()),
            server_settings(disabled_tools=list(CORE)),
        )


async def test_a_toolset_whose_capability_is_missing_is_hidden() -> None:
    server = serve(with_ai(enabled=False), catalogue=SYNTHETIC, toolsets=["core", "ai"])

    assert await names(server) == CORE


async def test_a_toolset_whose_capability_is_present_is_offered() -> None:
    server = serve(with_ai(enabled=True), catalogue=SYNTHETIC, toolsets=["core", "ai"])

    assert await names(server) == CORE | {"ask_something"}


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
