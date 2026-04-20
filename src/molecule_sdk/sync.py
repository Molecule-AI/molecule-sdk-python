"""Synchronous (blocking) wrappers around the async workspace API.

All functions in this module are regular (non-``async``) functions that
delegate to their counterparts in :mod:`molecule_sdk.workspace` using
:func:`anyio.to_thread.run_sync`. They are intended for use in contexts where
``await`` is not available, such as synchronous scripts or non-async frameworks.

Usage
-----
.. code-block:: python

    from molecule_sdk import MoleculeClient

    client = MoleculeClient()
    peers = client.list_peers()
    print(peers)

Thread safety
-------------
Each call spawns a worker thread with its own event loop, so concurrent
synchronous calls are safely serialised. For high-throughput scenarios prefer
the async :class:`AsyncMoleculeClient` directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anyio.to_thread

from molecule_sdk.models import (
    A2AMessage,
    AsyncTaskRef,
    DelegationResponse,
    PeerInfo,
)

if TYPE_CHECKING:
    pass

logger: logging.Logger = logging.getLogger("molecule_sdk")


def _run_sync(coro: Any) -> Any:
    """Run an async coroutine in a blocking thread and return its result.

    anyio.to_thread.run_sync accepts an awaitable as its first positional
    argument, so we pass the coroutine directly without an extra lambda.
    """
    return anyio.to_thread.run_sync(coro)


class MoleculeClient:
    """Synchronous client for the Molecule workspace API.

    This is a thin wrapper around :mod:`molecule_sdk.workspace` that converts
    every async function to its blocking equivalent using
    :func:`anyio.to_thread.run_sync`. Instantiate it directly; no ``async with``
    or ``await`` is required.

    Parameters
    ----------
    api_key:
        Optional override for ``MOL_API_KEY``. If omitted the environment
        variable is used (as documented in :func:`molecule_sdk._client.auth_headers`).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
    ) -> None:
        if api_key is not None:
            import os

            os.environ["MOL_API_KEY"] = api_key
        logger.debug("MoleculeClient initialised (sync wrapper)")

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def list_peers(self) -> list[PeerInfo]:
        """List all active peer workspaces. See :func:`workspace.list_peers`."""
        from molecule_sdk import workspace as _aw

        return _run_sync(_aw.list_peers())

    def discover_peer(self, workspace_id: str) -> PeerInfo:
        """Look up a single peer by workspace ID.

        Parameters
        ----------
        workspace_id:
            The unique identifier of the peer workspace.

        See Also
        --------
        workspace.discover_peer
        """
        from molecule_sdk import workspace as _aw

        return _run_sync(_aw.discover_peer(workspace_id))

    # ------------------------------------------------------------------
    # Delegation
    # ------------------------------------------------------------------

    def delegate(
        self,
        workspace_id: str,
        task: str,
        *,
        async_mode: bool = False,
    ) -> DelegationResponse | AsyncTaskRef:
        """Delegate a task to a remote workspace.

        Parameters
        ----------
        workspace_id:
            The ID of the target workspace.
        task:
            Natural-language task description.
        async_mode:
            If True, return an :class:`AsyncTaskRef` immediately rather than
            blocking until completion.

        See Also
        --------
        workspace.delegate
        """
        from molecule_sdk import workspace as _aw

        return _run_sync(
            _aw.delegate(workspace_id, task, async_mode=async_mode)
        )

    # ------------------------------------------------------------------
    # A2A Messaging
    # ------------------------------------------------------------------

    def send_message(
        self,
        workspace_id: str,
        message: A2AMessage,
    ) -> A2AMessage:
        """Send an A2A message to a target workspace.

        See :func:`workspace.send_message` for details.
        """
        from molecule_sdk import workspace as _aw

        return _run_sync(_aw.send_message(workspace_id, message))

    # ------------------------------------------------------------------
    # Task status
    # ------------------------------------------------------------------

    def task_status(self, task_id: str) -> AsyncTaskRef:
        """Poll the status of an async task.

        See :func:`workspace.task_status` for details.
        """
        from molecule_sdk import workspace as _aw

        return _run_sync(_aw.task_status(task_id))
