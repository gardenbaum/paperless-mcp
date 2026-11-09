"""The backends the contract suite runs against: FakePaperless and the adapter on live Paperless.

`just check` runs the `fake` backend; `just e2e` runs `live` (marker `e2e`) against the seeded
3.4.2 stack. Both hold the same seed (tests/e2e/helpers.py `seed` and its `SEEDED_OBJECTS`
manifest, mirrored by `fakes.paperless.seeded`), so every contract test makes the same
assertions against both.

Isolation on the live stack, which persists between runs and is shared by every E2E test:
- Never change or delete a seed object, and never assert exact instance-wide counts; rely on the
  seed manifest as lower bounds, or on objects the test created itself.
- A test that writes creates objects with a name unique to the test run (e.g. with a uuid
  suffix) and deletes them again in a fixture finaliser, also when the test fails.

Adding a port: add a field for it to `Ports` and wire it in `fake_ports` and `live_ports`,
implement it in FakePaperless and the adapter, and write its contract tests with
`@pytest.mark.contract("Port.method")` and the `backend` fixture. test_coverage.py fails until
every port is wired and every method has such a test.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, seeded

from paperless_mcp.domain.ports import InstanceReader  # noqa: TC001 -- Ports' annotations are introspected
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import PaperlessClient, RetryPolicy
from paperless_mcp.paperless.instance import PaperlessInstanceReader

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from e2e.helpers import Stack


@dataclass(frozen=True)
class Ports:
    """One credential's implementation of every port, one field per port."""

    instance: InstanceReader


def fake_ports(fake: FakePaperless) -> Ports:
    """The fake implements every port itself."""
    return Ports(instance=fake)


def live_ports(client: PaperlessClient) -> Ports:
    """The adapter, one object per port, all on `client`."""
    return Ports(instance=PaperlessInstanceReader(client))


@dataclass(frozen=True)
class Backend:
    """One Paperless, seen through the seeded superuser and the seeded restricted user."""

    admin: Ports
    reader: Ports


def fake_backend() -> Backend:
    state = seeded()
    return Backend(
        admin=fake_ports(FakePaperless(state, user=ADMIN[0])),
        reader=fake_ports(FakePaperless(state, user=READER[0])),
    )


@pytest.fixture(params=["fake", pytest.param("live", marks=pytest.mark.e2e)])
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    """The backend under contract (the fixture value is created fresh for every test)."""
    if cast("str", request.param) == "fake":
        yield fake_backend()
        return
    stack = cast("Stack", request.getfixturevalue("stack"))
    async with httpx2.AsyncClient(base_url=stack.base_url, timeout=30.0) as http:

        def ports(token: str) -> Ports:
            credentials = StaticTokenProvider(token)
            return live_ports(
                PaperlessClient(http, credentials, retry=RetryPolicy(), upload_timeout=60.0)
            )

        yield Backend(admin=ports(stack.admin_token), reader=ports(stack.reader_token))
