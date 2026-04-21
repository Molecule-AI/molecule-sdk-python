# molecule-mcp-server

TypeScript MCP server that exposes the Molecule AI agent platform as tools via the Model Context Protocol (MCP).

## Project Overview

This server acts as a bridge between MCP clients (e.g., Claude Desktop, other MCP-compatible hosts) and the Molecule AI platform. It registers platform capabilities as MCP tools so agents can interact with the platform natively.

## Build and Test

```bash
# Install dependencies
npm install

# Build (TypeScript -> JS, output to dist/)
npm run build

# Run tests (Jest, config in jest.config.cjs)
npm test

# Type check without building
npm run lint    # if present
```

Watch mode for development:

```bash
npm run build -- --watch
```

## MCP Tool Conventions

All tools follow these conventions to ensure consistent behavior across the server.

### Naming

- Tool names: `snake_case` (e.g., `list_workspaces`, `create_agent`)
- Resource names: `camelCase` prefixed by type (e.g., `workspace:default`)
- Always use present tense imperatives for actions (list, create, delete, not `listing`)

### Error Codes

Use structured errors with known codes — never throw plain strings:

| Code | Meaning |
|------|---------|
| `TOOL_NOT_FOUND` | Tool/resource name not registered |
| `INVALID_ARGUMENTS` | Arguments failed schema validation |
| `PLATFORM_ERROR` | Upstream platform API error |
| `AUTH_ERROR` | Authentication/authorization failure |
| `RATE_LIMITED` | Platform rate limit hit |
| `INTERNAL_ERROR` | Unexpected server-side failure |

All tool responses wrap errors in the MCP `error` shape — never return error text as a plain string in `content`.

### Streaming Behavior

- If a tool supports streaming, declare it in the tool manifest
- Stream results incrementally via `ContentBlock` chunks — do not buffer and return all at once
- On cancellation, stop emitting and close the stream cleanly (no half-written responses)

### Tool Schema

Every tool must have a JSON Schema (Draft 7) `inputSchema`. Keep it minimal — only expose parameters the server actually uses. Do not mirror the full platform API surface if MCP does not need it.

## Release Process

Releases are automated via GitHub Actions on every tag matching `v*`.

### Cutting a Release

```bash
# Make sure you're on main and all tests pass
git checkout main
git pull

# Bump version in package.json, commit
vim package.json
git add package.json
git commit -m "chore: bump version to x.y.z"

# Tag and push
git tag vx.y.z
git push origin main --tags
```

The workflow:
1. Pushes `v*` tag → triggers `publish.yml` workflow
2. Workflow runs `npm install`, `npm run build`, `npm test`
3. On success: publishes to npm (`npm publish --access public`)
4. Creates a GitHub Release with the tag

**Do not publish manually.** Let the tag push flow handle it.

## Platform Integration

### APIs Connected

The server connects to the Molecule AI platform REST API. See the platform SDK (`../molecule-sdk-python`) for the underlying API client used.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MOLECULE_API_URL` | Yes | Base URL of the Molecule platform API |
| `MOLECULE_API_KEY` | Yes | API key for platform authentication |
| `MCP_SERVER_PORT` | No | Port to run the MCP server on (default: `3000`) |

For local development, copy `.env.example` → `.env` and fill in values.

### Postgres

Platform data lives in Postgres (source of truth). The server reads data via the platform SDK — it does not connect to Postgres directly.

## TypeScript Conventions

### Async Patterns

- Use `async`/`await` throughout — no `.then()` chains except for bridging legacy callback code
- Every handler function is `async`
- Never use `void` async functions unless the MCP spec explicitly requires fire-and-forget

### Error Handling

- Never `console.log` user-facing errors — use structured logging and return MCP errors
- Wrap every tool handler in a `try/catch`; catch errors and re-throw as MCP-structured errors
- Avoid non-Error throws (numbers, strings) — always throw or return `Error` instances

### Typing Standards

- Strict mode is enabled (`"strict": true` in `tsconfig.json`)
- Avoid `any` — use `unknown` and narrow with type guards or Zod validators
- Use `zod` for all external input validation (API args, tool schemas)
- Export types from `src/types/` for shared interfaces

### File Structure

```
src/
  index.ts          # Server entry point
  tools/            # MCP tool implementations
  types/            # Shared TypeScript types
  utils/            # Helpers, validators
```

## MCP Tool Registry

Full list of tools exposed by this server. Each is implemented in `src/tools/<name>.ts`.

### Workspace Tools
| Tool | Description |
|------|-------------|
| `list_workspaces` | List all workspaces accessible to the authenticated user |
| `create_workspace` | Create a new workspace with name, role, tier, and template |
| `get_workspace` | Get workspace details by ID |
| `update_workspace` | Patch workspace fields (name, tier, parent_id, etc.) |
| `delete_workspace` | Delete a workspace (cascades to children) |
| `restart_workspace` | Restart all agents in a workspace (picks up new secrets/prompts) |

### Agent Tools
| Tool | Description |
|------|-------------|
| `list_agents` | List agents in a workspace |
| `get_agent` | Get agent details by ID |
| `send_message` | Send an A2A message to an agent (returns structured response) |
| `list_peers` | List peer agents discoverable by a given agent |

### Delegation Tools
| Tool | Description |
|------|-------------|
| `delegate_task` | Delegate a task to a child workspace (sync, waits for response) |
| `delegate_task_async` | Delegate a task to a child workspace (fire-and-forget, returns task_id) |

### Secrets Tools
| Tool | Description |
|------|-------------|
| `get_secret` | Retrieve a secret value for a workspace |
| `set_secret` | Set a key/value secret for a workspace |
| `delete_secret` | Delete a secret |

### Files Tools
| Tool | Description |
|------|-------------|
| `list_files` | List files in a workspace container |
| `get_file` | Read a file's content |
| `put_file` | Write or update a file in the container |
| `delete_file` | Delete a file |

### Memory Tools
| Tool | Description |
|------|-------------|
| `commit_memory` | Commit a structured memory entry (with optional namespace) |
| `recall_memory` | Search previously committed memories |

### Plugins Tools
| Tool | Description |
|------|-------------|
| `install_plugin` | Download and install a plugin into a workspace from the registry |

### Channels Tools
| Tool | Description |
|------|-------------|
| `list_channels` | List communication channels |
| `get_channel` | Get channel details |
| `post_message` | Post a message to a channel |

### Schedules Tools
| Tool | Description |
|------|-------------|
| `list_schedules` | List scheduled tasks |
| `create_schedule` | Create a new scheduled task |
| `delete_schedule` | Delete a scheduled task |

### Discovery Tools
| Tool | Description |
|------|-------------|
| `check_access` | Verify A2A access between two workspace IDs |

### Remote Agents Tools
| Tool | Description |
|------|-------------|
| `get_remote_agent_info` | Get runtime info for a remote agent |
| `heartbeat` | Send a heartbeat to the platform |

### Approvals Tools
| Tool | Description |
|------|-------------|
| `list_approvals` | List pending approvals for a workspace |
| `approve` | Approve a pending item |
| `reject` | Reject a pending item |

## MCP Transport Gotchas

### STDIO Transport (Claude Desktop, CLI hosts)
- **Windows CORS issue:** STDIO transport does not use HTTP, so CORS is not a factor — but some Claude Desktop configurations on Windows proxy through an HTTP layer that adds CORS headers. If tools fail silently on Windows, check for a proxy intercepting the STDIO stream.
- **STDIO timeout:** STDIO mode has no built-in keepalive. If the MCP host is idle for >5 min, the platform may close the workspace. Send a `heartbeat` tool call every ~3 min from long-running sessions.
- **Windows binary path:** On Windows, the MCP server executable path in Claude Desktop config must use backslashes or forward slashes with escaped backslashes (`\\`) in JSON. Use forward slashes for portability.

### SSE Transport (web hosts)
- **SSE vs STDIO:** SSE (Server-Sent Events) is used when the MCP host connects over HTTP. It supports streaming responses natively. STDIO is for local CLI tools.
- **Heartbeat cleanup:** When using SSE, each tool call opens a new HTTP connection. Ensure the host sends a `close` event when the stream finishes to allow connection reuse. Unterminated SSE streams can hold connections open indefinitely.

### `--self-update` Flag
The server supports a `--self-update` flag for auto-updating:
```bash
mcp-server --self-update
```
**Proxy TLS note:** If the server is behind a corporate proxy, `--self-update` may fail with a TLS handshake error (`UNABLE_TO_VERIFY_LEAF_SIGNATURE`). The proxy intercepts the TLS cert, and the Go/MJS HTTP client rejects it. Fix: set `NODE_EXTRA_CA_CERTS=/path/to/proxy-ca.pem` in the environment, or disable `rejectUnauthorized` for the update endpoint only (do not disable globally).

## Claude Desktop Configuration

Add this server to Claude Desktop via `claude_desktop_config.json`:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Linux:** `~/.config/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "molecule-ai": {
      "command": "node",
      "args": ["/absolute/path/to/dist/index.js"],
      "env": {
        "MOLECULE_API_URL": "https://api.moleculesai.app",
        "MOLECULE_API_KEY": "your-api-key-here",
        "MCP_SERVER_PORT": "3000"
      }
    }
  }
}
```

To find the absolute path to the built binary:
```bash
node dist/index.js --help  # verify path
```

After editing the config, restart Claude Desktop (fully quit, then reopen) to load the new server.

## Known Issues

See `known-issues.md` at the repo root for the full tracked list.
