---
id: decision-4
title: Tool design rules
date: '2026-11-09T20:44:00Z'
status: accepted
---
## Context

The tool surface is what the LLM sees; it is the product. FastMCP's docs state that curated servers perform significantly better than auto-mirrored ones, so `FastMCP.from_openapi` was dropped. Tool lists cost tokens on every turn and clients differ in what they accept. The three reference servers (pvliesdonk, baruchiro, OrellBuehler) contributed good ideas, adopted explicitly: field projection, content slicing, central annotation table, names in output, a registry test over all tools.

## Decision

Rules for every tool (design 5.5, 7):

- `snake_case` verb-first name, a title, an LLM-oriented description, typed and described parameters.
- `ToolAnnotations` from one central table; exactly one toolset tag; a typed return model (structured output).
- Tools are grouped into toolsets; the default profile is `core` and stays within a tool budget; read-only mode hides every tool without `read_only_hint`.
- Token economy: default page size 25, max 100; content slice default 20 000 characters; aggregated fetches capped with `truncated`; list rows never contain OCR content.
- Errors become `ToolError` (client-safe message plus a hint naming the next tool) through one boundary; everything else is masked (`mask_error_details=True`). Every document-shaped output carries `web_url`.

## Consequences

- A new tool needs an entry in the annotation table and a toolset; a tool that skips them fails its first test.
- The surface is reviewed as a diff: any change shows up in the contract snapshot.
- Adding tools to the default profile is a deliberate budget decision.

## Enforcement

Registry tests over the assembled server (all toolsets enabled): every tool has a title, description, central-table annotations and exactly one toolset tag; schema-compatibility checks (object root, every parameter described); default-profile tool budget; read-only mode leaves only `read_only_hint` tools; every tool named in a prompt exists. Plus the syrupy contract snapshot of the full surface per profile. Added from PLMCP-8 (first tool) and PLMCP-15 (toolset selection and read-only mode), and extended by every toolset task. Snapshot updates must be explained in the PR and the task notes (CLAUDE.md rules for AI contributors).
