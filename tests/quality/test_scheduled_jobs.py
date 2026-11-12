"""Tests of the helpers behind the scheduled quality jobs (design 9.2, 9.5, PLMCP-14)."""

import io
import json
import sys
from typing import TYPE_CHECKING

import pytest
import scheduled_jobs

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

LS_REMOTE = """\
aaa\trefs/tags/v2.20.14
bbb\trefs/tags/v3.0.0
ccc\trefs/tags/v3.4.2
ddd\trefs/tags/v3.10.1
eee\trefs/tags/v3.11.0-beta.rc1
fff\trefs/tags/v4.0.0
ggg\trefs/tags/not-a-version
"""


def test_latest_release_is_the_highest_stable_3x_tag_compared_numerically() -> None:
    assert scheduled_jobs.latest_release(LS_REMOTE, major=3) == "3.10.1"


def test_latest_release_ignores_other_majors() -> None:
    assert scheduled_jobs.latest_release(LS_REMOTE, major=2) == "2.20.14"


def test_latest_release_fails_without_a_matching_tag() -> None:
    with pytest.raises(scheduled_jobs.JobError, match=r"no stable 5\.x release"):
        _ = scheduled_jobs.latest_release(LS_REMOTE, major=5)


def test_parse_version_rejects_anything_but_three_numbers() -> None:
    assert scheduled_jobs.parse_version("3.4.2") == (3, 4, 2)
    with pytest.raises(scheduled_jobs.JobError, match=r"not a release version: 3\.4"):
        _ = scheduled_jobs.parse_version("3.4")


def openapi(*operations: tuple[str, str]) -> str:
    paths: dict[str, dict[str, object]] = {}
    for method, path in operations:
        paths.setdefault(path, {"parameters": []})[method] = {}
    return json.dumps({"paths": paths})


def vendored_tree(root: Path, *, version: str, files: dict[str, str]) -> Path:
    root.mkdir(parents=True)
    _ = (root / "UPSTREAM").write_text(f"abc v{version} image-digest sha256:{version}\n")
    for name, content in files.items():
        _ = (root / name).write_text(content)
    return root


def test_identical_trees_are_no_drift_even_when_commit_and_digest_differ(tmp_path: Path) -> None:
    files = {"api.md": "same", "openapi.json": openapi(("get", "/api/tags/"))}
    vendored = vendored_tree(tmp_path / "a", version="3.4.2", files=files)
    fresh = vendored_tree(tmp_path / "b", version="3.4.2", files=files)
    _ = (fresh / "UPSTREAM").write_text("other-commit v3.4.2 image-digest sha256:rebuilt\n")

    drift = scheduled_jobs.compare_vendored(vendored, fresh)

    assert not drift.found
    assert scheduled_jobs.render_report(drift) == ""


def test_a_newer_release_is_drift_and_the_report_lists_every_difference(tmp_path: Path) -> None:
    old = {
        "api.md": "old",
        "usage.md": "same",
        "gone.md": "x",
        "openapi.json": openapi(("get", "/api/tags/"), ("post", "/api/tags/")),
    }
    new = {
        "api.md": "new",
        "usage.md": "same",
        "added.md": "y",
        "openapi.json": openapi(("get", "/api/tags/"), ("get", "/api/saved_views/")),
    }
    vendored = vendored_tree(tmp_path / "a", version="3.4.2", files=old)
    fresh = vendored_tree(tmp_path / "b", version="3.5.0", files=new)

    drift = scheduled_jobs.compare_vendored(vendored, fresh)
    report = scheduled_jobs.render_report(drift)

    assert drift.found
    assert (drift.vendored_version, drift.latest_version) == ("3.4.2", "3.5.0")
    assert drift.added == ("added.md",)
    assert drift.removed == ("gone.md",)
    assert drift.changed == ("api.md", "openapi.json")
    assert drift.operations_added == ("GET /api/saved_views/",)
    assert drift.operations_removed == ("POST /api/tags/",)
    assert "vendored `3.4.2`, latest `3.5.0`" in report
    assert "`added.md`" in report
    assert "`gone.md`" in report
    assert "`api.md`" in report
    assert "`GET /api/saved_views/`" in report
    assert "`POST /api/tags/`" in report
    assert "just docs-sync-paperless" in report


def test_changed_content_of_the_same_release_is_drift(tmp_path: Path) -> None:
    vendored = vendored_tree(tmp_path / "a", version="3.4.2", files={"api.md": "old"})
    fresh = vendored_tree(tmp_path / "b", version="3.4.2", files={"api.md": "new"})

    drift = scheduled_jobs.compare_vendored(vendored, fresh)

    assert drift.found
    assert drift.changed == ("api.md",)


def test_an_upstream_marker_without_a_version_is_an_error(tmp_path: Path) -> None:
    vendored = vendored_tree(tmp_path / "a", version="3.4.2", files={})
    fresh = vendored_tree(tmp_path / "b", version="3.4.2", files={})
    _ = (fresh / "UPSTREAM").write_text("abc no-version-here\n")

    with pytest.raises(scheduled_jobs.JobError, match="names no release version"):
        _ = scheduled_jobs.compare_vendored(vendored, fresh)


def test_an_older_latest_release_is_not_drift(tmp_path: Path) -> None:
    vendored = vendored_tree(tmp_path / "a", version="3.5.0", files={"api.md": "new"})
    fresh = vendored_tree(tmp_path / "b", version="3.4.2", files={"api.md": "old"})

    assert not scheduled_jobs.compare_vendored(vendored, fresh).found


def test_a_long_report_is_truncated_per_section(tmp_path: Path) -> None:
    many = {f"doc{n:03}.md": "x" for n in range(80)}
    vendored = vendored_tree(tmp_path / "a", version="3.4.2", files={})
    fresh = vendored_tree(tmp_path / "b", version="3.5.0", files=many)

    report = scheduled_jobs.render_report(scheduled_jobs.compare_vendored(vendored, fresh))

    assert "`doc049.md`" in report
    assert "`doc050.md`" not in report
    assert "30 more" in report


class FakeGh:
    """Records the `gh` calls and answers `issue list` from a scripted list of open issues."""

    def __init__(self, open_issues: list[dict[str, int]]) -> None:
        self.open_issues: list[dict[str, int]] = open_issues
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> str:
        self.calls.append(tuple(command))
        if command[1:3] == ["issue", "list"]:
            return json.dumps(self.open_issues)
        return ""


def test_open_issue_creates_the_label_and_the_issue_when_none_is_open() -> None:
    gh = FakeGh([])

    message = scheduled_jobs.open_issue(gh, title="T", label="drift", body="B")

    assert message == "opened a new issue"
    assert [call[1:3] for call in gh.calls] == [
        ("issue", "list"),
        ("label", "create"),
        ("issue", "create"),
    ]
    list_open = ("gh", "issue", "list", "--label", "drift", "--state", "open", "--json", "number")
    assert gh.calls[0] == list_open
    assert gh.calls[1][:6] == ("gh", "label", "create", "drift", "--force", "--description")
    create = ("gh", "issue", "create", "--title", "T", "--label", "drift", "--body", "B")
    assert gh.calls[2] == create


def test_open_issue_updates_the_open_one_instead_of_creating_a_duplicate() -> None:
    gh = FakeGh([{"number": 7}])

    message = scheduled_jobs.open_issue(gh, title="T", label="drift", body="Headline\nmore")

    assert message == "updated issue #7"
    assert [call[1:3] for call in gh.calls] == [
        ("issue", "list"),
        ("issue", "edit"),
        ("issue", "comment"),
    ]
    assert gh.calls[1] == (
        "gh",
        "issue",
        "edit",
        "7",
        "--title",
        "T",
        "--body",
        "Headline\nmore",
    )
    assert gh.calls[2] == ("gh", "issue", "comment", "7", "--body", "Drift still present. Headline")


def test_close_issue_closes_every_open_one_with_a_comment() -> None:
    gh = FakeGh([{"number": 7}, {"number": 9}])

    message = scheduled_jobs.close_issue(gh, label="drift", comment="Resolved")

    assert message == "closed issue #7, #9"
    assert [call[1:5] for call in gh.calls[1:]] == [
        ("issue", "close", "7", "--comment"),
        ("issue", "close", "9", "--comment"),
    ]
    assert gh.calls[1][-1] == "Resolved"


def test_close_issue_does_nothing_when_none_is_open() -> None:
    gh = FakeGh([])

    assert scheduled_jobs.close_issue(gh, label="drift", comment="Resolved") == "no open issue"
    assert len(gh.calls) == 1


def test_run_returns_the_output_of_the_command() -> None:
    assert scheduled_jobs.run([sys.executable, "-c", "print('hi')"]) == "hi\n"


def test_run_turns_a_failing_command_into_a_job_error() -> None:
    command = [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]
    with pytest.raises(scheduled_jobs.JobError, match=r"exit code 3.*boom"):
        _ = scheduled_jobs.run(command)


STATS = {
    "killed": 70,
    "survived": 20,
    "total": 110,
    "no_tests": 5,
    "skipped": 10,
    "suspicious": 0,
    "timeout": 10,
    "check_was_interrupted_by_user": 0,
    "segfault": 0,
}


def test_mutation_score_counts_killed_and_timeout_over_all_but_skipped_mutants() -> None:
    assert scheduled_jobs.mutation_score(STATS) == 80.0


def test_mutation_score_is_none_without_tested_mutants() -> None:
    assert scheduled_jobs.mutation_score({"total": 3, "skipped": 3}) is None
    assert scheduled_jobs.mutation_score({}) is None


def test_the_gate_is_eighty_percent() -> None:
    assert scheduled_jobs.MUTATION_GATE == 80.0


def test_mutation_summary_shows_the_score_the_counts_and_the_gate() -> None:
    summary = scheduled_jobs.render_mutation_summary(scheduled_jobs.judge(STATS))

    assert "Mutation score: 80.0 % (gate 80 %: passed)" in summary
    assert "| Survived | 20 |" in summary
    assert "| Killed | 70 |" in summary
    assert "report-only" not in summary


def test_mutation_summary_without_tested_mutants_fails_the_gate() -> None:
    verdict = scheduled_jobs.judge({"total": 0})

    assert not verdict.passed
    assert "Mutation score: n/a (gate 80 %: FAILED)" in scheduled_jobs.render_mutation_summary(
        verdict
    )


def test_a_score_just_below_the_gate_fails_and_exactly_at_it_passes() -> None:
    at_gate = {"total": 100, "killed": 80, "survived": 20}
    below = {"total": 1000, "killed": 799, "survived": 201}

    assert scheduled_jobs.judge(at_gate).passed
    assert not scheduled_jobs.judge(below).passed
    assert not scheduled_jobs.judge({**below, "killed": 700, "timeout": 99}).passed
    assert scheduled_jobs.judge({**below, "killed": 700, "timeout": 100}).passed


def test_the_gate_is_a_parameter_with_the_constant_as_default() -> None:
    stats = {"total": 100, "killed": 90, "survived": 10}

    assert scheduled_jobs.judge(stats, gate=95.0).gate == 95.0
    assert not scheduled_jobs.judge(stats, gate=95.0).passed
    assert scheduled_jobs.judge(stats).gate == scheduled_jobs.MUTATION_GATE


@pytest.mark.parametrize(
    "status", ["suspicious", "no_tests", "segfault", "check_was_interrupted_by_user"]
)
def test_mutants_that_were_not_properly_judged_count_against_the_score(status: str) -> None:
    stats = {"total": 100, "killed": 80, status: 20}

    verdict = scheduled_jobs.judge(stats)

    assert verdict.score == 80.0
    assert verdict.passed
    assert not scheduled_jobs.judge({**stats, "killed": 79, status: 21}).passed


RESULTS = """\
    paperless_mcp.services.a.x_f__mutmut_1: survived
    paperless_mcp.services.a.x_f__mutmut_2: survived
    paperless_mcp.paperless.b.x_g__mutmut_3: timeout
    paperless_mcp.paperless.b.x_g__mutmut_4: suspicious
"""
EQUIVALENTS_TOML = """\
[[equivalent]]
mutant = "paperless_mcp.services.a.x_f__mutmut_1"
reason = "the argument is ignored at run time"

[[equivalent]]
mutant = "paperless_mcp.services.a.x_f__mutmut_9"
reason = "was renumbered away"
"""


def test_results_are_read_as_mutant_and_status() -> None:
    assert scheduled_jobs.parse_results(RESULTS) == {
        "paperless_mcp.services.a.x_f__mutmut_1": "survived",
        "paperless_mcp.services.a.x_f__mutmut_2": "survived",
        "paperless_mcp.paperless.b.x_g__mutmut_3": "timeout",
        "paperless_mcp.paperless.b.x_g__mutmut_4": "suspicious",
    }
    assert scheduled_jobs.parse_results("") == {}


def test_equivalents_are_read_with_their_reasons() -> None:
    assert scheduled_jobs.parse_equivalents(EQUIVALENTS_TOML) == {
        "paperless_mcp.services.a.x_f__mutmut_1": "the argument is ignored at run time",
        "paperless_mcp.services.a.x_f__mutmut_9": "was renumbered away",
    }
    assert scheduled_jobs.parse_equivalents("") == {}


@pytest.mark.parametrize(
    "text",
    [
        '[[equivalent]]\nmutant = "m"\n',
        '[[equivalent]]\nmutant = "m"\nreason = "  "\n',
        '[[equivalent]]\nreason = "r"\n',
        '[[equivalent]]\nmutant = "m"\nreason = "r"\n[[equivalent]]\nmutant = "m"\nreason = "s"\n',
        "not = [valid",
    ],
    ids=["no reason", "blank reason", "no mutant", "duplicate", "bad toml"],
)
def test_an_equivalent_without_a_reason_or_a_duplicate_is_refused(text: str) -> None:
    with pytest.raises(scheduled_jobs.JobError):
        _ = scheduled_jobs.parse_equivalents(text)


def test_reviewed_equivalent_survivors_leave_the_denominator_like_skipped_mutants() -> None:
    stats = {"total": 100, "killed": 76, "survived": 24}
    results = {f"m{n}": "survived" for n in range(24)}
    equivalents = {f"m{n}": "reason" for n in range(4)}

    without = scheduled_jobs.judge(stats, results=results)
    with_equivalents = scheduled_jobs.judge(stats, results=results, equivalents=equivalents)

    assert (without.score, without.passed) == (76.0, False)
    assert with_equivalents.score == pytest.approx(76 / 96 * 100)
    assert with_equivalents.equivalent == 4
    assert with_equivalents.stale == ()


def test_only_survivors_can_be_equivalent_and_the_rest_is_reported_as_stale() -> None:
    stats = {"total": 10, "killed": 6, "timeout": 1, "survived": 1, "suspicious": 2}
    results = {"m1": "survived", "m2": "timeout", "m3": "suspicious"}
    equivalents = {"m1": "r", "m2": "r", "m3": "r", "gone": "r"}

    verdict = scheduled_jobs.judge(stats, results=results, equivalents=equivalents)

    assert verdict.equivalent == 1
    assert verdict.stale == ("gone", "m2", "m3")
    assert verdict.score == pytest.approx(7 / 9 * 100)


def test_the_summary_names_the_formula_the_equivalents_and_the_stale_entries() -> None:
    stats = {"total": 10, "killed": 9, "survived": 1}
    verdict = scheduled_jobs.judge(
        stats, results={"m1": "survived"}, equivalents={"m1": "r", "gone": "r"}
    )

    summary = scheduled_jobs.render_mutation_summary(verdict)

    assert "| Survived, reviewed as equivalent (not scored) | 1 |" in summary
    assert "(killed + timeout) / (total - skipped - equivalent)" in summary
    assert "Stale entries in mutation-equivalents.toml" in summary
    assert "- `gone`" in summary
    assert "Stale" not in scheduled_jobs.render_mutation_summary(scheduled_jobs.judge(STATS))


def run_main(
    argv: list[str], *, stdin: str = "", gh: FakeGh | None = None
) -> tuple[int, str, FakeGh]:
    code, out, _, runner = run_main_err(argv, stdin=stdin, gh=gh)
    assert isinstance(runner, FakeGh)
    return code, out, runner


def run_main_err(
    argv: list[str], *, stdin: str = "", gh: FakeGh | FakeDocker | None = None
) -> tuple[int, str, str, FakeGh | FakeDocker]:
    out, err = io.StringIO(), io.StringIO()
    runner = gh or FakeGh([])
    code = scheduled_jobs.main(argv, stdin=io.StringIO(stdin), out=out, err=err, runner=runner)
    return code, out.getvalue(), err.getvalue(), runner


def test_main_latest_3x_reads_ls_remote_output_from_stdin() -> None:
    code, out, _ = run_main(["latest", "--major", "3"], stdin=LS_REMOTE)

    assert (code, out) == (0, "3.10.1\n")


def test_main_latest_reports_a_missing_release_on_stderr() -> None:
    code, out, err, _ = run_main_err(["latest", "--major", "9"], stdin=LS_REMOTE)

    assert (code, out) == (1, "")
    assert "no stable 9.x release" in err


def test_main_drift_writes_the_report_and_exits_10_on_drift(tmp_path: Path) -> None:
    vendored = vendored_tree(tmp_path / "a", version="3.4.2", files={"api.md": "old"})
    fresh = vendored_tree(tmp_path / "b", version="3.5.0", files={"api.md": "new"})
    report = tmp_path / "report.md"

    code, out, _ = run_main(["drift", str(vendored), str(fresh), "--report", str(report)])

    assert code == 10
    assert out == "drift: vendored 3.4.2, latest 3.5.0\n"
    assert "`api.md`" in report.read_text()


def test_main_drift_exits_0_and_writes_nothing_without_drift(tmp_path: Path) -> None:
    vendored = vendored_tree(tmp_path / "a", version="3.4.2", files={"api.md": "same"})
    fresh = vendored_tree(tmp_path / "b", version="3.4.2", files={"api.md": "same"})
    report = tmp_path / "report.md"

    code, out, _ = run_main(["drift", str(vendored), str(fresh), "--report", str(report)])

    assert (code, out) == (0, "no drift: vendored 3.4.2, latest 3.4.2\n")
    assert not report.exists()


def test_main_open_issue_passes_title_label_and_report_to_gh(tmp_path: Path) -> None:
    body = tmp_path / "report.md"
    _ = body.write_text("the report")

    code, out, gh = run_main(
        ["open-issue", "--title", "T", "--label", "drift", "--body-file", str(body)]
    )

    assert (code, out) == (0, "opened a new issue\n")
    assert gh.calls[-1][-1] == "the report"


def test_main_mutation_score_prints_the_summary_and_passes_at_the_gate(tmp_path: Path) -> None:
    stats = tmp_path / "stats.json"
    _ = stats.write_text(json.dumps(STATS))

    code, out, _ = run_main(["mutation-score", str(stats)])

    assert code == 0
    assert "Mutation score: 80.0 % (gate 80 %: passed)" in out


def test_main_mutation_score_prints_the_summary_and_exits_nonzero_below_the_gate(
    tmp_path: Path,
) -> None:
    stats = tmp_path / "stats.json"
    _ = stats.write_text(json.dumps({"total": 10, "killed": 7, "survived": 3}))

    code, out, err, _ = run_main_err(["mutation-score", str(stats)])

    assert code == scheduled_jobs.GATE_FAILED_EXIT_CODE
    assert scheduled_jobs.GATE_FAILED_EXIT_CODE not in {0, 1, scheduled_jobs.DRIFT_EXIT_CODE}
    assert "Mutation score: 70.0 % (gate 80 %: FAILED)" in out
    assert "below the gate" in err


def test_main_mutation_score_takes_the_gate_results_and_equivalents(tmp_path: Path) -> None:
    stats = tmp_path / "stats.json"
    _ = stats.write_text(json.dumps({"total": 10, "killed": 7, "survived": 3}))
    results = tmp_path / "survivors.txt"
    _ = results.write_text("    m1: survived\n    m2: survived\n    m3: survived\n")
    equivalents = tmp_path / "equivalents.toml"
    _ = equivalents.write_text(
        '[[equivalent]]\nmutant = "m1"\nreason = "r"\n[[equivalent]]\nmutant = "m2"\nreason = "r"\n'
    )

    code, out, _, _ = run_main_err(
        [
            "mutation-score",
            str(stats),
            "--results",
            str(results),
            "--equivalents",
            str(equivalents),
            "--gate",
            "85",
        ]
    )

    assert code == 0  # 7 of 8 is 87.5 %
    assert "Mutation score: 87.5 % (gate 85 %: passed)" in out


def test_a_report_with_exactly_the_limit_lists_everything_without_a_more_line(
    tmp_path: Path,
) -> None:
    files = {f"doc{n:03}.md": "x" for n in range(scheduled_jobs.MAX_LISTED)}
    vendored = vendored_tree(tmp_path / "a", version="3.4.2", files={})
    fresh = vendored_tree(tmp_path / "b", version="3.5.0", files=files)

    report = scheduled_jobs.render_report(scheduled_jobs.compare_vendored(vendored, fresh))

    assert "`doc049.md`" in report
    assert "more" not in report.replace("Files added", "")


class FakeDocker:
    """Answers `docker manifest inspect` for the images in `published`; fails for the others."""

    def __init__(self, published: set[str]) -> None:
        self.published: set[str] = published
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> str:
        self.calls.append(tuple(command))
        if command[-1] not in self.published:
            message = f"no such manifest: {command[-1]}"
            raise scheduled_jobs.JobError(message)
        return "{}"


IMAGE = "ghcr.io/x/p"


def test_latest_published_release_takes_the_highest_tag_whose_image_exists() -> None:
    docker = FakeDocker({f"{IMAGE}:3.4.2", f"{IMAGE}:3.0.0"})

    chosen = scheduled_jobs.latest_published_release(LS_REMOTE, 3, IMAGE, docker)

    assert chosen == "3.4.2"
    assert [call[-1] for call in docker.calls] == [f"{IMAGE}:3.10.1", f"{IMAGE}:3.4.2"]
    assert docker.calls[0][:3] == ("docker", "manifest", "inspect")


def test_latest_published_release_fails_when_no_image_exists() -> None:
    with pytest.raises(scheduled_jobs.JobError, match=r"no published image for any 3\.x release"):
        _ = scheduled_jobs.latest_published_release(LS_REMOTE, 3, IMAGE, FakeDocker(set()))


def test_main_latest_with_an_image_skips_unpublished_releases_and_says_so() -> None:
    docker = FakeDocker({f"{IMAGE}:3.4.2"})

    code, out, err, _ = run_main_err(
        ["latest", "--major", "3", "--image", IMAGE], stdin=LS_REMOTE, gh=docker
    )

    assert (code, out) == (0, "3.4.2\n")
    assert "3.10.1 has no published image" in err


def test_main_release_accepts_a_release_of_the_major_version() -> None:
    assert run_main(["release", "3.5.1", "--major", "3"])[:2] == (0, "3.5.1\n")


@pytest.mark.parametrize("text", ["3.5", "v3.5.1", "3.5.1-beta", "4.0.0", ""])
def test_main_release_rejects_anything_else(text: str) -> None:
    code, out, err, _ = run_main_err(["release", text, "--major", "3"])

    assert (code, out) == (1, "")
    assert "scheduled_jobs:" in err


def test_main_close_issue_closes_the_open_one() -> None:
    code, out, gh = run_main(
        ["close-issue", "--label", "drift", "--comment", "Resolved"], gh=FakeGh([{"number": 4}])
    )

    assert (code, out) == (0, "closed issue #4\n")
    assert gh.calls[-1][:4] == ("gh", "issue", "close", "4")
