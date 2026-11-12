"""The MCP surface as clients see it: a reviewed snapshot plus schema-compatibility checks.

A change to the surface fails `test_the_surface_matches_the_snapshot`. Review the diff, then
regenerate with `uv run pytest tests/tools/test_surface.py --snapshot-update` and explain the
change in the task notes and the commit message (CLAUDE.md).
"""

from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client, FastMCP
from harness import admin, serve

if TYPE_CHECKING:
    from collections.abc import Iterator

    from syrupy.assertion import SnapshotAssertion

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-4")]

type Schema = dict[str, object]

# The profiles the surface is reviewed for: server settings per profile. `default` is the
# shipped default, `all` every toolset, `read-only` every toolset without tools that change data.
PROFILES: dict[str, tuple[list[str] | None, bool]] = {
    "default": (None, False),
    "all": (["all"], False),
    "read-only": (["all"], True),
}
profiles = pytest.mark.parametrize("profile", list(PROFILES))


def profile_server(profile: str) -> FastMCP:
    toolsets, read_only = PROFILES[profile]
    # AI on, so that capability gating cannot shrink the surface under review.
    return serve(admin(ai_enabled=True), toolsets=toolsets, read_only=read_only)


async def wire_tools(profile: str = "all") -> list[Schema]:
    """tools/list as sent on the wire: camelCase names (SDK v2 models are snake_case)."""
    async with Client(profile_server(profile)) as client:
        tools = sorted(await client.list_tools(), key=lambda tool: tool.name)
    return [tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in tools]


def keys(schema: object) -> Iterator[str]:
    """Every key anywhere in a JSON schema."""
    if isinstance(schema, dict):
        for key, value in cast("Schema", schema).items():
            yield key
            yield from keys(value)
    elif isinstance(schema, list):
        for value in cast("list[object]", schema):
            yield from keys(value)


def incompatible(name: str, input_schema: Schema, output_schema: Schema | None) -> list[str]:
    """What some MCP clients reject: a non-object root, `$schema`, undescribed parameters."""
    problems: list[str] = []
    for kind, schema in (("input", input_schema), ("output", output_schema)):
        if schema is None:
            continue
        if schema.get("type") != "object":
            problems.append(f"{name}: {kind} schema root is not an object")
        if "$schema" in set(keys(schema)):
            problems.append(f"{name}: {kind} schema has a $schema key")
    properties = cast("dict[str, Schema]", input_schema.get("properties", {}))
    problems.extend(
        f"{name}: parameter {parameter} has no description"
        for parameter, schema in properties.items()
        if not schema.get("description")
    )
    return problems


@profiles
async def test_the_surface_matches_the_snapshot(snapshot: SnapshotAssertion, profile: str) -> None:
    assert await wire_tools(profile) == snapshot


async def test_every_schema_is_compatible() -> None:
    problems = [
        problem
        for tool in await wire_tools()
        for problem in incompatible(
            str(tool["name"]),
            cast("Schema", tool["inputSchema"]),
            cast("Schema | None", tool.get("outputSchema")),
        )
    ]

    assert problems == []


def test_self_test_incompatible_schemas_are_found() -> None:
    problems = incompatible(
        "t",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"query": {"type": "string"}, "page": {"description": "Page."}},
        },
        {"type": "array", "items": {"$schema": "nested"}},
    )

    assert problems == [
        "t: input schema has a $schema key",
        "t: output schema root is not an object",
        "t: output schema has a $schema key",
        "t: parameter query has no description",
    ]


@profiles
async def test_the_resource_templates_match_the_snapshot(
    snapshot: SnapshotAssertion, profile: str
) -> None:
    async with Client(profile_server(profile)) as client:
        templates = sorted(await client.list_resource_templates(), key=lambda t: t.uri_template)
    wire = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in templates]

    assert wire == snapshot


@profiles
async def test_the_resources_and_prompts_match_the_snapshot(
    snapshot: SnapshotAssertion, profile: str
) -> None:
    server = profile_server(profile)
    async with Client(server) as client:
        resources = sorted(await client.list_resources(), key=lambda r: str(r.uri))
        prompts = sorted(await client.list_prompts(), key=lambda p: p.name)
    wire = {
        "resources": [
            r.model_dump(mode="json", by_alias=True, exclude_none=True) for r in resources
        ],
        "prompts": [p.model_dump(mode="json", by_alias=True, exclude_none=True) for p in prompts],
    }

    assert wire == snapshot
