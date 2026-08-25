"""Tests for units.py — analyse des quantités, conversion et formatage des ingrédients."""

from recipes.units import format_ingredient, parse_quantity


class TestParseQuantity:
    def test_int(self):
        assert parse_quantity(2) == 2.0

    def test_float(self):
        assert parse_quantity(1.5) == 1.5

    def test_fraction_string(self):
        assert parse_quantity("1/2") == 0.5

    def test_mixed_fraction_string(self):
        assert parse_quantity("1 1/2") == 1.5

    def test_comma_decimal_string(self):
        assert parse_quantity("0,5") == 0.5

    def test_numeric_string(self):
        assert parse_quantity("250") == 250.0

    def test_invalid_string(self):
        assert parse_quantity("abc") is None

    def test_empty_string(self):
        assert parse_quantity("  ") is None

    def test_none(self):
        assert parse_quantity(None) is None

    def test_negative(self):
        assert parse_quantity(-3) is None

    def test_negative_string(self):
        assert parse_quantity("-1/2") is None

    def test_bool(self):
        assert parse_quantity(True) is None

    def test_zero_division_string(self):
        assert parse_quantity("1/0") is None


class TestFormatIngredientOriginal:
    def test_simple_unit(self):
        ing = {"food": "farine", "quantity_min": 1.5, "quantity_max": None, "unit": "tasse"}
        assert format_ingredient(ing) == "1 1/2 tasses de farine"

    def test_single_quantity(self):
        ing = {"food": "beurre", "quantity_min": 0.5, "quantity_max": None, "unit": "tasse"}
        assert format_ingredient(ing) == "1/2 tasse de beurre"

    def test_range(self):
        ing = {"food": "sucre", "quantity_min": 1, "quantity_max": 2, "unit": "tasse"}
        assert format_ingredient(ing) == "1 à 2 tasses de sucre"

    def test_range_singular_when_max_is_one(self):
        ing = {"food": "levure", "quantity_min": 0.5, "quantity_max": 1, "unit": "c. à thé"}
        assert format_ingredient(ing) == "1/2 à 1 c. à thé de levure"

    def test_no_unit(self):
        ing = {"food": "oeufs", "quantity_min": 2, "quantity_max": None, "unit": None}
        assert format_ingredient(ing) == "2 oeufs"

    def test_no_quantity(self):
        ing = {"food": "sel au goût", "quantity_min": None, "quantity_max": None, "unit": None}
        assert format_ingredient(ing) == "sel au goût"

    def test_elision(self):
        ing = {
            "food": "huile d'olive",
            "quantity_min": 2,
            "quantity_max": None,
            "unit": "c. à soupe",
        }
        assert format_ingredient(ing) == "2 c. à soupe d'huile d'olive"

    def test_unknown_unit_plural(self):
        ing = {"food": "ail", "quantity_min": 3, "quantity_max": None, "unit": "gousse"}
        assert format_ingredient(ing) == "3 gousses d'ail"

    def test_unknown_unit_singular(self):
        ing = {"food": "ail", "quantity_min": 1, "quantity_max": None, "unit": "gousse"}
        assert format_ingredient(ing) == "1 gousse d'ail"

    def test_metric_unit_uses_decimal(self):
        ing = {"food": "sel", "quantity_min": 37.5, "quantity_max": None, "unit": "g"}
        assert format_ingredient(ing) == "37,5 g de sel"

    def test_plural_unit_input_canonicalized(self):
        ing = {"food": "farine", "quantity_min": 2, "quantity_max": None, "unit": "tasses"}
        assert format_ingredient(ing) == "2 tasses de farine"

    def test_legacy_string_passthrough(self):
        assert format_ingredient("1/2 cup butter") == "1/2 cup butter"

    def test_invalid_input(self):
        assert format_ingredient(42) == ""


class TestFormatIngredientMultiplier:
    def test_integers(self):
        ing = {"food": "farine", "quantity_min": 1, "quantity_max": None, "unit": "tasse"}
        assert format_ingredient(ing, multiplicateur=3.0) == "3 tasses de farine"

    def test_fraction(self):
        ing = {"food": "farine", "quantity_min": 1, "quantity_max": None, "unit": "tasse"}
        assert format_ingredient(ing, multiplicateur=1.5) == "1 1/2 tasses de farine"

    def test_range(self):
        ing = {"food": "sucre", "quantity_min": 1, "quantity_max": 2, "unit": "tasse"}
        assert format_ingredient(ing, multiplicateur=2.0) == "2 à 4 tasses de sucre"

    def test_unitless(self):
        ing = {"food": "oeufs", "quantity_min": 2, "quantity_max": None, "unit": None}
        assert format_ingredient(ing, multiplicateur=1.5) == "3 oeufs"


class TestFormatIngredientConversion:
    def test_metric_to_imperial_mass_lb(self):
        ing = {"food": "boeuf haché", "quantity_min": 450, "quantity_max": None, "unit": "g"}
        assert format_ingredient(ing, systeme="imperial") == "1 lb de boeuf haché"

    def test_metric_to_imperial_mass_oz(self):
        ing = {"food": "fromage", "quantity_min": 100, "quantity_max": None, "unit": "g"}
        assert format_ingredient(ing, systeme="imperial") == "3 1/2 oz de fromage"

    def test_imperial_to_metric_mass(self):
        ing = {"food": "boeuf haché", "quantity_min": 1, "quantity_max": None, "unit": "lb"}
        assert format_ingredient(ing, systeme="metric") == "454 g de boeuf haché"

    def test_imperial_to_metric_volume(self):
        ing = {"food": "eau", "quantity_min": 1, "quantity_max": None, "unit": "tasse"}
        assert format_ingredient(ing, systeme="metric") == "250 ml d'eau"

    def test_metric_to_imperial_volume_tbsp(self):
        ing = {"food": "sauce soya", "quantity_min": 15, "quantity_max": None, "unit": "ml"}
        assert format_ingredient(ing, systeme="imperial") == "1 c. à soupe de sauce soya"

    def test_metric_to_imperial_volume_quarter_cup(self):
        ing = {"food": "crème", "quantity_min": 62.5, "quantity_max": None, "unit": "ml"}
        assert format_ingredient(ing, systeme="imperial") == "1/4 tasse de crème"

    def test_metric_stays_metric(self):
        ing = {"food": "eau", "quantity_min": 250, "quantity_max": None, "unit": "ml"}
        assert format_ingredient(ing, systeme="metric") == "250 ml d'eau"

    def test_metric_kg_promotion(self):
        ing = {"food": "farine", "quantity_min": 1500, "quantity_max": None, "unit": "g"}
        assert format_ingredient(ing, systeme="metric") == "1,5 kg de farine"

    def test_imperial_stays_imperial(self):
        ing = {"food": "beurre", "quantity_min": 0.5, "quantity_max": None, "unit": "tasse"}
        assert format_ingredient(ing, systeme="imperial") == "1/2 tasse de beurre"

    def test_unknown_unit_not_converted(self):
        ing = {"food": "ail", "quantity_min": 2, "quantity_max": None, "unit": "gousse"}
        assert format_ingredient(ing, systeme="metric") == "2 gousses d'ail"

    def test_conversion_with_multiplier(self):
        ing = {"food": "eau", "quantity_min": 1, "quantity_max": None, "unit": "tasse"}
        assert format_ingredient(ing, multiplicateur=2.0, systeme="metric") == "500 ml d'eau"

    def test_range_converted_with_unit_from_max(self):
        ing = {"food": "boeuf haché", "quantity_min": 400, "quantity_max": 450, "unit": "g"}
        resultat = format_ingredient(ing, systeme="imperial")
        assert resultat == "7/8 à 1 lb de boeuf haché"

    def test_unit_without_quantity_keeps_canonical(self):
        ing = {"food": "farine", "quantity_min": None, "quantity_max": None, "unit": "tasses"}
        assert format_ingredient(ing) == "tasse de farine"

    def test_invalid_system_falls_back_to_original(self):
        ing = {"food": "eau", "quantity_min": 1, "quantity_max": None, "unit": "tasse"}
        assert format_ingredient(ing, systeme="bogus") == "1 tasse d'eau"
