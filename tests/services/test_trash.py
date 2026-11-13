"""TrashService: list, restore, and empty behind the confirmation flow (PLMCP-27).

Fakes of the "unconfirmed" cases are created without `allow_writes`: the write guard raises on
any write, so a passing test proves nothing was written. `everything` is tested on the fake only:
on the shared live stack it would destroy other tests' entries.
"""

from datetime import UTC, datetime
from typing import final

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded

from paperless_mcp.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
)
from paperless_mcp.domain.versions import VersionUpload
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.taxonomy import TaxonomyResolver
from paperless_mcp.services.trash import LOST, MAX_IDS, SHOWN, TrashService

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

DAY = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, writes: bool = True, max_all: int = 1000) -> None:
        self.state: FakeState = seeded()
        self.writer: FakePaperless = FakePaperless(self.state, user=ADMIN[0], allow_writes=True)
        self.fake: FakePaperless = FakePaperless(self.state, user=user, allow_writes=writes)
        self.taxonomy: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.service: TrashService = TrashService(
            self.fake, self.fake, self.taxonomy, max_all=max_all
        )

    def document(self, title: str, *, owner: int | None = 2) -> int:
        number = max(d.id for d in (*self.state.documents, *self.state.trash)) + 1
        self.state.documents.append(FakeDocument(id=number, title=title, owner=owner))
        return number

    async def trashed(self, title: str, *, owner: int | None = 2) -> int:
        """A new document that is in the trash since DAY."""
        number = self.document(title, owner=owner)
        await self.writer.delete_document(number)
        [entry] = [d for d in self.state.trash if d.id == number]
        entry.deleted_at = DAY
        return number

    async def version(self, title: str) -> tuple[int, int]:
        """A document with a second version that is in the trash since DAY: (document, version)."""
        number = self.document(title)
        upload = VersionUpload(filename="v.txt", mime_type="text/plain", content=b"v")
        [version] = (
            await self.writer.get_task(await self.writer.upload_document_version(number, upload))
        ).document_ids
        _ = await self.writer.delete_version(number, version)
        [entry] = [d for d in self.state.trash if d.id == version]
        entry.deleted_at = DAY
        return number, version

    def ids(self) -> list[int]:
        return [d.id for d in self.state.trash]

    def alive(self) -> list[int]:
        return [d.id for d in self.state.documents]


# --- list ---------------------------------------------------------------------------------------


async def test_the_trash_is_listed_newest_first_with_a_page_and_the_total() -> None:
    setup = Setup()
    older = await setup.trashed("older")
    newer = await setup.trashed("newer")
    [entry] = [d for d in setup.state.trash if d.id == newer]
    entry.deleted_at = datetime(2026, 11, 11, tzinfo=UTC)

    result = await setup.service.list(page=1, page_size=10)

    assert (result.count, result.page, result.next_page) == (2, 1, None)
    assert [(e.id, e.title, e.deleted_at, e.version_of) for e in result.entries] == [
        (newer, "newer", datetime(2026, 11, 11, tzinfo=UTC), None),
        (older, "older", DAY, None),
    ]


async def test_a_page_of_the_trash_names_the_next_one() -> None:
    setup = Setup()
    for number in range(3):
        _ = await setup.trashed(f"doc {number}")

    first = await setup.service.list(page=1, page_size=2)
    second = await setup.service.list(page=2, page_size=2)

    assert (first.count, first.next_page, len(first.entries)) == (3, 2, 2)
    assert (second.next_page, len(second.entries)) == (None, 1)
    with pytest.raises(NotFound):
        _ = await setup.service.list(page=3, page_size=2)


async def test_a_user_without_the_view_permission_cannot_list() -> None:
    setup = Setup("e2e-blind")

    with pytest.raises(PermissionDenied):
        _ = await setup.service.list(page=1, page_size=25)


# --- restore ------------------------------------------------------------------------------------


async def test_restoring_brings_documents_back_and_says_what_each_was() -> None:
    setup = Setup()
    one, two = await setup.trashed("Invoice one"), await setup.trashed("Invoice two")
    document, version = await setup.version("Report")

    result = await setup.service.restore([one, version, two])

    assert [e.id for e in result.restored] == [one, version, two]
    assert result.message == (
        "Restored 2 documents and 1 version from the trash: "
        f"document {one} 'Invoice one', version {version} of document {document} 'Report' "
        f"(attached to the document again) and document {two} 'Invoice two'."
    )
    assert setup.ids() == []
    assert {one, two} <= set(setup.alive())


async def test_restoring_one_document_reads_in_the_singular() -> None:
    setup = Setup()
    one = await setup.trashed("Only one")

    result = await setup.service.restore([one])

    assert result.message == f"Restored 1 document from the trash: document {one} 'Only one'."


async def test_restoring_forgets_the_document_counts_of_the_taxonomy() -> None:
    setup = Setup()
    tags = await setup.taxonomy.items("tag")
    number = setup.document("Tagged")
    [document] = [d for d in setup.state.documents if d.id == number]
    document.tags = [tags[0].id]
    await setup.writer.delete_document(number)
    before = (await setup.taxonomy.items("tag"))[0].document_count

    _ = await setup.service.restore([number])

    assert (await setup.taxonomy.items("tag"))[0].document_count == (before or 0) + 1


async def test_ids_are_deduplicated_and_an_empty_or_huge_list_is_refused() -> None:
    setup = Setup()
    one = await setup.trashed("Twice")

    result = await setup.service.restore([one, one])
    assert [e.id for e in result.restored] == [one]

    with pytest.raises(Validation) as empty:
        _ = await setup.service.restore([])
    assert empty.value.message == "Name at least one trash entry."
    assert empty.value.hint == "Pass the ids from list_trash."
    with pytest.raises(Validation) as huge:
        _ = await setup.service.restore(list(range(1, MAX_IDS + 2)))
    assert huge.value.message == f"Name at most {MAX_IDS} trash entries at once; got 101."
    assert huge.value.hint == "Work in several steps."


async def test_exactly_the_most_ids_are_accepted() -> None:
    setup = Setup()
    ids = [await setup.trashed(f"Doc {number}") for number in range(MAX_IDS)]

    result = await setup.service.restore(ids)

    assert [e.id for e in result.restored] == ids


async def test_entries_on_later_pages_of_the_trash_are_found_too() -> None:
    # 120 entries make two pages of 100; the oldest is on the second one.
    setup = Setup()
    oldest = await setup.trashed("Oldest")
    for number in range(119):
        _ = await setup.trashed(f"Doc {number}")
    [entry] = [d for d in setup.state.trash if d.id == oldest]
    entry.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    newest = max(setup.ids())

    result = await setup.service.restore([oldest, newest])

    assert [e.id for e in result.restored] == [oldest, newest]


async def test_an_id_that_is_not_in_the_trash_is_not_found_and_nothing_is_restored() -> None:
    setup = Setup(writes=False)  # a write would raise
    one = await setup.trashed("Here")

    with pytest.raises(NotFound) as raised:
        _ = await setup.service.restore([one, 4242, 4243])

    assert raised.value.message == "Not in the trash: 4242, 4243."
    assert raised.value.hint == (
        "List the trash with list_trash. Entries of documents the user does not own are not "
        "listed, and a version is listed only while its document is not in the trash."
    )
    assert setup.ids() == [one]


async def test_a_version_cannot_be_restored_while_its_document_is_in_the_trash() -> None:
    setup = Setup()
    document, version = await setup.version("Both")
    await setup.writer.delete_document(document)

    with pytest.raises(NotFound, match=f"Not in the trash: {version}"):
        _ = await setup.service.restore([version])

    result = await setup.service.restore([document])
    assert [e.id for e in result.restored] == [document]


async def test_a_user_without_the_delete_permission_may_not_restore() -> None:
    setup = Setup(READER[0])
    [public] = [d.id for d in setup.state.documents if d.title == "e2e-public-invoice"]
    await setup.writer.delete_document(public)

    with pytest.raises(PermissionDenied):
        _ = await setup.service.restore([public])


# --- empty --------------------------------------------------------------------------------------


async def test_emptying_never_defaults_to_everything() -> None:
    setup = Setup(writes=False)
    _ = await setup.trashed("Keep me")

    nothing: list[list[int] | None] = [None, []]
    for ids in nothing:
        with pytest.raises(Validation) as raised:
            _ = await setup.service.empty(ids, everything=False, confirm=True)
        assert raised.value.message == "Name what to empty: nothing was emptied."
        assert raised.value.hint == (
            "Pass document_ids from list_trash, or all=true to empty the whole trash."
        )
    assert len(setup.ids()) == 1


async def test_ids_and_all_together_are_ambiguous() -> None:
    setup = Setup(writes=False)
    one = await setup.trashed("Keep me")

    with pytest.raises(Validation) as raised:
        _ = await setup.service.empty([one], everything=True, confirm=True)

    assert raised.value.message == "Pass either document_ids or all=true, not both."
    assert raised.value.hint == "Use document_ids for some entries, all=true for every one."


async def test_emptying_ids_without_confirm_names_what_would_be_destroyed_for_good() -> None:
    setup = Setup(writes=False)
    invoice = await setup.trashed("Invoice March")
    document, version = await setup.version("Report")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.empty([invoice, version], everything=False)

    assert raised.value.message == (
        "Nothing was changed. Confirming would permanently delete 2 entries from the Paperless "
        f"trash: document {invoice} 'Invoice March' (deleted 2026-11-10) and version {version} of "
        f"document {document} 'Report' (deleted 2026-11-10). They cannot be restored afterwards: "
        "the files, text, notes and metadata of documents, and the deleted versions, are lost "
        "for good."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert sorted(setup.ids()) == sorted([invoice, version])


async def test_exactly_the_shown_number_of_entries_are_all_named_and_none_counted() -> None:
    setup = Setup(writes=False)
    ids = [await setup.trashed(f"Doc {number}") for number in range(SHOWN)]

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.empty(ids, everything=False)

    assert "more" not in raised.value.message
    assert all(f"'Doc {number}'" in raised.value.message for number in range(SHOWN))


async def test_emptying_one_entry_reads_in_the_singular() -> None:
    setup = Setup(writes=False)
    one = await setup.trashed("Single")

    with pytest.raises(
        ConfirmationRequired, match=r"permanently delete 1 entry from the Paperless"
    ):
        _ = await setup.service.empty([one], everything=False)


async def test_a_confirmed_empty_removes_exactly_the_named_entries() -> None:
    setup = Setup()
    gone, stays = await setup.trashed("Gone"), await setup.trashed("Stays")

    result = await setup.service.empty([gone, gone], everything=False, confirm=True)

    assert (result.document_ids, result.count) == ((gone,), 1)
    assert result.message == f"Permanently deleted 1 entry from the trash: document {gone} 'Gone'."
    assert setup.ids() == [stays]
    assert gone not in setup.alive()


async def test_emptying_something_not_in_the_trash_is_not_found_before_asking_to_confirm() -> None:
    setup = Setup(writes=False)
    one = await setup.trashed("Here")

    for confirm in (False, True):
        with pytest.raises(NotFound, match="Not in the trash: 4242"):
            _ = await setup.service.empty([one, 4242], everything=False, confirm=confirm)
    assert setup.ids() == [one]


async def test_all_without_confirm_counts_and_names_the_entries_and_changes_nothing() -> None:
    setup = Setup(writes=False)
    titles = [f"Doc {number:02d}" for number in range(SHOWN + 3)]
    for title in titles:
        _ = await setup.trashed(title)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.empty(None, everything=True)

    message = raised.value.message
    head = f"Nothing was changed. Confirming would permanently delete ALL {len(titles)} entries of "
    assert message.startswith(head + "the Paperless trash (every entry the user can see): ")
    assert message.endswith(f"and 3 more. {LOST}.")
    shown, hidden = titles[-SHOWN:], titles[:-SHOWN]  # newest first: the last ones made
    assert all(f"'{title}'" in message for title in shown)
    assert not any(f"'{title}'" in message for title in hidden)
    assert len(setup.ids()) == len(titles)


async def test_all_with_confirm_empties_every_entry_the_user_can_see() -> None:
    setup = Setup()
    emptied = [await setup.trashed(f"Doc {number}") for number in range(3)]

    result = await setup.service.empty(None, everything=True, confirm=True)

    assert result.count == 3
    assert sorted(result.document_ids) == sorted(emptied)
    assert result.message == "Permanently deleted ALL 3 entries of the trash."
    assert setup.ids() == []


async def test_all_reads_every_page_of_the_trash() -> None:
    setup = Setup()
    for number in range(205):
        _ = await setup.trashed(f"Doc {number}")

    result = await setup.service.empty(None, everything=True, confirm=True)

    assert result.count == 205
    assert setup.ids() == []


async def test_a_user_lists_only_the_entries_of_documents_they_own_or_nobody_owns() -> None:
    setup = Setup(READER[0])
    mine = await setup.trashed("Theirs and not mine", owner=2)
    shared = await setup.trashed("Nobody's", owner=None)

    listed = await setup.service.list(page=1, page_size=10)

    assert [e.id for e in listed.entries] == [shared]
    assert mine in setup.ids()


async def test_all_on_an_empty_trash_has_nothing_to_confirm() -> None:
    setup = Setup(writes=False)

    result = await setup.service.empty(None, everything=True)

    assert (result.document_ids, result.count) == ((), 0)
    assert result.message == "The trash is empty: nothing was deleted."


async def test_all_refuses_a_trash_larger_than_the_limit_and_points_to_ids() -> None:
    setup = Setup(writes=False, max_all=2)
    for number in range(3):
        _ = await setup.trashed(f"Doc {number}")

    with pytest.raises(Validation) as raised:
        _ = await setup.service.empty(None, everything=True, confirm=True)

    assert raised.value.message == "The trash holds 3 entries; all=true handles at most 2 at once."
    assert raised.value.hint == "Empty in steps with document_ids from list_trash."


async def test_all_handles_exactly_the_limit() -> None:
    setup = Setup(max_all=3)
    for number in range(3):
        _ = await setup.trashed(f"Doc {number}")

    result = await setup.service.empty(None, everything=True, confirm=True)

    assert result.count == 3


async def test_ids_beyond_the_limit_are_refused() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match=f"at most {MAX_IDS}"):
        _ = await setup.service.empty(list(range(1, MAX_IDS + 2)), everything=False, confirm=True)


async def test_a_user_without_the_delete_permission_cannot_empty() -> None:
    setup = Setup(READER[0])
    [public] = [d.id for d in setup.state.documents if d.title == "e2e-public-invoice"]
    await setup.writer.delete_document(public)
    [entry] = [d for d in setup.state.trash if d.id == public]
    entry.owner = None  # visible to the reader

    with pytest.raises(PermissionDenied):
        _ = await setup.service.empty([public], everything=False, confirm=True)

    assert setup.ids() == [public]
