"""PaperlessDocumentWriter and PaperlessTaxonomyWriter on the Paperless 3.4.2 wire format."""

import json
from datetime import date
from email.parser import BytesParser
from email.policy import default
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from adapter.conftest import tag
from adapter.test_documents import DETAIL
from paperless_mcp.domain.documents import CustomFieldValue, DocumentPatch, DocumentUpload
from paperless_mcp.domain.errors import NotFound, PermissionDenied, UpstreamUnavailable, Validation
from paperless_mcp.paperless.documents import DETAIL_FIELDS, PaperlessDocumentWriter
from paperless_mcp.paperless.taxonomy import PaperlessTaxonomyWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

PDF = b"%PDF-1.4 upload"


class Patched:
    """Answers a PATCH with `body` and records method, path, query and JSON body."""

    def __init__(self, body: object = DETAIL, status: int = 200) -> None:
        self.body: object = body
        self.status: int = status
        self.sent: list[tuple[str, str, dict[str, str], object]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload = cast("object", json.loads(request.content))
        self.sent.append((request.method, request.url.path, dict(request.url.params), payload))
        return httpx2.Response(self.status, json=self.body)


async def test_only_the_fields_the_patch_sets_are_sent_and_the_document_comes_back(
    make_client: ClientFactory,
) -> None:
    server = Patched()

    record = await PaperlessDocumentWriter(make_client(server)).update_document(
        1, DocumentPatch(title="New title")
    )

    [(method, path, params, body)] = server.sent
    assert (method, path, body) == ("PATCH", "/api/documents/1/", {"title": "New title"})
    assert params == {"fields": DETAIL_FIELDS}
    assert "content" not in DETAIL_FIELDS.split(",")
    assert (record.id, record.title, record.tags) == (1, "e2e-public-invoice", (1, 3, 2))
    assert record.notes_count == 1
    assert record.user_can_change is None  # an update's answer does not carry it


async def test_every_field_has_its_paperless_name_and_none_clears(
    make_client: ClientFactory,
) -> None:
    server = Patched()
    patch = DocumentPatch(
        title="t",
        created=date(2026, 2, 3),
        archive_serial_number=None,
        owner=None,
        correspondent=None,
        document_type=4,
        storage_path=5,
        tags=(1, 2),
        custom_fields=(
            CustomFieldValue(field=4, value=True),
            CustomFieldValue(field=7, value="EUR12.50"),
            CustomFieldValue(field=8, value=[2]),
        ),
    )

    _ = await PaperlessDocumentWriter(make_client(server)).update_document(1, patch)

    [(_, _, _, body)] = server.sent
    assert body == {
        "title": "t",
        "created": "2026-02-03",
        "archive_serial_number": None,
        "owner": None,
        "correspondent": None,
        "document_type": 4,
        "storage_path": 5,
        "tags": [1, 2],
        "custom_fields": [
            {"field": 4, "value": True},
            {"field": 7, "value": "EUR12.50"},
            {"field": 8, "value": [2]},
        ],
    }


async def test_an_empty_list_is_sent_as_such(make_client: ClientFactory) -> None:
    server = Patched()

    _ = await PaperlessDocumentWriter(make_client(server)).update_document(
        1, DocumentPatch(tags=(), custom_fields=())
    )

    assert server.sent[0][3] == {"tags": [], "custom_fields": []}


@pytest.mark.parametrize("name", ["title", "created", "tags", "custom_fields"])
def test_a_field_that_cannot_be_cleared_cannot_be_set_to_none(name: str) -> None:
    with pytest.raises(ValueError, match=f"{name} cannot be cleared"):
        _ = DocumentPatch.model_validate({name: None})


@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (404, {"detail": "No Document matches the given query."}, NotFound, "No document 9"),
        (403, {"detail": "You do not have permission."}, PermissionDenied, "may not perform"),
        (
            400,
            {"tags": ['Invalid pk "9999" - object does not exist.']},
            Validation,
            'tags: Invalid pk "9999"',
        ),
        (
            400,
            {
                "custom_fields": {
                    "0": {"non_field_errors": ["Enter a valid integer."]},
                }
            },
            Validation,
            "Enter a valid integer",
        ),
    ],
)
async def test_paperless_errors_become_domain_errors(
    make_client: ClientFactory, status: int, body: Json, error: type[Exception], message: str
) -> None:
    server = Patched(body, status)

    with pytest.raises(error, match=message):
        _ = await PaperlessDocumentWriter(make_client(server)).update_document(
            9, DocumentPatch(title="x")
        )


async def test_a_missing_document_is_not_found_with_a_hint(make_client: ClientFactory) -> None:
    server = Patched({"detail": "No Document matches the given query."}, 404)

    with pytest.raises(NotFound) as raised:
        _ = await PaperlessDocumentWriter(make_client(server)).update_document(
            9, DocumentPatch(title="x")
        )

    assert raised.value.hint
    assert "search_documents" in raised.value.hint


class Created:
    """Answers POST /api/tags/ with a created tag and records the JSON body."""

    def __init__(self, status: int = 201, body: object = None) -> None:
        self.status: int = status
        self.body: object = body
        self.sent: list[tuple[str, str, object]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload = cast("Json", json.loads(request.content))
        self.sent.append((request.method, request.url.path, payload))
        created = {**tag(7, str(payload["name"])), "matching_algorithm": 0, "owner": 2}
        del created["document_count"]  # a create answer has no count, as on 3.4.2
        answer = self.body or created
        return httpx2.Response(self.status, json=answer)


async def test_a_tag_is_created_by_name_and_never_matches_automatically(
    make_client: ClientFactory,
) -> None:
    server = Created()

    created = await PaperlessTaxonomyWriter(make_client(server)).create_tag("Receipts")

    assert server.sent == [("POST", "/api/tags/", {"name": "Receipts", "matching_algorithm": 0})]
    assert (created.id, created.name, created.document_count) == (7, "Receipts", 0)
    assert created.colour == "#a6cee3"
    assert created.parent is None


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (400, {"error": "Object violates owner / name unique constraint"}, Validation),
        (400, {"name": ["This field may not be blank."]}, Validation),
        (403, {"detail": "You do not have permission to perform this action."}, PermissionDenied),
    ],
)
async def test_a_refused_tag_is_a_domain_error(
    make_client: ClientFactory, status: int, body: Json, error: type[Exception]
) -> None:
    with pytest.raises(error):
        _ = await PaperlessTaxonomyWriter(make_client(Created(status, body))).create_tag("x")


# The upload of PLMCP-18

TASK_ID = "9f1c0f0a-61d4-4c06-9c5e-6c7a4b1f3e21"


class Form:
    """The fields of a multipart request: text fields in order, and the one file."""

    def __init__(self, request: httpx2.Request) -> None:
        header = f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
        message = BytesParser(policy=default).parsebytes(header + request.content)
        self.fields: list[tuple[str, str]] = []
        self.files: list[tuple[str, str | None, str, bytes]] = []
        for part in message.iter_parts():
            name = str(part.get_param("name", header="content-disposition"))
            raw = cast("bytes", part.get_payload(decode=True))
            filename = part.get_filename()
            if filename is None:
                self.fields.append((name, raw.decode()))
            else:
                self.files.append((name, filename, part.get_content_type(), raw))

    def all(self, name: str) -> list[str]:
        return [value for key, value in self.fields if key == name]


class Uploaded:
    """Answers a POST with `body` and records it."""

    def __init__(self, body: object = TASK_ID, status: int = 200) -> None:
        self.body: object = body
        self.status: int = status
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body)


async def test_an_upload_is_one_multipart_post_that_returns_the_task_id(
    make_client: ClientFactory,
) -> None:
    server = Uploaded()
    upload = DocumentUpload(filename="Rechnung Käse.pdf", mime_type="application/pdf", content=PDF)

    task_id = await PaperlessDocumentWriter(make_client(server)).upload_document(upload)

    assert task_id == TASK_ID
    [request] = server.requests
    assert (request.method, request.url.path) == ("POST", "/api/documents/post_document/")
    form = Form(request)
    assert form.files == [("document", "Rechnung Käse.pdf", "application/pdf", PDF)]
    assert form.fields == []  # nothing the caller did not give


async def test_every_metadata_field_has_its_paperless_form_name(make_client: ClientFactory) -> None:
    server = Uploaded()
    upload = DocumentUpload(
        filename="a.pdf",
        mime_type="application/pdf",
        content=PDF,
        title="Invoice März",
        created=date(2026, 2, 3),
        correspondent=1,
        document_type=2,
        storage_path=3,
        tags=(4, 5),
        archive_serial_number=0,
        custom_fields=(
            CustomFieldValue(field=6, value="EUR12.50"),
            CustomFieldValue(field=7, value=True),
            CustomFieldValue(field=8, value=[2, 3]),
        ),
    )

    _ = await PaperlessDocumentWriter(make_client(server)).upload_document(upload)

    form = Form(server.requests[0])
    assert dict(form.fields) | {"tags": ""} == {
        "title": "Invoice März",
        "created": "2026-02-03",
        "correspondent": "1",
        "document_type": "2",
        "storage_path": "3",
        "archive_serial_number": "0",
        "custom_fields": '{"6":"EUR12.50","7":true,"8":[2,3]}',
        "tags": "",
    }
    assert form.all("tags") == ["4", "5"]  # a repeated field, as Paperless wants it


@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (400, {"document": ["File type application/octet-stream not supported"]}, Validation,
         "document: File type application/octet-stream not supported"),
        (400, {"tags": ['Invalid pk "9999" - does not exist.']}, Validation, "tags: Invalid pk"),
        (403, {"detail": "You do not have permission to perform this action."},
         PermissionDenied, "may not perform"),
    ],
)  # fmt: skip
async def test_paperless_errors_of_an_upload_become_domain_errors(
    make_client: ClientFactory, status: int, body: Json, error: type[Exception], message: str
) -> None:
    server = Uploaded(body, status)
    upload = DocumentUpload(filename="a.pdf", mime_type="application/pdf", content=PDF)

    with pytest.raises(error, match=message):
        _ = await PaperlessDocumentWriter(make_client(server)).upload_document(upload)


async def test_an_upload_is_sent_once_even_when_paperless_is_busy(
    make_client: ClientFactory,
) -> None:
    """A retried upload could create the document twice."""
    server = Uploaded({"detail": "busy"}, 503)
    upload = DocumentUpload(filename="a.pdf", mime_type="application/pdf", content=PDF)

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessDocumentWriter(make_client(server)).upload_document(upload)

    assert len(server.requests) == 1
