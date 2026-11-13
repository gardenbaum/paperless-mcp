"""BulkDocumentEditor: selection, name resolution, diff, dry run, confirmation, caches.

The fakes of the "nothing may be written" cases are created without `allow_writes`: the fake's
write guard raises `WriteNotAllowedError` on any write, so a passing test proves nothing was
written (decision-6, decision-15).
"""

from dataclasses import replace
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded

from paperless_mcp.domain.bulk import (
    MAX_DOCUMENTS,
    SAMPLE_SIZE,
    AddTags,
    DeleteDocuments,
    FieldsChange,
    ModifyCustomFields,
    ModifyTags,
    ReferenceChange,
    RemoveTags,
    ReprocessChange,
    ReprocessDocuments,
    SetCorrespondent,
    SetDocumentType,
    SetStoragePath,
    TagsChange,
    TrashDocuments,
)
from paperless_mcp.domain.errors import (
    CONFIRM_HINT,
    Ambiguous,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    UpstreamTimeout,
    Validation,
)
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.bulk_documents import BulkDocumentEditor
from paperless_mcp.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.bulk import BulkOperation, DocumentBulkReport, DocumentChange

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

WEB = "https://paperless.example"
PUBLIC = 1  # tags finance, invoices (child of finance), inbox; correspondent, type, path set
PRIVATE = 2
SECOND, THIRD = 5, 6
FINANCE, INVOICES, E2E_PRIVATE, INBOX = 1, 2, 3, 4


class Recording(FakePaperless):
    """A fake that remembers the bulk edits it was asked for."""

    def __init__(self, state: FakeState, *, user: str, writes: bool) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.calls: list[tuple[tuple[int, ...], DocumentChange]] = []

    @override
    async def bulk_edit_documents(
        self, document_ids: Sequence[int], change: DocumentChange
    ) -> None:
        self.calls.append((tuple(document_ids), change))
        await super().bulk_edit_documents(document_ids, change)


@final
class Setup:
    def __init__(
        self, user: str = ADMIN[0], *, writes: bool = True, only: frozenset[str] | None = None
    ) -> None:
        self.state: FakeState = seeded()
        self.state.documents.extend(
            [
                FakeDocument(id=SECOND, title="Second invoice", tags=[]),
                FakeDocument(id=THIRD, title="Third invoice", tags=[INBOX]),
            ]
        )
        if only is not None:
            self.state.users = [
                replace(u, is_superuser=False, permissions=only) if u.username == user else u
                for u in self.state.users
            ]
        self.fake: Recording = Recording(self.state, user=user, writes=writes)
        self.taxonomy: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.editor: BulkDocumentEditor = BulkDocumentEditor(
            self.fake, self.fake, self.taxonomy, web_url=WEB
        )

    def document(self, number: int) -> FakeDocument:
        [found] = [d for d in self.state.documents if d.id == number]
        return found

    def field(self, name: str) -> int:
        [number] = [f.id for f in self.state.custom_fields if f.name == name]
        return number

    async def run(
        self,
        ids: Sequence[int],
        operation: BulkOperation,
        *,
        dry_run: bool = False,
        confirm: bool = True,
    ) -> DocumentBulkReport:
        return await self.editor.edit(ids, operation, dry_run=dry_run, confirm=confirm)


# --- dry run, confirmation, selection


async def test_a_dry_run_reports_what_would_change_and_writes_nothing() -> None:
    setup = Setup(writes=False)

    report = await setup.run(
        [PUBLIC, SECOND, THIRD], AddTags(op="add_tags", tags=["e2e-inbox"]), dry_run=True
    )

    assert (report.dry_run, report.applied, report.requested) == (True, False, 3)
    assert (report.affected_count, report.unchanged_count) == (1, 2)
    assert report.operation == "add_tags"
    assert report.summary == "add the tags 'e2e-inbox' to the selected documents"
    [sample] = report.sample
    assert (sample.id, sample.title) == (SECOND, "Second invoice")
    assert sample.change == "tags +e2e-inbox"
    assert sample.web_url == f"{WEB}/documents/{SECOND}/details"
    assert report.message == (
        "Dry run, nothing was changed. Confirming would add the tags 'e2e-inbox' to 1 document "
        "('Second invoice'); 2 documents already are as asked. To go ahead, call again without "
        "dry_run and with confirm=true."
    )
    assert setup.fake.calls == []


async def test_without_confirm_nothing_is_written_and_the_error_says_what_would_happen() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run(
            [PUBLIC, SECOND], AddTags(op="add_tags", tags=["e2e-private"]), confirm=False
        )

    assert raised.value.message == (
        "Nothing was changed. Confirming would add the tags 'e2e-private' to 2 documents "
        "('e2e-public-invoice', 'Second invoice')."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert setup.fake.calls == []


async def test_a_dry_run_needs_no_confirmation_and_confirm_without_changes_writes_nothing() -> None:
    setup = Setup(writes=False)

    report = await setup.run([PUBLIC], AddTags(op="add_tags", tags=["e2e-finance"]), confirm=False)

    assert (report.applied, report.affected_count, report.unchanged_count) == (False, 0, 1)
    assert report.sample == ()
    assert report.message == "Nothing to change: the selected document already is as asked."


async def test_a_confirmed_edit_sends_only_the_documents_that_change() -> None:
    setup = Setup()

    report = await setup.run([PUBLIC, SECOND, THIRD], AddTags(op="add_tags", tags=["e2e-inbox"]))

    assert setup.fake.calls == [((SECOND,), TagsChange(add=(INBOX,)))]
    assert (report.dry_run, report.applied) == (False, True)
    assert (report.requested, report.affected_count, report.unchanged_count) == (3, 1, 2)
    assert report.message == (
        "Applied to 1 document: add the tags 'e2e-inbox' to the selected documents."
    )
    assert setup.document(SECOND).tags == [INBOX]


async def test_the_sample_is_capped_and_in_selection_order() -> None:
    setup = Setup()
    numbers = list(range(10, 10 + SAMPLE_SIZE + 5))
    setup.state.documents.extend(FakeDocument(id=n, title=f"Doc {n}") for n in numbers)
    reversed_numbers = list(reversed(numbers))

    report = await setup.run(
        reversed_numbers, AddTags(op="add_tags", tags=["e2e-inbox"]), dry_run=True
    )

    assert report.affected_count == SAMPLE_SIZE + 5
    assert [row.id for row in report.sample] == reversed_numbers[:SAMPLE_SIZE]


@pytest.mark.parametrize("ids", [[], list(range(1, MAX_DOCUMENTS + 2))])
async def test_the_selection_is_one_to_a_hundred_documents(ids: list[int]) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.run(ids, AddTags(op="add_tags", tags=["e2e-inbox"]), dry_run=True)

    assert raised.value.message == f"Select between 1 and {MAX_DOCUMENTS} documents per call."
    assert raised.value.hint == (
        "Search with page_size 100 and edit the result page by page, or narrow the search."
    )


async def test_a_hundred_documents_are_allowed_and_repeated_ids_count_once() -> None:
    setup = Setup(writes=False)
    setup.state.documents.extend(
        FakeDocument(id=n, title=f"Doc {n}") for n in range(10, 10 + MAX_DOCUMENTS)
    )
    ids = list(range(10, 10 + MAX_DOCUMENTS))

    report = await setup.run([*ids, *ids], AddTags(op="add_tags", tags=["e2e-inbox"]), dry_run=True)

    assert (report.requested, report.affected_count) == (MAX_DOCUMENTS, MAX_DOCUMENTS)


async def test_a_missing_or_hidden_document_stops_everything_and_is_named() -> None:
    setup = Setup(READER[0], writes=False)

    with pytest.raises(NotFound) as raised:
        _ = await setup.run(
            [PUBLIC, PRIVATE, 999], AddTags(op="add_tags", tags=["e2e-inbox"]), dry_run=True
        )

    assert raised.value.message == "No documents 2, 999 exist that this user may see."
    assert raised.value.hint == "Take the ids from search_documents rows."


async def test_a_user_who_may_not_search_documents_is_denied() -> None:
    setup = Setup(READER[0], writes=False, only=frozenset({"view_tag"}))

    with pytest.raises(PermissionDenied):
        _ = await setup.run([PUBLIC], DeleteDocuments(op="delete"), dry_run=True)


async def test_a_user_who_may_view_but_not_change_gets_a_preview_but_no_write() -> None:
    setup = Setup(READER[0])

    preview = await setup.run(
        [PUBLIC], RemoveTags(op="remove_tags", tags=["e2e-inbox"]), dry_run=True
    )
    assert preview.affected_count == 1

    with pytest.raises(PermissionDenied):
        _ = await setup.run([PUBLIC], RemoveTags(op="remove_tags", tags=["e2e-inbox"]))
    with pytest.raises(PermissionDenied):
        _ = await setup.run([PUBLIC], DeleteDocuments(op="delete"))
    assert setup.document(PUBLIC).tags == [FINANCE, INVOICES, INBOX]


# --- tags


async def test_tags_are_found_by_name_ignoring_case_or_by_id() -> None:
    setup = Setup()

    _ = await setup.run([SECOND], AddTags(op="add_tags", tags=["E2E-INBOX", E2E_PRIVATE]))

    assert setup.document(SECOND).tags == [INBOX, E2E_PRIVATE]
    assert setup.fake.calls[0][1] == TagsChange(add=(INBOX, E2E_PRIVATE))


async def test_an_unknown_tag_is_not_found_with_a_hint_to_create_it_first() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound) as raised:
        _ = await setup.run([SECOND], AddTags(op="add_tags", tags=["no-such-tag"]), dry_run=True)

    assert "no-such-tag" in raised.value.message
    assert "create_taxonomy_item" in (raised.value.hint or "")


async def test_an_ambiguous_tag_name_is_reported() -> None:
    setup = Setup(writes=False)
    setup.state.tags.append(replace(setup.state.tags[0], id=9, name="E2E-Finance"))

    with pytest.raises(Ambiguous):
        _ = await setup.run([SECOND], AddTags(op="add_tags", tags=["E2E-FINANCE"]), dry_run=True)


async def test_adding_a_child_tag_also_adds_its_parent_and_says_so() -> None:
    setup = Setup()

    report = await setup.run([SECOND], AddTags(op="add_tags", tags=["e2e-invoices"]))

    assert setup.document(SECOND).tags == [INVOICES, FINANCE]
    assert report.sample[0].change == "tags +e2e-invoices, +e2e-finance"


async def test_removing_tags_reports_what_goes() -> None:
    setup = Setup()

    report = await setup.run(
        [PUBLIC, SECOND], RemoveTags(op="remove_tags", tags=["e2e-inbox"]), dry_run=True
    )

    assert (report.affected_count, report.unchanged_count) == (1, 1)
    assert report.sample[0].change == "tags -e2e-inbox"
    assert report.summary == "remove the tags 'e2e-inbox' from the selected documents"


async def test_removing_a_parent_whose_child_stays_is_refused_naming_the_child() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.run([PUBLIC], RemoveTags(op="remove_tags", tags=["e2e-finance"]))

    assert raised.value.message == (
        "Removing 'e2e-finance' also removes its child tags 'e2e-invoices' (on 1 document)."
    )
    assert raised.value.hint == "List them in the tags to remove too, or keep 'e2e-finance'."


async def test_removing_a_parent_with_its_children_is_fine() -> None:
    setup = Setup()

    _ = await setup.run(
        [PUBLIC], RemoveTags(op="remove_tags", tags=["e2e-finance", "e2e-invoices"])
    )

    assert setup.document(PUBLIC).tags == [INBOX]


async def test_removing_a_parent_from_a_document_that_only_has_it_is_fine() -> None:
    setup = Setup()
    setup.document(SECOND).tags = [FINANCE]

    _ = await setup.run([SECOND, PUBLIC], RemoveTags(op="remove_tags", tags=["e2e-invoices"]))

    assert setup.document(SECOND).tags == [FINANCE]
    assert setup.document(PUBLIC).tags == [FINANCE, INBOX]


async def test_adding_a_child_while_removing_its_parent_is_refused() -> None:
    setup = Setup(writes=False)  # whatever the documents hold, the request contradicts itself

    with pytest.raises(Validation) as raised:
        _ = await setup.run(
            [SECOND], ModifyTags(op="modify_tags", add=["e2e-invoices"], remove=["e2e-finance"])
        )

    assert raised.value.message == (
        "Adding 'e2e-invoices' needs its parent 'e2e-finance', which is also removed."
    )
    assert raised.value.hint == "Keep the parent, or do not add the child."


async def test_modifying_tags_adds_and_removes_in_one_step() -> None:
    setup = Setup()

    report = await setup.run(
        [PUBLIC, THIRD],
        ModifyTags(op="modify_tags", add=["e2e-private"], remove=["e2e-inbox"]),
    )

    assert setup.document(PUBLIC).tags == [FINANCE, INVOICES, E2E_PRIVATE]
    assert setup.document(THIRD).tags == [E2E_PRIVATE]
    assert report.summary == (
        "add the tags 'e2e-private' and remove the tags 'e2e-inbox' on the selected documents"
    )
    assert setup.fake.calls[0][1] == TagsChange(add=(E2E_PRIVATE,), remove=(INBOX,))


async def test_a_tag_both_added_and_removed_is_refused_before_anything_is_read() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.run(
            [PUBLIC], ModifyTags(op="modify_tags", add=["E2E-Inbox"], remove=[INBOX])
        )

    assert raised.value.message == "A tag is both added and removed."
    assert raised.value.hint == "Name each tag once, to add or to remove."


async def test_modifying_no_tags_is_refused() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.run([PUBLIC], ModifyTags(op="modify_tags"))

    assert raised.value.message == "Nothing to change: no tag was given."
    assert raised.value.hint == "Pass tags to add or to remove."


# --- correspondent, document type, storage path


@pytest.mark.parametrize(
    ("operation", "field", "name", "target"),
    [
        (
            SetCorrespondent(op="set_correspondent", correspondent="E2E-ACME"),
            "correspondent",
            "e2e-acme",
            1,
        ),
        (
            SetDocumentType(op="set_document_type", document_type=1),
            "document_type",
            "e2e-invoice",
            1,
        ),
        (
            SetStoragePath(op="set_storage_path", storage_path="e2e-archive"),
            "storage_path",
            "e2e-archive",
            1,
        ),
    ],
    ids=["correspondent", "document-type", "storage-path"],
)
async def test_a_reference_is_set_by_name_or_id_on_the_documents_that_lack_it(
    operation: BulkOperation, field: str, name: str, target: int
) -> None:
    setup = Setup()

    report = await setup.run([PUBLIC, SECOND], operation)

    assert (report.affected_count, report.unchanged_count) == (1, 1)
    assert getattr(setup.document(SECOND), field) == target
    expected = ReferenceChange.model_validate({"field": field, "value": target})
    assert setup.fake.calls == [((SECOND,), expected)]
    label = field.replace("_", " ")
    assert report.summary == f"set the {label} of the selected documents to '{name}'"
    assert report.sample[0].change == f"{label} none -> '{name}'"


async def test_a_reference_is_cleared_with_null() -> None:
    setup = Setup()

    report = await setup.run(
        [PUBLIC, SECOND], SetCorrespondent(op="set_correspondent", correspondent=None)
    )

    assert setup.document(PUBLIC).correspondent is None
    assert (report.affected_count, report.unchanged_count) == (1, 1)
    assert report.summary == "remove the correspondent from the selected documents"
    assert report.sample[0].change == "correspondent 'e2e-acme' -> none"
    assert setup.fake.calls[0] == (
        (PUBLIC,),
        ReferenceChange(field="correspondent", value=None),
    )


async def test_an_unknown_reference_is_not_found_and_nothing_is_written() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound):
        _ = await setup.run(
            [PUBLIC], SetCorrespondent(op="set_correspondent", correspondent="nobody")
        )


# --- custom fields


async def test_custom_fields_are_set_by_name_and_the_others_stay() -> None:
    setup = Setup()
    before = dict(setup.document(PUBLIC).custom_fields)
    string = setup.field("e2e-string")
    integer = setup.field("e2e-integer")

    report = await setup.run(
        [PUBLIC, SECOND],
        ModifyCustomFields(
            op="modify_custom_fields", values={"E2E-STRING": "changed", "e2e-integer": 5}
        ),
    )

    assert report.affected_count == 2
    assert setup.document(PUBLIC).custom_fields == {**before, string: "changed", integer: 5}
    assert setup.document(SECOND).custom_fields == {string: "changed", integer: 5}
    assert report.summary == (
        "set the custom fields 'e2e-string', 'e2e-integer' on the selected documents"
    )
    assert report.sample[0].change == "custom fields e2e-string, e2e-integer"
    assert setup.fake.calls[0][1] == FieldsChange(values={string: "changed", integer: 5})


async def test_a_value_that_already_holds_is_left_alone_and_false_is_not_true() -> None:
    setup = Setup(writes=False)
    same = ModifyCustomFields(
        op="modify_custom_fields", values={"e2e-string": "hello", "e2e-boolean": True}
    )
    flipped = ModifyCustomFields(op="modify_custom_fields", values={"e2e-boolean": False})

    held = await setup.run([PUBLIC], same, dry_run=True)
    changed = await setup.run([PUBLIC], flipped, dry_run=True)

    assert (held.affected_count, held.unchanged_count) == (0, 1)
    assert (changed.affected_count, changed.unchanged_count) == (1, 0)


async def test_values_are_put_into_paperless_form_by_data_type() -> None:
    setup = Setup()

    _ = await setup.run(
        [SECOND],
        ModifyCustomFields(
            op="modify_custom_fields",
            values={"e2e-select": "beta", "e2e-monetary": "EUR 3.50", "e2e-date": "2026-02-03"},
        ),
    )

    stored = setup.document(SECOND).custom_fields
    assert stored[setup.field("e2e-select")] == "opt-beta"
    assert stored[setup.field("e2e-monetary")] == "EUR3.50"
    assert stored[setup.field("e2e-date")] == "2026-02-03"


async def test_a_bad_value_names_the_field_and_changes_nothing() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="e2e-integer"):
        _ = await setup.run(
            [PUBLIC],
            ModifyCustomFields(op="modify_custom_fields", values={"e2e-integer": "abc"}),
            dry_run=True,
        )


async def test_fields_are_removed_and_removing_an_absent_one_changes_nothing() -> None:
    setup = Setup()
    string = setup.field("e2e-string")

    report = await setup.run(
        [PUBLIC, SECOND], ModifyCustomFields(op="modify_custom_fields", remove=["e2e-string"])
    )

    assert string not in setup.document(PUBLIC).custom_fields
    assert (report.affected_count, report.unchanged_count) == (1, 1)
    assert report.summary == "remove the custom fields 'e2e-string' from the selected documents"
    assert report.sample[0].change == "custom fields -e2e-string"
    assert setup.fake.calls[0] == ((PUBLIC,), FieldsChange(remove=(string,)))


async def test_setting_and_removing_in_one_step_is_summarised_once() -> None:
    setup = Setup()

    report = await setup.run(
        [SECOND],
        ModifyCustomFields(
            op="modify_custom_fields", values={"e2e-string": "x"}, remove=["e2e-integer"]
        ),
        dry_run=True,
    )

    assert report.summary == (
        "set the custom fields 'e2e-string' and remove 'e2e-integer' on the selected documents"
    )


@pytest.mark.parametrize(
    ("values", "remove", "message"),
    [
        ({}, [], "Nothing to change: no custom field was given."),
        ({"e2e-string": "a", "E2E-String": "b"}, [], "Custom field 'e2e-string' is given twice."),
        ({"e2e-string": "a"}, ["e2e-string"], "A custom field is both set and removed."),
    ],
)
async def test_contradictory_or_empty_field_edits_are_refused(
    values: dict[str, str], remove: list[str], message: str
) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.run(
            [PUBLIC],
            ModifyCustomFields.model_validate(
                {"op": "modify_custom_fields", "values": values, "remove": remove}
            ),
            dry_run=True,
        )

    assert raised.value.message == message


async def test_a_link_to_a_document_the_user_cannot_see_is_not_found() -> None:
    setup = Setup(READER[0], writes=False)

    with pytest.raises(NotFound, match="No document 2 exists that this user may see"):
        _ = await setup.run(
            [PUBLIC],
            ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [PRIVATE]}),
            dry_run=True,
        )


async def test_a_link_to_a_visible_document_is_accepted() -> None:
    setup = Setup()

    _ = await setup.run(
        [SECOND],
        ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [PUBLIC]}),
    )

    assert setup.document(SECOND).custom_fields[setup.field("e2e-documentlink")] == [PUBLIC]


# --- delete, reprocess


async def test_delete_moves_every_selected_document_to_the_trash_after_confirmation() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run([PUBLIC, SECOND], DeleteDocuments(op="delete"), confirm=False)
    assert raised.value.message == (
        "Nothing was changed. Confirming would move 2 documents ('e2e-public-invoice', "
        "'Second invoice') to the Paperless trash (they can be restored from there)."
    )

    writing = Setup()
    report = await writing.run([PUBLIC, SECOND], DeleteDocuments(op="delete"))

    assert [d.id for d in writing.state.trash] == [PUBLIC, SECOND]
    assert writing.fake.calls == [((PUBLIC, SECOND), TrashDocuments())]
    assert (report.applied, report.affected_count, report.unchanged_count) == (True, 2, 0)
    assert report.sample[0].change == "move to the trash"


async def test_a_delete_dry_run_previews_the_trash_without_writing() -> None:
    setup = Setup(writes=False)

    report = await setup.run([PUBLIC], DeleteDocuments(op="delete"), dry_run=True)

    assert report.message == (
        "Dry run, nothing was changed. Confirming would move 1 document ('e2e-public-invoice') "
        "to the Paperless trash (they can be restored from there). To go ahead, call again "
        "without dry_run and with confirm=true."
    )


async def test_reprocess_is_queued_for_every_selected_document() -> None:
    setup = Setup()

    report = await setup.run([PUBLIC, SECOND], ReprocessDocuments(op="reprocess"))

    assert setup.state.reprocessed == [PUBLIC, SECOND]
    assert report.message == (
        "Applied to 2 documents: reprocess the selected documents (OCR and archiving run again "
        "in the background)."
    )
    assert report.sample[0].change == "reprocess"


async def test_reprocess_needs_confirmation_too() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired, match="reprocess 1 document"):
        _ = await setup.run([PUBLIC], ReprocessDocuments(op="reprocess"), confirm=False)


# --- caches


async def test_the_taxonomy_cache_forgets_the_counts_a_bulk_edit_changed() -> None:
    setup = Setup()
    warm = {i.name: i.document_count for i in await setup.taxonomy.items("tag")}
    corr = {i.name: i.document_count for i in await setup.taxonomy.items("correspondent")}
    assert (warm["e2e-private"], corr["e2e-acme"]) == (1, 1)

    _ = await setup.run([SECOND], AddTags(op="add_tags", tags=["e2e-private"]))
    _ = await setup.run([PUBLIC], SetCorrespondent(op="set_correspondent", correspondent=None))

    tags = {i.name: i.document_count for i in await setup.taxonomy.items("tag")}
    assert tags["e2e-private"] == 2
    kinds = {i.name: i.document_count for i in await setup.taxonomy.items("correspondent")}
    assert kinds["e2e-acme"] == 0


async def test_a_bulk_delete_forgets_the_counts_of_every_kind() -> None:
    setup = Setup()
    warm = {i.name: i.document_count for i in await setup.taxonomy.items("document_type")}
    assert warm["e2e-invoice"] == 1

    _ = await setup.run([PUBLIC], DeleteDocuments(op="delete"))

    after = {i.name: i.document_count for i in await setup.taxonomy.items("document_type")}
    assert after["e2e-invoice"] == 0


class Flaky(Recording):
    """A fake whose bulk edit takes effect and then times out, as a lost answer would."""

    @override
    async def bulk_edit_documents(
        self, document_ids: Sequence[int], change: DocumentChange
    ) -> None:
        await super().bulk_edit_documents(document_ids, change)
        message = "Paperless did not respond in time."
        raise UpstreamTimeout(message)


async def test_a_failed_write_forgets_the_counts_too_because_it_may_have_happened() -> None:
    setup = Setup()
    setup.fake = Flaky(setup.state, user=ADMIN[0], writes=True)
    setup.editor = BulkDocumentEditor(setup.fake, setup.fake, setup.taxonomy, web_url=WEB)
    warm = {i.name: i.document_count for i in await setup.taxonomy.items("tag")}
    assert warm["e2e-private"] == 1

    with pytest.raises(UpstreamTimeout):
        _ = await setup.run([SECOND], AddTags(op="add_tags", tags=["e2e-private"]))

    after = {i.name: i.document_count for i in await setup.taxonomy.items("tag")}
    assert after["e2e-private"] == 2


async def test_a_link_naming_the_document_itself_is_left_alone_like_paperless_does() -> None:
    setup = Setup()
    link = ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [SECOND]})

    report = await setup.run([SECOND, THIRD], link)

    assert (report.affected_count, report.unchanged_count) == (1, 1)
    assert setup.fake.calls[0][0] == (THIRD,)
    assert setup.document(THIRD).custom_fields[setup.field("e2e-documentlink")] == [SECOND]
    # The link is mirrored on the other side, which is not the document itself.
    assert setup.document(SECOND).custom_fields[setup.field("e2e-documentlink")] == [THIRD]


async def test_the_old_name_of_a_cleared_reference_is_its_id_without_view_permission() -> None:
    setup = Setup(only=frozenset({"view_document", "change_document"}))

    report = await setup.run(
        [PUBLIC], SetCorrespondent(op="set_correspondent", correspondent=None), dry_run=True
    )

    assert report.sample[0].change == "correspondent #1 -> none"


# --- messages and edges found by the mutation run


async def test_a_single_missing_document_is_named_in_the_singular() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound) as raised:
        _ = await setup.run([PUBLIC, 999], DeleteDocuments(op="delete"), dry_run=True)

    assert raised.value.message == "No document 999 exists that this user may see."


async def test_a_confirmation_names_three_titles_and_counts_the_rest() -> None:
    setup = Setup(writes=False)
    setup.state.documents.extend(FakeDocument(id=n, title=f"Doc {n}") for n in range(10, 14))

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run(
            [SECOND, 10, 11, 12, 13], AddTags(op="add_tags", tags=["e2e-inbox"]), confirm=False
        )

    assert raised.value.message == (
        "Nothing was changed. Confirming would add the tags 'e2e-inbox' to 5 documents "
        "('Second invoice', 'Doc 10', 'Doc 11' and 2 more)."
    )


async def test_when_every_document_is_as_asked_the_message_counts_them() -> None:
    setup = Setup(writes=False)

    both = await setup.run(
        [PUBLIC, THIRD], AddTags(op="add_tags", tags=["e2e-inbox"]), confirm=False
    )

    assert both.message == "Nothing to change: all 2 selected documents already are as asked."


async def test_one_document_that_is_as_asked_is_counted_in_the_singular() -> None:
    setup = Setup(writes=False)

    report = await setup.run(
        [PUBLIC, SECOND], AddTags(op="add_tags", tags=["e2e-inbox"]), dry_run=True
    )

    assert report.message == (
        "Dry run, nothing was changed. Confirming would add the tags 'e2e-inbox' to 1 document "
        "('Second invoice'); 1 document already is as asked. To go ahead, call again without "
        "dry_run and with confirm=true."
    )


async def test_reprocessing_sends_the_reprocess_change() -> None:
    setup = Setup()

    _ = await setup.run([PUBLIC], ReprocessDocuments(op="reprocess"))

    assert setup.fake.calls == [((PUBLIC,), ReprocessChange())]


async def test_refusals_of_custom_field_edits_carry_their_hints() -> None:
    setup = Setup(writes=False)
    string = ModifyCustomFields(
        op="modify_custom_fields", values={"e2e-string": "a"}, remove=["e2e-string"]
    )
    twice = ModifyCustomFields(
        op="modify_custom_fields", values={"e2e-string": "a", "E2E-STRING": "b"}
    )
    empty = ModifyCustomFields(op="modify_custom_fields")

    with pytest.raises(Validation) as both:
        _ = await setup.run([PUBLIC], string, dry_run=True)
    with pytest.raises(Validation) as repeated:
        _ = await setup.run([PUBLIC], twice, dry_run=True)
    with pytest.raises(Validation) as nothing:
        _ = await setup.run([PUBLIC], empty, dry_run=True)

    assert both.value.hint == "Set it or remove it, not both."
    assert repeated.value.hint == "Name each field once, by name or by id."
    assert nothing.value.hint == "Pass values to set or custom fields to remove."


async def test_a_hidden_linked_document_comes_with_a_hint() -> None:
    setup = Setup(READER[0], writes=False)

    with pytest.raises(NotFound) as raised:
        _ = await setup.run(
            [PUBLIC],
            ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [PRIVATE]}),
            dry_run=True,
        )

    assert raised.value.hint == "Take the ids from search_documents rows."


async def test_a_link_to_a_document_the_user_may_only_view_is_refused_naming_it() -> None:
    setup = Setup(READER[0], writes=False)
    view_only(setup, THIRD)

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.run(
            [SECOND],
            ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [THIRD]}),
            dry_run=True,
        )

    assert raised.value.message == (
        "A link also changes the linked document, and you may not change 1 document "
        "('Third invoice')."
    )
    assert raised.value.hint == "Link only to documents you may change."


async def test_the_taxonomy_cache_forgets_custom_field_counts_after_a_field_edit() -> None:
    setup = Setup()
    warm = {i.name: i.document_count for i in await setup.taxonomy.items("custom_field")}
    assert warm["e2e-integer"] == 1

    _ = await setup.run(
        [PUBLIC], ModifyCustomFields(op="modify_custom_fields", remove=["e2e-integer"])
    )

    after = {i.name: i.document_count for i in await setup.taxonomy.items("custom_field")}
    assert after["e2e-integer"] == 0


async def test_a_refused_tag_removal_counts_the_documents_and_joins_their_children() -> None:
    setup = Setup(writes=False)
    setup.state.tags.append(replace(setup.state.tags[1], id=9, name="e2e-receipts"))
    setup.document(SECOND).tags = [FINANCE, INVOICES]
    setup.document(THIRD).tags = [FINANCE, 9]

    with pytest.raises(Validation) as raised:
        _ = await setup.run(
            [PUBLIC, SECOND, THIRD], RemoveTags(op="remove_tags", tags=["e2e-finance"])
        )

    assert raised.value.message == (
        "Removing 'e2e-finance' also removes its child tags 'e2e-invoices', 'e2e-receipts' "
        "(on 3 documents)."
    )


# --- review round 1


async def test_a_child_tag_under_a_parent_the_user_cannot_see_is_labelled_not_a_crash() -> None:
    setup = Setup(READER[0], writes=False)
    setup.state.tags.append(replace(setup.state.tags[0], id=9, name="visible child", parent=3))

    report = await setup.run([SECOND], AddTags(op="add_tags", tags=["visible child"]), dry_run=True)

    assert report.sample[0].change == "tags +visible child, +#3 (a tag you cannot see)"


async def test_removing_tags_says_that_child_tags_the_user_cannot_see_go_too() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run(
            [PUBLIC], RemoveTags(op="remove_tags", tags=["e2e-inbox"]), confirm=False
        )

    assert raised.value.message == (
        "Nothing was changed. Confirming would remove the tags 'e2e-inbox' from 1 document "
        "('e2e-public-invoice'); child tags you cannot see (other users') come off the "
        "document with them."
    )


def link_state(setup: Setup, links: dict[int, list[int]]) -> None:
    field = setup.field("e2e-documentlink")
    for number, targets in links.items():
        setup.document(number).custom_fields[field] = [*targets]


async def test_a_document_link_names_the_documents_that_are_changed_by_mirroring() -> None:
    setup = Setup(writes=False)
    link = ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [THIRD]})

    report = await setup.run([SECOND], link, dry_run=True)

    assert [(row.id, row.title) for row in report.also_changed] == [(THIRD, "Third invoice")]
    assert report.also_changed[0].change == "link mirrored"
    assert report.affected_count == 1
    assert report.message == (
        "Dry run, nothing was changed. Confirming would set the custom fields 'e2e-documentlink' "
        "on 1 document ('Second invoice') and, because document links are mirrored, also "
        "changes 1 document that is not selected ('Third invoice'). To go ahead, call again "
        "without dry_run and with confirm=true."
    )
    with pytest.raises(ConfirmationRequired, match="also changes 1 document that is not selected"):
        _ = await setup.run([SECOND], link, confirm=False)


async def test_dropped_links_and_a_removed_link_field_change_the_old_targets() -> None:
    setup = Setup(writes=False)
    link_state(setup, {SECOND: [THIRD], THIRD: [SECOND]})

    moved = await setup.run(
        [SECOND],
        ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [PUBLIC]}),
        dry_run=True,
    )
    dropped = await setup.run(
        [SECOND],
        ModifyCustomFields(op="modify_custom_fields", remove=["e2e-documentlink"]),
        dry_run=True,
    )

    # Setting a new list does not unlink the old target (3.4.2); removing the field does.
    assert [row.id for row in moved.also_changed] == [PUBLIC]
    assert [row.id for row in dropped.also_changed] == [THIRD]


async def test_a_selected_document_is_not_listed_as_also_changed_and_hidden_ones_are_ids() -> None:
    setup = Setup(READER[0], writes=False)
    link_state(setup, {SECOND: [PRIVATE]})
    both = await setup.run(
        [SECOND, THIRD],
        ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [SECOND]}),
        dry_run=True,
    )
    hidden = await setup.run(
        [SECOND],
        ModifyCustomFields(op="modify_custom_fields", remove=["e2e-documentlink"]),
        dry_run=True,
    )

    assert [row.id for row in both.also_changed] == []
    assert [(row.id, row.title) for row in hidden.also_changed] == [(PRIVATE, "#2")]


async def test_applied_links_report_the_mirrored_documents() -> None:
    setup = Setup()

    report = await setup.run(
        [SECOND],
        ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [THIRD]}),
    )

    assert report.message == (
        "Applied to 1 document: set the custom fields 'e2e-documentlink' on the selected "
        "documents. Mirrored on 1 other document ('Third invoice')."
    )
    assert [row.id for row in report.also_changed] == [THIRD]


def view_only(setup: Setup, number: int) -> None:
    """Make the document the admin's own, shared with the reader for viewing only."""
    setup.document(number).owner = 2
    setup.document(number).viewers = {3}


async def test_a_blocking_document_is_named_and_the_execution_is_refused_before_sending() -> None:
    setup = Setup(READER[0], writes=False)
    view_only(setup, SECOND)
    operation = AddTags(op="add_tags", tags=["e2e-inbox"])

    preview = await setup.run([PUBLIC, SECOND], operation, dry_run=True)
    with pytest.raises(PermissionDenied) as refused:
        _ = await setup.run([PUBLIC, SECOND], operation, confirm=True)

    assert [(row.id, row.change) for row in preview.blocked] == [(SECOND, "you may not change it")]
    assert preview.message == (
        "Dry run, nothing was changed. Paperless would refuse this batch, so none would "
        "change: you may not change 1 document ('Second invoice'). Leave it out, or ask its "
        "owner for change permission."
    )
    assert refused.value.message == (
        "You may not change 1 document ('Second invoice'), so Paperless would refuse the whole "
        "batch; nothing was sent."
    )
    assert (
        refused.value.hint == "Leave those documents out, or ask their owner for change permission."
    )
    assert setup.fake.calls == []


async def test_a_confirmation_without_confirm_also_comes_after_the_permission_check() -> None:
    setup = Setup(READER[0], writes=False)
    view_only(setup, SECOND)

    with pytest.raises(PermissionDenied):
        _ = await setup.run([SECOND], AddTags(op="add_tags", tags=["e2e-inbox"]), confirm=False)


async def test_a_document_that_is_already_as_asked_does_not_block_the_batch() -> None:
    setup = Setup(READER[0], only=frozenset({"view_document", "change_document", "view_tag"}))
    view_only(setup, THIRD)  # has the inbox tag already: not part of the batch

    report = await setup.run([SECOND, THIRD], AddTags(op="add_tags", tags=["e2e-inbox"]))

    assert (report.applied, report.blocked) == (True, ())


def extra_documents(setup: Setup, *numbers: int) -> None:
    setup.state.documents.extend(FakeDocument(id=n, title=f"Doc {n}") for n in numbers)


async def test_many_blocking_documents_are_listed_by_three_and_counted() -> None:
    setup = Setup(READER[0], writes=False)
    extra_documents(setup, 10, 11, 12, 13)
    for number in (SECOND, 10, 11, 12, 13):
        view_only(setup, number)

    report = await setup.run(
        [SECOND, 10, 11, 12, 13], AddTags(op="add_tags", tags=["e2e-inbox"]), dry_run=True
    )

    assert report.message == (
        "Dry run, nothing was changed. Paperless would refuse this batch, so none would "
        "change: you may not change 5 documents ('Second invoice', 'Doc 10', 'Doc 11' and 2 "
        "more). Leave them out, or ask their owner for change permission."
    )
    assert [row.web_url for row in report.blocked][:1] == [f"{WEB}/documents/{SECOND}/details"]
    assert len(report.blocked) == 5


async def test_removing_tags_from_several_documents_says_documents() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run(
            [PUBLIC, THIRD], RemoveTags(op="remove_tags", tags=["e2e-inbox"]), confirm=False
        )

    assert raised.value.message.endswith(
        "; child tags you cannot see (other users') come off the documents with them."
    )


async def test_many_mirrored_documents_are_counted_and_listed_by_three() -> None:
    setup = Setup()
    extra_documents(setup, 10, 11, 12, 13)
    link = ModifyCustomFields(
        op="modify_custom_fields", values={"e2e-documentlink": [10, 11, 12, 13]}
    )

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run([SECOND], link, confirm=False)
    report = await setup.run([SECOND], link)

    assert raised.value.message == (
        "Nothing was changed. Confirming would set the custom fields 'e2e-documentlink' on 1 "
        "document ('Second invoice') and, because document links are mirrored, also changes 4 "
        "documents that are not selected ('Doc 10', 'Doc 11', 'Doc 12' and 1 more)."
    )
    assert report.message == (
        "Applied to 1 document: set the custom fields 'e2e-documentlink' on the selected "
        "documents. Mirrored on 4 other documents ('Doc 10', 'Doc 11', 'Doc 12' and 1 more)."
    )
    assert report.also_changed[0].web_url == f"{WEB}/documents/10/details"
    assert len(report.also_changed) == 4


async def test_mirroring_collects_the_targets_of_every_document_and_field() -> None:
    setup = Setup(writes=False)
    extra_documents(setup, 10, 11)
    link_state(setup, {SECOND: [10], THIRD: [11]})
    setup.document(THIRD).custom_fields[setup.field("e2e-string")] = "x"

    moved = await setup.run(
        [SECOND, THIRD],
        ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [PUBLIC]}),
        dry_run=True,
    )
    dropped = await setup.run(
        [SECOND, THIRD],
        ModifyCustomFields(op="modify_custom_fields", remove=["e2e-documentlink", "e2e-string"]),
        dry_run=True,
    )

    assert [row.id for row in moved.also_changed] == [PUBLIC]
    assert sorted(row.id for row in dropped.also_changed) == [10, 11]


async def test_two_link_fields_both_count() -> None:
    setup = Setup(writes=False)
    extra_documents(setup, 10, 11)
    second = replace(setup.state.custom_fields[7], id=99, name="second-link")
    setup.state.custom_fields.append(second)

    report = await setup.run(
        [SECOND],
        ModifyCustomFields(
            op="modify_custom_fields",
            values={"e2e-documentlink": [10], "second-link": [11]},
        ),
        dry_run=True,
    )

    assert sorted(row.id for row in report.also_changed) == [10, 11]


async def test_a_target_that_is_already_linked_is_not_reported_and_hits_add_up() -> None:
    setup = Setup(writes=False)
    extra_documents(setup, 10, 11)
    link_state(setup, {SECOND: [10]})

    report = await setup.run(
        [THIRD, SECOND],
        ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [10, 11]}),
        dry_run=True,
    )

    assert sorted(row.id for row in report.also_changed) == [10, 11]


async def test_a_document_that_already_links_a_target_does_not_report_it() -> None:
    setup = Setup(writes=False)
    extra_documents(setup, 10, 11)
    link_state(setup, {SECOND: [10]})

    report = await setup.run(
        [SECOND],
        ModifyCustomFields(op="modify_custom_fields", values={"e2e-documentlink": [10, 11]}),
        dry_run=True,
    )

    assert [row.id for row in report.also_changed] == [11]


async def test_removing_a_link_field_from_a_document_that_lacks_it_reports_the_others() -> None:
    setup = Setup(writes=False)
    extra_documents(setup, 10)
    link_state(setup, {SECOND: [10]})
    setup.document(THIRD).custom_fields[setup.field("e2e-string")] = "x"

    report = await setup.run(
        [THIRD, SECOND],
        ModifyCustomFields(op="modify_custom_fields", remove=["e2e-documentlink", "e2e-string"]),
        dry_run=True,
    )

    assert [row.id for row in report.also_changed] == [10]
