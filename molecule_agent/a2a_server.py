"""A2A server for inbound agent calls.

Bundled alongside :class:`molecule_agent.client.RemoteAgentClient` to
enable remote agents to receive A2A calls from the platform without
requiring the agent author to provision their own HTTP endpoint.

Phase 30.8b contract — the server exposes ``POST /a2a/inbound`` which
the platform's ingress proxy calls when it needs to push work to a
registered remote agent.

Usage::

    from molecule_agent import RemoteAgentClient, A2AServer

    client = RemoteAgentClient(workspace_id="...", platform_url="...")
    server = A2AServer(
        agent_id=client.workspace_id,
        inbound_url="https://my-agent.example.com/a2a/inbound",
        message_handler=my_handler,
    )

    # Start server in background thread, then register with platform.
    server.start_in_background()
    client.reported_url = server.inbound_url  # platform reaches this URL
    token = client.register()

    # Heartbeat loop now reports a real URL instead of "remote://no-inbound".
    client.run_heartbeat_loop()

    # Shutdown the server when the agent exits.
    server.stop()

The ``message_handler`` signature is::

    async def my_handler(request: dict) -> dict:
        '''Return an A2A-formatted response dict.'''
        ...

Handlers are invoked on the server's internal thread pool.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Awaitable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Module-level HTTPServer instance so the handler can access server state.
_server: HTTPServer | None = None
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class _A2AHandler(BaseHTTPRequestHandler):
    """Handles ``POST /a2a/inbound`` requests.

    The request body is a JSON A2A task dispatch dict::

        {
            "task_id": "...",
            "sender": "...",
            "message": "...",
            "idempotency_key": "...",
        }

    The ``message_handler`` ( supplied at construction) is called with the
    parsed dict and its return value is written as a JSON response::

        200 {"status": "ok", "result": <handler-result>}
        400 {"error": "bad request: ..."}
        500 {"error": "internal error: ..."}
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr noise; use structured logging instead."""
        logger.debug("%s %s — %s", self.command, self.path, format % args)

    def log_error(self, format: str, *args: Any) -> None:
        logger.warning("%s %s — %s", self.command, self.path, format % args)

    def _send_json(self, status: int, body: dict) -> None:
        body_bytes = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body_bytes)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/a2a/inbound":
            self._send_json(404, {"error": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                raise ValueError("empty body")
            body = self.rfile.read(content_length)
            payload = json.loads(body)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": f"bad request: {exc}"})
            return

        try:
            result = _A2AHandler._message_handler(payload)
            if isinstance(result, Awaitable):
                # If the handler is async, run it synchronously in the server thread.
                # Agents that want full async semantics should use an explicit ASGI app;
                # this path covers the common case of a simple sync handler.
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(result)
                finally:
                    loop.close()
            self._send_json(200, {"status": "ok", "result": result})
        except Exception as exc:
            logger.exception("message_handler raised: %s", exc)
            self._send_json(500, {"error": f"internal error: {exc}"})


# ---------------------------------------------------------------------------
# A2AServer
# ---------------------------------------------------------------------------

class A2AServer:
    """HTTP server that receives inbound A2A calls and dispatches them to a
    handler running alongside :class:`~molecule_agent.client.RemoteAgentClient`.

    Args:
        agent_id: The workspace / agent identifier. Used in log messages.
        inbound_url: The URL the platform's ingress proxy uses to reach this
            server. Must be a reachable host:port (or a publicly accessible
            URL if a tunnel is in front). The value is typically assigned to
            ``RemoteAgentClient.reported_url`` before registration so the
            platform knows where to deliver inbound calls.
        message_handler: Callable that receives a parsed A2A task dict and
            returns a dict response. May be ``async def`` or regular ``def``.
        host: Address to bind the HTTP server to. Defaults to ``"0.0.0.0"``
            (all interfaces); bind to ``"127.0.0.1"`` if behind a reverse
            proxy or tunnel.
        port: TCP port to listen on. ``0`` picks an available ephemeral port
            (useful when the real public URL is managed by a proxy/tunnel).
    """

    def __init__(
        self,
        agent_id: str,
        inbound_url: str,
        message_handler: Callable[[dict], dict | Awaitable[dict]],
        host: str = "0.0.0.0",
        port: int = 0,
    ) -> None:
        self.agent_id = agent_id
        self.inbound_url = inbound_url
        self.host = host
        self.port = port
        self._handler = message_handler
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start_in_background(self) -> None:
        """Start the HTTP server in a daemon thread and return immediately.

        Call :py:meth:`stop` to shut it down cleanly.
        """
        global _server
        with _lock:
            self._server = HTTPServer((self.host, self.port), _A2AHandler)
            _server = self._server
            _A2AHandler._server = self  # type: ignore[attr-defined]
            _A2AHandler._message_handler = self._handler  # type: ignore[attr-defined]

        actual = self._server.server_address
        logger.info(
            "A2AServer for %s listening on %s:%s (inbound_url=%s)",
            self.agent_id, actual[0], actual[1], self.inbound_url,
        )

        self._thread = threading.Thread(target=self._serve_forever, daemon=True)
        self._thread.start()

    def _serve_forever(self) -> None:
        assert self._server is not None
        while not self._stop_event.is_set():
            try:
                self._server.timeout = 0.5
                self._server.handle_request()
            except Exception as exc:
                if not self._stop_event.is_set():
                    logger.warning("A2AServer handle_request raised: %s", exc)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the HTTP server and join the background thread.

        Idempotent — safe to call multiple times.
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        if self._server is not None:
            try:
                self._server.server_close()
            except Exception as exc:
                logger.warning("A2AServer server_close raised: %s", exc)
            self._server = None
        global _server
        with _lock:
            _server = None


__all__ = ["A2AServer"]
