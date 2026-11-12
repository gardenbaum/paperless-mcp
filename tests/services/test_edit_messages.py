"""The exact words of the errors an update gives: they are what an LLM acts on.

One recorded snapshot per source of errors, so a changed message or hint is a deliberate change
that shows up in review (and a mutated literal cannot go unnoticed).
"""

from typing import TYPE_CHECKING

import pytest
from fakes.paperless import FakeTag

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.domain.taxonomy import SelectOption, TaxonomyItem
from paperless_mcp.services.custom_values import normalise
from services.test_document_edits import Setup, as_editor

if TYPE_CHECKING:
    from pydantic import JsonValue
    from syrupy.assertion import SnapshotAssertion

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

OPTIONS = (("a", "Open"), ("b", "OPEN"), ("c", "Closed"))


def field(data_type: str) -> TaxonomyItem:
    options = tuple(SelectOption(id=i, label=label) for i, label in OPTIONS)
    return TaxonomyItem(
        id=1,
        name=f"my-{data_type}",
        document_count=0,
        data_type=data_type,
        select_options=options if data_type == "select" else None,
    )


def said(data_type: str, value: JsonValue) -> str:
    try:
        _ = normalise(field(data_type), value)
    except PaperlessMcpError as error:
        return error.describe()
    return "accepted"


VALUES: list[tuple[str, JsonValue]] = [
    ("string", 5),
    ("string", "x" * 129),
    ("longtext", 5),
    ("url", "nope"),
    ("date", "03.02.2026"),
    ("boolean", "yes"),
    ("integer", 1.5),
    ("integer", 2**31),
    ("float", "1.5"),
    ("monetary", "twelve"),
    ("monetary", "EUR12.505"),
    ("monetary", "EUR12345678901"),
    ("documentlink", "1"),
    ("select", 5),
    ("select", "Gamma"),
    ("select", "open"),
    ("select", None),
    ("holographic", "x"),
]


def test_the_errors_of_every_data_type(snapshot: SnapshotAssertion) -> None:
    assert {f"{kind} {value!r}": said(kind, value) for kind, value in VALUES} == snapshot


def test_the_edge_of_what_a_string_takes() -> None:
    assert said("string", "x" * 128) == "accepted"
    assert said("string", "x" * 129) != "accepted"


async def said_by(setup: Setup, document: int = 1, /, **edit: object) -> str:
    try:
        _ = await setup.update(document, **edit)
    except PaperlessMcpError as error:
        return error.describe()
    return "accepted"


async def cases() -> dict[str, str]:
    found: dict[str, str] = {}
    plain: dict[str, dict[str, object]] = {
        "tags_set with add": {"tags_set": ["a"], "tags_add": ["b"]},
        "clear and set correspondent": {"correspondent": "e2e-acme", "clear": ["correspondent"]},
        "clear and set document type": {"document_type": "e2e-invoice", "clear": ["document_type"]},
        "clear and set storage path": {"storage_path": "e2e-archive", "clear": ["storage_path"]},
        "clear and set serial number": {
            "archive_serial_number": 5,
            "clear": ["archive_serial_number"],
        },
        "clear and set two": {
            "correspondent": "e2e-acme",
            "document_type": "e2e-invoice",
            "clear": ["correspondent", "document_type"],
        },
        "clear and set owner": {"owner": "admin", "clear": ["owner"]},
        "blank title": {"title": " "},
        "long title": {"title": "x" * 129},
        "nothing": {},
        "tag added and removed": {"tags_add": ["A"], "tags_remove": ["a"]},
        "tag added and removed by id": {"tags_add": ["e2e-finance"], "tags_remove": [1]},
        "field set twice": {"custom_fields": {"e2e-string": "x", "1": "y"}},
        "field set and removed": {
            "custom_fields": {"e2e-string": "x"},
            "remove_custom_fields": [1],
        },
        "parent removal": {"tags_remove": ["e2e-finance"]},
        "owner without confirm": {"owner": "admin"},
        "unseen link": {"custom_fields": {"e2e-documentlink": [99]}},
        "unknown tag": {"tags_add": ["e2e-new"]},
        "unknown tag to remove": {"tags_remove": ["e2e-financ"]},
        "unknown owner id": {"owner": 99, "confirm": True},
    }
    for name, edit in plain.items():
        found[name] = await said_by(Setup(), **edit)
    exact = await said_by(Setup(), title="x" * 128, tags_remove=["e2e-inbox"])
    found["title of 128"] = exact

    two_children = Setup()
    two_children.state.tags.append(FakeTag(id=5, name="e2e-receipts", parent=1))
    two_children.document.tags = [1, 2, 5]
    found["parent removal with two children"] = await said_by(two_children, tags_remove=[1])

    setup = Setup()
    found["failure after tag creation"] = await said_by(
        setup, archive_serial_number=10, tags_add=["n1", "n2"], create_missing_tags=True
    )
    found["no change permission"] = await said_by(as_editor(), 3, title="x")
    found["link without permission"] = await said_by(
        as_editor(), 4, custom_fields={"e2e-documentlink": [3]}
    )
    unlisted = Setup("e2e-reader", can_change=True)
    found["owner name unlistable"] = await said_by(unlisted, owner="admin", confirm=True)
    return found


async def test_the_errors_of_an_update(snapshot: SnapshotAssertion) -> None:
    assert await cases() == snapshot
