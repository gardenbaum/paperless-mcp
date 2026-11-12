"""The core toolset: everyday reading and writing of documents and taxonomy."""

import base64
from datetime import date
from typing import Annotated

from fastmcp import Context
from fastmcp.exceptions import ResourceError
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.providers import LocalProvider
from fastmcp.tools import ToolResult
from mcp.types import (
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    TextContent,
)
from pydantic import Field, JsonValue

from paperless_mcp.domain.documents import (
    Clearable,
    ContentSlice,
    DocumentDeleted,
    DocumentDetail,
    DocumentEdit,
    DocumentQuery,
    DocumentSearchResult,
    DocumentUpdate,
    FileReport,
    FileVersion,
    Include,
    NameFilters,
    NameOrId,
    NoteAdded,
    NoteDeleted,
    Ordering,
)
from paperless_mcp.domain.instance import InstanceOverview
from paperless_mcp.domain.tasks import TaskReport
from paperless_mcp.domain.taxonomy import TaxonomyKind, TaxonomyListing
from paperless_mcp.services.documents import DEFAULT_SLICE, HISTORY_LIMIT
from paperless_mcp.services.notes import MAX_NOTE_CHARS
from paperless_mcp.services.tasks import MAX_WAIT_SECONDS
from paperless_mcp.tools._shared import ANNOTATIONS, Confirm, boundary, resource_boundary, services

TOOLSET = "core"
# Most rows list_taxonomy returns and largest search page (decision-4: lists hold at most 100).
LIST_LIMIT = 100
PAGE_SIZE = 25
UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
# Most characters of document text one call returns: the default slice. FastMCP sends results
# twice (content and structured) and clients cap tool output around 25k tokens (design 7).
MAX_SLICE = DEFAULT_SLICE

type Named = Annotated[NameOrId | None, Field(description="Name or id; see list_taxonomy.")]
type DocumentId = Annotated[int, Field(description="Document id, from search_documents.")]
type Offset = Annotated[int, Field(ge=0, description="Character offset to start at.")]
type SliceSize = Annotated[
    int,
    Field(ge=1, le=MAX_SLICE, description=f"Characters to return, at most {MAX_SLICE}."),
]
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


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_task"])
@boundary
async def get_task(
    ctx: Context,
    task_id: Annotated[
        str,
        Field(
            pattern=UUID_PATTERN,
            description="The task id (a UUID) that an upload or other background work returned.",
        ),
    ],
    wait_seconds: Annotated[
        int,
        Field(
            ge=0,
            description=(
                f"Wait up to this many seconds for the task to end; more than {MAX_WAIT_SECONDS} "
                f"is cut to {MAX_WAIT_SECONDS}. 0 reads it once."
            ),
        ),
    ] = 0,
) -> TaskReport:
    """Follow a Paperless background task, such as an upload being processed, to its result.

    Reports state (pending, started, success, failure, revoked), the error message of a failed
    task and the document ids once known. With wait_seconds it polls until the task ends or the
    wait (at most 30 s in total) is used up; wait says which: finished, timed_out (still
    running, call again) or not_requested. Needs the permission to view tasks in Paperless.
    """
    return await services(ctx).tasks.get(task_id, wait_seconds=wait_seconds)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_document"])
@boundary
async def get_document(
    ctx: Context,
    document_id: DocumentId,
    include: Annotated[
        list[Include] | None,
        Field(
            description=(
                "Extra sections: notes (their text), history "
                f"(the newest {HISTORY_LIMIT} changes, no OCR text), "
                "metadata (file names, sizes, checksums), versions (file versions), "
                "suggestions (the classifier's correspondents, tags, types, paths, dates)."
            )
        ),
    ] = None,
) -> DocumentDetail:
    """Read one document: names, dates, tags, readable custom fields, notes count and web_url.

    Never returns the document text; use get_document_content for it. Custom fields are
    rendered by type: select as its label, monetary as 'EUR 12.50', document links as the
    linked documents' id and title (title null if hidden). Sections in include that the user
    may not read in Paperless (notes and history need their own permissions, the audit log can
    be off) are listed in unavailable instead of failing.
    """
    return await services(ctx).documents.get(document_id, tuple(include or ()))


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_document_content"])
@boundary
async def get_document_content(
    ctx: Context,
    document_id: DocumentId,
    offset: Offset = 0,
    max_chars: SliceSize = DEFAULT_SLICE,
) -> ContentSlice:
    """Read the OCR text of a document in slices of max_chars characters (default 20000).

    total_chars is the length of the whole text; while next_offset is set, pass it as offset
    to read on. An offset past the end returns an empty slice with total_chars. Prefer
    search_documents with a full-text query to find the passage you need first.
    """
    return await services(ctx).documents.content(document_id, offset=offset, max_chars=max_chars)


def _text(report: FileReport) -> TextContent:
    return TextContent(type="text", text=report.model_dump_json())


def _meta(report: FileReport) -> dict[str, object] | None:
    """The metadata of file content: its name, if Paperless proposed one."""
    return None if report.filename is None else {"filename": report.filename}


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_document_thumbnail"])
@boundary
async def get_document_thumbnail(ctx: Context, document_id: DocumentId) -> ToolResult:
    """See a document as a small preview image (its first page), for clients that read images.

    Returns the image as MCP image content, after a JSON report (file name, size, web_url). Use
    it to recognise a document by its look; use get_document_content for its text and
    download_document for the file.
    """
    thumbnail = await services(ctx).files.thumbnail(document_id)
    image = ImageContent(
        type="image",
        data=base64.b64encode(thumbnail.data).decode(),
        mime_type=thumbnail.report.mime_type,
    )
    return ToolResult(content=[_text(thumbnail.report), image])


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["download_document"])
@boundary
async def download_document(
    ctx: Context,
    document_id: DocumentId,
    kind: Annotated[
        FileVersion,
        Field(
            description=(
                "original: the file as it was uploaded. archive: the searchable PDF/A "
                "Paperless made from it; not every document has one (text files do not)."
            )
        ),
    ] = "original",
) -> ToolResult:
    """Get the original file of a document, or its archive version, as an embedded resource.

    The file comes attached as MCP content with its MIME type and file name (also in the JSON
    report before it), never written to disk. A file over the server's download limit (512 KiB
    unless the administrator changed it) is not attached: delivered is false and the report holds
    its web_url. Use get_document_content when you only need the text.
    """
    delivery = await services(ctx).files.download(document_id, kind)
    content: list[ContentBlock] = [_text(delivery.report)]
    if delivery.data is not None:
        blob = BlobResourceContents(
            uri=f"paperless://documents/{document_id}/{kind}",
            mime_type=delivery.report.mime_type,
            blob=base64.b64encode(delivery.data).decode(),
            _meta=_meta(delivery.report),
        )
        content.append(EmbeddedResource(type="resource", resource=blob))
    return ToolResult(content=content)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["update_document"])
@boundary
async def update_document(  # noqa: PLR0913 -- one parameter per changeable field, described for the LLM
    ctx: Context,
    document_id: DocumentId,
    *,
    title: Annotated[str | None, Field(description="New title (at most 128 characters).")] = None,
    correspondent: Named = None,
    document_type: Named = None,
    storage_path: Named = None,
    created: Annotated[date | None, Field(description="New creation date (YYYY-MM-DD).")] = None,
    archive_serial_number: Annotated[
        int | None, Field(ge=0, description="New archive serial number; it must be unused.")
    ] = None,
    owner: Annotated[
        NameOrId | None, Field(description="Username or id of the new owning user.")
    ] = None,
    clear: Annotated[
        list[Clearable] | None,
        Field(
            description=(
                "Fields to empty: correspondent, document_type, storage_path, "
                "archive_serial_number or owner (no owner makes the document visible to every "
                "user with the document permission)."
            )
        ),
    ] = None,
    tags_add: Annotated[
        list[NameOrId] | None, Field(description="Tag names or ids to add; the others stay.")
    ] = None,
    tags_remove: Annotated[
        list[NameOrId] | None, Field(description="Tag names or ids to take off; the others stay.")
    ] = None,
    tags_set: Annotated[
        list[NameOrId] | None,
        Field(
            description="Replace all tags with exactly these (not with tags_add or tags_remove)."
        ),
    ] = None,
    create_missing_tags: Annotated[
        bool,
        Field(
            description=(
                "Create tags in tags_add or tags_set that do not exist yet; they are private to "
                "you (their creator) until shared. Names of only digits are read as ids."
            )
        ),
    ] = False,
    confirm: Annotated[
        bool,
        Field(
            description=(
                "Required to change owner (also to no owner): the new owner takes the document "
                "over and you may lose access, which you cannot undo yourself."
            )
        ),
    ] = False,
    custom_fields: Annotated[
        dict[str, JsonValue] | None,
        Field(
            description=(
                "Custom field values to set, keyed by field name or id; other fields stay. "
                "Values by data type: string/longtext text; url with scheme; date 'YYYY-MM-DD'; "
                "boolean true/false; integer; float; monetary 'EUR12.50' (or 12.5); "
                "documentlink a list of document ids; select the option's label."
            )
        ),
    ] = None,
    remove_custom_fields: Annotated[
        list[NameOrId] | None,
        Field(description="Custom field names or ids to take off the document."),
    ] = None,
) -> DocumentUpdate:
    """Change a document's title, correspondent, type, storage path, dates, owner, tags or custom fields.

    Only what you name changes; everything else stays, also the other custom fields and tags.
    Names match like in search_documents; an unknown or ambiguous name is an error that lists
    candidates and nothing is changed. Tags are hierarchical in Paperless: a child tag always
    comes with its parent (implicit_tags lists parents added for you), and removing a parent
    while its children stay is refused. Document links are symmetrical: linking A to B also
    links B to A and needs change permission on B. Asking for what already holds changes
    nothing. It reads, then writes: a concurrent change of the same document's tags or custom
    fields in between is overwritten. Tags created with create_missing_tags are private to
    you; tag names that are only numbers cannot be addressed by name. Returns the document as get_document does, plus implicit_tags.
    """  # noqa: E501 -- see list_taxonomy; FastMCP sends the docstring as is
    edit = DocumentEdit(
        title=title,
        created=created,
        archive_serial_number=archive_serial_number,
        owner=owner,
        correspondent=correspondent,
        document_type=document_type,
        storage_path=storage_path,
        clear=tuple(clear or ()),
        tags_add=tuple(tags_add or ()),
        tags_remove=tuple(tags_remove or ()),
        tags_set=None if tags_set is None else tuple(tags_set),
        create_missing_tags=create_missing_tags,
        confirm=confirm,
        custom_fields=custom_fields or {},
        remove_custom_fields=tuple(remove_custom_fields or ()),
    )
    return await services(ctx).edits.update(document_id, edit)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["add_note"])
@boundary
async def add_note(
    ctx: Context,
    document_id: DocumentId,
    note: Annotated[str, Field(description=f"Text of the note, 1 to {MAX_NOTE_CHARS} characters.")],
) -> NoteAdded:
    """Add a note to a document, as the current Paperless user.

    Notes are free-text remarks shown with the document and found by search. Each call adds
    another note, so do not repeat it. Needs permission to add notes, and to change the
    document. Read notes with get_document and include notes; delete one with delete_note.
    """
    return await services(ctx).notes.add(document_id, note)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_note"])
@boundary
async def delete_note(
    ctx: Context,
    document_id: DocumentId,
    note_id: Annotated[
        int, Field(description="Id of the note, from get_document with include notes.")
    ],
    *,
    confirm: Confirm = False,
) -> NoteDeleted:
    """Delete a note from a document for good; notes have no trash.

    Two steps: call without confirm first. Nothing is deleted and the error says which note and
    document it would be (author, length and the start of the text), with confirm=true to pass.
    Find note ids with get_document and include notes. Needs permission to view and delete notes,
    and to change the document.
    """
    return await services(ctx).notes.delete(document_id, note_id, confirm=confirm)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_document"])
@boundary
async def delete_document(
    ctx: Context, document_id: DocumentId, *, confirm: Confirm = False
) -> DocumentDeleted:
    """Delete a document by moving it to the Paperless trash, from where it can be restored.

    Two steps: call without confirm first. Nothing is deleted and the error names the document
    that would go, with confirm=true to pass. The trash keeps the document until it is emptied
    (by the administrator's retention setting or by hand); restore it in the Paperless web UI
    or with the trash toolset if enabled. Needs the permission to view and to delete documents,
    and the user must own the document, be a superuser, or the document must be unowned
    (a view or change grant is not enough).
    """
    return await services(ctx).removal.delete(document_id, confirm=confirm)


@provider.resource(
    "paperless://documents/{document_id}",
    name="document",
    title="Document",
    mime_type="application/json",
    tags={TOOLSET},
)
@resource_boundary
async def document_detail(ctx: Context, document_id: int) -> dict[str, object]:
    """A document as get_document returns it, without the include sections or the text."""
    detail = await services(ctx).documents.get(document_id)
    return detail.model_dump(mode="json")


@provider.resource(
    "paperless://documents/{document_id}/content{?offset,max_chars}",
    name="document_content",
    title="Document content",
    mime_type="application/json",
    tags={TOOLSET},
)
@resource_boundary
async def document_content(
    ctx: Context, document_id: int, offset: int = 0, max_chars: int = DEFAULT_SLICE
) -> dict[str, object]:
    """A slice of the OCR text as get_document_content returns it (first 20000 characters)."""
    if offset < 0 or not 1 <= max_chars <= MAX_SLICE:
        message = f"offset must be at least 0 and max_chars between 1 and {MAX_SLICE}."
        raise ResourceError(message)
    piece = await services(ctx).documents.content(document_id, offset=offset, max_chars=max_chars)
    return piece.model_dump(mode="json")


@provider.resource(
    "paperless://taxonomy/{kind}",
    name="taxonomy",
    title="Taxonomy table",
    mime_type="application/json",
    tags={TOOLSET},
)
@resource_boundary
async def taxonomy_table(ctx: Context, kind: TaxonomyKind) -> list[dict[str, object]]:
    """Every tag, correspondent, document type, storage path, custom field, saved view, user or
    group the current user may see, as JSON rows with id, name and document count.
    """  # noqa: D205 -- see list_taxonomy
    rows = await services(ctx).taxonomy.items(kind)
    return [row.model_dump(mode="json") for row in rows]


def _file_resource(report: FileReport, data: bytes | None) -> ResourceResult:
    """The bytes with their own MIME type and file name; a file over the limit is an error."""
    if data is None:
        raise ResourceError(report.note or "The file is not available.")
    content = ResourceContent(content=data, mime_type=report.mime_type, meta=_meta(report))
    return ResourceResult([content])


@provider.resource(
    "paperless://documents/{document_id}/thumbnail",
    name="document_thumbnail",
    title="Document thumbnail",
    mime_type="image/webp",
    tags={TOOLSET},
)
@resource_boundary
async def document_thumbnail(ctx: Context, document_id: int) -> ResourceResult:
    """The thumbnail image of a document, as get_document_thumbnail returns it."""
    thumbnail = await services(ctx).files.thumbnail(document_id)
    return _file_resource(thumbnail.report, thumbnail.data)


@provider.resource(
    "paperless://documents/{document_id}/original",
    name="document_original",
    title="Document original file",
    mime_type="application/octet-stream",
    tags={TOOLSET},
)
@resource_boundary
async def document_original(ctx: Context, document_id: int) -> ResourceResult:
    """The file as uploaded (its own MIME type and name), within the server's download limit."""
    delivery = await services(ctx).files.download(document_id, "original")
    return _file_resource(delivery.report, delivery.data)


@provider.resource(
    "paperless://documents/{document_id}/archive",
    name="document_archive",
    title="Document archive version",
    mime_type="application/pdf",
    tags={TOOLSET},
)
@resource_boundary
async def document_archive(ctx: Context, document_id: int) -> ResourceResult:
    """The searchable PDF/A Paperless made from the upload, within the server's download limit."""
    delivery = await services(ctx).files.download(document_id, "archive")
    return _file_resource(delivery.report, delivery.data)
