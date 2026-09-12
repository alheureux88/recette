"""Tests responsive mobile — pas de scroll horizontal, header compact."""

from pathlib import Path

CSS = Path("static/css/style.css").read_text(encoding="utf-8")


class TestNoHorizontalScroll:
    def test_overflow_x_clip_with_fallback(self):
        assert "overflow-x: clip" in CSS
        assert "@supports not (overflow: clip)" in CSS

    def test_images_cannot_overflow(self):
        assert "img {" in CSS
        assert "max-width: 100%" in CSS

    def test_flex_children_can_shrink(self):
        for selector in (".site-title", ".header-nav", ".cook-title"):
            assert selector in CSS
        assert "min-width: 0" in CSS

    def test_long_usernames_are_truncated(self):
        assert ".nav-user" in CSS
        assert "text-overflow: ellipsis" in CSS


class TestCompactMobileHeader:
    def test_mobile_media_query_compacts_header(self):
        assert "@media (max-width: 600px)" in CSS
        mobile = CSS.split("@media (max-width: 600px)", 1)[1]
        assert "header {" in mobile
        assert "padding: 0.6rem 0.75rem" in mobile
        assert ".nav-user" in mobile


class TestTabulatorTooltipTheme:
    def test_tooltip_uses_theme_variables(self):
        # Fond blanc + texte hérité du body = illisible en mode sombre.
        assert ".tabulator-tooltip" in CSS
        tooltip = CSS.split(".tabulator-tooltip", 1)[1]
        assert "var(--surface)" in tooltip
        assert "var(--text)" in tooltip
