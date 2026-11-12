"""Custom field values: checked per data type and put into the form Paperless stores.

Paperless answers a wrong value with a 400 whose text is hard to act on, and a boolean sent as a
string with a 500. Values are therefore checked before they are sent, and the error names the
field and what its data type takes. Callers may use friendlier forms where that is unambiguous:
a select takes the option's label, a monetary amount any of "EUR12.50", "EUR 12.50" or 12.5,
and a date object stands for its ISO form.
"""

import math
import re
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from paperless_mcp.domain.errors import Ambiguous, Validation
from paperless_mcp.services.taxonomy import fold

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

    from paperless_mcp.domain.taxonomy import TaxonomyItem

MAX_STRING = 128
MONEY_DECIMALS = 2
URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:\S*")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
AMOUNT = re.compile(
    r"\s*(?:(?P<before>[A-Za-z]{3})\s*)?(?P<amount>-?\d+(?:\.\d+)?)(?:\s*(?P<after>[A-Za-z]{3}))?\s*"
)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _text(field: TaxonomyItem, value: object) -> str:
    if not isinstance(value, str):
        message = f"Custom field {field.name!r} takes text."
        raise Validation(message, hint="Pass a string.")
    return value


def _select(field: TaxonomyItem, value: object) -> str:
    label = _text(field, value) if isinstance(value, str) else None
    options = field.select_options or ()
    if label is None:
        message = f"Custom field {field.name!r} takes the label of one of its options."
        raise Validation(message, hint=f"Options: {', '.join(repr(o.label) for o in options)}.")
    for matches in (
        [o for o in options if o.label == label],
        [o for o in options if o.id == label],
        [o for o in options if fold(o.label) == fold(label)],
    ):
        if len(matches) == 1:
            return matches[0].id
        if matches:
            names = ", ".join(repr(o.label) for o in matches)
            message = f"Several options of {field.name!r} match {label!r}: {names}."
            raise Ambiguous(message, hint="Pass the label exactly as listed.")
    message = f"{field.name!r} has no option {label!r}."
    listing = ", ".join(repr(o.label) for o in options)
    raise Validation(message, hint=f"Options: {listing}.")


def _money(field: TaxonomyItem, value: object) -> str:
    expected = f"Custom field {field.name!r} takes an amount with an optional currency."
    hint = "Pass 'EUR12.50', 'EUR 12.50' or 12.5; amounts have at most two decimals."
    if _is_number(value):
        parts = {"before": None, "amount": repr(value), "after": None}
    else:
        found = AMOUNT.fullmatch(value) if isinstance(value, str) else None
        if found is None:
            raise Validation(expected, hint=hint)
        parts = found.groupdict()
    if parts["before"] and parts["after"]:
        raise Validation(expected, hint=hint)
    amount = Decimal(str(parts["amount"]))
    if not amount.is_finite():
        raise Validation(expected, hint=hint)
    if cast("int", amount.as_tuple().exponent) < -MONEY_DECIMALS:
        message = f"The amount for {field.name!r} has more than two decimals."
        raise Validation(message, hint=hint)
    currency = (parts["before"] or parts["after"] or "").upper()
    return f"{currency}{amount:.2f}"


def _links(field: TaxonomyItem, value: object) -> list[JsonValue]:
    problem = f"Custom field {field.name!r} takes a list of document ids."
    hint = "Pass positive ids from search_documents, e.g. [12, 40]."
    if not isinstance(value, list):
        raise Validation(problem, hint=hint)
    items = cast("list[object]", value)
    ids = [n for n in items if isinstance(n, int) and not isinstance(n, bool) and n > 0]
    if len(ids) != len(items):
        raise Validation(problem, hint=hint)
    return list(dict.fromkeys(ids))


def _day(field: TaxonomyItem, value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    try:
        if isinstance(value, str) and DAY.fullmatch(value):
            return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    message = f"Custom field {field.name!r} takes a date as YYYY-MM-DD."
    raise Validation(message, hint="Pass e.g. '2026-02-03'.")


def _string(field: TaxonomyItem, value: object) -> str:
    text = _text(field, value)
    if len(text) > MAX_STRING:
        message = f"Custom field {field.name!r} takes at most {MAX_STRING} characters."
        raise Validation(message, hint="Use a longtext field for longer text.")
    return text


def _url(field: TaxonomyItem, value: object) -> str:
    url = value if isinstance(value, str) else ""
    if not URI_SCHEME.fullmatch(url):
        message = f"Custom field {field.name!r} takes a URL with a scheme."
        raise Validation(message, hint="Pass e.g. 'https://example.com'.")
    return url


def _boolean(field: TaxonomyItem, value: object) -> bool:
    if not isinstance(value, bool):
        message = f"Custom field {field.name!r} takes true or false."
        raise Validation(message, hint="Pass a JSON boolean, not text.")
    return value


def _integer(field: TaxonomyItem, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"Custom field {field.name!r} takes a whole number."
        raise Validation(message, hint="Pass a JSON integer, e.g. 42.")
    return value


def _float(field: TaxonomyItem, value: object) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        message = f"Custom field {field.name!r} takes a number."
        raise Validation(message, hint="Pass a JSON number, e.g. 2.5.")
    return value


_CHECKS: dict[str, Callable[[TaxonomyItem, object], JsonValue]] = {
    "string": _string,
    "longtext": _text,
    "url": _url,
    "date": _day,
    "boolean": _boolean,
    "integer": _integer,
    "float": _float,
    "monetary": _money,
    "documentlink": _links,
    "select": _select,
}


def normalise(field: TaxonomyItem, value: object) -> JsonValue:
    """The value to store in custom field ``field`` for ``value``.

    Raises:
        Validation: ``value`` does not fit the field's data type; the hint says what does.
        Ambiguous: Several select options match the label.
    """
    if value is None:
        message = f"Custom field {field.name!r} needs a value."
        hint = "To take the field off the document use remove_custom_fields."
        raise Validation(message, hint=hint)
    check = _CHECKS.get(field.data_type or "")
    if check is None:
        kind = field.data_type
        message = f"Custom field {field.name!r} has the data type {kind!r}, which is not supported."
        raise Validation(message)
    return check(field, value)
