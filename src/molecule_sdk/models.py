"""Pydantic v2 models for the Molecule platform API.

All models are dataclass-style :class:`pydantic.BaseModel` subclasses.
Use :func:`model_validate` / :meth:`model_validate_json` for parsing,
and :meth:`model_dump` / :meth:`model_dump_json` for serialisation.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PeerStatus(str, Enum):
    """Liveness state of a peer workspace."""

    ACTIVE = "active"
    DRAINING = "draining"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class WorkspaceInfo(BaseModel):
    """Summary information about a Molecule workspace.

    Attributes
    ----------
    workspace_id:
        Unique identifier for the workspace.
    name:
        Human-readable workspace name.
    status:
        Current operational status.
    created_at:
        ISO 8601 timestamp when the workspace was created.
    """

    workspace_id: str = Field(description="Unique identifier for the workspace.")
    name: str = Field(description="Human-readable workspace name.")
    status: str = Field(description="Current operational status of the workspace.")
    created_at: datetime = Field(description="ISO 8601 creation timestamp.")


class PeerInfo(BaseModel):
    """Information about a peer workspace registered in the platform registry.

    Attributes
    ----------
    workspace_id:
        Unique identifier of the peer workspace.
    name:
        Human-readable name of the peer.
    endpoint:
        The A2A endpoint URL at which the peer receives messages.
    status:
        Current liveness state of the peer.
    registered_at:
        ISO 8601 timestamp when the peer registered with the platform.
    """

    workspace_id: str = Field(description="Unique identifier of the peer workspace.")
    name: str = Field(description="Human-readable name of the peer.")
    endpoint: str = Field(description="A2A endpoint URL of the peer.")
    status: PeerStatus = Field(description="Current liveness state of the peer.")
    registered_at: datetime = Field(
        description="ISO 8601 timestamp of peer registration."
    )


class DelegationRequest(BaseModel):
    """Request payload for delegating a task to a remote workspace.

    Attributes
    ----------
    task:
        Natural-language description of the task to delegate.
    async_mode:
        If True, the delegation returns immediately with a task reference.
        If False, the caller blocks until the task completes (synchronous mode).
    workspace_id:
        Optional ID of the calling workspace. Set automatically by the platform
        when omitted.
    """

    task: str = Field(description="Natural-language description of the task.")
    async_mode: bool = Field(
        default=False,
        description="If True, return immediately with an async task reference.",
    )
    workspace_id: str | None = Field(
        default=None,
        description="ID of the calling workspace. Platform fills this if omitted.",
    )


class DelegationResponse(BaseModel):
    """Response returned after a delegation request.

    In synchronous mode (``async_mode=False``) the result is included directly.
    In asynchronous mode the caller must poll :class:`AsyncTaskRef` for completion.

    Attributes
    ----------
    task_id:
        Unique identifier for the delegated task.
    status:
        Shortcut to the current task status string.
    result:
        The completed result, present only when the task has finished.
    error:
        Error message, present only when the task failed.
    """

    task_id: str = Field(description="Unique identifier for the delegated task.")
    status: str = Field(
        description="Current status of the task (e.g. 'running', 'completed')."
    )
    result: Any = Field(
        default=None,
        description="Completed result. Present only when status is 'completed'.",
    )
    error: str | None = Field(
        default=None,
        description="Error message. Present only when status is 'failed'.",
    )


class TaskStatus(str, Enum):
    """Possible states of an asynchronous task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AsyncTaskRef(BaseModel):
    """Lightweight reference to a long-running async task.

    Attributes
    ----------
    task_id:
        Unique identifier for the task. Use :func:`task_status` to poll for
        completion.
    status:
        Current :class:`TaskStatus`.
    created_at:
        ISO 8601 timestamp when the task was created.
    """

    task_id: str = Field(description="Unique identifier for the task.")
    status: TaskStatus = Field(description="Current task state.")
    created_at: datetime = Field(description="ISO 8601 creation timestamp.")


class A2AMessage(BaseModel):
    """Schema for an A2A (agent-to-agent) message envelope.

    Attributes
    ----------
    sender:
        Workspace ID of the sending agent.
    recipient:
        Workspace ID of the receiving agent.
    message_type:
        Discriminator string for the message payload type.
    payload:
        Arbitrary JSON-serialisable payload.
    message_id:
        Unique identifier assigned by the platform.
    sent_at:
        ISO 8601 timestamp set by the platform on send.
    """

    sender: str = Field(description="Workspace ID of the sending agent.")
    recipient: str = Field(description="Workspace ID of the receiving agent.")
    message_type: str = Field(
        description="Discriminator string identifying the payload type."
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary JSON-serialisable message payload.",
    )
    message_id: str | None = Field(
        default=None,
        description="Platform-assigned message ID. Set on send.",
    )
    sent_at: datetime | None = Field(
        default=None,
        description="ISO 8601 send timestamp. Set by the platform.",
    )
