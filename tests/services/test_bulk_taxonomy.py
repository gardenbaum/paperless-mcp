"""BulkTaxonomyEditor: selection by name or id, previews, the confirmation flow, caches.

The fakes of the "nothing may be written" cases are created without `allow_writes` (see
tests/services/test_bulk_documents.py).
"""

from dataclasses import replace
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeState, FakeTaxonomy, seeded

from paperless_mcp.domain.bulk import (
    MAX_DOCUMENTS,
    DeleteItems,
    ObjectPermissions,
    PermissionsChange,
    SetPermissions,
)
from paperless_mcp.domain.errors import (
    CONFIRM_HINT,
    Ambiguous,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
)
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.bulk_taxonomy import BulkTaxonomyEditor
from paperless_mcp.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.bulk import BulkKind, TaxonomyBulkReport, TaxonomyOperation
    from paperless_mcp.domain.documents import NameOrId

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

FINANCE, INVOICES, E2E_PRIVATE, INBOX = 1, 2, 3, 4
ADMIN_ID, READER_ID, GROUP = 2, 3, 1


class Recording(FakePaperless):
    """A fake that remembers the bulk edits of objects it was asked for."""

    def __init__(self, state: FakeState, *, user: str, writes: bool) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.deleted: list[tuple[BulkKind, tuple[int, ...]]] = []
        self.permissions: list[tuple[BulkKind, tuple[int, ...], PermissionsChange]] = []

    @override
    async def delete_taxonomy_items(self, kind: BulkKind, item_ids: Sequence[int]) -> None:
        self.deleted.append((kind, tuple(item_ids)))
        await super().delete_taxonomy_items(kind, item_ids)

    @override
    async def set_taxonomy_permissions(
        self, kind: BulkKind, item_ids: Sequence[int], change: PermissionsChange
    ) -> None:
        self.permissions.append((kind, tuple(item_ids), change))
        await super().set_taxonomy_permissions(kind, item_ids, change)


@final
class Setup:
    def __init__(
        self, user: str = ADMIN[0], *, writes: bool = True, grant: frozenset[str] | None = None
    ) -> None:
        self.state: FakeState = seeded()
        self.state.users = [
            replace(u, permissions=u.permissions | (grant or frozenset()))
            if u.username == user
            else u
            for u in self.state.users
        ]
        self.state.correspondents.extend(
            [FakeTaxonomy(id=2, name="Beta GmbH"), FakeTaxonomy(id=3, name="Gamma AG")]
        )
        self.fake: Recording = Recording(self.state, user=user, writes=writes)
        self.taxonomy: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.editor: BulkTaxonomyEditor = BulkTaxonomyEditor(self.fake, self.fake, self.taxonomy)

    async def run(
        self,
        kind: BulkKind,
        items: Sequence[NameOrId],
        operation: TaxonomyOperation,
        *,
        dry_run: bool = False,
        confirm: bool = True,
    ) -> TaxonomyBulkReport:
        return await self.editor.edit(kind, items, operation, dry_run=dry_run, confirm=confirm)


DELETE = DeleteItems(op="delete")
MERGE_HINT = (
    "Paperless then sets it only on objects without an owner and never removes one; "
    "set the owner in a call without merge."
)


# --- delete


async def test_a_dry_run_of_a_delete_names_the_objects_their_documents_and_the_children() -> None:
    setup = Setup(writes=False)

    report = await setup.run("tag", ["e2e-finance"], DELETE, dry_run=True)

    assert (report.kind, report.operation, report.dry_run, report.applied) == (
        "tag",
        "delete",
        True,
        False,
    )
    assert report.affected_count == 1
    assert [(row.id, row.name, row.document_count) for row in report.sample] == [
        (FINANCE, "e2e-finance", 1)
    ]
    assert report.also_deleted == ("e2e-invoices",)
    assert report.summary == "delete the selected tags for good"
    assert report.message == (
        "Dry run, nothing was changed. Confirming would delete 1 tag 'e2e-finance' for good: "
        "it is removed from its documents ('e2e-finance (1 document)'); its child tags "
        "'e2e-invoices (1 document)' are deleted with it; child tags you cannot see (other "
        "users') are deleted with it as well. To go ahead, call again without dry_run and "
        "with confirm=true."
    )
    assert setup.fake.deleted == []


async def test_a_delete_without_confirm_is_refused_with_the_effect_and_writes_nothing() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run("correspondent", ["e2e-acme", "Beta GmbH"], DELETE, confirm=False)

    assert raised.value.message == (
        "Nothing was changed. Confirming would delete 2 correspondents 'e2e-acme', 'Beta GmbH' "
        "for good: the documents that use them lose their correspondent "
        "('e2e-acme (1 document)', 'Beta GmbH (0 documents)')."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert setup.fake.deleted == []


async def test_a_confirmed_delete_removes_the_objects_with_their_children() -> None:
    setup = Setup()

    report = await setup.run("tag", ["e2e-finance", INBOX], DELETE)

    assert setup.fake.deleted == [("tag", (FINANCE, INBOX))]
    assert {t.id for t in setup.state.tags} == {E2E_PRIVATE}
    assert (report.dry_run, report.applied, report.affected_count) == (False, True, 2)
    assert report.also_deleted == ("e2e-invoices",)
    assert report.message.startswith("Deleted 2 tags 'e2e-finance', 'e2e-inbox' for good: ")


async def test_a_child_that_is_selected_too_is_not_listed_as_also_deleted() -> None:
    setup = Setup(writes=False)

    report = await setup.run("tag", ["e2e-finance", "e2e-invoices"], DELETE, dry_run=True)

    assert report.also_deleted == ()
    assert report.affected_count == 2


async def test_the_selection_resolves_names_and_ids_once_each() -> None:
    setup = Setup()

    report = await setup.run("correspondent", ["beta gmbh", 2, "2", "Gamma AG"], DELETE)

    assert setup.fake.deleted == [("correspondent", (2, 3))]
    assert report.affected_count == 2


async def test_an_unknown_or_ambiguous_name_stops_everything() -> None:
    setup = Setup(writes=False)
    setup.state.correspondents.append(FakeTaxonomy(id=9, name="BETA gmbh"))

    with pytest.raises(NotFound):
        _ = await setup.run("correspondent", ["e2e-acme", "nobody"], DELETE)
    with pytest.raises(Ambiguous):
        _ = await setup.run("correspondent", ["BETA GMBH"], DELETE)


@pytest.mark.parametrize("count", [0, MAX_DOCUMENTS + 1])
async def test_the_selection_is_one_to_a_hundred_objects(count: int) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.run("tag", list(range(1, count + 1)), DELETE, dry_run=True)

    assert raised.value.message == f"Select between 1 and {MAX_DOCUMENTS} items per call."
    assert raised.value.hint == "Use list_taxonomy to find the ones you mean, and split the work."


async def test_the_sample_lists_at_most_ten_objects() -> None:
    setup = Setup(writes=False)
    setup.state.correspondents.extend(FakeTaxonomy(id=n, name=f"Firm {n}") for n in range(10, 30))

    report = await setup.run("correspondent", list(range(10, 30)), DELETE, dry_run=True)

    assert report.affected_count == 20
    assert [row.id for row in report.sample] == list(range(10, 20))


async def test_a_user_who_may_not_delete_gets_a_preview_but_no_deletion() -> None:
    setup = Setup(READER[0])
    setup.state.correspondents[0].owner = None  # shared, so the reader sees it

    preview = await setup.run("correspondent", ["e2e-acme"], DELETE, dry_run=True)
    assert preview.affected_count == 1

    with pytest.raises(PermissionDenied):
        _ = await setup.run("correspondent", ["e2e-acme"], DELETE)
    assert len(setup.state.correspondents) == 3


async def test_the_taxonomy_cache_forgets_deleted_objects() -> None:
    setup = Setup()
    assert {i.name for i in await setup.taxonomy.items("correspondent")} >= {"Beta GmbH"}

    _ = await setup.run("correspondent", ["Beta GmbH"], DELETE)

    assert "Beta GmbH" not in {i.name for i in await setup.taxonomy.items("correspondent")}


# --- permissions


async def test_the_owner_and_grants_are_set_by_name_after_confirmation() -> None:
    setup = Setup()
    operation = SetPermissions(
        op="set_permissions",
        owner="e2e-reader",
        view_users=["e2e-reader"],
        view_groups=["e2e-group"],
        change_groups=[GROUP],
    )

    report = await setup.run("tag", ["e2e-finance", "e2e-inbox"], operation)

    [(kind, ids, change)] = setup.fake.permissions
    assert (kind, ids) == ("tag", (FINANCE, INBOX))
    assert change == PermissionsChange(
        owner=READER_ID,
        permissions=ObjectPermissions(
            view_users=(READER_ID,), view_groups=(GROUP,), change_groups=(GROUP,)
        ),
    )
    finance = setup.state.tags[0]
    assert (finance.owner, finance.viewers, finance.view_groups) == (
        READER_ID,
        {READER_ID},
        {GROUP},
    )
    assert report.applied
    assert report.message == (
        "Applied: set the owner to 'e2e-reader' and the grants (view users 'e2e-reader', view "
        "groups 'e2e-group', change users none, change groups #1; replacing all existing "
        "grants) on 2 tags 'e2e-finance', 'e2e-inbox'."
    )


async def test_a_permissions_change_is_refused_without_confirm_and_says_what_it_does() -> None:
    setup = Setup(writes=False)
    operation = SetPermissions(op="set_permissions", clear_owner=True)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.run("storage_path", ["e2e-archive"], operation, confirm=False)

    assert raised.value.message == (
        "Nothing was changed. Confirming would remove the owner (every user may then use the "
        "objects) on 1 storage path 'e2e-archive'."
    )
    assert setup.fake.permissions == []


async def test_a_dry_run_of_a_permissions_change_writes_nothing() -> None:
    setup = Setup(writes=False)
    operation = SetPermissions(op="set_permissions", view_users=["e2e-reader"], merge=True)

    report = await setup.run("document_type", ["e2e-invoice"], operation, dry_run=True)

    assert report.dry_run
    assert report.summary == "set permissions on the selected document types"
    expected = (
        "Dry run, nothing was changed. Confirming would set the grants (view users 'e2e-reader'; "
        "added to the existing grants) on 1 document type 'e2e-invoice'."
    )
    assert report.message.startswith(expected)
    assert setup.fake.permissions == []


async def test_a_cleared_owner_is_sent_as_none_and_a_left_out_owner_is_not_sent() -> None:
    setup = Setup()

    _ = await setup.run("tag", [INBOX], SetPermissions(op="set_permissions", clear_owner=True))
    _ = await setup.run(
        "tag", [INBOX], SetPermissions(op="set_permissions", change_users=[ADMIN_ID])
    )

    cleared, grants = (change for _, _, change in setup.fake.permissions)
    assert "owner" in cleared.model_fields_set
    assert cleared.owner is None
    assert "owner" not in grants.model_fields_set
    assert grants.permissions == ObjectPermissions(change_users=(ADMIN_ID,))


async def test_ids_of_users_and_groups_are_passed_on_without_listing_them() -> None:
    setup = Setup()
    operation = SetPermissions(op="set_permissions", owner=READER_ID, view_users=["3", 2])

    _ = await setup.run("tag", [INBOX], operation)

    [(_, _, change)] = setup.fake.permissions
    assert change.owner == READER_ID
    assert change.permissions == ObjectPermissions(view_users=(READER_ID, ADMIN_ID))


async def test_a_user_name_needs_the_permission_to_list_users_else_an_id_is_asked_for() -> None:
    setup = Setup(READER[0])
    setup.state.tags[3].owner = None

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.run(
            "tag",
            [INBOX],
            SetPermissions(op="set_permissions", view_users=["e2e-reader"]),
            dry_run=True,
        )

    assert raised.value.hint == "Pass the user id instead of the name."


async def test_an_unknown_user_or_group_is_not_found() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound):
        _ = await setup.run(
            "tag", [INBOX], SetPermissions(op="set_permissions", owner="nobody"), dry_run=True
        )
    with pytest.raises(NotFound):
        _ = await setup.run(
            "tag", [INBOX], SetPermissions(op="set_permissions", view_groups=["nobody"])
        )


@pytest.mark.parametrize(
    ("operation", "message", "hint"),
    [
        (
            SetPermissions(op="set_permissions"),
            "Nothing to change: no owner and no grants were given.",
            "Pass owner, clear_owner or a list of users or groups.",
        ),
        (
            SetPermissions(op="set_permissions", owner=3, clear_owner=True),
            "Give owner or clear_owner, not both.",
            "clear_owner=true removes the owner.",
        ),
        (
            SetPermissions(op="set_permissions", owner=3, view_users=[3], merge=True),
            "An owner cannot be set while merging grants.",
            MERGE_HINT,
        ),
        (
            SetPermissions(op="set_permissions", clear_owner=True, merge=True),
            "An owner cannot be set while merging grants.",
            MERGE_HINT,
        ),
    ],
)
async def test_empty_or_contradictory_permission_changes_are_refused(
    operation: SetPermissions, message: str, hint: str
) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.run("tag", [INBOX], operation, dry_run=True)

    assert (raised.value.message, raised.value.hint) == (message, hint)


async def test_a_user_who_may_not_change_permissions_is_denied_after_the_preview() -> None:
    setup = Setup(READER[0])
    setup.state.tags[3].owner = None

    with pytest.raises(PermissionDenied):
        _ = await setup.run("tag", [INBOX], SetPermissions(op="set_permissions", clear_owner=True))


async def test_the_taxonomy_cache_forgets_what_a_permission_change_hides() -> None:
    setup = Setup(READER[0], grant=frozenset({"change_tag"}))
    setup.state.tags[3].owner = READER_ID
    assert INBOX in {i.id for i in await setup.taxonomy.items("tag")}

    _ = await setup.run("tag", [INBOX], SetPermissions(op="set_permissions", owner=ADMIN_ID))

    assert INBOX not in {i.id for i in await setup.taxonomy.items("tag")}


# --- messages and edges found by the mutation run


async def test_a_hundred_objects_are_allowed() -> None:
    setup = Setup(writes=False)
    setup.state.correspondents.extend(
        FakeTaxonomy(id=n, name=f"Firm {n}") for n in range(10, 10 + MAX_DOCUMENTS)
    )

    report = await setup.run(
        "correspondent", list(range(10, 10 + MAX_DOCUMENTS)), DELETE, dry_run=True
    )

    assert report.affected_count == MAX_DOCUMENTS


async def test_a_permissions_preview_does_not_list_child_tags_as_deleted() -> None:
    setup = Setup(writes=False)
    operation = SetPermissions(op="set_permissions", clear_owner=True)

    report = await setup.run("tag", ["e2e-finance"], operation, dry_run=True)

    assert report.also_deleted == ()


async def test_the_sample_carries_the_document_counts_including_zero() -> None:
    setup = Setup(writes=False)

    report = await setup.run("correspondent", ["e2e-acme", "Beta GmbH"], DELETE, dry_run=True)

    assert [(row.name, row.document_count) for row in report.sample] == [
        ("e2e-acme", 1),
        ("Beta GmbH", 0),
    ]


async def test_merge_reaches_the_port() -> None:
    setup = Setup()

    _ = await setup.run(
        "tag", [INBOX], SetPermissions(op="set_permissions", view_users=[READER_ID], merge=True)
    )
    _ = await setup.run("tag", [INBOX], SetPermissions(op="set_permissions", view_users=[ADMIN_ID]))

    merged, replaced = (change for _, _, change in setup.fake.permissions)
    assert (merged.merge, replaced.merge) == (True, False)


async def test_several_principals_are_listed_with_commas() -> None:
    setup = Setup(writes=False)
    operation = SetPermissions(op="set_permissions", view_users=["e2e-reader", 2])

    report = await setup.run("tag", [INBOX], operation, dry_run=True)

    assert "view users 'e2e-reader', #2," in report.message


async def test_a_removed_owner_with_grants_is_one_sentence() -> None:
    setup = Setup(writes=False)
    operation = SetPermissions(op="set_permissions", clear_owner=True, view_users=["e2e-reader"])

    report = await setup.run("tag", [INBOX], operation, dry_run=True)

    assert report.message == (
        "Dry run, nothing was changed. Confirming would remove the owner (every user may then "
        "use the objects) and set the grants (view users 'e2e-reader', view groups none, change "
        "users none, change groups none; replacing all existing grants) on 1 tag 'e2e-inbox'. "
        "To go ahead, call again without dry_run and with confirm=true."
    )


async def test_an_owner_alone_is_one_short_sentence() -> None:
    setup = Setup(writes=False)

    report = await setup.run(
        "tag", [INBOX], SetPermissions(op="set_permissions", owner="e2e-reader"), dry_run=True
    )

    assert (
        " Confirming would set the owner to 'e2e-reader' on 1 tag 'e2e-inbox'. " in report.message
    )


async def test_a_name_that_cannot_be_resolved_for_lack_of_permission_keeps_the_message() -> None:
    setup = Setup(READER[0])
    setup.state.tags[3].owner = None

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.run(
            "tag", [INBOX], SetPermissions(op="set_permissions", view_users=["e2e-reader"])
        )

    assert raised.value.message == "The Paperless user may not perform this action."
    assert raised.value.hint == "Pass the user id instead of the name."


async def test_child_tags_are_listed_in_name_order_and_plurals_read_right() -> None:
    setup = Setup(writes=False)
    setup.state.tags.extend(
        [
            replace(setup.state.tags[1], id=7, name="zeta", parent=FINANCE),
            replace(setup.state.tags[1], id=8, name="alpha", parent=FINANCE),
        ]
    )

    report = await setup.run("tag", ["e2e-finance", "e2e-private"], DELETE, dry_run=True)

    assert report.also_deleted == ("alpha", "e2e-invoices", "zeta")
    assert report.message == (
        "Dry run, nothing was changed. Confirming would delete 2 tags 'e2e-finance', "
        "'e2e-private' for good: they are removed from their documents ('e2e-finance (1 "
        "document)', 'e2e-private (1 document)'); their child tags 'alpha (0 documents)', "
        "'e2e-invoices (1 document)', 'zeta (0 documents)' are deleted with them; child tags "
        "you cannot see (other users') are deleted with them as well. To go ahead, call again "
        "without dry_run and with confirm=true."
    )
