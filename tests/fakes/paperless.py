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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Concatenate

from fakes.accounts import ADMIN, READER, READER_PERMISSIONS
from paperless_mcp.domain.errors import PermissionDenied
from paperless_mcp.domain.instance import (
    ComponentStatus,
    CurrentUser,
    InstanceOverview,
    InstanceStatistics,
    SystemStatus,
)
from paperless_mcp.domain.taxonomy import TaxonomyItem

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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
    select_options: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class FakeGroup:
    """A user group."""

    id: int
    name: str


@dataclass(kw_only=True)
class FakeDocument(Owned):
    """A document."""

    title: str
    tags: list[int] = field(default_factory=list[int])
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    custom_fields: list[int] = field(default_factory=list[int])


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
    system_status: SystemStatus = HEALTHY
    ai_enabled: bool = False


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


def seeded() -> FakeState:
    """A fresh state with the objects and users of the E2E seed."""
    admin = FakeUser(id=2, username=ADMIN[0], is_superuser=True, is_staff=True)
    reader = FakeUser(id=3, username=READER[0], permissions=frozenset(READER_PERMISSIONS))
    fields = [
        FakeCustomField(id=number, name=f"e2e-{data_type}", data_type=data_type)
        for number, data_type in enumerate(CUSTOM_FIELD_TYPES, start=1)
    ]
    fields.append(
        FakeCustomField(
            id=len(fields) + 1,
            name="e2e-select",
            data_type="select",
            select_options=("alpha", "beta"),
        )
    )
    return FakeState(
        users=[admin, reader],
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
                tags=[1, 2, 4],
                correspondent=1,
                document_type=1,
                storage_path=1,
                custom_fields=[f.id for f in fields],
            ),
            FakeDocument(id=2, title="e2e-private-note", owner=admin.id, tags=[3]),
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
