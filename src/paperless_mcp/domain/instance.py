"""What the instance overview reports: versions, statistics, system status and the current user.

What a restricted user may not see is None, never an error: Paperless shows the system status only
to superusers, staff and holders of ``view_system_monitoring``, and the current user and the AI
flag (UI settings) only to holders of ``view_uisettings``.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class InstanceStatistics(_Frozen):
    """Object counts; without global statistics permission, only of the objects the user sees.

    Attributes:
        inbox_documents: Documents with an inbox tag; None if the user sees no inbox tag.
    """

    documents: int
    inbox_documents: int | None
    tags: int
    correspondents: int
    document_types: int
    storage_paths: int


class ComponentStatus(_Frozen):
    """Health of one Paperless component.

    Attributes:
        status: As Paperless reports it: ``OK``, ``WARNING``, ``ERROR`` or ``DISABLED``.
        error: Paperless's explanation when the status is not ``OK``.
    """

    status: str
    error: str | None


class SystemStatus(_Frozen):
    """Storage and component health of the instance (no internal URLs or host details)."""

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
    """A snapshot of the instance as the current user sees it.

    Attributes:
        system_status: None if the user may not view the system status.
        current_user: None if the user may not view the UI settings.
        ai_enabled: Whether Paperless AI is enabled; None if unknown (as ``current_user``).
    """

    paperless_version: str
    api_version: int
    statistics: InstanceStatistics
    system_status: SystemStatus | None
    current_user: CurrentUser | None
    ai_enabled: bool | None
