"""Name resolution for taxonomy (decision-5) over a per-credential cache.

Callers pass names where Paperless wants ids. A wrong guess files a document under the wrong
tag or correspondent without anyone noticing, so resolution never guesses: a numeric input is an
id, a name must match exactly or, failing that, ignoring case and accents, and it must match one
object. Everything else is an error that names the candidates or the closest names.
"""

import difflib
import time
import unicodedata
from typing import TYPE_CHECKING

from paperless_mcp.domain.errors import Ambiguous, NotFound

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from paperless_mcp.domain.credentials import CredentialProvider
    from paperless_mcp.domain.ports import TaxonomyReader
    from paperless_mcp.domain.taxonomy import TaxonomyItem, TaxonomyKind

SUGGESTIONS = 5


def fold(text: str) -> str:
    """The case- and accent-insensitive form of ``text`` ("Ärzte" and "ARZTE" fold alike)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def pick(kind: TaxonomyKind, items: Sequence[TaxonomyItem], value: str | int) -> TaxonomyItem:
    """The one item of ``items`` that ``value`` names (rules in the module docstring).

    Raises:
        NotFound: No item has the id or name; the hint lists the closest names.
        Ambiguous: More than one item matches; the message lists them.
    """
    label = kind.replace("_", " ")
    text = str(value).strip()
    if isinstance(value, int) or (text.isascii() and text.isdigit()):
        wanted = int(text)
        found = [item for item in items if item.id == wanted]
        if found:
            return found[0]
        message = f"No {label} has id {wanted}."
        raise NotFound(message, hint=f"Call list_taxonomy(kind={kind!r}) to see the ids.")
    exact = [item for item in items if item.name == text]
    folded = [item for item in items if fold(item.name) == fold(text)]
    for matches in (exact, folded):
        if len(matches) == 1:
            return matches[0]
        if matches:
            candidates = ", ".join(f"{item.name!r} (id {item.id})" for item in matches)
            message = f"{len(matches)} {label}s match {text!r}: {candidates}."
            raise Ambiguous(message, hint="Pass the id of the one you mean.")
    message = f"No {label} is named {text!r}."
    raise NotFound(message, hint=_suggest(kind, items, text))


def _suggest(kind: TaxonomyKind, items: Sequence[TaxonomyItem], text: str) -> str:
    names: dict[str, list[str]] = {}
    for item in items:
        names.setdefault(fold(item.name), []).append(item.name)
    close = difflib.get_close_matches(fold(text), names, n=SUGGESTIONS)
    suggestions = [name for key in close for name in names[key]][:SUGGESTIONS]
    listing = f"list_taxonomy(kind={kind!r})"
    if not suggestions:
        return f"Call {listing} to see the names."
    return f"Did you mean {', '.join(map(repr, suggestions))}? Or call {listing} to see all names."


class TaxonomyResolver:
    """Taxonomy rows and name resolution, cached per credential and kind for a TTL.

    The cache key holds the credential fingerprint, so one user's taxonomy never answers
    another's. Writes of this server call ``invalidate``; changes made elsewhere show after the
    TTL.
    """

    def __init__(
        self,
        reader: TaxonomyReader,
        credentials: CredentialProvider,
        *,
        ttl: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Load through ``reader``; ``ttl`` is in seconds of ``clock``."""
        self._reader: TaxonomyReader = reader
        self._credentials: CredentialProvider = credentials
        self._ttl: float = ttl
        self._clock: Callable[[], float] = clock
        # shortcut: entries are replaced, never evicted; bound the cache once many credentials
        # exist (PLMCP-37).
        self._cache: dict[tuple[str, TaxonomyKind], tuple[float, list[TaxonomyItem]]] = {}

    async def items(
        self, kind: TaxonomyKind, name_contains: str | None = None
    ) -> list[TaxonomyItem]:
        """Every visible object of ``kind``, optionally only names containing ``name_contains``.

        The filter ignores case and accents.

        Raises:
            PermissionDenied: The user may not view ``kind``.
        """
        key = (self._credentials.current().fingerprint, kind)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is None or now - cached[0] >= self._ttl:
            cached = (now, await self._reader.list_taxonomy(kind))
            self._cache[key] = cached
        rows = cached[1]
        if name_contains is None:
            return list(rows)
        needle = fold(name_contains)
        return [row for row in rows if needle in fold(row.name)]

    async def resolve(self, kind: TaxonomyKind, value: str | int) -> TaxonomyItem:
        """The object of ``kind`` that ``value`` (an id or a name) names; see ``pick``."""
        return pick(kind, await self.items(kind), value)

    def invalidate(self, kind: TaxonomyKind) -> None:
        """Forget ``kind`` for every credential: a write can change what each user sees."""
        for key in [key for key in self._cache if key[1] == kind]:
            del self._cache[key]
