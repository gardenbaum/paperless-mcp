"""Registry tests: rules every tool of the assembled server follows (design 9.4).

Each check returns the names of the tools that break its rule. The server must give none; the
self-tests run every check over a synthetic provider of offending tools, so a check that would
pass for want of offenders (no destructive tool, no path parameter yet) is proven to fire.
"""

from pathlib import Path  # noqa: TC003 -- FastMCP reads tool signatures at runtime
from typing import TYPE_CHECKING, Annotated, cast

import pytest
from fastmcp import FastMCP
from fastmcp.server.providers import LocalProvider
from fastmcp.tools import ToolResult
from harness import admin, serve
from mcp.types import ToolAnnotations
from pydantic import Field

from paperless_mcp.tools._shared import (
    ANNOTATIONS,
    MEDIA_TOOLS,
    TOOL_TOOLSETS,
    TOOLSETS,
    boundary,
)
from paperless_mcp.tools.selection import hidden_tools

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from fastmcp.tools import Tool
    from fastmcp.utilities.components import FastMCPComponent

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-4")]

# Default profile budget (design 6): the core and ai toolsets together.
TOOL_BUDGET = 20
# (tool, parameter) pairs that may take a server-side path: stdio-only uploads (decision-7).
STDIO_PATH_PARAMETERS: frozenset[tuple[str, str]] = frozenset(
    {("upload_document", "path"), ("upload_document_version", "path")}
)
# JSON schema formats pydantic gives Path, FilePath and DirectoryPath. Filesystem paths must be
# typed as such, so this check sees them.
PATH_FORMATS = frozenset({"path", "file-path", "directory-path"})


async def _no_op() -> None: ...


GUARDED = boundary(_no_op).__code__


def untitled(tools: list[Tool]) -> list[str]:
    return [t.name for t in tools if t.annotations is None or not t.annotations.title]


def undescribed(tools: list[Tool]) -> list[str]:
    return [t.name for t in tools if not (t.description or "").strip()]


def off_table(tools: list[Tool], table: Mapping[str, ToolAnnotations]) -> list[str]:
    """Tools whose annotations are not their table entry, and table entries without a tool."""
    names = {t.name for t in tools}
    mismatched = [t.name for t in tools if t.name not in table or t.annotations != table[t.name]]
    return mismatched + sorted(set(table) - names)


def not_one_toolset(components: Sequence[FastMCPComponent]) -> list[str]:
    """Tools, resources, templates and prompts without exactly one toolset tag."""
    return [c.name for c in components if len(c.tags) != 1 or not c.tags <= TOOLSETS]


def off_toolset_table(tools: list[Tool], table: Mapping[str, str]) -> list[str]:
    """Tools whose tag is not their toolset in the central table, and stale table entries."""
    names = {t.name for t in tools}
    wrong = [t.name for t in tools if table.get(t.name) not in t.tags]
    return wrong + sorted(set(table) - names)


def unguarded(tools: list[Tool]) -> list[str]:
    """Tools whose function is not wrapped by the error boundary."""
    return [
        t.name for t in tools if getattr(getattr(t, "fn", None), "__code__", None) is not GUARDED
    ]


def destructive(tool: Tool) -> bool:
    """MCP's reading: a tool that is not read-only is destructive unless it says otherwise.

    A tool that says it is destructive is one, whatever else it claims.
    """
    hints = tool.annotations or ToolAnnotations()
    return hints.destructive_hint is True or (
        not hints.read_only_hint and hints.destructive_hint is not False
    )


def unconfirmed(tools: list[Tool]) -> list[str]:
    """Destructive tools without an optional `confirm: bool = False` (described or not)."""

    def confirmed(tool: Tool) -> bool:
        properties = cast("dict[str, dict[str, object]]", tool.parameters.get("properties", {}))
        confirm = properties.get("confirm", {})
        return (
            confirm.get("type") == "boolean"
            and confirm.get("default") is False
            and "confirm" not in cast("list[str]", tool.parameters.get("required", []))
        )

    return [t.name for t in tools if destructive(t) and not confirmed(t)]


def formats(schema: object) -> set[str]:
    """Every `format` anywhere in a JSON schema."""
    if isinstance(schema, dict):
        items = cast("dict[str, object]", schema)
        found = {str(items["format"])} if "format" in items else set[str]()
        return found.union(*(formats(value) for value in items.values()))
    if isinstance(schema, list):
        return set[str]().union(*(formats(value) for value in cast("list[object]", schema)))
    return set()


def path_parameters(
    tools: list[Tool], allowed: frozenset[tuple[str, str]] = STDIO_PATH_PARAMETERS
) -> list[str]:
    """Path-typed parameters (`name.parameter`) that are not in `allowed`."""
    return [
        f"{t.name}.{name}"
        for t in tools
        for name, schema in cast("dict[str, object]", t.parameters.get("properties", {})).items()
        if formats(schema) & PATH_FORMATS and (t.name, name) not in allowed
    ]


def stdio_only_parameters(tools: list[Tool]) -> list[str]:
    """Parameters of the stdio-only allowlist (`name.parameter`) that these tools declare."""
    return [
        f"{t.name}.{name}"
        for t in tools
        for name in cast("dict[str, object]", t.parameters.get("properties", {}))
        if (t.name, name) in STDIO_PATH_PARAMETERS
    ]


def without_schema(tools: list[Tool], media: frozenset[str]) -> list[str]:
    """Tools without an output schema that are not media tools (decision-11)."""
    return [t.name for t in tools if t.output_schema is None and t.name not in media]


def media_with_schema(tools: list[Tool], media: frozenset[str]) -> list[str]:
    """Media tools that declare an output schema, and allowlisted names that have no tool."""
    named = {t.name for t in tools}
    return [t.name for t in tools if t.name in media and t.output_schema is not None] + sorted(
        media - named
    )


def over_budget(tools: list[Tool]) -> list[str]:
    return [t.name for t in tools] if len(tools) > TOOL_BUDGET else []


def full_server() -> FastMCP:
    """Every toolset on, and a Paperless that offers every capability."""
    return serve(admin(ai_enabled=True), toolsets=["all"])


async def server_tools() -> list[Tool]:
    """The tools of the server with every toolset on: what the rules below apply to."""
    return list(await full_server().list_tools())


async def server_components() -> list[FastMCPComponent]:
    """Every tool, resource, resource template and prompt of the full server."""
    server = full_server()
    return [
        *await server.list_tools(),
        *await server.list_resources(),
        *await server.list_resource_templates(),
        *await server.list_prompts(),
    ]


def writing(tools: list[Tool]) -> list[str]:
    """Tools without the read-only hint."""
    return [t.name for t in tools if not (t.annotations and t.annotations.read_only_hint)]


async def test_every_tool_has_a_title_and_a_description() -> None:
    tools = await server_tools()

    assert (untitled(tools), undescribed(tools)) == ([], [])


async def test_every_tool_has_its_annotations_from_the_central_table() -> None:
    assert off_table(await server_tools(), ANNOTATIONS) == []


async def test_every_component_has_exactly_one_toolset_tag() -> None:
    assert not_one_toolset(await server_components()) == []


async def test_every_tool_is_in_the_toolset_table_under_its_tag() -> None:
    assert off_toolset_table(await server_tools(), TOOL_TOOLSETS) == []


async def test_every_tool_goes_through_the_error_boundary() -> None:
    assert unguarded(await server_tools()) == []


@pytest.mark.decision("decision-6")
async def test_destructive_tools_need_confirm_defaulting_to_false() -> None:
    assert unconfirmed(await server_tools()) == []


@pytest.mark.decision("decision-6")
async def test_the_confirm_check_has_destructive_tools_to_check() -> None:
    # Guards the check above against passing for want of a destructive tool.
    tools = await server_tools()

    found = {t.name for t in tools if destructive(t)}

    assert found >= {
        "delete_note",
        "delete_document",
        "delete_document_version",
        "merge_documents_as_versions",
        "empty_trash",
    }
    assert found.isdisjoint({"update_document", "add_note", "get_document"})


@pytest.mark.decision("decision-7")
async def test_no_tool_takes_a_filesystem_path_outside_the_stdio_allowlist() -> None:
    assert path_parameters(await server_tools()) == []


@pytest.mark.decision("decision-7")
async def test_no_path_parameter_is_in_a_schema_unless_stdio_runs_with_an_allowlist(
    tmp_path: Path,
) -> None:
    fake = admin(ai_enabled=True)
    everywhere = {
        "http": serve(fake, toolsets=["all"]),
        # The allowlist alone is not enough: HTTP never offers a path, whatever is configured.
        "http with an allowlist": serve(fake, toolsets=["all"], upload_dirs=[str(tmp_path)]),
        "stdio": serve(fake, toolsets=["all"], stdio=True),
    }

    for name, server in everywhere.items():
        tools = list(await server.list_tools())
        assert stdio_only_parameters(tools) == [], name
        assert path_parameters(tools, frozenset()) == [], name
        assert {"upload_document", "upload_document_version"} <= {t.name for t in tools}, name


@pytest.mark.decision("decision-7")
async def test_the_path_parameter_exists_over_stdio_with_an_allowlist_and_is_allowlisted(
    tmp_path: Path,
) -> None:
    server = serve(
        admin(ai_enabled=True), toolsets=["all"], stdio=True, upload_dirs=[str(tmp_path)]
    )
    tools = list(await server.list_tools())

    assert stdio_only_parameters(tools) == ["upload_document.path", "upload_document_version.path"]
    assert path_parameters(tools) == []
    assert path_parameters(tools, frozenset()) == [
        "upload_document.path",
        "upload_document_version.path",
    ]


@pytest.mark.decision("decision-11")
async def test_only_media_tools_go_without_an_output_schema() -> None:
    tools = await server_tools()

    assert without_schema(tools, MEDIA_TOOLS) == []
    assert media_with_schema(tools, MEDIA_TOOLS) == []


async def test_the_default_profile_stays_within_the_tool_budget() -> None:
    assert over_budget(list(await serve(admin(ai_enabled=True)).list_tools())) == []


async def test_read_only_mode_leaves_only_tools_with_the_read_only_hint() -> None:
    tools = list(await serve(admin(ai_enabled=True), toolsets=["all"], read_only=True).list_tools())

    assert tools != []
    assert writing(tools) == []


# --- Self-tests: every check fires on a tool that breaks its rule ------------------------------

offenders = LocalProvider()


@offenders.tool
async def bare(source: Path) -> int:
    return len(str(source))


@offenders.tool(
    tags={"core", "bulk"},
    annotations=ToolAnnotations(title="Delete everything", destructive_hint=True),
)
@boundary
async def delete_everything(*, confirm: bool = True) -> bool:
    """Delete every document."""
    return confirm


@offenders.tool(annotations=ToolAnnotations(title="Drop"))
@boundary
async def drop_without_confirm() -> None:
    """Not read-only and not marked non-destructive: destructive by MCP's default."""


@offenders.tool(annotations=ToolAnnotations(title="Empty trash", destructive_hint=True))
@boundary
async def empty_trash(
    *, confirm: Annotated[bool, Field(description="Set to true to really empty it.")] = False
) -> bool:
    """A correct destructive tool: a described confirm that defaults to false."""
    return confirm


@offenders.tool(
    annotations=ToolAnnotations(title="Contradiction", read_only_hint=True, destructive_hint=True)
)
@boundary
async def read_only_but_destructive() -> None:
    """Claims to be both read-only and destructive: the destructive claim counts."""


@offenders.tool(
    annotations=ToolAnnotations(title="Required confirm", destructive_hint=True),
)
@boundary
async def drop_with_required_confirm(*, confirm: bool) -> bool:
    """Has a confirm, but it must be passed: the first call would already act."""
    return confirm


@offenders.resource("data://untagged")
async def untagged_resource() -> str:
    return "data"


@offenders.prompt(tags={"core", "bulk"})
async def double_tagged_prompt() -> str:
    return "prompt"


async def offending_tools() -> list[Tool]:
    return list(await FastMCP(providers=[offenders]).list_tools())


async def test_self_test_untitled_and_undescribed_tools_are_found() -> None:
    tools = await offending_tools()

    assert "bare" in untitled(tools)
    assert "bare" in undescribed(tools)


async def test_self_test_annotations_off_the_table_and_stale_entries_are_found() -> None:
    table = {"delete_everything": ToolAnnotations(title="Other"), "ghost": ToolAnnotations()}

    assert set(off_table(await offending_tools(), table)) >= {"bare", "delete_everything", "ghost"}


async def test_self_test_missing_or_extra_toolset_tags_are_found() -> None:
    server = FastMCP(providers=[offenders])
    components = [
        *await server.list_tools(),
        *await server.list_resources(),
        *await server.list_prompts(),
    ]

    assert set(not_one_toolset(components)) >= {
        "bare",
        "delete_everything",
        "untagged_resource",
        "double_tagged_prompt",
    }


async def test_self_test_tools_off_the_toolset_table_are_found() -> None:
    table = {"delete_everything": "bulk", "ghost": "core"}

    found = off_toolset_table(await offending_tools(), table)

    assert "ghost" in found
    assert "bare" in found
    assert "delete_everything" not in found


async def test_self_test_tools_without_the_boundary_are_found() -> None:
    found = unguarded(await offending_tools())

    assert "bare" in found
    assert "delete_everything" not in found


@pytest.mark.decision("decision-6")
async def test_self_test_destructive_tools_without_a_false_confirm_are_found() -> None:
    found = set(unconfirmed(await offending_tools()))

    assert found >= {
        "delete_everything",
        "drop_without_confirm",
        "read_only_but_destructive",
        "drop_with_required_confirm",
    }
    assert "empty_trash" not in found


@pytest.mark.decision("decision-7")
async def test_self_test_path_parameters_are_found() -> None:
    assert path_parameters(await offending_tools()) == ["bare.source"]


@offenders.tool
async def upload_document(path: Path | None = None) -> int:
    """A stdio-only tool as the allowlist names it, here in the offenders' provider."""
    return len(str(path))


@pytest.mark.decision("decision-7")
async def test_self_test_a_path_parameter_is_found_unless_it_is_allowlisted_by_tool_and_name() -> (
    None
):
    tools = await offending_tools()
    allowlisted = frozenset({("upload_document", "path")})

    assert set(path_parameters(tools, frozenset())) == {"bare.source", "upload_document.path"}
    assert path_parameters(tools, allowlisted) == ["bare.source"]
    assert path_parameters(tools, frozenset({("other_tool", "path")})) == [
        "bare.source",
        "upload_document.path",
    ]


@pytest.mark.decision("decision-7")
async def test_self_test_a_stdio_only_parameter_in_a_schema_is_found() -> None:
    assert stdio_only_parameters(await offending_tools()) == ["upload_document.path"]


async def test_self_test_tools_without_the_read_only_hint_are_found() -> None:
    found = writing(await offending_tools())

    assert set(found) >= {"bare", "delete_everything", "drop_without_confirm", "empty_trash"}


# Synthetic check of the hiding rule alone; that create_server applies it to the real server is
# proven in test_visibility.py (read-only mode over a catalogue with writing tools).
async def test_self_test_read_only_mode_hides_what_the_table_marks_as_writing() -> None:
    table = {"bare": ToolAnnotations(), "delete_everything": ToolAnnotations(read_only_hint=True)}
    server = FastMCP(providers=[offenders])
    _ = server.disable(names=set(hidden_tools(table, read_only=True, disabled=())))

    remaining = {t.name for t in await server.list_tools()}

    assert "delete_everything" in remaining
    assert "bare" not in remaining


@offenders.tool(annotations=ToolAnnotations(title="Screenshot"), output_schema=None)
@boundary
async def screenshot() -> ToolResult:
    """A media tool: content blocks only."""
    return ToolResult(content="png")


@offenders.tool(annotations=ToolAnnotations(title="Typed media"))
@boundary
async def typed_media() -> int:
    """A media tool that declares an output schema anyway."""
    return 1


@pytest.mark.decision("decision-11")
async def test_self_test_a_schemaless_tool_outside_the_media_allowlist_is_found() -> None:
    tools = await offending_tools()

    assert "screenshot" in without_schema(tools, frozenset())
    assert "screenshot" not in without_schema(tools, frozenset({"screenshot"}))
    assert "bare" not in without_schema(tools, frozenset())  # typed: has a schema


@pytest.mark.decision("decision-11")
async def test_self_test_a_media_tool_with_a_schema_or_without_a_tool_is_found() -> None:
    tools = await offending_tools()

    found = media_with_schema(tools, frozenset({"typed_media", "screenshot", "ghost"}))

    assert found == ["typed_media", "ghost"]


async def test_self_test_a_profile_over_the_budget_is_found() -> None:
    crowded = LocalProvider()
    for number in range(TOOL_BUDGET + 1):
        _ = crowded.tool(_no_op, name=f"tool_{number}")

    assert len(over_budget(list(await FastMCP(providers=[crowded]).list_tools()))) == 21
