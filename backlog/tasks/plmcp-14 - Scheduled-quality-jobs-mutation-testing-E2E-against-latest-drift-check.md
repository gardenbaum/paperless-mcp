---
id: PLMCP-14
title: 'Scheduled quality jobs: mutation testing, E2E against latest, drift check'
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-1
dependencies:
  - PLMCP-8
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: chore
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Weekly jobs catch weak tests and upstream drift. Tests written by AI often only restate the code; mutation testing measures that. Paperless releases monthly (design §9.2, §9.5, §14).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 mutmut is configured for services/ and paperless/; a weekly workflow publishes the mutation score (report-only until PLMCP-21)
- [ ] #2 A weekly workflow runs the E2E suite against the latest paperless-ngx 3.x image
- [ ] #3 A weekly drift check compares the vendored Paperless schema and docs with the latest 3.x release and opens a GitHub issue when they differ
- [ ] #4 Every scheduled job can also be started on demand (workflow_dispatch and a just recipe)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
