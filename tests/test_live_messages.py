"""Explicit private-message tests; reports contain no received message bodies."""

import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from click.testing import CliRunner

from tests.test_live import live_client as live_client
from zhihu_cli.cli import cli
from zhihu_cli.exceptions import DataFetchError
from zhihu_cli.messaging import message_user_id

pytestmark = pytest.mark.integration


@pytest.fixture()
def dm_user(request):
    value = request.config.getoption("--live-dm-user-id")
    if not value:
        pytest.skip("An explicitly selected --live-dm-user-id is required")
    return message_user_id(value)


def test_live_dm_inbox(live_client):
    page = live_client.get_dm_page(limit=2)
    assert page["data"]
    assert all(x.get("participant", {}).get("id") and x.get("id") for x in page["data"])


def test_live_dm_inbox_pagination(live_client):
    first = live_client.get_dm_page(limit=1)
    assert first["data"] and first["paging"].get("next")
    second = live_client.get_dm_page(next_url=first["paging"]["next"])
    assert second["data"]
    assert {x["id"] for x in first["data"]}.isdisjoint(x["id"] for x in second["data"])


def test_live_dm_history_preserves_message_identity(live_client, dm_user):
    raw = live_client._get(
        "https://www.zhihu.com/api/v4/chat", params={"sender_id": dm_user, "limit": 2}
    )
    page = live_client.get_dm_page(dm_user, limit=2)

    def signatures(messages):
        return [
            (
                x.get("id"),
                hashlib.sha256(
                    json.dumps(x, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
            )
            for x in messages
        ]

    assert signatures(raw["data"]["messages"]) == signatures(page["data"])
    assert page["conversation"]["sender"]["id"] == dm_user


def test_live_dm_history_next_page(live_client, dm_user):
    first = live_client.get_dm_page(dm_user, limit=1)
    if first["paging"].get("is_end", True):
        pytest.skip("Selected conversation has no next page")
    second = live_client.get_dm_page(dm_user, next_url=first["paging"]["next"])
    assert second["conversation"]["sender"]["id"] == dm_user
    assert {x["id"] for x in first["data"]}.isdisjoint(x["id"] for x in second["data"])


def test_live_dm_json_commands(dm_user):
    for args in [["dm-inbox", "--limit", "1"], ["dm-read", dm_user, "--limit", "1"]]:
        result = CliRunner().invoke(cli, [*args, "--json"])
        assert result.exit_code == 0, "Private-message command failed; contents are not logged"
        data = json.loads(result.stdout)
        assert isinstance(data["data"], list) and isinstance(data["paging"], dict)


class DmWriteGuard:
    def __init__(self, original, user_id, text):
        self.original = original
        self.user_id = message_user_id(user_id)
        self.text = text
        self.posts = 0
        self.message_id = None
        self.diagnostic = {}

    def __call__(self, session, method, *args, **kwargs):
        url = args[0] if args else kwargs["url"]
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc == "www.zhihu.com"
        method = method.upper()
        if method == "GET":
            assert parsed.path in ("/api/v4/chat", "/api/v4/inbox", "/api/v4/me")
            if parsed.path == "/api/v4/chat":
                query = parse_qs(parsed.query)
                target = (kwargs.get("params") or {}).get("sender_id") or query.get(
                    "sender_id", [None]
                )[0]
                assert target == self.user_id
        else:
            assert method == "POST" and url == "https://www.zhihu.com/api/v4/chat"
            assert self.posts == 0
            assert json.loads(kwargs["data"]) == {
                "content_type": 0,
                "text": self.text,
                "receiver_id": self.user_id,
            }
            self.posts += 1
        try:
            response = self.original(session, method, *args, **kwargs)
        except requests.RequestException:
            raise DataFetchError(
                "Live private-message transport failed; read before retrying"
            ) from None
        if method == "POST" and response.status_code in (200, 201):
            try:
                obj = response.json()
                obj = obj.get("info", obj.get("data", obj))
                self.message_id = obj.get("id")
            except (ValueError, AttributeError):
                pass
        if method == "POST":
            self.diagnostic["status"] = response.status_code
            try:
                obj = response.json()
                self.diagnostic["keys"] = sorted(obj) if isinstance(obj, dict) else []
            except ValueError:
                self.diagnostic["json"] = False
        return response


def enable_dm_guard(monkeypatch, request):
    if not request.config.getoption("--live-dm-send"):
        pytest.skip("Explicit --live-dm-send approval is required")
    user_id = request.config.getoption("--live-dm-user-id")
    text = request.config.getoption("--live-dm-text")
    if not user_id or not text:
        pytest.fail("Exact approved user ID and text are required")
    guard = DmWriteGuard(requests.sessions.Session.request, user_id, text)
    request.node.dm_guard = guard

    def guarded(session, method, *args, **kwargs):
        return guard(session, method, *args, **kwargs)

    monkeypatch.setattr(requests.sessions.Session, "request", guarded)


@pytest.mark.integration_dm_write
def test_live_dm_reply_and_readback(live_client, dm_user, request):
    guard = request.node.dm_guard
    before = live_client.get_dm_page(dm_user)
    old_ids = {str(x["id"]) for x in before["data"]}
    assert not any(x.get("text") == guard.text for x in before["data"]), (
        "Test marker already exists"
    )
    result = CliRunner().invoke(cli, ["dm-reply", dm_user, guard.text, "--json"])
    for attempt in range(8):
        delivered = live_client.get_dm_page(dm_user)
        matches = [
            x
            for x in delivered["data"]
            if str(x.get("id")) not in old_ids
            and x.get("text") == guard.text
            and x.get("user_type") == "receiver"
        ]
        if matches:
            break
        if attempt < 7:
            time.sleep(1)
    request.node.user_properties.extend(
        [
            ("posts", str(guard.posts)),
            ("message_id", str(guard.message_id or "")),
            ("readback_matches", str(len(matches))),
            ("response_shape", json.dumps(guard.diagnostic)),
        ]
    )
    assert result.exit_code == 0, (
        "Send result unconfirmed; inspect readback metadata, never resend automatically"
    )
    assert len(matches) == 1 and str(matches[0]["id"]) == str(guard.message_id)
