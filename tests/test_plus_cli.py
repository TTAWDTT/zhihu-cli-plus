"""End-to-end CLI regressions with a mocked account and HTTP boundary."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from tests.test_cli import _make_mock_client
from tests.test_pagination import NEXT, page
from zhihu_cli.cli import cli
from zhihu_cli.exceptions import DataFetchError


@pytest.mark.parametrize(
    "command,method,args",
    [
        ("vote", "vote_up", []),
        ("vote", "vote_neutral", ["--neutral"]),
        ("vote", "vote_neutral", ["--undo"]),
        ("follow-question", "follow_question", []),
        ("follow-question", "unfollow_question", ["--unfollow"]),
        ("follow-question", "unfollow_question", ["--undo"]),
    ],
)
def test_rejected_interaction_fails(command, method, args, saved_cookies):
    client = _make_mock_client(**{method: False})
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, [command, "123", *args, "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "rejected" in result.stderr
    assert "success" not in result.stdout


@pytest.mark.parametrize(
    "command,method",
    [
        ("vote", "vote_up"),
        ("follow-question", "follow_question"),
    ],
)
def test_interaction_json(command, method, saved_cookies):
    client = _make_mock_client(**{method: True})
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, [command, "123", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["success"] is True


def test_answer_json_includes_all_comment_pages(saved_cookies):
    client = _make_mock_client(
        get_answer={"id": 123, "content": "<p>Answer</p>"},
        get_answer_comments=page([1], next_url=NEXT, is_end=False),
        get_next_page=page([2]),
    )
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["answer", "123", "--comments", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["content"] == "<p>Answer</p>"
    assert [c["id"] for c in data["comments"]["data"]] == [1, 2]
    assert data["comments"]["pages_fetched"] == 2


def test_comments_failure_is_not_empty_success(saved_cookies):
    client = _make_mock_client(get_answer={"id": 123})
    client.get_answer_comments.side_effect = DataFetchError("forbidden")
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["answer", "123", "--comments", "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "forbidden" in result.stderr


@pytest.mark.parametrize("kind", ["answer", "article", "pin"])
def test_standalone_comments(kind, saved_cookies):
    client = _make_mock_client(get_comments=page([1]))
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(
            cli,
            [
                "comments",
                "123",
                "--type",
                kind,
                "--offset",
                "cursor+value",
                "--order",
                "reverse",
                "--json",
            ],
        )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"][0]["id"] == 1
    client.get_comments.assert_called_once_with(
        "123", resource_type=kind, order="reverse", limit=20, offset="cursor+value"
    )


@pytest.mark.parametrize(
    "command,method,args",
    [
        ("search", "search", ["Python", "-a", "0"]),
        ("answers", "get_question_answers", ["123"]),
        ("user-answers", "get_user_answers", ["someone"]),
        ("user-articles", "get_user_articles", ["someone"]),
        ("followers", "get_followers", ["someone"]),
        ("following", "get_following", ["someone"]),
        ("collections", "get_collections", []),
        ("notifications", "get_notifications", []),
        ("feed", "get_feed", []),
    ],
)
def test_collection_commands_share_paging(command, method, args, saved_cookies):
    client = _make_mock_client(
        **{method: page([1], next_url=NEXT, is_end=False), "get_next_page": page([2])}
    )
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, [command, *args, "--all", "--json"])
    assert result.exit_code == 0, result.output
    assert [item["id"] for item in json.loads(result.stdout)["data"]] == [1, 2]
    client.get_next_page.assert_called_once_with(NEXT)


def test_feeds_json_enriches_content_and_comments(saved_cookies):
    client = _make_mock_client(
        get_feed={
            "data": [
                {"target": {"type": "answer", "id": 1}},
                {"target": {"type": "article", "id": 2}},
            ]
        },
        get_answer={"id": 1, "content": "<p>Full answer</p>"},
        get_article={"id": 2, "content": "<p>Full article</p>"},
        get_comments=page([3]),
    )
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["feeds", "--json"])
    assert result.exit_code == 0, result.output
    items = json.loads(result.stdout)["data"]
    assert items[0]["target"]["content"] == "<p>Full answer</p>"
    assert items[1]["target"]["content"] == "<p>Full article</p>"
    assert all(item["comments"]["data"][0]["id"] == 3 for item in items)
    assert client.get_comments.call_count == 2


def test_feeds_comment_failure_fails_json(saved_cookies):
    client = _make_mock_client(
        get_feed={"data": [{"target": {"type": "answer", "id": 1}}]}, get_answer={"id": 1}
    )
    client.get_comments.side_effect = DataFetchError("rate limited")
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["feeds", "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "rate limited" in result.stderr


@pytest.mark.parametrize(
    "command,method,flag,key",
    [
        ("question", "get_question", "--answers", "answers"),
        ("topic", "get_topic", "--questions", "questions"),
    ],
)
def test_detail_json_includes_requested_collection(command, method, flag, key, saved_cookies):
    client = _make_mock_client(
        **{
            method: {"id": 123},
            "get_question_answers": page([1]),
            "get_topic_hot_questions": page([1]),
        }
    )
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, [command, "123", flag, "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)[key]["data"][0]["id"] == 1


@pytest.mark.parametrize(
    "command,args",
    [
        ("answer", ["1", "--comments", "-l", "-1"]),
        ("search", ["Python", "-l", "0"]),
        ("feed", ["--page", "0"]),
        ("comments", ["1", "--max-pages", "0"]),
        ("question", ["1", "--all"]),
    ],
)
def test_invalid_options_fail_without_fetching(command, args, saved_cookies):
    with patch("zhihu_cli.client.ZhihuClient") as client:
        result = CliRunner().invoke(cli, [command, *args])
    assert result.exit_code == 2
    client.assert_not_called()


def test_article_does_not_wrap_existing_html(saved_cookies):
    client = _make_mock_client(create_article={"id": "1"})
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(
            cli, ["article", "Title", "<h2>Heading</h2><p>Body</p>", "--json"]
        )
    assert result.exit_code == 0
    assert client.create_article.call_args.kwargs["content"] == "<h2>Heading</h2><p>Body</p>"
    assert json.loads(result.stdout)["id"] == "1"


def test_status_json_explicitly_does_not_verify_session(saved_cookies):
    result = CliRunner().invoke(cli, ["status", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "authenticated": True,
        "verified": False,
        "source": "local",
    }


def test_login_json_never_contains_credentials(tmp_config_dir):
    result = CliRunner().invoke(
        cli, ["login", "--cookie", "z_c0=secret; _xsrf=x; d_c0=d", "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"saved": True, "verified": None}
    assert "secret" not in result.output


def test_answers_text_keeps_answer_content_and_id(saved_cookies):
    client = _make_mock_client(
        get_question_answers={
            "data": [
                {
                    "id": 999,
                    "type": "answer",
                    "question": {"id": 123, "title": "Question title"},
                    "author": {"name": "Author"},
                    "content": "<p>Answer body</p>",
                }
            ]
        }
    )
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["answers", "123"])
    assert result.exit_code == 0
    assert "Answer body" in result.stdout
    assert "ID: 999" in result.stdout
    assert "Author" in result.stdout


def test_feeds_text_handles_pin_content_blocks(saved_cookies):
    client = _make_mock_client(
        get_feed={"data": [{"target": {"id": 1, "type": "pin"}}]},
        get_pin={
            "id": 1,
            "content": [
                {"type": "text", "content": "Pin body"},
                {"type": "image", "url": "https://pic.zhihu.com/x"},
            ],
        },
    )
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["feeds", "-c", "0"])
    assert result.exit_code == 0, result.output
    assert "Pin body" in result.stdout


def test_all_commands_have_json_option():
    for name, command in cli.commands.items():
        assert any(parameter.name == "as_json" for parameter in command.params), name


def test_qr_login_json_keeps_progress_on_stderr(tmp_config_dir):
    def qr_login():
        from zhihu_cli.display import print_info

        print_info("QR code progress")
        return "z_c0=test; _xsrf=x; d_c0=d"

    with (
        patch("zhihu_cli.commands.auth.qrcode_login", side_effect=qr_login),
        patch("zhihu_cli.commands.auth._verify_cookies", return_value=True),
    ):
        result = CliRunner().invoke(cli, ["login", "--qrcode", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"saved": True, "verified": True}
    assert "QR code progress" in result.stderr


def test_feeds_uses_complete_content_already_provided_by_api(saved_cookies):
    client = _make_mock_client(
        get_feed={
            "data": [
                {"target": {"id": 1, "type": "answer", "content": "<p>Full provided answer</p>"}}
            ]
        },
        get_comments={"data": []},
    )
    client.get_answer.side_effect = DataFetchError("detail API unavailable")
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        result = CliRunner().invoke(cli, ["feeds", "--json"])
    assert result.exit_code == 0, result.output
    assert (
        json.loads(result.stdout)["data"][0]["target"]["content"] == "<p>Full provided answer</p>"
    )
    client.get_answer.assert_not_called()
    client.get_comments.assert_called_once()


def test_v5_comment_metadata_and_like_count_survive_json_and_text(saved_cookies):
    response = {
        "data": [
            {
                "id": "1",
                "content": "Root comment",
                "like_count": 7,
                "author": {"name": "Author"},
                "child_comment_count": 1,
                "child_comments": [{"id": "2", "content": "Child comment"}],
            }
        ],
        "paging": {"is_end": True},
        "counts": {"total_counts": 2},
    }
    client = _make_mock_client(get_comments=response)
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        json_result = CliRunner().invoke(cli, ["comments", "123", "--type", "article", "--json"])
        text_result = CliRunner().invoke(cli, ["comments", "123", "--type", "article"])
    assert json_result.exit_code == text_result.exit_code == 0
    assert json.loads(json_result.stdout) == response
    assert "7 likes" in text_result.stdout
    assert "Author" in text_result.stdout
