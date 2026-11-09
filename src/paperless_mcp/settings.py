"""Configuration read from environment variables.

The connection uses the ``PAPERLESS_*`` names other Paperless tools use as well; settings that
belong to this server use the ``PAPERLESS_MCP_*`` prefix.

``BaseSettings`` reads the environment in ``__init__``. Strict type checking sees only the
fields in the synthesized ``__init__``, so load with ``Model.model_validate({})``: pydantic calls
the custom ``__init__`` and the environment applies.
"""

from typing import ClassVar, Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


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
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="PAPERLESS_MCP_", frozen=True
    )

    log_level: LogLevel = "INFO"
    startup_timeout: float = Field(default=5.0, gt=0)
