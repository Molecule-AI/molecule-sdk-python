"""GAP-05: retry / back-off for RemoteAgentClient GET calls.

Per TEST_GAP_ANALYSIS.md backlog item #5: the MCP server's platformGet()
has retry-on-429 with exponential back-off; the Python RemoteAgentClient had
no equivalent.  These tests cover the new _get_with_retry() helper and the
four wired-in GET endpoints (poll_state, pull_secrets, get_peers, discover_peer).

Test conventions (mirrors test_remote_agent.py):
  - MagicMock session — no live platform required.
  - FakeResponse for HTTP responses.
  - monkeypatch time.sleep to avoid real delays.
  - Each test covers one specific behaviour surface.
"""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from molecule_agent import RemoteAgentClient


# ---------------------------------------------------------------------------
# FakeResponse — minimal requests.Response stand-in
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(
        self,
        status_code: int = 200,
        json_body: Any = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.headers = headers or {}

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
    session = MagicMock()
    return RemoteAgentClient(
        workspace_id="ws-test-123",
        platform_url="http://platform.test",
        agent_card={"name": "test-agent"},
        token_dir=tmp_token_dir,
        session=session,
    )


# ---------------------------------------------------------------------------
# _get_with_retry — happy path
# ---------------------------------------------------------------------------


class TestGetWithRetryHappyPath:
    """Successful 2xx on first attempt — no retry, no sleep."""

    def test_200_returns_without_retrying(self, client: RemoteAgentClient):
        client._session.get.return_value = FakeResponse(200, {"data": "ok"})
        resp = client._get_with_retry("http://platform.test/foo")
        assert resp.status_code == 200
        assert client._session.get.call_count == 1

    def test_headers_passed_through(self, client: RemoteAgentClient):
        client._session.get.return_value = FakeResponse(200, {})
        client._get_with_retry(
            "http://platform.test/foo",
            headers={"Authorization": "Bearer tok"},
        )
        kwargs = client._session.get.call_args[1]
        assert kwargs["headers"]["Authorization"] == "Bearer tok"
        assert kwargs["timeout"] == 10.0


# ---------------------------------------------------------------------------
# _get_with_retry — 429 retry
# ---------------------------------------------------------------------------


class TestGetWithRetry429:
    """429 triggers retry; Retry-After header or exponential back-off used."""

    def _resp_429(self, retry_after: str | None = None) -> FakeResponse:
        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return FakeResponse(429, {}, headers=headers)

    def test_429_then_200_retries_and_returns_200(
        self, client: RemoteAgentClient, monkeypatch
    ):
        """First attempt 429, second attempt 200 — sleep between attempts."""
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        client._session.get.side_effect = [
            self._resp_429(),
            FakeResponse(200, {"data": "ok"}),
        ]

        resp = client._get_with_retry("http://platform.test/foo")

        assert resp.status_code == 200
        assert client._session.get.call_count == 2
        assert len(sleeps) == 1  # one sleep between attempt 1 and 2

    def test_429_retry_after_integer_seconds(
        self, client: RemoteAgentClient, monkeypatch
    ):
        """Retry-After with an integer seconds value is honoured exactly."""
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        client._session.get.side_effect = [
            self._resp_429(retry_after="5"),
            FakeResponse(200, {}),
        ]

        client._get_with_retry("http://platform.test/foo")

        assert sleeps == [5.0]  # Retry-After=5s → sleep 5 s

    def test_429_retry_after_float_seconds_rounds_up(
        self, client: RemoteAgentClient, monkeypatch
    ):
        """Retry-After with a float is rounded up (ceil) to the nearest second."""
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        client._session.get.side_effect = [
            self._resp_429(retry_after="2.7"),  # ceil(2.7) = 3
            FakeResponse(200, {}),
        ]

        client._get_with_retry("http://platform.test/foo")

        assert sleeps == [3.0]

    def test_429_retry_after_capped_at_30_seconds(
        self, client: RemoteAgentClient, monkeypatch
    ):
        """Retry-After > 30 s is capped to 30 s to avoid consuming a handler slot."""
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        client._session.get.side_effect = [
            self._resp_429(retry_after="120"),
            FakeResponse(200, {}),
        ]

        client._get_with_retry("http://platform.test/foo")

        assert sleeps == [30.0]  # capped at 30 s

    def test_429_exponential_backoff_jitter_first_attempt(
        self, client: RemoteAgentClient, monkeypatch
    ):
        """Without Retry-After, first back-off is 1 s ± 25 %."""
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        # Mock random.uniform directly
        import random
        monkeypatch.setattr(random, "random", lambda: 0.5)  # jitter = 0

        client._session.get.side_effect = [
            self._resp_429(),
            FakeResponse(200, {}),
        ]

        client._get_with_retry("http://platform.test/foo")

        # base=1.0, jitter=0 → exactly 1.0
        assert sleeps == [1.0]

    def test_429_exponential_backoff_second_attempt(
        self, client: RemoteAgentClient, monkeypatch
    ):
        """Second retry uses base=2 s (doubling), third uses base=4 s."""
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(random, "random", lambda: 0.5)  # zero jitter

        client._session.get.side_effect = [
            self._resp_429(),
            self._resp_429(),
            FakeResponse(200, {}),
        ]

        client._get_with_retry("http://platform.test/foo")

        assert sleeps == [1.0, 2.0]  # 1 s then 2 s

    def test_429_exhausts_max_retries_returns_429(
        self, client: RemoteAgentClient, monkeypatch
    ):
        """After max_retries attempts, the final 429 is returned (no sleep after last attempt)."""
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        # All attempts return 429
        client._session.get.return_value = self._resp_429()
        monkeypatch.setattr(random, "random", lambda: 0.5)

        resp = client._get_with_retry("http://platform.test/foo", max_retries=3)

        assert resp.status_code == 429
        assert client._session.get.call_count == 4  # 1 first + 3 retries = 4 total
        # Sleeps between first→second, second→third, third→fourth (attempt 4 is 429,
        # attempt >= max_retries so no sleep after)
        assert sleeps == [1.0, 2.0, 4.0]

    def test_non_429_error_does_not_retry(
        self, client: RemoteAgentClient, monkeypatch
    ):
        """500 on first attempt — no retry, returns immediately."""
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        client._session.get.return_value = FakeResponse(500, {"error": "boom"})

        resp = client._get_with_retry("http://platform.test/foo")

        assert resp.status_code == 500
        assert client._session.get.call_count == 1
        assert sleeps == []


# ---------------------------------------------------------------------------
# Wired-in retry: poll_state
# ---------------------------------------------------------------------------


class TestPollStateRetry:
    """poll_state uses _get_with_retry — retries 429, honours Retry-After."""

    def test_poll_state_retries_on_429_then_returns_state(
        self, client: RemoteAgentClient, monkeypatch
    ):
        client.save_token("t")
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(random, "random", lambda: 0.5)

        client._session.get.side_effect = [
            FakeResponse(429, {}, headers={"Retry-After": "2"}),
            FakeResponse(200, {"status": "online", "paused": False, "deleted": False}),
        ]

        state = client.poll_state()

        assert state is not None
        assert state.status == "online"
        assert client._session.get.call_count == 2
        assert sleeps == [2.0]

    def test_poll_state_429_exhausts_retries_raises(
        self, client: RemoteAgentClient, monkeypatch
    ):
        client.save_token("t")
        monkeypatch.setattr(time, "sleep", lambda s: None)
        client._session.get.return_value = FakeResponse(429, {}, headers={"Retry-After": "1"})

        with pytest.raises(Exception):
            client.poll_state()

        # All attempts exhausted
        assert client._session.get.call_count == 4

    def test_poll_state_404_does_not_retry(self, client: RemoteAgentClient, monkeypatch):
        """404 is not a 429 — retry never triggers."""
        client.save_token("t")
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        client._session.get.return_value = FakeResponse(404, {"deleted": True})

        state = client.poll_state()

        # 404 → WorkspaceState(deleted=True); no retry
        assert state is not None
        assert state.deleted is True
        assert client._session.get.call_count == 1
        assert sleeps == []


# ---------------------------------------------------------------------------
# Wired-in retry: pull_secrets
# ---------------------------------------------------------------------------


class TestPullSecretsRetry:
    """pull_secrets uses _get_with_retry."""

    def test_pull_secrets_retries_on_429(
        self, client: RemoteAgentClient, monkeypatch
    ):
        client.save_token("t")
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(random, "random", lambda: 0.5)

        client._session.get.side_effect = [
            FakeResponse(429, {}, headers={"Retry-After": "3"}),
            FakeResponse(200, {"API_KEY": "secret"}),
        ]

        secrets = client.pull_secrets()

        assert secrets == {"API_KEY": "secret"}
        assert sleeps == [3.0]


# ---------------------------------------------------------------------------
# Wired-in retry: get_peers
# ---------------------------------------------------------------------------


class TestGetPeersRetry:
    """get_peers uses _get_with_retry."""

    def test_get_peers_retries_on_429_exponential_backoff(
        self, client: RemoteAgentClient, monkeypatch
    ):
        client.save_token("t")
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(random, "random", lambda: 0.5)

        client._session.get.side_effect = [
            FakeResponse(429, {}),
            FakeResponse(429, {}),
            FakeResponse(200, [{"id": "peer-1", "name": "P1", "url": "http://p1"}]),
        ]

        peers = client.get_peers()

        assert len(peers) == 1
        assert peers[0].id == "peer-1"
        assert sleeps == [1.0, 2.0]


# ---------------------------------------------------------------------------
# Wired-in retry: discover_peer
# ---------------------------------------------------------------------------


class TestDiscoverPeerRetry:
    """discover_peer uses _get_with_retry."""

    def test_discover_peer_retries_on_429_then_returns_url(
        self, client: RemoteAgentClient, monkeypatch
    ):
        client.save_token("t")
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(random, "random", lambda: 0.5)

        client._session.get.side_effect = [
            FakeResponse(429, {}, headers={"Retry-After": "1"}),
            FakeResponse(200, {"url": "http://discovered:9000"}),
        ]

        url = client.discover_peer("target-1")

        assert url == "http://discovered:9000"
        assert sleeps == [1.0]

    def test_discover_peer_404_after_429_returns_none(
        self, client: RemoteAgentClient, monkeypatch
    ):
        """When 429 is retried and resolves to 404, discover_peer returns None (no error)."""
        client.save_token("t")
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(random, "random", lambda: 0.5)

        client._session.get.side_effect = [
            FakeResponse(429, {}),
            FakeResponse(404, {}),
        ]

        url = client.discover_peer("deleted-target")

        assert url is None
        assert client._session.get.call_count == 2

    def test_discover_peer_429_exhausts_retries_raises(
        self, client: RemoteAgentClient, monkeypatch
    ):
        """Exhausted 429 retries → raise_for_status() raises HTTPError."""
        client.save_token("t")
        monkeypatch.setattr(time, "sleep", lambda s: None)
        client._session.get.return_value = FakeResponse(429, {})

        with pytest.raises(Exception):
            client.discover_peer("rate-limited")

        assert client._session.get.call_count == 4
