"""Contract of TaxonomyReader: the same assertions against the fake and live Paperless 3.4.2."""

from typing import TYPE_CHECKING

import pytest

from paperless_mcp.domain.errors import PermissionDenied

if TYPE_CHECKING:
    from contract.conftest import Backend
    from paperless_mcp.domain.taxonomy import TaxonomyItem, TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]

CONTRACT = "TaxonomyReader.list_taxonomy"


def by_name(rows: list[TaxonomyItem]) -> dict[str, TaxonomyItem]:
    return {row.name: row for row in rows}


@pytest.mark.contract(CONTRACT)
async def test_tags_carry_colour_parent_and_a_document_count(backend: Backend) -> None:
    tags = by_name(await backend.admin.taxonomy.list_taxonomy("tag"))

    child, parent = tags["e2e-invoices"], tags["e2e-finance"]
    assert child.parent == parent.id
    assert parent.parent is None
    assert (child.colour or "").startswith("#")
    assert child.document_count is not None
    assert child.document_count >= 1
    assert child.data_type is None


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("correspondent", "e2e-acme"),
        ("document_type", "e2e-invoice"),
        ("storage_path", "e2e-archive"),
    ],
)
@pytest.mark.contract(CONTRACT)
async def test_owned_kinds_carry_a_document_count_only(
    backend: Backend, kind: TaxonomyKind, name: str
) -> None:
    row = by_name(await backend.admin.taxonomy.list_taxonomy(kind))[name]

    assert row.document_count is not None
    assert row.document_count >= 1
    assert (row.colour, row.parent, row.data_type, row.select_options) == (None, None, None, None)


@pytest.mark.contract(CONTRACT)
async def test_custom_fields_carry_data_type_and_select_options(backend: Backend) -> None:
    fields = by_name(await backend.reader.taxonomy.list_taxonomy("custom_field"))

    select, text = fields["e2e-select"], fields["e2e-string"]
    assert select.data_type == "select"
    assert [option.label for option in select.select_options or ()] == ["alpha", "beta"]
    assert all(option.id for option in select.select_options or ())
    assert (text.data_type, text.select_options) == ("string", None)
    assert len([name for name in fields if name.startswith("e2e-")]) >= 10


@pytest.mark.parametrize(
    ("kind", "names"),
    [("saved_view", {"e2e-view"}), ("user", {"admin", "e2e-reader"}), ("group", {"e2e-group"})],
)
@pytest.mark.contract(CONTRACT)
async def test_views_users_and_groups_are_names_without_counts(
    backend: Backend, kind: TaxonomyKind, names: set[str]
) -> None:
    rows = by_name(await backend.admin.taxonomy.list_taxonomy(kind))

    assert names <= set(rows)
    assert all(rows[name].document_count is None for name in names)


@pytest.mark.contract(CONTRACT)
async def test_a_restricted_user_sees_only_shared_objects(backend: Backend) -> None:
    admin = by_name(await backend.admin.taxonomy.list_taxonomy("tag"))
    reader = by_name(await backend.reader.taxonomy.list_taxonomy("tag"))

    assert "e2e-private" in admin
    assert "e2e-private" not in reader
    assert {"e2e-finance", "e2e-invoices", "e2e-inbox"} <= set(reader)


@pytest.mark.parametrize("kind", ["saved_view", "user", "group"])
@pytest.mark.contract(CONTRACT)
async def test_a_kind_without_the_view_permission_is_denied(
    backend: Backend, kind: TaxonomyKind
) -> None:
    with pytest.raises(PermissionDenied):
        _ = await backend.reader.taxonomy.list_taxonomy(kind)
