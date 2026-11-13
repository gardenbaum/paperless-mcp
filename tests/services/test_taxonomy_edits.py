"""TaxonomyEditor: per-kind checks, names to ids, option merges, the delete preview, caches."""

from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import (
    FakeCustomField,
    FakeDocument,
    FakePaperless,
    FakeState,
    FakeTag,
    seeded,
)

from paperless_mcp.domain.errors import (
    Ambiguous,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from paperless_mcp.domain.taxonomy import SelectOption, TaxonomyValues
from paperless_mcp.domain.taxonomy_edits import TaxonomyCreate, TaxonomyEdit
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.taxonomy import TaxonomyResolver
from paperless_mcp.services.taxonomy_edits import PATH_HINT, TaxonomyEditor

if TYPE_CHECKING:
    from collections.abc import Awaitable, Collection

    from paperless_mcp.domain.documents import DocumentPage, DocumentQuery
    from paperless_mcp.domain.taxonomy import (
        TaxonomyDetail,
        TaxonomyItem,
        TaxonomyKind,
        WritableKind,
    )

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-14"),
]


class Recording(FakePaperless):
    """A fake that remembers every write it was given."""

    def __init__(self, state: FakeState, *, user: str) -> None:
        super().__init__(state, user=user, allow_writes=True)
        self.writes: list[tuple[str, object]] = []
        self.updates: list[TaxonomyValues] = []
        self.lists: list[TaxonomyKind] = []

    @override
    async def create_taxonomy_item(
        self, kind: WritableKind, name: str, values: TaxonomyValues
    ) -> TaxonomyDetail:
        self.writes.append(("create", (kind, name, values)))
        return await super().create_taxonomy_item(kind, name, values)

    @override
    async def update_taxonomy_item(
        self, kind: WritableKind, item_id: int, values: TaxonomyValues
    ) -> TaxonomyDetail:
        self.writes.append(("update", (kind, item_id, values)))
        self.updates.append(values)
        return await super().update_taxonomy_item(kind, item_id, values)

    @override
    async def delete_taxonomy_item(self, kind: WritableKind, item_id: int) -> None:
        self.writes.append(("delete", (kind, item_id)))
        await super().delete_taxonomy_item(kind, item_id)

    @override
    async def list_taxonomy(self, kind: TaxonomyKind):  # noqa: ANN201 -- see the base class
        self.lists.append(kind)
        return await super().list_taxonomy(kind)


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, grant: Collection[str] = ()) -> None:
        self.state: FakeState = seeded()
        self.state.users = [
            replace(u, permissions=u.permissions | frozenset(grant)) if u.username == user else u
            for u in self.state.users
        ]
        self.fake: Recording = Recording(self.state, user=user)
        self.resolver: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.editor: TaxonomyEditor = TaxonomyEditor(self.fake, self.fake, self.resolver, self.fake)

    async def create(
        self, kind: WritableKind, name: str = "New", **fields: object
    ) -> TaxonomyDetail:
        return await self.editor.create(
            kind, TaxonomyCreate.model_validate({"name": name, **fields})
        )

    async def update(self, kind: WritableKind, item: str | int, **fields: object) -> TaxonomyDetail:
        return await self.editor.update(kind, item, TaxonomyEdit.model_validate(fields))

    def field(self, name: str) -> int:
        [number] = [f.id for f in self.state.custom_fields if f.name == name]
        return number


# -- create


async def test_each_kind_is_created_with_what_it_needs_and_listed_afterwards() -> None:
    setup = Setup()

    tag = await setup.create("tag", "Receipts", colour="#112233", parent="e2e-finance")
    correspondent = await setup.create(
        "correspondent", "Acme Corp", matching_algorithm="any", match="acme"
    )
    document_type = await setup.create("document_type", "Contract")
    path = await setup.create("storage_path", "By year", path="{{ created_year }}/{{ title }}")
    field = await setup.create(
        "custom_field", "Rating", data_type="select", select_options=["good", "bad"]
    )

    assert (tag.parent, tag.colour, tag.matching_algorithm) == (1, "#112233", "none")
    assert (correspondent.matching_algorithm, correspondent.match) == ("any", "acme")
    assert document_type.matching_algorithm == "none"
    assert path.path == "{{ created_year }}/{{ title }}"
    assert field.select_options is not None
    assert [o.label for o in field.select_options] == ["good", "bad"]
    resolved = await setup.resolver.resolve("tag", "Receipts")
    assert resolved.id == tag.id


async def test_the_parent_is_found_by_name_ignoring_case_or_by_id() -> None:
    setup = Setup()

    by_name = await setup.create("tag", "A", parent="E2E-FINANCE")
    by_id = await setup.create("tag", "B", parent=1)

    assert (by_name.parent, by_id.parent) == (1, 1)


async def test_an_unknown_parent_is_not_found_with_suggestions_and_nothing_is_created() -> None:
    setup = Setup()

    with pytest.raises(NotFound, match="e2e-finnance") as raised:
        _ = await setup.create("tag", "A", parent="e2e-finnance")

    assert raised.value.hint
    assert "e2e-finance" in raised.value.hint
    assert setup.fake.writes == []


@pytest.mark.parametrize(
    ("kind", "fields", "message"),
    [
        ("correspondent", {"colour": "#112233"}, "colour is not a setting of a correspondent"),
        ("document_type", {"parent": 1}, "parent is not a setting of a document type"),
        ("storage_path", {"is_inbox_tag": True}, "is_inbox_tag is not a setting of a storage path"),
        ("tag", {"path": "x"}, "path is not a setting of a tag"),
        ("custom_field", {"match": "x"}, "match is not a setting of a custom field"),
        ("custom_field", {"matching_algorithm": "any"}, "matching_algorithm is not a setting"),
        ("tag", {"data_type": "string"}, "data_type is not a setting of a tag"),
        ("tag", {"select_options": ["a"]}, "select_options is not a setting of a tag"),
    ],
)
async def test_a_setting_that_does_not_belong_to_the_kind_is_refused(
    kind: WritableKind, fields: dict[str, object], message: str
) -> None:
    setup = Setup()

    with pytest.raises(Validation, match=message) as raised:
        _ = await setup.create(kind, "x", **fields)

    assert raised.value.hint
    assert setup.fake.writes == []


@pytest.mark.parametrize(
    ("kind", "fields", "message"),
    [
        ("storage_path", {}, "needs a path"),
        ("storage_path", {"path": "  "}, "needs a path"),
        ("custom_field", {}, "needs a data_type"),
        ("custom_field", {"data_type": "select"}, "at least one option"),
        ("custom_field", {"data_type": "select", "select_options": ["a", " "]}, "blank"),
        ("custom_field", {"data_type": "select", "select_options": ["a", "A"]}, "twice"),
        ("custom_field", {"data_type": "integer", "select_options": ["a"]}, "only for select"),
        ("tag", {"match": "invoice"}, "matching_algorithm"),
        ("tag", {"colour": "red"}, "#rrggbb"),
        ("tag", {"colour": "#fff"}, "#rrggbb"),
    ],
)
async def test_what_is_wrong_before_paperless_is_asked_is_refused_with_a_hint(
    kind: WritableKind, fields: dict[str, object], message: str
) -> None:
    setup = Setup()

    with pytest.raises(Validation, match=message) as raised:
        _ = await setup.create(kind, "x", **fields)

    assert raised.value.hint
    assert setup.fake.writes == []


@pytest.mark.parametrize("name", ["", "   ", "x" * 129])
async def test_a_blank_or_too_long_name_is_refused(name: str) -> None:
    setup = Setup()

    with pytest.raises(Validation, match="name"):
        _ = await setup.create("tag", name)

    assert setup.fake.writes == []


async def test_a_rejection_by_paperless_comes_through_and_a_template_error_gets_a_hint() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="path") as raised:
        _ = await setup.create("storage_path", "x", path="{{ nope }}")

    assert raised.value.hint
    assert "{{ created_year }}" in raised.value.hint


async def test_a_user_who_may_not_add_is_denied() -> None:
    setup = Setup(READER[0])

    with pytest.raises(PermissionDenied):
        _ = await setup.create("tag", "x")


async def test_the_cache_of_the_kind_is_dropped_after_a_create_for_every_credential() -> None:
    setup = Setup()
    before = await setup.resolver.items("correspondent")
    _ = await setup.resolver.items("tag")
    reads = list(setup.fake.lists)

    _ = await setup.create("correspondent", "Fresh")

    assert "Fresh" not in {i.name for i in before}
    assert "Fresh" in {i.name for i in await setup.resolver.items("correspondent")}
    assert (await setup.resolver.resolve("correspondent", "Fresh")).name == "Fresh"
    # The tag list was not asked for again.
    _ = await setup.resolver.items("tag")
    assert setup.fake.lists.count("tag") == reads.count("tag")


async def test_the_cache_is_dropped_even_when_the_write_fails() -> None:
    setup = Setup()
    _ = await setup.resolver.items("storage_path")
    reads = setup.fake.lists.count("storage_path")

    with pytest.raises(Validation):
        _ = await setup.create("storage_path", "x", path="{{ nope }}")

    _ = await setup.resolver.items("storage_path")
    assert setup.fake.lists.count("storage_path") == reads + 1


# -- update


async def test_an_item_is_found_by_name_or_id_and_only_what_is_named_changes() -> None:
    setup = Setup()

    renamed = await setup.update("correspondent", "E2E-ACME", name="Acme")
    recoloured = await setup.update("tag", 1, colour="#010203")

    assert renamed.name == "Acme"
    assert recoloured.colour == "#010203"
    assert setup.fake.updates[0].model_fields_set == {"name"}


async def test_nothing_to_change_is_refused_before_anything_is_read() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="Nothing to change") as raised:
        _ = await setup.update("tag", 1)

    assert raised.value.hint
    assert setup.fake.lists == []
    assert setup.fake.writes == []


async def test_a_name_is_found_in_the_current_state_not_in_a_stale_cache() -> None:
    setup = Setup()
    _ = await setup.resolver.items("tag")
    created = await setup.fake.create_taxonomy_item(
        "tag", "elsewhere", TaxonomyValues()
    )  # another client's write, behind the resolver

    changed = await setup.update("tag", "elsewhere", colour="#010203")

    assert changed.id == created.id


async def test_an_unknown_or_ambiguous_item_changes_nothing() -> None:
    setup = Setup()
    setup.state.tags.append(FakeTag(id=9, name="E2E-FINANCE"))

    with pytest.raises(NotFound, match="No tag has id 99"):
        _ = await setup.update("tag", 99, colour="#010203")
    with pytest.raises(NotFound):
        _ = await setup.update("tag", "nope", colour="#010203")
    with pytest.raises(Ambiguous):
        _ = await setup.update("tag", "e2E-finance", colour="#010203")

    assert setup.fake.writes == []


async def test_the_parent_can_be_changed_by_name_and_cleared() -> None:
    setup = Setup()

    moved = await setup.update("tag", "e2e-inbox", parent="e2e-finance")
    freed = await setup.update("tag", "e2e-inbox", clear_parent=True)

    assert (moved.parent, freed.parent) == (1, None)


async def test_a_parent_that_would_make_a_cycle_is_paperless_s_error() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="descendant"):
        _ = await setup.update("tag", "e2e-finance", parent="e2e-invoices")
    with pytest.raises(Validation, match="itself"):
        _ = await setup.update("tag", "e2e-finance", parent="e2e-finance")


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"parent": "e2e-finance", "clear_parent": True}, "parent or clear_parent"),
        ({"colour": "blue"}, "#rrggbb"),
        ({"name": " "}, "name"),
        ({"options_add": ["x"]}, "only select"),
        ({"options_rename": {"a": "b"}}, "only select"),
        ({"options_remove": ["a"]}, "only select"),
    ],
)
async def test_a_contradictory_or_misplaced_change_is_refused(
    fields: dict[str, object], message: str
) -> None:
    setup = Setup()

    with pytest.raises(Validation, match=message):
        _ = await setup.update("tag", "e2e-inbox", **fields)

    assert setup.fake.writes == []


async def test_clearing_the_parent_of_something_that_is_not_a_tag_is_refused() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="no parent: only tags do"):
        _ = await setup.update("correspondent", 1, clear_parent=True)


async def test_a_plain_custom_field_is_created_with_its_data_type() -> None:
    setup = Setup()

    field = await setup.create("custom_field", "Count", data_type="integer")

    assert (field.data_type, field.select_options) == ("integer", None)


async def test_a_setting_of_another_kind_is_refused_on_update_too() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="colour is not a setting of a correspondent"):
        _ = await setup.update("correspondent", 1, colour="#112233")


async def test_a_user_who_may_not_change_is_denied() -> None:
    setup = Setup(READER[0])

    with pytest.raises(PermissionDenied):
        _ = await setup.update("tag", 1, name="x")


async def test_the_cache_of_the_kind_is_dropped_after_an_update() -> None:
    setup = Setup()
    _ = await setup.resolver.items("tag")

    _ = await setup.update("tag", 1, name="renamed-finance")

    assert (await setup.resolver.resolve("tag", "renamed-finance")).id == 1
    with pytest.raises(NotFound):
        _ = await setup.resolver.resolve("tag", "e2e-finance")


# -- select options


def options_of(setup: Setup, name: str = "e2e-select") -> list[tuple[str, str]]:
    [field] = [f for f in setup.state.custom_fields if f.name == name]
    return [(o.id, o.label) for o in field.select_options]


async def test_options_are_added_renamed_and_removed_and_the_others_keep_their_ids() -> None:
    setup = Setup()
    alpha, beta = options_of(setup)

    changed = await setup.update(
        "custom_field",
        "e2e-select",
        options_add=["gamma"],
        options_rename={"alpha": "ALPHA"},
        options_remove=["beta"],
        confirm=True,
    )

    assert changed.select_options is not None
    [first, added] = changed.select_options
    assert (first.id, first.label) == (alpha[0], "ALPHA")
    assert added.label == "gamma"
    assert added.id not in {alpha[0], beta[0]}
    assert options_of(setup) == [(first.id, "ALPHA"), (added.id, "gamma")]


async def test_adding_and_renaming_options_needs_no_confirm_and_keeps_every_id() -> None:
    setup = Setup()
    before = options_of(setup)

    _ = await setup.update("custom_field", "e2e-select", options_add=["gamma"])
    _ = await setup.update("custom_field", "e2e-select", options_rename={"beta": "BETA"})

    assert [i for i, _ in options_of(setup)][:2] == [i for i, _ in before]
    assert [label for _, label in options_of(setup)] == ["alpha", "BETA", "gamma"]


async def test_options_are_named_by_label_ignoring_case_or_by_id() -> None:
    setup = Setup()
    [(alpha_id, _), _] = options_of(setup)

    _ = await setup.update("custom_field", "e2e-select", options_rename={"ALPHA": "a1"})
    _ = await setup.update("custom_field", "e2e-select", options_rename={alpha_id: "a2"})

    assert options_of(setup)[0] == (alpha_id, "a2")


async def test_removing_an_option_needs_confirm_and_counts_the_documents_that_hold_it() -> None:
    setup = Setup()
    before = options_of(setup)

    with pytest.raises(Validation) as unused:
        _ = await setup.update("custom_field", "e2e-select", options_remove=["beta"])
    with pytest.raises(Validation) as used:
        _ = await setup.update("custom_field", "e2e-select", options_remove=["alpha"])

    assert unused.value.message == (
        "Removing options needs confirm=true: 0 documents you can see hold a removed option of "
        "'e2e-select' and would keep a value that is no longer one of its options; "
        "update_document can no longer set it."
    )
    assert used.value.message.startswith("Removing options needs confirm=true: 1 document you")
    assert used.value.hint == "Pass confirm=true to remove the options."
    assert options_of(setup) == before
    assert setup.fake.writes == []


async def test_labels_that_differ_only_in_case_need_the_id_to_be_told_apart() -> None:
    setup = Setup()
    upper_id = "id-upper"
    setup.state.custom_fields.append(
        FakeCustomField(
            id=99,
            name="Case",
            data_type="select",
            select_options=(
                SelectOption(id=upper_id, label="Alpha"),
                SelectOption(id="id-lower", label="alpha"),
            ),
        )
    )

    with pytest.raises(Ambiguous, match="Several options"):
        _ = await setup.update("custom_field", "Case", options_rename={"ALPHA": "x"})
    renamed = await setup.update("custom_field", "Case", options_rename={"Alpha": "x"})
    by_id = await setup.update("custom_field", "Case", options_rename={upper_id: "y"})

    assert renamed.select_options is not None
    assert [o.label for o in renamed.select_options] == ["x", "alpha"]
    assert by_id.select_options is not None
    assert [o.label for o in by_id.select_options] == ["y", "alpha"]


async def test_renaming_the_field_alone_leaves_its_options_alone() -> None:
    setup = Setup()
    before = options_of(setup)

    changed = await setup.update("custom_field", "e2e-select", name="e2e-choice")

    assert changed.name == "e2e-choice"
    assert options_of(setup, "e2e-choice") == before


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"options_add": ["alpha"]}, "already"),
        ({"options_add": ["x", "X"]}, "twice"),
        ({"options_add": [" "]}, "blank"),
        ({"options_rename": {"gamma": "x"}}, "no option 'gamma'"),
        ({"options_rename": {"alpha": "beta"}}, "already"),
        ({"options_rename": {"alpha": " "}}, "blank"),
        ({"options_remove": ["gamma"]}, "no option 'gamma'"),
        ({"options_remove": ["alpha", "beta"], "confirm": True}, "at least one option"),
        ({"options_rename": {"alpha": "x"}, "options_remove": ["alpha"], "confirm": True}, "both"),
    ],
)
async def test_option_changes_that_make_no_sense_are_refused_and_nothing_is_written(
    fields: dict[str, object], message: str
) -> None:
    setup = Setup()
    before = options_of(setup)

    with pytest.raises(Validation, match=message):
        _ = await setup.update("custom_field", "e2e-select", **fields)

    assert options_of(setup) == before
    assert setup.fake.writes == []


async def test_options_of_a_field_that_is_not_a_select_are_refused() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="only select"):
        _ = await setup.update("custom_field", "e2e-string", options_add=["x"])


async def test_an_option_that_was_just_renamed_elsewhere_is_found_in_the_current_state() -> None:
    setup = Setup()
    stale = await setup.resolver.items("custom_field")
    setup.state.custom_fields[:] = [
        replace(
            f,
            select_options=(
                f.select_options[0].model_copy(update={"label": "renamed"}),
                *f.select_options[1:],
            ),
        )
        if f.name == "e2e-select"
        else f
        for f in setup.state.custom_fields
    ]

    changed = await setup.update("custom_field", "e2e-select", options_rename={"renamed": "r2"})

    assert stale
    assert changed.select_options is not None
    assert [o.label for o in changed.select_options] == ["r2", "beta"]


# -- delete


HIDDEN = "Child tags you cannot see (other users') are deleted with it as well."
EFFECTS = [
    ("tag", "e2e-private", 3, "Tag", "It is removed from its documents (1 document). " + HIDDEN),
    (
        "correspondent",
        "e2e-acme",
        1,
        "Correspondent",
        "The documents that use it (1 document) lose their correspondent.",
    ),
    (
        "document_type",
        "e2e-invoice",
        1,
        "Document type",
        "The documents that use it (1 document) lose their document type.",
    ),
    (
        "storage_path",
        "e2e-archive",
        1,
        "Storage path",
        "The documents that use it (1 document) lose their storage path.",
    ),
    (
        "custom_field",
        "e2e-string",
        1,
        "Custom field",
        "The documents that have the field (1 document) lose the field and its values.",
    ),
]


@pytest.mark.parametrize(("kind", "name", "number", "label", "effect"), EFFECTS)
async def test_a_delete_without_confirm_explains_and_deletes_nothing(
    kind: WritableKind, name: str, number: int, label: str, effect: str
) -> None:
    setup = Setup()

    preview = await setup.editor.delete(kind, name, confirm=False)

    assert preview.message == (
        f"Deleting {label} {name!r} (id {number}) cannot be undone. {effect} "
        "Nothing was deleted: pass confirm=true to delete it."
    )
    assert (preview.deleted, preview.kind, preview.id, preview.name) == (False, kind, number, name)
    assert (preview.document_count, preview.also_deleted) == (1, ())
    assert setup.fake.writes == []


@pytest.mark.parametrize(("kind", "name", "number", "label", "effect"), EFFECTS)
async def test_a_confirmed_delete_deletes_and_reports_it(
    kind: WritableKind, name: str, number: int, label: str, effect: str
) -> None:
    setup = Setup()

    result = await setup.editor.delete(kind, name, confirm=True)

    assert result.message == f"{label} {name!r} (id {number}) was deleted. {effect}"
    assert (result.deleted, result.kind, result.id, result.name) == (True, kind, number, name)
    assert (result.document_count, result.also_deleted) == (1, ())
    assert setup.fake.writes == [("delete", (kind, number))]
    with pytest.raises(NotFound):
        _ = await setup.editor.delete(kind, name, confirm=False)


async def test_a_tag_delete_names_its_child_tags_with_their_document_counts() -> None:
    setup = Setup()
    setup.state.tags.append(FakeTag(id=9, name="deep", parent=2))

    preview = await setup.editor.delete("tag", 1, confirm=False)
    result = await setup.editor.delete("tag", 1, confirm=True)

    children = "'deep' (0 documents), 'e2e-invoices' (1 document)"
    effect = (
        f"It is removed from its documents (1 document). Its child tags ({children}) "
        f"are deleted with it. {HIDDEN}"
    )
    assert preview.message == (
        f"Deleting Tag 'e2e-finance' (id 1) cannot be undone. {effect} "
        "Nothing was deleted: pass confirm=true to delete it."
    )
    assert result.message == f"Tag 'e2e-finance' (id 1) was deleted. {effect}"
    assert [(t.name, t.document_count) for t in result.also_deleted] == [
        ("deep", 0),
        ("e2e-invoices", 1),
    ]
    assert preview.also_deleted == result.also_deleted
    assert {t.id for t in setup.state.tags} == {3, 4}


async def test_a_tag_delete_also_takes_child_tags_the_caller_cannot_see() -> None:
    setup = Setup(READER[0], grant={"delete_tag"})
    setup.state.tags += [
        FakeTag(id=10, name="mine", owner=3),
        FakeTag(id=11, name="theirs", owner=2, parent=10),
    ]

    preview = await setup.editor.delete("tag", "mine", confirm=False)
    result = await setup.editor.delete("tag", "mine", confirm=True)

    assert preview.also_deleted == ()
    assert HIDDEN in preview.message
    assert result.deleted is True
    assert 11 not in {t.id for t in setup.state.tags}


async def test_a_missing_item_is_not_found_with_or_without_confirm() -> None:
    setup = Setup()

    for confirm in (False, True):
        with pytest.raises(NotFound):
            _ = await setup.editor.delete("tag", "nope", confirm=confirm)

    assert setup.fake.writes == []


async def test_the_count_comes_from_the_current_state_not_a_stale_cache() -> None:
    setup = Setup()
    _ = await setup.resolver.items("correspondent")
    setup.state.documents.append(FakeDocument(id=7, title="more", correspondent=1))

    preview = await setup.editor.delete("correspondent", "e2e-acme", confirm=False)

    assert preview.document_count == 2


async def test_a_user_who_may_not_delete_is_denied_on_confirm_only() -> None:
    setup = Setup(READER[0])

    preview = await setup.editor.delete("tag", 1, confirm=False)
    with pytest.raises(PermissionDenied):
        _ = await setup.editor.delete("tag", 1, confirm=True)

    assert preview.deleted is False


async def test_the_cache_is_dropped_after_a_delete() -> None:
    setup = Setup()
    _ = await setup.resolver.items("correspondent")

    _ = await setup.editor.delete("correspondent", "e2e-acme", confirm=True)

    with pytest.raises(NotFound):
        _ = await setup.resolver.resolve("correspondent", "e2e-acme")


# -- the exact words callers see (messages and hints are the interface for an LLM)

UNIQUE_HINT = "Choose another label, or rename the other option (by its id) first."
OPTIONS_HINT = "Options belong to custom fields of data type select."
DATA_TYPES = "One of string, longtext, url, date, boolean, integer, float, monetary, "
MATCHING_KINDS = "tag and correspondent and document type and storage path"


async def refusal(call: Awaitable[object]) -> tuple[str, str | None]:
    """The message and hint of the domain error the call ends with."""
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await call
    return raised.value.message, raised.value.hint


CREATE_REFUSALS: list[tuple[WritableKind, dict[str, object], tuple[str, str | None]]] = [
    (
        "tag",
        {"name": " "},
        ("The name must have 1 to 128 characters.", "Give a short, non-blank name."),
    ),
    (
        "tag",
        {"name": "x" * 129},
        ("The name must have 1 to 128 characters.", "Give a short, non-blank name."),
    ),
    (
        "correspondent",
        {"colour": "#112233"},
        ("colour is not a setting of a correspondent.", "colour belongs to tag; leave it out."),
    ),
    (
        "tag",
        {"path": "x"},
        ("path is not a setting of a tag.", "path belongs to storage path; leave it out."),
    ),
    (
        "custom_field",
        {"matching_algorithm": "any"},
        (
            "matching_algorithm is not a setting of a custom field.",
            f"matching_algorithm belongs to {MATCHING_KINDS}; leave it out.",
        ),
    ),
    (
        "tag",
        {"match": "x"},
        (
            "A match needs a matching_algorithm to say how it is used.",
            "Use any, all, literal, regex or fuzzy.",
        ),
    ),
    ("storage_path", {}, ("A storage path needs a path template.", PATH_HINT)),
    (
        "custom_field",
        {},
        (
            "A custom field needs a data_type.",
            DATA_TYPES + "documentlink, select.",
        ),
    ),
    (
        "custom_field",
        {"data_type": "integer", "select_options": ["a"]},
        ("select_options are only for select fields.", "Leave them out or use data_type select."),
    ),
    (
        "custom_field",
        {"data_type": "select"},
        (
            "A select field needs at least one option.",
            "Pass select_options, e.g. ['yes', 'no'].",
        ),
    ),
    (
        "custom_field",
        {"data_type": "select", "select_options": ["a", " "]},
        ("An option label cannot be blank.", "Give every option a label."),
    ),
    (
        "custom_field",
        {"data_type": "select", "select_options": ["a", "A"]},
        ("The option 'A' is given twice for 'the new field'.", UNIQUE_HINT),
    ),
    (
        "tag",
        {"colour": "red"},
        (
            "A colour is '#rrggbb' (six hexadecimal digits), not 'red'.",
            "For example '#a6cee3'.",
        ),
    ),
]


@pytest.mark.parametrize(("kind", "fields", "expected"), CREATE_REFUSALS)
async def test_a_refused_create_says_exactly_what_is_wrong_and_what_to_do(
    kind: WritableKind, fields: dict[str, object], expected: tuple[str, str | None]
) -> None:
    setup = Setup()
    name = fields.pop("name", "x")

    assert await refusal(setup.create(kind, str(name), **fields)) == expected
    assert setup.fake.writes == []


UPDATE_REFUSALS: list[tuple[WritableKind, str, dict[str, object], tuple[str, str | None]]] = [
    (
        "tag",
        "e2e-inbox",
        {"options_add": ["x"]},
        ("A tag has no select options: only select custom fields do.", OPTIONS_HINT),
    ),
    (
        "correspondent",
        "e2e-acme",
        {"clear_parent": True},
        ("A correspondent has no parent: only tags do.", "Leave clear_parent out."),
    ),
    (
        "tag",
        "e2e-inbox",
        {"parent": "e2e-finance", "clear_parent": True},
        (
            "Give parent or clear_parent, not both.",
            "clear_parent=true makes the tag a top-level tag.",
        ),
    ),
    (
        "tag",
        "e2e-inbox",
        {},
        ("Nothing to change: name at least one setting.", "For example name, colour or match."),
    ),
    (
        "tag",
        "e2e-inbox",
        {"name": "x" * 129},
        ("The name must have 1 to 128 characters.", "Give a short, non-blank name."),
    ),
    (
        "custom_field",
        "e2e-string",
        {"options_add": ["x"]},
        ("'e2e-string' has no select options: only select custom fields do.", OPTIONS_HINT),
    ),
    (
        "custom_field",
        "e2e-select",
        {"options_rename": {"alpha": "x"}, "options_remove": ["alpha"], "confirm": True},
        ("An option cannot be both renamed and removed.", "Rename it or remove it, not both."),
    ),
    (
        "custom_field",
        "e2e-select",
        {"options_remove": ["alpha", "beta"], "confirm": True},
        (
            "A select field needs at least one option.",
            "Keep at least one option or add a new one.",
        ),
    ),
    (
        "custom_field",
        "e2e-select",
        {"options_remove": ["gamma"]},
        ("'e2e-select' has no option 'gamma'.", "Options: 'alpha', 'beta'."),
    ),
    (
        "custom_field",
        "e2e-select",
        {"options_rename": {"gamma": "x"}},
        ("'e2e-select' has no option 'gamma'.", "Options: 'alpha', 'beta'."),
    ),
    (
        "custom_field",
        "e2e-select",
        {"options_add": ["ALPHA"]},
        ("'e2e-select' already has an option 'ALPHA' (ignoring case).", UNIQUE_HINT),
    ),
    (
        "custom_field",
        "e2e-select",
        {"options_rename": {"alpha": "beta"}},
        ("'e2e-select' already has an option 'beta' (ignoring case).", UNIQUE_HINT),
    ),
    (
        "custom_field",
        "e2e-select",
        {"options_add": ["x", "X"]},
        ("The option 'X' is given twice for 'e2e-select'.", UNIQUE_HINT),
    ),
    (
        "custom_field",
        "e2e-select",
        {"options_add": [" "]},
        ("An option label cannot be blank.", "Give every option a label."),
    ),
    (
        "custom_field",
        "e2e-select",
        {"options_rename": {"alpha": " "}},
        ("An option label cannot be blank.", "Give every option a label."),
    ),
]


@pytest.mark.parametrize(("kind", "item", "fields", "expected"), UPDATE_REFUSALS)
async def test_a_refused_update_says_exactly_what_is_wrong_and_what_to_do(
    kind: WritableKind, item: str, fields: dict[str, object], expected: tuple[str, str | None]
) -> None:
    setup = Setup()

    assert await refusal(setup.update(kind, item, **fields)) == expected
    assert setup.fake.writes == []


async def test_options_that_several_labels_match_ask_for_the_id() -> None:
    setup = Setup()
    setup.state.custom_fields.append(
        FakeCustomField(
            id=99,
            name="Case",
            data_type="select",
            select_options=(
                SelectOption(id="id-upper", label="Alpha"),
                SelectOption(id="id-lower", label="alpha"),
            ),
        )
    )

    assert await refusal(setup.update("custom_field", "Case", options_rename={"ALPHA": "x"})) == (
        "Several options of 'Case' match 'ALPHA'.",
        "Pass the id of the option; list_taxonomy(kind='custom_field') shows the ids.",
    )


async def test_an_unknown_parent_names_the_missing_tag() -> None:
    setup = Setup()

    message, _ = await refusal(setup.create("tag", "x", parent="nope"))

    assert message == "No tag is named 'nope'."


async def test_a_name_of_exactly_128_characters_is_fine_and_is_trimmed() -> None:
    setup = Setup()

    created = await setup.create("tag", f" {'x' * 128} ")

    assert created.name == "x" * 128


async def test_a_rejected_template_gets_the_variables_but_other_rejections_do_not() -> None:
    class Rejecting(Recording):
        def __init__(self, state: FakeState, *, user: str, message: str) -> None:
            super().__init__(state, user=user)
            self.message: str = message

        @override
        async def update_taxonomy_item(
            self, kind: WritableKind, item_id: int, values: TaxonomyValues
        ) -> TaxonomyDetail:
            raise Validation(self.message)

    async def update(kind: WritableKind, item: int, message: str) -> tuple[str, str | None]:
        fake = Rejecting(seeded(), user=ADMIN[0], message=message)
        resolver = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=60.0)
        editor = TaxonomyEditor(fake, fake, resolver, fake)
        return await refusal(editor.update(kind, item, TaxonomyEdit(name="x")))

    path = "Paperless rejected the request: path: Invalid variable detected."
    other = "Paperless rejected the request: name: This field may not be blank."
    assert await update("storage_path", 1, path) == (path, PATH_HINT)
    assert await update("storage_path", 1, other) == (other, None)
    assert await update("correspondent", 1, path) == (path, None)


async def test_the_documents_holding_a_removed_option_are_asked_for_with_the_removed_ids() -> None:
    class Asking(Recording):
        queries: ClassVar[list[tuple[object, int, int]]] = []

        @override
        async def search_documents(
            self, query: DocumentQuery, *, page: int, page_size: int
        ) -> DocumentPage:
            self.queries.append((query.custom_field_query, page, page_size))
            return await super().search_documents(query, page=page, page_size=page_size)

    state = seeded()
    state.custom_fields.append(
        FakeCustomField(
            id=99,
            name="Many",
            data_type="select",
            select_options=tuple(SelectOption(id=f"id-{n}", label=n) for n in "abc"),
        )
    )
    fake = Asking(state, user=ADMIN[0])
    resolver = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=60.0)
    editor = TaxonomyEditor(fake, fake, resolver, fake)

    with pytest.raises(Validation):
        _ = await editor.update("custom_field", "Many", TaxonomyEdit(options_remove=("b", "a")))

    assert Asking.queries == [(["Many", "in", ["id-a", "id-b"]], 1, 1)]


async def test_a_row_without_a_document_count_counts_none() -> None:
    class Uncounted(Recording):
        @override
        async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
            return [
                row.model_copy(update={"document_count": None})
                for row in await super().list_taxonomy(kind)
            ]

    fake = Uncounted(seeded(), user=ADMIN[0])
    resolver = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=60.0)

    preview = await TaxonomyEditor(fake, fake, resolver, fake).delete(
        "correspondent", "e2e-acme", confirm=False
    )

    assert preview.document_count == 0
    assert "(0 documents)" in preview.message
