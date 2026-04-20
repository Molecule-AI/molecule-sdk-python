# CLAUDE.md — molecule-sdk-python

## Project overview

Python SDK for the Molecule AI agent platform. Exposes two user-facing packages:

- **`molecule_agent`** — Phase 30.8 remote-agent client. Write an agent that runs
  outside the platform's Docker network; it registers with the platform, pulls
  secrets, sends heartbeats, and detects pause/delete. Wraps the Phase 30.1–30.7
  HTTP contract (register, secrets, heartbeat, state-poll, A2A peer discovery,
  delegation, plugin install).

- **`molecule_plugin`** — Plugin-authoring SDK. Build installable plugin directories
  that ship rules, skills (agentskills.io format), and per-runtime adaptors to any
  Molecule AI workspace. Ships validators for plugin.yaml, SKILL.md (agentskills.io
  spec), workspace/org/channel templates, and a `python -m molecule_plugin` CLI.

Both packages are published together as `molecule-ai-sdk` on PyPI (`setuptools`,
`pyproject.toml`, `requires-python = ">=3.11"`).

---

## Build and test

```bash
# Install in dev mode
pip install -e .

# Run the full suite
pytest

# Run only molecule_agent tests (remote-agent client)
pytest tests/test_remote_agent.py

# Run only molecule_plugin tests (SDK + validators)
pytest tests/test_sdk.py tests/test_validators.py

# CLI smoke
python -m molecule_plugin validate --help
python -m molecule_plugin validate plugin /path/to/my-plugin/
python -m molecule_plugin validate workspace /path/to/workspace-template/
```

Tests use standard `pytest` fixtures with in-memory mocks — no live platform
required. The `molecule_agent` tests mock `requests.Session` directly via
`unittest.mock.MagicMock`.

---

## Package conventions

```
molecule_agent/          # Remote-agent client (blocking requests, Phase 30)
  client.py              # RemoteAgentClient, WorkspaceState, PeerInfo,
                        # make_idempotency_key, _safe_extract_tar

molecule_plugin/         # Plugin-authoring SDK
  protocol.py            # PluginAdaptor (runtime_checkable Protocol),
                        # InstallContext, InstallResult
  builtins.py            # AgentskillsAdaptor (default),
                        # SKIP_ROOT_MD, _install_claude_layer
  manifest.py            # PLUGIN_YAML_SCHEMA, validate_manifest,
                        # parse_skill_md, validate_skill, validate_plugin
  workspace.py           # validate_workspace_template, SUPPORTED_RUNTIMES
  org.py                 # validate_org_template
  channel.py             # validate_channel_config, validate_channel_file,
                        # SUPPORTED_CHANNEL_TYPES
  __main__.py            # CLI: python -m molecule_plugin validate [plugin|workspace|org|channel]

template/               # Reference plugin layout (NOT pip-installable)
  adapters/
    claude_code.py       # AgentskillsAdaptor — one-liner per runtime
    deepagents.py        # AgentskillsAdaptor — one-liner per runtime

examples/remote-agent/   # Runnable Phase 30.1–30.5 demo
  run.py
```

### Adding a new tool or endpoint to molecule_agent

1. Pick the Phase 30 sub-phase that matches the contract (e.g. 30.6 = peer
   discovery).
2. Add the method to `RemoteAgentClient` in `client.py`. Follow the existing
   pattern: `_auth_headers()` for bearer token, `raise_for_status()` on the
   response, `logger.warning()` instead of re-raising for transient errors in
   loops.
3. Add a corresponding test fixture + test cases in `tests/test_remote_agent.py`.
   Mock `client._session.get/.post` with `FakeResponse` or a `side_effect`.
4. Export from `__init__.py` and add to `__all__`.

### Adding a new validator to molecule_plugin

1. Add the validation function to the appropriate module (`manifest.py` for
   SKILL.md, `workspace.py` for workspace templates, etc.).
2. Return a list of error strings (manifest layer) or a list of
   `ValidationError` objects (workspace/org/channel layer — see existing
   patterns in `workspace.py`).
3. Re-export from `molecule_plugin/__init__.py`.
4. Add `python -m molecule_plugin validate <kind> /path` CLI cases or hook into
   the existing dispatch in `__main__.py` if the kind is new.
5. Add tests in `tests/test_sdk.py` or `tests/test_validators.py`.

---

## Release process

PyPI publication is automated via GitHub Actions and triggered by **git tags** with
a `v` prefix matching the version in `pyproject.toml` (e.g. tag `v0.2.1` publishes
`molecule-ai-sdk==0.2.1`):

```bash
# 1. Update version in pyproject.toml
# 2. Tag and push
git tag v0.2.1
git push origin v0.2.1
```

The GitHub Actions workflow handles sdist + wheel build and upload to PyPI.
No manual steps required. Ensure you have PyPI token permissions in the repo
secrets before the first release.

---

## Platform integration notes

`molecule_agent` wraps these Phase 30 HTTP endpoints (all require bearer token
unless noted):

| Method | Endpoint | Phase | Auth |
|--------|----------|-------|------|
| `POST` | `/registry/register` | 30.1 | none (issues token) |
| `GET` | `/workspaces/:id/secrets/values` | 30.2 | bearer |
| `POST` | `/registry/heartbeat` | 30.1 | bearer |
| `GET` | `/workspaces/:id/state` | 30.4 | bearer |
| `GET` | `/registry/:id/peers` | 30.6 | bearer + X-Workspace-ID |
| `GET` | `/registry/discover/:id` | 30.6 | bearer + X-Workspace-ID |
| `POST` | peer direct URL (A2A) | 30.6 | bearer + X-Workspace-ID |
| `POST` | `/workspaces/:id/a2a` (proxy) | 30.6 | bearer + X-Workspace-ID |
| `POST` | `/workspaces/:id/delegate` | 30.6 | bearer + X-Workspace-ID, 300s timeout |
| `GET` | `/workspaces/:id/plugins/:name/download` | 30.3 | bearer |
| `POST` | `/workspaces/:id/plugins` | 30.3 | bearer |

**Token** is cached at `~/.molecule/<workspace_id>/.auth_token` with `0600`
permissions. On restart the client reuses the cached token — the platform
refuses to issue a second token when one is on file.

**Idempotency (KI-002):** `delegate()` auto-generates an idempotency key as
`SHA256(task + current_minute)` (rounded to the minute). Two container restarts
within the same minute that send the same task string share the key, preventing
duplicate processing.

**Plugin install:** Tars are extracted with `_safe_extract_tar()` — rejects
`..` path components and absolute paths; silently skips symlinks/hardlinks.
Atomic rename via staging dir + rename prevents partial installs.

---

## SDK-specific conventions

- **Python:** `>=3.11`, no external async dependencies in `molecule_agent`
  (uses blocking `requests` so it embeds in any event loop). `molecule_plugin`
  adaptor methods are `async` (`install`/`uninstall` satisfy `PluginAdaptor`).

- **Async:** `molecule_plugin` uses `async def`/`await` for `PluginAdaptor`.
  Call `asyncio.run(adaptor.install(ctx))` to run inline in a sync context.

- **Error handling:** Network errors in loops are logged and swallowed so a
  transient platform hiccup does not take a remote agent offline. API-level
  errors (4xx) propagate via `raise_for_status()`.

- **Token security:** Token file created with `0o600` — other local users must
  not be able to read it. `_safe_extract_tar` guards against tar-slip attacks
  in plugin install.

- **Validation:** `validate_manifest`/`validate_skill`/`validate_plugin` are
  pure and have no external dependencies (no `jsonschema`). They return lists
  of error strings. The workspace/org/channel validators return
  `list[ValidationError]` objects with `.file` and `.message` fields.

- **First-party plugins:** `test_first_party_plugins_are_spec_compliant()` in
  `tests/test_sdk.py` validates every plugin in the repo's top-level `plugins/`
  directory against full agentskills.io spec. Keep that test passing.

---

## Known issues

- Before patching a silent failure or quirky behavior, **file a GitHub issue
  first**. Do not patch silently — the SDK is consumed across multiple
  runtime environments and silent patches can cause subtle breakage elsewhere.

- `molecule_agent` does not yet bundle an inbound A2A server helper.
  Platform-initiated calls to a remote agent without a publicly reachable
  endpoint will not succeed. See Phase 30.8b in the platform's `PLAN.md`.

---

## Relevant platform docs

- **Platform conventions:** `docs/development/constraints-and-rules.md` — no auth
  for MVP, Postgres as source of truth, no secrets in bundles, generic
  workspace-template.
- **Secrets runbook:** `docs/runbooks/saas-secrets.md` — read before rotating any
  secrets.
- **Cron learnings:** `cron-learnings.md` (platform root) — read before reviewing
  PRs; write a 1-line reflection to `.claude/per-tick-reflections.md` after
  triage.
- **CLAUDE.md/PLAN.md sync PRs:** treat these as always noteworthy.
- **molecule-core docs:** Full platform `PLAN.md` and architecture docs at
  `https://github.com/hongmingw/molecule-monorepo`
