---
id: PLMCP-21
title: Enforce the mutation-score gate
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-14
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: chore
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
From M2 on, the mutation score is a gate, not a report (design §9.2).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A mutation score of at least 80 % on services/ and paperless/ is enforced by the weekly and on-demand runs
- [ ] #2 Every surviving mutant is either killed by a new test, turned into a Backlog task, or documented as equivalent
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
