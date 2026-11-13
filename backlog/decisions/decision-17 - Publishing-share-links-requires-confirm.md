---
id: decision-17
title: Publishing share links requires confirm
date: '2026-11-13T21:30:00Z'
status: accepted
---
## Context

The sharing toolset (PLMCP-26) can make documents readable by anyone: a share link or bundle is a public URL that needs no login, and whoever gets the URL can pass it on or save the file, so it cannot be taken back from them (deleting the link only ends future access). Decision-6 reserves `confirm` for destructive tools, and decision-12 already guards one non-destructive change for which the caller cannot undo the consequences, an owner change. A link looks harmless to a model that treats "create" as additive, yet it exposes the document outside Paperless. E-mail (`email_documents`) is the same kind of outward step and is guarded too.

## Decision

- `create_share_link` and `create_share_bundle` take `confirm: bool = False` and publish nothing without it. They stay annotated non-destructive (they take nothing away) and open-world (they publish outside Paperless); `rebuild_share_bundle` is open-world too, because the same URL then serves the documents' current files, but it needs no confirm: it publishes nothing that the bundle did not already publish.
- The refusal uses `ConfirmationRequired.for_effect` (decision-15) and names each document by quoted title and id, which file goes out (the archive file, the original where there is none, or the original), when the link expires or that it never expires, and that anyone with the URL can download it without logging in.
- Deleting a link or bundle and `email_documents` keep their own `confirm` (decision-6); the e-mail refusal names recipients, documents, subject and the start of the message.
- This supplements decision-6 and decision-12 and changes none of their rules.

## Consequences

- Sharing a document is two calls by design: the model must show the user what becomes public and get a yes.
- Creating a link no longer works for a client that never passes `confirm`; reading and deleting links are unaffected.
- Tool descriptions and the refusal text are part of the contract; the surface snapshot shows them.

## Enforcement

- The registry test (tests/tools/test_registry.py) requires an optional `confirm: bool = False` on `create_share_link` and `create_share_bundle`, with a self-test on a synthetic publishing tool without one.
- Service tests (tests/services/test_sharing.py, test_sharing_messages.py) refuse with `ConfirmationRequired` and publish nothing (the fake is created without `allow_writes`); tool tests (tests/tools/test_sharing.py) and E2E (tests/e2e/test_sharing.py) run the flow through the assembled server.
