"""The document filters that saved views use, as Paperless 3.4.2's DocumentFilterSet applies them.

`FakePaperless.search_documents` adds these to the filters of the search tool; the contract suite
(tests/contract/test_view_filters.py) runs the same cases against live Paperless. The documents
and state are protocols, so this module does not import the fake it serves.
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence
    from datetime import date, datetime

    from pydantic import JsonValue

    from paperless_mcp.domain.documents import DocumentQuery
    from paperless_mcp.domain.taxonomy import SelectOption


class Doc(Protocol):
    """What the filters read of a fake document."""

    @property
    def id(self) -> int: ...
    @property
    def content(self) -> str: ...
    @property
    def correspondent(self) -> int | None: ...
    @property
    def document_type(self) -> int | None: ...
    @property
    def storage_path(self) -> int | None: ...
    @property
    def tags(self) -> list[int]: ...
    @property
    def archive_serial_number(self) -> int | None: ...
    @property
    def created(self) -> date: ...
    @property
    def added(self) -> datetime: ...
    @property
    def modified(self) -> datetime: ...
    @property
    def owner(self) -> int | None: ...
    @property
    def viewers(self) -> set[int]: ...
    @property
    def editors(self) -> set[int]: ...
    @property
    def mime_type(self) -> str: ...
    @property
    def original(self) -> bytes: ...
    @property
    def custom_fields(self) -> dict[int, JsonValue]: ...


class Field(Protocol):
    """What the filters read of a custom field."""

    @property
    def id(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def select_options(self) -> tuple[SelectOption, ...]: ...


class State(Protocol):
    """What the filters read of the fake instance."""

    @property
    def documents(self) -> Sequence[Doc]: ...
    @property
    def custom_fields(self) -> Sequence[Field]: ...


type Match = Callable[[Doc], bool]

# The search parameters Paperless lets a request combine (`_TANTIVY_COMBINABLE_SEARCH_PARAMS`).
COMBINABLE_SEARCHES = ({"text", "query"}, {"title_search", "query"})
ONLY_ONE_SEARCH = "Specify only one of text, title_search, query, or more_like_id."


def searches(query: DocumentQuery) -> set[str]:
    """The search parameters `query` sets."""
    return {
        name
        for name, value in (
            ("text", query.text),
            ("title_search", query.title_search),
            ("query", query.query),
            ("more_like_id", query.more_like),
        )
        if value is not None
    }


def combines(active: set[str]) -> bool:
    """Whether Paperless accepts these search parameters in one request."""
    return len(active) <= 1 or active in COMBINABLE_SEARCHES


def _values(document: Doc, state: State) -> list[str]:
    """The names and text values of a document's custom fields (select options as labels)."""
    fields = {f.id: f for f in state.custom_fields}
    found: list[str] = []
    for number, value in document.custom_fields.items():
        field = fields[number]
        found.append(field.name)
        labels = {option.id: option.label for option in field.select_options}
        found.append(labels.get(value, str(value)) if isinstance(value, str) else str(value))
    return found


def _has_duplicate(document: Doc, state: State) -> bool:
    """Another document with the same file and text (Paperless compares checksums)."""
    return any(
        other is not document
        and other.original == document.original
        and other.content == document.content
        for other in state.documents
    )


def _by_id(
    isnull: bool | None,  # noqa: FBT001 -- a tri-state filter
    wanted: Collection[int],
    banned: Collection[int],
    get: Callable[[Doc], int | None],
) -> list[Match]:
    """A reference that is absent or present, in a list, or not in a list."""
    checks: list[Match] = []
    if isnull is not None:
        checks.append(lambda d: (get(d) is None) == isnull)
    if wanted:
        checks.append(lambda d: get(d) in wanted)
    if banned:
        checks.append(lambda d: get(d) not in banned)
    return checks


def _taxonomy(query: DocumentQuery) -> list[Match]:
    """Correspondent, document type, storage path and tags."""
    checks = [
        *_by_id(
            query.correspondent_isnull,
            query.correspondent_in,
            query.correspondent_none,
            lambda d: d.correspondent,
        ),
        *_by_id(
            query.document_type_isnull,
            query.document_type_in,
            query.document_type_none,
            lambda d: d.document_type,
        ),
        *_by_id(
            query.storage_path_isnull,
            query.storage_path_in,
            query.storage_path_none,
            lambda d: d.storage_path,
        ),
    ]
    if query.tagged is not None:
        checks.append(lambda d: bool(d.tags) == query.tagged)
    return checks


def _dates(after: date | None, before: date | None, get: Callable[[Doc], date]) -> list[Match]:
    """Strictly after and strictly before a day."""
    checks: list[Match] = []
    if after is not None:
        checks.append(lambda d: get(d) > after)
    if before is not None:
        checks.append(lambda d: get(d) < before)
    return checks


def _numbers_and_dates(query: DocumentQuery) -> list[Match]:
    """Archive serial numbers and the created, added and modified dates."""
    checks: list[Match] = [
        *_dates(query.created_gt, query.created_lt, lambda d: d.created),
        *_dates(query.added_gt, query.added_lt, lambda d: d.added.date()),
        *_dates(query.modified_gt, query.modified_lt, lambda d: d.modified.date()),
    ]
    if query.archive_serial_number is not None:
        checks.append(lambda d: d.archive_serial_number == query.archive_serial_number)
    if query.asn_gt is not None:
        asn_gt = query.asn_gt
        checks.append(
            lambda d: d.archive_serial_number is not None and d.archive_serial_number > asn_gt
        )
    if query.asn_lt is not None:
        asn_lt = query.asn_lt
        checks.append(
            lambda d: d.archive_serial_number is not None and d.archive_serial_number < asn_lt
        )
    if query.asn_isnull is not None:
        checks.append(lambda d: (d.archive_serial_number is None) == query.asn_isnull)
    if query.created_year is not None:
        checks.append(lambda d: d.created.year == query.created_year)
    if query.created_month is not None:
        checks.append(lambda d: d.created.month == query.created_month)
    if query.created_day is not None:
        checks.append(lambda d: d.created.day == query.created_day)
    return checks


def _owners(query: DocumentQuery) -> list[Match]:
    """Owners and sharing."""
    checks: list[Match] = []
    if query.owner_in:
        checks.append(lambda d: d.owner in query.owner_in)
    if query.owner_none:
        checks.append(lambda d: d.owner not in query.owner_none)
    if query.owner_isnull is not None:
        checks.append(lambda d: (d.owner is None) == query.owner_isnull)
    if query.shared_by is not None:
        checks.append(lambda d: d.owner == query.shared_by and bool(d.viewers or d.editors))
    return checks


def _files_and_fields(query: DocumentQuery, state: State) -> list[Match]:
    """Content, file type, duplicates and custom fields."""
    checks: list[Match] = []
    if query.content_contains is not None:
        checks.append(lambda d: (query.content_contains or "").lower() in d.content.lower())
    if query.mime_type is not None:
        checks.append(lambda d: (query.mime_type or "").lower() in d.mime_type.lower())
    if query.has_duplicates is not None:
        checks.append(lambda d: _has_duplicate(d, state) == query.has_duplicates)
    if query.custom_fields_icontains is not None:
        needle = query.custom_fields_icontains.lower()
        checks.append(lambda d: any(needle in text.lower() for text in _values(d, state)))
    if query.custom_fields_all:
        checks.append(lambda d: set(query.custom_fields_all) <= set(d.custom_fields))
    if query.custom_fields_in:
        checks.append(lambda d: bool(set(query.custom_fields_in) & set(d.custom_fields)))
    if query.custom_fields_none:
        checks.append(lambda d: not set(query.custom_fields_none) & set(d.custom_fields))
    if query.has_custom_fields is not None:
        checks.append(lambda d: bool(d.custom_fields) == query.has_custom_fields)
    return checks


def view_filters(query: DocumentQuery, state: State) -> list[Match]:
    """The tests of the saved view filters `query` sets."""
    return [
        *_taxonomy(query),
        *_numbers_and_dates(query),
        *_owners(query),
        *_files_and_fields(query, state),
    ]
