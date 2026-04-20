"""Pytest fixtures and configuration for the molecule-sdk-python test suite."""

from __future__ import annotations

import os
from typing import Generator

import httpx
import pytest

# Set dummy credentials so that auth_headers() never raises during tests.
# Tests that exercise auth-error paths should clear or mutate these directly.
os.environ.setdefault("MOL_API_KEY", "test-api-key-abc123")
os.environ.setdefault("MOL_PLATFORM_URL", "http://testplatform:8080")


@pytest.fixture
def test_base_url() -> str:
    """Return the base URL used by integration tests."""
    return os.environ["MOL_PLATFORM_URL"]


@pytest.fixture
def async_client(test_base_url: str) -> Generator[httpx.AsyncClient, None, None]:
    """Provide a shared :class:`httpx.AsyncClient` for integration tests.

    The client is configured with the ``test_base_url`` so that integration
    tests can make real HTTP requests against a local platform mock or the
    live platform when ``MOL_PLATFORM_URL`` points to it.

    Unit tests should *not* use this fixture; instead, patch
    :func:`molecule_sdk._client.request` directly (see ``tests/unit/``).
    """
    client = httpx.AsyncClient(
        base_url=test_base_url,
        timeout=httpx.Timeout(30.0, read=300.0, write=30.0),
    )
    yield client
    # Clean up on fixture teardown.
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — either the loop was already closed or we are in
        # a sync context.  Try to close synchronously.
        try:
            asyncio.run(client.aclose())
        except RuntimeError:
            pass
    else:
        loop.run_until_complete(client.aclose())
