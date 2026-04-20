"""Workspace API — delegation, peer discovery, and registry access.

All functions are async and use the shared :class:`httpx.AsyncClient` from
:mod:`molecule_sdk._client`.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from molecule_sdk import _client
from molecule_sdk.errors import MoleculeAPIError
from molecule_sdk.models import (
    A2AMessage,
    AsyncTaskRef,
    DelegationRequest,
    DelegationResponse,
    PeerInfo,
)

logger: logging.Logger = logging.getLogger("molecule_sdk")

# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------


def _parse_json(response: _client.httpx.Response, model: type[BaseModel]) -> Any:
    """Parse a successful JSON response into a Pydantic model.

    Parameters
    ----------
    response:
        A successful (2xx) httpx response.
    model:
        Pydantic model class to deserialise into.

    Returns
    -------
    The deserialised model instance.

    Raises
    ------
    MoleculeAPIError
        If the response body cannot be parsed as JSON or does not conform to
        the expected schema.
    """
    try:
        return model.model_validate(response.json())
    except Exception as exc:  # pragma: no cover — caught at a higher level in tests
        raise MoleculeAPIError(
            f"Failed to parse response as {model.__name__}: {exc}",
            status_code=response.status_code,
            response=response.json() if response.text else {},
        ) from exc


# ---------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------


async def list_peers() -> list[PeerInfo]:
    """Return the list of active peer workspaces registered with the platform.

    Returns
    -------
    list[PeerInfo]
        All peer workspaces currently known to the platform registry.

    Raises
    ------
    MoleculeAPIError
        If the platform returns a non-2xx response.

    Note
    ----
    Under high workspace churn the returned list may contain stale entries.
    See ``known-issues.md`` (KI-005 / issue #sdk-115).
    """
    logger.debug("Fetching peer list from registry")
    response = await _client.request("GET", "/registry/peers")
    peers: list[PeerInfo] = [PeerInfo.model_validate(p) for p in response.json()]
    logger.debug("Retrieved %d peers", len(peers))
    return peers


async def discover_peer(workspace_id: str) -> PeerInfo:
    """Look up a single peer workspace by its workspace ID.

    Parameters
    ----------
    workspace_id:
        The unique identifier of the peer workspace to look up.

    Returns
    -------
    PeerInfo
        Information about the discovered peer.

    Raises
    ------
    MoleculeAPIError
        If the platform returns a non-2xx response or no peer with the given
        ID exists.
    """
    logger.debug("Discovering peer workspace_id=%s", workspace_id)
    response = await _client.request("GET", f"/registry/discover/{workspace_id}")
    return _parse_json(response, PeerInfo)


# ---------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------


async def delegate(
    workspace_id: str,
    task: str,
    *,
    async_mode: bool = False,
) -> DelegationResponse | AsyncTaskRef:
    """Delegate a task to a remote workspace via the A2A protocol.

    Parameters
    ----------
    workspace_id:
        The ID of the target workspace that should execute the task.
    task:
        Natural-language description of the task to delegate.
    async_mode:
        If False (the default), block until the task completes and return the
        result. If True, return immediately with an :class:`AsyncTaskRef` that
        can be used with :func:`task_status` to poll for completion.

    Returns
    -------
    DelegationResponse
        When ``async_mode=False`` and the task has finished.
    AsyncTaskRef
        When ``async_mode=True``; poll :func:`task_status` for the result.

    Raises
    ------
    MoleculeAPIError
        If the platform returns a non-2xx response.
    """
    logger.debug(
        "Delegating task to workspace_id=%s (async_mode=%s)",
        workspace_id,
        async_mode,
    )
    payload = DelegationRequest(
        task=task,
        async_mode=async_mode,
        workspace_id=workspace_id,
    )
    response = await _client.request(
        "POST",
        "/a2a/delegate",
        json=payload.model_dump(mode="json"),
    )

    if async_mode:
        return _parse_json(response, AsyncTaskRef)
    return _parse_json(response, DelegationResponse)


# ---------------------------------------------------------------------
# A2A Messaging
# ---------------------------------------------------------------------


async def send_message(
    workspace_id: str,
    message: A2AMessage,
) -> A2AMessage:
    """Send an A2A message to a target workspace.

    Parameters
    ----------
    workspace_id:
        The ID of the receiving workspace.
    message:
        The :class:`A2AMessage` to send. The ``recipient`` field of the message
        is set to ``workspace_id`` if it is currently unset.

    Returns
    -------
    A2AMessage
        The message as acknowledged by the platform, with ``message_id`` and
        ``sent_at`` populated.

    Raises
    ------
    MoleculeAPIError
        If the platform returns a non-2xx response.
    """
    msg = message.model_copy(deep=True)
    if not msg.recipient:
        msg.recipient = workspace_id

    logger.debug(
        "Sending A2A message (type=%s) to workspace_id=%s",
        msg.message_type,
        workspace_id,
    )
    response = await _client.request(
        "POST",
        "/a2a/send",
        json=msg.model_dump(mode="json", exclude_none=True),
    )
    return _parse_json(response, A2AMessage)


# ---------------------------------------------------------------------
# Task status
# ---------------------------------------------------------------------


async def task_status(task_id: str) -> AsyncTaskRef:
    """Poll the status of an asynchronous task by its ID.

    Parameters
    ----------
    task_id:
        The unique identifier of the task to query.

    Returns
    -------
    AsyncTaskRef
        The current state of the task, including its status and creation
        timestamp.

    Raises
    ------
    MoleculeAPIError
        If the platform returns a non-2xx response.
    """
    logger.debug("Polling task status task_id=%s", task_id)
    response = await _client.request("GET", f"/a2a/tasks/{task_id}")
    return _parse_json(response, AsyncTaskRef)
