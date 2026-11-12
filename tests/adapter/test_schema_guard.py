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


FILE_PATHS = ["/api/documents/1/thumb/", "/api/documents/1/download/", "/api/documents/1/preview/"]
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
        httpx2.Response(204, headers=FILE_HEADERS),
    ],
    ids=["empty", "json", "no content type", "no disposition", "odd disposition", "undocumented"],
)
def test_a_file_response_is_still_checked_for_what_it_must_have(response: httpx2.Response) -> None:
    with pytest.raises(SchemaViolationError):
        check_response(get("/api/documents/1/download/"), response)


def test_the_file_exemption_covers_only_the_file_endpoints() -> None:
    response = httpx2.Response(200, content=b"%PDF-1.4", headers=FILE_HEADERS)

    with pytest.raises(SchemaViolationError):
        check_response(get("/api/documents/1/metadata/"), response)


def test_a_hidden_document_is_answered_with_html_by_the_file_endpoints() -> None:
    response = httpx2.Response(
        403, text="Insufficient permissions", headers={"Content-Type": "text/html"}
    )

    check_response(get("/api/documents/1/thumb/"), response)
    check_response(get("/api/documents/1/download/"), response)
