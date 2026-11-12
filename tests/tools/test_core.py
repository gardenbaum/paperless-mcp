"""Tools of the core toolset, called through the in-memory FastMCP client."""

import logging
from dataclasses import replace

import pytest
from fakes.accounts import READER
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client
from fastmcp.exceptions import ToolError
from harness import FailsAfterProbe, admin, serve

from paperless_mcp.domain.errors import ConfirmationRequired, UpstreamUnreachable
from paperless_mcp.tools._shared import boundary

pytestmark = pytest.mark.anyio


async def test_the_overview_is_the_structured_output_of_the_instance() -> None:
    fake = admin()

    async with Client(serve(fake)) as client:
        result = await client.call_tool("get_instance_overview")

    output = result.structured_content
    assert output is not None
    assert output == (await fake.get_overview()).model_dump(mode="json")
    assert output["current_user"] == {
        "id": 2,
        "username": "admin",
        "is_superuser": True,
    }


async def test_what_a_restricted_user_may_not_see_is_null() -> None:
    state = seeded()
    # Without view_uisettings Paperless hides the current user and the AI flag.
    state.users = [
        replace(user, permissions=user.permissions - {"view_uisettings"}) for user in state.users
    ]

    async with Client(serve(FakePaperless(state, user=READER[0]))) as client:
        result = await client.call_tool("get_instance_overview")

    output = result.structured_content
    assert output is not None
    assert (output["system_status"], output["current_user"], output["ai_enabled"]) == (
        None,
        None,
        None,
    )
    assert output["statistics"]["documents"] == 1


async def test_a_domain_error_reaches_the_client_with_its_message_and_hint() -> None:
    error = UpstreamUnreachable("Paperless is unreachable.", hint="Check that it runs.")

    async with Client(serve(FailsAfterProbe(error))) as client:
        result = await client.call_tool("get_instance_overview", raise_on_error=False)

    assert result.is_error
    assert [block.model_dump()["text"] for block in result.content] == [
        "Paperless is unreachable. Hint: Check that it runs."
    ]


async def test_any_other_error_is_masked() -> None:
    async with Client(serve(FailsAfterProbe(KeyError("internal-detail")))) as client:
        result = await client.call_tool("get_instance_overview", raise_on_error=False)

    assert result.is_error
    text = str([block.model_dump() for block in result.content])
    assert "Internal error in get_instance_overview; the server log has details." in text
    assert "internal-detail" not in text


async def test_a_tool_error_passes_the_boundary_unchanged(caplog: pytest.LogCaptureFixture) -> None:
    error = ToolError("Pass confirm=true to delete.")

    async def tool() -> None:
        raise error

    with pytest.raises(ToolError) as raised:
        await boundary(tool)()

    assert raised.value is error
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


@pytest.mark.decision("decision-15")
async def test_a_confirmation_refusal_is_logged_at_info_not_error() -> None:
    async def tool() -> None:
        effect = "delete something"
        raise ConfirmationRequired.for_effect(effect)

    with pytest.raises(ToolError) as raised:
        await boundary(tool)()

    assert raised.value.log_level == logging.INFO
    assert str(raised.value).startswith("Nothing was changed.")
    assert "confirm=true" in str(raised.value)


@pytest.mark.decision("decision-15")
async def test_other_domain_errors_keep_the_error_log_level() -> None:
    async def tool() -> None:
        message = "down"
        raise UpstreamUnreachable(message)

    with pytest.raises(ToolError) as raised:
        await boundary(tool)()

    assert raised.value.log_level == logging.ERROR
