"""translate: the filter rules of a saved view become a document query, as the web UI does it.

`TRANSLATED` lists every rule type of Paperless 3.4.2 with the query it becomes (acceptance
criterion: each type is translated or explicitly rejected, naming the type); a test keeps the list
complete against the rule type table, which a domain test keeps equal to the vendored schema.
"""

from datetime import date
from typing import TYPE_CHECKING

import pytest

from paperless_mcp.domain.documents import DocumentQuery
from paperless_mcp.domain.errors import Validation
from paperless_mcp.domain.views import RULE_TYPES, FilterRule
from paperless_mcp.services.filter_rules import EDIT_HINT, translate

if TYPE_CHECKING:
    from collections.abc import Sequence

D = date(2026, 1, 2)
# rule type -> (value, the query the single rule becomes)
TRANSLATED: dict[int, tuple[str | None, DocumentQuery]] = {
    0: ("x", DocumentQuery(title_search="x")),
    1: ("x", DocumentQuery(content_contains="x")),
    2: ("7", DocumentQuery(archive_serial_number=7)),
    3: ("4", DocumentQuery(correspondent=4)),
    4: ("4", DocumentQuery(document_type=4)),
    5: ("true", DocumentQuery(inbox=True)),
    6: ("3", DocumentQuery(tags_all=(3,))),
    7: ("true", DocumentQuery(tagged=True)),
    8: ("2026-01-02", DocumentQuery(created_lt=D)),
    9: ("2026-01-02", DocumentQuery(created_gt=D)),
    10: ("2026", DocumentQuery(created_year=2026)),
    11: ("11", DocumentQuery(created_month=11)),
    12: ("5", DocumentQuery(created_day=5)),
    13: ("2026-01-02", DocumentQuery(added_lt=D)),
    14: ("2026-01-02", DocumentQuery(added_gt=D)),
    15: ("2026-01-02", DocumentQuery(modified_lt=D)),
    16: ("2026-01-02", DocumentQuery(modified_gt=D)),
    17: ("3", DocumentQuery(tags_none=(3,))),
    18: ("true", DocumentQuery(asn_isnull=True)),
    19: ("x", DocumentQuery(text="x")),
    20: ("x AND y", DocumentQuery(query="x AND y")),
    21: ("5", DocumentQuery(more_like=5)),
    22: ("3", DocumentQuery(tags_any=(3,))),
    23: ("7", DocumentQuery(asn_gt=7)),
    24: ("7", DocumentQuery(asn_lt=7)),
    25: ("4", DocumentQuery(storage_path=4)),
    26: ("4", DocumentQuery(correspondent_in=(4,))),
    27: ("4", DocumentQuery(correspondent_none=(4,))),
    28: ("4", DocumentQuery(document_type_in=(4,))),
    29: ("4", DocumentQuery(document_type_none=(4,))),
    30: ("4", DocumentQuery(storage_path_in=(4,))),
    31: ("4", DocumentQuery(storage_path_none=(4,))),
    32: ("2", DocumentQuery(owner=2)),
    33: ("2", DocumentQuery(owner_in=(2,))),
    34: ("true", DocumentQuery(owner_isnull=True)),
    35: ("2", DocumentQuery(owner_none=(2,))),
    36: ("x", DocumentQuery(custom_fields_icontains="x")),
    37: ("2", DocumentQuery(shared_by=2)),
    38: ("4", DocumentQuery(custom_fields_all=(4,))),
    39: ("4", DocumentQuery(custom_fields_in=(4,))),
    40: ("4", DocumentQuery(custom_fields_none=(4,))),
    41: ("false", DocumentQuery(has_custom_fields=False)),
    42: ('["due", "exists", true]', DocumentQuery(custom_field_query=["due", "exists", True])),
    43: ("2026-01-02", DocumentQuery(created_to=D)),
    44: ("2026-01-02", DocumentQuery(created_from=D)),
    45: ("2026-01-02", DocumentQuery(added_to=D)),
    46: ("2026-01-02", DocumentQuery(added_from=D)),
    47: ("application/pdf", DocumentQuery(mime_type="application/pdf")),
    48: ("x", DocumentQuery(title_search="x")),
    49: ("x", DocumentQuery(text="x")),
    50: ("true", DocumentQuery(has_duplicates=True)),
}


def rules(*pairs: tuple[int, str | None]) -> list[FilterRule]:
    return [FilterRule(rule_type=number, value=value) for number, value in pairs]


def test_every_rule_type_of_paperless_3_4_2_is_listed_as_translated() -> None:
    assert sorted(TRANSLATED) == sorted(RULE_TYPES)


@pytest.mark.parametrize("rule_type", sorted(TRANSLATED))
def test_each_rule_type_becomes_its_document_filter(rule_type: int) -> None:
    value, expected = TRANSLATED[rule_type]

    assert translate(rules((rule_type, value)), None, sort_reverse=False) == expected


def test_a_view_without_rules_matches_every_document() -> None:
    assert translate([], None, sort_reverse=False) == DocumentQuery()


def test_a_rule_with_several_values_collects_them_in_order() -> None:
    found = translate(
        rules((6, "3"), (6, "1"), (17, "9"), (22, "5"), (22, "4")), None, sort_reverse=False
    )

    assert (found.tags_all, found.tags_none, found.tags_any) == ((3, 1), (9,), (5, 4))


def test_rules_of_different_types_are_anded_in_one_query() -> None:
    found = translate(rules((3, "4"), (5, "true"), (1, "acme")), None, sort_reverse=False)

    assert found == DocumentQuery(correspondent=4, inbox=True, content_contains="acme")


@pytest.mark.parametrize(
    ("rule_type", "value", "expected"),
    [
        (3, None, DocumentQuery(correspondent_isnull=True)),
        (3, "-1", DocumentQuery(correspondent_isnull=False)),
        (4, None, DocumentQuery(document_type_isnull=True)),
        (4, "-1", DocumentQuery(document_type_isnull=False)),
        (25, None, DocumentQuery(storage_path_isnull=True)),
        (25, "-1", DocumentQuery(storage_path_isnull=False)),
    ],
)
def test_no_value_means_not_assigned_and_minus_one_means_assigned(
    rule_type: int, value: str | None, expected: DocumentQuery
) -> None:
    assert translate(rules((rule_type, value)), None, sort_reverse=False) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("true", True), ("1", True), ("false", False), ("0", False), (" True ", True)],
)
def test_booleans_are_read_as_the_web_ui_writes_them(text: str, expected: bool) -> None:  # noqa: FBT001 -- a parametrised flag, not a switch
    found = translate(rules((5, text)), None, sort_reverse=False)

    assert found.inbox is expected


@pytest.mark.parametrize(
    ("sort_field", "reverse", "ordering"),
    [
        (None, False, None),
        (None, True, None),
        ("created", False, "created"),
        ("created", True, "-created"),
        ("archive_serial_number", True, "-archive_serial_number"),
        ("correspondent__name", False, "correspondent__name"),
        ("document_type__name", False, "document_type__name"),
        ("title", False, "title"),
        ("num_notes", False, "num_notes"),
        ("owner", False, "owner"),
        ("page_count", False, "page_count"),
        ("custom_field_12", True, "-custom_field_12"),
        ("score", True, "-score"),
    ],
)
def test_the_sort_of_the_view_becomes_the_ordering(
    sort_field: str | None,
    reverse: bool,  # noqa: FBT001 -- a parametrised flag, not a switch
    ordering: str | None,
) -> None:
    assert translate([], sort_field, sort_reverse=reverse).ordering == ordering


def test_an_unknown_sort_field_is_rejected_by_name() -> None:
    with pytest.raises(Validation, match="sort field 'bogus'") as raised:
        _ = translate([], "bogus", sort_reverse=False)

    assert raised.value.hint is not None
    assert "update_saved_view" in raised.value.hint


def test_an_unknown_rule_type_is_rejected_naming_it() -> None:
    with pytest.raises(Validation, match="Filter rule type 51") as raised:
        _ = translate(rules((6, "1"), (51, "x")), None, sort_reverse=False)

    assert raised.value.hint is not None
    assert "0 to 50" in raised.value.hint


@pytest.mark.parametrize(
    ("rule_type", "value", "message"),
    [
        (2, "seven", "not a whole number"),
        (2, None, "needs a value"),
        (6, "tag", "not a list of whole numbers"),
        (8, "yesterday", "not a date"),
        (8, "2026-13-40", "not a date"),
        (5, "maybe", "not true or false"),
        (7, None, "needs a value"),
        (42, "[not json", "not valid JSON"),
        (42, "null", "not a custom field query"),
        (42, '{"due": 1}', "not a custom field query"),
        (0, None, "needs a value"),
        (8, None, "needs a value"),
        (42, None, "needs a value"),
        (3, "x", "not a whole number"),
    ],
)
def test_a_bad_value_is_rejected_naming_the_rule_type(
    rule_type: int, value: str | None, message: str
) -> None:
    label = RULE_TYPES[rule_type]

    with pytest.raises(Validation, match=f"Filter rule type {rule_type} \\({label}\\)") as raised:
        _ = translate(rules((rule_type, value)), None, sort_reverse=False)

    assert message in raised.value.message


def test_a_custom_field_query_that_is_not_a_list_says_how_to_fix_it() -> None:
    with pytest.raises(Validation) as raised:
        _ = translate(rules((42, "null")), None, sort_reverse=False)

    assert raised.value.message == (
        f"Filter rule type 42 ({RULE_TYPES[42]}) has a value that is not a custom field query"
        " (a JSON list)."
    )
    assert raised.value.hint == EDIT_HINT


@pytest.mark.parametrize("pair", [(2, "1", "2"), (0, "a", "b"), (5, "true", "false")])
def test_a_second_rule_of_a_single_valued_type_is_rejected(pair: tuple[int, str, str]) -> None:
    number, first, second = pair

    with pytest.raises(Validation, match=f"Filter rule type {number} .* more than once"):
        _ = translate(rules((number, first), (number, second)), None, sort_reverse=False)


def test_rules_that_set_the_same_filter_are_rejected_as_duplicates() -> None:
    with pytest.raises(Validation, match=r"type 49 .* same filter as rule type 19"):
        _ = translate(rules((19, "a"), (49, "b")), None, sort_reverse=False)
    with pytest.raises(Validation, match=r"type 48 .* same filter as rule type 0"):
        _ = translate(rules((0, "a"), (48, "b")), None, sort_reverse=False)


@pytest.mark.parametrize(
    "searches",
    [
        [(19, "a"), (0, "b")],
        [(19, "a"), (21, "5")],
        [(20, "a"), (21, "5")],
        [(0, "a"), (21, "5")],
        [(19, "a"), (0, "b"), (20, "c")],
    ],
)
def test_searches_paperless_cannot_combine_are_rejected_naming_the_rules(
    searches: Sequence[tuple[int, str]],
) -> None:
    with pytest.raises(Validation, match="cannot be combined") as raised:
        _ = translate(rules(*searches), None, sort_reverse=False)

    for number, _ in searches:
        assert f"{number} ({RULE_TYPES[number]})" in raised.value.message


@pytest.mark.parametrize("searches", [[(19, "a"), (20, "b")], [(0, "a"), (20, "b")], [(20, "a")]])
def test_a_simple_search_may_come_with_a_fulltext_query(
    searches: Sequence[tuple[int, str]],
) -> None:
    _ = translate(rules(*searches), None, sort_reverse=False)


def test_without_a_sort_reverse_the_ordering_is_ascending() -> None:
    assert translate([], "created").ordering == "created"


@pytest.mark.parametrize(
    ("rule_type", "value", "expected"),
    [
        (35, "2,3", DocumentQuery(owner_none=(2, 3))),
        (33, " 2 , 3 ", DocumentQuery(owner_in=(2, 3))),
        (6, "3,1", DocumentQuery(tags_all=(3, 1))),
        (22, "3,,1,", DocumentQuery(tags_any=(3, 1))),
        (38, "4,5", DocumentQuery(custom_fields_all=(4, 5))),
        (27, "4,5", DocumentQuery(correspondent_none=(4, 5))),
    ],
)
def test_the_web_ui_writes_several_ids_into_one_rule_comma_joined(
    rule_type: int, value: str, expected: DocumentQuery
) -> None:
    assert translate(rules((rule_type, value))) == expected


@pytest.mark.parametrize("value", ["", "  ", ",", " , "])
def test_a_list_rule_without_ids_filters_nothing(value: str) -> None:
    assert translate(rules((33, value), (5, "true"))) == DocumentQuery(inbox=True)


def test_ids_of_several_rules_and_of_comma_joined_values_add_up() -> None:
    assert translate(rules((6, "1,2"), (6, "3"))).tags_all == (1, 2, 3)


def test_one_bad_id_in_a_list_is_rejected_naming_the_rule_type() -> None:
    with pytest.raises(
        Validation, match=r"Filter rule type 35 \(does not have owner in\)"
    ) as raised:
        _ = translate(rules((35, "2,x")))

    assert "'2,x'" in raised.value.message


def test_shared_by_stays_a_single_user_id() -> None:
    with pytest.raises(Validation, match=r"Filter rule type 37"):
        _ = translate(rules((37, "2,3")))


@pytest.mark.parametrize(
    ("first", "second", "named"),
    [
        ((44, "2026-02-01"), (43, "2026-01-01"), "44 (created from) and 43 (created to)"),
        ((46, "2026-02-01"), (45, "2026-01-01"), "46 (added from) and 45 (added to)"),
    ],
)
def test_a_range_that_ends_before_it_starts_is_rejected_naming_both_rule_types(
    first: tuple[int, str], second: tuple[int, str], named: str
) -> None:
    with pytest.raises(Validation, match="start after it ends") as raised:
        _ = translate(rules(first, second))

    assert named in raised.value.message
    assert raised.value.hint is not None


def test_a_range_of_one_day_and_open_ranges_are_fine() -> None:
    day = "2026-02-01"

    _ = translate(rules((44, day), (43, day), (46, day)))
    _ = translate(rules((43, day), (45, day)))
