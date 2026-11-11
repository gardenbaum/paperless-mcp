"""PaperlessTaxonomyReader on responses recorded from the Paperless 3.4.2 E2E stack."""

from typing import TYPE_CHECKING

import httpx2
import pytest

from adapter.conftest import tag
from adapter.test_pagination import TagServer
from paperless_mcp.domain.errors import PaperlessMcpError, PermissionDenied
from paperless_mcp.domain.taxonomy import TaxonomyItem
from paperless_mcp.paperless.pagination import MAX_ITEMS
from paperless_mcp.paperless.taxonomy import PaperlessTaxonomyReader

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory
    from paperless_mcp.domain.taxonomy import TaxonomyKind

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

OWNED: Json = {"match": "", "matching_algorithm": 1, "is_insensitive": True, "owner": None}
CORRESPONDENT: Json = {
    "id": 1,
    "slug": "e2e-acme",
    "name": "e2e-acme",
    **OWNED,
    "document_count": 1,
    "last_correspondence": "2026-11-11",
    "permissions": {},
    "user_can_change": True,
}
DOCUMENT_TYPE: Json = {
    "id": 1,
    "slug": "e2e-invoice",
    "name": "e2e-invoice",
    **OWNED,
    "document_count": 1,
    "permissions": {},
    "user_can_change": True,
}
STORAGE_PATH: Json = {
    "id": 1,
    "slug": "e2e-archive",
    "name": "e2e-archive",
    "path": "e2e/{{ created_year }}/{{ title }}",
    **OWNED,
    "document_count": 1,
    "user_can_change": True,
}
SELECT_FIELD: Json = {
    "id": 10,
    "name": "e2e-select",
    "data_type": "select",
    "extra_data": {
        "select_options": [
            {"label": "alpha", "id": "1sg5d8f1333w5Qak"},
            {"label": "beta", "id": "fV0FaQetPHJNFt8t"},
        ]
    },
    "document_count": 1,
}
MONETARY_FIELD: Json = {
    "id": 7,
    "name": "e2e-monetary",
    "data_type": "monetary",
    "extra_data": {"default_currency": None},
    "document_count": 1,
}
SAVED_VIEW: Json = {
    "id": 1,
    "name": "e2e-view",
    "icon": "funnel",
    "sort_field": None,
    "sort_reverse": False,
    "filter_rules": [{"rule_type": 6, "value": "1"}],
    "page_size": None,
    "display_mode": None,
    "display_fields": None,
    "owner": 2,
    "permissions": {},
    "user_can_change": True,
}
USER: Json = {
    "id": 3,
    "username": "e2e-reader",
    "email": "reader@example.com",
    "password": "**********",
    "first_name": "",
    "last_name": "",
    "date_joined": "2026-11-11T19:47:14.816796Z",
    "is_staff": False,
    "is_active": True,
    "is_superuser": False,
    "groups": [],
    "user_permissions": ["view_tag"],
    "inherited_permissions": [],
    "is_mfa_enabled": False,
}
GROUP: Json = {"id": 1, "name": "e2e-group", "permissions": []}
CHILD_TAG: Json = {**tag(2, "e2e-invoices"), "parent": 1, "document_count": 1}


def row(**fields: object) -> TaxonomyItem:
    return TaxonomyItem.model_validate({"document_count": None, **fields})


CASES: list[tuple[TaxonomyKind, str, list[Json], list[TaxonomyItem]]] = [
    (
        "tag",
        "/api/tags/",
        [CHILD_TAG],
        [row(id=2, name="e2e-invoices", document_count=1, colour="#a6cee3", parent=1)],
    ),
    (
        "correspondent",
        "/api/correspondents/",
        [CORRESPONDENT],
        [row(id=1, name="e2e-acme", document_count=1)],
    ),
    (
        "document_type",
        "/api/document_types/",
        [DOCUMENT_TYPE],
        [row(id=1, name="e2e-invoice", document_count=1)],
    ),
    (
        "storage_path",
        "/api/storage_paths/",
        [STORAGE_PATH],
        [row(id=1, name="e2e-archive", document_count=1)],
    ),
    (
        "custom_field",
        "/api/custom_fields/",
        [SELECT_FIELD, MONETARY_FIELD],
        [
            row(
                id=10,
                name="e2e-select",
                document_count=1,
                data_type="select",
                select_options=("alpha", "beta"),
            ),
            row(id=7, name="e2e-monetary", document_count=1, data_type="monetary"),
        ],
    ),
    ("saved_view", "/api/saved_views/", [SAVED_VIEW], [row(id=1, name="e2e-view")]),
    ("user", "/api/users/", [USER], [row(id=3, name="e2e-reader")]),
    ("group", "/api/groups/", [GROUP], [row(id=1, name="e2e-group")]),
]


class OnePage:
    """Serves `rows` as the only page of `path`; anything else is a 404."""

    def __init__(self, path: str, rows: list[Json]) -> None:
        self.path: str = path
        self.rows: list[Json] = rows

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path != self.path:
            return httpx2.Response(404, json={"detail": "Not found."})
        body = {"count": len(self.rows), "next": None, "previous": None, "results": self.rows}
        return httpx2.Response(200, json=body)


@pytest.mark.parametrize(("kind", "path", "rows", "expected"), CASES)
async def test_each_kind_is_read_from_its_endpoint_as_compact_rows(
    make_client: ClientFactory,
    kind: TaxonomyKind,
    path: str,
    rows: list[Json],
    expected: list[TaxonomyItem],
) -> None:
    reader = PaperlessTaxonomyReader(make_client(OnePage(path, rows)))

    assert await reader.list_taxonomy(kind) == expected


async def test_every_page_is_read(make_client: ClientFactory) -> None:
    server = TagServer(total=250)

    found = await PaperlessTaxonomyReader(make_client(server)).list_taxonomy("tag")

    assert [item.id for item in found] == list(range(1, 251))
    assert len(server.requests) == 3


async def test_more_objects_than_the_cap_fail_instead_of_resolving_on_a_part(
    make_client: ClientFactory,
) -> None:
    reader = PaperlessTaxonomyReader(make_client(TagServer(total=MAX_ITEMS + 1)))

    with pytest.raises(PaperlessMcpError, match=f"more than {MAX_ITEMS} tags"):
        _ = await reader.list_taxonomy("tag")


async def test_a_kind_the_user_may_not_view_is_permission_denied(
    make_client: ClientFactory,
) -> None:
    def forbidden(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, json={"detail": "You do not have permission."})

    with pytest.raises(PermissionDenied):
        _ = await PaperlessTaxonomyReader(make_client(forbidden)).list_taxonomy("user")
