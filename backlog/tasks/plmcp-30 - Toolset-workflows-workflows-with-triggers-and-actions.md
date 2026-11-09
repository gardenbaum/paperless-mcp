---
id: PLMCP-30
title: 'Toolset workflows: workflows with triggers and actions'
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
labels: []
milestone: m-4
dependencies:
  - PLMCP-15
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 30000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Workflows automate filing in Paperless; triggers and actions are nested objects with many types (design §6).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Workflows list, get, create, update and delete (confirm), with triggers and actions nested and taxonomy by name
- [ ] #2 Unsupported trigger or action types are rejected with an explicit error
- [ ] #3 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
- [ ] #4 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
