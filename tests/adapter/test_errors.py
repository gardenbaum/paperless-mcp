"""Error mapping for bodies a proxy in front of Paperless may send (outside the schema guard)."""

import httpx2
import pytest

from paperless_mcp.domain.errors import (
    Conflict,
    PaperlessMcpError,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
    Validation,
)
from paperless_mcp.paperless.errors import (
    MAX_MESSAGE_LENGTH,
    error_for_status,
    error_for_transport,
)


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [
        (400, Validation, "Paperless rejected the request."),
        (409, Conflict, "Paperless reported a conflict."),
    ],
)
def test_a_body_that_is_not_json_gives_the_plain_message(
    status: int, error: type[Exception], message: str
) -> None:
    mapped = error_for_status(httpx2.Response(status, text="<html>Bad Request</html>"))

    assert type(mapped) is error
    assert str(mapped) == message


# --- Mutation-testing additions (PLMCP-21): the exact wording clients see.


def rejected(body: str) -> str:
    return str(error_for_status(httpx2.Response(400, text=body)))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('{"detail": "No way."}', "Paperless rejected the request: No way."),
        ('{"non_field_errors": ["Bad mix."]}', "Paperless rejected the request: Bad mix."),
        ('"Plain text."', "Paperless rejected the request: Plain text."),
        ('["First.", "Second."]', "Paperless rejected the request: First.; Second."),
        ('{"title": ["Too long."]}', "Paperless rejected the request: title: Too long."),
        (
            '{"owner": {"id": ["Unknown."]}}',
            "Paperless rejected the request: owner.id: Unknown.",
        ),
        (
            '{"tags": [{"name": ["Required."]}]}',
            "Paperless rejected the request: tags.0.name: Required.",
        ),
        ('{"a": ["x"], "b": ["y"]}', "Paperless rejected the request: a: x; b: y"),
    ],
)
def test_validation_messages_name_the_field_path_and_join_with_semicolons(
    body: str, message: str
) -> None:
    assert rejected(body) == message


def test_control_characters_become_single_spaces() -> None:
    assert rejected('{"detail": "a\\u0000b\\n\\tc"}') == "Paperless rejected the request: a b c"


def test_a_message_is_cut_only_when_it_is_longer_than_the_limit() -> None:
    limit = MAX_MESSAGE_LENGTH
    exact = rejected(f'{{"detail": "{"x" * limit}"}}')
    longer = rejected(f'{{"detail": "{"x" * (limit + 1)}"}}')

    assert exact.removeprefix("Paperless rejected the request: ") == "x" * limit
    assert longer.removeprefix("Paperless rejected the request: ") == "x" * (limit - 1) + "…"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_busy_paperless_is_temporarily_unavailable_with_a_hint(status: int) -> None:
    mapped = error_for_status(httpx2.Response(status))

    assert type(mapped) is UpstreamUnavailable
    assert mapped.message == f"Paperless is temporarily unavailable (HTTP {status})."
    assert mapped.hint == "Try again in a moment."


def test_any_other_status_is_reported_by_its_number_without_hint() -> None:
    mapped = error_for_status(httpx2.Response(418))

    assert type(mapped) is PaperlessMcpError
    assert (mapped.message, mapped.hint) == ("Paperless answered with HTTP 418.", None)


def test_a_timeout_and_an_unreachable_paperless_say_so_and_what_to_do() -> None:
    timeout = error_for_transport(httpx2.ReadTimeout("slow"))
    unreachable = error_for_transport(httpx2.ConnectError("refused"))

    assert (type(timeout), timeout.message, timeout.hint) == (
        UpstreamTimeout,
        "Paperless did not respond in time.",
        "Try again in a moment.",
    )
    assert (type(unreachable), unreachable.message, unreachable.hint) == (
        UpstreamUnreachable,
        "Paperless is unreachable.",
        "Check that Paperless is running and its URL is right.",
    )
