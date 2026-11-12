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

from paperless_mcp import __version__
from paperless_mcp.domain.errors import InvalidConfiguration
from paperless_mcp.observability import CallLogging
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import RetryPolicy, connect
from paperless_mcp.paperless.documents import PaperlessDocumentReader
from paperless_mcp.paperless.instance import PaperlessInstanceReader
from paperless_mcp.paperless.tasks import PaperlessTaskReader
from paperless_mcp.paperless.taxonomy import PaperlessTaxonomyReader
from paperless_mcp.services.documents import DocumentService
from paperless_mcp.services.instance import InstanceService
from paperless_mcp.services.tasks import TaskService
from paperless_mcp.services.taxonomy import TaxonomyResolver
from paperless_mcp.tools import core
from paperless_mcp.tools._shared import (
    ANNOTATIONS,
    SERVICES,
    TOOLSET_REQUIRES,
    TOOLSETS,
    Services,
)
from paperless_mcp.tools.selection import hidden_tools, selected_toolsets, unavailable_toolsets

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

    from fastmcp.server.providers import Provider
    from mcp.types import ToolAnnotations

    from paperless_mcp.domain.instance import InstanceOverview
    from paperless_mcp.domain.ports import (
        DocumentReader,
        InstanceReader,
        TaskReader,
        TaxonomyReader,
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

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Catalogue:
    """Every tool the server can offer and what selection needs to know about them.

    Attributes:
        providers: One FastMCP provider per toolset module.
        annotations: The central annotation table, keyed by tool name.
        toolsets: Every toolset name.
        requires: Toolsets that need a Paperless capability, mapped to it.
    """

    providers: Sequence[Provider]
    annotations: Mapping[str, ToolAnnotations]
    toolsets: frozenset[str]
    requires: Mapping[str, str]


CATALOGUE: Final = Catalogue(
    providers=(core.provider,),
    annotations=ANNOTATIONS,
    toolsets=TOOLSETS,
    requires=TOOLSET_REQUIRES,
)


@dataclass(frozen=True, slots=True)
class Ports:
    """One implementation of every domain port, one field per port."""

    instance: InstanceReader
    taxonomy: TaxonomyReader
    documents: DocumentReader
    tasks: TaskReader


def paperless_ports(client: PaperlessClient) -> Ports:
    """The Paperless adapter for every port, all on ``client``."""
    return Ports(
        instance=PaperlessInstanceReader(client),
        taxonomy=PaperlessTaxonomyReader(client),
        documents=PaperlessDocumentReader(client),
        tasks=PaperlessTaskReader(client),
    )


def create_server(  # noqa: PLR0913 -- one keyword per seam, all defaulted
    paperless: PaperlessSettings,
    settings: ServerSettings,
    *,
    catalogue: Catalogue = CATALOGUE,
    ports: Callable[[PaperlessClient], Ports] = paperless_ports,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> FastMCP:
    """Assemble the MCP server.

    Args:
        paperless: Where Paperless is and the token to use.
        settings: Settings of this server, including which toolsets and tools to offer.
        catalogue: The tools the server can offer; tests pass a larger one.
        ports: Builds the ports on the connected client; tests pass fakes here.
        sleep: Waits between task polls; tests pass one that does not wait.
        clock: Measures the task wait; tests pass the clock their ``sleep`` advances.

    Returns:
        The server. Starting it (its lifespan) raises the probe's ``PaperlessMcpError`` when
        Paperless rejects the token or API version 10, and ``InvalidConfiguration`` when the
        selection leaves no tool.

    Raises:
        InvalidConfiguration: A toolset or a disabled tool in the settings does not exist.
    """
    credentials = StaticTokenProvider(paperless.token.get_secret_value())
    web_url = str(paperless.public_url or paperless.url)

    # Allowlist the selected toolsets (resources carry the tags too), then take out tools:
    # later visibility rules override earlier ones.
    enabled = selected_toolsets(settings.toolsets, catalogue.toolsets)
    hidden = hidden_tools(
        catalogue.annotations, read_only=settings.read_only, disabled=settings.disabled_tools
    )

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
            services = Services(
                instance=InstanceService(adapters.instance),
                taxonomy=taxonomy,
                documents=DocumentService(adapters.documents, taxonomy, web_url=web_url),
                tasks=TaskService(adapters.tasks, sleep=sleep, clock=clock),
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
            await _hide_unavailable(running, catalogue, overview, enabled)
            yield {SERVICES: services}

    server = FastMCP(
        name="paperless-mcp",
        instructions=INSTRUCTIONS,
        version=__version__,
        providers=list(catalogue.providers),
        middleware=[CallLogging()],
        lifespan=start,
        # Only ToolError messages reach the client; any other exception becomes a generic error.
        mask_error_details=True,
    )
    _ = server.enable(tags=set(enabled), only=True)
    if hidden:
        _ = server.disable(names=set(hidden), components={"tool"})
    return server


async def _hide_unavailable(
    server: FastMCP,
    catalogue: Catalogue,
    overview: InstanceOverview | None,
    enabled: frozenset[str],
) -> None:
    """Hide toolsets whose capability Paperless lacks; fail if nothing is left to offer."""
    ai_enabled = overview.ai_enabled if overview else None
    unavailable = unavailable_toolsets(ai_enabled=ai_enabled, requires=catalogue.requires)
    if unavailable:
        _ = server.disable(tags=set(unavailable))
        for toolset in sorted(unavailable & enabled):
            logger.warning("Toolset %s is hidden: Paperless does not offer what it needs", toolset)
    if not await server.list_tools():
        message = (
            "No tools are left to offer: PAPERLESS_MCP_TOOLSETS, PAPERLESS_MCP_READ_ONLY,"
            " PAPERLESS_MCP_DISABLED_TOOLS and the capabilities of this Paperless leave no tool."
        )
        raise InvalidConfiguration(message)
