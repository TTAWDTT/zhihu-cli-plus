"""Content workflow APIs built on the client's bounded reads and signed writes."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import requests

from .action_requests import user_token
from .comment_requests import numeric_id
from .config import DEFAULT_TIMEOUT
from .config import ZHIHU_API_V4 as API
from .exceptions import DataFetchError, LoginError
from .signing import sign_request_headers


def _object(result, key):
    nested = result.get(key) if isinstance(result, dict) else None
    return nested if isinstance(nested, dict) else result


def _server_message(result):
    """Keep a bounded, printable server explanation without request diagnostics."""
    if not isinstance(result, dict):
        return ""
    error = result.get("error")
    value = error.get("message") if isinstance(error, dict) else None
    value = value or result.get("message") or result.get("toast_message")
    return "".join(c for c in value if c.isprintable())[:300] if isinstance(value, str) else ""


def action_object(result):
    """Unwrap documented result objects without discarding the original response."""
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    if isinstance(data.get("result"), str) and data["result"]:
        try:
            data = json.loads(data["result"])
        except ValueError as error:
            raise DataFetchError(
                "Invalid published result; operation outcome is unknown"
            ) from error
        if not isinstance(data, dict):
            raise DataFetchError("Invalid published result; operation outcome is unknown")
    for key in ("collection", "answer", "draft"):
        if isinstance(data.get(key), dict):
            return data[key]
    return data


class WorkflowClientMixin:
    def get_user_items(self, kind, token=None, *, offset=0, limit=20):
        if kind not in ("pins", "questions", "activities", "collections"):
            raise ValueError("Unsupported user collection")
        token = user_token(token or self.get_self_info().get("url_token"))
        if kind == "activities":
            url = f"https://www.zhihu.com/api/v3/moments/{token}/activities"
            params = {"limit": limit, "desktop": "true"}
            if offset not in (0, "0", ""):
                params["offset"] = offset
        else:
            segment = "favlists" if kind == "collections" else kind
            url = f"{API}/members/{token}/{segment}"
            params = {"offset": offset, "limit": limit}
        return self._get(url, params=params)

    def get_follow_feed(self, *, offset=0, limit=20):
        params = {"limit": limit, "desktop": "true"}
        if offset not in (0, "0", ""):
            params["offset"] = offset
        return self._get("https://www.zhihu.com/api/v3/moments", params=params)

    def get_collection(self, collection_id):
        cid = numeric_id(collection_id, "Collection ID")
        result = self._get(f"{API}/collections/{cid}")
        data = _object(result, "collection")
        if not isinstance(data, dict) or str(data.get("id")) != cid:
            raise DataFetchError("Collection detail has a missing or mismatched ID")
        return result

    def get_collection_items(self, collection_id, *, offset=0, limit=20):
        cid = numeric_id(collection_id, "Collection ID")
        return self._get(
            f"{API}/collections/{cid}/contents", params={"offset": offset, "limit": limit}
        )

    def get_content_collections(self, resource_id, kind="answer", *, offset=0, limit=20):
        if kind not in ("answer", "article"):
            raise ValueError("Collection lookup supports answer or article")
        rid = numeric_id(resource_id, "Content ID")
        return self._get(
            f"{API}/collections/contents/{kind}/{rid}", params={"offset": offset, "limit": limit}
        )

    def get_history(self, *, offset=0, limit=20):
        return self._get(
            f"{API}/unify-consumption/read_history", params={"offset": offset, "limit": limit}
        )

    def get_drafts(self, kind="answer", *, offset=0, limit=20):
        paths = {
            "answer": "/answer-drafts",
            "article": "/articles/my_drafts",
            "pin": "/content/drafts",
        }
        if kind not in paths:
            raise ValueError("Unsupported draft kind")
        params = {"offset": offset, "limit": limit}
        if kind == "pin":
            params["action"] = "pin"
        return self._get(API + paths[kind], params=params)

    def get_draft_versions(self, resource_id, kind="question", *, offset=0, limit=20):
        if kind not in ("question", "answer", "article"):
            raise ValueError("Unsupported draft object type")
        return self._get(
            f"{API}/draft-histories",
            params={
                "object_type": kind,
                "object_id": numeric_id(resource_id),
                "offset": offset,
                "limit": limit,
            },
        )

    def get_draft_detail(self, draft_id, version_type="current"):
        if isinstance(draft_id, bool) or not isinstance(draft_id, (str, int)):
            raise ValueError("Invalid draft ID")
        if str(draft_id).isdecimal():
            numeric_id(draft_id, "Draft ID")
        draft_id = user_token(str(draft_id))
        result = self._get(
            f"{API}/draft-history", params={"id": draft_id, "version_type": version_type}
        )
        if not isinstance(result, dict) or str(result.get("id")) != draft_id:
            raise DataFetchError("Draft detail has a missing or mismatched ID")
        return result

    def get_notification_category(self, category, *, offset=0, limit=20):
        if category not in ("default", "follow", "vote_thank", "recent"):
            raise ValueError("Unsupported notification category")
        return self._get(
            f"{API}/notifications/v2/{category}", params={"offset": offset, "limit": limit}
        )

    def _prepare_action(self, plan):
        url = urlsplit(plan["url"])
        try:
            valid_port = url.port in (None, 443)
        except ValueError:
            valid_port = False
        if (
            url.scheme != "https"
            or url.hostname not in ("www.zhihu.com", "api.zhihu.com", "zhuanlan.zhihu.com")
            or url.username
            or url.password
            or url.fragment
            or not valid_port
        ):
            raise ValueError("Writes require a trusted Zhihu HTTPS URL")
        if plan["method"] not in ("POST", "PUT", "PATCH", "DELETE"):
            raise ValueError("Unsupported write method")
        headers = {"Origin": "https://www.zhihu.com"}
        kwargs = {"params": plan.get("params"), "headers": headers}
        if "json" in plan:
            kwargs["data"] = json.dumps(
                plan["json"], ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if "multipart" in plan:
            kwargs["files"] = {k: (None, v) for k, v in plan["multipart"].items()}
        prepared = self._session.prepare_request(
            requests.Request(plan["method"], plan["url"], **kwargs)
        )
        cookies = dict(
            fragment.strip().split("=", 1)
            for fragment in prepared.headers.get("Cookie", "").split(";")
            if "=" in fragment
        )
        if not all(cookies.get(key) for key in ("z_c0", "_xsrf", "d_c0")):
            raise LoginError("Complete login cookies are required for this operation")
        headers["x-xsrftoken"] = cookies["_xsrf"]
        if prepared.headers.get("Content-Type"):
            headers["Content-Type"] = prepared.headers["Content-Type"]
        headers.update(sign_request_headers(prepared.path_url, cookies["d_c0"], prepared.body))
        return prepared, headers

    def _require_owner(self, detail, *, label):
        me = self.get_self_info()
        author = detail.get("creator") or detail.get("author") or {}
        if not me.get("id") or not author.get("id") or str(author["id"]) != str(me["id"]):
            raise DataFetchError(f"Cannot confirm ownership of this {label}")

    def execute_action(self, plan):
        """Execute one planned write, with bounded preflight and strict acknowledgement."""
        import copy

        plan = copy.deepcopy(plan)
        supported = {
            "collection-create",
            "collection-delete",
            "collection-add",
            "collection-remove",
            "like-article",
            "like-pin",
            "follow-user",
            "history-add",
            "history-touch",
            "history-clear",
            "notifications-read",
            "draft-save",
            "answer-publish",
            "answer-edit",
            "answer-delete",
        }
        if plan.get("operation") not in supported:
            raise ValueError("Unsupported operation")
        self._prepare_action(plan)  # Reject incomplete credentials before any preflight HTTP.
        operation, targets = plan["operation"], plan.get("targets", {})
        if operation.startswith("collection-") and operation != "collection-create":
            self._require_owner(
                _object(self.get_collection(targets["collection_id"]), "collection"),
                label="collection",
            )
        if operation in ("answer-edit", "answer-delete"):
            detail = self.get_answer(targets["resource_id"])
            if str(detail.get("id")) != targets["resource_id"]:
                raise DataFetchError("Ownership preflight returned a different answer")
            self._require_owner(detail, label="answer")
        if operation == "draft-save":
            kind, rid = targets["content_type"], targets["resource_id"]
            if kind == "article":
                article = self.get_article(rid)
                if str(article.get("id")) != rid:
                    raise DataFetchError("Ownership preflight returned a different article")
                self._require_owner(article, label="article")
            versions = self.get_draft_versions(
                rid, "article" if kind == "article" else "question", limit=1
            )
            data = versions.get("data")
            if not isinstance(data, list):
                raise DataFetchError("Invalid draft history response")
            if kind == "answer" and bool(data) != bool(targets["update"]):
                raise DataFetchError(
                    "Draft existence does not match --update; read draft versions first"
                )
            if data:
                old = self.get_draft_detail(
                    str(data[0]["id"]), data[0].get("version_type", "current")
                )
                from .display import strip_html

                old = _object(old, "draft")
                old_length = old.get("content_words")
                if old_length is None:
                    old_length = len(strip_html(old.get("content", "")))
                plan["json"]["delta_time"] -= int(old_length)
        prepared, headers = self._prepare_action(plan)  # Sign current cookies after preflight.
        try:
            response = self._session.request(
                plan["method"],
                prepared.url,
                data=prepared.body,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            raise DataFetchError(
                "Operation result is unknown; read back before retrying"
            ) from error
        if response.status_code == 401:
            raise LoginError("Session expired", status_code=401)
        if response.status_code not in (200, 201, 204):
            error = self._api_error(response)
            code = error.get("code")
            suffix = f", code {code}" if code is not None else ""
            reason = _server_message({"error": error})
            raise DataFetchError(
                f"Operation rejected ({response.status_code}{suffix})"
                + (f": {reason}" if reason else "")
            )
        if response.status_code == 204:
            result = {"success": True}
        else:
            try:
                result = response.json()
            except ValueError as error:
                raise DataFetchError(
                    "Invalid response; result unknown, read back before retrying"
                ) from error
        if not isinstance(result, dict):
            raise DataFetchError("Invalid response; read back before retrying")
        data = action_object(result)
        envelope = result.get("data") if isinstance(result.get("data"), dict) else {}
        for wrapper in (result, envelope, data):
            write = wrapper.get("write")
            if isinstance(write, dict) and write.get("instruction") is True:
                reason = _server_message({"message": write.get("msg")})
                raise DataFetchError(
                    "Server blocked publication" + (f": {reason}" if reason else "")
                )
        if (
            result.get("error")
            or result.get("code", 0) not in (0, "0")
            or result.get("success") is False
            or envelope.get("success") is False
            or envelope.get("error")
            or envelope.get("code", 0) not in (0, "0")
            or data.get("success") is False
            or data.get("error")
            or data.get("code", 0) not in (0, "0")
        ):
            reason = _server_message(result) or _server_message(envelope) or _server_message(data)
            raise DataFetchError(
                "Server rejected the operation" + (f": {reason}" if reason else "")
            )
        confirmed = result.get("success") is True or data.get("success") is True
        if operation in ("like-article", "like-pin", "follow-user"):
            expected = not targets["undo"]
            field = {
                "like-article": "voting",
                "like-pin": "is_liked",
                "follow-user": "is_following",
            }[operation]
            if field in data:
                if data[field] != (int(expected) if field == "voting" else expected):
                    raise DataFetchError("Unexpected operation state; read back before retrying")
                confirmed = True
        if operation == "collection-create":
            obj = _object(data, "collection")
            try:
                numeric_id(obj.get("id"), "Created collection ID")
            except ValueError as error:
                raise DataFetchError("Created collection ID missing; result unknown") from error
            confirmed = True
        if operation in ("answer-publish", "answer-edit", "draft-save"):
            obj = action_object(result)
            returned_id = obj.get("id") or obj.get("content_id")
            if returned_id:
                try:
                    if operation == "draft-save":
                        if isinstance(returned_id, bool) or not isinstance(returned_id, (str, int)):
                            raise ValueError("Invalid draft ID")
                        user_token(str(returned_id))
                        if str(returned_id).isdecimal():
                            numeric_id(returned_id, "Draft ID")
                    else:
                        numeric_id(returned_id, "Returned content ID")
                except ValueError as error:
                    raise DataFetchError(
                        "Invalid returned ID; operation result is unknown"
                    ) from error
                if operation == "answer-edit" and str(returned_id) != targets["resource_id"]:
                    raise DataFetchError("Unexpected edited answer ID; read back before retrying")
                confirmed = True
            if operation == "answer-publish" and not returned_id:
                reason = _server_message(result) or _server_message(envelope)
                raise DataFetchError(
                    "Published answer ID missing; result unknown"
                    + (f": {reason}" if reason else "")
                )
        if not confirmed:
            raise DataFetchError("Operation not confirmed; read back before retrying")
        receipt = {**result, "operation": operation, "targets": targets}
        obj = action_object(result)
        resource_id = obj.get("id") or obj.get("content_id")
        if resource_id:
            receipt["resource_id"] = str(resource_id)
        return receipt
