---
id: PLMCP-3
title: Vendor FastMCP and Paperless reference docs with reading guides
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-09T20:41:00Z'
labels: []
milestone: m-0
dependencies:
  - PLMCP-1
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: docs
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Agents must consult current docs instead of memory: FastMCP 4 (MCP SDK v2, httpx2) is newer than most training data, and Paperless 3.x changed search (Tantivy), the API version (10), and added versions and AI. The vendored OpenAPI schema is also the input of the schema-guard tests (design §8, §9.2). tosca.mcp has a working docs-sync recipe to adapt.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 just docs-sync vendors the docs/ tree of the latest stable FastMCP vX.Y.Z tag into docs/vendor/fastmcp with an UPSTREAM marker (commit + tag)
- [x] #2 just docs-sync-paperless vendors openapi.json generated from the pinned paperless-ngx image 3.4.2, plus api.md and the search/usage docs from the same tag, into docs/vendor/paperless with an UPSTREAM marker
- [x] #3 Both recipes fetch into a temporary directory first and leave the committed copy untouched when any step fails
- [x] #4 docs/fastmcp-reading-guide.md and docs/paperless-reading-guide.md triage the vendored docs for this project
- [x] #5 CLAUDE.md and AGENTS.md contain the AI contributor rules of design §9.6 and point to the reading guides
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. justfile: docs-sync (FastMCP latest vX.Y.Z, sparse docs/, prune, temp-first) and docs-sync-paperless (PAPERLESS_VERSION 3.4.2; openapi.json via spectacular in pinned image; api.md/usage/search docs from same tag; temp-first).
2. Probe failure safety with broken tag/URL.
3. Reading guides for FastMCP and Paperless.
4. CLAUDE.md/AGENTS.md project section (design 9.6 rules + guide pointers).
5. Check gitleaks/hygiene; just check; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Task review (sonnet): approved, no Critical/Important. Vendored FastMCP v4.1.6 and paperless-ngx v3.4.2 (openapi.json from pinned image, 93 paths). Failure-safety proven by 4 probes (implementer report).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added `just docs-sync` (FastMCP latest stable tag, docs/ only, pruned v2/v3/contributor pages/media) and `just docs-sync-paperless` (PAPERLESS_VERSION 3.4.2: OpenAPI schema generated via drf-spectacular from the pinned image + api/usage/advanced_usage/migration-v3 docs), both staging in a temp dir and swapping only after every step succeeds. Wrote docs/fastmcp-reading-guide.md and docs/paperless-reading-guide.md, and added the AI contributor rules (design §9.6) to CLAUDE.md/AGENTS.md.
Known gap: more_like_id/all are documented in api.md but absent from the generated schema (noted in the guide; relevant for the PLMCP-6 schema guard). Minor follow-ups: unreachable friendly error on missing tag, non-atomic cross-filesystem swap, two cosmetic guide inaccuracies.
<!-- SECTION:FINAL_SUMMARY:END -->
