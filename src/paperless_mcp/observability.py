"""Logging: one JSON object per line on stderr, and one record per tool call.

stdout belongs to the MCP stdio transport, so nothing else may write there. Records never carry
document content, tokens or query text at INFO or above: tool arguments, error messages and
exception causes are not logged, and httpx2/httpcore2 (which log request URLs with their query
at INFO) are held at WARNING whatever the configured level.
"""

import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, cast, override

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

if TYPE_CHECKING:
    from fastmcp.tools import ToolResult
    from mcp.types import CallToolRequestParams

# Loggers that would put request URLs, and with them search text, into INFO records.
QUIET_LOGGERS: Final = ("httpx2", "httpcore2")

# The id of the tool call being handled; every record logged during the call carries it.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

logger = logging.getLogger(__name__)

# Attributes every LogRecord has; anything else on a record came in through ``extra``.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON line: time, level, logger, message, request id, extras."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        current = request_id.get()
        if current is not None:
            payload["request_id"] = current
        extras = cast("dict[str, object]", vars(record))
        payload |= {key: value for key, value in extras.items() if key not in _STANDARD}
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    """Send every record of ``level`` and above to stderr as JSON; quiet the HTTP loggers."""
    handler = logging.StreamHandler()  # stderr
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


class CallLogging(Middleware):
    """Log each tool call with a fresh request id, the tool name, its duration and outcome."""

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        token = request_id.set(uuid.uuid4().hex)
        started = time.perf_counter()
        outcome: dict[str, object] = {"outcome": "ok"}
        try:
            return await call_next(context)
        except Exception as error:
            # The boundary raises ToolError from None and FastMCP masks with `from error`:
            # name the original error's type, never its message.
            cause = error.__cause__ or error.__context__ or error
            outcome = {"outcome": "error", "error": type(cause).__name__}
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "tool call",
                extra={"tool": context.message.name, "duration_ms": duration_ms, **outcome},
            )
            request_id.reset(token)
