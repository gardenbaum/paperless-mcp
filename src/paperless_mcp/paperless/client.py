"""The Paperless HTTP client: transport rules every adapter call shares.

Every request sends ``Accept: application/json; version=10`` and the current credential's auth
header. Idempotent requests are retried on transient statuses; failures become domain errors.
This module never logs headers, tokens or query values (they can carry search text). httpx2's
own loggers log request URLs with their query at INFO; ``configure_logging`` (observability)
holds them at WARNING.
"""

import asyncio
import contextlib
import email.utils
import logging
import random
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import httpx2
import pydantic

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.paperless.errors import error_for_status, error_for_transport

if TYPE_CHECKING:
    from paperless_mcp.domain.credentials import CredentialProvider

ACCEPT = "application/json; version=10"
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
RETRY_STATUSES = frozenset({429, 502, 503, 504})

type Method = Literal["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
type Params = Mapping[str, str | int]
type Files = Mapping[str, tuple[str, bytes, str]]
type Sleep = Callable[[float], Awaitable[None]]
type Clock = Callable[[], datetime]

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


def utc_now() -> datetime:
    """The current time in UTC; the default clock."""
    return datetime.now(UTC)


def retry_after(value: str, now: datetime) -> float | None:
    """Seconds to wait from a ``Retry-After`` value (delta seconds or HTTP date); None if bad."""
    value = value.strip()
    try:
        return max(0.0, float(int(value)))
    except ValueError, OverflowError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except ValueError, TypeError:
        return None
    if when.tzinfo is None:  # "-0000" dates parse naive; RFC 9110 dates are GMT
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


def unexpected(response: httpx2.Response) -> PaperlessMcpError:
    """The error for a successful response whose content the adapter cannot use."""
    request = response.request
    return PaperlessMcpError(
        f"Paperless sent an unexpected response for {request.method} {request.url.path}."
    )


def parse[M: pydantic.BaseModel](response: httpx2.Response, model: type[M]) -> M:
    """Parse the JSON body of ``response`` into ``model``.

    Raises:
        PaperlessMcpError: ``model`` rejects the body.
    """
    try:
        return model.model_validate_json(response.content)
    except pydantic.ValidationError as error:
        raise unexpected(response) from error


@dataclass(frozen=True, slots=True)
class Download:
    """A body read up to a limit.

    Attributes:
        content: The whole body, or empty if it is ``truncated``.
        headers: The response headers.
        truncated: The body is larger than the limit; the rest was not read.
    """

    content: bytes
    headers: httpx2.Headers
    truncated: bool


class PaperlessClient:
    """Async client for one Paperless instance, over one shared ``httpx2.AsyncClient``.

    The injected client carries the base URL, default timeouts and connection limits; its
    transport is the test seam.
    """

    def __init__(  # noqa: PLR0913 -- seams and settings are injected, keyword-only
        self,
        http: httpx2.AsyncClient,
        credentials: CredentialProvider,
        *,
        retry: RetryPolicy,
        upload_timeout: float,
        sleep: Sleep = asyncio.sleep,
        now: Clock = utc_now,
    ) -> None:
        """Create the client.

        Args:
            http: The shared client with base URL, default timeouts and limits.
            credentials: Supplies the credential for each request.
            retry: Retry rules for idempotent requests.
            upload_timeout: Timeout in seconds for requests that send files.
            sleep: Waits between retries; injectable so tests do not wait.
            now: Current time, for ``Retry-After`` dates; injectable for tests.
        """
        self._http: httpx2.AsyncClient = http
        self._credentials: CredentialProvider = credentials
        self._retry: RetryPolicy = retry
        self._upload_timeout: float = upload_timeout
        self._sleep: Sleep = sleep
        self._now: Clock = now

    async def request(  # noqa: PLR0913 -- mirrors httpx2's request(); the parts are keyword-only
        self,
        method: Method,
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
        outgoing = self._build(method, path, params=params, json=json, data=data, files=files)
        response = await self._exchange(outgoing, stream=False)
        if response.is_error:
            raise error_for_status(response)
        return response

    async def download(
        self, path: str, *, params: Params | None = None, max_bytes: int
    ) -> Download:
        """GET ``path`` and read at most ``max_bytes`` bytes of the body.

        The body is streamed and the connection closed as soon as more than ``max_bytes`` bytes
        have arrived; the result is then ``truncated`` with no content.

        Raises:
            PaperlessMcpError: The matching subclass for an error status or a failed transport.
        """
        response = await self._exchange(self._build("GET", path, params=params), stream=True)
        try:
            if response.is_error:
                _ = await response.aread()
                raise error_for_status(response)
            received: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                received.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    return Download(b"", response.headers, truncated=True)
            return Download(b"".join(received), response.headers, truncated=False)
        finally:
            await response.aclose()

    def _build(  # noqa: PLR0913 -- the parts of httpx2's build_request(), keyword-only
        self,
        method: Method,
        path: str,
        *,
        params: Params | None = None,
        json: object = None,
        data: Mapping[str, str] | None = None,
        files: Files | None = None,
    ) -> httpx2.Request:
        name, value = self._credentials.current().header
        return self._http.build_request(
            method,
            path,
            params=params,
            json=json,
            data=data,
            files=files,
            headers={"Accept": ACCEPT, name: value},
            timeout=self._upload_timeout if files else httpx2.USE_CLIENT_DEFAULT,
        )

    async def _exchange(self, outgoing: httpx2.Request, *, stream: bool) -> httpx2.Response:
        """Send ``outgoing``, retrying transient statuses; the last response, errors included.

        With ``stream`` the body is not read yet: the caller reads and closes the response.
        """
        method, path = outgoing.method, outgoing.url.path
        attempt, waited = 1, 0.0
        while True:
            response = await self._send(outgoing, stream=stream)
            logger.debug("%s %s -> %d (attempt %d)", method, path, response.status_code, attempt)
            delay = self._retry_delay(response, attempt, waited)
            if delay is None:
                return response
            if stream:
                await response.aclose()
            logger.warning(
                "Paperless answered %d to %s %s; retrying in %.1fs",
                response.status_code,
                method,
                path,
                delay,
            )
            await self._sleep(delay)
            attempt, waited = attempt + 1, waited + delay

    async def call[M: pydantic.BaseModel](  # noqa: PLR0913 -- request() plus the model
        self,
        method: Method,
        path: str,
        model: type[M],
        *,
        params: Params | None = None,
        json: object = None,
        data: Mapping[str, str] | None = None,
        files: Files | None = None,
    ) -> M:
        """Send a request and parse the JSON body of the successful response into ``model``.

        Raises:
            PaperlessMcpError: An error response, a failed transport, or a body ``model`` rejects.
        """
        response = await self.request(
            method, path, params=params, json=json, data=data, files=files
        )
        return parse(response, model)

    async def get[M: pydantic.BaseModel](
        self, path: str, model: type[M], *, params: Params | None = None
    ) -> M:
        """GET ``path`` and parse the JSON body into ``model`` (see ``call``)."""
        return await self.call("GET", path, model, params=params)

    def _retry_delay(self, response: httpx2.Response, attempt: int, waited: float) -> float | None:
        """Seconds to wait before the next attempt, or None if the response is final."""
        if (
            response.status_code not in RETRY_STATUSES
            or response.request.method not in IDEMPOTENT_METHODS
            or attempt >= self._retry.attempts
        ):
            return None
        delay = retry_after(response.headers.get("Retry-After", ""), self._now())
        if delay is None:
            cap = self._retry.backoff * 2.0 ** (attempt - 1)
            delay = random.uniform(0.0, cap)  # noqa: S311 -- jitter, not cryptography
        return delay if waited + delay <= self._retry.budget else None

    async def _send(self, request: httpx2.Request, *, stream: bool) -> httpx2.Response:
        try:
            return await self._http.send(request, stream=stream)
        except httpx2.TransportError as error:
            logger.warning(
                "%s %s failed: %s", request.method, request.url.path, type(error).__name__
            )
            raise error_for_transport(error) from error


@contextlib.asynccontextmanager
async def connect(
    base_url: str,
    credentials: CredentialProvider,
    *,
    timeout: float,  # noqa: ASYNC109 -- the client's default request timeout, not a cancel scope
    upload_timeout: float,
    retry: RetryPolicy,
) -> AsyncGenerator[PaperlessClient]:
    """A client on a new ``httpx2.AsyncClient`` for ``base_url``, closed on exit.

    The process opens one, in the server lifespan; every request shares its connection pool.
    """
    async with httpx2.AsyncClient(base_url=base_url, timeout=timeout) as http:
        yield PaperlessClient(http, credentials, retry=retry, upload_timeout=upload_timeout)
