"""PaperlessBulkWriter on the Paperless 3.4.2 wire format (schema-guarded)."""

import json
from typing import TYPE_CHECKING

import httpx2
import pytest

from adapter.conftest import streaming
from paperless_mcp.domain.bulk import (
    FieldsChange,
    ObjectPermissions,
    PermissionsChange,
    ReferenceChange,
    ReprocessChange,
    TagsChange,
    TrashDocuments,
)
from paperless_mcp.domain.errors import (
    PaperlessMcpError,
    PermissionDenied,
    UpstreamUnavailable,
    Validation,
)
from paperless_mcp.paperless.bulk import PaperlessBulkWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory
    from paperless_mcp.domain.bulk import BulkKind, DocumentChange

pytestmark = pytest.mark.anyio

type Json = dict[str, object]
OK: Json = {"result": "OK"}


class Recorded:
    """Answers every request with one body and records method, path, query and JSON body."""

    def __init__(self, body: object = OK, status: int = 200) -> None:
        self.body: object = body
        self.status: int = status
        self.sent: list[tuple[str, str, dict[str, str], object]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload: object = json.loads(request.content) if request.content else None
        self.sent.append((request.method, request.url.path, dict(request.url.params), payload))
        if isinstance(self.body, str):
            return httpx2.Response(self.status, text=self.body)
        return httpx2.Response(self.status, json=self.body)


def body(method: str, parameters: Json, documents: list[int] | None = None) -> Json:
    return {"documents": documents or [4, 7], "method": method, "parameters": parameters}


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            TagsChange(add=(3, 5), remove=(9,)),
            body("modify_tags", {"add_tags": [3, 5], "remove_tags": [9]}),
        ),
        (TagsChange(add=(3,)), body("modify_tags", {"add_tags": [3], "remove_tags": []})),
        (
            ReferenceChange(field="correspondent", value=12),
            body("set_correspondent", {"correspondent": 12}),
        ),
        (
            ReferenceChange(field="correspondent", value=None),
            body("set_correspondent", {"correspondent": None}),
        ),
        (
            ReferenceChange(field="document_type", value=2),
            body("set_document_type", {"document_type": 2}),
        ),
        (
            ReferenceChange(field="storage_path", value=None),
            body("set_storage_path", {"storage_path": None}),
        ),
        (
            # The dict form keeps the other fields; the list form would empty a field's value.
            FieldsChange(values={5: "x", 6: [1, 2]}, remove=(8,)),
            body(
                "modify_custom_fields",
                {"add_custom_fields": {"5": "x", "6": [1, 2]}, "remove_custom_fields": [8]},
            ),
        ),
        (
            FieldsChange(remove=(8,)),
            body("modify_custom_fields", {"add_custom_fields": {}, "remove_custom_fields": [8]}),
        ),
        (TrashDocuments(), body("delete", {})),
        (ReprocessChange(), body("reprocess", {})),
    ],
    ids=[
        "tags",
        "tags-add-only",
        "correspondent",
        "correspondent-cleared",
        "document-type",
        "storage-path-cleared",
        "custom-fields",
        "custom-fields-remove-only",
        "delete",
        "reprocess",
    ],
)
async def test_a_document_change_is_one_bulk_edit_request(
    make_client: ClientFactory, change: DocumentChange, expected: Json
) -> None:
    server = Recorded()

    await PaperlessBulkWriter(make_client(server)).bulk_edit_documents((4, 7), change)

    assert server.sent == [("POST", "/api/documents/bulk_edit/", {}, expected)]


async def test_an_answer_that_is_not_the_result_object_is_unexpected() -> None:
    client, _ = streaming(lambda _: httpx2.Response(200, json={"unexpected": True}))

    with pytest.raises(PaperlessMcpError, match="unexpected response"):
        await PaperlessBulkWriter(client).bulk_edit_documents((4,), TrashDocuments())


async def test_a_refused_document_change_says_nothing_was_changed(
    make_client: ClientFactory,
) -> None:
    # Paperless checks every document first and answers a plain-text 403 for the whole batch.
    server = Recorded("Insufficient permissions", 403)

    with pytest.raises(PermissionDenied) as raised:
        await PaperlessBulkWriter(make_client(server)).bulk_edit_documents((4,), TrashDocuments())

    assert "nothing was changed" in raised.value.message
    assert "change permission" in (raised.value.hint or "")
    assert "owner" in (raised.value.hint or "")


async def test_rejected_values_are_a_validation_error_with_the_reason(
    make_client: ClientFactory,
) -> None:
    server = Recorded(
        {"add_custom_fields": {"111": {"non_field_errors": ["Enter a valid integer."]}}}, 400
    )
    change = FieldsChange(values={111: "abc"})

    with pytest.raises(Validation, match="Enter a valid integer"):
        await PaperlessBulkWriter(make_client(server)).bulk_edit_documents((4,), change)


async def test_a_failing_bulk_edit_is_never_retried() -> None:
    # A repeated tag change is harmless but a repeated reprocess queues more work: ask once.
    calls: list[str] = []

    def failing(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(503)

    client, _ = streaming(failing)

    with pytest.raises(UpstreamUnavailable):
        await PaperlessBulkWriter(client).bulk_edit_documents((4,), ReprocessChange())

    assert calls == ["/api/documents/bulk_edit/"]


@pytest.mark.parametrize(
    ("kind", "object_type"),
    [
        ("tag", "tags"),
        ("correspondent", "correspondents"),
        ("document_type", "document_types"),
        ("storage_path", "storage_paths"),
    ],
)
async def test_taxonomy_items_are_deleted_by_object_type(
    make_client: ClientFactory, kind: BulkKind, object_type: str
) -> None:
    server = Recorded()

    await PaperlessBulkWriter(make_client(server)).delete_taxonomy_items(kind, (3, 8))

    expected = {"objects": [3, 8], "object_type": object_type, "operation": "delete"}
    assert server.sent == [("POST", "/api/bulk_edit_objects/", {}, expected)]


async def test_permissions_are_sent_with_owner_only_when_it_was_set(
    make_client: ClientFactory,
) -> None:
    server = Recorded()
    permissions = ObjectPermissions(view_users=(3,), view_groups=(1,), change_groups=(1,))
    change = PermissionsChange(permissions=permissions, merge=True)

    await PaperlessBulkWriter(make_client(server)).set_taxonomy_permissions("tag", (5,), change)

    expected = {
        "objects": [5],
        "object_type": "tags",
        "operation": "set_permissions",
        "permissions": {
            "view": {"users": [3], "groups": [1]},
            "change": {"users": [], "groups": [1]},
        },
        "merge": True,
    }
    assert server.sent == [("POST", "/api/bulk_edit_objects/", {}, expected)]


async def test_a_cleared_owner_is_sent_as_null_but_a_left_out_owner_is_not_sent(
    make_client: ClientFactory,
) -> None:
    server = Recorded()
    writer = PaperlessBulkWriter(make_client(server))

    await writer.set_taxonomy_permissions("correspondent", (5,), PermissionsChange())
    await writer.set_taxonomy_permissions("correspondent", (5,), PermissionsChange(owner=None))

    base: Json = {"objects": [5], "object_type": "correspondents", "operation": "set_permissions"}
    assert [sent[3] for sent in server.sent] == [
        {**base, "merge": False},
        {**base, "merge": False, "owner": None},
    ]


async def test_an_owner_is_sent_as_the_user_id(make_client: ClientFactory) -> None:
    server = Recorded()

    await PaperlessBulkWriter(make_client(server)).set_taxonomy_permissions(
        "storage_path", (5,), PermissionsChange(owner=3)
    )

    sent = server.sent[0][3]
    assert isinstance(sent, dict)
    assert (sent["owner"], sent["object_type"], "permissions" in sent) == (
        3,
        "storage_paths",
        False,
    )


async def test_refused_object_changes_say_nothing_was_changed(make_client: ClientFactory) -> None:
    server = Recorded("Insufficient permissions", 403)

    with pytest.raises(PermissionDenied) as raised:
        await PaperlessBulkWriter(make_client(server)).delete_taxonomy_items("tag", (1,))

    assert "nothing was changed" in raised.value.message
    assert "owner" in (raised.value.hint or "")
