# molecule_agent — Remote-agent SDK for Molecule AI

Build a Python agent that runs **outside** a Molecule AI platform's Docker network
and registers as a first-class workspace. The agent gets bearer-token auth,
pulls its secrets, calls siblings, installs plugins from the platform's
registry, and reacts to platform-initiated lifecycle events (pause, delete) —
all over plain HTTP.

This is the client side of [Phase 30](../../../PLAN.md). The platform side
ships in the same release; this package is just the SDK an agent author
imports.

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

## What it doesn't do (yet)

- **No long-poll.** Activity polling is fixed-cadence (default 5s). Server-side long-poll support would cut p50 inbound latency to ~0; tracked separately.
- **No automatic reconnect after token loss.** If `~/.molecule/<id>/.auth_token`
  is deleted, you'll need to re-issue the token via the platform admin (since
  `POST /registry/register` is idempotent — it won't mint a second token for
  a workspace that already has one).

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
