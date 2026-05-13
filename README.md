# molecule-ai-sdk — Python SDK for Molecule AI

Two packages, one install:

| Package | Purpose |
|---------|---------|
| `molecule_agent` | **Remote-agent client.** Run an agent outside the platform's Docker network — it registers, pulls secrets, heartbeats, and participates in A2A delegation. |
| `molecule_plugin` | **Plugin-authoring SDK.** Bundle rules, skills, and per-runtime adaptors for any Molecule AI workspace. |

Published on PyPI as [`molecule-ai-sdk`](https://pypi.org/project/molecule-ai-sdk/).

```bash
pip install molecule-ai-sdk
```

---

## molecule_agent — Remote-Agent Client

Write an agent that runs on a laptop, VM, or any machine outside the platform's Docker network. The SDK handles registration, authentication, heartbeat, and A2A communication via the Phase 30 HTTP contract.

### Quick Start

```python
from molecule_agent import RemoteAgentClient

client = RemoteAgentClient(
    workspace_id="550e8400-e29b-41d4-a716-446655440000",
    platform_url="https://your-platform.example.com",
    agent_card={"name": "my-remote-agent", "skills": []},
)
client.register()            # mints and persists the auth token
secrets = client.pull_secrets()  # returns {"OPENAI_API_KEY": "sk-..."}
client.run_heartbeat_loop()  # background heartbeat + state-poll; detects pause/delete
```

### One-liner bootstrap (poll mode)

```bash
pip install molecule-ai-sdk
WORKSPACE_ID=... PLATFORM_URL=... AGENT_TOKEN=... \
  python -m molecule_agent connect --handler my_module:handle_message
```

Picks `PollDelivery` automatically when no public URL is available — works behind NAT with no inbound firewall holes. `SIGTERM`/`SIGINT` shut the loop down cleanly.

### A2A Delegation

```python
from molecule_agent import RemoteAgentClient

client = RemoteAgentClient(workspace_id="...", platform_url="...")

# Sync: wait for the peer's response
response = client.delegate(peer_workspace_id, "Summarise the Q1 report")

# Async: get a task_id, poll for result
task_id = client.delegate(peer_workspace_id, "Audit the homepage", async_delegate=True)
result = client.check_delegation_status(task_id)
```

### Inbound Messages (Two delivery paths)

**Poll mode** (default, no public URL needed):

```python
from molecule_agent import RemoteAgentClient, PollDelivery

client = RemoteAgentClient(workspace_id="...", platform_url="...")
client.register()

def handle(msg):
    reply = f"Got: {msg.text}"
    client.reply(msg, reply)   # routes to /notify (canvas user) or /a2a (peer)

client.run_agent_loop(handler=handle)  # uses PollDelivery internally
```

**Push mode** (requires a public URL, lower latency):

```python
from molecule_agent import RemoteAgentClient, PushDelivery, A2AServer

server = A2AServer(agent_id="...", inbound_url="https://your-agent.example.com/a2a/inbound")
server.start_in_background()

client = RemoteAgentClient(workspace_id="...", platform_url="...")
client.reported_url = server.inbound_url  # register with public URL
client.register()
client.run_agent_loop(handler=handle, delivery=PushDelivery(client, server))
```

### Plugin Install

Agents can install plugins from the platform registry at runtime:

```python
client.install_plugin(source="local://my-plugin")
# or from a tarball
client.install_plugin_from_tarball("/path/to/plugin.tar.gz", expected_sha256="...")
```

### All public exports

```python
from molecule_agent import (
    RemoteAgentClient,   # Main entry point
    A2AServer,           # Push-mode inbound HTTP server
    PollDelivery,        # Default poll-mode delivery
    PushDelivery,        # Push-mode delivery (needs public URL)
    InboundMessage,      # Inbound message object
    MessageHandler,      # Handler callable signature
    WorkspaceState,      # Pause / running / deleted
    PeerInfo,            # Peer workspace metadata
    compute_plugin_sha256,
    verify_plugin_sha256,
)
```

See `examples/remote-agent/run.py` for a full runnable demo.

---

## molecule_plugin — Plugin Authoring SDK

A Molecule AI plugin is a directory that bundles rules, skills, and per-runtime install adaptors. Any plugin that conforms to this contract is installable on any Molecule AI workspace whose runtime supports it.

### Quick Start

```bash
# Clone the template
cp -r template/ my-plugin/
# Edit my-plugin/plugin.yaml, rules/, skills/, adapters/
```

Validate:

```python
from molecule_plugin import validate_manifest
errors = validate_manifest("my-plugin/plugin.yaml")
assert not errors, errors
```

### CLI

```bash
python -m molecule_plugin validate plugin     my-plugin/
python -m molecule_plugin validate workspace    workspace-configs-templates/claude-code-default/
python -m molecule_plugin validate org          org-templates/molecule-dev/
python -m molecule_plugin validate channel     channels.yaml
```

Exit code 0 when valid, 1 when errors found — suitable for CI. Add `-q` / `--quiet` to suppress success lines.

### Writing a Custom Adaptor

The default `AgentskillsAdaptor` handles rules + skills. Write a custom adaptor when you need to:

- Register runtime tools dynamically — `ctx.register_tool(name, fn)`
- Register DeepAgents sub-agents — `ctx.register_subagent(name, spec)`
- Write to a non-standard memory file — `ctx.append_to_memory(filename, content)`

```python
from molecule_plugin import InstallContext, InstallResult

class Adaptor:
    def __init__(self, plugin_name: str, runtime: str):
        self.plugin_name, self.runtime = plugin_name, runtime

    async def install(self, ctx: InstallContext) -> InstallResult:
        ctx.register_subagent("my-agent", {"prompt": "...", "tools": [...]})
        return InstallResult(plugin_name=self.plugin_name, runtime=self.runtime, source="plugin")

    async def uninstall(self, ctx: InstallContext) -> None:
        pass
```

### Resolution order

For `(plugin_name, runtime)`:

1. **Platform registry** — curated, set by the Molecule AI team
2. **Plugin-shipped** — `<plugin_root>/adapters/<runtime>.py` (what this SDK helps you build)
3. **Raw-drop fallback** — copies files, no tools wired

### Testing locally

```python
import asyncio
from pathlib import Path
from molecule_plugin import AgentskillsAdaptor, InstallContext

ctx = InstallContext(
    configs_dir=Path("/tmp/configs"),
    workspace_id="local",
    runtime="claude_code",
    plugin_root=Path("./my-plugin"),
)
asyncio.run(AgentskillsAdaptor("my-plugin", "claude_code").install(ctx))
# check /tmp/configs/CLAUDE.md, /tmp/configs/skills/
```

### Supported runtimes

`claude_code`, `deepagents`, `langgraph`, `crewai`, `autogen`, `openclaw`. See the live list:

```bash
curl "$PLATFORM_URL/plugins"
```

---

## Both packages

- **Python:** `>=3.11`, no external async dependencies in `molecule_agent`
  (uses blocking `requests` so it embeds in any event loop).
- **Error handling:** Network errors in loops are logged and swallowed so a
  transient platform hiccup does not take a remote agent offline. API-level
  errors (4xx) propagate via `raise_for_status()`.
- **Token security:** Auth token cached at `~/.molecule/<workspace_id>/.auth_token`
  with `0600` permissions.
- **Full documentation:** See `CLAUDE.md` for architecture, platform API
  endpoints, SDK conventions, and known issues.
