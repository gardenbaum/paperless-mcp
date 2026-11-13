"""Contract of the version methods of DocumentWriter: the same assertions on the fake and 3.4.2.

Every test works on documents of its own (`backend.new_document`), never on a seeded one. Facts
probed on 3.4.2: a version upload is consumed like any upload and its task names the new version
(not the document); the newest version is listed first and is the file and text the document
shows; the checksum is the SHA-256 of the file; a blank label is stored as none; a deleted version
goes to the trash and can be restored from there; a merge removes its source documents for good.
"""

import asyncio
import hashlib
import time
from typing import TYPE_CHECKING

import pytest

from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation
from paperless_mcp.domain.versions import VersionUpload
from paperless_mcp.paperless.documents import unseen_document, unseen_version

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from paperless_mcp.domain.documents import DocumentVersion

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

UPLOAD = "DocumentWriter.upload_document_version"
LABEL = "DocumentWriter.set_version_label"
DELETE = "DocumentWriter.delete_version"
MERGE = "DocumentWriter.merge_as_versions"
MISSING = 999_999
CONSUMPTION_SECONDS = 180.0
# The global permissions a version change can ask for; what the user may do on a document then
# depends on owning it, or on a grant (the test says which).
CHANGING = frozenset({"view_document", "add_document", "change_document", "delete_document"})


def text_version(text: str, label: str | None = None) -> VersionUpload:
    """A plain text file: its text is the version's content on both backends."""
    return VersionUpload(
        filename=f"{text}.txt", mime_type="text/plain", content=text.encode(), label=label
    )


async def add_version(
    backend: Backend, document: int, text: str, label: str | None = None, *, by: Ports | None = None
) -> int:
    """Upload a text version (as `by`, default the admin), wait for it, return its version id."""
    ports = backend.admin
    task_id = await (by or ports).document_writes.upload_document_version(
        document, text_version(text, label)
    )
    deadline = time.monotonic() + CONSUMPTION_SECONDS
    while True:
        status = await ports.tasks.get_task(task_id)
        if status.is_final:
            break
        assert time.monotonic() < deadline, f"Task {task_id} took too long"
        await asyncio.sleep(1)
    assert status.state == "success", status.message
    [version] = status.document_ids
    backend.track_document(version)
    return version


async def versions(ports: Ports, document: int) -> tuple[DocumentVersion, ...]:
    return (await ports.documents.get_document(document)).versions


@pytest.mark.contract(UPLOAD)
async def test_a_new_version_is_the_newest_with_its_label_and_checksum(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    before = await ports.documents.get_document(document)

    version = await add_version(backend, document, "plmcp24 second version", "second")

    after = await ports.documents.get_document(document)
    [newest, root] = after.versions
    assert (newest.id, newest.version_label, newest.is_root) == (version, "second", False)
    assert newest.checksum == hashlib.sha256(b"plmcp24 second version").hexdigest()
    assert (root.id, root.is_root) == (document, True)
    assert version != document
    # The metadata belongs to the document and stays; the file is the newest version's.
    assert (after.title, after.tags) == (before.title, before.tags)
    assert await ports.documents.get_document_content(document) == "plmcp24 second version"
    original = await ports.documents.get_document_file(document, "original", max_bytes=10_000)
    assert original.content == b"plmcp24 second version"


@pytest.mark.contract(UPLOAD)
async def test_the_same_content_twice_makes_two_versions_with_one_checksum(
    backend: Backend,
) -> None:
    document = backend.new_document()

    first = await add_version(backend, document, "plmcp24 same content")
    second = await add_version(backend, document, "plmcp24 same content")

    found = await versions(backend.admin, document)
    assert [v.id for v in found] == [second, first, document]
    assert found[0].checksum == found[1].checksum != found[2].checksum


@pytest.mark.contract(UPLOAD)
@pytest.mark.parametrize("label", [None, ""])
async def test_no_label_and_a_blank_label_both_leave_the_version_without_one(
    backend: Backend, label: str | None
) -> None:
    document = backend.new_document()

    _ = await add_version(backend, document, "plmcp24 unlabelled", label)

    assert (await versions(backend.admin, document))[0].version_label is None


@pytest.mark.contract(UPLOAD)
async def test_an_unsupported_file_or_a_long_label_is_rejected_before_any_version_exists(
    backend: Backend,
) -> None:
    ports, document = backend.admin, backend.new_document()
    garbage = VersionUpload(
        filename="x.bin", mime_type="application/octet-stream", content=b"\x00\x01\x02garbage"
    )

    with pytest.raises(Validation):
        _ = await ports.document_writes.upload_document_version(document, garbage)
    with pytest.raises(Validation):
        _ = await ports.document_writes.upload_document_version(
            document, text_version("plmcp24 long label", "x" * 65)
        )

    assert [v.is_root for v in await versions(ports, document)] == [True]


@pytest.mark.contract(UPLOAD)
async def test_a_version_cannot_go_to_a_missing_document(backend: Backend) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await backend.admin.document_writes.upload_document_version(
            MISSING, text_version("plmcp24 nowhere")
        )

    assert raised.value.message == unseen_document(MISSING).message


@pytest.mark.contract(UPLOAD)
@pytest.mark.contract(LABEL)
@pytest.mark.contract(DELETE)
async def test_a_view_grant_or_no_change_permission_cannot_touch_versions(
    backend: Backend,
) -> None:
    admin = backend.admin
    document = backend.new_document()
    version = await add_version(backend, document, "plmcp24 guarded")
    viewer = backend.restricted(CHANGING, view=[document])
    reader = backend.restricted(frozenset({"view_document"}), change=[document])

    for user in (viewer, reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await user.document_writes.upload_document_version(
                document, text_version("plmcp24 denied")
            )
        with pytest.raises(PermissionDenied):
            _ = await user.document_writes.set_version_label(document, version, "no")
        with pytest.raises(PermissionDenied):
            _ = await user.document_writes.delete_version(document, version)

    assert [(v.id, v.version_label) for v in await versions(admin, document)] == [
        (version, None),
        (document, None),
    ]


@pytest.mark.contract(UPLOAD)
@pytest.mark.contract(LABEL)
@pytest.mark.contract(DELETE)
async def test_a_change_grant_adds_versions_and_sets_labels_but_does_not_delete_them(
    backend: Backend,
) -> None:
    document = backend.new_document()
    version = await add_version(backend, document, "plmcp24 shared")
    user = backend.restricted(CHANGING, change=[document])

    labelled = await user.document_writes.set_version_label(document, version, "by user")
    added = await add_version(backend, document, "plmcp24 by the user", by=user)
    with pytest.raises(PermissionDenied):
        _ = await user.document_writes.delete_version(document, added)  # deleting needs ownership

    assert labelled.version_label == "by user"
    assert [v.id for v in await versions(backend.admin, document)] == [added, version, document]


@pytest.mark.contract(UPLOAD)
@pytest.mark.contract(LABEL)
@pytest.mark.contract(DELETE)
async def test_a_document_the_user_has_no_grant_on_is_refused_not_hidden(
    backend: Backend,
) -> None:
    document = backend.new_document()
    version = await add_version(backend, document, "plmcp24 private")
    user = backend.restricted(CHANGING)

    with pytest.raises(PermissionDenied):
        _ = await user.document_writes.upload_document_version(
            document, text_version("plmcp24 intruder")
        )
    with pytest.raises(PermissionDenied):
        _ = await user.document_writes.set_version_label(document, version, "no")
    with pytest.raises(PermissionDenied):
        _ = await user.document_writes.delete_version(document, version)
    with pytest.raises(NotFound):
        _ = await user.document_writes.set_version_label(MISSING, version, "no")

    assert len(await versions(backend.admin, document)) == 2


@pytest.mark.contract(LABEL)
async def test_a_label_is_set_changed_and_cleared_on_any_version_the_root_included(
    backend: Backend,
) -> None:
    ports, document = backend.admin, backend.new_document()
    version = await add_version(backend, document, "plmcp24 labels")

    set_ = await ports.document_writes.set_version_label(document, version, "draft")
    root = await ports.document_writes.set_version_label(document, document, "original")

    assert (set_.id, set_.version_label, set_.is_root) == (version, "draft", False)
    assert set_.checksum == hashlib.sha256(b"plmcp24 labels").hexdigest()
    assert (root.id, root.version_label, root.is_root) == (document, "original", True)
    assert [(v.id, v.version_label) for v in await versions(ports, document)] == [
        (version, "draft"),
        (document, "original"),
    ]
    cleared = await ports.document_writes.set_version_label(document, version, None)
    blank = await ports.document_writes.set_version_label(document, document, "")
    assert (cleared.version_label, blank.version_label) == (None, None)


@pytest.mark.contract(LABEL)
async def test_a_label_of_65_characters_is_rejected_and_64_fit(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    version = await add_version(backend, document, "plmcp24 long")

    with pytest.raises(Validation):
        _ = await ports.document_writes.set_version_label(document, version, "x" * 65)
    fits = await ports.document_writes.set_version_label(document, version, "y" * 64)

    assert fits.version_label == "y" * 64


@pytest.mark.contract(LABEL)
@pytest.mark.contract(DELETE)
async def test_an_unknown_or_foreign_version_or_document_is_not_found(backend: Backend) -> None:
    ports, document, other = backend.admin, backend.new_document(), backend.new_document()
    foreign = await add_version(backend, other, "plmcp24 foreign")

    for version in (MISSING, foreign):
        with pytest.raises(NotFound) as raised:
            _ = await ports.document_writes.set_version_label(document, version, "x")
        assert raised.value.message == unseen_version(document, version).message
        with pytest.raises(NotFound):
            _ = await ports.document_writes.delete_version(document, version)
    with pytest.raises(NotFound):
        _ = await ports.document_writes.set_version_label(MISSING, foreign, "x")
    with pytest.raises(NotFound):
        _ = await ports.document_writes.delete_version(MISSING, foreign)

    assert [v.is_root for v in await versions(ports, document)] == [True]
    assert len(await versions(ports, other)) == 2


@pytest.mark.contract(DELETE)
async def test_deleting_the_newest_version_brings_back_the_previous_one_and_trashes_it(
    backend: Backend,
) -> None:
    ports, document = backend.admin, backend.new_document()
    first = await add_version(backend, document, "plmcp24 first")
    second = await add_version(backend, document, "plmcp24 second")

    current = await ports.document_writes.delete_version(document, second)

    assert current == first
    assert [v.id for v in await versions(ports, document)] == [first, document]
    assert await ports.documents.get_document_content(document) == "plmcp24 first"
    assert backend.in_trash(second)  # not gone for good: the trash can restore it
    assert not backend.in_trash(document)


@pytest.mark.contract(DELETE)
async def test_deleting_an_older_version_leaves_the_newest_current(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    first = await add_version(backend, document, "plmcp24 older")
    second = await add_version(backend, document, "plmcp24 newer")

    current = await ports.document_writes.delete_version(document, first)

    assert current == second
    assert [v.id for v in await versions(ports, document)] == [second, document]
    assert await ports.documents.get_document_content(document) == "plmcp24 newer"


@pytest.mark.contract(DELETE)
async def test_the_root_version_is_not_deleted_and_a_version_only_once(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    version = await add_version(backend, document, "plmcp24 once")

    with pytest.raises(Validation):
        _ = await ports.document_writes.delete_version(document, document)
    _ = await ports.document_writes.delete_version(document, version)
    with pytest.raises(NotFound):
        _ = await ports.document_writes.delete_version(document, version)

    assert [v.id for v in await versions(ports, document)] == [document]
    assert not backend.in_trash(document)


@pytest.mark.contract(MERGE)
async def test_one_merged_document_becomes_the_newest_version_with_its_label(
    backend: Backend,
) -> None:
    ports, root, source = backend.admin, backend.new_document(), backend.new_document()
    backend.track_document(source)
    note = await ports.document_writes.add_note(source, "belongs to the source")
    source_title = (await ports.documents.get_document(source)).title
    root_title = (await ports.documents.get_document(root)).title

    await ports.document_writes.merge_as_versions(root, (source,), "from source")

    [newest, original] = await versions(ports, root)
    assert (newest.id, newest.version_label, newest.is_root) == (source, "from source", False)
    assert (original.id, original.is_root) == (root, True)
    assert (await ports.documents.get_document(root)).title == root_title  # the root's metadata
    assert note.note not in [n.note for n in await ports.documents.list_document_notes(root)]
    # The source is gone as a document of its own, and not in the trash either.
    with pytest.raises(NotFound):
        _ = await ports.documents.get_document(source)
    assert not backend.in_trash(source)
    assert source_title != root_title


@pytest.mark.contract(MERGE)
async def test_several_merged_documents_become_versions_in_the_order_given(
    backend: Backend,
) -> None:
    ports, root = backend.admin, backend.new_document()
    first, second = backend.new_document(), backend.new_document()
    backend.track_document(first)
    backend.track_document(second)

    await ports.document_writes.merge_as_versions(root, (first, second), None)

    found = await versions(ports, root)
    assert [v.id for v in found] == [second, first, root]
    assert [v.version_label for v in found] == [None, None, None]


@pytest.mark.contract(MERGE)
async def test_a_merge_that_paperless_refuses_changes_nothing(backend: Backend) -> None:
    ports, root = backend.admin, backend.new_document()
    one, two, holder = backend.new_document(), backend.new_document(), backend.new_document()
    held = await add_version(backend, holder, "plmcp24 holds a version")

    refused = {
        "a label with several sources": (root, (one, two), "label"),
        "a document twice": (root, (one, one), None),
        "a document that does not exist": (root, (MISSING,), None),
        "a source with versions": (root, (holder,), None),
        "a version as source": (root, (held,), None),
    }
    for name, (target, sources, label) in refused.items():
        with pytest.raises(Validation):
            await ports.document_writes.merge_as_versions(target, sources, label)
        assert [v.is_root for v in await versions(ports, root)] == [True], name

    assert [(await ports.documents.get_document(d)).id for d in (one, two, holder)] == [
        one,
        two,
        holder,
    ]


@pytest.mark.contract(MERGE)
async def test_a_root_that_already_has_versions_takes_more(backend: Backend) -> None:
    ports, root, source = backend.admin, backend.new_document(), backend.new_document()
    backend.track_document(source)
    first = await add_version(backend, root, "plmcp24 root version")

    await ports.document_writes.merge_as_versions(root, (source,), None)

    assert [v.id for v in await versions(ports, root)] == [source, first, root]


@pytest.mark.contract(MERGE)
async def test_merging_needs_the_permission_to_change_the_documents(backend: Backend) -> None:
    ports, root, source = backend.admin, backend.new_document(), backend.new_document()
    viewer = backend.restricted(CHANGING, view=[root, source])
    half = backend.restricted(CHANGING, view=[source], change=[root])

    with pytest.raises(PermissionDenied):
        await viewer.document_writes.merge_as_versions(root, (source,), None)
    with pytest.raises(PermissionDenied):
        await half.document_writes.merge_as_versions(root, (source,), None)
    with pytest.raises(PermissionDenied):
        await backend.reader.document_writes.merge_as_versions(root, (source,), None)

    assert [v.is_root for v in await versions(ports, root)] == [True]
    assert (await ports.documents.get_document(source)).id == source


@pytest.mark.contract(MERGE)
async def test_a_change_grant_is_not_enough_to_merge_the_user_must_own_the_documents(
    backend: Backend,
) -> None:
    ports, root, source = backend.admin, backend.new_document(), backend.new_document()
    user = backend.restricted(CHANGING, change=[root, source])

    with pytest.raises(PermissionDenied):
        await user.document_writes.merge_as_versions(root, (source,), None)

    assert [v.is_root for v in await versions(ports, root)] == [True]
    assert (await ports.documents.get_document(source)).id == source


@pytest.mark.contract(MERGE)
async def test_a_document_the_user_cannot_see_is_refused_not_reported_missing(
    backend: Backend,
) -> None:
    root, hidden = backend.new_document(), backend.new_document()
    user = backend.restricted(CHANGING, change=[root])

    with pytest.raises(PermissionDenied):
        await user.document_writes.merge_as_versions(root, (hidden,), None)
    with pytest.raises(Validation):
        await user.document_writes.merge_as_versions(root, (MISSING,), None)

    assert (await backend.admin.documents.get_document(hidden)).id == hidden
