"""Contract of SharingReader and SharingWriter: the same assertions against the fake and live 3.4.2.

Links and bundles belong to documents a test creates (the live backend deletes those, and Paperless
deletes their links with them); a test deletes the bundles it makes through the `shares` fixture,
because a bundle outlives its documents. The E2E stack has no mail server (nothing in
tests/e2e/compose.yaml sets one), and neither has the fake until a test switches it on, so a valid
e-mail fails on both, and a successful send is checked on the fake only.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from paperless_mcp.domain.errors import FeatureUnavailable, NotFound, PermissionDenied, Validation

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from contract.conftest import Backend, Ports
    from paperless_mcp.domain.sharing import ShareBundle

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]

LIST_LINKS, GET_LINK = "SharingReader.list_share_links", "SharingReader.get_share_link"
LIST_BUNDLES, GET_BUNDLE = "SharingReader.list_share_bundles", "SharingReader.get_share_bundle"
CREATE_LINK, DELETE_LINK = "SharingWriter.create_share_link", "SharingWriter.delete_share_link"
CREATE_BUNDLE, REBUILD = "SharingWriter.create_share_bundle", "SharingWriter.rebuild_share_bundle"
DELETE_BUNDLE, EMAIL = "SharingWriter.delete_share_bundle", "SharingWriter.email_documents"
EXPIRY = datetime(2030, 1, 1, tzinfo=UTC)
ADDRESS = "someone@example.com"
LINKING = frozenset({"view_document", "view_sharelink", "add_sharelink", "delete_sharelink"})
BUNDLING = frozenset(
    {
        *("view_document", "view_sharelinkbundle", "add_sharelinkbundle"),
        *("change_sharelinkbundle", "delete_sharelinkbundle"),
    }
)
BUILD_SECONDS = 60.0


class Shares:
    """Bundles a test made; they are deleted when it ends."""

    def __init__(self, ports: Ports) -> None:
        self.ports: Ports = ports
        self.bundles: list[int] = []

    async def bundle(self, documents: list[int], **options: object) -> ShareBundle:
        made = await self.ports.sharing_writes.create_share_bundle(
            documents,
            expiration_days=options.get("expiration_days"),  # pyright: ignore[reportArgumentType] -- the tests pass an int or nothing
            file_version="archive",
        )
        self.bundles.append(made.id)
        return made


@pytest.fixture
async def shares(backend: Backend) -> AsyncIterator[Shares]:
    shared = Shares(backend.admin)
    yield shared
    for bundle in shared.bundles:
        try:
            await backend.admin.sharing_writes.delete_share_bundle(bundle)
        except NotFound:
            continue


async def built(ports: Ports, bundle_id: int) -> ShareBundle:
    """The bundle once Paperless has built its archive."""
    deadline = asyncio.get_running_loop().time() + BUILD_SECONDS
    while True:
        bundle = await ports.sharing.get_share_bundle(bundle_id)
        if bundle.status in {"ready", "failed"}:
            return bundle
        assert asyncio.get_running_loop().time() < deadline, "the bundle was not built in time"
        await asyncio.sleep(0.5)


@pytest.mark.contract(CREATE_LINK)
@pytest.mark.contract(LIST_LINKS)
async def test_a_created_link_keeps_its_settings_and_is_listed(backend: Backend) -> None:
    [document] = backend.new_documents(1)
    titles = await backend.admin.documents.get_document_titles([document])

    made = await backend.admin.sharing_writes.create_share_link(
        document, expiration=EXPIRY, file_version="original"
    )

    assert (made.document_id, made.document_title) == (document, titles[document])
    assert (made.expiration, made.file_version) == (EXPIRY, "original")
    assert len(made.slug) >= 20
    listed = [link for link in await backend.admin.sharing.list_share_links() if link.id == made.id]
    assert listed == [made]


@pytest.mark.contract(CREATE_LINK)
async def test_a_link_without_expiry_never_expires(backend: Backend) -> None:
    [document] = backend.new_documents(1)

    made = await backend.admin.sharing_writes.create_share_link(
        document, expiration=None, file_version="archive"
    )

    assert (made.expiration, made.file_version) == (None, "archive")


@pytest.mark.contract(CREATE_LINK)
async def test_a_link_for_an_unknown_document_is_refused(backend: Backend) -> None:
    with pytest.raises(Validation, match="document"):
        _ = await backend.admin.sharing_writes.create_share_link(
            999_999, expiration=None, file_version="archive"
        )


@pytest.mark.contract(CREATE_LINK)
async def test_a_user_shares_the_documents_it_can_see_and_no_others(backend: Backend) -> None:
    [seen, hidden] = backend.new_documents(2)
    sharer = backend.restricted(LINKING, view=[seen])

    made = await sharer.sharing_writes.create_share_link(
        seen, expiration=None, file_version="archive"
    )

    assert made.document_id == seen
    assert made.id in {link.id for link in await sharer.sharing.list_share_links()}
    with pytest.raises(PermissionDenied):
        _ = await sharer.sharing_writes.create_share_link(
            hidden, expiration=None, file_version="archive"
        )


@pytest.mark.contract(CREATE_LINK)
@pytest.mark.contract(DELETE_LINK)
async def test_a_user_without_the_permission_may_not_create_or_delete_links(
    backend: Backend,
) -> None:
    [document] = backend.new_documents(1)
    made = await backend.admin.sharing_writes.create_share_link(
        document, expiration=None, file_version="archive"
    )

    for ports in (backend.reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await ports.sharing_writes.create_share_link(
                document, expiration=None, file_version="archive"
            )
        with pytest.raises(PermissionDenied):
            await ports.sharing_writes.delete_share_link(made.id)


@pytest.mark.contract(LIST_LINKS)
@pytest.mark.contract(GET_LINK)
async def test_a_user_without_the_view_permission_is_denied_links(backend: Backend) -> None:
    [document] = backend.new_documents(1)
    made = await backend.admin.sharing_writes.create_share_link(
        document, expiration=None, file_version="archive"
    )

    for ports in (backend.reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await ports.sharing.list_share_links()
        with pytest.raises(PermissionDenied):
            _ = await ports.sharing.get_share_link(made.id)


@pytest.mark.contract(LIST_LINKS)
@pytest.mark.contract(GET_LINK)
async def test_a_user_sees_only_the_links_it_owns(backend: Backend) -> None:
    [document] = backend.new_documents(1)
    made = await backend.admin.sharing_writes.create_share_link(
        document, expiration=None, file_version="archive"
    )
    viewer = backend.restricted(LINKING, view=[document])

    assert made.id not in {link.id for link in await viewer.sharing.list_share_links()}
    with pytest.raises(NotFound, match=f"No share link {made.id}"):
        _ = await viewer.sharing.get_share_link(made.id)


@pytest.mark.contract(GET_LINK)
async def test_one_link_is_read_by_id_and_a_missing_one_is_not_found(backend: Backend) -> None:
    [document] = backend.new_documents(1)
    made = await backend.admin.sharing_writes.create_share_link(
        document, expiration=EXPIRY, file_version="archive"
    )

    assert await backend.admin.sharing.get_share_link(made.id) == made
    with pytest.raises(NotFound, match="No share link 999999"):
        _ = await backend.admin.sharing.get_share_link(999_999)


@pytest.mark.contract(DELETE_LINK)
async def test_a_deleted_link_is_gone(backend: Backend) -> None:
    [document] = backend.new_documents(1)
    made = await backend.admin.sharing_writes.create_share_link(
        document, expiration=None, file_version="archive"
    )

    await backend.admin.sharing_writes.delete_share_link(made.id)

    assert made.id not in {link.id for link in await backend.admin.sharing.list_share_links()}
    with pytest.raises(NotFound, match=f"No share link {made.id}"):
        await backend.admin.sharing_writes.delete_share_link(made.id)


@pytest.mark.contract(CREATE_BUNDLE)
@pytest.mark.contract(LIST_BUNDLES)
@pytest.mark.contract(GET_BUNDLE)
async def test_a_created_bundle_is_built_and_listed(backend: Backend, shares: Shares) -> None:
    documents = backend.new_documents(2)

    made = await shares.bundle(documents, expiration_days=3)

    assert made.status == "pending"
    assert made.document_ids == tuple(sorted(documents))
    assert made.file_version == "archive"
    assert made.expiration is not None
    assert abs(made.expiration - made.created - timedelta(days=3)) < timedelta(minutes=1)
    ready = await built(backend.admin, made.id)
    assert (ready.status, ready.last_error) == ("ready", None)
    assert ready.size_bytes is not None
    assert ready.built_at is not None
    listed = [b for b in await backend.admin.sharing.list_share_bundles() if b.id == made.id]
    assert listed == [ready]
    assert await backend.admin.sharing.get_share_bundle(made.id) == ready


@pytest.mark.contract(CREATE_BUNDLE)
async def test_a_bundle_without_expiry_never_expires(backend: Backend, shares: Shares) -> None:
    [document] = backend.new_documents(1)

    made = await shares.bundle([document])

    assert made.expiration is None


@pytest.mark.contract(CREATE_BUNDLE)
@pytest.mark.parametrize(
    ("documents", "days", "message"),
    [
        pytest.param([], None, "document_ids", id="no documents"),
        pytest.param([999_999], None, "999999", id="unknown document"),
        pytest.param(None, 0, "expiration_days", id="zero days"),
    ],
)
async def test_a_bundle_paperless_cannot_make_is_refused(
    backend: Backend, documents: list[int] | None, days: int | None, message: str
) -> None:
    ids = backend.new_documents(1) if documents is None else documents

    with pytest.raises(Validation, match=message):
        _ = await backend.admin.sharing_writes.create_share_bundle(
            ids, expiration_days=days, file_version="archive"
        )


@pytest.mark.contract(CREATE_BUNDLE)
@pytest.mark.contract(DELETE_BUNDLE)
async def test_a_user_without_the_permission_may_not_create_or_delete_bundles(
    backend: Backend, shares: Shares
) -> None:
    [document] = backend.new_documents(1)
    made = await shares.bundle([document])

    for ports in (backend.reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await ports.sharing_writes.create_share_bundle(
                [document], expiration_days=None, file_version="archive"
            )
        with pytest.raises(PermissionDenied):
            await ports.sharing_writes.delete_share_bundle(made.id)


@pytest.mark.contract(CREATE_BUNDLE)
async def test_a_user_bundles_the_documents_it_can_see_and_no_others(backend: Backend) -> None:
    [seen, hidden] = backend.new_documents(2)
    sharer = backend.restricted(BUNDLING, view=[seen])

    made = await sharer.sharing_writes.create_share_bundle(
        [seen], expiration_days=None, file_version="archive"
    )
    try:
        assert made.document_ids == (seen,)
        with pytest.raises(Validation, match=str(hidden)):
            _ = await sharer.sharing_writes.create_share_bundle(
                [seen, hidden], expiration_days=None, file_version="archive"
            )
    finally:
        await sharer.sharing_writes.delete_share_bundle(made.id)


@pytest.mark.contract(LIST_BUNDLES)
@pytest.mark.contract(GET_BUNDLE)
async def test_a_user_without_the_view_permission_is_denied_bundles(
    backend: Backend, shares: Shares
) -> None:
    [document] = backend.new_documents(1)
    made = await shares.bundle([document])

    for ports in (backend.reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await ports.sharing.list_share_bundles()
        with pytest.raises(PermissionDenied):
            _ = await ports.sharing.get_share_bundle(made.id)


@pytest.mark.contract(LIST_BUNDLES)
@pytest.mark.contract(GET_BUNDLE)
async def test_a_user_sees_only_the_bundles_it_owns(backend: Backend, shares: Shares) -> None:
    [document] = backend.new_documents(1)
    made = await shares.bundle([document])
    viewer = backend.restricted(BUNDLING, view=[document])

    assert made.id not in {b.id for b in await viewer.sharing.list_share_bundles()}
    with pytest.raises(NotFound, match=f"No share-link bundle {made.id}"):
        _ = await viewer.sharing.get_share_bundle(made.id)


@pytest.mark.contract(GET_BUNDLE)
async def test_a_missing_bundle_is_not_found(backend: Backend) -> None:
    with pytest.raises(NotFound, match="No share-link bundle 999999"):
        _ = await backend.admin.sharing.get_share_bundle(999_999)


@pytest.mark.contract(REBUILD)
async def test_a_rebuilt_bundle_is_pending_again_and_keeps_its_url(
    backend: Backend, shares: Shares
) -> None:
    documents = backend.new_documents(2)
    made = await shares.bundle(documents)
    ready = await built(backend.admin, made.id)

    rebuilt = await backend.admin.sharing_writes.rebuild_share_bundle(made.id)

    assert rebuilt.status == "pending"
    assert (rebuilt.id, rebuilt.slug, rebuilt.document_ids) == (
        ready.id,
        ready.slug,
        ready.document_ids,
    )
    assert (await built(backend.admin, made.id)).status == "ready"


@pytest.mark.contract(REBUILD)
async def test_rebuilding_a_missing_bundle_is_not_found_and_needs_the_permission(
    backend: Backend, shares: Shares
) -> None:
    [document] = backend.new_documents(1)
    made = await shares.bundle([document])

    with pytest.raises(NotFound, match="No share-link bundle 999999"):
        _ = await backend.admin.sharing_writes.rebuild_share_bundle(999_999)
    for ports in (backend.reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await ports.sharing_writes.rebuild_share_bundle(made.id)


@pytest.mark.contract(DELETE_BUNDLE)
async def test_a_deleted_bundle_is_gone(backend: Backend, shares: Shares) -> None:
    [document] = backend.new_documents(1)
    made = await shares.bundle([document])

    await backend.admin.sharing_writes.delete_share_bundle(made.id)

    assert made.id not in {b.id for b in await backend.admin.sharing.list_share_bundles()}
    with pytest.raises(NotFound, match=f"No share-link bundle {made.id}"):
        await backend.admin.sharing_writes.delete_share_bundle(made.id)


@pytest.mark.contract(EMAIL)
@pytest.mark.parametrize(
    ("addresses", "subject", "message", "complaint"),
    [
        pytest.param(["nope"], "s", "m", "addresses", id="bad address"),
        pytest.param([ADDRESS], "", "m", "subject", id="blank subject"),
        pytest.param([ADDRESS], "s", "", "message", id="blank message"),
    ],
)
async def test_an_email_paperless_rejects_names_the_field(
    backend: Backend, addresses: list[str], subject: str, message: str, complaint: str
) -> None:
    [document] = backend.new_documents(1)

    with pytest.raises(Validation, match=complaint):
        await backend.admin.sharing_writes.email_documents(
            [document],
            addresses=addresses,
            subject=subject,
            message=message,
            use_archive_version=True,
        )


@pytest.mark.contract(EMAIL)
async def test_an_email_needs_existing_documents(backend: Backend) -> None:
    for documents in ([], [999_999]):
        with pytest.raises(Validation, match="documents"):
            await backend.admin.sharing_writes.email_documents(
                documents,
                addresses=[ADDRESS],
                subject="s",
                message="m",
                use_archive_version=True,
            )


@pytest.mark.contract(EMAIL)
async def test_an_email_fails_where_paperless_has_no_mail_server(backend: Backend) -> None:
    [document] = backend.new_documents(1)

    with pytest.raises(FeatureUnavailable, match="could not send"):
        await backend.admin.sharing_writes.email_documents(
            [document],
            addresses=[ADDRESS],
            subject="s",
            message="m",
            use_archive_version=True,
        )
