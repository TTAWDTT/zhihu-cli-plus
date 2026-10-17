"""Private-message identity, cursor, and one-attempt delivery contracts."""

import copy
import json
from unittest.mock import Mock, patch

import pytest
import requests
from click.testing import CliRunner

from tests.test_client import _make_response
from tests.test_live_messages import DmWriteGuard
from zhihu_cli.cli import cli
from zhihu_cli.client import ZhihuClient
from zhihu_cli.exceptions import DataFetchError, LoginError
from zhihu_cli.messaging import build_dm_reply_request
from zhihu_cli.signing import sign_request_headers

USER = "a" * 32
SELF = "b" * 32
BASE = "https://www.zhihu.com/api/v4"


@pytest.fixture()
def client():
    with ZhihuClient({"z_c0": "dummy", "_xsrf": "xsrf", "d_c0": "dc0"}) as client:
        yield client


def chat_page(message_id="42", *, next_url=None):
    return {
        "data": {
            "sender": {"id": USER, "name": "Partner"},
            "receiver": {"id": SELF, "name": "Me"},
            "messages": [
                {"id": message_id, "type": "message", "user_type": "sender", "text": "你好"}
            ],
        },
        "paging": {"is_end": not bool(next_url), "next": next_url},
        "warning": {"text": "Platform warning"},
    }


@pytest.mark.parametrize("user", [None, "", "alice", "123", "../me", "a" * 31, "A" * 32, True])
def test_reply_rejects_ambiguous_recipient_ids_without_http(user):
    with pytest.raises(ValueError, match="participant"):
        build_dm_reply_request(user, "Hello")


@pytest.mark.parametrize("text", [None, 1, "", " \n\t"])
def test_reply_rejects_empty_or_nontext_messages(text):
    with pytest.raises(ValueError, match="text"):
        build_dm_reply_request(USER, text)


def test_reply_plan_preserves_plain_text():
    text = '中文 <literal> & "quote"\nsecond line'
    assert build_dm_reply_request(USER, text) == {
        "operation": "dm-reply",
        "method": "POST",
        "url": BASE + "/chat",
        "json": {"receiver_id": USER, "content_type": 0, "text": text},
    }


def test_inbox_keeps_thread_and_participant_ids_separate(client):
    raw = {
        "data": [{"id": "999", "participant": {"id": USER}, "unread_count": 3}],
        "paging": {"is_end": True},
        "new_count": 3,
    }
    with patch.object(client, "_get", return_value=raw) as get:
        result = client.get_dm_page(limit=2, after_id="44")
    assert result == raw
    assert result["data"][0]["participant"]["id"] == USER
    assert get.call_args.args[0] == BASE + "/inbox"
    assert get.call_args.kwargs["params"] == {"limit": 2, "after_id": "44"}


def test_chat_normalization_preserves_metadata_without_mutating_raw_response(client):
    raw = chat_page(
        next_url=f"https://www.zhihu.com/messages?sender_id={USER}&after_id=42&limit=20"
    )
    original = copy.deepcopy(raw)
    with patch.object(client, "_get", return_value=raw):
        result = client.get_dm_page(USER)
    assert result["data"] == original["data"]["messages"]
    assert result["conversation"]["sender"]["id"] == USER
    assert result["warning"] == original["warning"]
    assert result["paging"]["next"].startswith(BASE + "/chat?")
    assert raw == original


def test_chat_cursor_keeps_the_exact_target_and_opaque_order(client):
    url = f"https://www.zhihu.com/messages?after_id=9999999999999999999&limit=20&sender_id={USER}"
    with patch.object(client, "_get", return_value=chat_page()) as get:
        client.get_dm_page(USER, next_url=url)
    assert get.call_args.args[0] == BASE + "/chat"
    assert get.call_args.kwargs["params"] == {
        "sender_id": USER,
        "after_id": "9999999999999999999",
        "limit": "20",
    }


@pytest.mark.parametrize(
    "url",
    [
        f"http://www.zhihu.com/messages?sender_id={USER}&after_id=1",
        f"https://evil.invalid/messages?sender_id={USER}&after_id=1",
        f"https://www.zhihu.com:444/messages?sender_id={USER}&after_id=1",
        f"https://user@www.zhihu.com/messages?sender_id={USER}&after_id=1",
        f"https://www.zhihu.com/messages?sender_id={USER}&after_id=1#fragment",
        f"{BASE}/me?sender_id={USER}&after_id=1",
        f"{BASE}/chat?sender_id={SELF}&after_id=1",
        f"{BASE}/chat?sender_id={USER}&sender_id={SELF}&after_id=1",
        f"{BASE}/chat?sender_id={USER}&after_id=-1",
        f"{BASE}/chat?sender_id={USER}&after_id=1&redirect=evil",
        f"{BASE}/chat?sender_id={USER}",
    ],
)
def test_chat_rejects_bad_cursors_before_http(client, url):
    with patch.object(client, "_get") as get:
        with pytest.raises(DataFetchError):
            client.get_dm_page(USER, next_url=url)
        get.assert_not_called()


def test_wrong_participant_response_never_shows_another_conversation(client):
    raw = chat_page()
    raw["data"]["sender"]["id"] = SELF
    with patch.object(client, "_get", return_value=raw):
        with pytest.raises(DataFetchError, match="different participant"):
            client.get_dm_page(USER)


@pytest.mark.parametrize("raw", [[], {}, {"data": []}, {"data": {"messages": None}}])
def test_malformed_history_is_not_reported_as_empty_success(client, raw):
    with patch.object(client, "_get", return_value=raw):
        with pytest.raises(DataFetchError):
            client.get_dm_page(USER)


@pytest.mark.parametrize("wrapper", [None, "data", "info"])
def test_signed_message_is_sent_once_with_exact_utf8_bytes(client, wrapper):
    text = "中文私信\n第二行"
    receipt = {"id": "42", "receiver_id": USER, "text": text}
    raw = {wrapper: receipt} if wrapper else receipt
    with patch.object(
        client._session,
        "request",
        return_value=_make_response(201, raw),
    ) as send:
        result = client.reply_dm(USER, text)
    send.assert_called_once()
    assert result == raw
    kwargs = send.call_args.kwargs
    assert json.loads(kwargs["data"]) == {"receiver_id": USER, "content_type": 0, "text": text}
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["x-xsrftoken"] == "xsrf"
    expected = sign_request_headers("/api/v4/chat", "dc0", kwargs["data"])
    assert all(kwargs["headers"][k] == v for k, v in expected.items())


@pytest.mark.parametrize("status", [400, 403, 429, 500, 302])
def test_rejected_delivery_is_never_retried(client, status):
    with patch.object(
        client._session,
        "request",
        return_value=_make_response(status, {"error": {"message": "Denied"}}),
    ) as send:
        with pytest.raises(DataFetchError, match="rejected"):
            client.reply_dm(USER, "Text")
        send.assert_called_once()


def test_expired_login_does_not_look_like_delivery(client):
    with patch.object(client._session, "request", return_value=_make_response(401)):
        with pytest.raises(LoginError):
            client.reply_dm(USER, "Text")


def test_delivery_network_error_is_unknown_and_does_not_expose_request(client):
    with patch.object(
        client._session, "request", side_effect=requests.Timeout("Cookie: synthetic-secret")
    ) as send:
        with pytest.raises(DataFetchError, match="unknown") as error:
            client.reply_dm(USER, "Text")
        assert "synthetic-secret" not in str(error.value)
        assert error.value.__suppress_context__
        send.assert_called_once()


@pytest.mark.parametrize(
    "raw",
    [
        {},
        [],
        {"success": True},
        {"id": True},
        {"id": -1},
        {"id": "42", "success": False},
        {"id": "42", "code": 400},
        {"data": {"id": "42", "code": 400}},
        {"data": {"success": False}},
        {"info": {"success": False}},
        {"info": {"id": "42", "receiver_id": SELF}},
        {"id": "42", "receiver_id": SELF},
        {"id": "42", "text": "Wrong text"},
        {"id": "42", "content_type": 1},
    ],
)
def test_ambiguous_or_contradictory_ack_is_not_success(client, raw):
    with patch.object(client._session, "request", return_value=_make_response(200, raw)) as send:
        with pytest.raises(DataFetchError):
            client.reply_dm(USER, "Text")
        send.assert_called_once()


def test_incomplete_credentials_fail_before_post():
    with ZhihuClient({"z_c0": "dummy"}) as client, patch.object(client._session, "request") as send:
        with pytest.raises(LoginError):
            client.reply_dm(USER, "Text")
        send.assert_not_called()


def test_cli_dry_run_needs_no_login_and_sends_nothing():
    with patch("zhihu_cli.commands.messages._get_client") as factory:
        result = CliRunner().invoke(cli, ["dm-reply", USER, "Test", "--dry-run", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["json"]["receiver_id"] == USER
    factory.assert_not_called()


def test_cli_file_body_and_json_receipt(tmp_path):
    file = tmp_path / "message.txt"
    file.write_text("中文\n正文", encoding="utf-8")
    client = Mock()
    client.reply_dm.return_value = {"id": "42"}
    with patch("zhihu_cli.commands.messages._get_client") as factory:
        factory.return_value.__enter__.return_value = client
        result = CliRunner().invoke(cli, ["dm-reply", USER, "--file", str(file), "--json"])
    assert result.exit_code == 0 and json.loads(result.stdout)["id"] == "42"
    client.reply_dm.assert_called_once_with(USER, "中文\n正文")


def test_cli_human_receipt_uses_the_live_info_wrapper():
    client = Mock()
    client.reply_dm.return_value = {"info": {"id": "42"}, "receiver": {"id": USER}}
    with patch("zhihu_cli.commands.messages._get_client") as factory:
        factory.return_value.__enter__.return_value = client
        result = CliRunner().invoke(cli, ["dm-reply", USER, "Test"])
    assert result.exit_code == 0 and "Message accepted. ID: 42" in result.stdout


def test_cli_never_emits_partial_json_after_page_failure(client):
    first = chat_page(next_url=f"{BASE}/chat?sender_id={USER}&after_id=42")
    with (
        patch("zhihu_cli.commands.messages._get_client") as factory,
        patch.object(client, "_get", side_effect=[first, DataFetchError("Second page failed")]),
    ):
        factory.return_value.__enter__.return_value = client
        result = CliRunner().invoke(cli, ["dm-read", USER, "--all", "--json"])
    assert result.exit_code != 0 and result.stdout == ""
    assert "Second page failed" in result.stderr


def test_cli_all_pages_keep_message_ids_with_their_text(client):
    first = chat_page(next_url=f"https://www.zhihu.com/messages?sender_id={USER}&after_id=42")
    second = chat_page("41")
    second["data"]["messages"][0]["text"] = "Older text"
    with (
        patch("zhihu_cli.commands.messages._get_client") as factory,
        patch.object(client, "_get", side_effect=[first, second]),
    ):
        factory.return_value.__enter__.return_value = client
        result = CliRunner().invoke(cli, ["dm-read", USER, "--all", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert [(m["id"], m["text"]) for m in data["data"]] == [("42", "你好"), ("41", "Older text")]
    assert data["pages_fetched"] == 2


def test_live_guard_allows_only_one_exact_approved_post():
    original = Mock(return_value=_make_response(200, {"id": "42"}))
    guard = DmWriteGuard(original, USER, "Approved text")
    body = json.dumps({"content_type": 0, "text": "Approved text", "receiver_id": USER}).encode()
    guard(None, "POST", BASE + "/chat", data=body)
    assert guard.message_id == "42" and guard.posts == 1
    with pytest.raises(AssertionError):
        guard(None, "POST", BASE + "/chat", data=body)
    original.assert_called_once()


@pytest.mark.parametrize(
    "recipient,text,url",
    [
        (SELF, "Approved text", BASE + "/chat"),
        (USER, "Other text", BASE + "/chat"),
        (USER, "Approved text", BASE + "/comments"),
    ],
)
def test_live_guard_blocks_unapproved_messages(recipient, text, url):
    original = Mock()
    guard = DmWriteGuard(original, USER, "Approved text")
    body = json.dumps({"content_type": 0, "text": text, "receiver_id": recipient}).encode()
    with pytest.raises(AssertionError):
        guard(None, "POST", url, data=body)
    original.assert_not_called()


def test_live_guard_does_not_read_other_conversations():
    original = Mock()
    guard = DmWriteGuard(original, USER, "Approved text")
    with pytest.raises(AssertionError):
        guard(None, "GET", BASE + "/chat", params={"sender_id": SELF})
    original.assert_not_called()
