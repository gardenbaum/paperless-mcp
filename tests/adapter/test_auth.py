"""StaticTokenProvider: the credential seam's implementation for one configured API token."""

import hashlib

import pytest

from paperless_mcp.paperless.auth import StaticTokenProvider


@pytest.mark.decision("decision-9")
def test_static_token_becomes_a_token_auth_header() -> None:
    credential = StaticTokenProvider("abc123").current()

    assert credential.header == ("Authorization", "Token abc123")


@pytest.mark.decision("decision-9")
def test_fingerprint_is_stable_non_secret_and_distinguishes_tokens() -> None:
    first = StaticTokenProvider("abc123").current().fingerprint

    assert first == StaticTokenProvider("abc123").current().fingerprint
    assert first != StaticTokenProvider("abc124").current().fingerprint
    assert first == hashlib.sha256(b"abc123").hexdigest()[:16]
    assert "abc123" not in first


@pytest.mark.decision("decision-9")
def test_credential_repr_shows_only_the_fingerprint() -> None:
    credential = StaticTokenProvider("abc123").current()

    assert repr(credential) == f"PaperlessCredential(fingerprint='{credential.fingerprint}')"
    assert str(credential) == repr(credential)
