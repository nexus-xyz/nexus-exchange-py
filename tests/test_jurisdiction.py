"""Unit tests for the jurisdiction ``403`` typed error (ENG-9635, mocked httpx).

Spec v0.7.3 declares ``RestrictedJurisdiction`` on every state-changing
operation (and, for the sanctions code, on reads too). These cover the three
things that make the type worth having over a bare ``ApiError``:

* it is raised for each documented reason, and for an **undocumented** one — the
  spec keeps the code list open and says to treat an unknown value as a
  permanent refusal;
* ``block_reason`` prefers the ``x-nexus-block-reason`` header and falls back to
  the body ``code``, so a missing, truncated or non-JSON body still classifies —
  with the header normalized, so a padded value stays comparable and a blank one
  defers to the body rather than being taken as a reason;
* it does **not** capture the other ``403``\\ s in the contract
  (``credits_frozen``, the admin-secret refusal), which stay plain ``ApiError``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus_exchange import (
    ApiError,
    Client,
    Network,
    OrderRequest,
    RestrictedJurisdictionError,
)

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_ORDERS_URL = "http://localhost:9090/api/v1/orders"
_CREDIT_URL = "http://localhost:9090/api/v1/account/credit"
_FILLS_URL = "http://localhost:9090/api/v1/fills"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


def _place(client: Client):
    return client.create_order(OrderRequest.market("BTC-PERP", "buy", Decimal("1")))


# -- the documented reasons --------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    ["US_RESTRICTED", "GEO_UNRESOLVED", "RESTRICTED_JURISDICTION"],
)
def test_each_documented_reason_raises_typed_error(httpx_mock, reason: str) -> None:
    httpx_mock.add_response(
        url=_ORDERS_URL,
        method="POST",
        status_code=403,
        headers={"x-nexus-block-reason": reason},
        json={"code": reason, "message": "refused"},
    )
    with _authed() as client, pytest.raises(RestrictedJurisdictionError) as excinfo:
        _place(client)

    err = excinfo.value
    assert err.block_reason == reason
    assert err.code == reason
    assert err.status == 403
    assert err.message == "refused"


def test_is_terminal_and_still_an_api_error(httpx_mock) -> None:
    # Subclassing ApiError is the compatibility promise: an existing handler
    # that catches ApiError must keep catching this unchanged.
    httpx_mock.add_response(
        url=_ORDERS_URL,
        method="POST",
        status_code=403,
        headers={"x-nexus-block-reason": "US_RESTRICTED"},
        json={"code": "US_RESTRICTED", "message": "not available in the US"},
    )
    with _authed() as client, pytest.raises(ApiError) as excinfo:
        _place(client)

    err = excinfo.value
    assert isinstance(err, RestrictedJurisdictionError)
    # Permanent for the caller's origin — never retry.
    assert err.transient is False


# -- classification signals --------------------------------------------------


def test_unknown_reason_in_header_still_classifies(httpx_mock) -> None:
    # The spec deliberately leaves the code list open and says an unrecognized
    # value is still a permanent refusal, so a new server-side reason must not
    # silently degrade to a plain ApiError.
    httpx_mock.add_response(
        url=_ORDERS_URL,
        method="POST",
        status_code=403,
        headers={"x-nexus-block-reason": "SOME_FUTURE_CONTROL"},
        json={"code": "SOME_FUTURE_CONTROL", "message": "refused"},
    )
    with _authed() as client, pytest.raises(RestrictedJurisdictionError) as excinfo:
        _place(client)
    assert excinfo.value.block_reason == "SOME_FUTURE_CONTROL"


def test_body_code_classifies_when_header_is_absent(httpx_mock) -> None:
    # A proxy or older deployment that drops the header: the body still says so.
    httpx_mock.add_response(
        url=_ORDERS_URL,
        method="POST",
        status_code=403,
        json={"code": "RESTRICTED_JURISDICTION", "message": "access denied"},
    )
    with _authed() as client, pytest.raises(RestrictedJurisdictionError) as excinfo:
        _place(client)
    assert excinfo.value.block_reason == "RESTRICTED_JURISDICTION"


def test_header_classifies_when_body_is_not_json(httpx_mock) -> None:
    # The header is preferred precisely because it survives a body the parser
    # cannot read — here `code` is None, so only the header can classify.
    httpx_mock.add_response(
        url=_ORDERS_URL,
        method="POST",
        status_code=403,
        headers={"x-nexus-block-reason": "US_RESTRICTED"},
        content=b"<html>blocked at the edge</html>",
    )
    with _authed() as client, pytest.raises(RestrictedJurisdictionError) as excinfo:
        _place(client)

    err = excinfo.value
    assert err.block_reason == "US_RESTRICTED"
    assert err.code is None


def test_a_paginated_read_classifies_too(httpx_mock) -> None:
    # Sanctions is the one reason also returned on reads, and the classifier sits
    # in `_send` — the single error-mapping point every caller funnels through,
    # including the cursor-paginated readers. Pinned because that shared placement
    # is what makes the read path work; moving it into `_request` would type the
    # writes and silently leave `fetch_*`/`iter_*` on a bare ApiError.
    httpx_mock.add_response(
        url=_FILLS_URL,
        method="GET",
        status_code=403,
        headers={"x-nexus-block-reason": "RESTRICTED_JURISDICTION"},
        json={"code": "RESTRICTED_JURISDICTION", "message": "access denied"},
    )
    with _authed() as client, pytest.raises(RestrictedJurisdictionError) as excinfo:
        client.fetch_my_trades()
    assert excinfo.value.block_reason == "RESTRICTED_JURISDICTION"


def test_padded_header_value_is_stripped(httpx_mock) -> None:
    # Callers branch on `block_reason` by equality, so a header a proxy padded
    # must not arrive with the whitespace still attached.
    httpx_mock.add_response(
        url=_ORDERS_URL,
        method="POST",
        status_code=403,
        headers={"x-nexus-block-reason": "  US_RESTRICTED  "},
        content=b"",
    )
    with _authed() as client, pytest.raises(RestrictedJurisdictionError) as excinfo:
        _place(client)
    assert excinfo.value.block_reason == "US_RESTRICTED"


def test_blank_header_falls_through_to_the_body_code(httpx_mock) -> None:
    # A header present but blank names no reason to branch on, so it is read as
    # absent and the body decides — which still classifies, and still reports a
    # usable reason rather than the empty string the header carried.
    httpx_mock.add_response(
        url=_ORDERS_URL,
        method="POST",
        status_code=403,
        headers={"x-nexus-block-reason": "   "},
        json={"code": "GEO_UNRESOLVED", "message": "origin unresolved"},
    )
    with _authed() as client, pytest.raises(RestrictedJurisdictionError) as excinfo:
        _place(client)
    assert excinfo.value.block_reason == "GEO_UNRESOLVED"


def test_blank_header_alone_does_not_classify(httpx_mock) -> None:
    # The documented limit of the fallback: blank header *and* no usable body is
    # the one shape that degrades to a plain ApiError. Asserted so the behaviour
    # is pinned rather than merely described.
    httpx_mock.add_response(
        url=_ORDERS_URL,
        method="POST",
        status_code=403,
        headers={"x-nexus-block-reason": ""},
        content=b"<html>blocked at the edge</html>",
    )
    with _authed() as client, pytest.raises(ApiError) as excinfo:
        _place(client)
    assert not isinstance(excinfo.value, RestrictedJurisdictionError)


# -- what must NOT be captured -----------------------------------------------


def test_credits_frozen_403_stays_a_plain_api_error(httpx_mock) -> None:
    # `POST /account/credit` declares two different 403s. Only the jurisdiction
    # one is permanent for the origin; `credits_frozen` is an administrative
    # state that can lift, so typing it the same way would be wrong.
    httpx_mock.add_response(
        url=_CREDIT_URL,
        method="POST",
        status_code=403,
        json={"code": "credits_frozen", "message": "temporarily frozen"},
    )
    with _authed() as client, pytest.raises(ApiError) as excinfo:
        client.claim_credit()

    assert not isinstance(excinfo.value, RestrictedJurisdictionError)
    assert excinfo.value.code == "credits_frozen"


def test_unrelated_403_without_code_stays_a_plain_api_error(httpx_mock) -> None:
    # The admin-secret 403 carries no `code` and no header.
    httpx_mock.add_response(
        url=_ORDERS_URL,
        method="POST",
        status_code=403,
        json={"message": "admin secret required"},
    )
    with _authed() as client, pytest.raises(ApiError) as excinfo:
        _place(client)
    assert not isinstance(excinfo.value, RestrictedJurisdictionError)


def test_non_403_with_a_jurisdiction_like_code_is_not_captured(httpx_mock) -> None:
    # Classification is anchored on the status too: the jurisdiction contract is
    # a 403, so the same code on a 400 is some other server's vocabulary.
    httpx_mock.add_response(
        url=_ORDERS_URL,
        method="POST",
        status_code=400,
        json={"code": "US_RESTRICTED", "message": "bad request"},
    )
    with _authed() as client, pytest.raises(ApiError) as excinfo:
        _place(client)
    assert not isinstance(excinfo.value, RestrictedJurisdictionError)
