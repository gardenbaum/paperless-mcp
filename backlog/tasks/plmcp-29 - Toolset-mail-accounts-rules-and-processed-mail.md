---
id: PLMCP-29
title: 'Toolset mail: accounts, rules and processed mail'
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
ordinal: 29000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Mail import is configured through accounts and rules. Credentials must never flow back to the client (design §6, §7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Mail accounts list, get, create, update, delete (confirm), test and process; passwords are write-only and never returned
- [ ] #2 Mail rules list, get, create, update and delete (confirm) with taxonomy by name
- [ ] #3 list_processed_mail returns compact rows
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
