# molecule_agent — Remote-agent SDK for Molecule AI

Build a Python agent that runs **outside** a Molecule AI platform's Docker network
and registers as a first-class workspace. The agent gets bearer-token auth,
pulls its secrets, calls siblings, installs plugins from the platform's
registry, and reacts to platform-initiated lifecycle events (pause, delete) —
all over plain HTTP.

This is the client side of [Phase 30](../../../PLAN.md). The platform side
ships in the same release; this package is just the SDK an agent author
imports.

## What this is / what this isn't

| | `molecule_agent` (this package) | `molecule-ai-workspace-runtime` (separate PyPI wheel) |
|---|---|---|
| **Where it runs** | OUTSIDE Molecule workspaces — your laptop, CI runner, external cloud VM, sidecar service | INSIDE the workspace container, started by the platform |
| **What it talks to** | The platform's HTTP API (`/registry/*`, `/workspaces/:id/*`) | The platform's MCP server (`molecule_*` tools) plus the platform-managed A2A bus |
| **What it exposes** | `RemoteAgentClient`, `A2AServer`, `PollDelivery`, `MessageHandler` | `BaseAdapter`, `a2a_tools`, runtime capabilities, smoke-contract hooks |
| **Who installs it** | You, the external-agent author, via `pip install molecule-sdk` | The platform, baked into the workspace template image at provision time |
| **Auth model** | Bearer token minted by `POST /registry/register`, cached at `~/.molecule/<id>/.auth_token` | Token already present in the workspace environment; runtime reads it from env |

If you are writing an adapter for an SDK that the platform should run *inside* a
workspace (e.g. langchain, crewai, hermes), you want
[`molecule-ai-workspace-runtime`](https://pypi.org/project/molecule-ai-workspace-runtime/),
not this package. See <https://doc.moleculesai.app/docs/runtime-mcp> for the
in-workspace-runtime authoring guide.

## Install

```bash
pip install molecule-sdk     # ships molecule_plugin + molecule_agent
```

## 60-second example

```python
from molecule_agent import RemoteAgentClient

client = RemoteAgentClient(
    workspace_id="<the-uuid-of-an-external-workspace-on-the-platform>",
    platform_url="https://your-platform.example.com",
    agent_card={"name": "my-remote-agent", "skills": []},
)

# 1. Register and mint a bearer token (cached at ~/.molecule/<id>/.auth_token).
client.register()

# 2. Pull secrets the platform was set to inject.
secrets = client.pull_secrets()
# → {"OPENAI_API_KEY": "...", ...}

# 3. (Optional) install a plugin locally — pulls a tarball, unpacks, runs setup.sh.
client.install_plugin("molecule-dev")
client.install_plugin("my-plugin", source="github://acme/my-plugin")

# 4. Run the heartbeat + state-poll loop until the platform pauses/deletes us.
terminal = client.run_heartbeat_loop()
print(f"loop exited: {terminal}")
```

A runnable demo with full setup walkthrough lives at
[`sdk/python/examples/remote-agent/`](../examples/remote-agent).

## What the SDK gives you

| Method | Phase | What it does |
|---|---|---|
| `register()` | 30.1 | Mint + cache the workspace's bearer token |
| `pull_secrets()` | 30.2 | Token-gated GET of merged secrets dict |
| `install_plugin(name, source=None)` | 30.3 | Stream plugin tarball, atomic extract, run setup.sh |
| `poll_state()` | 30.4 | Lightweight `{status, paused, deleted}` poll |
| `heartbeat(...)` | 30.1 | Single bearer-authed heartbeat |
| `get_peers()` / `discover_peer()` | 30.6 | Sibling URL discovery with TTL cache |
| `call_peer(target, message)` | 30.6 | Direct A2A with proxy fallback |
| `fetch_inbound(since_id=…)` | 30.8c | One-shot poll of `/workspaces/:id/activity` for inbound A2A |
| `reply(msg, text)` | 30.8c | Smart-routes reply to `/notify` (canvas user) or `/a2a` (peer) |
| `run_heartbeat_loop()` | combo | Drives heartbeat + state-poll on a timer; exits on pause/delete |
| `run_agent_loop(handler)` | combo | Heartbeat + state + **inbound dispatch**; exits on pause/delete |

## Inbound delivery — push vs poll

Two ways an external agent can receive A2A messages:

| Path | When to use | Class |
|---|---|---|
| **Push** | Your agent has a publicly reachable URL (cloud VM, ngrok tunnel) | `A2AServer` (Phase 30.8b) |
| **Poll** | Your agent is behind NAT, on a laptop, or in a CI runner with no public URL | `PollDelivery` (Phase 30.8c) |

Both dispatch to the same `MessageHandler` callback through `run_agent_loop`:

```python
from molecule_agent import RemoteAgentClient, InboundMessage

def my_handler(msg: InboundMessage, client: RemoteAgentClient) -> str | None:
    print(f"← {msg.source}: {msg.text}")
    return f"echo: {msg.text}"   # auto-routed via /notify or /a2a

client = RemoteAgentClient(workspace_id="…", platform_url="…")
client.register()
client.run_agent_loop(my_handler)   # default: PollDelivery
```

The reply transport (`/notify` for canvas users, `/a2a` for peer agents) is hidden — `client.reply(msg, text)` picks based on `msg.source`. Async handlers work too; `PollDelivery` detects awaitable returns and `asyncio.run`s them.

### `InboundMessage` shape

`InboundMessage` is what `MessageHandler` receives. The typed fields the SDK
parses today:

| Field | Type | What it is |
|---|---|---|
| `activity_id` | `str` | Cursor — the `activity_logs.id` row this event came from. Pass to `fetch_inbound(since_id=…)` to skip past it on the next poll. |
| `source` | `Literal["canvas_user", "peer_agent", "unknown"]` | Normalized sender kind. `"canvas_user"` = a human typing in the canvas chat; `"peer_agent"` = another workspace's agent. `"unknown"` if the row's source is unrecognized — `reply()` will refuse to guess. |
| `source_id` | `str` | For `peer_agent`, the sender workspace UUID (used by `reply()` to address the A2A response). Empty for `canvas_user`. |
| `text` | `str` | The message body. Pulled from `data.text` then `data.message` in the underlying activity row. **Treat as untrusted user content** — same threat model as any chat input. |
| `raw` | `dict` | The full raw activity-log row. Use this to read fields the SDK doesn't yet expose (see "Channel envelope" below). |

### Channel envelope (wire format)

The platform delivers each inbound A2A event as an `activity_logs` row. As of
**2026-05-02** (CP push envelope, see <https://doc.moleculesai.app/docs/runtime-mcp>),
the envelope's `data` block carries:

```jsonc
{
  "id": "<activity-uuid>",                 // == InboundMessage.activity_id
  "type": "a2a_receive",
  "source_id": "<sender-workspace-uuid>",  // peer_agent only; empty for canvas_user
  "ts": "2026-05-02T10:15:30Z",            // RFC3339 — when the platform queued the event
  "data": {
    "source": "peer_agent",                // "canvas_user" | "peer_agent"
    "kind": "peer_agent",                  // mirrors the channel-tag attr
    "text": "<message body>",              // (or "message")
    "peer_id": "<sender-workspace-uuid>",  // duplicate of source_id, peer_agent only
    "activity_id": "<activity-uuid>",      // duplicate of top-level id

    // === enrichment fields added 2026-05-02 (CP PRs #2472, #2476) ===
    "peer_name": "ops-agent",                                          // peer's display name (registry-resolved); may be absent if the registry lookup failed
    "peer_role": "sre",                                                // peer's declared role; same registry source
    "agent_card_url": "https://<platform>/registry/discover/<peer_id>" // deterministic URL for the platform's discover endpoint for this peer
  }
}
```

**SDK status of the enrichment fields:** `InboundMessage` surfaces
`peer_name`, `peer_role`, and `agent_card_url` as typed string
attributes. Each defaults to the empty string when the registry lookup
failed at push time (or when the inbound row predates the 2026-05-02
enrichment), so handler code can read them without key-error guards:

```python
def handler(msg, client):
    if msg.peer_name:
        return f"hi {msg.peer_name}, you said: {msg.text}"
    return f"hi, you said: {msg.text}"
```

### A2A reply transport — what `reply()` actually does

`client.reply(msg, text)` dispatches based on `msg.source`. The transport is
chosen for you so handler code doesn't need to branch:

| `msg.source` | HTTP call `reply()` makes | Server-side effect |
|---|---|---|
| `canvas_user` | `POST /workspaces/<self>/notify` with `{"message": text}` | Canvas WebSocket pushes the text to the user's chat |
| `peer_agent` | `POST /workspaces/<msg.source_id>/a2a` with a JSON-RPC `message/send` envelope; sets `X-Source-Workspace-Id: <self>` and `X-Workspace-ID: <self>` | Platform routes the JSON-RPC message to the peer workspace's inbound A2A endpoint |
| `unknown` | Raises `ValueError` | The SDK refuses to guess. Inspect `msg.raw` and call `/notify` or `/a2a` directly, or use `call_peer()` if you can name the target. |

`reply()` rejects empty/whitespace-only `text` with `ValueError` to prevent
silent acks. On non-2xx the underlying `requests.HTTPError` propagates so the
handler can decide whether to retry, surface to its observability, or fail
loudly.

## CLI: `molecule_agent connect`

One command bootstraps the full poll-mode loop. No code beyond your handler:

```bash
python -m molecule_agent connect \
    --platform-url https://your-tenant.moleculesai.app \
    --workspace-id 550e8400-… \
    --token your-workspace-token \
    --handler my_handlers:echo \
    --poll-interval 5 \
    --cursor-file ~/.molecule/cursor
```

Where `my_handlers.py` is anywhere on `PYTHONPATH`:

```python
def echo(msg, client):
    return f"echo: {msg.text}"
```

All flags also read from environment variables (`MOLECULE_PLATFORM_URL`, `MOLECULE_WORKSPACE_ID`, `MOLECULE_WORKSPACE_TOKEN`, `MOLECULE_POLL_INTERVAL`, `MOLECULE_CURSOR_FILE`). SIGTERM/SIGINT shut the loop down cleanly.

## What it doesn't do (yet) — Limitations & roadmap

These are server-supported features that the SDK has not yet wrapped, plus
known protocol gaps. Each entry is named so a follow-up issue / PR can
reference it directly.

- **No long-poll.** Activity polling is fixed-cadence (default 5s). Server-side long-poll support would cut p50 inbound latency to ~0; tracked separately.

- **No automatic reconnect after token loss.** If `~/.molecule/<id>/.auth_token`
  is deleted, you'll need to re-issue the token via the platform admin (since
  `POST /registry/register` is idempotent — it won't mint a second token for
  a workspace that already has one).

- **SaaS multi-tenant headers are auto-injected.** On the multi-tenant SaaS
  edge (`*.staging.moleculesai.app`, `*.moleculesai.app`), the WAF requires
  `X-Molecule-Org-Id` (TenantGuard) and `Origin` (path-rewrite gate).
  `RemoteAgentClient` accepts both as constructor kwargs:

  ```python
  client = RemoteAgentClient(
      workspace_id="…",
      platform_url="https://acme.moleculesai.app",
      org_id="<your-org-uuid>",
      # origin defaults to platform_url; pass origin=None to opt out
      # on a self-hosted deployment.
  )
  ```

  Both headers are merged into every request (including `register()`,
  which predates the auth token). For self-hosted single-tenant
  deployments, leave `org_id` empty and pass `origin=None` to revert
  to the classic auth-only header set.

## Design choices

- **Blocking (`requests`), not async.** Drops into any runtime — script,
  thread, asyncio loop. No framework lock-in.
- **Token cached on disk with 0600** so a restart of the agent doesn't
  re-issue (the platform refuses anyway). Lives at
  `~/.molecule/<workspace_id>/.auth_token`.
- **URL cache for siblings is process-memory only**, 5-minute TTL. Cleared
  on graceful failures via `invalidate_peer_url`.
- **Tar extraction uses `_safe_extract_tar`** that rejects path-traversal
  and skips symlinks — defense against tar-slip CVEs in case a plugin
  source is compromised.

## Compatibility

Requires a Molecule AI platform with Phase 30 endpoints (PR #122 onwards).
Older platforms grandfather pre-token workspaces through, so this SDK
also works against a transition-period deployment — but you won't get
the security benefits of bearer auth until both sides upgrade.

## Related

- [`molecule_plugin`](../molecule_plugin) — the *other* SDK in this
  package, for plugin authors. Different audience.
- [`sdk/python/examples/remote-agent/run.py`](../examples/remote-agent/run.py)
  — the runnable demo that proves all of the above end-to-end.
