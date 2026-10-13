"""Exact comment targets, signed likes, cancellation, and failure reporting."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from click.testing import CliRunner

from tests.conftest import _enable_comment_like_test
from tests.test_cli import _make_mock_client
from tests.test_client import _make_response
from zhihu_cli.cli import cli
from zhihu_cli.client import ZhihuClient
from zhihu_cli.comment_requests import build_comment_like_request
from zhihu_cli.exceptions import DataFetchError, LoginError
from zhihu_cli.signing import sign_get_headers


@pytest.fixture()
def writer():
    with ZhihuClient({"z_c0": "dummy", "_xsrf": "xsrf", "d_c0": '"test=|1"'}) as client:
        yield client


@pytest.mark.parametrize("comment_id", ["11595516969", "11595572583", "2080378547047241207"])
@pytest.mark.parametrize("undo", [False, True])
def test_signed_bodyless_like_preserves_root_child_and_large_ids(writer, comment_id, undo):
    writer._session.cookies.set("_xsrf", "current-xsrf", domain=".zhihu.com")
    with patch.object(
        writer._session, "request", return_value=_make_response(200, {"success": True})
    ) as send:
        result = writer.like_comment(comment_id, undo=undo)
    assert result == {"success": True, "comment_id": comment_id, "liked": not undo}
    send.assert_called_once()
    url = f"https://www.zhihu.com/api/v4/comments/{comment_id}/like"
    assert send.call_args.args == ("DELETE" if undo else "POST", url)
    kwargs = send.call_args.kwargs
    assert kwargs["data"] is None
    assert "json" not in kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["x-xsrftoken"] == "current-xsrf"
    assert (
        kwargs["headers"]["x-zse-96"]
        == sign_get_headers(f"/api/v4/comments/{comment_id}/like", '"test=|1"')["x-zse-96"]
    )


@pytest.mark.parametrize("undo", [False, True])
def test_204_like_acknowledgement_does_not_need_json(writer, undo):
    response = _make_response(204)
    response.json.side_effect = ValueError("empty")
    with patch.object(writer._session, "request", return_value=response):
        result = writer.like_comment("123", undo=undo)
    assert result["liked"] is not undo
    response.json.assert_not_called()


def test_204_cannot_claim_that_a_new_comment_was_created(writer):
    with patch.object(writer._session, "request", return_value=_make_response(204)):
        with pytest.raises(DataFetchError, match="Comment ID missing"):
            writer.create_comment("123", "Text")


@pytest.mark.parametrize("comment_id", ["0", "-1", "../me", "１", True, None, 123.5])
def test_invalid_like_targets_never_send_http(writer, comment_id):
    with patch.object(writer._session, "request") as send:
        with pytest.raises(ValueError, match="positive numeric ID"):
            writer.like_comment(comment_id)
        send.assert_not_called()


def test_string_false_is_not_a_valid_cancellation_flag(writer):
    with patch.object(writer._session, "request") as send:
        with pytest.raises(ValueError, match="boolean"):
            writer.like_comment("123", undo="false")
        send.assert_not_called()


@pytest.mark.parametrize("missing", ["z_c0", "_xsrf", "d_c0"])
def test_like_requires_complete_cookies_before_dispatch(missing):
    cookies = {"z_c0": "z", "_xsrf": "x", "d_c0": "d"}
    del cookies[missing]
    with ZhihuClient(cookies) as client, patch.object(client._session, "request") as send:
        with pytest.raises(LoginError):
            client.like_comment("123")
        send.assert_not_called()


@pytest.mark.parametrize("undo", [False, True])
@pytest.mark.parametrize("status", [403, 404, 429, 500, 502, 503, 504])
def test_rejected_like_and_unlike_are_not_retried(writer, undo, status):
    with patch.object(
        writer._session, "request", return_value=_make_response(status, {"error": {"code": 10003}})
    ) as send:
        with pytest.raises(DataFetchError, match="rejected"):
            writer.like_comment("123", undo=undo)
        send.assert_called_once()


@pytest.mark.parametrize("undo", [False, True])
def test_expired_login_is_not_a_success(writer, undo):
    with patch.object(writer._session, "request", return_value=_make_response(401)) as send:
        with pytest.raises(LoginError):
            writer.like_comment("123", undo=undo)
        send.assert_called_once()


@pytest.mark.parametrize("undo", [False, True])
@pytest.mark.parametrize(
    "error", [requests.Timeout("timeout"), requests.ConnectionError("offline")]
)
def test_uncertain_likes_are_not_retried(writer, undo, error):
    with patch.object(writer._session, "request", side_effect=error) as send:
        with pytest.raises(DataFetchError, match="delivery is unknown"):
            writer.like_comment("123", undo=undo)
        send.assert_called_once()


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"success": False},
        {"success": 1},
        {"success": "true"},
        {"success": True, "code": 1},
        {"success": True, "error": {"code": 1}},
        {"success": True, "id": "999"},
        {"success": True, "comment_id": "999"},
        {"success": True, "liked": False},
        [],
    ],
)
def test_ambiguous_or_contradictory_success_responses_fail(writer, result):
    with patch.object(writer._session, "request", return_value=_make_response(200, result)) as send:
        with pytest.raises(DataFetchError):
            writer.like_comment("123")
        send.assert_called_once()


def test_unlike_does_not_accept_a_response_that_still_reports_liked(writer):
    with patch.object(
        writer._session,
        "request",
        return_value=_make_response(200, {"success": True, "liked": True}),
    ):
        with pytest.raises(DataFetchError, match="unexpected state"):
            writer.like_comment("123", undo=True)


def test_malformed_like_json_reports_uncertainty(writer):
    response = _make_response(200)
    response.json.side_effect = ValueError("not JSON")
    with patch.object(writer._session, "request", return_value=response) as send:
        with pytest.raises(DataFetchError, match="delivery is unknown"):
            writer.like_comment("123")
        send.assert_called_once()


def test_like_keeps_other_response_fields(writer):
    response = {"success": True, "id": "123", "liked": True, "like_count": 12}
    with patch.object(writer._session, "request", return_value=_make_response(200, response)):
        assert writer.like_comment("123") == {**response, "comment_id": "123"}


@pytest.mark.parametrize("undo", [False, True])
def test_like_cli_preview_is_offline_and_credential_free(undo, tmp_config_dir):
    args = ["like-comment", "2080378547047241207", "--dry-run", "--json"]
    if undo:
        args.append("--undo")
    with patch("zhihu_cli.client.ZhihuClient") as client:
        result = CliRunner().invoke(cli, args)
        client.assert_not_called()
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "dry_run": True,
        **build_comment_like_request("2080378547047241207", undo=undo),
    }
    assert "Cookie" not in result.stdout and "xsrf" not in result.stdout


@pytest.mark.parametrize("undo", [False, True])
def test_like_cli_preserves_exact_id_and_json_response(undo, saved_cookies):
    response = {"success": True, "comment_id": "2080378547047241207", "liked": not undo}
    client = _make_mock_client(like_comment=response)
    args = ["like-comment", "2080378547047241207", "--json"] + (["--undo"] if undo else [])
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == response
    client.like_comment.assert_called_once_with("2080378547047241207", undo=undo)


@pytest.mark.parametrize("undo", [False, True])
def test_like_cli_error_is_not_a_success_or_json_on_stdout(undo, saved_cookies):
    client = _make_mock_client()
    client.like_comment.side_effect = DataFetchError("Comment request rejected (403)")
    args = ["like-comment", "123", "--json"] + (["--undo"] if undo else [])
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1
    assert not result.stdout
    assert "rejected" in result.stderr


@pytest.mark.parametrize("liked", [False, True])
def test_comment_detail_text_shows_current_account_like_state(liked, saved_cookies):
    client = _make_mock_client(
        get_comment={"id": "123", "content": "Comment body", "liked": liked, "like_count": 7}
    )
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["comment-detail", "123"])
    assert result.exit_code == 0
    assert f"Liked by you: {'yes' if liked else 'no'}" in result.stdout
    assert "7 likes" in result.stdout


@pytest.mark.parametrize("args", [["0"], ["../me"], ["-1"], ["123", "--type", "article"]])
def test_like_cli_invalid_arguments_fail_before_login(args, tmp_config_dir):
    with patch("zhihu_cli.client.ZhihuClient") as client:
        result = CliRunner().invoke(cli, ["like-comment", *args])
        assert result.exit_code == 2
        client.assert_not_called()


def test_live_like_guard_blocks_other_targets_bodies_and_extra_writes(monkeypatch):
    options = {"--live-like": True, "--live-like-comment-id": "123"}
    request = SimpleNamespace(config=SimpleNamespace(getoption=options.get), node=SimpleNamespace())
    with patch("requests.sessions.Session.request", return_value=_make_response()) as send:
        _enable_comment_like_test(monkeypatch, request)
        with requests.Session() as session:
            allowed = "https://www.zhihu.com/api/v4/comments/123/like"
            invalid = [
                ("POST", "https://www.zhihu.com/api/v4/comments/999/like", {}),
                ("DELETE", "https://www.zhihu.com/api/v4/comment_v5/comment/123", {}),
                ("PUT", allowed, {}),
                ("POST", allowed, {"data": b"{}"}),
                ("POST", allowed, {"json": {}}),
                ("DELETE", allowed, {}),
            ]
            for method, url, kwargs in invalid:
                with pytest.raises(AssertionError):
                    session.request(method, url, **kwargs)
            send.assert_not_called()
            session.post(allowed)
            with pytest.raises(AssertionError):
                session.post(allowed)
            session.delete(allowed)
            with pytest.raises(AssertionError):
                session.delete(allowed)
        assert send.call_count == 2
        assert request.node.comment_like_state["requests"] == ["POST", "DELETE"]


def test_live_like_guard_needs_separate_approval(monkeypatch):
    request = SimpleNamespace(
        config=SimpleNamespace(getoption=lambda _: False), node=SimpleNamespace()
    )
    with patch("requests.sessions.Session.request") as send:
        with pytest.raises(pytest.skip.Exception, match="approval"):
            _enable_comment_like_test(monkeypatch, request)
        send.assert_not_called()
