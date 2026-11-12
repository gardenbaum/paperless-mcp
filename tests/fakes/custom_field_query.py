"""The fake's `custom_field_query`: the subset of Paperless 3.4.2's expressions the tests use.

Paperless (documents/filters.py `CustomFieldQueryParser`) accepts `[field, op, value]` with the
field by name or id, combined with `["AND", [...]]`, `["OR", [...]]` and `["NOT", expr]`. The
fake supports the operators below; date values are ISO dates or `today` (Paperless takes any
dateparser expression). Errors mirror Paperless's 400 messages, cut to what tests assert.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import JsonValue

from paperless_mcp.domain.errors import Validation

if TYPE_CHECKING:
    from paperless_mcp.domain.taxonomy import SelectOption

type Values = dict[int, JsonValue]
type Match = Callable[[Values], bool]
# A test of a stored value against the wanted one, and its builder for a data type.
type Test = Callable[[JsonValue, JsonValue], bool]
type Build = Callable[[str], Test]


class CustomField(Protocol):
    """What a query needs of a custom field (the fake's `CustomField`)."""

    @property
    def id(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def data_type(self) -> str: ...
    @property
    def select_options(self) -> tuple[SelectOption, ...]: ...


INVALID = "Invalid custom field query expression."
PREFIX = "Paperless rejected the request: custom_field_query"
TEXT_TYPES = frozenset({"string", "url", "monetary", "longtext"})
ORDERED_TYPES = frozenset({"integer", "float", "date"})


def _reject(detail: str) -> Validation:
    return Validation(f"{PREFIX}: {detail}")


def _number(value: JsonValue, data_type: str) -> float:
    if data_type == "date" and isinstance(value, str):
        # Paperless resolves "today" in PAPERLESS_TIME_ZONE; the E2E stack runs in UTC.
        day = datetime.now(UTC).date() if value == "today" else date.fromisoformat(value)
        return float(day.toordinal())
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    detail = f"{value!r} is not a valid {data_type} value."
    raise _reject(detail)


def _compare(test: Callable[[float, float], bool]) -> Build:
    def build(data_type: str) -> Test:
        return lambda stored, wanted: (
            stored is not None and test(_number(stored, data_type), _number(wanted, data_type))
        )

    return build


def _text(test: Callable[[str, str], bool]) -> Build:
    return lambda _type: (
        lambda stored, wanted: stored is not None and test(str(stored).lower(), str(wanted).lower())
    )


def _range(data_type: str) -> Test:
    def test(stored: JsonValue, wanted: JsonValue) -> bool:
        low, high = cast("list[JsonValue]", wanted)
        return stored is not None and (
            _number(low, data_type) <= _number(stored, data_type) <= _number(high, data_type)
        )

    return test


# Operator -> (data types it applies to, None for all; the test of a stored value).
OPERATORS: dict[str, tuple[frozenset[str] | None, Build]] = {
    "exact": (None, lambda _type: lambda stored, wanted: stored == wanted),
    "in": (None, lambda _type: lambda stored, wanted: stored in cast("list[JsonValue]", wanted)),
    "icontains": (TEXT_TYPES, _text(lambda stored, wanted: wanted in stored)),
    "istartswith": (TEXT_TYPES, _text(str.startswith)),
    "iendswith": (TEXT_TYPES, _text(str.endswith)),
    "gt": (ORDERED_TYPES, _compare(float.__gt__)),
    "gte": (ORDERED_TYPES, _compare(float.__ge__)),
    "lt": (ORDERED_TYPES, _compare(float.__lt__)),
    "lte": (ORDERED_TYPES, _compare(float.__le__)),
    "range": (ORDERED_TYPES, _range),
}


def _option_ids(field: CustomField, wanted: JsonValue) -> JsonValue:
    """Select values may name an option by label; stored values are option ids."""
    ids = {option.label: option.id for option in field.select_options}
    if isinstance(wanted, list):
        return [ids.get(str(item), item) for item in wanted]
    return ids.get(str(wanted), wanted)


def compile_query(expression: JsonValue, fields: Sequence[CustomField]) -> Match:
    """The test of a document's custom field values that `expression` describes.

    Raises:
        Validation: Paperless rejects the expression.
    """
    if not isinstance(expression, list) or not expression:
        raise _reject(INVALID)
    head = expression[0]
    if head in {"AND", "OR"} and len(expression) == 2:
        parts = [compile_query(part, fields) for part in cast("list[JsonValue]", expression[1])]
        combine = all if head == "AND" else any
        return lambda values: combine(part(values) for part in parts)
    if head == "NOT" and len(expression) == 2:
        inner = compile_query(expression[1], fields)
        return lambda values: not inner(values)
    if len(expression) != 3:
        raise _reject(INVALID)
    return _atom(expression[0], str(expression[1]), expression[2], fields)


def _atom(
    name: JsonValue, operator: str, wanted: JsonValue, fields: Sequence[CustomField]
) -> Match:
    found = [field for field in fields if name in {field.id, field.name}]
    if not found:
        detail = f"0: {name!r} is not a valid custom field."
        raise _reject(detail)
    field = found[0]
    if operator == "exists":
        return lambda values: (field.id in values) == wanted
    if operator == "isnull":
        return lambda values: field.id in values and (values[field.id] is None) == wanted
    types, build = OPERATORS.get(operator, (frozenset[str](), None))
    if build is None or (types is not None and field.data_type not in types):
        detail = f"1: {field.data_type} does not support query expr {operator!r}."
        raise _reject(detail)
    test = build(field.data_type)
    if field.data_type == "select":
        wanted = _option_ids(field, wanted)
    return lambda values: field.id in values and bool(test(values[field.id], wanted))
