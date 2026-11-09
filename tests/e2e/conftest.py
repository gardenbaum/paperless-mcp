"""Fixtures of the E2E suite; the session-wide `stack` lives in tests/conftest.py."""

import pytest

from e2e.helpers import Seeded, Stack, seed


@pytest.fixture
def reseed(stack: Stack) -> Seeded:
    """The result of running the seeding a second time against the already seeded stack."""
    return seed(stack.admin)
