---
id: decision-12
title: update_document classification and owner changes
date: '2026-11-12T19:37:00Z'
status: accepted
---
## Context

`update_document` (PLMCP-16) changes a document's classification (title, dates, correspondent, type, storage path, tags, custom fields) and its owner. Decision-6 keeps updates non-destructive: the editor merges lists, sends only what differs, and what it overwrites stays in the audit log. An owner change is different. The new owner (or, with no owner, every user who may view documents) takes the document over, and the caller can lose the right to see or change it again, so the caller cannot undo it themselves.

## Decision

- Classification changes (everything except the owner) need no confirmation. `update_document` stays annotated `destructive_hint: false`, `idempotent_hint: true`.
- Any change of the owner, to another user or to no owner, requires `confirm=true`. Without it the call fails before anything is written or created, with a client-safe error that explains the effect and names `confirm=true`. An owner that already is as requested needs no confirm (updates are diffs, so a repeated call is a no-op).
- This supplements decision-6; it changes none of its rules. Destructive tools keep their `confirm` parameter; this tool carries one for the owner change only and keeps its annotations.

## Consequences

- Handing a document over takes two calls, by design.
- Clearing the owner publishes the document to every user with the document permission, so it needs the same confirmation.
- A client that never passes `confirm` can still file and tag documents but cannot change ownership.

## Enforcement

- Service tests (tests/services/test_document_edits.py): an owner change and an owner clear fail without `confirm`, nothing is written, no tag is created before the check, and an owner that already matches needs none.
- Tool tests (tests/tools/test_update_document.py) and the E2E suite (tests/e2e/test_update_document.py) run it through the assembled server; the registry tests keep the annotations in the central table.
