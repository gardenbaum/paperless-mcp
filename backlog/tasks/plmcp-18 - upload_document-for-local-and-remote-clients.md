---
id: PLMCP-18
title: upload_document for local and remote clients
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-13T01:02:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-13
  - PLMCP-16
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Uploads must work for Claude Code today and remote clients later, without exposing the host filesystem: one reference server lets any HTTP caller read arbitrary server paths (design §5.7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Accepts base64 content up to a configurable size limit, or in stdio mode only a path inside configured allowlisted directories (symlinks resolved, traversal rejected)
- [ ] #2 Metadata by name: title, correspondent, document type, storage path, tags, created date, archive serial number, custom fields with values
- [ ] #3 Size, MIME type and filename are validated; non-ASCII filenames are preserved
- [ ] #4 Returns the consumption task; an optional bounded wait returns the new document id
- [ ] #5 The path parameter is absent from the tool schema outside stdio mode (registry test)
- [ ] #6 E2E uploads a PDF and finds it afterwards
- [ ] #7 FakePaperless and the contract suite cover every new port method
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
1. Domain: DocumentUpload (ids only) + UploadResult in domain/documents.py; port DocumentWriter.upload_document(upload)->task id; FakePaperless + adapter (POST /api/documents/post_document/ multipart, custom_fields as JSON form field); adapter schema-guard tests, contract tests on fake and live 3.4.2 (consume, wait, delete, empty trash).
2. Services: services/upload_types.py (magic-byte sniffing, allowlist, extension check), services/upload_source.py (base64 decode with pre-decode size bound; allowlisted path read via realpath + dir_fd/O_NOFOLLOW walk, regular files only, TOCTOU-safe), services/uploads.py UploadService (name resolution via TaxonomyResolver, custom values via custom_values.normalise, filename sanitising, TaskService bounded wait). Share resolve/visibility helpers with DocumentEditor (extract into custom_values.py).
3. Settings: PAPERLESS_MCP_MAX_UPLOAD_BYTES (default 10 MiB, 1 B..100 MiB) and PAPERLESS_MCP_UPLOAD_DIRS (absolute dirs, empty = path upload disabled).
4. Tools: schema-per-mode by conditional registration of two explicit tool variants of upload_document (remote: content_base64+filename; stdio with dirs: content_base64 or path), chosen in create_server(stdio=...) from Catalogue.remote_providers / local_providers; cli passes stdio=True. Annotation entry (write, non-destructive, non-idempotent, closed world). Registry: STDIO_PATH_PARAMETERS gets (upload_document, path), plus a registry test that HTTP-mode schemas never carry a path (with self-test), traversal/symlink/loop/device tests at service level.
5. E2E: upload a PDF through the MCP tool, wait for consumption, find it with search_documents, delete + empty trash. Live-verify duplicates, tag hierarchy, ASN clash, custom_fields form encoding on 3.4.2 and record in notes.
6. Gates: just check, just e2e, just mutation; deliberate surface snapshot update; README settings.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live-verified on 3.4.2: tags bring their parents and the inbox tag on consumption (no closure needed here); the same content uploaded twice makes two documents (no duplicate rejection, task success); a taken ASN fails the task ('Given ASN N already exists!'), not the POST; unknown ids and wrong custom field values and unsupported types (sniffed server-side) are 400 at POST; docx/odt/rtf/html/svg/json are 400 without Tika; reader without add_document gets 403; non-ASCII file names are kept (original_file_name); title defaults to the file stem; custom_fields form field is a JSON object id->value. Design: two explicit tool variants of upload_document (tools/upload.py remote, tools/upload_stdio.py with path) registered via Catalogue.remote_providers/local_providers, chosen by create_server(stdio=...) and upload_dirs; FastMCP rejects undeclared args so a path sent to the remote variant errors. Path safety: realpath + allowlist containment, then dir_fd/O_NOFOLLOW component walk, stat before open, fstat after, bounded read; uniform not-found answer. Settings: PAPERLESS_MCP_MAX_UPLOAD_BYTES (10 MiB default, max 100 MiB), PAPERLESS_MCP_UPLOAD_DIRS (absolute, '/' refused). Deliberate changes: surface snapshot gained upload_document (default/all/stdio-uploads profile new; read-only unchanged), schema guard got a documented multipart-form deviation, mutation-equivalents.toml function hashes for client._build/request refreshed (only a type annotation changed), contract conftest got track_document. Mutation 97.2 %, just check and full just e2e green.
<!-- SECTION:NOTES:END -->
