---
id: PLMCP-20
title: 'ai toolset: ask_paperless_ai and get_ai_suggestions'
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
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
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
