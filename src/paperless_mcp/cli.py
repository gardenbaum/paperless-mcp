"""Command-line entrypoint (stdio transport): ``paperless-mcp`` and ``python -m paperless_mcp``.

Configuration errors and a Paperless that rejects the token or API version end the process
with exit code 1 and one line on stderr, without a traceback.
"""

import sys
from typing import NoReturn

from pydantic import ValidationError
from pydantic_settings import BaseSettings

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.observability import configure_logging
from paperless_mcp.server import create_server
from paperless_mcp.settings import PaperlessSettings, ServerSettings


def _fail(message: str) -> NoReturn:
    print(f"paperless-mcp: {message}", file=sys.stderr)  # noqa: T201 -- the user-facing exit message
    raise SystemExit(1)


def _load[S: BaseSettings](kind: type[S], problems: list[str]) -> S | None:
    """The settings from the environment, or None with one line per invalid variable."""
    try:
        return kind.model_validate({})
    except ValidationError as error:
        prefix = kind.model_config.get("env_prefix") or ""
        # Only the variable and the reason: the input may be a token.
        problems.extend(
            f"{prefix}{'_'.join(map(str, problem['loc'])).upper()}: {problem['msg']}"
            for problem in error.errors()
        )
        return None


def main() -> None:
    """Run the server over stdio."""
    problems: list[str] = []
    paperless = _load(PaperlessSettings, problems)
    settings = _load(ServerSettings, problems)
    if paperless is None or settings is None:
        _fail("invalid configuration: " + "; ".join(problems))
    configure_logging(settings.log_level)
    try:
        # No banner: it is noise on stderr and would check PyPI for updates (decision-7).
        create_server(paperless, settings, stdio=True).run(show_banner=False)
    except PaperlessMcpError as error:
        _fail(error.describe())
