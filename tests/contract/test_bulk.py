"""Contract of BulkWriter on the fake and live Paperless 3.4.2.

Every test creates the documents and taxonomy objects it changes (names with `SCRATCH_ITEM` or
`SCRATCH_TAG` and a uuid, documents through `backend.new_document`) and never touches a seeded
one; the live backend deletes what is left when the test ends.
"""

import uuid
from typing import TYPE_CHECKING

import pytest
from fakes.accounts import READER

from contract.conftest import SCRATCH_ITEM, SCRATCH_TAG
from paperless_mcp.domain.bulk import (
    FieldsChange,
    ObjectPermissions,
    PermissionsChange,
    ReferenceChange,
    ReprocessChange,
    TagsChange,
    TrashDocuments,
)
from paperless_mcp.domain.errors import PermissionDenied, Validation
from paperless_mcp.domain.taxonomy import OptionInput, TaxonomyValues

if TYPE_CHECKING:
    from pydantic import JsonValue

    from contract.conftest import Backend, Ports
    from paperless_mcp.domain.bulk import BulkKind
    from paperless_mcp.domain.documents import DocumentRecord
    from paperless_mcp.domain.taxonomy import WritableKind

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-8"),
]

DOCUMENTS = "BulkWriter.bulk_edit_documents"
DELETE_ITEMS = "BulkWriter.delete_taxonomy_items"
PERMISSIONS = "BulkWriter.set_taxonomy_permissions"
MISSING = 999_999
KINDS: tuple[BulkKind, ...] = ("tag", "correspondent", "document_type", "storage_path")
REFERENCES = ("correspondent", "document_type", "storage_path")


async def make(ports: Ports, kind: WritableKind, **fields: object) -> int:
    """A new object of its own, named for cleanup; returns its id."""
    required: dict[str, dict[str, str]] = {
        "storage_path": {"path": "contract/{{ title }}"},
        "custom_field": {"data_type": "string"},
    }
    given: dict[str, object] = {**required.get(kind, {}), **fields}
    prefix = SCRATCH_TAG if kind == "tag" else SCRATCH_ITEM
    values = TaxonomyValues.model_validate(given)
    created = await ports.taxonomy_writes.create_taxonomy_item(
        kind, f"{prefix}{uuid.uuid4().hex}", values
    )
    return created.id


async def read(ports: Ports, document: int) -> DocumentRecord:
    return await ports.documents.get_document(document)


async def fields_of(ports: Ports, document: int) -> dict[int, JsonValue]:
    return {v.field: v.value for v in (await read(ports, document)).custom_fields}


async def user_id(ports: Ports, username: str) -> int:
    [found] = [u for u in await ports.taxonomy.list_taxonomy("user") if u.name == username]
    return found.id


async def group_id(ports: Ports) -> int:
    [found] = [g for g in await ports.taxonomy.list_taxonomy("group") if g.name == "e2e-group"]
    return found.id


# --- bulk_edit_documents


@pytest.mark.contract(DOCUMENTS)
async def test_tags_are_added_with_their_parents_and_removed_with_their_children(
    backend: Backend,
) -> None:
    ports = backend.admin
    parent = await make(ports, "tag")
    child = await make(ports, "tag", parent=parent)
    other = await make(ports, "tag")
    first, second = backend.new_document(), backend.new_document()
    before = {d: set((await read(ports, d)).tags) for d in (first, second)}

    await ports.bulk_writes.bulk_edit_documents((first, second), TagsChange(add=(child, other)))

    for document in (first, second):
        assert set((await read(ports, document)).tags) == before[document] | {parent, child, other}

    await ports.bulk_writes.bulk_edit_documents((first,), TagsChange(remove=(other,)))

    assert set((await read(ports, first)).tags) == before[first] | {parent, child}
    assert other in (await read(ports, second)).tags

    await ports.bulk_writes.bulk_edit_documents((first, second), TagsChange(remove=(parent,)))

    for document in (first, second):
        assert not {parent, child} & set((await read(ports, document)).tags)
    assert other in (await read(ports, second)).tags


@pytest.mark.contract(DOCUMENTS)
async def test_a_tag_in_both_lists_ends_up_added(backend: Backend) -> None:
    # Paperless removes first, then adds.
    ports, document = backend.admin, backend.new_document()
    tag = await make(ports, "tag")

    await ports.bulk_writes.bulk_edit_documents((document,), TagsChange(add=(tag,), remove=(tag,)))

    assert tag in (await read(ports, document)).tags


@pytest.mark.contract(DOCUMENTS)
@pytest.mark.parametrize("field", REFERENCES)
async def test_a_reference_is_set_on_the_selected_documents_only_and_can_be_cleared(
    backend: Backend, field: str
) -> None:
    ports = backend.admin
    target = await make(ports, field)  # pyright: ignore[reportArgumentType] -- one of the kinds
    first, second, untouched = (backend.new_document() for _ in range(3))
    change = ReferenceChange.model_validate

    await ports.bulk_writes.bulk_edit_documents(
        (first, second), change({"field": field, "value": target})
    )

    assert getattr(await read(ports, first), field) == target
    assert getattr(await read(ports, second), field) == target
    assert getattr(await read(ports, untouched), field) is None

    await ports.bulk_writes.bulk_edit_documents((first,), change({"field": field, "value": None}))

    assert getattr(await read(ports, first), field) is None
    assert getattr(await read(ports, second), field) == target


@pytest.mark.contract(DOCUMENTS)
async def test_custom_fields_are_set_and_removed_one_by_one_and_the_others_stay(
    backend: Backend,
) -> None:
    ports = backend.admin
    text = await make(ports, "custom_field", data_type="string")
    number = await make(ports, "custom_field", data_type="integer")
    first, second = backend.new_document(), backend.new_document()
    writer = ports.bulk_writes

    await writer.bulk_edit_documents((first, second), FieldsChange(values={text: "a"}))
    await writer.bulk_edit_documents((first,), FieldsChange(values={number: 5}))

    assert await fields_of(ports, first) == {text: "a", number: 5}
    assert await fields_of(ports, second) == {text: "a"}

    await writer.bulk_edit_documents((first, second), FieldsChange(values={text: "b"}))
    assert await fields_of(ports, first) == {text: "b", number: 5}

    await writer.bulk_edit_documents((first, second), FieldsChange(remove=(text,)))
    assert await fields_of(ports, first) == {number: 5}
    assert await fields_of(ports, second) == {}

    # Removing a field that a document does not have is fine.
    await writer.bulk_edit_documents((second,), FieldsChange(remove=(number,)))
    assert await fields_of(ports, first) == {number: 5}


@pytest.mark.contract(DOCUMENTS)
async def test_a_select_value_is_the_option_id(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    options = (OptionInput(label="red"), OptionInput(label="blue"))
    created = await ports.taxonomy_writes.create_taxonomy_item(
        "custom_field",
        f"{SCRATCH_ITEM}{uuid.uuid4().hex}",
        TaxonomyValues(data_type="select", select_options=options),
    )
    [blue] = [o for o in created.select_options or () if o.label == "blue"]

    await ports.bulk_writes.bulk_edit_documents(
        (document,), FieldsChange(values={created.id: blue.id})
    )

    assert await fields_of(ports, document) == {created.id: blue.id}


@pytest.mark.contract(DOCUMENTS)
async def test_document_links_are_mirrored_like_in_an_update(backend: Backend) -> None:
    ports = backend.admin
    link = await make(ports, "custom_field", data_type="documentlink")
    first, second = backend.new_document(), backend.new_document()

    await ports.bulk_writes.bulk_edit_documents((first,), FieldsChange(values={link: [second]}))

    assert await fields_of(ports, first) == {link: [second]}
    assert await fields_of(ports, second) == {link: [first]}


@pytest.mark.contract(DOCUMENTS)
async def test_a_document_that_the_link_names_itself_is_skipped(backend: Backend) -> None:
    ports = backend.admin
    link = await make(ports, "custom_field", data_type="documentlink")
    first, second = backend.new_document(), backend.new_document()

    await ports.bulk_writes.bulk_edit_documents(
        (first, second), FieldsChange(values={link: [first]})
    )

    assert await fields_of(ports, second) == {link: [first]}
    assert await fields_of(ports, first) == {link: [second]}


@pytest.mark.contract(DOCUMENTS)
async def test_a_wrong_value_or_an_unknown_field_is_rejected_and_nothing_changes(
    backend: Backend,
) -> None:
    ports = backend.admin
    number = await make(ports, "custom_field", data_type="integer")
    first, second = backend.new_document(), backend.new_document()

    with pytest.raises(Validation, match=r"(?i)valid integer"):
        await ports.bulk_writes.bulk_edit_documents(
            (first, second), FieldsChange(values={number: "abc"})
        )
    with pytest.raises(Validation, match=r"(?i)custom field"):
        await ports.bulk_writes.bulk_edit_documents(
            (first, second), FieldsChange(values={MISSING: "x"})
        )
    with pytest.raises(Validation, match=r"(?i)custom field"):
        await ports.bulk_writes.bulk_edit_documents(
            (first, second), FieldsChange(remove=(MISSING,))
        )

    assert await fields_of(ports, first) == {}
    assert await fields_of(ports, second) == {}


@pytest.mark.contract(DOCUMENTS)
@pytest.mark.parametrize(
    "change",
    [
        TagsChange(add=(MISSING,)),
        ReferenceChange(field="correspondent", value=MISSING),
        ReferenceChange(field="document_type", value=MISSING),
        ReferenceChange(field="storage_path", value=MISSING),
    ],
    ids=["tag", "correspondent", "document-type", "storage-path"],
)
async def test_a_missing_object_is_rejected_and_nothing_changes(
    backend: Backend, change: TagsChange | ReferenceChange
) -> None:
    ports, document = backend.admin, backend.new_document()
    before = await read(ports, document)

    with pytest.raises(Validation, match=r"(?i)(does not exist|don't exist)"):
        await ports.bulk_writes.bulk_edit_documents((document,), change)

    assert await read(ports, document) == before


@pytest.mark.contract(DOCUMENTS)
@pytest.mark.parametrize("bad", ["unknown", "twice"])
async def test_an_unknown_or_repeated_document_rejects_the_whole_batch(
    backend: Backend, bad: str
) -> None:
    ports, document = backend.admin, backend.new_document()
    tag = await make(ports, "tag")
    selection = (document, MISSING) if bad == "unknown" else (document, document)

    with pytest.raises(Validation, match=r"(?i)don't exist or were specified twice"):
        await ports.bulk_writes.bulk_edit_documents(selection, TagsChange(add=(tag,)))

    assert tag not in (await read(ports, document)).tags


@pytest.mark.contract(DOCUMENTS)
async def test_an_empty_selection_is_accepted_and_changes_nothing(backend: Backend) -> None:
    # Pinned because the service must refuse it itself: Paperless answers OK.
    await backend.admin.bulk_writes.bulk_edit_documents((), TagsChange(add=(1,)))


@pytest.mark.contract(DOCUMENTS)
async def test_deleted_documents_go_to_the_trash_and_cannot_be_changed_there(
    backend: Backend,
) -> None:
    ports = backend.admin
    first, second, kept = (backend.new_document() for _ in range(3))

    await ports.bulk_writes.bulk_edit_documents((first, second), TrashDocuments())

    assert backend.in_trash(first)
    assert backend.in_trash(second)
    assert not backend.in_trash(kept)
    assert (await read(ports, kept)).id == kept
    with pytest.raises(Validation, match=r"(?i)don't exist"):
        await ports.bulk_writes.bulk_edit_documents((first,), TrashDocuments())


@pytest.mark.contract(DOCUMENTS)
async def test_reprocessing_is_accepted_and_the_documents_stay(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()

    await ports.bulk_writes.bulk_edit_documents((document,), ReprocessChange())

    assert (await read(ports, document)).id == document


@pytest.mark.contract(DOCUMENTS)
async def test_a_user_without_the_change_permission_is_denied(backend: Backend) -> None:
    document = backend.new_document()
    tag = await make(backend.admin, "tag")

    with pytest.raises(PermissionDenied):
        await backend.reader.bulk_writes.bulk_edit_documents((document,), TagsChange(add=(tag,)))
    with pytest.raises(PermissionDenied):
        await backend.reader.bulk_writes.bulk_edit_documents((document,), ReprocessChange())

    assert tag not in (await read(backend.admin, document)).tags


@pytest.mark.contract(DOCUMENTS)
async def test_one_document_the_user_may_not_change_refuses_the_whole_batch(
    backend: Backend,
) -> None:
    mine, only_viewed = backend.new_document(), backend.new_document()
    tag = await make(backend.admin, "tag")
    allowed = frozenset({"view_document", "change_document"})
    user = backend.restricted(allowed, change=[mine], view=[only_viewed])

    with pytest.raises(PermissionDenied):
        await user.bulk_writes.bulk_edit_documents((mine, only_viewed), TagsChange(add=(tag,)))

    assert tag not in (await read(backend.admin, mine)).tags
    await user.bulk_writes.bulk_edit_documents((mine,), TagsChange(add=(tag,)))
    assert tag in (await read(backend.admin, mine)).tags


@pytest.mark.contract(DOCUMENTS)
async def test_deleting_needs_ownership_not_only_change_permission(backend: Backend) -> None:
    document = backend.new_document()
    tag = await make(backend.admin, "tag")
    allowed = frozenset({"view_document", "change_document", "delete_document"})
    user = backend.restricted(allowed, change=[document])

    with pytest.raises(PermissionDenied):
        await user.bulk_writes.bulk_edit_documents((document,), TrashDocuments())

    assert not backend.in_trash(document)
    await user.bulk_writes.bulk_edit_documents((document,), TagsChange(add=(tag,)))
    assert tag in (await read(backend.admin, document)).tags


# --- delete_taxonomy_items


@pytest.mark.contract(DELETE_ITEMS)
@pytest.mark.parametrize("kind", KINDS)
async def test_items_are_deleted_together_and_the_others_stay(
    backend: Backend, kind: BulkKind
) -> None:
    ports = backend.admin
    first, second, kept = [await make(ports, kind) for _ in range(3)]

    await ports.bulk_writes.delete_taxonomy_items(kind, (first, second))

    left = {item.id for item in await ports.taxonomy.list_taxonomy(kind)}
    assert not {first, second} & left
    assert kept in left


@pytest.mark.contract(DELETE_ITEMS)
async def test_deleted_tags_take_their_children_and_documents_lose_references(
    backend: Backend,
) -> None:
    ports = backend.admin
    parent = await make(ports, "tag")
    child = await make(ports, "tag", parent=parent)
    correspondent = await make(ports, "correspondent")
    document = backend.new_document()
    await ports.bulk_writes.bulk_edit_documents((document,), TagsChange(add=(child,)))
    await ports.bulk_writes.bulk_edit_documents(
        (document,), ReferenceChange(field="correspondent", value=correspondent)
    )

    await ports.bulk_writes.delete_taxonomy_items("tag", (parent,))
    await ports.bulk_writes.delete_taxonomy_items("correspondent", (correspondent,))

    tags = {item.id for item in await ports.taxonomy.list_taxonomy("tag")}
    assert not {parent, child} & tags
    record = await read(ports, document)
    assert not {parent, child} & set(record.tags)
    assert record.correspondent is None


@pytest.mark.contract(DELETE_ITEMS)
@pytest.mark.parametrize("bad", ["unknown", "twice", "empty"])
async def test_a_bad_selection_deletes_nothing(backend: Backend, bad: str) -> None:
    ports = backend.admin
    item = await make(ports, "correspondent")
    selection = {"unknown": (item, MISSING), "twice": (item, item), "empty": ()}[bad]

    with pytest.raises(Validation):
        await ports.bulk_writes.delete_taxonomy_items("correspondent", selection)

    assert item in {i.id for i in await ports.taxonomy.list_taxonomy("correspondent")}


@pytest.mark.contract(DELETE_ITEMS)
async def test_deleting_needs_the_permission_and_ownership(backend: Backend) -> None:
    item = await make(backend.admin, "correspondent")
    user = backend.restricted(frozenset({"view_correspondent", "delete_correspondent"}))

    with pytest.raises(PermissionDenied):
        await backend.reader.bulk_writes.delete_taxonomy_items("correspondent", (item,))
    with pytest.raises(PermissionDenied):
        await user.bulk_writes.delete_taxonomy_items("correspondent", (item,))

    assert item in {i.id for i in await backend.admin.taxonomy.list_taxonomy("correspondent")}


# --- set_taxonomy_permissions


@pytest.mark.contract(PERMISSIONS)
@pytest.mark.parametrize("kind", KINDS)
async def test_the_owner_is_set_cleared_or_left_alone(backend: Backend, kind: BulkKind) -> None:
    ports = backend.admin
    item = await make(ports, kind)
    admin = backend.grants(kind, item).owner
    reader = await user_id(ports, READER[0])
    assert admin is not None
    grant = ObjectPermissions(view_users=(reader,))

    await ports.bulk_writes.set_taxonomy_permissions(kind, (item,), PermissionsChange(owner=reader))
    assert backend.grants(kind, item).owner == reader

    await ports.bulk_writes.set_taxonomy_permissions(kind, (item,), PermissionsChange(owner=None))
    assert backend.grants(kind, item).owner is None

    await ports.bulk_writes.set_taxonomy_permissions(kind, (item,), PermissionsChange(owner=admin))
    await ports.bulk_writes.set_taxonomy_permissions(
        kind, (item,), PermissionsChange(permissions=grant)
    )
    assert backend.grants(kind, item).owner == admin
    assert backend.grants(kind, item).permissions == grant


@pytest.mark.contract(PERMISSIONS)
async def test_grants_replace_all_existing_ones_unless_merged(backend: Backend) -> None:
    ports = backend.admin
    first, second = await make(ports, "tag"), await make(ports, "tag")
    reader, group = await user_id(ports, READER[0]), await group_id(ports)
    admin = backend.grants("tag", first).owner
    assert admin is not None
    writer = ports.bulk_writes
    start = ObjectPermissions(view_users=(reader,), view_groups=(group,), change_groups=(group,))

    await writer.set_taxonomy_permissions(
        "tag", (first, second), PermissionsChange(permissions=start)
    )
    assert backend.grants("tag", first).permissions == start
    assert backend.grants("tag", second).permissions == start

    more = ObjectPermissions(change_users=(admin,))
    await writer.set_taxonomy_permissions(
        "tag", (first,), PermissionsChange(permissions=more, merge=True)
    )
    # Granting change grants view too.
    assert backend.grants("tag", first).permissions == ObjectPermissions(
        view_users=tuple(sorted({reader, admin})),
        view_groups=(group,),
        change_users=(admin,),
        change_groups=(group,),
    )
    assert backend.grants("tag", second).permissions == start

    replacement = ObjectPermissions(view_users=(admin,))
    await writer.set_taxonomy_permissions(
        "tag", (first,), PermissionsChange(permissions=replacement)
    )
    assert backend.grants("tag", first).permissions == replacement
    await writer.set_taxonomy_permissions(
        "tag", (first,), PermissionsChange(permissions=ObjectPermissions())
    )
    assert backend.grants("tag", first).permissions == ObjectPermissions()


@pytest.mark.contract(PERMISSIONS)
async def test_a_merged_owner_only_fills_objects_that_have_none(backend: Backend) -> None:
    ports = backend.admin
    owned, shared = await make(ports, "correspondent"), await make(ports, "correspondent")
    admin = backend.grants("correspondent", owned).owner
    reader = await user_id(ports, READER[0])
    await ports.bulk_writes.set_taxonomy_permissions(
        "correspondent", (shared,), PermissionsChange(owner=None)
    )

    await ports.bulk_writes.set_taxonomy_permissions(
        "correspondent", (owned, shared), PermissionsChange(owner=reader, merge=True)
    )
    assert backend.grants("correspondent", owned).owner == admin
    assert backend.grants("correspondent", shared).owner == reader

    await ports.bulk_writes.set_taxonomy_permissions(
        "correspondent", (owned, shared), PermissionsChange(owner=None, merge=True)
    )
    assert backend.grants("correspondent", owned).owner == admin
    assert backend.grants("correspondent", shared).owner == reader


@pytest.mark.contract(PERMISSIONS)
async def test_a_user_granted_view_sees_the_object(backend: Backend) -> None:
    ports = backend.admin
    item = await make(ports, "document_type")
    reader = await user_id(ports, READER[0])

    assert item not in {i.id for i in await backend.reader.taxonomy.list_taxonomy("document_type")}
    grant = ObjectPermissions(view_users=(reader,))
    await ports.bulk_writes.set_taxonomy_permissions(
        "document_type", (item,), PermissionsChange(permissions=grant)
    )

    assert item in {i.id for i in await backend.reader.taxonomy.list_taxonomy("document_type")}


@pytest.mark.contract(PERMISSIONS)
@pytest.mark.parametrize(
    "change",
    [
        PermissionsChange(owner=MISSING),
        PermissionsChange(permissions=ObjectPermissions(view_users=(MISSING,))),
        PermissionsChange(permissions=ObjectPermissions(change_groups=(MISSING,))),
    ],
    ids=["owner", "user", "group"],
)
async def test_unknown_users_and_groups_are_rejected_and_nothing_changes(
    backend: Backend, change: PermissionsChange
) -> None:
    ports = backend.admin
    item = await make(ports, "storage_path")
    before = backend.grants("storage_path", item)

    with pytest.raises(Validation):
        await ports.bulk_writes.set_taxonomy_permissions("storage_path", (item,), change)

    assert backend.grants("storage_path", item) == before


@pytest.mark.contract(PERMISSIONS)
@pytest.mark.parametrize("bad", ["unknown", "twice", "empty"])
async def test_a_bad_selection_changes_nothing(backend: Backend, bad: str) -> None:
    ports = backend.admin
    item = await make(ports, "tag")
    before = backend.grants("tag", item)
    selection = {"unknown": (item, MISSING), "twice": (item, item), "empty": ()}[bad]

    with pytest.raises(Validation):
        await ports.bulk_writes.set_taxonomy_permissions(
            "tag", selection, PermissionsChange(owner=None)
        )

    assert backend.grants("tag", item) == before


@pytest.mark.contract(PERMISSIONS)
async def test_changing_permissions_needs_the_permission_and_ownership(backend: Backend) -> None:
    item = await make(backend.admin, "tag")
    user = backend.restricted(frozenset({"view_tag", "change_tag"}))
    before = backend.grants("tag", item)

    with pytest.raises(PermissionDenied):
        await backend.reader.bulk_writes.set_taxonomy_permissions(
            "tag", (item,), PermissionsChange(owner=None)
        )
    with pytest.raises(PermissionDenied):
        await user.bulk_writes.set_taxonomy_permissions(
            "tag", (item,), PermissionsChange(owner=None)
        )

    assert backend.grants("tag", item) == before
