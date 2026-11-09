---
id: decision-7
title: No host filesystem or network access for remote callers
date: '2026-11-09T20:44:00Z'
status: accepted
---
## Context

When the server runs over HTTP for a remote caller, a tool that reads a server-side path or fetches a URL turns the caller into a confused deputy against the host and its network (arbitrary file read, SSRF). Local stdio use legitimately uploads files from the user's own machine.

## Decision

- Server-side file paths are accepted only in stdio mode and only inside configured allowlisted directories.
- HTTP mode accepts base64 uploads up to a size limit.
- No tool fetches arbitrary URLs; the only outbound calls go to the configured Paperless.
- Downloads are delivered as MCP image/embedded-resource content with a size cap, never written to the host; `next`/`previous` URLs are reduced to page numbers (no host leakage).

## Consequences

- Remote callers cannot upload by path; they send content.
- Any future feature that wants a path or URL parameter needs a new decision superseding this one.

## Enforcement

- Registry test (`tests/tools/test_registry.py`, from PLMCP-8, with a self-test over a synthetic offending tool): no tool exposes a filesystem path parameter (schema format `path`, `file-path` or `directory-path`, i.e. typed as `pathlib.Path`) outside the stdio-only allowlist. The first upload tool, PLMCP-18, adds its allowlist entry.
- Adapter design: the adapter's only network destination is the configured base URL (PLMCP-6).
- Review point: the `raw` toolset (PLMCP-32) is read-only and restricted to the Paperless API path space; its tests cover path escapes.
