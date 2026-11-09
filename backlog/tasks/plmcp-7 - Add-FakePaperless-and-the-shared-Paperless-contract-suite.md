---
id: PLMCP-7
title: Add FakePaperless and the shared Paperless contract suite
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-0
dependencies:
  - PLMCP-5
  - PLMCP-6
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Services and tools are tested against an in-memory fake instead of mocks. To keep the fake honest, one contract suite runs against the fake in the unit run and against the real adapter plus live Paperless in E2E (design §9.1). Every later task that adds port methods extends both.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 FakePaperless implements the ports defined so far with realistic pagination, permission behaviour (403/404 for objects the credential cannot see) and PATCH semantics
- [ ] #2 FakePaperless rejects write calls unless the test opts in (write guard)
- [ ] #3 The contract suite is parametrised over the fake and the live backend; just check runs it against the fake, just e2e against Paperless 3.4.2
- [ ] #4 A test fails when a port method has no contract test
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
