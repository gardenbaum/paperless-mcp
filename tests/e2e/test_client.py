"""Smoke tests of the Paperless client against the real 3.4.2 stack."""

from typing import TYPE_CHECKING

import httpx2
import pytest
from pydantic import BaseModel

from paperless_mcp.domain.errors import AuthenticationFailed
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import PaperlessClient, RetryPolicy
from paperless_mcp.paperless.pagination import Page, collect

if TYPE_CHECKING:
    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]


class TagName(BaseModel):
    id: int
    name: str


def paperless(http: httpx2.AsyncClient, token: str) -> PaperlessClient:
    return PaperlessClient(
        http, StaticTokenProvider(token), retry=RetryPolicy(), upload_timeout=60.0
    )


async def test_pages_and_collection_of_real_tags(stack: Stack) -> None:
    async with httpx2.AsyncClient(base_url=stack.base_url, timeout=30.0) as http:
        client = paperless(http, stack.admin_token)
        page = await client.get("/api/tags/", Page[TagName], params={"page": 2, "page_size": 1})
        collected = await collect(client, "/api/tags/", Page[TagName], cap=1000)

    assert (page.previous_page, page.next_page) == (1, 3)
    names = {tag.name for tag in collected.items}
    assert names >= {"e2e-finance", "e2e-invoices", "e2e-private"}
    assert (collected.count, collected.truncated) == (len(collected.items), False)


async def test_a_bad_token_is_an_authentication_error(stack: Stack) -> None:
    async with httpx2.AsyncClient(base_url=stack.base_url, timeout=30.0) as http:
        with pytest.raises(AuthenticationFailed):
            _ = await paperless(http, "not-a-valid-token").request("GET", "/api/tags/")
