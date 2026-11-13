"""ViewService: saved views by name or id, rules by label and name, confirmation, running."""

from dataclasses import replace
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeSavedView, FakeState, seeded

from paperless_mcp.domain.documents import DocumentQuery, NameFilters
from paperless_mcp.domain.errors import (
    CONFIRM_HINT,
    Ambiguous,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
)
from paperless_mcp.domain.views import FilterRule, RuleInput, ViewCreate, ViewEdit
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.documents import DocumentService
from paperless_mcp.services.filter_rules import translate
from paperless_mcp.services.taxonomy import TaxonomyResolver
from paperless_mcp.services.views import ViewService

if TYPE_CHECKING:
    from paperless_mcp.domain.views import SavedView, ViewValues

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-5"),
    pytest.mark.decision("decision-14"),
    pytest.mark.decision("decision-15"),
]

WEB = "https://paperless.example"


class Recording(FakePaperless):
    """A fake that remembers every write it was given."""

    def __init__(self, state: FakeState, *, user: str, writes: bool) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.writes: list[str] = []
        self.updates: list[ViewValues] = []

    @override
    async def create_saved_view(self, name: str, values: ViewValues) -> SavedView:
        self.writes.append("create")
        return await super().create_saved_view(name, values)

    @override
    async def update_saved_view(self, view_id: int, values: ViewValues) -> SavedView:
        self.writes.append("update")
        self.updates.append(values)
        return await super().update_saved_view(view_id, values)

    @override
    async def delete_saved_view(self, view_id: int) -> None:
        self.writes.append("delete")
        await super().delete_saved_view(view_id)


@final
class Setup:
    def __init__(
        self, user: str = ADMIN[0], *, writes: bool = True, grant: tuple[str, ...] = ()
    ) -> None:
        self.state: FakeState = seeded()
        self.state.users = [
            replace(u, permissions=u.permissions | frozenset(grant)) if u.username == user else u
            for u in self.state.users
        ]
        self.fake: Recording = Recording(self.state, user=user, writes=writes)
        self.resolver: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.documents: DocumentService = DocumentService(self.fake, self.resolver, web_url=WEB)
        self.views: ViewService = ViewService(self.fake, self.fake, self.resolver, self.documents)

    def view(self, name: str) -> FakeSavedView:
        return next(v for v in self.state.saved_views if v.name == name)


def rule(kind: int | str, value: str | int | bool | None = None) -> RuleInput:  # noqa: FBT001 -- a parametrised flag, not a switch
    return RuleInput(rule=kind, value=value)


async def test_the_views_are_listed_with_the_labels_of_their_rule_types() -> None:
    listing = await Setup().views.list()

    [view] = listing.views
    assert listing.count == 1
    assert (view.id, view.name, view.can_change, view.owner) == (1, "e2e-view", True, 2)
    assert [(r.rule_type, r.name, r.value) for r in view.rules] == [(6, "has tag", "1")]


async def test_a_view_is_read_by_name_ignoring_case_or_by_id() -> None:
    views = Setup().views

    assert (await views.get("E2E-VIEW")).id == 1
    assert (await views.get(1)).name == "e2e-view"
    assert (await views.get("1")).name == "e2e-view"


async def test_an_unknown_view_is_not_found_with_suggestions() -> None:
    with pytest.raises(NotFound, match="No saved view is named 'e2e-vew'") as raised:
        _ = await Setup().views.get("e2e-vew")

    assert raised.value.hint is not None
    assert "e2e-view" in raised.value.hint


async def test_two_views_with_one_name_are_ambiguous() -> None:
    setup = Setup()
    setup.state.saved_views += [
        FakeSavedView(id=5, name="Same", owner=2),
        FakeSavedView(id=6, name="SAME", owner=2),
    ]

    with pytest.raises(Ambiguous, match="2 saved views match") as raised:
        _ = await setup.views.get("same")

    assert raised.value.hint == "Pass the id of the one you mean."


async def test_a_user_without_the_view_permission_is_denied() -> None:
    with pytest.raises(PermissionDenied):
        _ = await Setup(READER[0]).views.list()


async def test_a_created_view_resolves_rule_labels_and_names_to_what_paperless_stores() -> None:
    setup = Setup()
    spec = ViewCreate(
        name="  Inbox invoices ",
        rules=(
            rule("has tag", "e2e-finance"),
            rule("Has Tag", 3),
            RuleInput(rule=5, value=True),
            rule("correspondent is", "E2E-ACME"),
            rule("document type is", "e2e-invoice"),
            rule("storage path is", "e2e-archive"),
            rule("created after", "2026-01-01"),
            rule("owner is", "admin"),
            rule("has custom fields", "e2e-string"),
            rule("title contains", "invoice"),
        ),
        sort_field="created",
        sort_reverse=True,
        page_size=10,
    )

    made = await setup.views.create(spec)

    assert made.name == "Inbox invoices"
    assert [(r.rule_type, r.value) for r in made.rules] == [
        (6, "1"),
        (6, "3"),
        (5, "true"),
        (3, "1"),
        (4, "1"),
        (25, "1"),
        (9, "2026-01-01"),
        (32, "2"),
        (38, "1"),
        (0, "invoice"),
    ]
    assert (made.sort_field, made.sort_reverse, made.page_size) == ("created", True, 10)
    stored = setup.view("Inbox invoices")
    assert stored.owner == 2


async def test_a_value_less_correspondent_rule_means_not_assigned_and_stays_null() -> None:
    made = await Setup().views.create(
        ViewCreate(name="n", rules=(rule(3), rule(4, "-1"), rule("storage path is", None)))
    )

    assert [(r.rule_type, r.value) for r in made.rules] == [(3, None), (4, "-1"), (25, None)]


async def test_a_rule_is_checked_before_anything_is_written() -> None:
    setup = Setup()

    for bad in (rule(2, "seven"), rule(51, "x"), rule("created after", "soon")):
        with pytest.raises(Validation, match="rule type"):
            _ = await setup.views.create(ViewCreate(name="n", rules=(bad,)))

    assert setup.fake.writes == []


async def test_an_unknown_rule_label_is_rejected_with_the_closest_labels() -> None:
    with pytest.raises(Validation, match="No filter rule type is called 'has tags'") as raised:
        _ = await Setup().views.create(ViewCreate(name="n", rules=(rule("has tags", "1"),)))

    assert raised.value.hint is not None
    assert "has tag" in raised.value.hint


async def test_an_unknown_taxonomy_name_in_a_rule_is_not_found() -> None:
    with pytest.raises(NotFound, match="No tag is named 'nope'"):
        _ = await Setup().views.create(ViewCreate(name="n", rules=(rule("has tag", "nope"),)))


async def test_a_blank_name_and_a_taken_name_are_rejected_before_the_write() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="name is empty"):
        _ = await setup.views.create(ViewCreate(name="  "))
    with pytest.raises(Validation, match="already has a saved view named 'e2e-view'"):
        _ = await setup.views.create(ViewCreate(name="E2E-VIEW"))

    assert setup.fake.writes == []


async def test_a_created_view_is_seen_by_name_resolution_at_once() -> None:
    setup = Setup()
    assert [item.name for item in await setup.resolver.items("saved_view")] == ["e2e-view"]

    _ = await setup.views.create(ViewCreate(name="fresh"))

    assert {item.name for item in await setup.resolver.items("saved_view")} == {"e2e-view", "fresh"}


async def test_an_update_changes_only_what_is_named() -> None:
    setup = Setup()
    made = await setup.views.create(
        ViewCreate(name="old", rules=(rule(5, "true"),), sort_field="title", page_size=7)
    )

    renamed = await setup.views.update("old", ViewEdit(name="new"))

    assert renamed.rules == made.rules
    assert (renamed.name, renamed.sort_field, renamed.page_size) == ("new", "title", 7)
    assert setup.fake.updates[-1].model_fields_set == {"name"}


async def test_new_rules_replace_all_old_ones_and_are_resolved() -> None:
    setup = Setup()

    after = await setup.views.update("e2e-view", ViewEdit(rules=(rule("has tag", "e2e-private"),)))

    assert [(r.rule_type, r.value) for r in after.rules] == [(6, "3")]


async def test_an_update_can_remove_every_rule_the_sort_and_the_page_size() -> None:
    setup = Setup()
    _ = await setup.views.create(
        ViewCreate(name="v", rules=(rule(5, "true"),), sort_field="title", page_size=7)
    )

    after = await setup.views.update(
        "v", ViewEdit(rules=(), clear_sort=True, clear_page_size=True, sort_reverse=True)
    )

    assert (after.rules, after.sort_field, after.page_size, after.sort_reverse) == (
        (),
        None,
        None,
        True,
    )


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (ViewEdit(), "Nothing to change"),
        (ViewEdit(sort_field="title", clear_sort=True), "sort_field and clear_sort"),
        (ViewEdit(page_size=5, clear_page_size=True), "page_size and clear_page_size"),
        (ViewEdit(name=" "), "name is empty"),
    ],
)
async def test_an_update_that_contradicts_itself_or_changes_nothing_is_refused(
    edit: ViewEdit, message: str
) -> None:
    setup = Setup()

    with pytest.raises(Validation, match=message):
        _ = await setup.views.update("e2e-view", edit)

    assert setup.fake.writes == []


async def test_a_rename_to_the_name_of_another_view_is_refused_although_paperless_accepts_it() -> (
    None
):
    setup = Setup()
    _ = await setup.views.create(ViewCreate(name="other"))

    with pytest.raises(Validation, match="already has a saved view named 'e2e-view'"):
        _ = await setup.views.update("other", ViewEdit(name="E2E-VIEW"))
    same = await setup.views.update("other", ViewEdit(name="other", page_size=3))

    assert (same.name, same.page_size) == ("other", 3)


async def test_an_update_of_a_missing_view_is_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().views.update(99, ViewEdit(name="x"))


async def test_a_delete_without_confirm_changes_nothing_and_says_what_it_would_do() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.views.delete("e2e-view", confirm=False)

    assert raised.value.message == (
        "Nothing was changed. Confirming would delete saved view 'e2e-view' (id 1) for good: "
        "its 1 filter rule and its sort order are lost; no document is touched."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert [v.name for v in setup.state.saved_views] == ["e2e-view"]


async def test_a_confirmed_delete_removes_the_view_and_forgets_its_name() -> None:
    setup = Setup()
    _ = await setup.resolver.items("saved_view")

    gone = await setup.views.delete(1, confirm=True)

    assert (gone.id, gone.name) == (1, "e2e-view")
    assert gone.message == "Saved view 'e2e-view' (id 1) was deleted; no document was touched."
    assert setup.state.saved_views == []
    assert await setup.resolver.items("saved_view") == []


async def test_the_unconfirmed_delete_of_a_view_with_no_rules_uses_the_plural() -> None:
    setup = Setup(writes=False)
    setup.state.saved_views.append(FakeSavedView(id=2, name="plain", owner=2))

    with pytest.raises(ConfirmationRequired, match="its 0 filter rules"):
        _ = await setup.views.delete("plain", confirm=False)


async def test_running_a_view_returns_the_rows_and_paging_of_the_same_search() -> None:
    setup = Setup()
    for number in range(3, 8):
        setup.state.documents.append(FakeDocument(id=number, title=f"extra {number}", tags=[1]))
    expected = await setup.documents.search(
        NameFilters(), DocumentQuery(tags_all=(1,)), page=2, page_size=2
    )

    found = await setup.views.run("e2e-view", page=2, page_size=2)

    assert found == expected
    assert (found.count, found.page, found.next_page) == (6, 2, 3)


async def test_a_view_runs_with_its_sort_order() -> None:
    setup = Setup()
    _ = await setup.views.create(
        ViewCreate(
            name="by title",
            rules=(RuleInput(rule="has any tag", value=True),),
            sort_field="title",
            sort_reverse=True,
        )
    )

    found = await setup.views.run("by title", page=1, page_size=25)

    assert [d.title for d in found.documents] == ["e2e-public-invoice", "e2e-private-note"]


async def test_a_view_with_a_rule_that_cannot_be_run_fails_naming_the_rule_type() -> None:
    setup = Setup()
    setup.state.saved_views.append(
        FakeSavedView(
            id=2, name="odd", owner=2, filter_rules=(FilterRule(rule_type=99, value="1"),)
        )
    )

    with pytest.raises(Validation, match="Filter rule type 99"):
        _ = await setup.views.run("odd", page=1, page_size=25)


async def test_running_a_view_translates_exactly_the_stored_rules() -> None:
    setup = Setup()
    rules = (FilterRule(rule_type=3, value=None), FilterRule(rule_type=5, value="false"))
    setup.state.saved_views.append(
        FakeSavedView(id=2, name="no correspondent", owner=2, filter_rules=rules)
    )
    expected = await setup.documents.search(
        NameFilters(), translate(rules, None, sort_reverse=False), page=1, page_size=25
    )

    assert await setup.views.run(2, page=1, page_size=25) == expected
    assert [d.title for d in expected.documents] == ["e2e-private-note"]


async def test_a_flag_value_of_false_is_stored_as_false() -> None:
    made = await Setup().views.create(
        ViewCreate(
            name="flags",
            rules=(RuleInput(rule=5, value=False), RuleInput(rule=7, value=True)),
        )
    )

    assert [(r.rule_type, r.value) for r in made.rules] == [(5, "false"), (7, "true")]


async def test_an_update_can_set_the_sort_field_and_the_page_size_alone() -> None:
    setup = Setup()

    after = await setup.views.update(1, ViewEdit(sort_field="created", page_size=4))

    assert (after.sort_field, after.page_size) == ("created", 4)
    assert setup.fake.updates[-1].model_fields_set == {"sort_field", "page_size"}


async def test_a_view_with_a_bad_stored_sort_can_still_be_renamed_and_read() -> None:
    setup = Setup()
    setup.state.saved_views.append(
        FakeSavedView(id=2, name="old", owner=2, sort_field="bogus", sort_reverse=True)
    )

    after = await setup.views.update("old", ViewEdit(name="fixed"))

    assert (after.name, after.sort_field) == ("fixed", "bogus")
    assert (await setup.views.get("fixed")).sort_reverse is True


async def test_a_stored_rule_type_that_paperless_does_not_know_is_listed_by_number() -> None:
    setup = Setup()
    setup.state.saved_views.append(
        FakeSavedView(
            id=2, name="odd", owner=2, filter_rules=(FilterRule(rule_type=99, value="1"),)
        )
    )

    [rule_info] = (await setup.views.get("odd")).rules

    assert (rule_info.rule_type, rule_info.name, rule_info.value) == (
        99,
        "unknown rule type 99",
        "1",
    )
