"""Composition root: wires settings, adapter, services and tool providers into the server.

The only module that imports both ``tools`` and ``paperless``. ``create_server`` assembles; the
server's lifespan connects: it owns the one ``httpx2.AsyncClient`` of the process, builds the
services on it and probes Paperless before the first request is served.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from paperless_mcp import __version__
from paperless_mcp.observability import CallLogging
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import RetryPolicy, connect
from paperless_mcp.paperless.documents import PaperlessDocumentReader
from paperless_mcp.paperless.instance import PaperlessInstanceReader
from paperless_mcp.paperless.taxonomy import PaperlessTaxonomyReader
from paperless_mcp.services.documents import DocumentService
from paperless_mcp.services.instance import InstanceService
from paperless_mcp.services.taxonomy import TaxonomyResolver
from paperless_mcp.tools import core
from paperless_mcp.tools._shared import SERVICES, Services

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from paperless_mcp.domain.ports import DocumentReader, InstanceReader, TaxonomyReader
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
class Ports:
    """One implementation of every domain port, one field per port."""

    instance: InstanceReader
    taxonomy: TaxonomyReader
    documents: DocumentReader


def paperless_ports(client: PaperlessClient) -> Ports:
    """The Paperless adapter for every port, all on ``client``."""
    return Ports(
        instance=PaperlessInstanceReader(client),
        taxonomy=PaperlessTaxonomyReader(client),
        documents=PaperlessDocumentReader(client),
    )


def create_server(
    paperless: PaperlessSettings,
    settings: ServerSettings,
    *,
    ports: Callable[[PaperlessClient], Ports] = paperless_ports,
) -> FastMCP:
    """Assemble the MCP server.

    Args:
        paperless: Where Paperless is and the token to use.
        settings: Settings of this server.
        ports: Builds the ports on the connected client; tests pass fakes here.

    Returns:
        The server. Starting it (its lifespan) raises the probe's ``PaperlessMcpError`` when
        Paperless rejects the token or API version 10.
    """
    credentials = StaticTokenProvider(paperless.token.get_secret_value())
    web_url = str(paperless.public_url or paperless.url)

    @lifespan
    async def start(_server: FastMCP) -> AsyncIterator[dict[str, object]]:
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
            yield {SERVICES: services}

    return FastMCP(
        name="paperless-mcp",
        instructions=INSTRUCTIONS,
        version=__version__,
        providers=[core.provider],
        middleware=[CallLogging()],
        lifespan=start,
        # Only ToolError messages reach the client; any other exception becomes a generic error.
        mask_error_details=True,
    )
