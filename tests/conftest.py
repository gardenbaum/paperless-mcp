"""Fixtures shared by every test level."""

from typing import TYPE_CHECKING

import pytest
from e2e.helpers import BASE_URL, READER, Stack, client, seed, token_for, wait_ready

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def anyio_backend() -> str:
    """Async tests (`pytest.mark.anyio`) run on asyncio, the event loop FastMCP uses."""
    return "asyncio"


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    """The ready, seeded Paperless 3.4.2 stack (E2E tests and the live contract backend)."""
    admin_token = wait_ready()
    with client(admin_token) as admin:
        seeded = seed(admin)
        reader_token = token_for(*READER)
        with client(reader_token) as reader:
            yield Stack(
                base_url=BASE_URL,
                admin=admin,
                reader=reader,
                seeded=seeded,
                admin_token=admin_token,
                reader_token=reader_token,
            )
