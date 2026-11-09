"""What the instance overview reports: versions, statistics, system status and the current user.

What a restricted user may not see is None, never an error: Paperless shows the system status only
to superusers, staff and holders of ``view_system_monitoring``, and the current user and the AI
flag (UI settings) only to holders of ``view_uisettings``.
"""

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field

# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# (``Field(description=...)``) only where the name does not say enough. Docstrings of these
# classes reach the LLM in every tools/list, once per use of the class.


class _Frozen(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class InstanceStatistics(_Frozen):
    """Object counts, of the objects the user may see unless they may view global statistics."""

    documents: int
    inbox_documents: Annotated[
        int | None,
        Field(description="Documents with an inbox tag; null if no inbox tag is visible."),
    ]
    tags: int
    correspondents: int
    document_types: int
    storage_paths: int


class ComponentStatus(_Frozen):
    """Health of one Paperless component."""

    status: Annotated[str, Field(description="OK, WARNING, ERROR or DISABLED.")]
    error: str | None


class SystemStatus(_Frozen):
    """Storage and component health of the instance."""

    install_type: str
    storage_total_bytes: int
    storage_available_bytes: int
    database: ComponentStatus
    redis: ComponentStatus
    celery: ComponentStatus
    search_index: ComponentStatus
    classifier: ComponentStatus
    sanity_check: ComponentStatus
    llm_index: ComponentStatus


class CurrentUser(_Frozen):
    """The Paperless user the credential belongs to."""

    id: int
    username: str
    is_superuser: bool


class InstanceOverview(_Frozen):
    """The instance as the current user sees it."""

    paperless_version: str
    api_version: int
    statistics: InstanceStatistics
    system_status: Annotated[
        SystemStatus | None,
        Field(description="Null if the user may not view the system status."),
    ]
    current_user: Annotated[
        CurrentUser | None, Field(description="Null if the user may not view the UI settings.")
    ]
    ai_enabled: Annotated[
        bool | None,
        Field(
            description=(
                "Whether Paperless AI is enabled; null means unknown (the user may not view the "
                "UI settings), not disabled."
            )
        ),
    ]
