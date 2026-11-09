---
id: PLMCP-28
title: 'Toolset permissions: set_permissions, users and groups lookup'
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
labels: []
milestone: m-3
dependencies:
  - PLMCP-9
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 28000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Owners and view/change permissions decide who sees what; a typo must not wipe permissions, as one reference server guards against with strict objects (design §6).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 set_permissions works on documents and taxonomy items: owner and view/change users and groups, by name or id, in merge or replace mode
- [ ] #2 Replace mode and owner changes require confirm; unknown users or groups fail before anything is sent
- [ ] #3 list_users and list_groups return compact rows
- [ ] #4 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
- [ ] #5 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
