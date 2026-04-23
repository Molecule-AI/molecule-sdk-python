"""Tests for molecule_agent.a2a_server."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from unittest.mock import MagicMock
import time

import pytest

from molecule_agent.a2a_server import A2AServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_json(host: str, port: int, payload: dict) -> tuple[int, dict]:
    conn = HTTPConnection(host, port, timeout=5)
    body = json.dumps(payload).encode()
    conn.request("POST", "/a2a/inbound", body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read())


# ---------------------------------------------------------------------------
# A2AServer tests
# ---------------------------------------------------------------------------


def test_start_stop() -> None:
    """Server starts, binds an ephemeral port, and shuts down cleanly."""
    handler = MagicMock(return_value={"ack": True})
    server = A2AServer(
        agent_id="test-agent",
        inbound_url="https://example.com/a2a/inbound",
        message_handler=handler,
    )
    server.start_in_background()
    try:
        host, port = server._server.server_address  # type: ignore[union-attr]
        assert host in ("0.0.0.0", "127.0.0.1", "::")
        assert isinstance(port, int) and port > 0
    finally:
        server.stop()


def test_stop_idempotent() -> None:
    """stop() called twice does not raise."""
    handler = MagicMock()
    server = A2AServer(
        agent_id="test-agent",
        inbound_url="https://example.com/a2a/inbound",
        message_handler=handler,
    )
    server.start_in_background()
    server.stop()
    server.stop()  # must not raise


def test_inbound_call_routes_to_handler() -> None:
    """POST /a2a/inbound calls message_handler and returns 200."""
    handler = MagicMock(return_value={"task_id": "reply-123"})
    server = A2AServer(
        agent_id="test-agent",
        inbound_url="https://example.com/a2a/inbound",
        message_handler=handler,
    )
    server.start_in_background()
    try:
        host, port = server._server.server_address  # type: ignore[union-attr]
        status, body = _post_json(host, port, {"task_id": "req-1", "message": "ping"})
        assert status == 200
        assert body["status"] == "ok"
        assert body["result"] == {"task_id": "reply-123"}
        handler.assert_called_once_with({"task_id": "req-1", "message": "ping"})
    finally:
        server.stop()


def test_non_json_body_returns_400() -> None:
    """Malformed JSON body returns 400 with error detail."""
    handler = MagicMock()
    server = A2AServer(
        agent_id="test-agent",
        inbound_url="https://example.com/a2a/inbound",
        message_handler=handler,
    )
    server.start_in_background()
    try:
        host, port = server._server.server_address  # type: ignore[union-attr]
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/a2a/inbound", body=b"not json{", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 400
        body = json.loads(resp.read())
        assert "error" in body
    finally:
        server.stop()


def test_empty_body_returns_400() -> None:
    """Empty body returns 400."""
    handler = MagicMock()
    server = A2AServer(
        agent_id="test-agent",
        inbound_url="https://example.com/a2a/inbound",
        message_handler=handler,
    )
    server.start_in_background()
    try:
        host, port = server._server.server_address  # type: ignore[union-attr]
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/a2a/inbound", body=b"", headers={"Content-Length": "0"})
        resp = conn.getresponse()
        assert resp.status == 400
    finally:
        server.stop()


def test_wrong_path_returns_404() -> None:
    """A POST to any path other than /a2a/inbound returns 404."""
    handler = MagicMock()
    server = A2AServer(
        agent_id="test-agent",
        inbound_url="https://example.com/a2a/inbound",
        message_handler=handler,
    )
    server.start_in_background()
    try:
        host, port = server._server.server_address  # type: ignore[union-attr]
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/other/path", body=b"{}")
        resp = conn.getresponse()
        assert resp.status == 404
        handler.assert_not_called()
    finally:
        server.stop()


def test_handler_exception_returns_500() -> None:
    """Handler raising an exception returns 500, not crashing the server."""
    handler = MagicMock(side_effect=RuntimeError("boom"))
    server = A2AServer(
        agent_id="test-agent",
        inbound_url="https://example.com/a2a/inbound",
        message_handler=handler,
    )
    server.start_in_background()
    try:
        host, port = server._server.server_address  # type: ignore[union-attr]
        status, body = _post_json(host, port, {"task_id": "req-1"})
        assert status == 500
        assert "error" in body
    finally:
        server.stop()


def test_async_handler_runs_sync() -> None:
    """An async handler is run to completion synchronously."""
    async_calls: list = []

    async def async_handler(payload: dict) -> dict:
        async_calls.append(payload)
        return {"async": True}

    server = A2AServer(
        agent_id="test-agent",
        inbound_url="https://example.com/a2a/inbound",
        message_handler=async_handler,
    )
    server.start_in_background()
    try:
        host, port = server._server.server_address  # type: ignore[union-attr]
        status, body = _post_json(host, port, {"task_id": "async-req"})
        assert status == 200
        assert body["result"] == {"async": True}
        assert len(async_calls) == 1
    finally:
        server.stop()


def test_concurrent_requests() -> None:
    """Multiple simultaneous POSTs are handled without crashing the server."""
    call_count = {"count": 0}
    lock = threading.Lock()

    def counting_handler(payload: dict) -> dict:
        with lock:
            call_count["count"] += 1
        time.sleep(0.05)  # simulate light processing
        return {"received": payload.get("task_id")}

    server = A2AServer(
        agent_id="test-agent",
        inbound_url="https://example.com/a2a/inbound",
        message_handler=counting_handler,
    )
    server.start_in_background()
    try:
        host, port = server._server.server_address  # type: ignore[union-attr]

        def send(n: int) -> tuple[int, dict]:
            return _post_json(host, port, {"task_id": f"concurrent-{n}"})

        threads = [threading.Thread(target=send, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count["count"] == 5
    finally:
        server.stop()
