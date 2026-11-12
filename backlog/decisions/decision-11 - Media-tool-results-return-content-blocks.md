---
id: decision-11
title: Media tool results return content blocks
date: '2026-11-12T18:45:00Z'
status: accepted
---
## Context

Decision-4 requires every tool to return a typed model, which FastMCP sends as structured content with an output schema. MCP clients that support structured output may hand only `structuredContent` to the model and drop the content blocks. The thumbnail and download tools (PLMCP-12) deliver their payload as content blocks: an `ImageContent` or an `EmbeddedResource` with the file. With a structured result next to them, such a client would show the model a report and lose the image or file.

## Decision

Tools whose result is a media payload return content blocks only: a short text block (the JSON report: file name, MIME type, size, `delivered`, `web_url`, `note`) plus the image or embedded resource. They declare no output schema and send no structured content. They are listed in `MEDIA_TOOLS` in `tools/_shared.py`; today `get_document_thumbnail` and `download_document`.

This narrows decision-4 ("a typed return model") for those tools only. Every other tool keeps a typed return model with an output schema. Adding a tool to `MEDIA_TOOLS` is a deliberate surface decision that needs a reason in the task notes.

## Consequences

- Clients that prefer structured output still receive the image or file as content.
- The report is machine-readable JSON in a text block but has no schema on the wire; its shape is the `FileReport` model.
- The tools list is smaller: no output schema for the two media tools.

## Enforcement

Registry test (`tests/tools/test_registry.py`): every tool without an output schema is in `MEDIA_TOOLS`, and every tool in `MEDIA_TOOLS` declares none, so the allowlist cannot grow silently or go stale. Self-tests over a synthetic provider prove both directions: a non-media tool without a schema and a media tool with one are found. The tool tests assert that the media tools send content blocks and no structured content.
