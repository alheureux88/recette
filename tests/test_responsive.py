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


class TestHamburgerMenu:
    def test_burger_hidden_on_desktop(self):
        assert ".nav-burger" in CSS
        base = CSS.split(".nav-burger", 1)[1]
        assert "display: none" in base

    def test_mobile_nav_becomes_dropdown_panel(self):
        assert "@media (max-width: 760px)" in CSS
        mobile = CSS.split("@media (max-width: 760px)", 1)[1]
        assert ".header-nav.open" in mobile
        assert "position: absolute" in mobile
        assert "display: none" in mobile

    def test_full_username_shown_in_mobile_menu(self):
        mobile = CSS.split("@media (max-width: 760px)", 1)[1]
        assert "max-width: none" in mobile

    def test_burger_uses_theme_variables(self):
        # Pas de couleur en dur : le burger suit le thème clair/sombre.
        burger = CSS.split(".nav-burger", 1)[1].split("}", 1)[0]
        assert "var(--text)" in burger
        assert "var(--border)" in burger

    def test_header_markup_has_burger_and_nav_id(self):
        base = Path("templates/base.html").read_text(encoding="utf-8")
        assert 'id="header-nav"' in base
        assert 'id="nav-burger"' in base
        assert 'aria-controls="header-nav"' in base
