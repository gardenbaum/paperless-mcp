"""Where an upload's bytes and name come from: base64 content, or a file inside an allowlist.

The path tests are the security tests of decision-7: traversal, symlinks (also loops and ones
that point out of the allowlist), devices, pipes, and a file swapped between the check and the
read. Every refusal says the same thing for a path outside the allowlist and for one that does
not exist, so the tool cannot be used to probe the host's file system.
"""

import base64
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from paperless_mcp.domain.errors import NotFound, Validation
from paperless_mcp.services.upload_source import (
    clean_filename,
    decode_base64,
    read_allowed,
    size_text,
)

if TYPE_CHECKING:
    from collections.abc import Callable

LIMIT = 100


@pytest.mark.parametrize(
    ("count", "text"),
    [(10 * 1024 * 1024, "10 MiB"), (1024 * 1024, "1 MiB"), (512 * 1024, "512 KiB"),
     (1024, "1 KiB"), (1536, "1536 bytes"), (100, "100 bytes")],
)  # fmt: skip
def test_sizes_read_naturally_in_messages(count: int, text: str) -> None:
    assert size_text(count) == text


# --- file names ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("invoice.pdf", "invoice.pdf"),
        ("Rechnung Käse 日本語 🧾.pdf", "Rechnung Käse 日本語 🧾.pdf"),
        ("../../etc/passwd", "passwd"),
        ("/etc/shadow", "shadow"),
        ("C:\\Users\\me\\Käse.pdf", "Käse.pdf"),
        ("a/b\\c/d.pdf", "d.pdf"),
        ("  spaced name.pdf  ", "spaced name.pdf"),
        (".hidden.pdf", ".hidden.pdf"),
        ("no extension", "no extension"),
        ("ä" * 127 + "a", "ä" * 127 + "a"),  # 255 bytes
    ],
)
def test_a_file_name_loses_its_directories_and_keeps_everything_else(
    given: str, expected: str
) -> None:
    assert clean_filename(given) == expected


@pytest.mark.parametrize(
    "given",
    ["", "   ", ".", "..", "dir/", "a/..", "a\\..\\", "nul\x00.pdf", "bell\x07.pdf",
     "line\nbreak.pdf", "gpj.exe\u202e.pdf", "ä" * 128, "del\x7f.pdf", "c1\x85.pdf",
     "zero\u200bwidth.pdf", "joiner\u200d.pdf", "isolate\u2066.pdf", "bom\ufeff.pdf",
     "line\u2028sep.pdf", "para\u2029sep.pdf"],
)  # fmt: skip
def test_a_file_name_without_a_usable_name_is_refused(given: str) -> None:
    with pytest.raises(Validation, match="file name"):
        _ = clean_filename(given)


# --- base64 -------------------------------------------------------------------------------------


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_base64_content_is_decoded() -> None:
    assert decode_base64(encoded(b"%PDF-1.4 \xff\x00"), max_bytes=LIMIT) == b"%PDF-1.4 \xff\x00"


def test_content_of_exactly_the_limit_is_accepted_and_one_byte_more_is_not() -> None:
    assert len(decode_base64(encoded(bytes(LIMIT)), max_bytes=LIMIT)) == LIMIT

    with pytest.raises(Validation, match="larger than the limit of 100 bytes"):
        _ = decode_base64(encoded(bytes(LIMIT + 1)), max_bytes=LIMIT)


def test_content_far_over_the_limit_is_refused_before_it_is_decoded() -> None:
    # Not even valid base64: only a check of the length first can report the size.
    with pytest.raises(Validation, match="larger than the limit"):
        _ = decode_base64("!" * 10_000, max_bytes=LIMIT)
    with pytest.raises(Validation, match="larger than the limit"):
        _ = decode_base64("!" * 140, max_bytes=LIMIT)  # just over the 136 characters of 100 bytes


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 5, 100])
def test_the_length_bound_is_the_encoded_size_of_the_limit(limit: int) -> None:
    longest = 4 * -(-limit // 3)  # characters of the largest content that fits

    assert decode_base64(encoded(bytes(limit)), max_bytes=limit) == bytes(limit)
    with pytest.raises(Validation, match="larger than the limit"):
        _ = decode_base64("A" * (longest + 4), max_bytes=limit)


@pytest.mark.parametrize(
    "text",
    ["!!!!", "AAA", "AA=A", "QUJD\nREVG", " QUJD", "QUJD ", "data:application/pdf;base64,QUJD",
     "QUJD-_8=", "ÄÖÜ"],
)  # fmt: skip
def test_text_that_is_not_standard_base64_is_refused(text: str) -> None:
    with pytest.raises(Validation, match="base64"):
        _ = decode_base64(text, max_bytes=LIMIT)


def test_empty_content_is_refused() -> None:
    with pytest.raises(Validation, match="empty"):
        _ = decode_base64("", max_bytes=LIMIT)


# --- paths --------------------------------------------------------------------------------------


@pytest.fixture
def allowed(tmp_path: Path) -> Path:
    root = tmp_path / "allowed"
    (root / "sub").mkdir(parents=True)
    _ = (root / "invoice.pdf").write_bytes(b"%PDF-1.4 inside")
    _ = (root / "sub" / "Käse ä.pdf").write_bytes(b"%PDF-1.4 nested")
    return root


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    folder = tmp_path / "outside"
    folder.mkdir()
    _ = (folder / "secret.pdf").write_bytes(b"%PDF-1.4 secret")
    return folder


def read(path: Path | str, root: Path, **extra: object) -> tuple[str, bytes]:
    return read_allowed(str(path), [str(root)], max_bytes=LIMIT, **extra)  # pyright: ignore[reportArgumentType] -- the hooks


def refused(path: Path | str, root: Path, **extra: object) -> NotFound:
    with pytest.raises(NotFound) as raised:
        _ = read(path, root, **extra)
    return raised.value


def test_a_file_inside_the_allowlist_is_read_with_its_name(allowed: Path) -> None:
    assert read(allowed / "invoice.pdf", allowed) == ("invoice.pdf", b"%PDF-1.4 inside")
    assert read(allowed / "sub" / "Käse ä.pdf", allowed) == ("Käse ä.pdf", b"%PDF-1.4 nested")


def test_the_allowlist_may_name_several_directories_and_the_first_that_holds_the_file_wins(
    allowed: Path, outside: Path
) -> None:
    roots = [str(outside), str(allowed)]

    assert read_allowed(str(allowed / "invoice.pdf"), roots, max_bytes=LIMIT)[0] == "invoice.pdf"
    assert read_allowed(str(outside / "secret.pdf"), roots, max_bytes=LIMIT)[0] == "secret.pdf"


def test_a_symlink_to_a_file_inside_the_allowlist_is_followed_and_named_as_asked(
    allowed: Path,
) -> None:
    (allowed / "latest.pdf").symlink_to(allowed / "sub" / "Käse ä.pdf")

    assert read(allowed / "latest.pdf", allowed) == ("latest.pdf", b"%PDF-1.4 nested")


def test_an_allowlist_that_is_itself_a_symlink_is_resolved(allowed: Path, tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(allowed, target_is_directory=True)

    assert read(allowed / "invoice.pdf", link)[1] == b"%PDF-1.4 inside"
    assert read(link / "invoice.pdf", allowed)[1] == b"%PDF-1.4 inside"


def test_without_an_allowlist_no_path_is_read(allowed: Path) -> None:
    with pytest.raises(Validation, match="not enabled"):
        _ = read_allowed(str(allowed / "invoice.pdf"), [], max_bytes=LIMIT)


@pytest.mark.parametrize(
    "relative",
    [
        "../outside/secret.pdf",
        "sub/../../outside/secret.pdf",
        "./../outside/secret.pdf",
        "sub/../../allowed2/x",
    ],
)
def test_traversal_out_of_the_allowlist_is_refused(allowed: Path, relative: str) -> None:
    _ = refused(str(allowed) + "/" + relative, allowed)


@pytest.mark.parametrize("tail", ["sub/../invoice.pdf", "../allowed/invoice.pdf", "sub/.."])
def test_a_parent_component_is_refused_even_when_it_stays_inside(allowed: Path, tail: str) -> None:
    """Resolving `..` would answer differently for paths that exist and that do not: an oracle."""
    refused_alike = refused(f"{allowed}/{tail}", allowed)

    assert refused_alike.message == refused(allowed / "nope.pdf", allowed).message


def test_a_file_with_two_names_is_refused(allowed: Path) -> None:
    """A hard link can name a file outside the allowlist; links cannot be traced to their origin."""
    os.link(allowed / "invoice.pdf", allowed / "second name.pdf")

    _ = refused(allowed / "invoice.pdf", allowed)
    _ = refused(allowed / "second name.pdf", allowed)


@pytest.mark.parametrize("name", [".ssh/id.pdf", "sub/.hidden.pdf", ".env", "sub/.git/config"])
def test_dot_files_and_dot_directories_below_the_allowlist_are_refused(
    allowed: Path, name: str
) -> None:
    target = allowed / name
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_bytes(b"%PDF-1.4 secret")

    refused_alike = refused(target, allowed)

    assert refused_alike.message == refused(allowed / "nope.pdf", allowed).message


def test_a_dot_directory_above_the_allowlist_is_no_problem(tmp_path: Path) -> None:
    root = tmp_path / ".config" / "scans"
    root.mkdir(parents=True)
    _ = (root / "a.pdf").write_bytes(b"%PDF-1.4 ok")

    assert read(root / "a.pdf", root)[1] == b"%PDF-1.4 ok"


@pytest.mark.parametrize("name", ["lone-low\udcff.pdf", "lone-high\ud800.pdf"])
def test_a_path_that_cannot_be_encoded_is_just_not_found(allowed: Path, name: str) -> None:
    _ = refused(f"{allowed}/{name}", allowed)


def test_an_absolute_path_outside_the_allowlist_is_refused(allowed: Path, outside: Path) -> None:
    _ = refused(outside / "secret.pdf", allowed)
    _ = refused("/etc/passwd", allowed)


def test_a_sibling_whose_name_starts_like_the_allowlist_is_outside(
    allowed: Path, tmp_path: Path
) -> None:
    sibling = tmp_path / "allowed-and-more"
    sibling.mkdir()
    _ = (sibling / "x.pdf").write_bytes(b"%PDF-1.4")

    _ = refused(sibling / "x.pdf", allowed)


def test_a_symlink_that_points_out_of_the_allowlist_is_refused(
    allowed: Path, outside: Path
) -> None:
    (allowed / "escape.pdf").symlink_to(outside / "secret.pdf")
    (allowed / "escape-dir").symlink_to(outside, target_is_directory=True)

    _ = refused(allowed / "escape.pdf", allowed)
    _ = refused(allowed / "escape-dir" / "secret.pdf", allowed)


def test_a_chain_of_symlinks_is_judged_by_where_it_ends(allowed: Path, outside: Path) -> None:
    (allowed / "one").symlink_to(allowed / "two")
    (allowed / "two").symlink_to(outside / "secret.pdf")

    _ = refused(allowed / "one", allowed)


def test_symlink_loops_are_refused_and_do_not_hang(allowed: Path) -> None:
    (allowed / "a").symlink_to(allowed / "b")
    (allowed / "b").symlink_to(allowed / "a")
    (allowed / "self").symlink_to(allowed / "self")

    for name in ("a", "b", "self", "self/more"):
        _ = refused(allowed / name, allowed)


def test_a_missing_file_looks_like_a_path_outside_the_allowlist(
    allowed: Path, outside: Path
) -> None:
    missing = refused(allowed / "nope.pdf", allowed)
    elsewhere = refused(outside / "nope.pdf", allowed)
    real_elsewhere = refused(outside / "secret.pdf", allowed)

    assert missing.message == elsewhere.message == real_elsewhere.message
    assert str(outside) not in missing.describe()  # nothing about the host leaks


def test_the_error_never_echoes_what_the_resolution_found(allowed: Path, outside: Path) -> None:
    (allowed / "escape.pdf").symlink_to(outside / "secret.pdf")

    assert "secret" not in refused(allowed / "escape.pdf", allowed).describe()


def test_a_device_a_pipe_a_directory_and_the_allowlist_itself_are_not_files(
    allowed: Path,
) -> None:
    os.mkfifo(allowed / "pipe")

    _ = refused(allowed / "pipe", allowed)  # must not block on open
    _ = refused(allowed / "sub", allowed)
    _ = refused(allowed, allowed)
    with pytest.raises(NotFound):
        _ = read_allowed("/dev/null", ["/dev"], max_bytes=LIMIT)
    with pytest.raises(NotFound):
        _ = read_allowed("/dev/zero", ["/dev"], max_bytes=LIMIT)  # endless: never read


def test_a_socket_is_not_a_file(allowed: Path) -> None:
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(allowed / "sock"))
        _ = refused(allowed / "sock", allowed)


@pytest.mark.parametrize("path", ["invoice.pdf", "./invoice.pdf", "~/invoice.pdf", "sub/x.pdf"])
def test_a_relative_path_is_refused_because_it_would_depend_on_the_working_directory(
    allowed: Path, path: str
) -> None:
    with pytest.raises(Validation, match="absolute"):
        _ = read(path, allowed)


def test_a_path_with_a_nul_byte_is_refused(allowed: Path) -> None:
    with pytest.raises(Validation, match="NUL"):
        _ = read(f"{allowed}/invoice.pdf\x00.txt", allowed)


def test_a_file_over_the_limit_is_refused_before_it_is_read(allowed: Path) -> None:
    _ = (allowed / "big.bin").write_bytes(bytes(LIMIT + 1))
    _ = (allowed / "exact.bin").write_bytes(bytes(LIMIT))

    with pytest.raises(Validation, match="larger than the limit of 100 bytes"):
        _ = read(allowed / "big.bin", allowed)
    assert len(read(allowed / "exact.bin", allowed)[1]) == LIMIT


def test_an_empty_file_is_refused(allowed: Path) -> None:
    _ = (allowed / "empty.pdf").write_bytes(b"")

    with pytest.raises(Validation, match="empty"):
        _ = read(allowed / "empty.pdf", allowed)


def at_stage(stage: str, action: Callable[[], None]) -> Callable[[str], None]:
    """A test hook that runs `action` when the read reaches `stage`."""

    def hook(reached: str) -> None:
        if reached == stage:
            action()

    return hook


def test_a_read_in_chunks_returns_every_byte_of_a_file_over_one_chunk(tmp_path: Path) -> None:
    big = bytes(range(256)) * 600  # 153 600 bytes: more than two 64 KiB chunks
    root = tmp_path / "big"
    root.mkdir()
    _ = (root / "big.bin").write_bytes(big)

    assert read_allowed(str(root / "big.bin"), [str(root)], max_bytes=len(big))[1] == big
    with pytest.raises(Validation, match="larger than the limit"):
        _ = read_allowed(str(root / "big.bin"), [str(root)], max_bytes=len(big) - 1)


def test_the_read_passes_the_three_stages_the_hooks_can_use(allowed: Path) -> None:
    stages: list[str] = []

    _ = read(allowed / "invoice.pdf", allowed, at=stages.append)

    assert stages == ["resolved", "checked", "opened"]


@pytest.mark.parametrize("stage", ["resolved", "opened"])
def test_a_file_that_grows_after_it_was_measured_is_still_bounded(
    allowed: Path, stage: str
) -> None:
    target = allowed / "growing.bin"
    _ = target.write_bytes(bytes(10))

    def grow() -> None:
        _ = target.write_bytes(bytes(LIMIT * 5))

    # It was 10 bytes when approved; reading must not take the 500 that are there by now.
    with pytest.raises(Validation, match="larger than the limit"):
        _ = read(target, allowed, at=at_stage(stage, grow))


# --- a file swapped between the check and the read (time of check to time of use) -----------


def swapped(target: Path, replacement: Callable[[], None]) -> Callable[[], None]:
    def swap() -> None:
        target.unlink()
        replacement()

    return swap


@pytest.mark.parametrize("stage", ["resolved", "checked"])
def test_a_file_replaced_by_a_symlink_after_the_check_is_not_followed(
    allowed: Path, outside: Path, stage: str
) -> None:
    target = allowed / "invoice.pdf"
    swap = swapped(target, lambda: target.symlink_to(outside / "secret.pdf"))

    _ = refused(target, allowed, at=at_stage(stage, swap))


@pytest.mark.parametrize("stage", ["resolved", "checked"])
def test_a_directory_replaced_by_a_symlink_after_the_check_is_not_followed(
    allowed: Path, outside: Path, stage: str
) -> None:
    target = allowed / "sub" / "Käse ä.pdf"
    folder = allowed / "sub"
    _ = (outside / "Käse ä.pdf").write_bytes(b"%PDF-1.4 stolen")

    def swap() -> None:
        target.unlink()
        folder.rmdir()
        folder.symlink_to(outside, target_is_directory=True)

    _ = refused(target, allowed, at=at_stage(stage, swap))


@pytest.mark.parametrize("stage", ["resolved", "checked"])
def test_a_file_replaced_by_a_pipe_after_the_check_does_not_block(
    allowed: Path, stage: str
) -> None:
    target = allowed / "invoice.pdf"
    swap = swapped(target, lambda: os.mkfifo(target))

    _ = refused(target, allowed, at=at_stage(stage, swap))


@pytest.mark.parametrize("stage", ["resolved", "checked"])
def test_a_file_replaced_by_another_regular_file_is_read_as_it_is_now(
    allowed: Path, stage: str
) -> None:
    target = allowed / "invoice.pdf"

    def newer() -> None:
        _ = target.write_bytes(b"%PDF-1.4 newer")

    assert read(target, allowed, at=at_stage(stage, swapped(target, newer)))[1] == b"%PDF-1.4 newer"


def test_the_platform_must_support_opening_relative_to_a_directory(allowed: Path) -> None:
    with pytest.raises(Validation, match="platform"):
        _ = read(allowed / "invoice.pdf", allowed, secure=False)


def test_the_files_opened_are_closed_again(allowed: Path) -> None:
    before = len(list(Path("/dev/fd").iterdir()))

    for _ in range(20):
        _ = read(allowed / "sub" / "Käse ä.pdf", allowed)
        _ = refused(allowed / "missing", allowed)

    assert len(list(Path("/dev/fd").iterdir())) == before


def test_a_file_that_grows_by_one_byte_past_a_whole_chunk_is_still_refused(tmp_path: Path) -> None:
    """The limit is one read chunk exactly: the guard must read on to see the extra byte."""
    chunk = 64 * 1024
    root = tmp_path / "chunk"
    root.mkdir()
    target = root / "growing.bin"
    _ = target.write_bytes(bytes(10))

    def grow() -> None:
        _ = target.write_bytes(bytes(chunk + 1))

    with pytest.raises(Validation, match="larger than the limit"):
        _ = read_allowed(str(target), [str(root)], max_bytes=chunk, at=at_stage("opened", grow))
