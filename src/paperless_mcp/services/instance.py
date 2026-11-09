"""Instance-wide use cases: the overview tool and the startup probe."""

import asyncio
from typing import TYPE_CHECKING

from paperless_mcp.domain.errors import UpstreamUnavailable

if TYPE_CHECKING:
    from paperless_mcp.domain.instance import InstanceOverview
    from paperless_mcp.domain.ports import InstanceReader


class InstanceService:
    """What the instance is and what the current user can do there."""

    def __init__(self, reader: InstanceReader) -> None:
        """Read the instance through ``reader``."""
        self._reader: InstanceReader = reader

    async def overview(self) -> InstanceOverview:
        """Versions, statistics, system status, current user and capabilities."""
        return await self._reader.get_overview()

    async def probe(self, timeout: float) -> InstanceOverview | None:  # noqa: ASYNC109 -- the bound is the probe's contract
        """Check at startup that Paperless accepts the credential and API version 10.

        A Paperless that is down or slow may come back, so the server starts anyway and the
        tools report it. Every other error stays the same until the configuration changes.

        Returns:
            The overview, or None if Paperless did not answer within ``timeout`` seconds or is
            unavailable.

        Raises:
            PaperlessMcpError: Paperless rejected the credential or the API version, or does not
                answer like Paperless (anything but ``UpstreamUnavailable``).
        """
        try:
            async with asyncio.timeout(timeout):
                return await self._reader.get_overview()
        except TimeoutError, UpstreamUnavailable:
            return None
