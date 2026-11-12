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
    assert gh.calls[0] == (
        "gh", "issue", "list", "--label", "drift", "--state", "open", "--json", "number",
    )  # fmt: skip
    assert gh.calls[1][:6] == ("gh", "label", "create", "drift", "--force", "--description")
    assert gh.calls[2] == (
        "gh", "issue", "create", "--title", "T", "--label", "drift", "--body", "B",
    )  # fmt: skip


def test_open_issue_updates_the_open_one_instead_of_creating_a_duplicate() -> None:
    gh = FakeGh([{"number": 7}])

    message = scheduled_jobs.open_issue(gh, title="T", label="drift", body="B")

    assert message == "updated issue #7"
    assert [call[1:3] for call in gh.calls] == [("issue", "list"), ("issue", "edit")]
    assert gh.calls[1] == ("gh", "issue", "edit", "7", "--title", "T", "--body", "B")


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


def test_mutation_summary_shows_the_score_the_counts_and_that_it_is_report_only() -> None:
    summary = scheduled_jobs.render_mutation_summary(STATS)

    assert "Mutation score: 80.0 %" in summary
    assert "| Survived | 20 |" in summary
    assert "| Killed | 70 |" in summary
    assert "report-only" in summary


def test_mutation_summary_without_tested_mutants_says_so() -> None:
    assert "Mutation score: n/a" in scheduled_jobs.render_mutation_summary({"total": 0})


def run_main(
    argv: list[str], *, stdin: str = "", gh: FakeGh | None = None
) -> tuple[int, str, FakeGh]:
    out = io.StringIO()
    runner = gh or FakeGh([])
    code = scheduled_jobs.main(argv, stdin=io.StringIO(stdin), out=out, runner=runner)
    return code, out.getvalue(), runner


def test_main_latest_3x_reads_ls_remote_output_from_stdin() -> None:
    code, out, _ = run_main(["latest", "--major", "3"], stdin=LS_REMOTE)

    assert (code, out) == (0, "3.10.1\n")


def test_main_latest_reports_a_missing_release_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run_main(["latest", "--major", "9"], stdin=LS_REMOTE)

    assert (code, out) == (1, "")
    assert "no stable 9.x release" in capsys.readouterr().err


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


def test_main_mutation_score_prints_the_summary(tmp_path: Path) -> None:
    stats = tmp_path / "stats.json"
    _ = stats.write_text(json.dumps(STATS))

    code, out, _ = run_main(["mutation-score", str(stats)])

    assert code == 0
    assert "Mutation score: 80.0 %" in out
