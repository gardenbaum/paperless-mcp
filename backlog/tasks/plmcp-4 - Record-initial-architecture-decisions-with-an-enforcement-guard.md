---
id: PLMCP-4
title: Record initial architecture decisions with an enforcement guard
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-09T20:48:00Z'
labels: []
milestone: m-0
dependencies:
  - PLMCP-1
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: docs
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The ten decisions in design §11 must be durable records that agents cannot silently bypass, so every accepted decision names the automated check that enforces it. Decision bodies are written per the decision-body exception in CLAUDE.md/AGENTS.md.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Decisions 1-10 of design §11 exist as Backlog decisions with Context, Decision, Consequences and Enforcement sections and status accepted
- [x] #2 A test fails when an accepted decision has no non-empty Enforcement section
- [x] #3 A pytest marker decision("decision-N") is registered and a test fails when a marker references a decision id that does not exist
- [x] #4 Decision frontmatter is only changed through the backlog CLI
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Write tests/test_decisions.py first (red): parse backlog/decisions, accepted => non-empty Enforcement, decision marker ids exist, decisions 1-10 exist; each check proven on synthetic tmp_path input.
2. Register the decision(id) marker in pyproject.
3. Create decisions 1-10 with the backlog CLI (status accepted), write bodies directly.
4. just check, commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Task review (sonnet): approved, no Critical/Important. Decisions decision-1..decision-10 accepted; guard tests in tests/test_decisions.py.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Recorded the ten initial architecture decisions (design §11) as accepted Backlog decisions, each with Context, Decision, Consequences and a concrete Enforcement section (existing guard or the PLMCP task that adds it). Added tests/test_decisions.py: accepted decisions must have a non-empty Enforcement section, and every @pytest.mark.decision("decision-N") must reference an existing decision; both checks are self-tested on synthetic input. Marker registered in pyproject.
Minor follow-ups: Enforcement regex rejects indented first lines; marker scan only sees the literal pytest.mark.decision spelling; frontmatter parser minimal.
<!-- SECTION:FINAL_SUMMARY:END -->
