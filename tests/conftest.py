"""Fixtures shared by every test level."""

import logging
from typing import TYPE_CHECKING

import pytest
from e2e.helpers import BASE_URL, Stack, client, seed, token_for, wait_ready
from fakes.accounts import BLIND, READER

from paperless_mcp.observability import QUIET_LOGGERS

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def anyio_backend() -> str:
    """Async tests (`pytest.mark.anyio`) run on asyncio, the event loop FastMCP uses."""
    return "asyncio"


@pytest.fixture
def isolated_logging() -> Iterator[None]:
    """Undo what `configure_logging` changes in the process-wide logging setup."""
    root = logging.getLogger()
    quiet = [logging.getLogger(name) for name in QUIET_LOGGERS]
    handlers, level, levels = root.handlers[:], root.level, [q.level for q in quiet]
    yield
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
    for logger, saved in zip(quiet, levels, strict=True):
        logger.setLevel(saved)


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    """The ready, seeded Paperless 3.4.2 stack (E2E tests and the live contract backend)."""
    admin_token = wait_ready()
    with client(admin_token) as admin:
        seeded = seed(admin)
        reader_token = token_for(*READER)
        blind_token = token_for(*BLIND)
        with client(reader_token) as reader:
            yield Stack(
                base_url=BASE_URL,
                admin=admin,
                reader=reader,
                seeded=seeded,
                admin_token=admin_token,
                reader_token=reader_token,
                blind_token=blind_token,
            )
