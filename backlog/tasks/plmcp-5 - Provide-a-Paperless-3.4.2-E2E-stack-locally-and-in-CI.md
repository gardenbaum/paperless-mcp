---
id: PLMCP-5
title: Provide a Paperless 3.4.2 E2E stack locally and in CI
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-09T21:11:00Z'
labels: []
milestone: m-0
dependencies:
  - PLMCP-2
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: chore
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The honest-fake guarantee and all API assumptions need a real Paperless. One compose file serves local development and CI (design §9.2). Paperless 3.x needs Redis; SQLite is enough.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 tests/e2e/compose.yaml runs paperless-ngx 3.4.2 and Redis with a fixed admin user and API token; just e2e-up, just e2e and just e2e-down manage it
- [x] #2 A session fixture waits for readiness and seeds documents, tags (incl. a parent/child pair), correspondents, document types, storage paths, a non-admin user with restricted permissions, and custom fields of every data type with values
- [x] #3 Tests marked e2e are skipped by just check and run by just e2e
- [x] #4 CI job e2e runs on every pull request and is a required check for main
- [x] #5 A smoke test asserts the instance reports API version 10
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
1. tests/e2e/compose.yaml: paperless-ngx 3.4.2 (digest) + redis (digest), SQLite, fixed localhost port, admin via env. 2. tests/e2e/conftest.py: readiness poll, token via POST /api/token/, idempotent API-only seeding (generated PDFs, all custom field types, tag parent/child, restricted user). 3. Marker e2e; addopts deselects it so just check skips it; just e2e-up/e2e/e2e-down. 4. Smoke test: X-Api-Version == 10. 5. CI job e2e (SHA-pinned actions, log upload on failure). 6. Draft PR to validate CI.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Task review (sonnet): approved, no Critical/Important. CI green incl. e2e (1m23s). e2e added to required checks by controller after merge.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added tests/e2e/compose.yaml (paperless-ngx 3.4.2 and Redis pinned by digest, SQLite, localhost:8010), just e2e-up/e2e/e2e-down, an idempotent API-only seeding fixture (all 10 custom-field data types with values, parent/child tags, correspondents, document types, storage path, restricted reader user with own token, a hidden private document), and smoke tests (API version 10 via X-Api-Version, marker decision-2). e2e tests are deselected from just check and coverage; new required CI job e2e uploads container logs on failure.
Follow-ups (minor): reader visibility of shared taxonomy is neither seeded deliberately nor tested (relevant for PLMCP-7); stale reader permissions on reused stacks; wait_ready token plumbing; persist-credentials hardening.
<!-- SECTION:FINAL_SUMMARY:END -->
