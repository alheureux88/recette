"""Shopping scan — OCR d'une photo de liste d'épicerie (local-first, sans LLM).

Pipeline :
1. `preprocess_image` — Pillow, en mémoire (gris, upscale ~300 DPI,
   contraste, débruitage, accentuation, marge blanche, downscale borné).
2. `run_tesseract` — binaire `tesseract` via stdin, sortie TSV (lignes +
   confiance), retry PSM 6 -> 4, meilleur résultat gardé.
3. `parse_scanned_lines` — lignes brutes -> candidats {text, quantity, department}.
4. `classify_local` — heuristique mots-clés -> clé de département (jamais de LLM).
5. `vision_scan_items` — fallback LLM vision quand le local échoue.

La photo n'est jamais écrite sur disque : tout transite en mémoire.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import subprocess
import unicodedata
from typing import TypedDict

from recipes.shared.units import parse_quantity

log = logging.getLogger(__name__)

# --- Limites ---------------------------------------------------------------

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_DIMENSION = 2600
# Sous ce seuil, le texte est trop petit pour le LSTM de Tesseract (~300 DPI
# requis) : on agrandit avant l'OCR.
MIN_IMAGE_DIMENSION = 1600
UPSCALE_FACTOR = 2.0
OCR_WHITE_BORDER = 24
TESSERACT_TIMEOUT_SECONDS = 30
TESSERACT_LANGS = "fra+eng"
MAX_SCAN_LINES = 100
MIN_LINE_LENGTH = 2
LOW_CONFIDENCE_THRESHOLD = 60.0
VISION_UNKNOWN_CONFIDENCE = -1.0
ALLOWED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

# --- Types -----------------------------------------------------------------


class OcrLine(TypedDict):
    """Une ligne brute sortie de Tesseract avec sa confiance moyenne (0-100)."""

    text: str
    confidence: float


class ScannedItem(TypedDict):
    """Un candidat article prêt pour l'écran de validation."""

    text: str
    quantity: str | None
    department: str
    confidence: float


class OcrError(Exception):
    """Échec de l'OCR local (binaire absent, timeout, image illisible)."""


class VisionError(Exception):
    """Échec du fallback LLM vision."""


# --- Binaire ---------------------------------------------------------------


def tesseract_cmd() -> str:
    """Commande Tesseract : `TESSERACT_CMD` ou `tesseract` du PATH.

    Pratique sur Windows où l'installeur n'ajoute pas toujours le PATH :
    `TESSERACT_CMD=C:\\Program Files\\Tesseract-OCR\\tesseract.exe`.
    """
    return os.environ.get("TESSERACT_CMD", "tesseract").strip() or "tesseract"


def tesseract_available() -> bool:
    """Indique si le binaire `tesseract` est utilisable sur le système."""
    return shutil.which(tesseract_cmd()) is not None


# --- Prétraitement ---------------------------------------------------------


def preprocess_image(raw: bytes) -> bytes:
    """Normalise une photo pour l'OCR et retourne un PNG en mémoire.

    Tesseract attend ~300 DPI (hauteur d'x d'au moins 30 px) avec une marge
    blanche : gris + borne haute + agrandissement si petit + autocontraste +
    débruitage + accentuation + bordure blanche. Tout reste en mémoire et le
    coût CPU est borné (anti-abus).
    """
    from PIL import Image, ImageFilter, ImageOps

    try:
        with Image.open(io.BytesIO(raw)) as raw_image:
            image = ImageOps.exif_transpose(raw_image).convert("L")
            image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
            width, height = image.size
            if max(width, height) < MIN_IMAGE_DIMENSION:
                factor = min(
                    UPSCALE_FACTOR,
                    MAX_IMAGE_DIMENSION / max(width, height),
                )
                image = image.resize(
                    (round(width * factor), round(height * factor)),
                    Image.Resampling.LANCZOS,
                )
            image = ImageOps.autocontrast(image, cutoff=2)
            image = image.filter(ImageFilter.MedianFilter(size=3))
            image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
            image = ImageOps.expand(image, border=OCR_WHITE_BORDER, fill=255)
            out = io.BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError("unreadable image") from exc


# --- Tesseract -------------------------------------------------------------


def parse_tsv(tsv_text: str) -> list[OcrLine]:
    """Regroupe les mots (niveau 5) du TSV Tesseract en lignes (bloc/par/ligne)."""
    words: dict[tuple[str, str, str], list[tuple[str, float]]] = {}
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    for row in reader:
        try:
            if int(float(row.get("level", "0"))) != 5:
                continue
        except (TypeError, ValueError):
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf", "-1"))
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            continue
        key = (row.get("block_num", ""), row.get("par_num", ""), row.get("line_num", ""))
        words.setdefault(key, []).append((text, conf))
    lines: list[OcrLine] = []
    for parts in words.values():
        joined = " ".join(word for word, _ in parts).strip()
        if not joined:
            continue
        mean_conf = sum(conf for _, conf in parts) / len(parts)
        lines.append({"text": joined, "confidence": round(mean_conf, 1)})
    return lines


# PSM 6 = bloc uniforme (listes denses), PSM 4 = colonne variable (listes
# clairsemées / espacements irréguliers typiques d'une photo de manuscrit).
_TESSERACT_PSMS = ("6", "4")


def _tesseract_score(lines: list[OcrLine]) -> tuple[int, float]:
    """Score de tri : lignes confiantes d'abord, confiance moyenne ensuite."""
    confident = sum(1 for line in lines if line["confidence"] >= LOW_CONFIDENCE_THRESHOLD)
    mean = sum(line["confidence"] for line in lines) / len(lines) if lines else 0.0
    return (confident, mean)


def _run_tesseract_once(image_png: bytes, psm: str) -> list[OcrLine]:
    """Une passe Tesseract avec un mode de segmentation donné."""
    try:
        completed = subprocess.run(
            [
                tesseract_cmd(),
                "stdin",
                "stdout",
                "--oem",
                "1",
                "--psm",
                psm,
                "--dpi",
                "300",
                "-l",
                TESSERACT_LANGS,
                "-c",
                "preserve_interword_spaces=1",
                "tsv",
            ],
            input=image_png,
            capture_output=True,
            timeout=TESSERACT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OcrError("tesseract binary not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrError("tesseract timeout") from exc
    if completed.returncode != 0:
        log.warning(
            "Tesseract failed (psm %s, code %s): %s",
            psm,
            completed.returncode,
            completed.stderr.decode("utf-8", "ignore")[-500:],
        )
        raise OcrError("tesseract failed")
    tsv_text = completed.stdout.decode("utf-8", "ignore")
    return parse_tsv(tsv_text)


def run_tesseract(image_png: bytes) -> list[OcrLine]:
    """Passe un PNG (mémoire) à Tesseract via stdin et retourne les lignes.

    Essaie PSM 6 puis PSM 4 si le premier résultat est vide ou peu confiant,
    et garde le meilleur des deux (les photos de listes manuscrites ont des
    espacements irréguliers que le seul PSM 6 rate souvent).
    """
    if not tesseract_available():
        raise OcrError("tesseract binary not found")
    best = _run_tesseract_once(image_png, _TESSERACT_PSMS[0])
    if best and _tesseract_score(best)[0] > 0:
        return best
    for psm in _TESSERACT_PSMS[1:]:
        try:
            lines = _run_tesseract_once(image_png, psm)
        except OcrError:
            log.warning("Tesseract retry (psm %s) failed", psm)
            break
        if _tesseract_score(lines) > _tesseract_score(best):
            best = lines
    return best


# --- Découpage quantité / texte --------------------------------------------

_FRACTION_GLYPHS = {"¼": "1/4", "½": "1/2", "¾": "3/4"}

_QUANTITY_RE = re.compile(
    r"^\s*(?P<number>\d+(?:[.,]\d+)?(?:\s+\d+\s*/\s*\d+)?|\d+\s*/\s*\d+|[¼½¾])"
    r"(?:\s*(?P<unit>kg|g|ml|l\b|oz|lb|tasses?|cups?|c\.\s*à\s*soupe|c\.\s*à\s*thé"
    r"|tbsp|tsps?|bo[iî]tes?|sachets?|paquets?|bouteilles?|canettes?"
    r"|pincées?|gousses?|tranches?|morceaux?))?"
    r"\s+(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)


def split_quantity(line: str) -> tuple[str, str | None]:
    """Sépare une quantité en tête de ligne du nom d'article.

    Retourne (texte, quantité) ; si pas de quantité valide, (ligne, None).
    """
    normalized = line
    for glyph, replacement in _FRACTION_GLYPHS.items():
        normalized = normalized.replace(glyph, replacement)
    match = _QUANTITY_RE.match(normalized)
    if not match:
        return line.strip(), None
    number = match.group("number").replace(",", ".")
    if parse_quantity(number) is None:
        return line.strip(), None
    rest = match.group("rest").strip(" ,-")
    if not rest or not re.search(r"[A-Za-zÀ-ÿ]", rest):
        return line.strip(), None
    unit = (match.group("unit") or "").strip()
    quantity = f"{match.group('number').strip()} {unit}".strip()
    return rest, quantity


# --- Classification locale (sans LLM) --------------------------------------

_KEYWORDS_BY_DEPARTMENT: list[tuple[str, list[str]]] = [
    (
        "fruits-legumes",
        [
            "pomme",
            "banane",
            "orange",
            "citron",
            "lime",
            "fraise",
            "framboise",
            "bleuet",
            "mure",
            "raisin",
            "cerise",
            "peche",
            "abricot",
            "poire",
            "prune",
            "melon",
            "pasteque",
            "ananas",
            "mangue",
            "kiwi",
            "avocat",
            "tomate",
            "concombre",
            "poivron",
            "carotte",
            "oignon",
            "echalote",
            "ail",
            "patate",
            "pomme de terre",
            "laitue",
            "salade",
            "epinard",
            "chou",
            "brocoli",
            "celeri",
            "courgette",
            "aubergine",
            "champignon",
            "asperge",
            "haricot vert",
            "petit pois",
            "mais",
            "radis",
            "betterave",
            "navet",
            "fenouil",
            "persil",
            "coriandre",
            "basilic",
            "aneth",
            "menthe",
            "ciboulette",
            "thym",
            "romarin",
            "legume",
            "fruit",
        ],
    ),
    (
        "boucherie",
        [
            "poulet",
            "boeuf",
            "porc",
            "veau",
            "agneau",
            "dinde",
            "canard",
            "steak",
            "filet",
            "cotelette",
            "roti",
            "cuisse",
            "pilon",
            "hache",
            "jarret",
            "viande",
            "volaille",
            "foie",
            "lapin",
        ],
    ),
    (
        "poissonnerie",
        [
            "poisson",
            "saumon",
            "thon",
            "truite",
            "morue",
            "sole",
            "fletan",
            "crevette",
            "homard",
            "crabe",
            "moule",
            "huitre",
            "petoncle",
            "calmar",
            "fruit de mer",
            "cabillaud",
            "tilapia",
        ],
    ),
    (
        "charcuterie",
        [
            "jambon",
            "saucisson",
            "saucisse",
            "salami",
            "pepperoni",
            "bacon",
            "prosciutto",
            "mortadelle",
            "pate",
            "rillette",
            "terrine",
            "chorizo",
            "pastrami",
        ],
    ),
    (
        "boulangerie",
        [
            "pain",
            "baguette",
            "croissant",
            "brioche",
            "muffin",
            "bagel",
            "pita",
            "tortilla",
            "focaccia",
            "beigne",
        ],
    ),
    ("surgelés", ["surgele", "congele", "glace"]),
    (
        "epicerie",
        [
            "riz",
            "pates",
            "nouille",
            "ramen",
            "couscous",
            "quinoa",
            "avoine",
            "gruau",
            "cereale",
            "farine",
            "sucre",
            "cassonade",
            "sel",
            "poivre",
            "epice",
            "paprika",
            "cumin",
            "cannelle",
            "origan",
            "huile",
            "vinaigre",
            "sauce",
            "ketchup",
            "moutarde",
            "mayo",
            "conserve",
            "soupe",
            "bouillon",
            "potage",
            "cafe",
            "the",
            "tisane",
            "miel",
            "sirop",
            "confiture",
            "arachide",
            "coco",
            "jus",
            "soda",
            "cola",
            "eau",
            "limonade",
            "biscuit",
            "craquelin",
            "chip",
            "chocolat",
            "bonbon",
            "melange",
            "levure",
            "vanille",
            "cacao",
            "lentille",
            "pois chiche",
            "haricot rouge",
            "haricot noir",
            "tofu",
            "soja",
            "tahini",
        ],
    ),
    (
        "produits-laitiers",
        [
            "lait",
            "fromage",
            "beurre",
            "yogourt",
            "yaourt",
            "creme",
            "oeuf",
            "margarine",
            "mozzarella",
            "cheddar",
            "parmesan",
            "brie",
            "ricotta",
            "kefir",
        ],
    ),
    (
        "pret-a-manger",
        [
            "sandwich",
            "pizza",
            "sushi",
            "quiche",
            "wrap",
            "traiteur",
            "rotisserie",
        ],
    ),
    (
        "entretien",
        [
            "savon",
            "detergent",
            "javel",
            "nettoyant",
            "eponge",
            "papier",
            "essuie",
            "mouchoir",
            "poubelle",
            "shampoing",
            "dentifrice",
            "lessive",
            "assouplissant",
            "desodorisant",
            "ampoule",
            "pile",
        ],
    ),
]


def _normalize(text: str) -> str:
    """Minuscules + sans accents pour la comparaison de mots-clés."""
    folded = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in folded if not unicodedata.combining(char))


def classify_local(text: str) -> str:
    """Devine la clé de département par mots-clés, `autre` par défaut.

    Les mots courts (<= 4 lettres) sont matchés sur frontières de mots pour
    éviter les faux positifs (`ail` dans `volaille`, `eau` dans `beau`).
    """
    normalized = _normalize(text)
    for department, keywords in _KEYWORDS_BY_DEPARTMENT:
        for keyword in keywords:
            if len(keyword) <= 4:
                if re.search(rf"\b{re.escape(keyword)}\b", normalized):
                    return department
            elif keyword in normalized:
                return department
    return "autre"


# --- Assemblage ------------------------------------------------------------


def parse_scanned_lines(lines: list[OcrLine]) -> list[ScannedItem]:
    """Filtre le bruit OCR et convertit les lignes en candidats articles."""
    items: list[ScannedItem] = []
    for line in lines:
        text = line["text"].strip()
        if len(text) < MIN_LINE_LENGTH or not re.search(r"[A-Za-zÀ-ÿ0-9]", text):
            continue
        food, quantity = split_quantity(text)
        items.append(
            {
                "text": food[:500],
                "quantity": quantity[:100] if quantity else None,
                "department": classify_local(food),
                "confidence": line["confidence"],
            }
        )
        if len(items) >= MAX_SCAN_LINES:
            break
    return items


def mean_confidence(items: list[ScannedItem]) -> float:
    """Confiance moyenne d'une transcription locale (0 si vide)."""
    if not items:
        return 0.0
    return sum(item["confidence"] for item in items) / len(items)


def is_low_confidence(items: list[ScannedItem]) -> bool:
    """Transcription vide ou sous le seuil : proposer le fallback IA."""
    return not items or mean_confidence(items) < LOW_CONFIDENCE_THRESHOLD


# --- Fallback LLM vision ---------------------------------------------------

_VISION_PROMPT = (
    "Transcris cette photo d'une liste d'épicerie manuscrite ou imprimée. "
    "Retourne UNIQUEMENT un tableau JSON, sans markdown ni explication. "
    'Chaque élément : {"text": "nom de l\'article sans quantité", '
    '"quantity": "quantité avec unité ou null"}. '
    "Une ligne = un élément. Ignore les ratures."
)


def vision_scan_items(image_bytes: bytes, content_type: str, lang: str = "fr") -> list[ScannedItem]:
    """Transcrit une photo via le LLM vision configuré (fallback).

    Réutilise le provider/modèle de `tagger` (OpenAI ou Anthropic).
    """
    from recipes.shared.tagger import chat_with_image

    prompt = _VISION_PROMPT
    if lang == "en":
        prompt += " The item names must be in English."
    else:
        prompt += " Les noms d'articles doivent être en français."
    try:
        raw = chat_with_image(
            system_prompt="Tu es un transcripteur de listes d'épicerie.",
            user_text=prompt,
            image_bytes=image_bytes,
            content_type=content_type,
        )
    except Exception as exc:
        raise VisionError("vision call failed") from exc
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise VisionError("vision returned invalid JSON") from exc
    if not isinstance(data, list):
        raise VisionError("vision returned invalid JSON")
    items: list[ScannedItem] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        quantity = entry.get("quantity")
        items.append(
            {
                "text": text.strip()[:500],
                "quantity": quantity.strip()[:100]
                if isinstance(quantity, str) and quantity.strip()
                else None,
                "department": classify_local(text),
                "confidence": VISION_UNKNOWN_CONFIDENCE,
            }
        )
        if len(items) >= MAX_SCAN_LINES:
            break
    return items
