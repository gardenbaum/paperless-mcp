---
id: PLMCP-17
title: Notes, trash deletion and the confirmation flow
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T23:54:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-15
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Destructive actions need an explicit confirmation that works on every MCP protocol era; elicitation is unavailable on the sessionless 2026-07-28 era (design §5.5).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 add_note, delete_note and delete_document exist
- [x] #2 A destructive tool called without confirm=true changes nothing and returns what would happen and how to confirm (verified with the write guard)
- [x] #3 delete_document moves the document to the trash and says so
- [x] #4 The registry test enforces confirm on every destructive tool
- [x] #5 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Shared confirmation mechanism: domain/errors.py ConfirmationRequired(Validation) (+ for_effect(effect) standard text, default hint 'call again with confirm=true'); a typed ToolError (decision-12 owner change reuses it, its message unchanged). Chosen over a non-error preview result: consistent with decision-12, an error cannot be mistaken for success, and the client-safe text carries what would happen.
2. Ports (DocumentWriter): add_note(document_id, note) -> Note, delete_note(document_id, note_id) -> None, delete_document(document_id) -> None (to the trash). Adapter POST/DELETE ?id= on /api/documents/{id}/notes/, DELETE /api/documents/{id}/ with retry disabled (a retried DELETE answers 404 after success). Live-probe note/trash behaviour first (response shapes, ordering, permissions, unknown id).
3. FakePaperless: add_note/delete_note/delete_document with @write, live-probed permission order, trash is invisible to reads; contract tests on fake+live (own documents, notes cleaned up, trash emptied for created ids).
4. Services: NoteService/DocumentRemoval in services/ (read first to name the target, ConfirmationRequired when not confirmed, nothing written; caches unaffected except taxonomy document_count invalidation on delete).
5. Tools add_note, delete_note, delete_document in tools/core.py (annotations: add_note write non-destructive; delete_* destructive, confirm: bool = False), ANNOTATIONS/TOOL_TOOLSETS, Services wiring, deliberate surface snapshot update; default profile tool count stays under budget 20.
6. Registry: destructive tools need confirm (exists) - add self-test coverage that the real server has destructive tools (non-vacuous) and the synthetic violator; proof 'changes nothing' via FakePaperless allow_writes=False at service and tool level.
7. E2E through the assembled server on 3.4.2: notes add/list/delete, unconfirmed deletes change nothing, confirmed delete lands in the trash and is purged afterwards.
8. just check, just e2e (flock), just mutation (decision-13).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live-verified on 3.4.2 (probes on own documents and temporary users, all deleted again, trash entries emptied): POST /api/documents/{id}/notes/ answers 200 with ALL notes of the document, oldest first, the new one last (highest id; ids are global and never reused); the author is {id, username, first_name, last_name}. Paperless accepts an empty and a whitespace-only note (200) and a 5000-character note, so the service rejects blank text itself and caps a note at 10 000 characters. A POST without the note key answers 200 {"error": "Error saving note, check logs for more detail."} (not a list): the adapter turns it into Validation. DELETE ?id= answers 200 with the remaining notes; a missing id is 400, an unknown id or a note of another document is 404 'No Note matches', a missing document is 404; a trashed document's notes are 404. Permissions: GET/POST/DELETE need the global view_document AND view_note/add_note/delete_note (a user with only add_note is 403 on POST); POST and DELETE additionally need the OBJECT change right: a view-only user reads notes but gets 403 'Insufficient permissions to create/delete notes', a hidden document is 403 for every note action, own/unowned/change-granted documents are fine. DELETE /api/documents/{id}/ is a soft delete to the trash (204), the document is then 404 on read and delete, appears in GET /api/trash/ and comes back with POST /api/trash/ {action: restore}; it needs only the global delete_document (NOT view_document), a hidden document is 404, and only the owner, any user for an unowned document, or a superuser may delete: a view-only or change-granted user gets 403. Trash entries stay until emptied, and earlier test runs left ~540 behind on the shared stack (not touched).

Decisions: (1) The confirmation mechanism is ONE shared class, domain/errors.py ConfirmationRequired(Validation) with ConfirmationRequired.for_effect(effect) ('Nothing was changed. Confirming would <effect>.') and the single hint CONFIRM_HINT, plus quoted(text, limit) to name a document or note on one line without its content. It is a ToolError (not a typed non-error result): consistent with decision-12's owner change, which now raises it too (message unchanged), and an error cannot be mistaken for a success. I read decision-6's 'return what would happen' as satisfied by the error text; no decision change. PLMCP-19 (and PLMCP-22/27) reuse: read the target, then 'if not confirm: raise ConfirmationRequired.for_effect(...)'. Messages carry titles and a 40-character note excerpt only, never full content; errors are not logged at INFO. (2) Services: services/notes.py NoteService (add, delete) and services/removal.py DocumentRemoval (delete, invalidates all counted taxonomy kinds). A note deletion reads document and notes first (needs view_note) so the preview can name the note and an unknown note is reported before the confirmation. (3) DELETE requests are sent with retry disabled in the adapter: a retried DELETE would answer 404 after the first succeeded. (4) Annotations: add_note write, non-destructive, not idempotent; delete_note and delete_document destructive, idempotent. delete_document's message points to the web UI trash and the trash toolset (PLMCP-27 should name restore_from_trash in services/removal.py RESTORE once it exists). (5) Default profile now has 14 tools (budget 20); PLMCP-19 adds 3. (6) Registry: destructive() now also counts destructive_hint=True together with read_only_hint=True; self-tests for a read-only-and-destructive tool and a required confirm; a non-vacuity test that the real server has delete_note and delete_document as destructive tools.

Deliberate snapshot/gate changes: tests/tools/__snapshots__/test_surface.ambr regenerated (pure additions: three tools; no existing line changed); test_visibility read-only expectation hides the three new tools; Backend (tests/contract/conftest.py) gained in_trash and the live finalizer now empties the trash entries of documents it deleted. No gate, threshold or decision weakened. Known fake gaps: no audit-log entries for note changes, no document-link cleanup when a linked document is trashed.

TDD evidence, honestly: red first (failing for the intended reason): domain ConfirmationRequired/quoted (import error), adapter tests (AttributeError: no add_note/delete_note/delete_document), contract tests on the fake (AttributeError, then the fake bug max() with no notes found by the fake test), service tests (import error, then 1 wrong regex), tool tests (unknown tool; one  deprecation fixed), registry self-tests (verified red against the old destructive() helper). Not run red: the live contract tests and the E2E file (written after the adapter and services, green on first run) and the mutation-driven tests added afterwards.

Shared confirmation API for PLMCP-19 and later tasks (domain/errors.py, no new decision written; decision-6 and decision-12 cover it): raise ConfirmationRequired.for_effect("<verb phrase naming what is touched, never content>") when confirm is false, AFTER reading the target and BEFORE any write. It is a Validation subclass, message "Nothing was changed. Confirming would <effect>.", hint CONFIRM_HINT ("Call the tool again with the same arguments and confirm=true."); ConfirmationRequired("own message", hint="own hint") for custom text (decision-12 owner change does this). quoted(text, limit=60) renders a title/name on one line, cut with an ellipsis. The boundary turns it into a ToolError via describe(). Tool side: parameter "confirm: Confirm = False" (keyword-only, type alias in tools/core.py) and destructive_hint=True in ANNOTATIONS; the registry test checks both. PLMCP-19 currently returns a typed TaxonomyDeletion preview and uses Validation for option removal: to unify, either raise ConfirmationRequired.for_effect with the affected counts/child tags in the effect text (preferred, same shape as delete_note/delete_document), or keep the typed preview and say so in its decision-14. Prove nothing changes with FakePaperless(allow_writes=False) (WriteNotAllowedError would be masked as an internal error, so assert the refusal text).

Review fix round 1. Decision-15 "Confirmation refusals are tool errors" records the ruling (supplements decision-6 and decision-12; PLMCP-19, 22, 27 adopt ConfirmationRequired; bulk dry_run stays a typed result). Changes: CONFIRM_HINT and the Confirm parameter text (now tools/_shared.py) tell the model to describe the effect to the user and pass confirm=true only once they agree; wording pinned in tests/domain/test_errors.py and tests/tools/test_notes_and_deletion.py. New helpers in domain/errors.py: listed(names, limit=5) renders "a, b and N more", quoted() picks double quotes or swaps apostrophes. ConfirmationRequired carries text only. The boundary raises ToolError(log_level=INFO) for a refusal (FastMCP logs a FastMCPError at its log_level; ERROR is the default). Limitation: when an operator enables OpenTelemetry, FastMCP puts the ToolError message in the span status, so titles and the 40-character note excerpt would reach the trace backend; this server enables no telemetry, and the limitation is in decision-15. FastMCP 4 drops string minLength/maxLength from the published schema and does not enforce them, so add_note keeps the Field bounds for documentation and the service enforces 1..10000. add_note now picks the newest note with the sent text (fallback: newest note). Live-verified with a temporary user (contract test, cleaned up): adding or deleting a note ALSO needs the global change_document permission, a change grant on the document alone gives 403 (the fake now requires it); view grant reads notes but cannot add or delete; neither a view nor a change grant lets a user delete a document (403), a hidden document is 404. Contract Backend has restricted(permissions, view=, change=) for such users; the live one gets its token from POST /api/profile/generate_auth_token/ with basic auth, avoiding the throttled /api/token/. The fake aligns the 404 of delete_note on a missing document with the adapter. Trash cleanup now raises on failure. client.request(retry=False) docstring explains why deletions are not retried; a 429 is not retried for DELETE either (kept simple, noted). RESTORE text names restore_from_trash and the 30-day default retention; note added to PLMCP-27. For the PR description (M9): confirmation is an error by decision-15; snapshot test_surface.ambr changed again (confirm and delete_document descriptions); a trashed document keeps its notes; live notes semantics above.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
add_note (non-destructive write), delete_note and delete_document (destructive, keyword-only confirm: bool = False). An unconfirmed destructive call changes nothing (proven with the FakePaperless write guard) and is refused through one shared mechanism, ConfirmationRequired: a client-safe ToolError describing the effect (titles and short excerpts only) with a hint to describe it to the user and confirm only once they agree; logged at INFO. New decision-15 records this interpretation of decision-6 (supplements 6 and 12; bulk dry_run previews stay typed); update_document's owner refusal uses it too. delete_document moves the document to the trash and says how to restore it (restore_from_trash, 30-day default retention). Registry rule: every destructive tool takes confirm, with self-tests. DELETE requests are not retried. Ports add_note, delete_note, delete_document with adapter, FakePaperless and contract tests on fake and live 3.4.2, including object-level permission rules via temporary users (notes need change_document globally plus change rights on the document; deleting needs ownership). Verified: just check 1156 passed; full just e2e 171 passed, 6 skipped; just mutation 100 %. Independent review: 2 Important and 10 Minor fixed. Deliberate: surface snapshot (three tools, confirm wording), read-only expectations, five mutation equivalents. DoD #1 left unchecked: live contract and e2e tests were written after the code. DoD #2 and #4 follow from CI and the merge.
<!-- SECTION:FINAL_SUMMARY:END -->
