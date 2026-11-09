"""Guards for the Backlog decisions: every accepted one names its enforcement (design §9.4)."""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
DECISIONS = ROOT / "backlog" / "decisions"
TESTS = ROOT / "tests"
INITIAL = {f"decision-{n}" for n in range(1, 11)}


def read_decisions(directory: Path) -> dict[str, tuple[str, str]]:
    """Map decision id to (status, body) for every decision file in the directory."""
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(directory.glob("*.md")):
        match = re.match(r"---\n(.*?)\n---\n(.*)", path.read_text(encoding="utf-8"), re.DOTALL)
        assert match, f"{path.name}: no frontmatter"
        fields = {
            key: value.strip().strip("'\"").lower()
            for key, _, value in (line.partition(":") for line in str(match[1]).splitlines())
        }
        found[fields["id"]] = (fields["status"], str(match[2]))
    return found


def missing_enforcement(decisions: dict[str, tuple[str, str]]) -> list[str]:
    """Ids of accepted decisions without a non-empty `## Enforcement` section."""
    return [
        decision_id
        for decision_id, (status, body) in decisions.items()
        if status == "accepted"
        and not re.search(r"^## Enforcement[ \t]*\n+(?!#)\S", body, re.MULTILINE)
    ]


def marker_ids(directory: Path) -> set[str]:
    """Ids used as `pytest.mark.decision("id")` anywhere in the test sources."""
    ids: set[str] = set()
    for path in directory.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) == "pytest.mark.decision"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                ids.add(str(node.args[0].value))
    return ids


def write_decision(directory: Path, number: int, status: str, body: str) -> None:
    _ = (directory / f"decision-{number} - t.md").write_text(
        f"---\nid: decision-{number}\ntitle: t\nstatus: {status}\n---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.mark.decision("decision-10")
def test_initial_decisions_are_recorded_and_accepted() -> None:
    decisions = read_decisions(DECISIONS)
    assert decisions.keys() >= INITIAL
    assert {
        decision_id for decision_id in INITIAL if decisions[decision_id][0] != "accepted"
    } == set()


@pytest.mark.decision("decision-10")
def test_every_accepted_decision_names_its_enforcement() -> None:
    assert missing_enforcement(read_decisions(DECISIONS)) == []


@pytest.mark.decision("decision-10")
def test_enforcement_check_flags_accepted_decisions_without_a_section(tmp_path: Path) -> None:
    write_decision(tmp_path, 1, "accepted", "## Context\n\nx")
    write_decision(tmp_path, 2, "accepted", "## Enforcement\n\n")
    write_decision(tmp_path, 3, "accepted", "## Enforcement\n\n## Other\n\ny")
    write_decision(tmp_path, 4, "accepted", "## Enforcement\n\nimport-linter contract")
    write_decision(tmp_path, 5, "proposed", "## Context\n\nx")
    assert missing_enforcement(read_decisions(tmp_path)) == [
        "decision-1",
        "decision-2",
        "decision-3",
    ]


@pytest.mark.decision("decision-10")
def test_every_decision_marker_references_an_existing_decision() -> None:
    assert marker_ids(TESTS) - read_decisions(DECISIONS).keys() == set()


@pytest.mark.decision("decision-10")
def test_marker_scan_finds_ids_in_decorators_and_ignores_other_calls(tmp_path: Path) -> None:
    _ = (tmp_path / "test_x.py").write_text(
        """import pytest
@pytest.mark.decision("decision-7")
def test_a(): ...
pytestmark = pytest.mark.decision("decision-99")
print("decision-5")
""",
        encoding="utf-8",
    )
    assert marker_ids(tmp_path) == {"decision-7", "decision-99"}
    assert marker_ids(tmp_path) - read_decisions(tmp_path).keys() == {"decision-7", "decision-99"}
