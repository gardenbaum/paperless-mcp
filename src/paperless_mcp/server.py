"""Composition root: wires settings, adapter, services and tool providers into the server.

The only module that imports both ``tools`` and ``paperless``. ``create_server`` assembles; the
server's lifespan connects: it owns the one ``httpx2.AsyncClient`` of the process, builds the
services on it and probes Paperless before the first request is served.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from fastmcp.server.transforms.visibility import Visibility

from paperless_mcp import __version__
from paperless_mcp.domain.errors import InvalidConfiguration
from paperless_mcp.observability import CallLogging
from paperless_mcp.paperless.ai import PaperlessAiReader
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.bulk import PaperlessBulkWriter
from paperless_mcp.paperless.client import RetryPolicy, connect
from paperless_mcp.paperless.documents import PaperlessDocumentReader, PaperlessDocumentWriter
from paperless_mcp.paperless.instance import PaperlessInstanceReader
from paperless_mcp.paperless.tasks import PaperlessTaskReader
from paperless_mcp.paperless.taxonomy import PaperlessTaxonomyReader, PaperlessTaxonomyWriter
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
from paperless_mcp.services.uploads import UploadService
from paperless_mcp.services.version_uploads import VersionUploadService
from paperless_mcp.services.versions import VersionService
from paperless_mcp.tools import ai, bulk, core, upload, upload_stdio, versions, versions_stdio
from paperless_mcp.tools._shared import (
    ANNOTATIONS,
    SERVICES,
    TOOL_TOOLSETS,
    TOOLSET_REQUIRES,
    TOOLSETS,
    Services,
)
from paperless_mcp.tools.selection import (
    hidden_tools,
    selected_toolsets,
    unavailable_toolsets,
    visible_tools,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

    from fastmcp.server.providers import Provider
    from mcp.types import ToolAnnotations

    from paperless_mcp.domain.instance import InstanceOverview
    from paperless_mcp.domain.ports import (
        AiReader,
        BulkWriter,
        DocumentReader,
        DocumentWriter,
        InstanceReader,
        TaskReader,
        TaxonomyReader,
        TaxonomyWriter,
    )
    from paperless_mcp.paperless.client import PaperlessClient
    from paperless_mcp.settings import PaperlessSettings, ServerSettings

# Seconds per Paperless request; uploads get longer (they carry the file).
REQUEST_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 120.0
# Seconds a taxonomy list is reused before it is read again; this server's writes invalidate it.
TAXONOMY_TTL = 300.0

INSTRUCTIONS = (
    "Tools for a paperless-ngx document archive: find, read and organise documents and their "
    "tags, correspondents, document types and storage paths. Start with get_instance_overview "
    "to learn the instance and what the current user may do."
)

NO_TOOLS = (
    "No tools are left to offer: PAPERLESS_MCP_TOOLSETS, PAPERLESS_MCP_READ_ONLY,"
    " PAPERLESS_MCP_DISABLED_TOOLS and the capabilities of this Paperless leave no tool."
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Catalogue:
    """Every tool the server can offer and what selection needs to know about them.

    Attributes:
        providers: One FastMCP provider per toolset module.
        remote_providers: Providers added when the server offers no uploads by path: the tools
            that exist in every mode, in their form for HTTP (and for stdio without an allowlist).
        local_providers: Providers added instead of ``remote_providers`` when the server runs
            over stdio with an upload allowlist: the same tools in their form with a path.
        annotations: The central annotation table, keyed by tool name.
        tool_toolsets: The toolset of every tool, keyed by tool name.
        toolsets: Every toolset name.
        requires: Toolsets that need a Paperless capability, mapped to it.
    """

    providers: Sequence[Provider]
    annotations: Mapping[str, ToolAnnotations]
    tool_toolsets: Mapping[str, str]
    toolsets: frozenset[str]
    requires: Mapping[str, str]
    remote_providers: Sequence[Provider] = ()
    local_providers: Sequence[Provider] = ()


CATALOGUE: Final = Catalogue(
    providers=(core.provider, ai.provider, bulk.provider, versions.provider),
    annotations=ANNOTATIONS,
    tool_toolsets=TOOL_TOOLSETS,
    toolsets=TOOLSETS,
    requires=TOOLSET_REQUIRES,
    remote_providers=(upload.remote, versions.remote),
    local_providers=(upload_stdio.local, versions_stdio.local),
)


@dataclass(frozen=True, slots=True)
class Ports:
    """One implementation of every domain port, one field per port."""

    instance: InstanceReader
    taxonomy: TaxonomyReader
    documents: DocumentReader
    tasks: TaskReader
    document_writes: DocumentWriter
    taxonomy_writes: TaxonomyWriter
    bulk_writes: BulkWriter
    ai: AiReader


def paperless_ports(client: PaperlessClient) -> Ports:
    """The Paperless adapter for every port, all on ``client``."""
    return Ports(
        instance=PaperlessInstanceReader(client),
        taxonomy=PaperlessTaxonomyReader(client),
        documents=PaperlessDocumentReader(client),
        tasks=PaperlessTaskReader(client),
        document_writes=PaperlessDocumentWriter(client),
        taxonomy_writes=PaperlessTaxonomyWriter(client),
        bulk_writes=PaperlessBulkWriter(client),
        ai=PaperlessAiReader(client),
    )


def create_server(  # noqa: PLR0913 -- one keyword per seam, all defaulted
    paperless: PaperlessSettings,
    settings: ServerSettings,
    *,
    catalogue: Catalogue = CATALOGUE,
    ports: Callable[[PaperlessClient], Ports] = paperless_ports,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    stdio: bool = False,
) -> FastMCP:
    """Assemble the MCP server.

    Args:
        paperless: Where Paperless is and the token to use.
        settings: Settings of this server, including which toolsets and tools to offer.
        catalogue: The tools the server can offer; tests pass a larger one.
        ports: Builds the ports on the connected client; tests pass fakes here.
        sleep: Waits between task polls; tests pass one that does not wait.
        clock: Measures the task wait; tests pass the clock their ``sleep`` advances.
        stdio: Whether the server runs over stdio. Only then, and only with
            ``settings.upload_dirs``, does ``upload_document`` take a path; in every other
            case that parameter is in no schema (decision-7).

    Returns:
        The server. Starting it (its lifespan) raises the probe's ``PaperlessMcpError`` when
        Paperless rejects the token or API version 10, and ``InvalidConfiguration`` when the
        selection leaves no tool.

    Raises:
        InvalidConfiguration: A toolset or a disabled tool in the settings does not exist, or
            the settings alone leave no tool.
    """
    credentials = StaticTokenProvider(paperless.token.get_secret_value())
    web_url = str(paperless.public_url or paperless.url)
    by_path = stdio and bool(settings.upload_dirs)
    variants = catalogue.local_providers if by_path else catalogue.remote_providers

    # Allowlist the selected toolsets (resources carry the tags too), then take out tools:
    # later visibility rules override earlier ones.
    enabled = selected_toolsets(settings.toolsets, catalogue.toolsets)
    hidden = hidden_tools(
        catalogue.annotations, read_only=settings.read_only, disabled=settings.disabled_tools
    )
    if not visible_tools(catalogue.tool_toolsets, enabled, hidden):
        raise InvalidConfiguration(NO_TOOLS)
    # The one rule that hides toolsets whose capability is missing; each start replaces its tags.
    capability_rule = Visibility(enabled=False, tags=set())

    @lifespan
    async def start(running: FastMCP) -> AsyncIterator[dict[str, object]]:
        async with connect(
            str(paperless.url),
            credentials,
            timeout=REQUEST_TIMEOUT,
            upload_timeout=UPLOAD_TIMEOUT,
            retry=RetryPolicy(),
        ) as client:
            adapters = ports(client)
            taxonomy = TaxonomyResolver(adapters.taxonomy, credentials, ttl=TAXONOMY_TTL)
            documents = DocumentService(adapters.documents, taxonomy, web_url=web_url)
            tasks = TaskService(adapters.tasks, sleep=sleep, clock=clock)
            services = Services(
                instance=InstanceService(adapters.instance),
                taxonomy=taxonomy,
                taxonomy_edits=TaxonomyEditor(
                    adapters.taxonomy, adapters.taxonomy_writes, taxonomy, adapters.documents
                ),
                documents=documents,
                files=FileService(
                    adapters.documents, web_url=web_url, max_bytes=settings.max_download_bytes
                ),
                edits=DocumentEditor(
                    adapters.documents,
                    adapters.document_writes,
                    adapters.taxonomy_writes,
                    taxonomy,
                    documents=documents,
                ),
                notes=NoteService(adapters.documents, adapters.document_writes, web_url=web_url),
                removal=DocumentRemoval(adapters.documents, adapters.document_writes, taxonomy),
                tasks=tasks,
                ai=AiService(adapters.ai, taxonomy, web_url=web_url),
                uploads=UploadService(
                    adapters.documents,
                    adapters.document_writes,
                    taxonomy,
                    tasks,
                    web_url=web_url,
                    max_bytes=settings.max_upload_bytes,
                    upload_dirs=settings.upload_dirs if by_path else (),
                ),
                versions=VersionService(
                    adapters.documents, adapters.document_writes, taxonomy, web_url=web_url
                ),
                version_uploads=VersionUploadService(
                    adapters.documents,
                    adapters.document_writes,
                    tasks,
                    web_url=web_url,
                    max_bytes=settings.max_upload_bytes,
                    upload_dirs=settings.upload_dirs if by_path else (),
                ),
                bulk_documents=BulkDocumentEditor(
                    adapters.documents, adapters.bulk_writes, taxonomy, web_url=web_url
                ),
                bulk_taxonomy=BulkTaxonomyEditor(adapters.taxonomy, adapters.bulk_writes, taxonomy),
            )
            overview = await services.instance.probe(settings.startup_timeout)
            if overview is None:
                logger.warning("Paperless is not reachable yet; tools report it until it is")
            else:
                logger.info(
                    "Connected to Paperless %s (API version %d)",
                    overview.paperless_version,
                    overview.api_version,
                )
            await _hide_unavailable(running, capability_rule, catalogue, overview, enabled)
            yield {SERVICES: services}

    server = FastMCP(
        name="paperless-mcp",
        instructions=INSTRUCTIONS,
        version=__version__,
        providers=[*catalogue.providers, *variants],
        middleware=[CallLogging()],
        lifespan=start,
        # Only ToolError messages reach the client; any other exception becomes a generic error.
        mask_error_details=True,
    )
    _ = server.enable(tags=set(enabled), only=True)
    if hidden:
        _ = server.disable(names=set(hidden), components={"tool"})
    server.add_transform(capability_rule)
    return server


async def _hide_unavailable(
    server: FastMCP,
    rule: Visibility,
    catalogue: Catalogue,
    overview: InstanceOverview | None,
    enabled: frozenset[str],
) -> None:
    """Hide toolsets whose capability Paperless lacks; fail if nothing is left to offer."""
    ai_enabled = overview.ai_enabled if overview else None
    unavailable = unavailable_toolsets(ai_enabled=ai_enabled, requires=catalogue.requires)
    rule.tags = set(unavailable)
    if unavailable:
        for toolset in sorted(unavailable & enabled):
            logger.warning("Toolset %s is hidden: Paperless does not offer what it needs", toolset)
    if not await server.list_tools():
        raise InvalidConfiguration(NO_TOOLS)
