# molecule-sdk-python

Python SDK for the Molecule AI platform. Provides typed Python clients for:

- **Workspace API** — interact with Molecule workspaces, delegate tasks, query peer status
- **A2A protocol** — send async/sync messages between workspaces
- **Platform API** — org management, skill registry, audit/compliance
- **MCP tools** — consume Molecule tools from any MCP-compatible client

## Purpose

`molecule-sdk-python` is the canonical Python client library for external agents,
integrations, and workspace templates that need to talk to a Molecule platform
instance. It wraps the platform HTTP API and A2A wire protocol with typed models,
async-first helpers, and automatic auth injection.

## Key Conventions

| Topic | Convention |
|---|---|
| **Async** | All I/O is `async def`; sync wrappers are provided at the top level (`sync.py`) |
| **Auth** | API key via `MOL_API_KEY` env var; injected automatically via `platform_auth.auth_headers()` |
| **Base URL** | `MOL_PLATFORM_URL` env var (default: `http://platform:8080`) |
| **Models** | Pydantic v2 `BaseModel` for all request/response types |
| **Error handling** | Custom `MoleculeError` hierarchy; non-2xx responses raise `MoleculeAPIError` |
| **Logging** | `logging.getLogger("molecule_sdk")` — follows molecule-core logging conventions |
| **Type stubs** | Inline type annotations; no `.pyi` stub files |

## Dev Setup

```bash
# Prerequisites
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint / type check
ruff check src/molecule_sdk/
mypy src/molecule_sdk/

# Build the package
python -m build
```

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `MOL_PLATFORM_URL` | No | `http://platform:8080` | Platform API base URL |
| `MOL_API_KEY` | Yes | — | API key for authentication |
| `MOL_WORKSPACE_ID` | For delegation | — | ID of the calling workspace |

## Project Structure

```
src/molecule_sdk/
├── __init__.py          # Public API exports
├── _client.py           # Shared httpx AsyncClient setup + auth
├── workspace.py         # Workspace API (delegate, list_peers, discover)
├── a2a.py               # A2A protocol (send_message, task status)
├── platform.py          # Org/skill/audit platform APIs
├── models.py            # Pydantic models shared across modules
└── errors.py            # Exception hierarchy
tests/
├── unit/
├── integration/
└── conftest.py
```

## Release Process

1. Bump `version` in `src/molecule_sdk/_version.py`
2. Update `CHANGELOG.md` with a human-readable diff
3. Tag: `git tag vX.Y.Z && git push --tags`
4. GitHub Actions publishes to PyPI automatically on tag push

> **Note:** Releases follow [Keep a Changelog](https://keepachangelog.com/) and
> Semantic Versioning. No release from `main` without a tag.

## Known Gotchas

- `MOL_API_KEY` must be set before importing `molecule_sdk` if you use the
  module-level convenience clients — otherwise a `MoleculeConfigError` is raised.
- A2A delegation timeouts are server-side bounded at 300 s; the SDK adds a
  matching read timeout so you won't wait indefinitely.
- Snapshot/scrub headers (`X-Snapshot-*)` are injected by the platform proxy and
  are not part of the public SDK interface — do not rely on them in external code.
- Connection pooling is shared via a module-level `httpx.AsyncClient` in `_client.py`;
  call `await molecule_sdk.close()` on exit to clean up.
