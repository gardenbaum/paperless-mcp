"""Contract of the note and deletion methods of DocumentWriter on the fake and live Paperless.

Every test works on a document of its own (`backend.new_document`), never on a seeded one; the
live backend deletes the documents again and empties their trash entries when the test ends.
"""

from typing import TYPE_CHECKING

import pytest
from fakes.accounts import PUBLIC_NOTE

from paperless_mcp.domain.documents import DocumentQuery
from paperless_mcp.domain.errors import NotFound, PermissionDenied
from paperless_mcp.paperless.documents import unseen_note

if TYPE_CHECKING:
    from contract.conftest import Backend

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

ADD = "DocumentWriter.add_note"
DELETE_NOTE = "DocumentWriter.delete_note"
DELETE_DOCUMENT = "DocumentWriter.delete_document"
MISSING = 999_999
PUBLIC = "e2e-public-invoice"


@pytest.mark.contract(ADD)
async def test_a_new_note_has_its_author_and_shows_in_the_notes_and_the_count(
    backend: Backend,
) -> None:
    ports, document = backend.admin, backend.new_document()

    note = await ports.document_writes.add_note(document, "first remark")

    assert (note.note, note.user) == ("first remark", "admin")
    assert note.created is not None
    assert await ports.documents.list_document_notes(document) == (note,)
    assert (await ports.documents.get_document(document)).notes_count == 1


@pytest.mark.contract(ADD)
async def test_each_note_is_returned_by_its_own_call_and_ids_grow(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()

    first = await ports.document_writes.add_note(document, "one")
    second = await ports.document_writes.add_note(document, "two")

    assert second.id > first.id
    assert [n.note for n in await ports.documents.list_document_notes(document)] == ["one", "two"]


@pytest.mark.contract(ADD)
async def test_paperless_itself_accepts_empty_and_blank_notes(backend: Backend) -> None:
    # Pinned because the service must not rely on Paperless to reject them (it does not).
    ports, document = backend.admin, backend.new_document()

    empty = await ports.document_writes.add_note(document, "")
    blank = await ports.document_writes.add_note(document, "   ")

    assert (empty.note, blank.note) == ("", "   ")


@pytest.mark.contract(ADD)
async def test_a_note_needs_the_add_note_permission_and_an_existing_document(
    backend: Backend,
) -> None:
    public = (
        (
            await backend.reader.documents.search_documents(
                DocumentQuery(title_contains=PUBLIC), page=1, page_size=10
            )
        )
        .hits[0]
        .id
    )

    with pytest.raises(PermissionDenied):
        _ = await backend.reader.document_writes.add_note(public, "no")
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.document_writes.add_note(public, "no")
    with pytest.raises(NotFound):
        _ = await backend.admin.document_writes.add_note(MISSING, "no")


@pytest.mark.contract(DELETE_NOTE)
async def test_a_note_is_deleted_alone(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    keep = await ports.document_writes.add_note(document, "keep")
    drop = await ports.document_writes.add_note(document, "drop")

    await ports.document_writes.delete_note(document, drop.id)

    assert await ports.documents.list_document_notes(document) == (keep,)
    assert (await ports.documents.get_document(document)).notes_count == 1


@pytest.mark.contract(DELETE_NOTE)
async def test_deleting_an_unknown_or_foreign_note_is_not_found(backend: Backend) -> None:
    ports, document, other = backend.admin, backend.new_document(), backend.new_document()
    mine = await ports.document_writes.add_note(document, "mine")
    foreign = await ports.document_writes.add_note(other, "foreign")

    with pytest.raises(NotFound):
        await ports.document_writes.delete_note(document, MISSING)
    with pytest.raises(NotFound):
        await ports.document_writes.delete_note(document, foreign.id)
    with pytest.raises(NotFound) as raised:
        await ports.document_writes.delete_note(MISSING, mine.id)
    # Paperless answers 404 alike to a missing document and to a missing note.
    assert raised.value.message == unseen_note(MISSING, mine.id).message

    assert await ports.documents.list_document_notes(document) == (mine,)
    assert await ports.documents.list_document_notes(other) == (foreign,)


@pytest.mark.contract(DELETE_NOTE)
async def test_a_note_cannot_be_deleted_twice_or_without_the_permission(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    note = await ports.document_writes.add_note(document, "once")
    public = (
        (
            await backend.admin.documents.search_documents(
                DocumentQuery(title_contains=PUBLIC), page=1, page_size=10
            )
        )
        .hits[0]
        .id
    )
    [seeded_note] = await ports.documents.list_document_notes(public)

    with pytest.raises(PermissionDenied):
        await backend.reader.document_writes.delete_note(public, seeded_note.id)
    await ports.document_writes.delete_note(document, note.id)
    with pytest.raises(NotFound):
        await ports.document_writes.delete_note(document, note.id)

    assert seeded_note.note == PUBLIC_NOTE
    assert await ports.documents.list_document_notes(public) == (seeded_note,)


@pytest.mark.contract(DELETE_DOCUMENT)
async def test_a_deleted_document_goes_to_the_trash_and_vanishes_from_reads(
    backend: Backend,
) -> None:
    ports, document = backend.admin, backend.new_document()
    title = (await ports.documents.get_document(document)).title

    await ports.document_writes.delete_document(document)

    assert backend.in_trash(document)
    with pytest.raises(NotFound):
        _ = await ports.documents.get_document(document)
    found = await ports.documents.search_documents(
        DocumentQuery(title_contains=title), page=1, page_size=10
    )
    assert found.hits == ()


@pytest.mark.contract(DELETE_DOCUMENT)
async def test_deleting_a_missing_or_trashed_document_is_not_found(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    await ports.document_writes.delete_document(document)

    with pytest.raises(NotFound):
        await ports.document_writes.delete_document(document)
    with pytest.raises(NotFound):
        await ports.document_writes.delete_document(MISSING)


@pytest.mark.contract(DELETE_DOCUMENT)
async def test_deleting_needs_the_delete_permission(backend: Backend) -> None:
    public = (
        (
            await backend.reader.documents.search_documents(
                DocumentQuery(title_contains=PUBLIC), page=1, page_size=10
            )
        )
        .hits[0]
        .id
    )

    with pytest.raises(PermissionDenied):
        await backend.reader.document_writes.delete_document(public)
    with pytest.raises(PermissionDenied):
        await backend.blind.document_writes.delete_document(public)

    assert (await backend.reader.documents.get_document(public)).id == public


NOTES = frozenset({"view_document", "view_note", "add_note", "delete_note", "delete_document"})
# Live 3.4.2: adding and deleting notes also needs the global change_document permission.
CHANGING = NOTES | {"change_document"}


@pytest.mark.contract(ADD)
@pytest.mark.contract(DELETE_NOTE)
async def test_a_view_grant_reads_notes_but_writing_them_needs_a_change_grant_and_permission(
    backend: Backend,
) -> None:
    admin = backend.admin
    viewed, changed, hidden, granted_alone = (backend.new_document() for _ in range(4))
    note = await admin.document_writes.add_note(viewed, "admin's note")
    user = backend.restricted(CHANGING, view=[viewed], change=[changed])
    without_change_permission = backend.restricted(NOTES, change=[granted_alone])

    with pytest.raises(PermissionDenied):
        _ = await without_change_permission.document_writes.add_note(granted_alone, "no")
    assert await user.documents.list_document_notes(viewed) == (note,)
    with pytest.raises(PermissionDenied):
        _ = await user.document_writes.add_note(viewed, "no")
    with pytest.raises(PermissionDenied):
        await user.document_writes.delete_note(viewed, note.id)
    with pytest.raises(PermissionDenied):
        _ = await user.document_writes.add_note(hidden, "no")
    mine = await user.document_writes.add_note(changed, "yes")
    await user.document_writes.delete_note(changed, mine.id)

    assert await admin.documents.list_document_notes(viewed) == (note,)
    assert await admin.documents.list_document_notes(changed) == ()


@pytest.mark.contract(DELETE_DOCUMENT)
async def test_a_view_or_change_grant_is_not_enough_to_delete_a_document(backend: Backend) -> None:
    viewed, changed, hidden = (backend.new_document() for _ in range(3))
    user = backend.restricted(NOTES, view=[viewed], change=[changed])

    with pytest.raises(PermissionDenied):
        await user.document_writes.delete_document(viewed)
    with pytest.raises(PermissionDenied):
        await user.document_writes.delete_document(changed)
    with pytest.raises(NotFound):
        await user.document_writes.delete_document(hidden)

    assert not any(backend.in_trash(d) for d in (viewed, changed, hidden))
