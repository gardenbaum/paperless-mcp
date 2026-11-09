---
id: PLMCP-1
title: Scaffold project and local quality gates
status: To Do
assignee: []
created_date: '2026-11-09T19:24:00Z'
labels: []
milestone: m-0
dependencies: []
references:
  - /Users/fabian/Documents/Git/dynasoft/Dynasoft-AI/tosca.mcp
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: chore
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Every later task needs one local gate that mirrors CI, and the layer boundaries of the design must be machine-checked from the first commit because AI agents write the code. The base setup comes from tosca.mcp (uv, justfile, gates), trimmed of everything tosca-specific (design §8: knowledge/dense/graph components, ONNX/numpy, Ladybug, telemetry, Entra, OpenSpec, Gitea workflows).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 uv project with uv_build and requires-python >=3.14; runtime dependencies fastmcp 4.1.x, pydantic, pydantic-settings and httpx2; httpx is not a declared dependency
- [ ] #2 Package src/paperless_mcp has the packages domain, services, paperless and tools plus server, settings and entrypoint modules from design §5.1
- [ ] #3 import-linter contracts encode every import rule of design §5.1 (layers, forbidden imports, toolset independence, httpx2 only in paperless, no httpx) and pass
- [ ] #4 ruff (select ALL with a documented ignore list, Google docstring convention), basedpyright strict on src and tests, and deptry pass
- [ ] #5 Suppressions (noqa, type: ignore, pragma: no cover) without a specific code and a reason comment fail lint; unused suppressions fail
- [ ] #6 unittest.mock is banned in src and tests via ruff banned-api; the only test seams are constructor injection and the HTTP transport
- [ ] #7 just check runs lint, format check, type check, import contracts, deptry and the tests with branch coverage >= 95 % overall and 100 % on changed lines (diff-cover)
- [ ] #8 pre-commit (including gitleaks) runs the same checks; MIT LICENSE, .gitignore, .editorconfig and a minimal README exist
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
