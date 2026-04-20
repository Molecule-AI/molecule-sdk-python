# molecule-sdk-python

<p>
  <a href="https://pypi.org/project/molecule-sdk-python/"><img src="https://img.shields.io/pypi/v/molecule-sdk-python?style=flat-square" alt="PyPI" /></a>
  <a href="https://pypi.org/project/molecule-sdk-python/"><img src="https://img.shields.io/pypi/pyversions/molecule-sdk-python?style=flat-square" alt="Python 3.11+" /></a>
  <a href="https://github.com/Molecule-AI/molecule-sdk-python/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/molecule-sdk-python?style=flat-square" alt="License: MIT" /></a>
</p>

Python SDK for the Molecule AI platform. Interact with workspaces, delegate tasks,
discover peers, send A2A messages, and query the platform registry — from any
Python 3.11+ application.

## Features

- **Async-first** — all I/O is `async def` using `httpx`
- **Sync wrapper** — `MoleculeClient` for non-async callers via `anyio`
- **Typed models** — Pydantic v2 request/response models for every API resource
- **Platform API** — workspace delegation, peer registry, A2A messaging, task polling
- **Auto-auth** — `MOL_API_KEY` injected automatically on every request

## Quick Start

```bash
pip install molecule-sdk-python
```

```python
import os
from molecule_sdk import AsyncMoleculeClient

os.environ["MOL_API_KEY"] = "your-api-key"
os.environ["MOL_PLATFORM_URL"] = "https://api.moleculesai.app"  # optional

client = AsyncMoleculeClient()

# List peer workspaces
peers = await client.workspace.list_peers()
for peer in peers:
    print(peer.name, peer.status)

# Delegate a task
result = await client.workspace.delegate(
    workspace_id="ws-abc123",
    task="Summarise the last 10 commits in repo owner/name",
)
print(result)

await client.close()
```

### Sync usage

```python
from molecule_sdk import MoleculeClient

client = MoleculeClient()
peers = client.workspace.list_peers()
print(peers)
client.close()  # optional; closes the background event loop
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `MOL_PLATFORM_URL` | No | `http://platform:8080` | Platform API base URL |
| `MOL_API_KEY` | Yes | — | API key for authentication |
| `MOL_WORKSPACE_ID` | For delegation | — | ID of the calling workspace |

Set `MOL_API_KEY` before instantiating a client. `MoleculeConfigError` is raised
at the first request if it is missing.

## API Reference

### Workspace API

```python
from molecule_sdk.workspace import list_peers, discover_peer, delegate, send_message, task_status

# List all active peer workspaces
peers: list[PeerInfo] = await list_peers()

# Resolve a workspace ID to endpoint info
peer: PeerInfo = await discover_peer("ws-abc123")

# Delegate synchronously (blocks until result)
response: DelegationResponse = await delegate("ws-abc123", "do the thing")

# Delegate asynchronously (immediate AsyncTaskRef; poll manually)
ref: AsyncTaskRef = await delegate("ws-abc123", "do the thing", async_mode=True)
print(ref.task_id)
status: AsyncTaskRef = await task_status(ref.task_id)

# Send a one-way A2A message
from molecule_sdk.models import A2AMessage
ack = await send_message(
    "ws-abc123",
    A2AMessage(sender="ws-me", recipient="ws-abc123", message_type="tool_result", payload={"tool": "fetch", "url": "..."}),
)
print(ack.message_id, ack.sent_at)
```

### Models

All models are Pydantic v2 `BaseModel` subclasses:

| Model | Description |
|---|---|
| `PeerInfo` | Peer workspace — ID, name, endpoint, status |
| `WorkspaceInfo` | Local workspace summary |
| `DelegationRequest` | Payload for task delegation |
| `DelegationResponse` | Delegation result (sync mode) |
| `AsyncTaskRef` | Async task reference for polling |
| `TaskStatus` | Enum: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED` |
| `A2AMessage` | A2A message envelope |

## Error Handling

```python
from molecule_sdk.errors import MoleculeError, MoleculeAPIError, MoleculeTimeoutError

try:
    result = await delegate("ws-nonexistent", "task")
except MoleculeAPIError as e:
    print(e.status_code)  # e.g. 404
    print(e.response)      # parsed JSON error body
except MoleculeTimeoutError:
    print("Platform did not respond within timeout")
```

## Installation

### From PyPI

```bash
pip install molecule-sdk-python
```

### From source

```bash
git clone https://github.com/Molecule-AI/molecule-sdk-python
cd molecule-sdk-python
pip install -e ".[dev]"
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Lint
ruff check src/molecule_sdk/

# Type check
mypy src/molecule_sdk/

# Run unit tests (mocked HTTP — no platform required)
pytest tests/unit/ -v

# Run integration tests (requires a running platform)
export MOL_API_KEY=your-key
export MOL_PLATFORM_URL=http://localhost:8080
pytest tests/integration/ -v

# Run all tests
pytest tests/ -v

# Build package
python -m build
```

## Release Process

1. Bump `__version__` in `src/molecule_sdk/_version.py`
2. Update `CHANGELOG.md`
3. Tag: `git tag vX.Y.Z && git push --tags`
4. GitHub Actions publishes to PyPI automatically on tag push

## Known Issues

See [`known-issues.md`](./known-issues.md) for open issues including:

- **KI-005** — `httpx` pinned `<1.0` as precaution pending a2a-sdk migration
- **sdk-42** — Snapshot headers not forwarded in test fixtures
- **sdk-71** — No token refresh for rotated API keys
- **sdk-103** — Large delegation payloads may hit platform NGINX limits

## License

MIT © Molecule AI
