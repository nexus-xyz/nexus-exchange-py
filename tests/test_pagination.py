"""Cursor pagination tests (spec v0.7.2) — mocked httpx.

v0.7.2 added an opaque ``cursor`` query parameter and an ``X-Next-Cursor``
response header to the list endpoints. The behaviour that matters is *when the
walk stops*, so these pin all four ways it can end:

  * a next cursor is present  → keep going, sending it back verbatim;
  * the header is absent      → done, and not an error;
  * an empty page still carrying a cursor → keep going (a sparse page is not
    the end);
  * the same cursor comes back → :class:`PaginationError`, never an infinite
    request loop.

Plus the `limit` bounds, which are per-endpoint in the spec (trades and fills
1000) and are enforced before the request rather than left to server clamping.
"""

from __future__ import annotations

import httpx
import pytest

from nexus_exchange import (
    FILLS_LIMIT_MAX,
    TRADES_LIMIT_MAX,
    Client,
    Network,
    PaginationError,
)

BASE = "http://localhost:9090"
TRADES_URL = f"{BASE}/api/v1/markets/BTC-USDX-PERP/trades"
FILLS_URL = f"{BASE}/api/v1/fills"

# A well-formed 32-byte hex secret, matching the other signed-request tests.
_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def _trade(trade_id: str) -> dict[str, object]:
    return {
        "id": trade_id,
        "symbol": "BTC-USDX-PERP",
        "price": 50000.0,
        "amount": 1.0,
        "cost": 50000.0,
        "side": "buy",
        "timestamp": 1776033900000,
        "datetime": "2026-04-12T00:05:00Z",
    }


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


def _signed_client() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


def _client() -> Client:
    """Unsigned client, for the public paginated routes."""
    return Client(Network.LOCAL)


# -- multi-page traversal ---------------------------------------------------


def test_iter_trades_follows_next_cursor_across_pages(httpx_mock) -> None:
    # Page 1 hands back a cursor; page 2 does not, which ends the walk.
    httpx_mock.add_response(
        url=f"{TRADES_URL}?limit=2",
        json=[_trade("t1"), _trade("t2")],
        headers={"x-next-cursor": "cur-2"},
    )
    httpx_mock.add_response(
        url=f"{TRADES_URL}?limit=2&cursor=cur-2",
        json=[_trade("t3")],
    )
    with Client(Network.LOCAL) as client:
        trades = list(client.iter_trades("BTC-USDX-PERP", limit=2))

    assert [t.id for t in trades] == ["t1", "t2", "t3"]
    # Exactly two requests: no speculative extra fetch past the final page.
    assert len(httpx_mock.get_requests()) == 2


def test_iter_my_trades_pages_and_signs_every_request(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{FILLS_URL}?limit=1",
        json=[_fill("f1")],
        headers={"x-next-cursor": "cur-b"},
    )
    httpx_mock.add_response(url=f"{FILLS_URL}?limit=1&cursor=cur-b", json=[_fill("f2")])
    with _signed_client() as client:
        fills = list(client.iter_my_trades(limit=1))

    assert [f.id for f in fills] == ["f1", "f2"]
    # The cursor rides in the query, so it is inside the signed canonical string
    # on page 2 as well — every page is independently signed.
    for request in httpx_mock.get_requests():
        assert request.headers["x-signature"]


def test_cursor_is_sent_back_verbatim(httpx_mock) -> None:
    # Cursors are opaque; a token with URL-hostile characters must survive
    # round-tripping percent-encoded, and must be signed exactly as sent.
    opaque = "eyJvIjoxMH0=+/"
    httpx_mock.add_response(
        url=f"{TRADES_URL}?limit=1",
        json=[_trade("t1")],
        headers={"x-next-cursor": opaque},
    )
    httpx_mock.add_response(
        url=f"{TRADES_URL}?limit=1&cursor=eyJvIjoxMH0%3D%2B%2F",
        json=[],
    )
    with Client(Network.LOCAL) as client:
        assert [t.id for t in client.iter_trades("BTC-USDX-PERP", limit=1)] == ["t1"]

    sent = httpx_mock.get_requests()[1]
    assert sent.url.params["cursor"] == opaque


# -- termination ------------------------------------------------------------


def test_single_page_without_header_terminates(httpx_mock) -> None:
    # The header is absent — the documented signal for "last page". One request,
    # no error.
    httpx_mock.add_response(url=f"{TRADES_URL}?limit=2", json=[_trade("t1")])
    with Client(Network.LOCAL) as client:
        trades = list(client.iter_trades("BTC-USDX-PERP", limit=2))

    assert [t.id for t in trades] == ["t1"]
    assert len(httpx_mock.get_requests()) == 1


def test_empty_first_page_terminates_without_error(httpx_mock) -> None:
    # No results at all: an empty body and no cursor is a clean, empty walk —
    # not an error and not a retry.
    httpx_mock.add_response(url=FILLS_URL, json=[])
    with _signed_client() as client:
        assert list(client.iter_my_trades()) == []
    assert len(httpx_mock.get_requests()) == 1


def test_empty_page_with_cursor_keeps_paging(httpx_mock) -> None:
    # An empty page that still carries a cursor is NOT the end. Stopping here
    # would silently truncate a walk over a sparse window.
    httpx_mock.add_response(url=FILLS_URL, json=[], headers={"x-next-cursor": "cur-2"})
    httpx_mock.add_response(url=f"{FILLS_URL}?cursor=cur-2", json=[_fill("f9")])
    with _signed_client() as client:
        assert [f.id for f in client.iter_my_trades()] == ["f9"]
    assert len(httpx_mock.get_requests()) == 2


def test_blank_cursor_header_is_treated_as_absent(httpx_mock) -> None:
    # A present-but-empty header cannot be sent back meaningfully; treating it
    # as a cursor would re-request the first page forever.
    httpx_mock.add_response(url=FILLS_URL, json=[_fill("f1")], headers={"x-next-cursor": ""})
    with _signed_client() as client:
        assert [f.id for f in client.iter_my_trades()] == ["f1"]
    assert len(httpx_mock.get_requests()) == 1


def test_repeated_cursor_raises_instead_of_looping(httpx_mock) -> None:
    # Pathological server: echoes back the cursor it was given. Advancing is
    # impossible, so the SDK must stop — and say so, rather than quietly
    # reporting a truncated history as complete.
    httpx_mock.add_response(
        url=FILLS_URL,
        json=[_fill("f1")],
        headers={"x-next-cursor": "stuck"},
    )
    httpx_mock.add_response(
        url=f"{FILLS_URL}?cursor=stuck",
        json=[_fill("f2")],
        headers={"x-next-cursor": "stuck"},
        is_reusable=True,
    )
    with _signed_client() as client:
        walk = client.iter_my_trades()
        assert next(walk).id == "f1"
        assert next(walk).id == "f2"
        with pytest.raises(PaginationError, match="already issued") as exc:
            next(walk)
    # The immediate-repeat case still says so explicitly: same guard, but the
    # message distinguishes "handed back what I just gave you" from "sent me
    # somewhere I had already been", because the server bugs differ in obviousness.
    assert "the same cursor it was just given" in str(exc.value)

    # Bounded: the guard fired on the page whose cursor did not advance, so the
    # identical request was never re-issued a third time.
    assert len(httpx_mock.get_requests()) == 2


def test_repeated_cursor_terminates_a_full_walk(httpx_mock) -> None:
    # The production shape of the same failure: `list(...)` over a stuck server
    # must come back, not spin. Only two responses are registered, so a build
    # that dropped the guard fails loudly (a third request finds no mock and
    # raises TransportError) instead of hanging the suite.
    httpx_mock.add_response(
        url=FILLS_URL,
        json=[_fill("f1")],
        headers={"x-next-cursor": "stuck"},
    )
    httpx_mock.add_response(
        url=f"{FILLS_URL}?cursor=stuck",
        json=[_fill("f2")],
        headers={"x-next-cursor": "stuck"},
    )
    with _signed_client() as client:
        with pytest.raises(PaginationError, match="2 page\\(s\\) were read"):
            list(client.iter_my_trades())
    assert len(httpx_mock.get_requests()) == 2


def test_max_pages_bounds_an_endlessly_advancing_server(httpx_mock) -> None:
    # A server that keeps advancing cursors is indistinguishable from a very
    # long history, so the caller's bound is what stops it.
    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params.get("cursor", "0"))
        return httpx.Response(
            200,
            json=[_fill(f"f{n}")],
            headers={"x-next-cursor": str(n + 1)},
        )

    httpx_mock.add_callback(handler, is_reusable=True)
    with _signed_client() as client:
        fills = list(client.iter_my_trades(max_pages=3))

    assert [f.id for f in fills] == ["f0", "f1", "f2"]
    assert len(httpx_mock.get_requests()) == 3


def test_max_pages_zero_issues_no_request(httpx_mock) -> None:
    # The cap is checked before fetching, so 0 means "no requests at all".
    with _signed_client() as client:
        assert list(client.iter_my_trades(max_pages=0)) == []
    assert httpx_mock.get_requests() == []


def test_negative_max_pages_rejected(httpx_mock) -> None:
    with _signed_client() as client:
        with pytest.raises(ValueError, match="max_pages must be non-negative"):
            list(client.iter_my_trades(max_pages=-1))
    assert httpx_mock.get_requests() == []


def test_generator_is_lazy_and_stops_when_the_caller_stops(httpx_mock) -> None:
    # Breaking out of the loop must stop the requests: page 2 is never fetched
    # even though page 1 advertised a cursor for it.
    httpx_mock.add_response(
        url=FILLS_URL,
        json=[_fill("f1"), _fill("f2")],
        headers={"x-next-cursor": "cur-2"},
    )
    with _signed_client() as client:
        for fill in client.iter_my_trades():
            assert fill.id == "f1"
            break
    assert len(httpx_mock.get_requests()) == 1


# -- manual paging ----------------------------------------------------------


def test_fetch_page_exposes_the_cursor_for_manual_paging(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{TRADES_URL}?limit=1",
        json=[_trade("t1")],
        headers={"x-next-cursor": "cur-2"},
    )
    httpx_mock.add_response(url=f"{TRADES_URL}?limit=1&cursor=cur-2", json=[_trade("t2")])
    with Client(Network.LOCAL) as client:
        first = client.fetch_trades_page("BTC-USDX-PERP", limit=1)
        assert first.next_cursor == "cur-2"
        assert not first.is_last

        second = client.fetch_trades_page("BTC-USDX-PERP", limit=1, cursor=first.next_cursor)
    assert second.next_cursor is None
    assert second.is_last
    assert [t.id for t in second.items] == ["t2"]


def test_flat_fetch_methods_return_the_first_page(httpx_mock) -> None:
    # The pre-pagination signatures still work and still return a plain list —
    # the cursor on the first page does not leak into their return type.
    httpx_mock.add_response(
        url=TRADES_URL,
        json=[_trade("t1")],
        headers={"x-next-cursor": "cur-2"},
    )
    httpx_mock.add_response(url=FILLS_URL, json=[_fill("f1")])
    with _signed_client() as client:
        trades = client.fetch_trades("BTC-USDX-PERP")
        fills = client.fetch_my_trades()
    assert [t.id for t in trades] == ["t1"]
    assert [f.id for f in fills] == ["f1"]


def test_fills_limit_is_forwarded(httpx_mock) -> None:
    # /fills accepts `limit` in v0.7.2; the SDK previously sent none at all.
    httpx_mock.add_response(url=f"{FILLS_URL}?limit=500", json=[])
    with _signed_client() as client:
        assert client.fetch_my_trades(limit=500) == []


# -- limit bounds -----------------------------------------------------------


def test_paginated_limit_maxima_are_per_endpoint(httpx_mock) -> None:
    # Both cursor endpoints this SDK implements cap `limit` at 1000 — NOT at the
    # 366 that belongs to /account/portfolio-history, which is not paginated.
    assert TRADES_LIMIT_MAX == 1000
    assert FILLS_LIMIT_MAX == 1000

    httpx_mock.add_response(url=f"{TRADES_URL}?limit=1000", json=[])
    httpx_mock.add_response(url=f"{FILLS_URL}?limit=1000", json=[])
    httpx_mock.add_response(url=f"{TRADES_URL}?limit=400", json=[])
    with _signed_client() as client:
        # At the maximum: allowed, and well above 366.
        assert client.fetch_trades("BTC-USDX-PERP", limit=1000) == []
        assert client.fetch_my_trades(limit=1000) == []
        assert client.fetch_trades_page("BTC-USDX-PERP", limit=400).items == []


@pytest.mark.parametrize("limit", [1001, 5000, 0, -1])
def test_out_of_range_limit_raises_before_any_request(httpx_mock, limit: int) -> None:
    # A request-schema violation costs no round trip — and on /fills, no
    # signature over a query the server would reject.
    with _signed_client() as client:
        with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
            client.fetch_trades("BTC-USDX-PERP", limit=limit)
        with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
            client.fetch_my_trades(limit=limit)
        with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
            next(iter(client.iter_my_trades(limit=limit)))
    assert httpx_mock.get_requests() == []


def test_bool_limit_rejected(httpx_mock) -> None:
    # `bool` is an `int` subclass; unchecked it would send `limit=True`.
    with _signed_client() as client:
        with pytest.raises(ValueError, match="limit must be an integer"):
            client.fetch_my_trades(limit=True)  # type: ignore[arg-type]
    assert httpx_mock.get_requests() == []


def test_limit_error_names_the_endpoint(httpx_mock) -> None:
    # The maxima differ per endpoint, so the message has to say which one it is.
    with _signed_client() as client:
        with pytest.raises(ValueError, match="^fills limit"):
            client.fetch_my_trades(limit=99999)
        with pytest.raises(ValueError, match="^trades limit"):
            client.fetch_trades("BTC-USDX-PERP", limit=99999)


# -- query construction ----------------------------------------------------


def test_page_query_omits_absent_params(httpx_mock) -> None:
    # No limit and no cursor means no query string at all — an empty `cursor=`
    # is not the same request, and on signed routes the query is signed.
    httpx_mock.add_response(url=FILLS_URL, json=[])
    with _signed_client() as client:
        client.fetch_my_trades_page()
    assert httpx_mock.get_requests()[0].url.query == b""


def test_two_cycle_cursor_terminates_instead_of_paging_forever(httpx_mock) -> None:
    """A cycle longer than one hop must still terminate (ENG-8081 review).

    The guard used to compare only against the *immediately preceding* cursor, so
    a server rotating ``None -> "A"``, ``"A" -> "B"``, ``"B" -> "A"`` never
    repeated consecutively and slipped through. With ``max_pages`` defaulting to
    ``None``, `list(client.iter_my_trades())` then paged without end — measured at
    202 requests before this fix, growing without bound.

    Only three responses are registered and the first is not reusable, so a build
    that regresses to the consecutive-only check cannot hang the suite: the fourth
    request finds no mock and fails loudly instead.
    """
    httpx_mock.add_response(
        url=FILLS_URL,
        json=[_fill("f1")],
        headers={"x-next-cursor": "A"},
    )
    httpx_mock.add_response(
        url=f"{FILLS_URL}?cursor=A",
        json=[_fill("f2")],
        headers={"x-next-cursor": "B"},
    )
    httpx_mock.add_response(
        url=f"{FILLS_URL}?cursor=B",
        json=[_fill("f3")],
        headers={"x-next-cursor": "A"},  # back to a cursor already issued
    )
    with _signed_client() as client:
        with pytest.raises(PaginationError, match="already issued") as exc:
            list(client.iter_my_trades())

    # "A" was seen on hop 1, so returning it on hop 3 is the cycle. Three pages
    # were read before the guard fired.
    assert "'A'" in str(exc.value)
    assert "3 page(s) were read" in str(exc.value)
    # And it is NOT reported as an immediate repeat — the previous cursor was "B".
    assert "the same cursor it was just given" not in str(exc.value)
    assert len(httpx_mock.get_requests()) == 3


def test_resume_cursor_is_remembered_so_a_server_cannot_send_you_straight_back(
    httpx_mock,
) -> None:
    """Resuming from ``X`` and being handed ``X`` back is a cycle on hop one.

    Documents the seeding of `seen` with the resume cursor. Being precise about
    what this does and does not prove: the *consecutive* check would also catch
    this exact case, so this is not a regression guard for the seen-set — it pins
    that resuming does not start the walk with an empty history. The case only
    the seen-set catches is resume-``X`` -> ``Y`` -> ``X``, which is the general
    property `test_two_cycle_cursor_terminates_instead_of_paging_forever` covers.
    """
    httpx_mock.add_response(
        url=f"{FILLS_URL}?cursor=resume-here",
        json=[_fill("f1")],
        headers={"x-next-cursor": "resume-here"},
    )
    with _signed_client() as client:
        with pytest.raises(PaginationError, match="already issued"):
            list(client.iter_my_trades(cursor="resume-here"))
    assert len(httpx_mock.get_requests()) == 1


# ---------------------------------------------------------------------------
# Eager validation of the caller's own arguments (ENG-8081 review)
# ---------------------------------------------------------------------------


def test_iter_trades_rejects_a_bad_limit_at_call_time() -> None:
    """Not at the first ``next()``. The paired methods must agree on when.

    `iter_*` returns the generator `iter_items` builds, and a generator function
    defers its body — so every check inside it was deferred too, and a plainly
    invalid argument came back as a healthy-looking generator that raised later,
    somewhere unrelated to the mistake.
    """
    with _client() as client:
        with pytest.raises(ValueError, match="trades limit must be between 1 and 1000"):
            client.iter_trades("BTC-USDX-PERP", limit=999_999)


def test_iter_my_trades_rejects_a_negative_max_pages_at_call_time() -> None:
    with _signed_client() as client:
        with pytest.raises(ValueError, match="max_pages must be non-negative"):
            client.iter_my_trades(max_pages=-5)


def test_iter_and_fetch_page_agree_on_limit_validation() -> None:
    """The property the review actually asked for, asserted directly.

    Both forms of the same call must reject the same argument at the same moment,
    so a caller cannot learn about their own mistake at two different times
    depending on which method they reached for.
    """
    with _client() as client:
        with pytest.raises(ValueError) as eager:
            client.iter_trades("BTC-USDX-PERP", limit=0)
        with pytest.raises(ValueError) as direct:
            client.fetch_trades_page("BTC-USDX-PERP", limit=0)
    assert str(eager.value) == str(direct.value)


def test_a_valid_iter_call_still_issues_no_request_until_iterated(httpx_mock) -> None:
    """Eager *validation* must not become eager *fetching*.

    The laziness is the feature — a caller that breaks out early stops the
    requests — so this pins that adding the argument checks did not also pull the
    first page forward.
    """
    httpx_mock.add_response(url=f"{FILLS_URL}?limit=100", json=[_fill("f1")], headers={})
    with _signed_client() as client:
        walk = client.iter_my_trades(limit=100)
        assert httpx_mock.get_requests() == []  # nothing sent yet
        assert next(walk).id == "f1"
    assert len(httpx_mock.get_requests()) == 1
