---
id: decision-6
title: Write safety
date: '2026-11-09T20:44:00Z'
status: accepted
---
## Context

An LLM with write tools can destroy data. Paperless PATCH replaces whole lists (custom fields), so a naive update silently deletes values. Elicitation cannot confirm destructive calls (it is unavailable in the sessionless MCP era targeted here). Some clients and operators want a server that cannot write at all.

## Decision

- Updates compute a diff against the current state; tags and custom fields support add/remove/set with merge semantics, and the service always sends the merged list.
- Destructive tools take `confirm: bool = False` and carry `destructive_hint`; without `confirm` they return what would happen and how to confirm. Elicitation is not used.
- Deletes go to the trash where Paperless supports it; `empty_trash` never defaults to "everything".
- Bulk operations offer `dry_run` (affected count and a sample) before execution.
- Read-only mode (`PAPERLESS_MCP_READ_ONLY`) hides every tool without `read_only_hint`. Size limits apply to all inbound payloads; secrets are redacted; mail passwords are never echoed.

## Consequences

- Destructive flows take two calls by design.
- A read-only tool that writes is a bug caught by its own tests: `FakePaperless` rejects writes unless a test opts in.
- Server-side read-only is independent of Paperless permissions; both apply.

## Enforcement

- Registry test: every destructive tool has a `confirm` parameter defaulting to false; read-only mode leaves only `read_only_hint` tools. PLMCP-15 (read-only mode) and PLMCP-17 (confirmation flow).
- Service tests for merge semantics and dry-run: PLMCP-16 (update_document with merge) and PLMCP-22 (bulk).
- Write guard in `FakePaperless` (rejects writes unless a test opts in): PLMCP-7.
- Contract snapshot of annotations (decision 4).
