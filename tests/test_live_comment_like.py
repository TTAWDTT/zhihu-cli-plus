"""Opt-in real CLI like, readback, cancellation, and state restoration."""

from __future__ import annotations

import time

import pytest

from tests.test_live import run_json
from zhihu_cli.auth import cookie_str_to_dict, get_saved_cookie_string
from zhihu_cli.client import ZhihuClient

pytestmark = [pytest.mark.integration, pytest.mark.integration_like]


def _wait_for_liked(client, comment_id, expected):
    for _ in range(5):
        detail = client.get_comment(comment_id)
        if detail.get("liked") is expected:
            return detail
        time.sleep(0.5)
    raise AssertionError(f"Comment {comment_id} did not read back with liked={expected}")


def test_approved_like_and_restore(request):
    state = request.node.comment_like_state
    comment_id = state["comment_id"]
    cookie = get_saved_cookie_string()
    if not cookie:
        pytest.fail("A complete local login is required; no credentials printed")
    with ZhihuClient(cookie_str_to_dict(cookie)) as client:
        before = client.get_comment(comment_id)
        me = client.get_self_info()
        assert before.get("liked") is False, "Preserve any pre-existing like; do not write"
        assert before.get("can_like") is True
        assert before.get("author", {}).get("id") != me["id"], "Use another person's comment"
        try:
            result = run_json(["like-comment", comment_id])
            assert result["success"] is True and result["liked"] is True
            assert result["comment_id"] == comment_id
            liked = _wait_for_liked(client, comment_id, True)
            assert liked["content"] == before["content"]
        finally:
            if state["requests"]:
                result = run_json(["like-comment", comment_id, "--undo"])
                assert result["success"] is True and result["liked"] is False
                after = _wait_for_liked(client, comment_id, False)
                assert after["content"] == before["content"]
                request.node.user_properties.extend(
                    [
                        ("approved_comment_id", comment_id),
                        ("write_requests", ",".join(state["requests"])),
                        ("original_liked", "false"),
                        ("restored_liked", "false"),
                    ]
                )
        assert state["requests"] == ["POST", "DELETE"]
