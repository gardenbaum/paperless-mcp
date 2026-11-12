"""Helpers behind the scheduled quality jobs (design 9.2, 9.5): here, not in shell, to be tested.

Subcommands (see the justfile recipes `e2e-latest`, `drift-check` and `mutation`):

* ``latest``: the highest stable release of a major version, from ``git ls-remote --tags`` output.
* ``drift``: compare the vendored Paperless reference with a freshly generated one.
* ``open-issue``: open (or refresh) the one GitHub issue that reports drift.
* ``mutation-score``: render mutmut's CI/CD statistics as a Markdown job summary and fail below
  the mutation-score gate.
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, TypeAdapter

# The justfile recipe `drift-check` tests for this exit code; keep the two in step.
DRIFT_EXIT_CODE = 10
# The justfile recipe `mutation` and the workflow rely on any non-zero code; this one tells a score
# below the gate (3) and stale equivalents (4) from a job that could not run (1).
GATE_FAILED_EXIT_CODE = 3
# Entries of mutation-equivalents.toml that no longer match a surviving mutant of the same function
# source: the weekly run stays red until the file is reviewed again.
STALE_EXIT_CODE = 4
# The mutation-score gate in percent (design 9.2): services/ and paperless/ must reach it. The one
# place the threshold lives; changing it needs a decision (doc-2).
MUTATION_GATE = 80.0
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


def format_version(version: Version) -> str:
    """``(3, 4, 2)`` as ``3.4.2``."""
    return ".".join(str(part) for part in version)


def releases(ls_remote: str, *, major: int) -> list[str]:
    """The stable ``vX.Y.Z`` tags of ``major`` in ``git ls-remote --tags`` output, highest first."""
    versions = [
        parse_version(match[1])
        for line in ls_remote.splitlines()
        if (match := _RELEASE_TAG.search(line.strip()))
    ]
    candidates = sorted((version for version in versions if version[0] == major), reverse=True)
    return [format_version(version) for version in candidates]


def latest_release(ls_remote: str, *, major: int) -> str:
    """The highest stable ``vX.Y.Z`` tag of ``major`` in ``git ls-remote --tags`` output."""
    found = releases(ls_remote, major=major)
    if not found:
        message = f"no stable {major}.x release found in the tag list"
        raise JobError(message)
    return found[0]


def release_of_major(text: str, *, major: int) -> str:
    """``text`` if it is a plain ``MAJOR.MINOR.PATCH`` release of ``major``."""
    if parse_version(text)[0] != major:
        message = f"{text} is not a {major}.x release"
        raise JobError(message)
    return text


def latest_published_release(
    ls_remote: str, major: int, image: str, runner: Runner, err: TextIO | None = None
) -> str:
    """The highest ``major`` release whose ``image:<version>`` exists (a tag may precede it)."""
    for version in releases(ls_remote, major=major):
        try:
            _ = runner(["docker", "manifest", "inspect", f"{image}:{version}"])
        except JobError:
            if err is not None:
                _ = err.write(f"scheduled_jobs: {version} has no published image, trying next\n")
            continue
        return version
    message = f"no published image for any {major}.x release of {image}"
    raise JobError(message)


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


_ISSUES = TypeAdapter(list[dict[str, int]])


def _open_numbers(runner: Runner, label: str) -> list[str]:
    listing = runner(
        ["gh", "issue", "list", "--label", label, "--state", "open", "--json", "number"]
    )
    return [str(issue["number"]) for issue in _ISSUES.validate_json(listing)]


def open_issue(runner: Runner, *, title: str, label: str, body: str) -> str:
    """Open the one issue labelled ``label``, or refresh the already open one (no duplicates)."""
    open_ones = _open_numbers(runner, label)
    if open_ones:
        number = open_ones[0]
        _ = runner(["gh", "issue", "edit", number, "--title", title, "--body", body])
        # An edit notifies nobody; a comment does.
        headline = body.splitlines()[0] if body else ""
        _ = runner(["gh", "issue", "comment", number, "--body", f"Drift still present. {headline}"])
        return f"updated issue #{number}"
    _ = runner(
        ["gh", "label", "create", label, "--force", "--description", "Upstream Paperless drift"]
    )
    _ = runner(["gh", "issue", "create", "--title", title, "--label", label, "--body", body])
    return "opened a new issue"


def close_issue(runner: Runner, *, label: str, comment: str) -> str:
    """Close the open issues labelled ``label`` (the drift is gone), each with ``comment``."""
    numbers = _open_numbers(runner, label)
    for number in numbers:
        _ = runner(["gh", "issue", "close", number, "--comment", comment])
    if not numbers:
        return "no open issue"
    return "closed issue " + ", ".join(f"#{number}" for number in numbers)


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


_FORMULA = (
    "Score = (killed + timeout) / (total - skipped - equivalent), as mutmut's badge, with the "
    "survivors reviewed as equivalent left out. Suspicious, no-tests, segfault, interrupted and "
    "not-checked (type check) mutants stay in the denominator, so they count against the score."
)
_RESULT = re.compile(r"^\s*(\S+): (\w[\w ]*?)\s*$")


@dataclass(frozen=True)
class Verdict:
    """The mutation score against the gate."""

    stats: Mapping[str, int]
    score: float | None
    gate: float
    equivalent: int
    stale: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """At or above the gate; no score (nothing was tested) cannot pass."""
        return self.score is not None and self.score >= self.gate


def mutation_score(stats: Mapping[str, int], *, equivalent: int = 0) -> float | None:
    """Percentage of killed and timed-out mutants among all but skipped and equivalent ones."""
    tested = stats.get("total", 0) - stats.get("skipped", 0) - equivalent
    if tested <= 0:
        return None
    return (stats.get("killed", 0) + stats.get("timeout", 0)) / tested * 100


def parse_results(text: str) -> dict[str, str]:
    """The ``mutant: status`` lines of ``mutmut results``."""
    return {m[1]: m[2] for line in text.splitlines() if (m := _RESULT.match(line))}


@dataclass(frozen=True)
class Equivalent:
    """A reviewed equivalent mutant: why no test can kill it, and the function it is in."""

    reason: str
    function_hash: str


class _Entry(BaseModel):
    mutant: str
    function_hash: str
    reason: str


class _Equivalents(BaseModel):
    equivalent: list[_Entry] = []


def function_of(mutant: str) -> str:
    """The mangled function a mutant belongs to (its key in mutmut's ``function_hashes``)."""
    return mutant.rsplit("__mutmut_", 1)[0]


def parse_hashes(text: str) -> dict[str, str]:
    """The source hash of every function mutmut mutated (``function_hashes`` in its stats file).

    Raises:
        JobError: Not mutmut's stats file.
    """
    try:
        return TypeAdapter(dict[str, str]).validate_python(json.loads(text)["function_hashes"])
    except (ValueError, KeyError, TypeError) as error:
        message = "mutmut's stats file has no valid function_hashes"
        raise JobError(message) from error


def parse_equivalents(text: str) -> dict[str, Equivalent]:
    """The reviewed equivalent mutants (``[[equivalent]]`` tables) with reason and function hash.

    Raises:
        JobError: Not valid TOML, an entry without a mutant, a function hash or a reason, or a
            mutant listed twice.
    """
    try:
        entries = _Equivalents.model_validate(tomllib.loads(text)).equivalent
    except (tomllib.TOMLDecodeError, ValueError) as error:
        message = f"mutation equivalents file is invalid: {error}"
        raise JobError(message) from error
    found: dict[str, Equivalent] = {}
    for entry in entries:
        if not entry.reason.strip() or not entry.function_hash.strip():
            message = f"equivalent mutant {entry.mutant} needs a reason and a function_hash"
            raise JobError(message)
        if entry.mutant in found:
            message = f"equivalent mutant {entry.mutant} is listed twice"
            raise JobError(message)
        found[entry.mutant] = Equivalent(entry.reason, entry.function_hash)
    return found


def judge(
    stats: Mapping[str, int],
    *,
    results: Mapping[str, str] | None = None,
    equivalents: Mapping[str, Equivalent] | None = None,
    hashes: Mapping[str, str] | None = None,
    gate: float = MUTATION_GATE,
) -> Verdict:
    """The score of ``stats`` against ``gate``.

    A listed equivalent counts only while its mutant survives in ``results`` and its function
    still has the source hash it was reviewed with (ids are positional: after an edit the same
    id can name a different mutant). Any other entry is stale and not counted.
    """
    survivors = {name for name, status in (results or {}).items() if status == "survived"}
    known = hashes or {}
    confirmed = {
        name
        for name, entry in (equivalents or {}).items()
        if name in survivors and known.get(function_of(name)) == entry.function_hash
    }
    stale = tuple(sorted(set(equivalents or {}) - confirmed))
    score = mutation_score(stats, equivalent=len(confirmed))
    return Verdict(stats=stats, score=score, gate=gate, equivalent=len(confirmed), stale=stale)


def render_mutation_summary(verdict: Verdict) -> str:
    """Markdown job summary of a ``Verdict`` (from mutmut's ``mutmut-cicd-stats.json``)."""
    stats = verdict.stats
    shown = "n/a" if verdict.score is None else f"{verdict.score:.1f} %"
    outcome = "passed" if verdict.passed else "FAILED"
    counts = {key: stats.get(key, 0) for _, key in _COUNTS}
    counts["survived"] -= verdict.equivalent
    rows = [f"| {label} | {counts[key]} |" for label, key in _COUNTS]
    other = stats.get("total", 0) - sum(counts.values()) - verdict.equivalent
    lines = [
        f"## Mutation score: {shown} (gate {verdict.gate:g} %: {outcome})",
        "",
        "Scope: `services/` and `paperless/`, non-E2E tests. " + _FORMULA,
        "",
        "| Result | Mutants |",
        "| --- | ---: |",
        *rows,
        f"| Survived, reviewed as equivalent (not scored) | {verdict.equivalent} |",
        f"| Other (not checked, type check) | {other} |",
        f"| **Total** | **{stats.get('total', 0)}** |",
        "",
    ]
    if verdict.stale:
        lines += [
            "### Stale entries in mutation-equivalents.toml",
            "",
            "Not a surviving mutant of the function as it was reviewed (killed, renumbered or",
            "the function changed). Review them again, then delete or update the entries.",
            "",
            *(f"- `{name}`" for name in verdict.stale),
            "",
        ]
    return "\n".join(lines)


class _Args(argparse.Namespace):
    """The parsed command line, typed."""

    command: str = ""
    major: int = 0
    image: str = ""
    version: str = ""
    comment: str = ""
    vendored: Path = Path()
    fresh: Path = Path()
    report: Path = Path()
    title: str = ""
    label: str = ""
    body_file: Path = Path()
    stats: Path = Path()
    results: Path | None = None
    equivalents: Path | None = None
    hashes: Path | None = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    latest = commands.add_parser("latest", help="highest stable release of a major version")
    _ = latest.add_argument("--major", type=int, required=True)
    _ = latest.add_argument("--image", default="", help="skip releases without this image")
    release = commands.add_parser("release", help="validate a release version")
    _ = release.add_argument("version")
    _ = release.add_argument("--major", type=int, required=True)
    drift = commands.add_parser("drift", help="compare vendored and freshly generated references")
    _ = drift.add_argument("vendored", type=Path)
    _ = drift.add_argument("fresh", type=Path)
    _ = drift.add_argument("--report", type=Path, required=True, help="written only on drift")
    issue = commands.add_parser("open-issue", help="open or refresh the drift issue")
    _ = issue.add_argument("--title", required=True)
    _ = issue.add_argument("--label", required=True)
    _ = issue.add_argument("--body-file", type=Path, required=True)
    close = commands.add_parser("close-issue", help="close the drift issue: drift is gone")
    _ = close.add_argument("--label", required=True)
    _ = close.add_argument("--comment", required=True)
    score = commands.add_parser("mutation-score", help="job summary from mutmut's CI/CD stats")
    _ = score.add_argument("stats", type=Path)
    _ = score.add_argument("--results", type=Path, help="`mutmut results` output")
    _ = score.add_argument("--equivalents", type=Path, help="reviewed equivalents (TOML)")
    _ = score.add_argument("--hashes", type=Path, help="mutmut-stats.json (function hashes)")
    return parser


def _say(out: TextIO, text: str) -> None:
    _ = out.write(text if text.endswith("\n") else text + "\n")


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8")
    except OSError as error:
        message = f"cannot read {path}: run mutmut first (just mutation)"
        raise JobError(message) from error


def _mutation_score(args: _Args, out: TextIO, err: TextIO) -> int:
    """Print the summary; the exit code is the gate's (3), then the stale entries' (4)."""
    try:
        stats = TypeAdapter(dict[str, int]).validate_json(_read(args.stats))
    except ValueError as error:
        message = f"{args.stats} is not mutmut's CI/CD stats: run mutmut first (just mutation)"
        raise JobError(message) from error
    results = parse_results(_read(args.results)) if args.results else {}
    equivalents: dict[str, Equivalent] = {}
    if args.equivalents:
        if not args.hashes:
            message = "--equivalents needs --hashes (mutmut-stats.json) to tell edited functions"
            raise JobError(message)
        equivalents = parse_equivalents(_read(args.equivalents))
    hashes = parse_hashes(_read(args.hashes)) if args.hashes else {}
    verdict = judge(stats, results=results, equivalents=equivalents, hashes=hashes)
    _say(out, render_mutation_summary(verdict))
    for name in verdict.stale:
        _ = err.write(f"scheduled_jobs: stale equivalent {name}\n")
    if not verdict.passed:
        shown = "n/a" if verdict.score is None else f"{verdict.score:.1f} %"
        reason = f"mutation score {shown} is below the gate of {verdict.gate:g} %"
        _ = err.write(f"scheduled_jobs: {reason}\n")
        return GATE_FAILED_EXIT_CODE
    return STALE_EXIT_CODE if verdict.stale else 0


def main(
    argv: Sequence[str], *, stdin: TextIO, out: TextIO, err: TextIO, runner: Runner = run
) -> int:
    """Run one subcommand; returns the process exit code (``DRIFT_EXIT_CODE`` on drift)."""
    args = _parser().parse_args(argv, namespace=_Args())
    try:
        match args.command:
            case "latest":
                tags = stdin.read()
                if args.image:
                    _say(out, latest_published_release(tags, args.major, args.image, runner, err))
                else:
                    _say(out, latest_release(tags, major=args.major))
            case "release":
                _say(out, release_of_major(args.version, major=args.major))
            case "close-issue":
                _say(out, close_issue(runner, label=args.label, comment=args.comment))
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
            case _:  # mutation-score
                return _mutation_score(args, out, err)
    except JobError as error:
        _ = err.write(f"scheduled_jobs: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], stdin=sys.stdin, out=sys.stdout, err=sys.stderr))
