"""The core toolset: everyday reading (and later writing) of documents and taxonomy."""

from typing import Annotated

from fastmcp import Context
from fastmcp.exceptions import ResourceError
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.domain.instance import InstanceOverview
from paperless_mcp.domain.taxonomy import TaxonomyKind, TaxonomyListing
from paperless_mcp.tools._shared import ANNOTATIONS, boundary, services

TOOLSET = "core"
# Most rows list_taxonomy returns (decision-4: lists hold at most 100 items).
LIST_LIMIT = 100

provider = LocalProvider()


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_instance_overview"])
@boundary
async def get_instance_overview(ctx: Context) -> InstanceOverview:
    """Describe the Paperless instance as the current user sees it.

    Reports the Paperless and API version, the document, inbox and taxonomy counts, the system
    health (storage, database, task queue, search index, classifier), the current user and
    whether Paperless AI is enabled. Use it first to orient yourself, or to diagnose failures.

    Counts include only what the user may see, unless they may view global statistics. A null
    field means the user may not see it, not that it is off: ai_enabled null means unknown.
    """
    return await services(ctx).instance.overview()


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_taxonomy"])
@boundary
async def list_taxonomy(
    ctx: Context,
    kind: Annotated[TaxonomyKind, Field(description="Which kind of object to list.")],
    name_contains: Annotated[
        str | None,
        Field(description="Only names that contain this text, ignoring case and accents."),
    ] = None,
) -> TaxonomyListing:
    """List the tags, correspondents, document types, storage paths, custom fields, saved views,
    users or groups the current user may see, as compact rows with their ids.

    Use it to find the exact name or id to pass to other tools. Returns at most 100 rows in
    Paperless order (by name); truncated true means more match, so narrow with name_contains.
    Users, groups and saved views need the matching view permission in Paperless.
    """  # noqa: D205 -- the summary is one sentence; FastMCP sends the docstring as is
    rows = await services(ctx).taxonomy.items(kind, name_contains)
    return TaxonomyListing(
        kind=kind,
        count=len(rows),
        truncated=len(rows) > LIST_LIMIT,
        items=tuple(rows[:LIST_LIMIT]),
    )


@provider.resource(
    "paperless://taxonomy/{kind}",
    name="taxonomy",
    title="Taxonomy table",
    mime_type="application/json",
    tags={TOOLSET},
)
async def taxonomy_table(ctx: Context, kind: TaxonomyKind) -> list[dict[str, object]]:
    """Every tag, correspondent, document type, storage path, custom field, saved view, user or
    group the current user may see, as JSON rows with id, name and document count.
    """  # noqa: D205 -- see list_taxonomy
    try:
        rows = await services(ctx).taxonomy.items(kind)
    except PaperlessMcpError as error:
        raise ResourceError(error.describe()) from None
    return [row.model_dump(mode="json") for row in rows]
