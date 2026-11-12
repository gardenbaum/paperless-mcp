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
        # the value is everything after the second quote, even if it holds another quote
        ("attachment; filename*=UTF-8''na'me.pdf", "na'me.pdf"),
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
        # spoofing: parameters inside a quoted value are part of that value
        ("attachment; filename=\"a filename*=utf-8''evil.exe\"", "a filename*=utf-8''evil.exe"),
        ("attachment; filename=\"a; filename*=utf-8''evil.exe\"", "a; filename*=utf-8''evil.exe"),
        ('attachment; filename="a"; filename*="utf-8\'\'x.pdf"', "a"),
        ('attachment; filename="unterminated.pdf', "unterminated.pdf"),
        ('attachment; filename="a.pdf"; filename="b.pdf"', "a.pdf"),
        # names that are not names
        ('attachment; filename="."', None),
        ('attachment; filename=".."', None),
        ("attachment; filename*=UTF-8''..", None),
        ("attachment; filename*=UTF-8''a%00b%0Ac%1Fd%7Fe%C2%85f.pdf", "abcdef.pdf"),
        ("attachment; filename*=UTF-8''gpj.%E2%80%AEfdp", "gpj.fdp"),
        ("attachment; filename*=UTF-8''a%E2%81%A6b%E2%81%A9c.pdf", "abc.pdf"),
        ("attachment; filename*=UTF-8''%E2%80%AE", None),
        # charsets: UTF-8 and ISO-8859-1 only
        ("attachment; filename*=UTF-16''a.pdf; filename=\"fallback.pdf\"", "fallback.pdf"),
        ("attachment; filename*=ISO-8859-1''caf%E9.pdf", "café.pdf"),
        ("attachment; filename*=utf8''a.pdf; filename=\"fallback.pdf\"", "fallback.pdf"),
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
        "quote inside the extended value",
        "slashes dropped",
        "backslashes dropped",
        "encoded slash dropped",
        "trailing slash",
        "empty",
        "other parameter",
        "no parameter",
        "empty header",
        "no header",
        "parameter inside quotes",
        "separator inside quotes",
        "quoted extended form is malformed",
        "unterminated quote",
        "first of a repeated parameter",
        "dot",
        "dot dot",
        "encoded dot dot",
        "control characters",
        "bidi override",
        "bidi isolates",
        "only a bidi override",
        "utf-16 refused",
        "latin-1 upper case",
        "unlisted charset alias refused",
    ],
)
def test_the_filename_of_a_content_disposition(
    disposition: str | None, expected: str | None
) -> None:
    assert filename(disposition) == expected
