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


def test_the_startup_check_is_bounded_by_five_seconds_by_default() -> None:
    assert ServerSettings.model_validate({}).startup_timeout == 5.0


@pytest.mark.parametrize("value", ["0", "-1", "soon"])
def test_the_startup_timeout_must_be_a_positive_number(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_STARTUP_TIMEOUT", value)

    assert invalid_fields(ServerSettings) == [("startup_timeout",)]


def test_the_core_and_ai_toolsets_are_on_by_default() -> None:
    settings = ServerSettings.model_validate({})

    assert (settings.toolsets, settings.read_only, settings.disabled_tools) == (
        ("core", "ai"),
        False,
        (),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("core", ("core",)),
        ("core, pdf ,bulk", ("core", "pdf", "bulk")),
        ("CORE,core", ("core",)),
        ("all", ("all",)),
        ('["core","pdf"]', ("core", "pdf")),
    ],
)
def test_toolsets_are_a_comma_separated_list_or_all(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: tuple[str, ...]
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_TOOLSETS", value)

    assert ServerSettings.model_validate({}).toolsets == expected


@pytest.mark.parametrize("value", ["", " , ", "all,core"])
def test_toolsets_must_name_something_and_all_stands_alone(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_TOOLSETS", value)

    assert invalid_fields(ServerSettings) == [("toolsets",)]


def test_disabled_tools_are_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_DISABLED_TOOLS", "get_task, list_taxonomy,get_task")

    assert ServerSettings.model_validate({}).disabled_tools == ("get_task", "list_taxonomy")


def test_an_empty_disabled_tools_list_disables_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_DISABLED_TOOLS", "")

    assert ServerSettings.model_validate({}).disabled_tools == ()


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("1", True), ("false", False)])
def test_read_only_is_a_boolean(
    monkeypatch: pytest.MonkeyPatch, value: str, *, expected: bool
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_READ_ONLY", value)

    assert ServerSettings.model_validate({}).read_only is expected


def test_read_only_must_be_a_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_READ_ONLY", "maybe")

    assert invalid_fields(ServerSettings) == [("read_only",)]


@pytest.mark.parametrize("variable", ["PAPERLESS_MCP_TOOLSETS", "PAPERLESS_MCP_DISABLED_TOOLS"])
def test_malformed_json_names_both_accepted_formats(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, '["core", ')

    with pytest.raises(ValidationError) as caught:
        _ = ServerSettings.model_validate({})

    message = caught.value.errors()[0]["msg"]
    assert "comma-separated" in message
    assert "JSON array" in message


def test_downloads_up_to_512_kibibytes_are_attached_by_default() -> None:
    assert ServerSettings.model_validate({}).max_download_bytes == 512 * 1024


def test_the_download_limit_has_an_upper_bound_of_32_mebibytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_MAX_DOWNLOAD_BYTES", str(32 * 1024 * 1024))
    assert ServerSettings.model_validate({}).max_download_bytes == 32 * 1024 * 1024

    monkeypatch.setenv("PAPERLESS_MCP_MAX_DOWNLOAD_BYTES", str(32 * 1024 * 1024 + 1))
    assert invalid_fields(ServerSettings) == [("max_download_bytes",)]


def test_the_download_limit_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_MAX_DOWNLOAD_BYTES", "4096")

    assert ServerSettings.model_validate({}).max_download_bytes == 4096


@pytest.mark.parametrize("value", ["0", "-1", "big", "1.5"])
def test_the_download_limit_must_be_a_positive_whole_number(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_MAX_DOWNLOAD_BYTES", value)

    assert invalid_fields(ServerSettings) == [("max_download_bytes",)]
