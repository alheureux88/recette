"""Tests for duration.py — formatage lisible des minuteurs."""

from recipes.shared.duration import format_duration


class TestFormatDuration:
    def test_zero(self):
        assert format_duration(0) == "00:00"

    def test_seconds_only(self):
        assert format_duration(45) == "00:45"

    def test_five_minutes(self):
        assert format_duration(300) == "05:00"

    def test_with_hours(self):
        assert format_duration(3600) == "1:00:00"

    def test_hours_minutes_seconds(self):
        assert format_duration(3723) == "1:02:03"

    def test_negative_clamped(self):
        assert format_duration(-10) == "00:00"


class TestBuildStepsExposesDisplay:
    def test_duration_display_matches_cook_mode(self):
        from recipes.features.recipes.controllers import _build_steps_with_ingredients

        steps = _build_steps_with_ingredients(
            [{"text": "Mijoter", "timer_seconds": 300, "ingredients": []}]
        )
        assert steps[0]["duration_seconds"] == 300
        assert steps[0]["duration_display"] == "05:00"
        assert steps[0]["has_timer"] is True
