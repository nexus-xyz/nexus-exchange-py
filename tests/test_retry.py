"""Unit tests for client-side retries + backoff (mocked httpx).

Mirrors the Rust SDK's ``tests/retry.rs`` and the TS SDK's ``test/retry.test.ts``
(ENG-5295): only idempotent (GET) requests are auto-retried, transient 5xx /
transport failures back off, 429 honors ``Retry-After`` (clamped), non-idempotent
writes are never auto-retried, and each retry re-signs with a fresh timestamp.

Backoff is recorded via an injected ``_sleep`` so tests stay instant, and
``jitter`` is disabled (or ``_rand`` pinned) so delays are deterministic.
"""

from __future__ import annotations

import httpx
import pytest

from nexus_exchange import ApiError, Client, Network, OrderRequest, RetryConfig

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def _client(delays: list[float], **retry_kw) -> Client:
    kw = {"jitter": False, **retry_kw}
    client = Client(Network.LOCAL, retry=RetryConfig(**kw))
    client._sleep = delays.append  # record instead of waiting
    return client


def _authed(delays: list[float], **retry_kw) -> Client:
    kw = {"jitter": False, **retry_kw}
    client = Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET, retry=RetryConfig(**kw))
    client._sleep = delays.append
    return client


_SUMMARY_URL = "http://localhost:9090/api/v1/markets/summary"


def test_retries_transient_5xx_on_get_then_succeeds(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=503)
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=503)
    httpx_mock.add_response(url=_SUMMARY_URL, json=[])
    delays: list[float] = []
    with _client(delays, min_delay=0.01) as client:
        assert client.fetch_market_summaries() == []
    assert len(httpx_mock.get_requests()) == 3, "one initial + two retries"
    assert len(delays) == 2, "slept once per retry"


def test_retries_transport_error_on_get(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("network down"))
    httpx_mock.add_response(url=_SUMMARY_URL, json=[])
    delays: list[float] = []
    with _client(delays, min_delay=0.01) as client:
        client.fetch_market_summaries()
    assert len(httpx_mock.get_requests()) == 2


def test_does_not_retry_non_idempotent_post(httpx_mock) -> None:
    httpx_mock.add_response(url="http://localhost:9090/api/v1/orders", status_code=503)
    delays: list[float] = []
    order = OrderRequest.limit("BTC-USDX-PERP", "Buy", "100", "1")
    with _authed(delays) as client:
        with pytest.raises(ApiError) as exc:
            client.create_order(order)
    assert exc.value.status == 503
    assert len(httpx_mock.get_requests()) == 1, "POST must not be auto-retried"
    assert delays == []


def test_does_not_retry_terminal_4xx(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=400)
    delays: list[float] = []
    with _client(delays) as client:
        with pytest.raises(ApiError) as exc:
            client.fetch_market_summaries()
    assert exc.value.status == 400
    assert len(httpx_mock.get_requests()) == 1


def test_gives_up_after_max_retries(httpx_mock) -> None:
    for _ in range(3):
        httpx_mock.add_response(url=_SUMMARY_URL, status_code=500)
    delays: list[float] = []
    with _client(delays, max_retries=2, min_delay=0.01) as client:
        with pytest.raises(ApiError) as exc:
            client.fetch_market_summaries()
    assert exc.value.status == 500
    assert len(httpx_mock.get_requests()) == 3, "initial + 2 retries"


def test_max_retries_zero_disables_retries(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=503)
    delays: list[float] = []
    with _client(delays, max_retries=0) as client:
        with pytest.raises(ApiError):
            client.fetch_market_summaries()
    assert len(httpx_mock.get_requests()) == 1


def test_retries_429_and_waits_at_least_retry_after(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=429, headers={"retry-after": "2"})
    httpx_mock.add_response(url=_SUMMARY_URL, json=[])
    delays: list[float] = []
    # Tiny backoff so the only way a delay reaches >= 2s is honoring Retry-After.
    with _client(delays, min_delay=0.001, max_delay=0.005) as client:
        client.fetch_market_summaries()
    assert len(httpx_mock.get_requests()) == 2
    assert delays[0] >= 2.0, f"expected >= 2s (Retry-After), got {delays[0]}"


def test_clamps_oversized_retry_after(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=429, headers={"retry-after": "3600"})
    httpx_mock.add_response(url=_SUMMARY_URL, json=[])
    delays: list[float] = []
    with _client(delays, min_delay=0.001, max_delay=0.005) as client:
        client.fetch_market_summaries()
    assert delays[0] <= 60.0, f"expected clamped to <= 60s, got {delays[0]}"


def test_429_apierror_carries_retry_after_ms(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=429, headers={"retry-after": "3"})
    delays: list[float] = []
    with _client(delays, max_retries=0) as client:
        with pytest.raises(ApiError) as exc:
            client.fetch_market_summaries()
    assert exc.value.status == 429
    assert exc.value.retry_after_ms == 3000


def test_each_retry_resigns_with_fresh_timestamp(httpx_mock) -> None:
    orders_url = "http://localhost:9090/api/v1/orders"
    httpx_mock.add_response(url=orders_url, status_code=503)
    httpx_mock.add_response(url=orders_url, json=[])
    delays: list[float] = []
    with _authed(delays, min_delay=0.01) as client:
        ticks = iter([1_000_000, 1_001_000])
        client._now_ms = lambda: next(ticks)
        client.fetch_open_orders()
    reqs = httpx_mock.get_requests()
    assert len(reqs) == 2
    assert reqs[0].headers["x-timestamp"] != reqs[1].headers["x-timestamp"], (
        "retry must re-sign, not reuse the first attempt's stale timestamp"
    )
