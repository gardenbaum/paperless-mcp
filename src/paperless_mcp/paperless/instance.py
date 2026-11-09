"""InstanceReader on /api/statistics/, /api/status/ and /api/ui_settings/.

Versions come from the ``X-Version`` and ``X-Api-Version`` headers Paperless sends on every
response, so every user gets them. ``/api/remote_version/`` (an update check) and
``/api/profile/`` (it returns the user's API token) are deliberately not used.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel

from paperless_mcp.domain.errors import PermissionDenied
from paperless_mcp.domain.instance import (
    ComponentStatus,
    CurrentUser,
    InstanceOverview,
    InstanceStatistics,
    SystemStatus,
)
from paperless_mcp.paperless.client import parse, unexpected

if TYPE_CHECKING:
    from paperless_mcp.paperless.client import PaperlessClient


class _Statistics(BaseModel):
    documents_total: int
    documents_inbox: int | None
    tag_count: int
    correspondent_count: int
    document_type_count: int
    storage_path_count: int


class _Storage(BaseModel):
    total: int
    available: int


class _Database(BaseModel):
    status: str
    error: str | None


class _Tasks(BaseModel):
    redis_status: str
    redis_error: str | None
    celery_status: str
    celery_error: str | None
    index_status: str
    index_error: str | None
    classifier_status: str
    classifier_error: str | None
    sanity_check_status: str
    sanity_check_error: str | None
    llmindex_status: str
    llmindex_error: str | None


class _Status(BaseModel):
    install_type: str
    storage: _Storage
    database: _Database
    tasks: _Tasks


class _User(BaseModel):
    id: int
    username: str
    is_superuser: bool


class _Settings(BaseModel):
    ai_enabled: bool | None = None


class _UiSettings(BaseModel):
    user: _User
    settings: _Settings


def _system_status(wire: _Status) -> SystemStatus:
    tasks = wire.tasks
    return SystemStatus(
        install_type=wire.install_type,
        storage_total_bytes=wire.storage.total,
        storage_available_bytes=wire.storage.available,
        database=ComponentStatus(status=wire.database.status, error=wire.database.error),
        redis=ComponentStatus(status=tasks.redis_status, error=tasks.redis_error),
        celery=ComponentStatus(status=tasks.celery_status, error=tasks.celery_error),
        search_index=ComponentStatus(status=tasks.index_status, error=tasks.index_error),
        classifier=ComponentStatus(status=tasks.classifier_status, error=tasks.classifier_error),
        sanity_check=ComponentStatus(
            status=tasks.sanity_check_status, error=tasks.sanity_check_error
        ),
        llm_index=ComponentStatus(status=tasks.llmindex_status, error=tasks.llmindex_error),
    )


class PaperlessInstanceReader:
    """``InstanceReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def get_overview(self) -> InstanceOverview:
        """See ``InstanceReader.get_overview``."""
        response = await self._client.request("GET", "/api/statistics/")
        statistics = parse(response, _Statistics)
        try:
            version = response.headers["X-Version"]
            api_version = int(response.headers["X-Api-Version"])
        except (KeyError, ValueError) as error:
            raise unexpected(response) from error
        status = await self._unless_forbidden("/api/status/", _Status)
        ui_settings = await self._unless_forbidden("/api/ui_settings/", _UiSettings)
        user = ui_settings.user if ui_settings else None
        return InstanceOverview(
            paperless_version=version,
            api_version=api_version,
            statistics=InstanceStatistics(
                documents=statistics.documents_total,
                inbox_documents=statistics.documents_inbox,
                tags=statistics.tag_count,
                correspondents=statistics.correspondent_count,
                document_types=statistics.document_type_count,
                storage_paths=statistics.storage_path_count,
            ),
            system_status=_system_status(status) if status else None,
            current_user=(
                CurrentUser(id=user.id, username=user.username, is_superuser=user.is_superuser)
                if user
                else None
            ),
            ai_enabled=ui_settings.settings.ai_enabled if ui_settings else None,
        )

    async def _unless_forbidden[M: BaseModel](self, path: str, model: type[M]) -> M | None:
        """GET ``path``, or None if the user may not see it (HTTP 403)."""
        try:
            return await self._client.get(path, model)
        except PermissionDenied:
            return None
