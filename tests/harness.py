"""Building blocks for tests of the assembled server: settings, injected readers, fake endpoints.

Tool tests reach the server only through the composition root's seam (`create_server(...,
ports=...)`) and the in-memory FastMCP client; the readers below are fakes, not mocks.
"""

import asyncio
import contextlib
import http.server
import json
import socket
import threading
import time
from typing import TYPE_CHECKING, ClassVar, override

from fakes.accounts import ADMIN
from fakes.paperless import FakePaperless, seeded

from paperless_mcp.server import CATALOGUE, Catalogue, Ports, create_server
from paperless_mcp.settings import PaperlessSettings, ServerSettings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator, Sequence

    from fastmcp import FastMCP

    from paperless_mcp.domain.errors import PaperlessMcpError
    from paperless_mcp.domain.instance import InstanceOverview
    from paperless_mcp.domain.ports import (
        DocumentReader,
        InstanceReader,
        TaskReader,
        TaxonomyReader,
    )

URL = "http://paperless.test"
TOKEN = "harness-token-0123456789"


def paperless_settings(url: str = URL, token: str = TOKEN) -> PaperlessSettings:
    return PaperlessSettings.model_validate({"url": url, "token": token})


def server_settings(
    startup_timeout: float = 2.0,
    *,
    toolsets: Sequence[str] = ("core",),
    read_only: bool = False,
    disabled_tools: Sequence[str] = (),
) -> ServerSettings:
    return ServerSettings.model_validate(
        {
            "startup_timeout": startup_timeout,
            "toolsets": ",".join(toolsets),
            "read_only": read_only,
            "disabled_tools": ",".join(disabled_tools),
        }
    )


def serve(  # noqa: PLR0913 -- one keyword per seam of the server
    reader: InstanceReader,
    *,
    taxonomy: TaxonomyReader | None = None,
    documents: DocumentReader | None = None,
    tasks: TaskReader | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    startup_timeout: float = 2.0,
    catalogue: Catalogue = CATALOGUE,
    toolsets: Sequence[str] = ("core",),
    read_only: bool = False,
    disabled_tools: Sequence[str] = (),
) -> FastMCP:
    """The assembled server, with `reader` in place of the Paperless adapter.

    `taxonomy`, `documents` and `tasks` default to `reader` if that is a fake, else to the seeded
    admin's fake. `sleep` and `clock` replace the real ones the task wait uses. `toolsets`,
    `read_only` and `disabled_tools` go to the server settings; `catalogue` replaces the
    tools the server offers, to try toolsets that do not exist yet.
    """
    fake = reader if isinstance(reader, FakePaperless) else admin()
    return create_server(
        paperless_settings(),
        server_settings(
            startup_timeout,
            toolsets=toolsets,
            read_only=read_only,
            disabled_tools=disabled_tools,
        ),
        catalogue=catalogue,
        ports=lambda _client: Ports(
            instance=reader,
            taxonomy=taxonomy or fake,
            documents=documents or fake,
            tasks=tasks or fake,
        ),
        sleep=sleep,
        clock=clock,
    )


def admin() -> FakePaperless:
    return FakePaperless(seeded(), user=ADMIN[0])


class FailsAfterProbe:
    """Answers the startup probe like the seeded fake, then fails every call with `error`."""

    def __init__(self, error: Exception) -> None:
        self.error: Exception = error
        self._probed: bool = False

    async def get_overview(self) -> InstanceOverview:
        if not self._probed:
            self._probed = True
            return await admin().get_overview()
        raise self.error


class FailingReader:
    """Every call fails with `error`, the startup probe included."""

    def __init__(self, error: PaperlessMcpError) -> None:
        self.error: PaperlessMcpError = error

    async def get_overview(self) -> InstanceOverview:
        raise self.error


class HangingReader:
    """Paperless never answers."""

    async def get_overview(self) -> InstanceOverview:
        _ = await asyncio.Event().wait()
        raise AssertionError


def refused_url() -> str:
    """A local URL on which nothing listens, so connecting is refused at once."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])  # pyright: ignore[reportAny] -- (host, port) tuple
    return f"http://127.0.0.1:{port}"


class _Answer(http.server.BaseHTTPRequestHandler):
    status: ClassVar[int] = 200

    def do_GET(self) -> None:
        body = json.dumps({"detail": "stub"}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        _ = self.wfile.write(body)

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Keep the stub quiet."""


@contextlib.contextmanager
def paperless_answering(status: int) -> Generator[str]:
    """A local HTTP endpoint that answers every GET with `status`; yields its URL."""
    handler = type("Answer", (_Answer,), {"status": status})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
