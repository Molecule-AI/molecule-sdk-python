# Known Issues — molecule-sdk-python

Issues identified in source but not yet filed as GitHub issues (GH_TOKEN
unavailable in automated agent contexts). Each entry has: location,
symptom, impact, suggested fix.

Format per entry:
```
## KI-N — Short title

**File:** `<path>:<line>`
**Status:** TODO comment / identified / partially fixed
**Severity:** Critical / High / Medium / Low
**Platform phase:** (optional — which Phase 30 sub-phase is affected)

### Symptom
...

### Impact
...

### Suggested fix
...
---
```

---

## KI-001 — RemoteAgentClient does not implement inbound A2A server

**File:** `molecule_agent/client.py`  
**Status:** Known limitation; not yet implemented  
**Severity:** Medium  
**Platform phase:** Phase 30.8b

### Symptom
`RemoteAgentClient` can call other workspaces via A2A (outbound), but cannot
receive inbound A2A calls. Any workspace that tries to delegate to or message
this agent will get a connection refused or timeout.

### Impact
Agents running outside the platform's Docker network via `molecule_agent` are
one-directional. Platform agents cannot push work to them — the remote agent
must poll or be provisioned with a publicly reachable webhook endpoint.

### Suggested fix
Add an `A2AServerMixin` class that exposes a `FastAPI` or `flask` route
(`POST /a2a/inbound`) and runs in a background thread alongside the client's
heartbeat loop. Register the inbound URL with the platform via the
`/registry/discover` update endpoint when the server starts. See Phase 30.8b
in the platform `PLAN.md`.

---

## KI-002 — Delegation has no server-side idempotency key enforcement

**File:** `molecule_agent/client.py` (client-side SHA256 key)  
**Status:** Partially mitigated client-side (SHA256 rounded-to-minute)  
**Severity:** Medium  
**Platform phase:** Phase 30.6

### Symptom
The client generates an idempotency key as `SHA256(task + current_minute)`, but
the platform's `POST /workspaces/:id/delegate` endpoint does not enforce
idempotency server-side. Two identical tasks sent within the same calendar
minute produce duplicate processing if the platform accepts both.

### Impact
A workspace container restart mid-delegation (e.g. liveness probe restart) that
fires the same delegation request twice will result in duplicate side-effects
(double commits, double API calls, double messages) if the platform has not yet
stored the first delegation's result.

### Suggested fix
Platform-side: accept an optional `idempotency_key` field in
`POST /workspaces/:id/delegate`, check for existing non-failed delegation with
the same `(workspace_id, idempotency_key)`, return HTTP 200 with existing ID
instead of creating a new row. Client-side key generation is correct; it is
the server that needs to honor it.

---

## KI-003 — `_safe_extract_tar` silently skips all symlinks

**File:** `molecule_agent/client.py:_safe_extract_tar`  
**Status:** By design (security posture)  
**Severity:** Low (misleading behavior)

### Symptom
When extracting plugin tarballs, `_safe_extract_tar` silently skips any entry
that is a symlink. This means plugin tarballs that legitimately use symlinks
for shared assets (e.g., `assets/logo.png -> ../shared/logo.png`) will be
silently omitted from the extracted plugin directory with no error or warning.

### Impact
Some valid plugins may appear to install successfully but be missing files at
runtime. This can manifest as confusing "file not found" errors that are hard to
trace to the install step.

### Suggested fix
Emit a `logger.warning()` for each skipped symlink so operators can see what
was dropped. Alternatively, allow safe relative symlinks (those resolving
within the extraction root) while blocking absolute symlinks and `..`-escaping
symlinks. Document the behavior in the plugin authoring guide.

---

## KI-004 — Token file races between concurrent instances of RemoteAgentClient

**File:** `molecule_agent/client.py` (token caching)  
**Status:** Identified  
**Severity:** Low

### Symptom
Multiple `RemoteAgentClient` instances sharing the same `workspace_id` write to
the same token cache file (`~/.molecule/<workspace_id>/.auth_token`). If two
instances start simultaneously, the file read/write is not atomic — one
instance may read a partially-written token or overwrite a valid token with an
older one.

### Impact
On a cold start with multiple workers for the same workspace, some workers may
fail to register because their token is stale. The platform refuses to issue a
second token when one exists on disk.

### Suggested fix
Use a file-based lock (e.g. `fcntl.flock` or `portalocker`) around token read
and write operations. Alternatively, use per-process token storage (in-memory)
and only write to disk as a recovery fallback.

---

## KI-005 — `validate_plugin` does not check for secrets in bundle manifests

**File:** `molecule_plugin/manifest.py:validate_manifest`  
**Status:** Not yet implemented  
**Severity:** High

### Symptom
`validate_manifest` does not scan the `env:` or `secrets:` fields of a
`plugin.yaml` for hardcoded credentials (API keys, passwords, tokens). Plugin
authors could accidentally commit secrets into what should be a generic bundle.

### Impact
Secrets committed to a plugin manifest are visible in the repo and any tarball
published to PyPI or the plugin registry. Per platform constraints
(`constraints-and-rules.md`), bundles must never contain secrets.

### Suggested fix
Add a `validate_no_secrets()` check in `validate_manifest` that scans all
string values in the manifest for patterns matching common secret formats
(`sk-`, `ghp_`, ` Bearer `, 32+ char hex strings, etc.). Return a
`ValidationError` with level `HIGH` if any are found, even in example or
placeholder values. Add a corresponding test with a manifest containing a
known secret pattern.
