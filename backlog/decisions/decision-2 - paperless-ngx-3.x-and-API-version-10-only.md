---
id: decision-2
title: paperless-ngx 3.x and API version 10 only
date: '2026-11-09T20:44:00Z'
status: accepted
---
## Context

paperless-ngx 2.x stops at API version 9; every 3.x release supports version 10, which is the current default. The 3.x line brings the features this server exposes (document versions, trash, PDF operations, AI suggestions). Supporting several API versions would multiply the wire models and the test matrix for no user the project serves. The reference version for development and E2E is 3.4.2.

## Decision

Support paperless-ngx 3.x with API version 10 only.

- Every request sends `Accept: application/json; version=10` and the auth header of the current opaque `PaperlessCredential`.
- A startup probe with a short timeout fails fast with a clear message on a wrong API version or an invalid token; an unreachable Paperless does not stop the start (tools return `UpstreamUnavailable`).
- The vendored schema (`docs/vendor/paperless/openapi.json`, from the 3.4.2 image) is the reference; E2E runs against 3.4.2 on every PR and weekly against the latest 3.x, with a weekly drift check of the vendored schema and docs.

## Consequences

- Paperless 2.x users are not served; this is intentional and stated in the README.
- Upgrading the reference version is a deliberate change (`PAPERLESS_VERSION` in the justfile, `just docs-sync-paperless`, snapshot and schema review).
- A Paperless release that changes API 10 shows up as a failing weekly E2E or drift check, not in production.

## Enforcement

- Adapter test that every request carries `Accept: application/json; version=10` and the credential's auth header: PLMCP-6 (adapter foundation).
- Startup version and token probe tests: PLMCP-8 (walking skeleton).
- E2E pin to 3.4.2 and the weekly run against latest: PLMCP-5 (E2E stack) and PLMCP-14 (scheduled quality jobs, incl. drift check).
- The pin itself: `PAPERLESS_VERSION` in `justfile`, `UPSTREAM` marker of `docs/vendor/paperless/`.
