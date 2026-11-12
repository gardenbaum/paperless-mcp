"""Which tools are offered: toolset selection, read-only mode, disabled tools, capabilities.

Pure functions over the tables in ``_shared``; the composition root applies the results with
FastMCP's visibility (``enable``/``disable``). Every misspelt name is an ``InvalidConfiguration``
at startup, never a silently empty selection.
"""

import difflib
from typing import TYPE_CHECKING

from paperless_mcp.domain.errors import InvalidConfiguration
from paperless_mcp.domain.toolsets import ALL_TOOLSETS as ALL

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from mcp.types import ToolAnnotations


def _suggestions(unknown: Collection[str], known: Collection[str]) -> str:
    """' Did you mean x?' for the unknown names that have a close known name."""
    matches = [
        match[0]
        for name in sorted(unknown)
        if (match := difflib.get_close_matches(name, known, n=1))
    ]
    return f" Did you mean {', '.join(matches)}?" if matches else ""


def selected_toolsets(names: Collection[str], known: Collection[str]) -> frozenset[str]:
    """The toolsets named by ``PAPERLESS_MCP_TOOLSETS``; ``all`` selects every known one."""
    if ALL in names:
        return frozenset(known)
    unknown = sorted(set(names) - set(known))
    if unknown:
        message = (
            f"Unknown toolset in PAPERLESS_MCP_TOOLSETS: {', '.join(unknown)}."
            f"{_suggestions(unknown, known)}"
            f" Known toolsets: {', '.join(sorted(known))}, or {ALL}."
        )
        raise InvalidConfiguration(message)
    return frozenset(names)


def hidden_tools(
    annotations: Mapping[str, ToolAnnotations], *, read_only: bool, disabled: Collection[str]
) -> frozenset[str]:
    """Tools never offered: the disabled ones, and in read-only mode all that may change data."""
    unknown = sorted(set(disabled) - set(annotations))
    if unknown:
        message = (
            f"Unknown tool in PAPERLESS_MCP_DISABLED_TOOLS: {', '.join(unknown)}."
            f"{_suggestions(unknown, annotations)}"
            f" Known tools: {', '.join(sorted(annotations))}."
        )
        raise InvalidConfiguration(message)
    changing = (
        {name for name, hints in annotations.items() if not hints.read_only_hint}
        if read_only
        else set[str]()
    )
    return frozenset(changing | set(disabled))


def unavailable_toolsets(*, ai_enabled: bool | None, requires: Mapping[str, str]) -> frozenset[str]:
    """Toolsets whose Paperless capability is known to be missing.

    ``requires`` maps a toolset to the capability it needs. Only a definite "no" hides a
    toolset: unknown (the user may not view the UI settings, or Paperless did not answer at
    startup) leaves it visible, and its tools report what is wrong when called.
    """
    missing = {"ai"} if ai_enabled is False else set[str]()
    return frozenset(toolset for toolset, capability in requires.items() if capability in missing)


def visible_tools(
    tool_toolsets: Mapping[str, str], toolsets: Collection[str], hidden: Collection[str]
) -> frozenset[str]:
    """The tools left once the selected toolsets and the hidden names are applied."""
    return frozenset(
        name
        for name, toolset in tool_toolsets.items()
        if toolset in toolsets and name not in hidden
    )
