"""Session fixtures of the E2E suite; the helpers they use live in helpers.py."""

from typing import TYPE_CHECKING

import pytest

from e2e.helpers import BASE_URL, READER, Seeded, Stack, client, seed, token_for, wait_ready

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    """The ready, seeded Paperless 3.4.2 stack."""
    admin_token = wait_ready()
    with client(admin_token) as admin:
        seeded = seed(admin)
        with client(token_for(*READER)) as reader:
            yield Stack(base_url=BASE_URL, admin=admin, reader=reader, seeded=seeded)


@pytest.fixture
def reseed(stack: Stack) -> Seeded:
    """The result of running the seeding a second time against the already seeded stack."""
    return seed(stack.admin)
