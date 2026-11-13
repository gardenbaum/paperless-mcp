"""Page ranges of the pdf toolset: parsed and checked against the page count (PLMCP-23)."""

import pytest

from paperless_mcp.domain.errors import Validation
from paperless_mcp.services.page_ranges import describe, parse_pages


@pytest.mark.parametrize(
    ("spec", "pages"),
    [
        ("3", [3]),
        ("1-3", [1, 2, 3]),
        ("1,3,5", [1, 3, 5]),
        (" 2 - 4 , 7 ", [2, 3, 4, 7]),
        ("4-4", [4]),
        ("5-", [5, 6, 7, 8]),
        ("1-2,8", [1, 2, 8]),
        ("8,1", [8, 1]),
    ],
)
def test_a_spec_lists_its_pages_in_the_order_written(spec: str, pages: list[int]) -> None:
    assert parse_pages(spec, page_count=8) == pages


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("", "No pages given"),
        ("  ", "No pages given"),
        ("0", "Page numbers start at 1"),
        ("0-2", "Page numbers start at 1"),
        ("9", "document has 8 pages"),
        ("2-9", "document has 8 pages"),
        ("3-1", "'3-1' runs backwards"),
        ("a", "'a' is not a page or a range"),
        ("1-b", "'1-b' is not a page or a range"),
        ("1,,2", "'' is not a page or a range"),
        ("-3", "'-3' is not a page or a range"),
        ("1-2-3", "'1-2-3' is not a page or a range"),
        ("1,1", "Page 1 is listed twice"),
        ("1-3,2", "Page 2 is listed twice"),
        ("\u0661", "' is not a page or a range"),
    ],
)
def test_a_wrong_spec_is_refused_with_the_reason(spec: str, message: str) -> None:
    with pytest.raises(Validation, match=message):
        _ = parse_pages(spec, page_count=8)


def test_the_message_of_a_page_past_the_end_names_the_count_and_hints_at_the_range() -> None:
    with pytest.raises(Validation) as caught:
        _ = parse_pages("9", page_count=1)

    assert caught.value.message == "Page 9 does not exist: the document has 1 page."
    assert caught.value.hint == "Use pages from 1 to 1."


@pytest.mark.parametrize(
    ("pages", "text"),
    [
        ([4], "page 4"),
        ([1, 2], "pages 1-2"),
        ([5, 1, 2, 3], "pages 1-3 and 5"),
        ([9, 7, 2, 3, 4, 8], "pages 2-4 and 7-9"),
        ([1, 3, 5], "pages 1, 3 and 5"),
        ([2, 2, 3], "pages 2-3"),
        ([1, 2, 4, 6, 7, 8], "pages 1-2, 4 and 6-8"),
    ],
)
def test_pages_are_described_as_sorted_ranges(pages: list[int], text: str) -> None:
    assert describe(pages) == text


UNREADABLE_HINT = "Write pages like 3, ranges like 2-5 or 8- (to the end), separated by commas."


@pytest.mark.parametrize(
    ("spec", "message", "hint"),
    [
        ("", "No pages given.", "Name the pages, e.g. '1-3,5'."),
        ("0", "Page numbers start at 1.", "Page 1 is the first page."),
        ("0-2", "Page numbers start at 1.", "Page 1 is the first page."),
        ("3-1", "The range '3-1' runs backwards.", "Write the lower page first, e.g. 2-5."),
        (" 3 - 1 ", "The range '3 - 1' runs backwards.", "Write the lower page first, e.g. 2-5."),
        ("a", "'a' is not a page or a range.", UNREADABLE_HINT),
        ("1-b", "'1-b' is not a page or a range.", UNREADABLE_HINT),
        ("1-2-3", "'1-2-3' is not a page or a range.", UNREADABLE_HINT),
        ("-3", "'-3' is not a page or a range.", UNREADABLE_HINT),
        ("1,,2", "'' is not a page or a range.", UNREADABLE_HINT),
        ("9", "Page 9 does not exist: the document has 8 pages.", "Use pages from 1 to 8."),
        ("7-9", "Page 9 does not exist: the document has 8 pages.", "Use pages from 1 to 8."),
        ("1,1", "Page 1 is listed twice.", "List each page once."),
        ("1-3,3", "Page 3 is listed twice.", "List each page once."),
    ],
)
def test_the_refusals_have_the_exact_words_and_a_hint(spec: str, message: str, hint: str) -> None:
    with pytest.raises(Validation) as raised:
        _ = parse_pages(spec, page_count=8)

    assert (raised.value.message, raised.value.hint) == (message, hint)


def test_a_page_count_of_one_is_singular_and_the_last_page_is_allowed() -> None:
    assert parse_pages("1", page_count=1) == [1]
    assert parse_pages("2-", page_count=2) == [2]
    assert parse_pages("8", page_count=8) == [8]
    assert parse_pages("1-8", page_count=8) == list(range(1, 9))
    with pytest.raises(Validation) as raised:
        _ = parse_pages("2", page_count=1)
    assert raised.value.message == "Page 2 does not exist: the document has 1 page."


def test_an_open_range_needs_a_start_and_runs_to_the_last_page() -> None:
    assert parse_pages("3-", page_count=5) == [3, 4, 5]
    assert parse_pages("3- ", page_count=5) == [3, 4, 5]
    with pytest.raises(Validation, match="'-' is not a page or a range"):
        _ = parse_pages("-", page_count=5)
