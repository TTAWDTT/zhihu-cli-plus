"""Shared collection paging, preserving server cursors and response metadata."""

from __future__ import annotations

import json
from collections.abc import Callable

import click

from .exceptions import DataFetchError


def pagination_options(function):
    """Options shared by commands that return collections."""
    for option in (
        click.option("--offset", default="0", help="Starting offset or opaque API cursor."),
        click.option("--next-url", default=None, help="Resume from an API paging.next URL."),
        click.option("--all", "all_pages", is_flag=True, help="Fetch all pages."),
        click.option(
            "--max-pages",
            default=100,
            type=click.IntRange(min=1),
            show_default=True,
            help="Maximum pages; exceeding this fails explicitly.",
        ),
    ):
        function = option(function)
    return function


def collect_pages(
    client,
    fetch: Callable,
    *,
    all_pages: bool = False,
    max_pages: int = 100,
    next_url: str | None = None,
) -> dict:
    """Follow paging.next verbatim, stopping safely on empty or terminal pages.

    The single-page response is unchanged. Multi-page results replace only data,
    paging, and pages_fetched. Never return partial success after a failed page.
    """
    if max_pages < 1:
        raise DataFetchError("max_pages must be positive")
    page = client.get_next_page(next_url) if next_url else fetch()
    result = None
    items = []
    visited = {next_url} if next_url else set()
    fingerprints = set()
    pages = 0
    while True:
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise DataFetchError("Invalid collection response")
        paging = page.get("paging", {})
        if not isinstance(paging, dict):
            raise DataFetchError("Invalid paging metadata")
        if not all_pages:
            return page
        if result is None:
            result = dict(page)
        data = page.get("data", [])
        if data:
            fingerprint = json.dumps(data, sort_keys=True, ensure_ascii=False)
            if fingerprint in fingerprints:
                raise DataFetchError("Pagination repeated a page; refusing an infinite loop")
            fingerprints.add(fingerprint)
        items.extend(data)
        pages += 1
        result.update(data=items, paging=paging, pages_fetched=pages)
        if not data or paging.get("is_end", not bool(paging.get("next"))):
            return result
        cursor_url = paging.get("next")
        if not isinstance(cursor_url, str) or not cursor_url:
            raise DataFetchError("API reports more pages but omitted paging.next")
        if cursor_url in visited:
            raise DataFetchError("Pagination repeated a cursor; refusing an infinite loop")
        if pages >= max_pages:
            raise DataFetchError("Pagination exceeded --max-pages; increase the bound to continue")
        visited.add(cursor_url)
        page = client.get_next_page(cursor_url)
