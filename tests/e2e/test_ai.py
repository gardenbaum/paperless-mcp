"""The ai toolset through the assembled server on Paperless 3.4.2.

The stack of `just e2e` has Paperless AI switched off: there the toolset must be hidden. The
tests marked `ai_e2e` need a Paperless with an LLM backend and skip without one (`just e2e-ai`).
"""

from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import BASE_URL
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

AI_TOOLS = {"ask_paperless_ai", "get_ai_suggestions"}
SKIPPED = "AI is disabled on this Paperless; `just e2e-ai` runs this against an AI backend."


async def offered(stack: Stack, toolsets: list[str] | None = None) -> set[str]:
    settings = paperless_settings(stack.base_url, stack.admin_token)
    async with Client(create_server(settings, server_settings(toolsets=toolsets))) as client:
        return {tool.name for tool in await client.list_tools()}


async def test_the_ai_tools_are_hidden_while_paperless_ai_is_disabled(stack: Stack) -> None:
    assert stack.admin.get("/api/ui_settings/").json()["settings"]["ai_enabled"] is False

    assert AI_TOOLS.isdisjoint(await offered(stack))
    assert AI_TOOLS.isdisjoint(await offered(stack, ["all"]))


# --- With an AI backend (`just e2e-ai`) ---------------------------------------------------------


@pytest.mark.ai_e2e
async def test_the_assistant_answers_and_suggests_through_the_server(stack: Stack) -> None:
    if stack.admin.get("/api/ui_settings/").json()["settings"]["ai_enabled"] is not True:
        pytest.skip(SKIPPED)
    settings = paperless_settings(BASE_URL, stack.admin_token)
    async with Client(create_server(settings, server_settings())) as client:
        tools = {tool.name for tool in await client.list_tools()}
        asked = await client.call_tool(
            "ask_paperless_ai", {"question": "What is the e2e public invoice about?"}
        )
        suggested = await client.call_tool(
            "get_ai_suggestions", {"document_id": stack.seeded.public_document}
        )

    assert tools >= AI_TOOLS
    answer, suggestions = asked.structured_content, suggested.structured_content
    assert answer is not None
    assert str(cast("object", answer["answer"])).strip()
    assert suggestions is not None
    assert suggestions["document_id"] == stack.seeded.public_document
