"""FakePaperless: an in-memory Paperless 3.4.2 that implements the domain ports (design 9.1).

Services and tools are tested against it instead of mocks. The contract suite (tests/contract)
runs the same tests against it and against the adapter on live Paperless, so it cannot drift.

- `FakeState` is the instance: users, documents and taxonomy. `seeded()` mirrors the E2E seed
  (tests/e2e/helpers.py `seed`); tests change the state directly to set up a case.
- `FakePaperless(state, user=...)` is one credential: it sees what that user sees in Paperless
  (owner, object-level view grants, superuser and global permissions).
- Write guard: methods of write ports are decorated with `@write` and raise
  `WriteNotAllowedError` unless the fake was created with `allow_writes=True`, so a read-only
  tool that writes fails its own tests. tests/contract/test_coverage.py checks that every
  method of a `*Writer` port is decorated.

Adding a port method: implement it here with Paperless 3.4.2's behaviour (read the view in the
Paperless source of the tag, not the docs alone), extend the state only as far as it needs, and
write its contract tests.
"""

import functools
import hashlib
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import PurePath
from typing import TYPE_CHECKING, Concatenate, cast

from pydantic import JsonValue

from fakes.accounts import (
    ADMIN,
    BLIND,
    BLIND_PERMISSIONS,
    PRIVATE_ASN,
    PRIVATE_CONTENT,
    PUBLIC_ASN,
    PUBLIC_NOTE,
    READER,
    READER_PERMISSIONS,
)
from fakes.custom_field_query import compile_query
from fakes.document_values import coerce, reject, tag_closure
from fakes.taxonomy_rules import check_colour, check_match, check_name, check_path, with_ids
from paperless_mcp.domain.ai import AiSuggestions, ChatAnswer, ChatSource
from paperless_mcp.domain.bulk import (
    FieldsChange,
    ReferenceChange,
    TagsChange,
    TrashDocuments,
)
from paperless_mcp.domain.documents import (
    CustomFieldValue,
    DocumentFile,
    DocumentHit,
    DocumentPage,
    DocumentPatch,
    DocumentQuery,
    DocumentRecord,
    DocumentUpload,
    DocumentVersion,
    FileMetadata,
    HistoryEntry,
    Note,
    Suggestions,
)
from paperless_mcp.domain.errors import FeatureUnavailable, NotFound, PermissionDenied, Validation
from paperless_mcp.domain.instance import (
    ComponentStatus,
    CurrentUser,
    InstanceOverview,
    InstanceStatistics,
    SystemStatus,
)
from paperless_mcp.domain.tasks import TaskState, TaskStatus
from paperless_mcp.domain.taxonomy import SelectOption, TaxonomyDetail, TaxonomyItem
from paperless_mcp.paperless.bulk import documents_refused, objects_refused
from paperless_mcp.paperless.documents import (
    beyond_last_page,
    unseen_document,
    unseen_note,
    unseen_version,
)
from paperless_mcp.paperless.errors import ai_disabled
from paperless_mcp.paperless.tasks import not_found

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence
    from types import CoroutineType

    from _typeshed import SupportsRichComparison

    from paperless_mcp.domain.bulk import (
        BulkKind,
        DocumentChange,
        ObjectPermissions,
        PermissionsChange,
    )
    from paperless_mcp.domain.documents import FileVersion
    from paperless_mcp.domain.taxonomy import (
        MatchingAlgorithm,
        TaxonomyKind,
        TaxonomyValues,
        WritableKind,
    )
    from paperless_mcp.domain.versions import VersionUpload

# Paperless's max_length of document titles and tag names.
MAX_TITLE = 128
# And of version labels.
MAX_LABEL = 64

VERSION = "3.4.2"
API_VERSION = 10

# The types 3.4.2 consumes without Tika or Gotenberg; `post_document` answers 400 for any other.
CONSUMABLE = frozenset(
    {
        *("application/pdf", "text/plain", "text/csv", "text/markdown"),
        *("image/jpeg", "image/png", "image/gif", "image/tiff", "image/bmp", "image/webp"),
    }
)

# A WebP header, as far as clients sniff it: Paperless 3.4 renders thumbnails as WebP.
THUMBNAIL = b"RIFF\x1a\x00\x00\x00WEBPVP8L fake"

# What the E2E stack reports: a fresh instance without classifier, sanity check or AI.
HEALTHY = SystemStatus(
    install_type="docker",
    storage_total_bytes=50_000_000_000,
    storage_available_bytes=40_000_000_000,
    database=ComponentStatus(status="OK", error=None),
    redis=ComponentStatus(status="OK", error=None),
    celery=ComponentStatus(status="OK", error=None),
    search_index=ComponentStatus(status="OK", error=None),
    classifier=ComponentStatus(status="WARNING", error="No classifier training tasks found"),
    sanity_check=ComponentStatus(status="WARNING", error="No sanity check tasks found"),
    llm_index=ComponentStatus(status="DISABLED", error=None),
)


@dataclass(frozen=True, kw_only=True)
class FakeUser:
    """A Paperless user.

    Attributes:
        permissions: Global permission codenames without the app label, e.g. `view_document`.
    """

    id: int
    username: str
    is_superuser: bool = False
    is_staff: bool = False
    permissions: frozenset[str] = frozenset()

    def has(self, permission: str) -> bool:
        """Django's `has_perm`: superusers have every permission."""
        return self.is_superuser or permission in self.permissions

    def may_change_object(self, document: Owned) -> bool:
        """Paperless's `user_can_change`: object permissions only, not the global permission.

        Known gap: object-level change grants are not modelled, so a user who does not own a
        document, is not a superuser and has it shared for viewing cannot change it here.
        (A search hit does model them: `Owned.changeable_by`.)
        """
        return self.is_superuser or document.owner in {None, self.id}

    def may_change(self, document: Owned) -> bool:
        """Whether a PATCH of `document` is allowed: `change_document` and the object."""
        return self.has("change_document") and self.may_change_object(document)


@dataclass(kw_only=True)
class Owned:
    """An object with Paperless object permissions.

    Attributes:
        owner: The owning user's id; None means shared, visible to every user.
        viewers: Ids of users with an object-level view grant.
        editors: Ids of users with an object-level change grant.
    """

    id: int
    owner: int | None = None
    viewers: set[int] = field(default_factory=set[int])
    editors: set[int] = field(default_factory=set[int])
    # Group grants are stored but give nobody access: group membership is not modelled.
    view_groups: set[int] = field(default_factory=set[int])
    change_groups: set[int] = field(default_factory=set[int])

    def changeable_by(self, user: FakeUser) -> bool:
        """Paperless's `has_perms_owner_aware(user, "change_document", obj)`."""
        return user.is_superuser or self.owner in {None, user.id} or user.id in self.editors

    def visible_to(self, user: FakeUser) -> bool:
        """Paperless's `permitted_object_ids` (group grants are not modelled yet)."""
        return user.is_superuser or self.owner in {None, user.id} or user.id in self.viewers


@dataclass(kw_only=True)
class FakeTaxonomy(Owned):
    """A correspondent, document type, storage path or saved view.

    Matching is Paperless's default for a new object made by hand (any word, nothing to match).
    """

    name: str
    matching_algorithm: MatchingAlgorithm = "any"
    match: str = ""
    case_insensitive: bool = True


@dataclass(kw_only=True)
class FakeTag(FakeTaxonomy):
    """A tag; inbox tags are what the statistics count as the inbox."""

    is_inbox: bool = False
    colour: str = "#a6cee3"
    parent: int | None = None


@dataclass(kw_only=True)
class FakeStoragePath(FakeTaxonomy):
    """A storage path with its template."""

    path: str = "{{ title }}"


@dataclass(frozen=True, kw_only=True)
class FakeCustomField:
    """A custom field; Paperless does not give custom fields an owner."""

    id: int
    name: str
    data_type: str
    select_options: tuple[SelectOption, ...] = ()


@dataclass(frozen=True, kw_only=True)
class FakeGroup:
    """A user group."""

    id: int
    name: str


SEED_DAY = date(2026, 11, 11)


NO_SUGGESTIONS = Suggestions(
    correspondents=(), tags=(), document_types=(), storage_paths=(), dates=()
)


NO_AI_SUGGESTIONS = AiSuggestions(
    title=None,
    correspondents=(),
    new_correspondents=(),
    tags=(),
    new_tags=(),
    document_types=(),
    new_document_types=(),
    storage_paths=(),
    new_storage_paths=(),
    dates=(),
)
# What Paperless's chat answers when it finds no document text to answer from.
CHAT_NO_CONTENT = "Sorry, I couldn't find any content to answer your question."


@dataclass(frozen=True)
class FakeFile:
    """The file of one version of a document, and the text Paperless read from it."""

    original: bytes
    archive: bytes | None
    content: str


@dataclass(kw_only=True)
class FakeDocument(Owned):
    """A document.

    Attributes:
        custom_fields: The value of each custom field the document has, by field id, as
            Paperless stores it (a select field stores the option id).
    """

    title: str
    content: str = ""
    created: date = SEED_DAY
    added: datetime = datetime(2026, 11, 11, 19, 47, tzinfo=UTC)
    modified: datetime = datetime(2026, 11, 11, 19, 59, tzinfo=UTC)
    archive_serial_number: int | None = None
    tags: list[int] = field(default_factory=list[int])
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    custom_fields: dict[int, JsonValue] = field(default_factory=dict[int, JsonValue])
    mime_type: str = "application/pdf"
    page_count: int | None = 1
    notes: list[Note] = field(default_factory=list[Note])
    # Newest first, as Paperless sends it; a "create" entry holds the OCR text as `content`.
    history: list[HistoryEntry] = field(default_factory=list[HistoryEntry])
    versions: list[DocumentVersion] = field(default_factory=list[DocumentVersion])
    # The file of every version by version id, filled in when the document gets a second version
    # (the root's file is then kept here); `original`, `archive` and `content` are the newest's.
    version_files: dict[int, FakeFile] = field(default_factory=dict[int, FakeFile])
    suggestions: Suggestions = field(default_factory=lambda: NO_SUGGESTIONS)
    ai_suggestions: AiSuggestions = field(default_factory=lambda: NO_AI_SUGGESTIONS)
    # The name of the uploaded file; the seed's files are named after the title.
    file_name: str | None = None
    original: bytes = b"%PDF-1.4 fake original"
    # None: Paperless made no archive version (e.g. a plain text document).
    archive: bytes | None = b"%PDF-1.4 fake archive version, longer than the original"
    thumbnail: bytes = THUMBNAIL

    def __post_init__(self) -> None:
        """Name the file after the title unless given; a rename keeps the file's name.

        A document made without versions gets its root version, as every Paperless document has.
        """
        self.file_name = self.file_name or f"{self.title}.pdf"
        if not self.versions:
            self.versions = [_root_version(self.id, self.original)]

    @property
    def original_filename(self) -> str:
        """The uploaded file's name; the seed's files are named after the title."""
        return self.file_name or f"{self.title}.pdf"

    def download_filename(self, *, archive: bool) -> str:
        """The name Paperless proposes for a download: the created date, then the title.

        The archive version is always a PDF; the original keeps the upload's extension.
        """
        extension = ".pdf" if archive else PurePath(self.original_filename).suffix
        return f"{self.created.isoformat()} {self.title}{extension}"


@dataclass(kw_only=True)
class FakeTask:
    """A background task, as `/api/tasks/` reports it.

    Not owner-filtered: whether Paperless hides another owner's task from a user who may view
    tasks is not verified on 3.4.2 (the seed has no such user).

    Attributes:
        task_id: The Celery UUID clients look tasks up by.
        message: The error message of a failed task (its traceback is not modelled).
        document_ids: The task's related documents.
    """

    id: int
    task_id: str
    task_type: str = "consume_file"
    status: TaskState = "pending"
    created: datetime = datetime(2026, 11, 11, 19, 47, tzinfo=UTC)
    finished: datetime | None = None
    message: str | None = None
    document_ids: tuple[int, ...] = ()


@dataclass(kw_only=True)
class FakeState:
    """The data of one fake Paperless instance, shared by every `FakePaperless` on it."""

    users: list[FakeUser] = field(default_factory=list[FakeUser])
    documents: list[FakeDocument] = field(default_factory=list[FakeDocument])
    tags: list[FakeTag] = field(default_factory=list[FakeTag])
    correspondents: list[FakeTaxonomy] = field(default_factory=list[FakeTaxonomy])
    document_types: list[FakeTaxonomy] = field(default_factory=list[FakeTaxonomy])
    storage_paths: list[FakeStoragePath] = field(default_factory=list[FakeStoragePath])
    custom_fields: list[FakeCustomField] = field(default_factory=list[FakeCustomField])
    saved_views: list[FakeTaxonomy] = field(default_factory=list[FakeTaxonomy])
    groups: list[FakeGroup] = field(default_factory=list[FakeGroup])
    tasks: list[FakeTask] = field(default_factory=list[FakeTask])
    # Deleted documents, newest last; reads no longer see them, the Paperless trash does.
    trash: list[FakeDocument] = field(default_factory=list[FakeDocument])
    # The ids of the documents a bulk reprocess queued, in order (the fake does no OCR).
    reprocessed: list[int] = field(default_factory=list[int])
    # The id of the last note created: note ids are global and never reused.
    last_note_id: int = 1
    system_status: SystemStatus = HEALTHY
    ai_enabled: bool = False
    audit_log_enabled: bool = True


# The seeded custom fields in creation order (tests/e2e/helpers.py `seed`), `select` comes last.
CUSTOM_FIELD_TYPES = (
    *("string", "url", "date", "boolean", "integer", "float", "monetary"),
    *("documentlink", "longtext"),
)
# The global permission Paperless requires to list each kind (PaperlessObjectPermissions).
VIEW_PERMISSION: dict[TaxonomyKind, str] = {
    "tag": "view_tag",
    "correspondent": "view_correspondent",
    "document_type": "view_documenttype",
    "storage_path": "view_storagepath",
    "custom_field": "view_customfield",
    "saved_view": "view_savedview",
    "user": "view_user",
    "group": "view_group",
}


# The seeded public document's custom field values by data type (tests/e2e/helpers.py `seed`).
SEED_VALUES: dict[str, JsonValue] = {
    "string": "hello",
    "url": "https://example.com/e2e",
    "date": "2026-01-15",
    "boolean": True,
    "integer": 42,
    "float": 3.25,
    "monetary": "EUR12.50",
    "documentlink": [2],
    "longtext": "line one\nline two",
    "select": "opt-alpha",
}


def _created(document_id: int, title: str, content: str) -> HistoryEntry:
    """The audit log entry Paperless writes when it consumes a document (it holds the text)."""
    return HistoryEntry(
        id=document_id * 10,
        timestamp=datetime(2026, 11, 11, 19, 47, tzinfo=UTC),
        action="create",
        actor="admin",
        changes={"title": ["None", title], "content": ["None", content]},
    )


def _root_version(document_id: int, original: bytes = b"%PDF-1.4 fake original") -> DocumentVersion:
    return DocumentVersion(
        id=document_id,
        added=datetime(2026, 11, 11, 19, 47, tzinfo=UTC),
        version_label=None,
        is_root=True,
        checksum=hashlib.sha256(original).hexdigest(),
    )


def seeded() -> FakeState:
    """A fresh state with the objects and users of the E2E seed."""
    admin = FakeUser(id=2, username=ADMIN[0], is_superuser=True, is_staff=True)
    reader = FakeUser(id=3, username=READER[0], permissions=frozenset(READER_PERMISSIONS))
    blind = FakeUser(id=4, username=BLIND[0], permissions=frozenset(BLIND_PERMISSIONS))
    fields = [
        FakeCustomField(id=number, name=f"e2e-{data_type}", data_type=data_type)
        for number, data_type in enumerate(CUSTOM_FIELD_TYPES, start=1)
    ]
    fields.append(
        FakeCustomField(
            id=len(fields) + 1,
            name="e2e-select",
            data_type="select",
            select_options=tuple(
                SelectOption(id=f"opt-{label}", label=label) for label in ("alpha", "beta")
            ),
        )
    )
    return FakeState(
        users=[admin, reader, blind],
        tags=[
            FakeTag(id=1, name="e2e-finance"),
            FakeTag(id=2, name="e2e-invoices", parent=1),
            FakeTag(id=3, name="e2e-private", owner=admin.id),
            FakeTag(id=4, name="e2e-inbox", is_inbox=True),
        ],
        correspondents=[FakeTaxonomy(id=1, name="e2e-acme")],
        document_types=[FakeTaxonomy(id=1, name="e2e-invoice")],
        storage_paths=[
            FakeStoragePath(id=1, name="e2e-archive", path="e2e/{{ created_year }}/{{ title }}")
        ],
        custom_fields=fields,
        saved_views=[FakeTaxonomy(id=1, name="e2e-view", owner=admin.id)],
        groups=[FakeGroup(id=1, name="e2e-group")],
        documents=[
            FakeDocument(
                id=1,
                title="e2e-public-invoice",
                content="E2E public invoice ACME 2026",
                tags=[1, 2, 4],
                correspondent=1,
                document_type=1,
                storage_path=1,
                archive_serial_number=PUBLIC_ASN,
                custom_fields={f.id: SEED_VALUES[f.data_type] for f in fields},
                notes=[
                    Note(
                        id=1,
                        note=PUBLIC_NOTE,
                        created=datetime(2026, 11, 11, 20, 0, tzinfo=UTC),
                        user="admin",
                    )
                ],
                history=[
                    HistoryEntry(
                        id=15,
                        timestamp=datetime(2026, 11, 11, 20, 0, tzinfo=UTC),
                        action="update",
                        actor=None,
                        changes={"Note Added": ["None", 1]},
                    ),
                    _created(1, "e2e-public-invoice", "E2E public invoice ACME 2026"),
                ],
                versions=[_root_version(1)],
            ),
            FakeDocument(
                id=2,
                title="e2e-private-note",
                content=PRIVATE_CONTENT,
                owner=admin.id,
                archive_serial_number=PRIVATE_ASN,
                tags=[3],
                custom_fields={fields[7].id: [1]},  # the mirror of the public document's link
                history=[_created(2, "e2e-private-note", PRIVATE_CONTENT)],
                versions=[_root_version(2)],
            ),
        ],
    )


class WriteNotAllowedError(AssertionError):
    """A test wrote to the fake without opting in with `allow_writes=True`."""


_WRITES: set[object] = set()


def write[F: FakePaperless, **P, R](
    method: Callable[Concatenate[F, P], CoroutineType[object, object, R]],
) -> Callable[Concatenate[F, P], CoroutineType[object, object, R]]:
    """Mark a fake method as a write: it fails unless the fake allows writes."""

    @functools.wraps(method)
    async def guarded(self: F, /, *args: P.args, **kwargs: P.kwargs) -> R:
        if not self.allow_writes:
            message = (
                f"{method.__qualname__} writes to Paperless; create the fake with "
                "allow_writes=True in tests that mean to write."
            )
            raise WriteNotAllowedError(message)
        return await method(self, *args, **kwargs)

    _WRITES.add(guarded)
    return guarded


def is_write(method: object) -> bool:
    """Whether `method` carries the write guard."""
    return method in _WRITES


class FakePaperless:
    """The ports as one Paperless user sees them, on an in-memory instance."""

    def __init__(self, state: FakeState, *, user: str, allow_writes: bool = False) -> None:
        """Act as the user named `user` of `state`.

        Raises:
            LookupError: `state` has no such user (a mistake in the test setup).
        """
        self.state: FakeState = state
        found = [candidate for candidate in state.users if candidate.username == user]
        if not found:
            message = f"the fake has no user {user!r}"
            raise LookupError(message)
        self.user: FakeUser = found[0]
        self.allow_writes: bool = allow_writes

    async def get_overview(self) -> InstanceOverview:
        """`InstanceReader.get_overview` (StatisticsView, SystemStatusView, UiSettingsView)."""
        user = self.user
        may_view_status = user.is_staff or user.has("view_system_monitoring")
        may_view_settings = user.has("view_uisettings")
        return InstanceOverview(
            paperless_version=VERSION,
            api_version=API_VERSION,
            statistics=self._statistics(),
            system_status=self.state.system_status if may_view_status else None,
            current_user=(
                CurrentUser(id=user.id, username=user.username, is_superuser=user.is_superuser)
                if may_view_settings
                else None
            ),
            ai_enabled=self.state.ai_enabled if may_view_settings else None,
        )

    def _statistics(self) -> InstanceStatistics:
        everything = self.user.has("view_global_statistics")

        def visible[O: Owned](objects: list[O]) -> list[O]:
            return [o for o in objects if everything or o.visible_to(self.user)]

        documents = visible(self.state.documents)
        inbox = {tag.id for tag in visible(self.state.tags) if tag.is_inbox}
        return InstanceStatistics(
            documents=len(documents),
            inbox_documents=(
                sum(1 for document in documents if inbox & set(document.tags)) if inbox else None
            ),
            tags=len(visible(self.state.tags)),
            correspondents=len(visible(self.state.correspondents)),
            document_types=len(visible(self.state.document_types)),
            storage_paths=len(visible(self.state.storage_paths)),
        )

    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        """`TaxonomyReader.list_taxonomy` (the taxonomy, user and group viewsets).

        Paperless orders by name and counts only the documents the user may see.
        """
        if not self.user.has(VIEW_PERMISSION[kind]):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        rows = self._rows(kind)
        return sorted(rows, key=lambda row: (row.name.lower(), row.id))

    def _rows(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        state, user = self.state, self.user
        documents = [d for d in state.documents if d.visible_to(user)]

        def count(uses: Callable[[FakeDocument], bool]) -> int:
            return sum(1 for document in documents if uses(document))

        def tags() -> list[TaxonomyItem]:
            return [
                TaxonomyItem(
                    id=t.id,
                    name=t.name,
                    document_count=count(lambda d, t=t: t.id in d.tags),
                    colour=t.colour,
                    parent=t.parent,
                )
                for t in state.tags
                if t.visible_to(user)
            ]

        def owned(
            objects: Sequence[FakeTaxonomy], link: Callable[[FakeDocument], int | None]
        ) -> Callable[[], list[TaxonomyItem]]:
            return lambda: [
                TaxonomyItem(
                    id=o.id, name=o.name, document_count=count(lambda d, o=o: link(d) == o.id)
                )
                for o in objects
                if o.visible_to(user)
            ]

        def custom_fields() -> list[TaxonomyItem]:
            return [
                TaxonomyItem(
                    id=f.id,
                    name=f.name,
                    document_count=count(lambda d, f=f: f.id in d.custom_fields),
                    data_type=f.data_type,
                    select_options=f.select_options if f.data_type == "select" else None,
                )
                for f in state.custom_fields
            ]

        def names(rows: list[tuple[int, str]]) -> Callable[[], list[TaxonomyItem]]:
            return lambda: [TaxonomyItem(id=i, name=n, document_count=None) for i, n in rows]

        views = [(v.id, v.name) for v in state.saved_views if v.visible_to(user)]
        kinds: dict[TaxonomyKind, Callable[[], list[TaxonomyItem]]] = {
            "tag": tags,
            "correspondent": owned(state.correspondents, lambda d: d.correspondent),
            "document_type": owned(state.document_types, lambda d: d.document_type),
            "storage_path": owned(state.storage_paths, lambda d: d.storage_path),
            "custom_field": custom_fields,
            "saved_view": names(views),
            "user": names([(u.id, u.username) for u in state.users]),
            "group": names([(g.id, g.name) for g in state.groups]),
        }
        return kinds[kind]()

    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        """`DocumentReader.search_documents` (UnifiedSearchViewSet, DocumentFilterSet).

        Full text matches whole words of the title and content, all terms required (Tantivy
        also searches notes, names and fields and supports its query syntax); more-like finds
        visible documents sharing a word of the content. Without full text Paperless orders
        newest first.

        Raises:
            Validation: Both `query` and `more_like`, or a bad `custom_field_query`.
            NotFound: `page` is beyond the last page.
            PermissionDenied: The user lacks the global `view_document` permission (HTTP 403).
        """
        if not self.user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if query.query is not None and query.more_like is not None:
            message = (
                "Paperless rejected the request: "
                "Specify only one of text, title_search, query, or more_like_id."
            )
            raise Validation(message)
        documents = [d for d in self.state.documents if d.visible_to(self.user)]
        matches = _filters(query, self.state)
        if query.custom_field_query is not None:
            test = compile_query(query.custom_field_query, self.state.custom_fields)
            matches.append(lambda d: test(d.custom_fields))
        terms = _words(query.query or "") - TANTIVY_OPERATORS
        if query.query is not None:
            matches.append(lambda d: terms <= _words(f"{d.title} {d.content}"))
        if query.more_like is not None:
            like = [d for d in documents if d.id == query.more_like]
            if not like:
                raise unseen_document(query.more_like)
            words = _words(like[0].content)
            matches.append(lambda d: d.id != query.more_like and bool(words & _words(d.content)))
        found = [d for d in documents if all(match(d) for match in matches)]
        found = _ordered(found, query.ordering, ranked=query.query or query.more_like)
        start = (page - 1) * page_size
        if page > 1 and start >= len(found):
            raise beyond_last_page(page)
        hits = tuple(
            _hit(d, self.user, _highlight(d.content, terms) if query.query is not None else None)
            for d in found[start : start + page_size]
        )
        return DocumentPage(
            count=len(found),
            next_page=page + 1 if start + page_size < len(found) else None,
            hits=hits,
        )

    async def get_task(self, task_id: str) -> TaskStatus:
        """`TaskReader.get_task` (TaskViewSet filtered by `task_id`).

        Raises:
            NotFound: No visible task has that id (Paperless answers an empty list).
            PermissionDenied: The user lacks the global `view_paperlesstask` permission.
        """
        if not self.user.has("view_paperlesstask"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        for task in self.state.tasks:
            if task.task_id == task_id:
                return TaskStatus(
                    task_id=task.task_id,
                    task_type=task.task_type,
                    state=task.status,
                    message=task.message,
                    document_ids=task.document_ids,
                    created=task.created,
                    finished=task.finished,
                )
        raise not_found(task_id)

    def _document(self, document_id: int) -> FakeDocument:
        """The document if it exists (`NotFound` otherwise); callers check visibility."""
        found = [d for d in self.state.documents if d.id == document_id]
        if not found:
            raise unseen_document(document_id)
        return found[0]

    def _seen(self, document_id: int) -> FakeDocument:
        """The document for the detail view: a hidden document looks like a missing one (404)."""
        if not self.user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        document = self._document(document_id)
        if not document.visible_to(self.user):
            raise unseen_document(document_id)
        return document

    def _sub(self, document_id: int, *permissions: str) -> FakeDocument:
        """The document for a sub-resource.

        The global permission is checked first (403), then existence (404); a hidden document
        is 403, which differs from the detail view.
        """
        for needed in ("view_document", *permissions):
            if not self.user.has(needed):
                message = "The Paperless user may not perform this action."
                raise PermissionDenied(message)
        document = self._document(document_id)
        if not document.visible_to(self.user):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        return document

    async def get_document(self, document_id: int) -> DocumentRecord:
        """`DocumentReader.get_document` (DocumentViewSet.retrieve, `fields=` without content)."""
        return _record(self._seen(document_id), self.user)

    @write
    async def update_document(self, document_id: int, patch: DocumentPatch) -> DocumentRecord:
        """`DocumentWriter.update_document` (DocumentViewSet.partial_update).

        Order in Paperless 3.4.2: the global `change_document` permission (403), then the
        document among those the user may see (404), then object permission (403: an owner or
        a superuser may change it, a viewer may not). Lists are replaced as a whole.

        Raises:
            Validation: Paperless's 400 for a value, with its message.
        """
        if not self.user.has("change_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        document = self._seen(document_id)
        if not self.user.may_change(document):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        changes = self._changes(document, patch)
        if "custom_fields" in changes:
            self._mirror_links(document, cast("dict[int, JsonValue]", changes["custom_fields"]))
        document.__dict__.update(changes)
        document.modified = datetime.now(UTC)
        return _record(document, self.user, report_change=False)  # an update's answer omits it

    @write
    async def upload_document(self, upload: DocumentUpload) -> str:
        """`DocumentWriter.upload_document` (PostDocumentView): the form is checked, then a task.

        Consumption is immediate here. Probed on 3.4.2: a file type it cannot consume, an
        unknown id and a custom field value of the wrong type are 400s; a taken archive serial
        number only fails the task; the same content twice makes two documents; the new
        document is owned by the uploader, gets the parents of its tags and the inbox tags.

        Raises:
            PermissionDenied: The user lacks the global `add_document` permission.
            Validation: Paperless's 400 for the form, with its message.
        """
        if not self.user.has("add_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if upload.mime_type not in CONSUMABLE:
            reject("document", f"File type {upload.mime_type} not supported")
        state = self.state
        for name, known in (
            ("correspondent", state.correspondents),
            ("document_type", state.document_types),
            ("storage_path", state.storage_paths),
        ):
            value = cast("int | None", getattr(upload, name))
            if value is not None and value not in {o.id for o in known}:
                reject(name, f'Invalid pk "{value}" - object does not exist.')
        tags = {t.id: t for t in state.tags}
        for tag in upload.tags:
            if tag not in tags:
                reject("tags", f'Invalid pk "{tag}" - object does not exist.')
        values = self._values(upload)
        now = datetime.now(UTC)
        task = FakeTask(
            id=len(state.tasks) + 1, task_id=str(uuid.uuid4()), created=now, finished=now
        )
        state.tasks.append(task)
        taken = upload.archive_serial_number is not None and any(
            d.archive_serial_number == upload.archive_serial_number for d in state.documents
        )
        if taken:
            task.status = "failure"
            task.message = (
                f"{upload.filename}: Not consuming {upload.filename}: "
                f"Given ASN {upload.archive_serial_number} already exists!"
            )
            return task.task_id
        number = max((d.id for d in state.documents), default=0) + 1
        title = upload.title or PurePath(upload.filename).stem
        text = (
            upload.content.decode(errors="replace") if upload.mime_type.startswith("text/") else ""
        )
        inbox = [t.id for t in state.tags if t.is_inbox]
        state.documents.append(
            FakeDocument(
                id=number,
                title=title,
                content=text,
                created=upload.created or now.date(),
                added=now,
                modified=now,
                owner=self.user.id,
                archive_serial_number=upload.archive_serial_number,
                tags=tag_closure([], [*upload.tags, *inbox], tags),
                correspondent=upload.correspondent,
                document_type=upload.document_type,
                storage_path=upload.storage_path,
                custom_fields=values,
                mime_type=upload.mime_type,
                file_name=upload.filename,
                original=upload.content,
                archive=upload.content + b"\n%archive"
                if upload.mime_type == "application/pdf"
                else None,
                history=[_created(number, title, text)],
                versions=[_root_version(number, upload.content)],
            )
        )
        task.status = "success"
        task.document_ids = (number,)
        return task.task_id

    def _values(self, upload: DocumentUpload) -> dict[int, JsonValue]:
        """The custom field values of an upload, checked like the form serializer does."""
        fields = {f.id: f for f in self.state.custom_fields}
        documents = {d.id for d in self.state.documents}
        stored: dict[int, JsonValue] = {}
        for item in upload.custom_fields:
            if item.field not in fields:
                reject("custom_fields", f'Invalid pk "{item.field}" - object does not exist.')
            try:
                stored[item.field] = coerce(fields[item.field], item.value, documents)
            except ValueError as error:
                reject("custom_fields", str(error))
        return stored

    def _changes(self, document: FakeDocument, patch: DocumentPatch) -> dict[str, object]:
        """The attributes `patch` sets, validated like the serializer; nothing is applied."""
        state = self.state
        exists: dict[str, set[int]] = {
            "correspondent": {o.id for o in state.correspondents},
            "document_type": {o.id for o in state.document_types},
            "storage_path": {o.id for o in state.storage_paths},
            "owner": {u.id for u in state.users},
        }
        changes: dict[str, object] = {}
        for name in patch.model_fields_set - {"tags", "custom_fields"}:
            value = cast("object", getattr(patch, name))
            if name in exists and value is not None and value not in exists[name]:
                reject(name, f'Invalid pk "{value}" - object does not exist.')
            changes[name] = value
        self._check_title_and_number(document, changes)
        if patch.tags is not None:
            known = {t.id: t for t in state.tags}
            for tag in patch.tags:
                if tag not in known:
                    reject("tags", f'Invalid pk "{tag}" - object does not exist.')
            changes["tags"] = tag_closure(document.tags, list(patch.tags), known)
        if patch.custom_fields is not None:
            changes["custom_fields"] = self._custom_fields(patch)
        return changes

    def _check_title_and_number(self, document: FakeDocument, changes: dict[str, object]) -> None:
        title = changes.get("title")
        if isinstance(title, str) and len(title) > MAX_TITLE:
            reject("title", f"Ensure this field has no more than {MAX_TITLE} characters.")
        number = changes.get("archive_serial_number")
        if isinstance(number, int):
            if number < 0:
                reject("archive_serial_number", "Ensure this value is greater than or equal to 0.")
            taken = (
                d.archive_serial_number == number and d is not document
                for d in self.state.documents
            )
            if any(taken):
                message = "document with this archive serial number already exists."
                reject("archive_serial_number", message)

    def _mirror_links(self, document: FakeDocument, new: dict[int, JsonValue]) -> None:
        """Keep document links symmetrical like Paperless: link and unlink the other side.

        Linking needs change permission on every document linked to (403 otherwise, also for a
        document the user cannot see); the other side keeps an empty list when unlinked.
        """
        link_fields = [f.id for f in self.state.custom_fields if f.data_type == "documentlink"]

        def targets(fields: dict[int, JsonValue], number: int) -> set[int]:
            return set(cast("list[int]", fields.get(number) or []))

        by_id = {d.id: d for d in self.state.documents}
        added = {n: targets(new, n) - targets(document.custom_fields, n) for n in link_fields}
        if any(not self.user.may_change(by_id[t]) for ids in added.values() for t in ids):
            message = "Insufficient permissions."
            raise PermissionDenied(message)
        for number in link_fields:
            for target in added[number]:
                other = by_id[target].custom_fields
                other[number] = [*cast("list[int]", other.get(number) or []), document.id]
            for target in targets(document.custom_fields, number) - targets(new, number):
                if target in by_id:
                    other = by_id[target].custom_fields
                    other[number] = [
                        n for n in cast("list[int]", other.get(number)) if n != document.id
                    ]

    def _custom_fields(self, patch: DocumentPatch) -> dict[int, JsonValue]:
        """The new custom field values; a field named twice keeps its last value."""
        fields = {f.id: f for f in self.state.custom_fields}
        documents = {d.id for d in self.state.documents}
        stored: dict[int, JsonValue] = {}
        for index, item in enumerate(patch.custom_fields or ()):
            if item.field not in fields:
                where = f"custom_fields.{index}.field"
                reject(where, f'Invalid pk "{item.field}" - object does not exist.')
            try:
                stored[item.field] = coerce(fields[item.field], item.value, documents)
            except ValueError as error:
                where = f"custom_fields.{index}.non_field_errors"
                reject(where, str(error))
        return stored

    @write
    async def create_tag(self, name: str) -> TaxonomyItem:
        """`TaxonomyWriter.create_tag` (TagViewSet.create): owned by the creator.

        Raises:
            PermissionDenied: The user lacks the global `add_tag` permission.
            Validation: A blank or too long name, or the creator has a tag of that name.
        """
        if not self.user.has("add_tag"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if not name.strip():
            reject("name", "This field may not be blank.")
        if len(name) > MAX_TITLE:
            reject("name", f"Ensure this field has no more than {MAX_TITLE} characters.")
        if any(t.name == name and t.owner == self.user.id for t in self.state.tags):
            reject("error", "Object violates owner / name unique constraint")
        tag = FakeTag(id=max((t.id for t in self.state.tags), default=0) + 1, name=name)
        tag.owner = self.user.id
        self.state.tags.append(tag)
        return TaxonomyItem(id=tag.id, name=tag.name, document_count=0, colour=tag.colour)

    @write
    async def add_note(self, document_id: int, text: str) -> Note:
        """`DocumentWriter.add_note` (the `notes` action, POST).

        Order in Paperless 3.4.2: the global `view_document`, `add_note` and `change_document`
        permissions (403; live: a change grant on the document does not replace the last),
        the document (404), the object view (403 for a hidden document) and the object change
        permission (403: a viewer may not add notes). Empty and blank text is accepted.
        """
        document = self._sub(document_id, "add_note", "change_document")
        if not document.changeable_by(self.user):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        state = self.state
        newest = max((n.id for d in state.documents for n in d.notes), default=0)
        state.last_note_id = max(state.last_note_id, newest) + 1
        note = Note(
            id=state.last_note_id, note=text, created=datetime.now(UTC), user=self.user.username
        )
        document.notes.append(note)
        return note

    @write
    async def delete_note(self, document_id: int, note_id: int) -> None:
        """`DocumentWriter.delete_note` (the `notes` action, DELETE ?id=).

        Same order as `add_note` with `delete_note` as the global permission; then the note
        must be one of this document (404).
        """
        try:
            document = self._sub(document_id, "delete_note", "change_document")
        except NotFound:
            raise unseen_note(document_id, note_id) from None  # the same 404 for both
        if not document.changeable_by(self.user):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if note_id not in {n.id for n in document.notes}:
            raise unseen_note(document_id, note_id)
        document.notes = [n for n in document.notes if n.id != note_id]

    @write
    async def delete_document(self, document_id: int) -> None:
        """`DocumentWriter.delete_document` (DocumentViewSet.destroy: soft delete to the trash).

        Order in Paperless 3.4.2: the global `delete_document` permission (403, `view_document`
        is not needed), the document among those the user may see (404), then the object: only
        an owner, a superuser or anyone for an unowned document may delete it (403: a view or
        change grant is not enough).
        """
        if not self.user.has("delete_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        document = self._document(document_id)
        if not document.visible_to(self.user):
            raise unseen_document(document_id)
        if not self.user.may_change_object(document):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        self.state.documents.remove(document)
        self.state.trash.append(document)

    def _next_id(self) -> int:
        """The next free id: versions share the id space of documents (trash included)."""
        state = self.state
        used = {d.id for d in (*state.documents, *state.trash)}
        used |= {v.id for d in (*state.documents, *state.trash) for v in d.versions}
        return max(used, default=0) + 1

    def _denied(self) -> PermissionDenied:
        return PermissionDenied("The Paperless user may not perform this action.")

    def _versioned(self, document_id: int, *global_permissions: str) -> FakeDocument:
        """The document for a change of its versions, in Paperless 3.4.2's order of checks.

        The global permissions the HTTP method needs (403), the document, which only has to exist
        (404: a hidden one is a 403 below, not a 404), then `has_perms_owner_aware`: the owner,
        a superuser, anyone for an unowned document, or a user with a change grant (403).
        """
        for needed in global_permissions:
            if not self.user.has(needed):
                raise self._denied()
        document = self._document(document_id)
        if not document.changeable_by(self.user):
            raise self._denied()
        return document

    @staticmethod
    def _show(document: FakeDocument, file: FakeFile) -> None:
        """Make `file` the file and text the document shows (its newest version's)."""
        document.original, document.archive, document.content = (
            file.original,
            file.archive,
            file.content,
        )
        document.modified = datetime.now(UTC)

    @staticmethod
    def _push(
        document: FakeDocument, version_id: int, file: FakeFile, label: str | None, now: datetime
    ) -> None:
        """Add `file` as the newest version `version_id`; the root's file is kept on the first."""
        if not document.version_files:
            document.version_files[document.id] = FakeFile(
                document.original, document.archive, document.content
            )
        document.version_files[version_id] = file
        document.versions.insert(
            0,
            DocumentVersion(
                id=version_id,
                added=now,
                version_label=label,
                is_root=False,
                checksum=hashlib.sha256(file.original).hexdigest(),
            ),
        )
        FakePaperless._show(document, file)

    @write
    async def upload_document_version(self, document_id: int, upload: VersionUpload) -> str:
        """`DocumentWriter.upload_document_version` (the `update_version` action).

        Like `upload_document`, consumption is immediate: the task names the new version, which
        becomes the newest. The document keeps its metadata, its MIME type and its first file
        name. Probed on 3.4.2: a file type it cannot consume and a label over 64 characters are
        400s; a blank label is stored as none; the same content again is a second version.

        Deferred (not modelled): `update_version` on a version id (Paperless resolves it to the
        root), and a restore from the trash re-attaching a trashed version (live: it comes back as
        a version of its root; here the trash entry stays a stub).

        Order: the global `add_document` permission (403, the HTTP method's), the form (400), the
        document (404), then `change_document` and the object (403, see `_versioned`).

        Raises:
            PermissionDenied: See above.
            Validation: Paperless's 400 for the form, with its message.
        """
        if not self.user.has("add_document"):
            raise self._denied()
        if upload.mime_type not in CONSUMABLE:
            reject("document", f"File type {upload.mime_type} not supported")
        if upload.label is not None and len(upload.label) > MAX_LABEL:
            reject("version_label", f"Ensure this field has no more than {MAX_LABEL} characters.")
        document = self._versioned(document_id, "change_document")
        now = datetime.now(UTC)
        text = (
            upload.content.decode(errors="replace") if upload.mime_type.startswith("text/") else ""
        )
        archive = upload.content + b"\n%archive" if upload.mime_type == "application/pdf" else None
        version_id = self._next_id()
        file = FakeFile(upload.content, archive, text)
        self._push(document, version_id, file, upload.label or None, now)
        task = FakeTask(
            id=len(self.state.tasks) + 1,
            task_id=str(uuid.uuid4()),
            created=now,
            finished=now,
            status="success",
            document_ids=(version_id,),
        )
        self.state.tasks.append(task)
        return task.task_id

    @write
    async def set_version_label(
        self, document_id: int, version_id: int, label: str | None
    ) -> DocumentVersion:
        """`DocumentWriter.set_version_label` (PATCH `versions/{id}/`); a blank label is none.

        Order: the global `change_document` permission (403), the label (400), the document (404),
        the object (403, see `_versioned`), the version (404).

        Raises:
            NotFound: The document has no such version (404 alike for both).
            PermissionDenied: See above.
            Validation: The label is longer than 64 characters.
        """
        if not self.user.has("change_document"):
            raise self._denied()
        if label is not None and len(label) > MAX_LABEL:
            reject("version_label", f"Ensure this field has no more than {MAX_LABEL} characters.")
        document = self._versioned(document_id)
        found = [v for v in document.versions if v.id == version_id]
        if not found:
            raise unseen_version(document_id, version_id)
        changed = found[0].model_copy(update={"version_label": label or None})
        document.versions = [changed if v.id == version_id else v for v in document.versions]
        return changed

    @write
    async def delete_version(self, document_id: int, version_id: int) -> int:
        """`DocumentWriter.delete_version` (DELETE `versions/{id}/`): a soft delete.

        The version row goes to the trash under its own id, and the document shows the newest
        version that is left (its file and text). Deleting needs the delete permission, not the
        change permission: the global `delete_document` (403), the document (404), then the
        owner, a superuser or anyone for an unowned document (403: a change grant is not enough),
        then the version (404) and not the root (400).

        Raises:
            NotFound: The document has no such version.
            PermissionDenied: See above.
            Validation: The version is the root.
        """
        if not self.user.has("delete_document"):
            raise self._denied()
        document = self._document(document_id)
        if not self.user.may_change_object(document):
            raise self._denied()
        found = [v for v in document.versions if v.id == version_id]
        if not found:
            raise unseen_version(document_id, version_id)
        if found[0].is_root:
            reject(
                "version", "Cannot delete the root/original version. Delete the document instead."
            )
        document.versions = [v for v in document.versions if v.id != version_id]
        gone = document.version_files.pop(version_id)
        self.state.trash.append(
            FakeDocument(
                id=version_id,
                title=document.title,
                owner=document.owner,
                versions=[found[0]],
                original=gone.original,
                archive=gone.archive,
                content=gone.content,
            )
        )
        current = document.versions[0]
        self._show(document, document.version_files[current.id])
        return current.id

    @write
    async def merge_as_versions(
        self, root_id: int, source_ids: Sequence[int], label: str | None
    ) -> None:
        """`DocumentWriter.merge_as_versions`: the sources vanish, their files become versions.

        Probed on 3.4.2: it merges at once; the sources are removed for good (no trash) and
        keep nothing but their file; the last source becomes the newest version; a source with
        versions, a version as source, a duplicate or an unknown id and a label with several
        sources are 400s. The form is checked first (it does not look at who may see what: a
        document the user may not see is a 403 below, not a missing one), then, for anyone but a
        superuser: the global `change_document` and a change
        grant or ownership of every document, ownership (or no owner) of every document, and the
        global `delete_document` (403), because the sources stop existing.

        Raises:
            PermissionDenied: See above.
            Validation: Paperless's 400, with its message.
        """
        state = self.state
        wanted = [root_id, *source_ids]
        seen = {d.id: d for d in state.documents}
        child_versions = {v.id for d in state.documents for v in d.versions if not v.is_root}
        if len(set(wanted)) != len(wanted) or not set(wanted) <= (seen.keys() | child_versions):
            reject("documents", "Some documents in documents don't exist or were specified twice.")
        if child_versions & set(wanted):
            reject("non_field_errors", "Only top-level documents can be merged as versions.")
        if label is not None and len(source_ids) != 1:
            reject(
                "non_field_errors",
                "version_label can only be used when merging one source document.",
            )
        if any(len(seen[n].versions) > 1 for n in source_ids):
            reject(
                "non_field_errors",
                "Documents with existing versions cannot be merged into another document.",
            )
        owned = all(seen[n].owner in {None, self.user.id} for n in wanted)
        changeable = all(seen[n].changeable_by(self.user) for n in wanted)
        if not self.user.is_superuser and not (
            self.user.has("change_document")
            and changeable
            and owned
            and self.user.has("delete_document")
        ):
            raise self._denied()
        root = seen[root_id]
        for number in source_ids:
            source = seen[number]
            file = FakeFile(source.original, source.archive, source.content)
            self._push(root, number, file, label, datetime.now(UTC))
            state.documents.remove(source)

    def _owned(self, kind: WritableKind) -> list[FakeTaxonomy]:
        """The objects of a kind with owners (everything but custom fields)."""
        state = self.state
        return cast(
            "list[FakeTaxonomy]",
            {
                "tag": state.tags,
                "correspondent": state.correspondents,
                "document_type": state.document_types,
                "storage_path": state.storage_paths,
            }[kind],
        )

    def _allowed(self, action: str, kind: WritableKind) -> None:
        """The global permission of an action on a kind: `add_tag`, `change_documenttype`, ..."""
        model = kind.replace("_", "")
        if not self.user.has(f"{action}_{model}"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)

    def _target(self, kind: WritableKind, item_id: int) -> FakeTaxonomy | FakeCustomField:
        """The object among those the user may see (`NotFound` otherwise), then may change."""
        if kind == "custom_field":
            found = [f for f in self.state.custom_fields if f.id == item_id]
        else:
            found = [o for o in self._owned(kind) if o.id == item_id and o.visible_to(self.user)]
        if not found:
            message = "The Paperless object does not exist or is not visible to this user."
            raise NotFound(message)
        if not isinstance(found[0], FakeCustomField) and not self.user.may_change_object(found[0]):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        return found[0]

    def _detail(self, kind: WritableKind, item_id: int) -> TaxonomyDetail:
        """The object as a write answers it: the list row plus the matching rule."""
        [row] = [r for r in self._rows(kind) if r.id == item_id]
        extras: dict[str, object] = {}
        if kind != "custom_field":
            [obj] = [o for o in self._owned(kind) if o.id == item_id]
            extras = {
                "matching_algorithm": obj.matching_algorithm,
                "match": obj.match,
                "case_insensitive": obj.case_insensitive,
            }
            if isinstance(obj, FakeTag):
                extras["is_inbox_tag"] = obj.is_inbox
            if isinstance(obj, FakeStoragePath):
                extras["path"] = obj.path
        return TaxonomyDetail.model_validate({**row.model_dump(), **extras})

    def _apply(self, obj: FakeTaxonomy, values: TaxonomyValues, *, creating: bool) -> None:
        """Set what `values` sets on `obj`, validated like the serializers (3.4.2)."""
        sent = values.model_fields_set
        if {"matching_algorithm", "match"} & sent:
            algorithm = values.matching_algorithm or obj.matching_algorithm
            check_match(algorithm, obj.match if values.match is None else values.match)
        if values.matching_algorithm is not None:
            obj.matching_algorithm = values.matching_algorithm
        if values.match is not None:
            obj.match = values.match
        if values.case_insensitive is not None:
            obj.case_insensitive = values.case_insensitive
        if isinstance(obj, FakeTag):
            self._apply_tag(obj, values)
        if isinstance(obj, FakeStoragePath):
            if values.path is None and creating:
                reject("path", "This field is required.")
            if values.path is not None:
                check_path(values.path)
                obj.path = values.path

    def _apply_tag(self, tag: FakeTag, values: TaxonomyValues) -> None:
        if values.colour is not None:
            check_colour(values.colour)
            tag.colour = values.colour
        if values.is_inbox_tag is not None:
            tag.is_inbox = values.is_inbox_tag
        if "parent" not in values.model_fields_set:
            return
        parent = values.parent
        if parent is not None:
            if parent == tag.id:
                reject("parent", "Cannot set itself as parent.")
            if parent not in {t.id for t in self.state.tags}:
                reject("parent", f'Invalid pk "{parent}" - object does not exist.')
            if parent in self._tag_tree(tag.id) - {tag.id}:
                reject("parent", "Cannot set parent to a descendant.")
        tag.parent = parent

    def _tag_tree(self, root: int) -> set[int]:
        """The tag and every tag below it."""
        found = {root}
        grew = True
        while grew:
            below = {t.id for t in self.state.tags if t.parent in found}
            grew = not below <= found
            found |= below
        return found

    @write
    async def create_taxonomy_item(
        self, kind: WritableKind, name: str, values: TaxonomyValues
    ) -> TaxonomyDetail:
        """`TaxonomyWriter.create_taxonomy_item` (the viewsets' create): owned by the creator.

        Raises:
            PermissionDenied: The user lacks the global add permission of the kind.
            Validation: Paperless's 400 for a value, with its message.
        """
        self._allowed("add", kind)
        name = check_name(name)
        if kind == "custom_field":
            return self._create_field(name, values)
        objects = self._owned(kind)
        if any(o.name == name and o.owner == self.user.id for o in objects):
            reject("error", "Object violates owner / name unique constraint")
        number = max((o.id for o in objects), default=0) + 1
        made = {"tag": FakeTag, "storage_path": FakeStoragePath}.get(kind, FakeTaxonomy)
        obj = made(id=number, name=name, owner=self.user.id, matching_algorithm="none")
        self._apply(obj, values, creating=True)
        objects.append(obj)
        return self._detail(kind, number)

    def _create_field(self, name: str, values: TaxonomyValues) -> TaxonomyDetail:
        if values.data_type is None:
            reject("data_type", "This field is required.")
        if any(f.name == name for f in self.state.custom_fields):
            reject("name", "custom field with this name already exists.")
        options = self._options(values.data_type, values)
        number = max((f.id for f in self.state.custom_fields), default=0) + 1
        self.state.custom_fields.append(
            FakeCustomField(
                id=number, name=name, data_type=values.data_type, select_options=options
            )
        )
        return self._detail("custom_field", number)

    @staticmethod
    def _options(data_type: str, values: TaxonomyValues) -> tuple[SelectOption, ...]:
        """The options a select field stores; a select field needs at least one."""
        if data_type != "select":
            return ()
        if not values.select_options:
            reject("error", "extra_data.select_options must be a valid list")
        return with_ids(values.select_options)

    @write
    async def update_taxonomy_item(
        self, kind: WritableKind, item_id: int, values: TaxonomyValues
    ) -> TaxonomyDetail:
        """`TaxonomyWriter.update_taxonomy_item` (the taxonomy viewsets' partial_update).

        Order in Paperless 3.4.2: the global change permission (403), the object among those
        the user may see (404), the object permission (403), then the values (400). A select
        field PATCHed without options is a 400 in Paperless; the adapter sends them along, so
        the fake keeps them when `values` leaves them out.

        Raises:
            NotFound: No such object, or the user may not see it.
            PermissionDenied: The user may not change objects of the kind, or this one.
            Validation: Paperless's 400 for a value, with its message.
        """
        self._allowed("change", kind)
        target = self._target(kind, item_id)
        if isinstance(target, FakeCustomField):
            self._update_field(target, values)
            return self._detail(kind, item_id)
        name = target.name
        if values.name is not None:
            name = check_name(values.name)
            clash = (o for o in self._owned(kind) if o.name == name and o.owner == target.owner)
            if any(o is not target for o in clash):
                reject("non_field_errors", "The fields name, owner must make a unique set.")
        self._apply(target, values, creating=False)
        target.name = name
        return self._detail(kind, item_id)

    def _update_field(self, field: FakeCustomField, values: TaxonomyValues) -> None:
        name = field.name if values.name is None else check_name(values.name)
        if any(f.name == name and f.id != field.id for f in self.state.custom_fields):
            reject("name", "custom field with this name already exists.")
        data_type = values.data_type or field.data_type
        options = field.select_options
        if values.select_options is not None or data_type != field.data_type:
            options = self._options(data_type, values)
        fields = self.state.custom_fields
        fields[fields.index(field)] = replace(
            field, name=name, data_type=data_type, select_options=options
        )
        if field.data_type == "select" and data_type == "select":
            # Paperless does this asynchronously (Celery task process_cf_select_update after the
            # save); the fake does it at once.
            kept = {o.id for o in options}
            for document in self.state.documents:
                held = document.custom_fields.get(field.id)
                if held is not None and held not in kept:
                    document.custom_fields[field.id] = None

    @write
    async def delete_taxonomy_item(self, kind: WritableKind, item_id: int) -> None:
        """`TaxonomyWriter.delete_taxonomy_item` (the taxonomy viewsets' destroy).

        Documents lose what refers to the object; a tag takes its descendants with it.

        Raises:
            NotFound: No such object, or the user may not see it.
            PermissionDenied: The user may not delete objects of the kind, or this one.
        """
        self._allowed("delete", kind)
        _ = self._target(kind, item_id)
        self._remove(kind, item_id)

    def _remove(self, kind: WritableKind, item_id: int) -> None:
        """Delete the object (if it still exists): documents lose it, a tag takes its children."""
        state = self.state
        if kind == "custom_field":
            state.custom_fields[:] = [f for f in state.custom_fields if f.id != item_id]
            for document in state.documents:
                _ = document.custom_fields.pop(item_id, None)
            return
        if kind == "tag":
            gone = self._tag_tree(item_id)
            state.tags[:] = [t for t in state.tags if t.id not in gone]
            for document in state.documents:
                document.tags = [t for t in document.tags if t not in gone]
            return
        self._owned(kind)[:] = [o for o in self._owned(kind) if o.id != item_id]
        for document in state.documents:
            if kind == "correspondent" and document.correspondent == item_id:
                document.correspondent = None
            if kind == "document_type" and document.document_type == item_id:
                document.document_type = None
            if kind == "storage_path" and document.storage_path == item_id:
                document.storage_path = None

    # --- BulkWriter

    @write
    async def bulk_edit_documents(
        self, document_ids: Sequence[int], change: DocumentChange
    ) -> None:
        """`BulkWriter.bulk_edit_documents` (BulkEditView).

        Order in Paperless 3.4.2: the request is validated as a whole (400: unknown or repeated
        documents, unknown tags, references or custom fields, wrong values), then permissions
        (403: the global `change_document`, change permission on every document, hidden ones
        included; a deletion also needs ownership and `delete_document`), then everything is
        applied. An empty selection is accepted.
        """
        state = self.state
        known = {d.id: d for d in state.documents}
        if len(set(document_ids)) != len(document_ids) or not set(document_ids) <= known.keys():
            reject("documents", "Some documents in documents don't exist or were specified twice.")
        documents = [known[number] for number in document_ids]
        apply = self._plan(change)
        user = self.user
        if not user.is_superuser:
            allowed = user.has("change_document") and all(d.changeable_by(user) for d in documents)
            if allowed and isinstance(change, TrashDocuments):
                owns = all(d.owner in {None, user.id} for d in documents)
                allowed = owns and user.has("delete_document")
            if not allowed:
                raise documents_refused()
        apply(documents)

    def _plan(self, change: DocumentChange) -> Callable[[list[FakeDocument]], None]:
        """Validate ``change`` like the serializer and return what applies it to documents."""
        state = self.state
        if isinstance(change, TagsChange):
            known = {t.id for t in state.tags}
            for where, wanted in (("add_tags", change.add), ("remove_tags", change.remove)):
                if not set(wanted) <= known:
                    message = (
                        f"Paperless rejected the request: Some tags in {where} "
                        "don't exist or were specified twice."
                    )
                    raise Validation(message)
            return lambda docs: self._tags(docs, change)
        if isinstance(change, ReferenceChange):
            table = {
                "correspondent": state.correspondents,
                "document_type": state.document_types,
                "storage_path": state.storage_paths,
            }[change.field]
            if change.value is not None and change.value not in {o.id for o in table}:
                label = change.field.replace("_", " ").capitalize()
                message = f"Paperless rejected the request: {label} does not exist"
                raise Validation(message)
            return lambda docs: self._reference(docs, change)
        if isinstance(change, FieldsChange):
            return self._plan_fields(change)
        if isinstance(change, TrashDocuments):
            return self._trash
        return lambda docs: state.reprocessed.extend(d.id for d in docs)

    @staticmethod
    def _reference(documents: list[FakeDocument], change: ReferenceChange) -> None:
        for document in documents:
            document.__dict__[change.field] = change.value

    def _tags(self, documents: list[FakeDocument], change: TagsChange) -> None:
        """Remove (with the descendants), then add (with the ancestors)."""
        tags = {t.id: t for t in self.state.tags}
        remove = {n for tag in change.remove for n in self._tag_tree(tag)}
        add: list[int] = []
        for tag in change.add:
            step: int | None = tag
            while step is not None and step not in add:
                add.append(step)
                step = tags[step].parent
        for document in documents:
            kept = [t for t in document.tags if t not in remove]
            document.tags = kept + [t for t in add if t not in kept]

    def _checked_values(
        self, change: FieldsChange, fields: dict[int, FakeCustomField]
    ) -> dict[int, JsonValue]:
        """The values in stored form, after the serializer's checks (400)."""
        documents = {d.id for d in self.state.documents}
        for where, wanted in (
            ("add_custom_fields", change.values),
            ("remove_custom_fields", change.remove),
        ):
            if not set(wanted) <= fields.keys():
                message = (
                    f"Paperless rejected the request: Some custom fields in {where} "
                    "don't exist or were specified twice."
                )
                raise Validation(message)
        stored: dict[int, JsonValue] = {}
        for number, value in change.values.items():
            try:
                stored[number] = coerce(fields[number], value, documents)
            except ValueError as error:
                reject(f"add_custom_fields.{number}.non_field_errors", str(error))

        return stored

    def _plan_fields(self, change: FieldsChange) -> Callable[[list[FakeDocument]], None]:
        fields = {f.id: f for f in self.state.custom_fields}
        stored = self._checked_values(change, fields)
        links = {n for n, f in fields.items() if f.data_type == "documentlink"}
        user = self.user
        if not user.is_superuser:
            # The serializer checks the linked documents too: change permission on all of them.
            by_id = {d.id: d for d in self.state.documents}
            for number in links & stored.keys():
                if any(not by_id[t].changeable_by(user) for t in _ids(stored[number])):
                    raise documents_refused()

        return lambda selected: self._set_fields(selected, stored, links, change.remove)

    def _set_fields(
        self,
        selected: list[FakeDocument],
        stored: dict[int, JsonValue],
        links: set[int],
        removed: tuple[int, ...],
    ) -> None:
        for document in selected:
            for number, value in stored.items():
                if number in links and document.id in _ids(value):
                    continue  # Paperless skips the field of a document that links itself
                document.custom_fields[number] = value
                if number in links:
                    self._link_back(document, number, _ids(value))
            for number in removed:
                old = document.custom_fields.pop(number, None)
                if number in links:
                    self._unlink_back(document, number, _ids(old))

    def _link_back(self, document: FakeDocument, number: int, targets: list[int]) -> None:
        """Add the reverse link on the targets; 3.4.2 never takes one from an old target."""
        by_id = {d.id: d for d in self.state.documents}
        for target in targets:
            other = by_id[target].custom_fields
            back = _ids(other.get(number))
            if document.id not in back:
                other[number] = [*back, document.id]

    def _unlink_back(self, document: FakeDocument, number: int, targets: list[int]) -> None:
        """Take the reverse link from the targets of a removed field."""
        by_id = {d.id: d for d in self.state.documents}
        for target in targets:
            if target in by_id and number in by_id[target].custom_fields:
                other = by_id[target].custom_fields
                other[number] = [n for n in _ids(other[number]) if n != document.id]

    def _trash(self, documents: list[FakeDocument]) -> None:
        for document in documents:
            self.state.documents.remove(document)
            self.state.trash.append(document)

    def _bulk_items(
        self, kind: BulkKind, item_ids: Sequence[int], action: str
    ) -> list[FakeTaxonomy]:
        """The selected objects, after validating the selection (400) and the permission (403)."""
        rows = {o.id: o for o in self._owned(kind)}
        if not item_ids:
            message = "Paperless rejected the request: objects must not be empty"
            raise Validation(message)
        if len(set(item_ids)) != len(item_ids) or not set(item_ids) <= rows.keys():
            message = (
                "Paperless rejected the request: "
                "Some ids in objects don't exist or were specified twice."
            )
            raise Validation(message)
        selected = [rows[number] for number in item_ids]
        user = self.user
        if not user.is_superuser:
            model = kind.replace("_", "")
            owns = all(o.owner in {None, user.id} for o in selected)
            if not (user.has(f"{action}_{model}") and owns):
                raise objects_refused()
        return selected

    @write
    async def delete_taxonomy_items(self, kind: BulkKind, item_ids: Sequence[int]) -> None:
        """`BulkWriter.delete_taxonomy_items` (BulkEditObjectsView, operation delete).

        400 for an empty, unknown or repeated selection; 403 without the global `delete_*`
        permission or for an object another user owns (hidden ones included).
        """
        _ = self._bulk_items(kind, item_ids, "delete")
        for number in item_ids:
            self._remove(kind, number)

    @write
    async def set_taxonomy_permissions(
        self, kind: BulkKind, item_ids: Sequence[int], change: PermissionsChange
    ) -> None:
        """`BulkWriter.set_taxonomy_permissions` (BulkEditObjectsView, set_permissions).

        Same checks as a deletion with `change_*`, and 400 for an unknown or repeated user or
        group. Without ``merge`` the grants replace all existing ones. An owner is set when the
        request carries one (`null` removes it), except that ``merge`` only sets it on objects
        without an owner and never removes one.
        """
        state = self.state
        given = change.permissions
        if given is not None:
            lists = {
                "users": [*given.view_users, *given.change_users],
                "groups": [*given.view_groups, *given.change_groups],
            }
            known = {"users": {u.id for u in state.users}, "groups": {g.id for g in state.groups}}
            for name, wanted in lists.items():
                if not set(wanted) <= known[name]:
                    message = (
                        f"Paperless rejected the request: Some {name} in "
                        "don't exist or were specified twice."
                    )
                    raise Validation(message)
        if change.owner is not None and change.owner not in {u.id for u in state.users}:
            reject("owner", f'Invalid pk "{change.owner}" - object does not exist.')
        for item in self._bulk_items(kind, item_ids, "change"):
            if "owner" in change.model_fields_set and (
                not change.merge or (change.owner is not None and item.owner is None)
            ):
                item.owner = change.owner
            if given is not None:
                self._grant(item, given, merge=change.merge)

    @staticmethod
    def _grant(item: Owned, given: ObjectPermissions, *, merge: bool) -> None:
        """Replace the grants of ``item`` with ``given``, or add ``given`` to them.

        Granting change also grants view (3.4.2 `set_permissions_for_objects`).
        """
        views = set(given.view_users) | set(given.change_users)
        changes = set(given.change_users)
        view_groups = set(given.view_groups) | set(given.change_groups)
        change_groups = set(given.change_groups)
        if merge:
            views, changes = views | item.viewers, changes | item.editors
            view_groups, change_groups = (
                view_groups | item.view_groups,
                change_groups | item.change_groups,
            )
        item.viewers, item.editors = views, changes
        item.view_groups, item.change_groups = view_groups, change_groups

    async def get_document_content(self, document_id: int) -> str:
        """`DocumentReader.get_document_content` (retrieve with `fields=content`)."""
        return self._seen(document_id).content

    async def list_document_notes(self, document_id: int) -> tuple[Note, ...]:
        """`DocumentReader.list_document_notes` (the `notes` action: `view_note` is required)."""
        return tuple(self._sub(document_id, "view_note").notes)

    async def list_document_history(self, document_id: int) -> tuple[HistoryEntry, ...]:
        """`DocumentReader.list_document_history` (the `history` action).

        Order in Paperless 3.4.2: `view_document` (viewset), audit log enabled (400), the
        document exists (404, even without `view_logentry`), then `view_logentry` and ownership:
        an object-level view grant is not enough, the document must be unowned, the user's own,
        or the user a superuser (403). The create entry's `content` (OCR text) is dropped.
        """
        user = self.user
        if not user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if not self.state.audit_log_enabled:
            message = "The Paperless audit log is disabled on this instance."
            raise FeatureUnavailable(message, hint="Leave history out of include.")
        document = self._document(document_id)
        owns = document.owner in {None, user.id} or user.is_superuser
        if not (user.has("view_logentry") and owns):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        entries = document.history
        return tuple(
            entry.model_copy(
                update={
                    "changes": {k: v for k, v in entry.changes.items() if k != "content"},
                }
            )
            for entry in entries
        )

    async def get_document_metadata(self, document_id: int) -> FileMetadata:
        """`DocumentReader.get_document_metadata` (the `metadata` action)."""
        document = self._sub(document_id)
        return FileMetadata(
            original_filename=document.original_filename,
            original_mime_type=document.mime_type,
            original_size=len(document.original),
            original_checksum=f"{document.id:064x}",
            media_filename=f"e2e/2026/{document.original_filename}",
            has_archive_version=document.archive is not None,
            archive_size=None if document.archive is None else len(document.archive),
            archive_checksum=None if document.archive is None else f"{document.id + 1000:064x}",
            archive_media_filename=(
                None if document.archive is None else f"e2e/2026/{document.original_filename}"
            ),
        )

    async def get_document_thumbnail(self, document_id: int) -> DocumentFile:
        """`DocumentReader.get_document_thumbnail` (the `thumb` action: WebP, numbered name)."""
        document = self._sub(document_id)
        return DocumentFile(
            content=document.thumbnail,
            mime_type="image/webp",
            filename=f"{document.id:07d}.webp",
        )

    async def get_document_file(
        self, document_id: int, version: FileVersion, *, max_bytes: int
    ) -> DocumentFile:
        """`DocumentReader.get_document_file` (the `download` action).

        Without an archive version `archive` answers the original, as Paperless does.
        """
        document = self._sub(document_id)
        archive = version == "archive" and document.archive is not None
        mime_type = document.mime_type
        if document.mime_type.startswith("text/"):
            mime_type += "; charset=utf-8"
        content = document.archive if archive and document.archive else document.original
        return DocumentFile(
            content=b"" if len(content) > max_bytes else content,
            mime_type="application/pdf" if archive else mime_type,
            filename=document.download_filename(archive=archive),
            truncated=len(content) > max_bytes,
        )

    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        """`DocumentReader.get_document_titles` (the list view filtered by `id__in`)."""
        if not self.user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        wanted = set(document_ids)
        return {
            d.id: d.title
            for d in self.state.documents
            if d.id in wanted and d.visible_to(self.user)
        }

    async def get_document_suggestions(self, document_id: int) -> Suggestions:
        """`DocumentReader.get_document_suggestions` (the `suggestions` action)."""
        return self._sub(document_id).suggestions

    async def ask(self, question: str, *, document_id: int | None) -> ChatAnswer:
        """`AiReader.ask` (ChatStreamingView), answering from the documents that match.

        Paperless checks the global `view_document` permission, then that AI is on, then the
        document (unknown: 400, hidden: 403). The language model is replaced by a lookup: the
        documents (at most three) that share a word with the question are the sources, and the
        answer lists their titles; none match, Paperless's no-content sentence.
        """
        if not self.user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if not self.state.ai_enabled:
            raise ai_disabled()
        if document_id is None:
            candidates = [d for d in self.state.documents if d.visible_to(self.user)]
        else:
            candidates = [self._sub(document_id)]
        words = _words(question)
        found = [d for d in candidates if words & _words(f"{d.title} {d.content}")][:3]
        if not found:
            return ChatAnswer(answer=CHAT_NO_CONTENT, truncated=False, sources=())
        titles = ", ".join(d.title for d in found)
        return ChatAnswer(
            answer=f"The documents that match are: {titles}.",
            truncated=False,
            sources=tuple(ChatSource(id=d.id, title=d.title) for d in found),
        )

    async def get_ai_suggestions(self, document_id: int) -> AiSuggestions:
        """`AiReader.get_ai_suggestions` (the `ai_suggestions` action).

        Paperless needs `change_document` on the document (403) before it looks at AI (400):
        the owner, anyone for an unowned document, or a user with a change grant.
        """
        document = self._sub(document_id)
        if not document.changeable_by(self.user):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if not self.state.ai_enabled:
            raise ai_disabled()
        return document.ai_suggestions


type _Match = Callable[[FakeDocument], bool]


def _filters(query: DocumentQuery, state: FakeState) -> list[_Match]:
    """The tests of the structured filters `query` sets."""
    inbox = {tag.id for tag in state.tags if tag.is_inbox}
    checks: list[tuple[object, _Match]] = [
        (query.title_contains, lambda d: (query.title_contains or "").lower() in d.title.lower()),
        (query.correspondent, lambda d: d.correspondent == query.correspondent),
        (query.document_type, lambda d: d.document_type == query.document_type),
        (query.storage_path, lambda d: d.storage_path == query.storage_path),
        (query.tags_all, lambda d: set(query.tags_all) <= set(d.tags)),
        (query.tags_any, lambda d: bool(set(query.tags_any) & set(d.tags))),
        (query.tags_none, lambda d: not set(query.tags_none) & set(d.tags)),
        (
            query.created_from,
            lambda d: query.created_from is None or d.created >= query.created_from,
        ),
        (query.created_to, lambda d: query.created_to is None or d.created <= query.created_to),
        (
            query.added_from,
            lambda d: query.added_from is None or d.added.date() >= query.added_from,
        ),
        (query.added_to, lambda d: query.added_to is None or d.added.date() <= query.added_to),
        (query.owner, lambda d: d.owner == query.owner),
        (query.inbox, lambda d: bool(inbox & set(d.tags)) == query.inbox),
        (query.ids, lambda d: d.id in query.ids),
    ]
    return [match for value, match in checks if value not in {None, ()}]


# Words Tantivy reads as operators, not as terms.
TANTIVY_OPERATORS = frozenset({"and", "or", "not"})


def _words(text: str) -> set[str]:
    return {word.lower() for word in cast("list[str]", re.findall(r"\w+", text))}


def _highlight(content: str, terms: set[str]) -> str | None:
    marked = re.sub(r"\w+", lambda m: f"<b>{m[0]}</b>" if m[0].lower() in terms else m[0], content)
    return marked if marked != content else None


def _ordered(
    documents: list[FakeDocument], ordering: str | None, *, ranked: object
) -> list[FakeDocument]:
    if ordering is None:
        return (
            documents
            if ranked
            else sorted(documents, key=lambda d: (d.created, d.id), reverse=True)
        )
    name = ordering.removeprefix("-")

    def key(document: FakeDocument) -> tuple[bool, SupportsRichComparison, int]:
        # The native value, so numbers sort as numbers (ASN 2 before 10); None sorts last.
        value = cast("SupportsRichComparison | None", getattr(document, name))
        return (value is None, 0 if value is None else value, document.id)

    return sorted(documents, key=key, reverse=ordering.startswith("-"))


def _record(
    document: FakeDocument, user: FakeUser, *, report_change: bool = True
) -> DocumentRecord:
    return DocumentRecord(
        id=document.id,
        title=document.title,
        created=document.created,
        added=document.added,
        modified=document.modified,
        archive_serial_number=document.archive_serial_number,
        owner=document.owner,
        correspondent=document.correspondent,
        document_type=document.document_type,
        storage_path=document.storage_path,
        tags=tuple(document.tags),
        custom_fields=tuple(
            CustomFieldValue(field=number, value=value)
            for number, value in document.custom_fields.items()
        ),
        original_file_name=document.original_filename,
        mime_type=document.mime_type,
        page_count=document.page_count,
        notes_count=len(document.notes),
        versions=tuple(document.versions),
        user_can_change=document.changeable_by(user) if report_change else None,
    )


def _ids(value: JsonValue) -> list[int]:
    """The document ids of a document-link value (nothing for any other value)."""
    return [n for n in value if isinstance(n, int)] if isinstance(value, list) else []


def _hit(document: FakeDocument, user: FakeUser, highlights: str | None) -> DocumentHit:
    return DocumentHit(
        id=document.id,
        title=document.title,
        created=document.created,
        added=document.added,
        correspondent=document.correspondent,
        document_type=document.document_type,
        storage_path=document.storage_path,
        tags=tuple(document.tags),
        custom_fields=tuple(
            CustomFieldValue(field=number, value=value)
            for number, value in document.custom_fields.items()
        ),
        highlights=highlights,
        user_can_change=document.changeable_by(user),
    )
