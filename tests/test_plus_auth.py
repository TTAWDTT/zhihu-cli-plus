"""Offline status, corrupt storage, and QR polling regressions."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from zhihu_cli.auth import _qrcode_login_api, get_saved_cookie_string
from zhihu_cli.cli import cli
from zhihu_cli.commands.auth import _verify_cookies
from zhihu_cli.exceptions import LoginError


def test_offline_status_does_not_repair_or_rewrite(tmp_config_dir):
    _, cookie_file = tmp_config_dir
    content = json.dumps({"cookies": {"z_c0": "dummy"}})
    cookie_file.write_text(content)
    with patch("zhihu_cli.auth._fetch_missing_cookies") as repair:
        result = CliRunner().invoke(cli, ["status", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["authenticated"] is False
    repair.assert_not_called()
    assert cookie_file.read_text() == content


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        "null",
        '{"cookies": []}',
        '{"cookies": null}',
        '{"cookies": {"z_c0": "", "_xsrf": "x", "d_c0": "d"}}',
    ],
)
def test_invalid_cookie_file_is_not_authenticated(tmp_config_dir, content):
    _, cookie_file = tmp_config_dir
    cookie_file.write_text(content)
    assert get_saved_cookie_string() is None


@pytest.mark.parametrize("status,expected", [(401, False), (403, None)])
def test_verification_distinguishes_expired_session_from_access_denied(status, expected):
    client = MagicMock()
    client.__enter__.return_value = client
    client.get_self_info.side_effect = LoginError("rejected", status_code=status)
    with patch("zhihu_cli.client.ZhihuClient", return_value=client):
        assert _verify_cookies({"z_c0": "dummy"}) is expected


def qr_session(status=200, body=None):
    session = MagicMock()
    session.cookies = requests.cookies.RequestsCookieJar()
    session.cookies.set("_xsrf", "x", domain=".zhihu.com")
    session.cookies.set("d_c0", "d", domain=".zhihu.com")
    initial = requests.Response()
    initial.status_code = 200
    scan = requests.Response()
    scan.status_code = status
    scan._content = json.dumps(body or {}).encode()
    qr = requests.Response()
    qr.status_code = 200
    qr._content = b'{"token": "token", "link": "https://www.zhihu.com/qr"}'
    session.get.side_effect = [initial, initial, scan]
    session.post.side_effect = [initial, qr]
    return session


@pytest.mark.parametrize("status", [401, 403])
def test_qr_polling_rejection_fails_fast_without_saving(status, tmp_config_dir):
    session = qr_session(status)
    with (
        patch("zhihu_cli.auth.requests.Session", return_value=session),
        patch("zhihu_cli.auth._save_qrcode_image"),
        patch("zhihu_cli.auth._display_qr_text_in_terminal", return_value=True),
        patch("zhihu_cli.auth.save_cookies") as save,
    ):
        with pytest.raises(LoginError, match=f"rejected \\({status}\\)"):
            _qrcode_login_api()
    assert session.get.call_count == 3
    save.assert_not_called()


def test_qr_confirmed_body_cookies_are_applied_before_break(tmp_config_dir):
    session = qr_session(body={"login_status": "CONFIRMED", "z_c0": "test_session"})
    with (
        patch("zhihu_cli.auth.requests.Session", return_value=session),
        patch("zhihu_cli.auth._save_qrcode_image"),
        patch("zhihu_cli.auth._display_qr_text_in_terminal", return_value=True),
    ):
        cookie = _qrcode_login_api()
    assert "z_c0=test_session" in cookie
    assert tmp_config_dir[1].exists()
