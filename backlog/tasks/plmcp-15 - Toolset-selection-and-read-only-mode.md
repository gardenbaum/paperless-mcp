---
id: PLMCP-15
title: Toolset selection and read-only mode
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T18:45:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-8
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The user wants to be able to do everything, but the default tool list must stay small so the model chooses well. Only core (plus ai when available) is on by default; everything else is opt-in (design §5.8).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 PAPERLESS_MCP_TOOLSETS (list or all, default core), PAPERLESS_MCP_READ_ONLY and PAPERLESS_MCP_DISABLED_TOOLS control which tools are visible
- [x] #2 Read-only mode leaves only tools with read_only_hint (registry test)
- [x] #3 Startup fails with a clear message when the filters leave zero tools
- [x] #4 Toolsets whose Paperless capability is missing (e.g. AI disabled) are hidden automatically
- [x] #5 The contract snapshot covers the profiles default, all and read-only
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Settings: ServerSettings gains toolsets (PAPERLESS_MCP_TOOLSETS, comma list or 'all', default core), read_only (PAPERLESS_MCP_READ_ONLY), disabled_tools (PAPERLESS_MCP_DISABLED_TOOLS, comma list). Tests first.
2. tools/_shared.py: DEFAULT_TOOLSETS and TOOLSET_CAPABILITY tables (toolset -> required Paperless capability, ai ready for PLMCP-20). New tools/selection.py: pure functions to resolve the visible toolsets (unknown names -> InvalidConfiguration at create_server), read-only hidden tool names from ANNOTATIONS, unknown disabled tool names -> error, capability-missing toolsets from the probe overview. Synthetic-table tests.
3. server.py: apply with FastMCP enable(tags, only=True)/disable(names)/disable(tags); lifespan hides toolsets whose capability the probe found missing (ai_enabled is False; unknown/unreachable keeps them) then fails startup if list_tools is empty.
4. Registry test: read-only leaves only read_only_hint tools, with self-test using a synthetic violating provider; budget test on default profile.
5. Surface snapshots for profiles default, all, read-only (parametrized).
6. E2E over stdio: env vars select tools; zero tools exits 1 with message.
7. Append notes with decisions; just check, flock just e2e; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Design: settings ServerSettings.toolsets/read_only/disabled_tools (comma list or JSON array, 'all' stands alone, empty list rejected). Pure selection in tools/selection.py; composition root applies it with FastMCP visibility: enable(tags=selected, only=True) then disable(names=read-only/disabled tools, components={'tool'}) in create_server; the lifespan, after the startup probe, disable(tags=unavailable toolsets) and then fails startup (InvalidConfiguration -> exit 1 via cli) when list_tools() is empty. Catalogue dataclass (providers, annotations, toolsets, requires) in server.py is the seam tests use to add synthetic toolsets (ai, bulk); TOOLSET_REQUIRES in tools/_shared.py is empty until PLMCP-20 adds 'ai': 'ai'. Read-only mode is computed from the central ANNOTATIONS table (registry test guarantees table == tools).

Decisions: (1) Unknown toolset in PAPERLESS_MCP_TOOLSETS or unknown tool in PAPERLESS_MCP_DISABLED_TOOLS is a startup error (InvalidConfiguration) naming the offender and the known names, raised by create_server. (2) Capability detection happens once at startup from the probe overview: only ai_enabled == False hides a toolset; null (user may not view UI settings) and an unreachable Paperless at startup leave it visible so a transient outage does not hide it for the process lifetime; its tools report errors themselves. An explicitly selected toolset hidden by a missing capability logs a WARNING. (3) Zero tools after all filters/capabilities fails startup with one message naming the three variables. (4) Default is core only (AC #1); design says core+ai: PLMCP-20 must add 'ai' to the toolsets default in settings.py, to TOOLSETS and to TOOLSET_REQUIRES. (5) Resources carry the toolset tag, so an unselected toolset also hides its resources.

Deliberate snapshot change: tests/tools/__snapshots__/test_surface.ambr regenerated because the surface tests are now parametrized by profile (default, all, read-only); old unparametrised entries replaced by 6 new ones. Content is unchanged: all three profiles are identical today (core is the only toolset and all its tools are read-only). No gate or decision weakened.

Review round 1: tests take the default profile from ServerSettings (toolsets=None in the harness) and run budget, registry and profile snapshots on a fake with ai_enabled=True. Capability hiding is now one Visibility rule registered in create_server whose tags each lifespan entry replaces (no accumulation; a server follows AI on/off across starts). Filters that alone leave no tool fail in create_server before any request, via the new TOOL_TOOLSETS table (registry-tested against tags); the lifespan keeps the capability case. Registry tags rule covers resources, templates and prompts. ALL_TOOLSETS lives in domain/toolsets.py (tools may not import settings). Malformed JSON and unknown names give clear messages with suggestions. Deliberate snapshot change: 3 new entries (resources and prompts per profile), nothing else changed. test_seeding_is_idempotent no longer asserts instance-wide document counts.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Toolset selection: PAPERLESS_MCP_TOOLSETS (comma list or JSON array, or all; default core), PAPERLESS_MCP_READ_ONLY and PAPERLESS_MCP_DISABLED_TOOLS decide the visible surface through FastMCP's enable/disable visibility in the composition root. Read-only hides every tool whose central annotation lacks read_only_hint (non-vacuous self-tests with synthetic providers). Unknown names and filters that leave zero tools fail at startup with a clear message (suggestions via difflib) before any Paperless request; a toolset whose capability is known to be missing (ai_enabled False) is hidden by one replaceable rule per server after the startup probe (unknown or unreachable keeps it visible). Every tool, resource, template and prompt must carry exactly one toolset tag; new TOOL_TOOLSETS table checked against the tags. Surface snapshots for the profiles default, all and read-only (tools, templates, resources, prompts). Handoff to PLMCP-20 recorded as its acceptance criterion (ai joins the default). test_seeding_is_idempotent no longer asserts instance-wide counts. Verified: just check 517 passed, 100 %; just e2e 83 passed. Independent review: 2 Important and 6 Minor fixed. Deliberate: snapshot restructured per profile (content per tool unchanged) and extended. DoD #1 left unchecked: some registry tests and the CLI/stdio tests were written after the code and not run red. DoD #4 is checked by the merge itself.
<!-- SECTION:FINAL_SUMMARY:END -->
