"""The versions toolset: the file versions of a document (opt-in, not in the default profile).

Paperless 3.x keeps several files per document. The root version is the original upload, the
newest version is the file and text the document shows, and title, tags and the other metadata
belong to the document. Uploading a file adds a version; deleting one moves it to the trash;
merging documents as versions removes the merged documents for good, so it needs confirm like
deleting does (decision-6, decision-15). ``upload_document_version`` has the same two forms as
``upload_document`` (decision-7): ``remote`` here, with base64 content, and ``local`` in
``versions_stdio``, which adds a path for stdio with an allowlist.
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from paperless_mcp.domain.versions import (
    DocumentVersions,
    VersionDeleted,
    VersionLabelSet,
    VersionsMerged,
    VersionUploadResult,
)
from paperless_mcp.services.tasks import MAX_WAIT_SECONDS
from paperless_mcp.services.versions import MAX_LABEL, MAX_SOURCES
from paperless_mcp.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "versions"

provider = LocalProvider()
# The tool's name is the same in both variants; the server registers exactly one of them.
remote = LocalProvider()

type DocumentId = Annotated[int, Field(description="Document id, from search_documents.")]
type VersionId = Annotated[
    int,
    Field(description="Version id, from list_document_versions (the root's is the document's)."),
]
type Label = Annotated[
    str | None,
    Field(description=f"Version label, at most {MAX_LABEL} characters; default: none."),
]
type Wait = Annotated[
    int,
    Field(
        ge=0,
        description=(
            f"Wait up to this many seconds for Paperless to consume the file and return the new "
            f"version_id; more than {MAX_WAIT_SECONDS} is cut to {MAX_WAIT_SECONDS}. "
            "0 returns the task id at once; follow it with get_task."
        ),
    ),
]

COMMON = (
    "The new version becomes the newest one: from then on the document's file, download and "
    "text are the new file's, while title, tags and other metadata stay and the older versions "
    "are kept. The type comes from the content, not the name: PDF, images, plain text and CSV "
    "(Office, OpenDocument and RTF only if Paperless runs Tika). Paperless consumes the file "
    "later: the result holds the task_id and, with wait_seconds, the version_id; a problem found "
    "only then shows as state failure. Each call adds a version, also for content Paperless "
    "already holds (no duplicate check). Needs the permissions to add and change documents, and "
    "the user must own the document, hold a change grant on it, or be a superuser, or the "
    "document must be unowned."
)
DESCRIPTION = "Upload a file, sent as base64 content, to a document as a new version. " + COMMON


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_document_versions"])
@boundary
async def list_document_versions(ctx: Context, document_id: DocumentId) -> DocumentVersions:
    """List the file versions of a document, newest first.

    The newest version is the file and text the document shows (get_document_content,
    download_document); the root is the original upload. Each version has an id, its upload
    time, an optional label and the checksum of its file (equal checksums are the same file).
    """
    return await services(ctx).versions.list_versions(document_id)


@remote.tool(
    tags={TOOLSET}, annotations=ANNOTATIONS["upload_document_version"], description=DESCRIPTION
)
@boundary
async def upload_document_version(  # noqa: PLR0913 -- the document, the file and its options
    ctx: Context,
    document_id: DocumentId,
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
                "The file's name, e.g. 'Rechnung März v2.pdf'. Directories are dropped, other "
                "characters (non-ASCII too) are kept."
            ),
        ),
    ],
    *,
    label: Label = None,
    wait_seconds: Wait = 0,
) -> VersionUploadResult:
    """Upload a file as base64 content as a new version; the tool description is DESCRIPTION."""
    return await services(ctx).version_uploads.from_content(
        document_id, content_base64, filename, label, wait_seconds=wait_seconds
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["set_document_version_label"])
@boundary
async def set_document_version_label(
    ctx: Context,
    document_id: DocumentId,
    version_id: VersionId,
    label: Annotated[
        str,
        Field(
            description=(
                f"The new label, at most {MAX_LABEL} characters; an empty string clears it."
            )
        ),
    ],
) -> VersionLabelSet:
    """Set or clear the label of one version of a document, the root version's too.

    Labels are free text (e.g. 'signed copy') and only help tell versions apart; nothing else
    changes. Needs the permission to change documents and ownership of the document or a change
    grant on it.
    """
    return await services(ctx).versions.set_label(document_id, version_id, label)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_document_version"])
@boundary
async def delete_document_version(
    ctx: Context, document_id: DocumentId, version_id: VersionId, *, confirm: Confirm = False
) -> VersionDeleted:
    """Delete one version of a document by moving it to the Paperless trash.

    Two steps: call without confirm first. Nothing is deleted and the error names the version
    and what the document shows afterwards, with confirm=true to pass. If it is the newest
    version, the document's file and text revert to the version before it. The root version (the
    original upload) cannot be deleted this way: delete the whole document with delete_document.
    The trash keeps the version until it is emptied; restore it in the Paperless web UI or with
    the trash toolset if enabled. Needs the permission to delete documents, and the user must own
    the document, be a superuser, or the document must be unowned (a change grant is not enough).
    """
    return await services(ctx).versions.delete(document_id, version_id, confirm=confirm)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["merge_documents_as_versions"])
@boundary
async def merge_documents_as_versions(
    ctx: Context,
    root_document_id: Annotated[
        int, Field(description="The document that keeps its identity and gets the new versions.")
    ],
    source_document_ids: Annotated[
        list[int],
        Field(
            description=(
                f"The documents whose files become versions of the root, in this order (the last "
                f"one newest); at most {MAX_SOURCES}, none with versions of its own."
            )
        ),
    ],
    *,
    label: Annotated[
        str | None,
        Field(
            description=(
                f"Version label (at most {MAX_LABEL} characters) for the new version; only when "
                "exactly one document is merged."
            )
        ),
    ] = None,
    confirm: Confirm = False,
) -> VersionsMerged:
    """Merge documents into another document as its newest versions.

    The merged documents vanish for good (not even into the trash): their titles, tags,
    correspondents, notes and other metadata are lost and only their files stay, as versions of
    the root document, which keeps its own metadata. Two steps: call without confirm first.
    Nothing is merged and the error names the documents and what would happen, with confirm=true
    to pass. Documents that already have versions of their own cannot be merged in. Needs the
    permissions to change and to delete documents, and the user must own every document (or be a
    superuser, or the documents must be unowned); a change grant is not enough.
    """
    return await services(ctx).versions.merge(
        root_document_id, source_document_ids, label, confirm=confirm
    )
