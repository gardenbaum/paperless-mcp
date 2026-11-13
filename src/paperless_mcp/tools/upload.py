"""upload_document for every caller: the file arrives as base64 content.

This is the variant HTTP clients get, and stdio clients too unless the operator allows paths
(``PAPERLESS_MCP_UPLOAD_DIRS``, see ``upload_stdio``). It has no path parameter and no code that
reads a path (decision-7). The metadata parameters and the description's wording are shared with
the stdio variant, which differs only in the source of the bytes.
"""

from datetime import date
from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field, JsonValue

from paperless_mcp.domain.documents import NameOrId, UploadMetadata, UploadResult
from paperless_mcp.services.tasks import MAX_WAIT_SECONDS
from paperless_mcp.tools._shared import ANNOTATIONS, boundary, services

TOOLSET = "core"

# The tool's name is the same in both variants; the server registers exactly one of them.
remote = LocalProvider()

type Named = Annotated[NameOrId | None, Field(description="Name or id; see list_taxonomy.")]
type Title = Annotated[
    str | None,
    Field(description="Title (at most 128 characters); default: the file name without extension."),
]
type Created = Annotated[
    date | None, Field(description="Creation date (YYYY-MM-DD); default: Paperless reads it.")
]
type SerialNumber = Annotated[
    int | None,
    Field(ge=0, le=4_294_967_295, description="Archive serial number; it must be unused."),
]
type Tags = Annotated[
    list[NameOrId] | None,
    Field(description="Tag names or ids; they must exist (none is created)."),
]
type CustomFields = Annotated[
    dict[str, JsonValue] | None,
    Field(
        description=(
            "Custom field values keyed by field name or id. Values by data type: string/longtext "
            "text; url with scheme; date 'YYYY-MM-DD'; boolean true/false; integer; float; "
            "monetary 'EUR12.50' (or 12.5); documentlink a list of document ids; select the "
            "option's label."
        )
    ),
]
type Wait = Annotated[
    int,
    Field(
        ge=0,
        description=(
            f"Wait up to this many seconds for Paperless to consume the file and return the new "
            f"document_id; more than {MAX_WAIT_SECONDS} is cut to {MAX_WAIT_SECONDS}. "
            "0 returns the task id at once; follow it with get_task."
        ),
    ),
]

COMMON = (
    "The type comes from the content, not the name: PDF, images, plain text and CSV (Office, "
    "OpenDocument and RTF only if Paperless runs Tika). Metadata is given by name as in "
    "search_documents; an unknown name is an error and nothing is uploaded; tags are never "
    "created. Paperless consumes the file later: the result holds the task_id and, with "
    "wait_seconds, the new document_id and web_url; a problem found only then (a taken archive "
    "serial number) shows as state failure. Each call adds a document, also for content "
    "Paperless already holds (no duplicate check)."
)


DESCRIPTION = "Upload a file, sent as base64 content, to Paperless as a new document. " + COMMON


@remote.tool(tags={TOOLSET}, annotations=ANNOTATIONS["upload_document"], description=DESCRIPTION)
@boundary
async def upload_document(  # noqa: PLR0913 -- the file plus one parameter per metadata field
    ctx: Context,
    content_base64: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "The file's bytes as standard base64 (RFC 4648) on one line, without a data: "
                "prefix. The server refuses files over its size limit (10 MiB by default)."
            ),
        ),
    ],
    filename: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "The file's name, e.g. 'Rechnung März.pdf'. Directories are dropped, other "
                "characters (non-ASCII too) are kept; Paperless keeps it as the original name."
            ),
        ),
    ],
    *,
    title: Title = None,
    correspondent: Named = None,
    document_type: Named = None,
    storage_path: Named = None,
    tags: Tags = None,
    created: Created = None,
    archive_serial_number: SerialNumber = None,
    custom_fields: CustomFields = None,
    wait_seconds: Wait = 0,
) -> UploadResult:
    """Upload a file as base64 content; the tool description is DESCRIPTION."""
    metadata = UploadMetadata(
        title=title,
        created=created,
        archive_serial_number=archive_serial_number,
        correspondent=correspondent,
        document_type=document_type,
        storage_path=storage_path,
        tags=tuple(tags or ()),
        custom_fields=custom_fields or {},
    )
    return await services(ctx).uploads.from_content(
        content_base64, filename, metadata, wait_seconds=wait_seconds
    )
