# Known Issues

> This is a living document. Entries are added when issues are discovered during
> development, testing, or production use. Each entry should include the affected
> version(s), a description, and a workaround or fix status.

---

## 1. Snapshot headers not forwarded in test fixtures

**Severity:** Low (test-only)
**Affected versions:** `< 0.3.0`
**Status:** Known; fix tracked in issue `#sdk-42`

The `conftest.py` integration test fixtures do not simulate the
`X-Snapshot-ID` / `X-Snapshot-Version` headers that the platform proxy injects
on real requests. Tests that exercise snapshot-aware endpoints (audit trail,
memory snapshot reads) may pass locally but fail against a live platform.

**Workaround:** Manually patch `httpx.AsyncClient.send` in affected tests to
inject the headers, or rely on the E2E suite in molecule-core for snapshot
coverage.

---

## 2. Token refresh not implemented for API key auth

**Severity:** Medium
**Affected versions:** All stable (`< 1.0.0`)
**Status:** Known; fix tracked in issue `#sdk-71`

`molecule_sdk` currently ships a single-shot API key (`MOL_API_KEY`). If your
deployment rotates tokens server-side, the SDK will start returning 401s once
the old token expires. There is no automatic refresh loop.

**Workaround:** Re-initialise the `MoleculeClient` after a token rotation
event, or set `MOL_API_KEY` to the fresh value before instantiating the client.

---

## 3. Pydantic v1 compatibility break in `models.py`

**Severity:** Medium
**Affected versions:** `>= 0.4.0`
**Status:** Known; fix tracked in issue `#sdk-88`

The SDK was migrated to Pydantic v2 in v0.4.0. Downstream projects pinned to
Pydantic v1 will encounter `ValidationError` on some model fields that changed
schema (e.g., `Optional[list]` → `list` with default_factory).

**Workaround:** Pin `molecule-sdk-python` alongside `pydantic<2` or upgrade your
Pydantic dependency to `>=2.0`.

---

## 4. Large delegation payloads may hit platform header size limits

**Severity:** Low
**Affected versions:** All stable
**Status:** Known; fix tracked in issue `#sdk-103`

Delegating a task with a message body > ~64 KB can cause the platform's
`X-Workspace-ID` / auth header concatenation to exceed internal NGINX limits,
returning a 502 before the request reaches the workspace.

**Workaround:** Chunk large task descriptions or embed the payload in a
platform object (e.g., a snapshot) and pass only the reference ID in the
delegation message.

---

## 5. `list_peers` returns stale peer list under high churn

**Severity:** Low
**Affected versions:** `< 0.5.2`
**Status:** Known; fix tracked in issue `#sdk-115`

When workspaces are rapidly created or torn down (e.g., in CI burst runs),
`list_peers` may return peer entries with outdated URLs or statuses that have
not yet propagated through the platform registry.

**Workaround:** Add a 2–5 s delay between workspace creation and the first
`list_peers` call in burst scenarios. For production workloads, rely on the
`discover_peer` endpoint directly with the known workspace ID rather than
listing all peers.
