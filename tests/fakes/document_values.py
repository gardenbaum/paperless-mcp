"""What Paperless 3.4.2 does to the values of a document PATCH (probed on the E2E stack).

Pure helpers of `FakePaperless.update_document`: custom field value coercion and validation, and
the tag hierarchy. Every message is the one Paperless answers, so contract tests match both.
"""

import re
from datetime import date
from typing import TYPE_CHECKING, NoReturn, Protocol

from paperless_mcp.domain.errors import Validation

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

    from pydantic import JsonValue

    from paperless_mcp.domain.taxonomy import SelectOption

MAX_STRING = 128
MONEY = re.compile(r"([A-Z]{3})?-?\d+(\.\d{2})?")
URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class HasOptions(Protocol):
    """A custom field as far as values are concerned (`HasOptions`)."""

    @property
    def data_type(self) -> str: ...
    @property
    def select_options(self) -> tuple[SelectOption, ...]: ...


class HasParent(Protocol):
    """A tag as far as the hierarchy is concerned (`FakeTag`)."""

    @property
    def parent(self) -> int | None: ...


def reject(where: str, message: str) -> NoReturn:
    """Fail like a 400 whose body names `where` (a field path) and `message`."""
    text = f"Paperless rejected the request: {where}: {message}"
    raise Validation(text)


def _integer(value: JsonValue) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    message = "Enter a valid integer."
    raise ValueError(message)


def _number(value: JsonValue) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    message = "A valid number is required."
    raise ValueError(message)


def _text(value: JsonValue, *, limit: int | None) -> str:
    if not isinstance(value, str):
        message = "Not a valid string."
        raise ValueError(message)  # noqa: TRY004 -- the message is Paperless's
    if limit is not None and len(value) > limit:
        message = f"Ensure this value has at most {limit} characters (it has {len(value)})."
        raise ValueError(message)
    return value


def _url(value: JsonValue) -> str:
    url = _text(value, limit=None)
    if not URI_SCHEME.match(url):
        message = f"Unable to parse URI {url}, missing scheme"
        raise ValueError(message)
    return url


def _date(value: JsonValue) -> str:
    day = _text(value, limit=None)
    try:
        if not DAY.fullmatch(day):
            raise ValueError  # noqa: TRY301 -- one message for both ways to fail
        _ = date.fromisoformat(day)
    except ValueError:
        message = "Date has wrong format. Use one of these formats instead: YYYY-MM-DD."
        raise ValueError(message) from None
    return day


def _boolean(value: JsonValue) -> bool:
    if isinstance(value, bool) or value in {0, 1}:
        return bool(value)
    message = "Must be a boolean."
    raise ValueError(message)


def _money(value: JsonValue) -> str:
    text = str(value) if isinstance(value, int) and not isinstance(value, bool) else value
    if not isinstance(text, str) or not MONEY.fullmatch(text):
        message = "Must be a two-decimal number with optional currency code e.g. GBP123.45"
        raise ValueError(message)
    return text


def _links(value: JsonValue, documents: Collection[int]) -> JsonValue:
    if not isinstance(value, list):
        message = "Value must be a list"
        raise ValueError(message)  # noqa: TRY004 -- the message is Paperless's
    if len(set(map(str, value))) != len(value) or not all(
        isinstance(n, int) and n in documents for n in value
    ):
        message = "Some documents in value don't exist or were specified twice."
        raise ValueError(message)
    return value


def _option(field: HasOptions, value: JsonValue) -> JsonValue:
    if value not in [option.id for option in field.select_options]:
        listing = [{"label": o.label, "id": o.id} for o in field.select_options]
        message = f"Value must be an id of an element in {listing}"
        raise ValueError(message)
    return value


def coerce(field: HasOptions, value: JsonValue, documents: Collection[int]) -> JsonValue:
    """The value Paperless stores for `value` in `field`.

    Raises:
        ValueError: Paperless's message for a value the field's data type does not accept.
    """
    if value is None:
        return None
    plain: dict[str, Callable[[JsonValue], JsonValue]] = {
        "string": lambda v: _text(v, limit=MAX_STRING),
        "longtext": lambda v: _text(v, limit=None),
        "url": _url,
        "date": _date,
        "boolean": _boolean,
        "integer": _integer,
        "float": _number,
        "monetary": _money,
        "documentlink": lambda v: _links(v, documents),
        "select": lambda v: _option(field, v),
    }
    return plain.get(field.data_type, lambda v: v)(value)


def tag_closure(
    current: Collection[int], requested: list[int], tags: Mapping[int, HasParent]
) -> list[int]:
    """The tags a document ends up with when `requested` replaces `current`.

    Paperless 3 tags are hierarchical: a tag taken away takes its descendants with it, a tag
    added brings its ancestors, and what was explicitly added stays. Duplicates collapse.
    """

    def parent(number: int) -> int | None:
        return tags[number].parent

    def ancestors(number: int) -> list[int]:
        found: list[int] = []
        step = parent(number)
        while step is not None and step not in found:
            found.append(step)
            step = parent(step)
        return found

    wanted = list(dict.fromkeys(requested))
    removed = set(current) - set(wanted)
    added = [n for n in wanted if n not in current]
    kept = [n for n in wanted if n in current and not removed & set(ancestors(n))]
    result = list(dict.fromkeys([*kept, *added]))
    for number in added:
        result.extend(a for a in ancestors(number) if a not in result)
    return result
