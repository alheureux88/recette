"""Tests for shopping scan — local OCR parsing, classification and routes."""

import io

import pytest
from fastapi.testclient import TestClient

import recipes.features.shopping.scan_controllers as scan_controllers
from recipes.features.shopping import scan_services
from recipes.features.shopping.scan_services import (
    classify_local,
    is_low_confidence,
    mean_confidence,
    parse_scanned_lines,
    parse_tsv,
    preprocess_image,
    split_quantity,
)
from recipes.features.shopping.services import (
    create_shopping_list,
    get_shopping_departments,
    get_shopping_list_items,
)
from recipes.shared.db import init_db


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()
    scan_controllers._scan_attempts.clear()


def _login_as(monkeypatch, user_id: int = 42) -> None:
    """Simule un usager connecté (les tests n'ont pas d'OIDC)."""
    monkeypatch.setattr(scan_controllers, "require_user", lambda request: {"id": user_id})
    monkeypatch.setattr(
        "recipes.features.shopping.controllers.get_user",
        lambda request: {"id": user_id},
    )
    monkeypatch.setattr("recipes.shared.web.get_user", lambda request: {"id": user_id})


def _owned_list(user_id: int = 42):
    from recipes.shared.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, subject, email, name) VALUES (?, ?, ?, ?)",
            (user_id, f"test-subject-{user_id}", "test@example.com", "Test User"),
        )
    return create_shopping_list("Scan List", user_id=user_id)


def _png_bytes() -> bytes:
    from PIL import Image

    image = Image.new("L", (100, 100), color=255)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class TestSplitQuantity:
    def test_leading_quantity_with_unit(self):
        assert split_quantity("2 kg pommes") == ("pommes", "2 kg")

    def test_leading_quantity_no_unit(self):
        assert split_quantity("3 bananes") == ("bananes", "3")

    def test_decimal_comma(self):
        assert split_quantity("1,5 L lait") == ("lait", "1,5 L")

    def test_fraction(self):
        assert split_quantity("1/2 tasse farine") == ("farine", "1/2 tasse")

    def test_no_quantity(self):
        assert split_quantity("pommes") == ("pommes", None)

    def test_quantity_only_is_text(self):
        assert split_quantity("2") == ("2", None)


class TestTesseractCmd:
    def test_default_is_path_binary(self, monkeypatch):
        monkeypatch.delenv("TESSERACT_CMD", raising=False)
        assert scan_services.tesseract_cmd() == "tesseract"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        assert scan_services.tesseract_cmd() == r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    def test_blank_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("TESSERACT_CMD", "   ")
        assert scan_services.tesseract_cmd() == "tesseract"


class TestClassifyLocal:
    def test_dairy(self):
        assert classify_local("Lait 2%") == "produits-laitiers"

    def test_produce_beats_dairy_substring(self):
        # "laitue" contient "lait" mais doit rester en fruits-légumes.
        assert classify_local("laitue") == "fruits-legumes"

    def test_butcher(self):
        assert classify_local("poitrines de poulet") == "boucherie"

    def test_word_boundary_ail(self):
        # "volaille" contient "ail" : ne doit pas matcher les fines herbes.
        assert classify_local("volaille") == "boucherie"

    def test_bakery(self):
        assert classify_local("baguette") == "boulangerie"

    def test_unknown(self):
        assert classify_local("truc bizarre xyz") == "autre"

    def test_frozen_before_dairy(self):
        assert classify_local("crème glacée") == "surgelés"


class TestParseTsv:
    def test_groups_words_into_lines(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
            "\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\tPommes\n"
            "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t80\tvertes\n"
            "5\t1\t1\t1\t2\t1\t0\t0\t10\t10\t70\tLait\n"
        )
        lines = parse_tsv(tsv)
        assert len(lines) == 2
        assert lines[0] == {"text": "Pommes vertes", "confidence": 85.0}
        assert lines[1] == {"text": "Lait", "confidence": 70.0}


class TestParseScannedLines:
    def test_filters_noise_and_splits(self):
        items = parse_scanned_lines(
            [
                {"text": "2 kg pommes", "confidence": 90.0},
                {"text": "x", "confidence": 10.0},
                {"text": "lait", "confidence": 80.0},
            ]
        )
        assert len(items) == 2
        assert items[0]["text"] == "pommes"
        assert items[0]["quantity"] == "2 kg"
        assert items[0]["department"] == "fruits-legumes"
        assert items[1]["department"] == "produits-laitiers"

    def test_low_confidence(self):
        items = parse_scanned_lines([{"text": "lait", "confidence": 10.0}])
        assert is_low_confidence(items) is True
        assert mean_confidence([]) == 0.0


class TestPreprocess:
    def test_downscales_and_returns_png(self):
        from PIL import Image

        from recipes.features.shopping.scan_services import (
            MAX_IMAGE_DIMENSION,
            OCR_WHITE_BORDER,
        )

        image = Image.new("RGB", (3000, 100), color="red")
        out = io.BytesIO()
        image.save(out, format="JPEG")
        result = preprocess_image(out.getvalue())
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        reopened = Image.open(io.BytesIO(result))
        assert reopened.mode == "L"
        assert max(reopened.size) <= MAX_IMAGE_DIMENSION + 2 * OCR_WHITE_BORDER

    def test_upscales_small_images_for_ocr(self):
        from PIL import Image

        image = Image.new("RGB", (400, 300), color="white")
        out = io.BytesIO()
        image.save(out, format="PNG")
        result = preprocess_image(out.getvalue())
        reopened = Image.open(io.BytesIO(result))
        # x2 + bordure blanche : le texte trop petit est agrandi vers ~300 DPI.
        assert reopened.size == (400 * 2 + 48, 300 * 2 + 48)

    def test_adds_white_border(self):
        from PIL import Image

        image = Image.new("RGB", (2000, 2000), color="white")
        out = io.BytesIO()
        image.save(out, format="PNG")
        result = preprocess_image(out.getvalue())
        reopened = Image.open(io.BytesIO(result))
        assert reopened.size == (2000 + 48, 2000 + 48)
        assert reopened.getpixel((0, 0)) == 255


class TestRunTesseract:
    def _tsv(self, words: list[tuple[str, float]]) -> bytes:
        header = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
            "\tleft\ttop\twidth\theight\tconf\ttext\n"
        )
        rows = "".join(
            f"5\t1\t1\t1\t1\t{i}\t0\t0\t10\t10\t{conf}\t{text}\n"
            for i, (text, conf) in enumerate(words, start=1)
        )
        return (header + rows).encode()

    def test_keeps_good_psm6_result_without_retry(self, monkeypatch):
        import subprocess

        calls: list[str] = []

        def fake_run(cmd, **kwargs):
            psm = cmd[cmd.index("--psm") + 1]
            calls.append(psm)
            assert "--dpi" in cmd
            completed = subprocess.CompletedProcess(cmd, 0, self._tsv([("pommes", 90.0)]), b"")
            return completed

        monkeypatch.setattr(scan_services, "tesseract_available", lambda: True)
        monkeypatch.setattr(scan_services.subprocess, "run", fake_run)
        lines = scan_services.run_tesseract(b"png")
        assert lines == [{"text": "pommes", "confidence": 90.0}]
        assert calls == ["6"]

    def test_retries_psm4_when_psm6_weak(self, monkeypatch):
        import subprocess

        def fake_run(cmd, **kwargs):
            psm = cmd[cmd.index("--psm") + 1]
            if psm == "6":
                out = self._tsv([("flou", 10.0)])
            else:
                out = self._tsv([("pommes", 90.0), ("lait", 85.0)])
            return subprocess.CompletedProcess(cmd, 0, out, b"")

        monkeypatch.setattr(scan_services, "tesseract_available", lambda: True)
        monkeypatch.setattr(scan_services.subprocess, "run", fake_run)
        lines = scan_services.run_tesseract(b"png")
        assert [line["text"] for line in lines] == ["pommes lait"]


class TestScanRoutes:
    def test_scan_requires_login(self, client: TestClient):
        lst = create_shopping_list("Victim")
        resp = client.post(
            f"/shopping/lists/{lst['id']}/scan",
            files={"photo": ("l.jpg", _png_bytes(), "image/jpeg")},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 401)

    def test_scan_local_returns_review(self, client: TestClient, monkeypatch):
        _login_as(monkeypatch)
        lst = _owned_list()
        monkeypatch.setattr(scan_controllers, "tesseract_available", lambda: True)
        monkeypatch.setattr(scan_controllers, "preprocess_image", lambda raw: b"png")
        monkeypatch.setattr(
            scan_controllers,
            "run_tesseract",
            lambda png: [{"text": "2 kg pommes", "confidence": 90.0}],
        )
        resp = client.post(
            f"/shopping/lists/{lst['id']}/scan",
            files={"photo": ("l.jpg", _png_bytes(), "image/jpeg")},
        )
        assert resp.status_code == 200
        assert "pommes" in resp.text
        assert "scan_confirm_add" not in resp.text  # clé traduite, pas brute

    def test_scan_empty_shows_message(self, client: TestClient, monkeypatch):
        _login_as(monkeypatch)
        lst = _owned_list()
        monkeypatch.setattr(scan_controllers, "tesseract_available", lambda: True)
        monkeypatch.setattr(scan_controllers, "preprocess_image", lambda raw: b"png")
        monkeypatch.setattr(scan_controllers, "run_tesseract", lambda png: [])
        resp = client.post(
            f"/shopping/lists/{lst['id']}/scan",
            files={"photo": ("l.jpg", _png_bytes(), "image/jpeg")},
        )
        assert resp.status_code == 200
        assert "Aucune ligne" in resp.text

    def test_scan_rejects_bad_type(self, client: TestClient, monkeypatch):
        _login_as(monkeypatch)
        lst = _owned_list()
        resp = client.post(
            f"/shopping/lists/{lst['id']}/scan",
            files={"photo": ("l.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 400

    def test_scan_rate_limited(self, client: TestClient, monkeypatch):
        _login_as(monkeypatch)
        lst = _owned_list()
        monkeypatch.setattr(scan_controllers, "SCAN_MAX_PER_WINDOW", 1)
        monkeypatch.setattr(scan_controllers, "tesseract_available", lambda: True)
        monkeypatch.setattr(scan_controllers, "preprocess_image", lambda raw: b"png")
        monkeypatch.setattr(scan_controllers, "run_tesseract", lambda png: [])
        url = f"/shopping/lists/{lst['id']}/scan"
        files = {"photo": ("l.jpg", _png_bytes(), "image/jpeg")}
        assert client.post(url, files=files).status_code == 200
        assert client.post(url, files=files).status_code == 429

    def test_scan_vision_fallback(self, client: TestClient, monkeypatch):
        _login_as(monkeypatch)
        lst = _owned_list()
        monkeypatch.setattr(
            scan_controllers,
            "vision_scan_items",
            lambda raw, ct, lang="fr": [
                {
                    "text": "lait",
                    "quantity": "1 L",
                    "department": "produits-laitiers",
                    "confidence": -1.0,
                }
            ],
        )
        resp = client.post(
            f"/shopping/lists/{lst['id']}/scan/vision",
            files={"photo": ("l.jpg", _png_bytes(), "image/jpeg")},
        )
        assert resp.status_code == 200
        assert "lait" in resp.text

    def test_scan_confirm_adds_items(self, client: TestClient, monkeypatch):
        _login_as(monkeypatch)
        lst = _owned_list()
        list_id = int(str(lst["id"]))
        departments = get_shopping_departments(lang="fr")
        dept_id = next(int(str(d["id"])) for d in departments if d["name"] == "autre")
        resp = client.post(
            f"/shopping/lists/{list_id}/scan/confirm",
            data={
                "row_count": "2",
                "include_0": "on",
                "text_0": "Lait",
                "quantity_0": "1 L",
                "department_0": str(dept_id),
                "text_1": "Ignored",
                "quantity_1": "",
                "department_1": str(dept_id),
            },
        )
        assert resp.status_code == 200
        items = get_shopping_list_items(list_id, lang="fr")
        assert len(items) == 1
        assert items[0]["text"] == "Lait"

    def test_scan_confirm_empty_selection(self, client: TestClient, monkeypatch):
        _login_as(monkeypatch)
        lst = _owned_list()
        resp = client.post(f"/shopping/lists/{lst['id']}/scan/confirm", data={"row_count": "0"})
        assert resp.status_code == 400

    def test_scan_section_visible_when_logged_in(self, client: TestClient, monkeypatch):
        _login_as(monkeypatch)
        lst = _owned_list()
        resp = client.get(f"/shopping/{lst['id']}")
        assert resp.status_code == 200
        assert 'id="shopping-scan-form"' in resp.text

    def test_scan_section_hidden_when_anonymous(self, client: TestClient):
        # La session anonyme possède sa liste via la route de création.
        created = client.post("/shopping/lists", data={"name": "Anon Own"}, follow_redirects=False)
        assert created.status_code == 303
        own_id = int(created.headers["location"].rstrip("/").split("/")[-1])
        resp = client.get(f"/shopping/{own_id}")
        assert resp.status_code == 200
        assert 'id="shopping-scan-form"' not in resp.text
