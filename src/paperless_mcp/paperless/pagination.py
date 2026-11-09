"""Paperless list pages: page numbers instead of links, and capped collection of all pages."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

if TYPE_CHECKING:
    from paperless_mcp.paperless.client import PaperlessClient, Params

MAX_PAGE_SIZE = 100
# Hard cap of aggregated fetches (design §7); callers may ask for less.
MAX_ITEMS = 2000


def page_number(link: str | None) -> int | None:
    """The page a ``next``/``previous`` link points to, so no host or URL leaks out.

    Paperless omits the ``page`` parameter in the link to the first page.

    Raises:
        ValueError: The ``page`` parameter is not a number.
    """
    if link is None:
        return None
    pages = parse_qs(urlsplit(link).query).get("page")
    return int(pages[0]) if pages else 1


type PageNumber = Annotated[int | None, BeforeValidator(page_number)]


class Page[T](BaseModel):
    """One page of a Paperless list response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    next_page: PageNumber = Field(alias="next")
    previous_page: PageNumber = Field(alias="previous")
    results: list[T]


@dataclass(frozen=True, slots=True)
class Collected[T]:
    """Items gathered over several pages.

    Attributes:
        items: At most ``cap`` items, in Paperless order.
        count: Total number of matching items Paperless reported.
        truncated: True if more items exist than were returned.
    """

    items: list[T]
    count: int
    truncated: bool


async def collect[T](
    client: PaperlessClient,
    path: str,
    page_model: type[Page[T]],
    *,
    params: Params | None = None,
    cap: int = MAX_ITEMS,
) -> Collected[T]:
    """Fetch the pages of a list endpoint until ``cap`` items or the last page."""
    size = min(cap, MAX_PAGE_SIZE)
    items: list[T] = []
    count = 0
    number: int | None = 1
    while number is not None and len(items) < cap:
        page = await client.get(
            path, page_model, params={**(params or {}), "page": number, "page_size": size}
        )
        items.extend(page.results)
        count = page.count
        number = page.next_page if page.results else None
    kept = items[:cap]
    return Collected(items=kept, count=count, truncated=len(items) > cap or count > len(kept))
