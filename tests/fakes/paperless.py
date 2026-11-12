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
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
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
from paperless_mcp.domain.documents import (
    CustomFieldValue,
    DocumentHit,
    DocumentPage,
    DocumentQuery,
    DocumentRecord,
    DocumentVersion,
    FileMetadata,
    HistoryEntry,
    Note,
    Suggestions,
)
from paperless_mcp.domain.errors import FeatureUnavailable, PermissionDenied, Validation
from paperless_mcp.domain.instance import (
    ComponentStatus,
    CurrentUser,
    InstanceOverview,
    InstanceStatistics,
    SystemStatus,
)
from paperless_mcp.domain.tasks import TaskState, TaskStatus
from paperless_mcp.domain.taxonomy import SelectOption, TaxonomyItem
from paperless_mcp.paperless.documents import beyond_last_page, unseen_document
from paperless_mcp.paperless.tasks import not_found

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection

    from _typeshed import SupportsRichComparison

    from paperless_mcp.domain.taxonomy import TaxonomyKind

VERSION = "3.4.2"
API_VERSION = 10

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


@dataclass(kw_only=True)
class Owned:
    """An object with Paperless object permissions.

    Attributes:
        owner: The owning user's id; None means shared, visible to every user.
        viewers: Ids of users with an object-level view grant.
    """

    id: int
    owner: int | None = None
    viewers: set[int] = field(default_factory=set[int])

    def visible_to(self, user: FakeUser) -> bool:
        """Paperless's `permitted_object_ids` (group grants are not modelled yet)."""
        return user.is_superuser or self.owner in {None, user.id} or user.id in self.viewers


@dataclass(kw_only=True)
class FakeTaxonomy(Owned):
    """A correspondent, document type, storage path or saved view."""

    name: str


@dataclass(kw_only=True)
class FakeTag(FakeTaxonomy):
    """A tag; inbox tags are what the statistics count as the inbox."""

    is_inbox: bool = False
    colour: str = "#a6cee3"
    parent: int | None = None


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
    suggestions: Suggestions = field(default_factory=lambda: NO_SUGGESTIONS)

    @property
    def original_filename(self) -> str:
        """Paperless names the uploaded file after the title in the seed."""
        return f"{self.title}.pdf"


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
    storage_paths: list[FakeTaxonomy] = field(default_factory=list[FakeTaxonomy])
    custom_fields: list[FakeCustomField] = field(default_factory=list[FakeCustomField])
    saved_views: list[FakeTaxonomy] = field(default_factory=list[FakeTaxonomy])
    groups: list[FakeGroup] = field(default_factory=list[FakeGroup])
    tasks: list[FakeTask] = field(default_factory=list[FakeTask])
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


def _root_version(document_id: int) -> DocumentVersion:
    return DocumentVersion(
        id=document_id,
        added=datetime(2026, 11, 11, 19, 47, tzinfo=UTC),
        version_label=None,
        is_root=True,
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
        storage_paths=[FakeTaxonomy(id=1, name="e2e-archive")],
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
                history=[_created(2, "e2e-private-note", PRIVATE_CONTENT)],
                versions=[_root_version(2)],
            ),
        ],
    )


class WriteNotAllowedError(AssertionError):
    """A test wrote to the fake without opting in with `allow_writes=True`."""


_WRITES: set[object] = set()


def write[F: FakePaperless, **P, R](
    method: Callable[Concatenate[F, P], Awaitable[R]],
) -> Callable[Concatenate[F, P], Awaitable[R]]:
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
            objects: list[FakeTaxonomy], link: Callable[[FakeDocument], int | None]
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
            _hit(d, _highlight(d.content, terms) if query.query is not None else None)
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

    def _sub(self, document_id: int, permission: str | None = None) -> FakeDocument:
        """The document for a sub-resource.

        The global permission is checked first (403), then existence (404); a hidden document
        is 403, which differs from the detail view.
        """
        for needed in ("view_document", permission):
            if needed and not self.user.has(needed):
                message = "The Paperless user may not perform this action."
                raise PermissionDenied(message)
        document = self._document(document_id)
        if not document.visible_to(self.user):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        return document

    async def get_document(self, document_id: int) -> DocumentRecord:
        """`DocumentReader.get_document` (DocumentViewSet.retrieve, `fields=` without content)."""
        document = self._seen(document_id)
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
        )

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
            original_size=644,
            original_checksum=f"{document.id:064x}",
            media_filename=f"e2e/2026/{document.original_filename}",
            has_archive_version=True,
            archive_size=9138,
            archive_checksum=f"{document.id + 1000:064x}",
            archive_media_filename=f"e2e/2026/{document.original_filename}",
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


def _hit(document: FakeDocument, highlights: str | None) -> DocumentHit:
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
    )
