"""Content browsing, comments, and collection pagination."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager

import click

from ..auth import cookie_str_to_dict, get_cookie_string
from ..display import console, format_count, print_error, print_info, strip_html
from ..pagination import collect_pages, pagination_options


@contextmanager
def _get_client():
    from ..client import ZhihuClient

    cookie = get_cookie_string()
    if not cookie:
        raise click.ClickException("Not authenticated — run zhihu login")
    with ZhihuClient(cookie_str_to_dict(cookie)) as client:
        yield client


def _fetch(
    client, method, *args, offset="0", all_pages=False, max_pages=100, next_url=None, **kwargs
):
    return collect_pages(
        client,
        lambda: method(*args, offset=offset, **kwargs),
        all_pages=all_pages,
        max_pages=max_pages,
        next_url=next_url,
    )


def _json(value):
    click.echo(json.dumps(value, indent=2, ensure_ascii=False))


def _fail(message, error):
    print_error(f"{message}: {error}")
    sys.exit(1)


def _show_comment(comment, label, indent="  ", root_id=None):
    author = comment.get("author", {})
    author = author.get("member", author).get("name", "Anonymous")
    comment_id = comment.get("id", "")
    console.print(f"{indent}{label}. {author}  ID: {comment_id}", markup=False)
    reply_target = comment.get("reply_comment_id")
    if reply_target and str(reply_target) != "0":
        root_id = comment.get("reply_root_comment_id") or root_id or ""
        console.print(f"{indent}   Root ID: {root_id}", markup=False)
        console.print(f"{indent}   Reply target: {reply_target}", markup=False)
    for line in strip_html(comment.get("content", "")).strip().splitlines():
        console.print(f"{indent}   {line}", markup=False)
    likes = comment.get("vote_count", comment.get("like_count", 0))
    console.print(f"{indent}   {format_count(likes)} likes", markup=False)
    if isinstance(comment.get("liked"), bool):
        console.print(
            f"{indent}   Liked by you: {'yes' if comment['liked'] else 'no'}", markup=False
        )
    console.print()


def _show_comments(result):
    data = result.get("data", [])
    if not data:
        print_info("No comments")
    for i, comment in enumerate(data, 1):
        _show_comment(comment, str(i))
        children = comment.get("child_comments", [])
        for j, child in enumerate(children, 1):
            _show_comment(child, f"{i}.{j}", indent="      ", root_id=comment.get("id"))
        if comment.get("child_comment_count", 0) > len(children):
            console.print(
                f"     Replies: {comment['child_comment_count']} ({len(children)} shown). "
                f"Read all: zhihu replies {comment.get('id', '')} --all",
                markup=False,
            )
            console.print()


def _target(item):
    """Unwrap feed/search entries without replacing an answer by its question."""
    if "object" in item:
        return item["object"]
    if "target" in item:
        return item["target"]
    if "question" in item and "id" not in item:
        return item["question"]
    return item


def _show_collection(data):
    if not data:
        print_info("No results")
    for i, item in enumerate(data, 1):
        obj = _target(item)
        title = (
            obj.get("title")
            or obj.get("name")
            or obj.get("question", {}).get("title")
            or obj.get("excerpt", "")
        )
        console.print(f"\n{i}. {strip_html(title)}", markup=False)
        console.print(f"   ID: {obj.get('id', '')}  Type: {obj.get('type', '')}", markup=False)
        author = obj.get("author", {}).get("name")
        if author:
            console.print(f"   Author: {author}", markup=False)
        stats = [
            f"{key}: {format_count(obj[key])}"
            for key in (
                "voteup_count",
                "comment_count",
                "answer_count",
                "follower_count",
                "visit_count",
            )
            if key in obj
        ]
        heat = item.get("detail_text") or item.get("reaction", {}).get("pv")
        if heat:
            stats.append(f"Heat: {heat}")
        if stats:
            console.print("   " + " | ".join(stats), markup=False)
        content = obj.get("content", obj.get("detail", obj.get("introduction", "")))
        if isinstance(content, list):
            content = "\n".join(
                part.get("content", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        if content:
            console.print(strip_html(content), markup=False)
        for answer in item.get("answers", {}).get("data", []):
            author = answer.get("author", {}).get("name", "Anonymous")
            body = strip_html(answer.get("content", answer.get("excerpt", "")))
            console.print(f"   {author}: {body}", markup=False)
        if "comments" in item:
            _show_comments(item["comments"])


@click.command()
@click.argument("query")
@click.option(
    "-t",
    "--type",
    "search_type",
    default="general",
    type=click.Choice(["general", "people", "topic"]),
)
@click.option("-l", "--limit", default=10, type=click.IntRange(min=1), show_default=True)
@click.option("-a", "--answers", default=3, type=click.IntRange(min=0), show_default=True)
@click.option("--json", "as_json", is_flag=True)
@pagination_options
def search(query, search_type, limit, answers, as_json, **paging):
    """Search Zhihu content."""
    with _get_client() as client:
        try:
            result = _fetch(
                client, client.search, query, search_type=search_type, limit=limit, **paging
            )
            for item in result.get("data", []):
                obj = item.get("object", item)
                obj_type = obj.get("type")
                qid = (
                    obj.get("id")
                    if obj_type == "question"
                    else obj.get("question", {}).get("id")
                    if obj_type == "answer"
                    else None
                )
                if answers and qid:
                    item["answers"] = client.get_question_answers(str(qid), limit=answers)
        except Exception as error:
            _fail("Search failed", error)
        if as_json:
            _json(result)
        elif not result.get("data"):
            print_info(f'No results for "{query}"')
        else:
            _show_collection(result["data"])


@click.command()
@click.option("-l", "--limit", default=50, type=click.IntRange(min=1), show_default=True)
@click.option("-a", "--answers", default=3, type=click.IntRange(min=0), show_default=True)
@click.option("--json", "as_json", is_flag=True)
def hot(limit, answers, as_json):
    """Show trending questions (热榜; one bounded ranking)."""
    with _get_client() as client:
        try:
            result = client.get_hot_list(limit=limit)
            for item in result.get("data", []):
                target = item.get("target", item.get("question", item))
                if answers and target.get("id"):
                    item["answers"] = client.get_question_answers(str(target["id"]), limit=answers)
        except Exception as error:
            _fail("Failed to fetch hot list", error)
        if as_json:
            _json(result)
        else:
            _show_collection(result.get("data", []))


@click.command()
@click.argument("question_id", type=int)
@click.option("--answers", "with_answers", is_flag=True, help="Include answers.")
@click.option("-l", "--limit", default=5, type=click.IntRange(min=1), show_default=True)
@click.option("--sort", "sort_by", default="default", type=click.Choice(["default", "created"]))
@click.option("--json", "as_json", is_flag=True)
@pagination_options
def question(question_id, with_answers, limit, sort_by, as_json, **paging):
    """View a question, optionally with paginated answers."""
    if not with_answers and (paging["all_pages"] or paging["next_url"] or paging["offset"] != "0"):
        raise click.UsageError("Pagination requires --answers")
    with _get_client() as client:
        try:
            result = client.get_question(question_id)
            if with_answers:
                result["answers"] = _fetch(
                    client,
                    client.get_question_answers,
                    question_id,
                    limit=limit,
                    sort_by=sort_by,
                    **paging,
                )
        except Exception as error:
            _fail("Failed to fetch question", error)
        if as_json:
            _json(result)
        else:
            _show_collection([result])


@click.command()
@click.argument("question_id", type=int)
@click.option("-l", "--limit", default=5, type=click.IntRange(min=1), show_default=True)
@click.option("--sort", "sort_by", default="default", type=click.Choice(["default", "created"]))
@click.option("--json", "as_json", is_flag=True)
@pagination_options
def answers(question_id, limit, sort_by, as_json, **paging):
    """List answers for a question."""
    with _get_client() as client:
        try:
            result = _fetch(
                client,
                client.get_question_answers,
                question_id,
                limit=limit,
                sort_by=sort_by,
                **paging,
            )
        except Exception as error:
            _fail("Failed to fetch answers", error)
        if as_json:
            _json(result)
        else:
            _show_collection(result.get("data", []))


@click.command()
@click.argument("answer_id", type=int)
@click.option("--json", "as_json", is_flag=True)
@click.option("-c", "--comments", "with_comments", is_flag=True)
@click.option(
    "-l",
    "--limit",
    default=0,
    type=click.IntRange(min=0),
    help="Comments per page; 0 fetches all in pages of 20.",
    show_default=True,
)
@pagination_options
def answer(answer_id, as_json, with_comments, limit, **paging):
    """Read an answer, optionally with paginated comments."""
    if not with_comments and (paging["all_pages"] or paging["next_url"] or paging["offset"] != "0"):
        raise click.UsageError("Pagination requires --comments")
    with _get_client() as client:
        try:
            result = client.get_answer(answer_id)
            if with_comments:
                paging["all_pages"] = paging["all_pages"] or limit == 0
                result["comments"] = _fetch(
                    client, client.get_answer_comments, str(answer_id), limit=limit or 20, **paging
                )
        except Exception as error:
            _fail("Failed to fetch answer or comments", error)
        if as_json:
            _json(result)
        else:
            author = result.get("author", {}).get("name", "Anonymous")
            console.print(f"Answer by {author}", markup=False)
            console.print(strip_html(result.get("content", "")), markup=False)
            if with_comments:
                _show_comments(result["comments"])


@click.command()
@click.argument("resource_id", type=int)
@click.option(
    "-t",
    "--type",
    "resource_type",
    default="answer",
    type=click.Choice(["answer", "article", "pin", "question"]),
    show_default=True,
)
@click.option("--order", default="normal", type=click.Choice(["normal", "reverse"]))
@click.option("-l", "--limit", default=20, type=click.IntRange(min=1), show_default=True)
@click.option("--json", "as_json", is_flag=True)
@pagination_options
def comments(resource_id, resource_type, order, limit, as_json, **paging):
    """Read comments on an answer, article, pin, or question."""
    with _get_client() as client:
        try:
            result = _fetch(
                client,
                client.get_comments,
                str(resource_id),
                resource_type=resource_type,
                order=order,
                limit=limit,
                **paging,
            )
        except Exception as error:
            _fail("Failed to fetch comments", error)
        if as_json:
            _json(result)
        else:
            _show_comments(result)


@click.command()
@click.option("-l", "--limit", default=10, type=click.IntRange(min=1), show_default=True)
@click.option("--page", "page_number", default=1, type=click.IntRange(min=1), show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--next-url", default=None)
@click.option("--all", "all_pages", is_flag=True)
@click.option("--max-pages", default=100, type=click.IntRange(min=1), show_default=True)
def feed(limit, page_number, as_json, **paging):
    """Show recommended feed (推荐)."""
    with _get_client() as client:
        try:
            result = collect_pages(
                client, lambda: client.get_feed(limit=limit, page_number=page_number), **paging
            )
        except Exception as error:
            _fail("Failed to fetch feed", error)
        if as_json:
            _json(result)
        else:
            _show_collection(result.get("data", []))


@click.command()
@click.option("-l", "--limit", default=6, type=click.IntRange(min=1), show_default=True)
@click.option(
    "-c",
    "--comment-limit",
    default=10,
    type=click.IntRange(min=0),
    show_default=True,
    help="Comments per item; 0 hides comments.",
)
@click.option("--all-comments", is_flag=True, help="Fetch all comments on each supported item.")
@click.option("--page", "page_number", default=1, type=click.IntRange(min=1), show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--next-url", default=None)
@click.option("--all", "all_pages", is_flag=True)
@click.option("--max-pages", default=100, type=click.IntRange(min=1), show_default=True)
def feeds(limit, comment_limit, all_comments, page_number, as_json, **paging):
    """Read recommended content with comments, including JSON output."""
    with _get_client() as client:
        try:
            result = collect_pages(
                client, lambda: client.get_feed(limit=limit, page_number=page_number), **paging
            )
            methods = {
                "answer": client.get_answer,
                "article": client.get_article,
                "pin": client.get_pin,
            }
            for item in result.get("data", []):
                target = item.get("target", {})
                kind, item_id = target.get("type"), target.get("id")
                if kind in methods and item_id:
                    if not target.get("content"):
                        item["target"] = {**target, **methods[kind](str(item_id))}
                    if comment_limit or all_comments:
                        item["comments"] = _fetch(
                            client,
                            client.get_comments,
                            str(item_id),
                            resource_type=kind,
                            limit=comment_limit or 20,
                            all_pages=all_comments,
                            max_pages=paging["max_pages"],
                        )
        except Exception as error:
            _fail("Failed to fetch feed content or comments", error)
        if as_json:
            _json(result)
        else:
            _show_collection(result.get("data", []))


@click.command()
@click.argument("topic_id", type=int)
@click.option(
    "--questions",
    "with_questions",
    is_flag=True,
    help="Include the topic's essence feed (which may contain answers/articles).",
)
@click.option("-l", "--limit", default=10, type=click.IntRange(min=1), show_default=True)
@click.option("--json", "as_json", is_flag=True)
@pagination_options
def topic(topic_id, with_questions, limit, as_json, **paging):
    """View topic details, optionally with its paginated essence feed."""
    if not with_questions and (
        paging["all_pages"] or paging["next_url"] or paging["offset"] != "0"
    ):
        raise click.UsageError("Pagination requires --questions")
    with _get_client() as client:
        try:
            result = client.get_topic(topic_id)
            if with_questions:
                result["questions"] = _fetch(
                    client, client.get_topic_hot_questions, topic_id, limit=limit, **paging
                )
        except Exception as error:
            _fail("Failed to fetch topic", error)
        if as_json:
            _json(result)
        else:
            _show_collection([result])
            if with_questions:
                _show_collection(result["questions"].get("data", []))
