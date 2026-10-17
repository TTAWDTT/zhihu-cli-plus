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
        if request.node.get_closest_marker("integration_dm_write"):
            from tests.test_live_messages import enable_dm_guard

            enable_dm_guard(monkeypatch, request)
            return
        if request.node.get_closest_marker("integration_workflow_write"):
            from tests.test_live_workflow_write import enable_guard

            enable_guard(monkeypatch, request)
            return
        if request.node.get_closest_marker("integration_like"):
            _enable_comment_like_test(monkeypatch, request)
            return
        write_mode = bool(request.node.get_closest_marker("integration_write"))
        if write_mode and not request.config.getoption("--live-write"):
            pytest.skip("Explicit --live-write approval is required for comment lifecycle tests")
        if write_mode:
            from tests.test_live_comment_write import lifecycle_texts
            from zhihu_cli.comment_requests import numeric_id

            pin_id = request.config.getoption("--live-write-pin-id")
            marker = request.config.getoption("--live-write-marker")
            if not pin_id or not marker:
                pytest.fail("A specific --live-write-pin-id and --live-write-marker are required")
            state = {
                "pin_id": numeric_id(pin_id),
                "created": [],
                "posts": 0,
                "texts": lifecycle_texts(marker),
                "deleted": [],
            }
            request.node.comment_write_state = state
        import time

        import requests

        original_request = requests.sessions.Session.request
        last_request = [0.0]

        def read_only(session, method, *args, **kwargs):
            if method.upper() != "GET":
                if not write_mode:
                    raise AssertionError("Online tests only allow GET requests")
                url = args[0] if args else kwargs.get("url")
                if method.upper() == "POST":
                    expected = (
                        f"https://www.zhihu.com/api/v4/comment_v5/pins/{state['pin_id']}/comment"
                    )
                    if url != expected or state["posts"] >= 3:
                        raise AssertionError(
                            "Only three approved posts on the specified pin are allowed"
                        )
                    payload = json.loads(kwargs.get("data", b"{}"))
                    from html import escape

                    index = state["posts"]
                    if payload.get("content") != f"<p>{escape(state['texts'][index])}</p>":
                        raise AssertionError("Post body differs from the approved lifecycle text")
                    target = payload.get("reply_comment_id")
                    if index == 0 and target is not None:
                        raise AssertionError("The first test comment must be a root comment")
                    if index > 0 and (
                        len(state["created"]) < index or str(target) != state["created"][index - 1]
                    ):
                        raise AssertionError(
                            "Replies may only target comments created by this test"
                        )
                    state["posts"] += 1
                elif method.upper() == "DELETE":
                    prefix = "https://www.zhihu.com/api/v4/comment_v5/comment/"
                    comment_id = url.removeprefix(prefix)
                    if not url.startswith(prefix) or comment_id not in state["created"]:
                        raise AssertionError("Only comments created by this test may be deleted")
                else:
                    raise AssertionError(
                        "This lifecycle test only allows GET, POST and cleanup DELETE"
                    )
            time.sleep(max(0.0, 0.25 - (time.monotonic() - last_request[0])))
            last_request[0] = time.monotonic()
            response = original_request(session, method, *args, **kwargs)
            if write_mode and method.upper() == "POST" and response.status_code in (200, 201):
                try:
                    data = response.json()
                    comment_id = numeric_id(data.get("id"))
                except (ValueError, AttributeError):
                    pass
                else:
                    state["created"].append(comment_id)
            return response

        monkeypatch.setattr("requests.sessions.Session.request", read_only)
        return

    def blocked(*args, **kwargs):
        raise AssertionError("Live HTTP is disabled in unit tests")

    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    monkeypatch.setattr("zhihu_cli.client.time.sleep", lambda _: None)


def _enable_comment_like_test(monkeypatch, request):
    """Allow exactly one like and one restoration on an explicitly selected ID."""
    import time

    import requests

    from zhihu_cli.comment_requests import numeric_id

    if not request.config.getoption("--live-like"):
        pytest.skip("Explicit --live-like approval is required for a real comment like")
    comment_id = request.config.getoption("--live-like-comment-id")
    if not comment_id:
        pytest.fail("A specific --live-like-comment-id is required")
    state = {"comment_id": numeric_id(comment_id), "requests": []}
    request.node.comment_like_state = state
    allowed_url = f"https://www.zhihu.com/api/v4/comments/{state['comment_id']}/like"
    original = requests.sessions.Session.request
    last_request = [0.0]

    def guarded(session, method, *args, **kwargs):
        method = method.upper()
        if method != "GET":
            url = args[0] if args else kwargs.get("url")
            if url != allowed_url or method not in ("POST", "DELETE"):
                raise AssertionError("Only likes on the explicitly approved comment are allowed")
            if kwargs.get("data") is not None or kwargs.get("json") is not None:
                raise AssertionError("Comment likes must not send a body")
            expected = "POST" if not state["requests"] else "DELETE"
            if len(state["requests"]) >= 2 or method != expected:
                raise AssertionError("Only one like followed by one restoration is allowed")
            state["requests"].append(method)
        time.sleep(max(0.0, 0.25 - (time.monotonic() - last_request[0])))
        last_request[0] = time.monotonic()
        return original(session, method, *args, **kwargs)

    monkeypatch.setattr("requests.sessions.Session.request", guarded)


def pytest_addoption(parser):
    parser.addoption(
        "--live-dm-user-id", help="Participant explicitly selected for private-message tests"
    )
    parser.addoption(
        "--live-dm-send", action="store_true", help="Allow one explicitly approved private message"
    )
    parser.addoption("--live-dm-text", help="Exact approved private-message test text")
    parser.addoption(
        "--live", action="store_true", help="Enable read-only online integration tests"
    )
    parser.addoption(
        "--live-article-id", help="Optional published article for online comment tests"
    )
    parser.addoption("--live-pin-id", help="Optional published pin for online comment tests")
    parser.addoption(
        "--live-write",
        action="store_true",
        help="Explicitly allow the temporary comment lifecycle test",
    )
    parser.addoption("--live-write-pin-id", help="Your own pin for the approved lifecycle test")
    parser.addoption("--live-write-marker", help="Unique marker shown in the approved test text")
    parser.addoption(
        "--live-like", action="store_true", help="Allow one approved comment like and restoration"
    )
    parser.addoption("--live-like-comment-id", help="Exact comment approved for the like test")
    parser.addoption(
        "--live-workflow-write",
        action="store_true",
        help="Enable an explicitly approved workflow lifecycle",
    )
    parser.addoption("--live-workflow-article-id", help="Owned article approved for testing")
    parser.addoption("--live-workflow-pin-id", help="Owned pin approved for testing")
    parser.addoption(
        "--live-workflow-question-id", help="Owned question approved for temporary answer testing"
    )
    parser.addoption(
        "--live-workflow-marker", help="Marker shown in the approved temporary content"
    )
