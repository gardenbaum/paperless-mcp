"""Fixtures of the adapter tests: a PaperlessClient on the schema-guarded MockTransport."""

from typing import TYPE_CHECKING, Protocol

import httpx2
import pytest

from adapter.schema_guard import Handler, SchemaGuardedTransport
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import PaperlessClient, RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

BASE_URL = "http://paperless.test"
TOKEN = "0123456789abcdef-secret-token"


class ClientFactory(Protocol):
    """Builds a PaperlessClient that sends to `handler` through the schema guard."""

    def __call__(
        self, handler: Handler, *, retry: RetryPolicy | None = None, token: str = TOKEN
    ) -> PaperlessClient: ...


class RecordingSleep:
    """Stands in for asyncio.sleep: records the delays instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def tag(tag_id: int, name: str) -> dict[str, object]:
    """A Tag as Paperless 3.4.2 serialises it."""
    return {
        "id": tag_id,
        "slug": name,
        "name": name,
        "color": "#a6cee3",
        "text_color": "#000000",
        "match": "",
        "matching_algorithm": 1,
        "is_insensitive": True,
        "is_inbox_tag": False,
        "document_count": 0,
        "owner": None,
        "user_can_change": True,
        "parent": None,
        "children": [],
    }


@pytest.fixture
def sleep() -> RecordingSleep:
    return RecordingSleep()


@pytest.fixture
async def make_client(sleep: RecordingSleep) -> AsyncIterator[ClientFactory]:
    """Build a PaperlessClient whose requests go to `handler` through the schema guard."""
    clients: list[httpx2.AsyncClient] = []

    def factory(
        handler: Handler, *, retry: RetryPolicy | None = None, token: str = TOKEN
    ) -> PaperlessClient:
        http = httpx2.AsyncClient(base_url=BASE_URL, transport=SchemaGuardedTransport(handler))
        clients.append(http)
        return PaperlessClient(
            http,
            StaticTokenProvider(token),
            retry=retry or RetryPolicy(),
            upload_timeout=300.0,
            sleep=sleep,
        )

    yield factory
    for http in clients:
        await http.aclose()
