"""Shared test fixtures for molecule-sdk-python.

To use these fixtures, add to your test file:
    from tests.conftest import *

All fixtures are session-scoped by default to avoid repeated setup overhead.
"""

from __future__ import annotations

import logging
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import pytest

# Ensure the package is importable without installing it
_SDK_ROOT = Path(__file__).resolve().parents[1]
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))

from molecule_agent import RemoteAgentClient


# ─── Token directory fixture ─────────────────────────────────────────────────

@pytest.fixture
def tmp_token_dir(tmp_path: Path) -> Path:
    """A temporary directory for token storage, isolated per test."""
    d = tmp_path / "tokens"
    d.mkdir()
    return d


# ─── Mock platform server fixtures ──────────────────────────────────────────

class _CaptureHandler(BaseHTTPRequestHandler):
    """In-process HTTP server that records requests and returns configurable responses."""

    requests: list[dict] = []
    _responses: dict[str, tuple[int, dict, str]] = {}

    def log_message(self, format, *args):
        # Silence default stderr logging during tests
        pass

    @classmethod
    def reset(cls):
        cls.requests.clear()
        cls._responses.clear()

    @classmethod
    def stub(cls, method: str, path: str, status: int, headers: dict | None = None, body: str = ""):
        key = f"{method} {path}"
        cls._responses[key] = (status, headers or {}, body)

    def _match(self) -> tuple[int, dict, str]:
        key = f"{self.command} {self.path}"
        if key in self._responses:
            return self._responses[key]
        # Default: 404 for unmatched requests (fail-fast)
        return (404, {}, "not found")

    def do_GET(self):
        status, headers, body = self._match()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body.encode())

        # Record for assertions
        self.server.requests.append({
            "method": "GET",
            "path": self.path,
            "status": status,
        })

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode() if length else ""
        status, headers, body_resp = self._match()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if body_resp:
            self.wfile.write(body_resp.encode())

        self.server.requests.append({
            "method": "POST",
            "path": self.path,
            "body": body,
            "status": status,
        })

    def do_PATCH(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode() if length else ""
        status, headers, body_resp = self._match()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if body_resp:
            self.wfile.write(body_resp.encode())

        self.server.requests.append({
            "method": "PATCH",
            "path": self.path,
            "body": body,
            "status": status,
        })

    def do_DELETE(self):
        status, headers, body = self._match()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body.encode())

        self.server.requests.append({
            "method": "DELETE",
            "path": self.path,
            "status": status,
        })


class _CaptureServer(HTTPServer):
    """HTTP server that collects captured requests."""
    requests: list[dict] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = []


@pytest.fixture
def mockserver(tmp_path: Path) -> Generator[_CaptureServer, None, None]:
    """Start an in-process HTTP server that records requests.

    Use `mockserver.url` as base_url for RemoteAgentClient.
    Use `mockserver.requests` to assert on what was sent.

    Example:
        _CaptureHandler.stub("GET", "/workspaces/test-ws/state", 200, {}, '{"status":"online"}')
        client = RemoteAgentClient(base_url=mockserver.url, workspace_id="test-ws")
        state = client.poll_state()
        assert mockserver.requests[0]["path"] == "/workspaces/test-ws/state"
    """
    import socket
    # Bind to port 0 → kernel picks an available port
    server = _CaptureServer(("127.0.0.1", 0), _CaptureHandler)
    port = server.server_address[1]
    _CaptureHandler.reset()

    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    yield server

    server.shutdown()
    t.join(timeout=5)


# ─── RemoteAgentClient fixture ───────────────────────────────────────────────

@pytest.fixture
def base_url(mockserver: _CaptureServer) -> str:
    """Return the base URL of the mock server."""
    return f"http://127.0.0.1:{mockserver.server_address[1]}"


@pytest.fixture
def client(base_url: str) -> RemoteAgentClient:
    """A RemoteAgentClient pointed at the mock server, isolated per test."""
    return RemoteAgentClient(base_url=base_url, workspace_id="test-ws")


@pytest.fixture
def ws_id() -> str:
    return "test-workspace-abc123"


# ─── Common stubs for _CaptureHandler ───────────────────────────────────────

def stub_ok(path: str, body: str = "", *, method: str = "GET", status: int = 200, headers: dict | None = None):
    """Register a 200 OK stub for a path. Convenience for common happy-path setup."""
    _CaptureHandler.stub(method, path, status, headers, body)


def stub_json(path: str, body: dict, *, method: str = "GET", status: int = 200):
    """Register a JSON response stub. Body is serialized to JSON automatically."""
    import json
    headers = {"Content-Type": "application/json"}
    _CaptureHandler.stub(method, path, status, headers, json.dumps(body))


# ─── Logger fixture ───────────────────────────────────────────────────────────

@pytest.fixture
def quiet_logger() -> logging.Logger:
    """A logger that emits nothing — useful for suppressing SDK chatter in tests."""
    logger = logging.getLogger("molecule_agent.test")
    logger.setLevel(logging.CRITICAL + 1)  # Above CRITICAL = never emits
    return logger