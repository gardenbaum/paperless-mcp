"""The core toolset: everyday reading (and later writing) of documents and taxonomy."""

from datetime import date
from typing import Annotated

from fastmcp import Context
from fastmcp.exceptions import ResourceError
from fastmcp.server.providers import LocalProvider
from pydantic import Field, JsonValue

from paperless_mcp.domain.documents import (
    DocumentQuery,
    DocumentSearchResult,
    NameFilters,
    NameOrId,
    Ordering,
)
from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.domain.instance import InstanceOverview
from paperless_mcp.domain.taxonomy import TaxonomyKind, TaxonomyListing
from paperless_mcp.tools._shared import ANNOTATIONS, boundary, services

TOOLSET = "core"
# Most rows list_taxonomy returns and largest search page (decision-4: lists hold at most 100).
LIST_LIMIT = 100
PAGE_SIZE = 25

type Named = Annotated[NameOrId | None, Field(description="Name or id; see list_taxonomy.")]
type Day = Annotated[date | None, Field(description="Date (YYYY-MM-DD), inclusive.")]

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


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["search_documents"])
@boundary
async def search_documents(  # noqa: PLR0913 -- one parameter per filter, described for the LLM
    ctx: Context,
    *,
    query: Annotated[
        str | None,
        Field(
            description=(
                "Full-text query in Paperless syntax over title, content, notes and fields, "
                "e.g. 'invoice AND acme', 'created:[2024 to 2025]', 'notes.note:paid'. "
                "Results are ranked by relevance and carry highlights."
            )
        ),
    ] = None,
    more_like_id: Annotated[
        int | None, Field(description="Find documents similar to this document id.")
    ] = None,
    title_contains: Annotated[
        str | None, Field(description="Only titles containing this text, ignoring case.")
    ] = None,
    correspondent: Named = None,
    document_type: Named = None,
    storage_path: Named = None,
    tags_all: Annotated[
        list[NameOrId] | None, Field(description="Tag names or ids; documents have all of them.")
    ] = None,
    tags_any: Annotated[
        list[NameOrId] | None, Field(description="Tag names or ids; documents have at least one.")
    ] = None,
    tags_none: Annotated[
        list[NameOrId] | None, Field(description="Tag names or ids; documents have none of them.")
    ] = None,
    created_from: Day = None,
    created_to: Day = None,
    added_from: Day = None,
    added_to: Day = None,
    owner: Annotated[
        NameOrId | None, Field(description="Username or id of the owning user.")
    ] = None,
    inbox: Annotated[
        bool | None, Field(description="true: only inbox documents; false: none of them.")
    ] = None,
    custom_field_query: Annotated[
        list[JsonValue] | None,
        Field(
            description=(
                "Paperless custom field query: [field, op, value], fields by name, combined "
                "with ['AND'|'OR', [...]] or ['NOT', [...]]. Ops: exact, in, isnull, exists, "
                "icontains, gt, gte, lt, lte, range, contains. Dates take relative values: "
                "['due', 'range', ['today', 'in 30 days']]."
            )
        ),
    ] = None,
    ordering: Annotated[
        Ordering | None,
        Field(description="Sort field, '-' for descending; default newest first or relevance."),
    ] = None,
    page: Annotated[int, Field(ge=1, description="Page number, from 1.")] = 1,
    page_size: Annotated[
        int, Field(ge=1, le=LIST_LIMIT, description="Documents per page, at most 100.")
    ] = PAGE_SIZE,
) -> DocumentSearchResult:
    """Find documents by full text, similarity or structured filters; all filters combine.

    Names (correspondent, document type, storage path, tags, owner) match exactly or ignoring
    case and accents; an unknown or ambiguous name is an error that lists candidates. Rows hold
    ids, names, dates, custom field values and web_url, never the document text. count is the
    total; pass next_page as page to see more.
    """
    names = NameFilters(
        correspondent=correspondent,
        document_type=document_type,
        storage_path=storage_path,
        tags_all=tuple(tags_all or ()),
        tags_any=tuple(tags_any or ()),
        tags_none=tuple(tags_none or ()),
        owner=owner,
    )
    filters = DocumentQuery(
        query=query,
        more_like=more_like_id,
        title_contains=title_contains,
        created_from=created_from,
        created_to=created_to,
        added_from=added_from,
        added_to=added_to,
        inbox=inbox,
        custom_field_query=custom_field_query,
        ordering=ordering,
    )
    return await services(ctx).documents.search(names, filters, page=page, page_size=page_size)


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
