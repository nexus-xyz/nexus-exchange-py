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

One decode rule, applied consistently: a field the spec marks **required**
decodes strictly — absent, ``null`` or the wrong shape raises
:class:`~nexus_exchange.DecodeError` rather than yielding a fabricated value.
A field the spec marks optional or nullable decodes to ``None``, so "the server
did not report this" stays distinguishable from a real zero. The ``to_*``
helpers are the strict half, the ``opt_*`` helpers the lenient half.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import DecodeError


def _describe(field: str | None, kind: str) -> str:
    # Deliberately not "a required ... field": the malformed-value branches below
    # are reachable from the `opt_*` helpers too, where the field is optional and
    # only its *value* is wrong.
    return f"{field!r}" if field else f"a {kind} field"


def to_decimal(value: Any, field: str | None = None) -> Decimal:
    """Coerce a *required* wire value (string or JSON number) to an exact ``Decimal``.

    Goes through ``str`` so a JSON number decodes to the decimal text that
    arrived, not an ``f64`` round-trip.

    Raises :class:`~nexus_exchange.DecodeError` when ``value`` is ``None`` — i.e.
    the field was missing or sent ``null`` — or when it is not parseable as a
    decimal. Required money fields must not silently default to ``Decimal(0)``,
    since that would mask a malformed payload. Use :func:`opt_decimal` for
    fields that are legitimately optional/nullable. Pass ``field`` to name it in
    the error message.
    """
    if value is None:
        raise DecodeError(f"{_describe(field, 'decimal')} is missing or null")
    if isinstance(value, Decimal):
        parsed = value
    else:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise DecodeError(f"{_describe(field, 'decimal')} is not a decimal: {value!r}") from exc
    # `Decimal("NaN")` / `Decimal("Infinity")` parse happily and then poison every
    # comparison and sum they touch (NaN != NaN), so a non-finite money value is
    # a decode failure, not a number.
    if not parsed.is_finite():
        raise DecodeError(f"{_describe(field, 'decimal')} is not finite: {value!r}")
    return parsed


def opt_decimal(value: Any, field: str | None = None) -> Decimal | None:
    """Like :func:`to_decimal`, but ``None`` (or missing) stays ``None``.

    Lets an optional/nullable money field decode without failing the whole
    model when the API sends ``null`` or omits it. A *present* but unparseable
    or non-finite value still raises — that is a malformed payload, not an
    absent field.
    """
    if value is None:
        return None
    return to_decimal(value, field)


def to_int(value: Any, field: str | None = None) -> int:
    """Coerce a *required* wire integer to ``int``.

    The integer counterpart of :func:`to_decimal`: raises
    :class:`~nexus_exchange.DecodeError` when ``value`` is ``None`` (missing or
    sent ``null``) rather than defaulting. Used for spec-``required`` structural
    integers where a fabricated ``0`` would be nonsense downstream (a zero
    downsample cadence, a zero timestamp, a zero fee rate). Use :func:`opt_int`
    for genuinely optional fields.

    A non-integral value raises rather than truncating: ``int(2.9)`` silently
    yielding a ``2`` bps fee rate is the same class of fabrication as a
    defaulted zero.
    """
    if value is None:
        raise DecodeError(f"{_describe(field, 'integer')} is missing or null")
    return _coerce_int(value, field, integral=True)


def _coerce_int(value: Any, field: str | None, *, integral: bool) -> int:
    # `bool` is an `int` subclass; `True` as a count, a cadence or a fee rate is a
    # payload defect, not the number 1.
    if isinstance(value, bool):
        raise DecodeError(f"{_describe(field, 'integer')} is a bool, not an integer: {value!r}")
    if isinstance(value, int):
        return value
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise DecodeError(f"{_describe(field, 'integer')} is not an integer: {value!r}") from exc
    if not parsed.is_finite():
        raise DecodeError(f"{_describe(field, 'integer')} is not finite: {value!r}")
    # `integral=True` refuses to truncate: a 2.9 that becomes a 2 bps fee rate or
    # a 1.5 ms cadence is a fabricated figure. The lenient path keeps truncating,
    # since its callers are timestamps and counters where a venue sending a
    # fractional millisecond should not fail the whole surrounding model.
    if integral and parsed != parsed.to_integral_value():
        raise DecodeError(f"{_describe(field, 'integer')} is not an integer: {value!r}")
    return int(parsed)


def opt_int(value: Any, field: str | None = None) -> int | None:
    """Coerce an optional/nullable integer field; ``None`` (or missing) stays ``None``.

    For fields like a CCXT ``timestamp`` that a venue may legitimately omit, so
    callers can tell "no timestamp" from a real ``0``.

    Leniently truncates a fractional number (unlike :func:`to_int`) so an odd
    venue timestamp does not fail the whole surrounding model, but still rejects
    a ``bool``, a non-finite value, and anything unparseable.
    """
    if value is None:
        return None
    return _coerce_int(value, field, integral=False)


def opt_str(value: Any) -> str | None:
    """Coerce an optional/nullable string field; ``None`` (or missing) stays ``None``."""
    if value is None:
        return None
    return str(value)


def to_str(value: Any, field: str | None = None) -> str:
    """Coerce a *required* wire string to ``str``.

    Raises :class:`~nexus_exchange.DecodeError` when ``value`` is ``None``
    (missing or sent ``null``). The string counterpart of :func:`to_decimal`,
    for spec-``required`` strings — including *open* ones the SDK deliberately
    does not model as an enum, so an unknown value still decodes.

    Deliberately does **not** substitute ``""``: an empty string is a value that
    matches no caller branch and is indistinguishable from a server that really
    sent ``""``, which is the same "plausible but never reported" failure a
    defaulted ``0`` would be.
    """
    if value is None:
        raise DecodeError(f"{_describe(field, 'string')} is missing or null")
    return str(value)


def to_dict_list(value: Any, field: str, *, required: bool = True) -> list[dict[str, Any]]:
    """Coerce a wire array-of-objects, rejecting malformed elements.

    Raises :class:`~nexus_exchange.DecodeError` when ``value`` is not a list, or
    when any element is not an object. Elements are never silently skipped: a
    list that comes back shorter than the server sent is undetectable by the
    caller and bends everything derived from it — a downsampled series with a
    hole no longer matches its own cadence, and a risk snapshot missing a
    position understates exposure. That is the same harm as a fabricated number.

    ``required=False`` lets an absent or ``null`` array decode to ``[]``, for
    endpoints whose payload legitimately omits it.
    """
    if value is None:
        if required:
            raise DecodeError(f"required array field {field!r} is missing or null")
        return []
    if not isinstance(value, list):
        raise DecodeError(f"field {field!r} must be an array, got {type(value).__name__}")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise DecodeError(
                f"{field}[{index}] must be an object, got {type(item).__name__}: {item!r}"
            )
    # A copy, so a caller mutating the returned list cannot reach into `raw`.
    return list(value)
