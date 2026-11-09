---
id: PLMCP-15
title: Toolset selection and read-only mode
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-8
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The user wants to be able to do everything, but the default tool list must stay small so the model chooses well. Only core (plus ai when available) is on by default; everything else is opt-in (design §5.8).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 PAPERLESS_MCP_TOOLSETS (list or all, default core), PAPERLESS_MCP_READ_ONLY and PAPERLESS_MCP_DISABLED_TOOLS control which tools are visible
- [ ] #2 Read-only mode leaves only tools with read_only_hint (registry test)
- [ ] #3 Startup fails with a clear message when the filters leave zero tools
- [ ] #4 Toolsets whose Paperless capability is missing (e.g. AI disabled) are hidden automatically
- [ ] #5 The contract snapshot covers the profiles default, all and read-only
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
