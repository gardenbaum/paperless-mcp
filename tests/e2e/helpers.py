"""Helpers of the E2E suite: wait for the compose stack, then seed it through the API.

The stack itself is started by `just e2e-up` (tests/e2e/compose.yaml). Seeding is idempotent: every
object is looked up by its name or title first, so rerunning on a seeded stack changes nothing.
"""

import os
import time
from dataclasses import dataclass
from typing import cast

import httpx2
import pytest
from fakes.accounts import ADMIN, READER, READER_PERMISSIONS

PORT = os.environ.get("PAPERLESS_E2E_PORT", "8010")
BASE_URL = f"http://127.0.0.1:{PORT}"
API_VERSION = 10
READY_TIMEOUT = 300.0
CONSUME_TIMEOUT = 180.0
# Every custom field data type of 3.4.2 except `select`, which needs options (see seed()).
DATA_TYPES = (
    *("string", "url", "date", "boolean", "integer", "float", "monetary"),
    *("documentlink", "longtext"),
)
LOGS_HINT = "docker compose -f tests/e2e/compose.yaml logs paperless"

type Json = dict[str, object]


def parse(response: httpx2.Response) -> object:
    """The response's JSON body; the one place untyped JSON enters, callers narrow it."""
    return response.json()  # pyright: ignore[reportAny] -- narrowed by obj/seq/num below


def obj(value: object) -> Json:
    """Narrow a JSON value to an object."""
    assert isinstance(value, dict)
    return cast("Json", value)


def seq(value: object) -> list[object]:
    """Narrow a JSON value to an array."""
    assert isinstance(value, list)
    return cast("list[object]", value)


def num(value: object) -> int:
    """Narrow a JSON value to an integer."""
    assert isinstance(value, int)
    return value


def results(response: httpx2.Response) -> list[Json]:
    """The `results` of a paged list response."""
    return [obj(item) for item in seq(obj(parse(response))["results"])]


@dataclass(frozen=True)
class Seeded:
    """Ids of the seeded objects, by role."""

    parent_tag: int
    child_tag: int
    inbox_tag: int
    private_tag: int
    correspondent: int
    document_type: int
    storage_path: int
    custom_fields: dict[str, int]  # data_type -> field id
    public_document: int  # visible to the restricted user
    private_document: int  # owned by admin, invisible to the restricted user


@dataclass(frozen=True)
class Stack:
    """The running, seeded Paperless instance."""

    base_url: str
    admin: httpx2.Client
    reader: httpx2.Client
    seeded: Seeded
    # Fetched once: Paperless throttles /api/token/.
    admin_token: str
    reader_token: str


def pdf(text: str) -> bytes:
    """Build a one-page PDF with `text` as its text layer (no binary fixtures in the repo)."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources 6 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Font << /F1 5 0 R >> >>",
    ]
    out = b"%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return out


def client(token: str) -> httpx2.Client:
    """An API client pinned to API version 10, authenticated with `token`."""
    return httpx2.Client(
        base_url=BASE_URL,
        headers={
            "Accept": f"application/json; version={API_VERSION}",
            "Authorization": f"Token {token}",
        },
        timeout=30.0,
    )


def token_for(username: str, password: str) -> str:
    """Return the user's API token (Paperless returns the same one on every call)."""
    response = httpx2.post(
        f"{BASE_URL}/api/token/",
        headers={"Accept": f"application/json; version={API_VERSION}"},
        data={"username": username, "password": password},
        timeout=30.0,
    )
    _ = response.raise_for_status()
    return str(obj(parse(response))["token"])


def wait_ready() -> str:
    """Poll until the API answers authenticated requests; return the admin token."""
    deadline = time.monotonic() + READY_TIMEOUT
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with client(token_for(*ADMIN)) as probe:
                response = probe.get("/api/tags/")
            if response.is_success:
                return str(response.request.headers["Authorization"]).removeprefix("Token ")
            last = f"HTTP {response.status_code}"
        except (httpx2.HTTPError, KeyError) as error:
            last = repr(error)
        time.sleep(2)
    message = f"Paperless not ready at {BASE_URL} after {READY_TIMEOUT:.0f}s ({last})."
    pytest.fail(f"{message} Is the stack up (`just e2e-up`)? Container logs: `{LOGS_HINT}`")


def get_or_create(
    api: httpx2.Client, path: str, key: str, body: Json, *, reconcile: Json | None = None
) -> int:
    """Return the id of the object whose `key` equals body[key], creating it if absent.

    `reconcile` fields are also PATCHed onto an existing object, so a reused stack seeded by
    older code converges to the current seed.
    """
    found = results(api.get(path, params={f"{key}__iexact": str(body[key])}).raise_for_status())
    if not found:
        return num(obj(parse(api.post(path, json=body).raise_for_status()))["id"])
    object_id = num(found[0]["id"])
    if reconcile:
        _ = api.patch(f"{path}{object_id}/", json=reconcile).raise_for_status()
    return object_id


def ensure_document(api: httpx2.Client, title: str, text: str) -> int:
    """Upload a generated PDF titled `title` unless it exists; wait for its consumption."""
    found = results(api.get("/api/documents/", params={"title__iexact": title}).raise_for_status())
    if found:
        return num(found[0]["id"])
    uploaded = api.post(
        "/api/documents/post_document/",
        data={"title": title},
        files={"document": (f"{title}.pdf", pdf(text), "application/pdf")},
    ).raise_for_status()
    task_id = str(parse(uploaded))
    deadline = time.monotonic() + CONSUME_TIMEOUT
    while time.monotonic() < deadline:
        tasks = results(api.get("/api/tasks/", params={"task_id": task_id}).raise_for_status())
        task = tasks[0] if tasks else None
        if task and task["status"] == "success":
            return num(seq(task["related_document_ids"])[0])
        if task and task["status"] in {"failure", "revoked"}:
            pytest.fail(f"Consuming {title!r} failed: {task}. Container logs: `{LOGS_HINT}`")
        time.sleep(2)
    pytest.fail(f"Consuming {title!r} timed out after {CONSUME_TIMEOUT:.0f}s. Logs: `{LOGS_HINT}`")


# The seed's manifest per instance statistic: (objects every user sees, objects only the admin
# sees). `fakes.paperless.seeded` matches it exactly; on the persistent live stack other tests may
# add objects, so the contract suite only relies on these as lower bounds.
SEEDED_OBJECTS: dict[str, tuple[int, int]] = {
    "documents": (1, 1),
    "inbox_documents": (1, 0),
    "tags": (3, 1),
    "correspondents": (1, 0),
    "document_types": (1, 0),
    "storage_paths": (1, 0),
}


def seed(admin: httpx2.Client) -> Seeded:
    """Create the fixture data; safe to run any number of times.

    Paperless makes the creator the owner of a new object, which hides it from other users. The
    shared taxonomy is created with `owner: None` so the restricted user sees it; `e2e-private`
    keeps the admin as owner and stays hidden from that user.
    """
    shared: Json = {"owner": None}
    parent = get_or_create(
        admin, "/api/tags/", "name", {"name": "e2e-finance", **shared}, reconcile=shared
    )
    child = get_or_create(
        admin,
        "/api/tags/",
        "name",
        {"name": "e2e-invoices", "parent": parent, **shared},
        reconcile=shared,
    )
    inbox = get_or_create(
        admin,
        "/api/tags/",
        "name",
        {"name": "e2e-inbox", "is_inbox_tag": True, **shared},
        reconcile={"is_inbox_tag": True, **shared},
    )
    private_tag = get_or_create(admin, "/api/tags/", "name", {"name": "e2e-private"})
    correspondent = get_or_create(
        admin, "/api/correspondents/", "name", {"name": "e2e-acme", **shared}, reconcile=shared
    )
    document_type = get_or_create(
        admin, "/api/document_types/", "name", {"name": "e2e-invoice", **shared}, reconcile=shared
    )
    storage_path = get_or_create(
        admin,
        "/api/storage_paths/",
        "name",
        {"name": "e2e-archive", "path": "e2e/{{ created_year }}/{{ title }}", **shared},
        reconcile=shared,
    )
    options = [{"label": "alpha"}, {"label": "beta"}]
    field_bodies: dict[str, Json] = {
        kind: {"name": f"e2e-{kind}", "data_type": kind}
        for kind in (
            "string",
            "url",
            "date",
            "boolean",
            "integer",
            "float",
            "monetary",
            "documentlink",
            "longtext",
        )
    }
    field_bodies["select"] = {
        "name": "e2e-select",
        "data_type": "select",
        "extra_data": {"select_options": options},
    }
    fields = {
        kind: get_or_create(admin, "/api/custom_fields/", "name", body)
        for kind, body in field_bodies.items()
    }

    public = ensure_document(admin, "e2e-public-invoice", "E2E public invoice ACME 2026")
    private = ensure_document(admin, "e2e-private-note", "E2E private admin only note")
    select_field = obj(parse(admin.get(f"/api/custom_fields/{fields['select']}/")))
    option_id = obj(seq(obj(select_field["extra_data"])["select_options"])[0])["id"]
    values: Json = {
        "string": "hello",
        "url": "https://example.com/e2e",
        "date": "2026-01-15",
        "boolean": True,
        "integer": 42,
        "float": 3.25,
        "monetary": "EUR12.50",
        "documentlink": [private],
        "select": option_id,
        "longtext": "line one\nline two",
    }
    _ = admin.patch(
        f"/api/documents/{public}/",
        json={
            "owner": None,  # unowned: visible to every user with the model permission
            "tags": [parent, child, inbox],
            "correspondent": correspondent,
            "document_type": document_type,
            "storage_path": storage_path,
            "custom_fields": [{"field": fields[kind], "value": v} for kind, v in values.items()],
        },
    ).raise_for_status()
    admin_id = num(
        results(admin.get("/api/users/", params={"username__iexact": ADMIN[0]}))[0]["id"]
    )
    _ = admin.patch(
        f"/api/documents/{private}/",
        json={
            "owner": admin_id,
            "tags": [private_tag],
            "set_permissions": {
                "view": {"users": [], "groups": []},
                "change": {"users": [], "groups": []},
            },
        },
    ).raise_for_status()
    _ = get_or_create(
        admin,
        "/api/users/",
        "username",
        {
            "username": READER[0],
            "password": READER[1],
            "user_permissions": READER_PERMISSIONS,
        },
        reconcile={"user_permissions": READER_PERMISSIONS},
    )
    return Seeded(
        parent_tag=parent,
        child_tag=child,
        inbox_tag=inbox,
        private_tag=private_tag,
        correspondent=correspondent,
        document_type=document_type,
        storage_path=storage_path,
        custom_fields=fields,
        public_document=public,
        private_document=private,
    )
