"""upload_document_version for stdio with an allowlist: base64 content or a path.

Registered instead of the plain variant (``versions.remote``) only when the server runs over
stdio and the operator named directories in ``PAPERLESS_MCP_UPLOAD_DIRS`` (decision-7), exactly
like ``upload_stdio`` for ``upload_document``; over HTTP this module's tool does not exist, so its
``path`` parameter is in no schema a remote caller sees. The service reads only regular files
inside the allowlist and answers every other path the same way.
"""

from pathlib import Path
from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from paperless_mcp.domain.errors import Validation
from paperless_mcp.domain.versions import VersionUploadResult
from paperless_mcp.tools._shared import ANNOTATIONS, boundary, check_transport, services
from paperless_mcp.tools.versions import COMMON, TOOLSET, DocumentId, Label, Wait

local = LocalProvider()

LEAD = (
    "Upload a file to a document as a new version. Give the file either as path (an absolute "
    "path of a regular file inside the directories the operator allowed; symlinks are followed "
    "only to files inside them) or as base64 content_base64 with a filename. "
)
DESCRIPTION = LEAD + COMMON


@local.tool(
    tags={TOOLSET}, annotations=ANNOTATIONS["upload_document_version"], description=DESCRIPTION
)
@boundary
async def upload_document_version(  # noqa: PLR0913 -- the document, the file and its options
    ctx: Context,
    document_id: DocumentId,
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
                "The name to upload under, e.g. 'Rechnung März v2.pdf'; required with "
                "content_base64, default with path: the file's own name. Directories are dropped."
            )
        ),
    ] = None,
    label: Label = None,
    wait_seconds: Wait = 0,
) -> VersionUploadResult:
    """Upload a file by path or as base64 content as a new version; see DESCRIPTION."""
    if (path is None) == (content_base64 is None):
        message = "Give exactly one of path and content_base64."
        raise Validation(message, hint="Use path for a file on this machine, else content_base64.")
    uploads = services(ctx).version_uploads
    if path is not None:
        check_transport(ctx.transport)
        return await uploads.from_path(
            document_id, str(path), filename, label, wait_seconds=wait_seconds
        )
    if filename is None:
        message = "filename is required with content_base64."
        raise Validation(message, hint="Name the file, e.g. 'invoice.pdf'.")
    return await uploads.from_content(
        document_id, content_base64 or "", filename, label, wait_seconds=wait_seconds
    )
