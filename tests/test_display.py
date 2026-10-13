"""Tests for zhihu_cli.display module."""

from __future__ import annotations

from rich.table import Table

from zhihu_cli.display import (
    ZHIHU_THEME,
    format_count,
    format_stats_line,
    make_kv_table,
    make_table,
    strip_html,
    truncate,
)

# ── strip_html ─────────────────────────────────────────────────────────────────


class TestStripHtml:
    def test_removes_tags(self):
        assert strip_html("<p>hello</p>") == "hello"

    def test_removes_nested_tags(self):
        assert strip_html("<div><b>bold</b> text</div>") == "bold text"

    def test_unescapes_entities(self):
        assert strip_html("a &amp; b") == "a & b"

    def test_empty_string(self):
        assert strip_html("") == ""

    def test_none_returns_empty(self):
        assert strip_html(None) == ""

    def test_plain_text_unchanged(self):
        assert strip_html("no tags here") == "no tags here"

    def test_mixed_html_entities(self):
        assert strip_html("<a href='#'>click &gt; here</a>") == "click > here"

    def test_strips_whitespace(self):
        assert strip_html("  <p> padded </p>  ") == "padded"

    def test_self_closing_tags(self):
        assert strip_html("line1<br/>line2") == "line1\nline2"


# ── format_count ───────────────────────────────────────────────────────────────


class TestFormatCount:
    def test_small_number(self):
        assert format_count(42) == "42"

    def test_zero(self):
        assert format_count(0) == "0"

    def test_wan(self):
        assert format_count(12345) == "1.2万"

    def test_exact_wan(self):
        assert format_count(10000) == "1.0万"

    def test_large_wan(self):
        assert format_count(99999) == "10.0万"

    def test_yi(self):
        assert format_count(100_000_000) == "1.0亿"

    def test_large_yi(self):
        assert format_count(350_000_000) == "3.5亿"

    def test_string_number(self):
        assert format_count("5000") == "5000"

    def test_string_wan(self):
        assert format_count("50000") == "5.0万"

    def test_invalid_string(self):
        assert format_count("abc") == "abc"

    def test_below_boundary(self):
        assert format_count(9999) == "9999"


# ── truncate ───────────────────────────────────────────────────────────────────


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 10) == "hello"

    def test_exact_length(self):
        assert truncate("12345", 5) == "12345"

    def test_truncates_with_ellipsis(self):
        result = truncate("hello world", 6)
        assert result == "hello…"

    def test_empty_string(self):
        assert truncate("") == ""

    def test_none_returns_empty(self):
        assert truncate(None) == ""

    def test_newlines_replaced(self):
        assert truncate("line1\nline2", 50) == "line1 line2"

    def test_newlines_before_truncation(self):
        result = truncate("line1\nline2\nline3", 8)
        assert "\n" not in result
        assert result.endswith("…")

    def test_default_max_len(self):
        long_text = "a" * 60
        result = truncate(long_text)
        assert len(result) == 50
        assert result.endswith("…")


# ── format_stats_line ──────────────────────────────────────────────────────────


class TestFormatStatsLine:
    def test_single_stat(self):
        result = format_stats_line({"Answers": 42})
        assert "42" in result
        assert "Answers" in result

    def test_multiple_stats(self):
        result = format_stats_line({"Answers": 42, "Followers": 100})
        assert "42" in result
        assert "100" in result
        assert "Answers" in result
        assert "Followers" in result

    def test_large_numbers_formatted(self):
        result = format_stats_line({"Views": 50000})
        assert "5.0万" in result

    def test_empty_dict(self):
        result = format_stats_line({})
        assert result == ""

    def test_contains_separator(self):
        result = format_stats_line({"A": 1, "B": 2})
        assert "▸" in result


# ── make_table ─────────────────────────────────────────────────────────────────


class TestMakeTable:
    def test_returns_table(self):
        t = make_table("Test Title")
        assert isinstance(t, Table)

    def test_table_not_expanded(self):
        t = make_table("Title")
        assert t.expand is False

    def test_show_lines_default_false(self):
        t = make_table("Title")
        assert t.show_lines is False

    def test_show_lines_enabled(self):
        t = make_table("Title", show_lines=True)
        assert t.show_lines is True


class TestMakeKvTable:
    def test_returns_table(self):
        t = make_kv_table("Profile")
        assert isinstance(t, Table)

    def test_has_two_columns(self):
        t = make_kv_table("Profile")
        assert len(t.columns) == 2

    def test_no_header(self):
        t = make_kv_table("Profile")
        assert t.show_header is False

    def test_can_add_rows(self):
        t = make_kv_table("Profile")
        t.add_row("Name", "Alice")
        t.add_row("Age", "30")
        assert t.row_count == 2


# ── Theme ──────────────────────────────────────────────────────────────────────


class TestTheme:
    def test_theme_has_required_styles(self):
        for style_name in ["info", "success", "warning", "error", "title"]:
            assert style_name in ZHIHU_THEME.styles
