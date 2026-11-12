"""The tag hierarchy rules of Paperless 3, as pure functions."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from paperless_mcp.services.tag_hierarchy import (
    ancestors,
    blocked_removals,
    close_over_ancestors,
    descendants,
)

# 1 > 2 > 3, 1 > 4, 5 alone
PARENTS: dict[int, int | None] = {1: None, 2: 1, 3: 2, 4: 1, 5: None}


def test_ancestors_run_from_the_parent_to_the_root() -> None:
    assert ancestors(3, PARENTS) == [2, 1]
    assert ancestors(1, PARENTS) == []
    assert ancestors(99, PARENTS) == []


def test_descendants_are_all_children_at_any_depth() -> None:
    assert descendants(1, PARENTS) == {2, 3, 4}
    assert descendants(2, PARENTS) == {3}
    assert descendants(5, PARENTS) == set()


def test_closing_adds_missing_ancestors_after_the_tags_and_keeps_their_order() -> None:
    assert close_over_ancestors([3, 5], PARENTS) == [3, 5, 2, 1]
    assert close_over_ancestors([1, 3], PARENTS) == [1, 3, 2]
    assert close_over_ancestors([], PARENTS) == []


def test_a_cycle_in_the_parents_does_not_hang() -> None:
    cyclic: dict[int, int | None] = {1: 2, 2: 1}

    assert ancestors(1, cyclic) == [2, 1]
    assert descendants(1, cyclic) == {2, 1}
    assert close_over_ancestors([1], cyclic) == [1, 2]


def test_removing_a_parent_is_blocked_by_the_children_that_stay() -> None:
    assert blocked_removals({1}, [1, 2, 3, 5], PARENTS) == {1: [2, 3]}
    assert blocked_removals({2}, [1, 2, 3], PARENTS) == {2: [3]}


def test_removing_a_parent_with_all_its_children_is_not_blocked() -> None:
    assert blocked_removals({1}, [5], PARENTS) == {}
    assert blocked_removals({2}, [1, 4], PARENTS) == {}


tags = st.integers(min_value=1, max_value=5)


@given(chosen=st.lists(tags, max_size=5))
def test_closing_is_idempotent_and_only_adds_ancestors(chosen: list[int]) -> None:
    closed = close_over_ancestors(chosen, PARENTS)

    assert close_over_ancestors(closed, PARENTS) == closed
    assert len(closed) == len(set(closed))
    assert set(chosen) <= set(closed)
    assert all(a in closed for t in closed for a in ancestors(t, PARENTS))
    assert set(closed) - set(chosen) <= {a for t in chosen for a in ancestors(t, PARENTS)}


@given(remaining=st.lists(tags, max_size=5, unique=True), removed=st.sets(tags))
def test_a_closed_remainder_without_the_removed_tags_is_never_blocked(
    remaining: list[int], removed: set[int]
) -> None:
    kept = close_over_ancestors([t for t in remaining if t not in removed], PARENTS)
    kept = [t for t in kept if t not in removed]

    blocked = blocked_removals(removed, kept, PARENTS)

    for tag, children in blocked.items():
        assert tag in removed
        assert set(children) <= set(kept) & descendants(tag, PARENTS)


@pytest.mark.parametrize("removed", [{1}, {2}])
def test_every_blocking_child_is_a_descendant_of_the_removed_tag(removed: set[int]) -> None:
    for tag, children in blocked_removals(removed, [1, 2, 3, 4], PARENTS).items():
        assert set(children) <= descendants(tag, PARENTS)
