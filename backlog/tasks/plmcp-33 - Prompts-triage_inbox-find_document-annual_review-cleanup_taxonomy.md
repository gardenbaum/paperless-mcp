---
id: PLMCP-33
title: 'Prompts: triage_inbox, find_document, annual_review, cleanup_taxonomy'
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
labels: []
milestone: m-4
dependencies:
  - PLMCP-16
  - PLMCP-19
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 33000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Guided multi-step tasks belong in prompts the LLM runs with the normal tools, not in server-side agent logic; prompts always propose changes before applying them (design §5.6, non-goals).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The four prompts exist, each declares the toolsets it needs and tells the model to propose and wait for confirmation before writing
- [ ] #2 A test fails when a prompt names a tool that does not exist or is not in its declared toolsets
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
