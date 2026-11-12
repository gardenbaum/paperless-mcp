"""Document search: names to ids (decision-5), contradictions caught early, readable rows.

Rows name what they refer to and render custom field values readably, without the content. A
kind the user may not list leaves its names null instead of failing the search.
"""

import asyncio
import re
from typing import TYPE_CHECKING, cast

from paperless_mcp.domain.documents import (
    ContentSlice,
    DocumentDetail,
    DocumentHistory,
    DocumentSearchResult,
    DocumentSummary,
    FieldValue,
    Ref,
    SuggestionNames,
)
from paperless_mcp.domain.errors import FeatureUnavailable, PermissionDenied, Validation

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import date

    from pydantic import JsonValue

    from paperless_mcp.domain.documents import (
        CustomFieldValue,
        DocumentHit,
        DocumentQuery,
        DocumentRecord,
        Include,
        NameFilters,
        NameOrId,
    )
    from paperless_mcp.domain.ports import DocumentReader
    from paperless_mcp.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from paperless_mcp.services.taxonomy import TaxonomyResolver

# The kinds a search row refers to by id.
REFERRED: tuple[TaxonomyKind, ...] = (
    "tag",
    "correspondent",
    "document_type",
    "storage_path",
    "custom_field",
)
# Characters of the OCR text per slice unless the caller asks otherwise (design 7).
DEFAULT_SLICE = 20_000
# Newest audit log entries returned with include history; the rest is counted.
HISTORY_LIMIT = 50
# Most distinct linked documents whose titles a document read looks up.
LINK_LIMIT = 100
# The order sections are fetched and reported in.
SECTIONS: tuple[Include, ...] = ("notes", "history", "metadata", "versions", "suggestions")
MONETARY = re.compile(r"([A-Z]{3})(-?\d+(?:\.\d+)?)")


def document_link(web_url: str, document_id: int) -> str:
    """The link to a document in the Paperless web UI under ``web_url``."""
    return f"{web_url.rstrip('/')}/documents/{document_id}/details"


def _check_range(name: str, start: date | None, end: date | None) -> None:
    if start and end and start > end:
        message = f"{name}_from is after {name}_to."
        raise Validation(message, hint=f"Swap them, or leave one out for an open {name} range.")


def _readable(value: JsonValue, field: TaxonomyItem | None) -> JsonValue:
    """Select option ids as labels and "EUR12.50" as "EUR 12.50"; anything else unchanged."""
    if field is None or not isinstance(value, str):
        return value
    if field.data_type == "select":
        labels = {option.id: option.label for option in field.select_options or ()}
        return labels.get(value, value)
    money = MONETARY.fullmatch(value) if field.data_type == "monetary" else None
    return f"{money[1]} {money[2]}" if money else value


class DocumentService:
    """Finds documents for the current user."""

    def __init__(self, reader: DocumentReader, taxonomy: TaxonomyResolver, *, web_url: str) -> None:
        """Search through ``reader``; ``web_url`` is the base of links to the Paperless UI."""
        self._reader: DocumentReader = reader
        self._taxonomy: TaxonomyResolver = taxonomy
        self._web_url: str = web_url

    async def search(
        self, names: NameFilters, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentSearchResult:
        """One page of the documents matching ``names`` and ``query``.

        Raises:
            Validation: The filters contradict each other, or Paperless rejects them.
            NotFound: A name matches nothing, or ``page`` is beyond the last page.
            Ambiguous: A name matches more than one object.
        """
        if query.query is not None and query.more_like is not None:
            message = "Pass either query or more_like_id, not both."
            hint = "Search similar documents first, then filter by title or tags."
            raise Validation(message, hint=hint)
        _check_range("created", query.created_from, query.created_to)
        _check_range("added", query.added_from, query.added_to)
        resolved = query.model_copy(update=await self._ids(names))
        found = await self._reader.search_documents(resolved, page=page, page_size=page_size)
        known: dict[TaxonomyKind, dict[int, TaxonomyItem]] = {
            kind: await self._known(kind) for kind in REFERRED
        }
        return DocumentSearchResult(
            count=found.count,
            page=page,
            next_page=found.next_page,
            documents=tuple(self._summary(hit, known) for hit in found.hits),
        )

    async def _ids(self, names: NameFilters) -> dict[str, object]:
        async def one(kind: TaxonomyKind, value: NameOrId | None) -> int | None:
            return None if value is None else (await self._taxonomy.resolve(kind, value)).id

        async def tags(values: tuple[NameOrId, ...]) -> tuple[int, ...]:
            return tuple([(await self._taxonomy.resolve("tag", value)).id for value in values])

        tags_all, tags_none = await tags(names.tags_all), await tags(names.tags_none)
        required = dict(zip(tags_all, names.tags_all, strict=True))
        both = [required[tag] for tag in tags_none if tag in required]
        if both:
            message = f"Tags both required and excluded: {', '.join(map(repr, both))}."
            raise Validation(message, hint="Remove them from tags_all or from tags_none.")
        return {
            "correspondent": await one("correspondent", names.correspondent),
            "document_type": await one("document_type", names.document_type),
            "storage_path": await one("storage_path", names.storage_path),
            "owner": await self._owner(names.owner),
            "tags_all": tags_all,
            "tags_any": await tags(names.tags_any),
            "tags_none": tags_none,
        }

    async def _owner(self, value: NameOrId | None) -> int | None:
        """Paperless filters by any owner id; only a username needs the user list."""
        if isinstance(value, int) or (
            value and value.strip().isascii() and value.strip().isdigit()
        ):
            return int(value)
        try:
            return None if value is None else (await self._taxonomy.resolve("user", value)).id
        except PermissionDenied as error:
            hint = "Pass the owner's user id instead of the username."
            raise PermissionDenied(error.message, hint=hint) from None

    async def _known(self, kind: TaxonomyKind) -> dict[int, TaxonomyItem]:
        try:
            return {item.id: item for item in await self._taxonomy.items(kind)}
        except PermissionDenied:
            return {}

    def _summary(
        self, hit: DocumentHit, known: dict[TaxonomyKind, dict[int, TaxonomyItem]]
    ) -> DocumentSummary:
        return DocumentSummary(
            id=hit.id,
            title=hit.title,
            created=hit.created,
            added=hit.added,
            correspondent=_maybe(known, "correspondent", hit.correspondent),
            document_type=_maybe(known, "document_type", hit.document_type),
            storage_path=_maybe(known, "storage_path", hit.storage_path),
            tags=tuple(_ref(known, "tag", tag) for tag in hit.tags),
            custom_fields=_fields(known, hit.custom_fields),
            highlights=hit.highlights,
            web_url=self._link(hit.id),
        )

    def _link(self, document_id: int) -> str:
        return document_link(self._web_url, document_id)

    async def get(self, document_id: int, include: Collection[Include] = ()) -> DocumentDetail:
        """One document with names, readable custom fields and the sections in ``include``.

        A section the user may not read in Paperless (notes and the audit log need their own
        permissions, and the audit log can be switched off) is named in ``unavailable``
        instead of failing the call.

        Raises:
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user may not view documents at all.
        """
        record = await self._reader.get_document(document_id)
        kinds: tuple[TaxonomyKind, ...] = (*REFERRED, "user")
        loaded = await asyncio.gather(*(self._known(kind) for kind in kinds))
        known = dict(zip(kinds, loaded, strict=True))
        titles = await self._linked_titles(record, known)
        wanted: list[Include] = [section for section in SECTIONS if section in include]
        sections: dict[str, object] = {}
        unavailable: list[Include] = []
        for section in wanted:
            try:
                sections.update(await self._section(section, record, known))
            except PermissionDenied, FeatureUnavailable:
                unavailable.append(section)
        return DocumentDetail(
            id=record.id,
            title=record.title,
            created=record.created,
            added=record.added,
            modified=record.modified,
            archive_serial_number=record.archive_serial_number,
            owner=_maybe(known, "user", record.owner),
            correspondent=_maybe(known, "correspondent", record.correspondent),
            document_type=_maybe(known, "document_type", record.document_type),
            storage_path=_maybe(known, "storage_path", record.storage_path),
            tags=tuple(_ref(known, "tag", tag) for tag in record.tags),
            custom_fields=_fields(known, record.custom_fields, titles),
            original_file_name=record.original_file_name,
            mime_type=record.mime_type,
            page_count=record.page_count,
            notes_count=record.notes_count,
            web_url=self._link(record.id),
        ).model_copy(update={**sections, "unavailable": tuple(unavailable)})

    async def _linked_titles(
        self, record: DocumentRecord, known: dict[TaxonomyKind, dict[int, TaxonomyItem]]
    ) -> dict[int, str]:
        """Titles of the documents the document-link fields of ``record`` point to."""
        linked = [
            number
            for value in record.custom_fields
            if _is_link(known, value)
            for number in cast("list[int]", value.value)
        ]
        return await self._reader.get_document_titles(list(dict.fromkeys(linked))[:LINK_LIMIT])

    async def _section(
        self,
        section: Include,
        record: DocumentRecord,
        known: dict[TaxonomyKind, dict[int, TaxonomyItem]],
    ) -> dict[str, object]:
        """The fields of the detail one ``include`` section fills."""
        reader, number = self._reader, record.id
        match section:
            case "notes":
                return {"notes": await reader.list_document_notes(number)}
            case "history":
                entries = await reader.list_document_history(number)
                shown = entries[:HISTORY_LIMIT]
                return {
                    "history": DocumentHistory(
                        total=len(entries), truncated=len(shown) < len(entries), entries=shown
                    )
                }
            case "metadata":
                return {"file_metadata": await reader.get_document_metadata(number)}
            case "versions":
                return {"versions": record.versions}
            case "suggestions":
                found = await reader.get_document_suggestions(number)
                return {
                    "suggestions": SuggestionNames(
                        correspondents=tuple(
                            _ref(known, "correspondent", n) for n in found.correspondents
                        ),
                        tags=tuple(_ref(known, "tag", n) for n in found.tags),
                        document_types=tuple(
                            _ref(known, "document_type", n) for n in found.document_types
                        ),
                        storage_paths=tuple(
                            _ref(known, "storage_path", n) for n in found.storage_paths
                        ),
                        dates=found.dates,
                    )
                }

    async def content(self, document_id: int, *, offset: int, max_chars: int) -> ContentSlice:
        """``max_chars`` characters of the OCR text from ``offset``.

        An offset at or past the end gives an empty slice, so callers can always read
        ``total_chars``. ``next_offset`` is null once the slice reaches the end.

        Raises:
            NotFound: No such document, or the user may not see it.
        """
        text = await self._reader.get_document_content(document_id)
        end = offset + max_chars
        return ContentSlice(
            document_id=document_id,
            offset=offset,
            content=text[offset:end],
            total_chars=len(text),
            next_offset=end if end < len(text) else None,
            web_url=self._link(document_id),
        )


def _ref(
    known: dict[TaxonomyKind, dict[int, TaxonomyItem]], kind: TaxonomyKind, number: int
) -> Ref:
    item = known[kind].get(number)
    return Ref(id=number, name=item.name if item else None)


def _maybe(
    known: dict[TaxonomyKind, dict[int, TaxonomyItem]], kind: TaxonomyKind, number: int | None
) -> Ref | None:
    return None if number is None else _ref(known, kind, number)


def _is_link(known: dict[TaxonomyKind, dict[int, TaxonomyItem]], value: CustomFieldValue) -> bool:
    """Whether ``value`` is a document-link field holding a list of document ids."""
    item = known["custom_field"].get(value.field)
    return bool(item and item.data_type == "documentlink" and isinstance(value.value, list))


def _fields(
    known: dict[TaxonomyKind, dict[int, TaxonomyItem]],
    values: tuple[CustomFieldValue, ...],
    titles: dict[int, str] | None = None,
) -> tuple[FieldValue, ...]:
    """Custom field values, readable; with ``titles`` document links name their documents."""
    fields = known["custom_field"]

    def render(value: CustomFieldValue) -> JsonValue:
        if titles is not None and _is_link(known, value):
            return [{"id": n, "name": titles.get(n)} for n in cast("list[int]", value.value)]
        return _readable(value.value, fields.get(value.field))

    return tuple(
        FieldValue(
            id=value.field,
            name=item.name if (item := fields.get(value.field)) else None,
            value=render(value),
        )
        for value in values
    )
