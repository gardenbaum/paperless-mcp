"""VersionService: list, label, delete behind confirm and merge behind confirm (PLMCP-24).

The fakes of the "unconfirmed" cases are created without `allow_writes`: the fake's write guard
raises `WriteNotAllowedError` on any write, so a passing test proves nothing was written.
"""

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
from paperless_mcp.services.versions import MAX_LABEL, MAX_SOURCES, VersionService

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

WEB = "https://paperless.example"
PUBLIC = 1
PUBLIC_TITLE = "e2e-public-invoice"
LABEL_HINT = "Merge one document to label it, or use set_document_version_label afterwards."


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, writes: bool = True) -> None:
        self.state: FakeState = seeded()
        self.writer: FakePaperless = FakePaperless(self.state, user=ADMIN[0], allow_writes=True)
        self.fake: FakePaperless = FakePaperless(self.state, user=user, allow_writes=writes)
        self.service: VersionService = VersionService(self.fake, self.fake, web_url=WEB)

    async def add(self, document: int, text: str, label: str | None = None) -> int:
        """A text version of `document`, made behind the service's back; returns its id."""
        upload = VersionUpload(
            filename=f"{text}.txt", mime_type="text/plain", content=text.encode(), label=label
        )
        task = await self.writer.upload_document_version(document, upload)
        [version] = (await self.writer.get_task(task)).document_ids
        return version

    def document(self, *, owner: int | None = 2) -> int:
        """A new document (the admin's unless `owner` says otherwise); returns its id."""
        number = max(d.id for d in self.state.documents) + 1
        self.state.documents.append(FakeDocument(id=number, title=f"doc {number}", owner=owner))
        return number

    def as_user(self, user: str) -> VersionService:
        """The service for another user of the same instance."""
        fake = FakePaperless(self.state, user=user, allow_writes=True)
        return VersionService(fake, fake, web_url=WEB)

    def version_ids(self, document: int) -> list[int]:
        [found] = [d for d in self.state.documents if d.id == document]
        return [v.id for v in found.versions]


# --- list ---------------------------------------------------------------------------------------


async def test_the_versions_come_newest_first_with_the_current_one_named() -> None:
    setup = Setup()
    first = await setup.add(PUBLIC, "one", "draft")
    second = await setup.add(PUBLIC, "two")

    result = await setup.service.list_versions(PUBLIC)

    assert (result.document_id, result.title) == (PUBLIC, PUBLIC_TITLE)
    assert result.current_version_id == second
    assert [(v.id, v.version_label, v.is_root) for v in result.versions] == [
        (second, None, False),
        (first, "draft", False),
        (PUBLIC, None, True),
    ]
    assert result.web_url == f"{WEB}/documents/1/details"


async def test_a_document_with_one_version_is_its_own_current_version() -> None:
    result = await Setup().service.list_versions(PUBLIC)

    assert result.current_version_id == PUBLIC
    assert [v.is_root for v in result.versions] == [True]


async def test_the_versions_of_a_missing_or_hidden_document_are_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().service.list_versions(999)
    with pytest.raises(NotFound):
        _ = await Setup(READER[0]).service.list_versions(2)  # the private document


# --- labels -------------------------------------------------------------------------------------


async def test_a_label_is_set_trimmed_and_returned_with_its_version() -> None:
    setup = Setup()
    version = await setup.add(PUBLIC, "one")

    result = await setup.service.set_label(PUBLIC, version, "  final  ")

    assert (result.document_id, result.version.id, result.version.version_label) == (
        PUBLIC,
        version,
        "final",
    )
    assert result.web_url == f"{WEB}/documents/1/details"
    assert [v.version_label for v in setup.state.documents[0].versions] == ["final", None]


@pytest.mark.parametrize("label", ["", "   "])
async def test_an_empty_label_clears_it(label: str) -> None:
    setup = Setup()
    version = await setup.add(PUBLIC, "one", "old")

    result = await setup.service.set_label(PUBLIC, version, label)

    assert result.version.version_label is None


async def test_a_label_of_64_characters_fits_and_65_do_not() -> None:
    setup = Setup()
    version = await setup.add(PUBLIC, "one")

    fits = await setup.service.set_label(PUBLIC, version, "x" * MAX_LABEL)
    with pytest.raises(Validation) as raised:
        _ = await setup.service.set_label(PUBLIC, version, "y" * (MAX_LABEL + 1))

    assert fits.version.version_label == "x" * MAX_LABEL
    assert raised.value.message == "A version label holds at most 64 characters; this one has 65."
    assert raised.value.hint == "Shorten the label."


async def test_a_label_for_an_unknown_version_or_without_permission_is_refused() -> None:
    setup = Setup()

    with pytest.raises(NotFound):
        _ = await setup.service.set_label(PUBLIC, 999, "x")
    with pytest.raises(PermissionDenied):
        _ = await setup.as_user(READER[0]).set_label(PUBLIC, PUBLIC, "x")


# --- deleting a version -----------------------------------------------------------------------


async def test_deleting_without_confirm_changes_nothing_and_says_what_would_happen() -> None:
    setup = Setup(writes=False)
    first = await setup.add(PUBLIC, "one", "draft")
    second = await setup.add(PUBLIC, "two")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.delete(PUBLIC, second)
    with pytest.raises(ConfirmationRequired) as older:
        _ = await setup.service.delete(PUBLIC, first)

    added = setup.state.documents[0].versions[0].added.date().isoformat()
    assert raised.value.message == (
        f"Nothing was changed. Confirming would delete version {second} (added {added}) of "
        f"document 1 '{PUBLIC_TITLE}'; it moves to the Paperless trash and can be restored from "
        f"there; the document's file and text then revert to version {first}."
    )
    assert older.value.message == (
        f"Nothing was changed. Confirming would delete version {first} 'draft' (added {added}) of "
        f"document 1 '{PUBLIC_TITLE}'; it moves to the Paperless trash and can be restored from "
        f"there; version {second} stays the newest."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert setup.version_ids(PUBLIC) == [second, first, PUBLIC]


async def test_a_confirmed_delete_removes_the_version_and_names_the_current_one() -> None:
    setup = Setup()
    first = await setup.add(PUBLIC, "one")
    second = await setup.add(PUBLIC, "two")

    result = await setup.service.delete(PUBLIC, second, confirm=True)

    assert (result.document_id, result.version_id, result.current_version_id) == (
        PUBLIC,
        second,
        first,
    )
    assert "in the Paperless trash" in result.message
    assert "restored" in result.message
    assert setup.version_ids(PUBLIC) == [first, PUBLIC]


async def test_the_root_version_is_never_deleted_even_with_confirm() -> None:
    setup = Setup(writes=False)

    for confirm in (False, True):
        with pytest.raises(Validation) as raised:
            _ = await setup.service.delete(PUBLIC, PUBLIC, confirm=confirm)
        assert raised.value.message == (
            "Version 1 is the root version, the original file of document 1."
        )
        assert raised.value.hint == "Delete the whole document with delete_document instead."


async def test_deleting_an_unknown_version_is_not_found_before_asking_for_confirmation() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound) as raised:
        _ = await setup.service.delete(PUBLIC, 999)

    assert raised.value.message == "Document 1 has no version 999."
    assert raised.value.hint == "List the versions with list_document_versions."


async def test_deleting_a_version_needs_the_permission() -> None:
    setup = Setup()
    version = await setup.add(PUBLIC, "one")

    with pytest.raises(PermissionDenied):
        _ = await setup.as_user(READER[0]).delete(PUBLIC, version, confirm=True)

    assert setup.version_ids(PUBLIC) == [version, PUBLIC]


# --- merging --------------------------------------------------------------------------------


async def test_merging_without_confirm_changes_nothing_and_says_what_would_happen() -> None:
    setup = Setup(writes=False)
    other, third = setup.document(), setup.document()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.merge(PUBLIC, [other, third], None)

    assert raised.value.message == (
        "Nothing was changed. Confirming would merge document "
        f"{other} 'doc {other}' and document {third} 'doc {third}' into document 1 "
        f"'{PUBLIC_TITLE}' as its newest versions: the merged documents vanish for good, not even "
        "into the trash. Their titles, tags, correspondents, notes and other metadata are lost; "
        "only their files stay, as versions of document 1."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert {d.id for d in setup.state.documents} >= {PUBLIC, other, third}


async def test_merging_one_document_without_a_label_says_so_in_the_singular() -> None:
    setup = Setup(writes=False)
    other = setup.document()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.merge(PUBLIC, [other], None)

    assert raised.value.message == (
        f"Nothing was changed. Confirming would merge document {other} 'doc {other}' into "
        f"document 1 '{PUBLIC_TITLE}' as its newest version: the merged document vanishes for "
        "good, not even into the trash. Its title, tags, correspondents, notes and other "
        "metadata are lost; only its file stays, as a version of document 1."
    )


async def test_merging_three_documents_lists_them_with_commas_and_an_and() -> None:
    setup = Setup(writes=False)
    one, two, three = setup.document(), setup.document(), setup.document()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.merge(PUBLIC, [one, two, three], None)

    assert (
        f"merge document {one} 'doc {one}', document {two} 'doc {two}' and document {three} "
        f"'doc {three}' into document 1"
    ) in raised.value.message


async def test_the_merge_message_names_the_label() -> None:
    setup = Setup(writes=False)
    other = setup.document()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.merge(PUBLIC, [other], " scan ")

    assert f"merge document {other} 'doc {other}' into document 1" in raised.value.message
    assert "as its newest version, labelled 'scan': the merged document vanishes" in (
        raised.value.message
    )
    assert "Its title, tags, correspondents, notes and other metadata are lost" in (
        raised.value.message
    )


async def test_a_confirmed_merge_returns_the_new_versions_and_the_sources_are_gone() -> None:
    setup = Setup()
    one, two = setup.document(), setup.document()

    result = await setup.service.merge(PUBLIC, [one, two], None, confirm=True)

    assert (result.document_id, result.merged_document_ids) == (PUBLIC, (one, two))
    assert [v.id for v in result.versions] == [two, one, PUBLIC]
    assert result.web_url == f"{WEB}/documents/1/details"
    assert {d.id for d in setup.state.documents}.isdisjoint({one, two})


async def test_a_confirmed_merge_of_one_document_passes_the_label_trimmed() -> None:
    setup = Setup()
    one = setup.document()

    result = await setup.service.merge(PUBLIC, [one], " scan ", confirm=True)

    assert [v.version_label for v in result.versions] == ["scan", None]


@pytest.mark.parametrize(
    ("sources", "label", "message", "hint"),
    [
        (
            [],
            None,
            "Name at least one document to merge into document 1.",
            "Pass the ids of the documents in source_document_ids.",
        ),
        (
            [1],
            None,
            "Document 1 cannot be merged into itself.",
            "List only the other documents as sources.",
        ),
        (
            [10, 10],
            None,
            "Document 10 is listed twice.",
            "List each document once.",
        ),
        (
            [10, 11],
            "x",
            "A label fits only when one document is merged.",
            LABEL_HINT,
        ),
        (
            [10],
            "x" * 65,
            "A version label holds at most 64 characters; this one has 65.",
            "Shorten the label.",
        ),
    ],
)
async def test_an_invalid_merge_is_refused_before_anything_is_read_or_asked(
    sources: list[int], label: str | None, message: str, hint: str | None
) -> None:
    setup = Setup(writes=False)
    setup.state.documents.extend(FakeDocument(id=n, title=f"doc {n}", owner=2) for n in (10, 11))

    for confirm in (False, True):
        with pytest.raises(Validation) as raised:
            _ = await setup.service.merge(PUBLIC, sources, label, confirm=confirm)
        assert not isinstance(raised.value, ConfirmationRequired)
        assert raised.value.message == message
        assert raised.value.hint == hint


async def test_no_more_than_the_limit_of_documents_is_merged_at_once() -> None:
    setup = Setup(writes=False)
    sources = list(range(100, 100 + MAX_SOURCES + 1))

    with pytest.raises(Validation) as raised:
        _ = await setup.service.merge(PUBLIC, sources, None)

    assert raised.value.message == f"At most {MAX_SOURCES} documents are merged at once; got 21."
    assert raised.value.hint == "Merge in several steps."


async def test_the_limit_itself_is_allowed() -> None:
    setup = Setup(writes=False)
    sources = [setup.document() for _ in range(MAX_SOURCES)]

    with pytest.raises(ConfirmationRequired):
        _ = await setup.service.merge(PUBLIC, sources, None)


async def test_a_source_with_versions_is_refused_before_asking_for_confirmation() -> None:
    setup = Setup(writes=False)
    holder = setup.document()
    _ = await setup.add(holder, "has one")

    with pytest.raises(Validation) as raised:
        _ = await setup.service.merge(PUBLIC, [holder], None)

    assert raised.value.message == f"Document {holder} has 2 versions and cannot be merged."
    assert raised.value.hint == (
        "Only a document with a single version can be merged. Merge into that document "
        "instead, or delete its other versions first."
    )


async def test_missing_documents_are_not_found_and_nothing_is_merged() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound):
        _ = await setup.service.merge(999, [PUBLIC], None, confirm=True)
    with pytest.raises(NotFound):
        _ = await setup.service.merge(PUBLIC, [999], None, confirm=True)
    with pytest.raises(NotFound):
        _ = await Setup(READER[0], writes=False).service.merge(PUBLIC, [2], None, confirm=True)


async def test_merging_needs_the_permission() -> None:
    setup = Setup()
    other = setup.document(owner=None)  # visible to the reader, who may not change documents

    with pytest.raises(PermissionDenied):
        _ = await setup.as_user(READER[0]).merge(PUBLIC, [other], None, confirm=True)

    assert other in {d.id for d in setup.state.documents}
