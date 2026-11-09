---
id: PLMCP-5
title: Provide a Paperless 3.4.2 E2E stack locally and in CI
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
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
- [ ] #1 tests/e2e/compose.yaml runs paperless-ngx 3.4.2 and Redis with a fixed admin user and API token; just e2e-up, just e2e and just e2e-down manage it
- [ ] #2 A session fixture waits for readiness and seeds documents, tags (incl. a parent/child pair), correspondents, document types, storage paths, a non-admin user with restricted permissions, and custom fields of every data type with values
- [ ] #3 Tests marked e2e are skipped by just check and run by just e2e
- [ ] #4 CI job e2e runs on every pull request and is a required check for main
- [ ] #5 A smoke test asserts the instance reports API version 10
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
