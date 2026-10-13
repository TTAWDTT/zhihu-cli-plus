"""Opt-in GET-only tests of the installed source against real Zhihu APIs.

Run: pytest -m integration --live tests/test_live.py
Credentials come from the existing local cookie file and are never serialized.
Published resource IDs can be supplied when an account has no article or pin.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from zhihu_cli.auth import cookie_str_to_dict, get_saved_cookie_string
from zhihu_cli.cli import cli
from zhihu_cli.client import ZhihuClient
from zhihu_cli.exceptions import DataFetchError
from zhihu_cli.pagination import collect_pages

pytestmark = pytest.mark.integration
_CONTEXT = {}


@pytest.fixture()
def live_client():
    cookie = get_saved_cookie_string()
    if not cookie:
        pytest.fail("Online tests require a complete local Zhihu login; no credentials printed")
    with ZhihuClient(cookie_str_to_dict(cookie)) as client:
        yield client


@pytest.fixture()
def live_context(live_client, request):
    if _CONTEXT:
        return _CONTEXT
    profile = live_client.get_self_info()
    if not profile.get("url_token"):
        pytest.fail("The live profile response has no URL token")
    feed = live_client.get_feed(limit=6)
    search = live_client.search("Python", limit=10)
    targets = [item.get("target", {}) for item in feed.get("data", [])]
    targets.extend(item.get("object", {}) for item in search.get("data", []))
    answer = next(
        (
            item
            for item in targets
            if item.get("type") == "answer"
            and item.get("comment_count", 0) > 4
            and item.get("question", {}).get("id")
        ),
        None,
    )
    if not answer:
        pytest.fail("Live sample must have an answer with a question and at least five comments")
    article_id = request.config.getoption("--live-article-id")
    if not article_id:
        article = next((item for item in targets if item.get("type") == "article"), None)
        if not article:
            articles = live_client.get_user_articles(profile["url_token"], limit=2)
            article = next(iter(articles.get("data", [])), None)
        article_id = article.get("id") if article else None
    pin_id = request.config.getoption("--live-pin-id")
    if not pin_id:
        pin = next((item for item in targets if item.get("type") == "pin"), None)
        if not pin and profile.get("pins_count"):
            pins = live_client._get(
                f"https://www.zhihu.com/api/v4/members/{profile['url_token']}/pins",
                params={"limit": 2},
            )
            pin = next(iter(pins.get("data", [])), None)
        pin_id = pin.get("id") if pin else None
    if not article_id or not pin_id:
        pytest.fail("Provide --live-article-id / --live-pin-id for missing published fixtures")
    _CONTEXT.update(
        user=profile["url_token"],
        answer=str(answer["id"]),
        question=str(answer["question"]["id"]),
        article=str(article_id),
        pin=str(pin_id),
    )
    return _CONTEXT


def run_json(args):
    result = CliRunner().invoke(cli, [*args, "--json"])
    if result.exit_code != 0:
        pytest.fail(
            f"Live CLI {args[0]} failed with exit {result.exit_code}: {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except ValueError:
        pytest.fail(f"Live CLI {args[0]} did not produce valid JSON")


def check_collection(result):
    assert isinstance(result, dict)
    assert isinstance(result.get("data"), list)
    assert isinstance(result.get("paging", {}), dict)


def test_live_whoami():
    result = run_json(["whoami"])
    assert bool(result.get("id"))
    assert bool(result.get("url_token"))


@pytest.mark.parametrize(
    "command",
    [
        "search",
        "hot",
        "feed",
        "user-answers",
        "user-articles",
        "followers",
        "following",
        "collections",
        "notifications",
    ],
)
def test_live_collection_json(command, live_context):
    args = [command, "--limit", "2"]
    if command == "search":
        args.extend(["Python", "--answers", "0"])
    elif command == "hot":
        args.extend(["--answers", "0"])
    elif command in ("user-answers", "user-articles", "followers", "following"):
        args.append(live_context["user"])
    check_collection(run_json(args))


@pytest.mark.parametrize("kind", ["answer", "article", "pin"])
def test_live_comment_json(kind, live_context):
    result = run_json(["comments", live_context[kind], "--type", kind, "--limit", "2"])
    check_collection(result)
    if result.get("counts", {}).get("total_counts", 0) > 0:
        assert len(result["data"]) > 0, "A nonempty comment thread must not pass as an empty list"
    for comment in result["data"]:
        assert bool(comment.get("id"))
        assert "content" in comment
        assert isinstance(comment.get("author"), dict)


@pytest.mark.parametrize("order", ["normal", "reverse"])
def test_live_reported_article_root_comment_pagination(order):
    # User-reported regression: offset=0 gave data=[] despite counts=15.
    result = run_json(
        [
            "comments",
            "2093329704367428295",
            "--type",
            "article",
            "--order",
            order,
            "--limit",
            "2",
            "--all",
            "--max-pages",
            "20",
        ]
    )
    check_collection(result)
    assert len(result["data"]) > 2
    assert result["pages_fetched"] > 1
    assert result["paging"]["is_end"] is True
    ids = [str(comment["id"]) for comment in result["data"]]
    assert len(ids) == len(set(ids))


def test_live_reported_article_ids_match_root_and_reply_bodies(live_client):
    roots = collect_pages(
        live_client,
        lambda: live_client.get_comments("2093329704367428295", "article", limit=2),
        all_pages=True,
        max_pages=20,
    )["data"]
    assert roots
    checked_replies = 0
    for root in roots:
        root_id = str(root["id"])
        detail = live_client.get_comment(root_id)
        assert detail["content"] == root["content"]
        if not root.get("child_comment_count"):
            continue
        replies = collect_pages(
            live_client,
            lambda root_id=root_id: live_client.get_comment_replies(root_id, limit=2),
            all_pages=True,
            max_pages=20,
        )["data"]
        assert replies
        for reply in replies:
            detail = live_client.get_comment(str(reply["id"]))
            assert detail["content"] == reply["content"]
            assert str(reply["reply_root_comment_id"]) == root_id
            assert str(detail["reply_comment_id"]) == str(reply["reply_comment_id"])
            checked_replies += 1
    assert checked_replies > 0


@pytest.mark.parametrize("undo", [False, True])
def test_live_comment_like_is_only_previewed(undo):
    args = ["like-comment", "11595516969", "--dry-run"] + (["--undo"] if undo else [])
    result = run_json(args)
    assert result["dry_run"] is True
    assert result["comment_id"] == "11595516969"
    assert result["url"] == "https://www.zhihu.com/api/v4/comments/11595516969/like"
    assert result["method"] == ("DELETE" if undo else "POST")


@pytest.mark.parametrize("kind", ["article", "pin"])
def test_live_v5_reverse_comment_sort(kind, live_context):
    check_collection(
        run_json(
            ["comments", live_context[kind], "--type", kind, "--order", "reverse", "--limit", "2"]
        )
    )


def test_live_answer_with_comment_json(live_context):
    result = run_json(["answer", live_context["answer"], "--comments", "--limit", "2"])
    assert isinstance(result.get("content"), str)
    check_collection(result["comments"])
    assert len(result["comments"]["data"]) > 0


def test_live_question_with_answers_json(live_context):
    result = run_json(["question", live_context["question"], "--answers", "--limit", "2"])
    assert bool(result.get("title"))
    check_collection(result["answers"])
    assert len(result["answers"]["data"]) > 0


def test_live_recommended_content_and_comments_json():
    result = run_json(["feeds", "--limit", "1", "--comment-limit", "1"])
    check_collection(result)
    supported = [
        item
        for item in result["data"]
        if item.get("target", {}).get("type") in ("answer", "article", "pin")
    ]
    assert len(supported) > 0
    for item in supported:
        assert "content" in item["target"]
        check_collection(item["comments"])


def test_live_article_and_pin_detail(live_client, live_context):
    article = live_client.get_article(live_context["article"])
    pin = live_client.get_pin(live_context["pin"])
    assert "content" in article
    assert "content" in pin


def test_live_comment_cursor_resumes_second_page(live_context):
    first = run_json(["comments", live_context["answer"], "--limit", "2"])
    next_url = first.get("paging", {}).get("next")
    assert bool(next_url)
    assert first["paging"]["is_end"] is False
    second = run_json(["comments", live_context["answer"], "--next-url", next_url])
    check_collection(second)
    first_ids = {str(item["id"]) for item in first["data"]}
    second_ids = {str(item["id"]) for item in second["data"]}
    assert len(second_ids) > 0
    assert first_ids.isdisjoint(second_ids)


def test_live_feed_cursor(live_client):
    first = live_client.get_feed(limit=2)
    next_url = first.get("paging", {}).get("next")
    assert bool(next_url)
    second = live_client.get_next_page(next_url)
    check_collection(second)
    assert len(second["data"]) > 0


def test_live_all_comments_on_small_pin(live_context):
    result = run_json(
        ["comments", live_context["pin"], "--type", "pin", "--all", "--max-pages", "5"]
    )
    check_collection(result)
    assert result.get("pages_fetched", 0) >= 1
    assert result["paging"].get("is_end") is True or not result["data"]


def test_live_page_bound_does_not_emit_partial_success(live_context):
    result = CliRunner().invoke(
        cli,
        ["comments", live_context["answer"], "--limit", "1", "--all", "--max-pages", "1", "--json"],
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "max-pages" in result.stderr


def test_live_aggregation_stops_at_page_bound(live_client, live_context):
    """Exercise two real HTTP pages, then reject an incomplete aggregate."""
    with pytest.raises(DataFetchError, match="max-pages"):
        collect_pages(
            live_client,
            lambda: live_client.get_answer_comments(live_context["answer"], limit=1),
            all_pages=True,
            max_pages=2,
        )


def test_live_guard_blocks_mutation_before_network(live_client):
    with pytest.raises(AssertionError, match="only allow GET"):
        live_client._session.post("https://www.zhihu.com/api/v4/content/publish", json={})


def test_live_question_detail_preserves_full_schema(live_context):
    result = run_json(["question", live_context["question"]])
    assert str(result.get("id")) == live_context["question"]
    assert isinstance(result.get("detail"), str)
    assert isinstance(result.get("topics"), list)
    assert isinstance(result.get("answer_count"), int)
    assert isinstance(result.get("follower_count"), int)


def test_live_question_detail_on_multiple_hot_questions(live_client):
    hot = live_client.get_hot_list(limit=3)
    ids = []
    for item in hot.get("data", []):
        target = item.get("target", item.get("question", item))
        if target.get("id"):
            ids.append(str(target["id"]))
    assert len(set(ids)) >= 2
    for question_id in dict.fromkeys(ids):
        question = live_client.get_question(question_id)
        assert str(question.get("id")) == question_id
        assert bool(question.get("title"))
        assert "detail" in question


def test_live_question_text_output(live_context):
    result = CliRunner().invoke(cli, ["question", live_context["question"]])
    assert result.exit_code == 0, result.stderr
    assert f"ID: {live_context['question']}" in result.stdout


@pytest.fixture()
def live_comment_thread(live_client):
    # Stable public article with a long comment conversation; this fixture only reads.
    resource_id = "621012373"
    result = live_client.get_comments(resource_id, resource_type="article", limit=2)
    root = next(
        (
            comment
            for comment in result.get("data", [])
            if comment.get("child_comment_count", 0) > 4
        ),
        None,
    )
    if root is None:
        pytest.fail("The public comment-thread fixture has no paginated replies")
    return resource_id, str(root["id"])


def test_live_v5_comment_detail_json(live_comment_thread):
    _, root_id = live_comment_thread
    result = run_json(["comment-detail", root_id])
    assert str(result["id"]) == root_id
    assert result["resource_type"] == "article"
    assert "reply_comment_id" in result
    assert str(result["reply_root_comment_id"]) == root_id


def test_live_replies_and_cursor_json(live_comment_thread):
    _, root_id = live_comment_thread
    first = run_json(["replies", root_id, "--limit", "2"])
    check_collection(first)
    assert len(first["data"]) > 0
    next_url = first["paging"]["next"]
    second = run_json(["replies", root_id, "--next-url", next_url])
    check_collection(second)
    assert len(second["data"]) > 0
    assert {str(c["id"]) for c in first["data"]}.isdisjoint({str(c["id"]) for c in second["data"]})


def test_live_reply_to_a_child_is_only_previewed(live_client, live_comment_thread):
    resource_id, root_id = live_comment_thread
    children = live_client.get_comment_replies(root_id, limit=2)["data"]
    child_id = str(children[0]["id"])
    assert child_id != root_id
    plan = run_json(
        [
            "reply",
            resource_id,
            child_id,
            "--type",
            "article",
            "Dry-run verification only",
            "--dry-run",
        ]
    )
    assert plan["dry_run"] is True
    assert plan["json"]["reply_comment_id"] == child_id
    assert plan["reply_comment_id"] != root_id
    assert "headers" not in plan


def test_live_actual_comment_writer_is_blocked_from_sending(live_client, live_context):
    with pytest.raises(AssertionError, match="only allow GET"):
        live_client.create_comment(live_context["pin"], "Blocked test; never sent", "pin")


def test_live_question_comment_read_json(live_context):
    check_collection(
        run_json(["comments", live_context["question"], "--type", "question", "--limit", "2"])
    )
