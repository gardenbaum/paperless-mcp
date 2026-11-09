"""Logging: JSON lines on stderr, one record per tool call, nothing sensitive at INFO or above."""

import json
import logging
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from fastmcp import Client
from harness import FailsAfterProbe, admin, serve

from paperless_mcp.domain.errors import UpstreamUnavailable
from paperless_mcp.observability import configure_logging, request_id
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import PaperlessClient, RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.anyio

QUERY = "tax-return-of-jane-doe"
TOKEN = "observability-token-9f8e7d"
CONTENT = "OCR text of a private letter"


@pytest.fixture
def fastmcp_records(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog, also for FastMCP's own logger (it does not propagate to the root logger)."""
    fastmcp = logging.getLogger("fastmcp")
    fastmcp.addHandler(caplog.handler)
    yield caplog
    fastmcp.removeHandler(caplog.handler)


def lines(err: str) -> list[dict[str, object]]:
    return [cast("dict[str, object]", json.loads(line)) for line in err.splitlines()]


@pytest.mark.usefixtures("isolated_logging")
def test_records_are_json_lines_on_stderr_with_the_request_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    token = request_id.set("req-1")
    try:
        logging.getLogger("paperless_mcp.test").info("hello %s", "world", extra={"tool": "t"})
    finally:
        request_id.reset(token)
    logging.getLogger("paperless_mcp.test").debug("below the level")

    captured = capsys.readouterr()
    assert captured.out == ""
    [record] = lines(captured.err)
    assert record["message"] == "hello world"
    assert (record["level"], record["logger"], record["tool"]) == (
        "INFO",
        "paperless_mcp.test",
        "t",
    )
    assert record["request_id"] == "req-1"


@pytest.mark.usefixtures("isolated_logging")
def test_exceptions_are_rendered_into_the_record(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    try:
        message = "boom"
        raise ValueError(message)  # noqa: TRY301 -- the exception to log
    except ValueError:
        logging.getLogger("paperless_mcp.test").exception("failed")

    [record] = lines(capsys.readouterr().err)
    assert "ValueError: boom" in str(record["exc_info"])


async def request_with_query() -> None:
    def ok(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={}, request=request)

    async with httpx2.AsyncClient(
        base_url="http://paperless.test", transport=httpx2.MockTransport(ok)
    ) as http:
        client = PaperlessClient(
            http, StaticTokenProvider(TOKEN), retry=RetryPolicy(), upload_timeout=1.0
        )
        _ = await client.request("GET", "/api/documents/", params={"query": QUERY})


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
@pytest.mark.usefixtures("isolated_logging")
async def test_query_values_and_tokens_never_reach_info_records(
    capsys: pytest.CaptureFixture[str], level: str
) -> None:
    configure_logging(level)

    await request_with_query()

    important = [r for r in lines(capsys.readouterr().err) if r["level"] != "DEBUG"]
    assert not [r for r in important if QUERY in str(r) or TOKEN in str(r)]


@pytest.mark.usefixtures("isolated_logging")
async def test_without_the_quieted_http_loggers_the_query_would_be_logged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Proves the test above can see what httpx2 logs."""
    configure_logging("INFO")
    logging.getLogger("httpx2").setLevel(logging.INFO)

    await request_with_query()

    assert QUERY in capsys.readouterr().err


@pytest.mark.usefixtures("isolated_logging")
async def test_each_tool_call_is_logged_with_request_id_tool_duration_and_outcome(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")

    async with Client(serve(admin())) as client:
        _ = await client.call_tool("get_instance_overview")
        _ = await client.call_tool("get_instance_overview")

    calls = [r for r in lines(capsys.readouterr().err) if r["message"] == "tool call"]
    assert [(r["tool"], r["outcome"]) for r in calls] == [
        ("get_instance_overview", "ok"),
        ("get_instance_overview", "ok"),
    ]
    assert all(cast("float", r["duration_ms"]) >= 0 for r in calls)
    assert len({r["request_id"] for r in calls}) == 2


async def test_a_failed_call_logs_the_error_type_but_neither_message_nor_cause(
    fastmcp_records: pytest.LogCaptureFixture,
) -> None:
    fastmcp_records.set_level(logging.INFO)
    error = UpstreamUnavailable(f"Paperless failed for {QUERY}")
    error.__cause__ = ValueError(CONTENT)  # what a pydantic cause can carry

    async with Client(serve(FailsAfterProbe(error))) as client:
        result = await client.call_tool("get_instance_overview", raise_on_error=False)

    assert result.is_error
    [call] = [r for r in fastmcp_records.records if r.getMessage() == "tool call"]
    assert (vars(call)["outcome"], vars(call)["error"]) == ("error", "UpstreamUnavailable")
    logged = fastmcp_records.text + str([vars(r) for r in fastmcp_records.records])
    assert CONTENT not in logged
    assert QUERY not in logged
