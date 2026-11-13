"""Where an upload's bytes and name come from, checked before anything reaches Paperless.

Two sources: base64 content (any caller) and a file inside an allowlist of directories (stdio
only; decision-7). A path is the dangerous one: the server reads it on the caller's behalf, so
the rules below make it read nothing but a regular file inside a directory the operator named.

- The path is resolved with ``realpath`` (symlinks, ``..``, loops) and must then lie inside one
  of the allowlisted directories, which are resolved the same way.
- The file is then opened *relative to the directory*, one component at a time, each with
  ``O_NOFOLLOW`` (and directories with ``O_DIRECTORY``): if anything is swapped for a symlink
  between the check and the open, the open fails instead of following it. Opening by the resolved
  string alone would leave that gap.
- Only a regular file is read, decided on the descriptor that was opened (``fstat``), after a
  ``stat`` without opening so a pipe cannot block the open. A device, pipe, socket, directory or
  symlink is refused. At most ``max_bytes`` + 1 are read, so a file that grows is still bounded.
- A file with more than one hard link is refused: a link can name a file outside the allowlist,
  and there is no telling where it came from. Names below the allowlist that start with a dot
  (``.ssh``, ``.env``, ``.git``) are refused: credentials live there.
- A path with a ``..`` component is refused before anything is resolved. Resolving it first would
  answer differently depending on what exists outside the allowlist.
- A path outside the allowlist, a missing one, an unreadable one and all of the above get the
  same answer, so the tool is no oracle for what exists on the host.

Known limit: the allowlisted directory itself is opened by its resolved name, so someone who may
rewrite a parent of that directory can still swap it between the check and the open. The
directory belongs to the operator who allows it; everything below it is walked without
following links. A file whose extension does not match its content keeps its name: Paperless
decides the type from the content.
"""

import base64
import contextlib
import os
import stat
import unicodedata
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Final

from paperless_mcp.domain.errors import NotFound, Validation

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# Longest file name Paperless and common file systems take, in UTF-8 bytes.
MAX_NAME_BYTES: Final = 255
# Unicode categories no file name may hold: controls, format characters (bidirectional
# overrides, zero-width and byte order marks) and line and paragraph separators.
FORBIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})
KIB: Final = 1024
MIB: Final = KIB * KIB
# `dir_fd` and `O_NOFOLLOW` are what make the walk safe; Windows has neither.
SECURE: Final = os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW")
READ_CHUNK: Final = 64 * KIB


class _Refused(Exception):  # noqa: N818 -- internal signal, turned into one client answer
    """The path leads to something that is not a plain file to read."""


def size_text(count: int) -> str:
    """A byte count for messages: 10 MiB, 512 KiB or 100 bytes."""
    if count % MIB == 0:
        return f"{count // MIB} MiB"
    if count % KIB == 0:
        return f"{count // KIB} KiB"
    return f"{count} bytes"


def _too_large(max_bytes: int) -> Validation:
    message = f"The file is larger than the limit of {size_text(max_bytes)}."
    hint = "Upload a smaller file; the operator can raise PAPERLESS_MCP_MAX_UPLOAD_BYTES."
    return Validation(message, hint=hint)


def clean_filename(name: str) -> str:
    """The name to upload under: directories gone, everything else (non-ASCII too) kept.

    Raises:
        Validation: Nothing usable is left, or the name has control characters or is too long.
    """
    last = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    problem = None
    if last in {"", ".", ".."}:
        problem = "is empty or only dots"
    elif any(unicodedata.category(c) in FORBIDDEN_CATEGORIES for c in last):
        problem = "has control characters"
    elif len(last.encode()) > MAX_NAME_BYTES:
        problem = f"is longer than {MAX_NAME_BYTES} bytes"
    if problem:
        message = f"The file name {problem}."
        raise Validation(message, hint="Give the file a plain name such as invoice.pdf.")
    return last


def decode_base64(content: str, *, max_bytes: int) -> bytes:
    """The bytes of standard base64 ``content``, at most ``max_bytes`` of them.

    The length is checked before decoding, so an oversized payload costs a comparison, not a
    decode (decoding is linear, about a gigabyte per second, but allocates the whole result).

    Raises:
        Validation: Too large, empty, or not standard base64 (no whitespace, prefix or padding
            problems).
    """
    if len(content) > 4 * -(-max_bytes // 3):
        raise _too_large(max_bytes)
    try:
        data = base64.b64decode(content, validate=True)
    except ValueError:  # binascii.Error, or characters outside ASCII
        message = "The content is not valid base64."
        hint = "Send standard base64 (RFC 4648) on one line: no line breaks, no data: prefix."
        raise Validation(message, hint=hint) from None
    if not data:
        message = "The file is empty."
        raise Validation(message, hint="Send the file's bytes, base64-encoded.")
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    return data


def _outside(roots: Sequence[str]) -> NotFound:
    listing = ", ".join(roots)
    message = "No file was found at that path inside the allowed directories."
    return NotFound(
        message, hint=f"Give the absolute path of a regular file inside one of: {listing}."
    )


def _inside(resolved: str, roots: Sequence[str]) -> tuple[str, tuple[str, ...]] | None:
    """The allowlisted directory holding ``resolved`` and the path components below it."""
    for root in roots:
        real = os.path.realpath(root)
        try:
            relative = PurePath(resolved).relative_to(real).parts
        except ValueError:
            continue
        return real, relative
    return None


def _read(fd: int, max_bytes: int) -> bytes:
    """At most ``max_bytes`` + 1 bytes from ``fd``."""
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        chunk = os.read(fd, min(READ_CHUNK, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _open_regular(
    root: str, parts: tuple[str, ...], max_bytes: int, at: Callable[[str], None]
) -> bytes:
    """Walk ``parts`` below ``root`` without following a symlink and read the regular file."""
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    held = os.open(root, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            deeper = os.open(part, flags | os.O_DIRECTORY, dir_fd=held)
            os.close(held)
            held = deeper
        name = parts[-1]
        # Decided without opening: opening a pipe would wait for a writer.
        if not stat.S_ISREG(os.stat(name, dir_fd=held, follow_symlinks=False).st_mode):
            raise _Refused
        at("checked")
        fd = os.open(name, flags | os.O_NONBLOCK, dir_fd=held)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise _Refused
            if info.st_size > max_bytes:
                raise _too_large(max_bytes)
            at("opened")
            return _read(fd, max_bytes)
        finally:
            os.close(fd)
    finally:
        with contextlib.suppress(OSError):
            os.close(held)


def read_allowed(
    path: str,
    roots: Sequence[str],
    *,
    max_bytes: int,
    secure: bool = SECURE,
    at: Callable[[str], None] = lambda _stage: None,
) -> tuple[str, bytes]:
    """The name and bytes of the regular file at ``path``, which must lie inside ``roots``.

    ``at`` is a test seam, called with the stage just reached: ``resolved`` (the path is approved),
    ``checked`` (the file was seen to be a regular one) and ``opened`` (it was opened and
    measured). Tests change the file system there, to prove the later steps do not trust the
    earlier ones.

    Raises:
        Validation: No allowlist, a relative path, a platform without safe opening, an empty
            file or one over ``max_bytes``.
        NotFound: The path is outside the allowlist, does not exist, or is not a regular file.
    """
    if not roots:
        message = "Uploading by path is not enabled."
        hint = "Send content_base64; the operator enables paths with PAPERLESS_MCP_UPLOAD_DIRS."
        raise Validation(message, hint=hint)
    if "\x00" in path:
        message = "The path contains a NUL byte."
        raise Validation(message, hint="Give the plain path of the file.")
    if not Path(path).is_absolute():
        message = "The path must be absolute."
        raise Validation(message, hint="Give the full path starting with / (or a drive).")
    if not secure:
        message = "Uploading by path is not supported on this platform."
        raise Validation(message, hint="Send the file as content_base64.")
    if ".." in PurePath(path).parts:
        raise _outside(roots)
    try:
        resolved = os.path.realpath(path)
        found = _inside(resolved, roots)
        if found is None or not found[1] or any(part.startswith(".") for part in found[1]):
            raise _Refused  # noqa: TRY301 -- one answer for every refusal
        at("resolved")
        data = _open_regular(found[0], found[1], max_bytes, at)
    except OSError, ValueError, _Refused:  # ValueError: a path that cannot be encoded
        raise _outside(roots) from None
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    if not data:
        message = "The file is empty."
        raise Validation(message, hint="Upload a file that has content.")
    return PurePath(path).name, data
