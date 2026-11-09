---
id: PLMCP-6
title: Build the Paperless HTTP adapter foundation with schema-guarded tests
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-09T21:31:00Z'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. domain/errors.py: PaperlessMcpError(message, hint) + NotFound, Validation, PermissionDenied, Conflict, AuthenticationFailed (401), UpstreamUnavailable, UnsupportedVersion (406). domain/credentials.py: opaque frozen PaperlessCredential (auth header hidden from repr, fingerprint) and CredentialProvider Protocol.
2. paperless/auth.py: StaticTokenProvider (Authorization: Token ..., fingerprint = first 16 hex of SHA-256).
3. paperless/errors.py: HTTP status and transport failures to domain errors; 400 field messages flattened, length-capped and stripped of control characters.
4. paperless/client.py: PaperlessClient over one injected httpx2.AsyncClient; Accept version=10 + credential header per request; retries GET/HEAD/OPTIONS/PUT/DELETE on 429/502/503/504 honouring Retry-After (seconds or HTTP date) within attempts + sleep budget, exponential backoff with full jitter, injectable sleep; separate upload timeout for multipart requests; typed get() parsing into pydantic models; logs never carry headers.
5. paperless/pagination.py: generic Page[T] (next/previous reduced to page numbers) and collect() with hard item cap and truncated flag.
6. tests/adapter/schema_guard.py: MockTransport wrapper validating every request and response against docs/vendor/paperless/openapi.json with openapi-core (thin httpx2 adapter); rejects undeclared query params; explicit exception list with reasons; undocumented DRF 4xx error bodies checked for DRF error shape. Tests prove the guard bites.
7. Adapter tests (TDD) on real endpoint shapes: GET /api/tags/ pages, 404 /api/documents/{id}/, 400 POST /api/tags/; decision markers 2, 3, 9. Dev deps: openapi-core, anyio (pytest plugin), hypothesis. deptry: drop httpx2 from DEP002.
8. Live smoke in tests/e2e: paginated tags via the real client, 401 with a bad token.
9. just check, just e2e, just e2e-down; report.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented domain errors + credential seam, StaticTokenProvider, PaperlessClient (version-10 Accept + credential header per request, idempotent-only retries honouring Retry-After within attempts and a sleep budget, upload timeout for multipart), error mapping, Page[T]/collect pagination, and the openapi-core schema guard (tests/adapter/schema_guard.py) with bite tests. Choices: 401 maps to a new AuthenticationFailed, 406 to UnsupportedVersion; unreachable and timeout are both UpstreamUnavailable with distinct messages and hints; transport errors are not retried. Gates: deptry DEP002 no longer lists httpx2; the 'Only paperless imports httpx2' contract keeps its explicit source list (complete because the layers contract is exhaustive) and was probed to break on an httpx2 import in domain; ruff per-file N818 ignore for the design's error names. Dev deps: openapi-core, anyio, hypothesis. E2E smoke tests/e2e/test_client.py passes against 3.4.2.
<!-- SECTION:NOTES:END -->
