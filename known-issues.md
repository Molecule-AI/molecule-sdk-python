# Known Issues — molecule-mcp-server

Issues identified in source but not yet filed as GitHub issues (GH_TOKEN
unavailable in automated agent contexts). Each entry has: location,
symptom, impact, suggested fix.

Format per entry:
```
## KI-N — Short title

**File:** `<path>:<line>`
**Status:** TODO comment / identified / partially fixed
**Severity:** Critical / High / Medium / Low

### Symptom
...

### Impact
...

### Suggested fix
...
---
```

---

## KI-006 — `anyOf` schemas cause `INVALID_ARGUMENTS` on valid inputs

**File:** `src/tools/plugins.ts` (and other tools with union-typed schemas)  
**Status:** Identified  
**Severity:** Medium

### Symptom
Tool `inputSchema` definitions that use JSON Schema `anyOf` to express union types
(e.g., `anyOf: [{ type: "string" }, { type: "null" }]`) are not handled correctly by
the MCP JSON Schema validator. Even when the actual input matches a valid branch of
the `anyOf`, validation fails and returns `INVALID_ARGUMENTS`.

### Impact
Tools using optional or nullable fields defined with `anyOf` reject all calls,
breaking plugin installation and other workflows that depend on those tools.

### Suggested fix
Replace `anyOf` with nullable types directly (`{ type: "string", nullable: true }`)
or flatten the schema to use oneOf with concrete variants. Alternatively, pre-process
the schema before passing to the validator to normalize `anyOf` into supported forms.

---

## KI-007 — Heartbeat cleanup fires after SSE stream closes

**File:** `src/tools/remote_agents.ts` (heartbeat tool)  
**Status:** Identified  
**Severity:** Low

### Symptom
When using SSE transport, the heartbeat mechanism does not immediately clean up
when a stream closes. A background timer or goroutine may continue sending heartbeats
to workspaces whose SSE connections have been closed by the client.

### Impact
Orphaned heartbeat calls continue consuming platform API quota after the MCP client
has disconnected. Over time this can cause the workspace to accumulate heartbeat
sessions that never expire on the platform side.

### Suggested fix
Attach a cleanup function to the SSE stream `close` event. Invalidate the heartbeat
timer when the stream ends so no further calls are made. Document the expected
SSE session lifecycle in the streaming convention section of CLAUDE.md.

**File:** `src/index.ts` (and likely all tool handlers)  
**Status:** Identified  
**Severity:** Medium

### Symptom
Tool handlers use `console.log` and `console.error` for output. Structured JSON
logs (for ingestion into Datadog, Grafana, or the platform's Langfuse traces)
are not emitted. MCP `INTERNAL_ERROR` responses include human-readable text
but no correlation ID or structured metadata.

### Impact
Debugging production issues requires reading raw console output. Correlation IDs
from the platform request context are not attached to errors, making it hard to
trace a failing tool call back to a specific workspace or delegation in the
platform logs.

### Suggested fix
Replace `console.log/error` with a structured logger (e.g. `pino` or
`winston` with JSON format). Attach `requestId` / `workspaceId` from the MCP
request context to every log entry. Ensure errors include a correlation ID
from the platform trace header (`X-Trace-ID` or similar).

---

## KI-002 — Tool input schemas are not validated before passing to handlers

**File:** `src/tools/*.ts` (tool handlers)  
**Status:** Identified  
**Severity:** High

### Symptom
Tool handlers receive raw JSON arguments from the MCP client and pass them
directly to business logic without schema validation. If a client sends a
malformed or unexpected argument shape, the handler throws a TypeError or
returns a cryptic 500 before any error handling can run.

### Impact
Malformed tool calls from a client result in a generic `INTERNAL_ERROR` rather
than `INVALID_ARGUMENTS` (HTTP 400 equivalent). Clients cannot distinguish
between "you sent bad arguments" and "the server crashed" programmatically.

### Suggested fix
Add a Zod schema (already listed as a project dependency in `package.json`)
for every tool's `inputSchema`. Validate arguments at the top of each handler
and return `INVALID_ARGUMENTS` with a detailed list of validation failures
before calling any business logic. This also serves as living documentation
for what each tool accepts.

---

## KI-003 — `test.txt` artifact left in repo root

**File:** `test.txt` (root)  
**Status:** Unresolved — must be removed  
**Severity:** Low

### Symptom
A 5-byte file named `test.txt` with content `"test"` exists in the repo root.
This is not a legitimate file (no reference in `.gitignore` or build tooling)
and appears to be a leftover debug artifact.

### Impact
Clutter. Could be accidentally included in the npm package if `files` in
`package.json` is ever set to include all non-ignored files.

### Suggested fix
Remove it: `rm test.txt && git add test.txt && git commit -m "chore: remove test artifact"`.

---

## KI-004 — No rate limiting or backpressure on platform API calls

**File:** `src/tools/` (all tool implementations)  
**Status:** Identified  
**Severity:** Medium

### Symptom
Tool handlers make direct HTTP calls to the platform API without any
client-side rate limiting or retry backoff. If the platform returns 429
(Too Many Requests), the handler surfaces a `PLATFORM_ERROR` immediately
without retrying or honouring any `Retry-After` header.

### Impact
A burst of tool calls from a single MCP client can exceed platform rate limits
and produce cascading failures. The `RATE_LIMITED` error code is defined in
the conventions but never returned.

### Suggested fix
Add a shared `PlatformClient` (or extend the SDK client) with built-in
rate-limit handling: respect `Retry-After`, implement exponential backoff
with jitter (max 3 retries), and return `RATE_LIMITED` only after
exhausting retries. Share the client instance across handlers to enable
per-client rate limiting.

---

## KI-005 — Streaming tools do not honour cancellation signals

**File:** `src/tools/` (streaming-capable tool handlers)  
**Status:** Identified  
**Severity:** Low

### Symptom
If a streaming tool is cancelled mid-stream (the MCP host closes the connection
or sends a cancellation signal), the handler continues emitting chunks until
the full response is complete. There is no check for cancellation before each
chunk emission.

### Impact
Cancelled requests continue consuming platform API resources (and possibly
incurring cost) even after the client has disconnected. Chunks emitted after
cancellation are silently dropped by the transport but still consumed
upstream.

### Suggested fix
If the MCP server library exposes a cancellation token or abort signal,
check it before each `ContentBlock` emission and stop cleanly (close the
stream without error) if cancelled. Document the behaviour in the streaming
convention in CLAUDE.md.
