"""What every toolset module shares: toolsets, the annotation table, services, the error boundary.

A new tool takes four steps: an entry in ``ANNOTATIONS`` (title and hints), the tool function in
its toolset module with ``tags={TOOLSET}`` and ``annotations=ANNOTATIONS[name]``, ``@boundary``
directly on the function, and the use case it calls on ``Services``. The registry tests
(tests/tools/test_registry.py) fail until all four are in place.
"""

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from paperless_mcp.domain.errors import PaperlessMcpError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from fastmcp import Context

    from paperless_mcp.services.instance import InstanceService

# Every toolset; each tool carries exactly one of them as its only tag.
TOOLSETS: Final = frozenset({"core"})

# The one place tool titles and behaviour hints are set (decision-4), keyed by tool name.
ANNOTATIONS: Final[Mapping[str, ToolAnnotations]] = {
    "get_instance_overview": ToolAnnotations(
        title="Get instance overview",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
}

# Key of the ``Services`` in the server's lifespan context.
SERVICES: Final = "services"


@dataclass(frozen=True, slots=True)
class Services:
    """The use cases tools call, one field per service; built by the composition root."""

    instance: InstanceService


def services(ctx: Context) -> Services:
    """The services of the running server (set up by its lifespan)."""
    return cast("Services", ctx.lifespan_context[SERVICES])


def boundary[**P, R](tool: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """The error boundary of a tool: a domain error becomes a ``ToolError`` the client sees.

    The message and hint are client-safe by contract. The cause is dropped (``from None``):
    FastMCP logs a ``ToolError`` without traceback, so a cause that holds response content is
    never logged. Every other exception passes on to FastMCP, which masks it.
    """

    @functools.wraps(tool)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await tool(*args, **kwargs)
        except PaperlessMcpError as error:
            raise ToolError(error.describe()) from None

    return guarded
