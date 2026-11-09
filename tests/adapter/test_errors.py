"""Error mapping for bodies a proxy in front of Paperless may send (outside the schema guard)."""

import httpx2
import pytest

from paperless_mcp.domain.errors import Conflict, Validation
from paperless_mcp.paperless.errors import error_for_status


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
