"""A real-write test must block every unrelated or repeated request before HTTP."""

import json
from unittest.mock import Mock

import pytest

from tests.test_client import _make_response
from tests.test_live_workflow_write import WriteGuard, lifecycle_texts


@pytest.fixture()
def guard():
    send = Mock(return_value=_make_response(200, {"id": "700"}))
    g = WriteGuard(send, article_id="1", pin_id="2", question_id="3", marker="test-marker")
    return g


def test_guard_requires_successful_preflight(guard):
    with pytest.raises(AssertionError):
        guard(None, "POST", "https://www.zhihu.com/api/v4/collections", data=b"{}")
    guard.original.assert_not_called()


@pytest.mark.parametrize(
    "method,url,body",
    [
        ("POST", "https://www.zhihu.com/api/v4/read_history/add", {}),
        ("POST", "https://api.zhihu.com/read_history/batch_del", {}),
        ("DELETE", "https://www.zhihu.com/api/v4/answers/999", None),
        ("DELETE", "https://www.zhihu.com/api/v4/collections/999", None),
        ("POST", "https://www.zhihu.com/api/v4/articles/999/voters", {"voting": 1}),
        ("POST", "https://www.zhihu.com/api/v4/members/other/followers", None),
        ("POST", "https://www.zhihu.com/api/v4/collections", {"title": "other", "is_public": True}),
    ],
)
def test_guard_rejects_unapproved_targets_and_operations(guard, method, url, body):
    guard.ready = True
    with pytest.raises(AssertionError):
        guard(None, method, url, data=json.dumps(body).encode() if body is not None else None)
    guard.original.assert_not_called()


def test_guard_binds_only_a_new_collection_and_blocks_repeated_creation(guard):
    guard.ready = True
    body = {"title": guard.title, "description": "临时接口实测，完成后删除。", "is_public": False}
    guard(None, "POST", "https://www.zhihu.com/api/v4/collections", data=json.dumps(body).encode())
    assert guard.collection == "700"
    with pytest.raises(AssertionError):
        guard(
            None, "POST", "https://www.zhihu.com/api/v4/collections", data=json.dumps(body).encode()
        )
    guard.original.assert_called_once()


def test_guard_cannot_bind_or_delete_an_existing_resource(guard):
    guard.old_collections = {"700"}
    guard.old_answers = {"800"}
    with pytest.raises(AssertionError):
        guard.bind("collection", "700")
    with pytest.raises(AssertionError):
        guard.bind("answer", "800")


def test_guard_publish_text_and_question_are_both_exact(guard):
    guard.ready = True
    from zhihu_cli.action_requests import build_answer_publish_request

    plan = build_answer_publish_request("999", lifecycle_texts(guard.marker)[0])
    with pytest.raises(AssertionError):
        guard(None, "POST", plan["url"], data=json.dumps(plan["json"]).encode())
    guard.original.assert_not_called()
