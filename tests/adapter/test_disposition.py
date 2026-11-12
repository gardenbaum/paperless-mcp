"""The file name of a Content-Disposition header: both forms, bad input, never a path."""

import pytest

from paperless_mcp.paperless.disposition import filename


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        ('attachment; filename="plain name.pdf"', "plain name.pdf"),
        ("attachment; filename=bare.pdf", "bare.pdf"),
        ('attachment; filename="a \\"quoted\\" name.pdf"', 'a "quoted" name.pdf'),
        ('ATTACHMENT; FILENAME="Upper.PDF"', "Upper.PDF"),
        ("attachment; filename*=UTF-8''caf%C3%A9.pdf", "café.pdf"),
        ("attachment; filename*=iso-8859-1'en'caf%E9.pdf", "café.pdf"),
        ("attachment; filename*=UTF-8''%FF%FE.pdf", None),
        ("attachment; filename*=UTF-8''%FF%FE.pdf; filename=\"fallback.pdf\"", "fallback.pdf"),
        ("attachment; filename*=x-unknown''a.pdf; filename=\"fallback.pdf\"", "fallback.pdf"),
        ('attachment; filename*=no-quotes.pdf; filename="fallback.pdf"', "fallback.pdf"),
        ("attachment; filename*=UTF-8''x%C3%A4.pdf; filename=\"x.pdf\"", "xä.pdf"),
        ("attachment; filename=\"x.pdf\"; filename*=UTF-8''x%C3%A4.pdf", "xä.pdf"),
        ('attachment; filename="../../etc/passwd"', "passwd"),
        ('attachment; filename="..\\\\evil.pdf"', "evil.pdf"),
        ("attachment; filename*=UTF-8''%2E%2E%2Fsecret.pdf", "secret.pdf"),
        ('attachment; filename="dir/"', None),
        ('attachment; filename=""', None),
        ('attachment; xfilename="x.pdf"', None),
        ("attachment", None),
        ("", None),
        (None, None),
    ],
    ids=[
        "quoted",
        "token",
        "escaped quotes",
        "case-insensitive",
        "utf-8 extended",
        "latin-1 extended",
        "undecodable extended",
        "undecodable extended with fallback",
        "unknown charset falls back",
        "malformed extended falls back",
        "extended wins (first)",
        "extended wins (last)",
        "slashes dropped",
        "backslashes dropped",
        "encoded slash dropped",
        "trailing slash",
        "empty",
        "other parameter",
        "no parameter",
        "empty header",
        "no header",
    ],
)
def test_the_filename_of_a_content_disposition(
    disposition: str | None, expected: str | None
) -> None:
    assert filename(disposition) == expected
