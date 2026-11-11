"""DocumentService.search: names to ids, invalid combinations, and readable rows."""

from datetime import date

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeTaxonomy, FakeUser, seeded

from paperless_mcp.domain.documents import DocumentQuery, FieldValue, NameFilters, Ref
from paperless_mcp.domain.errors import Ambiguous, NotFound, PermissionDenied, Validation
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.documents import DocumentService
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
