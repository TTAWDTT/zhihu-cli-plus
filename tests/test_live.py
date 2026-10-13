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
    for comment in result["data"]:
        assert bool(comment.get("id"))
        assert "content" in comment
        assert isinstance(comment.get("author"), dict)


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
