"""Observable workflow contracts, exact writes, pagination, and failure boundaries."""

import copy
import json
from unittest.mock import patch

import pytest
import requests
from click.testing import CliRunner

from tests.test_cli import _make_mock_client
from tests.test_client import _make_response
from tests.test_pagination import NEXT, page
from zhihu_cli.action_requests import (
    build_answer_publish_request,
    build_collection_request,
    build_draft_save_request,
    build_history_request,
    build_like_request,
)
from zhihu_cli.cli import cli
from zhihu_cli.client import ZhihuClient
from zhihu_cli.exceptions import DataFetchError, LoginError
from zhihu_cli.signing import sign_request_headers


@pytest.fixture()
def client():
    with ZhihuClient({"z_c0": "dummy", "_xsrf": "xsrf", "d_c0": '"test=|1"'}) as c:
        yield c


@pytest.mark.parametrize(
    "kind,segment", [("pins", "pins"), ("questions", "questions"), ("collections", "favlists")]
)
def test_user_collections_keep_token_and_numeric_offset(client, kind, segment):
    with patch.object(client, "_get", return_value=page([])) as get:
        client.get_user_items(kind, "alice-123", offset="cursor+value", limit=2)
    assert get.call_args.args[0].endswith(f"/members/alice-123/{segment}")
    assert get.call_args.kwargs["params"]["offset"] == "cursor+value"


def test_own_user_collection_resolves_account_without_changing_it(client):
    with (
        patch.object(client, "get_self_info", return_value={"url_token": "me"}),
        patch.object(client, "_get", return_value=page([])) as get,
    ):
        client.get_user_items("pins")
    assert get.call_args.args[0].endswith("/members/me/pins")


@pytest.mark.parametrize("kind", ["activities", "feed"])
@pytest.mark.parametrize("offset", [0, "0", "", "opaque+cursor"])
def test_moment_first_page_omits_offset_but_continuation_is_unchanged(client, kind, offset):
    with patch.object(client, "_get", return_value=page([])) as get:
        if kind == "feed":
            client.get_follow_feed(offset=offset)
        else:
            client.get_user_items(kind, "me", offset=offset)
    params = get.call_args.kwargs["params"]
    if offset == "opaque+cursor":
        assert params["offset"] == offset
    else:
        assert "offset" not in params


@pytest.mark.parametrize("token", ["../me", "https://bad.invalid", "me?x=1", "me/other", "", None])
def test_bad_user_tokens_never_send_http(client, token):
    with (
        patch.object(client, "_get") as get,
        patch.object(client, "get_self_info", return_value={}),
    ):
        with pytest.raises(ValueError):
            client.get_user_items("pins", token)
        get.assert_not_called()


@pytest.mark.parametrize(
    "kind,path",
    [("answer", "/answer-drafts"), ("article", "/articles/my_drafts"), ("pin", "/content/drafts")],
)
def test_draft_lists_keep_kind_and_cursor(client, kind, path):
    with patch.object(client, "_get", return_value=page([])) as get:
        client.get_drafts(kind, offset="abc+cursor", limit=3)
    assert get.call_args.args[0].endswith(path)
    assert get.call_args.kwargs["params"]["offset"] == "abc+cursor"
    if kind == "pin":
        assert get.call_args.kwargs["params"]["action"] == "pin"


@pytest.mark.parametrize("response", [{}, {"id": "999"}, {"collection": {"id": "999"}}])
def test_collection_detail_rejects_wrong_identity(client, response):
    with patch.object(client, "_get", return_value=response):
        with pytest.raises(DataFetchError, match="mismatched ID"):
            client.get_collection("123")


def test_collection_detail_preserves_wrapped_api_response(client):
    result = {"collection": {"id": "123", "title": "Collection"}, "meta": {"total": 2}}
    with patch.object(client, "_get", return_value=result):
        assert client.get_collection("123") is result


@pytest.mark.parametrize("response", [{}, {"id": "other"}, []])
def test_draft_detail_never_uses_the_wrong_draft(client, response):
    with patch.object(client, "_get", return_value=response):
        with pytest.raises(DataFetchError, match="mismatched ID"):
            client.get_draft_detail("2093329719202788253")


@pytest.mark.parametrize("draft_id", [True, False, None, "../draft", 0])
def test_invalid_draft_id_never_fetches(client, draft_id):
    with patch.object(client, "_get") as get:
        with pytest.raises(ValueError):
            client.get_draft_detail(draft_id)
        get.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "https://api.zhihu.com/unify-consumption/read_history?offset=a%2Bb",
        "https://api.zhihu.com/api/v4/unify-consumption/read_history?offset=20",
    ],
)
def test_history_cursor_accepts_only_its_expected_api_routes(client, url):
    with patch.object(client, "_get", return_value=page([])) as get:
        client.get_next_page(url)
    get.assert_called_once_with(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.zhihu.com/me",
        "https://api.zhihu.com/read_history/batch_del",
        "https://api.zhihu.com/unify-consumption/read_history/extra",
        "https://evil.invalid/unify-consumption/read_history",
        "https://user@api.zhihu.com/unify-consumption/read_history",
    ],
)
def test_new_history_cursor_allowlist_does_not_expand_to_other_apis(client, url):
    with patch.object(client, "_get") as get:
        with pytest.raises(DataFetchError):
            client.get_next_page(url)
        get.assert_not_called()


def test_legacy_member_cursor_is_upgraded_without_touching_its_opaque_query(client):
    with patch.object(client, "_get", return_value=page([])) as get:
        client.get_next_page("http://www.zhihu.com/api/v4/members/alice/pins?offset=a%2Bb&limit=1")
    get.assert_called_once_with(
        "https://www.zhihu.com/api/v4/members/alice/pins?offset=a%2Bb&limit=1"
    )


READ_CASES = [
    (["user-pins", "alice"], "get_user_items"),
    (["user-questions", "alice"], "get_user_items"),
    (["user-activities", "alice"], "get_user_items"),
    (["user-collections", "alice"], "get_user_items"),
    (["collection-items", "123"], "get_collection_items"),
    (["content-collections", "123"], "get_content_collections"),
    (["follow-feed"], "get_follow_feed"),
    (["history"], "get_history"),
    (["drafts"], "get_drafts"),
    (["draft-versions", "123"], "get_draft_versions"),
    (["notification-list"], "get_notification_category"),
]


@pytest.mark.parametrize("args,method", READ_CASES)
def test_all_new_collection_commands_follow_the_actual_next_url(args, method, saved_cookies):
    mock = _make_mock_client(
        **{method: page([1], next_url=NEXT, is_end=False), "get_next_page": page([2])}
    )
    with patch("zhihu_cli.client.ZhihuClient", return_value=mock):
        result = CliRunner().invoke(cli, [*args, "--all", "--json"])
    assert result.exit_code == 0, result.output
    assert [x["id"] for x in json.loads(result.stdout)["data"]] == [1, 2]
    mock.get_next_page.assert_called_once_with(NEXT)


@pytest.mark.parametrize("args,method", READ_CASES)
def test_new_queries_fail_without_partial_json(args, method, saved_cookies):
    mock = _make_mock_client(**{method: page([1], next_url=NEXT, is_end=False)})
    mock.get_next_page.side_effect = DataFetchError("forbidden")
    with patch("zhihu_cli.client.ZhihuClient", return_value=mock):
        result = CliRunner().invoke(cli, [*args, "--all", "--json"])
    assert result.exit_code == 1 and result.stdout == ""
    assert "forbidden" in result.stderr


def test_history_text_uses_real_title_and_url_without_confusing_record_and_content_ids(
    saved_cookies,
):
    raw = {
        "data": [
            {
                "id": "record-123",
                "data": {
                    "header": {"title": "History title"},
                    "action": {"url": "https://www.zhihu.com/question/1/answer/456"},
                    "content": {"summary": "History summary"},
                },
            }
        ],
        "paging": {"is_end": True},
    }
    mock = _make_mock_client(get_history=raw)
    with patch("zhihu_cli.client.ZhihuClient", return_value=mock):
        text = CliRunner().invoke(cli, ["history"])
        structured = CliRunner().invoke(cli, ["history", "--json"])
    assert text.exit_code == structured.exit_code == 0
    assert "History title" in text.stdout and "History summary" in text.stdout
    assert "https://www.zhihu.com/question/1/answer/456" in text.stdout
    assert json.loads(structured.stdout) == raw
    assert "ID: record-123" not in text.stdout


WRITE_CASES = [
    ["create-collection", "测试 🤔"],
    ["delete-collection", "123"],
    ["collect", "123", "456"],
    ["collect", "123", "456", "--undo"],
    ["like-article", "123"],
    ["like-article", "123", "--undo"],
    ["like-pin", "123"],
    ["like-pin", "123", "--undo"],
    ["follow-user", "alice"],
    ["follow-user", "alice", "--undo"],
    ["history-add", "123"],
    ["history-touch", "123"],
    ["history-clear"],
    ["notifications-read"],
    ["save-draft", "123", "草稿 🤔"],
    ["save-draft", "123", "草稿", "--update"],
    ["publish-answer", "123", "回答 & <b>literal</b> 🤔"],
    ["edit-answer", "123", "修改后的正文"],
    ["delete-answer", "123"],
]


@pytest.mark.parametrize("args", WRITE_CASES)
def test_every_write_preview_is_offline_and_never_requires_confirmation(args, tmp_config_dir):
    with patch("zhihu_cli.client.ZhihuClient") as client:
        result = CliRunner().invoke(cli, [*args, "--dry-run", "--json"])
        client.assert_not_called()
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    assert plan["dry_run"] is True and plan["method"] in ("POST", "DELETE", "PUT", "PATCH")
    assert "Cookie" not in result.stdout and "xsrf" not in result.stdout


@pytest.mark.parametrize("args", WRITE_CASES)
def test_write_failure_never_appears_as_success(args, saved_cookies):
    mock = _make_mock_client()
    mock.execute_action.side_effect = DataFetchError("not confirmed")
    with patch("zhihu_cli.client.ZhihuClient", return_value=mock):
        result = CliRunner().invoke(cli, [*args, "--json"], input="y\n")
    assert result.exit_code == 1 and not result.stdout
    assert "not confirmed" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["history-clear"],
        ["notifications-read"],
        ["delete-collection", "123"],
        ["delete-answer", "123"],
    ],
)
def test_declined_destructive_confirmation_never_logs_in_or_writes(args, tmp_config_dir):
    with patch("zhihu_cli.client.ZhihuClient") as client:
        result = CliRunner().invoke(cli, args, input="n\n")
    assert result.exit_code == 1
    client.assert_not_called()


def test_plain_text_and_explicit_html_are_distinct_publication_contracts():
    plan = build_answer_publish_request("2080378547047241207", "<b>A & B</b>\n🤔")
    assert plan["targets"]["resource_id"] == "2080378547047241207"
    body = plan["json"]["data"]
    assert body["extra_info"]["question_id"] == "2080378547047241207"
    assert body["hybrid"]["html"] == "<p>&lt;b&gt;A &amp; B&lt;/b&gt;</p><p>🤔</p>"
    explicit = build_answer_publish_request("123", "<p>A &amp; B</p>", as_html=True)
    assert explicit["json"]["data"]["hybrid"]["html"] == "<p>A &amp; B</p>"
    edited = build_answer_publish_request("456", "New", edit=True)
    assert edited["json"]["data"]["draft"]["contentId"] == "456"
    assert "question_id" not in edited["json"]["data"]["extra_info"]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: build_collection_request("create", title=""),
        lambda: build_collection_request("add", "123", content_id="0"),
        lambda: build_collection_request("create", title="Private", public="false"),
        lambda: build_collection_request("create", title="Valid", description={}),
        lambda: build_like_request("123", kind="pin", undo="false"),
        lambda: build_history_request("add", "123", kind="zvideo"),
        lambda: build_draft_save_request("123", "", kind="answer"),
        lambda: build_draft_save_request("123", "Text", update="false"),
        lambda: build_answer_publish_request("123", "Text", edit="false"),
        lambda: build_answer_publish_request("123", "<p> </p>", as_html=True),
    ],
)
def test_invalid_inputs_fail_before_any_network(factory):
    with pytest.raises(ValueError):
        factory()


@pytest.mark.parametrize(
    "plan",
    [
        build_history_request("add", "123", kind="article"),
        build_history_request("touch", "123"),
        build_like_request("123", kind="article"),
    ],
)
def test_exact_body_query_cookie_and_multipart_bytes_are_signed(client, plan):
    result = {"success": True}
    client._session.cookies.set("_xsrf", "fresh", domain=".zhihu.com")
    with patch.object(client._session, "request", return_value=_make_response(200, result)) as send:
        client.execute_action(plan)
    send.assert_called_once()
    call = send.call_args
    path = call.args[1].replace("https://www.zhihu.com", "")
    assert (
        call.kwargs["headers"]["x-zse-96"]
        == sign_request_headers(path, '"test=|1"', call.kwargs["data"])["x-zse-96"]
    )
    assert call.kwargs["headers"]["x-xsrftoken"] == "fresh"
    assert call.kwargs["allow_redirects"] is False
    if "multipart" in plan:
        assert b'name="items"' in call.kwargs["data"]
        assert plan["multipart"]["items"].encode() in call.kwargs["data"]
        assert "boundary=" in call.kwargs["headers"]["Content-Type"]


@pytest.mark.parametrize("missing", ["z_c0", "_xsrf", "d_c0"])
def test_incomplete_credentials_fail_before_preflight(missing):
    cookies = {"z_c0": "z", "_xsrf": "x", "d_c0": "d"}
    del cookies[missing]
    with (
        ZhihuClient(cookies) as client,
        patch.object(client, "get_collection") as get,
        patch.object(client._session, "request") as send,
    ):
        with pytest.raises(LoginError):
            client.execute_action(build_collection_request("delete", "123"))
        get.assert_not_called()
        send.assert_not_called()


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 502, 503, 504])
def test_rejected_mutations_are_never_retried(client, status):
    with patch.object(client._session, "request", return_value=_make_response(status)) as send:
        with pytest.raises((LoginError, DataFetchError)):
            client.execute_action(build_history_request("add", "123"))
        send.assert_called_once()


@pytest.mark.parametrize(
    "error", [requests.Timeout("timeout"), requests.ConnectionError("offline")]
)
def test_uncertain_delivery_is_not_repeated(client, error):
    with patch.object(client._session, "request", side_effect=error) as send:
        with pytest.raises(DataFetchError, match="unknown"):
            client.execute_action(build_history_request("add", "123"))
        send.assert_called_once()


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"success": False},
        {"success": "true"},
        {"success": True, "code": 10003},
        {"data": {"success": False}},
        {"success": True, "data": {"code": 10003}},
        {"code": 0, "data": {"result": '{"success": true, "code": 10003}'}},
        [],
    ],
)
def test_no_false_success_for_missing_or_rejected_acknowledgement(client, result):
    with patch.object(client._session, "request", return_value=_make_response(200, result)):
        with pytest.raises(DataFetchError):
            client.execute_action(build_history_request("add", "123"))


@pytest.mark.parametrize(
    "kind,result",
    [("article", {"success": True, "voting": 0}), ("pin", {"success": True, "is_liked": False})],
)
def test_contradictory_like_state_fails_even_if_success_is_true(client, kind, result):
    with patch.object(client._session, "request", return_value=_make_response(200, result)):
        with pytest.raises(DataFetchError, match="Unexpected operation state"):
            client.execute_action(build_like_request("123", kind=kind))


@pytest.mark.parametrize("action", ["add", "remove", "delete"])
def test_collection_mutations_require_confirmed_ownership(client, action):
    plan = build_collection_request(action, "123", content_id="456")
    with (
        patch.object(
            client, "get_collection", return_value={"id": "123", "creator": {"id": "other"}}
        ),
        patch.object(client, "get_self_info", return_value={"id": "me"}),
        patch.object(client._session, "request") as send,
    ):
        with pytest.raises(DataFetchError, match="ownership"):
            client.execute_action(plan)
        send.assert_not_called()


@pytest.mark.parametrize("missing_id", [None, "", 0])
def test_missing_account_identity_cannot_confirm_resource_ownership(client, missing_id):
    with (
        patch.object(client, "get_self_info", return_value={"id": missing_id}),
        patch.object(client._session, "request") as send,
    ):
        with pytest.raises(DataFetchError, match="ownership"):
            client._require_owner({"author": {"id": str(missing_id)}}, label="answer")
        send.assert_not_called()


def test_accepted_collection_membership_keeps_exact_target_and_encoded_query(client):
    plan = build_collection_request(
        "add", "2080378547047241207", content_id="2093329704367428295", kind="article"
    )
    with (
        patch.object(
            client,
            "get_collection",
            return_value={"id": plan["targets"]["collection_id"], "creator": {"id": "me"}},
        ),
        patch.object(client, "get_self_info", return_value={"id": "me"}),
        patch.object(
            client._session, "request", return_value=_make_response(200, {"success": True})
        ) as send,
    ):
        result = client.execute_action(plan)
    assert result["targets"] == plan["targets"]
    assert "content_id=2093329704367428295" in send.call_args.args[1]
    assert "content_type=article" in send.call_args.args[1]


@pytest.mark.parametrize(
    "detail",
    [
        {"id": "123", "author": {"id": "other"}},
        {"id": "999", "author": {"id": "me"}},
        {"id": "123"},
    ],
)
def test_editing_cannot_use_another_author_or_a_mismatched_preflight(client, detail):
    with (
        patch.object(client, "get_answer", return_value=detail),
        patch.object(client, "get_self_info", return_value={"id": "me"}),
        patch.object(client._session, "request") as send,
    ):
        with pytest.raises(DataFetchError):
            client.execute_action(build_answer_publish_request("123", "Text", edit=True))
        send.assert_not_called()


def test_editing_rejects_unexpected_returned_answer_id(client):
    with (
        patch.object(client, "get_answer", return_value={"id": "123", "author": {"id": "me"}}),
        patch.object(client, "get_self_info", return_value={"id": "me"}),
        patch.object(client._session, "request", return_value=_make_response(200, {"id": "999"})),
    ):
        with pytest.raises(DataFetchError, match="Unexpected edited answer ID"):
            client.execute_action(build_answer_publish_request("123", "Text", edit=True))


def test_publishing_decodes_the_actual_nested_result_string_and_keeps_raw_fields(client):
    raw = {
        "code": 0,
        "data": {"result": json.dumps({"id": "2093329704367428295", "reviewing": True})},
    }
    with patch.object(client._session, "request", return_value=_make_response(200, raw)):
        receipt = client.execute_action(build_answer_publish_request("123", "Text"))
    assert receipt["resource_id"] == "2093329704367428295"
    assert receipt["data"] == raw["data"]


@pytest.mark.parametrize("value", ["invalid JSON", "[]", '{"success":false}', '{"id":true}'])
def test_invalid_embedded_publish_result_is_never_reported_as_success(client, value):
    with patch.object(
        client._session,
        "request",
        return_value=_make_response(200, {"code": 0, "data": {"result": value}}),
    ):
        with pytest.raises(DataFetchError):
            client.execute_action(build_answer_publish_request("123", "Text"))


def test_draft_update_resolves_previous_length_without_mutating_preview(client):
    plan = build_draft_save_request("123", "New body", update=True)
    original = copy.deepcopy(plan)
    with (
        patch.object(client, "get_draft_versions", return_value={"data": [{"id": "draft1"}]}),
        patch.object(
            client, "get_draft_detail", return_value={"id": "draft1", "draft": {"content_words": 3}}
        ),
        patch.object(
            client._session, "request", return_value=_make_response(200, {"success": True})
        ) as send,
    ):
        client.execute_action(plan)
    assert json.loads(send.call_args.kwargs["data"])["delta_time"] == 5
    assert plan == original


@pytest.mark.parametrize("update,data", [(False, [{"id": "existing"}]), (True, [])])
def test_answer_draft_mode_must_match_existing_state(client, update, data):
    with (
        patch.object(client, "get_draft_versions", return_value={"data": data}),
        patch.object(client._session, "request") as send,
    ):
        with pytest.raises(DataFetchError, match="does not match --update"):
            client.execute_action(build_draft_save_request("123", "Text", update=update))
        send.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://www.zhihu.com/api/v4/me",
        "https://evil.invalid/api/v4/me",
        "https://user@www.zhihu.com/api/v4/me",
        "https://www.zhihu.com:444/api/v4/me",
        "https://www.zhihu.com/api/v4/me#x",
    ],
)
def test_untrusted_write_url_never_receives_credentials(client, url):
    plan = build_history_request("add", "123")
    plan["url"] = url
    with patch.object(client._session, "request") as send:
        with pytest.raises(ValueError, match="trusted"):
            client.execute_action(plan)
        send.assert_not_called()
