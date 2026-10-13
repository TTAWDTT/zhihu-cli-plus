"""Explicit content workflows, collection management and reading history."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..action_requests import (
    CONTENT_TYPES,
    build_answer_delete_request,
    build_answer_publish_request,
    build_collection_request,
    build_draft_save_request,
    build_follow_user_request,
    build_history_request,
    build_like_request,
    build_notification_read_request,
)
from ..display import console, print_success, strip_html
from ..pagination import collect_pages, pagination_options
from ..workflow_client import action_resource_id
from .comments import _read_content
from .content import _get_client, _show_collection


def _emit(result, as_json):
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    elif isinstance(result.get("data"), list):
        _show_collection(result["data"])
    else:
        data = result.get("collection") or result.get("draft") or result
        console.print(f"ID: {result.get('id', data.get('id', ''))}", markup=False)
        if data.get("title"):
            console.print(strip_html(data["title"]), markup=False)
        content = data.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                block.get("content", "") for block in content if block.get("type") == "text"
            )
        if content:
            console.print(strip_html(content), markup=False)
        if data.get("description"):
            console.print(strip_html(data["description"]), markup=False)


def _query(method, as_json, *, renderer=_emit, **paging):
    with _get_client() as client:
        try:
            result = collect_pages(
                client,
                lambda: method(client, paging.get("offset", "0")),
                all_pages=paging.get("all_pages", False),
                max_pages=paging.get("max_pages", 100),
                next_url=paging.get("next_url"),
            )
        except Exception as error:
            raise click.ClickException(str(error)) from error
    renderer(result, as_json)


def _emit_history(result, as_json):
    if as_json:
        _emit(result, True)
        return
    for index, item in enumerate(result.get("data", []), 1):
        card = item.get("data", item)
        target = card.get("target", {})
        header = card.get("header", {})
        action = card.get("action", {})
        title = (
            header.get("title")
            or target.get("title")
            or target.get("question", {}).get("title", "")
        )
        url = action.get("url") or target.get("url", "")
        console.print(f"{index}. {strip_html(title)}", markup=False)
        if url:
            console.print(f"   URL: {url}", markup=False)
        summary = (
            card.get("content", {}).get("summary")
            if isinstance(card.get("content"), dict)
            else None
        )
        if summary:
            console.print(f"   {strip_html(summary)}", markup=False)
    if not result.get("data"):
        console.print("No browsing history")


def _write(plan_factory, dry_run, as_json, *, confirm=None, yes=False):
    try:
        plan = plan_factory()
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    if dry_run:
        click.echo(json.dumps({"dry_run": True, **plan}, ensure_ascii=False, indent=2))
        return
    if confirm and not yes:
        click.confirm(confirm, abort=True, err=True)
    with _get_client() as client:
        try:
            result = client.execute_action(plan)
        except Exception as error:
            raise click.ClickException(str(error)) from error
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_success(f"Operation accepted: {plan['operation']}")
        created_id = action_resource_id(result)
        if created_id:
            console.print(f"ID: {created_id}", markup=False)


def _read_options(function):
    function = click.option("--limit", default=20, type=click.IntRange(min=1), show_default=True)(
        function
    )
    function = click.option("--json", "as_json", is_flag=True)(function)
    return pagination_options(function)


def _write_options(function):
    function = click.option("--dry-run", is_flag=True, help="Preview offline without credentials.")(
        function
    )
    return click.option("--json", "as_json", is_flag=True)(function)


def _content_options(function):
    function = click.argument("content", required=False)(function)
    function = click.option("--file", type=click.Path(exists=True, dir_okay=False, path_type=Path))(
        function
    )
    function = click.option("--html", "as_html", is_flag=True, help="Submit explicit HTML.")(
        function
    )
    return _write_options(function)


def _detail_command(name, method):
    @click.command(name)
    @click.argument("resource_id", type=click.IntRange(min=1))
    @click.option("--json", "as_json", is_flag=True)
    def command(resource_id, as_json):
        with _get_client() as client:
            try:
                result = getattr(client, method)(str(resource_id))
                data = result.get("collection") or result
                if str(data.get("id")) != str(resource_id):
                    raise ValueError("Detail response has a missing or mismatched ID")
            except Exception as error:
                raise click.ClickException(str(error)) from error
        _emit(result, as_json)

    command.help = f"Read {name.replace('-detail', '')} detail by ID."
    return command


def _user_list_command(name, kind):
    @click.command(name)
    @click.argument("user", required=False)
    @_read_options
    def command(user, limit, as_json, **paging):
        _query(
            lambda c, offset: c.get_user_items(kind, user, offset=offset, limit=limit),
            as_json,
            **paging,
        )

    command.help = "Read this user's collection; omit USER for your own account."
    return command


article_detail = _detail_command("article-detail", "get_article")
pin_detail = _detail_command("pin-detail", "get_pin")
collection_detail = _detail_command("collection-detail", "get_collection")
user_pins = _user_list_command("user-pins", "pins")
user_questions = _user_list_command("user-questions", "questions")
user_activities = _user_list_command("user-activities", "activities")
user_collections = _user_list_command("user-collections", "collections")


@click.command("collection-items")
@click.argument("collection_id", type=click.IntRange(min=1))
@_read_options
def collection_items(collection_id, limit, as_json, **paging):
    """Read the contents of a collection, including pagination."""
    _query(
        lambda c, offset: c.get_collection_items(str(collection_id), offset=offset, limit=limit),
        as_json,
        **paging,
    )


@click.command("content-collections")
@click.argument("resource_id", type=click.IntRange(min=1))
@click.option("--type", "kind", type=click.Choice(["answer", "article"]), default="answer")
@_read_options
def content_collections(resource_id, kind, limit, as_json, **paging):
    """Find collections containing an answer or article."""
    _query(
        lambda c, offset: c.get_content_collections(
            str(resource_id), kind, offset=offset, limit=limit
        ),
        as_json,
        **paging,
    )


@click.command("follow-feed")
@_read_options
def follow_feed(limit, as_json, **paging):
    """Read activity from followed people."""
    _query(lambda c, offset: c.get_follow_feed(offset=offset, limit=limit), as_json, **paging)


@click.command()
@_read_options
def history(limit, as_json, **paging):
    """Read online browsing history without adding or clearing entries."""
    _query(
        lambda c, offset: c.get_history(offset=offset, limit=limit),
        as_json,
        renderer=_emit_history,
        **paging,
    )


@click.command()
@click.option("--type", "kind", default="answer", type=click.Choice(["answer", "article", "pin"]))
@_read_options
def drafts(kind, limit, as_json, **paging):
    """List your answer, article or pin drafts."""
    _query(lambda c, offset: c.get_drafts(kind, offset=offset, limit=limit), as_json, **paging)


@click.command("draft-versions")
@click.argument("resource_id", type=click.IntRange(min=1))
@click.option(
    "--type", "kind", default="question", type=click.Choice(["question", "answer", "article"])
)
@_read_options
def draft_versions(resource_id, kind, limit, as_json, **paging):
    """List draft versions for a question, answer or article."""
    _query(
        lambda c, offset: c.get_draft_versions(str(resource_id), kind, offset=offset, limit=limit),
        as_json,
        **paging,
    )


@click.command("draft-detail")
@click.argument("draft_id")
@click.option("--version-type", default="current")
@click.option("--json", "as_json", is_flag=True)
def draft_detail(draft_id, version_type, as_json):
    """Read one draft without publishing it."""
    with _get_client() as client:
        try:
            result = client.get_draft_detail(draft_id, version_type)
        except Exception as error:
            raise click.ClickException(str(error)) from error
    _emit(result, as_json)


@click.command("notification-list")
@click.option(
    "--category",
    default="default",
    type=click.Choice(["recent", "default", "follow", "vote_thank"]),
)
@_read_options
def notification_list(category, limit, as_json, **paging):
    """Read a notification category without marking it read."""
    _query(
        lambda c, offset: c.get_notification_category(category, offset=offset, limit=limit),
        as_json,
        **paging,
    )


@click.command("create-collection")
@click.argument("title")
@click.option("--description", default="")
@click.option("--public", is_flag=True, help="Create a public collection (default: private).")
@_write_options
def create_collection(title, description, public, dry_run, as_json):
    """Create a private collection by default."""
    _write(
        lambda: build_collection_request(
            "create", title=title, description=description, public=public
        ),
        dry_run,
        as_json,
    )


@click.command("delete-collection")
@click.argument("collection_id", type=click.IntRange(min=1))
@click.option("-y", "--yes", is_flag=True)
@_write_options
def delete_collection(collection_id, yes, dry_run, as_json):
    """Delete your own collection after explicit confirmation."""
    _write(
        lambda: build_collection_request("delete", collection_id),
        dry_run,
        as_json,
        confirm=f"Delete collection {collection_id}?",
        yes=yes,
    )


@click.command("collect")
@click.argument("collection_id", type=click.IntRange(min=1))
@click.argument("resource_id", type=click.IntRange(min=1))
@click.option("--type", "kind", default="answer", type=click.Choice(["answer", "article"]))
@click.option("--undo", is_flag=True, help="Remove this item from this collection.")
@_write_options
def collect(collection_id, resource_id, kind, undo, dry_run, as_json):
    """Add an answer/article to your collection, or remove it with --undo."""
    _write(
        lambda: build_collection_request(
            "remove" if undo else "add", collection_id, content_id=resource_id, kind=kind
        ),
        dry_run,
        as_json,
    )


def _like_command(name, kind):
    @click.command(name)
    @click.argument("resource_id", type=click.IntRange(min=1))
    @click.option("--undo", is_flag=True)
    @_write_options
    def command(resource_id, undo, dry_run, as_json):
        _write(lambda: build_like_request(resource_id, kind=kind, undo=undo), dry_run, as_json)

    command.help = f"Like this {kind}, or cancel your like with --undo."
    return command


like_article = _like_command("like-article", "article")
like_pin = _like_command("like-pin", "pin")


@click.command("follow-user")
@click.argument("user")
@click.option("--undo", is_flag=True)
@_write_options
def follow_user(user, undo, dry_run, as_json):
    """Follow a user, or unfollow with --undo."""
    _write(lambda: build_follow_user_request(user, undo=undo), dry_run, as_json)


@click.command("history-add")
@click.argument("resource_id", type=click.IntRange(min=1))
@click.option("--type", "kind", default="answer", type=click.Choice(CONTENT_TYPES))
@_write_options
def history_add(resource_id, kind, dry_run, as_json):
    """Explicitly add one content item to online browsing history."""
    _write(lambda: build_history_request("add", resource_id, kind=kind), dry_run, as_json)


@click.command("history-touch")
@click.argument("resource_id", type=click.IntRange(min=1))
@click.option("--type", "kind", default="answer", type=click.Choice(CONTENT_TYPES))
@click.option("--state", default="read", type=click.Choice(["read", "touch"]))
@_write_options
def history_touch(resource_id, kind, state, dry_run, as_json):
    """Explicitly report one read/touch event. No automatic reporting."""
    _write(
        lambda: build_history_request("touch", resource_id, kind=kind, state=state),
        dry_run,
        as_json,
    )


@click.command("history-clear")
@click.option("-y", "--yes", is_flag=True)
@_write_options
def history_clear(yes, dry_run, as_json):
    """Clear online history; this cannot restore your previous history."""
    _write(
        lambda: build_history_request("clear"),
        dry_run,
        as_json,
        confirm="Clear all online browsing history? This cannot be undone.",
        yes=yes,
    )


@click.command("notifications-read")
@click.option(
    "--category", default="default", type=click.Choice(["default", "follow", "vote_thank"])
)
@click.option("-y", "--yes", is_flag=True)
@_write_options
def notifications_read(category, yes, dry_run, as_json):
    """Mark a category of notifications read after confirmation."""
    _write(
        lambda: build_notification_read_request(category),
        dry_run,
        as_json,
        confirm=f"Mark all {category} notifications read?",
        yes=yes,
    )


@click.command("save-draft")
@click.argument("resource_id", type=click.IntRange(min=1))
@click.option("--type", "kind", default="answer", type=click.Choice(["answer", "article"]))
@click.option("--update", is_flag=True, help="Update an existing answer draft rather than create.")
@_content_options
def save_draft(resource_id, kind, update, content, file, as_html, dry_run, as_json):
    """Save an answer draft (question ID) or an article draft (article ID)."""
    _write(
        lambda: build_draft_save_request(
            resource_id, _read_content(content, file), kind=kind, update=update, as_html=as_html
        ),
        dry_run,
        as_json,
    )


def _answer_write_command(name, edit):
    @click.command(name)
    @click.argument("resource_id", type=click.IntRange(min=1))
    @_content_options
    def command(resource_id, content, file, as_html, dry_run, as_json):
        _write(
            lambda: build_answer_publish_request(
                resource_id, _read_content(content, file), edit=edit, as_html=as_html
            ),
            dry_run,
            as_json,
        )

    command.help = (
        "Edit your own answer by ID." if edit else "Publish an answer to this question ID."
    )
    return command


publish_answer = _answer_write_command("publish-answer", False)
edit_answer = _answer_write_command("edit-answer", True)


@click.command("delete-answer")
@click.argument("answer_id", type=click.IntRange(min=1))
@click.option("-y", "--yes", is_flag=True)
@_write_options
def delete_answer(answer_id, yes, dry_run, as_json):
    """Delete your own answer after confirmation."""
    _write(
        lambda: build_answer_delete_request(answer_id),
        dry_run,
        as_json,
        confirm=f"Delete answer {answer_id}?",
        yes=yes,
    )


WORKFLOW_COMMANDS = (
    article_detail,
    pin_detail,
    collection_detail,
    user_pins,
    user_questions,
    user_activities,
    user_collections,
    collection_items,
    content_collections,
    follow_feed,
    history,
    drafts,
    draft_versions,
    draft_detail,
    notification_list,
    create_collection,
    delete_collection,
    collect,
    like_article,
    like_pin,
    follow_user,
    history_add,
    history_touch,
    history_clear,
    notifications_read,
    save_draft,
    publish_answer,
    edit_answer,
    delete_answer,
)
