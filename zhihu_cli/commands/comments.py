"""Comment writes, exact-target replies, and reply conversation reads."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..comment_requests import (
    RESOURCE_TYPES,
    build_comment_like_request,
    build_comment_request,
    numeric_id,
)
from ..config import ZHIHU_API_V4
from ..display import console, print_success, print_warning
from ..pagination import collect_pages, pagination_options
from .content import _get_client, _show_comments


def _read_content(content: str | None, file: Path | None) -> str:
    if content is not None and file is not None:
        raise click.UsageError("Use either a content argument or --file")
    if file is not None:
        try:
            return file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise click.ClickException("Cannot read comment file as UTF-8") from error
    if content is None:
        raise click.UsageError("Provide comment content or --file")
    return content


def _write_options(function):
    for option in (
        click.option(
            "-t",
            "--type",
            "resource_type",
            default="answer",
            type=click.Choice(list(RESOURCE_TYPES)),
            show_default=True,
        ),
        click.option(
            "-f",
            "--file",
            type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
            help="Read UTF-8 comment text.",
        ),
        click.option("--html", "as_html", is_flag=True, help="Treat content as explicit HTML."),
        click.option(
            "--dry-run", is_flag=True, help="Preview the request without logging in or sending."
        ),
        click.option("--json", "as_json", is_flag=True, help="Output JSON."),
    ):
        function = option(function)
    return function


def _submit(resource_id, content, resource_type, reply_comment_id, file, as_html, dry_run, as_json):
    content = _read_content(content, file)
    try:
        plan = build_comment_request(
            resource_id, content, resource_type, reply_comment_id, as_html=as_html
        )
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    if dry_run:
        click.echo(json.dumps({"dry_run": True, **plan}, indent=2, ensure_ascii=False))
        return
    with _get_client() as client:
        try:
            result = client.create_comment(
                plan["resource_id"],
                content,
                resource_type,
                reply_comment_id=plan["reply_comment_id"],
                as_html=as_html,
            )
        except Exception as error:
            raise click.ClickException(str(error)) from error
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print_success(f"Comment accepted — ID: {result['id']}")
    if result.get("reviewing"):
        print_warning("The server marked this comment as under review")
    if result.get("is_visible_only_to_myself"):
        print_warning("The server marked this comment visible only to you")


@click.command()
@click.argument("resource_id", type=click.IntRange(min=1))
@click.argument("content", required=False)
@click.option(
    "--reply-to",
    "reply_comment_id",
    type=click.IntRange(min=1),
    help="Reply to this exact comment ID instead of starting a root comment.",
)
@_write_options
def comment(resource_id, content, reply_comment_id, **options):
    """Post a comment on a specified answer, article, pin, or question."""
    _submit(resource_id, content, reply_comment_id=reply_comment_id, **options)


@click.command()
@click.argument("resource_id", type=click.IntRange(min=1))
@click.argument("comment_id", type=click.IntRange(min=1))
@click.argument("content", required=False)
@_write_options
def reply(resource_id, comment_id, content, **options):
    """Reply to a root or child comment on the specified content.

    RESOURCE_ID identifies the original content, not a comment. COMMENT_ID is
    the exact comment being replied to. Both IDs are required by the V5 API.
    """
    _submit(resource_id, content, reply_comment_id=comment_id, **options)


@click.command("comment-detail")
@click.argument("comment_id", type=click.IntRange(min=1))
@click.option("--json", "as_json", is_flag=True)
def comment_detail(comment_id, as_json):
    """Read a comment and its direct/root reply IDs."""
    with _get_client() as client:
        try:
            result = client.get_comment(str(comment_id))
        except Exception as error:
            raise click.ClickException(str(error)) from error
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _show_comments({"data": [result]})
        console.print(f"Root ID: {result.get('reply_root_comment_id', '')}", markup=False)
        console.print(f"Reply target: {result.get('reply_comment_id', '')}", markup=False)


@click.command()
@click.argument("comment_id", type=click.IntRange(min=1))
@click.option("-l", "--limit", default=20, type=click.IntRange(min=1), show_default=True)
@click.option("--json", "as_json", is_flag=True)
@pagination_options
def replies(comment_id, limit, as_json, **paging):
    """Read the paginated conversation beneath a ROOT comment ID."""
    with _get_client() as client:
        try:
            offset = paging.pop("offset")
            result = collect_pages(
                client,
                lambda: client.get_comment_replies(str(comment_id), offset=offset, limit=limit),
                **paging,
            )
        except Exception as error:
            raise click.ClickException(str(error)) from error
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _show_comments(result)


@click.command("delete-comment")
@click.argument("comment_id", type=click.IntRange(min=1))
@click.option("-y", "--yes", "skip_confirm", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
def delete_comment(comment_id, skip_confirm, dry_run, as_json):
    """Delete your own comment (also useful for cleaning up an approved test)."""
    comment_id = numeric_id(comment_id)
    if dry_run:
        click.echo(
            json.dumps(
                {
                    "dry_run": True,
                    "method": "DELETE",
                    "url": f"{ZHIHU_API_V4}/comment_v5/comment/{comment_id}",
                },
                indent=2,
            )
        )
        return
    if not skip_confirm:
        click.confirm(f"Delete comment {comment_id}? This cannot be undone.", abort=True, err=True)
    with _get_client() as client:
        try:
            if not client.delete_comment(comment_id):
                raise click.ClickException("Comment deletion was not confirmed")
        except Exception as error:
            raise click.ClickException(str(error)) from error
    if as_json:
        click.echo(json.dumps({"success": True, "id": comment_id}))
    else:
        print_success(f"Comment {comment_id} deleted")


@click.command("like-comment")
@click.argument("comment_id", type=click.IntRange(min=1))
@click.option("--undo", is_flag=True, help="Remove your like from this comment.")
@click.option("--dry-run", is_flag=True, help="Preview without logging in or sending.")
@click.option("--json", "as_json", is_flag=True)
def like_comment(comment_id, undo, dry_run, as_json):
    """Like a root or child comment by its exact COMMENT_ID."""
    plan = build_comment_like_request(comment_id, undo=undo)
    if dry_run:
        click.echo(json.dumps({"dry_run": True, **plan}, indent=2, ensure_ascii=False))
        return
    with _get_client() as client:
        try:
            result = client.like_comment(plan["comment_id"], undo=undo)
        except Exception as error:
            raise click.ClickException(str(error)) from error
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        action = "Removed like from" if undo else "Liked"
        print_success(f"{action} comment {plan['comment_id']}")
