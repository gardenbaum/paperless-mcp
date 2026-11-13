---
id: decision-16
title: Removed select options clear document values
date: '2026-11-13T08:19:00Z'
status: accepted
---
## Context

decision-14 (accepted, not edited) states in its context that removing a select option that documents hold "leaves the stale id on them; `update_document` cannot set it again". That was probed right after the change and is wrong in the long run. Paperless 3.4.2 clears the value asynchronously: a `post_save` on a select `CustomField` (`documents/signals/handlers.py`) enqueues the Celery task `process_cf_select_update`, which runs `custom_field.fields.exclude(value_select__in=<current option ids>).update(value_select=None)`. The documents that held a removed option therefore end up with the value `None` for that field once the worker has run the task. The PLMCP-19 live contract test passed only because it read the document before the worker did; it failed in CI on PR #28.

## Decision

- Removing a select option makes the documents that held it lose their value for that field; Paperless clears it shortly after the save, not within the request. This corrects the factual statement and the consequence named in decision-14's context; nothing else in decision-14 changes.
- Confirmation (`confirm=true`) for an option removal stays required, as decision-14 and decision-15 say. Only the effect the refusal names changes: the removed options and how many documents the caller can see that hold one, which lose their value for the field, cleared by Paperless shortly after.
- `update_document` still cannot set a removed option id (Paperless answers 400).
- FakePaperless nulls the values of documents holding a removed option when a select field's options change. It does so synchronously; Paperless does it in a Celery task.

## Consequences

- A caller that reads a document right after a confirmed removal can still see the old id for a moment; the value is `None` once the task has run. Tools make no promise about the moment.
- The counts in the refusal are unchanged (documents the caller may see); documents the caller cannot see lose their value too.
- Contract tests poll the live document for a bounded time (30 s, 0.5 s steps) instead of asserting the transient state.

## Enforcement

- Service test `test_removing_an_option_needs_confirm_and_counts_the_documents_that_hold_it` pins the exact refusal text; the E2E taxonomy test asserts the "that hold a removed option" wording.
- Contract test `test_the_documents_that_held_a_removed_option_end_up_without_a_value` (tests/contract/test_taxonomy_edits.py, fake and live 3.4.2) asserts the value ends up `None`, that a document holding a kept option keeps it, and that `update_document` cannot set the removed id.
