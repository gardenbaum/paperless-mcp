"""PaperlessClient: headers, retries, error mapping and redaction, on real endpoint shapes."""

import logging
from typing import cast, override

import httpx2
import pytest
from pydantic import BaseModel

from adapter.conftest import TOKEN, ClientFactory, RecordingSleep, tag
from paperless_mcp.domain.errors import (
    AuthenticationFailed,
    Conflict,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UnsupportedVersion,
    UpstreamUnavailable,
    Validation,
)
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import RetryPolicy

pytestmark = pytest.mark.anyio

TAG_PAGE = {"count": 1, "next": None, "previous": None, "results": [tag(1, "inbox")]}


class TagStub(BaseModel):
    id: int
    name: str


def answer(status: int, body: object = None, **headers: str) -> httpx2.Response:
    return httpx2.Response(status, json=body, headers=headers)


class Script:
    """A handler that answers with the given responses in order and records the requests."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses: list[httpx2.Response] = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def raising(error: type[httpx2.TransportError]) -> Script:
    class Raising(Script):
        @override
        def __call__(self, request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            message = "simulated"
            raise error(message, request=request)

    return Raising()


@pytest.mark.decision("decision-2")
async def test_every_request_sends_api_version_10_and_the_credential(
    make_client: ClientFactory,
) -> None:
    script = Script(answer(200, tag(1, "inbox")), answer(201, tag(2, "new")))
    client = make_client(script)

    _ = await client.request("GET", "/api/tags/1/")
    _ = await client.request("POST", "/api/tags/", json={"name": "new"})

    for request in script.requests:
        assert request.headers["Accept"] == "application/json; version=10"
        assert request.headers["Authorization"] == f"Token {TOKEN}"


@pytest.mark.decision("decision-3")
async def test_requests_go_through_the_injected_client(make_client: ClientFactory) -> None:
    script = Script(answer(200, TAG_PAGE))

    _ = await make_client(script).request("GET", "/api/tags/", params={"page_size": 25})

    assert str(script.requests[0].url) == "http://paperless.test/api/tags/?page_size=25"


async def test_get_parses_the_body_into_the_model(make_client: ClientFactory) -> None:
    client = make_client(Script(answer(200, tag(7, "inbox"))))

    assert await client.get("/api/tags/7/", TagStub) == TagStub(id=7, name="inbox")


async def test_an_unexpected_body_is_a_client_safe_error(make_client: ClientFactory) -> None:
    client = make_client(Script(answer(200, tag(7, "inbox"))))

    with pytest.raises(PaperlessMcpError, match="unexpected response"):
        _ = await client.get("/api/tags/7/", BaseModelWithMissingField)


class BaseModelWithMissingField(BaseModel):
    not_in_the_response: str


@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_idempotent_requests_retry_transient_statuses(
    make_client: ClientFactory, sleep: RecordingSleep, status: int
) -> None:
    script = Script(answer(status, {"detail": "busy"}), answer(200, TAG_PAGE))

    response = await make_client(script).request("GET", "/api/tags/")

    assert response.status_code == 200
    assert len(script.requests) == 2
    assert len(sleep.delays) == 1


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
async def test_every_idempotent_method_retries(make_client: ClientFactory, method: str) -> None:
    done = answer(204) if method == "DELETE" else answer(200, tag(1, "inbox"))
    script = Script(answer(503), done)
    body = {"name": "inbox"} if method == "PUT" else None

    _ = await make_client(script).request(method, "/api/tags/1/", json=body)

    assert len(script.requests) == 2


@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_post_and_patch_never_retry(
    make_client: ClientFactory, sleep: RecordingSleep, method: str
) -> None:
    script = Script(answer(503))
    path = "/api/tags/" if method == "POST" else "/api/tags/1/"

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(script).request(method, path, json={"name": "x"})

    assert len(script.requests) == 1
    assert sleep.delays == []


async def test_retry_after_seconds_are_honoured(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(
        answer(429, {"detail": "slow down"}, **{"Retry-After": "3"}), answer(200, TAG_PAGE)
    )

    _ = await make_client(script).request("GET", "/api/tags/")

    assert sleep.delays == [3.0]


async def test_retry_after_in_the_past_retries_at_once(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    past = "Wed, 21 Oct 2015 07:28:00 GMT"
    script = Script(answer(503, **{"Retry-After": past}), answer(200, TAG_PAGE))

    _ = await make_client(script).request("GET", "/api/tags/")

    assert sleep.delays == [0.0]


async def test_retry_after_beyond_the_budget_gives_up_without_waiting(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(answer(503, **{"Retry-After": "Fri, 01 Jan 2100 00:00:00 GMT"}))

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(script).request("GET", "/api/tags/")

    assert sleep.delays == []


async def test_unparsable_retry_after_falls_back_to_jittered_backoff(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(answer(503, **{"Retry-After": "soon"}), answer(503), answer(200, TAG_PAGE))
    policy = RetryPolicy(attempts=3, budget=10.0, backoff=0.5)

    _ = await make_client(script, retry=policy).request("GET", "/api/tags/")

    first, second = sleep.delays
    assert 0.0 <= first <= 0.5
    assert 0.0 <= second <= 1.0


async def test_attempts_are_bounded(make_client: ClientFactory, sleep: RecordingSleep) -> None:
    script = Script(*(answer(503) for _ in range(3)))

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(script, retry=RetryPolicy(attempts=3)).request("GET", "/api/tags/")

    assert len(script.requests) == 3
    assert len(sleep.delays) == 2


async def test_total_wait_stays_within_the_budget(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(*(answer(503, **{"Retry-After": "4"}) for _ in range(5)))

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(script, retry=RetryPolicy(attempts=5, budget=10.0)).request(
            "GET", "/api/tags/"
        )

    assert sleep.delays == [4.0, 4.0]


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"detail": "Invalid token."}, AuthenticationFailed),
        (403, {"detail": "You do not have permission to perform this action."}, PermissionDenied),
        (404, {"detail": "No Document matches the given query."}, NotFound),
        (406, {"detail": 'Invalid version in "Accept" header.'}, UnsupportedVersion),
        (409, {"detail": "Conflict."}, Conflict),
        (500, None, UpstreamUnavailable),
        (418, {"detail": "teapot"}, PaperlessMcpError),
    ],
)
async def test_http_errors_map_to_domain_errors(
    make_client: ClientFactory, status: int, body: object, error: type[PaperlessMcpError]
) -> None:
    with pytest.raises(error) as caught:
        _ = await make_client(Script(answer(status, body))).request("GET", "/api/documents/1/")
    assert type(caught.value) is error


async def test_validation_errors_carry_sanitised_field_messages(make_client: ClientFactory) -> None:
    body = {
        "name": ["Tag with this name already exists.", "Too\nlong\x00 " + "x" * 400],
        "non_field_errors": ["Owner is not valid."],
        "custom_fields": [{"value": ["Not a valid date."]}],
    }
    client = make_client(Script(answer(400, body)))

    with pytest.raises(Validation) as caught:
        _ = await client.request("POST", "/api/tags/", json={"name": "inbox"})

    message = caught.value.message
    assert "name: Tag with this name already exists." in message
    assert "Owner is not valid." in message
    assert "custom_fields.0.value: Not a valid date." in message
    assert "\n" not in message
    assert "\x00" not in message
    assert "x" * 201 not in message


async def test_validation_without_field_messages_still_explains(make_client: ClientFactory) -> None:
    client = make_client(Script(httpx2.Response(400, json=[])))

    with pytest.raises(Validation, match="rejected the request"):
        _ = await client.request("POST", "/api/tags/", json={"name": "inbox"})


async def test_validation_messages_are_capped(make_client: ClientFactory) -> None:
    body = {f"field{n}": [f"message {n}"] for n in range(30)}
    client = make_client(Script(answer(400, body)))

    with pytest.raises(Validation) as caught:
        _ = await client.request("POST", "/api/tags/", json={"name": "inbox"})

    assert "message 9" in caught.value.message
    assert "message 10" not in caught.value.message


async def test_unreachable_slow_and_revoked_are_distinct_client_safe_errors(
    make_client: ClientFactory,
) -> None:
    caught: list[PaperlessMcpError] = []
    for handler in (
        raising(httpx2.ConnectError),
        raising(httpx2.ReadTimeout),
        Script(answer(401, {"detail": "Invalid token."})),
    ):
        with pytest.raises(PaperlessMcpError) as error:
            _ = await make_client(handler).request("GET", "/api/tags/")
        caught.append(error.value)

    unreachable, slow, revoked = caught
    assert isinstance(unreachable, UpstreamUnavailable)
    assert isinstance(slow, UpstreamUnavailable)
    assert isinstance(revoked, AuthenticationFailed)
    assert len({error.message for error in caught}) == 3
    assert all(error.hint for error in caught)
    assert "simulated" not in unreachable.message + slow.message


async def test_transport_errors_are_not_retried(make_client: ClientFactory) -> None:
    handler = raising(httpx2.ConnectError)

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(handler).request("GET", "/api/tags/")

    assert len(handler.requests) == 1


async def test_uploads_use_the_upload_timeout(make_client: ClientFactory) -> None:
    script = Script(httpx2.Response(200, json="0b7c3a8e-task-id"), answer(200, TAG_PAGE))
    client = make_client(script)

    _ = await client.request(
        "POST",
        "/api/documents/post_document/",
        data={"title": "Invoice"},
        files={"document": ("invoice.pdf", b"%PDF-1.4", "application/pdf")},
    )
    _ = await client.request("GET", "/api/tags/")

    upload, other = (cast("dict[str, float]", r.extensions["timeout"]) for r in script.requests)
    assert upload == {"connect": 300.0, "read": 300.0, "write": 300.0, "pool": 300.0}
    assert other["read"] == 5.0


@pytest.mark.decision("decision-9")
async def test_token_never_appears_in_logs_errors_or_reprs(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    script = Script(
        answer(503, {"detail": "busy"}),
        answer(200, TAG_PAGE),
        answer(400, {"name": ["exists"]}),
        answer(401, {"detail": "Invalid token."}),
    )
    client = make_client(script)
    errors: list[BaseException] = []

    _ = await client.request("GET", "/api/tags/")
    for method, path in (("POST", "/api/tags/"), ("GET", "/api/tags/")):
        with pytest.raises(PaperlessMcpError) as caught:
            _ = await client.request(method, path, json={"name": "x"} if method == "POST" else None)
        errors.append(caught.value)
    for handler in (raising(httpx2.ConnectError), raising(httpx2.ReadTimeout)):
        with pytest.raises(PaperlessMcpError) as caught:
            _ = await make_client(handler).request("GET", "/api/tags/")
        errors.append(caught.value)

    provider = StaticTokenProvider(TOKEN)
    texts = [caplog.text, repr(client), repr(provider), str(provider)]
    texts += [repr(provider.current()), str(provider.current())]
    for error in errors:
        texts += [str(error), repr(error), repr(error.__cause__), str(error.__cause__)]
    assert caplog.records  # the test saw log output at all
    assert not [text for text in texts if TOKEN in text]
