"""Tools of the core toolset, called through the in-memory FastMCP client."""

from dataclasses import replace

import pytest
from fakes.accounts import READER
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client
from harness import FailsAfterProbe, admin, serve

from paperless_mcp.domain.errors import UpstreamUnreachable

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
