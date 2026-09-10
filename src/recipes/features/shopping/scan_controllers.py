"""Shopping scan — photo d'une liste manuscrite -> OCR -> validation -> ajout.

Réservé aux usagers connectés (`require_user`) : l'OCR est coûteux en CPU
et on ne veut pas d'abus anonyme. La photo n'est jamais stockée, tout est
traité en mémoire. L'ajout réel passe toujours par un écran de validation.
"""

import logging
import sqlite3
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Path, Request, UploadFile
from fastapi.responses import HTMLResponse

from recipes.features.preferences.controllers import ordered_departments_for_user
from recipes.features.shopping.controllers import _can_edit_shopping_list
from recipes.features.shopping.scan_services import (
    ALLOWED_IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    OcrError,
    ScannedItem,
    VisionError,
    is_low_confidence,
    mean_confidence,
    parse_scanned_lines,
    preprocess_image,
    run_tesseract,
    tesseract_available,
    vision_scan_items,
)
from recipes.features.shopping.services import (
    add_shopping_list_item,
    get_shopping_departments,
    get_shopping_list_by_id,
    get_shopping_list_items,
)
from recipes.shared.auth import require_user
from recipes.shared.db import get_db
from recipes.shared.i18n import gettext
from recipes.shared.models import JsonDict

log = logging.getLogger(__name__)

router = APIRouter(tags=["shopping-scan"])

# --- Anti-abus (en mémoire ; suffisant pour une instance unique) --------------

SCAN_WINDOW_SECONDS = 600
SCAN_MAX_PER_WINDOW = 5

_scan_attempts: dict[int, deque[float]] = {}


def _check_rate_limit(user_id: int, lang: str) -> None:
    """Limite les scans par usager pour protéger le CPU (429 sinon)."""
    now = time.monotonic()
    attempts = _scan_attempts.setdefault(user_id, deque())
    while attempts and now - attempts[0] > SCAN_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= SCAN_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail=gettext("error.scan_rate_limited", lang))
    attempts.append(now)


def _get_scannable_list(
    request: Request, list_id: int, conn: sqlite3.Connection, lang: str
) -> tuple[JsonDict, int]:
    """Liste éditable par l'usager connecté, 404 sinon (anti-énumération)."""
    user = require_user(request)
    try:
        user_id = int(str(user.get("id")))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=404, detail=gettext("error.shopping_list_not_found", lang)
        ) from None
    shopping_list = get_shopping_list_by_id(list_id, conn=conn)
    if not shopping_list or not _can_edit_shopping_list(request, shopping_list):
        raise HTTPException(status_code=404, detail=gettext("error.shopping_list_not_found", lang))
    return shopping_list, user_id


async def _read_upload(photo: UploadFile, lang: str) -> tuple[bytes, str]:
    """Valide et lit une photo uploadée (type + taille bornés)."""
    content_type = (photo.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=gettext("error.scan_unsupported_type", lang))
    raw = await photo.read()
    if not raw:
        raise HTTPException(status_code=400, detail=gettext("error.scan_image_required", lang))
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=gettext("error.scan_too_large", lang))
    return raw, content_type


def _render_review(
    request: Request,
    shopping_list: JsonDict,
    items: list[ScannedItem],
    departments: list[JsonDict],
    source: str,
    lang: str,
) -> HTMLResponse:
    """Écran de validation HTMX : lignes éditables avant ajout réel."""
    from recipes.shared.web import _base_context, templates

    dept_id_by_key = {str(d["name"]): int(str(d["id"])) for d in departments}
    default_dept = dept_id_by_key.get("autre", departments[0]["id"] if departments else 1)
    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append(
            {
                "text": item["text"],
                "quantity": item["quantity"] or "",
                "department_id": dept_id_by_key.get(item["department"], default_dept),
                "confidence": item["confidence"],
            }
        )
    return templates.TemplateResponse(
        request=request,
        name="partials/shopping_scan_review.html",
        context=_base_context(
            request,
            shopping_list=shopping_list,
            scan_rows=rows,
            departments=departments,
            source=source,
            low_confidence=is_low_confidence(items) if source == "local" else False,
            mean_confidence=round(mean_confidence(items), 1),
        ),
    )


def _render_items(
    request: Request,
    shopping_list: JsonDict,
    list_id: int,
    lang: str,
    conn: sqlite3.Connection,
) -> HTMLResponse:
    """Re-rend la liste des articles après ajout (même partial que l'ajout manuel)."""
    from recipes.shared.web import _base_context, templates

    items = get_shopping_list_items(list_id, lang=lang, conn=conn)
    departments = ordered_departments_for_user(request, lang, conn)
    grouped: dict[int, dict[str, Any]] = {}
    for dept in departments:
        grouped[int(str(dept["id"]))] = {"department": dept, "item_list": []}
    for item in items:
        dept_id = int(str(item["department_id"]))
        if dept_id in grouped:
            grouped[dept_id]["item_list"].append(item)
    mode = request.query_params.get("mode", "edit")
    return templates.TemplateResponse(
        request=request,
        name="partials/shopping_list_items.html",
        context=_base_context(
            request,
            grouped_departments=list(grouped.values()),
            mode=mode,
            can_edit=True,
            shopping_list=shopping_list,
        ),
    )


@router.post("/shopping/lists/{list_id}/scan", response_class=HTMLResponse)
async def shopping_list_scan(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    list_id: int = Path(gt=0),
    photo: UploadFile = File(...),
) -> HTMLResponse:
    """OCR local d'une photo de liste -> écran de validation (usager connecté)."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    shopping_list, user_id = _get_scannable_list(request, list_id, conn, lang)
    _check_rate_limit(user_id, lang)
    raw, _ = await _read_upload(photo, lang)
    if not tesseract_available():
        raise HTTPException(status_code=503, detail=gettext("error.scan_ocr_unavailable", lang))
    try:
        image_png = preprocess_image(raw)
        lines = run_tesseract(image_png)
    except OcrError:
        log.exception("Local OCR failed for user %s", user_id)
        raise HTTPException(
            status_code=422, detail=gettext("error.scan_ocr_failed", lang)
        ) from None
    items = parse_scanned_lines(lines)
    departments = get_shopping_departments(lang=lang, conn=conn)
    return _render_review(request, shopping_list, items, departments, "local", lang)


@router.post("/shopping/lists/{list_id}/scan/vision", response_class=HTMLResponse)
async def shopping_list_scan_vision(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    list_id: int = Path(gt=0),
    photo: UploadFile = File(...),
) -> HTMLResponse:
    """Fallback LLM vision quand l'OCR local échoue (usager connecté)."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    shopping_list, user_id = _get_scannable_list(request, list_id, conn, lang)
    _check_rate_limit(user_id, lang)
    raw, content_type = await _read_upload(photo, lang)
    try:
        items = vision_scan_items(raw, content_type, lang)
    except VisionError as exc:
        log.exception("Vision scan failed for user %s", user_id)
        raise HTTPException(
            status_code=502, detail=gettext("error.scan_vision_failed", lang)
        ) from exc
    departments = get_shopping_departments(lang=lang, conn=conn)
    return _render_review(request, shopping_list, items, departments, "llm", lang)


@router.post("/shopping/lists/{list_id}/scan/confirm", response_class=HTMLResponse)
async def shopping_list_scan_confirm(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    list_id: int = Path(gt=0),
) -> HTMLResponse:
    """Ajoute les lignes cochées de l'écran de validation (usager connecté)."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    shopping_list, _ = _get_scannable_list(request, list_id, conn, lang)
    form = await request.form()
    try:
        row_count = int(str(form.get("row_count", "0")))
    except (TypeError, ValueError):
        row_count = 0
    departments = get_shopping_departments(lang=lang, conn=conn)
    valid_dept_ids = {int(str(d["id"])) for d in departments}
    default_dept = next(
        (int(str(d["id"])) for d in departments if str(d["name"]) == "autre"),
        next(iter(valid_dept_ids), 1),
    )
    added = 0
    for index in range(min(row_count, 100)):
        if f"include_{index}" not in form:
            continue
        text = str(form.get(f"text_{index}", "")).strip()
        if not text:
            continue
        quantity = str(form.get(f"quantity_{index}", "")).strip() or None
        try:
            department_id = int(str(form.get(f"department_{index}", default_dept)))
        except (TypeError, ValueError):
            department_id = default_dept
        if department_id not in valid_dept_ids:
            department_id = default_dept
        add_shopping_list_item(
            list_id,
            department_id,
            text[:500],
            quantity[:100] if quantity else None,
            conn=conn,
        )
        added += 1
    if not added:
        raise HTTPException(status_code=400, detail=gettext("error.scan_no_selection", lang))
    return _render_items(request, shopping_list, list_id, lang, conn)
