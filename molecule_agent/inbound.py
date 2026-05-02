"""Poll-mode inbound delivery for remote agents that can't expose an HTTP endpoint.

The :class:`A2AServer` companion (Phase 30.8b) covers the case where an agent
can host a publicly reachable HTTP endpoint and the platform pushes work to it.
Many real adopters can't — laptops behind NAT, ephemeral CI runners, hermes
self-hosted on a developer machine. For those, the platform queues inbound
A2A messages on the workspace's ``activity_logs`` and the agent polls.

This module provides:

* :class:`InboundMessage` — typed view over an ``activity_logs`` row that
  carries an ``a2a_receive`` event. Source is normalized to ``canvas_user``
  vs ``peer_agent`` so the SDK can route replies without the caller having
  to know which envelope to use.
* :class:`CursorLostError` — raised when the activity endpoint returns
  410 Gone (the cursor's row was rotated out). Caller resets and re-polls.
* :class:`InboundDelivery` — protocol that ``run_agent_loop`` accepts; both
  :class:`PollDelivery` and :class:`PushDelivery` satisfy it.
* :class:`PollDelivery` — the new poll-mode implementation.
* :class:`PushDelivery` — thin wrapper over :class:`A2AServer` so the same
  ``run_agent_loop`` works for push-mode agents that expose an inbound URL.

Big-tech prior art: Slack Socket Mode, Telegram getUpdates, AWS SQS long
polling, Stripe ``stripe listen``. Same shape — cursor-based poll, SDK-owned
loop, single handler callback, smart-reply hidden behind the SDK.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Literal,
    Protocol,
    TYPE_CHECKING,
    runtime_checkable,
)

if TYPE_CHECKING:
    from .client import RemoteAgentClient

logger = logging.getLogger(__name__)


InboundSource = Literal["canvas_user", "peer_agent", "unknown"]


@dataclass
class InboundMessage:
    """One inbound A2A event the agent must handle.

    The ``activity_id`` is the cursor — pass it as ``since_id`` on the next
    fetch to avoid re-receiving this message.

    ``source`` is normalized so the SDK can pick the reply transport:

    * ``canvas_user`` — a user typing in the canvas chat. Reply via
      ``POST /workspaces/:id/notify``.
    * ``peer_agent`` — another workspace's agent. Reply via
      ``POST /workspaces/:peer_id/a2a`` with a JSON-RPC envelope and
      ``X-Source-Workspace-Id`` header.
    * ``unknown`` — the activity row didn't carry a recognizable source.
      :py:meth:`RemoteAgentClient.reply` raises ``ValueError`` rather than
      guess.
    """

    activity_id: str
    source: InboundSource
    source_id: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)

    # Enrichment fields landed on the platform push envelope on 2026-05-02
    # (CP PRs #2472, #2476). The platform resolves these from the registry
    # at push time; on registry-lookup failure they may be empty strings.
    # Empty-string default keeps consumers branch-free — no key-error guards
    # needed when the field is absent.
    peer_name: str = ""
    peer_role: str = ""
    agent_card_url: str = ""


class CursorLostError(Exception):
    """Raised when ``GET /workspaces/:id/activity`` returns 410 Gone.

    The platform retires old activity rows on a fixed window (see
    workspace-server's activity_logs retention policy). If the agent's
    cursor points at a row that has been rotated out, the server replies
    410. Callers should reset the cursor (``since_id=None``) and re-poll;
    they will catch up on whatever's still in the window.
    """


# ---------------------------------------------------------------------------
# Activity row → InboundMessage parsing
# ---------------------------------------------------------------------------


def _parse_activity_row(row: dict[str, Any]) -> InboundMessage | None:
    """Convert one ``activity_logs`` row into an :class:`InboundMessage`.

    Returns ``None`` if the row is malformed or doesn't carry text we can
    deliver — preferable to raising and aborting the whole poll batch.

    Activity row shape (per workspace-server's handlers/activity.go):
    ``{"id": ..., "type": "a2a_receive", "source_id": ..., "data": {...}, ...}``
    """
    aid = str(row.get("id") or "")
    if not aid:
        return None

    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    source_kind = str(data.get("source") or row.get("source") or "")
    source_id = str(row.get("source_id") or data.get("source_id") or "")

    # Normalize source. The platform uses "canvas_user" / "peer_agent" /
    # sometimes "user" (legacy). Anything else falls into "unknown" so we
    # don't accidentally route a reply down the wrong transport.
    source: InboundSource
    if source_kind in ("canvas_user", "user"):
        source = "canvas_user"
    elif source_kind == "peer_agent":
        source = "peer_agent"
    elif source_id and source_id != "user":
        # Heuristic: a non-empty source_id that isn't the "user" sentinel
        # is almost certainly a peer workspace.
        source = "peer_agent"
    elif source_id == "user":
        source = "canvas_user"
    else:
        source = "unknown"

    text = str(data.get("text") or data.get("message") or "")

    return InboundMessage(
        activity_id=aid,
        source=source,
        source_id=source_id,
        text=text,
        raw=row,
        peer_name=str(data.get("peer_name") or ""),
        peer_role=str(data.get("peer_role") or ""),
        agent_card_url=str(data.get("agent_card_url") or ""),
    )


# ---------------------------------------------------------------------------
# Handler + delivery protocol
# ---------------------------------------------------------------------------


# A handler receives the inbound message + the client (so it can reply, fetch
# secrets, call peers, etc.) and returns either a reply string or None.
# Sync OR async — :class:`PollDelivery` detects ``Awaitable`` results and
# awaits them, mirroring the pattern in :class:`A2AServer`.
MessageHandler = Callable[
    ["InboundMessage", "RemoteAgentClient"],
    "str | None | Awaitable[str | None]",
]


@runtime_checkable
class InboundDelivery(Protocol):
    """The contract :py:meth:`RemoteAgentClient.run_agent_loop` calls into.

    Two implementations ship with the SDK:

    * :class:`PollDelivery` — for agents without a reachable URL.
    * :class:`PushDelivery` — for agents that host an A2AServer.

    Third parties can supply their own (e.g. WebSocket, gRPC streaming)
    by satisfying this protocol.
    """

    def run_once(self, handler: MessageHandler) -> int:
        """Drain one batch of inbound messages and dispatch to handler.

        Returns the count of messages dispatched. The caller's outer loop
        decides cadence / sleep.
        """
        ...

    def stop(self) -> None:
        """Release any resources (close sockets, stop background threads)."""
        ...


# ---------------------------------------------------------------------------
# PollDelivery — the new path
# ---------------------------------------------------------------------------


# Default poll cadence. 5s gives <5s p50 latency for canvas-user messages
# while keeping load on workspace-server modest (one GET per agent per 5s).
# Slack Socket Mode runs at ~1s, Telegram getUpdates with timeout=30 is the
# canonical long-poll. We don't have long-poll support server-side yet, so
# fixed 5s is the conservative choice. Tunable via constructor.
DEFAULT_POLL_INTERVAL = 5.0


class PollDelivery:
    """Poll ``GET /workspaces/:id/activity?type=a2a_receive&since_id=…``.

    The cursor is process-memory by default; a restart re-polls from
    scratch, which is harmless because handlers should be idempotent
    (the platform makes no exactly-once guarantees on activity poll —
    the same SDK-level convention as Slack Events API).

    Pass ``cursor_file`` to persist the cursor across restarts:

        PollDelivery(client, cursor_file=Path("~/.molecule/cursor"))

    Cursor-loss (HTTP 410) is handled transparently — the cursor is
    reset to ``None`` and the next poll starts fresh with whatever's in
    the activity window.
    """

    def __init__(
        self,
        client: "RemoteAgentClient",
        interval: float = DEFAULT_POLL_INTERVAL,
        type: str = "a2a_receive",
        limit: int = 100,
        cursor_file: Path | None = None,
    ) -> None:
        self._client = client
        self.interval = interval
        self.type = type
        self.limit = limit
        self._cursor_file = cursor_file
        self._cursor: str | None = self._load_cursor()
        self._stopped = False

    def _load_cursor(self) -> str | None:
        if self._cursor_file is None or not self._cursor_file.exists():
            return None
        try:
            cur = self._cursor_file.read_text().strip()
            return cur or None
        except OSError as exc:
            logger.warning("could not read cursor file %s: %s", self._cursor_file, exc)
            return None

    def _save_cursor(self) -> None:
        if self._cursor_file is None or self._cursor is None:
            return
        try:
            self._cursor_file.parent.mkdir(parents=True, exist_ok=True)
            self._cursor_file.write_text(self._cursor)
        except OSError as exc:
            logger.warning("could not write cursor file %s: %s", self._cursor_file, exc)

    @property
    def cursor(self) -> str | None:
        """Current cursor (``activity_id`` of the most recently dispatched
        message). Useful for tests and observability."""
        return self._cursor

    def run_once(self, handler: MessageHandler) -> int:
        """Fetch one batch and dispatch each message to ``handler``.

        Returns the number of messages dispatched. The cursor advances past
        every dispatched row, including ones whose handler raised — a
        poison-pill input shouldn't block the queue forever. The handler
        is responsible for surfacing its own errors via logging or its own
        observability. This matches Slack Events delivery and SQS DLQ
        semantics; the platform makes no exactly-once guarantees on
        activity poll, so handlers must be idempotent regardless.
        """
        if self._stopped:
            return 0
        try:
            batch = self._client.fetch_inbound(
                since_id=self._cursor,
                limit=self.limit,
                type=self.type,
            )
        except CursorLostError:
            logger.info("cursor %s lost (410 Gone) — resetting", self._cursor)
            self._cursor = None
            return 0

        dispatched = 0
        for msg in batch:
            try:
                self._dispatch(handler, msg)
            except Exception as exc:
                # Log + continue. We DO advance the cursor past this message
                # so a poison-pill input doesn't block the queue forever —
                # this matches how Slack Events delivers and how SQS DLQs
                # work. The handler is expected to surface its own errors
                # via logging or its own observability.
                logger.exception("handler raised on activity %s: %s", msg.activity_id, exc)
            self._cursor = msg.activity_id
            dispatched += 1

        if dispatched:
            self._save_cursor()
        return dispatched

    def _dispatch(self, handler: MessageHandler, msg: "InboundMessage") -> None:
        """Invoke handler, await if async, send the reply if returned."""
        result = handler(msg, self._client)
        if inspect.isawaitable(result):
            # Detect a running loop without using the deprecated
            # asyncio.get_event_loop() (Py3.12+). If a loop is running we
            # refuse — the caller is async and should await the handler
            # themselves; we can't synchronously block on an awaitable
            # without deadlocking the running loop.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # No running loop — safe to spin up a fresh one. Mirrors
                # A2AServer's pattern: build, run, close. asyncio.run is
                # the modern equivalent of new_loop+run_until_complete+close
                # and handles the close even on exception.
                result = asyncio.run(result)  # type: ignore[arg-type]
            else:
                raise RuntimeError(
                    "PollDelivery.run_once was called from inside a running "
                    "event loop with an async handler. Use a sync handler "
                    "here, or schedule run_once on a worker thread via "
                    "asyncio.to_thread()."
                )

        reply_text = result if isinstance(result, str) else None
        if reply_text:
            try:
                self._client.reply(msg, reply_text)
            except Exception as exc:
                logger.warning("reply send failed for activity %s: %s", msg.activity_id, exc)

    def stop(self) -> None:
        self._stopped = True


# ---------------------------------------------------------------------------
# PushDelivery — wraps the existing A2AServer
# ---------------------------------------------------------------------------


class PushDelivery:
    """Adapt :class:`A2AServer` to the :class:`InboundDelivery` protocol.

    Use this when the agent CAN expose a reachable HTTP endpoint. The
    A2AServer runs in its own thread and dispatches to ``handler`` as
    HTTP requests arrive — ``run_once`` is a no-op (the loop driver in
    :py:meth:`RemoteAgentClient.run_agent_loop` simply sleeps and
    keeps the heartbeat alive).
    """

    def __init__(self, client: "RemoteAgentClient", server: Any) -> None:
        # ``server`` typed Any to avoid a circular import; it's an A2AServer.
        self._client = client
        self._server = server

    def run_once(self, handler: MessageHandler) -> int:  # noqa: ARG002 — handler unused
        # A2AServer dispatches synchronously on its own thread; nothing
        # for the outer loop to do per-tick.
        return 0

    def stop(self) -> None:
        try:
            self._server.stop()
        except Exception as exc:
            logger.warning("PushDelivery stop: A2AServer.stop raised: %s", exc)


__all__ = [
    "CursorLostError",
    "DEFAULT_POLL_INTERVAL",
    "InboundDelivery",
    "InboundMessage",
    "InboundSource",
    "MessageHandler",
    "PollDelivery",
    "PushDelivery",
]
