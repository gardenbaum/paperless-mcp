"""The tag hierarchy rules of Paperless 3, as pure functions over a tag-to-parent map.

On a document, a child tag needs its parent: Paperless adds the ancestors of every tag added and
takes the descendants of every tag removed. Updates therefore close the wanted tags over their
ancestors before they diff and send, and refuse removals that would take other tags along.
Callers (document update now, bulk edit later) pass ``{tag id: parent id or None}``.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping


def ancestors(tag: int, parents: Mapping[int, int | None]) -> list[int]:
    """The parent of ``tag``, its parent and so on up to the root."""
    found: list[int] = []
    step = parents.get(tag)
    while step is not None and step not in found:
        found.append(step)
        step = parents.get(step)
    return found


def descendants(tag: int, parents: Mapping[int, int | None]) -> set[int]:
    """Every tag below ``tag``, at any depth."""
    found: set[int] = set()
    pending = [tag]
    while pending:
        current = pending.pop()
        children = {child for child, parent in parents.items() if parent == current}
        pending.extend(children - found)
        found |= children
    return found


def close_over_ancestors(tags: Iterable[int], parents: Mapping[int, int | None]) -> list[int]:
    """``tags`` without duplicates, then the ancestors they lack, nearest first."""
    result = list(dict.fromkeys(tags))
    for tag in list(result):
        result.extend(a for a in ancestors(tag, parents) if a not in result)
    return result


def blocked_removals(
    removed: Collection[int], remaining: Iterable[int], parents: Mapping[int, int | None]
) -> dict[int, list[int]]:
    """The removed tags that would take tags with them: removed tag to the remaining children.

    ``remaining`` is what the document would keep. Removing a tag whose descendants stay is not
    possible: Paperless drops the descendants too.
    """
    kept = list(dict.fromkeys(remaining))
    blocked = {
        tag: [child for child in kept if child in descendants(tag, parents)] for tag in removed
    }
    return {tag: children for tag, children in blocked.items() if children}
