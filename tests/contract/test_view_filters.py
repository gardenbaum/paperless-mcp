"""Contract of the document filters that saved views translate to (DocumentReader).

Same assertions against the fake and live Paperless 3.4.2, over the two seeded documents: the
public invoice (tags, correspondent, type, path, every custom field, no owner) and the private
note (owned by the admin, one tag, the document link).
"""

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from fakes.accounts import PRIVATE_ASN, PUBLIC_ASN

from paperless_mcp.domain.documents import DocumentQuery
from paperless_mcp.domain.errors import Validation

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from paperless_mcp.domain.documents import DocumentHit
    from paperless_mcp.domain.taxonomy import TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]

CONTRACT = "DocumentReader.search_documents"
PUBLIC, PRIVATE = "e2e-public-invoice", "e2e-private-note"
BOTH, NONE = {PUBLIC, PRIVATE}, set[str]()
SEEDED = DocumentQuery(title_contains="e2e-p")
DAY = timedelta(days=1)
LONG_AGO, FAR_AHEAD = date(2000, 1, 1), date(2100, 1, 1)


async def titles(ports: Ports, **filters: object) -> set[str]:
    query = SEEDED.model_copy(update=filters)
    found = await ports.documents.search_documents(query, page=1, page_size=100)
    return {hit.title for hit in found.hits}


async def ids(ports: Ports, kind: TaxonomyKind) -> dict[str, int]:
    return {item.name: item.id for item in await ports.taxonomy.list_taxonomy(kind)}


async def public(ports: Ports) -> DocumentHit:
    found = await ports.documents.search_documents(SEEDED, page=1, page_size=100)
    return next(hit for hit in found.hits if hit.title == PUBLIC)


@pytest.mark.contract(CONTRACT)
async def test_absent_and_listed_taxonomy_filters(backend: Backend) -> None:
    ports = backend.admin
    correspondent = (await ids(ports, "correspondent"))["e2e-acme"]
    document_type = (await ids(ports, "document_type"))["e2e-invoice"]
    storage_path = (await ids(ports, "storage_path"))["e2e-archive"]

    assert await titles(ports, correspondent_isnull=True) == {PRIVATE}
    assert await titles(ports, correspondent_isnull=False) == {PUBLIC}
    assert await titles(ports, correspondent_in=(correspondent, 9999)) == {PUBLIC}
    assert await titles(ports, correspondent_none=(correspondent,)) == {PRIVATE}
    assert await titles(ports, document_type_isnull=True) == {PRIVATE}
    assert await titles(ports, document_type_in=(document_type,)) == {PUBLIC}
    assert await titles(ports, document_type_none=(document_type,)) == {PRIVATE}
    assert await titles(ports, storage_path_isnull=False) == {PUBLIC}
    assert await titles(ports, storage_path_in=(storage_path,)) == {PUBLIC}
    assert await titles(ports, storage_path_none=(storage_path,)) == {PRIVATE}


@pytest.mark.contract(CONTRACT)
async def test_tagged_and_archive_serial_number_filters(backend: Backend) -> None:
    ports = backend.admin

    assert await titles(ports, tagged=True) == BOTH
    assert await titles(ports, tagged=False) == NONE
    assert await titles(ports, archive_serial_number=PUBLIC_ASN) == {PUBLIC}
    assert await titles(ports, asn_gt=PUBLIC_ASN) == {PRIVATE}
    assert await titles(ports, asn_lt=PRIVATE_ASN) == {PUBLIC}
    assert await titles(ports, asn_isnull=False) == BOTH
    assert await titles(ports, asn_isnull=True) == NONE


@pytest.mark.contract(CONTRACT)
async def test_created_added_and_modified_filters(backend: Backend) -> None:
    ports = backend.admin
    hit = await public(ports)
    created, added = hit.created, hit.added.date()

    assert await titles(ports, created_year=created.year, created_month=created.month) == BOTH
    assert await titles(ports, created_day=created.day) == BOTH
    assert await titles(ports, created_year=created.year + 1) == NONE
    assert await titles(ports, created_month=created.month % 12 + 1) == NONE
    assert await titles(ports, created_day=created.day % 28 + 1) == NONE
    assert PUBLIC in await titles(ports, created_gt=created - DAY)
    assert PUBLIC not in await titles(ports, created_gt=created)
    assert PUBLIC in await titles(ports, created_lt=created + DAY)
    assert PUBLIC not in await titles(ports, created_lt=created)
    assert PUBLIC in await titles(ports, added_gt=added - DAY)
    assert PUBLIC not in await titles(ports, added_gt=added)
    assert PUBLIC in await titles(ports, added_lt=added + DAY)
    assert PUBLIC not in await titles(ports, added_lt=added)
    assert await titles(ports, modified_gt=LONG_AGO, modified_lt=FAR_AHEAD) == BOTH
    assert await titles(ports, modified_lt=LONG_AGO) == NONE
    assert await titles(ports, modified_gt=FAR_AHEAD) == NONE


@pytest.mark.contract(CONTRACT)
async def test_owner_sharing_mime_and_duplicate_filters(backend: Backend) -> None:
    ports = backend.admin
    admin = (await ids(ports, "user"))["admin"]

    assert await titles(ports, owner_in=(admin, 9999)) == {PRIVATE}
    assert await titles(ports, owner_none=(admin,)) == {PUBLIC}
    users = await ids(ports, "user")
    both = (admin, users["e2e-reader"])
    assert await titles(ports, owner_in=both) == {PRIVATE}
    assert await titles(ports, owner_none=both) == {PUBLIC}
    assert await titles(ports, owner_isnull=True) == {PUBLIC}
    assert await titles(ports, owner_isnull=False) == {PRIVATE}
    assert await titles(ports, shared_by=admin) == NONE
    assert await titles(ports, mime_type="application/pdf") == BOTH
    assert await titles(ports, mime_type="image/png") == NONE
    assert await titles(ports, has_duplicates=True) == NONE
    assert await titles(ports, has_duplicates=False) == BOTH


@pytest.mark.contract(CONTRACT)
async def test_custom_field_presence_filters(backend: Backend) -> None:
    ports = backend.admin
    fields = await ids(ports, "custom_field")
    string, link = fields["e2e-string"], fields["e2e-documentlink"]

    assert await titles(ports, custom_fields_all=(string, link)) == {PUBLIC}
    assert await titles(ports, custom_fields_in=(string, link)) == BOTH
    assert await titles(ports, custom_fields_none=(string,)) == {PRIVATE}
    assert await titles(ports, has_custom_fields=True) == BOTH
    assert await titles(ports, has_custom_fields=False) == NONE
    assert await titles(ports, custom_fields_icontains="HELLO") == {PUBLIC}


@pytest.mark.contract(CONTRACT)
async def test_text_filters_search_the_index(backend: Backend) -> None:
    ports = backend.admin

    assert await titles(ports, content_contains="ACME") == {PUBLIC}
    assert await titles(ports, text="acme") == {PUBLIC}
    assert await titles(ports, title_search="invoice") == {PUBLIC}
    assert await titles(ports, text="acme", query="invoice") == {PUBLIC}
    assert await titles(ports, title_search="invoice", query="acme") == {PUBLIC}
    assert await titles(ports, text="acme", query="zebraxyz") == NONE


@pytest.mark.contract(CONTRACT)
async def test_two_simple_searches_at_once_are_rejected(backend: Backend) -> None:
    query = SEEDED.model_copy(update={"text": "acme", "title_search": "invoice"})

    with pytest.raises(Validation, match="Specify only one of text, title_search, query"):
        _ = await backend.admin.documents.search_documents(query, page=1, page_size=10)


@pytest.mark.contract(CONTRACT)
async def test_an_ordering_by_a_number_or_a_title_sorts_numerically_and_alphabetically(
    backend: Backend,
) -> None:
    ports = backend.admin

    async def order(ordering: str) -> list[str]:
        query = SEEDED.model_copy(update={"ordering": ordering})
        found = await ports.documents.search_documents(query, page=1, page_size=100)
        return [hit.title for hit in found.hits]

    assert await order("archive_serial_number") == [PUBLIC, PRIVATE]
    assert await order("-title") == [PUBLIC, PRIVATE]
    assert await order("title") == [PRIVATE, PUBLIC]
