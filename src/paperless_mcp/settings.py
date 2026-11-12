"""Configuration read from environment variables.

The connection uses the ``PAPERLESS_*`` names other Paperless tools use as well; settings that
belong to this server use the ``PAPERLESS_MCP_*`` prefix.

``BaseSettings`` reads the environment in ``__init__``. Strict type checking sees only the
fields in the synthesized ``__init__``, so load with ``Model.model_validate({})``: pydantic calls
the custom ``__init__`` and the environment applies.
"""

import json
from typing import Annotated, ClassVar, Literal, cast

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from paperless_mcp.domain.toolsets import ALL_TOOLSETS

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _names(value: object, *, lower: bool) -> object:
    """A list setting: a JSON array or comma-separated names, trimmed, without duplicates."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    try:
        parsed = cast("list[object]", json.loads(text) if text.startswith("[") else text.split(","))
    except ValueError:
        message = (
            'not valid JSON; use a comma-separated list (core,pdf) or a JSON array (["core","pdf"])'
        )
        raise ValueError(message) from None
    names = [str(item).strip() for item in parsed]
    names = [name.lower() if lower else name for name in names if name]
    return tuple(dict.fromkeys(names))


class PaperlessSettings(BaseSettings):
    """Connection to the paperless-ngx instance.

    Attributes:
        url: Base URL this server uses to reach Paperless (``PAPERLESS_URL``).
        token: API token (``PAPERLESS_TOKEN``); masked in ``repr`` and ``str``.
        public_url: Base URL for links handed to users when it differs from ``url``
            (``PAPERLESS_PUBLIC_URL``).
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="PAPERLESS_", frozen=True
    )

    url: HttpUrl
    token: SecretStr = Field(min_length=1)
    public_url: HttpUrl | None = None


class ServerSettings(BaseSettings):
    """Settings of this MCP server.

    Attributes:
        log_level: Minimum level of log records written to stderr
            (``PAPERLESS_MCP_LOG_LEVEL``).
        startup_timeout: Most seconds the startup check of the Paperless connection may take
            (``PAPERLESS_MCP_STARTUP_TIMEOUT``); a Paperless that does not answer in time does
            not stop the start.
        toolsets: Toolsets whose tools are offered (``PAPERLESS_MCP_TOOLSETS``): names, or
            ``all``; comma-separated or a JSON array. Default ``core,ai``; ``ai`` is hidden
            while Paperless AI is disabled. The ai tools are read-only but each call may cost
            money (Paperless asks its LLM backend), so clients that auto-approve read-only tools
            will run them unasked: set ``core`` to leave them out, or name them in
            ``PAPERLESS_MCP_DISABLED_TOOLS`` (``ask_paperless_ai,get_ai_suggestions``).
        read_only: Offer only tools that do not change anything (``PAPERLESS_MCP_READ_ONLY``).
        disabled_tools: Tools never offered, whatever the toolsets say
            (``PAPERLESS_MCP_DISABLED_TOOLS``).
        max_download_bytes: Largest file ``download_document`` attaches
            (``PAPERLESS_MCP_MAX_DOWNLOAD_BYTES``, 1 byte to 32 MiB); larger files are reported
            with their link only. An embedded file travels base64-encoded (a third larger) in one
            MCP message, and the FastMCP client's default limit for one HTTP event is 1 MiB, so
            the default of 512 KiB (about 700 KB on the wire) fits that with room for the report.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="PAPERLESS_MCP_", frozen=True
    )

    log_level: LogLevel = "INFO"
    startup_timeout: float = Field(default=5.0, gt=0)
    toolsets: Annotated[tuple[str, ...], NoDecode] = ("core", "ai")
    read_only: bool = False
    disabled_tools: Annotated[tuple[str, ...], NoDecode] = ()
    max_download_bytes: int = Field(default=512 * 1024, ge=1, le=32 * 1024 * 1024)

    @field_validator("toolsets", mode="before")
    @classmethod
    def _split_toolsets(cls, value: object) -> object:
        return _names(value, lower=True)

    @field_validator("disabled_tools", mode="before")
    @classmethod
    def _split_disabled_tools(cls, value: object) -> object:
        return _names(value, lower=False)

    @field_validator("toolsets", mode="after")
    @classmethod
    def _check_toolsets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            message = "name at least one toolset, or all"
            raise ValueError(message)
        if ALL_TOOLSETS in value and len(value) > 1:
            message = f"{ALL_TOOLSETS} stands alone; do not combine it with toolset names"
            raise ValueError(message)
        return value
