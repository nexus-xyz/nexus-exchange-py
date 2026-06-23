"""Small parsing helpers shared by the typed models.

Money is modeled as :class:`decimal.Decimal` throughout, mirroring the Rust
SDK's ``rust_decimal::Decimal``. The wire sends money two ways and these helpers
keep one consistent type either way:

* **Decimal *strings*** (e.g. ``"50011.60"``) — authoritative, exact. Parsed
  straight into ``Decimal`` with no intermediate float.
* **JSON *numbers*** (e.g. ``50011.6``) on the CCXT-style market-data routes.
  These are parsed via ``Decimal(str(x))`` so the value matches the JSON text
  that arrived rather than an ``f64`` re-rendering. They are still *number*
  fields on the wire, so treat them as display/heuristic values — round to the
  market's tick/lot size before equality checks; use the string-typed fields
  (balances, fills, order prices, funding) for anything authoritative.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def to_decimal(value: Any) -> Decimal:
    """Coerce a wire value (string or JSON number) to an exact ``Decimal``.

    Goes through ``str`` so a JSON number decodes to the decimal text that
    arrived, not an ``f64`` round-trip.
    """
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def opt_decimal(value: Any) -> Decimal | None:
    """Like :func:`to_decimal`, but ``None`` (or missing) stays ``None``.

    Lets an optional/nullable money field decode without failing the whole
    model when the API sends ``null`` or omits it.
    """
    if value is None:
        return None
    return to_decimal(value)
