"""Tests for zhihu_cli.auth module."""

from __future__ import annotations

import json

from zhihu_cli.auth import (
    _dict_to_cookie_str,
    _has_required_cookies,
    _render_qr_half_blocks,
    clear_cookies,
    cookie_str_to_dict,
    get_cookie_string,
    get_saved_cookie_string,
    save_cookies,
)

# ── cookie_str_to_dict ─────────────────────────────────────────────────────────


class TestCookieStrToDict:
    def test_basic_parse(self):
        result = cookie_str_to_dict("z_c0=abc; _xsrf=xyz")
        assert result == {"z_c0": "abc", "_xsrf": "xyz"}

    def test_single_cookie(self):
        result = cookie_str_to_dict("z_c0=token123")
        assert result == {"z_c0": "token123"}

    def test_empty_string(self):
        result = cookie_str_to_dict("")
        assert result == {}

    def test_strips_whitespace(self):
        result = cookie_str_to_dict("  a = 1 ;  b = 2  ")
        assert result == {"a": "1", "b": "2"}

    def test_value_with_equals_sign(self):
        result = cookie_str_to_dict("z_c0=abc=def=ghi")
        assert result == {"z_c0": "abc=def=ghi"}

    def test_no_equals_ignored(self):
        result = cookie_str_to_dict("z_c0=abc; invalid; d_c0=xyz")
        assert result == {"z_c0": "abc", "d_c0": "xyz"}

    def test_multiple_cookies_semicolons(self):
        result = cookie_str_to_dict("a=1;b=2;c=3")
        assert len(result) == 3


# ── _dict_to_cookie_str ───────────────────────────────────────────────────────


class TestDictToCookieStr:
    def test_basic(self):
        result = _dict_to_cookie_str({"z_c0": "abc", "_xsrf": "xyz"})
        assert "z_c0=abc" in result
        assert "_xsrf=xyz" in result
        assert "; " in result

    def test_empty_dict(self):
        assert _dict_to_cookie_str({}) == ""

    def test_single_entry(self):
        assert _dict_to_cookie_str({"z_c0": "token"}) == "z_c0=token"


# ── _has_required_cookies ─────────────────────────────────────────────────────


class TestHasRequiredCookies:
    def test_has_all_required(self):
        assert _has_required_cookies({"z_c0": "abc", "_xsrf": "x", "d_c0": "d"})

    def test_missing_z_c0(self):
        assert not _has_required_cookies({"_xsrf": "abc", "d_c0": "d"})

    def test_empty_dict(self):
        assert not _has_required_cookies({})


# ── save_cookies & load ────────────────────────────────────────────────────────


class TestSaveCookies:
    def test_saves_and_loads(self, tmp_config_dir):
        config_dir, cookie_file = tmp_config_dir
        save_cookies("z_c0=test_token; _xsrf=xsrf_val; d_c0=dc0_val")

        assert cookie_file.exists()
        data = json.loads(cookie_file.read_text(encoding="utf-8"))
        assert data["cookies"]["z_c0"] == "test_token"
        assert data["cookies"]["_xsrf"] == "xsrf_val"
        assert data["cookies"]["d_c0"] == "dc0_val"

    def test_get_saved_cookie_string(self, saved_cookies):
        _, _, cookie_dict = saved_cookies
        result = get_saved_cookie_string()
        assert result is not None
        assert "z_c0=test_token_abc" in result

    def test_get_cookie_string(self, saved_cookies):
        result = get_cookie_string()
        assert result is not None
        assert "z_c0=test_token_abc" in result

    def test_returns_none_when_no_file(self, tmp_config_dir):
        result = get_saved_cookie_string()
        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_config_dir):
        _, cookie_file = tmp_config_dir
        cookie_file.write_text("not valid json", encoding="utf-8")
        result = get_saved_cookie_string()
        assert result is None

    def test_returns_none_when_missing_z_c0(self, tmp_config_dir):
        _, cookie_file = tmp_config_dir
        cookie_file.write_text(
            json.dumps({"cookies": {"_xsrf": "abc"}}),
            encoding="utf-8",
        )
        result = get_saved_cookie_string()
        assert result is None


# ── clear_cookies ──────────────────────────────────────────────────────────────


class TestClearCookies:
    def test_removes_cookie_file(self, saved_cookies):
        _, cookie_file, _ = saved_cookies
        assert cookie_file.exists()
        removed = clear_cookies()
        assert not cookie_file.exists()
        assert len(removed) == 1

    def test_no_file_returns_empty(self, tmp_config_dir):
        removed = clear_cookies()
        assert removed == []


# ── _render_qr_half_blocks ────────────────────────────────────────────────────


class TestRenderQrHalfBlocks:
    def test_empty_matrix(self):
        assert _render_qr_half_blocks([]) == ""

    def test_simple_matrix(self):
        matrix = [
            [True, False],
            [False, True],
        ]
        result = _render_qr_half_blocks(matrix)
        assert isinstance(result, str)
        assert len(result) > 0
        # Should contain block characters
        block_chars = {"▀", "▄", "█", " "}
        for line in result.split("\n"):
            for ch in line:
                assert ch in block_chars

    def test_all_true(self):
        matrix = [[True, True], [True, True]]
        result = _render_qr_half_blocks(matrix)
        assert "█" in result

    def test_all_false(self):
        matrix = [[False, False], [False, False]]
        result = _render_qr_half_blocks(matrix)
        # All spaces (plus border padding)
        for line in result.split("\n"):
            assert line.strip() == ""

    def test_odd_row_count(self):
        matrix = [
            [True, False],
            [False, True],
            [True, True],
        ]
        result = _render_qr_half_blocks(matrix)
        assert isinstance(result, str)
