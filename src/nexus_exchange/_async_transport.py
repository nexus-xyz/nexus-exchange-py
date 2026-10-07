"""The async I/O half of :class:`~nexus_exchange.AsyncClient` (ENG-20361).

Everything that is not I/O (configuration, signing, the retry policy, the error
mapping) is :class:`~nexus_exchange.client._ClientCore`, shared with the sync
:class:`~nexus_exchange.Client`. This module holds only what has to differ: an
``httpx.AsyncClient``, an awaited request call, and a non-blocking
``asyncio.sleep`` between retries. The endpoint methods live in the generated
``async_client.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

from .auth import AgentSigner
from .client import (
    DEFAULT_TIMEOUT,
    RetryConfig,
    _ClientCore,
    _decode_body,
    _next_cursor,
)
from .networks import Network, NetworkConfig

_Self = TypeVar("_Self", bound="_AsyncTransport")


class _AsyncTransport(_ClientCore):
    """Lifecycle and the request loop for :class:`~nexus_exchange.AsyncClient`."""

    def __init__(
        self,
        network: Network | NetworkConfig | str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_version: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.AsyncClient | None = None,
        retry: RetryConfig | None = None,
        agent: AgentSigner | None = None,
    ) -> None:
        super().__init__(
            network,
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            api_version=api_version,
            retry=retry,
            agent=agent,
        )
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        # Never `time.sleep`: a retry backoff must yield the event loop, not
        # block it. Injectable so tests record delays without waiting.
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    # -- lifecycle --------------------------------------------------------
    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient`` if this client created it."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self: _Self) -> _Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- request plumbing -------------------------------------------------
    async def _send(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        body: Any | None = None,
        signed: bool = False,
        bearer: str | None = None,
    ) -> httpx.Response:
        """The async twin of ``Client._send``: same steps, awaited I/O."""
        req = self._prepare(method, path, query=query, body=body, signed=signed, bearer=bearer)
        attempt = 0
        while True:
            headers = self._attempt_headers(req)
            try:
                resp = await self._http.request(
                    method, req.url, headers=headers, content=req.content
                )
            except httpx.HTTPError as exc:
                delay = self._transport_retry_delay(req, attempt, exc)
            else:
                retry_delay = self._response_retry_delay(req, attempt, resp)
                if retry_delay is None:
                    return resp
                delay = retry_delay
            await self._sleep(delay)
            attempt += 1

    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        body: Any | None = None,
        signed: bool = False,
        bearer: str | None = None,
    ) -> Any:
        resp = await self._send(method, path, query=query, body=body, signed=signed, bearer=bearer)
        return _decode_body(resp)

    async def _request_page(
        self,
        path: str,
        *,
        query: str = "",
        signed: bool = False,
    ) -> tuple[Any, str | None]:
        """``GET`` one page: the decoded body and the ``X-Next-Cursor`` header."""
        resp = await self._send("GET", path, query=query, signed=signed)
        return _decode_body(resp), _next_cursor(resp)
