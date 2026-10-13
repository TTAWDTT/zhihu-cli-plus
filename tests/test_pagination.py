"""Regression tests for opaque cursors, bounded paging, and partial failures."""

from unittest.mock import Mock

import pytest

from zhihu_cli.exceptions import DataFetchError
from zhihu_cli.pagination import collect_pages

NEXT = "https://www.zhihu.com/api/v4/answers/1/comments?offset=opaque%2Bcursor&limit=2"


def page(ids, *, next_url=None, is_end=True):
    return {
        "data": [{"id": item} for item in ids],
        "paging": {"is_end": is_end, "next": next_url},
        "extra": "preserved",
    }


def test_single_page_preserves_response():
    first = page([1], next_url=NEXT, is_end=False)
    client = Mock()
    assert collect_pages(client, lambda: first) is first
    client.get_next_page.assert_not_called()


def test_follows_opaque_url_without_rewriting_cursor():
    client = Mock()
    client.get_next_page.return_value = page([3])
    result = collect_pages(
        client, lambda: page([1, 2], next_url=NEXT, is_end=False), all_pages=True
    )
    assert [item["id"] for item in result["data"]] == [1, 2, 3]
    assert result["extra"] == "preserved"
    assert result["pages_fetched"] == 2
    assert result["paging"]["is_end"] is True
    client.get_next_page.assert_called_once_with(NEXT)


def test_resume_url_skips_initial_fetch():
    client, fetch = Mock(), Mock()
    first = page([2])
    client.get_next_page.return_value = first
    assert collect_pages(client, fetch, next_url=NEXT) is first
    fetch.assert_not_called()


def test_repeated_cursor_fails():
    client = Mock()
    client.get_next_page.return_value = page([2], next_url=NEXT, is_end=False)
    with pytest.raises(DataFetchError, match="repeated a cursor"):
        collect_pages(client, lambda: page([1], next_url=NEXT, is_end=False), all_pages=True)
    assert client.get_next_page.call_count == 1


def test_repeated_page_fails_even_with_changing_cursor():
    client = Mock()
    client.get_next_page.return_value = page([1], next_url=NEXT + "&other=2", is_end=False)
    with pytest.raises(DataFetchError, match="repeated a page"):
        collect_pages(client, lambda: page([1], next_url=NEXT, is_end=False), all_pages=True)


def test_page_bound_never_returns_truncated_success():
    client = Mock()
    with pytest.raises(DataFetchError, match="max-pages"):
        collect_pages(
            client, lambda: page([1], next_url=NEXT, is_end=False), all_pages=True, max_pages=1
        )
    client.get_next_page.assert_not_called()


def test_later_page_failure_propagates():
    client = Mock()
    client.get_next_page.side_effect = DataFetchError("offline")
    with pytest.raises(DataFetchError, match="offline"):
        collect_pages(client, lambda: page([1], next_url=NEXT, is_end=False), all_pages=True)


@pytest.mark.parametrize("response", [[], {"data": {}}, {"data": [], "paging": []}])
def test_malformed_pages_fail(response):
    with pytest.raises(DataFetchError):
        collect_pages(Mock(), lambda: response, all_pages=True)


def test_missing_next_is_an_explicit_error():
    with pytest.raises(DataFetchError, match="omitted"):
        collect_pages(Mock(), lambda: page([1], is_end=False), all_pages=True)


def test_empty_page_stops():
    client = Mock()
    result = collect_pages(client, lambda: page([], next_url=NEXT, is_end=False), all_pages=True)
    assert result["data"] == []
    client.get_next_page.assert_not_called()


def test_next_without_is_end_still_follows_cursor():
    client = Mock()
    client.get_next_page.return_value = page([2])
    result = collect_pages(
        client, lambda: {"data": [{"id": 1}], "paging": {"next": NEXT}}, all_pages=True
    )
    assert len(result["data"]) == 2
