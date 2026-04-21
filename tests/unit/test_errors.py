"""Unit tests for :mod:`molecule_sdk.errors` and :mod:`molecule_sdk._client`."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from molecule_sdk import _client
from molecule_sdk.errors import (
    MoleculeAPIError,
    MoleculeConfigError,
    MoleculeError,
    MoleculeTimeoutError,
)


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


def test_MoleculeError_is_base() -> None:
    """MoleculeError is the root of the exception hierarchy."""
    with pytest.raises(MoleculeError, match="boom"):
        raise MoleculeError("boom")


def test_MoleculeConfigError_inherits() -> None:
    """MoleculeConfigError should be caught by ``except MoleculeError``."""
    try:
        raise MoleculeConfigError("bad config")
    except MoleculeError:
        pass  # Expected


def test_MoleculeAPIError_has_status_code_and_response() -> None:
    """MoleculeAPIError should expose status_code and response attributes."""
    exc = MoleculeAPIError(
        "not found",
        status_code=404,
        response={"error": "workspace not found"},
    )
    assert exc.status_code == 404
    assert exc.response == {"error": "workspace not found"}
    assert str(exc) == "not found"


def test_MoleculeTimeoutError_inherits() -> None:
    """MoleculeTimeoutError should be caught by ``except MoleculeError``."""
    try:
        raise MoleculeTimeoutError("connection timed out")
    except MoleculeError:
        pass  # Expected


# ---------------------------------------------------------------------------
# auth_headers()
# ---------------------------------------------------------------------------


def test_auth_headers_raises_when_key_missing() -> None:
    """auth_headers should raise MoleculeConfigError when MOL_API_KEY is absent."""
    env = {_client._API_KEY_ENV: ""}
    with patch.dict(_client.os.environ, env, clear=True):
        with pytest.raises(MoleculeConfigError) as exc_info:
            _client.auth_headers()
        assert _client._API_KEY_ENV in str(exc_info.value)


def test_auth_headers_returns_bearer_token() -> None:
    """auth_headers should return a correct Authorization header."""
    token = "test-secret-key"
    env = {_client._API_KEY_ENV: token}
    with patch.dict(_client.os.environ, env, clear=True):
        headers = _client.auth_headers()
    assert headers == {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# _client.request()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_raises_on_non_2xx_with_status_and_body() -> None:
    """Non-2xx responses should raise MoleculeAPIError with status_code and response."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.is_success = False
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    mock_response.json.return_value = {"error": "overloaded"}

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.request = AsyncMock(return_value=mock_response)

    with patch.object(_client, "_CLIENT", None):
        with patch.object(_client, "get_client", return_value=mock_client):
            with pytest.raises(MoleculeAPIError) as exc_info:
                await _client.request("GET", "/health")

    assert exc_info.value.status_code == 503
    assert exc_info.value.response == {"error": "overloaded"}


@pytest.mark.asyncio
async def test_request_passes_through_on_2xx() -> None:
    """2xx responses should be returned directly without raising."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.is_success = True
    mock_response.status_code = 200

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.request = AsyncMock(return_value=mock_response)

    with patch.object(_client, "_CLIENT", None):
        with patch.object(_client, "get_client", return_value=mock_client):
            result = await _client.request("GET", "/health")

    assert result is mock_response


# ---------------------------------------------------------------------------
# get_client()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_client_lazy_creates() -> None:
    """get_client should lazily create and cache an httpx.AsyncClient."""
    mock_client_instance = MagicMock()
    mock_client_instance.aclose = AsyncMock()

    # Replace the httpx module reference inside _client so that both
    # AsyncClient and Timeout come from our mock, preventing any real I/O.
    fake_httpx = MagicMock()
    fake_httpx.AsyncClient.return_value = mock_client_instance
    fake_httpx.Timeout.return_value = MagicMock()

    original_client = _client._CLIENT
    original_httpx = _client.httpx
    _client._CLIENT = None
    _client.httpx = fake_httpx
    try:
        client1 = _client.get_client()
        client2 = _client.get_client()
        assert client1 is client2
        assert client1 is mock_client_instance
        assert _client._CLIENT is client1
        fake_httpx.AsyncClient.assert_called_once()
    finally:
        _client._CLIENT = original_client
        _client.httpx = original_httpx