---
id: decision-1
title: Layered architecture (ports and adapters)
date: '2026-11-09T20:44:00Z'
status: accepted
---
## Context

The code is written largely by AI agents, which drift toward shortcuts: a tool calling the HTTP client directly, a service importing FastMCP, configuration read from globals. Design 4 compared three approaches: (A) a new layered build with hand-written models, (B) the same with a client generated from the OpenAPI schema, (C) a fork of pvliesdonk/paperless-mcp. B was rejected (more than 10k unreviewed lines, inherits schema bugs, noisy under strict typing, upper layers stay manual anyway); C was rejected (bound to the author's framework and template, reaching the target architecture touches nearly every file). The vendored OpenAPI schema verifies requests and responses but generates no code.

## Decision

Ports and adapters (design 5.1): entrypoints (`__main__`, `app`, `cli`) > `server` (the composition root, the only module importing both `tools` and `paperless`) > `settings` > `tools` | `paperless` > `services` > `domain`.

- `domain` imports nothing from the package and no I/O or MCP library; it holds models, errors and the ports (Protocols).
- `services` imports `domain` only. `paperless` implements ports and imports `domain` only. `tools` imports `services` and `domain`, never `paperless` or `httpx2`.
- Toolset modules in `tools/` are independent of each other; shared helpers live in `tools/_shared`.
- Configuration is injected; only the composition root reads settings.
- Wire models are hand-written and mirror only what the project uses.

## Consequences

- Services and tools are testable against an in-memory fake; the adapter is testable against a mock transport.
- More files and mapping code than a flat design; accepted for reviewability.
- Every new module must be placed in a layer (the layers contract is exhaustive) and added to the source lists of the forbidden-import contracts that apply to it.
- Replacing the HTTP library, the MCP framework or the identity source touches one layer.

## Enforcement

import-linter contracts in `pyproject.toml`, run by `just imports` inside `just check` and in CI (`check (3.14)`, `check (3.15)`):

1. "Layers: entrypoints > server > observability > settings > tools | paperless > services > domain" (layers, exhaustive; `observability` added in PLMCP-8: logging setup and the tool-call logging middleware)
2. "The package root imports nothing from the package and no I/O or MCP library"
3. "The adapter does not use services"
4. "Only the composition root imports tools and paperless"
5. "Only paperless imports httpx2"
6. "Only tools, observability, server and entrypoints import fastmcp and mcp" (observability holds FastMCP middleware)
7. "No pydantic-settings below the composition root (configuration is injected)"
8. "No httpx"

Toolset independence needs two toolset modules; the task that creates the second one adds an `independence` contract (see the note at the end of the contracts in `pyproject.toml`).
