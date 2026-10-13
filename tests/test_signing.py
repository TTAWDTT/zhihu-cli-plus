"""Fixed cipher vectors and signatures bound to the actual HTTP URL/cookie."""

from unittest.mock import patch

import pytest
import requests

from tests.test_client import _make_response
from zhihu_cli.client import ZhihuClient
from zhihu_cli.signing import _encrypt_digest, sign_get_headers


def rejected_parameters():
    return _make_response(403, json_data={"error": {"code": 10003}})


@pytest.mark.parametrize(
    "digest,expected",
    [
        ("0" * 32, "lqfPFvqHlOjArC49moJlh3Z9STi6W=/Q6Oc=+jroYRsDzUxnuMRYJSNxFbA/M/4b"),
        ("f" * 32, "pzMjO5WkNYDBinMPvTrWAdxiHD9O56muy1qhQAxqj8PUY8VrM=wF3FXmaFUwq5iQ"),
        (
            "c75e30054afa08c704f7574f65a2b4d7",
            "93poilmvNX6UXB8W7Hta7XfYDOIDifBfBSQNlihLOh8RuK/=hs=gPzwmqQoU=KnF",
        ),
    ],
)
def test_cipher_matches_independent_reference_vectors(digest, expected):
    assert _encrypt_digest(digest) == expected


def test_signature_preserves_literal_device_cookie_quotes():
    headers = sign_get_headers("/api/v4/questions/123?include=detail%2Ctopics", '"test=|1"')
    assert headers == {
        "x-zse-93": "101_3_3.0",
        "x-zse-96": "2.0_6OZu9vDDginMzuPRKGlDu0fXkSsWC+IpqfCduPpuDbJFkuOFMX/nWzdPY0xlIkz+",
        "x-requested-with": "fetch",
    }
    assert headers != sign_get_headers("/api/v4/questions/123?include=detail%2Ctopics", "test=|1")


def test_client_signs_requests_encoded_unicode_and_cursor():
    with ZhihuClient({"d_c0": "device=|123"}) as client:
        with patch.object(
            client._session, "get", side_effect=[rejected_parameters(), _make_response()]
        ) as get:
            client._get(
                "https://www.zhihu.com/api/v4/questions/123", params={"q": "测试", "offset": "a+b"}
            )
        headers = get.call_args.kwargs["headers"]
    assert headers["x-zse-96"] == (
        "2.0_4wIbrPqTGRo1UAoJFvhl7xCJcyG4232qo=3ygURZ4qaB+cJciw9ZPHu5L0l4xswa"
    )


def test_client_signs_existing_query_without_reordering_or_double_encoding():
    url = "https://www.zhihu.com/api/v4/questions/123?include=detail%2Ctopics"
    with ZhihuClient({"d_c0": '"test=|1"'}) as client:
        with patch.object(
            client._session, "get", side_effect=[rejected_parameters(), _make_response()]
        ) as get:
            client._get(url)
        assert get.call_args.kwargs["headers"]["x-zse-96"] == (
            "2.0_6OZu9vDDginMzuPRKGlDu0fXkSsWC+IpqfCduPpuDbJFkuOFMX/nWzdPY0xlIkz+"
        )


def test_retry_resigns_after_server_changes_device_cookie():
    with ZhihuClient({"d_c0": "first"}) as client:

        def response(*args, **kwargs):
            if not kwargs["headers"]:
                return rejected_parameters()
            if client._session.cookies.get("d_c0") == "first":
                client._session.cookies.set("d_c0", "second", domain=".zhihu.com")
                return _make_response(503)
            return _make_response()

        with patch.object(client._session, "get", side_effect=response) as get:
            client._get("https://www.zhihu.com/api/v4/questions/123")
        sent = [call.kwargs["headers"] for call in get.call_args_list]
        assert sent == [
            {},
            sign_get_headers("/api/v4/questions/123", "first"),
            sign_get_headers("/api/v4/questions/123", "second"),
        ]


def test_prepared_session_parameters_are_included_in_signature():
    with ZhihuClient({"d_c0": "device"}) as client:
        client._session.params.update({"platform": "desktop"})
        params = {"include": "detail,topics"}
        prepared = client._session.prepare_request(
            requests.Request("GET", "https://www.zhihu.com/api/v4/questions/123", params=params)
        )
        with patch.object(
            client._session, "get", side_effect=[rejected_parameters(), _make_response()]
        ) as get:
            client._get("https://www.zhihu.com/api/v4/questions/123", params)
        assert get.call_args.kwargs["headers"] == sign_get_headers(prepared.path_url, "device")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.zhihu.com.attacker.invalid/api/v4/questions/1",
        "https://www.zhihu.com/question/123",
        "http://www.zhihu.com/api/v4/questions/1",
    ],
)
def test_only_expected_https_web_api_gets_signed(url):
    with ZhihuClient({"d_c0": "device"}) as client:
        assert client._get_signing_headers(url, None) == {}


def test_missing_device_cookie_does_not_add_a_fake_signature():
    with ZhihuClient({"z_c0": "test"}) as client:
        assert client._get_signing_headers("https://www.zhihu.com/api/v4/me", None) == {}


@pytest.mark.parametrize("path,device", [("https://example.invalid", "device"), ("/api/v4/me", "")])
def test_invalid_signing_inputs_fail_without_exposing_credentials(path, device):
    with pytest.raises(ValueError, match="request path and a device cookie"):
        sign_get_headers(path, device)


def test_legacy_endpoint_keeps_working_unsigned_protocol():
    with ZhihuClient({"d_c0": "device"}) as client:
        with patch.object(client._session, "get", return_value=_make_response()) as get:
            client._get("https://www.zhihu.com/api/v4/members/user/followers")
        assert get.call_count == 1
        assert get.call_args.kwargs["headers"] == {}


def test_signed_rejection_is_not_retried_indefinitely():
    with ZhihuClient({"d_c0": "device"}) as client:
        with patch.object(client._session, "get", return_value=rejected_parameters()) as get:
            from zhihu_cli.exceptions import DataFetchError

            with pytest.raises(DataFetchError, match="10003"):
                client._get("https://www.zhihu.com/api/v4/questions/123")
        assert get.call_count == 2
        assert get.call_args_list[0].kwargs["headers"] == {}
        assert get.call_args_list[1].kwargs["headers"]["x-zse-96"].startswith("2.0_")


@pytest.mark.parametrize("status,code", [(401, 10001), (403, 40350), (403, 40362)])
def test_auth_or_permission_rejection_does_not_trigger_signing(status, code):
    from zhihu_cli.exceptions import LoginError

    with ZhihuClient({"d_c0": "device"}) as client:
        with patch.object(
            client._session,
            "get",
            return_value=_make_response(status, json_data={"error": {"code": code}}),
        ) as get:
            with pytest.raises(LoginError):
                client._get("https://www.zhihu.com/api/v4/questions/123")
        assert get.call_count == 1
        assert get.call_args.kwargs["headers"] == {}
