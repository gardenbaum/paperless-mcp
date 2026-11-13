"""Contract of TaxonomyWriter's create, update and delete: fake and live Paperless 3.4.2.

Every object is named with `SCRATCH_ITEM` and a uuid; the live backend deletes what is left when
the test ends. Documents are the test's own (`backend.new_document`), never a seeded one.
"""

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

import pytest

from contract.conftest import SCRATCH_ITEM
from paperless_mcp.domain.documents import CustomFieldValue, DocumentPatch
from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation
from paperless_mcp.domain.taxonomy import OptionInput, TaxonomyValues

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from paperless_mcp.domain.taxonomy import TaxonomyDetail, TaxonomyItem, WritableKind

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-14"),
]

CLEARING_SECONDS = 30
CLEARING_STEP = 0.5
CREATE = "TaxonomyWriter.create_taxonomy_item"
UPDATE = "TaxonomyWriter.update_taxonomy_item"
DELETE = "TaxonomyWriter.delete_taxonomy_item"
MISSING = 999_999
KINDS: tuple[WritableKind, ...] = (
    "tag",
    "correspondent",
    "document_type",
    "storage_path",
    "custom_field",
)
MATCHING_KINDS = ("tag", "correspondent", "document_type", "storage_path")


async def value_after_clearing(backend: Backend, document: int, field: int) -> CustomFieldValue:
    """The document's value of the field once it is None, or the last one after the wait."""
    deadline = time.monotonic() + CLEARING_SECONDS
    while True:
        record = await backend.admin.documents.get_document(document)
        [found] = [v for v in record.custom_fields if v.field == field]
        if found.value is None or time.monotonic() >= deadline:
            return found
        await asyncio.sleep(CLEARING_STEP)


def new_name() -> str:
    return f"{SCRATCH_ITEM}{uuid.uuid4().hex}"


def values_for(kind: WritableKind, **fields: object) -> TaxonomyValues:
    """The minimum Paperless needs to create ``kind``, plus ``fields``."""
    required: dict[str, str] = {
        "storage_path": {"path": "contract/{{ title }}"},
        "custom_field": {"data_type": "string"},
    }.get(kind, {})
    return TaxonomyValues.model_validate({**required, **fields})


async def make(ports: Ports, kind: WritableKind, **fields: object) -> TaxonomyDetail:
    return await ports.taxonomy_writes.create_taxonomy_item(
        kind, new_name(), values_for(kind, **fields)
    )


async def listed(ports: Ports, kind: WritableKind) -> dict[int, TaxonomyItem]:
    return {item.id: item for item in await ports.taxonomy.list_taxonomy(kind)}


def options(*labels: str) -> tuple[OptionInput, ...]:
    return tuple(OptionInput(label=label) for label in labels)


@pytest.mark.contract(CREATE)
@pytest.mark.parametrize("kind", KINDS)
async def test_a_created_object_is_listed_and_has_no_documents(
    backend: Backend, kind: WritableKind
) -> None:
    name = new_name()

    created = await backend.admin.taxonomy_writes.create_taxonomy_item(
        kind, f"  {name}  ", values_for(kind)
    )

    assert (created.name, created.document_count) == (name, 0)
    row = (await listed(backend.admin, kind))[created.id]
    assert row.name == name
    assert row.document_count == 0


@pytest.mark.contract(CREATE)
@pytest.mark.parametrize("kind", MATCHING_KINDS)
async def test_a_created_object_matches_nothing_unless_asked_to(
    backend: Backend, kind: WritableKind
) -> None:
    plain = await make(backend.admin, kind)
    rule = await make(
        backend.admin,
        kind,
        matching_algorithm="regex",
        match="^invoice [0-9]+$",
        case_insensitive=False,
    )

    assert (plain.matching_algorithm, plain.match) == ("none", "")
    assert plain.case_insensitive is True
    assert (rule.matching_algorithm, rule.match, rule.case_insensitive) == (
        "regex",
        "^invoice [0-9]+$",
        False,
    )


@pytest.mark.contract(CREATE)
async def test_a_custom_field_has_no_matching_rule(backend: Backend) -> None:
    created = await make(backend.admin, "custom_field")

    assert (created.matching_algorithm, created.match, created.case_insensitive) == (
        None,
        None,
        None,
    )
    assert created.data_type == "string"
    assert created.select_options is None


@pytest.mark.contract(CREATE)
async def test_a_tag_has_a_colour_a_parent_and_an_inbox_flag(backend: Backend) -> None:
    parent = await make(backend.admin, "tag")

    plain = await make(backend.admin, "tag")
    child = await make(backend.admin, "tag", colour="#ff0000", parent=parent.id, is_inbox_tag=True)

    assert (plain.colour, plain.parent, plain.is_inbox_tag) == ("#a6cee3", None, False)
    assert (child.colour, child.parent, child.is_inbox_tag) == ("#ff0000", parent.id, True)
    assert (await listed(backend.admin, "tag"))[child.id].parent == parent.id


@pytest.mark.contract(CREATE)
async def test_a_storage_path_keeps_its_template(backend: Backend) -> None:
    created = await make(backend.admin, "storage_path", path="{{ created_year }}/{{ title }}")

    assert created.path == "{{ created_year }}/{{ title }}"


@pytest.mark.contract(CREATE)
@pytest.mark.parametrize(
    "path", ["", "   ", "{{ no_such_variable }}/x", "{{ title ", "../{{ title }}"]
)
async def test_a_bad_storage_path_template_is_rejected(backend: Backend, path: str) -> None:
    with pytest.raises(Validation, match="path"):
        _ = await make(backend.admin, "storage_path", path=path)


@pytest.mark.contract(CREATE)
async def test_a_storage_path_needs_a_template(backend: Backend) -> None:
    with pytest.raises(Validation, match="path"):
        _ = await backend.admin.taxonomy_writes.create_taxonomy_item(
            "storage_path", new_name(), TaxonomyValues()
        )


@pytest.mark.contract(CREATE)
async def test_a_select_field_gets_an_id_for_every_option_in_order(backend: Backend) -> None:
    created = await backend.admin.taxonomy_writes.create_taxonomy_item(
        "custom_field",
        new_name(),
        TaxonomyValues(data_type="select", select_options=options("one", "two", "three")),
    )

    assert created.data_type == "select"
    assert created.select_options is not None
    assert [o.label for o in created.select_options] == ["one", "two", "three"]
    ids = [o.id for o in created.select_options]
    assert len(set(ids)) == 3
    assert all(ids)
    assert (await listed(backend.admin, "custom_field"))[created.id].select_options == (
        created.select_options
    )


@pytest.mark.contract(CREATE)
@pytest.mark.parametrize("given", [{}, {"select_options": ()}])
async def test_a_select_field_needs_options(backend: Backend, given: dict[str, object]) -> None:
    values = TaxonomyValues.model_validate({"data_type": "select", **given})

    with pytest.raises(Validation, match="select_options"):
        _ = await backend.admin.taxonomy_writes.create_taxonomy_item(
            "custom_field", new_name(), values
        )


@pytest.mark.contract(CREATE)
async def test_a_custom_field_needs_a_data_type(backend: Backend) -> None:
    with pytest.raises(Validation, match="data_type"):
        _ = await backend.admin.taxonomy_writes.create_taxonomy_item(
            "custom_field", new_name(), TaxonomyValues()
        )


@pytest.mark.contract(CREATE)
@pytest.mark.parametrize("kind", KINDS)
async def test_a_name_twice_is_rejected(backend: Backend, kind: WritableKind) -> None:
    first = await make(backend.admin, kind)

    with pytest.raises(Validation):
        _ = await backend.admin.taxonomy_writes.create_taxonomy_item(
            kind, first.name, values_for(kind)
        )


@pytest.mark.contract(CREATE)
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", ["", "  ", "x" * 129])
async def test_a_blank_or_too_long_name_is_rejected(
    backend: Backend, kind: WritableKind, name: str
) -> None:
    with pytest.raises(Validation, match="name"):
        _ = await backend.admin.taxonomy_writes.create_taxonomy_item(kind, name, values_for(kind))


@pytest.mark.contract(CREATE)
@pytest.mark.parametrize(
    ("fields", "where"),
    [
        ({"colour": "red"}, "color"),
        ({"colour": "#fff"}, "color"),
        ({"parent": MISSING}, "parent"),
        ({"matching_algorithm": "regex", "match": "("}, "match"),
        ({"match": "x" * 257}, "match"),
    ],
)
async def test_a_value_paperless_refuses_is_a_validation_error(
    backend: Backend, fields: dict[str, object], where: str
) -> None:
    with pytest.raises(Validation, match=where):
        _ = await make(backend.admin, "tag", **fields)


@pytest.mark.contract(CREATE)
@pytest.mark.parametrize("kind", ["tag", "correspondent", "document_type", "storage_path"])
async def test_an_object_belongs_to_its_creator(backend: Backend, kind: WritableKind) -> None:
    created = await make(backend.admin, kind)

    assert created.id not in await listed(backend.reader, kind)


@pytest.mark.contract(CREATE)
@pytest.mark.parametrize("kind", KINDS)
async def test_a_user_who_may_not_add_is_denied(backend: Backend, kind: WritableKind) -> None:
    with pytest.raises(PermissionDenied):
        _ = await make(backend.reader, kind)


@pytest.mark.contract(UPDATE)
async def test_an_update_changes_what_it_sets_and_nothing_else(backend: Backend) -> None:
    created = await make(
        backend.admin, "tag", colour="#ff0000", matching_algorithm="any", match="invoice"
    )

    renamed = await backend.admin.taxonomy_writes.update_taxonomy_item(
        "tag", created.id, TaxonomyValues(name=f"{created.name}-renamed")
    )
    recoloured = await backend.admin.taxonomy_writes.update_taxonomy_item(
        "tag", created.id, TaxonomyValues(colour="#00ff00", case_insensitive=False)
    )

    assert renamed.name == f"{created.name}-renamed"
    assert renamed.model_copy(update={"name": created.name}) == created
    assert recoloured.model_copy(update={"colour": "#ff0000", "case_insensitive": True}) == (
        renamed
    )
    assert (recoloured.colour, recoloured.case_insensitive) == ("#00ff00", False)
    assert (await listed(backend.admin, "tag"))[created.id].colour == "#00ff00"


@pytest.mark.contract(UPDATE)
@pytest.mark.parametrize("kind", MATCHING_KINDS)
async def test_the_matching_rule_can_be_changed_and_switched_off(
    backend: Backend, kind: WritableKind
) -> None:
    created = await make(backend.admin, kind)
    writes = backend.admin.taxonomy_writes

    on = await writes.update_taxonomy_item(
        kind, created.id, TaxonomyValues(matching_algorithm="all", match="alpha beta")
    )
    off = await writes.update_taxonomy_item(
        kind, created.id, TaxonomyValues(matching_algorithm="none")
    )

    assert (on.matching_algorithm, on.match) == ("all", "alpha beta")
    assert (off.matching_algorithm, off.match) == ("none", "alpha beta")


@pytest.mark.contract(UPDATE)
async def test_an_invalid_pattern_is_rejected_also_when_only_the_algorithm_changes(
    backend: Backend,
) -> None:
    created = await make(backend.admin, "correspondent", match="(")

    with pytest.raises(Validation, match="match"):
        _ = await backend.admin.taxonomy_writes.update_taxonomy_item(
            "correspondent", created.id, TaxonomyValues(matching_algorithm="regex")
        )


@pytest.mark.contract(UPDATE)
async def test_an_invalid_pattern_is_rejected_also_when_only_the_pattern_changes(
    backend: Backend,
) -> None:
    created = await make(backend.admin, "correspondent", matching_algorithm="regex", match="^a")

    with pytest.raises(Validation, match="match"):
        _ = await backend.admin.taxonomy_writes.update_taxonomy_item(
            "correspondent", created.id, TaxonomyValues(match="(")
        )

    still = (await listed(backend.admin, "correspondent"))[created.id]
    assert still.name == created.name


@pytest.mark.contract(UPDATE)
async def test_a_storage_path_template_can_be_changed_but_not_broken(backend: Backend) -> None:
    created = await make(backend.admin, "storage_path")
    writes = backend.admin.taxonomy_writes

    changed = await writes.update_taxonomy_item(
        "storage_path", created.id, TaxonomyValues(path="{{ correspondent }}/{{ title }}")
    )
    with pytest.raises(Validation, match="path"):
        _ = await writes.update_taxonomy_item(
            "storage_path", created.id, TaxonomyValues(path="{{ nope }}")
        )

    assert changed.path == "{{ correspondent }}/{{ title }}"


@pytest.mark.contract(UPDATE)
async def test_the_parent_of_a_tag_can_be_set_and_cleared(backend: Backend) -> None:
    parent, child = await make(backend.admin, "tag"), await make(backend.admin, "tag")
    writes = backend.admin.taxonomy_writes

    moved = await writes.update_taxonomy_item("tag", child.id, TaxonomyValues(parent=parent.id))
    freed = await writes.update_taxonomy_item("tag", child.id, TaxonomyValues(parent=None))

    assert moved.parent == parent.id
    assert freed.parent is None


@pytest.mark.contract(UPDATE)
async def test_a_tag_cannot_be_its_own_ancestor(backend: Backend) -> None:
    top = await make(backend.admin, "tag")
    middle = await make(backend.admin, "tag", parent=top.id)
    bottom = await make(backend.admin, "tag", parent=middle.id)
    writes = backend.admin.taxonomy_writes

    for tag, parent in ((top, top), (top, middle), (top, bottom), (middle, bottom)):
        with pytest.raises(Validation, match="parent"):
            _ = await writes.update_taxonomy_item("tag", tag.id, TaxonomyValues(parent=parent.id))

    assert (await listed(backend.admin, "tag"))[top.id].parent is None


@pytest.mark.contract(UPDATE)
async def test_an_unknown_parent_is_rejected(backend: Backend) -> None:
    tag = await make(backend.admin, "tag")

    with pytest.raises(Validation, match="parent"):
        _ = await backend.admin.taxonomy_writes.update_taxonomy_item(
            "tag", tag.id, TaxonomyValues(parent=MISSING)
        )


@pytest.mark.contract(UPDATE)
@pytest.mark.parametrize("kind", KINDS)
async def test_a_name_that_is_taken_is_rejected(backend: Backend, kind: WritableKind) -> None:
    first, second = await make(backend.admin, kind), await make(backend.admin, kind)

    with pytest.raises(Validation):
        _ = await backend.admin.taxonomy_writes.update_taxonomy_item(
            kind, second.id, TaxonomyValues(name=first.name)
        )


@pytest.mark.contract(UPDATE)
async def test_an_update_answers_with_the_documents_that_use_the_object(
    backend: Backend,
) -> None:
    tag, document = await make(backend.admin, "tag"), backend.new_document()
    _ = await backend.admin.document_writes.update_document(document, DocumentPatch(tags=(tag.id,)))

    renamed = await backend.admin.taxonomy_writes.update_taxonomy_item(
        "tag", tag.id, TaxonomyValues(name=f"{tag.name}-b")
    )

    assert renamed.document_count == 1


async def select_field(backend: Backend, *labels: str) -> TaxonomyDetail:
    return await backend.admin.taxonomy_writes.create_taxonomy_item(
        "custom_field",
        new_name(),
        TaxonomyValues(data_type="select", select_options=options(*labels)),
    )


@pytest.mark.contract(UPDATE)
async def test_renaming_a_select_field_keeps_its_options_and_ids(backend: Backend) -> None:
    field = await select_field(backend, "one", "two")

    renamed = await backend.admin.taxonomy_writes.update_taxonomy_item(
        "custom_field", field.id, TaxonomyValues(name=f"{field.name}-b")
    )

    assert renamed.name == f"{field.name}-b"
    assert renamed.select_options == field.select_options


@pytest.mark.contract(UPDATE)
async def test_options_given_with_their_ids_keep_them_and_a_new_one_gets_its_own(
    backend: Backend,
) -> None:
    field = await select_field(backend, "one", "two")
    assert field.select_options is not None
    one, two = field.select_options

    changed = await backend.admin.taxonomy_writes.update_taxonomy_item(
        "custom_field",
        field.id,
        TaxonomyValues(
            select_options=(
                OptionInput(id=one.id, label="ONE"),
                OptionInput(id=two.id, label="two"),
                OptionInput(label="three"),
            )
        ),
    )

    assert changed.select_options is not None
    assert [(o.id, o.label) for o in changed.select_options[:2]] == [
        (one.id, "ONE"),
        (two.id, "two"),
    ]
    assert changed.select_options[2].label == "three"
    assert changed.select_options[2].id not in {one.id, two.id}


@pytest.mark.contract(UPDATE)
async def test_options_given_without_ids_are_all_new(backend: Backend) -> None:
    """Why callers send the ids back: Paperless gives every option without one a new id."""
    field = await select_field(backend, "one", "two")
    assert field.select_options is not None

    changed = await backend.admin.taxonomy_writes.update_taxonomy_item(
        "custom_field", field.id, TaxonomyValues(select_options=options("one", "two"))
    )

    assert changed.select_options is not None
    assert {o.id for o in changed.select_options}.isdisjoint({o.id for o in field.select_options})


@pytest.mark.contract(UPDATE)
async def test_the_documents_that_held_a_removed_option_end_up_without_a_value(
    backend: Backend,
) -> None:
    field = await select_field(backend, "one", "two")
    assert field.select_options is not None
    one, two = field.select_options
    document = backend.new_document()
    other = backend.new_document()
    _ = await backend.admin.document_writes.update_document(
        document, DocumentPatch(custom_fields=(CustomFieldValue(field=field.id, value=two.id),))
    )
    _ = await backend.admin.document_writes.update_document(
        other, DocumentPatch(custom_fields=(CustomFieldValue(field=field.id, value=one.id),))
    )

    _ = await backend.admin.taxonomy_writes.update_taxonomy_item(
        "custom_field",
        field.id,
        TaxonomyValues(select_options=(OptionInput(id=one.id, label="one"),)),
    )

    # Paperless clears the value in a Celery task (process_cf_select_update) after the save.
    assert await value_after_clearing(backend, document, field.id) == CustomFieldValue(
        field=field.id, value=None
    )
    kept = await backend.admin.documents.get_document(other)
    assert kept.custom_fields == (CustomFieldValue(field=field.id, value=one.id),)
    with pytest.raises(Validation):
        _ = await backend.admin.document_writes.update_document(
            document,
            DocumentPatch(custom_fields=(CustomFieldValue(field=field.id, value=two.id),)),
        )


@pytest.mark.contract(UPDATE)
async def test_a_select_field_cannot_lose_all_options(backend: Backend) -> None:
    field = await select_field(backend, "one")

    with pytest.raises(Validation, match="select_options"):
        _ = await backend.admin.taxonomy_writes.update_taxonomy_item(
            "custom_field", field.id, TaxonomyValues(select_options=())
        )


@pytest.mark.contract(UPDATE)
@pytest.mark.parametrize("kind", KINDS)
async def test_updating_a_missing_object_is_not_found(backend: Backend, kind: WritableKind) -> None:
    with pytest.raises(NotFound):
        _ = await backend.admin.taxonomy_writes.update_taxonomy_item(
            kind, MISSING, TaxonomyValues(name="x")
        )


@pytest.mark.contract(UPDATE)
@pytest.mark.parametrize("kind", KINDS)
async def test_a_user_who_may_not_change_is_denied(backend: Backend, kind: WritableKind) -> None:
    created = await make(backend.admin, kind)

    with pytest.raises(PermissionDenied):
        _ = await backend.reader.taxonomy_writes.update_taxonomy_item(
            kind, created.id, TaxonomyValues(name="x")
        )


@pytest.mark.contract(DELETE)
@pytest.mark.parametrize("kind", KINDS)
async def test_a_deleted_object_is_gone(backend: Backend, kind: WritableKind) -> None:
    created = await make(backend.admin, kind)

    await backend.admin.taxonomy_writes.delete_taxonomy_item(kind, created.id)

    assert created.id not in await listed(backend.admin, kind)
    with pytest.raises(NotFound):
        await backend.admin.taxonomy_writes.delete_taxonomy_item(kind, created.id)


@pytest.mark.contract(DELETE)
async def test_a_deleted_tag_takes_its_child_tags_and_leaves_the_documents(
    backend: Backend,
) -> None:
    parent = await make(backend.admin, "tag")
    child = await make(backend.admin, "tag", parent=parent.id)
    grandchild = await make(backend.admin, "tag", parent=child.id)
    other = await make(backend.admin, "tag")
    document = backend.new_document()
    _ = await backend.admin.document_writes.update_document(
        document, DocumentPatch(tags=(parent.id, child.id, grandchild.id, other.id))
    )

    await backend.admin.taxonomy_writes.delete_taxonomy_item("tag", parent.id)

    rows = await listed(backend.admin, "tag")
    assert {parent.id, child.id, grandchild.id}.isdisjoint(rows)
    assert other.id in rows
    record = await backend.admin.documents.get_document(document)
    assert other.id in record.tags
    assert {parent.id, child.id, grandchild.id}.isdisjoint(record.tags)


@pytest.mark.contract(DELETE)
@pytest.mark.parametrize("kind", ["correspondent", "document_type", "storage_path"])
async def test_documents_lose_a_deleted_reference_but_stay(
    backend: Backend, kind: WritableKind
) -> None:
    created = await make(backend.admin, kind)
    document = backend.new_document()
    _ = await backend.admin.document_writes.update_document(
        document, DocumentPatch.model_validate({kind: created.id})
    )

    await backend.admin.taxonomy_writes.delete_taxonomy_item(kind, created.id)

    record = await backend.admin.documents.get_document(document)
    assert getattr(record, kind) is None


@pytest.mark.contract(DELETE)
async def test_a_deleted_custom_field_leaves_the_documents_without_its_value(
    backend: Backend,
) -> None:
    field = await make(backend.admin, "custom_field", data_type="integer")
    document = backend.new_document()
    _ = await backend.admin.document_writes.update_document(
        document, DocumentPatch(custom_fields=(CustomFieldValue(field=field.id, value=5),))
    )

    await backend.admin.taxonomy_writes.delete_taxonomy_item("custom_field", field.id)

    record = await backend.admin.documents.get_document(document)
    assert field.id not in {value.field for value in record.custom_fields}


@pytest.mark.contract(DELETE)
@pytest.mark.parametrize("kind", KINDS)
async def test_deleting_a_missing_object_is_not_found(backend: Backend, kind: WritableKind) -> None:
    with pytest.raises(NotFound):
        await backend.admin.taxonomy_writes.delete_taxonomy_item(kind, MISSING)


@pytest.mark.contract(DELETE)
@pytest.mark.parametrize("kind", KINDS)
async def test_a_user_who_may_not_delete_is_denied(backend: Backend, kind: WritableKind) -> None:
    created = await make(backend.admin, kind)

    with pytest.raises(PermissionDenied):
        await backend.reader.taxonomy_writes.delete_taxonomy_item(kind, created.id)

    assert created.id in await listed(backend.admin, kind)
