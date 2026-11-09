"""Contract of InstanceReader: the same assertions against the fake and live Paperless 3.4.2."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from contract.conftest import Backend

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]


@pytest.mark.contract("InstanceReader.get_overview")
async def test_overview_reports_versions_and_the_current_user(backend: Backend) -> None:
    overview = await backend.admin.get_overview()

    assert (overview.paperless_version, overview.api_version) == ("3.4.2", 10)
    assert overview.current_user is not None
    assert (overview.current_user.username, overview.current_user.is_superuser) == ("admin", True)
    assert overview.ai_enabled is False


@pytest.mark.contract("InstanceReader.get_overview")
async def test_a_superuser_sees_every_object_and_the_system_status(backend: Backend) -> None:
    overview = await backend.admin.get_overview()

    statistics = overview.statistics
    assert (statistics.documents, statistics.inbox_documents) == (2, 1)
    assert (statistics.tags, statistics.correspondents) == (4, 1)
    assert (statistics.document_types, statistics.storage_paths) == (1, 1)
    status = overview.system_status
    assert status is not None
    assert (status.database.status, status.redis.status) == ("OK", "OK")
    assert status.llm_index.status == "DISABLED"
    assert 0 < status.storage_available_bytes <= status.storage_total_bytes


@pytest.mark.contract("InstanceReader.get_overview")
async def test_a_restricted_user_sees_shared_objects_and_no_system_status(
    backend: Backend,
) -> None:
    overview = await backend.reader.get_overview()

    statistics = overview.statistics
    # Shared taxonomy and the unowned document; the admin's private tag and document stay hidden.
    assert (statistics.documents, statistics.inbox_documents) == (1, 1)
    assert (statistics.tags, statistics.correspondents) == (3, 1)
    assert (statistics.document_types, statistics.storage_paths) == (1, 1)
    assert overview.system_status is None
    assert overview.current_user is not None
    assert (overview.current_user.username, overview.current_user.is_superuser) == (
        "e2e-reader",
        False,
    )
    assert overview.paperless_version == "3.4.2"
