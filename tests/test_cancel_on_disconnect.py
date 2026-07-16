"""Unit tests for the account cancel-on-disconnect methods (mocked httpx).

Covers ``fetch_cancel_on_disconnect`` / ``set_cancel_on_disconnect`` (ENG-6132):
both sign (asserted via the ``x-api-key`` header on the captured request) and
hit the direct ``/api/v1`` surface, the getter decodes ``enabled`` / ``active``
/ ``grace_secs`` (a null/absent ``grace_secs`` stays ``None``), and the setter
sends exactly ``{"enabled": <bool>}`` as a ``PUT``.
"""

from __future__ import annotations

import json

from nexus_exchange import (
    CancelOnDisconnectStatus,
    Client,
    Network,
)

_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_URL = "http://localhost:9090/api/v1/account/cancel-on-disconnect"


def _authed() -> Client:
    return Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET)


# -- fetch_cancel_on_disconnect ----------------------------------------------


def test_fetch_cancel_on_disconnect_signs_and_parses(httpx_mock) -> None:
    httpx_mock.add_response(
        url=_URL,
        method="GET",
        json={"enabled": True, "active": False, "grace_secs": 30},
    )
    with _authed() as client:
        status = client.fetch_cancel_on_disconnect()
    assert isinstance(status, CancelOnDisconnectStatus)
    assert status.enabled is True
    # enabled but not active: the exchange has the feature switched off.
    assert status.active is False
    assert status.grace_secs == 30
    req = httpx_mock.get_request()
    assert req.headers["x-api-key"] == "nx_test"
    assert req.method == "GET"


def test_fetch_cancel_on_disconnect_null_grace_secs(httpx_mock) -> None:
    # grace_secs absent (feature unavailable on this deployment) → None.
    httpx_mock.add_response(
        url=_URL,
        method="GET",
        json={"enabled": False, "active": False},
    )
    with _authed() as client:
        status = client.fetch_cancel_on_disconnect()
    assert status.enabled is False
    assert status.active is False
    assert status.grace_secs is None


# -- set_cancel_on_disconnect ------------------------------------------------


def test_set_cancel_on_disconnect_enable(httpx_mock) -> None:
    httpx_mock.add_response(
        url=_URL,
        method="PUT",
        json={"enabled": True, "active": True, "grace_secs": 15},
    )
    with _authed() as client:
        status = client.set_cancel_on_disconnect(True)
    assert isinstance(status, CancelOnDisconnectStatus)
    assert status.enabled is True
    assert status.active is True
    assert status.grace_secs == 15
    req = httpx_mock.get_request()
    assert req.headers["x-api-key"] == "nx_test"
    assert req.method == "PUT"
    assert json.loads(req.content) == {"enabled": True}


def test_set_cancel_on_disconnect_disable(httpx_mock) -> None:
    httpx_mock.add_response(
        url=_URL,
        method="PUT",
        json={"enabled": False, "active": False, "grace_secs": None},
    )
    with _authed() as client:
        status = client.set_cancel_on_disconnect(False)
    assert status.enabled is False
    assert status.grace_secs is None
    req = httpx_mock.get_request()
    assert req.headers["x-api-key"] == "nx_test"
    assert req.method == "PUT"
    assert json.loads(req.content) == {"enabled": False}
