"""The three history endpoints added for spec v0.7.2 — mocked httpx.

``GET /orders/history``, ``GET /positions/closed`` and
``GET /account/equity-history`` were in the pinned spec but unimplemented here.
All three are cursor-paginated, so these tests cover both halves of the gap:

  * the typed decode (models, nullable fields, signed routing), and
  * the pagination behaviour, per endpoint — because the ``limit`` maximum is
    **per endpoint** in the spec (500 / 200 / 720) and a shared bound would be
    wrong on all three.

The pagination mechanics themselves (``iter_pages`` termination rules) are pinned
in ``test_pagination.py``; what is pinned here is that each *new* endpoint is
actually wired to them.
"""

from __future__ import annotations

import httpx
import pytest

from nexus_exchange import (
    CLOSED_POSITIONS_LIMIT_MAX,
    EQUITY_HISTORY_LIMIT_MAX,
    ORDER_HISTORY_LIMIT_MAX,
    PORTFOLIO_LIMIT_MAX,
    Client,
    DecodeError,
    Network,
    PaginationError,
)

BASE = "http://localhost:9090"
ORDER_HISTORY_URL = f"{BASE}/api/v1/orders/history"
CLOSED_POSITIONS_URL = f"{BASE}/api/v1/positions/closed"
EQUITY_HISTORY_URL = f"{BASE}/api/v1/account/equity-history"

# A well-formed 32-byte hex secret, matching the other signed-request tests.
_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def _signed_client() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


def _order(order_id: str, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": order_id,
        "market_id": "BTC-USDX-PERP",
        "side": "buy",
        "order_type": "limit",
        "price": "50000.25",
        "size": "2",
        "filled_qty": "2",
        "status": "Filled",
        "cancellation_reason": None,
        "created_at_ms": 1776033900000,
        "completed_at_ms": 1776033901000,
    }
    row.update(over)
    return row


def _closed(market_id: str = "BTC-USDX-PERP", **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "market_id": market_id,
        "side": "Long",
        "size": "1.5",
        "entry_price": "50000",
        "exit_price": "51000",
        "realized_pnl": "1500",
        "closed_at_ms": 1776033900000,
    }
    row.update(over)
    return row


def _point(ts: int, equity: float = 10000.5) -> dict[str, object]:
    return {"timestamp_ms": ts, "equity": equity}


# -- typed decode -----------------------------------------------------------


def test_order_history_decodes_and_signs(httpx_mock) -> None:
    httpx_mock.add_response(url=ORDER_HISTORY_URL, json=[_order("o1")])
    with _signed_client() as client:
        entries = client.fetch_order_history()

    assert len(entries) == 1
    entry = entries[0]
    assert entry.id == "o1"
    assert str(entry.price) == "50000.25"
    assert str(entry.size) == "2"
    assert entry.status == "Filled"
    assert entry.cancellation_reason is None
    assert entry.completed_at_ms == 1776033901000
    assert entry.raw["market_id"] == "BTC-USDX-PERP"
    # Signed route: the /api/v1 prefix is part of the canonical signed path.
    assert httpx_mock.get_requests()[0].headers["x-signature"]


def test_order_history_market_order_price_is_none(httpx_mock) -> None:
    # `price` is nullable in the spec (market orders have no limit price). It must
    # decode to None, not a fabricated Decimal(0) that reads as a real price.
    httpx_mock.add_response(
        url=ORDER_HISTORY_URL,
        json=[
            _order(
                "o2",
                price=None,
                order_type="market",
                status="Cancelled",
                cancellation_reason="user_requested",
            )
        ],
    )
    with _signed_client() as client:
        entry = client.fetch_order_history()[0]

    assert entry.price is None
    assert entry.cancellation_reason == "user_requested"


def test_closed_positions_decode(httpx_mock) -> None:
    httpx_mock.add_response(url=CLOSED_POSITIONS_URL, json=[_closed()])
    with _signed_client() as client:
        closed = client.fetch_closed_positions()

    assert len(closed) == 1
    position = closed[0]
    assert position.market_id == "BTC-USDX-PERP"
    # Capitalized on this schema, unlike the "buy"/"sell" of orders and fills.
    assert position.side == "Long"
    assert str(position.exit_price) == "51000"
    assert str(position.realized_pnl) == "1500"
    assert position.closed_at_ms == 1776033900000
    assert httpx_mock.get_requests()[0].headers["x-signature"]


def test_equity_history_decodes_json_number_equity_without_float_drift(httpx_mock) -> None:
    # `equity` is a JSON *number* on this schema (a decimal string on
    # PortfolioPoint). It decodes through str(), so the value is the decimal text
    # that arrived rather than an f64 re-rendering.
    httpx_mock.add_response(url=EQUITY_HISTORY_URL, json=[_point(1776033900000, 10000.1)])
    with _signed_client() as client:
        points = client.fetch_equity_history()

    assert len(points) == 1
    assert str(points[0].equity) == "10000.1"
    assert points[0].timestamp_ms == 1776033900000


def test_missing_fields_decode_leniently(httpx_mock) -> None:
    # None of the three schemas has a `required` array in v0.7.2, so a slim
    # payload must still decode (forward-compatible) rather than raise.
    httpx_mock.add_response(url=ORDER_HISTORY_URL, json=[{}])
    httpx_mock.add_response(url=CLOSED_POSITIONS_URL, json=[{}])
    httpx_mock.add_response(url=EQUITY_HISTORY_URL, json=[{}])
    with _signed_client() as client:
        assert client.fetch_order_history()[0].id == ""
        assert client.fetch_closed_positions()[0].market_id == ""
        assert client.fetch_equity_history()[0].timestamp_ms is None


def test_absent_money_and_timestamps_are_none_not_zero(httpx_mock) -> None:
    """Absent is not zero, and this test used to assert the opposite.

    It read ``timestamp_ms == 0`` — encoding the very defaulting
    @Luc-Campos flagged on #47. A zeroed money field is a real, wrong number:
    an absent ``realized_pnl`` decoding to ``Decimal('0')`` reads as "closed
    flat", and an absent ``closed_at_ms`` of ``0`` plots at 1970, on the far
    left of any chart. Neither is distinguishable from a genuine zero.
    """
    httpx_mock.add_response(url=CLOSED_POSITIONS_URL, json=[{"market_id": "BTC-USDX-PERP"}])
    httpx_mock.add_response(url=EQUITY_HISTORY_URL, json=[{}])
    with _signed_client() as client:
        pos = client.fetch_closed_positions()[0]
        assert pos.realized_pnl is None
        assert pos.entry_price is None
        assert pos.size is None
        assert pos.closed_at_ms is None
        point = client.fetch_equity_history()[0]
        assert point.equity is None
        assert point.timestamp_ms is None


def test_absent_and_null_agree(httpx_mock) -> None:
    """The asymmetry that made the old shape indefensible.

    ``{"realized_pnl": None}`` raised ``DecodeError`` while omitting the key
    returned ``Decimal('0')`` — two opposite outcomes for the same semantic
    content, with the loud one landing on the case the spec is most explicit
    about permitting. Both are ``None`` now.
    """
    httpx_mock.add_response(url=CLOSED_POSITIONS_URL, json=[{"realized_pnl": None}])
    httpx_mock.add_response(url=CLOSED_POSITIONS_URL, json=[{}])
    with _signed_client() as client:
        explicit_null = client.fetch_closed_positions()[0].realized_pnl
        absent = client.fetch_closed_positions()[0].realized_pnl
    assert explicit_null is None
    assert absent is None


def test_a_bool_is_not_a_timestamp(httpx_mock) -> None:
    # `int(True)` is 1, so the bare `int()` this replaced decoded
    # `{"closed_at_ms": True}` to a real millisecond value. `opt_int` rejects it
    # — the trap it was added to close in #43.
    httpx_mock.add_response(url=CLOSED_POSITIONS_URL, json=[{"closed_at_ms": True}])
    with _signed_client() as client:
        with pytest.raises(DecodeError):
            client.fetch_closed_positions()


def test_a_malformed_money_value_names_its_field(httpx_mock) -> None:
    # `to_decimal`'s `field` argument was omitted, so a bad value reported
    # "a decimal field is not a decimal: 'abc'" with no way to tell which.
    httpx_mock.add_response(url=CLOSED_POSITIONS_URL, json=[{"realized_pnl": "abc"}])
    with _signed_client() as client:
        with pytest.raises(DecodeError, match="realized_pnl"):
            client.fetch_closed_positions()


# -- multi-page traversal ---------------------------------------------------


def test_iter_order_history_follows_next_cursor(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{ORDER_HISTORY_URL}?limit=2",
        json=[_order("o1"), _order("o2")],
        headers={"x-next-cursor": "cur-2"},
    )
    httpx_mock.add_response(
        url=f"{ORDER_HISTORY_URL}?limit=2&cursor=cur-2",
        json=[_order("o3")],
    )
    with _signed_client() as client:
        ids = [o.id for o in client.iter_order_history(limit=2)]

    assert ids == ["o1", "o2", "o3"]
    assert len(httpx_mock.get_requests()) == 2
    # Every page is independently signed — the cursor rides in the signed query.
    for request in httpx_mock.get_requests():
        assert request.headers["x-signature"]


def test_iter_closed_positions_follows_next_cursor(httpx_mock) -> None:
    httpx_mock.add_response(
        url=CLOSED_POSITIONS_URL,
        json=[_closed("BTC-USDX-PERP")],
        headers={"x-next-cursor": "cur-2"},
    )
    httpx_mock.add_response(
        url=f"{CLOSED_POSITIONS_URL}?cursor=cur-2",
        json=[_closed("ETH-USDX-PERP")],
    )
    with _signed_client() as client:
        markets = [p.market_id for p in client.iter_closed_positions()]

    assert markets == ["BTC-USDX-PERP", "ETH-USDX-PERP"]
    assert len(httpx_mock.get_requests()) == 2


def test_iter_equity_history_follows_next_cursor(httpx_mock) -> None:
    httpx_mock.add_response(
        url=EQUITY_HISTORY_URL,
        json=[_point(1), _point(2)],
        headers={"x-next-cursor": "cur-2"},
    )
    httpx_mock.add_response(url=f"{EQUITY_HISTORY_URL}?cursor=cur-2", json=[_point(3)])
    with _signed_client() as client:
        stamps = [p.timestamp_ms for p in client.iter_equity_history()]

    assert stamps == [1, 2, 3]
    assert len(httpx_mock.get_requests()) == 2


def test_cursor_is_sent_back_verbatim(httpx_mock) -> None:
    # Cursors are opaque: a token with URL-hostile characters survives
    # percent-encoded and is signed exactly as sent.
    opaque = "eyJvIjoxMH0=+/"
    httpx_mock.add_response(
        url=CLOSED_POSITIONS_URL,
        json=[_closed()],
        headers={"x-next-cursor": opaque},
    )
    httpx_mock.add_response(url=f"{CLOSED_POSITIONS_URL}?cursor=eyJvIjoxMH0%3D%2B%2F", json=[])
    with _signed_client() as client:
        assert len(list(client.iter_closed_positions())) == 1

    assert httpx_mock.get_requests()[1].url.params["cursor"] == opaque


# -- termination ------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "walk"),
    [
        (ORDER_HISTORY_URL, "iter_order_history"),
        (CLOSED_POSITIONS_URL, "iter_closed_positions"),
        (EQUITY_HISTORY_URL, "iter_equity_history"),
    ],
)
def test_single_page_without_header_terminates(httpx_mock, url: str, walk: str) -> None:
    # No X-Next-Cursor means last page — one request, no error, on all three.
    httpx_mock.add_response(url=url, json=[{}])
    with _signed_client() as client:
        assert len(list(getattr(client, walk)())) == 1
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    ("url", "walk"),
    [
        (ORDER_HISTORY_URL, "iter_order_history"),
        (CLOSED_POSITIONS_URL, "iter_closed_positions"),
        (EQUITY_HISTORY_URL, "iter_equity_history"),
    ],
)
def test_empty_first_page_terminates_without_error(httpx_mock, url: str, walk: str) -> None:
    # An account with no history at all: an empty walk, not an error or a retry.
    httpx_mock.add_response(url=url, json=[])
    with _signed_client() as client:
        assert list(getattr(client, walk)()) == []
    assert len(httpx_mock.get_requests()) == 1


def test_empty_page_with_cursor_keeps_paging(httpx_mock) -> None:
    # An empty page carrying a cursor is NOT the end — stopping here would
    # truncate a walk over a sparse window (a market with no closes in a slice).
    httpx_mock.add_response(url=CLOSED_POSITIONS_URL, json=[], headers={"x-next-cursor": "cur-2"})
    httpx_mock.add_response(url=f"{CLOSED_POSITIONS_URL}?cursor=cur-2", json=[_closed()])
    with _signed_client() as client:
        assert len(list(client.iter_closed_positions())) == 1
    assert len(httpx_mock.get_requests()) == 2


def test_blank_cursor_header_is_treated_as_absent(httpx_mock) -> None:
    httpx_mock.add_response(
        url=ORDER_HISTORY_URL, json=[_order("o1")], headers={"x-next-cursor": ""}
    )
    with _signed_client() as client:
        assert [o.id for o in client.iter_order_history()] == ["o1"]
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    ("url", "walk"),
    [
        (ORDER_HISTORY_URL, "iter_order_history"),
        (CLOSED_POSITIONS_URL, "iter_closed_positions"),
        (EQUITY_HISTORY_URL, "iter_equity_history"),
    ],
)
def test_repeated_cursor_raises_instead_of_looping(httpx_mock, url: str, walk: str) -> None:
    # A server echoing back the cursor it was given cannot advance. Only two
    # responses are registered, so a build that dropped the guard fails loudly
    # (a third request finds no mock) instead of hanging the suite.
    httpx_mock.add_response(url=url, json=[{}], headers={"x-next-cursor": "stuck"})
    httpx_mock.add_response(
        url=f"{url}?cursor=stuck", json=[{}], headers={"x-next-cursor": "stuck"}
    )
    with _signed_client() as client:
        with pytest.raises(PaginationError, match="same pagination cursor"):
            list(getattr(client, walk)())
    assert len(httpx_mock.get_requests()) == 2


def test_max_pages_bounds_an_endlessly_advancing_server(httpx_mock) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params.get("cursor", "0"))
        return httpx.Response(200, json=[_point(n)], headers={"x-next-cursor": str(n + 1)})

    httpx_mock.add_callback(handler, is_reusable=True)
    with _signed_client() as client:
        points = list(client.iter_equity_history(max_pages=3))

    assert [p.timestamp_ms for p in points] == [0, 1, 2]
    assert len(httpx_mock.get_requests()) == 3


def test_generator_is_lazy_and_stops_when_the_caller_stops(httpx_mock) -> None:
    httpx_mock.add_response(
        url=ORDER_HISTORY_URL,
        json=[_order("o1"), _order("o2")],
        headers={"x-next-cursor": "cur-2"},
    )
    with _signed_client() as client:
        for entry in client.iter_order_history():
            assert entry.id == "o1"
            break
    assert len(httpx_mock.get_requests()) == 1


# -- manual paging ----------------------------------------------------------


def test_fetch_page_exposes_the_cursor_for_manual_paging(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{ORDER_HISTORY_URL}?limit=1",
        json=[_order("o1")],
        headers={"x-next-cursor": "cur-2"},
    )
    httpx_mock.add_response(url=f"{ORDER_HISTORY_URL}?limit=1&cursor=cur-2", json=[_order("o2")])
    with _signed_client() as client:
        first = client.fetch_order_history_page(limit=1)
        assert first.next_cursor == "cur-2"
        assert not first.is_last

        second = client.fetch_order_history_page(limit=1, cursor=first.next_cursor)
    assert second.is_last
    assert [o.id for o in second.items] == ["o2"]


def test_flat_fetch_methods_return_the_first_page_only(httpx_mock) -> None:
    # The plain fetch_* methods return a list, and the presence of a cursor on
    # the first page does not leak into their return type or trigger a walk.
    httpx_mock.add_response(
        url=CLOSED_POSITIONS_URL, json=[_closed()], headers={"x-next-cursor": "cur-2"}
    )
    with _signed_client() as client:
        closed = client.fetch_closed_positions()
    assert isinstance(closed, list)
    assert len(closed) == 1
    assert len(httpx_mock.get_requests()) == 1


def test_page_query_omits_absent_params(httpx_mock) -> None:
    # No limit and no cursor means no query string at all — on a signed route the
    # query is part of the canonical string, so an empty `cursor=` is a different
    # request.
    httpx_mock.add_response(url=EQUITY_HISTORY_URL, json=[])
    with _signed_client() as client:
        client.fetch_equity_history_page()
    assert httpx_mock.get_requests()[0].url.query == b""


# -- limit bounds -----------------------------------------------------------


def test_each_endpoint_has_its_own_limit_maximum() -> None:
    # These differ per endpoint in v0.7.2 and are NOT interchangeable. In
    # particular none of them is 366: that bound belongs to
    # /account/portfolio-history, which is not cursor-paginated at all — and on
    # equity-history it would sit below the server's own default of 720.
    assert ORDER_HISTORY_LIMIT_MAX == 500
    assert CLOSED_POSITIONS_LIMIT_MAX == 200
    assert EQUITY_HISTORY_LIMIT_MAX == 720
    assert PORTFOLIO_LIMIT_MAX == 366
    assert EQUITY_HISTORY_LIMIT_MAX > PORTFOLIO_LIMIT_MAX


@pytest.mark.parametrize(
    ("url", "method", "maximum"),
    [
        (ORDER_HISTORY_URL, "fetch_order_history", ORDER_HISTORY_LIMIT_MAX),
        (CLOSED_POSITIONS_URL, "fetch_closed_positions", CLOSED_POSITIONS_LIMIT_MAX),
        (EQUITY_HISTORY_URL, "fetch_equity_history", EQUITY_HISTORY_LIMIT_MAX),
    ],
)
def test_limit_at_the_maximum_is_sent(httpx_mock, url: str, method: str, maximum: int) -> None:
    httpx_mock.add_response(url=f"{url}?limit={maximum}", json=[])
    with _signed_client() as client:
        assert getattr(client, method)(limit=maximum) == []
    assert httpx_mock.get_requests()[0].url.params["limit"] == str(maximum)


@pytest.mark.parametrize(
    ("method", "maximum"),
    [
        ("fetch_order_history", ORDER_HISTORY_LIMIT_MAX),
        ("fetch_closed_positions", CLOSED_POSITIONS_LIMIT_MAX),
        ("fetch_equity_history", EQUITY_HISTORY_LIMIT_MAX),
    ],
)
def test_limit_over_the_maximum_raises_before_any_request(
    httpx_mock, method: str, maximum: int
) -> None:
    # A request-schema violation costs no round trip — and no signature over a
    # query the server would reject.
    with _signed_client() as client:
        for limit in (maximum + 1, maximum * 10, 0, -1):
            with pytest.raises(ValueError, match=f"limit must be between 1 and {maximum}"):
                getattr(client, method)(limit=limit)
    assert httpx_mock.get_requests() == []


def test_a_limit_valid_on_one_endpoint_is_rejected_on_a_stricter_one(httpx_mock) -> None:
    # The whole point of per-endpoint bounds: 500 is fine on /orders/history and
    # out of range on /positions/closed. A single shared cap would get one wrong.
    httpx_mock.add_response(url=f"{ORDER_HISTORY_URL}?limit=500", json=[])
    with _signed_client() as client:
        assert client.fetch_order_history(limit=500) == []
        with pytest.raises(ValueError, match="^positions/closed limit"):
            client.fetch_closed_positions(limit=500)
    assert len(httpx_mock.get_requests()) == 1


def test_limit_errors_name_the_endpoint(httpx_mock) -> None:
    with _signed_client() as client:
        with pytest.raises(ValueError, match="^orders/history limit"):
            client.fetch_order_history(limit=99999)
        with pytest.raises(ValueError, match="^positions/closed limit"):
            client.fetch_closed_positions(limit=99999)
        with pytest.raises(ValueError, match="^account/equity-history limit"):
            client.fetch_equity_history(limit=99999)
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize(
    "walk", ["iter_order_history", "iter_closed_positions", "iter_equity_history"]
)
def test_iterators_validate_limit_before_the_first_request(httpx_mock, walk: str) -> None:
    with _signed_client() as client:
        with pytest.raises(ValueError, match="limit must be between"):
            next(iter(getattr(client, walk)(limit=100000)))
    assert httpx_mock.get_requests() == []


def test_bool_limit_rejected(httpx_mock) -> None:
    # `bool` is an `int` subclass; unchecked it would send `limit=True`.
    with _signed_client() as client:
        with pytest.raises(ValueError, match="limit must be an integer"):
            client.fetch_equity_history(limit=True)  # type: ignore[arg-type]
    assert httpx_mock.get_requests() == []
