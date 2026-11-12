"""NoteService and DocumentRemoval: reads first, the confirmation flow, nothing written without it.

The fakes of the "unconfirmed" cases are created without `allow_writes`: the fake's write guard
raises `WriteNotAllowedError` on any write, so a passing test proves nothing was written.
"""

from typing import final

import pytest
from fakes.accounts import ADMIN, PUBLIC_NOTE, READER
from fakes.paperless import FakePaperless, FakeState, seeded

from paperless_mcp.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
    quoted,
)
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.notes import EXCERPT_CHARS, MAX_NOTE_CHARS, NoteService
from paperless_mcp.services.removal import DocumentRemoval
from paperless_mcp.services.taxonomy import TaxonomyResolver

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

WEB = "https://paperless.example"
PUBLIC = 1
PUBLIC_TITLE = "e2e-public-invoice"


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, writes: bool = True) -> None:
        self.state: FakeState = seeded()
        self.fake: FakePaperless = FakePaperless(self.state, user=user, allow_writes=writes)
        self.taxonomy: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.notes: NoteService = NoteService(self.fake, self.fake, web_url=WEB)
        self.removal: DocumentRemoval = DocumentRemoval(self.fake, self.fake, self.taxonomy)

    @property
    def stored_notes(self) -> list[str]:
        return [n.note for n in self.state.documents[0].notes]


async def test_a_note_is_added_and_returned_with_its_author_and_link() -> None:
    setup = Setup()

    added = await setup.notes.add(PUBLIC, "paid on 2026-11-12")

    assert (added.document_id, added.note.note, added.note.user) == (
        PUBLIC,
        "paid on 2026-11-12",
        "admin",
    )
    assert added.web_url == f"{WEB}/documents/1/details"
    assert setup.stored_notes == [PUBLIC_NOTE, "paid on 2026-11-12"]


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
async def test_an_empty_note_is_refused_because_paperless_would_store_it(text: str) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.notes.add(PUBLIC, text)

    assert raised.value.message == "A note cannot be empty."
    assert raised.value.hint == "Pass the text of the note."
    assert setup.stored_notes == [PUBLIC_NOTE]


async def test_a_note_is_limited_in_length_and_the_limit_is_allowed() -> None:
    setup = Setup()
    added = await setup.notes.add(PUBLIC, "x" * MAX_NOTE_CHARS)

    assert len(added.note.note) == MAX_NOTE_CHARS
    with pytest.raises(Validation) as raised:
        _ = await setup.notes.add(PUBLIC, "x" * (MAX_NOTE_CHARS + 1))

    assert raised.value.message == (
        f"A note holds at most {MAX_NOTE_CHARS} characters; this one has {MAX_NOTE_CHARS + 1}."
    )
    assert raised.value.hint == "Shorten the note or split it into several."


async def test_a_note_on_a_missing_document_or_without_permission_is_refused() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().notes.add(999, "x")
    with pytest.raises(PermissionDenied):
        _ = await Setup(READER[0]).notes.add(PUBLIC, "x")


async def test_deleting_a_note_without_confirm_changes_nothing_and_says_what_would_happen() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.notes.delete(PUBLIC, 1)

    message = raised.value.message
    assert message == (
        "Nothing was changed. Confirming would delete note 1 (by admin, "
        f"{len(PUBLIC_NOTE)} characters, starting '{PUBLIC_NOTE}') from document 1 "
        f"'{PUBLIC_TITLE}' for good; notes have no trash."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert setup.stored_notes == [PUBLIC_NOTE]


async def test_a_note_without_a_known_author_is_described_without_one() -> None:
    setup = Setup(writes=False)
    note = setup.state.documents[0].notes[0]
    setup.state.documents[0].notes[0] = note.model_copy(update={"user": None})

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.notes.delete(PUBLIC, 1)

    assert "delete note 1 (16 characters, starting" in raised.value.message


async def test_the_preview_shows_a_short_excerpt_never_the_whole_note() -> None:
    setup = Setup(writes=False)
    long_note = "start " + "word " * 100 + "THE-END"
    setup.state.documents[0].notes[0] = (
        setup.state.documents[0].notes[0].model_copy(update={"note": long_note})
    )

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.notes.delete(PUBLIC, 1)

    assert quoted(long_note, EXCERPT_CHARS) in raised.value.message
    assert len(quoted(long_note, EXCERPT_CHARS)) == EXCERPT_CHARS + 2
    assert "THE-END" not in raised.value.message
    assert f"{len(long_note)} characters" in raised.value.message


async def test_a_confirmed_note_deletion_removes_that_note_only() -> None:
    setup = Setup()
    kept = await setup.notes.add(PUBLIC, "keep me")

    deleted = await setup.notes.delete(PUBLIC, 1, confirm=True)

    assert (deleted.document_id, deleted.note_id) == (PUBLIC, 1)
    assert deleted.web_url == f"{WEB}/documents/1/details"
    assert [n.id for n in setup.state.documents[0].notes] == [kept.note.id]


async def test_an_unknown_note_is_not_found_with_or_without_confirm_and_nothing_is_written() -> (
    None
):
    setup = Setup(writes=False)

    for confirm in (False, True):
        with pytest.raises(NotFound) as raised:
            _ = await setup.notes.delete(PUBLIC, 99, confirm=confirm)
        assert raised.value.message == "Document 1 has no note 99."
        assert raised.value.hint == "List the notes with get_document and include notes."
    assert setup.stored_notes == [PUBLIC_NOTE]


async def test_a_user_who_may_not_view_notes_cannot_delete_them() -> None:
    # The preview needs the note's text length, i.e. view_note; without it nothing is deleted.
    setup = Setup(READER[0])

    with pytest.raises(PermissionDenied):
        _ = await setup.notes.delete(PUBLIC, 1, confirm=True)

    assert setup.stored_notes == [PUBLIC_NOTE]


async def test_deleting_a_document_without_confirm_changes_nothing_and_says_what_would_happen() -> (
    None
):
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.removal.delete(PUBLIC)

    message = raised.value.message
    assert message.startswith("Nothing was changed.")
    assert f"document {PUBLIC} '{PUBLIC_TITLE}'" in message
    assert "trash" in message
    assert "restore" in message
    assert "confirm=true" in (raised.value.hint or "")
    assert len(setup.state.documents) == 2
    assert setup.state.trash == []


async def test_a_confirmed_deletion_moves_the_document_to_the_trash_and_says_so() -> None:
    setup = Setup()

    result = await setup.removal.delete(PUBLIC, confirm=True)

    assert (result.document_id, result.title, result.in_trash) == (PUBLIC, PUBLIC_TITLE, True)
    assert result.message == (
        f"Document 1 '{PUBLIC_TITLE}' is in the trash. It stays there until the trash is emptied "
        "(by default after 30 days) and can be restored in the Paperless web UI, or with "
        "restore_from_trash if the trash toolset is enabled."
    )
    assert [d.id for d in setup.state.trash] == [PUBLIC]
    assert [d.id for d in setup.state.documents] == [2]


async def test_a_missing_document_is_not_found_and_a_reader_is_refused_on_confirm() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().removal.delete(999)
    reader = Setup(READER[0])

    with pytest.raises(PermissionDenied):
        _ = await reader.removal.delete(PUBLIC, confirm=True)

    assert len(reader.state.documents) == 2


async def test_the_taxonomy_counts_forget_a_deleted_document() -> None:
    setup = Setup()
    before = {t.name: t.document_count for t in await setup.taxonomy.items("tag")}
    assert before["e2e-finance"] == 1

    _ = await setup.removal.delete(PUBLIC, confirm=True)

    after = {t.name: t.document_count for t in await setup.taxonomy.items("tag")}
    assert after["e2e-finance"] == 0
    correspondent = {t.name: t.document_count for t in await setup.taxonomy.items("correspondent")}
    assert correspondent["e2e-acme"] == 0


async def test_an_unconfirmed_deletion_leaves_the_cache_alone() -> None:
    setup = Setup(writes=False)
    _ = await setup.taxonomy.items("tag")
    setup.state.documents[0].tags = []  # a change made behind the cache's back

    with pytest.raises(ConfirmationRequired):
        _ = await setup.removal.delete(PUBLIC)

    assert {t.name: t.document_count for t in await setup.taxonomy.items("tag")}["e2e-finance"] == 1
