"""The composition root: the startup probe through the real adapter, and the server lifespan."""

import time

import pytest
from fastmcp import Client
from harness import (
    HangingReader,
    paperless_answering,
    paperless_settings,
    refused_url,
    serve,
    server_settings,
)

from paperless_mcp.domain.errors import (
    AuthenticationFailed,
    PaperlessMcpError,
    UnsupportedVersion,
)
from paperless_mcp.server import create_server

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-2")]


@pytest.mark.parametrize(
    ("status", "error"), [(401, AuthenticationFailed), (406, UnsupportedVersion)]
)
async def test_the_server_does_not_start_when_paperless_rejects_the_token_or_api_version(
    status: int, error: type[PaperlessMcpError]
) -> None:
    with paperless_answering(status) as url:
        server = create_server(paperless_settings(url), server_settings())

        with pytest.raises(RuntimeError) as caught:
            async with Client(server):
                pass

    assert isinstance(caught.value.__cause__, error)


async def test_an_unreachable_paperless_does_not_stop_the_start() -> None:
    server = create_server(paperless_settings(refused_url()), server_settings())

    async with Client(server) as client:
        result = await client.call_tool("get_instance_overview", raise_on_error=False)

    assert result.is_error
    assert "Paperless is unreachable." in str(result.content)


async def test_a_paperless_that_does_not_answer_delays_the_start_only_up_to_the_timeout() -> None:
    started = time.monotonic()

    async with Client(serve(HangingReader(), startup_timeout=0.1)):
        pass

    assert time.monotonic() - started < 2.0
