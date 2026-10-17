from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests

from .comment_requests import numeric_id
from .config import DEFAULT_TIMEOUT, ZHIHU_API_V4
from .exceptions import DataFetchError, LoginError
from .workflow_client import _server_message


def message_user_id(value):
    """Accept a participant's user ID, not an inbox ID or URL token."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Use the participant's 32-character user ID from dm-inbox or user --json")
    return value


def build_dm_reply_request(user_id, text):
    user_id = message_user_id(user_id)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Message text cannot be empty")
    return {
        "operation": "dm-reply",
        "method": "POST",
        "url": f"{ZHIHU_API_V4}/chat",
        "json": {"receiver_id": user_id, "content_type": 0, "text": text},
    }


def _cursor_params(url, user_id):
    parsed = urlsplit(url)
    try:
        valid_port = parsed.port in (None, 443)
    except ValueError:
        valid_port = False
    paths = ("/api/v4/chat", "/messages") if user_id else ("/api/v4/inbox",)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.zhihu.com"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not valid_port
        or parsed.path not in paths
    ):
        raise DataFetchError("Invalid private-message pagination URL")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    params = dict(pairs)
    if len(params) != len(pairs) or set(params) - {"sender_id", "after_id", "before_id", "limit"}:
        raise DataFetchError("Invalid private-message cursor parameters")
    if params.get("sender_id") != user_id:
        raise DataFetchError("Private-message cursor targets a different participant")
    if not params.get("after_id") and not params.get("before_id"):
        raise DataFetchError("Private-message cursor has no message boundary")
    for key in ("after_id", "before_id", "limit"):
        if key in params:
            try:
                numeric_id(params[key], key)
            except ValueError as error:
                raise DataFetchError("Invalid private-message cursor value") from error
    return params


class MessagingClientMixin:
    def get_dm_page(self, user_id=None, *, limit=20, after_id=None, next_url=None):
        """Read inbox or one conversation; conversation reads may mark messages read."""
        if user_id is not None:
            user_id = message_user_id(user_id)
        path = "/chat" if user_id else "/inbox"
        params = {"limit": limit}
        if user_id:
            params["sender_id"] = user_id
        if after_id is not None:
            params["after_id"] = numeric_id(after_id, "Message boundary ID")
        if next_url:
            params = _cursor_params(next_url, user_id)
        result = self._get(ZHIHU_API_V4 + path, params=params)
        if not isinstance(result, dict):
            raise DataFetchError("Invalid private-message response")
        result = dict(result)
        if user_id:
            data = result.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
                raise DataFetchError("Invalid conversation response")
            sender = data.get("sender")
            if not isinstance(sender, dict) or sender.get("id") != user_id:
                raise DataFetchError("Conversation returned a different participant")
            result["conversation"] = {k: v for k, v in data.items() if k != "messages"}
            result["data"] = data["messages"]
        elif not isinstance(result.get("data"), list):
            raise DataFetchError("Invalid inbox response")
        if not all(isinstance(item, dict) for item in result["data"]):
            raise DataFetchError("Invalid private-message entries")
        paging = result.get("paging", {})
        if not isinstance(paging, dict):
            raise DataFetchError("Invalid private-message paging metadata")
        result["paging"] = dict(paging)
        for key in ("next", "previous"):
            if paging.get(key):
                cursor = _cursor_params(paging[key], user_id)
                result["paging"][key] = ZHIHU_API_V4 + path + "?" + urlencode(cursor)
        return result

    def reply_dm(self, user_id, text):
        """Send one plain-text message and require a confirmed message ID."""
        plan = build_dm_reply_request(user_id, text)
        prepared, headers = self._prepare_action(plan)
        try:
            response = self._session.request(
                "POST",
                prepared.url,
                data=prepared.body,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise DataFetchError(
                "Message result unknown; read the conversation before retrying"
            ) from None
        if response.status_code == 401:
            raise LoginError("Session expired", status_code=401)
        try:
            result = response.json()
        except ValueError:
            raise DataFetchError("Invalid message acknowledgement; read before retrying") from None
        if not isinstance(result, dict):
            raise DataFetchError("Invalid message acknowledgement; read before retrying")
        if (
            response.status_code not in (200, 201)
            or result.get("error")
            or result.get("success") is False
            or result.get("code", 0) not in (0, "0")
        ):
            reason = _server_message(result)
            raise DataFetchError(
                f"Message rejected ({response.status_code})" + (f": {reason}" if reason else "")
            )
        data = result.get("info", result.get("data", result))
        if (
            not isinstance(data, dict)
            or data.get("error")
            or data.get("success") is False
            or data.get("code", 0) not in (0, "0")
        ):
            raise DataFetchError("Message was not confirmed; read before retrying")
        try:
            numeric_id(data.get("id"), "Message ID")
        except ValueError:
            raise DataFetchError("Message ID missing or invalid; read before retrying") from None
        if data.get("receiver_id", user_id) != user_id:
            raise DataFetchError("Message acknowledgement targets a different recipient")
        if data.get("content_type", 0) != 0:
            raise DataFetchError("Message acknowledgement has a different content type")
        if "text" in data and data["text"] != text:
            raise DataFetchError("Message acknowledgement has different text; read before retrying")
        return result
