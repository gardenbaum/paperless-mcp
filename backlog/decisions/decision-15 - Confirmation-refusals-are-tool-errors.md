---
id: decision-15
title: Confirmation refusals are tool errors
date: '2026-11-12T23:02:00Z'
status: accepted
---
## Context

Decision-6 says a destructive tool called without `confirm` "returns what would happen and how to confirm"; elicitation is not available on every protocol era. PLMCP-16 (decision-12) already refused an unconfirmed owner change with an error. PLMCP-17 adds `delete_note` and `delete_document`, PLMCP-19, 22 and 27 add more `confirm` tools, so the form of the refusal must be one thing. A typed non-error preview could be taken for a success by a client or model that does not read the result; an error cannot.

## Decision

- The refusal of an unconfirmed destructive call is an `isError` tool result. Its text is a client-safe description of the effect (counts, names, short excerpts, never full content) followed by the confirm hint. Nothing has been changed when it is returned.
- It is one mechanism for every tool that has a `confirm` parameter: services raise `ConfirmationRequired` (`domain/errors.py`, a `Validation`) after reading the target and before any write. `ConfirmationRequired.for_effect(effect)` builds the standard message ("Nothing was changed. Confirming would ..."); `quoted` and `listed` render names, excerpts and capped lists the same way everywhere. The error carries text only, no structured payload.
- The hint tells the model to describe the effect to the user and to call again with `confirm=true` only once the user agrees, or when the user already asked for exactly this. The `confirm` parameter description says the same.
- The error boundary raises the `ToolError` for a refusal with log level INFO, because a refusal is the normal first step of the two-step flow, not a failure.
- Bulk `dry_run` previews (decision-6) stay separate typed results: they are requested explicitly and return data, not a refusal.
- This interprets decision-6 ("return what would happen") and supplements decision-6 and decision-12; it changes none of their rules.

## Consequences

- Destructive flows take two calls by design; a client that never passes `confirm` cannot destroy anything.
- The refusal text may contain a document title and the start of a note. Error messages are not logged by this server. If an operator enables OpenTelemetry, FastMCP records a tool error's message on the span as the error status, so titles and excerpts would reach the trace backend: keep excerpts short and treat traces as sensitive.
- Clients see an error for the first call. That is intended.

## Enforcement

- Domain tests (tests/domain/test_errors.py): the message, hint wording, `quoted` and `listed`.
- Service tests (tests/services/test_notes_and_removal.py, tests/services/test_document_edits.py) refuse with `ConfirmationRequired` and write nothing; the fakes are created without `allow_writes`, so any write fails the test.
- Tool tests (tests/tools/test_notes_and_deletion.py): the `confirm` description, the refusal as `isError`, and the boundary's log level.
- The registry test (tests/tools/test_registry.py, decision-6) keeps `confirm: bool = False` on every destructive tool, with self-tests; E2E (tests/e2e/test_notes_and_trash.py) runs the flow through the assembled server.
