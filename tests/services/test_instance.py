"""InstanceService: the overview use case and the startup probe."""

import time

import pytest
from harness import FailingReader, HangingReader, admin

from paperless_mcp.domain.errors import (
    AuthenticationFailed,
    NotFound,
    PaperlessMcpError,
    UnsupportedVersion,
    UpstreamTimeout,
    UpstreamUnreachable,
)
from paperless_mcp.services.instance import InstanceService

pytestmark = pytest.mark.anyio


async def test_overview_is_what_the_reader_reports() -> None:
    reader = admin()

    assert await InstanceService(reader).overview() == await reader.get_overview()


async def test_the_probe_returns_the_overview_of_a_reachable_paperless() -> None:
    overview = await InstanceService(admin()).probe(timeout=1.0)

    assert overview is not None
    assert overview.paperless_version == "3.4.2"


@pytest.mark.decision("decision-2")
@pytest.mark.parametrize(
    "error",
    [
        AuthenticationFailed("bad token"),
        UnsupportedVersion("not API 10"),
        NotFound("no statistics: not a Paperless URL"),
        PaperlessMcpError("unexpected response"),
    ],
    ids=type,
)
async def test_the_probe_fails_fast_on_what_a_retry_cannot_fix(error: PaperlessMcpError) -> None:
    with pytest.raises(type(error)) as caught:
        _ = await InstanceService(FailingReader(error)).probe(timeout=1.0)

    assert caught.value is error


@pytest.mark.decision("decision-2")
@pytest.mark.parametrize("error", [UpstreamUnreachable("down"), UpstreamTimeout("slow")], ids=type)
async def test_the_probe_tolerates_an_unavailable_paperless(error: PaperlessMcpError) -> None:
    assert await InstanceService(FailingReader(error)).probe(timeout=1.0) is None


@pytest.mark.decision("decision-2")
async def test_the_probe_gives_up_after_its_timeout() -> None:
    started = time.monotonic()

    assert await InstanceService(HangingReader()).probe(timeout=0.05) is None
    assert time.monotonic() - started < 1.0
