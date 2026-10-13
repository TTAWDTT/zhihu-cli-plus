"""Explicitly approved writes on your own pin, with cleanup on assertion failure.

Never included in ordinary offline or GET-only test runs. The request guard
enforces the exact pin, three exact texts, and deletion only of newly created IDs.
"""

from __future__ import annotations

import time

import pytest

from zhihu_cli.auth import cookie_str_to_dict, get_saved_cookie_string
from zhihu_cli.client import ZhihuClient
from zhihu_cli.display import strip_html
from zhihu_cli.exceptions import DataFetchError

pytestmark = [pytest.mark.integration, pytest.mark.integration_write]


def lifecycle_texts(marker: str) -> list[str]:
    return [
        f"zhihu-cli-plus 临时测试（{marker}）：验证{label}，测试完成后删除。"
        for label in ("发表评论", "回复根评论", "回复子评论")
    ]


def _deleted(client, comment_id):
    for _ in range(5):
        try:
            comment = client.get_comment(comment_id)
        except DataFetchError as error:
            if "status 404" in str(error):
                return True
            raise
        if comment.get("is_delete"):
            return True
        time.sleep(0.5)
    return False


def test_approved_root_reply_child_reply_and_cleanup(request):
    state = request.node.comment_write_state
    cookie = get_saved_cookie_string()
    if not cookie:
        pytest.fail("A complete local login is required; no credentials printed")
    with ZhihuClient(cookie_str_to_dict(cookie)) as client:
        me = client.get_self_info()
        pin = client.get_pin(state["pin_id"])
        if pin.get("author", {}).get("id") != me.get("id"):
            pytest.fail("The approved lifecycle pin is not owned by the current logged-in account")
        try:
            root = client.create_comment(state["pin_id"], state["texts"][0], "pin")
            root_id = str(root["id"])
            time.sleep(5)
            reply = client.reply_comment(state["pin_id"], root_id, state["texts"][1], "pin")
            reply_id = str(reply["id"])
            time.sleep(5)
            child = client.reply_comment(state["pin_id"], reply_id, state["texts"][2], "pin")
            child_id = str(child["id"])
            expected_ids = [root_id, reply_id, child_id]
            assert len(set(expected_ids)) == 3
            for index, comment_id in enumerate(expected_ids):
                detail = client.get_comment(comment_id)
                assert detail.get("is_author") is True
                assert detail.get("resource_type") == "pin"
                assert strip_html(detail.get("content", "")) == state["texts"][index]
                assert not detail.get("is_delete")
                if index > 0:
                    assert str(detail["reply_comment_id"]) == expected_ids[index - 1]
                    assert str(detail["reply_root_comment_id"]) == root_id
        finally:
            failures = []
            for comment_id in reversed(state["created"]):
                try:
                    if _deleted(client, comment_id):
                        state["deleted"].append(comment_id)
                        continue
                    client.delete_comment(comment_id)
                    if not _deleted(client, comment_id):
                        raise AssertionError("The deleted comment is still visible through the API")
                    state["deleted"].append(comment_id)
                except Exception as error:
                    failures.append(f"{comment_id}: {type(error).__name__}: {error}")
            if failures:
                pytest.fail("Cleanup needs attention: " + "; ".join(failures))
        assert state["posts"] == 3
        assert set(state["deleted"]) == set(state["created"])
