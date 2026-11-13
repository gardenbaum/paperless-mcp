"""Contract of TrashReader and TrashWriter: the same assertions on the fake and on Paperless 3.4.2.

Every test works on documents of its own (`backend.new_documents`, plain text) and looks them up
in the trash by id: the live trash is shared with every other test and holds hundreds of entries,
so no test asserts exact counts or empties anything it did not delete itself. Facts probed on
3.4.2 (PLMCP-27):

- Deleting a document or a version puts an entry in the trash; the entry of a version has the
  version's id, the document's title and `root_document` set. A document in the trash covers its
  versions (they are not listed).
- Restoring a document brings back its notes and versions; restoring a version re-attaches it to
  its document with its label, as the newest version if it was the newest.
- Ids that are not in the trash, or listed twice, are a 400 for the whole request. A version
  whose document is in the trash too cannot be restored on its own.
- Listing needs `view_document`, restoring and emptying `delete_document`; a document the user
  does not own is refused (403) and stays where it is. The list shows owned and unowned entries.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from contract.test_document_versions import add_version
from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from paperless_mcp.domain.trash import TrashedDocument

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

LIST = "TrashReader.list_trash"
RESTORE = "TrashWriter.restore_from_trash"
EMPTY = "TrashWriter.empty_trash"
MISSING = 999_999
SCAN_SIZE = 100


async def find(ports: Ports, wanted: set[int]) -> dict[int, TrashedDocument]:
    """The entries of the trash with these ids, read page by page until all are found."""
    found: dict[int, TrashedDocument] = {}
    page: int | None = 1
    while page is not None and not wanted <= found.keys():
        listed = await ports.trash.list_trash(page=page, page_size=SCAN_SIZE)
        found |= {e.id: e for e in listed.entries if e.id in wanted}
        page = listed.next_page
    return found


async def trashed(backend: Backend, count: int) -> list[int]:
    """New documents that have been deleted again, so they are in the trash."""
    documents = backend.new_documents(count)
    for document in documents:
        await backend.admin.document_writes.delete_document(document)
    return documents


@pytest.mark.contract(LIST)
async def test_deleted_documents_are_listed_paged_and_only_to_those_who_may_view_them(
    backend: Backend,
) -> None:
    ports = backend.admin
    first, second = backend.new_documents(2)
    titles = {d: (await ports.documents.get_document(d)).title for d in (first, second)}
    before = datetime.now(UTC) - timedelta(minutes=5)
    for document in (first, second):
        await ports.document_writes.delete_document(document)

    found = await find(ports, {first, second})
    assert {d: e.title for d, e in found.items()} == titles
    assert all(e.version_of is None for e in found.values())
    after = datetime.now(UTC) + timedelta(minutes=5)
    assert all(before < e.deleted_at < after for e in found.values())

    # Paging: one entry a page, and a page beyond the last is not found.
    one = await ports.trash.list_trash(page=1, page_size=1)
    assert (len(one.entries), one.next_page) == (1, 2)
    assert one.count >= 2
    with pytest.raises(NotFound):
        _ = await ports.trash.list_trash(page=one.count + 1, page_size=1)

    # The reader sees owned and unowned entries only, and a user without view_document none.
    shown = await backend.reader.trash.list_trash(page=1, page_size=SCAN_SIZE)
    assert not {first, second} & {e.id for e in shown.entries}
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.trash.list_trash(page=1, page_size=SCAN_SIZE)


@pytest.mark.contract(RESTORE)
async def test_a_restored_document_is_back_with_its_notes_and_a_bad_selection_restores_nothing(
    backend: Backend,
) -> None:
    ports = backend.admin
    [kept, alive] = backend.new_documents(2)
    _ = await ports.document_writes.add_note(kept, "plmcp27 note")
    await ports.document_writes.delete_document(kept)

    for ids in ([alive], [MISSING], [kept, alive], [kept, kept]):
        with pytest.raises(Validation):
            await ports.trash_writes.restore_from_trash(ids)
    assert backend.in_trash(kept)

    await ports.trash_writes.restore_from_trash([kept])

    assert not await find(ports, {kept})
    assert not backend.in_trash(kept)
    notes = await ports.documents.list_document_notes(kept)
    assert [n.note for n in notes] == ["plmcp27 note"]
    with pytest.raises(Validation):  # a second restore finds nothing in the trash
        await ports.trash_writes.restore_from_trash([kept])


@pytest.mark.contract(EMPTY)
async def test_emptying_removes_the_named_entries_for_good_and_needs_ownership(
    backend: Backend,
) -> None:
    ports = backend.admin
    [gone, stays] = await trashed(backend, 2)
    [alive] = backend.new_documents(1)
    deleter = backend.restricted(frozenset({"view_document", "delete_document"}))

    # Without delete_document, or without owning the documents: refused, the ids are not looked at.
    for user in (backend.reader, deleter):
        with pytest.raises(PermissionDenied):
            await user.trash_writes.empty_trash([gone, stays])
        with pytest.raises(PermissionDenied):
            await user.trash_writes.restore_from_trash([gone])
    with pytest.raises(PermissionDenied):
        await backend.reader.trash_writes.empty_trash([MISSING])
    # A bad selection empties nothing.
    for ids in ([gone, alive], [gone, MISSING], [gone, gone]):
        with pytest.raises(Validation):
            await ports.trash_writes.empty_trash(ids)
    assert (await find(ports, {gone, stays})).keys() == {gone, stays}

    await ports.trash_writes.empty_trash([gone])

    assert (await find(ports, {gone, stays})).keys() == {stays}
    with pytest.raises(NotFound):
        _ = await ports.documents.get_document(gone)
    assert await ports.documents.get_document(alive)
    with pytest.raises(Validation):  # nothing left to empty, and nothing to restore
        await ports.trash_writes.empty_trash([gone])
    with pytest.raises(Validation):
        await ports.trash_writes.restore_from_trash([gone])


@pytest.mark.contract(RESTORE)
@pytest.mark.contract(EMPTY)
@pytest.mark.contract(LIST)
async def test_a_deleted_version_is_an_entry_of_its_document_and_restored_onto_it(
    backend: Backend,
) -> None:
    ports = backend.admin
    [document] = backend.new_documents(1)
    version = await add_version(backend, document, "plmcp27 second file", "kept label")
    title = (await ports.documents.get_document(document)).title
    assert await ports.document_writes.delete_version(document, version) == document

    entry = (await find(ports, {version}))[version]
    assert (entry.version_of, entry.title) == (document, title)
    assert [v.id for v in (await ports.documents.get_document(document)).versions] == [document]

    await ports.trash_writes.restore_from_trash([version])

    restored = await ports.documents.get_document(document)
    assert [(v.id, v.version_label) for v in restored.versions] == [
        (version, "kept label"),
        (document, None),
    ]
    assert await ports.documents.get_document_content(document) == "plmcp27 second file"
    assert not await find(ports, {version})

    # A document in the trash covers its versions: they are not listed, cannot be restored on
    # their own, and come back with the document.
    await ports.document_writes.delete_document(document)
    assert (await find(ports, {document, version})).keys() == {document}
    with pytest.raises(Validation, match="root document"):
        await ports.trash_writes.restore_from_trash([version])
    await ports.trash_writes.restore_from_trash([document])
    assert [v.id for v in (await ports.documents.get_document(document)).versions] == [
        version,
        document,
    ]

    # A version deleted before its document stays an entry of its own when the document comes
    # back, and can then be restored.
    _ = await ports.document_writes.delete_version(document, version)
    await ports.document_writes.delete_document(document)
    await ports.trash_writes.restore_from_trash([document])
    assert [v.id for v in (await ports.documents.get_document(document)).versions] == [document]
    assert (await find(ports, {version}))[version].version_of == document
    await ports.trash_writes.restore_from_trash([version])
    assert [v.id for v in (await ports.documents.get_document(document)).versions] == [
        version,
        document,
    ]

    # An emptied version is gone for good; the document keeps its other versions.
    _ = await ports.document_writes.delete_version(document, version)
    await ports.trash_writes.empty_trash([version])
    assert not await find(ports, {version})
    assert [v.id for v in (await ports.documents.get_document(document)).versions] == [document]
