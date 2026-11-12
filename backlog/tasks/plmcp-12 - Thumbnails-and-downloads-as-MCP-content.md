---
id: PLMCP-12
title: Thumbnails and downloads as MCP content
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T18:38:00Z'
labels: []
milestone: m-1
dependencies:
  - PLMCP-11
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Vision-capable clients can read thumbnails, and originals or archive PDFs must be reachable without touching the host filesystem (design §5.5, §5.7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_document_thumbnail returns MCP image content
- [ ] #2 download_document (original or archive) returns an embedded resource up to a configurable size cap; above it, metadata and web_url only
- [ ] #3 paperless://documents/{id}/thumbnail, /original and /archive resources exist
- [ ] #4 MIME type and filename, including non-ASCII names, are preserved
- [ ] #5 FakePaperless and the contract suite cover every new port method
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
1. Domain: DocumentFile (bytes, mime_type, filename) and FileVersion on the DocumentReader port: get_document_thumbnail(id) and get_document_file(id, version); FileReport (structured result: document_id, kind, filename, mime_type, size_bytes, delivered, max_bytes, web_url, note) and FileDelivery(report, data).
2. Adapter (red first): PaperlessClient gets no new path; PaperlessDocumentReader fetches /thumb/ and /download/?original=; filename from Content-Disposition (RFC 5987 filename* preferred over the ASCII-folded filename=), Content-Type kept verbatim; NotFound->unseen_document. Schema guard: the schema declares these 200s as application/json (format binary) but Paperless sends the file with its own type, and 403 for a hidden document is text/html: one narrowly scoped BINARY_RESPONSES handling (checks status, non-JSON content type, Content-Disposition, non-empty body) plus WRONG_RESPONSE_SCHEMAS for the html 403s, with schema-guard self-tests.
3. FakePaperless: documents hold file bytes (original, optional archive, thumbnail), metadata sizes derived from them, hidden-document and missing semantics as live; contract tests for both new methods on fake and live (non-ASCII upload, text/plain upload without archive version, hidden/missing/blind), Backend gets an upload helper (live deletes after consumption).
4. Settings: PAPERLESS_MCP_MAX_DOWNLOAD_BYTES (default 2 MiB). Service: DocumentService.thumbnail and .file(id, version): metadata first (no archive version -> NotFound with hint; size above cap -> report without bytes and no download), then download, then re-check the cap on the real size.
5. Tools get_document_thumbnail (ImageContent + JSON report) and download_document (EmbeddedResource blob + report, or report only above the cap) as ToolResult with the report model as output schema; resources paperless://documents/{id}/thumbnail, /original, /archive via ResourceResult with per-document MIME and filename in meta; ANNOTATIONS entries; snapshots regenerated deliberately (surface + resource templates); tool budget 8/20.
6. E2E through the assembled server with a real non-ASCII upload and a no-archive text upload (deleted after consumption); README setting docs; just check and just e2e green.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live-verified on Paperless 3.4.2 (E2E stack, probe uploads deleted again): thumb/ answers image/webp with 'inline; filename="0000030.webp"'; download/?original=true|false answers the file with 'attachment; filename="<ASCII-folded>"; filename*=utf-8''<percent-encoded UTF-8>' - for a title/upload name with non-ASCII characters the plain filename= drops them ("probe-Uni  .pdf") and only filename* has the real name, so the adapter prefers filename* (RFC 5987/6266) and falls back to filename=, never returning a path. The proposed name is the formatted one (created date, correspondent, title + extension), not the upload's name. A text/plain upload has no archive version (has_archive_version false) and answers download/ without original with the original (Content-Type 'text/plain; charset=utf-8', kept verbatim). Missing document 404 (DRF json); a document hidden from the user 403 text/html; a user without view_document 403 json. The schema declares thumb/download/preview 200 as application/json (format binary), so the schema guard got one narrowly scoped FILE_RESPONSES exemption (status 200 only; instead it requires a non-JSON content type, an inline/attachment Content-Disposition and a non-empty body; self-tested) plus WRONG_RESPONSE_SCHEMAS for the text/html 403s of thumb and download.

Design decisions: (1) size check before download: DocumentService-independent FileService reads get_document_metadata first (sizes, has_archive_version), so a file over the cap is never downloaded; the real byte count is checked again afterwards. (2) archive of a document without archive version is NotFound with hint 'ask for the original' (tool error and resource error), not a silent fallback. (3) Cap setting PAPERLESS_MCP_MAX_DOWNLOAD_BYTES, default 2 MiB (2097152): base64 adds a third and the file travels in one MCP message that clients cap; 2 MiB holds a typical multi-page scan. Thumbnails have no cap (about 2 KB-30 KB WebP). (4) Tools return ToolResult: [JSON report text, ImageContent | EmbeddedResource(BlobResourceContents with uri paperless://documents/{id}/{kind}, mimeType verbatim incl. charset, _meta.filename)] plus structured content = FileReport, with FileReport.model_json_schema() as explicit output schema to keep the typed-return-model rule. Text files are blobs too (bytes preserved). (5) Resources /thumbnail (image/webp), /original (application/octet-stream declared, real type per content), /archive (application/pdf) return ResourceResult with per-content MIME and _meta.filename; over the cap they raise ResourceError with the link. (6) download_document parameter is 'kind' (original|archive, default original). (7) Tool budget: 8 of 20. Deliberate snapshot change: tests/tools/__snapshots__/test_surface.ambr regenerated with --snapshot-update for the two new tools (+ output schemas) and three new resource templates; reviewed diff is additions only. No gate, threshold, contract or decision weakened.
<!-- SECTION:NOTES:END -->
