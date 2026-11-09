import os

import pytest
from pydantic import ValidationError

from paperless_mcp.settings import PaperlessSettings, ServerSettings

URL = "http://paperless.internal:8000"
TOKEN = "test-token"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide PAPERLESS_* variables of the developer's shell from every test."""
    for name in os.environ:
        if name.upper().startswith("PAPERLESS_"):
            monkeypatch.delenv(name)


def invalid_fields(
    settings: type[PaperlessSettings | ServerSettings],
) -> list[tuple[int | str, ...]]:
    with pytest.raises(ValidationError) as caught:
        _ = settings.model_validate({})
    return [error["loc"] for error in caught.value.errors()]


@pytest.fixture
def connection_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_URL", URL)
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)


@pytest.mark.usefixtures("connection_env")
def test_connection_is_read_from_paperless_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_PUBLIC_URL", "https://paperless.example.org")

    settings = PaperlessSettings.model_validate({})

    assert str(settings.url) == "http://paperless.internal:8000/"
    assert settings.token.get_secret_value() == TOKEN
    assert str(settings.public_url) == "https://paperless.example.org/"


@pytest.mark.usefixtures("connection_env")
def test_public_url_is_optional() -> None:
    assert PaperlessSettings.model_validate({}).public_url is None


@pytest.mark.usefixtures("connection_env")
def test_token_never_appears_in_repr_or_str() -> None:
    settings = PaperlessSettings.model_validate({})

    assert TOKEN not in repr(settings)
    assert TOKEN not in str(settings)


@pytest.mark.parametrize("missing", ["PAPERLESS_URL", "PAPERLESS_TOKEN"])
@pytest.mark.usefixtures("connection_env")
def test_url_and_token_are_required(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.delenv(missing)

    assert invalid_fields(PaperlessSettings) == [(missing.removeprefix("PAPERLESS_").lower(),)]


@pytest.mark.usefixtures("connection_env")
def test_empty_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_TOKEN", "")

    assert invalid_fields(PaperlessSettings) == [("token",)]


@pytest.mark.parametrize("variable", ["PAPERLESS_URL", "PAPERLESS_PUBLIC_URL"])
@pytest.mark.parametrize("value", ["paperless.internal", "ftp://paperless.internal"])
@pytest.mark.usefixtures("connection_env")
def test_urls_must_be_http_or_https(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    assert invalid_fields(PaperlessSettings) == [(variable.removeprefix("PAPERLESS_").lower(),)]


@pytest.mark.usefixtures("connection_env")
def test_settings_are_immutable() -> None:
    settings = PaperlessSettings.model_validate({})

    with pytest.raises(ValidationError, match="frozen"):
        settings.url = settings.url


def test_log_level_defaults_to_info() -> None:
    assert ServerSettings.model_validate({}).log_level == "INFO"


def test_server_settings_use_the_paperless_mcp_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_LOG_LEVEL", "DEBUG")

    assert ServerSettings.model_validate({}).log_level == "DEBUG"


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_LOG_LEVEL", "VERBOSE")

    assert invalid_fields(ServerSettings) == [("log_level",)]
