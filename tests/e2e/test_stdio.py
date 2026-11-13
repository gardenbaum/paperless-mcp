"""Smoke test of the installed `paperless-mcp` console script over stdio against Paperless 3.4.2.

This is how Claude Code runs the server (`claude mcp add`): a subprocess speaking MCP on
stdin/stdout, configured by environment variables, logging to stderr.
"""

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from e2e.helpers import EXPECTED_VERSION

if TYPE_CHECKING:
    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]


def console_script() -> str:
    path = shutil.which("paperless-mcp")
    assert path, "the paperless-mcp console script is not installed (uv sync)"
    return path


async def test_claude_code_can_call_get_instance_overview_over_stdio(stack: Stack) -> None:
    transport = StdioTransport(
        console_script(),
        [],
        env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token},
        keep_alive=False,
    )

    async with Client(transport) as client:
        result = await client.call_tool("get_instance_overview")

    output = result.structured_content
    assert output is not None
    assert (output["paperless_version"], output["api_version"]) == (EXPECTED_VERSION, 10)
    assert output["current_user"]["username"] == "admin"
    assert output["system_status"] is not None


@pytest.mark.decision("decision-2")
def test_a_wrong_token_stops_the_console_script_with_a_clear_message(stack: Stack) -> None:
    finished = subprocess.run(  # noqa: S603 -- our own console script, no shell
        [console_script()],
        env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": "not-a-token"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert finished.returncode == 1
    assert finished.stdout == ""
    assert "paperless-mcp: Paperless rejected the credentials" in finished.stderr
    assert "Traceback" not in finished.stderr


async def listed_tools(stack: Stack, **env: str) -> set[str]:
    transport = StdioTransport(
        console_script(),
        [],
        env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token, **env},
        keep_alive=False,
    )
    async with Client(transport) as client:
        return {tool.name for tool in await client.list_tools()}


@pytest.mark.decision("decision-4")
async def test_the_environment_selects_the_tools_over_stdio(stack: Stack) -> None:
    default = await listed_tools(stack)
    read_only = await listed_tools(
        stack, PAPERLESS_MCP_TOOLSETS="all", PAPERLESS_MCP_READ_ONLY="true"
    )
    trimmed = await listed_tools(stack, PAPERLESS_MCP_DISABLED_TOOLS="get_task, list_taxonomy")

    assert "get_instance_overview" in default
    writing = {
        "update_document",
        "add_note",
        "delete_note",
        "delete_document",
        "upload_document",
    } | {
        "create_taxonomy_item",
        "update_taxonomy_item",
        "delete_taxonomy_item",
    }
    # `all` adds the toolsets that are off by default: of versions only the listing is read-only.
    assert read_only == (default - writing) | {"list_document_versions"}
    assert trimmed == default - {"get_task", "list_taxonomy"}
