"""Comment contract, exact-body signing, delivery uncertainty, and CLI tests."""

import json
from unittest.mock import patch

import pytest
import requests
from click.testing import CliRunner

from tests.test_cli import _make_mock_client
from tests.test_client import _make_response
from tests.test_pagination import NEXT, page
from zhihu_cli.cli import cli
from zhihu_cli.client import ZhihuClient
from zhihu_cli.comment_requests import build_comment_request
from zhihu_cli.exceptions import DataFetchError, LoginError
from zhihu_cli.signing import sign_get_headers, sign_request_headers


@pytest.fixture()
def writer():
    with ZhihuClient({"z_c0": "dummy", "_xsrf": "xsrf", "d_c0": '"test=|1"'}) as client:
        yield client


@pytest.mark.parametrize(
    "kind,segment",
    [("answer", "answers"), ("article", "articles"), ("pin", "pins"), ("question", "questions")],
)
def test_root_comment_routes_and_payload(kind, segment):
    plan = build_comment_request("123", "Hello", kind)
    assert plan["url"].endswith(f"/comment_v5/{segment}/123/comment")
    assert plan["json"] == {"content": "<p>Hello</p>"}
    assert plan["reply_comment_id"] is None


@pytest.mark.parametrize("target", ["10635778308", "2080378547047241207"])
def test_reply_keeps_the_actual_target_including_large_child_ids(target):
    plan = build_comment_request("123", "Reply", "article", target)
    assert plan["json"]["reply_comment_id"] == target
    assert plan["json"]["unfriendly_check"] == "strict"
    assert plan["json"]["has_img"] is False
    assert plan["json"]["selected_settings"] == []


def test_plain_text_is_escaped_exactly_once():
    plan = build_comment_request("1", '<b>A & B</b>\n"quoted" 🤔')
    assert (
        plan["json"]["content"] == "<p>&lt;b&gt;A &amp; B&lt;/b&gt;</p><p>&quot;quoted&quot; 🤔</p>"
    )


def test_explicit_html_is_preserved():
    body = "<p>A &amp; B <strong>bold</strong></p>"
    assert build_comment_request("1", body, as_html=True)["json"]["content"] == body


@pytest.mark.parametrize(
    "resource,content,kind,target",
    [
        ("0", "text", "answer", None),
        ("../me", "text", "answer", None),
        ("1", "", "answer", None),
        ("1", " \n ", "answer", None),
        ("1", "text", "unsupported", None),
        ("1", "text", "answer", "0"),
        ("1", "text", "answer", "bad-id"),
        ("１", "text", "answer", None),
    ],
)
def test_invalid_inputs_fail_without_network(resource, content, kind, target):
    with pytest.raises(ValueError):
        build_comment_request(resource, content, kind, target)


def test_empty_html_is_rejected():
    with pytest.raises(ValueError, match="contain text"):
        build_comment_request("1", "<p> </p>", as_html=True)


def test_unicode_post_vector_matches_independent_reference():
    body = '{"content":"<p>测试 🤔</p>"}'.encode()
    assert sign_request_headers("/api/v4/comment_v5/pins/123/comment", '"test=|1"', body)[
        "x-zse-96"
    ] == ("2.0_eqk1VKA=3=LvgdQyORd2ju1CxH6J/AKPuiYMvq1Pb9ORR30uSBA25RLGzqyS0gg/")
    assert sign_request_headers("/api/v4/comment_v5/pins/123/comment", '"test=|1"', body) != (
        sign_get_headers("/api/v4/comment_v5/pins/123/comment", '"test=|1"')
    )


def test_request_signs_the_exact_bytes_sent_and_current_xsrf(writer):
    writer._session.cookies.set("_xsrf", "new-xsrf", domain=".zhihu.com")
    with patch.object(
        writer._session, "request", return_value=_make_response(201, {"id": "456"})
    ) as send:
        result = writer.create_comment("123", "测试 🤔", "pin")
    assert result["id"] == "456"
    send.assert_called_once()
    call = send.call_args
    assert call.args == ("POST", "https://www.zhihu.com/api/v4/comment_v5/pins/123/comment")
    assert call.kwargs["data"] == '{"content":"<p>测试 🤔</p>"}'.encode()
    assert call.kwargs["headers"]["x-zse-96"] == (
        "2.0_eqk1VKA=3=LvgdQyORd2ju1CxH6J/AKPuiYMvq1Pb9ORR30uSBA25RLGzqyS0gg/"
    )
    assert call.kwargs["headers"]["Content-Type"] == "application/json"
    assert call.kwargs["headers"]["x-xsrftoken"] == "new-xsrf"
    assert call.kwargs["allow_redirects"] is False


def test_reply_to_child_preserves_target_and_server_thread_metadata(writer):
    response = {"id": "900", "reply_comment_id": "800", "reply_root_comment_id": "700"}
    with patch.object(
        writer._session, "request", return_value=_make_response(201, response)
    ) as send:
        result = writer.reply_comment("123", "800", "Child reply", "article")
    assert result == response
    body = json.loads(send.call_args.kwargs["data"])
    assert body["reply_comment_id"] == "800"
    assert "reply_root_comment_id" not in body


@pytest.mark.parametrize(
    "error", [requests.Timeout("timeout"), requests.ConnectionError("offline")]
)
def test_uncertain_delivery_is_not_retried(writer, error):
    with patch.object(writer._session, "request", side_effect=error) as send:
        with pytest.raises(DataFetchError, match="delivery is unknown"):
            writer.create_comment("123", "Text")
    assert send.call_count == 1


@pytest.mark.parametrize("status", [403, 429, 500, 502, 503, 504])
def test_rejected_writes_are_not_retried_even_for_10003(writer, status):
    with patch.object(
        writer._session, "request", return_value=_make_response(status, {"error": {"code": 10003}})
    ) as send:
        with pytest.raises(DataFetchError, match="rejected"):
            writer.create_comment("123", "Text")
    assert send.call_count == 1


def test_write_401_is_authentication_error(writer):
    with patch.object(writer._session, "request", return_value=_make_response(401)) as send:
        with pytest.raises(LoginError):
            writer.create_comment("123", "Text")
    assert send.call_count == 1


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"id": None},
        {"id": 0},
        {"id": True},
        {"id": "bad"},
        {"id": "1", "success": False},
        {"id": "1", "code": 1},
        {"id": "1", "error": {"code": 1}},
        {"id": "1", "is_delete": True},
        [],
    ],
)
def test_invalid_or_rejected_200_does_not_report_success(writer, result):
    with patch.object(writer._session, "request", return_value=_make_response(200, result)) as send:
        with pytest.raises(DataFetchError):
            writer.create_comment("123", "Text")
    assert send.call_count == 1


def test_malformed_success_json_reports_unknown_delivery(writer):
    response = _make_response(200)
    response.json.side_effect = ValueError("invalid JSON")
    with patch.object(writer._session, "request", return_value=response) as send:
        with pytest.raises(DataFetchError, match="delivery is unknown"):
            writer.create_comment("123", "Text")
    assert send.call_count == 1


@pytest.mark.parametrize("missing", ["z_c0", "_xsrf", "d_c0"])
def test_incomplete_cookies_fail_before_dispatch(missing):
    cookies = {"z_c0": "z", "_xsrf": "x", "d_c0": "d"}
    del cookies[missing]
    with ZhihuClient(cookies) as client, patch.object(client._session, "request") as send:
        with pytest.raises(LoginError, match="Complete login cookies"):
            client.create_comment("1", "Text")
        send.assert_not_called()


def test_moderation_flags_are_retained(writer):
    response = {"id": "1", "reviewing": True, "is_visible_only_to_myself": True}
    with patch.object(writer._session, "request", return_value=_make_response(201, response)):
        assert writer.create_comment("1", "Text") == response


@pytest.mark.parametrize("status,response", [(200, {"success": True}), (204, {})])
def test_cleanup_delete_is_signed_and_confirmed(writer, status, response):
    with patch.object(
        writer._session, "request", return_value=_make_response(status, response)
    ) as send:
        assert writer.delete_comment("123") is True
    assert send.call_args.args[0] == "DELETE"
    assert send.call_args.kwargs["data"] is None
    assert (
        send.call_args.kwargs["headers"]["x-zse-96"]
        == (sign_get_headers("/api/v4/comment_v5/comment/123", '"test=|1"')["x-zse-96"])
    )


def test_cleanup_needs_explicit_success(writer):
    with patch.object(writer._session, "request", return_value=_make_response(200, {})):
        with pytest.raises(DataFetchError, match="not confirmed"):
            writer.delete_comment("123")


@pytest.mark.parametrize(
    "args,reply_id",
    [
        (["comment", "123", "Text"], None),
        (["reply", "123", "456", "Text"], "456"),
        (["comment", "123", "Text", "--reply-to", "456"], "456"),
    ],
)
def test_dry_run_is_offline_and_credential_free(args, reply_id, tmp_config_dir):
    with patch("zhihu_cli.client.ZhihuClient") as client:
        result = CliRunner().invoke(cli, [*args, "--type", "pin", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    assert plan["dry_run"] is True
    assert plan["reply_comment_id"] == reply_id
    assert plan["resource_type"] == "pin"
    assert "headers" not in plan
    assert "cookies" not in plan
    client.assert_not_called()


def test_file_input_keeps_unicode_and_multiline_text(tmp_path, tmp_config_dir):
    path = tmp_path / "comment.txt"
    path.write_text("测试 🤔\nSecond line", encoding="utf-8")
    result = CliRunner().invoke(cli, ["comment", "123", "--file", str(path), "--dry-run"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["json"]["content"] == "<p>测试 🤔</p><p>Second line</p>"


@pytest.mark.parametrize(
    "args",
    [
        ["comment", "0", "Text"],
        ["reply", "1", "0", "Text"],
        ["comment", "1", ""],
        ["reply", "1", "2"],
        ["comment", "1", "--html", "<p></p>"],
    ],
)
def test_invalid_cli_input_never_constructs_client(args):
    with patch("zhihu_cli.client.ZhihuClient") as client:
        result = CliRunner().invoke(cli, [*args, "--dry-run"])
    assert result.exit_code == 2
    client.assert_not_called()


def test_file_and_content_are_mutually_exclusive(tmp_path):
    path = tmp_path / "text.txt"
    path.write_text("Text")
    result = CliRunner().invoke(cli, ["comment", "1", "Text", "--file", str(path), "--dry-run"])
    assert result.exit_code == 2


@pytest.mark.parametrize(
    "args,reply_id", [(["comment", "123", "Text"], None), (["reply", "123", "456", "Text"], "456")]
)
def test_comment_cli_json_calls_writer_once(args, reply_id, saved_cookies):
    response = {"id": "900", "content": "<p>Text</p>", "reply_comment_id": reply_id}
    client = _make_mock_client(create_comment=response)
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, [*args, "--type", "article", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == response
    client.create_comment.assert_called_once_with(
        "123", "Text", "article", reply_comment_id=reply_id, as_html=False
    )


def test_failed_cli_write_has_no_success_json(saved_cookies):
    client = _make_mock_client()
    client.create_comment.side_effect = DataFetchError("delivery is unknown")
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["comment", "123", "Text", "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "delivery is unknown" in result.stderr
    assert client.create_comment.call_count == 1


def test_reply_reads_follow_the_server_cursor(saved_cookies):
    client = _make_mock_client(
        get_comment_replies=page([1], next_url=NEXT, is_end=False), get_next_page=page([2])
    )
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["replies", "123", "--all", "--json"])
    assert result.exit_code == 0
    assert [item["id"] for item in json.loads(result.stdout)["data"]] == [1, 2]


def test_comment_detail_keeps_reply_metadata(saved_cookies):
    response = {"id": "3", "reply_comment_id": "2", "reply_root_comment_id": "1"}
    client = _make_mock_client(get_comment=response)
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["comment-detail", "3", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == response


def test_cli_cleanup_does_not_claim_success_when_rejected(saved_cookies):
    client = _make_mock_client(delete_comment=False)
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["delete-comment", "123", "--yes", "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""


def test_cli_cleanup_preview_is_offline(tmp_config_dir):
    with patch("zhihu_cli.client.ZhihuClient") as client:
        result = CliRunner().invoke(cli, ["delete-comment", "123", "--dry-run", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["method"] == "DELETE"
    client.assert_not_called()


def test_question_comment_reads_use_v5_route(writer):
    with patch.object(
        writer._session, "get", return_value=_make_response(200, {"data": []})
    ) as get:
        result = writer.get_comments("123", "question")
    assert result == {"data": []}
    assert get.call_args.args[0].endswith("/comment_v5/questions/123/root_comment")


def test_question_comment_cli_matches_writer_resource_type(saved_cookies):
    client = _make_mock_client(get_comments={"data": []})
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["comments", "123", "--type", "question", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"data": []}
    assert client.get_comments.call_args.kwargs["resource_type"] == "question"


@pytest.mark.parametrize("target", [None, "0", "777"])
def test_reply_never_claims_success_for_an_unconfirmed_or_wrong_target(writer, target):
    response = {"id": "900"}
    if target is not None:
        response["reply_comment_id"] = target
    with patch.object(
        writer._session, "request", return_value=_make_response(201, response)
    ) as send:
        with pytest.raises(DataFetchError, match="900.*reply target is unconfirmed"):
            writer.reply_comment("123", "800", "Reply")
    assert send.call_count == 1


def test_root_comment_does_not_claim_success_for_an_unexpected_reply(writer):
    with patch.object(
        writer._session,
        "request",
        return_value=_make_response(201, {"id": "900", "reply_comment_id": "800"}),
    ):
        with pytest.raises(DataFetchError, match="unexpected reply"):
            writer.create_comment("123", "Root comment")
