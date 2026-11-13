"""A path is read only when the request came over stdio: the second lock of decision-7.

The server never registers a path variant over HTTP; these tests start the variants anyway and
make the request look like it came over HTTP, so the check inside the tools is what refuses.
"""

import base64
from typing import TYPE_CHECKING, Literal

import pytest
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client
from fastmcp.server.context import (
    _current_transport,  # pyright: ignore[reportPrivateUsage] -- the seam FastMCP itself sets
)
from harness import serve

from paperless_mcp.domain.errors import Validation
from paperless_mcp.tools._shared import check_transport

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-7")]


@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
def test_a_path_is_refused_over_an_http_transport(transport: str) -> None:
    with pytest.raises(Validation, match="stdio"):
        check_transport(transport)


@pytest.mark.parametrize("transport", ["stdio", None])
def test_a_path_is_allowed_over_stdio_or_without_a_transport(transport: str | None) -> None:
    check_transport(transport)  # None: the server runs in-process, as the tests do


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("upload_document", {}), ("upload_document_version", {"document_id": 1})],
)
@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
async def test_both_path_tools_refuse_a_path_when_the_request_came_over_http(
    tmp_path: Path,
    tool: str,
    arguments: dict[str, object],
    transport: Literal["sse", "streamable-http"],
) -> None:
    _ = (tmp_path / "scan.txt").write_bytes(b"text")
    fake = FakePaperless(seeded(), user="admin", allow_writes=True)
    server = serve(fake, toolsets=["all"], stdio=True, upload_dirs=[str(tmp_path)])
    token = _current_transport.set(transport)
    try:
        async with Client(server) as client:
            refused = await client.call_tool(
                tool, {**arguments, "path": str(tmp_path / "scan.txt")}, raise_on_error=False
            )
            content = base64.b64encode(b"text").decode()
            allowed = await client.call_tool(
                tool,
                {**arguments, "content_base64": content, "filename": "a.txt"},
                raise_on_error=False,
            )
    finally:
        _current_transport.reset(token)

    assert refused.is_error
    assert "stdio" in str(refused.content)
    assert not allowed.is_error  # content still works: only a path needs stdio
    assert len(fake.state.tasks) == 1
