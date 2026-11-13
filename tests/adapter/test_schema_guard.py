"""The schema guard rejects what the vendored schema does not allow (it must bite)."""

import httpx2
import pytest

from adapter.conftest import BASE_URL, ClientFactory, tag
from adapter.schema_guard import SchemaViolationError, check_request, check_response

HEADERS = {"Accept": "application/json; version=10", "Authorization": "Token t"}
TAG_PAGE = {"count": 1, "next": None, "previous": None, "results": [tag(1, "inbox")]}


def get(path: str, **params: str) -> httpx2.Request:
    return httpx2.Request("GET", BASE_URL + path, params=params, headers=HEADERS)


def test_accepts_a_documented_request_and_response() -> None:
    request = get("/api/tags/", page="2", page_size="25")
    check_request(request)
    check_response(request, httpx2.Response(200, json=TAG_PAGE))


@pytest.mark.parametrize(
    "request_",
    [
        pytest.param(get("/api/no_such_endpoint/"), id="unknown path"),
        pytest.param(
            httpx2.Request("TRACE", BASE_URL + "/api/tags/", headers=HEADERS), id="unknown method"
        ),
        pytest.param(get("/api/tags/", page="two"), id="wrong parameter type"),
        pytest.param(get("/api/tags/", name__contains="x"), id="undeclared parameter"),
        pytest.param(get("/api/documents/", more_like="1"), id="undeclared, not an exception"),
        pytest.param(httpx2.Request("GET", BASE_URL + "/api/tags/"), id="no credentials"),
        pytest.param(
            httpx2.Request("POST", BASE_URL + "/api/tags/", json={"title": "x"}, headers=HEADERS),
            id="body without the required name",
        ),
    ],
)
def test_rejects_requests_outside_the_schema(request_: httpx2.Request) -> None:
    with pytest.raises(SchemaViolationError):
        check_request(request_)


def test_listed_exceptions_pass() -> None:
    check_request(get("/api/documents/", more_like_id="1"))


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, json={"count": "one", "results": []}),
        httpx2.Response(200, json={"count": 1, "results": [{"id": 1}]}),
        httpx2.Response(201, json=TAG_PAGE),
        httpx2.Response(404, json={"detail": 404}),
        httpx2.Response(404, text="<html>Not Found</html>"),
    ],
    ids=["wrong type", "incomplete item", "undocumented status", "not DRF", "not JSON"],
)
def test_rejects_responses_outside_the_schema(response: httpx2.Response) -> None:
    with pytest.raises(SchemaViolationError):
        check_response(get("/api/tags/"), response)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(404, json={"detail": "No Document matches the given query."}),
        httpx2.Response(400, json={"name": ["This field is required."]}),
        httpx2.Response(400, json={"custom_fields": [{"value": ["Not a valid date."]}]}),
        httpx2.Response(503, text="<html>Service Unavailable</html>"),
    ],
)
def test_accepts_drf_errors_and_any_server_error(response: httpx2.Response) -> None:
    check_response(get("/api/documents/1/"), response)


@pytest.mark.anyio
async def test_a_wrong_request_fails_the_adapter_call(make_client: ClientFactory) -> None:
    client = make_client(lambda _: httpx2.Response(200, json=TAG_PAGE))

    with pytest.raises(SchemaViolationError, match="undeclared query parameters"):
        _ = await client.request("GET", "/api/tags/", params={"nmae__icontains": "x"})


@pytest.mark.anyio
async def test_a_wrong_recorded_response_fails_the_adapter_call(make_client: ClientFactory) -> None:
    client = make_client(lambda _: httpx2.Response(200, json={"id": 1, "name": "inbox"}))

    with pytest.raises(SchemaViolationError, match="response does not match"):
        _ = await client.request("GET", "/api/tags/1/")


@pytest.mark.parametrize(
    ("path", "response"),
    [
        pytest.param("/api/status/", httpx2.Response(200, json={"tasks": {}}), id="status 200"),
        pytest.param("/api/status/", httpx2.Response(403, text="Forbidden"), id="status 403"),
        pytest.param(
            "/api/ui_settings/", httpx2.Response(200, json={"user": {}}), id="ui settings 200"
        ),
    ],
)
def test_listed_response_deviations_pass(path: str, response: httpx2.Response) -> None:
    check_response(get(path), response)


def test_a_response_deviation_covers_only_its_status() -> None:
    with pytest.raises(SchemaViolationError):
        check_response(get("/api/status/"), httpx2.Response(404, text="<html>Not Found</html>"))


FILE_PATHS = ["/api/documents/1/thumb/", "/api/documents/1/download/"]
FILE_HEADERS = {
    "Content-Type": "application/pdf",
    "Content-Disposition": 'inline; filename="a.pdf"',
}


@pytest.mark.parametrize("path", FILE_PATHS)
def test_a_file_response_passes_although_the_schema_says_json(path: str) -> None:
    response = httpx2.Response(200, content=b"%PDF-1.4", headers=FILE_HEADERS)

    check_response(get(path), response)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, content=b"", headers=FILE_HEADERS),
        httpx2.Response(
            200, content=b"{}", headers={**FILE_HEADERS, "Content-Type": "application/json"}
        ),
        httpx2.Response(200, content=b"x", headers={"Content-Disposition": "inline"}),
        httpx2.Response(200, content=b"x", headers={"Content-Type": "application/pdf"}),
        httpx2.Response(200, content=b"x", headers={**FILE_HEADERS, "Content-Disposition": "x"}),
        httpx2.Response(
            200, content=b"{}", headers={**FILE_HEADERS, "Content-Type": "Application/JSON; x=1"}
        ),
        httpx2.Response(204, headers=FILE_HEADERS),
    ],
    ids=[
        "empty",
        "json",
        "no content type",
        "no disposition",
        "odd disposition",
        "json in other case",
        "undocumented",
    ],
)
def test_a_file_response_is_still_checked_for_what_it_must_have(response: httpx2.Response) -> None:
    with pytest.raises(SchemaViolationError):
        check_response(get("/api/documents/1/download/"), response)


@pytest.mark.parametrize("path", ["/api/documents/1/metadata/", "/api/documents/1/preview/"])
def test_the_file_exemption_covers_only_the_endpoints_the_adapter_uses(path: str) -> None:
    response = httpx2.Response(200, content=b"%PDF-1.4", headers=FILE_HEADERS)

    with pytest.raises(SchemaViolationError):
        check_response(get(path), response)


def test_a_hidden_document_is_answered_with_html_by_the_file_endpoints() -> None:
    response = httpx2.Response(
        403, text="Insufficient permissions", headers={"Content-Type": "text/html"}
    )

    check_response(get("/api/documents/1/thumb/"), response)
    check_response(get("/api/documents/1/download/"), response)


def patch(body: object) -> httpx2.Request:
    return httpx2.Request(
        "PATCH", BASE_URL + "/api/documents/1/", json=body, params={"fields": "id"}, headers=HEADERS
    )


def test_a_document_patch_may_carry_boolean_and_id_list_custom_field_values() -> None:
    check_request(
        patch(
            {
                "title": "t",
                "custom_fields": [
                    {"field": 1, "value": True},
                    {"field": 2, "value": [3, 4]},
                    {"field": 3, "value": "EUR12.50"},
                ],
            }
        )
    )


@pytest.mark.parametrize(
    "body",
    [
        {"title": 5},
        {"tags": "one"},
        {"custom_fields": [{"field": "one", "value": True}]},
        {"custom_fields": [{"value": True}]},
        {"custom_fields": [{"field": 1, "value": None}]},
        {"custom_fields": [{"field": 1, "value": {"nested": 1}}, {"field": 2}]},
    ],
)
def test_the_rest_of_a_document_patch_is_still_checked(body: object) -> None:
    with pytest.raises(SchemaViolationError):
        check_request(patch(body))


def upload(
    fields: dict[str, str | list[str]], *, document: bool = True, headers: dict[str, str] = HEADERS
) -> httpx2.Request:
    """A multipart upload; `document=False` leaves out the one required part."""
    files = (
        {"document": ("a.pdf", b"%PDF-1.4", "application/pdf")} if document else {"t": ("", b"")}
    )
    request = httpx2.Request(
        "POST",
        BASE_URL + "/api/documents/post_document/",
        data=fields,
        files=files,
        headers=headers,
    )
    _ = request.read()
    return request


def test_a_form_upload_with_plain_values_passes() -> None:
    check_request(
        upload({"title": "t", "created": "2026-02-03", "tags": ["1", "2"], "correspondent": "3"})
    )
    check_request(upload({"created": "2026-02-03 06:15:00+02:00", "archive_serial_number": "0"}))


@pytest.mark.parametrize(
    "request_",
    [
        pytest.param(upload({"title": "t", "colour": "red"}), id="undeclared field"),
        pytest.param(upload({"title": "t"}, document=False), id="no document"),
        pytest.param(upload({"correspondent": "one"}), id="integer that is not one"),
        pytest.param(upload({"tags": ["1", "x"]}), id="a tag that is not an integer"),
        pytest.param(upload({"archive_serial_number": "-1"}), id="below the minimum"),
        pytest.param(upload({"archive_serial_number": "4294967296"}), id="above the maximum"),
        pytest.param(upload({"created": "yesterday"}), id="not a date"),
        pytest.param(upload({"title": ""}), id="an empty title"),
        pytest.param(upload({"custom_fields": "{not json"}), id="custom fields that are no JSON"),
        pytest.param(upload({"custom_fields": '{"1": '}), id="custom fields cut short"),
        pytest.param(upload({"title": "t"}, headers={"Accept": HEADERS["Accept"]}), id="no token"),
    ],
)
def test_a_form_upload_outside_the_schema_is_rejected(request_: httpx2.Request) -> None:
    with pytest.raises(SchemaViolationError):
        check_request(request_)


def test_a_json_body_for_the_upload_is_rejected() -> None:
    request = httpx2.Request(
        "POST", BASE_URL + "/api/documents/post_document/", json={"title": "t"}, headers=HEADERS
    )

    with pytest.raises(SchemaViolationError, match="not multipart"):
        check_request(request)


def test_the_document_must_be_a_file_part_not_a_text_field() -> None:
    request = httpx2.Request(
        "POST",
        BASE_URL + "/api/documents/post_document/",
        data={"document": "just text"},
        files={"other": ("", b"")},
        headers=HEADERS,
    )
    _ = request.read()

    with pytest.raises(SchemaViolationError, match="document is not a file"):
        check_request(request)


def test_valid_custom_fields_pass() -> None:
    check_request(upload({"custom_fields": '{"1": "a", "2": [3, 4], "3": true}'}))
    check_request(upload({"custom_fields": "[1, 2]"}))


# The version endpoints of PLMCP-24

HTML = {"Content-Type": "text/html"}


def version_form(
    fields: dict[str, str], *, document: bool = True, path: str = "/api/documents/1/update_version/"
) -> httpx2.Request:
    """A multipart version upload; `document=False` leaves out the one required part."""
    files = (
        {"document": ("v2.pdf", b"%PDF-1.4", "application/pdf")} if document else {"t": ("", b"")}
    )
    request = httpx2.Request("POST", BASE_URL + path, data=fields, files=files, headers=HEADERS)
    _ = request.read()
    return request


def test_a_version_upload_form_with_and_without_a_label_passes() -> None:
    check_request(version_form({}))
    check_request(version_form({"version_label": "final"}))
    check_request(version_form({"version_label": "x" * 64}))


@pytest.mark.parametrize(
    "request_",
    [
        pytest.param(version_form({"title": "t"}), id="undeclared field"),
        pytest.param(version_form({}, document=False), id="no document"),
        pytest.param(version_form({"version_label": "x" * 65}), id="a label over 64 characters"),
        pytest.param(
            httpx2.Request(
                "POST",
                BASE_URL + "/api/documents/1/update_version/",
                json={"version_label": "x"},
                headers=HEADERS,
            ),
            id="JSON instead of a form",
        ),
    ],
)
def test_a_version_upload_form_outside_the_schema_is_rejected(request_: httpx2.Request) -> None:
    with pytest.raises(SchemaViolationError):
        check_request(request_)


def test_the_label_patch_and_the_merge_body_are_checked_against_their_schemas() -> None:
    label = httpx2.Request(
        "PATCH",
        BASE_URL + "/api/documents/1/versions/2/",
        json={"version_label": None},
        headers=HEADERS,
    )
    merge = httpx2.Request(
        "POST",
        BASE_URL + "/api/documents/merge_as_versions/",
        json={"documents": [1, 2], "root_document_id": 1, "version_label": "x"},
        headers=HEADERS,
    )
    check_request(label)
    check_request(merge)
    for bad in (
        httpx2.Request(
            "PATCH",
            BASE_URL + "/api/documents/1/versions/2/",
            json={"version_label": "x" * 65},
            headers=HEADERS,
        ),
        httpx2.Request(
            "POST",
            BASE_URL + "/api/documents/merge_as_versions/",
            json={"documents": [1, 2]},
            headers=HEADERS,
        ),
    ):
        with pytest.raises(SchemaViolationError):
            check_request(bad)


def test_deleting_a_version_is_answered_200_with_the_current_version_not_204() -> None:
    request = httpx2.Request("DELETE", BASE_URL + "/api/documents/1/versions/2/", headers=HEADERS)

    check_response(request, httpx2.Response(200, json={"result": "OK", "current_version_id": 1}))
    check_response(request, httpx2.Response(400, text="Cannot delete the root", headers=HTML))


def test_a_viewer_merging_is_answered_with_html() -> None:
    request = httpx2.Request(
        "POST",
        BASE_URL + "/api/documents/merge_as_versions/",
        json={"documents": [1, 2], "root_document_id": 1},
        headers=HEADERS,
    )

    check_response(request, httpx2.Response(403, text="Insufficient permissions", headers=HTML))
