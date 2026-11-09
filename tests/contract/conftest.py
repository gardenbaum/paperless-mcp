"""The backends the contract suite runs against: FakePaperless and the adapter on live Paperless.

`just check` runs the `fake` backend; `just e2e` runs `live` (marker `e2e`) against the seeded
3.4.2 stack. Both are seeded with the same scenario (tests/e2e/helpers.py `seed`, mirrored by
`fakes.paperless.seeded`), so every contract test makes the same assertions against both.

Adding a port: add it to `Paperless` below, implement it in FakePaperless and in the adapter
(and in `live` if it is a new adapter class), and write its contract tests with
`@pytest.mark.contract("Port.method")`; test_coverage.py fails until every method has one.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import httpx2
import pytest
from e2e.helpers import ADMIN, READER
from fakes.paperless import FakePaperless, seeded

from paperless_mcp.domain.ports import InstanceReader
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import PaperlessClient, RetryPolicy
from paperless_mcp.paperless.instance import PaperlessInstanceReader

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from e2e.helpers import Stack


class Paperless(InstanceReader, Protocol):
    """Every port of the domain: what a backend under contract implements."""


class Adapter(PaperlessInstanceReader):
    """The adapter classes combined into one `Paperless`, as the contract suite needs it."""


@dataclass(frozen=True)
class Backend:
    """One Paperless, seen through the seeded superuser and the seeded restricted user."""

    admin: Paperless
    reader: Paperless


def fake_backend() -> Backend:
    state = seeded()
    return Backend(
        admin=FakePaperless(state, user=ADMIN[0]), reader=FakePaperless(state, user=READER[0])
    )


@pytest.fixture(params=["fake", pytest.param("live", marks=pytest.mark.e2e)])
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    """The backend under contract (the fixture value is created fresh for every test)."""
    if cast("str", request.param) == "fake":
        yield fake_backend()
        return
    stack = cast("Stack", request.getfixturevalue("stack"))
    async with httpx2.AsyncClient(base_url=stack.base_url, timeout=30.0) as http:

        def adapter(token: str) -> Adapter:
            credentials = StaticTokenProvider(token)
            return Adapter(
                PaperlessClient(http, credentials, retry=RetryPolicy(), upload_timeout=60.0)
            )

        yield Backend(admin=adapter(stack.admin_token), reader=adapter(stack.reader_token))
