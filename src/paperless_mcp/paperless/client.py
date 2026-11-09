"""The Paperless HTTP client: transport rules every adapter call shares.

Every request sends ``Accept: application/json; version=10`` and the current credential's auth
header. Idempotent requests are retried on transient statuses; failures become domain errors.
Headers are never logged, and query strings neither (they can carry search text).
"""

import asyncio
import email.utils
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx2
import pydantic

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.paperless.errors import error_for_status, error_for_transport

if TYPE_CHECKING:
    from paperless_mcp.domain.credentials import CredentialProvider

ACCEPT = "application/json; version=10"
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
RETRY_STATUSES = frozenset({429, 502, 503, 504})

type Params = Mapping[str, str | int]
type Files = Mapping[str, tuple[str, bytes, str]]
type Sleep = Callable[[float], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How idempotent requests are retried on 429/502/503/504.

    Attributes:
        attempts: Most attempts per request, the first one included.
        budget: Most seconds to wait between attempts in total; a longer ``Retry-After`` gives up.
        backoff: Base delay in seconds without ``Retry-After``: attempt n waits up to
            ``backoff * 2 ** (n - 1)`` (full jitter).
    """

    attempts: int = 3
    budget: float = 10.0
    backoff: float = 0.5


def _retry_after(response: httpx2.Response) -> float | None:
    """Seconds to wait from ``Retry-After`` (delta seconds or HTTP date); None if absent or bad."""
    value = response.headers.get("Retry-After", "").strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except ValueError:
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


class PaperlessClient:
    """Async client for one Paperless instance, over one shared ``httpx2.AsyncClient``.

    The injected client carries the base URL, default timeouts and connection limits; its
    transport is the test seam.
    """

    def __init__(
        self,
        http: httpx2.AsyncClient,
        credentials: CredentialProvider,
        *,
        retry: RetryPolicy,
        upload_timeout: float,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        """Create the client.

        Args:
            http: The shared client with base URL, default timeouts and limits.
            credentials: Supplies the credential for each request.
            retry: Retry rules for idempotent requests.
            upload_timeout: Timeout in seconds for requests that send files.
            sleep: Waits between retries; injectable so tests do not wait.
        """
        self._http: httpx2.AsyncClient = http
        self._credentials: CredentialProvider = credentials
        self._retry: RetryPolicy = retry
        self._upload_timeout: float = upload_timeout
        self._sleep: Sleep = sleep

    async def request(  # noqa: PLR0913 -- mirrors httpx2's request(); the parts are keyword-only
        self,
        method: str,
        path: str,
        *,
        params: Params | None = None,
        json: object = None,
        data: Mapping[str, str] | None = None,
        files: Files | None = None,
    ) -> httpx2.Response:
        """Send a request and return the successful response.

        Raises:
            PaperlessMcpError: The matching subclass for an error status or a failed transport.
        """
        name, value = self._credentials.current().header
        outgoing = self._http.build_request(
            method,
            path,
            params=params,
            json=json,
            data=data,
            files=files,
            headers={"Accept": ACCEPT, name: value},
            timeout=self._upload_timeout if files else httpx2.USE_CLIENT_DEFAULT,
        )
        attempt, waited = 1, 0.0
        while True:
            response = await self._send(outgoing)
            logger.debug("%s %s -> %d (attempt %d)", method, path, response.status_code, attempt)
            delay = self._retry_delay(response, attempt, waited)
            if delay is None:
                break
            logger.warning(
                "Paperless answered %d to %s %s; retrying in %.1fs",
                response.status_code,
                method,
                path,
                delay,
            )
            await self._sleep(delay)
            attempt, waited = attempt + 1, waited + delay
        if response.is_error:
            raise error_for_status(response)
        return response

    async def get[M: pydantic.BaseModel](
        self, path: str, model: type[M], *, params: Params | None = None
    ) -> M:
        """GET ``path`` and parse the JSON body into ``model``.

        Raises:
            PaperlessMcpError: An error response, a failed transport, or a body ``model`` rejects.
        """
        response = await self.request("GET", path, params=params)
        try:
            return model.model_validate_json(response.content)
        except pydantic.ValidationError as error:
            message = f"Paperless sent an unexpected response for GET {path}."
            raise PaperlessMcpError(message) from error

    def _retry_delay(self, response: httpx2.Response, attempt: int, waited: float) -> float | None:
        """Seconds to wait before the next attempt, or None if the response is final."""
        if (
            response.status_code not in RETRY_STATUSES
            or response.request.method not in IDEMPOTENT_METHODS
            or attempt >= self._retry.attempts
        ):
            return None
        delay = _retry_after(response)
        if delay is None:
            cap = self._retry.backoff * 2.0 ** (attempt - 1)
            delay = random.uniform(0.0, cap)  # noqa: S311 -- jitter, not cryptography
        return delay if waited + delay <= self._retry.budget else None

    async def _send(self, request: httpx2.Request) -> httpx2.Response:
        try:
            return await self._http.send(request)
        except httpx2.TransportError as error:
            logger.warning(
                "%s %s failed: %s", request.method, request.url.path, type(error).__name__
            )
            raise error_for_transport(error) from error
