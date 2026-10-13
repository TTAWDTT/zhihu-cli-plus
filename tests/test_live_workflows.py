"""GET-only workflow verification against the current account and owned fixtures."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from tests.test_live import check_collection, run_json
from tests.test_live import live_client as live_client
from zhihu_cli.cli import cli

pytestmark = pytest.mark.integration
_CONTEXT = {}


@pytest.fixture()
def workflow_context(live_client):
    if _CONTEXT:
        return _CONTEXT
    me = live_client.get_self_info()
    collections = live_client.get_user_items("collections", me["url_token"], limit=2)["data"]
    if not collections:
        pytest.fail("Workflow verification requires an existing readable collection")
    versions = live_client.get_draft_versions("2093329704367428295", "article", limit=2)["data"]
    if not versions:
        pytest.fail("Workflow verification requires an existing article draft version")
    _CONTEXT.update(
        {
            "user": me["url_token"],
            "collection": str(collections[0]["id"]),
            "draft": str(versions[0]["id"]),
            "version_type": versions[0].get("version_type", "current"),
        }
    )
    return _CONTEXT


@pytest.mark.parametrize(
    "command", ["user-pins", "user-questions", "user-activities", "user-collections"]
)
def test_live_user_content_collections(command, workflow_context):
    result = run_json([command, workflow_context["user"], "--limit", "2"])
    check_collection(result)
    assert result["data"], "Owned fixtures must not pass as false empty results"


@pytest.mark.parametrize(
    "command,resource",
    [("article-detail", "2093329704367428295"), ("pin-detail", "2080378547047241207")],
)
def test_live_independent_content_details(command, resource):
    data = run_json([command, resource])
    assert str(data["id"]) == resource and "content" in data


def test_live_collection_detail_and_items(workflow_context):
    collection_id = workflow_context["collection"]
    detail = run_json(["collection-detail", collection_id])
    obj = detail.get("collection", detail)
    assert str(obj["id"]) == collection_id
    items = run_json(["collection-items", collection_id, "--limit", "2"])
    check_collection(items)
    assert items["data"]


def test_live_content_collection_lookup():
    check_collection(
        run_json(
            ["content-collections", "2093329704367428295", "--type", "article", "--limit", "2"]
        )
    )


@pytest.mark.parametrize("command", ["follow-feed", "history"])
def test_live_history_and_follow_feed_are_nonempty(command):
    result = run_json([command, "--limit", "2"])
    check_collection(result)
    assert result["data"]


@pytest.mark.parametrize("kind", ["answer", "article", "pin"])
def test_live_draft_lists(kind):
    result = run_json(["drafts", "--type", kind, "--limit", "2"])
    check_collection(result)
    if kind == "article":
        assert result["data"]


def test_live_draft_versions_and_detail(workflow_context):
    versions = run_json(
        ["draft-versions", "2093329704367428295", "--type", "article", "--limit", "2"]
    )
    check_collection(versions)
    detail = run_json(
        [
            "draft-detail",
            workflow_context["draft"],
            "--version-type",
            workflow_context["version_type"],
        ]
    )
    assert str(detail["id"]) == workflow_context["draft"]
    assert isinstance(detail["draft"], dict) and "content" in detail["draft"]


@pytest.mark.parametrize("category", ["default", "follow", "vote_thank"])
def test_live_notification_categories(category):
    check_collection(run_json(["notification-list", "--category", category, "--limit", "2"]))


@pytest.mark.parametrize("command", ["history", "follow-feed", "user-pins", "collection-items"])
def test_live_new_cursor_reads_a_different_page(command, workflow_context):
    args = [command, "--limit", "1"]
    if command == "user-pins":
        # Current account has one pin; use a public member with a long pin list.
        args.append("zhang-jia-wei")
    elif command == "collection-items":
        args.append(workflow_context["collection"])
    first = run_json(args)
    check_collection(first)
    assert first["data"] and not first["paging"].get("is_end", True)
    second = run_json([*args, "--next-url", first["paging"]["next"]])
    check_collection(second)
    assert second["data"] and second["data"] != first["data"]


def test_live_destructive_and_publication_requests_are_only_previewed():
    for args in [
        ["history-clear"],
        ["notifications-read"],
        ["publish-answer", "2073514883392463629", "Offline preview only"],
        ["save-draft", "2073514883392463629", "Offline preview only"],
    ]:
        result = run_json([*args, "--dry-run"])
        assert result["dry_run"] is True


def test_live_history_text_has_a_readable_title_or_url():
    result = CliRunner().invoke(cli, ["history", "--limit", "2"])
    assert result.exit_code == 0
    assert "URL:" in result.stdout


def test_live_current_answer_draft_is_separate_from_history(live_client):
    current = live_client.get_current_answer_draft("2073514883392463629")
    assert isinstance(current, dict)
    versions = live_client.get_draft_versions("2073514883392463629", "question")
    assert isinstance(versions["data"], list)
