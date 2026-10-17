from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import click

from ..display import console, strip_html
from ..messaging import build_dm_reply_request, message_user_id
from ..pagination import collect_pages
from .comments import _read_content
from .content import _get_client


def _read(user_id, limit, after_id, as_json, paging):
    if user_id is not None:
        try:
            user_id = message_user_id(user_id)
        except ValueError as error:
            raise click.UsageError(str(error)) from error
    with _get_client() as client:
        try:
            pages = SimpleNamespace(
                get_next_page=lambda url: client.get_dm_page(user_id, next_url=url)
            )
            result = collect_pages(
                pages,
                lambda: client.get_dm_page(user_id, limit=limit, after_id=after_id),
                **paging,
            )
        except Exception as error:
            raise click.ClickException(str(error)) from error
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    for item in result["data"]:
        if user_id is None:
            person = item.get("participant", {})
            console.print(
                f"User ID: {person.get('id', '')} | {person.get('name', '')}", markup=False
            )
            console.print(
                f"Thread ID: {item.get('id', '')} | Unread: {item.get('unread_count', 0)}",
                markup=False,
            )
            console.print(strip_html(item.get("snippet", "")), markup=False)
        else:
            role = item.get("user_type", "")
            person = result.get("conversation", {}).get(role, {})
            console.print(
                f"Message ID: {item.get('id', '')} | {person.get('name', role)}"
                f" | {item.get('created_time', '')}",
                markup=False,
            )
            if item.get("is_canceled"):
                console.print("[Message recalled]", markup=False)
            elif item.get("content_type", 0) == 0:
                console.print(strip_html(item.get("text", "")), markup=False)
            else:
                console.print(f"[Media message, content_type={item.get('content_type')}]")
    if not result["data"]:
        console.print("No private messages")


def _read_options(function):
    for option in (
        click.option("--limit", default=20, type=click.IntRange(1, 100), show_default=True),
        click.option(
            "--after-id",
            type=click.IntRange(min=1),
            help="Read older messages after this boundary ID.",
        ),
        click.option("--json", "as_json", is_flag=True),
        click.option("--next-url", default=None),
        click.option("--all", "all_pages", is_flag=True),
        click.option("--max-pages", default=100, type=click.IntRange(min=1), show_default=True),
    ):
        function = option(function)
    return function


@click.command("dm-inbox")
@_read_options
def dm_inbox(limit, after_id, as_json, **paging):
    """List private conversations, participant user IDs, and unread counts."""
    _read(None, limit, after_id, as_json, paging)


@click.command("dm-read")
@click.argument("user_id")
@_read_options
def dm_read(user_id, limit, after_id, as_json, **paging):
    """Read newest-first messages. Zhihu may mark this conversation as read.

    USER_ID is the participant's user ID from dm-inbox, not its thread ID.
    """
    _read(user_id, limit, after_id, as_json, paging)


@click.command("dm-reply")
@click.argument("user_id")
@click.argument("content", required=False)
@click.option("--file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Preview without credentials or sending.")
@click.option("--json", "as_json", is_flag=True)
def dm_reply(user_id, content, file, dry_run, as_json):
    """Send one text message to this participant user ID; never retry automatically."""
    text = _read_content(content, file)
    try:
        plan = build_dm_reply_request(user_id, text)
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    if dry_run:
        click.echo(json.dumps({"dry_run": True, **plan}, ensure_ascii=False, indent=2))
        return
    with _get_client() as client:
        try:
            result = client.reply_dm(user_id, text)
        except Exception as error:
            raise click.ClickException(str(error)) from error
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        data = result.get("info", result.get("data", result))
        console.print(f"Message accepted. ID: {data['id']}", markup=False)
