"""PaperlessViewReader and PaperlessViewWriter on the Paperless 3.4.2 wire format."""

import json
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from paperless_mcp.domain.errors import NotFound, PaperlessMcpError, PermissionDenied, Validation
from paperless_mcp.domain.views import FilterRule, SavedView, ViewValues
from paperless_mcp.paperless.pagination import MAX_ITEMS
from paperless_mcp.paperless.views import PaperlessViewReader, PaperlessViewWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

# A row as 3.4.2 sends it without full_perms: no `permissions`, which the schema requires.
WIRE: Json = {
    "id": 2,
    "name": "plmcp25-probe",
    "icon": "funnel",
    "sort_field": "created",
    "sort_reverse": True,
    "filter_rules": [{"rule_type": 6, "value": "1"}, {"rule_type": 3, "value": None}],
    "page_size": 10,
    "display_mode": None,
    "display_fields": None,
    "owner": 2,
    "user_can_change": True,
}
VIEW = SavedView(
    id=2,
    name="plmcp25-probe",
    sort_field="created",
    sort_reverse=True,
    page_size=10,
    owner=2,
    user_can_change=True,
    filter_rules=(FilterRule(rule_type=6, value="1"), FilterRule(rule_type=3, value=None)),
)


class Recording:
    """Answers each request with the next scripted response and records method, path, body."""

    def __init__(self, *answers: httpx2.Response) -> None:
        self.answers: list[httpx2.Response] = list(answers)
        self.sent: list[tuple[str, str, Json | None, dict[str, str]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = cast("Json", json.loads(request.content)) if request.content else None
        self.sent.append((request.method, request.url.path, body, dict(request.url.params)))
        return self.answers.pop(0)


def page(*rows: Json, next_link: str | None = None, count: int | None = None) -> httpx2.Response:
    body = {
        "count": len(rows) if count is None else count,
        "next": next_link,
        "previous": None,
        "results": list(rows),
    }
    return httpx2.Response(200, json=body)


async def test_the_views_are_listed_with_their_rules_and_sort(make_client: ClientFactory) -> None:
    server = Recording(page(WIRE))

    found = await PaperlessViewReader(make_client(server)).list_saved_views()

    assert found == (VIEW,)
    [(method, path, _, params)] = server.sent
    assert (method, path) == ("GET", "/api/saved_views/")
    assert params == {"page": "1", "page_size": "100"}


async def test_every_page_of_views_is_read(make_client: ClientFactory) -> None:
    link = "http://paperless.test/api/saved_views/?page=2&page_size=100"
    second = {**WIRE, "id": 3, "name": "second"}
    server = Recording(page(WIRE, next_link=link, count=2), page(second, count=2))

    found = await PaperlessViewReader(make_client(server)).list_saved_views()

    assert [view.id for view in found] == [2, 3]


async def test_more_views_than_the_cap_fail_instead_of_resolving_on_a_part(
    make_client: ClientFactory,
) -> None:
    server = Recording(page(WIRE, count=MAX_ITEMS + 1))

    with pytest.raises(PaperlessMcpError, match=f"more than {MAX_ITEMS} saved views"):
        _ = await PaperlessViewReader(make_client(server)).list_saved_views()


async def test_a_user_without_the_view_permission_is_denied(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(403, json={"detail": "no"}))

    with pytest.raises(PermissionDenied):
        _ = await PaperlessViewReader(make_client(server)).list_saved_views()


async def test_one_view_is_read_by_id(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(200, json=WIRE))

    found = await PaperlessViewReader(make_client(server)).get_saved_view(2)

    assert found == VIEW
    assert server.sent == [("GET", "/api/saved_views/2/", None, {})]


async def test_a_missing_view_is_not_found_with_a_hint(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No SavedView matches."}))

    with pytest.raises(NotFound, match="No saved view 9") as raised:
        _ = await PaperlessViewReader(make_client(server)).get_saved_view(9)

    assert raised.value.hint == "Call list_saved_views to see the views you can use."


async def test_a_created_view_sends_its_settings_and_always_a_rule_list(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(201, json=WIRE))
    values = ViewValues(
        name="ignored",
        filter_rules=VIEW.filter_rules,
        sort_field="created",
        sort_reverse=True,
        page_size=10,
    )

    made = await PaperlessViewWriter(make_client(server)).create_saved_view("plmcp25-probe", values)

    assert made == VIEW
    assert server.sent == [
        (
            "POST",
            "/api/saved_views/",
            {
                "name": "plmcp25-probe",
                "filter_rules": [
                    {"rule_type": 6, "value": "1"},
                    {"rule_type": 3, "value": None},
                ],
                "sort_field": "created",
                "sort_reverse": True,
                "page_size": 10,
            },
            {},
        )
    ]


async def test_a_view_without_rules_is_created_with_an_empty_list(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(201, json={**WIRE, "filter_rules": []}))

    _ = await PaperlessViewWriter(make_client(server)).create_saved_view("x", ViewValues())

    assert server.sent[0][2] == {"name": "x", "filter_rules": []}


async def test_an_update_sends_only_what_was_set_and_can_clear_sort_and_page_size(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(200, json=WIRE), httpx2.Response(200, json=WIRE))
    writer = PaperlessViewWriter(make_client(server))

    _ = await writer.update_saved_view(2, ViewValues(name="renamed"))
    _ = await writer.update_saved_view(
        2, ViewValues(sort_field=None, page_size=None, sort_reverse=False)
    )

    assert [sent[:3] for sent in server.sent] == [
        ("PATCH", "/api/saved_views/2/", {"name": "renamed"}),
        (
            "PATCH",
            "/api/saved_views/2/",
            {"sort_field": None, "page_size": None, "sort_reverse": False},
        ),
    ]


async def test_an_update_can_replace_the_rules_by_an_empty_list(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(200, json={**WIRE, "filter_rules": []}))

    _ = await PaperlessViewWriter(make_client(server)).update_saved_view(
        2, ViewValues(filter_rules=())
    )

    assert server.sent[0][2] == {"filter_rules": []}


async def test_a_delete_sends_a_delete_and_expects_no_body(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(204))

    await PaperlessViewWriter(make_client(server)).delete_saved_view(2)

    assert server.sent == [("DELETE", "/api/saved_views/2/", None, {})]


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (400, {"error": "Object violates owner / name unique constraint"}, Validation),
        (400, {"filter_rules": {"0": {"rule_type": ['"99" is not a valid choice.']}}}, Validation),
        (403, {"detail": "no"}, PermissionDenied),
        (404, {"detail": "No SavedView matches the given query."}, NotFound),
    ],
)
async def test_failed_writes_name_the_cause(
    make_client: ClientFactory, status: int, body: Json, error: type[PaperlessMcpError]
) -> None:
    server = Recording(*[httpx2.Response(status, json=body)] * 3)
    writer = PaperlessViewWriter(make_client(server))

    with pytest.raises(error):
        _ = await writer.create_saved_view("x", ViewValues())
    with pytest.raises(error):
        _ = await writer.update_saved_view(2, ViewValues(name="y"))
    with pytest.raises(error):
        await writer.delete_saved_view(2)


async def test_a_rejected_rule_type_is_reported_with_its_position(
    make_client: ClientFactory,
) -> None:
    body = {"filter_rules": {"0": {"rule_type": ['"99" is not a valid choice.']}}}
    server = Recording(httpx2.Response(400, json=body))

    with pytest.raises(Validation, match=r"filter_rules\.0\.rule_type"):
        _ = await PaperlessViewWriter(make_client(server)).create_saved_view("x", ViewValues())


async def test_a_write_to_a_missing_view_names_its_id_and_says_what_to_do(
    make_client: ClientFactory,
) -> None:
    missing = httpx2.Response(404, json={"detail": "No SavedView matches the given query."})
    server = Recording(missing, missing)
    writer = PaperlessViewWriter(make_client(server))

    with pytest.raises(NotFound, match="No saved view 7 exists") as updated:
        _ = await writer.update_saved_view(7, ViewValues(name="y"))
    with pytest.raises(NotFound, match="No saved view 8 exists") as deleted:
        await writer.delete_saved_view(8)

    assert updated.value.hint == deleted.value.hint
    assert updated.value.hint == "Call list_saved_views to see the views you can use."
