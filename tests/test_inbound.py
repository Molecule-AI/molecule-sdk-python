"""Tests for poll-mode inbound delivery (Phase 30.8c).

Covers:

* :func:`_parse_activity_row` source normalization and edge cases.
* :py:meth:`RemoteAgentClient.fetch_inbound` happy path, cursor, 410, shapes.
* :py:meth:`RemoteAgentClient.reply` smart-routing (canvas vs peer).
* :class:`PollDelivery` cursor advancement, async/sync handler dispatch,
  error handling, 410 reset, cursor-file persistence, stop().
* :py:meth:`RemoteAgentClient.run_agent_loop` heartbeat + state + delivery
  composition, default-delivery selection, terminal-status handling, sleep
  cadence selection.

Mocking style matches ``tests/test_remote_agent.py``: a ``FakeResponse`` /
``MagicMock`` session, no third-party HTTP mock library.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from molecule_agent import (
    CursorLostError,
    InboundMessage,
    PollDelivery,
    PushDelivery,
    RemoteAgentClient,
    WorkspaceState,
)
from molecule_agent.inbound import _parse_activity_row


# ---------------------------------------------------------------------------
# FakeResponse — same shape as the existing test_remote_agent helper
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int = 200, json_body: Any = None, text: str = ""):
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture
def tmp_token_dir(tmp_path: Path) -> Path:
    return tmp_path / "molecule-token-cache"


@pytest.fixture
def client(tmp_token_dir: Path) -> RemoteAgentClient:
    session = MagicMock()
    c = RemoteAgentClient(
        workspace_id="ws-abc-123",
        platform_url="http://platform.test",
        agent_card={"name": "test-agent"},
        token_dir=tmp_token_dir,
        session=session,
    )
    # Pre-seed the cached token so _auth_headers returns one and we don't
    # have to mock /registry/register on every test.
    c.save_token("test-token-secret")
    return c


# ---------------------------------------------------------------------------
# _parse_activity_row
# ---------------------------------------------------------------------------


def test_parse_activity_row_canvas_user_explicit():
    row = {
        "id": "act-1",
        "type": "a2a_receive",
        "source_id": "user",
        "data": {"source": "canvas_user", "text": "hi"},
    }
    msg = _parse_activity_row(row)
    assert msg is not None
    assert msg.activity_id == "act-1"
    assert msg.source == "canvas_user"
    assert msg.source_id == "user"
    assert msg.text == "hi"


def test_parse_activity_row_legacy_user_normalizes_to_canvas():
    # Older platform versions used 'user' instead of 'canvas_user'.
    row = {"id": "act-2", "data": {"source": "user", "text": "hello"}}
    msg = _parse_activity_row(row)
    assert msg is not None
    assert msg.source == "canvas_user"


def test_parse_activity_row_peer_agent_explicit():
    row = {
        "id": "act-3",
        "source_id": "peer-ws-77",
        "data": {"source": "peer_agent", "text": "ping"},
    }
    msg = _parse_activity_row(row)
    assert msg is not None
    assert msg.source == "peer_agent"
    assert msg.source_id == "peer-ws-77"


def test_parse_activity_row_inferred_peer_from_source_id():
    # No explicit source field but a non-'user' source_id present → infer peer_agent.
    # This protects us from server-side variants that omit 'source' in data.
    row = {"id": "act-4", "source_id": "peer-ws-88", "data": {"text": "ping"}}
    msg = _parse_activity_row(row)
    assert msg is not None
    assert msg.source == "peer_agent"


def test_parse_activity_row_inferred_canvas_from_user_source_id():
    row = {"id": "act-5", "source_id": "user", "data": {"text": "hi"}}
    msg = _parse_activity_row(row)
    assert msg is not None
    assert msg.source == "canvas_user"


def test_parse_activity_row_unknown_source_falls_through():
    # No source_id, no source → unknown. Reply path will refuse to guess.
    row = {"id": "act-6", "data": {"text": "??"}}
    msg = _parse_activity_row(row)
    assert msg is not None
    assert msg.source == "unknown"


def test_parse_activity_row_no_id_returns_none():
    row = {"data": {"source": "canvas_user", "text": "no id"}}
    assert _parse_activity_row(row) is None


def test_parse_activity_row_text_alt_key():
    # Some server paths use 'message' instead of 'text'. Accept both.
    row = {"id": "act-7", "data": {"source": "canvas_user", "message": "alt"}}
    msg = _parse_activity_row(row)
    assert msg is not None
    assert msg.text == "alt"


# ---------------------------------------------------------------------------
# fetch_inbound
# ---------------------------------------------------------------------------


def test_fetch_inbound_happy_path(client: RemoteAgentClient):
    rows = [
        {"id": "act-1", "data": {"source": "canvas_user", "text": "hi"}},
        {"id": "act-2", "source_id": "peer-77", "data": {"source": "peer_agent", "text": "ping"}},
    ]
    client._session.get.return_value = FakeResponse(200, rows)

    out = client.fetch_inbound()

    assert len(out) == 2
    assert out[0].source == "canvas_user"
    assert out[1].source == "peer_agent"
    # Verify the GET shape.
    call_args = client._session.get.call_args
    assert call_args.args[0] == "http://platform.test/workspaces/ws-abc-123/activity"
    assert call_args.kwargs["params"]["type"] == "a2a_receive"
    assert call_args.kwargs["params"]["limit"] == "100"
    assert "since_id" not in call_args.kwargs["params"]


def test_fetch_inbound_with_since_id_passes_cursor(client: RemoteAgentClient):
    client._session.get.return_value = FakeResponse(200, [])
    client.fetch_inbound(since_id="act-prev")
    params = client._session.get.call_args.kwargs["params"]
    assert params["since_id"] == "act-prev"


def test_fetch_inbound_410_raises_cursor_lost(client: RemoteAgentClient):
    client._session.get.return_value = FakeResponse(410, {"error": "cursor lost"})
    with pytest.raises(CursorLostError):
        client.fetch_inbound(since_id="act-stale")


def test_fetch_inbound_accepts_dict_items_wrapper(client: RemoteAgentClient):
    # If a future server version wraps in {"items": [...]}, we still parse.
    body = {"items": [{"id": "act-1", "data": {"source": "canvas_user", "text": "hi"}}]}
    client._session.get.return_value = FakeResponse(200, body)
    out = client.fetch_inbound()
    assert len(out) == 1
    assert out[0].activity_id == "act-1"


def test_fetch_inbound_skips_malformed_rows(client: RemoteAgentClient):
    rows = [
        {"id": "act-1", "data": {"source": "canvas_user", "text": "ok"}},
        "not a dict",
        {"data": {"text": "no id"}},  # missing id → skipped
    ]
    client._session.get.return_value = FakeResponse(200, rows)
    out = client.fetch_inbound()
    assert len(out) == 1
    assert out[0].activity_id == "act-1"


def test_fetch_inbound_401_raises_http_error(client: RemoteAgentClient):
    client._session.get.return_value = FakeResponse(401)
    with pytest.raises(requests.HTTPError):
        client.fetch_inbound()


def test_fetch_inbound_empty_returns_empty(client: RemoteAgentClient):
    client._session.get.return_value = FakeResponse(200, [])
    assert client.fetch_inbound() == []


def test_fetch_inbound_429_retries_via_get_with_retry(
    client: RemoteAgentClient, monkeypatch
):
    """A 429 on the first GET should route through _get_with_retry, which
    honours Retry-After / jittered backoff and eventually returns a 2xx.
    """
    # Don't actually sleep during the retry — keeps the test fast.
    monkeypatch.setattr("time.sleep", lambda _s: None)

    rows = [{"id": "act-after-retry", "data": {"source": "canvas_user", "text": "ok"}}]

    # First call: 429. Second call (the retry): 200 + rows. _get_with_retry
    # will see 429 and call session.get again with the rebuilt URL — both
    # responses come from the same mocked session.get, so we use side_effect.
    first_429 = FakeResponse(429)
    first_429.headers = {"Retry-After": "0"}
    second_200 = FakeResponse(200, rows)
    client._session.get.side_effect = [first_429, second_200]

    out = client.fetch_inbound(since_id="act-prev")

    assert len(out) == 1
    assert out[0].activity_id == "act-after-retry"
    # Two GETs total: one 429, one 200.
    assert client._session.get.call_count == 2


# ---------------------------------------------------------------------------
# reply()
# ---------------------------------------------------------------------------


def test_reply_canvas_user_hits_notify(client: RemoteAgentClient):
    msg = InboundMessage(
        activity_id="act-1", source="canvas_user", source_id="user", text="hi"
    )
    client._session.post.return_value = FakeResponse(200, {"status": "sent"})

    client.reply(msg, "hello")

    call_args = client._session.post.call_args
    assert call_args.args[0] == "http://platform.test/workspaces/ws-abc-123/notify"
    assert call_args.kwargs["json"] == {"message": "hello"}
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer test-token-secret"


def test_reply_peer_agent_hits_a2a(client: RemoteAgentClient):
    msg = InboundMessage(
        activity_id="act-2", source="peer_agent", source_id="peer-ws-77", text="ping"
    )
    client._session.post.return_value = FakeResponse(200, {"jsonrpc": "2.0", "result": {}})

    client.reply(msg, "pong")

    call_args = client._session.post.call_args
    assert call_args.args[0] == "http://platform.test/workspaces/peer-ws-77/a2a"
    body = call_args.kwargs["json"]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "message/send"
    assert body["params"]["message"]["parts"][0]["text"] == "pong"
    headers = call_args.kwargs["headers"]
    assert headers["X-Source-Workspace-Id"] == "ws-abc-123"
    assert headers["X-Workspace-ID"] == "ws-abc-123"


def test_reply_unknown_source_raises_value_error(client: RemoteAgentClient):
    msg = InboundMessage(activity_id="act-3", source="unknown", source_id="", text="?")
    with pytest.raises(ValueError, match="cannot auto-route"):
        client.reply(msg, "won't send")
    client._session.post.assert_not_called()


def test_reply_empty_text_raises_value_error(client: RemoteAgentClient):
    msg = InboundMessage(activity_id="act-4", source="canvas_user", source_id="user", text="hi")
    with pytest.raises(ValueError, match="non-empty"):
        client.reply(msg, "")
    with pytest.raises(ValueError, match="non-empty"):
        client.reply(msg, "   ")
    client._session.post.assert_not_called()


def test_reply_peer_agent_missing_source_id_raises(client: RemoteAgentClient):
    msg = InboundMessage(activity_id="act-5", source="peer_agent", source_id="", text="?")
    with pytest.raises(ValueError, match="no source_id"):
        client.reply(msg, "won't send")


def test_reply_propagates_http_error(client: RemoteAgentClient):
    msg = InboundMessage(activity_id="act-6", source="canvas_user", source_id="user", text="hi")
    client._session.post.return_value = FakeResponse(500)
    with pytest.raises(requests.HTTPError):
        client.reply(msg, "boom")


# ---------------------------------------------------------------------------
# PollDelivery
# ---------------------------------------------------------------------------


def test_poll_delivery_run_once_advances_cursor(client: RemoteAgentClient):
    rows = [
        {"id": "act-1", "data": {"source": "canvas_user", "text": "a"}},
        {"id": "act-2", "data": {"source": "canvas_user", "text": "b"}},
    ]
    client._session.get.return_value = FakeResponse(200, rows)
    delivery = PollDelivery(client, interval=0.0)

    received: list[str] = []

    def handler(msg: InboundMessage, _client: RemoteAgentClient):
        received.append(msg.text)
        return None  # no reply

    n = delivery.run_once(handler)
    assert n == 2
    assert received == ["a", "b"]
    assert delivery.cursor == "act-2"


def test_poll_delivery_handler_exception_advances_and_continues(
    client: RemoteAgentClient, caplog
):
    rows = [
        {"id": "act-1", "data": {"source": "canvas_user", "text": "poison"}},
        {"id": "act-2", "data": {"source": "canvas_user", "text": "next"}},
    ]
    client._session.get.return_value = FakeResponse(200, rows)
    delivery = PollDelivery(client, interval=0.0)

    seen: list[str] = []

    def handler(msg, _c):
        seen.append(msg.text)
        if msg.text == "poison":
            raise RuntimeError("kaboom")
        return None

    n = delivery.run_once(handler)
    # Both messages should be dispatched even though the first raised.
    assert n == 2
    assert seen == ["poison", "next"]
    # Cursor advances past the failure so we don't get stuck on poison forever.
    assert delivery.cursor == "act-2"


def test_poll_delivery_async_handler_awaited(client: RemoteAgentClient):
    rows = [{"id": "act-1", "data": {"source": "canvas_user", "text": "ahoy"}}]
    client._session.get.return_value = FakeResponse(200, rows)
    delivery = PollDelivery(client, interval=0.0)

    seen: list[str] = []

    async def async_handler(msg, _c):
        await asyncio.sleep(0)
        seen.append(msg.text)
        return None

    n = delivery.run_once(async_handler)
    assert n == 1
    assert seen == ["ahoy"]


def test_poll_delivery_handler_returns_text_triggers_reply(client: RemoteAgentClient):
    rows = [{"id": "act-1", "data": {"source": "canvas_user", "text": "hi"}}]
    # First mock the GET (fetch_inbound), then the POST (reply).
    client._session.get.return_value = FakeResponse(200, rows)
    client._session.post.return_value = FakeResponse(200, {"status": "sent"})

    delivery = PollDelivery(client, interval=0.0)

    def handler(msg, _c):
        return f"echo:{msg.text}"

    n = delivery.run_once(handler)
    assert n == 1
    # /notify should have been called with the echo body.
    post_call = client._session.post.call_args
    assert "/notify" in post_call.args[0]
    assert post_call.kwargs["json"] == {"message": "echo:hi"}


def test_poll_delivery_handler_returns_none_no_reply(client: RemoteAgentClient):
    rows = [{"id": "act-1", "data": {"source": "canvas_user", "text": "hi"}}]
    client._session.get.return_value = FakeResponse(200, rows)
    delivery = PollDelivery(client, interval=0.0)

    def handler(_msg, _c):
        return None

    delivery.run_once(handler)
    client._session.post.assert_not_called()


def test_poll_delivery_410_resets_cursor(client: RemoteAgentClient):
    delivery = PollDelivery(client, interval=0.0)
    delivery._cursor = "act-stale"

    client._session.get.return_value = FakeResponse(410, {"error": "gone"})
    n = delivery.run_once(lambda *_: None)

    # No messages dispatched, cursor reset to None.
    assert n == 0
    assert delivery.cursor is None


def test_poll_delivery_cursor_file_persistence(
    client: RemoteAgentClient, tmp_path: Path
):
    cursor_file = tmp_path / "cursor"
    rows = [{"id": "act-XYZ", "data": {"source": "canvas_user", "text": "hi"}}]
    client._session.get.return_value = FakeResponse(200, rows)

    delivery = PollDelivery(client, interval=0.0, cursor_file=cursor_file)
    assert delivery.cursor is None  # nothing on disk yet

    delivery.run_once(lambda *_: None)
    assert cursor_file.read_text() == "act-XYZ"

    # New delivery instance reads the cursor from disk.
    fresh = PollDelivery(client, interval=0.0, cursor_file=cursor_file)
    assert fresh.cursor == "act-XYZ"


def test_poll_delivery_stop_makes_run_once_noop(client: RemoteAgentClient):
    delivery = PollDelivery(client, interval=0.0)
    delivery.stop()

    n = delivery.run_once(lambda *_: None)
    assert n == 0
    # GET should not have been issued.
    client._session.get.assert_not_called()


# ---------------------------------------------------------------------------
# PushDelivery
# ---------------------------------------------------------------------------


def test_push_delivery_run_once_is_noop(client: RemoteAgentClient):
    fake_server = MagicMock()
    delivery = PushDelivery(client, fake_server)
    n = delivery.run_once(lambda *_: None)
    assert n == 0


def test_push_delivery_stop_calls_server_stop(client: RemoteAgentClient):
    fake_server = MagicMock()
    delivery = PushDelivery(client, fake_server)
    delivery.stop()
    fake_server.stop.assert_called_once()


def test_push_delivery_stop_swallows_server_exception(
    client: RemoteAgentClient, caplog
):
    fake_server = MagicMock()
    fake_server.stop.side_effect = RuntimeError("server down hard")
    delivery = PushDelivery(client, fake_server)
    # Should not raise.
    delivery.stop()


# ---------------------------------------------------------------------------
# run_agent_loop
# ---------------------------------------------------------------------------


def _stub_state(client: RemoteAgentClient, paused=False, deleted=False, status="online"):
    """Make poll_state return a stub WorkspaceState."""
    client.poll_state = MagicMock(  # type: ignore[method-assign]
        return_value=WorkspaceState(
            workspace_id=client.workspace_id,
            status=status,
            paused=paused,
            deleted=deleted,
        )
    )


def test_run_agent_loop_exits_on_paused(client: RemoteAgentClient, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client.heartbeat = MagicMock()  # type: ignore[method-assign]
    _stub_state(client, paused=True, status="paused")
    delivery = MagicMock()
    delivery.run_once.return_value = 0
    delivery.interval = 0.0

    terminal = client.run_agent_loop(lambda *_: None, delivery=delivery)
    assert terminal == "paused"
    delivery.stop.assert_called_once()


def test_run_agent_loop_exits_on_deleted(client: RemoteAgentClient, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client.heartbeat = MagicMock()  # type: ignore[method-assign]
    _stub_state(client, deleted=True, status="removed")
    delivery = MagicMock()
    delivery.run_once.return_value = 0
    delivery.interval = 0.0

    terminal = client.run_agent_loop(lambda *_: None, delivery=delivery)
    assert terminal == "removed"


def test_run_agent_loop_max_iterations(client: RemoteAgentClient, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client.heartbeat = MagicMock()  # type: ignore[method-assign]
    _stub_state(client)  # online forever
    delivery = MagicMock()
    delivery.run_once.return_value = 0
    delivery.interval = 0.0

    terminal = client.run_agent_loop(lambda *_: None, delivery=delivery, max_iterations=3)
    assert terminal == "max_iterations"
    assert delivery.run_once.call_count == 3
    assert client.heartbeat.call_count == 3


def test_run_agent_loop_default_delivery_is_poll(client: RemoteAgentClient, monkeypatch):
    """When delivery=None, run_agent_loop should construct a PollDelivery."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client.heartbeat = MagicMock()  # type: ignore[method-assign]
    _stub_state(client, paused=True, status="paused")
    # fetch_inbound returns an empty list once for the default-poll path.
    client.fetch_inbound = MagicMock(return_value=[])  # type: ignore[method-assign]

    terminal = client.run_agent_loop(lambda *_: None)
    assert terminal == "paused"
    client.fetch_inbound.assert_called()


def test_run_agent_loop_swallows_heartbeat_exception(
    client: RemoteAgentClient, monkeypatch
):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client.heartbeat = MagicMock(side_effect=RuntimeError("hb down"))  # type: ignore[method-assign]
    _stub_state(client, paused=True, status="paused")
    delivery = MagicMock()
    delivery.run_once.return_value = 0
    delivery.interval = 0.0

    terminal = client.run_agent_loop(lambda *_: None, delivery=delivery)
    # Heartbeat failure does NOT stop the loop — we still detect 'paused'.
    assert terminal == "paused"


def test_run_agent_loop_swallows_delivery_exception(
    client: RemoteAgentClient, monkeypatch
):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client.heartbeat = MagicMock()  # type: ignore[method-assign]
    _stub_state(client, paused=True, status="paused")
    delivery = MagicMock()
    delivery.run_once.side_effect = RuntimeError("delivery exploded")
    delivery.interval = 0.0

    terminal = client.run_agent_loop(lambda *_: None, delivery=delivery)
    # Delivery failure logged + continued; loop still exits cleanly on paused.
    assert terminal == "paused"


def test_run_agent_loop_uses_min_of_intervals(client: RemoteAgentClient, monkeypatch):
    """The loop should sleep min(heartbeat_interval, delivery.interval)."""
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    client.heartbeat_interval = 30.0
    client.heartbeat = MagicMock()  # type: ignore[method-assign]
    _stub_state(client)  # online; uses max_iterations to exit
    delivery = MagicMock()
    delivery.run_once.return_value = 0
    delivery.interval = 5.0

    client.run_agent_loop(lambda *_: None, delivery=delivery, max_iterations=2)
    assert sleeps == [5.0, 5.0]


def test_run_agent_loop_calls_task_supplier(client: RemoteAgentClient, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client.heartbeat = MagicMock()  # type: ignore[method-assign]
    _stub_state(client, paused=True, status="paused")
    delivery = MagicMock()
    delivery.run_once.return_value = 0
    delivery.interval = 0.0

    def supplier():
        return {"current_task": "doing-thing", "active_tasks": 2}

    client.run_agent_loop(lambda *_: None, delivery=delivery, task_supplier=supplier)
    # Heartbeat receives the supplied report.
    hb_kwargs = client.heartbeat.call_args.kwargs
    assert hb_kwargs["current_task"] == "doing-thing"
    assert hb_kwargs["active_tasks"] == 2


def test_run_agent_loop_swallows_task_supplier_exception(
    client: RemoteAgentClient, monkeypatch
):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client.heartbeat = MagicMock()  # type: ignore[method-assign]
    _stub_state(client, paused=True, status="paused")
    delivery = MagicMock()
    delivery.run_once.return_value = 0
    delivery.interval = 0.0

    def supplier():
        raise RuntimeError("supplier broken")

    terminal = client.run_agent_loop(
        lambda *_: None, delivery=delivery, task_supplier=supplier
    )
    assert terminal == "paused"
    # Heartbeat called with empty task fields (the default when supplier fails).
    hb_kwargs = client.heartbeat.call_args.kwargs
    assert hb_kwargs["current_task"] == ""
    assert hb_kwargs["active_tasks"] == 0
