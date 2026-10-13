"""Shared fixtures for zhihu-cli tests."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect CONFIG_DIR and COOKIE_FILE to a temp directory."""
    config_dir = tmp_path / ".zhihu-cli"
    config_dir.mkdir()
    cookie_file = config_dir / "cookies.json"

    monkeypatch.setattr("zhihu_cli.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("zhihu_cli.config.COOKIE_FILE", cookie_file)
    # Also patch the auth module which imports at module level
    monkeypatch.setattr("zhihu_cli.auth.CONFIG_DIR", config_dir)
    monkeypatch.setattr("zhihu_cli.auth.COOKIE_FILE", cookie_file)

    return config_dir, cookie_file


@pytest.fixture()
def saved_cookies(tmp_config_dir):
    """Write a valid cookie file with z_c0 and return (dir, file, cookie_dict)."""
    config_dir, cookie_file = tmp_config_dir
    cookie_dict = {"z_c0": "test_token_abc", "_xsrf": "xsrf_123", "d_c0": "dc0_456"}
    cookie_file.write_text(
        json.dumps({"cookies": cookie_dict}, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_dir, cookie_file, cookie_dict


@pytest.fixture()
def mock_user_info():
    """Sample user info response from /api/v4/me."""
    return {
        "id": "123456",
        "name": "TestUser",
        "url_token": "test-user",
        "headline": "Test headline",
        "description": "A <b>test</b> user",
        "gender": 0,
        "answer_count": 42,
        "articles_count": 5,
        "follower_count": 12345,
        "following_count": 100,
        "voteup_count": 99999,
        "thanked_count": 500,
    }


@pytest.fixture()
def mock_search_result():
    """Sample search response."""
    return {
        "data": [
            {
                "type": "search_result",
                "object": {
                    "type": "answer",
                    "title": "How to learn Python",
                    "excerpt": "Start with the basics...",
                },
            },
            {
                "type": "search_result",
                "object": {
                    "type": "question",
                    "title": "Best resources?",
                    "answer_count": 10,
                },
            },
        ],
    }


@pytest.fixture()
def mock_hot_list():
    """Sample hot list response."""
    return {
        "data": [
            {
                "question": {"title": "Hot question 1", "id": "111"},
                "reaction": {"pv": 1500000, "new_pv": 85000},
            },
            {
                "question": {"title": "Hot question 2", "id": "222"},
                "reaction": {"pv": 800000, "new_pv": 42000},
            },
        ],
    }


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch, request):
    """Unit tests may only use mocked HTTP and must never touch a real account."""

    if request.node.get_closest_marker("integration"):
        if not request.config.getoption("--live"):
            pytest.skip("Explicit --live is required for online tests")
        import time

        import requests

        original_request = requests.sessions.Session.request
        last_request = [0.0]

        def read_only(session, method, *args, **kwargs):
            if method.upper() != "GET":
                raise AssertionError("Online tests only allow GET requests")
            time.sleep(max(0.0, 0.25 - (time.monotonic() - last_request[0])))
            last_request[0] = time.monotonic()
            return original_request(session, method, *args, **kwargs)

        monkeypatch.setattr("requests.sessions.Session.request", read_only)
        return

    def blocked(*args, **kwargs):
        raise AssertionError("Live HTTP is disabled in unit tests")

    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    monkeypatch.setattr("zhihu_cli.client.time.sleep", lambda _: None)


def pytest_addoption(parser):
    parser.addoption(
        "--live", action="store_true", help="Enable read-only online integration tests"
    )
    parser.addoption(
        "--live-article-id", help="Optional published article for online comment tests"
    )
    parser.addoption("--live-pin-id", help="Optional published pin for online comment tests")
