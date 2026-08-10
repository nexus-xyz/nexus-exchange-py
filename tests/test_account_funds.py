"""Account funds + equity-history tests (mocked httpx) — ENG-9200, slice 1.

Covers the four operations this slice adds: ``GET /account/equity-history``
(paginated), ``GET /deposits``, ``POST /deposits`` and ``POST /faucet``.

The load-bearing guarantees asserted here:

* every call signs (``x-api-key`` on the captured request), and each one lands on
  the surface its ``endpoints.txt`` line names — equity history on the direct
  ``/api/v1`` service, the three funds routes on the legacy gateway. Getting that
  wrong is silent: the HMAC would be computed over the same wrong path, so it
  reads as an auth failure rather than a misroute;
* a figure the spec marks optional and the server does not report decodes to
  ``None``, never a defaulted ``0`` — on these routes a fabricated zero is a
  balance that reads as a wiped account, an equity point that plots as a crash,
  or a faucet cooldown that reads as "claimable now";
* a malformed list element raises ``DecodeError`` instead of being dropped: a
  hole in a fixed-cadence equity series bends every delta drawn from it, and a
  funds ledger that silently comes back short is one a reconciliation trusts;
* a bad caller argument raises ``ValueError`` **before** anything is signed or
  sent. For ``submit_deposit`` that includes the three amounts a bare
  ``str(amount)`` would happily put on the wire as a funds instruction —
  unparseable, non-finite (``Decimal("NaN") <= 0`` is ``False``), and scientific
  notation;
* ``claim_faucet`` refuses outright on a network with no faucet, rather than
  spending a signed request against a real-funds host.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from nexus_exchange import (
    Client,
    DecodeError,
    DepositAck,
    EquityPoint,
    FaucetClaim,
    FundsEntry,
    Network,
    PaginationError,
)
from nexus_exchange.client import DEPOSITS_LIMIT_MAX, EQUITY_HISTORY_LIMIT_MAX

BASE = "http://localhost:9090"
GATEWAY = f"{BASE}/api/exchange"
EQUITY_URL = f"{BASE}/api/v1/account/equity-history"
DEPOSITS_URL = f"{BASE}/deposits"
FAUCET_URL = f"{BASE}/faucet"

# A well-formed 32-byte hex secret, matching the other signed-request tests.
_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


def _entry(entry_id: int, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": entry_id,
        "kind": "deposit",
        "account": "0xabc",
        "amount": "1000",
        "asset": "USDX",
        "timestamp": 1776033900000,
        "status": "confirmed",
        "tx_hash": "0xdeadbeef",
    }
    row.update(over)
    return row


# -- GET /account/equity-history --------------------------------------------


def test_fetch_equity_history_signs_and_hits_the_direct_surface(httpx_mock) -> None:
    httpx_mock.add_response(
        url=EQUITY_URL,
        json=[
            {"timestamp_ms": 1776033900000, "equity": 10000.5},
            {"timestamp_ms": 1776033905000, "equity": 10001.25},
        ],
    )
    with _authed() as client:
        points = client.fetch_equity_history()

    assert [p.timestamp_ms for p in points] == [1776033900000, 1776033905000]
    # A JSON *number* still lands as an exact Decimal of the arriving text, not an
    # f64 round-trip: Decimal(10000.5) would be 10000.5000000000000000...
    assert points[0].equity == Decimal("10000.5")
    assert isinstance(points[0], EquityPoint)
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers["x-api-key"] == "nx_test"
    assert req.url.path == "/api/v1/account/equity-history"


def test_fetch_equity_history_sends_limit(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{EQUITY_URL}?limit=10", json=[])
    with _authed() as client:
        assert client.fetch_equity_history(limit=10) == []


def test_equity_history_unreported_fields_stay_none(httpx_mock) -> None:
    # Both properties are spec-optional. A missing equity must not read as a zero
    # balance, and a missing timestamp must not read as the epoch.
    httpx_mock.add_response(url=EQUITY_URL, json=[{}, {"timestamp_ms": 1776033900000}])
    with _authed() as client:
        points = client.fetch_equity_history()
    assert points[0].timestamp_ms is None
    assert points[0].equity is None
    assert points[1].equity is None
    # A *reported* zero is still a zero.
    assert points[1].timestamp_ms == 1776033900000


def test_equity_history_reported_zero_is_kept(httpx_mock) -> None:
    httpx_mock.add_response(url=EQUITY_URL, json=[{"timestamp_ms": 0, "equity": 0}])
    with _authed() as client:
        point = client.fetch_equity_history()[0]
    assert point.equity == Decimal(0)
    assert point.timestamp_ms == 0


def test_equity_history_malformed_element_raises_rather_than_shortening(httpx_mock) -> None:
    httpx_mock.add_response(url=EQUITY_URL, json=[{"timestamp_ms": 1, "equity": 1}, "nope"])
    with _authed() as client:
        with pytest.raises(DecodeError):
            client.fetch_equity_history()


def test_equity_history_non_finite_equity_raises(httpx_mock) -> None:
    # "NaN" parses as a Decimal and then poisons every comparison it touches.
    httpx_mock.add_response(url=EQUITY_URL, json=[{"timestamp_ms": 1, "equity": "NaN"}])
    with _authed() as client:
        with pytest.raises(DecodeError):
            client.fetch_equity_history()


def test_equity_history_empty_body_is_an_empty_series(httpx_mock) -> None:
    httpx_mock.add_response(url=EQUITY_URL, status_code=200, content=b"")
    with _authed() as client:
        assert client.fetch_equity_history() == []


def test_equity_history_object_body_raises(httpx_mock) -> None:
    # The response is a bare array; an object is a re-shaped payload, not zero
    # points — reporting "no equity history" for it would be a fabrication.
    httpx_mock.add_response(url=EQUITY_URL, json={"points": []})
    with _authed() as client:
        with pytest.raises(DecodeError):
            client.fetch_equity_history()


@pytest.mark.parametrize("limit", [0, -1, EQUITY_HISTORY_LIMIT_MAX + 1, True, 1.5])
def test_equity_history_rejects_out_of_range_limit_before_signing(limit: object) -> None:
    # No response registered: a request would fail the test outright.
    with _authed() as client:
        with pytest.raises(ValueError, match="equity-history limit|limit must be an integer"):
            client.fetch_equity_history(limit=limit)  # type: ignore[arg-type]


def test_iter_equity_history_validates_its_arguments_eagerly() -> None:
    # A generator body does not run until the first next(), so without the eager
    # check a caller mistake would surface far from the call that made it.
    with _authed() as client:
        with pytest.raises(ValueError, match="equity-history limit"):
            client.iter_equity_history(limit=EQUITY_HISTORY_LIMIT_MAX + 1)
        with pytest.raises(ValueError, match="max_pages"):
            client.iter_equity_history(max_pages=-1)


def test_iter_equity_history_follows_the_cursor(httpx_mock) -> None:
    httpx_mock.add_response(
        url=EQUITY_URL,
        json=[{"timestamp_ms": 1, "equity": 1}],
        headers={"x-next-cursor": "cur-2"},
    )
    httpx_mock.add_response(
        url=f"{EQUITY_URL}?cursor=cur-2",
        json=[{"timestamp_ms": 2, "equity": 2}],
    )
    with _authed() as client:
        points = list(client.iter_equity_history())
    assert [p.timestamp_ms for p in points] == [1, 2]


def test_equity_history_page_exposes_the_cursor(httpx_mock) -> None:
    httpx_mock.add_response(url=EQUITY_URL, json=[], headers={"x-next-cursor": "cur-2"})
    with _authed() as client:
        page = client.fetch_equity_history_page()
    assert page.next_cursor == "cur-2"
    assert page.is_last is False


def test_iter_equity_history_refuses_a_cursor_cycle(httpx_mock) -> None:
    httpx_mock.add_response(
        url=EQUITY_URL,
        json=[{"timestamp_ms": 1, "equity": 1}],
        headers={"x-next-cursor": "loop"},
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=f"{EQUITY_URL}?cursor=loop",
        json=[{"timestamp_ms": 2, "equity": 2}],
        headers={"x-next-cursor": "loop"},
        is_reusable=True,
    )
    with _authed() as client:
        with pytest.raises(PaginationError):
            list(client.iter_equity_history())


# -- GET /deposits -----------------------------------------------------------


def test_fetch_deposits_signs_and_stays_on_the_gateway(httpx_mock) -> None:
    httpx_mock.add_response(url=DEPOSITS_URL, json=[_entry(2), _entry(1)])
    with _authed() as client:
        entries = client.fetch_deposits()

    assert [e.id for e in entries] == [2, 1]
    assert isinstance(entries[0], FundsEntry)
    assert entries[0].amount == Decimal("1000")
    assert entries[0].kind == "deposit"
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers["x-api-key"] == "nx_test"
    # No /api/v1 prefix: this operation has no direct-service route.
    assert req.url.path == "/deposits"


def test_fetch_deposits_sends_limit(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{DEPOSITS_URL}?limit=5", json=[])
    with _authed() as client:
        assert client.fetch_deposits(limit=5) == []


def test_fetch_deposits_leaves_unreported_fields_none(httpx_mock) -> None:
    # tx_hash is null until on-chain (and always, for synthetic credit); an
    # unreported status must not decode to a plausible "confirmed".
    httpx_mock.add_response(url=DEPOSITS_URL, json=[{"id": 7, "kind": "faucet", "tx_hash": None}])
    with _authed() as client:
        entry = client.fetch_deposits()[0]
    assert entry.id == 7
    assert entry.kind == "faucet"
    assert entry.tx_hash is None
    assert entry.status is None
    assert entry.amount is None
    assert entry.timestamp is None


def test_fetch_deposits_malformed_element_raises(httpx_mock) -> None:
    httpx_mock.add_response(url=DEPOSITS_URL, json=[_entry(1), 42])
    with _authed() as client:
        with pytest.raises(DecodeError):
            client.fetch_deposits()


def test_fetch_deposits_empty_body_is_an_empty_ledger(httpx_mock) -> None:
    httpx_mock.add_response(url=DEPOSITS_URL, status_code=200, content=b"")
    with _authed() as client:
        assert client.fetch_deposits() == []


@pytest.mark.parametrize("limit", [0, -1, DEPOSITS_LIMIT_MAX + 1, True])
def test_fetch_deposits_rejects_out_of_range_limit_before_signing(limit: object) -> None:
    with _authed() as client:
        with pytest.raises(ValueError, match="deposits limit|limit must be an integer"):
            client.fetch_deposits(limit=limit)  # type: ignore[arg-type]


# -- POST /deposits ----------------------------------------------------------


def test_submit_deposit_sends_amount_and_omits_the_default_asset(httpx_mock) -> None:
    httpx_mock.add_response(url=DEPOSITS_URL, json={"balance": "1500.00"})
    with _authed() as client:
        ack = client.submit_deposit("500.00")

    assert isinstance(ack, DepositAck)
    assert ack.balance == Decimal("1500.00")
    req = httpx_mock.get_request()
    assert req is not None
    assert json.loads(req.content) == {"amount": "500.00"}
    assert req.headers["x-api-key"] == "nx_test"
    assert req.url.path == "/deposits"


def test_submit_deposit_passes_an_explicit_asset(httpx_mock) -> None:
    httpx_mock.add_response(url=DEPOSITS_URL, json={"balance": "1"})
    with _authed() as client:
        client.submit_deposit(Decimal("1"), asset="USDC")
    assert json.loads(httpx_mock.get_request().content) == {"amount": "1", "asset": "USDC"}


def test_submit_deposit_renders_exponent_notation_in_full(httpx_mock) -> None:
    # str(Decimal("1e4")) is "1E+4"; whether the server's decimal parser accepts
    # an exponent is not part of the contract, so send the plain digits.
    httpx_mock.add_response(url=DEPOSITS_URL, json={"balance": "10000"})
    with _authed() as client:
        client.submit_deposit(Decimal("1e4"))
    assert json.loads(httpx_mock.get_request().content) == {"amount": "10000"}


def test_submit_deposit_keeps_an_unreported_balance_none(httpx_mock) -> None:
    # A fabricated Decimal(0) here reads as a wiped account.
    httpx_mock.add_response(url=DEPOSITS_URL, json={})
    with _authed() as client:
        assert client.submit_deposit("1").balance is None


def test_submit_deposit_keeps_engine_extras_on_raw(httpx_mock) -> None:
    httpx_mock.add_response(url=DEPOSITS_URL, json={"balance": "5", "tx_hash": "0xfeed"})
    with _authed() as client:
        ack = client.submit_deposit("5")
    assert ack.raw["tx_hash"] == "0xfeed"


@pytest.mark.parametrize(
    "amount",
    [
        "NaN",  # Decimal("NaN") <= 0 is False, so a bare positivity check passes it
        "Infinity",
        "-Infinity",
        "abc",
        "1,000",
        "",
        "0",
        "-1",
        Decimal("-0.0001"),
        True,
    ],
)
def test_submit_deposit_rejects_a_bad_amount_before_signing(amount: object) -> None:
    # No response registered: this must raise without issuing a funds instruction.
    with _authed() as client:
        with pytest.raises(ValueError, match="amount must be"):
            client.submit_deposit(amount)  # type: ignore[arg-type]


@pytest.mark.parametrize("asset", ["", "   "])
def test_submit_deposit_rejects_a_blank_asset(asset: str) -> None:
    with _authed() as client:
        with pytest.raises(ValueError, match="non-empty symbol"):
            client.submit_deposit("1", asset=asset)


# -- POST /faucet ------------------------------------------------------------


def test_claim_faucet_signs_and_sends_no_body(httpx_mock) -> None:
    httpx_mock.add_response(
        url=FAUCET_URL, json={"amount": "1000", "available_at_ms": 1776037500000}
    )
    with _authed() as client:
        claim = client.claim_faucet()

    assert isinstance(claim, FaucetClaim)
    assert claim.amount == Decimal("1000")
    assert claim.available_at_ms == 1776037500000
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers["x-api-key"] == "nx_test"
    assert req.url.path == "/faucet"
    # The operation declares no requestBody: an empty body is what the HMAC
    # signature is computed over, so sending "{}" would sign a different digest.
    assert req.content == b""
    assert "content-type" not in req.headers


def test_claim_faucet_keeps_an_unreported_cooldown_none(httpx_mock) -> None:
    # None is "the server did not say", which is not "claimable now" — a
    # defaulted 0 would read as the latter and invite a 429 loop.
    httpx_mock.add_response(url=FAUCET_URL, json={})
    with _authed() as client:
        claim = client.claim_faucet()
    assert claim.amount is None
    assert claim.available_at_ms is None


def test_claim_faucet_is_refused_on_a_faucet_less_network() -> None:
    # Marked `x-nexus-network-availability: [testnet, local]` in the spec. Branch
    # on the network's own `has_faucet`, never on the host string — and fail here
    # rather than spending a signed request against a real-funds host.
    with Client(Network.MAINNET, base_url="https://api.nexus.xyz") as client:
        with pytest.raises(ValueError, match="no faucet"):
            client.claim_faucet()


def test_claim_faucet_is_allowed_on_testnet(httpx_mock) -> None:
    # Testnet's legacy surface is the `/api/exchange` gateway; only the direct
    # `/api/v1` surface sits at the host root.
    httpx_mock.add_response(
        url="https://exchange.nexus.xyz/api/exchange/faucet", json={"amount": "1000"}
    )
    with Client(Network.TESTNET, api_key="nx_test", api_secret=_SECRET) as client:
        assert client.claim_faucet().amount == Decimal("1000")


def test_the_funds_routes_follow_a_custom_gateway_base(httpx_mock) -> None:
    # `base_url` alone covers both surfaces, so a gateway base must still carry
    # the three prefix-less funds operations — the direct surface is what rejects
    # it (see `_reject_gateway_direct_base`), not the legacy one.
    httpx_mock.add_response(url=f"{GATEWAY}/faucet", json={"amount": "1"})
    with Client(
        Network.LOCAL,
        base_url=GATEWAY,
        direct_base_url=BASE,
        api_key="nx_test",
        api_secret=_SECRET,
    ) as client:
        assert client.claim_faucet().amount == Decimal("1")
    assert httpx_mock.get_request().url.path == "/api/exchange/faucet"
