"""Contract of DocumentReader: the same assertions against the fake and live Paperless 3.4.2.

The seed's ids differ between the backends, so every test looks them up by name first.
"""

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from e2e.helpers import pdf
from fakes.accounts import PRIVATE_CONTENT, PUBLIC_ASN, PUBLIC_NOTE

from paperless_mcp.domain.documents import DocumentQuery
from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from paperless_mcp.domain.documents import DocumentHit, FileVersion
    from paperless_mcp.domain.taxonomy import TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]

CONTRACT = "DocumentReader.search_documents"
PUBLIC, PRIVATE = "e2e-public-invoice", "e2e-private-note"
SEEDED = DocumentQuery(title_contains="e2e-p")


async def search(ports: Ports, query: DocumentQuery, page_size: int = 100) -> list[DocumentHit]:
    return list((await ports.documents.search_documents(query, page=1, page_size=page_size)).hits)


async def titles(ports: Ports, **filters: object) -> set[str]:
    query = SEEDED.model_copy(update=filters)
    return {hit.title for hit in await search(ports, query)}


async def ids(ports: Ports, kind: TaxonomyKind) -> dict[str, int]:
    return {item.name: item.id for item in await ports.taxonomy.list_taxonomy(kind)}


async def document(ports: Ports, title: str) -> DocumentHit:
    [hit] = [hit for hit in await search(ports, SEEDED) if hit.title == title]
    return hit


@pytest.mark.contract(CONTRACT)
async def test_a_restricted_user_finds_only_documents_it_may_see(backend: Backend) -> None:
    assert {PUBLIC, PRIVATE} <= await titles(backend.admin)
    assert PRIVATE not in await titles(backend.reader)
    assert PUBLIC in await titles(backend.reader)


@pytest.mark.contract(CONTRACT)
async def test_a_hit_carries_ids_and_the_stored_custom_field_values(backend: Backend) -> None:
    tags = await ids(backend.reader, "tag")
    fields = await ids(backend.reader, "custom_field")

    hit = await document(backend.reader, PUBLIC)

    assert set(hit.tags) == {tags["e2e-finance"], tags["e2e-invoices"], tags["e2e-inbox"]}
    assert (await ids(backend.reader, "correspondent"))["e2e-acme"] == hit.correspondent
    values = {value.field: value.value for value in hit.custom_fields}
    assert values[fields["e2e-string"]] == "hello"
    assert values[fields["e2e-monetary"]] == "EUR12.50"
    assert values[fields["e2e-boolean"]] is True
    assert hit.highlights is None


@pytest.mark.contract(CONTRACT)
async def test_taxonomy_filters_match_by_id(backend: Backend) -> None:
    ports = backend.admin
    tags = await ids(ports, "tag")
    correspondent = (await ids(ports, "correspondent"))["e2e-acme"]
    document_type = (await ids(ports, "document_type"))["e2e-invoice"]
    storage_path = (await ids(ports, "storage_path"))["e2e-archive"]

    assert await titles(ports, correspondent=correspondent) == {PUBLIC}
    assert await titles(ports, document_type=document_type) == {PUBLIC}
    assert await titles(ports, storage_path=storage_path) == {PUBLIC}
    both = (tags["e2e-finance"], tags["e2e-private"])
    assert await titles(ports, tags_all=both) == set()
    assert await titles(ports, tags_any=both) == {PUBLIC, PRIVATE}
    assert await titles(ports, tags_none=(tags["e2e-private"],)) == {PUBLIC}


@pytest.mark.contract(CONTRACT)
async def test_ids_select_exactly_the_documents_the_user_may_see(backend: Backend) -> None:
    public = (await document(backend.admin, PUBLIC)).id
    private = (await document(backend.admin, PRIVATE)).id
    both = DocumentQuery(ids=(public, private, 999_999_999))

    assert {hit.id for hit in await search(backend.admin, both)} == {public, private}
    assert {hit.id for hit in await search(backend.reader, both)} == {public}
    assert {hit.id for hit in await search(backend.admin, DocumentQuery(ids=(private,)))} == {
        private
    }


@pytest.mark.contract(CONTRACT)
async def test_date_ranges_include_both_ends(backend: Backend) -> None:
    ports = backend.admin
    hit = await document(ports, PUBLIC)
    day, added = hit.created, hit.added.date()
    before = timedelta(days=1)

    assert PUBLIC in await titles(ports, created_from=day, created_to=day)
    assert PUBLIC not in await titles(ports, created_to=day - before)
    assert PUBLIC not in await titles(ports, created_from=day + before)
    assert PUBLIC in await titles(ports, added_from=added, added_to=added)
    assert PUBLIC not in await titles(ports, added_to=added - before)


@pytest.mark.contract(CONTRACT)
async def test_owner_and_inbox_filters(backend: Backend) -> None:
    ports = backend.admin
    admin = (await ids(ports, "user"))["admin"]

    assert await titles(ports, owner=admin) == {PRIVATE}
    assert await titles(ports, inbox=True) == {PUBLIC}
    assert await titles(ports, inbox=False) == {PRIVATE}


@pytest.mark.contract(CONTRACT)
async def test_full_text_finds_words_of_the_content_with_highlights(backend: Backend) -> None:
    [hit] = await search(backend.reader, DocumentQuery(query="ACME"))

    assert hit.title == PUBLIC
    assert "<b>acme</b>" in (hit.highlights or "").lower()
    assert await search(backend.reader, DocumentQuery(query="admin")) == []


@pytest.mark.contract(CONTRACT)
async def test_more_like_finds_other_documents(backend: Backend) -> None:
    private = await document(backend.admin, PRIVATE)

    found = {hit.title for hit in await search(backend.admin, DocumentQuery(more_like=private.id))}

    assert PUBLIC in found
    assert PRIVATE not in found


@pytest.mark.contract(CONTRACT)
async def test_more_like_a_document_the_user_cannot_see_is_not_found(backend: Backend) -> None:
    private = await document(backend.admin, PRIVATE)

    for unseen in (private.id, 999_999):
        with pytest.raises(NotFound, match=f"document {unseen}"):
            _ = await search(backend.reader, DocumentQuery(more_like=unseen))


@pytest.mark.contract(CONTRACT)
async def test_a_restricted_user_filters_by_owner_id(backend: Backend) -> None:
    admin = (await ids(backend.admin, "user"))["admin"]

    assert await titles(backend.reader, owner=admin) == set()


@pytest.mark.parametrize("ordering", ["modified", "-archive_serial_number"])
@pytest.mark.contract(CONTRACT)
async def test_every_ordering_is_accepted(backend: Backend, ordering: str) -> None:
    assert PUBLIC in await titles(backend.admin, ordering=ordering)


@pytest.mark.contract(CONTRACT)
async def test_full_text_takes_boolean_operators(backend: Backend) -> None:
    found = await search(backend.reader, DocumentQuery(query="invoice AND acme"))

    assert [hit.title for hit in found] == [PUBLIC]


@pytest.mark.parametrize(
    "expression",
    [
        ["e2e-string", "exact", "hello"],
        ["e2e-select", "exact", "alpha"],
        ["e2e-date", "range", ["2026-01-01", "today"]],
        ["AND", [["e2e-integer", "gt", 40], ["e2e-monetary", "icontains", "12.5"]]],
        ["NOT", ["e2e-string", "exact", "bye"]],
    ],
)
@pytest.mark.contract(CONTRACT)
async def test_custom_field_queries_pass_through(backend: Backend, expression: object) -> None:
    assert PUBLIC in await titles(backend.reader, custom_field_query=expression)


@pytest.mark.contract(CONTRACT)
async def test_a_query_on_an_unknown_custom_field_is_rejected(backend: Backend) -> None:
    query = DocumentQuery(custom_field_query=["e2e-nope", "exact", "x"])

    with pytest.raises(Validation, match="e2e-nope"):
        _ = await search(backend.reader, query)


@pytest.mark.contract(CONTRACT)
async def test_paging_reports_the_count_and_the_next_page(backend: Backend) -> None:
    ports = backend.admin

    first = await ports.documents.search_documents(SEEDED, page=1, page_size=1)

    assert first.count >= 2
    assert (len(first.hits), first.next_page) == (1, 2)
    with pytest.raises(NotFound, match="beyond the last page") as raised:
        _ = await ports.documents.search_documents(SEEDED, page=first.count + 1, page_size=1)
    assert raised.value.hint


@pytest.mark.contract(CONTRACT)
async def test_ordering_by_title(backend: Backend) -> None:
    ports = backend.admin

    ascending = await search(ports, SEEDED.model_copy(update={"ordering": "title"}))
    descending = await search(ports, SEEDED.model_copy(update={"ordering": "-title"}))

    assert [hit.title for hit in ascending] == sorted(hit.title for hit in ascending)
    assert [hit.id for hit in descending] == [hit.id for hit in reversed(ascending)]


@pytest.mark.contract(CONTRACT)
async def test_a_user_without_view_document_is_denied(backend: Backend) -> None:
    with pytest.raises(PermissionDenied):
        _ = await search(backend.blind, SEEDED)


@pytest.mark.contract(CONTRACT)
async def test_ordering_by_archive_serial_number_is_numeric(backend: Backend) -> None:
    ports = backend.admin
    by_asn = [PUBLIC, PRIVATE]  # ASN 2, then ASN 10: a string sort would put 10 first

    ascending = await search(ports, SEEDED.model_copy(update={"ordering": "archive_serial_number"}))
    descending = await search(
        ports, SEEDED.model_copy(update={"ordering": "-archive_serial_number"})
    )

    assert [hit.title for hit in ascending] == by_asn
    assert [hit.title for hit in descending] == by_asn[::-1]


MISSING = 999_999


async def users(ports: Ports) -> dict[str, int]:
    return {item.name: item.id for item in await ports.taxonomy.list_taxonomy("user")}


@pytest.mark.contract("DocumentReader.get_document")
async def test_a_document_carries_ids_dates_and_the_stored_custom_field_values(
    backend: Backend,
) -> None:
    tags = await ids(backend.reader, "tag")
    fields = await ids(backend.reader, "custom_field")
    private = await document(backend.admin, PRIVATE)
    hit = await document(backend.reader, PUBLIC)

    record = await backend.reader.documents.get_document(hit.id)

    assert (record.id, record.title, record.created) == (hit.id, PUBLIC, hit.created)
    assert set(record.tags) == {tags["e2e-finance"], tags["e2e-invoices"], tags["e2e-inbox"]}
    assert record.correspondent == (await ids(backend.reader, "correspondent"))["e2e-acme"]
    assert (record.document_type, record.storage_path) == (hit.document_type, hit.storage_path)
    values = {value.field: value.value for value in record.custom_fields}
    assert values[fields["e2e-monetary"]] == "EUR12.50"
    assert values[fields["e2e-documentlink"]] == [private.id]
    assert values[fields["e2e-longtext"]] == "line one\nline two"
    assert record.archive_serial_number == PUBLIC_ASN
    assert record.owner is None
    assert record.original_file_name == f"{PUBLIC}.pdf"
    assert (record.mime_type, record.page_count) == ("application/pdf", 1)
    assert record.added <= record.modified


@pytest.mark.contract("DocumentReader.get_document")
async def test_a_document_counts_its_notes_and_lists_its_versions(backend: Backend) -> None:
    public = await backend.admin.documents.get_document((await document(backend.admin, PUBLIC)).id)
    private = await backend.admin.documents.get_document(
        (await document(backend.admin, PRIVATE)).id
    )

    assert (public.notes_count, private.notes_count) == (1, 0)
    [version] = public.versions
    assert (version.is_root, version.version_label) == (True, None)
    assert private.owner == (await users(backend.admin))["admin"]


@pytest.mark.contract("DocumentReader.get_document")
async def test_a_missing_or_hidden_document_is_not_found(backend: Backend) -> None:
    private = await document(backend.admin, PRIVATE)

    with pytest.raises(NotFound) as missing:
        _ = await backend.admin.documents.get_document(MISSING)
    with pytest.raises(NotFound) as hidden:
        _ = await backend.reader.documents.get_document(private.id)

    assert "No document" in missing.value.message
    assert missing.value.hint
    assert hidden.value.message.startswith(f"No document {private.id} exists")


@pytest.mark.contract("DocumentReader.get_document")
async def test_a_user_without_view_document_may_not_get_a_document(backend: Backend) -> None:
    public = await document(backend.admin, PUBLIC)

    with pytest.raises(PermissionDenied):
        _ = await backend.blind.documents.get_document(public.id)


@pytest.mark.contract("DocumentReader.get_document_content")
async def test_the_content_is_the_whole_text(backend: Backend) -> None:
    public = await document(backend.reader, PUBLIC)
    private = await document(backend.admin, PRIVATE)

    assert await backend.reader.documents.get_document_content(public.id) == (
        "E2E public invoice ACME 2026"
    )
    assert await backend.admin.documents.get_document_content(private.id) == PRIVATE_CONTENT


@pytest.mark.contract("DocumentReader.get_document_content")
async def test_the_content_of_a_missing_or_hidden_document_is_not_found(backend: Backend) -> None:
    private = await document(backend.admin, PRIVATE)

    with pytest.raises(NotFound):
        _ = await backend.admin.documents.get_document_content(MISSING)
    with pytest.raises(NotFound):
        _ = await backend.reader.documents.get_document_content(private.id)
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.documents.get_document_content(private.id)


@pytest.mark.contract("DocumentReader.list_document_notes")
async def test_notes_come_with_their_author(backend: Backend) -> None:
    public = await document(backend.admin, PUBLIC)

    [note] = await backend.admin.documents.list_document_notes(public.id)

    assert (note.note, note.user) == (PUBLIC_NOTE, "admin")
    assert note.created is not None
    assert (
        await backend.admin.documents.list_document_notes(
            (await document(backend.admin, PRIVATE)).id
        )
        == ()
    )


@pytest.mark.contract("DocumentReader.list_document_notes")
async def test_notes_need_the_view_note_permission_and_an_existing_document(
    backend: Backend,
) -> None:
    public = await document(backend.reader, PUBLIC)

    with pytest.raises(PermissionDenied):
        _ = await backend.reader.documents.list_document_notes(public.id)
    with pytest.raises(NotFound):
        _ = await backend.admin.documents.list_document_notes(MISSING)


@pytest.mark.contract("DocumentReader.list_document_history")
async def test_the_history_runs_newest_first_and_never_holds_the_ocr_text(
    backend: Backend,
) -> None:
    private = await document(backend.admin, PRIVATE)

    entries = await backend.admin.documents.list_document_history(private.id)

    assert entries
    assert entries[-1].action == "create"
    assert [entry.id for entry in entries] == sorted((e.id for e in entries), reverse=True)
    assert all("content" not in entry.changes for entry in entries)
    assert "private admin only" not in repr(entries)
    assert any(entry.actor == "admin" for entry in entries)


@pytest.mark.contract("DocumentReader.list_document_history")
async def test_the_history_needs_its_permission_and_an_existing_document(
    backend: Backend,
) -> None:
    public = await document(backend.reader, PUBLIC)

    with pytest.raises(PermissionDenied):
        _ = await backend.reader.documents.list_document_history(public.id)
    with pytest.raises(NotFound):
        _ = await backend.admin.documents.list_document_history(MISSING)
    with pytest.raises(NotFound):  # existence is checked before the log permission
        _ = await backend.reader.documents.list_document_history(MISSING)


@pytest.mark.contract("DocumentReader.get_document_metadata")
async def test_the_file_metadata_describes_the_files(backend: Backend) -> None:
    public = await document(backend.reader, PUBLIC)

    metadata = await backend.reader.documents.get_document_metadata(public.id)

    assert metadata.original_filename == f"{PUBLIC}.pdf"
    assert metadata.original_mime_type == "application/pdf"
    assert metadata.original_size > 0
    assert metadata.original_checksum
    assert metadata.media_filename.endswith(f"{PUBLIC}.pdf")
    assert metadata.has_archive_version
    assert (metadata.archive_size or 0) > 0
    assert metadata.archive_checksum


@pytest.mark.contract("DocumentReader.get_document_metadata")
async def test_the_file_metadata_of_a_missing_or_hidden_document(backend: Backend) -> None:
    private = await document(backend.admin, PRIVATE)

    with pytest.raises(NotFound):
        _ = await backend.reader.documents.get_document_metadata(MISSING)
    with pytest.raises(PermissionDenied):
        _ = await backend.reader.documents.get_document_metadata(private.id)


@pytest.mark.contract("DocumentReader.get_document_suggestions")
async def test_a_fresh_instance_suggests_nothing(backend: Backend) -> None:
    public = await document(backend.reader, PUBLIC)

    found = await backend.reader.documents.get_document_suggestions(public.id)

    assert (found.correspondents, found.tags, found.document_types) == ((), (), ())
    assert (found.storage_paths, found.dates) == ((), ())


@pytest.mark.contract("DocumentReader.get_document_suggestions")
async def test_suggestions_of_a_missing_or_hidden_document(backend: Backend) -> None:
    private = await document(backend.admin, PRIVATE)

    with pytest.raises(NotFound):
        _ = await backend.reader.documents.get_document_suggestions(MISSING)
    with pytest.raises(PermissionDenied):
        _ = await backend.reader.documents.get_document_suggestions(private.id)


@pytest.mark.contract("DocumentReader.get_document_titles")
async def test_titles_are_given_for_the_documents_the_user_may_see(backend: Backend) -> None:
    public = await document(backend.admin, PUBLIC)
    private = await document(backend.admin, PRIVATE)
    wanted = [public.id, private.id, MISSING]

    assert await backend.admin.documents.get_document_titles(wanted) == {
        public.id: PUBLIC,
        private.id: PRIVATE,
    }
    assert await backend.reader.documents.get_document_titles(wanted) == {public.id: PUBLIC}
    assert await backend.reader.documents.get_document_titles([]) == {}


# --- Files: thumbnails, originals and archive versions -----------------------------------------

MAX = 1_000_000
NON_ASCII = "Rechnung für Müller 日本語 €"


def named(label: str) -> str:
    """A title unique to the test run, so the live upload cannot collide with another."""
    return f"{label} {uuid.uuid4().hex[:12]}"


def uploaded(backend: Backend, title: str, filename: str, mime_type: str) -> int:
    content = pdf(uuid.uuid4().hex) if mime_type == "application/pdf" else title.encode()
    return backend.upload(title, filename, content, mime_type)


@pytest.mark.contract("DocumentReader.get_document_thumbnail")
async def test_a_thumbnail_is_a_webp_image(backend: Backend) -> None:
    public = await document(backend.reader, PUBLIC)

    thumbnail = await backend.reader.documents.get_document_thumbnail(public.id)

    assert thumbnail.mime_type == "image/webp"
    assert thumbnail.content[:4] == b"RIFF"
    assert thumbnail.content[8:12] == b"WEBP"


@pytest.mark.contract("DocumentReader.get_document_thumbnail")
async def test_the_thumbnail_of_a_missing_hidden_or_blind_document(backend: Backend) -> None:
    private = await document(backend.admin, PRIVATE)

    with pytest.raises(NotFound):
        _ = await backend.admin.documents.get_document_thumbnail(MISSING)
    with pytest.raises(PermissionDenied):
        _ = await backend.reader.documents.get_document_thumbnail(private.id)
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.documents.get_document_thumbnail(private.id)


@pytest.mark.contract("DocumentReader.get_document_file")
async def test_the_original_and_the_archive_are_pdfs_of_the_size_the_metadata_names(
    backend: Backend,
) -> None:
    public = await document(backend.reader, PUBLIC)
    documents = backend.reader.documents
    metadata = await documents.get_document_metadata(public.id)

    original = await documents.get_document_file(public.id, "original", max_bytes=MAX)
    archive = await documents.get_document_file(public.id, "archive", max_bytes=MAX)

    assert (original.mime_type, archive.mime_type) == ("application/pdf", "application/pdf")
    assert original.content.startswith(b"%PDF")
    assert archive.content.startswith(b"%PDF")
    assert len(original.content) == metadata.original_size
    assert len(archive.content) == metadata.archive_size
    assert original.content != archive.content
    assert (original.filename or "").endswith(f"{PUBLIC}.pdf")
    assert (archive.filename or "").endswith(f"{PUBLIC}.pdf")


@pytest.mark.contract("DocumentReader.get_document_file")
async def test_a_non_ascii_title_survives_in_the_file_name(backend: Backend) -> None:
    title = named(NON_ASCII)
    number = uploaded(backend, title, f"{NON_ASCII} Käse.pdf", "application/pdf")

    for version in ("original", "archive"):
        found = await backend.admin.documents.get_document_file(number, version, max_bytes=MAX)

        assert found.mime_type == "application/pdf"
        assert (found.filename or "").endswith(f" {title}.pdf")
        assert found.content.startswith(b"%PDF")


@pytest.mark.contract("DocumentReader.get_document_file")
async def test_a_document_without_archive_version_answers_its_original(backend: Backend) -> None:
    title = named("kein-archiv-ä")
    number = uploaded(backend, title, "notiz-ä.txt", "text/plain")
    documents = backend.admin.documents

    metadata = await documents.get_document_metadata(number)
    original = await documents.get_document_file(number, "original", max_bytes=MAX)
    archive = await documents.get_document_file(number, "archive", max_bytes=MAX)

    assert not metadata.has_archive_version
    assert metadata.original_mime_type == "text/plain"
    assert original.mime_type.startswith("text/plain")
    assert original.content == title.encode()
    assert (original.filename or "").endswith(f"{title}.txt")
    assert archive == original


@pytest.mark.contract("DocumentReader.get_document_file")
async def test_the_file_of_a_missing_hidden_or_blind_document(backend: Backend) -> None:
    private = await document(backend.admin, PRIVATE)

    for version in ("original", "archive"):
        with pytest.raises(NotFound):
            _ = await backend.admin.documents.get_document_file(MISSING, version, max_bytes=MAX)
        with pytest.raises(PermissionDenied):
            _ = await backend.reader.documents.get_document_file(private.id, version, max_bytes=MAX)
        with pytest.raises(PermissionDenied):
            _ = await backend.blind.documents.get_document_file(private.id, version, max_bytes=MAX)


@pytest.mark.contract("DocumentReader.get_document_file")
async def test_a_file_over_the_byte_limit_is_truncated_but_still_named_and_typed(
    backend: Backend,
) -> None:
    public = await document(backend.reader, PUBLIC)
    documents = backend.reader.documents
    metadata = await documents.get_document_metadata(public.id)

    versions: list[tuple[FileVersion, int | None]] = [
        ("original", metadata.original_size),
        ("archive", metadata.archive_size),
    ]
    for version, size in versions:
        assert size
        cut = await documents.get_document_file(public.id, version, max_bytes=size - 1)
        whole = await documents.get_document_file(public.id, version, max_bytes=size)

        assert (cut.content, cut.truncated, cut.mime_type) == (b"", True, "application/pdf")
        assert (cut.filename or "").endswith(f"{PUBLIC}.pdf")
        assert (len(whole.content), whole.truncated) == (size, False)
