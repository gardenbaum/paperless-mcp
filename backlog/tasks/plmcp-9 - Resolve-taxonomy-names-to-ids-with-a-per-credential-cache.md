---
id: PLMCP-9
title: Resolve taxonomy names to ids with a per-credential cache
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-1
dependencies:
  - PLMCP-8
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Paperless wants ids; users and LLMs think in names. None of the reference servers does this correctly: one resolves only page 1, another picks the first fuzzy match. Name resolution is this server's main differentiator (design §5.4).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Resolves tags, correspondents, document types, storage paths, custom fields, saved views, users and groups
- [ ] #2 Numeric input is an id; otherwise exact match, then case- and accent-insensitive match; more than one match raises Ambiguous with the candidates; no match raises NotFound with the closest suggestions; a fuzzy match is never picked automatically (property tests with hypothesis)
- [ ] #3 The same name in different kinds (tag Tax vs document type Tax) resolves independently per kind
- [ ] #4 Objects the credential cannot see are treated as not existing, never leaked through suggestions
- [ ] #5 The cache loads all pages, expires after a TTL, is keyed by credential fingerprint and is invalidated by the server's own writes
- [ ] #6 list_taxonomy(kind, name_contains) and paperless://taxonomy/{kind} return compact rows (id, name, document count; colour and parent for tags; data type and select options for custom fields)
- [ ] #7 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
