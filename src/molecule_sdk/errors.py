"""Exception hierarchy for the Molecule SDK.

All SDK exceptions inherit from :class:`MoleculeError` so callers can catch
the entire hierarchy with a single ``except MoleculeError`` clause.
"""

from __future__ import annotations


class MoleculeError(Exception):
    """Base class for all Molecule SDK exceptions."""


class MoleculeConfigError(MoleculeError):
    """Raised when required environment variables are missing or invalid.

    Examples
    --------
    - ``MOL_API_KEY`` is not set before making an authenticated request.
    - ``MOL_PLATFORM_URL`` is set to a value that cannot be parsed as a URL.
    """


class MoleculeAPIError(MoleculeError):
    """Raised when the platform returns a non-2xx HTTP response.

    Attributes
    ----------
    status_code:
        The HTTP status code returned by the platform (e.g. 400, 404, 500).
    response:
        The parsed JSON body of the error response, or an empty dict if the
        body could not be decoded as JSON.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.status_code: int = status_code
        self.response: dict[str, object] = response

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"status_code={self.status_code!r}, "
            f"message={str(self)!r})"
        )


class MoleculeTimeoutError(MoleculeError):
    """Raised when a request to the platform exceeds its configured timeout.

    Timeouts are configured per-client; the default read timeout is 300 s to
    match the platform-side delegation timeout.
    """
