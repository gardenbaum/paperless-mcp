"""The exact words of the errors saved views give: they are what an LLM acts on.

One recorded snapshot, so a changed message or hint is a deliberate change that shows up in
review (and a mutated literal cannot go unnoticed).
"""

from typing import TYPE_CHECKING

import pytest
from fakes.paperless import FakeSavedView

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.domain.views import FilterRule, RuleInput, ViewCreate, ViewEdit
from paperless_mcp.services.filter_rules import translate
from paperless_mcp.services.views import rule_type
from services.test_views import Setup

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from syrupy.assertion import SnapshotAssertion

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-15")]


def rules(*pairs: tuple[int, str | None]) -> list[FilterRule]:
    return [FilterRule(rule_type=number, value=value) for number, value in pairs]


def said(action: Callable[[], object]) -> str:
    try:
        _ = action()
    except PaperlessMcpError as error:
        return error.describe()
    return "accepted"


async def said_later(action: Callable[[], Awaitable[object]]) -> str:
    try:
        _ = await action()
    except PaperlessMcpError as error:
        return error.describe()
    return "accepted"


TRANSLATIONS: dict[str, list[FilterRule]] = {
    "unknown type": rules((51, "x")),
    "no value, number": rules((2, None)),
    "no value, flag": rules((5, None)),
    "no value, date": rules((8, None)),
    "no value, json": rules((42, None)),
    "no value, text": rules((0, None)),
    "bad number": rules((2, "x")),
    "bad flag": rules((5, "x")),
    "bad date": rules((8, "x")),
    "bad json": rules((42, "[x")),
    "same type twice": rules((2, "1"), (2, "2")),
    "same filter by two types": rules((19, "a"), (49, "b")),
    "two searches": rules((19, "a"), (0, "b")),
    "bad id list": rules((35, "2,x")),
    "no id list value": rules((35, None)),
    "created range backwards": rules((44, "2026-02-01"), (43, "2026-01-01")),
    "added range backwards": rules((46, "2026-02-01"), (45, "2026-01-01")),
    "three searches": rules((0, "a"), (20, "b"), (21, "5")),
}


def test_filter_rule_errors(snapshot: SnapshotAssertion) -> None:
    found = {
        name: said(lambda r=r: translate(r, None, sort_reverse=False))
        for name, r in TRANSLATIONS.items()
    }
    found["unknown sort field"] = said(lambda: translate([], "bogus", sort_reverse=False))

    assert found == snapshot


def test_rule_type_errors(snapshot: SnapshotAssertion) -> None:
    labels = ["has tags", "does not have", "zzzzqq", " HAS TAG "]

    assert {label: said(lambda t=label: rule_type(t)) for label in labels} == snapshot


async def test_view_service_errors(snapshot: SnapshotAssertion) -> None:
    setup = Setup()
    views = setup.views
    setup.state.saved_views.append(
        FakeSavedView(
            id=2, name="odd", owner=2, filter_rules=(FilterRule(rule_type=99, value="1"),)
        )
    )
    created = {
        "blank name": ViewCreate(name=" "),
        "taken name": ViewCreate(name="E2E-view"),
        "bad sort": ViewCreate(name="n", sort_field="bogus"),
        "bad rule value": ViewCreate(name="n", rules=(RuleInput(rule=2, value="x"),)),
        "unknown label": ViewCreate(name="n", rules=(RuleInput(rule="has tags", value="1"),)),
    }
    edited = {
        "nothing": ViewEdit(),
        "both sort": ViewEdit(sort_field="title", clear_sort=True),
        "both page size": ViewEdit(page_size=3, clear_page_size=True),
        "blank name": ViewEdit(name=" "),
        "taken name": ViewEdit(name="ODD"),
        "bad sort": ViewEdit(sort_field="bogus"),
        "bad rule value": ViewEdit(rules=(RuleInput(rule=2, value="x"),)),
    }
    found: dict[str, str] = {}
    for name, spec in created.items():
        found[f"create, {name}"] = await said_later(lambda s=spec: views.create(s))
    for name, edit in edited.items():
        found[f"update, {name}"] = await said_later(lambda e=edit: views.update("e2e-view", e))
    found["update, missing view"] = await said_later(
        lambda: views.update("nope", ViewEdit(name="x"))
    )
    found["get, near miss"] = await said_later(lambda: views.get("e2e-vew"))
    found["get, missing id"] = await said_later(lambda: views.get(99))
    setup.state.saved_views[0].viewers.add(3)
    other = Setup("e2e-reader", grant=("view_savedview", "delete_savedview", "change_savedview"))
    other.state.saved_views[0].viewers.add(3)
    found["delete, not allowed"] = await said_later(lambda: other.views.delete(1, confirm=False))
    found["update, not allowed"] = await said_later(
        lambda: other.views.update(1, ViewEdit(name="x"))
    )
    found["delete, unconfirmed"] = await said_later(lambda: views.delete("odd", confirm=False))
    found["delete, one rule"] = await said_later(lambda: views.delete("e2e-view", confirm=False))
    found["run, unknown type"] = await said_later(lambda: views.run("odd", page=1, page_size=5))

    assert found == snapshot
