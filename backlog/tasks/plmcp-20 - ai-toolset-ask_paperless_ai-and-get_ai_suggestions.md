---
id: PLMCP-20
title: 'ai toolset: ask_paperless_ai and get_ai_suggestions'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T20:42:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-15
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 20000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Paperless 3.x ships its own AI (RAG chat over documents and AI suggestions), so this server proxies it instead of building its own vector search (design non-goals, §6, §9.2).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 ask_paperless_ai(question, document_ids optional) uses the Paperless chat endpoint and returns the complete answer
- [ ] #2 get_ai_suggestions returns the AI suggestions for one document
- [ ] #3 The toolset is hidden when Paperless AI is disabled
- [ ] #4 Adapter tests are schema-guarded; just e2e-ai runs against an AI backend the developer provides via environment; CI needs no AI backend
- [ ] #5 FakePaperless and the contract suite cover every new port method
- [ ] #6 ai is in the default PAPERLESS_MCP_TOOLSETS (settings.py) and in TOOLSETS, TOOL_TOOLSETS and TOOLSET_REQUIRES (tools/_shared.py); the settings default test and the default-profile snapshot and budget test cover it
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
1. Live facts (E2E stack, AI off, 3.4.2 source read in the image): chat POST /api/documents/chat/ takes q + ONE optional document_id (no id list), answers text/event-stream plain text chunks, ends with a '\n\n__PAPERLESS_CHAT_METADATA__{json references}' trailer, in-band error/no-content sentences; AI off => 400 text/html 'AI is required for this feature' (checked before body validation); ai_suggestions GET needs change_document (403) before the AI check (400).
2. Domain: new domain/ai.py (ChatAnswer, ChatSource, AiSuggestions, tool-facing AiAnswer/AiSuggestionReport); port AiReader {ask, get_ai_suggestions} in domain/ports.py (chat does not change Paperless, so a Reader).
3. Adapter (paperless/ai.py): schema-guarded ai_suggestions tests; chat tests on a streaming MockTransport (schema guard reads whole bodies) with request-body validation against ChatStreamingRequest; client gets a bounded streaming POST (byte cap + total-seconds cap, transport errors mapped like download, never retried); AI-off 400 => FeatureUnavailable; in-band failure sentence => UpstreamUnavailable; trailer parsed into sources.
4. Fake + contract: FakePaperless implements AiReader (ai_enabled gating, change_document, canned answers through state), contract tests on fake and live (live = AI off behaviour in CI; AI-on contract only in e2e-ai).
5. Service AiService (titles/names resolution for suggestions via TaxonomyResolver known-kinds, web_url on sources), tools ask_paperless_ai(question, document_id) and get_ai_suggestions(document_id) in tools/ai.py, toolset ai: TOOLSETS, TOOL_TOOLSETS, TOOLSET_REQUIRES, ANNOTATIONS, settings default ('core','ai'), harness + Ports wiring, snapshots deliberately regenerated, budget test.
6. Decision to record: get_ai_suggestions (LLM, costs, sends content to the LLM backend) stays separate from get_document include=suggestions (classifier); descriptions cross-link.
7. just e2e-ai recipe + tests/e2e/test_ai.py gated by env (PAPERLESS_E2E_AI_ENV...), documented; just check, e2e, mutation.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live facts (E2E stack 3.4.2 with AI off, port 8010; plus the 3.4.2 source read inside the image). (1) Chat: POST /api/documents/chat/ takes q (1..4000) and ONE optional document_id; there is no id list, so ask_paperless_ai takes document_id (single) instead of the AC's document_ids: the endpoint cannot restrict to a subset. (2) AI off: chat and ai_suggestions answer 400 text/html 'AI is required for this feature'; for chat this check comes before body validation, for ai_suggestions after 404 and the change_document check (403). Mapped to FeatureUnavailable in paperless/errors.py (shared by every AI endpoint). (3) With AI off, GET /documents/{id}/suggestions/ (classifier) still answers 200 with the five id lists: it stays in get_document include=suggestions. (4) The chat streams text/event-stream as plain text chunks; if sources were found the body ends with '\n\n__PAPERLESS_CHAT_METADATA__{"references":[{id,title}...]}' (max 3). Failures while generating are an in-band 200 with 'Sorry, something went wrong while generating a response.'; 'no content' is the sentence 'Sorry, I couldn't find any content to answer your question.' (a normal answer here). (5) ai_suggestions body: title, correspondents/tags/document_types/storage_paths (ids), suggested_* (new names), dates; errors in 3.4.2: 400 JSON {ai:[Invalid AI configuration.]}, 503 AI backend timed out, 502 AI backend rejected the request. (6) /api/tasks/run/ accepts task_type llm_index to build the RAG index.

Design choices. (a) get_ai_suggestions vs get_document include=suggestions: kept separate on purpose. include=suggestions is the classifier (ids only, instant, free, no LLM, works with AI off); get_ai_suggestions asks the LLM (title, existing objects as ids WITH names, new names in new_*, dates; slow, costs, sends text to the backend, needs change_document). Tool descriptions point at each other; no duplicated surface. (b) Annotations: both ai tools read_only_hint=True (Paperless state unchanged, so read-only mode keeps them and AiReader is a Reader), idempotent_hint=False (an LLM answers differently each time), open_world_hint=True (Paperless forwards document text to an external LLM backend); this is the first open_world tool in the table. (c) Chat transport: PaperlessClient.stream_post, a POST that is never retried (a retry is a second model call), bounded by CHAT_MAX_BYTES=64 KiB and CHAT_MAX_SECONDS=120 s in total (wait for first byte included); past a limit the bytes that arrived come back truncated=true, time out before the first byte is UpstreamTimeout; transport errors mid-stream map like download (logged by type only). The question and answer are never logged. (d) client.request/call/get got an optional per-request timeout; ai_suggestions uses 120 s because a model call exceeds the 30 s default; being a GET it is still retried on 502/503/504 by the existing policy (up to 3 model calls; accepted, noted as a risk). (e) paperless/errors.py: 400 'AI is required for this feature' => FeatureUnavailable for every AI endpoint (ai_disabled() is shared with the fake). (f) The 'no content' sentence is an ordinary answer; the generation-failure sentence becomes UpstreamUnavailable. (g) Budget: default profile is now core 8 + ai 2 = 10 of 20. (h) New marker ai_e2e; live AI-on contract fixture ai_backend skips when the stack has AI off, so plain just e2e shows 6 clean skips; just e2e-ai (own compose project paperless-mcp-e2e-ai, port 8012, overlay tests/e2e/compose.ai.yaml, variables PAPERLESS_E2E_AI_* documented there and in the justfile) exits 0 with a notice when PAPERLESS_E2E_AI_LLM_BACKEND/MODEL are unset.

Deliberate gate/snapshot changes: (1) tests/tools/__snapshots__/test_surface.ambr regenerated with --snapshot-update: only additions, the two ai tools in the default, all and read-only profiles (the surface test serves with AI on). (2) mutation-equivalents.toml: five entries re-reviewed (same reasons, new mutant ids and function hashes because client.request/get/_build gained the timeout parameter: request 16->18, _build 19/20->22/23, get 10->12) and one added (stream_post__mutmut_11, 'post' vs 'POST', httpx2 upper-cases); threshold, formula and rules untouched. (3) tests/adapter/schema_guard.py: three WRONG_RESPONSE_SCHEMAS entries for POST /api/documents/chat/ (200 is text/event-stream not JSON; 400 and 403 are text/html, not documented), each with a reason. (4) pyproject.toml: new marker ai_e2e. (5) test_visibility.py: CORE is now the core-toolset tools only (the shipped ai tools are asserted in test_ai.py), and the settings default test now expects ('core','ai'). Live AI-on facts (second stack, project paperless-mcp-e2e-ai on port 8012 with the overlay and an UNREACHABLE openai-like backend, removed afterwards): ui_settings ai_enabled true; chat with no documents -> 200 text/event-stream 'Sorry, I couldn't find any content...'; chat with an unknown document_id -> 400 text/html 'Document not found' (mapped to NotFound); too long q -> 400 JSON {q:[...]}; with documents but dead embeddings the chat blocked ~96 s (the OpenAI client retried) and then answered 200 with the in-band error sentence, which the server maps to the ToolError 'Paperless AI could not generate a response' (seen through the assembled server); ai_suggestions with a dead LLM -> 500 text/html after ~12 s (mapped to UpstreamUnavailable HTTP 500, not retried); the 2 AI-on contract tests for missing/hidden documents passed live (NotFound, PermissionDenied). NOT verified anywhere: a successful answer from a working LLM (stream with sources trailer, real suggestions, LLM index build via POST /api/tasks/run/ task_type llm_index, which only the owner can check with a real backend via just e2e-ai). Results: just check green (100% diff coverage), just e2e 102 passed 6 skipped (the AI-on ones), just mutation 100.0% (2324 killed, 4 timeout, 0 survived, 42 reviewed equivalent). Open risks: GET ai_suggestions may be retried on 502/503/504 (up to 3 model calls); chat/ai_suggestions timeouts are fixed constants (120 s) not settings; the trailer delimiter could be cut when an answer is truncated at 64 KiB (sources then empty).
<!-- SECTION:NOTES:END -->
