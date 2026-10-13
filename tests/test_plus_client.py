"""HTTP and publication regressions; all requests stay mocked."""

from unittest.mock import patch

import pytest
import requests
from PIL import Image

from tests.test_client import _make_response
from zhihu_cli.client import ZhihuClient
from zhihu_cli.exceptions import DataFetchError, LoginError


@pytest.fixture()
def client():
    with ZhihuClient({"z_c0": "dummy", "_xsrf": "dummy"}) as instance:
        yield instance


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.invalid/api/v4/me",
        "http://www.zhihu.com/api/v4/me",
        "https://www.zhihu.com.attacker.invalid/api/v4/me",
        "//attacker.invalid/api/v4/me",
        "https://www.zhihu.com:8443/api/v4/me",
        "https://user@www.zhihu.com/api/v4/me",
        "https://www.zhihu.com/api/v4/me#fragment",
        "https://www.zhihu.com/not-an-api",
        "https://www.zhihu.com:invalid/api/v4/me",
    ],
)
def test_untrusted_paging_url_never_sends_http(client, url):
    with patch.object(client._session, "get") as get:
        with pytest.raises(DataFetchError, match="pagination URL"):
            client.get_next_page(url)
        get.assert_not_called()


def test_relative_cursor_and_redirect_policy(client):
    with patch.object(client._session, "get", return_value=_make_response()) as get:
        client.get_next_page("/api/v4/answers/1/comments?offset=a%2Bb")
    assert get.call_args.args[0] == "https://www.zhihu.com/api/v4/answers/1/comments?offset=a%2Bb"
    assert get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_get_failures_are_retried(client, status):
    transient = _make_response(status)
    transient.headers = {"Retry-After": "99"}
    with (
        patch.object(
            client._session, "get", side_effect=[transient, _make_response(json_data={"ok": True})]
        ) as get,
        patch("zhihu_cli.client.time.sleep") as sleep,
    ):
        assert client._get("https://www.zhihu.com/api/v4/me") == {"ok": True}
    assert get.call_count == 2
    sleep.assert_called_once_with(5)


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_are_not_retried(client, status):
    with patch.object(client._session, "get", return_value=_make_response(status)) as get:
        with pytest.raises(LoginError) as error:
            client.get_self_info()
    assert error.value.status_code == status
    assert get.call_count == 1


def test_network_retries_are_bounded(client):
    with patch.object(
        client._session, "get", side_effect=requests.ConnectionError("offline")
    ) as get:
        with pytest.raises(DataFetchError, match="offline"):
            client.get_self_info()
    assert get.call_count == 3


def test_posts_are_never_retried(client):
    with patch.object(
        client._session, "post", side_effect=requests.ConnectionError("offline")
    ) as post:
        with pytest.raises(DataFetchError):
            client.create_question("Title")
    assert post.call_count == 1


def test_image_article_preserves_topics_and_plain_text_length(client):
    with (
        patch.object(client, "_create_content_draft", return_value="draft"),
        patch.object(client, "_content_publish", return_value={"id": "1"}) as publish,
    ):
        client.create_article(
            "Title",
            "<p>Body &amp; text</p>",
            topic_ids=["100", "200"],
            image_infos=[{"src": "https://pic.zhihu.com/image.jpg"}],
        )
    data = publish.call_args.args[0]["data"]
    assert data["topic"] == {"topics": ["100", "200"]}
    assert data["hybrid"]["textLength"] == len("Body & text")


def test_pin_text_length_is_consistent_with_and_without_images(client):
    with (
        patch.object(client, "_create_content_draft", return_value="draft"),
        patch.object(client, "_content_publish", return_value={}) as publish,
    ):
        client.create_pin("Title", "<p>Body</p>")
        plain = publish.call_args.args[0]["data"]["hybrid"]
        client.create_pin("Title", "<p>Body</p>", image_infos=[{"src": "https://pic.zhihu.com/x"}])
        illustrated = publish.call_args.args[0]["data"]["hybrid"]
    assert plain["html"] == "<p>Body</p>"
    assert plain["textLength"] == illustrated["textLength"] == 4


def test_oss_uses_actual_image_mime(client, tmp_path):
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    with patch("requests.put", return_value=_make_response()) as put:
        client._upload_to_oss(
            "key",
            image.read_bytes(),
            {"access_key": "key", "access_id": "id", "access_token": "token"},
        )
    assert put.call_args.kwargs["headers"]["Content-Type"] == "image/png"


def test_malformed_publish_response_is_not_success(client):
    response = _make_response()
    response.json.side_effect = ValueError("invalid JSON")
    with patch.object(client._session, "post", return_value=response):
        with pytest.raises(DataFetchError, match="verify before retrying"):
            client._content_publish({})


@pytest.mark.parametrize(
    "kind,path", [("answer", "answers"), ("article", "articles"), ("pin", "pins")]
)
def test_comment_endpoint_and_cursor(client, kind, path):
    with patch.object(
        client._session, "get", return_value=_make_response(json_data={"data": []})
    ) as get:
        client.get_comments("123", resource_type=kind, offset="opaque+cursor", order="reverse")
    suffix = f"/{path}/123/comments" if kind == "answer" else f"/comment_v5/{path}/123/root_comment"
    assert get.call_args.args[0].endswith(suffix)
    assert get.call_args.kwargs["params"]["offset"] == "opaque+cursor"
    if kind == "answer":
        assert get.call_args.kwargs["params"]["order"] == "reverse"
    else:
        assert get.call_args.kwargs["params"]["order_by"] == "ts"


def test_api_version_and_web_platform_headers(client):
    prepared = client._session.prepare_request(
        requests.Request("GET", "https://www.zhihu.com/api/v4/me")
    )
    assert prepared.headers["x-api-version"] == "3.0.91"
    assert prepared.headers["x-app-za"] == "OS=Web"


def test_article_detail_uses_working_zhuanlan_api(client):
    with patch.object(
        client._session,
        "get",
        return_value=_make_response(json_data={"id": 1, "content": "<p>Body</p>"}),
    ) as get:
        article = client.get_article("1")
    assert get.call_args.args[0] == "https://zhuanlan.zhihu.com/api/articles/1"
    assert article["content"] == "<p>Body</p>"


def test_request_parameter_rejection_is_not_an_expired_session(client):
    response = _make_response(
        403, json_data={"error": {"code": 10003, "message": "请求参数异常，请升级客户端后重试。"}}
    )
    with patch.object(client._session, "get", return_value=response):
        with pytest.raises(DataFetchError, match="code 10003"):
            client.get_question("123")
