"""The stdio entrypoint: configuration and startup failures end with a clear message on stderr."""

import os
import runpy

import pytest
from harness import TOKEN, paperless_answering, refused_url

from paperless_mcp.cli import main
from paperless_mcp.tools._shared import ANNOTATIONS


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the variables a test sets; the developer's PAPERLESS_* stay out."""
    for name in os.environ:
        if name.upper().startswith("PAPERLESS_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("PAPERLESS_MCP_STARTUP_TIMEOUT", "2")


def exit_message(capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    return err


@pytest.mark.usefixtures("isolated_logging")
def test_missing_configuration_names_the_variables(capsys: pytest.CaptureFixture[str]) -> None:
    err = exit_message(capsys)

    assert "paperless-mcp: invalid configuration" in err
    assert "PAPERLESS_URL" in err
    assert "PAPERLESS_TOKEN" in err


@pytest.mark.usefixtures("isolated_logging")
def test_invalid_values_are_named_but_not_echoed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "paperless.internal")
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)
    monkeypatch.setenv("PAPERLESS_MCP_LOG_LEVEL", "LOUD")

    err = exit_message(capsys)

    assert "PAPERLESS_URL" in err
    assert "PAPERLESS_MCP_LOG_LEVEL" in err
    assert "paperless.internal" not in err


@pytest.mark.decision("decision-2")
@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "Paperless rejected the credentials"),
        (406, "Paperless does not accept API version 10."),
    ],
)
@pytest.mark.usefixtures("isolated_logging")
def test_a_rejected_token_or_api_version_stops_the_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: int, message: str
) -> None:
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)
    with paperless_answering(status) as url:
        monkeypatch.setenv("PAPERLESS_URL", url)
        err = exit_message(capsys)

    assert f"paperless-mcp: {message}" in err
    assert "Hint: " in err
    assert TOKEN not in err


@pytest.mark.decision("decision-4")
@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("PAPERLESS_MCP_TOOLSETS", "core,pdff"),
        ("PAPERLESS_MCP_DISABLED_TOOLS", "get_taks"),
    ],
)
@pytest.mark.usefixtures("isolated_logging")
def test_a_misspelt_toolset_or_tool_stops_the_start_naming_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], variable: str, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://paperless.test")
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)
    monkeypatch.setenv(variable, value)

    err = exit_message(capsys)

    assert f"Unknown {'toolset' if 'TOOLSETS' in variable else 'tool'} in {variable}" in err
    assert value.rsplit(",", maxsplit=1)[-1] in err


@pytest.mark.decision("decision-4")
@pytest.mark.usefixtures("isolated_logging")
def test_filters_that_leave_no_tool_stop_the_start_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", refused_url())
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)
    monkeypatch.setenv("PAPERLESS_MCP_DISABLED_TOOLS", ",".join(ANNOTATIONS))

    err = exit_message(capsys)

    assert "paperless-mcp: No tools are left to offer" in err


@pytest.mark.usefixtures("isolated_logging")
def test_python_dash_m_runs_the_same_entrypoint(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        _ = runpy.run_module("paperless_mcp", run_name="__main__")

    assert caught.value.code == 1
    assert "invalid configuration" in capsys.readouterr().err
