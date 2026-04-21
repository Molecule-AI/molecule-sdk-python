"""GAP-03: call_peer error paths — documents and tests the error surface.

Per PLAN.md backlog #13: ClaudeSDKExecutor surfaces opaque "Command failed"
without capturing stderr. These tests document the desired behavior for the
SDK's call_peer method in molecule_agent/client.py.

The tests use the ``client`` fixture (MagicMock session) to simulate error
conditions without a live platform.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SDK_ROOT = Path(__file__).resolve().parents[1]
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))

from molecule_agent.client import RemoteAgentClient
from tests.conftest import FakeResponse


class TestCallPeerErrors:
    """Tests for call_peer error handling and error message clarity."""

    def test_http_timeout_propagates_as_readable_error(self, client: RemoteAgentClient):
        """A connect or read timeout should surface as a descriptive error, not opaque."""
        client._session.post.side_effect = TimeoutError("Connect timeout")

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", "hello")

        err_str = str(exc_info.value).lower()
        assert "timeout" in err_str or "unreachable" in err_str

    def test_connection_refused_propagates_as_readable_error(self, client: RemoteAgentClient):
        """A connection refused error should propagate with context."""
        client._session.post.side_effect = ConnectionError("Connection refused")

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", "hello")

        err_str = str(exc_info.value).lower()
        assert "refused" in err_str or "connection" in err_str

    def test_502_bad_gateway_includes_context(self, client: RemoteAgentClient):
        """502 from platform should include the HTTP status or upstream error."""
        client._session.post.return_value = FakeResponse(
            502, {"error": "upstream overwhelmed"}
        )

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", "hello")

        err_str = str(exc_info.value).lower()
        assert any(kw in err_str for kw in ["502", "upstream", "gateway", "bad"])

    def test_503_service_unavailable_is_retriable_or_raises(self, client: RemoteAgentClient):
        """503 from platform should be distinguishable from 500."""
        client._session.post.return_value = FakeResponse(
            503, {"error": "service unavailable"}
        )

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", "hello")

        err_str = str(exc_info.value)
        assert "503" in err_str or "unavailable" in err_str.lower()

    def test_500_internal_error_raises(self, client: RemoteAgentClient):
        """500 from platform should raise with status code."""
        client._session.post.return_value = FakeResponse(
            500, {"error": "internal error"}
        )

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", "hello")

        err_str = str(exc_info.value)
        assert "500" in err_str or "internal" in err_str.lower()

    def test_401_on_call_peer_surfaces_with_auth_context(self, client: RemoteAgentClient):
        """401 on call_peer should surface with auth context."""
        client._session.post.return_value = FakeResponse(
            401, {"error": "invalid or expired token", "hint": "re-register"}
        )

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", "hello")

        err_str = str(exc_info.value).lower()
        assert "401" in err_str or "auth" in err_str or "token" in err_str

    def test_403_on_call_peer_surfaces_with_diagnostic_info(self, client: RemoteAgentClient):
        """403 on call_peer should distinguish auth failure from generic 4xx."""
        client._session.post.return_value = FakeResponse(
            403, {"error": "insufficient scope for this peer"}
        )

        with pytest.raises(Exception) as exc_info:
            client.call_peer("peer-id", "hello")

        err_str = str(exc_info.value)
        assert "403" in err_str or "scope" in err_str.lower()

    def test_200_with_json_body_returns_result(self, client: RemoteAgentClient):
        """A successful A2A response should be returned as a dict."""
        client._session.post.return_value = FakeResponse(
            200, {"jsonrpc": "2.0", "result": {"ok": True}}
        )

        result = client.call_peer("peer-id", "hello")

        assert result["result"]["ok"] is True

    def test_call_peer_via_proxy_when_direct_fails(self, client: RemoteAgentClient):
        """When prefer_direct=True but direct fails, call_peer falls back to proxy.

        - discover_peer finds a cached URL (cache hit) → direct POST attempted
        - Direct POST raises ConnectionError → exception caught, cache invalidated
        - Proxy POST succeeds → result returned
        """
        # Seed the cache so discover_peer returns a URL (cache hit, no GET needed)
        import time
        client._url_cache["peer-id"] = ("http://dead.peer:8000", time.time() + 60)

        post_calls = []

        def track_post(*args, **kwargs):
            post_calls.append((args, kwargs))
            if len(post_calls) == 1:
                raise ConnectionError("refused")
            return FakeResponse(200, {"parts": [{"kind": "text", "text": "proxied"}]})

        client._session.post.side_effect = track_post

        result = client.call_peer("peer-id", "hello")

        assert result.get("parts", [{}])[0].get("text") == "proxied"
        assert len(post_calls) == 2, f"expected 2 POST calls, got {len(post_calls)}"
        # First URL should be the cached dead peer URL (direct)
        assert "dead.peer" in str(post_calls[0][0][0])
        # Second URL should be the platform proxy (fallback)
        assert "/workspaces/peer-id/a2a" in str(post_calls[1][0][0])

    def test_call_peer_prefer_direct_false_skips_discover(self, client: RemoteAgentClient):
        """With prefer_direct=False, call_peer should skip discover and go to proxy."""
        client.save_token("secret-token-abc")
        client._session.post.return_value = FakeResponse(200, {"ok": True})

        result = client.call_peer("peer-id", "hello", prefer_direct=False)

        assert result == {"ok": True}
        call_url = client._session.post.call_args[0][0]
        assert "/workspaces/peer-id/a2a" in call_url

    def test_call_peer_includes_auth_headers(self, client: RemoteAgentClient):
        """call_peer proxy calls should include Authorization and X-Workspace-ID."""
        client.save_token("secret-token-abc")
        client._session.post.return_value = FakeResponse(200, {})

        client.call_peer("peer-id", "hello")

        call_kwargs = client._session.post.call_args[1]
        assert "Authorization" in call_kwargs["headers"]
        assert call_kwargs["headers"]["X-Workspace-ID"] == "ws-test-123"
        assert call_kwargs["headers"]["Content-Type"] == "application/json"

    def test_call_peer_json_rpc_envelope_format(self, client: RemoteAgentClient):
        """The POST body should match A2A JSON-RPC message/send format."""
        client.save_token("tok")
        client._session.post.return_value = FakeResponse(200, {"result": {"ok": True}})

        client.call_peer("peer-id", "hello world")

        body = client._session.post.call_args[1]["json"]
        assert body["jsonrpc"] == "2.0"
        assert body["method"] == "message/send"
        assert "messageId" in body["params"]["message"]
        assert body["params"]["message"]["role"] == "user"
        assert body["params"]["message"]["parts"][0]["kind"] == "text"
        assert body["params"]["message"]["parts"][0]["text"] == "hello world"