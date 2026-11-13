"""Documents: the search query, the rows the port returns and the summaries tools return.

The port speaks ids (``DocumentQuery``, ``DocumentHit``); services resolve names into the query
and enrich the hits with names into ``DocumentSummary``. No row ever holds the OCR content.
"""

from datetime import date, datetime
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from paperless_mcp.domain.tasks import TaskState, Wait

type Include = Literal["notes", "history", "metadata", "versions", "suggestions"]
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
type FileVersion = Literal["original", "archive"]
type FileKind = Literal["thumbnail", "original", "archive"]


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


class DocumentPatch(BaseModel):
    """What a document update changes, ids only; the port sends exactly the fields that are set.

    A field that was never given is left alone, one given as ``None`` is cleared (correspondent,
    document type, storage path, archive serial number, owner). ``tags`` and ``custom_fields``
    are the whole new lists: Paperless replaces them as a whole, so whoever builds the patch
    merges them with the current state first.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    title: str | None = None
    created: date | None = None
    archive_serial_number: int | None = None
    owner: int | None = None
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    tags: tuple[int, ...] | None = None
    custom_fields: tuple[CustomFieldValue, ...] | None = None

    @model_validator(mode="after")
    def _required_values(self) -> DocumentPatch:
        """Title, created date and the two lists cannot be cleared, only changed."""
        for name in ("title", "created", "tags", "custom_fields"):
            if name in self.model_fields_set and getattr(self, name) is None:
                message = f"{name} cannot be cleared"
                raise ValueError(message)
        return self


class DocumentUpload(BaseModel):
    """A file with the metadata Paperless should give it, ids only; the port sends it as is.

    Paperless applies the metadata when it consumes the file, so a field left out is decided by
    its own rules (matching, workflows, the file name as the title).

    Attributes:
        filename: The name the file is uploaded under; Paperless keeps it as the original name.
        mime_type: What the content is; the services have sniffed it, Paperless does so again.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    filename: str
    mime_type: str
    content: bytes = Field(repr=False)
    title: str | None = None
    created: date | None = None
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    tags: tuple[int, ...] = ()
    archive_serial_number: int | None = None
    custom_fields: tuple[CustomFieldValue, ...] = ()


class UploadMetadata(BaseModel):
    """What a caller wants a new document to have, with names where Paperless wants ids."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    title: str | None = None
    created: date | None = None
    archive_serial_number: int | None = None
    correspondent: NameOrId | None = None
    document_type: NameOrId | None = None
    storage_path: NameOrId | None = None
    tags: tuple[NameOrId, ...] = ()
    custom_fields: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


type Clearable = Literal[
    "correspondent", "document_type", "storage_path", "archive_serial_number", "owner"
]


class DocumentEdit(BaseModel):
    """What a caller wants changed on a document, with names where Paperless wants ids.

    A field left out stays as it is. ``clear`` empties the fields that can be empty. Tags are
    added, removed or replaced as a whole (``tags_set``, not together with the others); custom
    fields are set by name or id and taken off with ``remove_custom_fields``; whatever is not
    named stays. Services resolve the names and merge, see ``DocumentEditor``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    title: str | None = None
    created: date | None = None
    archive_serial_number: int | None = None
    owner: NameOrId | None = None
    correspondent: NameOrId | None = None
    document_type: NameOrId | None = None
    storage_path: NameOrId | None = None
    clear: tuple[Clearable, ...] = ()
    tags_add: tuple[NameOrId, ...] = ()
    tags_remove: tuple[NameOrId, ...] = ()
    tags_set: tuple[NameOrId, ...] | None = None
    create_missing_tags: bool = False
    confirm: bool = False
    custom_fields: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    remove_custom_fields: tuple[NameOrId, ...] = ()


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


class Note(BaseModel):
    """A note on a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    note: str
    created: datetime | None
    user: Annotated[str | None, Field(description="Username of the author, if known.")]


class DocumentVersion(BaseModel):
    """One file version of a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    added: datetime
    version_label: str | None
    is_root: Annotated[bool, Field(description="True for the original upload.")]


class HistoryEntry(BaseModel):
    """One change of a document in its audit log; the OCR text is never part of it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    timestamp: datetime
    action: str
    actor: Annotated[str | None, Field(description="Username; null for system changes.")]
    changes: Annotated[
        dict[str, JsonValue],
        Field(description="Changed attribute to its old and new value, or to the m2m change."),
    ]


class FileMetadata(BaseModel):
    """What Paperless knows about the files of a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    original_filename: str
    original_mime_type: str
    original_size: Annotated[int, Field(description="Bytes.")]
    original_checksum: str
    media_filename: str
    has_archive_version: bool
    archive_size: Annotated[int | None, Field(description="Bytes; null without archive version.")]
    archive_checksum: str | None
    archive_media_filename: str | None


class Suggestions(BaseModel):
    """The classifier's suggestions for a document, as ids (dates as Paperless sends them)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    correspondents: tuple[int, ...]
    tags: tuple[int, ...]
    document_types: tuple[int, ...]
    storage_paths: tuple[int, ...]
    dates: tuple[str, ...]


class DocumentRecord(BaseModel):
    """One document with ids where Paperless has them; no content, notes only counted."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    created: date
    added: datetime
    modified: datetime
    archive_serial_number: int | None
    owner: int | None
    correspondent: int | None
    document_type: int | None
    storage_path: int | None
    tags: tuple[int, ...]
    custom_fields: tuple[CustomFieldValue, ...]
    original_file_name: str | None
    mime_type: str
    page_count: int | None
    notes_count: int
    versions: tuple[DocumentVersion, ...]
    user_can_change: Annotated[
        bool | None,
        Field(
            description=(
                "Whether object permissions let the user change the document; null where "
                "Paperless does not say (the answer to an update)."
            )
        ),
    ]


class SuggestionNames(BaseModel):
    """Suggestions with names; a name is null if the user may not see that object."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    correspondents: tuple[Ref, ...]
    tags: tuple[Ref, ...]
    document_types: tuple[Ref, ...]
    storage_paths: tuple[Ref, ...]
    dates: tuple[str, ...]


class DocumentHistory(BaseModel):
    """The newest changes of a document, newest first."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    total: Annotated[int, Field(description="How many changes the document has in total.")]
    truncated: Annotated[bool, Field(description="Older changes exist beyond the entries.")]
    entries: tuple[HistoryEntry, ...]


class DocumentDetail(BaseModel):
    """A document with names, readable custom fields and what ``include`` asked for."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    created: date
    added: datetime
    modified: datetime
    archive_serial_number: int | None
    owner: Annotated[Ref | None, Field(description="Null: no owner, visible to every user.")]
    correspondent: Ref | None
    document_type: Ref | None
    storage_path: Ref | None
    tags: tuple[Ref, ...]
    custom_fields: tuple[FieldValue, ...]
    original_file_name: str | None
    mime_type: str
    page_count: int | None
    notes_count: int
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]
    notes: Annotated[tuple[Note, ...] | None, Field(description="Only with include notes.")] = None
    history: Annotated[DocumentHistory | None, Field(description="Only with include history.")] = (
        None
    )
    file_metadata: Annotated[
        FileMetadata | None, Field(description="Only with include metadata.")
    ] = None
    versions: Annotated[
        tuple[DocumentVersion, ...] | None, Field(description="Only with include versions.")
    ] = None
    suggestions: Annotated[
        SuggestionNames | None, Field(description="Only with include suggestions.")
    ] = None
    unavailable: Annotated[
        tuple[Include, ...],
        Field(description="Included sections the user may not read in Paperless."),
    ] = ()


class DocumentUpdate(DocumentDetail):
    """The document after an update, and what the update added on its own."""

    implicit_tags: Annotated[
        tuple[Ref, ...],
        Field(
            description=(
                "Parent tags added because a child tag was added or kept: Paperless tags are "
                "hierarchical and a child needs its parent."
            )
        ),
    ] = ()


class NoteAdded(BaseModel):
    """The note that was added to a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    note: Note
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class NoteDeleted(BaseModel):
    """A note that was deleted for good."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    note_id: int
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class DocumentDeleted(BaseModel):
    """A document that was moved to the Paperless trash."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    title: Annotated[str, Field(description="The title the document had.")]
    in_trash: Annotated[
        bool, Field(description="True: the document is in the trash and can be restored.")
    ]
    message: Annotated[str, Field(description="What happened and how to get the document back.")]


class ContentSlice(BaseModel):
    """A slice of the OCR text of a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    offset: Annotated[int, Field(description="Where the slice starts, in characters.")]
    content: str
    total_chars: Annotated[int, Field(description="Length of the whole text.")]
    next_offset: Annotated[
        int | None, Field(description="Offset of the next slice, null at the end of the text.")
    ]
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class DocumentFile(BaseModel):
    """A file of a document as Paperless sends it: the bytes, their MIME type and file name.

    Attributes:
        content: The file itself.
        mime_type: The ``Content-Type`` Paperless sent, parameters included
            (``text/plain; charset=utf-8``).
        filename: The name Paperless proposes (RFC 6266), non-ASCII characters intact and never
            holding a path, control or bidi characters; None if it proposes none.
        truncated: The file is larger than the limit the caller gave; ``content`` is then empty.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    content: bytes
    mime_type: str
    filename: str | None
    truncated: bool = False


class FileReport(BaseModel):
    """What a tool says about a thumbnail or download, whether or not the bytes came with it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: Annotated[int, Field(description="The document the file belongs to.")]
    kind: Annotated[FileKind, Field(description="Which file: the thumbnail, original or archive.")]
    filename: Annotated[
        str | None,
        Field(
            description=(
                "File name, non-ASCII characters intact. An attached file has the name Paperless "
                "proposes for a download (date, correspondent, title); a file that is not attached "
                "has the name of the upload (the archive version with .pdf), or null."
            )
        ),
    ]
    mime_type: Annotated[
        str, Field(description="MIME type as Paperless sends it, charset parameter included.")
    ]
    size_bytes: Annotated[
        int | None, Field(description="Size of the file in bytes; null if it was not measured.")
    ]
    delivered: Annotated[
        bool, Field(description="The file is attached as content; false: it is over the limit.")
    ]
    max_bytes: Annotated[
        int | None, Field(description="The server's download limit; null for thumbnails.")
    ]
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]
    note: Annotated[str | None, Field(description="Why the file is not attached, if it is not.")]


class FileDelivery(BaseModel):
    """A report and, unless the file is over the size limit, the bytes it describes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    report: FileReport
    data: bytes | None


class Thumbnail(BaseModel):
    """A report and the image it describes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    report: FileReport
    data: bytes


type UploadState = TaskState | Literal["unknown"]


class UploadResult(BaseModel):
    """What an upload did: the file Paperless accepted and, if it was waited for, its outcome."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: Annotated[str, Field(description="Follow it with get_task; Paperless consumes later.")]
    state: Annotated[
        UploadState,
        Field(
            description=(
                "pending, started, success, failure or revoked; unknown when Paperless does not "
                "let this user view tasks (the upload was accepted all the same)."
            )
        ),
    ]
    message: Annotated[
        str | None, Field(description="Why consumption failed, or why the state is unknown.")
    ]
    document_id: Annotated[
        int | None, Field(description="The new document once consumption succeeded; else null.")
    ]
    web_url: Annotated[str | None, Field(description="Link to the new document, once known.")]
    wait: Annotated[
        Wait,
        Field(
            description=(
                "not_requested: wait_seconds was 0; finished: consumption ended within the wait "
                "(see state); timed_out: still running, ask get_task later."
            )
        ),
    ]
    waited_seconds: float
    filename: Annotated[str, Field(description="The name the file was uploaded under.")]
    mime_type: Annotated[str, Field(description="The type found in the content.")]
    size_bytes: int
