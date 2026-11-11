"""Documents: the search query, the rows the port returns and the summaries tools return.

The port speaks ids (``DocumentQuery``, ``DocumentHit``); services resolve names into the query
and enrich the hits with names into ``DocumentSummary``. No row ever holds the OCR content.
"""

from datetime import date, datetime
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

type Ordering = Literal[
    "created",
    "-created",
    "added",
    "-added",
    "modified",
    "-modified",
    "title",
    "-title",
    "archive_serial_number",
    "-archive_serial_number",
]


class DocumentQuery(BaseModel):
    """Filters of a document search, every name already resolved to an id; all are ANDed.

    Attributes:
        query: Full-text query in Paperless's Tantivy syntax, passed through unchanged.
        more_like: Id of a document to find similar ones to; never together with ``query``.
        custom_field_query: Paperless's ``custom_field_query`` expression, passed through.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    query: str | None = None
    more_like: int | None = None
    title_contains: str | None = None
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    tags_all: tuple[int, ...] = ()
    tags_any: tuple[int, ...] = ()
    tags_none: tuple[int, ...] = ()
    created_from: date | None = None
    created_to: date | None = None
    added_from: date | None = None
    added_to: date | None = None
    owner: int | None = None
    inbox: bool | None = None
    custom_field_query: JsonValue = None
    ordering: Ordering | None = None


type NameOrId = str | int


class NameFilters(BaseModel):
    """The filters of a search that callers may give by name; services resolve them to ids."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    correspondent: NameOrId | None = None
    document_type: NameOrId | None = None
    storage_path: NameOrId | None = None
    tags_all: tuple[NameOrId, ...] = ()
    tags_any: tuple[NameOrId, ...] = ()
    tags_none: tuple[NameOrId, ...] = ()
    owner: NameOrId | None = None


class CustomFieldValue(BaseModel):
    """A custom field's value on a document, as Paperless stores it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    field: int
    value: JsonValue


class DocumentHit(BaseModel):
    """One document of a search result, with ids where Paperless has them."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    created: date
    added: datetime
    correspondent: int | None
    document_type: int | None
    storage_path: int | None
    tags: tuple[int, ...]
    custom_fields: tuple[CustomFieldValue, ...]
    highlights: str | None = None


class DocumentPage(BaseModel):
    """One page of search hits."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    next_page: int | None
    hits: tuple[DocumentHit, ...]


class Ref(BaseModel):
    """An object a document refers to; name is null if the user may not see it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str | None


class FieldValue(BaseModel):
    """A custom field's value, readable: select labels, "EUR 12.50", document ids."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str | None
    value: JsonValue


class DocumentSummary(BaseModel):
    """A document as a compact search row, without its content."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    created: date
    added: datetime
    correspondent: Ref | None
    document_type: Ref | None
    storage_path: Ref | None
    tags: tuple[Ref, ...]
    custom_fields: tuple[FieldValue, ...]
    highlights: Annotated[
        str | None,
        Field(description="Full-text search only: matching excerpt, terms marked with <b>."),
    ] = None
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class DocumentSearchResult(BaseModel):
    """One page of a document search."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many documents match in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    documents: tuple[DocumentSummary, ...]
