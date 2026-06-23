"""Error taxonomy for the Nexus Exchange SDK.

Mirrors the Rust SDK's split between *terminal* failures (the request was
rejected — don't retry) and *transient* failures (transport / 5xx — safe to
retry an idempotent request). Everything subclasses :class:`NexusExchangeError`.
"""

from __future__ import annotations


class NexusExchangeError(Exception):
    """Base class for all SDK errors."""

    #: Whether retrying the same idempotent request might succeed.
    transient: bool = False


class ApiError(NexusExchangeError):
    """The API returned a non-2xx response.

    Terminal for 4xx (the request was rejected); transient for 5xx / 408.
    """

    def __init__(
        self,
        status: int,
        body: str,
        *,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.code = code
        self.message = message or body
        super().__init__(f"Exchange API {status}: {self.message}")

    @property
    def transient(self) -> bool:  # type: ignore[override]
        return self.status >= 500 or self.status == 408


class TransportError(NexusExchangeError):
    """A connection / timeout error before any response was received."""

    transient = True


class MissingCredentialsError(NexusExchangeError):
    """A signed request was attempted without ``api_key`` / ``api_secret``."""
