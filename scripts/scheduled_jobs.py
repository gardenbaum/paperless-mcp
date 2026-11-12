"""Helpers behind the scheduled quality jobs (design 9.2, 9.5): here, not in shell, to be tested.

Subcommands (see the justfile recipes `e2e-latest`, `drift-check` and `mutation`):

* ``latest``: the highest stable release of a major version, from ``git ls-remote --tags`` output.
* ``drift``: compare the vendored Paperless reference with a freshly generated one.
* ``open-issue``: open (or refresh) the one GitHub issue that reports drift.
* ``mutation-score``: render mutmut's CI/CD statistics as a Markdown job summary.
"""

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, TypeAdapter

DRIFT_EXIT_CODE = 10
MAX_LISTED = 50

# Stable releases only: pre-releases (v3.5.0-beta.rc1) do not end after the patch number.
_RELEASE_TAG = re.compile(r"refs/tags/v(\d+\.\d+\.\d+)$")
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
_UPSTREAM_VERSION = re.compile(r"\bv(\d+\.\d+\.\d+)\b")

Version = tuple[int, int, int]
Runner = Callable[[Sequence[str]], str]


class JobError(Exception):
    """A scheduled job cannot continue; the message is for the person reading the job log."""


def parse_version(text: str) -> Version:
    """Parse ``MAJOR.MINOR.PATCH``."""
    match = _VERSION.fullmatch(text)
    if not match:
        message = f"not a release version: {text}"
        raise JobError(message)
    return int(match[1]), int(match[2]), int(match[3])


def latest_release(ls_remote: str, *, major: int) -> str:
    """The highest stable ``vX.Y.Z`` tag of ``major`` in ``git ls-remote --tags`` output."""
    versions = [
        parse_version(match[1])
        for line in ls_remote.splitlines()
        if (match := _RELEASE_TAG.search(line.strip()))
    ]
    candidates = [version for version in versions if version[0] == major]
    if not candidates:
        message = f"no stable {major}.x release found in the tag list"
        raise JobError(message)
    return ".".join(str(part) for part in max(candidates))


@dataclass(frozen=True)
class Drift:
    """How a freshly generated Paperless reference differs from the vendored one."""

    vendored_version: str
    latest_version: str
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]
    operations_added: tuple[str, ...]
    operations_removed: tuple[str, ...]

    @property
    def found(self) -> bool:
        """Drift is a newer release, or other content of the same release; never an older one."""
        vendored, latest = parse_version(self.vendored_version), parse_version(self.latest_version)
        differs = bool(self.added or self.removed or self.changed)
        return latest > vendored or (latest == vendored and differs)


def _version_of(directory: Path) -> str:
    match = _UPSTREAM_VERSION.search((directory / "UPSTREAM").read_text(encoding="utf-8"))
    if not match:
        message = f"{directory / 'UPSTREAM'} names no release version"
        raise JobError(message)
    return match[1]


def _files(directory: Path) -> set[str]:
    # UPSTREAM holds the commit and image digest, which change without the content changing.
    return {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.name != "UPSTREAM"
    }


class _Schema(BaseModel):
    paths: dict[str, dict[str, object]]


def _operations(path: Path) -> set[str]:
    schema = _Schema.model_validate_json(path.read_text(encoding="utf-8"))
    return {
        f"{key.upper()} {route}"
        for route, item in schema.paths.items()
        for key in item
        if key in _HTTP_METHODS  # path items also carry `parameters`, `summary`, ...
    }


def compare_vendored(vendored: Path, fresh: Path) -> Drift:
    """Compare two ``docs/vendor/paperless`` trees, ignoring the commit and digest in UPSTREAM."""
    old, new = _files(vendored), _files(fresh)
    changed = tuple(
        sorted(
            name
            for name in old & new
            if (vendored / name).read_bytes() != (fresh / name).read_bytes()
        )
    )
    operations_added: tuple[str, ...] = ()
    operations_removed: tuple[str, ...] = ()
    if "openapi.json" in changed:
        before, after = _operations(vendored / "openapi.json"), _operations(fresh / "openapi.json")
        operations_added, operations_removed = (
            tuple(sorted(after - before)),
            tuple(sorted(before - after)),
        )
    return Drift(
        vendored_version=_version_of(vendored),
        latest_version=_version_of(fresh),
        added=tuple(sorted(new - old)),
        removed=tuple(sorted(old - new)),
        changed=changed,
        operations_added=operations_added,
        operations_removed=operations_removed,
    )


def _section(heading: str, items: Sequence[str]) -> list[str]:
    if not items:
        return []
    lines = [f"### {heading}", "", *(f"- `{item}`" for item in items[:MAX_LISTED])]
    if len(items) > MAX_LISTED:
        lines.append(f"- ... and {len(items) - MAX_LISTED} more")
    return [*lines, ""]


_INTRO = "The vendored Paperless reference (`docs/vendor/paperless`) differs from the latest 3.x:"
_ADOPT_PARTS = (
    "To adopt the release: bump `PAPERLESS_VERSION` in the justfile and the image in",
    "`tests/e2e/compose.yaml`, run `just docs-sync-paperless`, review the diff, and fix",
    "what the schema-guarded adapter tests and `just e2e` report.",
)
_ADOPT = " ".join(_ADOPT_PARTS)


def render_report(drift: Drift) -> str:
    """The issue body for ``drift``; empty when there is none."""
    if not drift.found:
        return ""
    versions = f"vendored `{drift.vendored_version}`, latest `{drift.latest_version}`"
    lines = [
        f"{_INTRO} {versions}.",
        "",
        *_section("API operations added", drift.operations_added),
        *_section("API operations removed", drift.operations_removed),
        *_section("Files added", drift.added),
        *_section("Files removed", drift.removed),
        *_section("Files changed", drift.changed),
        _ADOPT,
        "",
        "Opened by the weekly `drift` workflow (`just drift-check`).",
    ]
    return "\n".join(lines) + "\n"


def run(command: Sequence[str]) -> str:
    """Run a command and return its standard output; a failure becomes a `JobError`."""
    finished = subprocess.run(  # noqa: S603 -- fixed argument vectors built by this module
        command, capture_output=True, text=True, check=False
    )
    if finished.returncode:
        message = f"{command[0]} failed with exit code {finished.returncode}: {finished.stderr}"
        raise JobError(message)
    return finished.stdout


def open_issue(runner: Runner, *, title: str, label: str, body: str) -> str:
    """Open the one issue labelled ``label``, or refresh the already open one (no duplicates)."""
    listing = runner(
        ["gh", "issue", "list", "--label", label, "--state", "open", "--json", "number"]
    )
    open_ones = TypeAdapter(list[dict[str, int]]).validate_json(listing)
    if open_ones:
        number = str(open_ones[0]["number"])
        _ = runner(["gh", "issue", "edit", number, "--title", title, "--body", body])
        return f"updated issue #{number}"
    _ = runner(
        ["gh", "label", "create", label, "--force", "--description", "Upstream Paperless drift"]
    )
    _ = runner(["gh", "issue", "create", "--title", title, "--label", label, "--body", body])
    return "opened a new issue"


_COUNTS = (
    ("Killed", "killed"),
    ("Timeout (counted as killed)", "timeout"),
    ("Survived", "survived"),
    ("No tests", "no_tests"),
    ("Suspicious", "suspicious"),
    ("Segfault", "segfault"),
    ("Interrupted", "check_was_interrupted_by_user"),
    ("Skipped (not scored)", "skipped"),
)


_MUTATION_SCOPE_PARTS = (
    "Scope: `services/` and `paperless/`, non-E2E tests. The score is report-only for now",
    "(no failing threshold); PLMCP-21 turns it into a gate of 80 %.",
)
_MUTATION_SCOPE = " ".join(_MUTATION_SCOPE_PARTS)


def mutation_score(stats: Mapping[str, int]) -> float | None:
    """Percentage of killed and timed-out mutants among all but the skipped (as mutmut's badge)."""
    tested = stats.get("total", 0) - stats.get("skipped", 0)
    if tested <= 0:
        return None
    return (stats.get("killed", 0) + stats.get("timeout", 0)) / tested * 100


def render_mutation_summary(stats: Mapping[str, int]) -> str:
    """Markdown job summary for mutmut's ``mutmut-cicd-stats.json``."""
    score = mutation_score(stats)
    shown = "n/a" if score is None else f"{score:.1f} %"
    rows = [f"| {label} | {stats.get(key, 0)} |" for label, key in _COUNTS]
    return "\n".join(
        [
            f"## Mutation score: {shown}",
            "",
            _MUTATION_SCOPE,
            "",
            "| Result | Mutants |",
            "| --- | ---: |",
            *rows,
            f"| **Total** | **{stats.get('total', 0)}** |",
            "",
        ]
    )


class _Args(argparse.Namespace):
    """The parsed command line, typed."""

    command: str = ""
    major: int = 0
    vendored: Path = Path()
    fresh: Path = Path()
    report: Path = Path()
    title: str = ""
    label: str = ""
    body_file: Path = Path()
    stats: Path = Path()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    latest = commands.add_parser("latest", help="highest stable release of a major version")
    _ = latest.add_argument("--major", type=int, required=True)
    drift = commands.add_parser("drift", help="compare vendored and freshly generated references")
    _ = drift.add_argument("vendored", type=Path)
    _ = drift.add_argument("fresh", type=Path)
    _ = drift.add_argument("--report", type=Path, required=True, help="written only on drift")
    issue = commands.add_parser("open-issue", help="open or refresh the drift issue")
    _ = issue.add_argument("--title", required=True)
    _ = issue.add_argument("--label", required=True)
    _ = issue.add_argument("--body-file", type=Path, required=True)
    score = commands.add_parser("mutation-score", help="job summary from mutmut's CI/CD stats")
    _ = score.add_argument("stats", type=Path)
    return parser


def _say(out: TextIO, text: str) -> None:
    _ = out.write(text if text.endswith("\n") else text + "\n")


def main(argv: Sequence[str], *, stdin: TextIO, out: TextIO, runner: Runner = run) -> int:
    """Run one subcommand; returns the process exit code (``DRIFT_EXIT_CODE`` on drift)."""
    args = _parser().parse_args(argv, namespace=_Args())
    try:
        match args.command:
            case "latest":
                _say(out, latest_release(stdin.read(), major=args.major))
            case "drift":
                drift = compare_vendored(args.vendored, args.fresh)
                versions = f"vendored {drift.vendored_version}, latest {drift.latest_version}"
                if not drift.found:
                    _say(out, f"no drift: {versions}")
                    return 0
                _ = args.report.write_text(render_report(drift), encoding="utf-8")
                _say(out, f"drift: {versions}")
                return DRIFT_EXIT_CODE
            case "open-issue":
                body = args.body_file.read_text(encoding="utf-8")
                _say(out, open_issue(runner, title=args.title, label=args.label, body=body))
            case _:
                stats = TypeAdapter(dict[str, int]).validate_json(args.stats.read_text("utf-8"))
                _say(out, render_mutation_summary(stats))
    except JobError as error:
        _ = sys.stderr.write(f"scheduled_jobs: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], stdin=sys.stdin, out=sys.stdout))
