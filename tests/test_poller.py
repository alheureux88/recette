"""Tests for poller.py — file filtering, title extraction, and Dropbox operations."""

from unittest.mock import MagicMock, patch

import pytest

from recipes.db import init_db


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


class TestFileHash:
    def test_returns_sha256_hex(self):
        from recipes.poller import file_hash

        result = file_hash(b"hello world")
        assert len(result) == 64
        assert result == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_different_content_different_hash(self):
        from recipes.poller import file_hash

        assert file_hash(b"hello") != file_hash(b"world")


class TestListRecipeFiles:
    def _make_file_entry(self, name: str, path_lower: str) -> MagicMock:
        entry = MagicMock()
        entry.name = name
        entry.path_lower = path_lower
        # Make isinstance check pass for dropbox.files.FileMetadata
        import dropbox.files

        entry.__class__ = dropbox.files.FileMetadata
        return entry

    def test_filters_by_extension(self):
        from recipes.poller import list_recipe_files

        mock_dbx = MagicMock()
        mock_result = MagicMock()
        mock_result.has_more = False

        import dropbox.files

        file1 = MagicMock(spec=dropbox.files.FileMetadata)
        file1.name = "recipe.docx"
        file1.path_lower = "/recipes/recipe.docx"
        file2 = MagicMock(spec=dropbox.files.FileMetadata)
        file2.name = "image.jpg"
        file2.path_lower = "/recipes/image.jpg"
        file3 = MagicMock(spec=dropbox.files.FileMetadata)
        file3.name = "notes.txt"
        file3.path_lower = "/recipes/notes.txt"

        mock_result.entries = [file1, file2, file3]
        mock_dbx.files_list_folder.return_value = mock_result

        files = list_recipe_files(mock_dbx)
        names = [f.name for f in files]
        assert "recipe.docx" in names
        assert "notes.txt" in names
        assert "image.jpg" not in names

    def test_applies_file_filter(self, monkeypatch):
        from recipes.poller import list_recipe_files

        monkeypatch.setattr("recipes.poller.DROPBOX_FILE_FILTER", "BOEUF*")

        mock_dbx = MagicMock()
        mock_result = MagicMock()
        mock_result.has_more = False

        import dropbox.files

        file1 = MagicMock(spec=dropbox.files.FileMetadata)
        file1.name = "BOEUF Bourguignon.docx"
        file1.path_lower = "/recipes/boeuf_bourguignon.docx"
        file2 = MagicMock(spec=dropbox.files.FileMetadata)
        file2.name = "DESSERT Tarte.docx"
        file2.path_lower = "/recipes/dessert_tarte.docx"

        mock_result.entries = [file1, file2]
        mock_dbx.files_list_folder.return_value = mock_result

        files = list_recipe_files(mock_dbx)
        assert len(files) == 1
        assert files[0].name == "BOEUF Bourguignon.docx"

    def test_handles_pagination(self):
        from recipes.poller import list_recipe_files

        mock_dbx = MagicMock()

        import dropbox.files

        page1 = MagicMock()
        page1.has_more = True
        page1.cursor = "cursor123"
        file1 = MagicMock(spec=dropbox.files.FileMetadata)
        file1.name = "recipe1.txt"
        file1.path_lower = "/recipes/recipe1.txt"
        page1.entries = [file1]

        page2 = MagicMock()
        page2.has_more = False
        file2 = MagicMock(spec=dropbox.files.FileMetadata)
        file2.name = "recipe2.txt"
        file2.path_lower = "/recipes/recipe2.txt"
        page2.entries = [file2]

        mock_dbx.files_list_folder.return_value = page1
        mock_dbx.files_list_folder_continue.return_value = page2

        files = list_recipe_files(mock_dbx)
        assert len(files) == 2
        mock_dbx.files_list_folder_continue.assert_called_once_with("cursor123")


class TestGetOrCreateSharedLink:
    def test_reuses_existing_link(self):
        from recipes.poller import get_or_create_shared_link

        mock_dbx = MagicMock()
        mock_links = MagicMock()
        mock_links.links = [MagicMock(url="https://dropbox.com/existing")]
        mock_dbx.sharing_list_shared_links.return_value = mock_links

        result = get_or_create_shared_link(mock_dbx, "/recipes/test.docx")
        assert result == "https://dropbox.com/existing"
        mock_dbx.sharing_create_shared_link_with_settings.assert_not_called()

    def test_creates_new_link_when_none_exists(self):
        from recipes.poller import get_or_create_shared_link

        mock_dbx = MagicMock()
        mock_links = MagicMock()
        mock_links.links = []
        mock_dbx.sharing_list_shared_links.return_value = mock_links

        mock_result = MagicMock()
        mock_result.url = "https://dropbox.com/new"
        mock_dbx.sharing_create_shared_link_with_settings.return_value = mock_result

        result = get_or_create_shared_link(mock_dbx, "/recipes/test.docx")
        assert result == "https://dropbox.com/new"

    def test_returns_none_on_api_error(self):
        from dropbox.exceptions import ApiError

        from recipes.poller import get_or_create_shared_link

        mock_dbx = MagicMock()
        mock_dbx.sharing_list_shared_links.side_effect = ApiError(
            request_id="req1", error=None, user_message_text="error", user_message_locale="en"
        )

        result = get_or_create_shared_link(mock_dbx, "/recipes/test.docx")
        assert result is None


class TestProcessFile:
    @pytest.fixture
    def setup_db(self, temp_db):
        init_db()

    def test_skips_unchanged_file(self, setup_db):
        from recipes.db import mark_processed
        from recipes.poller import file_hash, process_file

        content = b"recipe content"
        content_hash = file_hash(content)
        mark_processed("/recipes/test.txt", content_hash)

        mock_dbx = MagicMock()
        mock_response = MagicMock()
        mock_response.content = content
        mock_dbx.files_download.return_value = (None, mock_response)

        mock_entry = MagicMock()
        mock_entry.name = "test.txt"
        mock_entry.path_lower = "/recipes/test.txt"

        with patch("recipes.poller.extract_text") as mock_extract:
            process_file(mock_dbx, mock_entry)

        mock_extract.assert_not_called()

    def test_processes_new_file(self, setup_db):
        from recipes.poller import process_file

        content = b"Recette de tarte aux pommes"

        mock_dbx = MagicMock()
        mock_response = MagicMock()
        mock_response.content = content
        mock_dbx.files_download.return_value = (None, mock_response)
        mock_dbx.sharing_list_shared_links.return_value = MagicMock(links=[])
        mock_link_result = MagicMock()
        mock_link_result.url = "https://dropbox.com/shared"
        mock_dbx.sharing_create_shared_link_with_settings.return_value = mock_link_result

        mock_entry = MagicMock()
        mock_entry.name = "DESSERT Tarte aux pommes.txt"
        mock_entry.path_lower = "/recipes/tarte.txt"
        mock_entry.client_modified = None

        with (
            patch("recipes.poller.extract_text", return_value="Tarte aux pommes recipe"),
            patch(
                "recipes.poller.tag_recipe",
                return_value={
                    "lang_fr": {
                        "title": "Tarte aux pommes",
                        "description": "Delicious tart",
                        "ingredients": ["apples"],
                        "instructions": "Bake",
                    },
                    "lang_en": {
                        "title": "Apple tart",
                        "description": "Delicious tart",
                        "ingredients": ["apples"],
                        "instructions": "Bake",
                    },
                    "category": "dessert",
                    "tags": {"origin": ["francais"]},
                    "source_url": None,
                },
            ),
        ):
            process_file(mock_dbx, mock_entry)

        mock_dbx.files_download.assert_called_once()

    def test_handles_parse_error(self, setup_db):
        from recipes.poller import process_file

        content = b"bad content"

        mock_dbx = MagicMock()
        mock_response = MagicMock()
        mock_response.content = content
        mock_dbx.files_download.return_value = (None, mock_response)

        mock_entry = MagicMock()
        mock_entry.name = "bad.docx"
        mock_entry.path_lower = "/recipes/bad.docx"

        with patch("recipes.poller.extract_text", side_effect=ValueError("Parse error")):
            process_file(mock_dbx, mock_entry)

    def test_handles_empty_text(self, setup_db):
        from recipes.poller import process_file

        content = b"   "

        mock_dbx = MagicMock()
        mock_response = MagicMock()
        mock_response.content = content
        mock_dbx.files_download.return_value = (None, mock_response)

        mock_entry = MagicMock()
        mock_entry.name = "empty.txt"
        mock_entry.path_lower = "/recipes/empty.txt"

        with (
            patch("recipes.poller.extract_text", return_value="   "),
            patch("recipes.poller.tag_recipe") as mock_tag,
        ):
            process_file(mock_dbx, mock_entry)
            mock_tag.assert_not_called()

    def test_handles_tagging_error(self, setup_db):
        from recipes.poller import process_file

        content = b"Some recipe text"

        mock_dbx = MagicMock()
        mock_response = MagicMock()
        mock_response.content = content
        mock_dbx.files_download.return_value = (None, mock_response)

        mock_entry = MagicMock()
        mock_entry.name = "recipe.txt"
        mock_entry.path_lower = "/recipes/recipe.txt"

        with (
            patch("recipes.poller.extract_text", return_value="Recipe text"),
            patch("recipes.poller.tag_recipe", side_effect=ValueError("LLM error")),
        ):
            process_file(mock_dbx, mock_entry)


class TestDropboxTokenRefresh:
    def test_refresh_token_success(self, monkeypatch):
        from recipes.poller import _refresh_dropbox_token

        monkeypatch.setenv("DROPBOX_REFRESH_TOKEN", "test_refresh_token")
        monkeypatch.setenv("DROPBOX_APP_KEY", "test_app_key")
        monkeypatch.setenv("DROPBOX_APP_SECRET", "test_app_secret")

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "expires_in": 14400,
        }

        with patch("recipes.poller.requests.post", return_value=mock_response) as mock_post:
            token, expiry = _refresh_dropbox_token()

        assert token == "new_access_token"
        mock_post.assert_called_once()
        assert mock_post.call_args[1]["data"]["grant_type"] == "refresh_token"
        assert mock_post.call_args[1]["data"]["refresh_token"] == "test_refresh_token"

    def test_refresh_token_failure_logs_error(self, monkeypatch):
        from recipes.poller import _refresh_dropbox_token

        monkeypatch.setenv("DROPBOX_REFRESH_TOKEN", "test_refresh_token")
        monkeypatch.setenv("DROPBOX_APP_KEY", "test_app_key")
        monkeypatch.setenv("DROPBOX_APP_SECRET", "test_app_secret")

        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 400
        mock_response.text = '{"error": "invalid_grant"}'

        with (
            patch("recipes.poller.requests.post", return_value=mock_response),
            pytest.raises(ValueError, match="invalid_grant"),
        ):
            _refresh_dropbox_token()

    def test_refresh_token_missing_env_vars(self, monkeypatch):
        from recipes.poller import _refresh_dropbox_token

        monkeypatch.delenv("DROPBOX_REFRESH_TOKEN", raising=False)
        monkeypatch.delenv("DROPBOX_APP_KEY", raising=False)
        monkeypatch.delenv("DROPBOX_APP_SECRET", raising=False)

        with pytest.raises(ValueError, match="DROPBOX_REFRESH_TOKEN"):
            _refresh_dropbox_token()

    def test_get_client_uses_refresh_token(self, monkeypatch):
        from recipes.poller import _get_dropbox_client, reset_dropbox_client

        reset_dropbox_client()
        monkeypatch.setenv("DROPBOX_REFRESH_TOKEN", "test_refresh")
        monkeypatch.setenv("DROPBOX_APP_KEY", "test_key")
        monkeypatch.setenv("DROPBOX_APP_SECRET", "test_secret")

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "access_token": "refreshed_token",
            "expires_in": 14400,
        }

        with (
            patch("recipes.poller.requests.post", return_value=mock_response),
            patch("recipes.poller.dropbox.Dropbox") as mock_dropbox,
        ):
            mock_client = MagicMock()
            mock_dropbox.return_value = mock_client

            client = _get_dropbox_client()

        assert client == mock_client
        mock_dropbox.assert_called_once_with("refreshed_token")

    def test_get_client_caches_token(self, monkeypatch):
        from recipes.poller import _get_dropbox_client, reset_dropbox_client

        reset_dropbox_client()
        monkeypatch.setenv("DROPBOX_REFRESH_TOKEN", "test_refresh")
        monkeypatch.setenv("DROPBOX_APP_KEY", "test_key")
        monkeypatch.setenv("DROPBOX_APP_SECRET", "test_secret")

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "access_token": "refreshed_token",
            "expires_in": 14400,
        }

        with (
            patch("recipes.poller.requests.post", return_value=mock_response) as mock_post,
            patch("recipes.poller.dropbox.Dropbox") as mock_dropbox,
        ):
            mock_client = MagicMock()
            mock_dropbox.return_value = mock_client

            client1 = _get_dropbox_client()
            client2 = _get_dropbox_client()

        assert client1 == client2
        assert mock_post.call_count == 1

    def test_get_client_falls_back_to_static_token(self, monkeypatch):
        from recipes.poller import _get_dropbox_client, reset_dropbox_client

        reset_dropbox_client()
        monkeypatch.delenv("DROPBOX_REFRESH_TOKEN", raising=False)
        monkeypatch.setenv("DROPBOX_TOKEN", "static_token")

        with patch("recipes.poller.dropbox.Dropbox") as mock_dropbox:
            mock_client = MagicMock()
            mock_dropbox.return_value = mock_client

            client = _get_dropbox_client()

        assert client == mock_client
        mock_dropbox.assert_called_once_with("static_token")

    def test_get_client_raises_without_credentials(self, monkeypatch):
        from recipes.poller import _get_dropbox_client, reset_dropbox_client

        reset_dropbox_client()
        monkeypatch.delenv("DROPBOX_REFRESH_TOKEN", raising=False)
        monkeypatch.delenv("DROPBOX_TOKEN", raising=False)

        with pytest.raises(ValueError, match="No Dropbox credentials"):
            _get_dropbox_client()
