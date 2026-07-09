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

    Terminal for most 4xx (the request was rejected); transient for 5xx / 408 /
    429 (server, timeout, or rate limited — worth retrying an idempotent request).
    """

    def __init__(
        self,
        status: int,
        body: str,
        *,
        code: str | None = None,
        message: str | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.code = code
        self.message = message or body
        #: Parsed ``Retry-After`` hint (milliseconds), when the server sent one
        #: on a ``429``. The client honors it automatically when retrying an
        #: idempotent request; a caller retrying a non-idempotent request by
        #: hand can read it to pace the retry.
        self.retry_after_ms = retry_after_ms
        super().__init__(f"Exchange API {status}: {self.message}")

    @property
    def transient(self) -> bool:  # type: ignore[override]
        # 5xx / 408 (server or timeout) and 429 (rate limited) are worth
        # retrying an idempotent request; every other 4xx is terminal.
        return self.status >= 500 or self.status in (408, 429)


class TransportError(NexusExchangeError):
    """A connection / timeout error before any response was received."""

    transient = True


class MissingCredentialsError(NexusExchangeError):
    """A signed request was attempted without ``api_key`` / ``api_secret``."""


class AuthError(NexusExchangeError):
    """A wallet-signing input was invalid (bad key, bad address, out of range).

    Mirrors the Rust SDK's ``Error::Auth`` — terminal, never retryable.
    """
