"""Credential-free request plans for content workflows and reading history."""

from __future__ import annotations

import json
import re
import time
import uuid
from html import escape

from .comment_requests import numeric_id
from .config import ZHIHU_API_V4 as API
from .display import strip_html

CONTENT_TYPES = ("answer", "article", "pin", "question")


def _flags(**values):
    for name, value in values.items():
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")


def user_token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("User token must be a Zhihu URL token")
    return value


def text_html(content, *, as_html=False):
    _flags(as_html=as_html)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Content cannot be empty")
    html = (
        content.strip()
        if as_html
        else "".join(f"<p>{escape(line)}</p>" for line in content.strip().splitlines())
    )
    if not strip_html(html).strip():
        raise ValueError("HTML must contain text")
    return html


def _plan(operation, method, path, *, json_body=None, params=None, **targets):
    result = {
        "operation": operation,
        "method": method,
        "url": path if path.startswith("https://") else API + path,
        "targets": targets,
    }
    if json_body is not None:
        result["json"] = json_body
    if params is not None:
        result["params"] = params
    return result


def build_collection_request(
    action,
    collection_id=None,
    *,
    content_id=None,
    kind="answer",
    title=None,
    description="",
    public=False,
):
    _flags(public=public)
    if not isinstance(description, str):
        raise ValueError("Description must be text")
    if action == "create":
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Collection title cannot be empty")
        return _plan(
            "collection-create",
            "POST",
            "/collections",
            json_body={
                "title": title.strip(),
                "description": description,
                "is_public": bool(public),
            },
        )
    cid = numeric_id(collection_id, "Collection ID")
    if action == "delete":
        return _plan("collection-delete", "DELETE", f"/collections/{cid}", collection_id=cid)
    if action not in ("add", "remove") or kind not in ("answer", "article"):
        raise ValueError("Collection membership requires add/remove and answer/article")
    content_id = numeric_id(content_id, "Content ID")
    return _plan(
        "collection-" + action,
        "POST" if action == "add" else "DELETE",
        f"/collections/{cid}/contents",
        params={"content_id": content_id, "content_type": kind},
        collection_id=cid,
        content_id=content_id,
        content_type=kind,
    )


def build_like_request(resource_id, *, kind, undo=False):
    _flags(undo=undo)
    resource_id = numeric_id(resource_id, "Content ID")
    if kind == "article":
        return _plan(
            "like-article",
            "POST",
            f"/articles/{resource_id}/voters",
            json_body={"voting": 0 if undo else 1},
            resource_id=resource_id,
            undo=undo,
        )
    if kind == "pin":
        return _plan(
            "like-pin",
            "DELETE" if undo else "POST",
            f"/pins/{resource_id}/voters/up",
            resource_id=resource_id,
            undo=undo,
        )
    raise ValueError("Likes support article or pin")


def build_follow_user_request(token, *, undo=False):
    _flags(undo=undo)
    token = user_token(token)
    return _plan(
        "follow-user",
        "DELETE" if undo else "POST",
        f"/members/{token}/followers",
        user=token,
        undo=undo,
    )


def build_history_request(action, content_id=None, *, kind="answer", state="read"):
    if action == "clear":
        return _plan(
            "history-clear",
            "POST",
            "https://api.zhihu.com/read_history/batch_del",
            json_body={"pairs": [], "clear": True},
        )
    if kind not in CONTENT_TYPES:
        raise ValueError("History supports answer, article, pin or question")
    content_id = numeric_id(content_id, "Content ID")
    if action == "add":
        return _plan(
            "history-add",
            "POST",
            "/read_history/add",
            json_body={"content_token": content_id, "content_type": kind},
            content_id=content_id,
            content_type=kind,
        )
    if action != "touch" or state not in ("read", "touch"):
        raise ValueError("Use history add/touch, with read/touch state")
    plan = _plan(
        "history-touch",
        "POST",
        "https://www.zhihu.com/lastread/touch",
        content_id=content_id,
        content_type=kind,
        state=state,
    )
    plan["multipart"] = {"items": json.dumps([[kind, content_id, state]], separators=(",", ":"))}
    return plan


def build_notification_read_request(category):
    if category not in ("default", "follow", "vote_thank"):
        raise ValueError("Unsupported notification category")
    return _plan(
        "notifications-read",
        "POST",
        f"/notifications/v2/{category}/actions/readall",
        category=category,
    )


def build_draft_save_request(resource_id, content, *, kind="answer", update=False, as_html=False):
    _flags(update=update)
    resource_id = numeric_id(resource_id, "Content ID")
    html = text_html(content, as_html=as_html)
    if kind == "answer":
        path = f"/questions/{resource_id}/draft"
        method = "PUT" if update else "POST"
        body = {
            "content": html,
            "draft_type": "normal",
            "delta_time": len(strip_html(html)),
            "settings": {
                "reshipment_settings": "allowed",
                "comment_permission": "all",
                "table_of_contents_enabled": False,
            },
        }
    elif kind == "article":
        path = f"https://zhuanlan.zhihu.com/api/articles/{resource_id}/draft"
        method = "PATCH"
        body = {"content": html, "delta_time": len(strip_html(html)), "table_of_contents": False}
    else:
        raise ValueError("Draft saving supports answer or article")
    return _plan(
        "draft-save",
        method,
        path,
        json_body=body,
        resource_id=resource_id,
        content_type=kind,
        update=update,
    )


def build_answer_publish_request(resource_id, content, *, edit=False, as_html=False):
    _flags(edit=edit)
    resource_id = numeric_id(resource_id, "Answer ID" if edit else "Question ID")
    html = text_html(content, as_html=as_html)
    draft = {"disabled": 1, "isPublished": edit}
    extra = {"publisher": "pc"}
    if edit:
        draft["contentId"] = resource_id
    else:
        extra["question_id"] = resource_id
    settings = {
        "reshipment_settings": "allowed",
        "comment_permission": "all",
        "table_of_contents_enabled": False,
        "push_activity": True,
    }
    extra["pc_business_params"] = json.dumps(settings, separators=(",", ":"))
    payload = {
        "action": "answer",
        "data": {
            "publish": {"traceId": f"{int(time.time() * 1000)},{uuid.uuid4()}"},
            "draft": draft,
            "hybridInfo": {},
            "extra_info": extra,
            "hybrid": {"html": html, "textLength": len(strip_html(html))},
            "publishSwitch": {"draft_type": "normal"},
            "commentsPermission": {"comment_permission": "all"},
        },
    }
    return _plan(
        "answer-edit" if edit else "answer-publish",
        "POST",
        "/content/publish",
        json_body=payload,
        resource_id=resource_id,
    )


def build_answer_delete_request(answer_id):
    answer_id = numeric_id(answer_id, "Answer ID")
    return _plan("answer-delete", "DELETE", f"/answers/{answer_id}", resource_id=answer_id)
