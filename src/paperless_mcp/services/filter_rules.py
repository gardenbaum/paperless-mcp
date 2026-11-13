"""Filter rules of a saved view to a document query, as the Paperless web UI does it.

Paperless stores a view as numeric rule types with text values and never runs it: its web UI maps
each type to a document list filter (``FILTER_RULE_TYPES`` in the 3.4.2 frontend). This module
does the same, for every one of the 51 types (0 to 50). A rule is either translated or rejected
with an error that names its type, never skipped: a view that ran on a part of its rules would
return documents the user did not mean.

Deliberate differences from the web UI: it silently lets the last of two rules of a single-valued
type win (we reject the view), and it sends a nonsense value on to Paperless (we reject it first).
Like the UI, the title rules (0 and 48) use the title search and the text rules (19 and 49) the
text search, which Paperless 3.4 answers from its index, not the deprecated ``title_content``.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final, Literal, cast

from paperless_mcp.domain.documents import DocumentQuery
from paperless_mcp.domain.errors import Validation
from paperless_mcp.domain.views import RULE_TYPES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from paperless_mcp.domain.views import FilterRule

type Kind = Literal["text", "int", "bool", "date", "ids", "ref", "json"]


@dataclass(frozen=True, slots=True)
class Spec:
    """How one rule type is translated.

    Attributes:
        field: The ``DocumentQuery`` field the value goes to; a ``ref`` rule also sets
            ``<field>_isnull``.
        kind: How the value is read: text as is, a whole number, true or false, an ISO date,
            one id of a list, an id or "not assigned" (no value) or "assigned" (-1), JSON.
    """

    field: str
    kind: Kind


SPECS: Final[dict[int, Spec]] = {
    0: Spec("title_search", "text"),
    1: Spec("content_contains", "text"),
    2: Spec("archive_serial_number", "int"),
    3: Spec("correspondent", "ref"),
    4: Spec("document_type", "ref"),
    5: Spec("inbox", "bool"),
    6: Spec("tags_all", "ids"),
    7: Spec("tagged", "bool"),
    8: Spec("created_lt", "date"),
    9: Spec("created_gt", "date"),
    10: Spec("created_year", "int"),
    11: Spec("created_month", "int"),
    12: Spec("created_day", "int"),
    13: Spec("added_lt", "date"),
    14: Spec("added_gt", "date"),
    15: Spec("modified_lt", "date"),
    16: Spec("modified_gt", "date"),
    17: Spec("tags_none", "ids"),
    18: Spec("asn_isnull", "bool"),
    19: Spec("text", "text"),
    20: Spec("query", "text"),
    21: Spec("more_like", "int"),
    22: Spec("tags_any", "ids"),
    23: Spec("asn_gt", "int"),
    24: Spec("asn_lt", "int"),
    25: Spec("storage_path", "ref"),
    26: Spec("correspondent_in", "ids"),
    27: Spec("correspondent_none", "ids"),
    28: Spec("document_type_in", "ids"),
    29: Spec("document_type_none", "ids"),
    30: Spec("storage_path_in", "ids"),
    31: Spec("storage_path_none", "ids"),
    32: Spec("owner", "int"),
    33: Spec("owner_in", "ids"),
    34: Spec("owner_isnull", "bool"),
    35: Spec("owner_none", "ids"),
    36: Spec("custom_fields_icontains", "text"),
    37: Spec("shared_by", "int"),
    38: Spec("custom_fields_all", "ids"),
    39: Spec("custom_fields_in", "ids"),
    40: Spec("custom_fields_none", "ids"),
    41: Spec("has_custom_fields", "bool"),
    42: Spec("custom_field_query", "json"),
    43: Spec("created_to", "date"),
    44: Spec("created_from", "date"),
    45: Spec("added_to", "date"),
    46: Spec("added_from", "date"),
    47: Spec("mime_type", "text"),
    48: Spec("title_search", "text"),
    49: Spec("text", "text"),
    50: Spec("has_duplicates", "bool"),
}

# Paperless answers 400 unless a request has at most one of these, apart from a simple search
# (text or title search) together with a fulltext query.
SEARCH_FIELDS: Final = ("text", "title_search", "query", "more_like")
COMBINABLE: Final = ({"text", "query"}, {"title_search", "query"})
# The columns a view can sort by (the web UI's list), custom fields as custom_field_<id>.
SORT_FIELDS: Final = frozenset(
    {
        *("archive_serial_number", "correspondent__name", "title", "document_type__name"),
        *("created", "added", "modified", "num_notes", "owner", "page_count", "score"),
    }
)
CUSTOM_SORT: Final = re.compile(r"custom_field_\d+")
NOT_ASSIGNED: Final = None
ASSIGNED: Final = "-1"
TRUE: Final = frozenset({"true", "1"})
FALSE: Final = frozenset({"false", "0"})
EDIT_HINT: Final = "Change the view with update_saved_view."


def _label(number: int) -> str:
    return f"Filter rule type {number} ({RULE_TYPES[number]})"


def _bad(number: int, problem: str, hint: str) -> Validation:
    return Validation(f"{_label(number)} {problem}", hint=hint)


def _whole(number: int, value: str | None) -> int:
    if value is None:
        raise _bad(number, "needs a value.", EDIT_HINT)
    try:
        return int(value.strip())
    except ValueError:
        text = f"has the value {value!r}, which is not a whole number."
        raise _bad(number, text, EDIT_HINT) from None


def _flag(number: int, value: str | None) -> bool:
    if value is None:
        raise _bad(number, "needs a value.", EDIT_HINT)
    text = value.strip().lower()
    if text in TRUE | FALSE:
        return text in TRUE
    raise _bad(number, f"has the value {value!r}, which is not true or false.", EDIT_HINT)


def _day(number: int, value: str | None) -> date:
    if value is None:
        raise _bad(number, "needs a value.", EDIT_HINT)
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        text = f"has the value {value!r}, which is not a date (YYYY-MM-DD)."
        raise _bad(number, text, EDIT_HINT) from None


def _json(number: int, value: str | None) -> JsonValue:
    if value is None:
        raise _bad(number, "needs a value.", EDIT_HINT)
    try:
        return cast("JsonValue", json.loads(value))
    except ValueError:
        raise _bad(number, "has a value that is not valid JSON.", EDIT_HINT) from None


def _text(number: int, value: str | None) -> str:
    if value is None:
        raise _bad(number, "needs a value.", EDIT_HINT)
    return value


def check_sort(sort_field: str) -> None:
    """Accept a sort field the web UI offers.

    Raises:
        Validation: The field is unknown; the hint says how to drop or change the sort.
    """
    if sort_field not in SORT_FIELDS and not CUSTOM_SORT.fullmatch(sort_field):
        message = f"The view sorts by the unknown sort field {sort_field!r}."
        hint = f"{EDIT_HINT} Use clear_sort to drop the sort, or a field such as created or title."
        raise Validation(message, hint=hint)


def _ordering(sort_field: str | None, *, sort_reverse: bool) -> str | None:
    if sort_field is None:
        return None
    check_sort(sort_field)
    return f"-{sort_field}" if sort_reverse else sort_field


def translate(
    rules: Sequence[FilterRule], sort_field: str | None = None, *, sort_reverse: bool = False
) -> DocumentQuery:
    """The document query that the ``rules`` and the sort of a saved view stand for.

    Raises:
        Validation: A rule has an unknown type or a value its type cannot read, a single-valued
            type occurs twice, the searches cannot be combined, or the sort field is unknown.
            The message names the rule type.
    """
    fields: dict[str, object] = {}
    setters: dict[str, int] = {}
    for rule in rules:
        number = rule.rule_type
        spec = SPECS.get(number)
        if spec is None:
            message = f"Filter rule type {number} is not one of the types of Paperless 3.4.2."
            raise Validation(
                message, hint="The types are numbered 0 to 50; create_saved_view lists them."
            )
        _apply(fields, setters, number, spec, rule.value)
    _check_searches(setters)
    return DocumentQuery.model_validate(
        {**fields, "ordering": _ordering(sort_field, sort_reverse=sort_reverse)}
    )


def _apply(
    fields: dict[str, object], setters: dict[str, int], number: int, spec: Spec, value: str | None
) -> None:
    """Add one rule to ``fields``; ``setters`` remembers which rule set which single filter."""
    if spec.kind == "ids":
        fields[spec.field] = (*(fields.get(spec.field) or ()), _whole(number, value))  # pyright: ignore[reportGeneralTypeIssues] -- tuple of ints
        return
    if spec.field in setters:
        other = setters[spec.field]
        if other == number:
            message = f"{_label(number)} occurs more than once; a view may have it only once."
        else:
            message = (
                f"{_label(number)} sets the same filter as rule type {other}, which the view has."
            )
        raise Validation(message, hint=EDIT_HINT)
    setters[spec.field] = number
    if spec.kind == "ref":
        if value == ASSIGNED:
            fields[f"{spec.field}_isnull"] = False
        elif value is NOT_ASSIGNED:
            fields[f"{spec.field}_isnull"] = True
        else:
            fields[spec.field] = _whole(number, value)
        return
    reader = {
        "text": _text,
        "int": _whole,
        "bool": _flag,
        "date": _day,
        "json": _json,
    }[spec.kind]
    fields[spec.field] = reader(number, value)


def _check_searches(setters: dict[str, int]) -> None:
    active = {name for name in SEARCH_FIELDS if name in setters}
    if len(active) <= 1 or active in COMBINABLE:
        return
    named = ", ".join(
        _label(setters[name]).removeprefix("Filter rule type ")
        for name in SEARCH_FIELDS
        if name in active
    )
    message = f"Paperless can run only one search per query: rule types {named} cannot be combined."
    raise Validation(
        message, hint=f"Keep one of them (or a simple search with a fulltext query). {EDIT_HINT}"
    )
