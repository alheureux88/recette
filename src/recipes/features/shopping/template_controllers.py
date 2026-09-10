"""Shopping templates — pages de gestion (usagers connectés uniquement)."""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from recipes.features.preferences.controllers import ordered_departments_for_user
from recipes.features.shopping.template_services import (
    add_template_item,
    create_template,
    delete_template,
    get_template_by_id,
    get_template_items,
    get_user_templates,
    remove_template_item,
    rename_template,
    update_template_item,
)
from recipes.shared import auth as auth_module
from recipes.shared.db import get_db
from recipes.shared.i18n import gettext
from recipes.shared.models import JsonDict

router = APIRouter(tags=["shopping-templates"])


def _current_user_id(request: Request) -> int | None:
    user = auth_module.get_user(request)
    if not user:
        return None
    try:
        return int(str(user.get("id")))
    except (TypeError, ValueError):
        return None


def _login_redirect() -> RedirectResponse:
    if not auth_module.OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url="/auth/login", status_code=302)


def _require_owner(request: Request, template: JsonDict | None, lang: str) -> JsonDict:
    user_id = _current_user_id(request)
    if user_id is None or template is None:
        raise HTTPException(status_code=404, detail=gettext("error.template_not_found", lang))
    try:
        owner_id = int(str(template.get("user_id")))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=404, detail=gettext("error.template_not_found", lang)
        ) from None
    if owner_id != user_id:
        raise HTTPException(status_code=404, detail=gettext("error.template_not_found", lang))
    return template


@router.get("/shopping/templates", response_class=HTMLResponse, response_model=None)
async def templates_page(
    request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    """Liste les modèles d'épicerie de l'usager connecté."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    user_id = _current_user_id(request)
    if user_id is None:
        return _login_redirect()
    lang = _resolve_request_lang(request)
    template_list = get_user_templates(user_id, conn=conn)
    with_counts = []
    for tpl in template_list:
        items = get_template_items(int(str(tpl["id"])), lang=lang, conn=conn)
        tpl["item_count"] = len(items)
        with_counts.append(tpl)
    return templates.TemplateResponse(
        request=request,
        name="shopping_templates.html",
        context=_base_context(request, templates_list=with_counts),
    )


@router.post("/shopping/templates", response_model=None)
async def template_create(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    name: str = Form(...),
) -> RedirectResponse:
    """Crée un nouveau modèle."""
    from recipes.shared.web import _resolve_request_lang

    user_id = _current_user_id(request)
    if user_id is None:
        return _login_redirect()
    name = name.strip()
    if not name:
        lang = _resolve_request_lang(request)
        raise HTTPException(status_code=400, detail=gettext("error.name_required", lang))
    tpl = create_template(name, user_id, conn=conn)
    return RedirectResponse(url=f"/shopping/templates/{tpl['id']}", status_code=303)


@router.get("/shopping/templates/{template_id}", response_class=HTMLResponse, response_model=None)
async def template_detail(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    template_id: int = Path(gt=0),
) -> HTMLResponse | RedirectResponse:
    """Page d'édition d'un modèle."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    user_id = _current_user_id(request)
    if user_id is None:
        return _login_redirect()
    lang = _resolve_request_lang(request)
    template = _require_owner(request, get_template_by_id(template_id, conn=conn), lang)
    items = get_template_items(template_id, lang=lang, conn=conn)
    departments = ordered_departments_for_user(request, lang, conn)
    grouped: dict[int, dict[str, Any]] = {}
    for dept in departments:
        grouped[int(str(dept["id"]))] = {"department": dept, "item_list": []}
    for item in items:
        dept_id = int(str(item["department_id"]))
        if dept_id in grouped:
            grouped[dept_id]["item_list"].append(item)
    return templates.TemplateResponse(
        request=request,
        name="shopping_template_detail.html",
        context=_base_context(
            request,
            shopping_template=template,
            departments=departments,
            grouped_departments=list(grouped.values()),
        ),
    )


@router.post("/shopping/templates/{template_id}/delete", response_model=None)
async def template_delete(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    template_id: int = Path(gt=0),
) -> RedirectResponse:
    """Supprime un modèle."""
    from recipes.shared.web import _resolve_request_lang

    user_id = _current_user_id(request)
    if user_id is None:
        return _login_redirect()
    lang = _resolve_request_lang(request)
    _require_owner(request, get_template_by_id(template_id, conn=conn), lang)
    delete_template(template_id, conn=conn)
    return RedirectResponse(url="/shopping/templates", status_code=303)


@router.post("/shopping/templates/{template_id}/rename", response_model=None)
async def template_rename(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    template_id: int = Path(gt=0),
    name: str = Form(...),
) -> RedirectResponse:
    """Renomme un modèle."""
    from recipes.shared.web import _resolve_request_lang

    user_id = _current_user_id(request)
    if user_id is None:
        return _login_redirect()
    lang = _resolve_request_lang(request)
    _require_owner(request, get_template_by_id(template_id, conn=conn), lang)
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail=gettext("error.name_required", lang))
    rename_template(template_id, name, conn=conn)
    return RedirectResponse(url=f"/shopping/templates/{template_id}", status_code=303)


@router.post("/shopping/templates/{template_id}/items", response_model=None)
async def template_add_item(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    template_id: int = Path(gt=0),
    department_id: int = Form(...),
    text: str = Form(...),
    quantity: str | None = Form(None),
) -> RedirectResponse:
    """Ajoute un article au modèle."""
    from recipes.shared.web import _resolve_request_lang

    user_id = _current_user_id(request)
    if user_id is None:
        return _login_redirect()
    lang = _resolve_request_lang(request)
    _require_owner(request, get_template_by_id(template_id, conn=conn), lang)
    text = text.strip()
    quantity = quantity.strip() if quantity else None
    if not text:
        raise HTTPException(status_code=400, detail=gettext("error.text_required", lang))
    if not department_id:
        raise HTTPException(status_code=400, detail=gettext("error.department_required", lang))
    add_template_item(template_id, department_id, text, quantity, conn=conn)
    return RedirectResponse(url=f"/shopping/templates/{template_id}", status_code=303)


@router.post("/shopping/template-items/{item_id}/remove", response_model=None)
async def template_item_remove(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    item_id: int = Path(gt=0),
) -> RedirectResponse:
    """Retire un article d'un modèle."""
    from recipes.shared.web import _resolve_request_lang

    user_id = _current_user_id(request)
    if user_id is None:
        return _login_redirect()
    lang = _resolve_request_lang(request)
    owner_row = conn.execute(
        "SELECT template_id FROM shopping_template_items WHERE id = ?", (item_id,)
    ).fetchone()
    if not owner_row:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))
    template = get_template_by_id(int(str(owner_row["template_id"])), conn=conn)
    _require_owner(request, template, lang)
    remove_template_item(item_id, conn=conn)
    return RedirectResponse(url=f"/shopping/templates/{owner_row['template_id']}", status_code=303)


@router.post("/shopping/template-items/{item_id}/update", response_model=None)
async def template_item_update(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    item_id: int = Path(gt=0),
    text: str = Form(...),
    quantity: str | None = Form(None),
    department_id: int | None = Form(None),
) -> RedirectResponse:
    """Met à jour un article d'un modèle."""
    from recipes.shared.web import _resolve_request_lang

    user_id = _current_user_id(request)
    if user_id is None:
        return _login_redirect()
    lang = _resolve_request_lang(request)
    owner_row = conn.execute(
        "SELECT template_id FROM shopping_template_items WHERE id = ?", (item_id,)
    ).fetchone()
    if not owner_row:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))
    template = get_template_by_id(int(str(owner_row["template_id"])), conn=conn)
    _require_owner(request, template, lang)
    text = text.strip()
    quantity = quantity.strip() if quantity else None
    update_template_item(item_id, text, quantity, department_id, conn=conn)
    return RedirectResponse(url=f"/shopping/templates/{owner_row['template_id']}", status_code=303)
