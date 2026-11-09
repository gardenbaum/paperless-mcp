---
id: PLMCP-4
title: Record initial architecture decisions with an enforcement guard
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-09T20:44:00Z'
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
- [ ] #1 Decisions 1-10 of design §11 exist as Backlog decisions with Context, Decision, Consequences and Enforcement sections and status accepted
- [ ] #2 A test fails when an accepted decision has no non-empty Enforcement section
- [ ] #3 A pytest marker decision("decision-N") is registered and a test fails when a marker references a decision id that does not exist
- [ ] #4 Decision frontmatter is only changed through the backlog CLI
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Write tests/test_decisions.py first (red): parse backlog/decisions, accepted => non-empty Enforcement, decision marker ids exist, decisions 1-10 exist; each check proven on synthetic tmp_path input.
2. Register the decision(id) marker in pyproject.
3. Create decisions 1-10 with the backlog CLI (status accepted), write bodies directly.
4. just check, commit.
<!-- SECTION:PLAN:END -->
