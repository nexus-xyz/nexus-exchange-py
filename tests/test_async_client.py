"""AsyncClient (ENG-20361): the same surface and behaviour as Client, awaitable.

``AsyncClient``'s endpoint methods are generated from ``Client``'s by
``scripts/gen_async_client.py``, and its signing, retry policy and error mapping
are ``Client``'s own code (``_ClientCore``). These tests pin that the two cannot
drift: the generated file is fresh, the public surface matches name for name and
argument for argument, and signing, retries and pagination behave as the sync
tests in ``test_client.py`` / ``test_retry.py`` / ``test_pagination.py`` require.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
from pathlib import Path
from typing import Any

import httpx
import pytest

from nexus_exchange import (
    TRADES_LIMIT_MAX,
    ApiError,
    AsyncClient,
    Client,
    MissingCredentialsError,
    Network,
    OrderRequest,
    PaginationError,
    RetryConfig,
    TransportError,
)

REPO = Path(__file__).resolve().parent.parent
BASE = "http://localhost:9090"
_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_SUMMARY_URL = f"{BASE}/markets/summary"
_FILLS_URL = f"{BASE}/fills"


# -- generated file and surface parity -----------------------------------------


def test_generated_async_client_is_fresh() -> None:
    spec = importlib.util.spec_from_file_location(
        "gen_async_client", REPO / "scripts" / "gen_async_client.py"
    )
    assert spec is not None and spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    current = (REPO / "src" / "nexus_exchange" / "async_client.py").read_text()
    assert current == gen.render(), (
        "src/nexus_exchange/async_client.py is stale: run `python scripts/gen_async_client.py`"
    )


#: Lifecycle differs by design: `close`/`with` versus `aclose`/`async with`.
_LIFECYCLE = {"close", "aclose"}


def _public(cls: type) -> dict[str, Any]:
    return {
        name: member
        for name, member in inspect.getmembers(cls)
        if not name.startswith("_") and name not in _LIFECYCLE
    }


def test_async_client_exposes_the_same_public_surface() -> None:
    sync, aio = _public(Client), _public(AsyncClient)
    assert sorted(sync) == sorted(aio)
    assert len(sync) > 80, "sanity: the whole surface is compared, not an empty one"


@pytest.mark.parametrize(
    "name", sorted(n for n, m in _public(Client).items() if inspect.isfunction(m))
)
def test_each_method_has_the_same_signature(name: str) -> None:
    sync = inspect.signature(getattr(Client, name))
    aio = inspect.signature(getattr(AsyncClient, name))
    # Parameters match exactly, annotations included. Only the return type may
    # differ, and only for the iter_* walkers (Iterator -> AsyncIterator).
    assert list(sync.parameters.values()) == list(aio.parameters.values())
    if name.startswith("iter_"):
        assert aio.return_annotation == f"Async{sync.return_annotation}"
    else:
        assert aio.return_annotation == sync.return_annotation


@pytest.mark.parametrize(
    "name", sorted(n for n, m in _public(Client).items() if inspect.isfunction(m))
)
def test_methods_are_coroutines_except_the_walkers(name: str) -> None:
    method = getattr(AsyncClient, name)
    assert inspect.iscoroutinefunction(method) is not name.startswith("iter_")


def test_constructor_matches_except_the_http_client_type() -> None:
    sync = inspect.signature(Client.__init__).parameters
    aio = inspect.signature(AsyncClient.__init__).parameters
    assert list(sync) == list(aio)
    for name in sync:
        if name == "http_client":
            assert aio[name].annotation == "httpx.AsyncClient | None"
            continue
        assert sync[name] == aio[name], name


# -- lifecycle -----------------------------------------------------------------


async def test_async_with_closes_an_owned_http_client() -> None:
    async with AsyncClient(Network.LOCAL) as client:
        http = client._http
    assert http.is_closed


async def test_aclose_leaves_a_caller_supplied_http_client_open() -> None:
    http = httpx.AsyncClient()
    async with AsyncClient(Network.LOCAL, http_client=http):
        pass
    assert not http.is_closed
    await http.aclose()


def test_default_backoff_sleep_is_asyncio_sleep() -> None:
    # A blocking `time.sleep` here would stall the whole event loop on a retry.
    assert AsyncClient(Network.LOCAL)._sleep is asyncio.sleep


# -- signing -------------------------------------------------------------------


def _fixed_clock(client: Client | AsyncClient) -> None:
    client._now_ms = lambda: 1_700_000_000_000


async def test_signed_get_matches_sync_signature_bytes(httpx_mock) -> None:
    url = f"{BASE}/orders/o-1?market_id=BTC-USDX-PERP"
    httpx_mock.add_response(url=url, json={}, is_reusable=True)
    with Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET) as sync:
        _fixed_clock(sync)
        sync.fetch_order("o-1", "BTC-USDX-PERP")
    async with AsyncClient(Network.LOCAL, api_key="nx_test", api_secret=_SECRET) as aio:
        _fixed_clock(aio)
        await aio.fetch_order("o-1", "BTC-USDX-PERP")

    s, a = httpx_mock.get_requests()
    assert s.headers["x-timestamp"] == a.headers["x-timestamp"] == "1700000000000"
    assert s.headers["x-signature"] == a.headers["x-signature"]
    assert s.headers["x-api-key"] == a.headers["x-api-key"] == "nx_test"


async def test_signed_post_matches_sync_signature_and_body(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{BASE}/orders", json={}, is_reusable=True)
    order = OrderRequest.limit("BTC-USDX-PERP", "Buy", "100", "1")
    with Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET) as sync:
        _fixed_clock(sync)
        sync.create_order(order)
    async with AsyncClient(Network.LOCAL, api_key="nx_test", api_secret=_SECRET) as aio:
        _fixed_clock(aio)
        await aio.create_order(order)

    s, a = httpx_mock.get_requests()
    assert s.content == a.content and a.content
    assert s.headers["content-type"] == a.headers["content-type"] == "application/json"
    assert s.headers["x-signature"] == a.headers["x-signature"]


async def test_default_headers_ride_along(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, json=[])
    async with AsyncClient(Network.LOCAL, api_version="v9.9.9") as client:
        await client.fetch_markets_summary()
    req = httpx_mock.get_request()
    assert req.headers["user-agent"].startswith("nexus-exchange-py/")
    assert req.headers["x-nexus-api-version"] == "v9.9.9"


async def test_signed_without_credentials_raises() -> None:
    async with AsyncClient(Network.LOCAL) as client:
        with pytest.raises(MissingCredentialsError):
            await client.fetch_balance()


# -- retry ---------------------------------------------------------------------


def _client(delays: list[float], *, signed: bool = False, **retry_kw: Any) -> AsyncClient:
    creds = {"api_key": "nx_test", "api_secret": _SECRET} if signed else {}
    client = AsyncClient(Network.LOCAL, retry=RetryConfig(**{"jitter": False, **retry_kw}), **creds)

    async def record(delay: float) -> None:
        delays.append(delay)

    client._sleep = record
    return client


async def test_retries_transient_5xx_on_get_then_succeeds(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=503)
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=503)
    httpx_mock.add_response(url=_SUMMARY_URL, json=[])
    delays: list[float] = []
    async with _client(delays, min_delay=0.01) as client:
        assert await client.fetch_markets_summary() == []
    assert len(httpx_mock.get_requests()) == 3
    assert delays == [0.01, 0.02], "exponential backoff, awaited once per retry"


async def test_retries_transport_error_on_get(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("network down"))
    httpx_mock.add_response(url=_SUMMARY_URL, json=[])
    delays: list[float] = []
    async with _client(delays, min_delay=0.01) as client:
        await client.fetch_markets_summary()
    assert len(httpx_mock.get_requests()) == 2


async def test_transport_error_surfaces_as_transport_error(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("network down"))
    async with AsyncClient(Network.LOCAL) as client:
        with pytest.raises(TransportError):
            await client.fetch_markets_summary()


async def test_does_not_retry_non_idempotent_post(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{BASE}/orders", status_code=503)
    delays: list[float] = []
    order = OrderRequest.limit("BTC-USDX-PERP", "Buy", "100", "1")
    async with _client(delays, signed=True) as client:
        with pytest.raises(ApiError) as exc:
            await client.create_order(order)
    assert exc.value.status == 503
    assert len(httpx_mock.get_requests()) == 1, "POST must not be auto-retried"
    assert delays == []


async def test_gives_up_after_max_retries(httpx_mock) -> None:
    for _ in range(3):
        httpx_mock.add_response(url=_SUMMARY_URL, status_code=500)
    delays: list[float] = []
    async with _client(delays, max_retries=2, min_delay=0.01) as client:
        with pytest.raises(ApiError) as exc:
            await client.fetch_markets_summary()
    assert exc.value.status == 500
    assert len(httpx_mock.get_requests()) == 3


async def test_retries_are_off_by_default(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=503)
    async with AsyncClient(Network.LOCAL) as client:
        with pytest.raises(ApiError):
            await client.fetch_markets_summary()
    assert len(httpx_mock.get_requests()) == 1


async def test_retries_429_and_waits_at_least_retry_after(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=429, headers={"retry-after": "2"})
    httpx_mock.add_response(url=_SUMMARY_URL, json=[])
    delays: list[float] = []
    async with _client(delays, min_delay=0.001, max_delay=0.005) as client:
        await client.fetch_markets_summary()
    assert delays == [2.0]


async def test_clamps_oversized_retry_after(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=429, headers={"retry-after": "3600"})
    httpx_mock.add_response(url=_SUMMARY_URL, json=[])
    delays: list[float] = []
    async with _client(delays, min_delay=0.001, max_delay=0.005) as client:
        await client.fetch_markets_summary()
    assert delays == [60.0]


async def test_429_apierror_carries_retry_after_ms(httpx_mock) -> None:
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=429, headers={"retry-after": "3"})
    async with AsyncClient(Network.LOCAL) as client:
        with pytest.raises(ApiError) as exc:
            await client.fetch_markets_summary()
    assert exc.value.status == 429
    assert exc.value.retry_after_ms == 3000


async def test_each_retry_resigns_with_fresh_timestamp(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{BASE}/orders", status_code=503)
    httpx_mock.add_response(url=f"{BASE}/orders", json=[])
    delays: list[float] = []
    async with _client(delays, signed=True, min_delay=0.01) as client:
        ticks = iter([1_000_000, 1_001_000])
        client._now_ms = lambda: next(ticks)
        await client.fetch_open_orders()
    first, second = httpx_mock.get_requests()
    assert first.headers["x-timestamp"] != second.headers["x-timestamp"]


async def test_retry_sleep_does_not_block_the_event_loop(httpx_mock) -> None:
    # With the real asyncio.sleep, another task runs while a retry backs off.
    httpx_mock.add_response(url=_SUMMARY_URL, status_code=503)
    httpx_mock.add_response(url=_SUMMARY_URL, json=[])
    ran: list[str] = []

    async def other() -> None:
        ran.append("other")

    retry = RetryConfig(min_delay=0.05, jitter=False)
    async with AsyncClient(Network.LOCAL, retry=retry) as client:
        task = asyncio.ensure_future(other())
        await client.fetch_markets_summary()
        assert ran == ["other"], "the backoff yielded to the loop"
        await task


# -- pagination ----------------------------------------------------------------


def _fill(fill_id: str) -> dict[str, object]:
    return {
        "id": fill_id,
        "order_id": f"o-{fill_id}",
        "market_id": "BTC-USDX-PERP",
        "side": "buy",
        "price": "50000",
        "size": "1",
        "fee": "0.5",
        "timestamp": 1776033900000,
    }


def _signed() -> AsyncClient:
    return AsyncClient(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


async def test_iter_my_trades_follows_the_cursor_to_the_last_page(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{_FILLS_URL}?limit=2", json=[_fill("1"), _fill("2")], headers={"x-next-cursor": "c1"}
    )
    httpx_mock.add_response(url=f"{_FILLS_URL}?limit=2&cursor=c1", json=[_fill("3")])
    async with _signed() as client:
        fills = [f async for f in client.iter_my_trades(limit=2)]
    assert [f.id for f in fills] == ["1", "2", "3"]


async def test_fetch_page_returns_the_cursor(httpx_mock) -> None:
    httpx_mock.add_response(url=_FILLS_URL, json=[_fill("1")], headers={"x-next-cursor": " c9 "})
    async with _signed() as client:
        page = await client.fetch_my_trades_page()
    assert [f.id for f in page.items] == ["1"]
    assert page.next_cursor == "c9"


async def test_fetch_first_page_only(httpx_mock) -> None:
    httpx_mock.add_response(url=_FILLS_URL, json=[_fill("1")], headers={"x-next-cursor": "c1"})
    async with _signed() as client:
        assert [f.id for f in await client.fetch_my_trades()] == ["1"]


async def test_a_repeated_cursor_raises_rather_than_looping(httpx_mock) -> None:
    httpx_mock.add_response(url=_FILLS_URL, json=[_fill("1")], headers={"x-next-cursor": "c1"})
    httpx_mock.add_response(
        url=f"{_FILLS_URL}?cursor=c1", json=[_fill("2")], headers={"x-next-cursor": "c1"}
    )
    async with _signed() as client:
        with pytest.raises(PaginationError):
            [f async for f in client.iter_my_trades()]


async def test_max_pages_bounds_the_walk(httpx_mock) -> None:
    httpx_mock.add_response(url=_FILLS_URL, json=[_fill("1")], headers={"x-next-cursor": "c1"})
    async with _signed() as client:
        fills = [f async for f in client.iter_my_trades(max_pages=1)]
    assert [f.id for f in fills] == ["1"]


async def test_iter_args_are_checked_at_call_time() -> None:
    async with AsyncClient(Network.LOCAL) as client:
        with pytest.raises(ValueError):
            client.iter_trades("BTC-USDX-PERP", limit=TRADES_LIMIT_MAX + 1)
        with pytest.raises(ValueError):
            client.iter_trades("BTC-USDX-PERP", max_pages=-1)
