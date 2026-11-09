---
id: PLMCP-37
title: Per-principal Paperless credentials
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
labels: []
milestone: m-6
dependencies:
  - PLMCP-36
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 37000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Several people should use one server with their own Paperless permissions. Where per-user credentials come from (admin mapping, user-supplied tokens or a trusted upstream header) is decided at the start of this task (design §5.7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A Backlog decision records the credential source and why, with its Enforcement section
- [ ] #2 The CredentialProvider resolves the credential from the authenticated MCP principal; requests without a known principal are rejected
- [ ] #3 Caches are keyed by credential fingerprint; E2E with an admin and a restricted user proves that neither sees the other's cached or hidden data
- [ ] #4 README documents the multi-user setup
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
