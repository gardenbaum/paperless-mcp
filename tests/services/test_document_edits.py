"""DocumentEditor: names to ids, diff against the current state, merged lists, caches."""

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, FakeUser, seeded
from hypothesis import given
from hypothesis import strategies as st

from paperless_mcp.domain.documents import CustomFieldValue, DocumentEdit, DocumentPatch
from paperless_mcp.domain.errors import Ambiguous, NotFound, PermissionDenied, Validation
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.document_edits import DocumentEditor, merged_fields, merged_tags
from paperless_mcp.services.documents import DocumentService
from paperless_mcp.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from pydantic import JsonValue

    from paperless_mcp.domain.documents import DocumentDetail, DocumentRecord, DocumentUpdate

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

WEB = "https://paperless.example"
PUBLIC = 1


@final
class Recording(FakePaperless):
    """A fake that remembers the patches it was given."""

    def __init__(self, state: FakeState, *, user: str) -> None:
        super().__init__(state, user=user, allow_writes=True)
        self.patches: list[DocumentPatch] = []

    @override
    async def update_document(self, document_id: int, patch: DocumentPatch) -> DocumentRecord:
        self.patches.append(patch)
        return await super().update_document(document_id, patch)


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, can_change: bool = False) -> None:
        self.state: FakeState = seeded()
        if can_change:
            self.state.users = [
                replace(u, permissions=u.permissions | {"change_document"})
                if u.username == user
                else u
                for u in self.state.users
            ]
        self.fake: Recording = Recording(self.state, user=user)
        self.taxonomy: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        documents = DocumentService(self.fake, self.taxonomy, web_url=WEB)
        self.editor: DocumentEditor = DocumentEditor(
            self.fake, self.fake, self.fake, self.taxonomy, documents=documents
        )

    @property
    def document(self) -> FakeDocument:
        return self.state.documents[0]

    async def update(self, document_id: int = PUBLIC, /, **edit: object) -> DocumentUpdate:
        return await self.editor.update(document_id, DocumentEdit.model_validate(edit))

    def field(self, name: str) -> int:
        [number] = [f.id for f in self.state.custom_fields if f.name == name]
        return number


def names(detail: DocumentDetail) -> dict[str | None, JsonValue]:
    return {value.name: value.value for value in detail.custom_fields}


async def test_setting_one_custom_field_keeps_every_other_one() -> None:
    setup = Setup()
    before = dict(setup.document.custom_fields)

    detail = await setup.update(custom_fields={"e2e-string": "changed"})

    after = setup.document.custom_fields
    assert after[setup.field("e2e-string")] == "changed"
    assert {k: v for k, v in after.items() if k != setup.field("e2e-string")} == {
        k: v for k, v in before.items() if k != setup.field("e2e-string")
    }
    assert len(detail.custom_fields) == len(before) == 10
    assert names(detail)["e2e-string"] == "changed"


async def test_the_patch_sent_holds_the_whole_merged_list_and_nothing_else() -> None:
    setup = Setup()

    _ = await setup.update(custom_fields={"e2e-integer": 43})

    [patch] = setup.fake.patches
    assert patch.model_fields_set == {"custom_fields"}
    assert patch.custom_fields is not None
    assert len(patch.custom_fields) == 10
    assert CustomFieldValue(field=setup.field("e2e-integer"), value=43) in patch.custom_fields


async def test_values_are_checked_and_put_into_paperless_form_by_data_type() -> None:
    setup = Setup()

    detail = await setup.update(
        custom_fields={
            "e2e-select": "beta",
            "e2e-monetary": "USD 7.5",
            "e2e-documentlink": [2, 2],
            "e2e-date": "2027-01-02",
            "e2e-boolean": False,
        }
    )

    stored = setup.document.custom_fields
    assert stored[setup.field("e2e-select")] == "opt-beta"
    assert stored[setup.field("e2e-monetary")] == "USD7.50"
    assert stored[setup.field("e2e-documentlink")] == [2]
    assert names(detail)["e2e-select"] == "beta"
    assert names(detail)["e2e-monetary"] == "USD 7.50"


async def test_a_bad_value_changes_nothing_and_names_the_field() -> None:
    setup = Setup()
    before = dict(setup.document.custom_fields)

    with pytest.raises(Validation, match="e2e-integer"):
        _ = await setup.update(title="never", custom_fields={"e2e-integer": "many"})

    assert setup.document.custom_fields == before
    assert setup.document.title == "e2e-public-invoice"
    assert setup.fake.patches == []


async def test_custom_fields_are_found_by_name_ignoring_case_or_by_id() -> None:
    setup = Setup()

    _ = await setup.update(custom_fields={"E2E-STRING": "one", str(setup.field("e2e-integer")): 5})

    assert setup.document.custom_fields[setup.field("e2e-string")] == "one"
    assert setup.document.custom_fields[setup.field("e2e-integer")] == 5


async def test_a_field_the_document_lacks_is_added() -> None:
    setup = Setup()
    setup.document.custom_fields = {setup.field("e2e-string"): "x"}

    detail = await setup.update(custom_fields={"e2e-integer": 1})

    assert names(detail) == {"e2e-string": "x", "e2e-integer": 1}


async def test_fields_are_removed_by_name_and_removing_an_absent_one_changes_nothing() -> None:
    setup = Setup()

    detail = await setup.update(remove_custom_fields=["e2e-string", setup.field("e2e-url")])
    again = await setup.update(remove_custom_fields=["e2e-string"])

    assert "e2e-string" not in names(detail)
    assert "e2e-url" not in names(detail)
    assert len(detail.custom_fields) == 8
    assert again == detail
    assert len(setup.fake.patches) == 1


async def test_an_unknown_custom_field_is_not_found_with_suggestions() -> None:
    with pytest.raises(NotFound, match="e2e-strin") as raised:
        _ = await Setup().update(custom_fields={"e2e-strin": "x"})

    assert "e2e-string" in raised.value.describe()


async def test_tags_are_added_removed_or_replaced_by_name_or_id() -> None:
    setup = Setup()  # the public document has e2e-finance, e2e-invoices, e2e-inbox

    added = await setup.update(tags_add=["e2e-private"])
    removed = await setup.update(tags_remove=["E2E-INBOX", 2])
    replaced = await setup.update(tags_set=["e2e-inbox"])

    assert {t.name for t in added.tags} == {
        "e2e-finance",
        "e2e-invoices",
        "e2e-inbox",
        "e2e-private",
    }
    assert {t.name for t in removed.tags} == {"e2e-finance", "e2e-private"}
    assert [t.name for t in replaced.tags] == ["e2e-inbox"]


async def test_tags_set_to_an_empty_list_clears_them_but_no_list_leaves_them() -> None:
    setup = Setup()

    untouched = await setup.update(title="x")
    cleared = await setup.update(tags_set=[])

    assert len(untouched.tags) == 3
    assert cleared.tags == ()


async def test_adding_a_tag_the_document_has_sends_nothing() -> None:
    setup = Setup()

    detail = await setup.update(tags_add=["e2e-finance"], tags_remove=["e2e-private"])

    assert setup.fake.patches == []
    assert len(detail.tags) == 3


@pytest.mark.parametrize(
    "edit",
    [
        {"tags_set": ["a"], "tags_add": ["b"]},
        {"tags_set": ["a"], "tags_remove": ["b"]},
        {"tags_add": ["e2e-finance"], "tags_remove": [1]},
        {"custom_fields": {"e2e-string": "x"}, "remove_custom_fields": ["e2e-string"]},
        {"custom_fields": {"e2e-string": "x", "1": "y"}},
        {"correspondent": "e2e-acme", "clear": ["correspondent"]},
        {"archive_serial_number": 5, "clear": ["archive_serial_number"]},
        {"owner": "admin", "clear": ["owner"]},
        {},
        {"title": "  "},
        {"tags_add": ["a", "a"], "create_missing_tags": True, "tags_remove": ["a"]},
    ],
)
async def test_contradictory_or_empty_edits_are_rejected_before_anything_is_read(
    edit: dict[str, object],
) -> None:
    setup = Setup()

    with pytest.raises(Validation) as raised:
        _ = await setup.update(**edit)

    assert raised.value.hint
    assert setup.fake.patches == []


async def test_an_unknown_tag_name_is_not_found_with_a_hint_to_create_it() -> None:
    setup = Setup()

    with pytest.raises(NotFound, match="'e2e-new'") as raised:
        _ = await setup.update(tags_add=["e2e-new"])

    assert "create_missing_tags" in raised.value.describe()
    assert setup.fake.patches == []
    assert len(setup.state.tags) == 4


async def test_an_unknown_tag_id_cannot_be_created() -> None:
    with pytest.raises(NotFound, match="No tag has id 99"):
        _ = await Setup().update(tags_add=[99], create_missing_tags=True)


async def test_missing_tags_are_created_once_when_asked_and_attached() -> None:
    setup = Setup()
    before = {t.name: t.document_count for t in await setup.taxonomy.items("tag")}

    detail = await setup.update(
        tags_add=["e2e-new", "E2E-NEW", "e2e-finance"], create_missing_tags=True
    )

    assert [t.name for t in setup.state.tags].count("e2e-new") == 1
    assert "e2e-new" in {t.name for t in detail.tags}
    assert before.get("e2e-new") is None
    after = {t.name: t.document_count for t in await setup.taxonomy.items("tag")}
    assert after["e2e-new"] == 1


async def test_a_name_that_exists_ignoring_case_is_used_not_created() -> None:
    setup = Setup()

    _ = await setup.update(tags_add=["E2E-PRIVATE"], create_missing_tags=True)

    assert len(setup.state.tags) == 4


async def test_creating_tags_needs_the_permission_and_stops_before_any_change() -> None:
    setup = Setup(READER[0], can_change=True)

    with pytest.raises(PermissionDenied):
        _ = await setup.update(tags_add=["e2e-new"], create_missing_tags=True)

    assert setup.fake.patches == []


async def test_a_removed_unknown_tag_is_not_found_even_when_creating() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().update(tags_remove=["e2e-typo"], create_missing_tags=True)


async def test_references_are_set_by_name_or_id_and_cleared_on_request() -> None:
    setup = Setup()
    setup.state.correspondents.append(setup.state.correspondents[0].__class__(id=2, name="Other"))

    set_ = await setup.update(correspondent="other", document_type=1, storage_path="e2e-archive")
    cleared = await setup.update(clear=["correspondent", "document_type", "storage_path"])

    assert set_.correspondent is not None
    assert (set_.correspondent.id, set_.correspondent.name) == (2, "Other")
    assert (set_.document_type and set_.document_type.name) == "e2e-invoice"
    assert (cleared.correspondent, cleared.document_type, cleared.storage_path) == (
        None,
        None,
        None,
    )


async def test_title_date_serial_number_and_owner_change() -> None:
    setup = Setup()

    detail = await setup.update(
        title="New", created=date(2025, 5, 6), archive_serial_number=77, owner="admin", confirm=True
    )
    cleared = await setup.update(clear=["archive_serial_number", "owner"], confirm=True)

    assert (detail.title, detail.created, detail.archive_serial_number) == (
        "New",
        date(2025, 5, 6),
        77,
    )
    assert detail.owner is not None
    assert (detail.owner.id, detail.owner.name) == (2, "admin")
    assert (cleared.archive_serial_number, cleared.owner) == (None, None)


async def test_the_owner_may_be_given_as_an_id_without_listing_users() -> None:
    setup = Setup()

    detail = await setup.update(owner=2, confirm=True)

    assert detail.owner is not None
    assert detail.owner.id == 2


async def test_an_unknown_name_is_not_found_and_changes_nothing() -> None:
    setup = Setup()

    with pytest.raises(NotFound, match="e2e-acm"):
        _ = await setup.update(title="never", correspondent="e2e-acm")

    assert setup.fake.patches == []


async def test_an_ambiguous_name_is_reported() -> None:
    setup = Setup()
    setup.state.document_types.append(
        setup.state.document_types[0].__class__(id=2, name="E2E-Invoice")
    )
    setup.state.document_types[0].name = "e2e-invoice"

    with pytest.raises(Ambiguous):
        _ = await setup.update(document_type="e2E-INVOICE")


async def test_a_change_that_is_already_true_is_not_sent_and_the_document_comes_back() -> None:
    setup = Setup()
    modified = setup.document.modified

    detail = await setup.update(
        title="e2e-public-invoice",
        correspondent="e2e-acme",
        custom_fields={"e2e-string": "hello", "e2e-boolean": True, "e2e-float": 3.25},
        tags_set=["e2e-finance", "e2e-invoices", "e2e-inbox"],
    )

    assert setup.fake.patches == []
    assert detail.modified == modified
    assert detail.title == "e2e-public-invoice"


async def test_true_is_not_the_same_value_as_one() -> None:
    setup = Setup()
    setup.document.custom_fields[setup.field("e2e-boolean")] = True
    setup.document.custom_fields[setup.field("e2e-integer")] = 1

    _ = await setup.update(custom_fields={"e2e-integer": 1})
    assert setup.fake.patches == []
    _ = await setup.update(custom_fields={"e2e-boolean": False})
    assert len(setup.fake.patches) == 1


async def test_only_what_differs_is_sent() -> None:
    setup = Setup()

    _ = await setup.update(
        title="e2e-public-invoice", correspondent="e2e-acme", archive_serial_number=3
    )

    [patch] = setup.fake.patches
    assert patch.model_fields_set == {"archive_serial_number"}


async def test_a_missing_document_is_not_found_before_names_are_resolved() -> None:
    with pytest.raises(NotFound, match="No document 99"):
        _ = await Setup().update(99, tags_add=["e2e-new"], create_missing_tags=True)


async def test_a_user_who_may_not_change_documents_is_denied_before_any_tag_is_created() -> None:
    setup = Setup(READER[0])

    with pytest.raises(PermissionDenied):
        _ = await setup.update(tags_add=["e2e-new"], create_missing_tags=True)

    assert len(setup.state.tags) == 4


async def test_the_taxonomy_cache_forgets_the_counts_a_write_changed() -> None:
    setup = Setup()
    kinds = ("tag", "correspondent", "document_type", "storage_path", "custom_field")
    counts = {k: {i.id: i.document_count for i in await setup.taxonomy.items(k)} for k in kinds}

    _ = await setup.update(tags_add=["e2e-private"])
    _ = await setup.update(clear=["correspondent"])
    _ = await setup.update(remove_custom_fields=["e2e-string"])

    tags = {i.name: i.document_count for i in await setup.taxonomy.items("tag")}
    assert tags["e2e-private"] == 2
    assert {i.id: i.document_count for i in await setup.taxonomy.items("correspondent")}[1] == 0
    assert {i.name: i.document_count for i in await setup.taxonomy.items("custom_field")}[
        "e2e-string"
    ] == 0
    assert counts["document_type"] == {
        i.id: i.document_count for i in await setup.taxonomy.items("document_type")
    }


async def test_an_owner_change_forgets_every_count_because_visibility_changes() -> None:
    setup = Setup(READER[0], can_change=True)  # the reader sees the public document and owns it
    warm = {i.name: i.document_count for i in await setup.taxonomy.items("document_type")}
    assert warm["e2e-invoice"] == 1

    _ = await setup.update(owner=2, confirm=True)  # to the admin: the reader no longer sees it

    assert setup.document.owner == 2
    after = {i.name: i.document_count for i in await setup.taxonomy.items("document_type")}
    assert after["e2e-invoice"] == 0


current_fields = st.dictionaries(
    st.integers(min_value=1, max_value=12), st.integers(min_value=0, max_value=5), max_size=10
)


@given(
    current=current_fields,
    sets=st.dictionaries(
        st.integers(min_value=1, max_value=12), st.integers(min_value=6, max_value=9)
    ),
    removed=st.sets(st.integers(min_value=1, max_value=12)),
)
def test_merging_custom_fields_never_touches_a_field_it_was_not_told_about(
    current: dict[int, int], sets: dict[int, int], removed: set[int]
) -> None:
    removed -= sets.keys()
    cur = tuple(CustomFieldValue(field=k, value=v) for k, v in current.items())

    merged = merged_fields(cur, dict(sets), removed)

    result = {m.field: m.value for m in merged}
    assert len(merged) == len(result)
    for number, value in current.items():
        if number not in sets and number not in removed:
            assert result[number] == value
    assert all(result[n] == v for n, v in sets.items())
    assert not removed & result.keys()
    assert set(result) == (set(current) | set(sets)) - removed


@given(
    current=st.lists(st.integers(1, 12), unique=True, max_size=8),
    add=st.lists(st.integers(1, 12), max_size=6),
    remove=st.sets(st.integers(1, 12)),
)
def test_merging_tags_adds_and_removes_exactly_what_was_named(
    current: list[int], add: list[int], remove: set[int]
) -> None:
    remove -= set(add)

    merged = merged_tags(tuple(current), add, remove)

    assert len(merged) == len(set(merged))
    assert set(merged) == (set(current) | set(add)) - remove
    assert [t for t in merged if t in current] == [t for t in current if t not in remove]


# --- review round 1 -------------------------------------------------------------------------


async def test_tags_set_to_a_child_keeps_its_parent_and_repeating_it_changes_nothing() -> None:
    setup = Setup()  # e2e-finance, e2e-invoices (child of finance), e2e-inbox

    first = await setup.update(tags_set=["e2e-invoices"])
    second = await setup.update(tags_set=["e2e-invoices"])

    assert {t.name for t in first.tags} == {"e2e-finance", "e2e-invoices"}
    assert second == first
    assert len(setup.fake.patches) == 1
    assert first.implicit_tags == ()


async def test_a_child_added_alone_brings_its_parent_and_the_result_says_so() -> None:
    setup = Setup()
    setup.document.tags = []

    detail = await setup.update(tags_add=["e2e-invoices"])
    again = await setup.update(tags_add=["e2e-invoices"])

    assert {t.name for t in detail.tags} == {"e2e-finance", "e2e-invoices"}
    assert [(t.id, t.name) for t in detail.implicit_tags] == [(1, "e2e-finance")]
    assert again.implicit_tags == ()
    [patch] = setup.fake.patches
    assert patch.tags is not None
    assert sorted(patch.tags) == [1, 2]


async def test_a_created_child_needs_no_parent() -> None:
    setup = Setup()

    detail = await setup.update(tags_set=["e2e-new"], create_missing_tags=True)

    assert [t.name for t in detail.tags] == ["e2e-new"]
    assert detail.implicit_tags == ()


async def test_removing_a_parent_whose_children_stay_is_refused_naming_them() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="'e2e-invoices'") as raised:
        _ = await setup.update(tags_remove=["e2e-finance"])

    assert "also removes" in raised.value.message
    assert "tags_remove" in (raised.value.hint or "")
    assert setup.fake.patches == []


async def test_removing_a_parent_with_all_its_children_is_allowed() -> None:
    setup = Setup()

    detail = await setup.update(tags_remove=["e2e-finance", "e2e-invoices"])

    assert {t.name for t in detail.tags} == {"e2e-inbox"}


async def test_a_parent_cannot_be_removed_while_a_child_is_added_in_the_same_call() -> None:
    setup = Setup()
    setup.document.tags = [1]

    with pytest.raises(Validation, match="'e2e-invoices'"):
        _ = await setup.update(tags_add=["e2e-invoices"], tags_remove=["e2e-finance"])


async def test_tags_set_to_the_parent_alone_drops_the_children() -> None:
    detail = await Setup().update(tags_set=["e2e-finance"])

    assert [t.name for t in detail.tags] == ["e2e-finance"]


async def test_a_title_is_checked_before_a_tag_is_created() -> None:
    setup = Setup()

    for title in ("x" * 129, "   "):
        with pytest.raises(Validation):
            _ = await setup.update(title=title, tags_add=["e2e-new"], create_missing_tags=True)

    assert len(setup.state.tags) == 4


async def test_an_unknown_owner_id_is_not_found_before_a_tag_is_created() -> None:
    setup = Setup()

    with pytest.raises(NotFound, match="No user has id 99"):
        _ = await setup.update(
            owner=99, confirm=True, tags_add=["e2e-new"], create_missing_tags=True
        )

    assert len(setup.state.tags) == 4


async def test_an_owner_id_is_left_to_paperless_when_the_user_cannot_list_users() -> None:
    setup = Setup(READER[0], can_change=True)
    setup.state.users = [
        replace(u, permissions=u.permissions - {"view_user"}) for u in setup.state.users
    ]

    with pytest.raises(Validation, match="owner"):
        _ = await setup.update(owner=99, confirm=True)


async def test_a_failure_after_tags_were_created_names_them() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="archive serial number") as raised:
        _ = await setup.update(
            archive_serial_number=10, tags_add=["e2e-new", "e2e-other"], create_missing_tags=True
        )

    assert "'e2e-new'" in raised.value.message
    assert "'e2e-other'" in raised.value.message
    assert "e2e-new" in {t.name for t in setup.state.tags}


@pytest.mark.decision("decision-12")
async def test_changing_the_owner_needs_confirm_and_writes_nothing_without_it() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="confirm") as raised:
        _ = await setup.update(owner="admin", title="never")
    setup.document.owner = 2
    with pytest.raises(Validation, match="confirm"):
        _ = await setup.update(clear=["owner"])

    assert "owner" in raised.value.message
    assert raised.value.hint
    assert setup.fake.patches == []
    assert setup.document.owner == 2


@pytest.mark.decision("decision-12")
async def test_an_owner_that_is_already_right_needs_no_confirm() -> None:
    setup = Setup()
    setup.document.owner = 2

    detail = await setup.update(owner="admin", clear=[])

    assert setup.fake.patches == []
    assert detail.owner is not None


@pytest.mark.decision("decision-12")
async def test_the_owner_check_comes_before_a_tag_is_created() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="confirm"):
        _ = await setup.update(owner="admin", tags_add=["e2e-new"], create_missing_tags=True)

    assert len(setup.state.tags) == 4


async def test_a_linked_document_must_exist_and_be_visible() -> None:
    setup = Setup()
    setup.document.custom_fields[setup.field("e2e-documentlink")] = []

    with pytest.raises(NotFound, match="No document 99") as raised:
        _ = await setup.update(
            custom_fields={"e2e-documentlink": [99]}, tags_add=["e2e-new"], create_missing_tags=True
        )

    assert "search_documents" in raised.value.describe()
    assert len(setup.state.tags) == 4


async def test_links_are_mirrored_on_the_linked_document_and_dropped_from_the_old_one() -> None:
    setup = Setup()
    link = setup.field("e2e-documentlink")
    setup.state.documents.append(FakeDocument(id=3, title="third"))

    _ = await setup.update(custom_fields={"e2e-documentlink": [3]})

    assert setup.state.documents[2].custom_fields[link] == [1]
    assert setup.state.documents[1].custom_fields[link] == []


def as_editor() -> Setup:
    """A setup acting as a user who owns document 4 and may only view document 3."""
    setup = Setup()
    editor = FakeUser(
        id=9,
        username="editor",
        permissions=frozenset(
            {
                *("view_document", "change_document", "view_customfield", "view_tag", "add_tag"),
                *("view_correspondent", "view_documenttype", "view_storagepath", "view_user"),
            }
        ),
    )
    setup.state.users.append(editor)
    setup.state.documents.append(FakeDocument(id=3, title="view only", owner=2, viewers={9}))
    setup.state.documents.append(FakeDocument(id=4, title="mine", owner=9))
    setup.fake = Recording(setup.state, user="editor")
    setup.taxonomy = TaxonomyResolver(setup.fake, StaticTokenProvider("e"), ttl=60.0)
    setup.editor = DocumentEditor(
        setup.fake,
        setup.fake,
        setup.fake,
        setup.taxonomy,
        documents=DocumentService(setup.fake, setup.taxonomy, web_url=WEB),
    )
    return setup


async def test_a_link_to_a_document_the_user_may_not_change_says_what_is_needed() -> None:
    setup = as_editor()

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.update(4, custom_fields={"e2e-documentlink": [3]})

    assert "change permission" in raised.value.describe()


async def test_a_document_the_user_may_only_view_is_refused_before_any_tag_is_created() -> None:
    setup = as_editor()

    with pytest.raises(PermissionDenied, match="may not change this document"):
        _ = await setup.update(3, tags_add=["e2e-new"], create_missing_tags=True)

    assert len(setup.state.tags) == 4


async def test_links_are_compared_as_sets() -> None:
    setup = Setup()
    link = setup.field("e2e-documentlink")
    setup.state.documents.append(FakeDocument(id=3, title="third"))
    setup.document.custom_fields[link] = [2, 3]

    _ = await setup.update(custom_fields={"e2e-documentlink": [3, 2]})

    assert setup.fake.patches == []


async def test_a_title_is_stripped_before_it_is_compared_and_sent() -> None:
    setup = Setup()

    same = await setup.update(title="  e2e-public-invoice ")
    changed = await setup.update(title="  New title\n")

    assert same.title == "e2e-public-invoice"
    assert changed.title == "New title"
    assert len(setup.fake.patches) == 1


async def test_an_unknown_tag_in_tags_remove_gets_the_plain_hint() -> None:
    with pytest.raises(NotFound) as raised:
        _ = await Setup().update(tags_remove=["e2e-financ"])

    assert "create_missing_tags" not in raised.value.describe()
    assert "e2e-finance" in raised.value.describe()


async def test_an_owner_name_needs_the_user_list_and_the_hint_says_to_pass_the_id() -> None:
    setup = Setup(READER[0], can_change=True)

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.update(owner="admin", confirm=True)

    assert "user id" in (raised.value.hint or "")
