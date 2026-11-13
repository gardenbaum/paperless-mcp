"""The sharing toolset through the assembled server against the seeded Paperless 3.4.2 stack.

The stack has no mail server (tests/e2e/compose.yaml sets none), so the e-mail tests cover the
confirmation, the validation and the refusal that says the send failed; a successful send is
covered by the fake and the adapter tests only (task PLMCP-26).
"""

import asyncio
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from fastmcp import Client
from harness import server_settings

from e2e.helpers import results
from paperless_mcp.server import create_server
from paperless_mcp.settings import PaperlessSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]
type Connected = Client[FastMCPTransport]

PUBLIC_URL = "https://docs.example.test/paperless"
PUBLIC, PRIVATE = "e2e-public-invoice", "e2e-private-note"
BUILD_SECONDS = 60.0


@pytest.fixture
async def client(stack: Stack) -> AsyncIterator[Connected]:
    paperless = PaperlessSettings.model_validate(
        {"url": stack.base_url, "token": stack.admin_token, "public_url": PUBLIC_URL}
    )
    server = create_server(paperless, server_settings(toolsets=["core", "sharing"]))
    before_links = {r["id"] for r in results(stack.admin.get("/api/share_links/"))}
    before_bundles = {r["id"] for r in results(stack.admin.get("/api/share_link_bundles/"))}
    async with Client(server) as connected:
        yield connected
    # Whatever a test left behind, also when it failed; shares of other runs are not touched.
    for row in results(stack.admin.get("/api/share_links/")):
        if row["id"] not in before_links:
            _ = stack.admin.delete(f"/api/share_links/{row['id']}/")
    for row in results(stack.admin.get("/api/share_link_bundles/")):
        if row["id"] not in before_bundles:
            _ = stack.admin.delete(f"/api/share_link_bundles/{row['id']}/")


async def call(client: Connected, tool: str, **arguments: object) -> Json:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def error(client: Connected, tool: str, **arguments: object) -> str:
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


async def document_ids(client: Connected) -> dict[str, int]:
    found = await call(client, "search_documents", page_size=100)
    return {
        str(d["title"]): int(cast("int", d["id"])) for d in cast("list[Json]", found["documents"])
    }


def served(stack: Stack, url: object) -> httpx2.Response:
    """The share fetched from the stack: the public URL's path under the stack's own address."""
    assert str(url).startswith(f"{PUBLIC_URL}/share/")
    return httpx2.get(f"{stack.base_url}/share/{str(url).rsplit('/', 1)[1]}")


async def test_a_link_is_created_served_listed_and_deleted(client: Connected, stack: Stack) -> None:
    ids = await document_ids(client)

    made = await call(client, "create_share_link", document=ids[PUBLIC], expires_in_days=2)

    assert str(made["url"]).startswith(f"{PUBLIC_URL}/share/")
    assert made["document_title"] == PUBLIC
    assert made["expired"] is False
    assert made["expiration"] is not None
    fetched = served(stack, made["url"])
    assert fetched.status_code == httpx2.codes.OK
    assert fetched.headers["content-type"] == "application/pdf"
    listed = await call(client, "list_share_links", document=ids[PUBLIC])
    assert made["url"] in {link["url"] for link in cast("list[Json]", listed["links"])}

    text = await error(client, "delete_share_link", link=made["id"])
    assert "Nothing was changed" in text
    assert f"delete share link {made['id']} for the document '{PUBLIC}'" in text
    assert served(stack, made["url"]).status_code == httpx2.codes.OK
    gone = await call(client, "delete_share_link", link=made["id"], confirm=True)
    assert gone["id"] == made["id"]
    after = await call(client, "list_share_links", document=ids[PUBLIC])
    assert made["url"] not in {link["url"] for link in cast("list[Json]", after["links"])}


async def test_a_link_for_the_original_file_and_without_expiry(client: Connected) -> None:
    ids = await document_ids(client)

    made = await call(client, "create_share_link", document=ids[PRIVATE], file_version="original")

    assert (made["file_version"], made["expiration"]) == ("original", None)


async def test_a_bundle_is_built_served_rebuilt_and_deleted(
    client: Connected, stack: Stack
) -> None:
    ids = await document_ids(client)

    made = await call(
        client,
        "create_share_bundle",
        documents=[ids[PUBLIC], ids[PRIVATE]],
        expires_in_days=1,
    )

    assert made["status"] in {"pending", "processing", "ready"}
    assert made["document_count"] == 2
    ready = await ready_bundle(client, made["id"])
    assert (ready["status"], ready["url"]) == ("ready", made["url"])
    fetched = served(stack, made["url"])
    assert fetched.headers["content-type"] == "application/zip"

    rebuilt = await call(client, "rebuild_share_bundle", bundle=made["id"])
    assert (rebuilt["url"], rebuilt["status"]) == (made["url"], "pending")
    assert (await ready_bundle(client, made["id"]))["status"] == "ready"

    text = await error(client, "delete_share_bundle", bundle=made["id"])
    assert f"delete share-link bundle {made['id']} of 2 documents" in text
    _ = await call(client, "delete_share_bundle", bundle=made["id"], confirm=True)
    rows = cast("list[Json]", (await call(client, "list_share_bundles", page_size=100))["bundles"])
    assert made["id"] not in {row["id"] for row in rows}


async def ready_bundle(client: Connected, bundle_id: object) -> Json:
    deadline = asyncio.get_running_loop().time() + BUILD_SECONDS
    while True:
        listed = await call(client, "list_share_bundles", page_size=100)
        [row] = [b for b in cast("list[Json]", listed["bundles"]) if b["id"] == bundle_id]
        if row["status"] in {"ready", "failed"}:
            return row
        assert asyncio.get_running_loop().time() < deadline, "the bundle was not built in time"
        await asyncio.sleep(0.5)


async def test_an_email_asks_for_confirmation_naming_recipients_and_documents(
    client: Connected,
) -> None:
    ids = await document_ids(client)

    text = await error(
        client,
        "email_documents",
        documents=[ids[PUBLIC]],
        recipients=["someone@example.com"],
        subject="Invoice",
        message="Attached.",
    )

    assert "Nothing was changed" in text
    assert f"'{PUBLIC}' (id {ids[PUBLIC]})" in text
    assert "someone@example.com" in text


async def test_an_email_without_a_mail_server_fails_and_says_so(client: Connected) -> None:
    ids = await document_ids(client)

    text = await error(
        client,
        "email_documents",
        documents=[ids[PUBLIC]],
        recipients=["someone@example.com"],
        subject="Invoice",
        message="Attached.",
        confirm=True,
    )

    # No mail server on the stack: Paperless answers 500 and the tool says the send failed.
    assert "could not send the e-mail" in text
    assert "PAPERLESS_EMAIL_HOST" in text


async def test_an_email_to_an_invalid_address_is_refused_before_the_confirmation(
    client: Connected,
) -> None:
    ids = await document_ids(client)

    text = await error(
        client,
        "email_documents",
        documents=[ids[PUBLIC]],
        recipients=["nobody"],
        subject="Invoice",
        message="Attached.",
    )

    assert "'nobody' is not an e-mail address" in text
    assert "Nothing was changed" not in text
