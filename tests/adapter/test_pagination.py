"""Pagination: page numbers instead of links, and collecting pages up to a hard item cap."""

from typing import cast

import httpx2
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

from adapter.conftest import BASE_URL, ClientFactory, tag
from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.paperless.pagination import MAX_ITEMS, Page, collect, page_number

pytestmark = pytest.mark.anyio


class TagStub(BaseModel):
    id: int
    name: str


def link(**params: str) -> str:
    return str(httpx2.URL(f"{BASE_URL}/api/tags/", params=params))


class TagServer:
    """Serves `total` tags like Paperless: page and page_size parameters, absolute links."""

    def __init__(self, total: int) -> None:
        self.total: int = total
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        page = int(cast("str", request.url.params.get("page", "1")))
        size = int(cast("str", request.url.params.get("page_size", "25")))
        start = (page - 1) * size
        others = {k: v for k, v in request.url.params.items() if k not in {"page", "page_size"}}
        more = start + size < self.total
        body = {
            "count": self.total,
            "next": link(**others, page=str(page + 1), page_size=str(size)) if more else None,
            "previous": link(**others, page_size=str(size))
            if page == 2
            else (link(**others, page=str(page - 1), page_size=str(size)) if page > 2 else None),
            "results": [
                tag(n, f"tag-{n}") for n in range(start + 1, min(start + size, self.total) + 1)
            ],
        }
        return httpx2.Response(200, json=body)


@given(
    page=st.integers(min_value=1),
    other=st.dictionaries(st.sampled_from(["ordering", "name__icontains", "page_size"]), st.text()),
)
def test_page_number_reads_the_page_parameter_of_any_link(page: int, other: dict[str, str]) -> None:
    assert page_number(link(**other, page=str(page))) == page


@given(
    other=st.dictionaries(st.sampled_from(["ordering", "name__icontains", "page_size"]), st.text())
)
def test_a_link_without_page_parameter_is_the_first_page(other: dict[str, str]) -> None:
    assert page_number(link(**other)) == 1


def test_no_link_is_no_page() -> None:
    assert page_number(None) is None


async def test_a_page_exposes_page_numbers_not_links(make_client: ClientFactory) -> None:
    client = make_client(TagServer(total=60))

    page = await client.get("/api/tags/", Page[TagStub], params={"page": 2, "page_size": 25})

    assert (page.count, page.previous_page, page.next_page) == (60, 1, 3)
    assert [item.id for item in page.results] == list(range(26, 51))
    assert "http" not in page.model_dump_json()


async def test_collect_gathers_every_page_below_the_cap(make_client: ClientFactory) -> None:
    server = TagServer(total=230)

    collected = await collect(make_client(server), "/api/tags/", Page[TagStub], cap=1000)

    assert [item.id for item in collected.items] == list(range(1, 231))
    assert (collected.count, collected.truncated) == (230, False)
    assert [r.url.params["page"] for r in server.requests] == ["1", "2", "3"]
    assert {r.url.params["page_size"] for r in server.requests} == {"100"}


async def test_collect_stops_at_the_cap_and_reports_truncated(make_client: ClientFactory) -> None:
    server = TagServer(total=230)

    collected = await collect(
        make_client(server), "/api/tags/", Page[TagStub], params={"ordering": "name"}, cap=150
    )

    assert len(collected.items) == 150
    assert (collected.count, collected.truncated) == (230, True)
    assert len(server.requests) == 2
    assert {r.url.params["ordering"] for r in server.requests} == {"name"}


async def test_collect_exactly_at_the_cap_is_not_truncated(make_client: ClientFactory) -> None:
    collected = await collect(make_client(TagServer(total=40)), "/api/tags/", Page[TagStub], cap=40)

    assert (len(collected.items), collected.truncated) == (40, False)


async def test_collect_small_cap_asks_for_small_pages(make_client: ClientFactory) -> None:
    server = TagServer(total=40)

    collected = await collect(make_client(server), "/api/tags/", Page[TagStub], cap=10)

    assert (len(collected.items), collected.truncated) == (10, True)
    assert server.requests[0].url.params["page_size"] == "10"


async def test_collect_clamps_the_cap_to_the_hard_maximum(make_client: ClientFactory) -> None:
    collected = await collect(
        make_client(TagServer(total=2500)), "/api/tags/", Page[TagStub], cap=10_000
    )

    assert (len(collected.items), collected.truncated) == (MAX_ITEMS, True)


@pytest.mark.parametrize("cap", [0, -1])
async def test_collect_rejects_a_cap_below_one(make_client: ClientFactory, cap: int) -> None:
    with pytest.raises(ValueError, match="cap must be at least 1"):
        _ = await collect(make_client(TagServer(total=5)), "/api/tags/", Page[TagStub], cap=cap)


async def test_collect_stops_on_an_empty_page(make_client: ClientFactory) -> None:
    def endless(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json={"count": 5, "next": link(page="2"), "previous": None, "results": []}
        )

    collected = await collect(make_client(endless), "/api/tags/", Page[TagStub], cap=10)

    assert (collected.items, collected.truncated) == ([], True)


async def test_a_malformed_link_is_an_unexpected_response(make_client: ClientFactory) -> None:
    def broken(_: httpx2.Request) -> httpx2.Response:
        body = {"count": 1, "next": link(page="last"), "previous": None, "results": [tag(1, "a")]}
        return httpx2.Response(200, json=body)

    with pytest.raises(PaperlessMcpError, match="unexpected response"):
        _ = await make_client(broken).get("/api/tags/", Page[TagStub])


# --- Mutation-testing additions (PLMCP-21)


async def test_collect_accepts_a_cap_of_one_and_reads_one_page_of_one(
    make_client: ClientFactory,
) -> None:
    server = TagServer(total=5)

    collected = await collect(make_client(server), "/api/tags/", Page[TagStub], cap=1)

    assert ([item.id for item in collected.items], collected.truncated) == ([1], True)
    assert [(r.url.params["page"], r.url.params["page_size"]) for r in server.requests] == [
        ("1", "1")
    ]


async def test_collect_does_not_read_another_page_once_the_cap_is_reached(
    make_client: ClientFactory,
) -> None:
    server = TagServer(total=40)

    _ = await collect(make_client(server), "/api/tags/", Page[TagStub], cap=10)

    assert len(server.requests) == 1
