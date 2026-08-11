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


#: Body ``code`` values that identify a jurisdiction refusal when the
#: ``x-nexus-block-reason`` header is absent (spec v0.7.3, ``JurisdictionError``).
#:
#: This set is a *fallback discriminator*, not a closed enum. The spec is explicit
#: that the code list is open and that an unrecognized value must be treated as a
#: permanent refusal, so it is only consulted when the header — which by itself
#: proves the refusal came from a jurisdiction control — did not arrive.
JURISDICTION_CODES = frozenset({"US_RESTRICTED", "GEO_UNRESOLVED", "RESTRICTED_JURISDICTION"})


class RestrictedJurisdictionError(ApiError):
    """A ``403`` from a jurisdiction control (spec v0.7.3).

    Declared on every state-changing operation — placing, amending and batching
    orders, deposits, margin adjustments, credits and the faucet — and, for the
    sanctions case alone, on reads too.

    **Permanent for the caller's origin.** Terminal like any 4xx, but stronger:
    the same request will keep being refused from the same origin, so this is not
    a failure to surface and retry later. That distinction is why it gets a type
    rather than being left as a bare :class:`ApiError` with a ``code`` to
    string-match — the SDK's own error taxonomy exists to make "stop" and "try
    again" different classes rather than different field values.

    Subclasses :class:`ApiError`, so existing ``except ApiError`` handlers keep
    catching it unchanged and ``status`` / ``code`` / ``message`` / ``body`` all
    stay where they were.

    Branch on :attr:`block_reason`:

    ``RESTRICTED_JURISDICTION``
        Sanctions list. The only reason that can also be returned on a read.
    ``US_RESTRICTED``
        US write restriction; state-changing operations only.
    ``GEO_UNRESOLVED``
        The origin could not be resolved and the write failed closed. Not a
        statement about where the caller actually is.

    Treat an unrecognized reason exactly like a recognized one — the spec keeps
    the list open on purpose, so this class is raised on any value the header
    carries rather than only on the three above.

    That header check is on the *value*, not on presence: a header sent empty or
    whitespace-only is malformed and names no reason to branch on, so it is read
    as absent and the body ``code`` decides. Classification therefore still holds
    for the proxy shapes that actually occur — a dropped header with an intact
    body, or an intact header with a body that was absent, truncated at
    :class:`ApiError`'s 2000-character bound, or never JSON. It is lost only if a
    proxy blanks the header *and* the body is unusable, which no realistic
    deployment does; do not read the header branch as unconditional.

    Never match on ``message``: the spec marks its wording unstable.
    """

    def __init__(
        self,
        status: int,
        body: str,
        *,
        code: str | None = None,
        message: str | None = None,
        block_reason: str | None = None,
    ) -> None:
        super().__init__(status, body, code=code, message=message)
        #: The ``x-nexus-block-reason`` header, falling back to the body ``code``.
        #: The spec guarantees the two are identical; the header is preferred
        #: because it survives a body that was absent, truncated or not JSON.
        #: Whitespace-stripped, and empty reads as absent, so an equality test
        #: against a documented reason is not defeated by a padded header value.
        self.block_reason = block_reason or code


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


class PaginationError(NexusExchangeError):
    """Cursor pagination could not make progress.

    Raised when a list endpoint hands back the same ``X-Next-Cursor`` it was
    given: the cursor cannot advance, so continuing would re-request one page
    forever. The SDK stops and says so instead of hanging, and instead of
    returning quietly — a silent stop is indistinguishable from "that was the
    last page", which would hand the caller a truncated history it believes is
    complete.

    Terminal: a server that cannot advance its own cursor will not advance it on
    retry.
    """


class MissingCredentialsError(NexusExchangeError):
    """A signed request was attempted without ``api_key`` / ``api_secret``."""


class AuthError(NexusExchangeError):
    """A wallet-signing input was invalid (bad key, bad address, out of range).

    Mirrors the Rust SDK's ``Error::Auth`` — terminal, never retryable.
    """
