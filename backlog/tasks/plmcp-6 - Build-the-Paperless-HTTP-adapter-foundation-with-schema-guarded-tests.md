---
id: PLMCP-6
title: Build the Paperless HTTP adapter foundation with schema-guarded tests
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-0
dependencies:
  - PLMCP-3
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Every adapter call needs the same transport rules, and every request and response must be checked against the vendored OpenAPI schema, so mismatches like those found in the reference servers (wrong note-delete shape, invalid object_type) fail tests instead of production calls (design §5.3, §9.2).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every request carries Accept: application/json; version=10 and the auth header of the current PaperlessCredential; the CredentialProvider seam has a static-token implementation and credentials expose a non-secret fingerprint
- [ ] #2 Idempotent requests retry on 429/502/503/504 honouring Retry-After within a bounded budget; POST and PATCH never retry
- [ ] #3 HTTP errors map to domain errors: 400 to Validation with sanitised field messages, 401, 403, 404, 409, and 5xx/timeouts/connection errors to UpstreamUnavailable
- [ ] #4 Unreachable Paperless, a slow response hitting the timeout and an expired or revoked token each produce a distinct client-safe error
- [ ] #5 Tokens and Authorization headers never appear in logs, exception messages or reprs (tested)
- [ ] #6 The pagination helper stops at a hard item cap and reports truncated; next/previous links are reduced to page numbers
- [ ] #7 Adapter tests run on httpx2 MockTransport through a schema guard (openapi-core) that fails on any request or recorded response not matching the vendored schema; per-endpoint exceptions must be listed with a reason
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
