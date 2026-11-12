"""Errors the server reports to clients.

Every message is client-safe: it never carries credentials, stack traces or raw upstream bodies.
``hint`` optionally names the next useful step.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class PaperlessMcpError(Exception):
    """Base of every error with a client-safe message.

    Attributes:
        message: What went wrong, safe to show to the client.
        hint: The next useful step, if there is one.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        """Create the error from a client-safe message and an optional hint."""
        super().__init__(message)
        self.message: str = message
        self.hint: str | None = hint

    def describe(self) -> str:
        """The message, followed by the hint if there is one: what clients and users see."""
        return f"{self.message} Hint: {self.hint}" if self.hint else self.message


class NotFound(PaperlessMcpError):
    """The object does not exist or is not visible to the current user."""


class Ambiguous(PaperlessMcpError):
    """A name matches more than one object; the message lists the candidates."""


class Validation(PaperlessMcpError):
    """Paperless rejected the input."""


# What every refusal for a missing confirmation tells the caller to do.
CONFIRM_HINT = (
    "Describe the effect to the user and call the tool again with the same arguments and "
    "confirm=true only once they agree, or if they already asked for exactly this."
)


class ConfirmationRequired(Validation):
    """A destructive action was asked for without ``confirm=true``; nothing was changed.

    The one mechanism of the confirmation flow (decision-6): services raise it after reading
    what the action would touch, so the message can say what would happen. It is an error, not
    a result, so a client cannot mistake it for a success; elicitation is not used.
    """

    @classmethod
    def for_effect(cls, effect: str) -> ConfirmationRequired:
        """The standard refusal: nothing was changed, and confirming would ``effect``.

        ``effect`` is a verb phrase such as ``move document 5 to the trash``; it names what is
        touched but never document content.
        """
        return cls(f"Nothing was changed. Confirming would {effect}.")

    def __init__(self, message: str, *, hint: str = CONFIRM_HINT) -> None:
        """Create the refusal from a client-safe message; ``hint`` defaults to ``CONFIRM_HINT``."""
        super().__init__(message, hint=hint)


def quoted(text: str, limit: int = 60) -> str:
    """``text`` on one line, cut to ``limit`` characters with an ellipsis, in quotes.

    For naming a document or a note in a message without sending its whole content. Single
    quotes, or double quotes when the text holds a single quote; with both, the single ones
    become typographic apostrophes.
    """
    line = " ".join(text.split())
    shown = line if len(line) <= limit else line[: limit - 1] + "…"
    if "'" not in shown:
        return f"'{shown}'"
    if '"' not in shown:
        return f'"{shown}"'
    return f"'{shown.replace(chr(39), chr(0x2019))}'"


def listed(names: Sequence[str], limit: int = 5) -> str:
    """The first ``limit`` names quoted, then "and N more"; "none" for an empty list."""
    if not names:
        return "none"
    shown = ", ".join(quoted(name) for name in names[:limit])
    rest = len(names) - limit
    return f"{shown} and {rest} more" if rest > 0 else shown


class AuthenticationFailed(PaperlessMcpError):
    """Paperless rejected the credential (wrong, expired or revoked token)."""


class PermissionDenied(PaperlessMcpError):
    """The current user may not perform the action."""


class Conflict(PaperlessMcpError):
    """The action conflicts with the current state of the object."""


class UpstreamUnavailable(PaperlessMcpError):
    """Paperless is unreachable, too slow or temporarily failing."""


class UpstreamTimeout(UpstreamUnavailable):
    """Paperless did not answer within the timeout."""


class UpstreamUnreachable(UpstreamUnavailable):
    """No connection to Paperless could be made."""


class FeatureUnavailable(PaperlessMcpError):
    """The instance has the feature switched off (e.g. the audit log)."""


class UnsupportedVersion(PaperlessMcpError):
    """The Paperless instance does not speak the API version this server needs."""


class InvalidConfiguration(PaperlessMcpError):
    """The server's configuration is wrong or leaves it nothing to offer."""
