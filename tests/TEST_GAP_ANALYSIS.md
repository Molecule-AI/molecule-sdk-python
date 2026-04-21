"""Test coverage gap analysis for molecule-sdk-python.

Priority order for writing new tests.
Run: pytest tests/ -v --tb=short

Legend:
  [COVERED]  Already tested
  [GAP]      Untested — needs test
  [PARTIAL]  Partially covered — needs more cases
"""

# ─── Priority 1: Security + Error paths ──────────────────────────────────────

GAP_01 = """
GAP_01 — _safe_extract_tar security edge cases  [HIGH]

Untested paths in molecule_agent/client.py:_safe_extract_tar:
- Symlink pointing outside dest (tar slip attempt)
- Hardlink pointing outside dest
- Block device / char device in tar (should skip or reject)
- Absolute path entry (should reject)
- Entry with path containing ".." (should reject)
- Empty tar (should extract 0 files)
- Tar with pax headers (long name > 100 chars)

File: tests/test_safe_extract.py (new)
"""

GAP_02 = """
GAP_02 — install_plugin SHA256 verification  [HIGH — prerequisite: verify_plugin_sha256() implementation]

Once verify_plugin_sha256() lands (PR #3):
- Correct SHA256 matches → install proceeds
- SHA256 mismatch → raises ValueError, setup.sh not run
- SHA256 mismatch → install_plugin rolls back staged dir
- Missing sha256 field in plugin.yaml → install proceeds without check (backwards compat)
- sha256 field present but invalid format (not 64-char hex) → raises ValueError at schema validation

File: tests/test_sha256_verification.py (new)
"""

GAP_03 = """
GAP_03 — A2A error paths  [HIGH — BLOCKS debugging per PLAN.md backlog #13]

All covered by current tests? Check:
- HTTP timeout (connect timeout, read timeout)
- 502 Bad Gateway from platform
- 503 Service Unavailable (rate limited) — should retriable?
- 504 Gateway Timeout
- Malformed JSON in A2A response
- Empty A2A response body
- 401 on call_peer → should surface error, not silent
- 403 on call_peer → auth failure logged at ERROR with first ~1KB

File: tests/test_call_peer_errors.py (new)
"""

GAP_04 = """
GAP_04 — verify_plugin_sha256 implementation  [HIGH — prerequisite for GAP_02]

Implement in molecule_agent/client.py:
- _sha256(path) → hex digest
- verify_plugin_sha256(plugin_dir, expected) → bool
  Computes deterministic manifest hash: SHA256(sorted((relpath, SHA256(content)) pairs))
  Independent of extraction order / timestamps.
  Raises ValueError if expected not 64-char hex.

Also add to molecule_plugin/manifest.py PLUGIN_YAML_SCHEMA["properties"]["sha256"].

File: molecule_agent/client.py, molecule_plugin/manifest.py
"""

# ─── Priority 2: Token file safety ────────────────────────────────────────────

GAP_05 = """
GAP_05 — token file error paths  [MED]

Untested in test_remote_agent.py:
- Token file exists but is not a regular file (symlink → should skip)
- Token file has 0 permissions (OSError on read) → should fall through to platform
- Token dir is not writable (OSError on write) → should raise, not silently swallow
- Concurrent write race (two instances, same ws_id) → last-write-wins, no flock
  [KI-004 notes this is by design — flock is the suggested fix; test documents the race]

File: tests/test_token_safety.py (new)
"""

GAP_06 = """
GAP_06 — call_peer bearer token correctness  [MED]

Verified in test_remote_agent.py that Authorization header is sent.
What needs explicit testing:
- Token in response.body is used in Authorization header (not cached stale token)
- 401 response on call_peer surfaces as readable error (not "Command failed")
- 401 on call_peer with prefer_direct=True → falls back to proxy correctly

File: extend test_call_peer_* in test_remote_agent.py
"""

# ─── Priority 3: Run loop + state poll ───────────────────────────────────────

GAP_07 = """
GAP_07 — run_heartbeat_loop transient error handling  [MED]

test_run_loop_continues_through_transient_errors already exists.
What's missing:
- State poll returns HTTP error (not just 200/404) → loop continues
- State poll returns unexpected JSON (non-dict body) → loop continues
- Heartbeat fails but state poll succeeds → loop continues
- Configured heartbeat interval is respected (not hardcoded)

File: tests/test_run_loop.py (new)
"""

GAP_08 = """
GAP_08 — peer discovery cache TTL  [MED]

What needs explicit testing:
- Cache entry expires after DEFAULT_URL_CACHE_TTL (300s) — next call triggers re-discovery
- Cache miss → discover_peer called → result cached
- Invalidate_peer_url removes from cache
- 404 on peer discovery → cache not populated (return None)

File: extend tests in test_remote_agent.py (test_discover_peer_*)
"""

GAP_09 = """
GAP_09 — install_plugin residual state  [MED]

What needs testing:
- install_plugin with no plugin.yaml present → should not crash
- install_plugin where setup.sh exits non-zero → exception raised with stderr captured
- install_plugin overwrites plugin that was previously installed
- install_plugin cleanup: staged dir removed even on failure

File: extend test_install_plugin_* in test_remote_agent.py
"""

# ─── Priority 4: Lower-risk additions ────────────────────────────────────────

GAP_10 = """
GAP_10 — _rmtree_quiet error handling  [LOW]

Test:
- path does not exist → no exception, silent pass
- path exists but permission denied → logs warning, no exception
- path is a file (not dir) → TypeError, logs warning

File: tests/test_rmtree_quiet.py (new)
"""

GAP_11 = """
GAP_11 — Delegation idempotency (KI-002)  [LOW — documents existing behavior]

Client-side: idempotency_key = SHA256(task + current_minute) is already tested conceptually.
What's missing:
- Test that two identical tasks in same minute produce same idempotency_key
- Test that same task in different minute produces different idempotency_key
- Server-side idempotency not enforced (documents the gap — see known-issues.md KI-002)

File: tests/test_delegation_idempotency.py (new)
"""

# ─── Summary table ────────────────────────────────────────────────────────────

SUMMARY = """
| Gap | Area | Severity | File |
|-----|------|----------|------|
| GAP-01 | _safe_extract_tar security | HIGH | test_safe_extract.py |
| GAP-02 | SHA256 verification | HIGH | test_sha256_verification.py |
| GAP-03 | A2A error paths | HIGH | test_call_peer_errors.py |
| GAP-04 | verify_plugin_sha256 impl | HIGH | client.py + manifest.py |
| GAP-05 | Token file safety | MED | test_token_safety.py |
| GAP-06 | call_peer bearer token | MED | extend test_remote_agent.py |
| GAP-07 | run_heartbeat_loop errors | MED | test_run_loop.py |
| GAP-08 | Peer cache TTL | MED | extend test_remote_agent.py |
| GAP-09 | install_plugin edge cases | MED | extend test_remote_agent.py |
| GAP-10 | _rmtree_quiet errors | LOW | test_rmtree_quiet.py |
| GAP-11 | Delegation idempotency | LOW | test_delegation_idempotency.py |

Total: 6 new test files, 4 extensions to existing test files, 1 new implementation.
"""