"""molecule-sdk-python — Python SDK for the Molecule AI platform.

Quick start
-----------

Async usage::

    import httpx
    from molecule_sdk import AsyncMoleculeClient
    from molecule_sdk.models import A2AMessage, PeerInfo

    async with httpx.AsyncClient() as http:
        client = AsyncMoleculeClient(http=http)
        peers = await client.list_peers()
        print(peers)

Sync usage::

    from molecule_sdk import MoleculeClient

    client = MoleculeClient()
    peers = client.list_peers()
    print(peers)

Environment variables
---------------------
``MOL_PLATFORM_URL``  Base URL of the platform API (default: ``http://platform:8080``)
``MOL_API_KEY``       API key used for authentication (required)

"""

from __future__ import annotations

from molecule_sdk._version import __version__
from molecule_sdk.errors import (
    MoleculeAPIError,
    MoleculeConfigError,
    MoleculeError,
    MoleculeTimeoutError,
)
from molecule_sdk.models import (
    A2AMessage,
    AsyncTaskRef,
    DelegationRequest,
    DelegationResponse,
    PeerInfo,
    PeerStatus,
    TaskStatus,
    WorkspaceInfo,
)
from molecule_sdk.sync import MoleculeClient
from molecule_sdk.workspace import (
    delegate,
    discover_peer,
    list_peers,
    send_message,
    task_status,
)

# Re-expose httpx so callers can pass in custom client options without
# an extra dependency install.
import httpx as _httpx

__all__ = [
    # Version
    "__version__",
    # Errors
    "MoleculeError",
    "MoleculeConfigError",
    "MoleculeAPIError",
    "MoleculeTimeoutError",
    # Models
    "WorkspaceInfo",
    "PeerInfo",
    "PeerStatus",
    "DelegationRequest",
    "DelegationResponse",
    "TaskStatus",
    "AsyncTaskRef",
    "A2AMessage",
    # Async workspace API (top-level functions)
    "list_peers",
    "discover_peer",
    "delegate",
    "send_message",
    "task_status",
    # Sync wrapper
    "MoleculeClient",
    # Async client class
    "AsyncMoleculeClient",
    # httpx re-export
    "_httpx",
]


class AsyncMoleculeClient:
    """Async-first client providing typed access to the workspace API.

    Parameters
    ----------
    http:
        An :class:`httpx.AsyncClient` instance. If not provided, one is created
        using the default base URL and timeout from :mod:`molecule_sdk._client`.
        When a custom client is supplied the caller is responsible for its
        lifecycle (opening / closing).
    """

    def __init__(
        self,
        http: _httpx.AsyncClient | None = None,
    ) -> None:
        if http is not None:
            self._http = http
            self._owns_client = False
        else:
            from molecule_sdk import _client as _mc

            self._http = _mc.get_client()
            self._owns_client = True
        logger = __import__("logging").getLogger("molecule_sdk")
        logger.debug("AsyncMoleculeClient initialised (owns_client=%s)", self._owns_client)

    async def list_peers(self) -> list[PeerInfo]:
        """List all active peer workspaces. See :func:`list_peers`."""
        from molecule_sdk import workspace as _w

        return await _w.list_peers()

    async def discover_peer(self, workspace_id: str) -> PeerInfo:
        """Look up a peer by workspace ID. See :func:`discover_peer`."""
        from molecule_sdk import workspace as _w

        return await _w.discover_peer(workspace_id)

    async def delegate(
        self,
        workspace_id: str,
        task: str,
        *,
        async_mode: bool = False,
    ) -> DelegationResponse | AsyncTaskRef:
        """Delegate a task to a remote workspace. See :func:`delegate`."""
        from molecule_sdk import workspace as _w

        return await _w.delegate(workspace_id, task, async_mode=async_mode)

    async def send_message(
        self,
        workspace_id: str,
        message: A2AMessage,
    ) -> A2AMessage:
        """Send an A2A message. See :func:`send_message`."""
        from molecule_sdk import workspace as _w

        return await _w.send_message(workspace_id, message)

    async def task_status(self, task_id: str) -> AsyncTaskRef:
        """Poll async task status. See :func:`task_status`."""
        from molecule_sdk import workspace as _w

        return await _w.task_status(task_id)

    async def close(self) -> None:
        """Close the underlying HTTP client if this instance owns it."""
        if self._owns_client:
            from molecule_sdk import _client as _mc

            await _mc.close_client()
