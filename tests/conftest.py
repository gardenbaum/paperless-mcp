"""Fixtures shared by every test level."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Async tests (`pytest.mark.anyio`) run on asyncio, the event loop FastMCP uses."""
    return "asyncio"
