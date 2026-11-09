---
id: PLMCP-36
title: claude.ai connector via OAuth/OIDC proxy
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
labels: []
milestone: m-5
dependencies:
  - PLMCP-34
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 36000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
claude.ai custom connectors need OAuth. FastMCP 4 provides OIDCProxy/OAuthProxy (DCR, CIMD, claude.ai callback). The identity provider is chosen at the start of this task (design §5.7, deferred decision).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A Backlog decision records the chosen identity provider and why, with its Enforcement section
- [ ] #2 The auth provider is plugged in at the composition root only; nothing below server.py changes
- [ ] #3 An automated test runs the OAuth flow with the FastMCP client; the claude.ai callback is allowlisted
- [ ] #4 Verified manually as a claude.ai connector; README documents the setup
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
