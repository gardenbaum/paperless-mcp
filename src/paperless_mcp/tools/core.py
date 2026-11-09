"""The core toolset: everyday reading (and later writing) of documents and taxonomy."""

from fastmcp import Context
from fastmcp.server.providers import LocalProvider

from paperless_mcp.domain.instance import InstanceOverview
from paperless_mcp.tools._shared import ANNOTATIONS, boundary, services

TOOLSET = "core"

provider = LocalProvider()


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_instance_overview"])
@boundary
async def get_instance_overview(ctx: Context) -> InstanceOverview:
    """Describe the Paperless instance as the current user sees it.

    Reports the Paperless and API version, the document, inbox and taxonomy counts, the system
    health (storage, database, task queue, search index, classifier), the current user and
    whether Paperless AI is enabled. Use it first to orient yourself, or to diagnose failures.

    Counts include only what the user may see, unless they may view global statistics. A null
    field means the user may not see it, not that it is off: system_status needs the
    system-monitoring permission; current_user and ai_enabled need permission to view the UI
    settings, so ai_enabled null means unknown. inbox_documents is null if the user sees no
    inbox tag.
    """
    return await services(ctx).instance.overview()
