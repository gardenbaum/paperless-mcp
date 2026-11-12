---
id: PLMCP-37
title: Per-principal Paperless credentials
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-12T19:42:00Z'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Deferred from PLMCP-16 (multi-user): update_document reads a document and then PATCHes the merged tag and custom field lists, so with several principals a concurrent change in between is overwritten. Route tag and custom-field list changes through bulk_edit modify_tags / modify_custom_fields (server-side add/remove, validated; verified live in PLMCP-16) to avoid the race; see the PLMCP-16 notes.
<!-- SECTION:NOTES:END -->
