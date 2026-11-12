"""Contract of InstanceReader: the same assertions against the fake and live Paperless 3.4.2."""

from typing import TYPE_CHECKING

import pytest
from e2e.helpers import SEEDED_OBJECTS

if TYPE_CHECKING:
    from contract.conftest import Backend
    from paperless_mcp.domain.instance import InstanceStatistics

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]


def counts(statistics: InstanceStatistics) -> dict[str, int]:
    """The statistics by the names of the seed manifest (an unknown inbox count is none)."""
    return {
        "documents": statistics.documents,
        "inbox_documents": statistics.inbox_documents or 0,
        "tags": statistics.tags,
        "correspondents": statistics.correspondents,
        "document_types": statistics.document_types,
        "storage_paths": statistics.storage_paths,
    }


@pytest.mark.contract("InstanceReader.get_overview")
async def test_overview_reports_versions_and_the_current_user(backend: Backend) -> None:
    overview = await backend.admin.instance.get_overview()

    assert (overview.paperless_version, overview.api_version) == (backend.expected_version, 10)
    assert overview.current_user is not None
    assert (overview.current_user.username, overview.current_user.is_superuser) == ("admin", True)
    assert overview.ai_enabled is False


@pytest.mark.contract("InstanceReader.get_overview")
async def test_a_superuser_sees_the_private_objects_too_and_the_system_status(
    backend: Backend,
) -> None:
    admin = await backend.admin.instance.get_overview()
    reader = await backend.reader.instance.get_overview()

    hidden = {
        name: count - counts(reader.statistics)[name]
        for name, count in counts(admin.statistics).items()
    }
    assert all(hidden[name] >= private for name, (_, private) in SEEDED_OBJECTS.items()), hidden
    status = admin.system_status
    assert status is not None
    assert (status.database.status, status.redis.status) == ("OK", "OK")
    assert status.llm_index.status == "DISABLED"
    assert 0 < status.storage_available_bytes <= status.storage_total_bytes


@pytest.mark.contract("InstanceReader.get_overview")
async def test_a_restricted_user_sees_shared_objects_and_no_system_status(
    backend: Backend,
) -> None:
    overview = await backend.reader.instance.get_overview()

    seen = counts(overview.statistics)
    assert all(seen[name] >= shared for name, (shared, _) in SEEDED_OBJECTS.items()), seen
    assert overview.system_status is None
    assert overview.current_user is not None
    assert (overview.current_user.username, overview.current_user.is_superuser) == (
        "e2e-reader",
        False,
    )
    assert overview.paperless_version == backend.expected_version
