"""DocumentService.search: names to ids, invalid combinations, and readable rows."""

from datetime import UTC, date, datetime

import pytest
from fakes.accounts import ADMIN, PUBLIC_NOTE, READER
from fakes.paperless import FakePaperless, FakeTaxonomy, FakeUser, seeded

from paperless_mcp.domain.documents import (
    DocumentQuery,
    FieldValue,
    HistoryEntry,
    NameFilters,
    Ref,
    SuggestionNames,
    Suggestions,
)
from paperless_mcp.domain.errors import Ambiguous, NotFound, PermissionDenied, Validation
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.documents import DEFAULT_SLICE, DocumentService
from paperless_mcp.services.taxonomy import TaxonomyResolver

pytestmark = pytest.mark.anyio

WEB = "https://paperless.example/"


def service(fake: FakePaperless) -> DocumentService:
    resolver = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=60.0)
    return DocumentService(fake, resolver, web_url=WEB)


def admin() -> DocumentService:
    return service(FakePaperless(seeded(), user=ADMIN[0]))


async def titles(search: DocumentService, names: NameFilters) -> list[str]:
    found = await search.search(names, DocumentQuery(), page=1, page_size=25)
    return [document.title for document in found.documents]


@pytest.mark.decision("decision-5")
async def test_names_resolve_to_ids_ignoring_case() -> None:
    search = admin()

    assert await titles(search, NameFilters(correspondent="E2E-ACME")) == ["e2e-public-invoice"]
    assert await titles(search, NameFilters(tags_all=("e2e-finance", 2))) == ["e2e-public-invoice"]
    assert await titles(search, NameFilters(tags_none=("e2e-inbox",), owner="admin")) == [
        "e2e-private-note"
    ]
    assert await titles(
        search, NameFilters(document_type="e2e-invoice", storage_path="e2e-archive")
    ) == ["e2e-public-invoice"]


@pytest.mark.decision("decision-5")
async def test_an_unknown_name_is_not_found_with_suggestions() -> None:
    with pytest.raises(NotFound) as raised:
        _ = await titles(admin(), NameFilters(tags_any=("e2e-financ",)))

    assert "'e2e-finance'" in (raised.value.hint or "")


@pytest.mark.decision("decision-5")
async def test_an_ambiguous_name_names_the_candidates() -> None:
    state = seeded()
    state.correspondents.append(FakeTaxonomy(id=2, name="E2E-ACME"))
    search = service(FakePaperless(state, user=ADMIN[0]))

    with pytest.raises(Ambiguous, match="id 2"):
        _ = await titles(search, NameFilters(correspondent="e2e-Acme"))


@pytest.mark.parametrize(
    ("names", "query", "complaint"),
    [
        (NameFilters(), DocumentQuery(query="x", more_like=1), "either query or more_like_id"),
        (
            NameFilters(),
            DocumentQuery(created_from=date(2026, 2, 1), created_to=date(2026, 1, 1)),
            "created_from is after created_to",
        ),
        (
            NameFilters(),
            DocumentQuery(added_from=date(2026, 2, 1), added_to=date(2026, 1, 1)),
            "added_from is after added_to",
        ),
        (
            NameFilters(tags_all=("e2e-finance",), tags_none=(1,)),
            DocumentQuery(),
            "both required and excluded: 'e2e-finance'",
        ),
    ],
)
async def test_contradicting_filters_are_rejected_before_asking_paperless(
    names: NameFilters, query: DocumentQuery, complaint: str
) -> None:
    with pytest.raises(Validation) as raised:
        _ = await admin().search(names, query, page=1, page_size=25)

    assert complaint in raised.value.message
    assert raised.value.hint


async def test_rows_carry_names_readable_values_and_a_web_link() -> None:
    found = await admin().search(
        NameFilters(), DocumentQuery(query="invoice"), page=1, page_size=25
    )

    [row] = found.documents
    assert (found.count, found.page, found.next_page) == (1, 1, None)
    assert row.correspondent == Ref(id=1, name="e2e-acme")
    assert row.document_type == Ref(id=1, name="e2e-invoice")
    assert row.storage_path == Ref(id=1, name="e2e-archive")
    assert row.tags == (
        Ref(id=1, name="e2e-finance"),
        Ref(id=2, name="e2e-invoices"),
        Ref(id=4, name="e2e-inbox"),
    )
    values = {value.name: value.value for value in row.custom_fields}
    assert values["e2e-select"] == "alpha"
    assert values["e2e-monetary"] == "EUR 12.50"
    assert values["e2e-documentlink"] == [2]
    assert values["e2e-boolean"] is True
    assert row.highlights == "E2E public <b>invoice</b> ACME 2026"
    assert row.web_url == "https://paperless.example/documents/1/details"
    assert "content" not in row.model_dump()


async def test_names_the_user_may_not_see_are_null() -> None:
    state = seeded()
    state.users.append(
        FakeUser(id=10, username="docs-only", permissions=frozenset({"view_document"}))
    )
    state.documents[0].tags.append(3)  # e2e-private: owned by the admin

    found = await service(FakePaperless(state, user="docs-only")).search(
        NameFilters(), DocumentQuery(title_contains="public"), page=1, page_size=25
    )

    [row] = found.documents
    assert row.correspondent == Ref(id=1, name=None)
    assert Ref(id=3, name=None) in row.tags
    assert FieldValue(id=10, name=None, value="opt-alpha") in row.custom_fields


async def test_a_hidden_tag_on_a_visible_document_has_no_name() -> None:
    state = seeded()
    state.documents[0].tags.append(3)

    found = await service(FakePaperless(state, user=READER[0])).search(
        NameFilters(), DocumentQuery(title_contains="public"), page=1, page_size=25
    )

    assert Ref(id=3, name=None) in found.documents[0].tags
    assert Ref(id=1, name="e2e-finance") in found.documents[0].tags


async def test_paging_passes_through() -> None:
    found = await admin().search(
        NameFilters(), DocumentQuery(title_contains="e2e"), page=1, page_size=1
    )

    assert (found.count, found.page, found.next_page, len(found.documents)) == (2, 1, 2, 1)


@pytest.mark.decision("decision-5")
async def test_an_owner_id_needs_no_user_list() -> None:
    search = service(FakePaperless(seeded(), user=READER[0]))

    assert await titles(search, NameFilters(owner=2)) == []
    assert await titles(search, NameFilters(owner="2")) == []


async def test_an_owner_name_without_the_user_list_asks_for_the_id() -> None:
    search = service(FakePaperless(seeded(), user=READER[0]))

    with pytest.raises(PermissionDenied) as raised:
        _ = await titles(search, NameFilters(owner="admin"))

    assert "id" in (raised.value.hint or "")


def history_entry(number: int) -> HistoryEntry:
    return HistoryEntry(
        id=1000 - number,
        timestamp=datetime(2026, 11, 11, 20, 0, tzinfo=UTC),
        action="update",
        actor="admin",
        changes={"title": ["a", "b"]},
    )


async def test_a_document_has_names_readable_fields_and_a_web_link() -> None:
    detail = await admin().get(1)

    assert (detail.id, detail.title, detail.created) == (
        1,
        "e2e-public-invoice",
        date(2026, 11, 11),
    )
    assert (detail.archive_serial_number, detail.owner) == (2, None)
    assert detail.correspondent == Ref(id=1, name="e2e-acme")
    assert detail.document_type == Ref(id=1, name="e2e-invoice")
    assert detail.storage_path == Ref(id=1, name="e2e-archive")
    assert [tag.name for tag in detail.tags] == ["e2e-finance", "e2e-invoices", "e2e-inbox"]
    assert (detail.original_file_name, detail.mime_type, detail.page_count) == (
        "e2e-public-invoice.pdf",
        "application/pdf",
        1,
    )
    assert detail.notes_count == 1
    assert detail.web_url == "https://paperless.example/documents/1/details"
    assert "content" not in detail.model_dump()


async def test_custom_fields_of_every_data_type_are_rendered_readably() -> None:
    detail = await admin().get(1)

    values = {value.name: value.value for value in detail.custom_fields}
    assert values == {
        "e2e-string": "hello",
        "e2e-url": "https://example.com/e2e",
        "e2e-date": "2026-01-15",
        "e2e-boolean": True,
        "e2e-integer": 42,
        "e2e-float": 3.25,
        "e2e-monetary": "EUR 12.50",
        "e2e-documentlink": [2],
        "e2e-longtext": "line one\nline two",
        "e2e-select": "alpha",
    }


async def test_nothing_is_included_unless_asked_for() -> None:
    detail = await admin().get(1)

    assert (detail.notes, detail.history, detail.file_metadata) == (None, None, None)
    assert (detail.versions, detail.suggestions, detail.unavailable) == (None, None, ())


async def test_include_adds_notes_history_metadata_versions_and_suggestions() -> None:
    state = seeded()
    state.documents[0].suggestions = Suggestions(
        correspondents=(1,),
        tags=(2, 77),
        document_types=(),
        storage_paths=(1,),
        dates=("2026-01-15",),
    )

    detail = await service(FakePaperless(state, user=ADMIN[0])).get(
        1, ("notes", "history", "metadata", "versions", "suggestions")
    )

    assert detail.notes is not None
    assert [(note.note, note.user) for note in detail.notes] == [(PUBLIC_NOTE, "admin")]
    assert detail.history is not None
    assert (detail.history.total, detail.history.truncated) == (2, False)
    assert [entry.action for entry in detail.history.entries] == ["update", "create"]
    assert "ACME" not in repr(detail.history)
    assert detail.file_metadata is not None
    assert detail.file_metadata.original_filename == "e2e-public-invoice.pdf"
    assert detail.versions is not None
    assert [(v.id, v.is_root) for v in detail.versions] == [(1, True)]
    assert detail.suggestions == SuggestionNames(
        correspondents=(Ref(id=1, name="e2e-acme"),),
        tags=(Ref(id=2, name="e2e-invoices"), Ref(id=77, name=None)),
        document_types=(),
        storage_paths=(Ref(id=1, name="e2e-archive"),),
        dates=("2026-01-15",),
    )
    assert detail.unavailable == ()


async def test_only_what_is_asked_for_is_fetched() -> None:
    detail = await admin().get(1, ("versions", "versions"))

    assert detail.versions is not None
    assert (detail.notes, detail.history, detail.file_metadata, detail.suggestions) == (
        None,
        None,
        None,
        None,
    )


async def test_sections_the_user_may_not_read_are_named_not_failed() -> None:
    detail = await service(FakePaperless(seeded(), user=READER[0])).get(
        1, ("history", "metadata", "notes")
    )

    assert detail.file_metadata is not None
    assert (detail.notes, detail.history) == (None, None)
    assert detail.unavailable == ("notes", "history")


async def test_a_long_history_keeps_the_newest_entries_and_says_so() -> None:
    state = seeded()
    state.documents[0].history = [history_entry(number) for number in range(60)]

    detail = await service(FakePaperless(state, user=ADMIN[0])).get(1, ("history",))

    assert detail.history is not None
    assert (detail.history.total, detail.history.truncated) == (60, True)
    assert [entry.id for entry in detail.history.entries] == [1000 - n for n in range(50)]


async def test_the_owner_is_named_if_the_user_may_list_users() -> None:
    detail = await admin().get(2)

    assert detail.owner == Ref(id=2, name="admin")


async def test_the_owner_has_no_name_without_permission_to_list_users() -> None:
    state = seeded()
    state.documents[0].owner = 2
    state.documents[0].viewers.add(3)

    detail = await service(FakePaperless(state, user=READER[0])).get(1)

    assert detail.owner == Ref(id=2, name=None)


async def test_a_missing_document_is_not_found_with_a_hint() -> None:
    with pytest.raises(NotFound) as raised:
        _ = await admin().get(999)

    assert raised.value.hint


async def test_a_document_the_user_may_not_see_is_not_found() -> None:
    with pytest.raises(NotFound, match="No document 2 exists that this user may see"):
        _ = await service(FakePaperless(seeded(), user=READER[0])).get(2)


def big_document(characters: int) -> DocumentService:
    state = seeded()
    state.documents[0].content = "x" * characters
    return service(FakePaperless(state, user=ADMIN[0]))


async def test_content_comes_in_slices_with_the_offset_of_the_next() -> None:
    state = seeded()
    state.documents[0].content = "0123456789"
    search = service(FakePaperless(state, user=ADMIN[0]))

    first = await search.content(1, offset=0, max_chars=4)
    middle = await search.content(1, offset=4, max_chars=4)
    last = await search.content(1, offset=8, max_chars=4)

    assert (first.content, first.offset, first.next_offset, first.total_chars) == (
        "0123",
        0,
        4,
        10,
    )
    assert (middle.content, middle.next_offset) == ("4567", 8)
    assert (last.content, last.next_offset, last.total_chars) == ("89", None, 10)
    assert last.document_id == 1
    assert last.web_url == "https://paperless.example/documents/1/details"


async def test_a_slice_ending_exactly_at_the_end_has_no_next() -> None:
    state = seeded()
    state.documents[0].content = "0123"

    last = await service(FakePaperless(state, user=ADMIN[0])).content(1, offset=0, max_chars=4)

    assert (last.content, last.next_offset) == ("0123", None)


@pytest.mark.parametrize("offset", [28, 29, 5000])
async def test_an_offset_at_or_past_the_end_is_an_empty_slice_with_the_total(offset: int) -> None:
    last = await admin().content(1, offset=offset, max_chars=20_000)

    assert (last.content, last.next_offset, last.total_chars) == ("", None, 28)
    assert last.offset == offset


async def test_a_document_of_over_a_million_characters_is_served_slice_by_slice() -> None:
    search = big_document(1_200_000)
    pieces: list[str] = []
    offset: int | None = 0

    while offset is not None:
        piece = await search.content(1, offset=offset, max_chars=DEFAULT_SLICE)
        assert piece.total_chars == 1_200_000
        assert len(piece.content) <= DEFAULT_SLICE
        pieces.append(piece.content)
        offset = piece.next_offset

    assert len(pieces) == 60
    assert "".join(pieces) == "x" * 1_200_000


async def test_the_content_of_a_missing_document_is_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await admin().content(999, offset=0, max_chars=10)
