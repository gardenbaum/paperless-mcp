"""upload_document for stdio with an allowlist: base64 content or a path.

Registered instead of the plain variant (``upload``) only when the server runs over stdio and
the operator named directories in ``PAPERLESS_MCP_UPLOAD_DIRS`` (decision-7); over HTTP this
module's tool does not exist, so its ``path`` parameter is in no schema a remote caller sees. The
service reads only regular files inside the allowlist and answers every other path the same way.
"""

from pathlib import Path
from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from paperless_mcp.domain.documents import UploadMetadata, UploadResult
from paperless_mcp.domain.errors import Validation
from paperless_mcp.tools._shared import ANNOTATIONS, boundary, services
from paperless_mcp.tools.upload import (
    COMMON,
    TOOLSET,
    Created,
    CustomFields,
    Named,
    SerialNumber,
    Tags,
    Title,
    Wait,
)

local = LocalProvider()

# The transports of a remote caller (FastMCP's names); a path is never read for one of them.
HTTP_TRANSPORTS = frozenset({"sse", "streamable-http"})


def check_transport(transport: str | None) -> None:
    """Refuse a path unless the request came over stdio (or the server runs in-process).

    The server never registers this tool over HTTP; this is the second lock, for a server that
    was started on the wrong transport. ``None``: no transport (tests, embedding).

    Raises:
        Validation: The request came over an HTTP transport.
    """
    if transport in HTTP_TRANSPORTS:
        message = "A path can only be read when the server runs over stdio."
        raise Validation(message, hint="Send the file as content_base64.")


LEAD = (
    "Upload a file to Paperless as a new document. Give the file either as path (an absolute "
    "path of a regular file inside the directories the operator allowed; symlinks are followed "
    "only to files inside them) or as base64 content_base64 with a filename. "
)
DESCRIPTION = LEAD + COMMON


@local.tool(tags={TOOLSET}, annotations=ANNOTATIONS["upload_document"], description=DESCRIPTION)
@boundary
async def upload_document(  # noqa: PLR0913 -- the file plus one parameter per metadata field
    ctx: Context,
    *,
    path: Annotated[
        Path | None,
        Field(
            description=(
                "Absolute path of the file on the machine this server runs on. Only files inside "
                "the directories the operator allowed can be read; anything else is not found. "
                "Not together with content_base64."
            )
        ),
    ] = None,
    content_base64: Annotated[
        str | None,
        Field(
            description=(
                "The file's bytes as standard base64 on one line, without a data: prefix; needs "
                "filename. Not together with path."
            )
        ),
    ] = None,
    filename: Annotated[
        str | None,
        Field(
            description=(
                "The name to upload under, e.g. 'Rechnung März.pdf'; required with content_base64, "
                "default with path: the file's own name. Directories are dropped."
            )
        ),
    ] = None,
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
    """Upload a file by path or as base64 content; the tool description is DESCRIPTION."""
    if (path is None) == (content_base64 is None):
        message = "Give exactly one of path and content_base64."
        raise Validation(message, hint="Use path for a file on this machine, else content_base64.")
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
    uploads = services(ctx).uploads
    if path is not None:
        check_transport(ctx.transport)
        return await uploads.from_path(str(path), filename, metadata, wait_seconds=wait_seconds)
    if filename is None:
        message = "filename is required with content_base64."
        raise Validation(message, hint="Name the file, e.g. 'invoice.pdf'.")
    return await uploads.from_content(
        content_base64 or "", filename, metadata, wait_seconds=wait_seconds
    )
