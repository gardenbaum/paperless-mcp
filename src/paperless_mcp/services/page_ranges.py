"""Page ranges of the pdf tools: written like ``1-3,5,8-``, checked against the page count.

Paperless counts pages from 1. A spec lists pages and ranges separated by commas; a range
``a-b`` includes both ends and ``a-`` runs to the last page. Pages come out in the order they
are written, which is the order they take in a new file. A page may appear once per spec.
"""

from typing import TYPE_CHECKING

from paperless_mcp.domain.errors import Validation

if TYPE_CHECKING:
    from collections.abc import Iterable


def _number(text: str) -> int | None:
    """The page number ``text`` spells in ASCII digits, else None."""
    return int(text) if text.isascii() and text.isdigit() else None


def _unreadable(part: str) -> Validation:
    return Validation(
        f"{part!r} is not a page or a range.",
        hint="Write pages like 3, ranges like 2-5 or 8- (to the end), separated by commas.",
    )


def _bounds(part: str, page_count: int) -> tuple[int, int]:
    """First and last page of one comma-separated ``part``."""
    first_text, dash, last_text = part.partition("-")
    first = _number(first_text.strip())
    if first is None:
        raise _unreadable(part)
    if not dash:
        return first, first
    last = page_count if not last_text.strip() else _number(last_text.strip())
    if last is None:
        raise _unreadable(part)
    return first, last


def parse_pages(spec: str, *, page_count: int) -> list[int]:
    """The pages ``spec`` names, in the order written, each checked against ``page_count``.

    Raises:
        Validation: The spec is empty or unreadable, names page 0, a range that runs backwards,
            a page past the end of the document, or a page twice.
    """
    if not spec.strip():
        message = "No pages given."
        raise Validation(message, hint="Name the pages, e.g. '1-3,5'.")
    pages: list[int] = []
    for part in spec.split(","):
        first, last = _bounds(part.strip(), page_count)
        if first < 1:
            message = "Page numbers start at 1."
            raise Validation(message, hint="Page 1 is the first page.")
        if last < first:
            message = f"The range {part.strip()!r} runs backwards."
            raise Validation(message, hint="Write the lower page first, e.g. 2-5.")
        for page in range(first, last + 1):
            if page > page_count:
                plural = "" if page_count == 1 else "s"
                message = f"Page {page} does not exist: the document has {page_count} page{plural}."
                raise Validation(message, hint=f"Use pages from 1 to {page_count}.")
            if page in pages:
                message = f"Page {page} is listed twice."
                raise Validation(message, hint="List each page once.")
            pages.append(page)
    return pages


def describe(pages: Iterable[int]) -> str:
    """The pages as sorted ranges for a message: ``pages 1-3, 5 and 7-9``, or ``page 4``."""
    ordered = sorted(set(pages))
    ranges: list[str] = []
    start = previous = ordered[0]
    for page in (*ordered[1:], None):
        if page is not None and page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        if page is not None:
            start = previous = page
    noun = "page" if len(ordered) == 1 else "pages"
    joined = ranges[0] if len(ranges) == 1 else f"{', '.join(ranges[:-1])} and {ranges[-1]}"
    return f"{noun} {joined}"
