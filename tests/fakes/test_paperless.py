"""FakePaperless behaviour the contract suite cannot reach with the seeded users.

Each rule mirrors Paperless 3.4.2 (documents/views.py and documents/permissions.py of the tag);
the adapter tests check the adapter's side of the same cases on recorded responses.
"""

import pytest

from fakes.paperless import (
    FakeDocument,
    FakePaperless,
    FakeState,
    FakeTag,
    FakeUser,
    WriteNotAllowedError,
    seeded,
    write,
)

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]


def state_with(*users: FakeUser) -> FakeState:
    state = seeded()
    state.users.extend(users)
    return state


async def test_without_ui_settings_permission_the_user_and_ai_flag_are_unknown() -> None:
    state = state_with(FakeUser(id=10, username="bare", permissions=frozenset({"view_document"})))

    overview = await FakePaperless(state, user="bare").get_overview()

    assert (overview.current_user, overview.ai_enabled) == (None, None)
    assert overview.statistics.documents == 1


async def test_staff_and_system_monitoring_grant_the_system_status() -> None:
    state = state_with(
        FakeUser(id=10, username="staff", is_staff=True),
        FakeUser(id=11, username="monitor", permissions=frozenset({"view_system_monitoring"})),
    )

    for user in ("staff", "monitor"):
        assert (await FakePaperless(state, user=user).get_overview()).system_status is not None


async def test_global_statistics_count_every_object() -> None:
    state = state_with(
        FakeUser(id=10, username="stats", permissions=frozenset({"view_global_statistics"}))
    )

    statistics = (await FakePaperless(state, user="stats").get_overview()).statistics

    assert (statistics.documents, statistics.tags) == (2, 4)


async def test_an_object_level_view_grant_makes_an_object_visible() -> None:
    state = seeded()
    reader = next(user for user in state.users if user.username == "e2e-reader")
    state.documents.append(FakeDocument(id=99, title="granted", owner=1, viewers={reader.id}))

    statistics = (await FakePaperless(state, user="e2e-reader").get_overview()).statistics

    assert statistics.documents == 2


async def test_inbox_documents_are_unknown_without_a_visible_inbox_tag() -> None:
    state = seeded()
    state.tags = [tag for tag in state.tags if not tag.is_inbox]

    statistics = (await FakePaperless(state, user="admin").get_overview()).statistics

    assert statistics.inbox_documents is None


async def test_ai_enabled_follows_the_instance_configuration() -> None:
    state = seeded()
    state.ai_enabled = True

    assert (await FakePaperless(state, user="admin").get_overview()).ai_enabled is True


def test_an_unknown_user_is_a_test_setup_error() -> None:
    with pytest.raises(LookupError, match="nobody"):
        _ = FakePaperless(seeded(), user="nobody")


class TaggingFake(FakePaperless):
    """A test-only write, standing in for the write ports later tasks add."""

    @write
    async def add_tag(self, name: str) -> int:
        tag = FakeTag(id=len(self.state.tags) + 1, name=name, owner=self.user.id)
        self.state.tags.append(tag)
        return tag.id


async def test_a_write_fails_unless_the_test_opts_in() -> None:
    state = seeded()
    before = list(state.tags)

    with pytest.raises(WriteNotAllowedError, match=r"add_tag.*allow_writes=True"):
        _ = await TaggingFake(state, user="admin").add_tag("new")

    assert state.tags == before


async def test_a_write_runs_when_the_test_opts_in() -> None:
    state = seeded()

    tag_id = await TaggingFake(state, user="admin", allow_writes=True).add_tag("new")

    assert [tag.name for tag in state.tags if tag.id == tag_id] == ["new"]


async def test_reads_need_no_opt_in() -> None:
    overview = await TaggingFake(seeded(), user="admin").get_overview()

    assert overview.statistics.documents == 2
