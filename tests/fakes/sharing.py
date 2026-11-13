"""The sharing part of FakePaperless: share links, bundles and e-mail, as Paperless 3.4.2 does.

Plain functions on the fake's state and user; `FakePaperless` delegates to them (and carries the
write guard), so `paperless.py` does not grow further.

- A link or bundle belongs to its creator; others see it only as a superuser or when it has no
  owner. Every action needs the global permission of the model (`view_sharelink`, ...).
- Creating one needs the documents to exist (400 otherwise) and be visible to the user (a link
  for a hidden document is a 403, a bundle's is a 400 "Documents not found"). Owning them is not
  required: probed on 3.4.2, a user with only a view grant can share a document (the docs say
  only owners can, which the API does not enforce).
- A bundle is `pending` when created or rebuilt and `ready` from its next read on: Paperless
  builds the archive in the background, a moment later.
- The fake has no mail server unless a test sets `data.email_enabled`, like the E2E stack:
  a valid message then fails the way Paperless 3.4.2 does (a 500, here `FeatureUnavailable`).
"""

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fakes.document_values import reject
from paperless_mcp.domain.errors import PermissionDenied
from paperless_mcp.domain.sharing import ShareBundle, ShareLink
from paperless_mcp.paperless.sharing import not_sent, unseen_bundle, unseen_link

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from paperless_mcp.domain.sharing import BundleStatus, FileVersionKind

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
SLUG_LENGTH = 50
MAX_ADDRESSES = 100


@dataclass(kw_only=True)
class FakeShareLink:
    """A share link."""

    id: int
    document: int
    owner: int | None
    slug: str = field(default_factory=lambda: secrets.token_urlsafe(SLUG_LENGTH)[:SLUG_LENGTH])
    created: datetime = NOW
    expiration: datetime | None = None
    file_version: FileVersionKind = "archive"


@dataclass(kw_only=True)
class FakeShareBundle:
    """A share-link bundle; `status` turns from pending to ready when it is next read."""

    id: int
    documents: tuple[int, ...]
    owner: int | None
    slug: str = field(default_factory=lambda: secrets.token_urlsafe(SLUG_LENGTH)[:SLUG_LENGTH])
    created: datetime = NOW
    expiration: datetime | None = None
    file_version: FileVersionKind = "archive"
    status: BundleStatus = "pending"
    last_error: str | None = None
    built_at: datetime | None = None


@dataclass(frozen=True)
class SentEmail:
    """An e-mail the fake sent: who got which documents' files, and the text."""

    documents: tuple[int, ...]
    addresses: tuple[str, ...]
    subject: str
    message: str
    use_archive_version: bool


@dataclass(frozen=True)
class Viewer:
    """The acting user and the documents as they see them (FakePaperless fills it in).

    Attributes:
        has: Django's `has_perm` for the user.
        titles: The title of every document, trash included (a link keeps its document).
        visible: Ids of the documents the user may see.
    """

    id: int
    is_superuser: bool
    has: Callable[[str], bool]
    titles: Mapping[int, str]
    visible: frozenset[int]


@dataclass(frozen=True)
class SharingData:
    """The sharing part of the fake's state (lists are shared with `FakeState`)."""

    links: list[FakeShareLink]
    bundles: list[FakeShareBundle]
    outbox: list[SentEmail]
    email_enabled: bool


def denied() -> PermissionDenied:
    """Paperless's 403."""
    return PermissionDenied("The Paperless user may not perform this action.")


def _allow(user: Viewer, action: str, model: str) -> None:
    if not user.has(f"{action}_{model}"):
        raise denied()


def _visible(owner: int | None, user: Viewer) -> bool:
    return user.is_superuser or owner in {None, user.id}


def _link(row: FakeShareLink, user: Viewer) -> ShareLink:
    return ShareLink(
        id=row.id,
        document_id=row.document,
        document_title=user.titles[row.document],
        slug=row.slug,
        created=row.created,
        expiration=row.expiration,
        file_version=row.file_version,
    )


def _bundle(row: FakeShareBundle) -> ShareBundle:
    """The bundle as read; a pending build completes with this read."""
    shown = ShareBundle(
        id=row.id,
        slug=row.slug,
        created=row.created,
        expiration=row.expiration,
        file_version=row.file_version,
        status=row.status,
        size_bytes=None if row.status == "pending" else 1000 * len(row.documents),
        last_error=row.last_error,
        built_at=row.built_at,
        document_ids=tuple(sorted(row.documents)),
    )
    if row.status == "pending":
        row.status, row.built_at = "ready", NOW
    return shown


def _bundle_pending(row: FakeShareBundle) -> ShareBundle:
    """The bundle as the answer to a create or rebuild: still pending, its build not read yet."""
    return _bundle(row).model_copy(
        update={"status": "pending", "size_bytes": None, "built_at": None}
    )


def _own_link(data: SharingData, user: Viewer, link_id: int) -> FakeShareLink:
    found = [r for r in data.links if r.id == link_id and _visible(r.owner, user)]
    if not found:
        raise unseen_link(link_id)
    return found[0]


def _own_bundle(data: SharingData, user: Viewer, bundle_id: int) -> FakeShareBundle:
    found = [r for r in data.bundles if r.id == bundle_id and _visible(r.owner, user)]
    if not found:
        raise unseen_bundle(bundle_id)
    return found[0]


def _next(rows: Sequence[FakeShareLink] | Sequence[FakeShareBundle]) -> int:
    return max((r.id for r in rows), default=0) + 1


def list_links(data: SharingData, user: Viewer) -> tuple[ShareLink, ...]:
    """`SharingReader.list_share_links` (ShareLinkViewSet list)."""
    _allow(user, "view", "sharelink")
    return tuple(_link(r, user) for r in data.links if _visible(r.owner, user))


def get_link(data: SharingData, user: Viewer, link_id: int) -> ShareLink:
    """`SharingReader.get_share_link` (ShareLinkViewSet retrieve)."""
    _allow(user, "view", "sharelink")
    return _link(_own_link(data, user, link_id), user)


def list_bundles(data: SharingData, user: Viewer) -> tuple[ShareBundle, ...]:
    """`SharingReader.list_share_bundles` (ShareLinkBundleViewSet list)."""
    _allow(user, "view", "sharelinkbundle")
    return tuple(_bundle(r) for r in data.bundles if _visible(r.owner, user))


def get_bundle(data: SharingData, user: Viewer, bundle_id: int) -> ShareBundle:
    """`SharingReader.get_share_bundle` (ShareLinkBundleViewSet retrieve)."""
    _allow(user, "view", "sharelinkbundle")
    return _bundle(_own_bundle(data, user, bundle_id))


def create_link(
    data: SharingData,
    user: Viewer,
    document_id: int,
    expiration: datetime | None,
    file_version: FileVersionKind,
) -> ShareLink:
    """`SharingWriter.create_share_link` (ShareLinkViewSet create)."""
    _allow(user, "add", "sharelink")
    if document_id not in user.titles:
        reject("document", f'Invalid pk "{document_id}" - object does not exist.')
    if document_id not in user.visible:
        raise denied()
    row = FakeShareLink(
        id=_next(data.links),
        document=document_id,
        owner=user.id,
        expiration=expiration,
        file_version=file_version,
    )
    data.links.append(row)
    return _link(row, user)


def delete_link(data: SharingData, user: Viewer, link_id: int) -> None:
    """`SharingWriter.delete_share_link` (ShareLinkViewSet destroy)."""
    _allow(user, "delete", "sharelink")
    row = _own_link(data, user, link_id)
    if not user.is_superuser and row.owner != user.id:
        raise denied()
    data.links.remove(row)


def create_bundle(
    data: SharingData,
    user: Viewer,
    document_ids: Sequence[int],
    expiration_days: int | None,
    file_version: FileVersionKind,
) -> ShareBundle:
    """`SharingWriter.create_share_bundle` (ShareLinkBundleViewSet create); built afterwards."""
    _allow(user, "add", "sharelinkbundle")
    if not document_ids:
        reject("document_ids", "This list may not be empty.")
    if expiration_days is not None and expiration_days < 1:
        reject("expiration_days", "Ensure this value is greater than or equal to 1.")
    unknown = sorted(set(document_ids) - user.visible)
    if unknown:
        reject("document_ids", f"Documents not found: {', '.join(map(str, unknown))}")
    row = FakeShareBundle(
        id=_next(data.bundles),
        documents=tuple(sorted(set(document_ids))),
        owner=user.id,
        expiration=None if expiration_days is None else NOW + timedelta(days=expiration_days),
        file_version=file_version,
    )
    data.bundles.append(row)
    return _bundle_pending(row)


def rebuild_bundle(data: SharingData, user: Viewer, bundle_id: int) -> ShareBundle:
    """`SharingWriter.rebuild_share_bundle` (ShareLinkBundleViewSet rebuild)."""
    _allow(user, "change", "sharelinkbundle")
    row = _own_bundle(data, user, bundle_id)
    row.status, row.last_error, row.built_at = "pending", None, None
    return _bundle_pending(row)


def delete_bundle(data: SharingData, user: Viewer, bundle_id: int) -> None:
    """`SharingWriter.delete_share_bundle` (ShareLinkBundleViewSet destroy)."""
    _allow(user, "delete", "sharelinkbundle")
    row = _own_bundle(data, user, bundle_id)
    if not user.is_superuser and row.owner != user.id:
        raise denied()
    data.bundles.remove(row)


def email(  # noqa: PLR0913 -- the port's arguments
    data: SharingData,
    user: Viewer,
    document_ids: Sequence[int],
    addresses: Sequence[str],
    *,
    subject: str,
    message: str,
    use_archive_version: bool,
) -> None:
    """`SharingWriter.email_documents` (DocumentViewSet.email): validation, then the send."""
    joined = ",".join(addresses)
    bad = [a for a in (part.strip() for part in joined.split(",")) if "@" not in a or " " in a]
    if not document_ids:
        reject("documents", "At least one document is required")
    if not joined.strip():
        reject("addresses", "This field may not be blank.")
    if bad:
        reject("addresses", f"Invalid email address: {bad[0]}")
    if not subject:
        reject("subject", "This field may not be blank.")
    if not message:
        reject("message", "This field may not be blank.")
    if len(set(document_ids)) != len(document_ids) or not set(document_ids) <= user.visible:
        reject("documents", "Some documents in documents don't exist or were specified twice.")
    if not data.email_enabled:
        raise not_sent()
    data.outbox.append(
        SentEmail(
            documents=tuple(document_ids),
            addresses=tuple(addresses),
            subject=subject,
            message=message,
            use_archive_version=use_archive_version,
        )
    )
