"""The R2.25 renames (ENG-17744): every old name still works, and warns.

Each method was renamed to ``snake_case(operationId)``. The old name stays for
one minor release as an alias that emits ``DeprecationWarning`` and delegates to
the new one with the same arguments, returning its result unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import Mock, sentinel

import pytest

from nexus_exchange import AmendOrder, Client, Network

_SIGNER = sentinel.signer
_AMEND = AmendOrder(price=Decimal("1"))

# (old, new, args passed to old, (args, kwargs) the new one must receive)
_ALIASES = [
    ("fetch_market_summaries", "fetch_markets_summary", (), {}, (), {}),
    ("fetch_market_adl_events", "fetch_adl_events", ("BTC", 5), {}, ("BTC", 5), {}),
    ("fetch_account_adl_history", "fetch_adl_history", ("0xabc", 5), {}, ("0xabc", 5), {}),
    ("fetch_service_health", "fetch_status", (), {}, (), {}),
    ("sign_in", "login", (_SIGNER,), {}, (_SIGNER,), {}),
    ("fetch_closed_positions", "fetch_positions_history", (7,), {}, (7,), {}),
    (
        "fetch_closed_positions_page",
        "fetch_positions_history_page",
        (),
        {"limit": 7, "cursor": "c"},
        (),
        {"limit": 7, "cursor": "c"},
    ),
    (
        "iter_closed_positions",
        "iter_positions_history",
        (),
        {"limit": 7, "cursor": "c", "max_pages": 2},
        (),
        {"limit": 7, "cursor": "c", "max_pages": 2},
    ),
    ("fetch_account_fees", "fetch_trading_fees", (), {}, (), {}),
    ("fetch_account_funding", "fetch_funding_history", (3,), {}, (3,), {}),
    ("adjust_margin", "add_margin", ("BTC", "add", "1"), {}, ("BTC", "add", "1"), {}),
    ("fetch_order_history", "fetch_orders", (7,), {}, (7,), {}),
    (
        "fetch_order_history_page",
        "fetch_orders_page",
        (),
        {"limit": 7, "cursor": "c"},
        (),
        {"limit": 7, "cursor": "c"},
    ),
    (
        "iter_order_history",
        "iter_orders",
        (),
        {"limit": 7, "cursor": "c", "max_pages": 2},
        (),
        {"limit": 7, "cursor": "c", "max_pages": 2},
    ),
    ("amend_order", "edit_order", ("o1", "BTC", _AMEND), {}, ("o1", "BTC", _AMEND), {}),
    ("mint_web_socket_token", "create_ws_token_legacy", (), {}, (), {}),
    ("set_account_tier", "set_tier", ("0xabc", "mm"), {}, ("0xabc", "mm"), {}),
    ("fetch_tier_overrides", "fetch_tiers", (), {}, (), {}),
    ("reset_account_tier", "delete_tier", ("0xabc",), {}, ("0xabc",), {}),
    ("list_bridge_deposit_addresses", "fetch_bridge_deposit_addresses", (), {}, (), {}),
]


@pytest.mark.parametrize(
    ("old", "new", "args", "kwargs", "want_args", "want_kwargs"),
    _ALIASES,
    ids=[a[0] for a in _ALIASES],
)
def test_old_name_warns_and_delegates(
    old: str,
    new: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    want_args: tuple[Any, ...],
    want_kwargs: dict[str, Any],
) -> None:
    client = Client(Network.LOCAL)
    target = Mock(return_value=sentinel.result)
    setattr(client, new, target)
    with pytest.warns(DeprecationWarning, match=f"`{old}` is deprecated; use `{new}`"):
        result = getattr(client, old)(*args, **kwargs)
    assert result is sentinel.result
    target.assert_called_once_with(*want_args, **want_kwargs)


def test_the_warning_points_at_the_caller() -> None:
    client = Client(Network.LOCAL)
    client.fetch_status = Mock()  # type: ignore[method-assign]
    with pytest.warns(DeprecationWarning) as caught:
        client.fetch_service_health()
    assert caught[0].filename == __file__
