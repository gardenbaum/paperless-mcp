---
id: PLMCP-17
title: Notes, trash deletion and the confirmation flow
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-15
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Destructive actions need an explicit confirmation that works on every MCP protocol era; elicitation is unavailable on the sessionless 2026-07-28 era (design §5.5).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 add_note, delete_note and delete_document exist
- [ ] #2 A destructive tool called without confirm=true changes nothing and returns what would happen and how to confirm (verified with the write guard)
- [ ] #3 delete_document moves the document to the trash and says so
- [ ] #4 The registry test enforces confirm on every destructive tool
- [ ] #5 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
