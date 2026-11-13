# paperless-mcp

An [MCP](https://modelcontextprotocol.io) server for [paperless-ngx](https://docs.paperless-ngx.com)
3.x, built on FastMCP 4.

**Status:** early development. One tool so far, `get_instance_overview`; the plan lives in
`backlog/` (Backlog.md). Requires paperless-ngx 3.x (API version 10); 2.x is not supported.

## Use with Claude Code

Create an API token in Paperless (profile menu, "My Profile", API auth token), then register the
server over stdio from a clone of this repository:

```sh
uv sync
claude mcp add paperless \
    --env PAPERLESS_URL=https://paperless.example.org \
    --env PAPERLESS_TOKEN=your-api-token \
    -- "$PWD/.venv/bin/paperless-mcp"
```

Add `--scope user` to use it in every project. Check it with `claude mcp list` or `/mcp` in
Claude Code, then ask for an overview of your Paperless instance. `python -m paperless_mcp` is
the same entrypoint.

At startup the server checks the connection: a rejected token or a Paperless that does not speak
API version 10 stops it with a message on stderr (in Claude Code: `claude --debug` or the `/mcp`
details). An unreachable Paperless does not stop it; the tools report the problem until
Paperless is back. Logs go to stderr (the server's own records as JSON lines) and never contain
tokens, document content or query text at INFO or above: tool arguments and error messages are
not logged, and an internal error is logged by type, with its traceback only at DEBUG.

## Configuration

| Variable | Meaning |
|---|---|
| `PAPERLESS_URL` | Base URL of the Paperless instance (required) |
| `PAPERLESS_TOKEN` | Paperless API token (required) |
| `PAPERLESS_PUBLIC_URL` | Base URL for links shown to users, if it differs from `PAPERLESS_URL` |
| `PAPERLESS_MCP_LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` or `CRITICAL` |
| `PAPERLESS_MCP_STARTUP_TIMEOUT` | Seconds the startup check may take (default 5) |
| `PAPERLESS_MCP_MAX_DOWNLOAD_BYTES` | Largest original or archive file `download_document` attaches, in bytes (default 524288, at most 33554432); larger files are reported with their link only. Attached files travel base64-encoded (a third larger) in one MCP message and cost the client context tokens; the FastMCP client reads at most 1 MiB per HTTP event by default, so raise this only if your clients allow it |
| `PAPERLESS_MCP_MAX_UPLOAD_BYTES` | Largest file `upload_document` and `upload_document_version` take, in bytes (default 10485760 = 10 MiB, at most 104857600). Base64 content is a third larger than the file and must be written out by the caller in one tool call, so real uploads are far smaller; the text, the decoded bytes and the request body are in memory at once (about three times the file), and the size is checked on the text before anything is decoded |
| `PAPERLESS_MCP_UPLOAD_DIRS` | Directories `upload_document` and `upload_document_version` may read files from by path, absolute, comma-separated or a JSON array (default empty: uploading by path is off; `/`, your home directory itself, `/proc`, `/sys`, `/dev` and `/run` are refused as entries). Only the stdio transport ever offers the `path` parameter, and only when this is set; over HTTP callers send base64 content. Symlinks are resolved and must end inside these directories; only regular files with a single hard link are read, `..` in a path is refused, and files or folders whose name starts with a dot below an entry (`.ssh`, `.env`) are never read. The server runs these tools over stdio only, whatever `FASTMCP_TRANSPORT` says |
| `PAPERLESS_MCP_TOOLSETS` | Toolsets to offer, comma-separated or `all` (default `core,ai`). `ai` (`ask_paperless_ai`, `get_ai_suggestions`) appears only while Paperless AI is enabled. These tools are read-only, but each call makes Paperless query its LLM backend, which may cost money and sends document text to it: a client that auto-approves read-only tools will run them unasked. Use `core` to leave them out. `versions` (off by default; `list_document_versions`, `upload_document_version`, `set_document_version_label`, `delete_document_version`, `merge_documents_as_versions`) works with the file versions of documents: `delete_document_version` and `merge_documents_as_versions` need `confirm`, and a merge removes the merged documents for good. `views` (off by default; `PAPERLESS_MCP_TOOLSETS=core,ai,views`) adds the saved views: `list_saved_views`, `get_saved_view`, `create_saved_view`, `update_saved_view`, `delete_saved_view` and `run_saved_view`, which finds the documents of a view with the rows and paging of `search_documents`. `trash` (off by default; `list_trash`, `restore_from_trash`, `empty_trash`) lists what was deleted, restores it (a deleted version is re-attached to its document) and empties the trash: `empty_trash` never defaults to everything, it needs `document_ids` or `all=true`, and `confirm` in both cases, because it removes documents for good. `pdf` (off by default; `rotate_documents`, `merge_documents`, `split_document`, `delete_pages`, `edit_pdf`, `remove_document_password`, `reprocess_documents`) runs Paperless's server-side PDF operations; each returns the background tasks Paperless starts (`wait_seconds` waits for them) and page ranges are checked against the page count first. Deleting originals (they move to the Paperless trash), dropping pages from what a document shows and reprocessing need `confirm`. `reprocess_documents` does the same as the `reprocess` operation of `bulk_edit_documents` but returns its tasks. `sharing` (off by default) publishes documents to people outside Paperless: `list_share_links`, `create_share_link`, `delete_share_link`, `list_share_bundles`, `create_share_bundle`, `rebuild_share_bundle`, `delete_share_bundle` and `email_documents`. A share link or bundle is a public URL on `PAPERLESS_PUBLIC_URL` (else `PAPERLESS_URL`) that anyone who has it can open without logging in until it expires (`expires_in_days`) or is deleted; the delete tools and `email_documents` need `confirm`, and `email_documents` names the recipients and documents in its refusal. E-mail needs a mail server set up in Paperless (`PAPERLESS_EMAIL_HOST`); without one the send fails and says so |
| `PAPERLESS_MCP_DISABLED_TOOLS` | Tools never offered, e.g. `ask_paperless_ai,get_ai_suggestions` |

## Development

Requires [uv](https://docs.astral.sh/uv/), [just](https://just.systems) and Go (pre-commit builds
the pinned gitleaks that `just check` runs over the whole history).

```sh
just install                  # create the environment
uv run pre-commit install     # run the gates on every commit
just check                    # the full local gate, identical to CI
just --list                   # all recipes
```

Scheduled quality jobs (weekly GitHub workflows, each also a recipe and a manual dispatch):

```sh
just mutation                 # mutmut on services/ and paperless/, fails below the 80 % gate, a few minutes
just e2e-latest               # E2E suite against the latest stable paperless-ngx 3.x image
just drift-check              # vendored Paperless schema and docs vs. the latest 3.x release
```

Lint suppressions name a specific code and a reason, e.g. `# noqa: S101 -- reason`
(see `just hygiene`). Inline `# pragma` comments are banned: coverage exclusions are reviewed
`exclude_also` patterns in `pyproject.toml`.

## Credits

This is a new build, not a fork, but it learned from three existing paperless-ngx MCP servers and
adopts some of their ideas deliberately:

- [pvliesdonk/paperless-mcp](https://github.com/pvliesdonk/paperless-mcp) (MIT): field
  projection in lists, content slicing with a next offset, retries only for idempotent calls,
  token redaction, errors that name the next call, a central tool-annotation table, `web_url`.
- [baruchiro/paperless-mcp](https://github.com/baruchiro/paperless-mcp) (ISC): names instead of
  IDs in output, `confirm: true` for destructive calls, an upload allowlist, live end-to-end tests
  against a real Paperless, a test over every advertised tool schema.
- [OrellBuehler/paperless-mcp](https://github.com/OrellBuehler/paperless-mcp) (MIT): toolsets,
  a read-only mode and a test that every tool belongs to a toolset, coverage of Paperless 3.x
  features (versions, AI suggestions, trash, PDF operations), prompts that ask for confirmation,
  a test that every tool named in a prompt exists.

## Licence

MIT, see [LICENSE](LICENSE).
