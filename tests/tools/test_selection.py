"""Which tools are offered: toolset selection, read-only mode, disabled tools, capabilities.

The functions are pure, so these tests run them over synthetic tables with toolsets that do not
exist yet (``ai``, ``bulk``): the mechanism is proven for the toolsets later tasks add.
"""

from typing import TYPE_CHECKING

import pytest
from mcp.types import ToolAnnotations

from paperless_mcp.domain.errors import InvalidConfiguration
from paperless_mcp.tools.selection import hidden_tools, selected_toolsets, unavailable_toolsets

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = pytest.mark.decision("decision-4")

KNOWN = frozenset({"core", "ai", "bulk"})
READ = ToolAnnotations(title="R", read_only_hint=True)
WRITE = ToolAnnotations(title="W", read_only_hint=False)
TABLE: Mapping[str, ToolAnnotations] = {"find": READ, "change": WRITE, "plain": ToolAnnotations()}


def test_named_toolsets_are_selected() -> None:
    assert selected_toolsets(("core", "bulk"), KNOWN) == {"core", "bulk"}


def test_all_selects_every_toolset() -> None:
    assert selected_toolsets(("all",), KNOWN) == KNOWN


def test_an_unknown_toolset_is_an_error_naming_it_and_the_known_ones() -> None:
    with pytest.raises(InvalidConfiguration) as caught:
        _ = selected_toolsets(("core", "pdff"), KNOWN)

    assert "pdff" in caught.value.message
    assert "ai, bulk, core" in caught.value.message
    assert "PAPERLESS_MCP_TOOLSETS" in caught.value.message


def test_read_only_hides_every_tool_without_the_read_only_hint() -> None:
    assert hidden_tools(TABLE, read_only=True, disabled=()) == {"change", "plain"}


def test_without_read_only_nothing_is_hidden() -> None:
    assert hidden_tools(TABLE, read_only=False, disabled=()) == frozenset()


def test_disabled_tools_are_hidden() -> None:
    assert hidden_tools(TABLE, read_only=False, disabled=("find",)) == {"find"}


def test_disabled_and_read_only_combine() -> None:
    assert hidden_tools(TABLE, read_only=True, disabled=("find",)) == {"find", "change", "plain"}


def test_an_unknown_disabled_tool_is_an_error_naming_it() -> None:
    with pytest.raises(InvalidConfiguration) as caught:
        _ = hidden_tools(TABLE, read_only=False, disabled=("find", "fnid"))

    assert "fnid" in caught.value.message
    assert "PAPERLESS_MCP_DISABLED_TOOLS" in caught.value.message


REQUIRES = {"ai": "ai"}


@pytest.mark.parametrize(
    ("ai_enabled", "hidden"),
    [(False, {"ai"}), (True, set[str]()), (None, set[str]())],
)
def test_a_toolset_is_unavailable_only_when_its_capability_is_known_missing(
    *, ai_enabled: bool | None, hidden: set[str]
) -> None:
    assert unavailable_toolsets(ai_enabled=ai_enabled, requires=REQUIRES) == hidden


def test_toolsets_without_a_requirement_are_always_available() -> None:
    assert unavailable_toolsets(ai_enabled=False, requires={}) == frozenset()


def test_an_unreachable_paperless_leaves_every_toolset_visible() -> None:
    assert unavailable_toolsets(ai_enabled=None, requires=REQUIRES) == frozenset()
