---
id: decision-3
title: httpx2 as the only HTTP library with an injectable transport
date: '2026-11-09T20:44:00Z'
status: accepted
---
## Context

FastMCP 4's HTTP stack is httpx2. Two HTTP libraries in one process mean two sets of exceptions, transports and mocking tools, and agents tend to reach for the familiar `httpx`. Tests need a seam to exercise the adapter without patching internals.

## Decision

httpx2 is the only HTTP library, and only the `paperless` adapter imports it. The adapter takes its `httpx2.AsyncClient` (and thus its transport) by constructor injection; tests use httpx2's built-in `MockTransport` (no respx). One client per process, created in the server lifespan.

## Consequences

- No `httpx` import anywhere; `deptry` also rejects an undeclared import.
- Retries, timeouts, pagination caps and redaction live in one place.
- The transport is the second test seam next to constructor injection (decision 8).

## Enforcement

- import-linter contracts "Only paperless imports httpx2" and "No httpx" (`just imports`).
- ruff banned-api: `httpx` is banned with the message "This project uses httpx2 only." (`tool.ruff.lint.flake8-tidy-imports.banned-api`, `just lint`).
- Injectable transport exercised by the adapter tests: PLMCP-6.
