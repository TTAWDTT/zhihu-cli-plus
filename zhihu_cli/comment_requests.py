"""Pure, credential-free plans for comment writes."""

from __future__ import annotations

from .config import ZHIHU_API_V4
from .display import strip_html

RESOURCE_TYPES = {
    "answer": "answers",
    "article": "articles",
    "pin": "pins",
    "question": "questions",
}


def numeric_id(value: str | int, label: str = "ID") -> str:
    text = str(value)
    if not text.isascii() or not text.isdecimal() or int(text) < 1:
        raise ValueError(f"{label} must be a positive numeric ID")
    return str(int(text))


def build_comment_request(
    resource_id: str | int,
    content: str,
    resource_type: str = "answer",
    reply_comment_id: str | int | None = None,
    *,
    as_html: bool = False,
) -> dict:
    if resource_type not in RESOURCE_TYPES:
        raise ValueError("Unsupported comment resource type")
    resource_id = numeric_id(resource_id, "Content ID")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Comment content cannot be empty")
    if as_html:
        html = content.strip()
        if not strip_html(html):
            raise ValueError("Comment HTML must contain text")
    else:
        # Comment text is always literal unless --html was explicitly selected.
        from html import escape

        html = "".join(f"<p>{escape(line)}</p>" for line in content.strip().splitlines())
    payload = {"content": html}
    if reply_comment_id is not None:
        payload.update(
            comment_id="",
            extra_params="",
            has_img=False,
            reply_comment_id=numeric_id(reply_comment_id, "Reply comment ID"),
            score=0,
            selected_settings=[],
            segment=None,
            sticker_type=None,
            unfriendly_check="strict",
        )
    return {
        "method": "POST",
        "url": f"{ZHIHU_API_V4}/comment_v5/{RESOURCE_TYPES[resource_type]}/{resource_id}/comment",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "reply_comment_id": payload.get("reply_comment_id"),
        "json": payload,
    }
