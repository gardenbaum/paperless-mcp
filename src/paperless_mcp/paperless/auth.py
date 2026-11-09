"""Credential providers that know the Paperless auth schemes."""

import hashlib
from typing import override

from paperless_mcp.domain.credentials import PaperlessCredential

FINGERPRINT_LENGTH = 16


class StaticTokenProvider:
    """One API token for every request (``Authorization: Token <token>``)."""

    def __init__(self, token: str) -> None:
        """Wrap the API token; it is kept only inside the credential."""
        fingerprint = hashlib.sha256(token.encode()).hexdigest()[:FINGERPRINT_LENGTH]
        self._credential: PaperlessCredential = PaperlessCredential(
            header=("Authorization", f"Token {token}"), fingerprint=fingerprint
        )

    def current(self) -> PaperlessCredential:
        """Return the static credential."""
        return self._credential

    @override
    def __repr__(self) -> str:
        return f"StaticTokenProvider(fingerprint={self._credential.fingerprint!r})"
