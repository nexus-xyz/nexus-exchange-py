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


class DecodeError(NexusExchangeError, ValueError):
    """A 2xx response body did not match the API contract.

    Raised while decoding, *after* a successful response: a spec-``required``
    field was absent, ``null``, or the wrong shape. The SDK fails loudly here
    rather than fabricating a value — a defaulted ``0`` fee rate, equity or
    cadence is a plausible-looking number the server never sent, which is worse
    than an exception. Fields the spec marks optional or nullable decode to
    ``None`` instead and never raise.

    Terminal: the payload will not improve on retry.

    Distinct from a plain :class:`ValueError`, which the SDK raises for *caller*
    error (an out-of-range ``limit``, an unknown ``window``) before any request
    is sent — so the two situations are told apart by type. This also subclasses
    :class:`ValueError` for backwards compatibility, since strict decoding
    predates the class and callers may already catch ``ValueError``.
    """


class MissingCredentialsError(NexusExchangeError):
    """A signed request was attempted without ``api_key`` / ``api_secret``."""


class AuthError(NexusExchangeError):
    """A wallet-signing input was invalid (bad key, bad address, out of range).

    Mirrors the Rust SDK's ``Error::Auth`` — terminal, never retryable.
    """
