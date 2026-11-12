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

from paperless_mcp.domain.errors import PaperlessMcpError, UpstreamTimeout
from paperless_mcp.paperless.errors import (
    RETRY_LATER,
    error_for_status,
    error_for_transport,
)

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
type Jitter = Callable[[float], float]


def random_wait(cap: float) -> float:
    """A random wait between zero and ``cap`` seconds."""
    return random.uniform(0.0, cap)  # noqa: S311 -- jitter, not cryptography


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


@dataclass(frozen=True, slots=True)
class Streamed:
    """A streamed body read within a size and a time limit.

    Attributes:
        content: The body, or its first bytes if it is ``truncated``.
        truncated: The body is larger than the size limit or did not end within the time limit.
    """

    content: bytes
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
        jitter: Jitter = random_wait,
    ) -> None:
        """Create the client.

        Args:
            http: The shared client with base URL, default timeouts and limits.
            credentials: Supplies the credential for each request.
            retry: Retry rules for idempotent requests.
            upload_timeout: Timeout in seconds for requests that send files.
            sleep: Waits between retries; injectable so tests do not wait.
            now: Current time, for ``Retry-After`` dates; injectable for tests.
            jitter: Picks the wait below a cap when no ``Retry-After`` says; injectable for tests.
        """
        self._http: httpx2.AsyncClient = http
        self._credentials: CredentialProvider = credentials
        self._retry: RetryPolicy = retry
        self._upload_timeout: float = upload_timeout
        self._sleep: Sleep = sleep
        self._now: Clock = now
        self._jitter: Jitter = jitter

    async def request(  # noqa: PLR0913 -- mirrors httpx2's request(); the parts are keyword-only
        self,
        method: Method,
        path: str,
        *,
        params: Params | None = None,
        json: object = None,
        data: Mapping[str, str] | None = None,
        files: Files | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 -- the HTTP request timeout, not a cancel scope
        retry: bool = True,
    ) -> httpx2.Response:
        """Send a request and return the successful response.

        ``timeout`` replaces the client's default timeout (seconds) for this request. With
        ``retry=False`` transient statuses are not retried, for calls that cost money.

        Raises:
            PaperlessMcpError: The matching subclass for an error status or a failed transport.
        """
        outgoing = self._build(
            method, path, params=params, json=json, data=data, files=files, timeout=timeout
        )
        response = await self._exchange(outgoing, stream=False, retry=retry)
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
        outgoing = self._build("GET", path, params=params)
        response = await self._exchange(outgoing, stream=True)
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
        except httpx2.TransportError as error:
            # Not retried: the status was fine and the connection broke while the body came in;
            # the caller can ask again, and a retry would read the file from the start.
            logger.warning("GET %s failed: %s", outgoing.url.path, type(error).__name__)
            raise error_for_transport(error) from error
        finally:
            await response.aclose()

    async def stream_post(
        self,
        path: str,
        *,
        json: object,
        max_bytes: int,
        max_seconds: float,
    ) -> Streamed:
        """POST ``json`` to ``path`` and read the streamed answer within a size and a time limit.

        At most ``max_bytes`` bytes are kept, and the request, the wait for the first byte
        included, may take ``max_seconds`` in total. Past either limit the connection is closed
        and the bytes that arrived come back ``truncated``; running out of time before the first
        byte is a timeout. A POST is never retried: the answer may cost a model call.

        Deliberately asymmetric with ``download``: running out of time or size keeps the partial
        text (a half answer is useful and marked ``truncated``), whereas a connection that breaks
        mid-body is an error and the partial text is discarded, like a broken download.

        Raises:
            PaperlessMcpError: The matching subclass for an error status, a failed transport, or
                a time limit used up before any byte arrived.
        """
        outgoing = self._build("POST", path, json=json, timeout=max_seconds)
        received: list[bytes] = []
        total = 0
        truncated = False
        try:
            async with asyncio.timeout(max_seconds):
                response = await self._exchange(outgoing, stream=True)
                try:
                    if response.is_error:
                        _ = await response.aread()
                        raise error_for_status(response)
                    async for chunk in response.aiter_bytes():
                        room = max_bytes - total
                        received.append(chunk[:room])
                        total += len(received[-1])
                        if len(chunk) > room:
                            truncated = True
                            break
                finally:
                    await response.aclose()
        except TimeoutError:
            if not total:
                message = "Paperless did not respond in time."
                raise UpstreamTimeout(message, hint=RETRY_LATER) from None
            truncated = True
        except httpx2.TransportError as error:
            logger.warning("POST %s failed: %s", outgoing.url.path, type(error).__name__)
            raise error_for_transport(error) from error
        return Streamed(b"".join(received), truncated=truncated)

    def _build(  # noqa: PLR0913 -- the parts of httpx2's build_request(), keyword-only
        self,
        method: Method,
        path: str,
        *,
        params: Params | None = None,
        json: object = None,
        data: Mapping[str, str] | None = None,
        files: Files | None = None,
        timeout: float | None = None,
    ) -> httpx2.Request:
        name, value = self._credentials.current().header
        limit = self._upload_timeout if files else timeout
        return self._http.build_request(
            method,
            path,
            params=params,
            json=json,
            data=data,
            files=files,
            headers={"Accept": ACCEPT, name: value},
            timeout=httpx2.USE_CLIENT_DEFAULT if limit is None else limit,
        )

    async def _exchange(
        self, outgoing: httpx2.Request, *, stream: bool, retry: bool = True
    ) -> httpx2.Response:
        """Send ``outgoing``, retrying transient statuses; the last response, errors included.

        With ``stream`` the body is not read yet: the caller reads and closes the response.
        """
        method, path = outgoing.method, outgoing.url.path
        attempt, waited = 1, 0.0
        while True:
            response = await self._send(outgoing, stream=stream)
            logger.debug("%s %s -> %d (attempt %d)", method, path, response.status_code, attempt)
            delay = self._retry_delay(response, attempt, waited) if retry else None
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
        timeout: float | None = None,  # noqa: ASYNC109 -- the HTTP request timeout, not a cancel scope
        retry: bool = True,
    ) -> M:
        """Send a request and parse the JSON body of the successful response into ``model``.

        Raises:
            PaperlessMcpError: An error response, a failed transport, or a body ``model`` rejects.
        """
        response = await self.request(
            method,
            path,
            params=params,
            json=json,
            data=data,
            files=files,
            timeout=timeout,
            retry=retry,
        )
        return parse(response, model)

    async def get[M: pydantic.BaseModel](
        self,
        path: str,
        model: type[M],
        *,
        params: Params | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 -- the HTTP request timeout, not a cancel scope
        retry: bool = True,
    ) -> M:
        """GET ``path`` and parse the JSON body into ``model`` (see ``call``)."""
        return await self.call("GET", path, model, params=params, timeout=timeout, retry=retry)

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
            delay = self._jitter(cap)
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
