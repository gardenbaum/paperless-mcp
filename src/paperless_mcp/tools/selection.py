"""Which tools are offered: toolset selection, read-only mode, disabled tools, capabilities.

Pure functions over the tables in ``_shared``; the composition root applies the results with
FastMCP's visibility (``enable``/``disable``). Every misspelt name is an ``InvalidConfiguration``
at startup, never a silently empty selection.
"""

from typing import TYPE_CHECKING, Final

from paperless_mcp.domain.errors import InvalidConfiguration

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from mcp.types import ToolAnnotations

# The value of PAPERLESS_MCP_TOOLSETS that selects every toolset.
ALL: Final = "all"


def selected_toolsets(names: Collection[str], known: Collection[str]) -> frozenset[str]:
    """The toolsets named by ``PAPERLESS_MCP_TOOLSETS``; ``all`` selects every known one."""
    if ALL in names:
        return frozenset(known)
    unknown = sorted(set(names) - set(known))
    if unknown:
        message = (
            f"Unknown toolset in PAPERLESS_MCP_TOOLSETS: {', '.join(unknown)}."
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
            " Tool names are listed by the server's tools/list with all toolsets enabled."
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
