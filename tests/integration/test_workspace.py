"""Integration tests for :mod:`molecule_sdk.workspace`.

These tests exercise the full request pipeline (Pydantic serialisation,
httpx transport, error handling) using a mock httpx transport so no live
platform is required. They complement the unit tests in ``tests/unit/`` which
mock at the ``_client.request`` level.

To run against a live platform, use ``pytest tests/integration/`` with
``MOL_PLATFORM_URL`` and ``MOL_API_KEY`` set. Integration tests are skipped
automatically if the platform is unreachable.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from molecule_sdk import workspace as _w
from molecule_sdk.errors import MoleculeAPIError, MoleculeConfigError


# ---------------------------------------------------------------------------
# Mock transport helpers
# ---------------------------------------------------------------------------

# Store original module refs so we can restore after each test.
_original_request = _w._client.request


def _make_response(
    status_code: int,
    json_data: object,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a fake httpx.Response with the given status and JSON body."""
    raw = MagicMock(spec=httpx.Response)
    raw.status_code = status_code
    raw.is_success = 200 <= status_code < 300
    raw.headers = httpx.Headers(headers or {})
    raw.json.return_value = json_data
    raw.text = json.dumps(json_data)
    return raw


@asynccontextmanager
async def _mocked_client(
    func: AsyncMock,
) -> AsyncIterator[None]:
    """Patch httpx so that _client.request's own error-checking logic runs.

    Previous approach replaced ``_client.request`` directly, which bypassed
    its ``if not response.is_success: raise MoleculeAPIError`` guard — making
    error-path tests useless.  This version patches ``httpx.AsyncClient.request``
    at source so that every call to the real client goes through the mocked
    transport and ``_client.request`` code is fully exercised.

    ``func`` (the AsyncMock passed in) is set as the ``side_effect`` of the
    httpx patch so that ``mock.assert_awaited_once_with(...)`` in the tests
    records the actual call arguments.
    """
    # Ensure a fresh client is created so it picks up the httpx patch.
    _w._client._CLIENT = None
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_httpx:
        # side_effect makes the original mock record calls and return its own value.
        mock_httpx.side_effect = func
        yield


# ---------------------------------------------------------------------------
# Health / connectivity check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_platform_reachable() -> None:
    """Smoke test: the platform is reachable at the configured base URL.

    Skipped automatically if ``MOL_PLATFORM_URL`` is not set or the host
    is unreachable.
    """
    base_url = _w._client._BASE_URL or "http://platform:8080"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base_url}/health")
        # We don't assert 200 — a 404 is fine; it means the host is up.
        assert response.status_code in (200, 404)
    except (httpx.ConnectError, httpx.RemoteProtocolError, OSError):
        pytest.skip(f"Platform not reachable at {base_url}")


# ---------------------------------------------------------------------------
# list_peers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_peers_empty() -> None:
    """``list_peers`` should return an empty list when no peers are registered."""
    now = datetime.now(timezone.utc).isoformat()
    mock = AsyncMock(
        return_value=_make_response(
            200,
            [],  # empty peer list
            headers={"content-type": "application/json"},
        )
    )
    async with _mocked_client(mock):
        peers = await _w.list_peers()
    assert peers == []
    # authenticated=True is converted to an Authorization header inside
    # _client.request; assert on the resulting headers instead.
    mock.assert_awaited_once_with(
        "GET", "/registry/peers", headers={"Authorization": "Bearer test-api-key-abc123"}
    )


@pytest.mark.asyncio
async def test_list_peers_multiple() -> None:
    """``list_peers`` should parse each peer into a ``PeerInfo`` model."""
    now = datetime.now(timezone.utc).isoformat()
    mock = AsyncMock(
        return_value=_make_response(
            200,
            [
                {
                    "workspace_id": "ws-alpha",
                    "name": "Alpha",
                    "endpoint": "https://alpha.example.com/a2a",
                    "status": "active",
                    "registered_at": now,
                },
                {
                    "workspace_id": "ws-beta",
                    "name": "Beta",
                    "endpoint": "https://beta.example.com/a2a",
                    "status": "draining",
                    "registered_at": now,
                },
                {
                    "workspace_id": "ws-ghost",
                    "name": "Ghost",
                    "endpoint": "https://ghost.example.com/a2a",
                    "status": "offline",
                    "registered_at": now,
                },
            ],
        )
    )
    async with _mocked_client(mock):
        peers = await _w.list_peers()
    assert len(peers) == 3
    assert peers[0].workspace_id == "ws-alpha"
    assert peers[0].status.value == "active"
    assert peers[1].status.value == "draining"
    assert peers[2].status.value == "offline"


@pytest.mark.asyncio
async def test_list_peers_5xx_raises_api_error() -> None:
    """A 503 from the platform should raise :class:`MoleculeAPIError` with code 503."""
    mock = AsyncMock(
        return_value=_make_response(
            503,
            {"error": "Service Unavailable", "retry_after": 30},
        )
    )
    async with _mocked_client(mock):
        with pytest.raises(MoleculeAPIError) as exc_info:
            await _w.list_peers()
    assert exc_info.value.status_code == 503
    assert "retry_after" in exc_info.value.response


@pytest.mark.asyncio
async def test_list_peers_401_raises_api_error() -> None:
    """A 401 should raise :class:`MoleculeAPIError` with code 401."""
    mock = AsyncMock(
        return_value=_make_response(
            401,
            {"error": "Invalid or expired API key"},
        )
    )
    async with _mocked_client(mock):
        with pytest.raises(MoleculeAPIError) as exc_info:
            await _w.list_peers()
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# discover_peer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_peer_ok() -> None:
    """``discover_peer`` should return the resolved ``PeerInfo``."""
    now = datetime.now(timezone.utc).isoformat()
    mock = AsyncMock(
        return_value=_make_response(
            200,
            {
                "workspace_id": "ws-xyz",
                "name": "XYZ Workspace",
                "endpoint": "https://xyz.example.com/a2a",
                "status": "active",
                "registered_at": now,
            },
        )
    )
    async with _mocked_client(mock):
        peer = await _w.discover_peer("ws-xyz")
    assert peer.workspace_id == "ws-xyz"
    assert peer.name == "XYZ Workspace"
    # authenticated=True (the default) is converted to an Authorization header
    # inside _client.request; assert on the resulting headers instead.
    mock.assert_awaited_once_with(
        "GET",
        "/registry/discover/ws-xyz",
        headers={"Authorization": "Bearer test-api-key-abc123"},
    )


@pytest.mark.asyncio
async def test_discover_peer_not_found() -> None:
    """A 404 should raise :class:`MoleculeAPIError` with the platform error body."""
    mock = AsyncMock(
        return_value=_make_response(
            404,
            {"error": "workspace not found", "workspace_id": "ws-nonexistent"},
        )
    )
    async with _mocked_client(mock):
        with pytest.raises(MoleculeAPIError) as exc_info:
            await _w.discover_peer("ws-nonexistent")
    assert exc_info.value.status_code == 404
    assert "not found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# delegate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delegate_sync_completed() -> None:
    """Sync delegation returning a completed result should deserialize correctly."""
    mock = AsyncMock(
        return_value=_make_response(
            200,
            {
                "task_id": "task-1",
                "status": "completed",
                "result": {"output": "All done!"},
                "error": None,
            },
        )
    )
    async with _mocked_client(mock):
        result = await _w.delegate("ws-alpha", "Run the thing")
    assert result.task_id == "task-1"
    # Completed sync response is DelegationResponse (not AsyncTaskRef)
    assert hasattr(result, "result")
    # Verify the request body
    call_kwargs = mock.await_args
    payload = call_kwargs.kwargs["json"]
    assert payload["task"] == "Run the thing"
    assert payload["async_mode"] is False
    assert payload["workspace_id"] == "ws-alpha"


@pytest.mark.asyncio
async def test_delegate_async_returns_pending_ref() -> None:
    """Async delegation should return an ``AsyncTaskRef`` for polling."""
    now = datetime.now(timezone.utc).isoformat()
    mock = AsyncMock(
        return_value=_make_response(
            200,
            {
                "task_id": "task-async-1",
                "status": "pending",
                "created_at": now,
            },
        )
    )
    async with _mocked_client(mock):
        ref = await _w.delegate("ws-alpha", "Background work", async_mode=True)
    assert ref.task_id == "task-async-1"
    assert ref.status.value == "pending"


@pytest.mark.asyncio
async def test_delegate_sync_failed() -> None:
    """A failed sync delegation should return ``DelegationResponse`` with an error."""
    mock = AsyncMock(
        return_value=_make_response(
            200,
            {
                "task_id": "task-fail-1",
                "status": "failed",
                "result": None,
                "error": "Target workspace unreachable",
            },
        )
    )
    async with _mocked_client(mock):
        result = await _w.delegate("ws-dead", "Try this")
    assert result.task_id == "task-fail-1"
    assert result.error == "Target workspace unreachable"


@pytest.mark.asyncio
async def test_delegate_500_raises_api_error() -> None:
    """A 500 from the platform should propagate as :class:`MoleculeAPIError`."""
    mock = AsyncMock(
        return_value=_make_response(
            500,
            {"error": "Internal error in task scheduler"},
        )
    )
    async with _mocked_client(mock):
        with pytest.raises(MoleculeAPIError) as exc_info:
            await _w.delegate("ws-alpha", "Do work")
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_ok() -> None:
    """``send_message`` should return the platform-annotated ``A2AMessage``."""
    now = datetime.now(timezone.utc).isoformat()
    mock = AsyncMock(
        return_value=_make_response(
            200,
            {
                "sender": "ws-local",
                "recipient": "ws-remote",
                "message_type": "tool_result",
                "payload": {"tool": "fetch", "status": 200},
                "message_id": "msg-42",
                "sent_at": now,
            },
        )
    )
    from molecule_sdk.models import A2AMessage

    msg = A2AMessage(
        sender="ws-local",
        recipient="ws-remote",
        message_type="tool_result",
        payload={"tool": "fetch", "status": 200},
    )
    async with _mocked_client(mock):
        ack = await _w.send_message("ws-remote", msg)
    assert ack.message_id == "msg-42"
    assert ack.sent_at is not None
    # Recipient should have been filled from the call arg if unset
    assert ack.recipient == "ws-remote"


@pytest.mark.asyncio
async def test_send_message_recipient_filled_from_arg() -> None:
    """If ``A2AMessage.recipient`` is unset, it should be filled from the call arg."""
    now = datetime.now(timezone.utc).isoformat()
    mock = AsyncMock(
        return_value=_make_response(
            200,
            {
                "sender": "ws-local",
                "recipient": "ws-remote",
                "message_type": "ping",
                "payload": {},
                "message_id": "msg-99",
                "sent_at": now,
            },
        )
    )
    from molecule_sdk.models import A2AMessage

    # recipient not set on the message object
    msg = A2AMessage(
        sender="ws-local",
        recipient="",  # empty
        message_type="ping",
        payload={},
    )
    async with _mocked_client(mock):
        await _w.send_message("ws-remote", msg)
    # Verify the platform received the correct recipient
    call_kwargs = mock.await_args
    payload = call_kwargs.kwargs["json"]
    assert payload["recipient"] == "ws-remote"


# ---------------------------------------------------------------------------
# task_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_status_pending() -> None:
    """``task_status`` for a pending task should return ``AsyncTaskRef`` with status pending."""
    now = datetime.now(timezone.utc).isoformat()
    mock = AsyncMock(
        return_value=_make_response(
            200,
            {
                "task_id": "task-7",
                "status": "pending",
                "created_at": now,
            },
        )
    )
    async with _mocked_client(mock):
        ref = await _w.task_status("task-7")
    assert ref.task_id == "task-7"
    assert ref.status.value == "pending"


@pytest.mark.asyncio
async def test_task_status_completed() -> None:
    """``task_status`` for a completed task should return ``COMPLETED``."""
    now = datetime.now(timezone.utc).isoformat()
    mock = AsyncMock(
        return_value=_make_response(
            200,
            {
                "task_id": "task-7",
                "status": "completed",
                "created_at": now,
            },
        )
    )
    async with _mocked_client(mock):
        ref = await _w.task_status("task-7")
    assert ref.status.value == "completed"


@pytest.mark.asyncio
async def test_task_status_not_found() -> None:
    """Polling a task ID that has expired should return 404."""
    mock = AsyncMock(
        return_value=_make_response(
            404,
            {"error": "task not found or expired", "task_id": "task-dead"},
        )
    )
    async with _mocked_client(mock):
        with pytest.raises(MoleculeAPIError) as exc_info:
            await _w.task_status("task-dead")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# auth errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_api_key_raises_config_error() -> None:
    """If MOL_API_KEY is unset, any request should raise :class:`MoleculeConfigError`."""
    import os

    old_key = os.environ.pop("MOL_API_KEY", None)
    try:
        mock = AsyncMock(
            return_value=_make_response(200, []),
        )
        async with _mocked_client(mock):
            with pytest.raises(MoleculeConfigError) as exc_info:
                await _w.list_peers()
        assert "MOL_API_KEY" in str(exc_info.value)
    finally:
        if old_key is not None:
            os.environ["MOL_API_KEY"] = old_key
