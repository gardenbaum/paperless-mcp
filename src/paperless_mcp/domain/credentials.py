"""The credential seam: the adapter authenticates without knowing where credentials come from."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PaperlessCredential:
    """Opaque credential for one Paperless user.

    Attributes:
        header: The auth header (name, value) to send; secret, hence left out of ``repr``.
        fingerprint: Stable, non-secret identifier of the credential, e.g. for cache keys and logs.
    """

    header: tuple[str, str] = field(repr=False)
    fingerprint: str


class CredentialProvider(Protocol):
    """Supplies the credential for the current request."""

    def current(self) -> PaperlessCredential:
        """Return the credential to authenticate the current request with."""
        ...
