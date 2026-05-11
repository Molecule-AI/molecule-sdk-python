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

**File:** `molecule_agent/client.py`, `molecule_agent/a2a_server.py`, `molecule_agent/inbound.py`  
**Status:** ✅ Resolved  
**Severity:** Medium  
**Platform phase:** Phase 30.8b

### Resolution
The SDK now ships two inbound delivery paths:

**Push mode (`A2AServer`)** — `molecule_agent.a2a_server.A2AServer` exposes an HTTP
server with a `POST /a2a/inbound` endpoint. It runs in a background daemon thread
alongside the client's heartbeat loop. Use with `PushDelivery` from `inbound.py`:

```python
from molecule_agent import RemoteAgentClient, A2AServer
from molecule_agent.inbound import PushDelivery

server = A2AServer(agent_id=workspace_id, inbound_url="https://...", message_handler=my_handler)
server.start_in_background()
client = RemoteAgentClient(workspace_id=workspace_id, platform_url=...)
client.reported_url = server.inbound_url  # register with this URL
client.register()
# Pass PushDelivery so run_agent_loop doesn't also poll
client.run_agent_loop(handler=my_handler, delivery=PushDelivery(client, server))
```

**Poll mode (`PollDelivery`)** — for agents behind NAT or without a public endpoint,
the SDK's `PollDelivery` polls `GET /workspaces/:id/activity` on a configurable
interval (default 5s). Both paths feed the same `MessageHandler` callback.
`run_agent_loop` picks `PollDelivery` automatically when no explicit delivery is passed.

### Files added
- `molecule_agent/a2a_server.py` — `A2AServer` class; `HTTPServer` + `_A2AHandler`
  running in a daemon thread; handles `POST /a2a/inbound`, async/sync handlers,
  graceful stop.
- `molecule_agent/inbound.py` — `InboundDelivery` protocol, `PollDelivery`,
  `PushDelivery` (wraps `A2AServer`), `InboundMessage`, `MessageHandler`.
- `RemoteAgentClient.run_agent_loop` updated to accept any `InboundDelivery`.

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
**Status:** ✅ Resolved  
**Severity:** Low (misleading behavior)

### Resolution
`_safe_extract_tar` now emits a `logger.warning` for every skipped symlink:

```
skipping symlink in plugin tarball (not supported for security): <name> -> <target>
```

The file is still skipped (symlinks are a security risk in untrusted tarballs).
The warning lets operators audit what was dropped without changing the security
posture.

Added `test_safe_extract_logs_warning_for_skipped_symlink` in
`tests/test_remote_agent.py` asserting the warning is emitted.

### Suggested fix
Emit a `logger.warning()` for each skipped symlink so operators can see what
was dropped. Alternatively, allow safe relative symlinks (those resolving
within the extraction root) while blocking absolute symlinks and `..`-escaping
symlinks. Document the behavior in the plugin authoring guide.

---

## KI-004 — Token file races between concurrent instances of RemoteAgentClient

**File:** `molecule_agent/client.py` (token caching)  
**Status:** ✅ Resolved  
**Severity:** Low

### Resolution
Added `fcntl.flock` around token read/write operations in `load_token()` and
`save_token()`:

- `load_token()` — acquires a shared lock (`LOCK_SH | LOCK_NB`) before reading.
  Returns `None` immediately if the lock is contended rather than blocking.
- `save_token()` — acquires an exclusive lock (`LOCK_EX | LOCK_NB`) before
  writing. If the lock is held by another writer, logs a warning and skips the
  write (the in-memory `_token` is still updated so this instance functions
  correctly). Releases the lock in a `finally` block.

Concurrent readers are always safe (shared lock allows multiple simultaneous
readers). Concurrent writers are serialised by the exclusive lock; if a writer
cannot acquire the lock immediately it gracefully degrades rather than blocking.
The platform's one-token-per-workspace invariant is preserved — no stale token
overwrites.

---

## KI-005 — `validate_manifest` does not check for secrets in bundle manifests

**File:** `molecule_plugin/manifest.py:validate_manifest`  
**Status:** ✅ Fixed — `_scan_for_secrets()` added; called from `validate_manifest`  
**Resolved in:** `fix/ki-005-ki-007` branch  
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

---

## KI-006 — Plugin content integrity not verified client-side (RESOLVED)

**File:** `molecule_agent/client.py:verify_plugin_sha256`, `molecule_plugin/manifest.py:validate_manifest`  
**Status:** ✅ Implemented — see SDK PR on `docs/add-claude-md` branch  
**Severity:** Medium (mitigated by platform-side pinned-ref enforcement from molecule-core PR #1019)

### Symptom
`install_plugin()` downloaded and extracted plugin tarballs with no client-side
content verification. A compromised platform registry serving a tampered tarball
under a valid pinned-ref would pass `_safe_extract_tar` (no `..` or absolute
paths) but could contain a malicious `setup.sh`.

### Resolution
Added:
- `verify_plugin_sha256(plugin_dir, expected)` — computes a content-addressed
  manifest hash over sorted `(relative_path, SHA256(content))` pairs; deterministic
  regardless of extraction order or timestamps.
- `install_plugin()` reads `plugin.yaml → sha256` after atomic rename and before
  `setup.sh`; mismatches raise `ValueError` and delete the plugin directory.
- `PLUGIN_YAML_SCHEMA` gains an optional `sha256` field (64-char lowercase hex).
- `validate_manifest()` validates `sha256` format when present.

Platform-side (molecule-core PR #1019) enforces source integrity (pinned git SHAs
or semver tags). SDK-side closes the content-integrity gap. Together they cover
both the "which code was fetched" and "did it arrive intact" axes.

Authors should add `sha256` to their `plugin.yaml` (generate with
`python -m molecule_agent verify-sha256 <plugin-dir>`) and commit it alongside
the plugin content.

---

## KI-007 — `_is_hex` raises `TypeError` on non-string arguments instead of returning `False`

**File:** `molecule_agent/client.py:_is_hex`  
**Status:** ✅ Fixed — isinstance guard added  
**Resolved in:** `fix/ki-005-ki-007` branch  
**Severity:** Low

### Symptom
`_is_hex` is called inside `verify_plugin_sha256` after a length check. When
passed a non-string argument (e.g. `None`, an `int`, a `list`), `int(value, 16)`
raises `TypeError: int() can't convert non-string with explicit base` instead of
returning `False`. `verify_plugin_sha256` would surface a confusing `TypeError`
rather than a descriptive validation error.

### Impact
Any bug passing a non-string `expected` to `verify_plugin_sha256` produces a
confusing `TypeError` instead of the intended `ValueError`. Low-probability
edge case (function is internal), but violates the principle that validator
functions should never raise unexpected exceptions.

### Suggested fix
Guard at the top of `_is_hex`:
```python
def _is_hex(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False
```

---

## KI-008 — `test_call_peer_errors.py` fails collection due to missing `tests/conftest.py`

**File:** `tests/test_call_peer_errors.py`  
**Status:** ✅ Resolved  
**Severity:** Low

### Resolution
`tests/conftest.py` exists with the `_CaptureHandler` stub definition.
`pytest tests/test_call_peer_errors.py` runs all 12 tests cleanly.
`pytest tests/` collects all test files with no collection errors.

---

## KI-009 — `run_heartbeat_loop()` does not honour external stop signals

**File:** `molecule_agent/client.py` (`RemoteAgentClient.run_heartbeat_loop`)
**Status:** Identified
**Severity:** Low

### Symptom
`run_heartbeat_loop()` runs an unbounded `while True` loop with `sleep(heartbeat_interval)`
between iterations. There is no mechanism for an external caller to signal the loop
to exit cleanly. If the MCP client that launched the remote agent disconnects (e.g. via
SSE stream close), the heartbeat loop continues indefinitely until `max_iterations` is
reached or the process is killed externally.

### Impact
Orphaned heartbeat processes continue consuming platform API quota after the controlling
MCP client has disconnected. Each iteration sends a `POST /registry/heartbeat` and a
`GET /workspaces/:id/state` call. Over time this accumulates unnecessary API calls.

### Suggested fix
Add a `stop_event` parameter to `run_heartbeat_loop()` — a `threading.Event` or
`asyncio.Event` that, when set, causes the loop to exit cleanly with a `stopped`
return value:

```python
def run_heartbeat_loop(
    self,
    max_iterations: int | None = None,
    task_supplier: "callable | None" = None,
    stop_event: threading.Event | None = None,
) -> str:
    i = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            return "stopped"
        if max_iterations is not None and i >= max_iterations:
            return "max_iterations"
        # ... rest of loop
```

Callers (MCP client wrappers, shell scripts) can then call `stop_event.set()` on
SIGTERM/SIGINT to achieve clean shutdown.
