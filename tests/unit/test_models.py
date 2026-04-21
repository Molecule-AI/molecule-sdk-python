"""Unit tests for :mod:`molecule_sdk.models` Pydantic v2 models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

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


# ---------------------------------------------------------------------------
# PeerStatus
# ---------------------------------------------------------------------------


def test_PeerStatus_values() -> None:
    """PeerStatus enum should have the expected members."""
    assert PeerStatus.ACTIVE.value == "active"
    assert PeerStatus.DRAINING.value == "draining"
    assert PeerStatus.OFFLINE.value == "offline"
    assert PeerStatus.UNKNOWN.value == "unknown"


# ---------------------------------------------------------------------------
# PeerInfo
# ---------------------------------------------------------------------------


def test_PeerInfo_valid_minimal() -> None:
    """PeerInfo should parse with only required fields."""
    now = datetime.now(timezone.utc).isoformat()
    peer = PeerInfo(
        workspace_id="ws-abc",
        name="Alpha",
        endpoint="https://alpha.example.com/a2a",
        status=PeerStatus.ACTIVE,
        registered_at=now,
    )
    assert peer.workspace_id == "ws-abc"
    assert peer.name == "Alpha"
    assert peer.endpoint == "https://alpha.example.com/a2a"
    assert peer.status is PeerStatus.ACTIVE


def test_PeerInfo_full() -> None:
    """PeerInfo should accept and store all fields."""
    now = datetime.now(timezone.utc).isoformat()
    peer = PeerInfo(
        workspace_id="ws-xyz",
        name="Beta Workspace",
        endpoint="https://beta.example.com/a2a",
        status=PeerStatus.DRAINING,
        registered_at=now,
    )
    assert peer.workspace_id == "ws-xyz"
    assert peer.status is PeerStatus.DRAINING


def test_PeerInfo_extra_fields_stripped() -> None:
    """PeerInfo should silently drop extra fields (Pydantic v2 default)."""
    now = datetime.now(timezone.utc).isoformat()
    peer = PeerInfo(
        workspace_id="ws-extra",
        name="Extra",
        endpoint="https://extra.example.com/a2a",
        status=PeerStatus.OFFLINE,
        registered_at=now,
        # These fields are not defined on the model and should be ignored.
        extra_garbage="ignored",
        another_field=123,
    )
    # "extra_garbage" should not appear in the model's dict representation.
    assert "extra_garbage" not in peer.model_dump()


# ---------------------------------------------------------------------------
# DelegationRequest
# ---------------------------------------------------------------------------


def test_DelegationRequest_defaults() -> None:
    """DelegationRequest should default async_mode to False."""
    req = DelegationRequest(task="Do something useful")
    assert req.async_mode is False
    assert req.workspace_id is None


# ---------------------------------------------------------------------------
# DelegationResponse
# ---------------------------------------------------------------------------


def test_DelegationResponse_result_none_when_completed() -> None:
    """DelegationResponse with completed status should have result set."""
    resp = DelegationResponse(
        task_id="task-1",
        status="completed",
        result={"data": "the answer"},
    )
    assert resp.result == {"data": "the answer"}


def test_DelegationResponse_error_optional() -> None:
    """DelegationResponse error should be None by default when not failed."""
    resp = DelegationResponse(
        task_id="task-2",
        status="pending",
    )
    assert resp.error is None


# ---------------------------------------------------------------------------
# TaskStatus
# ---------------------------------------------------------------------------


def test_TaskStatus_enum() -> None:
    """TaskStatus should have the expected members."""
    assert TaskStatus.PENDING.value == "pending"
    assert TaskStatus.RUNNING.value == "running"
    assert TaskStatus.COMPLETED.value == "completed"
    assert TaskStatus.FAILED.value == "failed"
    assert TaskStatus.CANCELLED.value == "cancelled"


# ---------------------------------------------------------------------------
# AsyncTaskRef
# ---------------------------------------------------------------------------


def test_AsyncTaskRef_created_at_is_required() -> None:
    """AsyncTaskRef should require created_at to be provided."""
    ts = datetime.now(timezone.utc).isoformat()
    ref = AsyncTaskRef(
        task_id="task-99",
        status=TaskStatus.PENDING,
        created_at=ts,
    )
    assert ref.task_id == "task-99"
    assert ref.status is TaskStatus.PENDING
    assert isinstance(ref.created_at, datetime)


# ---------------------------------------------------------------------------
# A2AMessage
# ---------------------------------------------------------------------------


def test_A2AMessage_defaults() -> None:
    """A2AMessage should require sender/recipient and default payload to {}."""
    msg = A2AMessage(
        sender="ws-local",
        recipient="ws-remote",
        message_type="request",
    )
    assert msg.sender == "ws-local"
    assert msg.recipient == "ws-remote"
    assert msg.payload == {}
    assert msg.message_id is None
    assert msg.sent_at is None


def test_A2AMessage_serialization_roundtrip() -> None:
    """A2AMessage should survive model_dump -> model_validate roundtrip."""
    now = datetime.now(timezone.utc).isoformat()
    original = A2AMessage(
        sender="ws-sender",
        recipient="ws-recipient",
        message_type="response",
        payload={"result": 42},
        message_id="msg-123",
        sent_at=now,
    )
    roundtripped = A2AMessage.model_validate(original.model_dump())
    assert roundtripped.sender == original.sender
    assert roundtripped.recipient == original.recipient
    assert roundtripped.message_type == original.message_type
    assert roundtripped.payload == {"result": 42}
    assert roundtripped.message_id == "msg-123"


# ---------------------------------------------------------------------------
# WorkspaceInfo
# ---------------------------------------------------------------------------


def test_WorkspaceInfo_created_at_datetime() -> None:
    """WorkspaceInfo created_at should be parsed from an ISO datetime string."""
    ts = datetime.now(timezone.utc).isoformat()
    ws = WorkspaceInfo(
        workspace_id="ws-main",
        name="Main Workspace",
        status="running",
        created_at=ts,
    )
    assert isinstance(ws.created_at, datetime)
    assert ws.workspace_id == "ws-main"