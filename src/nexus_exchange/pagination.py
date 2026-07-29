"""Cursor pagination for the list endpoints (spec v0.7.2).

Five ``GET``s return a page of results plus an opaque cursor for the next page:
``/fills``, ``/orders/history``, ``/positions/closed``,
``/account/equity-history``, and ``/markets/{market_id}/trades`` (this SDK
implements trades and fills; see ``endpoints.txt``). The response body stays a
bare JSON array — pagination state rides only in the ``X-Next-Cursor`` response
header, which is **present only when more results exist**. Its absence means the
last page, not an error.

Two ways to consume a paginated endpoint, mirroring the Rust SDK's
``rest::pagination`` and the TS SDK's ``src/pagination.ts`` so the fleet reads
the same way:

* ``Client.fetch_*_page(...)`` returns one :class:`Page` — the items plus
  :attr:`Page.next_cursor`, which you pass back as ``cursor=`` to advance. Use
  this when the cursor has to be persisted between calls (a job that resumes).
* ``Client.iter_*(...)`` is a generator that walks every page to exhaustion,
  driving the cursor for you and yielding items one at a time. Pages are fetched
  lazily, so nothing beyond the current page is held in memory.

Cursors are opaque: treat the token as a blob, never parse it. Per the spec they
do not expire, a malformed cursor is served the first page rather than erroring,
and a cursor whose position has been evicted from the retained window resumes at
the nearest surviving boundary (so a resumed page is a continuation, not a fresh
first page).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Generic, TypeVar

from .errors import PaginationError

T = TypeVar("T")

#: Response header carrying the cursor for the next page. Absent on the last
#: page. Matched case-insensitively by httpx's header mapping.
NEXT_CURSOR_HEADER = "x-next-cursor"

__all__ = ["NEXT_CURSOR_HEADER", "Page", "iter_pages", "iter_items"]


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """One page of a list endpoint: the decoded items plus the next cursor.

    ``next_cursor`` is ``None`` on the final page (the endpoint sent no
    ``X-Next-Cursor`` header). Pass a non-``None`` value back to the same method
    as ``cursor=`` to fetch the page after this one.
    """

    #: The items in this page, in server order.
    items: list[T]
    #: Opaque cursor for the next page, or ``None`` when this is the last page.
    next_cursor: str | None = None

    @property
    def is_last(self) -> bool:
        """Whether this is the final page (no next cursor)."""
        return self.next_cursor is None


def iter_pages(
    fetch_page: Callable[[str | None], Page[T]],
    *,
    cursor: str | None = None,
    max_pages: int | None = None,
) -> Iterator[Page[T]]:
    """Yield every page from ``fetch_page``, following ``X-Next-Cursor``.

    ``fetch_page`` is called with the cursor for the page to fetch (``None`` for
    the first) and returns the :class:`Page` it decoded. Requests are issued
    lazily — one per ``next()`` — so a caller that stops early stops paging.

    ``cursor`` resumes from a previously obtained cursor instead of starting at
    the first page. ``max_pages`` caps how many pages (hence requests) are
    fetched: iteration ends cleanly once that many have been yielded, even if the
    server is still handing back a cursor. ``max_pages=0`` fetches nothing.

    Termination is guarded against a misbehaving server. Two distinct failures:

    * The server hands back the **same** cursor it was given. Advancing is then
      impossible, so re-issuing would spin forever on one page. This raises
      :class:`~nexus_exchange.PaginationError` rather than stopping quietly — the
      results so far are silently incomplete, and a caller told "that's all"
      would act on a truncated history. (The Rust SDK's paginator stops silently
      here; raising is the deliberate difference, because a Python generator
      feeding ``list(...)`` gives the caller no other signal.)
    * The server keeps advancing cursors without end. No client-side rule can
      tell that apart from a genuinely long result set, so bound it with
      ``max_pages`` when walking an unbounded history.

    An empty page that still carries a cursor is *not* the end: it is yielded as
    is and paging continues, so a sparse window does not truncate the walk.
    """
    if max_pages is not None and max_pages < 0:
        raise ValueError(f"max_pages must be non-negative (got {max_pages})")

    pages = 0
    while max_pages is None or pages < max_pages:
        requested = cursor
        page = fetch_page(requested)
        pages += 1
        yield page

        nxt = page.next_cursor
        if nxt is None:
            return
        if requested is not None and nxt == requested:
            raise PaginationError(
                "server returned the same pagination cursor it was given "
                f"({nxt!r}); refusing to re-request the same page forever. "
                f"{pages} page(s) were read, so any results collected so far are "
                "incomplete."
            )
        cursor = nxt


def iter_items(
    fetch_page: Callable[[str | None], Page[T]],
    *,
    cursor: str | None = None,
    max_pages: int | None = None,
) -> Iterator[T]:
    """Yield every item across every page — :func:`iter_pages`, flattened.

    The item-level view most callers want::

        for fill in client.iter_my_trades(limit=500):
            ...

    Arguments and termination semantics are :func:`iter_pages`'.
    """
    for page in iter_pages(fetch_page, cursor=cursor, max_pages=max_pages):
        yield from page.items
