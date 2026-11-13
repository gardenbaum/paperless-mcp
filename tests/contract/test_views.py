"""Contract of ViewReader and ViewWriter: the same assertions against the fake and live 3.4.2.

Views a test creates carry the scratch prefix (SCRATCH_VIEW); the live backend deletes them.
"""

import uuid
from typing import TYPE_CHECKING

import pytest

from contract.conftest import SCRATCH_VIEW
from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation
from paperless_mcp.domain.views import FilterRule, SavedView, ViewValues

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]

LIST, GET = "ViewReader.list_saved_views", "ViewReader.get_saved_view"
CREATE, UPDATE, DELETE = (
    "ViewWriter.create_saved_view",
    "ViewWriter.update_saved_view",
    "ViewWriter.delete_saved_view",
)
SEEDED = "e2e-view"
VIEWING = frozenset({"view_savedview", "view_uisettings"})
CHANGING = frozenset({"view_savedview", "add_savedview", "change_savedview", "delete_savedview"})
MAX_VALUE = 255


def scratch() -> str:
    return f"{SCRATCH_VIEW}{uuid.uuid4().hex}"


async def named(ports: Ports, name: str) -> SavedView:
    return next(view for view in await ports.views.list_saved_views() if view.name == name)


async def admin_id(ports: Ports) -> int:
    overview = await ports.instance.get_overview()
    assert overview.current_user is not None
    return overview.current_user.id


@pytest.mark.contract(LIST)
async def test_the_seeded_view_is_listed_with_its_rule(backend: Backend) -> None:
    tags = {tag.name: tag.id for tag in await backend.admin.taxonomy.list_taxonomy("tag")}

    view = await named(backend.admin, SEEDED)

    assert view.filter_rules == (FilterRule(rule_type=6, value=str(tags["e2e-finance"])),)
    assert (view.sort_field, view.sort_reverse, view.page_size) == (None, False, None)
    assert view.owner == await admin_id(backend.admin)
    assert view.user_can_change is True


@pytest.mark.contract(LIST)
async def test_a_user_sees_only_the_views_it_owns_or_shares(backend: Backend) -> None:
    viewer = backend.restricted(VIEWING)

    assert await viewer.views.list_saved_views() == ()


@pytest.mark.contract(LIST)
@pytest.mark.contract(GET)
async def test_a_user_without_the_view_permission_is_denied(backend: Backend) -> None:
    seeded = await named(backend.admin, SEEDED)

    for ports in (backend.reader, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await ports.views.list_saved_views()
        with pytest.raises(PermissionDenied):
            _ = await ports.views.get_saved_view(seeded.id)


@pytest.mark.contract(GET)
async def test_one_view_is_read_by_id(backend: Backend) -> None:
    listed = await named(backend.admin, SEEDED)

    assert await backend.admin.views.get_saved_view(listed.id) == listed


@pytest.mark.contract(GET)
async def test_a_missing_or_hidden_view_is_not_found(backend: Backend) -> None:
    seeded = await named(backend.admin, SEEDED)
    viewer = backend.restricted(VIEWING)

    with pytest.raises(NotFound, match="No saved view 999999"):
        _ = await backend.admin.views.get_saved_view(999_999)
    with pytest.raises(NotFound, match=f"No saved view {seeded.id}"):
        _ = await viewer.views.get_saved_view(seeded.id)


@pytest.mark.contract(CREATE)
async def test_a_created_view_keeps_its_settings_and_belongs_to_the_creator(
    backend: Backend,
) -> None:
    name = scratch()
    rules = (FilterRule(rule_type=6, value="1"), FilterRule(rule_type=3, value=None))
    values = ViewValues(filter_rules=rules, sort_field="title", sort_reverse=True, page_size=10)

    made = await backend.admin.view_writes.create_saved_view(name, values)

    assert (made.name, made.filter_rules) == (name, rules)
    assert (made.sort_field, made.sort_reverse, made.page_size) == ("title", True, 10)
    assert made.owner == await admin_id(backend.admin)
    assert made.user_can_change is True
    assert await named(backend.admin, name) == made


@pytest.mark.contract(CREATE)
async def test_a_view_without_settings_has_no_rules_and_the_defaults(backend: Backend) -> None:
    made = await backend.admin.view_writes.create_saved_view(scratch(), ViewValues())

    assert (made.filter_rules, made.sort_field, made.sort_reverse, made.page_size) == (
        (),
        None,
        False,
        None,
    )


@pytest.mark.contract(CREATE)
@pytest.mark.parametrize(
    ("values", "message"),
    [
        (ViewValues(filter_rules=(FilterRule(rule_type=99, value="1"),)), "rule_type"),
        (ViewValues(filter_rules=(FilterRule(rule_type=0, value="x" * 256),)), "255"),
        (ViewValues(page_size=0), "page_size"),
    ],
    ids=["unknown rule type", "long value", "page size"],
)
async def test_a_rejected_value_is_a_validation_error_naming_the_field(
    backend: Backend, values: ViewValues, message: str
) -> None:
    with pytest.raises(Validation, match=message):
        _ = await backend.admin.view_writes.create_saved_view(scratch(), values)


@pytest.mark.contract(CREATE)
async def test_a_blank_or_taken_name_is_rejected(backend: Backend) -> None:
    name = scratch()
    _ = await backend.admin.view_writes.create_saved_view(name, ViewValues())

    with pytest.raises(Validation, match="name"):
        _ = await backend.admin.view_writes.create_saved_view("", ViewValues())
    with pytest.raises(Validation, match="unique constraint"):
        _ = await backend.admin.view_writes.create_saved_view(name, ViewValues())


@pytest.mark.contract(CREATE)
@pytest.mark.contract(UPDATE)
@pytest.mark.contract(DELETE)
async def test_a_user_without_the_write_permissions_is_denied(backend: Backend) -> None:
    seeded = await named(backend.admin, SEEDED)
    viewer = backend.restricted(VIEWING)

    with pytest.raises(PermissionDenied):
        _ = await viewer.view_writes.create_saved_view(scratch(), ViewValues())
    with pytest.raises(PermissionDenied):
        _ = await viewer.view_writes.update_saved_view(seeded.id, ViewValues(name=scratch()))
    with pytest.raises(PermissionDenied):
        await viewer.view_writes.delete_saved_view(seeded.id)


@pytest.mark.contract(UPDATE)
async def test_an_update_changes_only_what_it_sets(backend: Backend) -> None:
    ports = backend.admin
    rules = (FilterRule(rule_type=6, value="1"),)
    made = await ports.view_writes.create_saved_view(
        scratch(), ViewValues(filter_rules=rules, sort_field="created", page_size=5)
    )
    renamed = scratch()

    after = await ports.view_writes.update_saved_view(made.id, ViewValues(name=renamed))

    assert after == made.model_copy(update={"name": renamed})
    again = await ports.view_writes.update_saved_view(
        made.id, ViewValues(filter_rules=(FilterRule(rule_type=5, value="true"),))
    )
    assert again.filter_rules == (FilterRule(rule_type=5, value="true"),)
    assert (again.name, again.sort_field, again.page_size) == (renamed, "created", 5)


@pytest.mark.contract(UPDATE)
async def test_an_update_can_clear_the_sort_and_the_page_size_and_all_rules(
    backend: Backend,
) -> None:
    ports = backend.admin
    made = await ports.view_writes.create_saved_view(
        scratch(),
        ViewValues(
            filter_rules=(FilterRule(rule_type=5, value="true"),), sort_field="title", page_size=5
        ),
    )

    after = await ports.view_writes.update_saved_view(
        made.id, ViewValues(filter_rules=(), sort_field=None, page_size=None, sort_reverse=True)
    )

    assert (after.filter_rules, after.sort_field, after.page_size) == ((), None, None)
    assert after.sort_reverse is True


@pytest.mark.contract(UPDATE)
async def test_paperless_accepts_a_rename_to_a_name_that_is_taken(backend: Backend) -> None:
    ports = backend.admin
    first = await ports.view_writes.create_saved_view(scratch(), ViewValues())
    second = await ports.view_writes.create_saved_view(scratch(), ViewValues())

    after = await ports.view_writes.update_saved_view(second.id, ViewValues(name=first.name))

    assert after.name == first.name


@pytest.mark.contract(UPDATE)
async def test_an_update_of_a_bad_value_or_a_missing_view_fails(backend: Backend) -> None:
    ports = backend.admin
    made = await ports.view_writes.create_saved_view(scratch(), ViewValues())

    with pytest.raises(Validation, match="rule_type"):
        _ = await ports.view_writes.update_saved_view(
            made.id, ViewValues(filter_rules=(FilterRule(rule_type=99, value=None),))
        )
    with pytest.raises(NotFound, match="No saved view 999999"):
        _ = await ports.view_writes.update_saved_view(999_999, ViewValues(name="x"))


@pytest.mark.contract(DELETE)
async def test_a_deleted_view_is_gone_and_a_second_delete_is_not_found(backend: Backend) -> None:
    ports = backend.admin
    made = await ports.view_writes.create_saved_view(scratch(), ViewValues())

    await ports.view_writes.delete_saved_view(made.id)

    assert made.name not in {view.name for view in await ports.views.list_saved_views()}
    with pytest.raises(NotFound, match=f"No saved view {made.id}"):
        await ports.view_writes.delete_saved_view(made.id)


@pytest.mark.contract(DELETE)
async def test_a_view_hidden_from_a_user_with_every_permission_is_not_found(
    backend: Backend,
) -> None:
    seeded = await named(backend.admin, SEEDED)
    other = backend.restricted(CHANGING)

    with pytest.raises(NotFound):
        await other.view_writes.delete_saved_view(seeded.id)
    assert await named(backend.admin, SEEDED) == seeded
