"""Map HTTP failures to client-safe domain errors."""

import json
from http import HTTPStatus
from typing import cast

import httpx2

from paperless_mcp.domain.errors import (
    AuthenticationFailed,
    Conflict,
    FeatureUnavailable,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UnsupportedVersion,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
    Validation,
)

MAX_MESSAGES = 10
MAX_MESSAGE_LENGTH = 200
# DRF keys whose messages are not about one field.
GENERAL_KEYS = frozenset({"detail", "non_field_errors"})
RETRY_LATER = "Try again in a moment."
# What Paperless 3.4 answers (400, text/html) to every AI endpoint while AI is switched off.
AI_DISABLED_BODY = "AI is required for this feature"


def _clean(text: str) -> str:
    """One line without control characters, cut to a bounded length."""
    line = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    if len(line) > MAX_MESSAGE_LENGTH:
        return line[: MAX_MESSAGE_LENGTH - 1] + "…"
    return line


def _messages(body: object, path: str = "") -> list[str]:
    """Flatten a DRF error body into "field.path: message" lines."""
    if isinstance(body, dict):
        items = cast("dict[str, object]", body).items()
        return [
            line
            for key, value in items
            for line in _messages(value, path if key in GENERAL_KEYS else f"{path}{key}.")
        ]
    if isinstance(body, list):
        values = cast("list[object]", body)
        return [
            line
            for index, value in enumerate(values)
            for line in _messages(value, path if isinstance(value, str) else f"{path}{index}.")
        ]
    prefix = f"{path.removesuffix('.')}: " if path else ""
    return [prefix + _clean(str(body))]


def _details(response: httpx2.Response) -> str:
    try:
        body = cast("object", json.loads(response.content))
    except ValueError:
        return ""
    return "; ".join(_messages(body)[:MAX_MESSAGES])


# Statuses whose client-safe message includes Paperless's sanitised messages: (error, prefix).
_WITH_DETAILS: dict[int, tuple[type[PaperlessMcpError], str]] = {
    HTTPStatus.BAD_REQUEST: (Validation, "Paperless rejected the request"),
    HTTPStatus.CONFLICT: (Conflict, "Paperless reported a conflict"),
}
# Statuses with a fixed message: (error, message, hint).
_FIXED: dict[int, tuple[type[PaperlessMcpError], str, str | None]] = {
    HTTPStatus.UNAUTHORIZED: (
        AuthenticationFailed,
        "Paperless rejected the credentials; the API token may be expired or revoked.",
        "Check the Paperless API token this server is configured with.",
    ),
    HTTPStatus.FORBIDDEN: (
        PermissionDenied,
        "The Paperless user may not perform this action.",
        None,
    ),
    HTTPStatus.NOT_FOUND: (
        NotFound,
        "The Paperless object does not exist or is not visible to this user.",
        None,
    ),
    HTTPStatus.NOT_ACCEPTABLE: (
        UnsupportedVersion,
        "Paperless does not accept API version 10.",
        "This server needs paperless-ngx 3.x.",
    ),
}


def ai_disabled() -> FeatureUnavailable:
    """The error for an AI endpoint of an instance that has Paperless AI switched off."""
    return FeatureUnavailable(
        "Paperless AI is not enabled on this instance.",
        hint="An administrator enables it with PAPERLESS_AI_ENABLED and an LLM backend.",
    )


def error_for_status(response: httpx2.Response) -> PaperlessMcpError:
    """The domain error for a non-success response."""
    status = response.status_code
    if status == HTTPStatus.BAD_REQUEST and AI_DISABLED_BODY in response.text:
        return ai_disabled()
    if status in _WITH_DETAILS:
        error, prefix = _WITH_DETAILS[status]
        details = _details(response)
        return error(f"{prefix}: {details}" if details else f"{prefix}.")
    if status in _FIXED:
        error, message, hint = _FIXED[status]
        return error(message, hint=hint)
    if status == HTTPStatus.TOO_MANY_REQUESTS or status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return UpstreamUnavailable(
            f"Paperless is temporarily unavailable (HTTP {status}).", hint=RETRY_LATER
        )
    return PaperlessMcpError(f"Paperless answered with HTTP {status}.")


def error_for_transport(error: httpx2.TransportError) -> UpstreamUnavailable:
    """The domain error for a request that got no response."""
    if isinstance(error, httpx2.TimeoutException):
        return UpstreamTimeout("Paperless did not respond in time.", hint=RETRY_LATER)
    return UpstreamUnreachable(
        "Paperless is unreachable.", hint="Check that Paperless is running and its URL is right."
    )
