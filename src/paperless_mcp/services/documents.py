"""Document search: names to ids (decision-5), contradictions caught early, readable rows.

Rows name what they refer to and render custom field values readably, without the content. A
kind the user may not list leaves its names null instead of failing the search.
"""

import re
from typing import TYPE_CHECKING

from paperless_mcp.domain.documents import (
    DocumentSearchResult,
    DocumentSummary,
    FieldValue,
    Ref,
)
from paperless_mcp.domain.errors import PermissionDenied, Validation

if TYPE_CHECKING:
    from datetime import date

    from pydantic import JsonValue

    from paperless_mcp.domain.documents import (
        DocumentHit,
        DocumentQuery,
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
MONETARY = re.compile(r"([A-Z]{3})(-?\d+(?:\.\d+)?)")


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
        self._web_url: str = web_url.rstrip("/")

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
        def ref(kind: TaxonomyKind, number: int) -> Ref:
            item = known[kind].get(number)
            return Ref(id=number, name=item.name if item else None)

        def maybe(kind: TaxonomyKind, number: int | None) -> Ref | None:
            return None if number is None else ref(kind, number)

        fields = known["custom_field"]
        return DocumentSummary(
            id=hit.id,
            title=hit.title,
            created=hit.created,
            added=hit.added,
            correspondent=maybe("correspondent", hit.correspondent),
            document_type=maybe("document_type", hit.document_type),
            storage_path=maybe("storage_path", hit.storage_path),
            tags=tuple(ref("tag", tag) for tag in hit.tags),
            custom_fields=tuple(
                FieldValue(
                    id=value.field,
                    name=item.name if (item := fields.get(value.field)) else None,
                    value=_readable(value.value, item),
                )
                for value in hit.custom_fields
            ),
            highlights=hit.highlights,
            web_url=f"{self._web_url}/documents/{hit.id}/details",
        )
