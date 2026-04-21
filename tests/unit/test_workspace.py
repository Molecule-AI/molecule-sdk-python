"""Unit tests for :mod:`molecule_sdk.workspace`."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import httpx
import pytest

from molecule_sdk.errors import MoleculeAPIError
from molecule_sdk.models import A2AMessage, AsyncTaskRef, PeerInfo, PeerStatus
from molecule_sdk import workspace as _w


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_request() -> Any:
    """Patch :func:`molecule_sdk._client.request` for the duration of a test."""
    with patch("molecule_sdk.workspace._client.request", new_callable=AsyncMock) as m:
        yield m


# ---------------------------------------------------------------------------
# list_peers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_peers_returns_list_of_peer_info(mock_request: AsyncMock) -> None:
    """``list_peers`` should parse the JSON array and return Pydantic models."""
    now = datetime.utcnow()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = [
        {
            "workspace_id": "ws-1",
            "name": "alpha",
            "endpoint": "https://alpha.example.com/a2a",
            "status": "active",
            "registered_at": now.isoformat(),
        },
        {
            "workspace_id": "ws-2",
            "name": "beta",
            "endpoint": "https://beta.example.com/a2a",
            "status": "offline",
            "registered_at": now.isoformat(),
        },
    ]
    mock_request.return_value = mock_response

    peers = await _w.list_peers()

    assert len(peers) == 2
    assert peers[0].workspace_id == "ws-1"
    assert peers[0].status == PeerStatus.ACTIVE
    assert peers[1].status == PeerStatus.OFFLINE


@pytest.mark.asyncio
async def test_list_peers_non_2xx_raises_api_error() -> None:
    """A non-2xx response should raise :class:`MoleculeAPIError`."""
    # Patch httpx.AsyncClient.request at source so that _client.request's
    # own error-checking logic (if not response.is_success) runs and raises
    # MoleculeAPIError instead of the patch swallowing it.
    from unittest.mock import PropertyMock

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.json.return_value = {"error": "boom"}
    # httpx.Response.is_success is a property; use PropertyMock so it returns
    # a real bool (False) rather than a truthy MagicMock child.
    type(mock_response).is_success = PropertyMock(return_value=False)

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_httpx:
        mock_httpx.return_value = mock_response
        with pytest.raises(MoleculeAPIError) as exc_info:
            await _w.list_peers()

        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# discover_peer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_peer_returns_peer_info(mock_request: AsyncMock) -> None:
    """``discover_peer`` should return a single ``PeerInfo`` from the response."""
    now = datetime.utcnow()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "workspace_id": "ws-alpha",
        "name": "Alpha Workspace",
        "endpoint": "https://alpha.example.com/a2a",
        "status": "active",
        "registered_at": now.isoformat(),
    }
    mock_request.return_value = mock_response

    peer = await _w.discover_peer("ws-alpha")

    assert peer.workspace_id == "ws-alpha"
    assert peer.name == "Alpha Workspace"
    assert peer.status == PeerStatus.ACTIVE


@pytest.mark.asyncio
async def test_discover_peer_not_found_raises_api_error(
    mock_request: AsyncMock,
) -> None:
    """A 404 response should raise :class:`MoleculeAPIError` with status 404."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.is_success = False
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    mock_response.json.return_value = {"error": "workspace not found"}
    mock_request.return_value = mock_response

    with pytest.raises(MoleculeAPIError) as exc_info:
        await _w.discover_peer("ws-nonexistent")

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# delegate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_sync_returns_delegation_response(
    mock_request: AsyncMock,
) -> None:
    """``delegate`` in sync mode should return a :class:`DelegationResponse`."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "task_id": "task-42",
        "status": "completed",
        "result": {"answer": 42},
        "error": None,
    }
    mock_request.return_value = mock_response

    result = await _w.delegate("ws-remote", "What is the answer?")
    mock_request.assert_awaited_once()
    call_args = mock_request.await_args
    # positional args: (method, url)
    assert call_args.args[1] == "/a2a/delegate"
    # keyword args: (json=...)
    payload = call_args.kwargs["json"]
    assert payload["task"] == "What is the answer?"
    assert payload["async_mode"] is False
    assert result.task_id == "task-42"


@pytest.mark.asyncio
async def test_delegate_async_returns_async_task_ref(
    mock_request: AsyncMock,
) -> None:
    """``delegate`` in async mode should return an :class:`AsyncTaskRef`."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "task_id": "task-99",
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
    }
    mock_request.return_value = mock_response

    result = await _w.delegate("ws-remote", "do work", async_mode=True)

    assert isinstance(result, AsyncTaskRef)
    assert result.task_id == "task-99"


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_returns_acknowledged_message(
    mock_request: AsyncMock,
) -> None:
    """``send_message`` should return the platform-annotated ``A2AMessage``."""
    msg_id = "msg-abc"
    now = datetime.utcnow()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "sender": "ws-local",
        "recipient": "ws-remote",
        "message_type": "query",
        "payload": {"q": "hello"},
        "message_id": msg_id,
        "sent_at": now.isoformat(),
    }
    mock_request.return_value = mock_response

    message = A2AMessage(
        sender="ws-local",
        recipient="ws-remote",
        message_type="query",
        payload={"q": "hello"},
    )
    result = await _w.send_message("ws-remote", message)

    assert result.message_id == msg_id
    assert result.sent_at is not None


# ---------------------------------------------------------------------------
# task_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_status_returns_async_task_ref(
    mock_request: AsyncMock,
) -> None:
    """``task_status`` should return an :class:`AsyncTaskRef`."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "task_id": "task-7",
        "status": "running",
        "created_at": datetime.utcnow().isoformat(),
    }
    mock_request.return_value = mock_response

    result = await _w.task_status("task-7")

    assert result.task_id == "task-7"
    # authenticated=True is the default; omit it from the assertion
    mock_request.assert_awaited_once_with("GET", "/a2a/tasks/task-7")
