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
from typing import TYPE_CHECKING, Final, cast

from fastmcp.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations

from paperless_mcp.domain.errors import PaperlessMcpError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from fastmcp import Context

    from paperless_mcp.services.document_edits import DocumentEditor
    from paperless_mcp.services.ai import AiService
    from paperless_mcp.services.documents import DocumentService
    from paperless_mcp.services.files import FileService
    from paperless_mcp.services.instance import InstanceService
    from paperless_mcp.services.tasks import TaskService
    from paperless_mcp.services.taxonomy import TaxonomyResolver

# Every toolset; each tool carries exactly one of them as its only tag.
TOOLSETS: Final = frozenset({"core", "ai"})

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
    "ask_paperless_ai": "ai",
    "get_ai_suggestions": "ai",
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
}

logger = logging.getLogger(__name__)

# Key of the ``Services`` in the server's lifespan context.
SERVICES: Final = "services"


@dataclass(frozen=True, slots=True)
class Services:
    """The use cases tools call, one field per service; built by the composition root."""

    instance: InstanceService
    taxonomy: TaxonomyResolver
    documents: DocumentService
    files: FileService
    edits: DocumentEditor
    tasks: TaskService
    ai: AiService


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
