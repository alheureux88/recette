"""Tests for poller.py — file filtering and title extraction."""


class TestMatchesFilter:
    def test_empty_filter_matches_all(self, monkeypatch):
        monkeypatch.setattr("recipes.poller.DROPBOX_FILE_FILTER", "")
        from recipes.poller import matches_filter

        assert matches_filter("BOEUF Bouilli d'antan.odt")
        assert matches_filter("DESSERT Tarte aux pommes.docx")
        assert matches_filter("anything.txt")

    def test_prefix_filter(self, monkeypatch):
        monkeypatch.setattr("recipes.poller.DROPBOX_FILE_FILTER", "BOEUF*")
        from recipes.poller import matches_filter

        assert matches_filter("BOEUF Bouilli d'antan.odt")
        assert matches_filter("boeuf something.doc")
        assert not matches_filter("DESSERT Tarte.docx")
        assert not matches_filter("POULET Poulet roti.txt")

    def test_suffix_filter(self, monkeypatch):
        monkeypatch.setattr("recipes.poller.DROPBOX_FILE_FILTER", "*.odt")
        from recipes.poller import matches_filter

        assert matches_filter("BOEUF Bouilli d'antan.odt")
        assert matches_filter("DESSERT Tarte.odt")
        assert not matches_filter("recipe.docx")
        assert not matches_filter("recipe.txt")

    def test_exact_match(self, monkeypatch):
        monkeypatch.setattr("recipes.poller.DROPBOX_FILE_FILTER", "recipe.txt")
        from recipes.poller import matches_filter

        assert matches_filter("recipe.txt")
        assert matches_filter("RECIPE.TXT")  # case insensitive
        assert not matches_filter("other.txt")
        assert not matches_filter("recipe.docx")

    def test_wildcard_in_middle(self, monkeypatch):
        monkeypatch.setattr("recipes.poller.DROPBOX_FILE_FILTER", "DESSERT*aux*")
        from recipes.poller import matches_filter

        assert matches_filter("DESSERT Tarte aux pommes.odt")
        assert matches_filter("DESSERT aux bleuets.docx")
        assert matches_filter("DESSERT Tarte aux fraises")  # contains "aux"
        assert not matches_filter("DESSERT Tarte fraises")  # no "aux"
        assert not matches_filter("BOEUF aux oignons")


class TestExtractTitleFromFilename:
    def test_category_prefix(self):
        from recipes.poller import extract_title_from_filename

        assert extract_title_from_filename("BOEUF Bouilli d'antan.odt") == "Bouilli d'antan"
        assert extract_title_from_filename("DESSERT Tarte aux pommes.docx") == "Tarte aux pommes"
        assert extract_title_from_filename("POULET Poulet roti.txt") == "Poulet roti"

    def test_no_category_prefix(self):
        from recipes.poller import extract_title_from_filename

        assert extract_title_from_filename("Ma recette favorite.docx") == "Ma recette favorite"
        assert extract_title_from_filename("recipe.txt") == "recipe"

    def test_lowercase_first_word(self):
        # If first word is not all uppercase, treat whole name as title
        from recipes.poller import extract_title_from_filename

        assert extract_title_from_filename("Boeuf bouilli.odt") == "Boeuf bouilli"
        assert extract_title_from_filename("Dessert aux pommes.docx") == "Dessert aux pommes"

    def test_single_word(self):
        from recipes.poller import extract_title_from_filename

        assert extract_title_from_filename("Recette.txt") == "Recette"
        assert extract_title_from_filename("SOUP.odt") == "SOUP"

    def test_removes_extension(self):
        from recipes.poller import extract_title_from_filename

        assert extract_title_from_filename("DESSERT Tarte.odt") == "Tarte"
        assert extract_title_from_filename("recipe.docx") == "recipe"
