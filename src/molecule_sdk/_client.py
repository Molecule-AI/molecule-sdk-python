"""Shared :class:`httpx.AsyncClient` factory and auth helpers.

All SDK modules share a single client instance managed at module level.
Call :func:`get_client()` to obtain the current client, or
:func:`close_client()` to shut it down cleanly.

Authentication
--------------
``MOL_API_KEY`` must be set in the environment before the first request.
:meth:`auth_headers()` raises :class:`MoleculeConfigError` if it is missing.

Base URL
--------
``MOL_PLATFORM_URL`` controls the platform API root (default:
``http://platform:8080``). It is read once at import time.

KI-005 note (a2a-sdk 0.3→1.x migration)
---------------------------------------
The SDK itself does NOT import ``a2a-sdk`` — it wraps the platform HTTP API
directly. The a2a-sdk migration therefore does not directly affect this package.
As a precaution, ``httpx`` is pinned ``>=0.27.0,<1.0`` in ``pyproject.toml`` and
``requirements.txt`` until httpx 1.x compatibility with the platform has been
confirmed.
"""

from __future__ import annotations

import logging
import os
from typing import Any, ClassVar

import httpx

from molecule_sdk.errors import MoleculeConfigError

logger: logging.Logger = logging.getLogger("molecule_sdk")

#: Default platform API root.
DEFAULT_BASE_URL: str = "http://platform:8080"

#: Environment variable name for the platform base URL.
_BASE_URL_ENV: str = "MOL_PLATFORM_URL"

#: Environment variable name for the API key.
_API_KEY_ENV: str = "MOL_API_KEY"

#: httpx connect timeout in seconds.
_CONNECT_TIMEOUT: float = 30.0

#: httpx read timeout in seconds (matches platform delegation timeout).
_READ_TIMEOUT: float = 300.0

#: httpx write timeout in seconds.
_WRITE_TIMEOUT: float = 30.0

#: httpx pool timeout in seconds (for connection acquisition from pool).
_POOL_TIMEOUT: float = 5.0

_BASE_URL: str = os.environ.get(_BASE_URL_ENV, DEFAULT_BASE_URL)
_CLIENT: httpx.AsyncClient | None = None


def auth_headers() -> dict[str, str]:
    """Return the Authorization header for the current request.

    Returns
    -------
    dict[str, str]
        ``{"Authorization": "Bearer <MOL_API_KEY>"}``

    Raises
    ------
    MoleculeConfigError
        If ``MOL_API_KEY`` is not set in the environment.
    """
    key = os.environ.get(_API_KEY_ENV)
    if not key:
        raise MoleculeConfigError(
            f"Missing required environment variable '{_API_KEY_ENV}'. "
            "Set it before making authenticated requests."
        )
    return {"Authorization": f"Bearer {key}"}


def get_client() -> httpx.AsyncClient:
    """Return the shared :class:`httpx.AsyncClient` instance.

    The client is lazily created on first access with connection pooling
    enabled. The same instance is returned on subsequent calls.
    """
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_READ_TIMEOUT,
                write=_WRITE_TIMEOUT,
                pool=_POOL_TIMEOUT,
            ),
            headers={"User-Agent": f"molecule-sdk-python/{__import__('molecule_sdk').__version__}"},
        )
        logger.debug("Created shared httpx.AsyncClient (base_url=%s)", _BASE_URL)
    return _CLIENT


async def close_client() -> None:
    """Close the shared client and release all open connections.

    Call this when you are done using the SDK to avoid resource leaks.
    Does nothing if no client has been created yet.
    """
    global _CLIENT
    if _CLIENT is not None:
        await _CLIENT.aclose()
        _CLIENT = None
        logger.debug("Closed shared httpx.AsyncClient")


async def build_request(
    method: str,
    url: str,
    *,
    authenticated: bool = True,
    **kwargs: Any,
) -> httpx.Request:
    """Build an :class:`httpx.Request` with auth headers and SDK defaults.

    Parameters
    ----------
    method:
        HTTP method (e.g. ``"GET"``, ``"POST"``).
    url:
        Path to append to :data:`_BASE_URL`. Must start with ``"/"``.
    authenticated:
        If True (the default), ``Authorization`` headers are injected from
        :func:`auth_headers`.
    **kwargs:
        Forwarded to :meth:`httpx.AsyncClient.build_request`.
    """
    headers = dict(kwargs.pop("headers", {}))
    if authenticated:
        headers.update(auth_headers())

    client = get_client()
    request = client.build_request(method, url, headers=headers, **kwargs)
    return request


async def request(
    method: str,
    url: str,
    *,
    authenticated: bool = True,
    **kwargs: Any,
) -> httpx.Response:
    """Send an HTTP request and return the response.

    Handles auth injection and timeout defaults. Raises
    :class:`MoleculeAPIError` on non-2xx responses.

    Parameters
    ----------
    method:
        HTTP method (e.g. ``"GET"``, ``"POST"``).
    url:
        Path to append to the platform base URL.
    authenticated:
        If True, inject the ``Authorization`` header.
    **kwargs:
        Forwarded to :meth:`httpx.AsyncClient.request`.

    Returns
    -------
    httpx.Response

    Raises
    ------
    MoleculeAPIError
        When the platform returns a status code outside the 2xx range.
    """
    from molecule_sdk.errors import MoleculeAPIError

    headers = dict(kwargs.pop("headers", {}))
    if authenticated:
        headers.update(auth_headers())

    client = get_client()
    response = await client.request(method, url, headers=headers, **kwargs)

    if not response.is_success:
        try:
            body: dict[str, object] = response.json()
        except Exception:
            body = {}
        raise MoleculeAPIError(
            f"Platform returned {response.status_code}: {response.text[:200]}",
            status_code=response.status_code,
            response=body,
        )

    return response
