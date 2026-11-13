"""Schema guard: a MockTransport that checks requests and responses against the vendored schema.

Adapter tests build their httpx2 client on `SchemaGuardedTransport(handler)`. Each request the
adapter sends and each response the handler returns is validated with openapi-core against
docs/vendor/paperless/openapi.json (Paperless 3.4.2); any mismatch raises `SchemaViolationError`,
which fails the test. On top of what openapi-core checks, query parameters the operation does not
declare are rejected, so a misspelt filter cannot pass silently.

Deliberate deviations from the schema are listed below, each with its reason.
"""

import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs

import httpx2
from openapi_core import Config, OpenAPI
from openapi_core.datatypes import RequestParameters
from openapi_core.templating.paths.exceptions import PathError
from openapi_core.templating.paths.finders import APICallPathFinder
from werkzeug.datastructures import Headers, ImmutableMultiDict

if TYPE_CHECKING:
    from email.message import MIMEPart

    from openapi_core.templating.datatypes import TemplateResult

SCHEMA_PATH = Path(__file__).parents[2] / "docs" / "vendor" / "paperless" / "openapi.json"

# Query parameters an operation accepts although the generated schema omits them:
# (method, path template) -> {parameter: reason}.
UNDECLARED_QUERY_PARAMETERS: Mapping[tuple[str, str], Mapping[str, str]] = {
    ("GET", "/api/documents/"): {
        "more_like_id": "documented in api.md, missing from the generated parameters (PLMCP-3)",
        "all": "documented in api.md, missing from the generated parameters (PLMCP-3)",
    },
    ("PATCH", "/api/documents/{id}/"): {
        "fields": (
            "the update serializer honours it like the retrieve one; without it the answer "
            "carries the whole OCR text (PLMCP-16)"
        ),
    },
}
# Multipart requests: (method, path template) -> reason. openapi-core reads every field of a form
# as text and does not convert it to the declared type, so it fails integers; Paperless takes a
# plain date for a `date-time` field. Their fields are checked by `_form_errors` instead: declared
# names, required ones present, integers and date(-time)s that parse.
FORM_REQUESTS: Mapping[tuple[str, str], str] = {
    ("POST", "/api/documents/post_document/"): (
        "form values are text, and `created` accepts a plain date (PLMCP-18)"
    ),
    ("POST", "/api/documents/{id}/update_version/"): (
        "openapi-core lets a form carry fields the schema does not declare (PLMCP-24)"
    ),
}
# Responses whose body the generated schema describes wrongly: (method, path template, status) ->
# reason. Their bodies are not checked; the live contract suite (tests/contract) checks the
# adapter against the real ones instead.
WRONG_RESPONSE_SCHEMAS: Mapping[tuple[str, str, int], str] = {
    ("DELETE", "/api/documents/{id}/versions/{version_id}/", 200): (
        "3.4.2 answers {result, current_version_id}; the schema documents 204 without a body "
        "(PLMCP-24)"
    ),
    ("DELETE", "/api/documents/{id}/versions/{version_id}/", 400): (
        "deleting the root version is answered with HttpResponseBadRequest, text/html (PLMCP-24)"
    ),
    ("POST", "/api/documents/merge_as_versions/", 403): (
        "a user without the change permission is answered with HttpResponseForbidden, text/html "
        "(PLMCP-24)"
    ),
    ("GET", "/api/documents/", 200): (
        "`fields=` drops properties the schema requires, and custom field values can be booleans "
        "and id lists, which the value's oneOf omits (PLMCP-10)"
    ),
    ("GET", "/api/documents/{id}/", 200): (
        "`fields=` drops properties the schema requires, and custom field values can be booleans "
        "and id lists, which the value's oneOf omits (PLMCP-11)"
    ),
    ("PATCH", "/api/documents/{id}/", 200): (
        "`fields=` drops properties the schema requires, and custom field values can be booleans "
        "and id lists, which the value's oneOf omits (PLMCP-16)"
    ),
    ("POST", "/api/tags/", 201): (
        "3.4.2 answers a created tag without the `document_count` the schema requires (PLMCP-16)"
    ),
    ("POST", "/api/correspondents/", 201): (
        "3.4.2 answers a created correspondent without `document_count`, `last_correspondence` "
        "and `permissions`, which the schema requires (PLMCP-19)"
    ),
    ("POST", "/api/document_types/", 201): (
        "3.4.2 answers a created document type without `document_count` and `permissions`, "
        "which the schema requires (PLMCP-19)"
    ),
    ("POST", "/api/storage_paths/", 201): (
        "3.4.2 answers a created storage path without the `document_count` the schema requires "
        "(PLMCP-19)"
    ),
    ("POST", "/api/custom_fields/", 201): (
        "3.4.2 answers a created custom field without the `document_count` the schema requires "
        "(PLMCP-19)"
    ),
    ("PATCH", "/api/correspondents/{id}/", 200): (
        "3.4.2 answers an updated correspondent without `last_correspondence` and `permissions`, "
        "which the schema requires (PLMCP-19)"
    ),
    ("PATCH", "/api/document_types/{id}/", 200): (
        "3.4.2 answers an updated document type without the `permissions` the schema requires "
        "(PLMCP-19)"
    ),
    ("GET", "/api/correspondents/{id}/", 200): (
        "3.4.2 answers a correspondent without `last_correspondence` and `permissions`, which "
        "the schema requires (PLMCP-19)"
    ),
    ("GET", "/api/document_types/{id}/", 200): (
        "3.4.2 answers a document type without the `permissions` the schema requires (PLMCP-19)"
    ),
    ("GET", "/api/documents/{id}/history/", 200): (
        "3.4.2 answers a bare list, not the paginated envelope of the schema, and a null actor "
        "for system changes (PLMCP-11)"
    ),
    ("GET", "/api/documents/{id}/thumb/", 403): (
        "a document the user may not see is answered with HttpResponseForbidden, text/html "
        "(PLMCP-12)"
    ),
    ("GET", "/api/documents/{id}/download/", 403): (
        "a document the user may not see is answered with HttpResponseForbidden, text/html "
        "(PLMCP-12)"
    ),
    ("GET", "/api/documents/", 403): (
        "an unknown or hidden more_like_id is answered with HttpResponseForbidden, text/html "
        "(PLMCP-10)"
    ),
    ("POST", "/api/documents/chat/", 200): (
        "the chat streams text/event-stream, plain text then a metadata trailer; the schema "
        "describes a JSON ChatStreaming object (PLMCP-20)"
    ),
    ("POST", "/api/documents/chat/", 400): (
        "AI switched off and an unknown document are answered with HttpResponseBadRequest, "
        "text/html; the schema documents no error response (PLMCP-20)"
    ),
    ("POST", "/api/documents/chat/", 403): (
        "a document the user may not see is answered with HttpResponseForbidden, text/html "
        "(PLMCP-20)"
    ),
    ("POST", "/api/documents/bulk_edit/", 403): (
        "BulkEditView answers HttpResponseForbidden('Insufficient permissions'), text/html "
        "(PLMCP-22)"
    ),
    ("POST", "/api/bulk_edit_objects/", 403): (
        "BulkEditObjectsView answers HttpResponseForbidden('Insufficient permissions'), "
        "text/html (PLMCP-22)"
    ),
    ("GET", "/api/status/", 200): (
        "3.4.2 nests index, classifier and sanity check under `tasks` and sends null errors; "
        "the schema has them top-level and non-nullable (PLMCP-7)"
    ),
    ("GET", "/api/status/", 403): (
        "SystemStatusView answers HttpResponseForbidden('Insufficient permissions'), text/html "
        "(PLMCP-7)"
    ),
    ("GET", "/api/ui_settings/", 200): (
        "3.4.2 answers {user, settings, permissions}; the schema has {id, settings} (PLMCP-7)"
    ),
}
# Endpoints that answer a file: (method, path template) -> reason. The schema declares their 200 as
# application/json (format binary), but Paperless sends the file with its own content type, so
# openapi-core cannot match it. Their 200 is checked for what every file response has instead: a
# non-JSON content type, an inline or attachment Content-Disposition and a body.
FILE_RESPONSES: Mapping[tuple[str, str], str] = {
    ("GET", "/api/documents/{id}/thumb/"): "the thumbnail is image/webp (PLMCP-12)",
    ("GET", "/api/documents/{id}/download/"): "the original or archive file as stored (PLMCP-12)",
}
# Requests whose custom field values the schema describes too narrowly (the value's oneOf omits
# booleans and id lists): (method, path template) -> reason. Such values are replaced by a
# string before the body is validated; the rest of the body is checked as usual.
NARROW_VALUE_REQUESTS: Mapping[tuple[str, str], str] = {
    ("PATCH", "/api/documents/{id}/"): (
        "custom field values can be booleans and id lists, which the value's oneOf omits (PLMCP-16)"
    ),
}
# drf-spectacular documents no error responses. Paperless (Django REST framework) answers 4xx with
# {"detail": "..."} or a map of field names to message lists; such bodies are checked for that shape
# instead. 5xx responses are not checked: proxies and the web server produce them, not the API.
DRF_ERROR_STATUSES = range(400, 500)
UNCHECKED_STATUSES = range(500, 600)

type Handler = Callable[[httpx2.Request], httpx2.Response]
type Json = dict[str, object]

# The vendored schema breaks one rule of the spec validator (a `default: ""` below `minLength: 1`)
# that does not matter for validating messages. None skips the spec check (OpenAPI.check_spec);
# the annotation of `spec_validator_cls` omits None.
_CONFIG = Config(spec_validator_cls=None)  # pyright: ignore[reportArgumentType] -- see above
_OPENAPI = OpenAPI.from_file_path(str(SCHEMA_PATH), config=_CONFIG)
_DOCUMENT = cast("Json", json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
_PATHS = cast("dict[str, Json]", _DOCUMENT["paths"])
_SCHEMAS = cast("dict[str, Json]", cast("Json", _DOCUMENT["components"])["schemas"])


class SchemaViolationError(AssertionError):
    """A request or response does not match the vendored Paperless schema."""


class _Request:
    """An httpx2 request in the shape of openapi-core's `Request` protocol."""

    def __init__(self, request: httpx2.Request, body: bytes | None = None) -> None:
        self._request: httpx2.Request = request
        self._body: bytes | None = body or request.content or None
        self.parameters: RequestParameters = RequestParameters(
            query=ImmutableMultiDict(parse_qs(request.url.query.decode(), keep_blank_values=True)),
            header=Headers(dict(request.headers)),
        )

    @property
    def host_url(self) -> str:
        return f"{self._request.url.scheme}://{self._request.url.netloc.decode()}"

    @property
    def path(self) -> str:
        return self._request.url.path

    @property
    def method(self) -> str:
        return self._request.method.lower()

    @property
    def body(self) -> bytes | None:
        return self._body

    @property
    def content_type(self) -> str:
        return self._request.headers.get("Content-Type", "")


class _Response:
    """An httpx2 response in the shape of openapi-core's `Response` protocol."""

    def __init__(self, response: httpx2.Response) -> None:
        self._response: httpx2.Response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Headers:
        return Headers(dict(self._response.headers))

    @property
    def content_type(self) -> str:
        return self._response.headers.get("Content-Type", "")

    @property
    def data(self) -> bytes | None:
        return self._response.content or None


def _fail(request: httpx2.Request, what: str, errors: list[Exception]) -> SchemaViolationError:
    details = "; ".join(f"{error!r} caused by {error.__cause__!r}" for error in errors)
    return SchemaViolationError(f"{request.method} {request.url.path}: {what}: {details}")


def _template(request: httpx2.Request) -> str:
    url = str(request.url.copy_with(query=None))
    found = APICallPathFinder(_OPENAPI.spec).find(request.method.lower(), url)
    return cast("TemplateResult", found.path_result).pattern


def _undeclared_query_parameters(request: httpx2.Request) -> set[str]:
    template = _template(request)
    path_item = _PATHS[template]
    operation = cast("Json", path_item[request.method.lower()])
    declared = cast("list[Json]", path_item.get("parameters", [])) + cast(
        "list[Json]", operation.get("parameters", [])
    )
    allowed = {str(p["name"]) for p in declared if p["in"] == "query"}
    allowed |= UNDECLARED_QUERY_PARAMETERS.get((request.method, template), {}).keys()
    return {name for name, _ in request.url.params.multi_items()} - allowed


def _json_or_none(message: httpx2.Request | httpx2.Response) -> object:
    try:
        return cast("object", json.loads(message.content))
    except ValueError:
        return None


def _is_drf_error(body: object) -> bool:
    """True for a DRF error body: strings nested in objects and arrays."""
    if isinstance(body, str):
        return True
    if isinstance(body, list):
        return all(_is_drf_error(item) for item in cast("list[object]", body))
    if isinstance(body, dict):
        return all(_is_drf_error(item) for item in cast("Json", body).values())
    return False


def _narrowed(request: httpx2.Request) -> bytes | None:
    """The body of a request listed in `NARROW_VALUE_REQUESTS` with its values made strings."""
    try:
        listed = (request.method, _template(request)) in NARROW_VALUE_REQUESTS
    except PathError:  # no such operation: the schema check reports it
        return None
    if not listed or not request.content:
        return None
    body = _json_or_none(request)
    if not isinstance(body, dict):
        return None
    fields = cast("Json", body).get("custom_fields")
    if isinstance(fields, list):
        for item in cast("list[object]", fields):
            if isinstance(item, dict) and "value" in item:
                value = cast("Json", item)["value"]
                if isinstance(value, bool | list):
                    cast("Json", item)["value"] = "value"
    return json.dumps(body).encode()


def _field_problems(name: str, part: MIMEPart, kind: Json) -> list[str]:
    """What is wrong with one part of a form, against its property of the form schema."""
    is_file = part.get_filename() is not None
    problems = ["document is not a file part"] if name == "document" and not is_file else []
    text = "" if is_file else cast("str", part.get_content())
    if not is_file and len(text) < cast("int", kind.get("minLength", 0)):
        problems.append(f"{name} is shorter than {kind['minLength']}")
    if not is_file and len(text) > cast("int", kind.get("maxLength", len(text))):
        problems.append(f"{name} is longer than {kind['maxLength']}")
    if name == "custom_fields":  # untyped in the schema; Paperless parses it as JSON
        try:
            _ = cast("object", json.loads(text))
        except ValueError:
            problems.append("custom_fields is not valid JSON")
    items = cast("Json", kind.get("items", kind))
    try:
        if items.get("type") == "integer":
            number = int(text)
            low, high = items.get("minimum", number), items.get("maximum", number)
            if not low <= number <= high:  # pyright: ignore[reportOperatorIssue] -- JSON numbers
                problems.append(f"{name} is out of range")
        if kind.get("format") == "date-time":
            _ = datetime.fromisoformat(text)
    except ValueError:
        problems.append(f"{name}={text!r} is not valid for its type")
    return problems


def _form_errors(request: httpx2.Request, template: str) -> list[str]:
    """What is wrong with the fields of a multipart request, against its form schema."""
    operation = cast("Json", _PATHS[template][request.method.lower()])
    body = cast("Json", cast("Json", operation["requestBody"])["content"])
    reference = str(cast("Json", cast("Json", body["multipart/form-data"])["schema"])["$ref"])
    schema = _SCHEMAS[reference.rsplit("/", 1)[-1]]
    properties = cast("dict[str, Json]", schema["properties"])
    problems: list[str] = []
    header = f"Content-Type: {request.headers.get('Content-Type', '')}\r\n\r\n".encode()
    message = BytesParser(policy=default).parsebytes(header + request.content)
    if not message.is_multipart():
        return ["the body is not multipart/form-data"]
    sent = [
        (str(p.get_param("name", header="content-disposition")), p) for p in message.iter_parts()
    ]
    names = [name for name, _ in sent]
    if "Authorization" not in request.headers:
        problems.append("no credentials")
    problems += [f"undeclared field {name!r}" for name in names if name not in properties]
    problems += [
        f"missing field {name!r}"
        for name in cast("list[str]", schema.get("required", []))
        if name not in names
    ]
    for name, part in sent:
        problems += _field_problems(name, part, properties.get(name, {}))
    return problems


def check_request(request: httpx2.Request) -> None:
    """Raise `SchemaViolationError` unless the request matches an operation of the schema."""
    try:
        template = _template(request)
    except PathError:  # no such operation: openapi-core reports it below
        template = ""
    if (request.method, template) in FORM_REQUESTS:
        if problems := _form_errors(request, template):
            where = f"{request.method} {request.url.path}: form does not match the schema"
            message = f"{where}: {'; '.join(problems)}"
            raise SchemaViolationError(message)
        return
    errors = list(_OPENAPI.iter_request_errors(_Request(request, _narrowed(request))))
    if errors:
        raise _fail(request, "request does not match the schema", errors)
    if undeclared := _undeclared_query_parameters(request):
        message = f"{request.method} {request.url.path}: undeclared query parameters"
        message += f" {sorted(undeclared)}"
        raise SchemaViolationError(message)


def _check_file_response(request: httpx2.Request, response: httpx2.Response) -> None:
    """Raise `SchemaViolationError` unless the response looks like a file Paperless sends."""
    content_type = response.headers.get("Content-Type", "")
    disposition = response.headers.get("Content-Disposition", "")
    problems = [
        problem
        for problem, found in (
            ("no content type", not content_type),
            ("a JSON content type", content_type.lower().startswith("application/json")),
            (
                "no inline or attachment Content-Disposition",
                not re.match(r"(inline|attachment)\b", disposition),
            ),
            ("an empty body", not response.content),
        )
        if found
    ]
    if problems:
        message = f"{request.method} {request.url.path}: file response with {', '.join(problems)}"
        raise SchemaViolationError(message)


def check_response(request: httpx2.Request, response: httpx2.Response) -> None:
    """Raise `SchemaViolationError` unless the response is one the schema allows for the request."""
    template = _template(request)
    if (request.method, template) in FILE_RESPONSES and response.status_code == HTTPStatus.OK:
        _check_file_response(request, response)
        return
    if (request.method, template, response.status_code) in WRONG_RESPONSE_SCHEMAS:
        return
    operation = cast("Json", _PATHS[template][request.method.lower()])
    documented = str(response.status_code) in cast("Json", operation["responses"])
    if not documented and response.status_code in UNCHECKED_STATUSES:
        return
    if not documented and response.status_code in DRF_ERROR_STATUSES:
        if not _is_drf_error(_json_or_none(response)):
            message = f"{request.method} {request.url.path}: not a DRF error body: {response.text}"
            raise SchemaViolationError(message)
        return
    errors = list(_OPENAPI.iter_response_errors(_Request(request), _Response(response)))
    if errors:
        raise _fail(request, f"{response.status_code} response does not match the schema", errors)


class SchemaGuardedTransport(httpx2.MockTransport):
    """A MockTransport that passes only schema-conforming requests and responses.

    The handler may raise an httpx2 transport error instead of answering, to simulate the network.
    """

    def __init__(self, handler: Handler) -> None:
        """Wrap `handler` in the request and response checks."""
        super().__init__(self._guarded)
        self._handler: Handler = handler

    def _guarded(self, request: httpx2.Request) -> httpx2.Response:
        check_request(request)
        response = self._handler(request)
        check_response(request, response)
        return response
