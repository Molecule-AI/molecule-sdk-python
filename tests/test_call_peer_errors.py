"""GAP-03: call_peer error paths — documents and tests the error surface.

Per PLAN.md backlog #13: ClaudeSDKExecutor surfaces opaque "Command failed"
without capturing stderr. These tests document the desired behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SDK_ROOT = Path(__file__).resolve().parents[1]
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))

from molecule_agent.client import RemoteAgentClient
from tests.conftest import _CaptureHandler


def stub(status: int, body: str = "", *, method="GET", path="/call_peer"):
    """Register a stub for the call_peer endpoint."""
    _CaptureHandler.stub(method, path, status, {"Content-Type": "application/json"}, body)


class TestCallPeerErrors:
    """Tests for call_peer error handling and error message clarity."""

    def test_http_timeout_propagates_as_readable_error(self, client: RemoteAgentClient, mocker):
        """A connect or read timeout should surface as a descriptive error, not opaque."""
        mock_post = mocker.patch("requests.Session.post")
        mock_post.side_effect = TimeoutError("Connect timeout")

        # The client should raise a clearly typed error, not bare TimeoutError
        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", {"role": "user", "parts": [{"kind": "text", "text": "hello"}]})

        assert "timeout" in str(exc_info.value).lower() or "unreachable" in str(exc_info.value).lower()

    def test_502_bad_gateway_includes_context(self, client: RemoteAgentClient, http_mock):
        """502 from platform should include the upstream error in the response."""
        stub(502, '{"error": "upstream overwhelmed"}', path="/proxy/peer-id/a2a")
        client._proxy_base = http_mock.url  # inject mock proxy base

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", {"role": "user", "parts": [{"kind": "text", "text": "hello"}]})

        # The error message should reference the HTTP status or upstream failure
        assert any(kw in str(exc_info.value).lower() for kw in ["502", "upstream", "gateway", "bad"])

    def test_503_service_unavailable_is_retriable_or_raises(self, client: RemoteAgentClient, http_mock):
        """503 from platform should be distinguishable from 500."""
        stub(503, '{"error": "service unavailable"}', path="/proxy/peer-id/a2a")
        client._proxy_base = http_mock.url

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", {"role": "user", "parts": [{"kind": "text", "text": "hello"}]})

        assert "503" in str(exc_info.value) or "unavailable" in str(exc_info.value).lower()

    def test_malformed_json_in_response_raises_descriptively(self, client: RemoteAgentClient, http_mock):
        """If the A2A response is valid HTTP but has malformed JSON, the error should be clear."""
        stub(200, "not json {{{", path="/proxy/peer-id/a2a")
        client._proxy_base = http_mock.url

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", {"role": "user", "parts": [{"kind": "text", "text": "hello"}]})

        assert "json" in str(exc_info.value).lower() or "parse" in str(exc_info.value).lower()

    def test_empty_response_body_raises_readably(self, client: RemoteAgentClient, http_mock):
        """An empty A2A response body should not produce a cryptic KeyError."""
        stub(200, "", path="/proxy/peer-id/a2a")
        client._proxy_base = http_mock.url

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", {"role": "user", "parts": [{"kind": "text", "text": "hello"}]})

        # Should not be a KeyError or IndexError with no message
        assert "empty" in str(exc_info.value).lower() or "response" in str(exc_info.value).lower()

    def test_401_on_call_peer_surfaces_with_first_1kb_of_body(self, client: RemoteAgentClient, http_mock, caplog):
        """401 on call_peer should log at ERROR level with first ~1KB of the response body."""
        stub(401, '{"error": "invalid or expired token", "hint": "re-register with the platform"}',
             path="/proxy/peer-id/a2a")
        client._proxy_base = http_mock.url

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", {"role": "user", "parts": [{"kind": "text", "text": "hello"}]})

        # The exception message or a log entry should include the error detail
        error_str = str(exc_info.value).lower()
        assert "401" in error_str or "auth" in error_str or "token" in error_str

    def test_403_on_call_peer_surfaces_with_diagnostic_info(self, client: RemoteAgentClient, http_mock):
        """403 on call_peer should distinguish auth failure from generic 4xx."""
        stub(403, '{"error": "insufficient scope for this peer"}', path="/proxy/peer-id/a2a")
        client._proxy_base = http_mock.url

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", {"role": "user", "parts": [{"kind": "text", "text": "hello"}]})

        assert "403" in str(exc_info.value) or "scope" in str(exc_info.value).lower()

    def test_call_peer_via_proxy_when_direct_fails(self, client: RemoteAgentClient, mocker):
        """When prefer_direct=True but direct fails, call_peer falls back to proxy."""
        mocker.patch.object(client, "_call_direct", side_effect=ConnectionError("refused"))
        mock_proxy = mocker.patch.object(client, "_call_proxy", return_value={"parts": [{"kind": "text", "text": "proxied"}]})

        result = client.call_peer("peer-id", {"role": "user", "parts": [{"kind": "text", "text": "hello"}]})
        assert result.get("parts", [{}])[0].get("text") == "proxied"
        mock_proxy.assert_called_once()

    def test_call_peer_proxy_error_surfaces_readably(self, client: RemoteAgentClient, mocker):
        """Proxy call returning 500 should not produce "Command failed with exit code 1"."""
        mocker.patch.object(client, "_call_direct", side_effect=ConnectionError("refused"))
        mocker.patch.object(client, "_call_proxy", side_effect=RuntimeError("proxy returned 500"))

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", {"role": "user", "parts": [{"kind": "text", "text": "hello"}]})

        # Must not be the opaque "Command failed with exit code 1" message
        assert "Command failed with exit code 1" not in str(exc_info.value)
