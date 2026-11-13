"""The version methods of PaperlessDocumentWriter on the Paperless 3.4.2 wire format (PLMCP-24)."""

import json
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from adapter.test_document_writes import TASK_ID, Form, Uploaded
from adapter.test_documents import DETAIL
from paperless_mcp.domain.errors import NotFound, PermissionDenied, UpstreamUnavailable, Validation
from paperless_mcp.domain.versions import VersionUpload
from paperless_mcp.paperless.documents import PaperlessDocumentReader, PaperlessDocumentWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

PDF = b"%PDF-1.4 version"
VERSION: Json = {
    "id": 7,
    "added": "2026-11-13T03:20:41.220934Z",
    "version_label": "second",
    "checksum": "83f3212313489c6c",
    "is_root": False,
}


class Answers:
    """Answers every request with `body` and `status`, and records the requests."""

    def __init__(
        self, body: object, status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self.body: object = body
        self.status: int = status
        self.headers: dict[str, str] = headers or {}
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.headers:
            return httpx2.Response(self.status, text=str(self.body), headers=self.headers)
        return httpx2.Response(self.status, json=self.body)

    def sent(self) -> object:
        [request] = self.requests
        return cast("object", json.loads(request.content))


async def test_a_version_upload_is_one_multipart_post_that_returns_the_task_id(
    make_client: ClientFactory,
) -> None:
    server = Uploaded()
    upload = VersionUpload(
        filename="Rechnung Käse v2.pdf", mime_type="application/pdf", content=PDF
    )

    task_id = await PaperlessDocumentWriter(make_client(server)).upload_document_version(5, upload)

    assert task_id == TASK_ID
    [request] = server.requests
    assert (request.method, request.url.path) == ("POST", "/api/documents/5/update_version/")
    form = Form(request)
    assert form.files == [("document", "Rechnung Käse v2.pdf", "application/pdf", PDF)]
    assert form.fields == []  # no label, nothing else


async def test_a_version_label_is_a_form_field(make_client: ClientFactory) -> None:
    server = Uploaded()
    upload = VersionUpload(
        filename="a.pdf", mime_type="application/pdf", content=PDF, label="final"
    )

    _ = await PaperlessDocumentWriter(make_client(server)).upload_document_version(5, upload)

    assert Form(server.requests[0]).fields == [("version_label", "final")]


@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (404, {"detail": "Not found."}, NotFound, "No document 5"),
        (403, {"detail": "You do not have permission."}, PermissionDenied, "may not perform"),
        (
            400,
            {"document": ["File type application/octet-stream not supported"]},
            Validation,
            "document: File type application/octet-stream not supported",
        ),
        (
            400,
            {"version_label": ["Ensure this field has no more than 64 characters."]},
            Validation,
            "version_label: Ensure this field",
        ),
    ],
)
async def test_paperless_errors_of_a_version_upload_become_domain_errors(
    make_client: ClientFactory, status: int, body: Json, error: type[Exception], message: str
) -> None:
    upload = VersionUpload(filename="a.pdf", mime_type="application/pdf", content=PDF)

    with pytest.raises(error, match=message):
        _ = await PaperlessDocumentWriter(
            make_client(Uploaded(body, status))
        ).upload_document_version(5, upload)


async def test_a_version_upload_is_sent_once_even_when_paperless_is_busy(
    make_client: ClientFactory,
) -> None:
    """A retried upload could add the version twice."""
    server = Uploaded({"detail": "busy"}, 503)
    upload = VersionUpload(filename="a.pdf", mime_type="application/pdf", content=PDF)

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessDocumentWriter(make_client(server)).upload_document_version(5, upload)

    assert len(server.requests) == 1


async def test_a_label_is_patched_as_json_and_the_version_comes_back(
    make_client: ClientFactory,
) -> None:
    server = Answers(VERSION)

    version = await PaperlessDocumentWriter(make_client(server)).set_version_label(5, 7, "second")

    [request] = server.requests
    assert (request.method, request.url.path) == ("PATCH", "/api/documents/5/versions/7/")
    assert server.sent() == {"version_label": "second"}
    assert (version.id, version.version_label, version.is_root) == (7, "second", False)
    assert version.checksum == "83f3212313489c6c"


async def test_a_label_is_cleared_with_null(make_client: ClientFactory) -> None:
    server = Answers({**VERSION, "version_label": None})

    version = await PaperlessDocumentWriter(make_client(server)).set_version_label(5, 7, None)

    assert server.sent() == {"version_label": None}
    assert version.version_label is None


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [
        (404, NotFound, "No version 7 exists on document 5"),
        (403, PermissionDenied, "may not perform"),
    ],
)
async def test_label_errors_become_domain_errors(
    make_client: ClientFactory, status: int, error: type[Exception], message: str
) -> None:
    server = Answers({"detail": "Not found."}, status)

    with pytest.raises(error, match=message):
        _ = await PaperlessDocumentWriter(make_client(server)).set_version_label(5, 7, "x")


async def test_a_missing_version_is_not_found_with_a_hint(make_client: ClientFactory) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await PaperlessDocumentWriter(
            make_client(Answers({"detail": "Not found."}, 404))
        ).set_version_label(5, 7, "x")

    assert raised.value.hint == "List the versions with list_document_versions."


async def test_a_version_is_deleted_and_the_current_one_comes_back(
    make_client: ClientFactory,
) -> None:
    server = Answers({"result": "OK", "current_version_id": 3})

    current = await PaperlessDocumentWriter(make_client(server)).delete_version(5, 7)

    [request] = server.requests
    assert (request.method, request.url.path) == ("DELETE", "/api/documents/5/versions/7/")
    assert current == 3


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [
        (404, NotFound, "No version 7 exists on document 5"),
        (403, PermissionDenied, "may not perform"),
    ],
)
async def test_delete_errors_become_domain_errors(
    make_client: ClientFactory, status: int, error: type[Exception], message: str
) -> None:
    server = Answers({"detail": "Not found."}, status)

    with pytest.raises(error, match=message):
        _ = await PaperlessDocumentWriter(make_client(server)).delete_version(5, 7)


async def test_refusing_the_root_is_a_validation_error(make_client: ClientFactory) -> None:
    server = Answers(
        "Cannot delete the root/original version. Delete the document instead.",
        400,
        {"Content-Type": "text/html"},
    )

    with pytest.raises(Validation):
        _ = await PaperlessDocumentWriter(make_client(server)).delete_version(5, 5)


async def test_a_delete_is_sent_once(make_client: ClientFactory) -> None:
    """The first delete may have worked when its answer was lost: a repeat would say 404."""
    server = Answers({"detail": "busy"}, 503)

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessDocumentWriter(make_client(server)).delete_version(5, 7)

    assert len(server.requests) == 1


async def test_a_merge_names_the_root_first_among_the_documents(
    make_client: ClientFactory,
) -> None:
    server = Answers({"result": "OK"})

    await PaperlessDocumentWriter(make_client(server)).merge_as_versions(5, (8, 9), None)

    [request] = server.requests
    assert (request.method, request.url.path) == ("POST", "/api/documents/merge_as_versions/")
    assert server.sent() == {"documents": [5, 8, 9], "root_document_id": 5}


async def test_a_merge_of_one_document_may_carry_a_label(make_client: ClientFactory) -> None:
    server = Answers({"result": "OK"})

    await PaperlessDocumentWriter(make_client(server)).merge_as_versions(5, (8,), "scan")

    assert server.sent() == {"documents": [5, 8], "root_document_id": 5, "version_label": "scan"}


@pytest.mark.parametrize(
    ("answer", "error", "message"),
    [
        (
            Answers(
                {"non_field_errors": ["Documents with existing versions cannot be merged."]}, 400
            ),
            Validation,
            "Documents with existing versions",
        ),
        (
            Answers(
                {"documents": ["Some documents in documents don't exist or were specified twice."]},
                400,
            ),
            Validation,
            "documents: Some documents",
        ),
        (
            Answers("Insufficient permissions", 403, {"Content-Type": "text/html"}),
            PermissionDenied,
            "may not",
        ),
    ],
)
async def test_merge_errors_become_domain_errors(
    make_client: ClientFactory, answer: Answers, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        await PaperlessDocumentWriter(make_client(answer)).merge_as_versions(5, (8,), None)


async def test_a_merge_is_sent_once(make_client: ClientFactory) -> None:
    server = Answers({"detail": "busy"}, 503)

    with pytest.raises(UpstreamUnavailable):
        await PaperlessDocumentWriter(make_client(server)).merge_as_versions(5, (8,), None)

    assert len(server.requests) == 1


async def test_the_versions_of_a_document_carry_their_checksum(make_client: ClientFactory) -> None:
    record = await PaperlessDocumentReader(make_client(Answers(DETAIL))).get_document(1)

    assert [v.checksum for v in record.versions] == ["c7d35dab"]


async def test_a_version_without_a_checksum_is_read_as_none(make_client: ClientFactory) -> None:
    [version] = cast("list[Json]", DETAIL["versions"])
    detail = {**DETAIL, "versions": [{k: v for k, v in version.items() if k != "checksum"}]}

    record = await PaperlessDocumentReader(make_client(Answers(detail))).get_document(1)

    assert [v.checksum for v in record.versions] == [None]
