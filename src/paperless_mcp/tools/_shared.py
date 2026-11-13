"""What every toolset module shares: toolsets, the annotation table, services, the error boundary.

A new tool takes five steps: an entry in ``ANNOTATIONS`` (title and hints), one in
``TOOL_TOOLSETS``, the tool function in its toolset module with ``tags={TOOLSET}`` and
``annotations=ANNOTATIONS[name]``, ``@boundary`` directly on the function, and the use case it
calls on ``Services``. The registry tests
(tests/tools/test_registry.py) fail until all five are in place.
"""

import functools
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Final, cast

from fastmcp.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from paperless_mcp.domain.errors import ConfirmationRequired, PaperlessMcpError, Validation

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from fastmcp import Context

    from paperless_mcp.services.ai import AiService
    from paperless_mcp.services.bulk_documents import BulkDocumentEditor
    from paperless_mcp.services.bulk_taxonomy import BulkTaxonomyEditor
    from paperless_mcp.services.document_edits import DocumentEditor
    from paperless_mcp.services.documents import DocumentService
    from paperless_mcp.services.files import FileService
    from paperless_mcp.services.instance import InstanceService
    from paperless_mcp.services.notes import NoteService
    from paperless_mcp.services.removal import DocumentRemoval
    from paperless_mcp.services.tasks import TaskService
    from paperless_mcp.services.taxonomy import TaxonomyResolver
    from paperless_mcp.services.taxonomy_edits import TaxonomyEditor
    from paperless_mcp.services.trash import TrashService
    from paperless_mcp.services.uploads import UploadService
    from paperless_mcp.services.version_uploads import VersionUploadService
    from paperless_mcp.services.versions import VersionService
    from paperless_mcp.services.views import ViewService

# Every toolset; each tool carries exactly one of them as its only tag.
TOOLSETS: Final = frozenset({"core", "ai", "bulk", "versions", "views", "trash"})

# Tools whose result is an image or a file as MCP content blocks, with no output schema and no
# structured content (decision-11); every other tool has a typed return model (decision-4).
MEDIA_TOOLS: Final = frozenset({"get_document_thumbnail", "download_document"})

# The toolset of every tool, keyed by tool name. It must equal each tool's tag (registry test); the
# server uses it to refuse a configuration that leaves no tool before it contacts Paperless.
TOOL_TOOLSETS: Final[Mapping[str, str]] = {
    "get_instance_overview": "core",
    "list_taxonomy": "core",
    "search_documents": "core",
    "get_document": "core",
    "get_document_content": "core",
    "get_document_thumbnail": "core",
    "download_document": "core",
    "get_task": "core",
    "update_document": "core",
    "add_note": "core",
    "delete_note": "core",
    "delete_document": "core",
    "upload_document": "core",
    "ask_paperless_ai": "ai",
    "get_ai_suggestions": "ai",
    "create_taxonomy_item": "core",
    "update_taxonomy_item": "core",
    "delete_taxonomy_item": "core",
    "bulk_edit_documents": "bulk",
    "bulk_edit_taxonomy": "bulk",
    "list_document_versions": "versions",
    "upload_document_version": "versions",
    "set_document_version_label": "versions",
    "delete_document_version": "versions",
    "merge_documents_as_versions": "versions",
    "list_saved_views": "views",
    "get_saved_view": "views",
    "create_saved_view": "views",
    "update_saved_view": "views",
    "delete_saved_view": "views",
    "run_saved_view": "views",
    "list_trash": "trash",
    "restore_from_trash": "trash",
    "empty_trash": "trash",
}

# Toolsets that need a Paperless capability, mapped to it (only "ai" so far); the startup probe
# hides such a toolset when the capability is known to be missing.
TOOLSET_REQUIRES: Final[Mapping[str, str]] = {"ai": "ai"}

# The one place tool titles and behaviour hints are set (decision-4), keyed by tool name.
ANNOTATIONS: Final[Mapping[str, ToolAnnotations]] = {
    "get_instance_overview": ToolAnnotations(
        title="Get instance overview",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "list_taxonomy": ToolAnnotations(
        title="List taxonomy",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "search_documents": ToolAnnotations(
        title="Search documents",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "get_document": ToolAnnotations(
        title="Get document",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "get_document_content": ToolAnnotations(
        title="Get document content",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "get_document_thumbnail": ToolAnnotations(
        title="Get document thumbnail",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "download_document": ToolAnnotations(
        title="Download document",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "get_task": ToolAnnotations(
        title="Get task",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Not destructive (decision-6): it never drops what the caller did not name, so it needs no
    # confirm; what it overwrites is the caller's explicit request and stays in the audit log.
    "update_document": ToolAnnotations(
        title="Update document",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Adds a record and removes none; repeating the call adds another note, so it is not idempotent.
    "add_note": ToolAnnotations(
        title="Add note",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    ),
    # Destructive tools take confirm and change nothing without it (decision-6). A note has no
    # trash. Deleting again leaves the state as it is (idempotent), though the second call fails.
    "delete_note": ToolAnnotations(
        title="Delete note",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Moves the document to the Paperless trash, from where it can be restored.
    "delete_document": ToolAnnotations(
        title="Delete document",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Not destructive and not idempotent (decision-6): it adds a document and takes nothing away,
    # but every call adds one, also for content Paperless already holds (3.4.2 keeps duplicates).
    "upload_document": ToolAnnotations(
        title="Upload document",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    ),
    # The AI tools change nothing in Paperless (read-only; repeating a call leaves the environment
    # unchanged, which is what MCP's idempotent hint means, though the text differs) and reach
    # beyond it: Paperless sends document text to its LLM backend (open world), at a cost per call.
    # Read-only clients may therefore auto-confirm paid calls; see PAPERLESS_MCP_TOOLSETS.
    "ask_paperless_ai": ToolAnnotations(
        title="Ask Paperless AI",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    ),
    "get_ai_suggestions": ToolAnnotations(
        title="Get AI suggestions",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    ),
    # Not destructive (decision-14): it never drops what the caller did not name; removing select
    # options, the one lossy change, needs confirm like update_document's owner change.
    "update_taxonomy_item": ToolAnnotations(
        title="Update taxonomy item",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "create_taxonomy_item": ToolAnnotations(
        title="Create taxonomy item",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    ),
    "list_saved_views": ToolAnnotations(
        title="List saved views",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "get_saved_view": ToolAnnotations(
        title="Get saved view",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "run_saved_view": ToolAnnotations(
        title="Run saved view",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Saved views are filters, not documents (decision-14 applies by analogy): creating one adds a
    # record, updating one replaces the rules the caller names, and neither needs a confirm.
    "create_saved_view": ToolAnnotations(
        title="Create saved view",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    ),
    "update_saved_view": ToolAnnotations(
        title="Update saved view",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Destructive (decision-6): a view has no trash. No document is touched. Deleting again
    # leaves the state as it is (idempotent), though the second call fails.
    "delete_saved_view": ToolAnnotations(
        title="Delete saved view",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Destructive (decision-6, decision-14): no trash for taxonomy, a tag takes its child tags.
    "delete_taxonomy_item": ToolAnnotations(
        title="Delete taxonomy item",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
    "list_document_versions": ToolAnnotations(
        title="List document versions",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Not destructive and not idempotent (decision-6): it adds a version and takes nothing away
    # (the older versions stay), but every call adds one, also for content already held.
    "upload_document_version": ToolAnnotations(
        title="Upload document version",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    ),
    # Not destructive: it only sets the one label the caller named, which is the whole request.
    "set_document_version_label": ToolAnnotations(
        title="Set document version label",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Destructive (decision-6): the version goes to the Paperless trash, from where it can be
    # restored, and the document then shows the version before it.
    "delete_document_version": ToolAnnotations(
        title="Delete document version",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Destructive (decision-6): the merged documents vanish for good, no trash, with their
    # metadata and notes; repeating the call fails because they are gone.
    "merge_documents_as_versions": ToolAnnotations(
        title="Merge documents as versions",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
    # Destructive (decision-6): it can trash documents and overwrite what many of them hold, so
    # every execution needs confirm, and dry_run previews it. Not idempotent: a repeated trashing
    # or reprocessing is another action (a repeated tag change leaves the state as it is).
    "bulk_edit_documents": ToolAnnotations(
        title="Bulk edit documents",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
    # Destructive (decision-6, decision-14): deleting objects is final and permissions can lock
    # users out, so every execution needs confirm, and dry_run previews it.
    "bulk_edit_taxonomy": ToolAnnotations(
        title="Bulk edit taxonomy",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
    "list_trash": ToolAnnotations(
        title="List trash",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Not destructive (decision-6): it only undoes a deletion and drops nothing. Restoring again
    # leaves the state as it is (idempotent), though the second call fails.
    "restore_from_trash": ToolAnnotations(
        title="Restore from trash",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    # Destructive (decision-6, decision-15): documents and deleted versions are removed for good,
    # so it takes ids or all=true and confirm in both cases. Not idempotent: with all=true a
    # repeated call empties whatever was deleted since.
    "empty_trash": ToolAnnotations(
        title="Empty trash",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
}

logger = logging.getLogger(__name__)

# The `confirm` parameter of every destructive tool (decision-6, decision-15).
type Confirm = Annotated[
    bool,
    Field(
        description=(
            "Must be true to go ahead. Without it nothing changes and the error says what would "
            "happen. Describe that to the user and pass true only once they agree, or if they "
            "already asked for exactly this."
        )
    ),
]

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


# Key of the ``Services`` in the server's lifespan context.
SERVICES: Final = "services"


@dataclass(frozen=True, slots=True)
class Services:
    """The use cases tools call, one field per service; built by the composition root."""

    instance: InstanceService
    taxonomy: TaxonomyResolver
    taxonomy_edits: TaxonomyEditor
    documents: DocumentService
    files: FileService
    edits: DocumentEditor
    notes: NoteService
    removal: DocumentRemoval
    tasks: TaskService
    ai: AiService
    uploads: UploadService
    versions: VersionService
    version_uploads: VersionUploadService
    bulk_documents: BulkDocumentEditor
    bulk_taxonomy: BulkTaxonomyEditor
    views: ViewService
    trash: TrashService


def services(ctx: Context) -> Services:
    """The services of the running server (set up by its lifespan)."""
    return cast("Services", ctx.lifespan_context[SERVICES])


def boundary[**P, R](tool: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """The error boundary of a tool: every exception becomes a ``ToolError`` the client sees.

    A domain error's message and hint are client-safe by contract and reach the client. Any
    other exception is a bug: the client gets a generic message, and the log at ERROR gets only
    its type, because its message or a cause may hold response content (pydantic errors quote
    their input). The traceback is logged at DEBUG. Causes are dropped (``from None``); FastMCP
    logs a ``ToolError`` without traceback.
    """
    name = tool.__name__

    @functools.wraps(tool)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await tool(*args, **kwargs)
        except ToolError:
            raise
        except ConfirmationRequired as refusal:
            # The normal first step of a two-step flow, not a failure (decision-15).
            raise ToolError(refusal.describe(), log_level=logging.INFO) from None
        except PaperlessMcpError as error:
            raise ToolError(error.describe()) from None
        except Exception as error:
            logger.error("%s failed: %s", name, type(error).__name__)  # noqa: TRY400 -- no traceback above DEBUG
            logger.debug("%s failed", name, exc_info=True)
            message = f"Internal error in {name}; the server log has details."
            raise ToolError(message) from None

    return guarded


def resource_boundary[**P, R](resource: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """The error boundary of a resource, like ``boundary`` for tools.

    A domain error becomes a ``ResourceError`` with its client-safe message and hint, logged at
    INFO. Any other exception is a bug: the client gets a generic message and the log gets only
    the exception type at ERROR (the traceback at DEBUG).
    """
    name = resource.__name__

    @functools.wraps(resource)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await resource(*args, **kwargs)
        except ResourceError:
            raise
        except PaperlessMcpError as error:
            logger.info("%s: %s", name, error.describe())
            raise ResourceError(error.describe()) from None
        except Exception as error:
            logger.error("%s failed: %s", name, type(error).__name__)  # noqa: TRY400 -- no traceback above DEBUG
            logger.debug("%s failed", name, exc_info=True)
            message = f"Internal error in {name}; the server log has details."
            raise ResourceError(message) from None

    return guarded
