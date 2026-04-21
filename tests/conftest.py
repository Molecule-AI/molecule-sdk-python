"""Pytest fixtures and helpers for molecule_agent tests.

All fixtures are pytest-scoped unless noted.  No live platform required —
all HTTP is mocked via ``unittest.mock``.
"""
from __future__ import annotations

import stat
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from molecule_agent import RemoteAgentClient


# ---------------------------------------------------------------------------
# FakeResponse — minimal requests-shaped response
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(
        self,
        status_code: int = 200,
        json_body: Any = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_token_dir(tmp_path: Path) -> Path:
    return tmp_path / "molecule-token-cache"


@pytest.fixture
def client(tmp_token_dir: Path) -> RemoteAgentClient:
    """RemoteAgentClient with a MagicMock session for unit tests."""
    session = MagicMock()
    return RemoteAgentClient(
        workspace_id="ws-test-123",
        platform_url="http://platform.test",
        agent_card={"name": "test-agent"},
        token_dir=tmp_token_dir,
        session=session,
    )


# ---------------------------------------------------------------------------
# _CaptureHandler — minimal HTTP mock for integration tests
# ---------------------------------------------------------------------------


class _CaptureHandler:
    """Thread-local registry of HTTP stubs for use in integration tests.

    Registered stubs are checked in order (last-registered first); the first
    matching (method, path) pair wins.  Unmatched requests raise
    ``RuntimeError("no stub for {method} {path}")``.

    Usage::

        _CaptureHandler.clear()
        _CaptureHandler.stub("GET", "/foo", 200, {}, "body")
        with some_client:
            result = await some_client.get("/foo")
    """

    _stubs: list[tuple[str, str, int, dict[str, str], str]] = []

    @classmethod
    def clear(cls) -> None:
        cls._stubs.clear()

    @classmethod
    def stub(
        cls,
        method: str,
        path: str,
        status: int,
        headers: dict[str, str],
        body: str,
    ) -> None:
        cls._stubs.append((method, path, status, headers, body))

    @classmethod
    def handle(cls, method: str, url: str, **kwargs: Any) -> FakeResponse:
        for m, p, status, headers, body in reversed(cls._stubs):
            if m == method and p in url:
                return FakeResponse(status, json_body={}, text=body)
        raise RuntimeError(f"no stub for {method} {url}")