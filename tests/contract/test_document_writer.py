"""Contract of DocumentWriter: the same assertions against the fake and live Paperless 3.4.2.

Every test writes to a document of its own (`backend.new_document`), never to a seeded one.
"""

import random
from datetime import date
from typing import TYPE_CHECKING

import pytest
from fakes.accounts import ADMIN, PUBLIC_ASN

from paperless_mcp.domain.documents import CustomFieldValue, DocumentPatch, DocumentQuery
from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation

if TYPE_CHECKING:
    from pydantic import JsonValue

    from contract.conftest import Backend, Ports
    from paperless_mcp.domain.documents import DocumentRecord
    from paperless_mcp.domain.taxonomy import TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

CONTRACT = "DocumentWriter.update_document"
MISSING = 999_999


async def ids(ports: Ports, kind: TaxonomyKind) -> dict[str, int]:
    return {item.name: item.id for item in await ports.taxonomy.list_taxonomy(kind)}


async def read(ports: Ports, document: int) -> DocumentRecord:
    return await ports.documents.get_document(document)


def values(record: DocumentRecord) -> dict[int, JsonValue]:
    return {value.field: value.value for value in record.custom_fields}


@pytest.mark.contract(CONTRACT)
async def test_a_patch_changes_what_it_sets_and_returns_the_document(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    before = await read(ports, document)

    after = await ports.document_writes.update_document(document, DocumentPatch(title="Renamed"))

    assert after.title == "Renamed"
    # An update's answer does not say whether the user may change the document; a read does.
    assert after.user_can_change is None
    assert after == (await read(ports, document)).model_copy(update={"user_can_change": None})
    unchanged = after.model_copy(
        update={"title": before.title, "modified": before.modified, "user_can_change": True}
    )
    assert unchanged == before
    assert after.modified >= before.modified


@pytest.mark.contract(CONTRACT)
async def test_references_are_set_by_id_and_cleared_with_none(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    correspondent = (await ids(ports, "correspondent"))["e2e-acme"]
    document_type = (await ids(ports, "document_type"))["e2e-invoice"]
    storage_path = (await ids(ports, "storage_path"))["e2e-archive"]

    set_ = await ports.document_writes.update_document(
        document,
        DocumentPatch(
            correspondent=correspondent, document_type=document_type, storage_path=storage_path
        ),
    )
    cleared = await ports.document_writes.update_document(
        document, DocumentPatch(correspondent=None, document_type=None, storage_path=None)
    )

    assert (set_.correspondent, set_.document_type, set_.storage_path) == (
        correspondent,
        document_type,
        storage_path,
    )
    assert (cleared.correspondent, cleared.document_type, cleared.storage_path) == (
        None,
        None,
        None,
    )


@pytest.mark.contract(CONTRACT)
async def test_created_date_archive_serial_number_and_owner_change(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    users = await ids(ports, "user")
    serial = random.randrange(1_000_000, 4_000_000_000)  # noqa: S311 -- unique, not secret

    set_ = await ports.document_writes.update_document(
        document,
        DocumentPatch(created=date(2025, 1, 2), archive_serial_number=serial, owner=None),
    )
    owned = await ports.document_writes.update_document(
        document, DocumentPatch(owner=users[ADMIN[0]], archive_serial_number=None)
    )

    assert (set_.created, set_.archive_serial_number, set_.owner) == (
        date(2025, 1, 2),
        serial,
        None,
    )
    assert (owned.owner, owned.archive_serial_number) == (users[ADMIN[0]], None)


@pytest.mark.contract(CONTRACT)
async def test_tags_are_replaced_as_a_whole_and_duplicates_collapse(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    tags = await ids(ports, "tag")
    finance, inbox = tags["e2e-finance"], tags["e2e-inbox"]

    both = await ports.document_writes.update_document(
        document, DocumentPatch(tags=(finance, inbox, finance))
    )
    one = await ports.document_writes.update_document(document, DocumentPatch(tags=(inbox,)))
    none = await ports.document_writes.update_document(document, DocumentPatch(tags=()))

    assert sorted(both.tags) == sorted([finance, inbox])
    assert one.tags == (inbox,)
    assert none.tags == ()


@pytest.mark.contract(CONTRACT)
async def test_tags_are_hierarchical_a_child_brings_its_parent_and_a_parent_takes_its_children(
    backend: Backend,
) -> None:
    ports, document = backend.admin, backend.new_document()
    tags = await ids(ports, "tag")
    finance, invoices = tags["e2e-finance"], tags["e2e-invoices"]

    child = await ports.document_writes.update_document(document, DocumentPatch(tags=(invoices,)))
    without_parent = await ports.document_writes.update_document(
        document, DocumentPatch(tags=(invoices,))
    )
    parent_only = await ports.document_writes.update_document(
        document, DocumentPatch(tags=(finance,))
    )

    assert sorted(child.tags) == sorted([finance, invoices])
    assert without_parent.tags == ()
    assert parent_only.tags == (finance,)


@pytest.mark.contract(CONTRACT)
async def test_custom_fields_are_replaced_as_a_whole(backend: Backend) -> None:
    """The reason services merge: a patch of one field drops every other one."""
    ports, document = backend.admin, backend.new_document()
    fields = await ids(ports, "custom_field")
    string, integer = fields["e2e-string"], fields["e2e-integer"]

    both = await ports.document_writes.update_document(
        document,
        DocumentPatch(
            custom_fields=(
                CustomFieldValue(field=string, value="a"),
                CustomFieldValue(field=integer, value=7),
            )
        ),
    )
    only = await ports.document_writes.update_document(
        document, DocumentPatch(custom_fields=(CustomFieldValue(field=string, value="b"),))
    )
    untouched = await ports.document_writes.update_document(document, DocumentPatch(title="x"))
    none = await ports.document_writes.update_document(document, DocumentPatch(custom_fields=()))

    assert values(both) == {string: "a", integer: 7}
    assert values(only) == {string: "b"}
    assert values(untouched) == {string: "b"}
    assert values(none) == {}


@pytest.mark.contract(CONTRACT)
async def test_values_of_every_data_type_are_stored_as_sent(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    items = {item.name: item for item in await ports.taxonomy.list_taxonomy("custom_field")}
    [option] = [o for o in (items["e2e-select"].select_options or ()) if o.label == "beta"]
    linked: JsonValue = [backend.new_document()]
    sent: dict[str, JsonValue] = {
        "e2e-string": "hello",
        "e2e-url": "https://example.com/contract",
        "e2e-date": "2026-02-03",
        "e2e-boolean": False,
        "e2e-integer": -3,
        "e2e-float": 2.5,
        "e2e-monetary": "EUR12.50",
        "e2e-documentlink": linked,
        "e2e-longtext": "line one\nline two",
        "e2e-select": option.id,
    }

    after = await ports.document_writes.update_document(
        document,
        DocumentPatch(
            custom_fields=tuple(
                CustomFieldValue(field=items[name].id, value=value) for name, value in sent.items()
            )
        ),
    )

    assert values(after) == {items[name].id: value for name, value in sent.items()}
    assert values(after) == values(await read(ports, document))


@pytest.mark.contract(CONTRACT)
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("e2e-integer", "abc", "valid integer"),
        ("e2e-integer", 1.5, "valid integer"),
        ("e2e-select", "no-such-option", "id of an element"),
        ("e2e-date", "03.02.2026", "wrong format"),
        ("e2e-monetary", "EUR 12.50", "two-decimal"),
        ("e2e-monetary", "EUR12.505", "two-decimal"),
        ("e2e-documentlink", [MISSING], "don't exist"),
        ("e2e-documentlink", "1", "must be a list"),
        ("e2e-url", "not a url", "missing scheme"),
        ("e2e-string", "x" * 300, "at most 128"),
        ("e2e-integer", 2_147_483_648, "less than or equal"),
        ("e2e-url", "http://", "net location or path"),
        ("e2e-monetary", "9" * 12, "two-decimal"),
    ],
)
async def test_a_value_of_the_wrong_kind_is_rejected_and_nothing_changes(
    backend: Backend, field: str, value: JsonValue, message: str
) -> None:
    ports, document = backend.admin, backend.new_document()
    field_id = (await ids(ports, "custom_field"))[field]
    before = await read(ports, document)

    with pytest.raises(Validation, match=f"(?i){message}"):
        _ = await ports.document_writes.update_document(
            document,
            DocumentPatch(
                title="never", custom_fields=(CustomFieldValue(field=field_id, value=value),)
            ),
        )

    assert await read(ports, document) == before


@pytest.mark.contract(CONTRACT)
@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (DocumentPatch(tags=(MISSING,)), "tags"),
        (DocumentPatch(correspondent=MISSING), "correspondent"),
        (DocumentPatch(document_type=MISSING), "document_type"),
        (DocumentPatch(storage_path=MISSING), "storage_path"),
        (DocumentPatch(owner=MISSING), "owner"),
        (DocumentPatch(archive_serial_number=PUBLIC_ASN), "archive serial number"),
        (DocumentPatch(archive_serial_number=-1), "archive_serial_number"),
        (DocumentPatch(title="x" * 129), "title"),
        (DocumentPatch(custom_fields=(CustomFieldValue(field=MISSING, value="x"),)), "field"),
    ],
)
async def test_unknown_ids_and_taken_numbers_are_rejected(
    backend: Backend, patch: DocumentPatch, message: str
) -> None:
    ports, document = backend.admin, backend.new_document()

    with pytest.raises(Validation, match=f"(?i){message}"):
        _ = await ports.document_writes.update_document(document, patch)


@pytest.mark.contract(CONTRACT)
async def test_a_missing_document_is_not_found(backend: Backend) -> None:
    with pytest.raises(NotFound, match=f"No document {MISSING}"):
        _ = await backend.admin.document_writes.update_document(MISSING, DocumentPatch(title="x"))


@pytest.mark.contract(CONTRACT)
async def test_a_user_without_the_change_permission_is_denied(backend: Backend) -> None:
    document = backend.new_document()
    before = await read(backend.admin, document)

    for ports in (backend.reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await ports.document_writes.update_document(document, DocumentPatch(title="x"))

    assert await read(backend.admin, document) == before


@pytest.mark.contract(CONTRACT)
async def test_a_monetary_value_with_one_decimal_is_stored_as_sent(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    field = (await ids(ports, "custom_field"))["e2e-monetary"]

    after = await ports.document_writes.update_document(
        document, DocumentPatch(custom_fields=(CustomFieldValue(field=field, value="EUR12.5"),))
    )

    assert values(after) == {field: "EUR12.5"}


@pytest.mark.contract(CONTRACT)
async def test_a_document_link_is_mirrored_on_the_linked_document_and_unlinked_with_it(
    backend: Backend,
) -> None:
    """Paperless keeps document links symmetrical (usage.md, "Document Link")."""
    ports = backend.admin
    first, second = backend.new_document(), backend.new_document()
    link = (await ids(ports, "custom_field"))["e2e-documentlink"]

    _ = await ports.document_writes.update_document(
        first, DocumentPatch(custom_fields=(CustomFieldValue(field=link, value=[second]),))
    )
    linked = values(await read(ports, second))
    _ = await ports.document_writes.update_document(first, DocumentPatch(custom_fields=()))
    unlinked = values(await read(ports, second))

    assert linked == {link: [first]}
    assert unlinked.get(link, []) == []


@pytest.mark.contract(CONTRACT)
async def test_a_read_reports_the_object_permission_to_change_not_the_global_one(
    backend: Backend,
) -> None:
    document = backend.new_document()
    found = await backend.admin.documents.search_documents(
        DocumentQuery(title_contains="e2e-public"), page=1, page_size=1
    )

    assert (await read(backend.admin, document)).user_can_change is True
    # The public document is unowned, so the reader may change it as far as objects go; the
    # missing change_document permission is a separate 403 on the update.
    assert (await read(backend.reader, found.hits[0].id)).user_can_change is True
