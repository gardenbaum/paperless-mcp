---
id: PLMCP-34
title: HTTP transport secure by default
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-13T01:46:00Z'
labels: []
milestone: m-5
dependencies:
  - PLMCP-18
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 34000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Remote use starts with an HTTP endpoint that cannot be opened by accident: without auth configuration it must not start, except on loopback with an explicit flag (design §5.7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Streamable HTTP app (ASGI) refuses to start without auth configuration unless bound to loopback with allow_unauthenticated_loopback=true
- [ ] #2 Static bearer tokens protect the MCP endpoint; requests without a valid token get 401
- [ ] #3 Trusted-host allowlist and request-id middleware are active
- [ ] #4 In HTTP mode uploads accept base64 only and no tool exposes a filesystem path parameter (registry test per transport)
- [ ] #5 README documents running over HTTP
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
From PLMCP-18 review: the stdio-only path variant of upload_document (tools/upload_stdio.py) must never be registered for HTTP: create_server(stdio=...) is the only switch and the CLI passes transport="stdio" explicitly (FASTMCP_TRANSPORT is ignored); the tool also refuses a path when ctx.transport is sse/streamable-http. HTTP entrypoints must call create_server with stdio=False. HTTP also needs a request-body size cap in front of the app: base64 content is parsed by FastMCP/pydantic before UploadService checks its size (the service only bounds the decode, not the parse), so an unbounded body costs memory first.
<!-- SECTION:NOTES:END -->
